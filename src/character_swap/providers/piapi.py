"""The only module that talks to PiAPI. Paid tasks are sent once and persisted."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired, ProviderError

API = "https://api.piapi.ai/api/v1"
UPLOAD_URL = "https://upload.theapi.app/api/ephemeral_resource"
# PiAPI's limit is 10 MB, but its docs don't say whether the base64 text counts; 7 MB of
# file is under 10 MB either way.
MAX_UPLOAD_BYTES = 7_000_000
UPLOAD_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".mp4")
POLL_GROWTH = 1.5  # each wait is 1.5x the last, up to max_poll_seconds
WORKSPACE = "https://piapi.ai/workspace"
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")

KEY_MESSAGE = (
    "Your PIAPI_API_KEY was rejected. Check .env (get it at https://piapi.ai, "
    "Workspace > API Keys)."
)
PLAN_MESSAGE = (
    "PiAPI refused this because it needs the Creator plan or higher (saved faces and "
    "file uploads). Upgrade at https://piapi.ai/pricing. Nothing was charged."
)


class PiAPIError(ProviderError):
    """PiAPI rejected, failed or has not finished a request."""


class PiAPIOutputExpired(PiAPIError, OutputExpired):
    """The finished task's video link is gone; only a new (paid) run recovers it."""


def task_status(raw: object) -> str:
    """PiAPI's status words vary in casing across its docs; map them to Replicate's words."""
    word = str(raw or "").strip().lower()
    if word in ("completed", "success", "succeeded"):
        return "succeeded"
    if word in ("failed", "cancelled", "canceled"):
        return "failed"
    if word in ("", "pending", "staged", "starting"):
        return "starting"
    return "processing"  # processing, retry, or a word PiAPI adds later: still running


