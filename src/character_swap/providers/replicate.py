"""The only module that talks to Replicate. Paid requests are sent once and persisted."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

API = "https://api.replicate.com/v1"
TERMINAL = {"succeeded", "failed", "canceled"}


class ReplicateError(RuntimeError):
    """Replicate rejected, failed or has not finished a prediction."""


class ReplicateClient:
    def __init__(
        self,
        token: str,
        *,
        http: httpx.Client | None = None,
        poll_seconds: float = 5.0,
        max_polls: int = 720,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = http or httpx.Client(timeout=httpx.Timeout(180, connect=15))
        self._headers = {"Authorization": f"Bearer {token}"}
        self._poll_seconds = poll_seconds
        self._max_polls = max_polls
        self._sleep = sleep

    # ---- free calls (retried) -------------------------------------------------
    def _retry(self, call: Callable[[], Any], attempts: int = 3) -> Any:
        """Retry transport errors, 5xx and 429. Raises the last raw httpx error."""
        for attempt in range(attempts):
            delay: float = 2**attempt
            try:
                return call()
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if (code < 500 and code != 429) or attempt == attempts - 1:
                    raise
                retry_after = error.response.headers.get("Retry-After", "")
                if code == 429 and retry_after.isdigit():
                    delay = min(int(retry_after), 60)
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
            self._sleep(delay)
        raise AssertionError("unreachable")

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
                raise ReplicateError(
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
        self, *, input: dict, record: Path, version: str | None = None, model: str | None = None
    ) -> Any:
        """Create a prediction once, save it to `record`, and wait for its output.

        A marker is created exclusively BEFORE the paid POST, so a crash or timeout can
        never lead to a second charge. If `record` exists, resume it instead.
        """
        record = Path(record)
        if record.exists():
            prediction = self._load(record)
            if prediction["status"] == "creating":
                raise ReplicateError(_unconfirmed(record))
            if prediction["status"] == "succeeded":
                return prediction.get("output")
            if prediction["status"] in ("failed", "canceled"):
                raise ReplicateError(
                    f"Earlier prediction {prediction.get('id')} {prediction['status']}: "
                    f"{prediction.get('error') or 'no reason given'}. "
                    f"Delete {record} to try again (this costs money)."
                )
        else:
            prediction = self._create(input=input, version=version, model=model, record=record)
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
        except (OSError, ValueError) as error:
            raise damaged from error
        if not isinstance(data, dict) or "status" not in data:
            raise damaged
        if data["status"] != "creating" and "id" not in data:
            raise damaged
        return data

    @staticmethod
    def _save(record: Path, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=record.parent, prefix=record.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(data, file, indent=2)
            os.replace(tmp, record)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _create(self, *, input: dict, version: str | None, model: str | None, record: Path) -> dict:
        if version:
            url, body = f"{API}/predictions", {"version": version, "input": input}
        elif model:
            url, body = f"{API}/models/{model}/predictions", {"input": input}
        else:
            raise ValueError("Pass a version or a model.")
        record.parent.mkdir(parents=True, exist_ok=True)
        marker = {"status": "creating", "created_at": datetime.now(UTC).isoformat()}
        try:
            with open(record, "x") as file:  # exclusive: a second runner cannot also pay
                json.dump(marker, file)
        except FileExistsError as error:
            raise ReplicateError(_unconfirmed(record)) from error
        try:
            response = self._http.post(url, headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise ReplicateError(_unconfirmed(record)) from error
        code = response.status_code
        if 400 <= code < 500 and code != 408:
            record.unlink(missing_ok=True)
            raise ReplicateError(_rejection(code, response.text))
        if response.is_error:
            raise ReplicateError(_unconfirmed(record))
        try:
            prediction = response.json()
            if (
                not isinstance(prediction, dict)
                or "id" not in prediction
                or "status" not in prediction
            ):
                raise ValueError("not a prediction")
        except ValueError as error:
            raise ReplicateError(_unconfirmed(record)) from error
        self._save(record, prediction)
        return prediction

    def _wait(self, prediction: dict, record: Path) -> Any:
        polls = 0
        while prediction.get("status") not in TERMINAL:
            if polls >= self._max_polls:
                raise ReplicateError(
                    f"Prediction {prediction.get('id')} is still running (saved in {record}). "
                    "Resume the job to keep waiting; no new charge."
                )
            self._sleep(self._poll_seconds)
            # Built from the id: never send the token to a URL taken from a response.
            url = f"{API}/predictions/{prediction['id']}"
            try:
                prediction = self._retry(lambda: self._get_json(url))  # noqa: B023
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 401:
                    raise ReplicateError(TOKEN_MESSAGE) from error
                raise self._lost(prediction, record) from error
            except httpx.TransportError as error:
                raise self._lost(prediction, record) from error
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


def _unconfirmed(record: Path) -> str:
    return (
        "The request was sent but not confirmed — it may have been charged. "
        f"Check https://replicate.com/predictions. If nothing is there, delete {record} "
        "and run again."
    )


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
