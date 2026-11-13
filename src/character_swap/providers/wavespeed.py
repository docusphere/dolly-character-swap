"""The only module that talks to WaveSpeed. Paid predictions are sent once and persisted."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired, ProviderError

API = "https://api.wavespeed.ai/api/v3"
MODEL = "bytedance/seedance-2.5/video-edit"
SUBMIT_URL = f"{API}/{MODEL}"
UPLOAD_URL = f"{API}/media/uploads"
MAX_UPLOAD_BYTES = 209_715_200  # 200 MB; WaveSpeed keeps uploads 7 days
POLL_GROWTH = 1.5  # each wait is 1.5x the last, up to max_poll_seconds
DASHBOARD = "your WaveSpeed dashboard (https://wavespeed.ai, Predictions)"
# A clear refusal of the paid create: no prediction exists and nothing was charged.
REJECTED = (400, 401, 403, 404, 422)
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")

KEY_MESSAGE = (
    "Your WAVESPEED_API_KEY was rejected. Check .env (get it at https://wavespeed.ai, "
    "Dashboard > API Keys)."
)
BUSY_MESSAGE = (
    "WaveSpeed is busy (429); nothing was charged, try again in a minute with the same command."
)


class WaveSpeedError(ProviderError):
    """WaveSpeed rejected, failed or has not finished a request."""


class WaveSpeedRejected(WaveSpeedError):
    """WaveSpeed refused the paid create outright: no prediction exists, nothing was charged."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class WaveSpeedOutputExpired(WaveSpeedError, OutputExpired):
    """The finished prediction's video link is gone; only a new (paid) run recovers it."""


def prediction_status(raw: object) -> str:
    """WaveSpeed's status words, mapped to the pipeline's (Replicate's) words."""
    word = str(raw or "").strip().lower()
    if word == "completed":
        return "succeeded"
    if word in ("failed", "cancelled", "canceled", "timeout", "deleted"):
        return "failed"
    if word in ("", "created", "queued", "pending"):
        return "starting"
    return "processing"  # processing, or a word WaveSpeed adds later: still running