def _data(body: object) -> dict:
    """PiAPI wraps answers as {code, data, message}; some answers are the data itself."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def _envelope_error(body: object) -> str | None:
    """The message of an answer whose own `code` says it failed (HTTP can still be 200)."""
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if isinstance(code, int) and code >= 400:
        return str(body.get("message") or f"code {code}")
    return None


class PiAPIClient:
    def __init__(
        self,
        key: str,
        *,
        http: httpx.Client | None = None,
        poll_seconds: float = 5.0,
        max_poll_seconds: float = 30.0,
        max_polls: int = 1440,  # ~12 h at the 30 s ceiling: PiAPI queues can take hours
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = http or httpx.Client(timeout=httpx.Timeout(180, connect=15))
        self._headers = {"x-api-key": key}
        self._poll_seconds = poll_seconds
        self._max_poll_seconds = max_poll_seconds
        self._max_polls = max_polls
        self._sleep = sleep

    # ---- free calls (retried) -------------------------------------------------
    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._http.request(method, url, headers=self._headers, **kwargs)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as error:
            raise PiAPIError(f"PiAPI sent an unreadable answer for {url}.") from error

    def _free(self, call: Callable[[], Any], what: str) -> Any:
        """A retried free call whose final failure is a PiAPIError."""
        try:
            return common.retry_free(call, sleep=self._sleep)
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if code == 401:
                raise PiAPIError(KEY_MESSAGE) from error
            if code == 403:
                raise PiAPIError(PLAN_MESSAGE) from error
            raise PiAPIError(
                f"PiAPI returned {code} while {what}: {error.response.text[:200]}"
            ) from error
        except httpx.TransportError as error:
            raise PiAPIError(f"Could not reach PiAPI while {what}: {error!r}") from error

    def upload_temp(self, path: Path) -> str:
        """Upload a file PiAPI can read for 24 hours; returns its link."""
        path = Path(path)
        if path.suffix.lower() not in UPLOAD_TYPES:
            raise PiAPIError(
                f"PiAPI can't take {path.suffix} files; use one of: {', '.join(UPLOAD_TYPES)}."
            )
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise PiAPIError(
                f"{path.name} is {size / 1e6:.1f} MB; PiAPI uploads must stay under "
                f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB."
            )
        payload = {
            "file_name": path.name,
            "file_data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
        body = self._free(lambda: self._send("POST", UPLOAD_URL, json=payload), "uploading")
        problem = _envelope_error(body)
        if problem:
            raise PiAPIError(f"PiAPI refused the upload of {path.name}: {problem}")
        url = _data(body).get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise PiAPIError(f"PiAPI's upload answer had no link for {path.name}.")
        return url

    def create_asset(self, url: str, name: str) -> dict:
        """Ask PiAPI to check and keep a face photo; returns {asset_id, status, ...}."""
        body = self._free(
            lambda: self._send("POST", f"{API}/asset/upload", json={"url": url, "name": name}),
            "saving a face",
        )
        problem = _envelope_error(body)
        if problem:
            raise PiAPIError(f"PiAPI refused the face: {problem}")
        data = _data(body)
        if not _ID.fullmatch(str(data.get("asset_id", ""))):
            raise PiAPIError("PiAPI's answer had no face id.")
        return data

    def get_asset(self, asset_id: str) -> dict | None:
        """The face's current record (this also refreshes its idle lifetime); None if purged."""
        if not _ID.fullmatch(asset_id):
            raise PiAPIError(f"{asset_id!r} is not a PiAPI face id.")
        try:
            body = self._free(
                lambda: self._send("GET", f"{API}/asset/{asset_id}"), "checking a face"
            )
        except PiAPIError as error:
            cause = error.__cause__
            if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 404:
                return None
            raise
        problem = _envelope_error(body)
        if problem:
            if body.get("code") == 404:
                return None
            raise PiAPIError(f"PiAPI could not check the face: {problem}")
        return _data(body)

    def download(self, url: str, dest: Path) -> Path:
        """Download a finished video without the API key (it is not an API call)."""

        def fetch() -> bytes:
            response = self._http.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content

        dest = Path(dest)
        try:
            data = common.retry_free(fetch, sleep=self._sleep)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (403, 404, 410):
                raise PiAPIOutputExpired(
                    "This video link has expired (PiAPI keeps results 24 hours)."
                ) from error
            raise PiAPIError(f"Download failed ({error.response.status_code}): {url}") from error
        except httpx.TransportError as error:
            raise PiAPIError(f"Download failed: {error!r}") from error
        part = dest.with_suffix(dest.suffix + ".part")
        part.write_bytes(data)
        os.replace(part, dest)
        return dest

    # ---- paid call (never retried) -------------------------------------------
    def run(
        self,
        *,
        body: dict,
        record: Path,
        on_created: Callable[[dict], None] | None = None,
    ) -> str:
        """Create a task once, save it to `record`, wait, and return the video link.

        A marker is created exclusively BEFORE the paid POST, so a crash or a timeout can
        never lead to a second charge. If `record` exists, it is resumed instead.
        `on_created(task)` runs once, right after PiAPI confirms a NEW task.
        """
        record = Path(record)
        task = None
        if record.exists():
            try:
                task = self._load(record)
            except FileNotFoundError:
                task = None  # removed since the check; the exclusive claim still guards
        if task is None:
            task = self._create(body, record, on_created)
        elif task["status"] == "creating":
            raise PiAPIError(unconfirmed_message(record))
        elif task["status"] == "failed":
            raise PiAPIError(failed_message(task, record))
        return self._output(self._wait(task, record), record)

    @staticmethod
    def _load(record: Path) -> dict:
        damaged = PiAPIError(
            f"{record} is damaged. The task may still exist: check your task list at "
            f"{WORKSPACE} before deleting it (deleting and running again costs money)."
        )
        try:
            data = json.loads(record.read_text())
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise damaged from error
        if not isinstance(data, dict) or "status" not in data:
            raise damaged
        if data["status"] != "creating" and not _ID.fullmatch(str(data.get("id", ""))):
            raise damaged
        return data

    def _create(self, body: dict, record: Path, on_created: Callable[[dict], None] | None) -> dict:
        if not body:
            # Resumes pass {} because the record exists; if it vanished since, posting {}
            # would buy a task with no input. Nothing sent, no marker written.
            raise PiAPIError(
                f"Refusing to create a paid task with no input; {record} vanished during "
                "resume. Run the command again."
            )
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker = common.claim(record)
        except FileExistsError as error:
            raise PiAPIError(unconfirmed_message(record)) from error
        try:
            response = self._http.post(f"{API}/task", headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise PiAPIError(unconfirmed_message(record)) from error
        code = response.status_code
        if 400 <= code < 500 and code != 408:
            rejection = _rejection(code, response.text)
            task = self._task_in(response)
            if task is not None:  # a "rejection" that still names a task: it may be billed
                self._keep(task, record, on_created)
                raise PiAPIError(
                    f"{rejection} PiAPI returned a task id {task['id']}; it is saved in "
                    f"{record}; check {WORKSPACE} before deleting it."
                )
            self._release(record, marker, rejection)
            raise PiAPIError(rejection)
        if response.is_error:
            raise PiAPIError(unconfirmed_message(record))
        try:
            answer = response.json()
        except ValueError as error:
            raise PiAPIError(unconfirmed_message(record)) from error
        data = _data(answer)
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            problem = _envelope_error(answer)
            envelope_code = answer.get("code") if isinstance(answer, dict) else None
            # Only a clear client error means nothing exists; 5xx/408/other: it may.
            if (
                problem is not None
                and isinstance(envelope_code, int)
                and 400 <= envelope_code < 500
                and envelope_code != 408
            ):
                rejection = f"PiAPI rejected the request (nothing was charged): {problem[:300]}"
                self._release(record, marker, rejection)
                raise PiAPIError(rejection)
            raise PiAPIError(unconfirmed_message(record))
        task = {"id": task_id, "status": task_status(data.get("status")), "task": data}
        self._keep(task, record, on_created)
        return task

    @staticmethod
    def _task_in(response: httpx.Response) -> dict | None:
        """The task a response names, if its body is JSON with a valid data.task_id."""
        try:
            data = _data(response.json())
        except ValueError:
            return None
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            return None
        return {"id": task_id, "status": task_status(data.get("status")), "task": data}

    @staticmethod
    def _keep(task: dict, record: Path, on_created: Callable[[dict], None] | None) -> None:
        """Save a newly created (paid) task and announce it, even if saving fails."""
        try:
            common.save(record, task)
        except OSError as error:
            common.announce(on_created, task, what="paid PiAPI task")  # paid all the same
            raise PiAPIError(
                f"Paid PiAPI task {task['id']} was created but its record could not be saved "
                f"({error}). Note this id; check {WORKSPACE}."
            ) from error
        common.announce(on_created, task, what="paid PiAPI task")

    @staticmethod
    def _release(record: Path, marker: dict, nothing_charged: str) -> None:
        common.release(
            record,
            marker,
            nothing_charged=nothing_charged,
            taken_over=_taken_over(record),
            error=PiAPIError,
        )

    def _wait(self, task: dict, record: Path) -> dict:
        polls, delay = 0, self._poll_seconds
        while task["status"] not in ("succeeded", "failed"):
            if polls >= self._max_polls:
                raise PiAPIError(
                    f"PiAPI task {task['id']} is still running (saved in {record}). PiAPI "
                    "queues can take hours at busy times. Run the same command later to keep "
                    "waiting; no new charge."
                )
            self._sleep(delay)
            delay = min(delay * POLL_GROWTH, self._max_poll_seconds)
            # Built from the saved id: the key never goes to a URL taken from a response.
            url = f"{API}/task/{task['id']}"
            try:
                answer = common.retry_free(lambda: self._send("GET", url), sleep=self._sleep)  # noqa: B023
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if code == 401:
                    raise PiAPIError(KEY_MESSAGE) from error
                if code == 404:
                    raise PiAPIError(
                        f"PiAPI no longer recognizes task {task['id']}; check {WORKSPACE}."
                    ) from error
                raise self._lost(task, record) from error
            except (httpx.TransportError, PiAPIError) as error:
                raise self._lost(task, record) from error
            data = _data(answer)
            if (
                _envelope_error(answer)
                or "status" not in data
                or data.get("task_id") not in (None, task["id"])
            ):
                raise self._lost(task, record)  # before saving: the record stays as it was
            task = {"id": task["id"], "status": task_status(data.get("status")), "task": data}
            try:
                common.save(record, task)
            except OSError as error:
                raise self._lost(task, record) from error
            polls += 1
        if task["status"] == "failed":
            raise PiAPIError(failed_message(task, record))
        return task

    @staticmethod
    def _output(task: dict, record: Path) -> str:
        output = (task.get("task") or {}).get("output")
        url = (output.get("video") or output.get("video_url")) if isinstance(output, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise PiAPIError(
                f"PiAPI task {task['id']} finished without a video link. Check {WORKSPACE} "
                f"before deleting {record} (a new run costs money)."
            )
        return url

    @staticmethod
    def _lost(task: dict, record: Path) -> PiAPIError:
        return PiAPIError(
            f"Lost connection while waiting; PiAPI task {task['id']} is saved in {record}. "
            "Run the same command again to keep waiting; no new charge."
        )


def unconfirmed_message(record: Path) -> str:
    """Guidance when a record holds only our "creating" marker (POST never confirmed)."""
    return (
        "The request was sent to PiAPI but not confirmed. It may have been charged, or "
        "another charswap command for this job is still sending it. First make sure no "
        f"other charswap command is running for this job. Then check your task list at "
        f"{WORKSPACE}; if nothing is there, delete {record} and run again."
    )


def failed_message(task: dict, record: Path) -> str:
    error = (task.get("task") or {}).get("error") or {}
    reason = (error.get("message") or error.get("raw_message")) if isinstance(error, dict) else None
    return (
        f"PiAPI task {task.get('id')} failed: {reason or 'no reason given'}. PiAPI refunds "
        "tasks it rejects for content. To try again, delete "
        f"{record} and run the same command (it shows the cost and asks first)."
    )


def _taken_over(record: Path) -> str:
    return (
        f"Another charswap run took over {record} while this one was talking to PiAPI; "
        "this run sent nothing more and was not charged for it. Let the other run finish, "
        "or run the command again to resume what it saved."
    )


def _rejection(code: int, body: str) -> str:
    if code == 401:
        return KEY_MESSAGE
    if code == 402:
        return "Add credit to your PiAPI account (nothing was charged)."
    if code == 403:
        return f"PiAPI refused the request (403; nothing was charged): {body[:300]}. {PLAN_MESSAGE}"
    if code == 429:
        return (
            "PiAPI is busy (429). Nothing was charged; wait a minute and run the same "
            "command again."
        )
    return f"PiAPI rejected the request ({code}); nothing was charged: {body[:300]}"
