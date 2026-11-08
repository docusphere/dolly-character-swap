"""The only module that talks to Replicate. Paid requests are sent once and persisted."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired, ProviderError

API = "https://api.replicate.com/v1"
TERMINAL = {"succeeded", "failed", "canceled"}
POLL_GROWTH = 1.5  # each wait is 1.5x the last, up to max_poll_seconds
CREATE_429_RETRIES = 3  # a throttled create (429) was refused before anything was created
RATE_LIMIT_WAIT = 10.0  # seconds, when Retry-After is missing or unreadable
MAX_RATE_LIMIT_WAIT = 60.0


class ReplicateError(ProviderError):
    """Replicate rejected, failed or has not finished a prediction."""


class ReplicateOutputExpired(ReplicateError, OutputExpired):
    """The finished prediction's output link is gone; only a new (paid) run recovers it."""


class ReplicateClient:
    def __init__(
        self,
        token: str,
        *,
        http: httpx.Client | None = None,
        poll_seconds: float = 5.0,
        max_poll_seconds: float = 30.0,
        max_polls: int = 720,
        sleep: Callable[[float], None] = time.sleep,
        on_wait: Callable[[float, int, int], None] | None = None,
    ):
        self._http = http or httpx.Client(timeout=httpx.Timeout(180, connect=15))
        self._headers = {"Authorization": f"Bearer {token}"}
        self._poll_seconds = poll_seconds
        self._max_poll_seconds = max_poll_seconds
        self._max_polls = max_polls
        self._sleep = sleep
        self._on_wait = on_wait  # told (seconds, attempt, max attempts) before each 429 wait

    # ---- free calls (retried) -------------------------------------------------
    def _retry(self, call: Callable[[], Any], attempts: int = 3) -> Any:
        """Retry transport errors, 5xx and 429. Raises the last raw httpx error."""
        return common.retry_free(call, sleep=self._sleep, attempts=attempts)

    def _free(self, call: Callable[[], Any]) -> Any:
        """A retried free call whose final failure is a ReplicateError."""
        try:
            return self._retry(call)
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if code == 401:
                raise ReplicateError(TOKEN_MESSAGE) from error
            raise ReplicateError(f"Replicate returned {code} for {error.request.url}") from error
        except httpx.TransportError as error:
            raise ReplicateError(f"Could not reach Replicate: {error!r}") from error

    def _get_json(self, url: str) -> dict:
        response = self._http.get(url, headers=self._headers)
        response.raise_for_status()
        return response.json()

    def upload(self, path: Path) -> str:
        """Upload a local file privately; returns a URL only Replicate can read."""

        def send() -> str:
            with open(path, "rb") as file:
                response = self._http.post(
                    f"{API}/files",
                    headers=self._headers,
                    files={"content": (Path(path).name, file)},
                )
            response.raise_for_status()
            return response.json()["urls"]["get"]

        return self._free(send)

    def latest_version(self, model: str) -> tuple[str, dict]:
        data = self._free(lambda: self._get_json(f"{API}/models/{model}"))
        version = data["latest_version"]
        props = version["openapi_schema"]["components"]["schemas"]["Input"]["properties"]
        return version["id"], props

    def download(self, url: str, dest: Path) -> Path:
        """Download without the API token (output URLs are not Replicate API calls)."""

        def fetch() -> bytes:
            response = self._http.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content

        dest = Path(dest)
        try:
            data = self._retry(fetch)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (403, 404, 410):
                raise ReplicateOutputExpired(
                    "This output link has expired (Replicate keeps outputs ~1 hour). "
                    "Delete the saved prediction record for this step to regenerate "
                    "— this costs money."
                ) from error
            raise ReplicateError(
                f"Download failed ({error.response.status_code}): {url}"
            ) from error
        except httpx.TransportError as error:
            raise ReplicateError(f"Download failed: {error!r}") from error
        part = dest.with_suffix(dest.suffix + ".part")
        part.write_bytes(data)
        os.replace(part, dest)
        return dest

    # ---- paid call (never retried) -------------------------------------------
    def run(
        self,
        *,
        input: dict,
        record: Path,
        version: str | None = None,
        model: str | None = None,
        on_created: Callable[[dict], None] | None = None,
    ) -> Any:
        """Create a prediction once, save it to `record`, and wait for its output.

        A marker is created exclusively BEFORE the paid POST, so a crash or timeout can
        never lead to a second charge. If `record` exists, resume it instead.
        `on_created(prediction)` runs once, right after Replicate confirms a NEW
        prediction (never on resume); a failing callback only warns.
        """
        record = Path(record)
        prediction = None
        if record.exists():
            try:
                prediction = self._load(record)
            except FileNotFoundError:
                prediction = None  # removed since the check; the exclusive claim still guards
        if prediction is not None:
            if prediction["status"] == "creating":
                raise ReplicateError(unconfirmed_message(record))
            if prediction["status"] == "succeeded":
                return prediction.get("output")
            if prediction["status"] in ("failed", "canceled"):
                raise ReplicateError(
                    f"Earlier prediction {prediction.get('id')} {prediction['status']}: "
                    f"{prediction.get('error') or 'no reason given'}. "
                    f"Delete {record} to try again (this costs money)."
                )
        else:
            prediction = self._create(
                input=input, version=version, model=model, record=record, on_created=on_created
            )
        return self._wait(prediction, record)

    @staticmethod
    def _load(record: Path) -> dict:
        damaged = ReplicateError(
            f"{record} is damaged. The prediction may still exist — check "
            "https://replicate.com/predictions before deleting it "
            "(deleting and re-running costs money)."
        )
        try:
            data = json.loads(record.read_text())
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise damaged from error
        if not isinstance(data, dict) or "status" not in data:
            raise damaged
        if data["status"] != "creating" and "id" not in data:
            raise damaged
        return data

    @staticmethod
    def _save(record: Path, data: dict) -> None:
        common.save(record, data)

    def _create(
        self,
        *,
        input: dict,
        version: str | None,
        model: str | None,
        record: Path,
        on_created: Callable[[dict], None] | None = None,
    ) -> dict:
        if version:
            url, body = f"{API}/predictions", {"version": version, "input": input}
        elif model:
            url, body = f"{API}/models/{model}/predictions", {"input": input}
        else:
            raise ValueError("Pass a version or a model.")
        if not input:
            # Resumes pass {} because the record exists; if it vanished since, posting {}
            # would buy a prediction with no input. Nothing sent, no marker written.
            raise ReplicateError(
                f"Refusing to create a paid prediction with no input; {record} vanished "
                "during resume. Run the command again."
            )
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker = self._claim(record)
        except FileExistsError as error:
            raise ReplicateError(unconfirmed_message(record)) from error
        response, marker = self._post_create(url, body, record, marker)
        code = response.status_code
        if 400 <= code < 500 and code != 408:
            rejection = _rejection(code, response.text)
            self._release(record, marker, rejection)
            raise ReplicateError(rejection)
        if response.is_error:
            raise ReplicateError(unconfirmed_message(record))
        try:
            prediction = response.json()
            if (
                not isinstance(prediction, dict)
                or "id" not in prediction
                or "status" not in prediction
            ):
                raise ValueError("not a prediction")
        except ValueError as error:
            raise ReplicateError(unconfirmed_message(record)) from error
        try:
            self._save(record, prediction)
        except OSError as error:
            _announce(on_created, prediction)  # paid even though the record was not saved
            raise ReplicateError(
                f"Paid prediction {prediction['id']} was created but its record could not be "
                f"saved ({error}). Note this id; check https://replicate.com/predictions."
            ) from error
        _announce(on_created, prediction)
        return prediction

    @staticmethod
    def _claim(record: Path) -> dict:
        return common.claim(record)

    @staticmethod
    def _release(record: Path, marker: dict, nothing_charged: str) -> None:
        common.release(
            record,
            marker,
            nothing_charged=nothing_charged,
            taken_over=_taken_over(record),
            error=ReplicateError,
        )

    def _post_create(
        self, url: str, body: dict, record: Path, marker: dict
    ) -> tuple[httpx.Response, dict]:
        """POST the paid create. Repeat it only after a 429, and only if we re-claim the marker.

        A 429 means Replicate throttled the request before creating anything: nothing exists
        and nothing was billed. We release our marker while we wait (so no waiting run holds
        a marker that someone might delete), then claim it again exclusively. If another run
        claimed it meanwhile, we stop without posting. Every other answer goes back to
        _create() and is never re-sent.
        """
        for attempt in range(CREATE_429_RETRIES + 1):
            try:
                response = self._http.post(url, headers=self._headers, json=body)
            except httpx.HTTPError as error:
                raise ReplicateError(unconfirmed_message(record)) from error
            if response.status_code != 429 or attempt == CREATE_429_RETRIES:
                return response, marker
            busy = "Replicate was busy (rate limit, 429); nothing was charged."
            self._release(record, marker, busy)  # only ours; the 429 means nothing exists
            wait = _retry_after(response)
            if self._on_wait:
                self._on_wait(wait, attempt + 1, CREATE_429_RETRIES)
            self._sleep(wait)
            try:
                marker = self._claim(record)
            except FileExistsError as error:
                raise ReplicateError(_taken_over(record)) from error
            except OSError as error:
                raise ReplicateError(
                    f"{busy} This run could not write its marker {record} to try again "
                    f"({error}) and sent nothing more. Fix the folder and run again."
                ) from error
        raise AssertionError("unreachable")

    def _wait(self, prediction: dict, record: Path) -> Any:
        polls, delay = 0, self._poll_seconds
        while prediction.get("status") not in TERMINAL:
            if polls >= self._max_polls:
                raise ReplicateError(
                    f"Prediction {prediction.get('id')} is still running (saved in {record}). "
                    "Resume the job to keep waiting; no new charge."
                )
            # Back off: low-balance accounts get ~6 requests/min, and a fixed 5 s poll
            # starved the next paid create of its quota (first live run).
            self._sleep(delay)
            delay = min(delay * POLL_GROWTH, self._max_poll_seconds)
            # Built from the id: never send the token to a URL taken from a response.
            url = f"{API}/predictions/{prediction['id']}"
            try:
                fresh = self._retry(lambda: self._get_json(url))  # noqa: B023
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if code == 401:
                    raise ReplicateError(TOKEN_MESSAGE) from error
                if code in (403, 404):
                    raise ReplicateError(
                        f"Replicate no longer recognizes prediction {prediction['id']} "
                        f"(status {code}); check https://replicate.com/predictions"
                    ) from error
                raise self._lost(prediction, record) from error
            except (httpx.TransportError, ValueError) as error:
                raise self._lost(prediction, record) from error
            if not isinstance(fresh, dict) or "id" not in fresh or "status" not in fresh:
                raise self._lost(prediction, record)
            prediction = fresh
            self._save(record, prediction)
            polls += 1
        if prediction["status"] != "succeeded":
            ident, state = prediction.get("id"), prediction["status"]
            raise ReplicateError(
                f"Prediction {ident} {state}: {prediction.get('error') or 'no reason given'}"
            )
        return prediction.get("output")

    @staticmethod
    def _lost(prediction: dict, record: Path) -> ReplicateError:
        return ReplicateError(
            f"Lost connection while waiting; prediction {prediction.get('id')} is saved in "
            f"{record}. Resume the job — no new charge."
        )