def _data(body: object) -> dict:
    """WaveSpeed wraps every answer as {code, message, data}."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return {}


def _envelope_error(body: object) -> str | None:
    """The message of an answer whose own `code` says it failed (HTTP can still be 200)."""
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if isinstance(code, int) and code >= 400:
        return str(body.get("message") or f"code {code}")
    return None


def _prediction_in(response: httpx.Response) -> dict | None:
    """The prediction a response names, if its body is JSON with a valid data.id."""
    try:
        data = _data(response.json())
    except ValueError:
        return None
    prediction_id = data.get("id")
    if not isinstance(prediction_id, str) or not _ID.fullmatch(prediction_id):
        return None
    return {"id": prediction_id, "status": prediction_status(data.get("status")), "task": data}


class WaveSpeedClient:
    def __init__(
        self,
        key: str,
        *,
        http: httpx.Client | None = None,
        poll_seconds: float = 5.0,
        max_poll_seconds: float = 15.0,
        max_polls: int = 720,  # ~3 h at the 15 s ceiling; a 4 s draft takes ~3 minutes
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = http or httpx.Client(timeout=httpx.Timeout(180, connect=15))
        self._headers = {"Authorization": f"Bearer {key}"}  # sent per call, never logged
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
            raise WaveSpeedError(f"WaveSpeed sent an unreadable answer for {url}.") from error

    def _free(self, call: Callable[[], Any], what: str) -> Any:
        """A retried free call whose final failure is a WaveSpeedError."""
        try:
            return common.retry_free(call, sleep=self._sleep)
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if code == 401:
                raise WaveSpeedError(KEY_MESSAGE) from error
            raise WaveSpeedError(
                f"WaveSpeed returned {code} while {what}: {error.response.text[:200]}. "
                "Nothing was charged."
            ) from error
        except httpx.RequestError as error:
            raise WaveSpeedError(
                f"Could not reach WaveSpeed while {what}: {error!r}. Nothing was charged."
            ) from error

    def upload(self, path: Path) -> str:
        """Upload a file WaveSpeed can read for 7 days; returns its link. Free."""
        path = Path(path)
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise WaveSpeedError(
                f"{path.name} is {size / 2**20:.0f} MB; WaveSpeed uploads must stay under "
                f"{MAX_UPLOAD_BYTES // 2**20} MB. Nothing was charged."
            )
        ticket = self._free(
            lambda: self._send("POST", UPLOAD_URL, json={"filename": path.name, "size": size}),
            "asking for an upload link",
        )
        problem = _envelope_error(ticket)
        if problem:
            raise WaveSpeedError(
                f"WaveSpeed refused the upload of {path.name}: {problem}. Nothing was charged."
            )
        data = _data(ticket)
        link = data.get("download_url")
        target = data.get("upload") if isinstance(data.get("upload"), dict) else {}
        put_url, headers = target.get("url"), target.get("headers") or {}
        if not (
            isinstance(link, str)
            and link.startswith("https://")
            and isinstance(put_url, str)
            and put_url.startswith("https://")
            and str(target.get("method") or "PUT").upper() == "PUT"
            and isinstance(headers, dict)
        ):
            raise WaveSpeedError(
                f"WaveSpeed's upload answer for {path.name} had no usable link. "
                "Nothing was charged."
            )
        content = path.read_bytes()
        put_headers = {str(k): str(v) for k, v in headers.items()}  # signed URL: no API key

        def put() -> None:
            self._http.request(
                "PUT", put_url, headers=put_headers, content=content
            ).raise_for_status()

        self._free(put, f"uploading {path.name}")
        return link

    def download(self, url: str, dest: Path) -> Path:
        """Download a finished video right away, without the API key."""
        return common.download_now(
            url,
            dest,
            http=self._http,
            sleep=self._sleep,
            provider="WaveSpeed",
            keeps="does not say how long it keeps results",
            expired=WaveSpeedOutputExpired,
            error=WaveSpeedError,
        )

    # ---- paid call (never retried) -------------------------------------------
    def run(
        self,
        *,
        body: dict,
        record: Path,
        on_created: Callable[[dict], None] | None = None,
    ) -> str:
        """Create a prediction once, save it to `record`, wait, and return the video link.

        A marker is created exclusively BEFORE the paid POST, so a crash or a timeout can
        never lead to a second charge. If `record` exists, it is resumed instead.
        `on_created(prediction)` runs once, right after WaveSpeed confirms a NEW prediction.
        """
        record = Path(record)
        prediction = None
        if record.exists():
            try:
                prediction = self._load(record)
            except FileNotFoundError:
                prediction = None  # removed since the check; the exclusive claim still guards
        if prediction is None:
            prediction = self._create(body, record, on_created)
        elif prediction["status"] == "creating":
            raise WaveSpeedError(unconfirmed_message(record))
        elif prediction["status"] == "failed":
            raise WaveSpeedError(failed_message(prediction, record))
        return self._output(self._wait(prediction, record), record)

    @staticmethod
    def _load(record: Path) -> dict:
        damaged = WaveSpeedError(
            f"{record} is damaged. The prediction may still exist: check {DASHBOARD} before "
            "deleting it (deleting and running again costs money)."
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
            # would buy a prediction with no input. Nothing sent, no marker written.
            raise WaveSpeedError(
                f"Refusing to create a paid prediction with no input; {record} vanished during "
                "resume. Run the command again."
            )
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker = common.claim(record)
        except FileExistsError as error:
            raise WaveSpeedError(unconfirmed_message(record)) from error
        try:
            response = self._http.post(SUBMIT_URL, headers=self._headers, json=body)  # once
        except httpx.HTTPError as error:
            raise WaveSpeedError(unconfirmed_message(record)) from error
        code = response.status_code
        if code in REJECTED or code == 429:
            found = _prediction_in(response)
            if found is not None:  # a refusal that still names a prediction: it may be billed
                self._keep(found, record, on_created)
                raise WaveSpeedError(
                    f"WaveSpeed refused the request ({code}) but still returned prediction "
                    f"{found['id']}; it may be charged. It is saved in {record}; check "
                    f"{DASHBOARD} before deleting it."
                )
            message = BUSY_MESSAGE if code == 429 else _rejection(code, response.text)
            self._release(record, marker, message)
            raise WaveSpeedRejected(message, code)
        if response.is_error:  # 5xx or another 4xx (e.g. 402): a prediction may exist
            raise WaveSpeedError(_ambiguous(response, record))
        prediction = _prediction_in(response)
        if prediction is None:  # unreadable, or no id: it may still have been created
            raise WaveSpeedError(_ambiguous(response, record))
        self._keep(prediction, record, on_created)
        return prediction

    @staticmethod
    def _keep(prediction: dict, record: Path, on_created: Callable[[dict], None] | None) -> None:
        """Save a newly created (paid) prediction and announce it, even if saving fails."""
        what = "paid WaveSpeed prediction"
        try:
            common.save(record, prediction)
        except OSError as error:
            common.announce(on_created, prediction, what=what, stacklevel=5)  # paid: tell anyway
            raise WaveSpeedError(
                f"Paid WaveSpeed prediction {prediction['id']} was created but its record could "
                f"not be saved ({error}). Note this id; check {DASHBOARD}."
            ) from error
        common.announce(on_created, prediction, what=what, stacklevel=5)

    @staticmethod
    def _release(record: Path, marker: dict, nothing_charged: str) -> None:
        common.release(
            record,
            marker,
            nothing_charged=nothing_charged,
            taken_over=_taken_over(record),
            error=WaveSpeedError,
        )

    def _wait(self, prediction: dict, record: Path) -> dict:
        polls, delay = 0, self._poll_seconds
        while prediction["status"] not in ("succeeded", "failed"):
            if polls >= self._max_polls:
                raise WaveSpeedError(
                    f"WaveSpeed prediction {prediction['id']} is still running (saved in "
                    f"{record}). Run the same command later to keep waiting; no new charge."
                )
            self._sleep(delay)
            delay = min(delay * POLL_GROWTH, self._max_poll_seconds)
            # Built from the saved id: the key never goes to a URL taken from a response.
            url = f"{API}/predictions/{prediction['id']}/result"
            try:
                answer = common.retry_free(lambda: self._send("GET", url), sleep=self._sleep)  # noqa: B023
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if code == 401:
                    raise WaveSpeedError(KEY_MESSAGE) from error
                if code == 404:
                    raise WaveSpeedError(
                        f"WaveSpeed no longer recognizes prediction {prediction['id']}; check "
                        f"{DASHBOARD}."
                    ) from error
                raise self._lost(prediction, record) from error
            except (httpx.RequestError, WaveSpeedError) as error:
                raise self._lost(prediction, record) from error
            data = _data(answer)
            if (
                _envelope_error(answer)
                or "status" not in data
                or data.get("id") not in (None, prediction["id"])
            ):
                raise self._lost(prediction, record)  # before saving: the record stays as it was
            prediction = {
                "id": prediction["id"],
                "status": prediction_status(data.get("status")),
                "task": data,
            }
            try:
                common.save(record, prediction)
            except OSError as error:
                raise self._lost(prediction, record) from error
            polls += 1
        if prediction["status"] == "failed":
            raise WaveSpeedError(failed_message(prediction, record))
        return prediction

    @staticmethod
    def _output(prediction: dict, record: Path) -> str:
        outputs = (prediction.get("task") or {}).get("outputs")
        first = outputs[0] if isinstance(outputs, list) and outputs else None
        url = first.get("url") if isinstance(first, dict) else first
        if not isinstance(url, str) or not url.startswith("https://"):
            raise WaveSpeedError(
                f"WaveSpeed prediction {prediction['id']} finished without a video link. Check "
                f"{DASHBOARD} before deleting {record} (a new run costs money)."
            )
        return url

    @staticmethod
    def _lost(prediction: dict, record: Path) -> WaveSpeedError:
        return WaveSpeedError(
            f"Lost connection while waiting; WaveSpeed prediction {prediction['id']} is saved in "
            f"{record}. Run the same command again to keep waiting; no new charge."
        )


def unconfirmed_message(record: Path) -> str:
    """Guidance when a record holds only our "creating" marker (POST never confirmed)."""
    return (
        "The request was sent to WaveSpeed but not confirmed. It may have been charged, or "
        "another charswap command for this job is still sending it. First make sure no other "
        f"charswap command is running for this job. Then check {DASHBOARD} before running "
        f"again; if nothing is there, delete {record} and run again."
    )


def failed_message(prediction: dict, record: Path) -> str:
    error = (prediction.get("task") or {}).get("error")
    reason = str(error).strip() if error else ""
    return (
        f"WaveSpeed prediction {prediction.get('id')} failed: {reason or 'no reason given'}. "
        f"It was created, so it may be charged; check {DASHBOARD}. To try again, delete "
        f"{record} and run the same command (it shows the cost and asks first)."
    )


def _taken_over(record: Path) -> str:
    return (
        f"Another charswap run took over {record} while this one was talking to WaveSpeed; "
        "this run sent nothing more and was not charged for it. Let the other run finish, "
        "or run the command again to resume what it saved."
    )


def _excerpt(text: str, limit: int = 200) -> str:
    """One line of a response body, for messages (WaveSpeed's answer only: never our headers)."""
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _ambiguous(response: httpx.Response, record: Path) -> str:
    """An unclear create answer: show what WaveSpeed said (e.g. a low balance), keep the marker."""
    said = _excerpt(response.text) or "(empty answer)"
    return f"WaveSpeed answered {response.status_code}: {said}. {unconfirmed_message(record)}"


def _rejection(code: int, body: str) -> str:
    if code == 401:
        return f"{KEY_MESSAGE} Nothing was charged."
    return f"WaveSpeed rejected the request ({code}); nothing was charged: {body[:300]}"
