"""The only module that talks to PiAPI. Paid tasks are sent once and persisted."""

from __future__ import annotations

import base64
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
    if word == "failed":
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
