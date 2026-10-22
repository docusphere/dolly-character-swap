"""The only module that talks to Replicate. Paid requests are sent once and persisted."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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
        for attempt in range(attempts):
            try:
                return call()
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt == attempts - 1:
                    raise
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
            self._sleep(2**attempt)
        raise AssertionError("unreachable")

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

        return self._retry(send)

    def latest_version(self, model: str) -> tuple[str, dict]:
        data = self._retry(lambda: self._get_json(f"{API}/models/{model}"))
        version = data["latest_version"]
        props = version["openapi_schema"]["components"]["schemas"]["Input"]["properties"]
        return version["id"], props

    def download(self, url: str, dest: Path) -> Path:
        def fetch() -> bytes:
            response = self._http.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content

        Path(dest).write_bytes(self._retry(fetch))
        return Path(dest)

    # ---- paid call (never retried) -------------------------------------------
    def run(
        self, *, input: dict, record: Path, version: str | None = None, model: str | None = None
    ) -> Any:
        """Create a prediction once, save it to `record`, and wait for its output.

        If `record` exists, resume that prediction instead of paying for a new one.
        """
        if record.exists():
            prediction = json.loads(record.read_text())
            if prediction.get("status") == "succeeded":
                return prediction.get("output")
            if prediction.get("status") in ("failed", "canceled"):
                raise ReplicateError(
                    f"Earlier prediction {prediction.get('id')} {prediction['status']}: "
                    f"{prediction.get('error')}. Delete {record} to try again (this costs money)."
                )
        else:
            prediction = self._create(input=input, version=version, model=model)
            record.write_text(json.dumps(prediction, indent=2))
        return self._wait(prediction, record)

    def _create(self, *, input: dict, version: str | None, model: str | None) -> dict:
        if version:
            url, body = f"{API}/predictions", {"version": version, "input": input}
        elif model:
            url, body = f"{API}/models/{model}/predictions", {"input": input}
        else:
            raise ValueError("Pass a version or a model.")
        response = self._http.post(url, headers=self._headers, json=body)
        if response.is_error:
            raise ReplicateError(
                f"Replicate rejected the request ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    def _wait(self, prediction: dict, record: Path) -> Any:
        polls = 0
        while prediction.get("status") not in TERMINAL:
            if polls >= self._max_polls:
                raise ReplicateError(
                    f"Prediction {prediction.get('id')} is still running (saved in {record}). "
                    "Resume the job to keep waiting; no new charge."
                )
            self._sleep(self._poll_seconds)
            url = prediction["urls"]["get"]
            prediction = self._retry(lambda: self._get_json(url))  # noqa: B023 - called at once
            record.write_text(json.dumps(prediction, indent=2))
            polls += 1
        if prediction["status"] != "succeeded":
            ident, state, error = (
                prediction.get("id"),
                prediction["status"],
                prediction.get("error"),
            )
            raise ReplicateError(f"Prediction {ident} {state}: {error}")
        return prediction.get("output")