TOKEN_MESSAGE = "Your REPLICATE_API_TOKEN was rejected — check .env"


def unconfirmed_message(record: Path) -> str:
    """Guidance when a record holds only our \"creating\" marker (POST never confirmed)."""
    return (
        "The request was sent but not confirmed — it may have been charged, or another "
        "charswap command for this job is still sending it. First make sure no other "
        "charswap command is running for this job. Then check https://replicate.com/predictions; "
        f"if nothing is there, delete {record} and run again."
    )


def _announce(callback: Callable[[dict], None] | None, prediction: dict) -> None:
    """Report a new paid prediction; a failing logger must never lose track of it."""
    # run -> _create -> _announce -> common.announce -> warn: 5 frames reach run's caller
    common.announce(callback, prediction, what="paid prediction", stacklevel=5)


def _taken_over(record: Path) -> str:
    return (
        f"Another charswap run took over {record} while this one was talking to Replicate; "
        "this run sent nothing more and was not charged for it. Let the other run finish, "
        "or run the command again to resume what it saved."
    )


def _retry_after(response: httpx.Response) -> float:
    """Seconds to wait after a 429: Retry-After when readable, else a default; capped."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return RATE_LIMIT_WAIT
    if not math.isfinite(seconds):
        return RATE_LIMIT_WAIT
    return min(max(seconds, 1.0), MAX_RATE_LIMIT_WAIT)


def _rejection(code: int, body: str) -> str:
    if code == 401:
        return TOKEN_MESSAGE
    if code == 402:
        return "Add billing credit at replicate.com/account/billing (nothing was charged)."
    if code == 422:
        return f"Replicate rejected the input (nothing was charged): {body[:300]}"
    if code == 429:
        return "Rate limited, nothing was charged; wait a minute and retry."
    return f"Replicate rejected the request ({code}); nothing was charged: {body[:300]}"
