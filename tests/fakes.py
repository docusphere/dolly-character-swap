"""Fake providers. They record calls and return canned outputs; no network, no cost."""

import json
import shutil
from pathlib import Path

from character_swap.providers.errors import OutputExpired


class ReplicateOutputExpired(OutputExpired):
    """Stands in for the real client's expiry error."""


class FakeReplicate:
    def __init__(self, outputs=None, files=None, schema=None, fail_download=(), flaky_download=()):
        self.outputs = outputs or {}  # key: version or model -> output
        self.files = files or {}  # url -> local path or bytes to "download"
        self.schema = schema or {"video": {"type": "string", "format": "uri"}}
        self.fail_download = set(fail_download)  # urls whose download raises (expired)
        self.flaky_download = set(flaky_download)  # urls whose download fails transiently
        self.latest_calls = 0
        self.uploads: list[Path] = []
        self.runs: list[dict] = []

    def upload(self, path: Path) -> str:
        self.uploads.append(Path(path))
        return f"https://fake.replicate/{Path(path).name}"

    def latest_version(self, model: str):
        self.latest_calls += 1
        return f"{model}-latest", self.schema

    def run(self, *, input, record: Path, version=None, model=None, on_created=None):
        key = version or model
        if record.exists() and json.loads(record.read_text()).get("status") == "succeeded":
            return self.outputs[key]  # resumed from the record: no new run, no new cost
        self.runs.append({"key": key, "input": input})
        prediction = {"id": f"pred-{len(self.runs)}", "status": "succeeded"}
        record.write_text(json.dumps(prediction))
        if on_created is not None:
            on_created({"id": prediction["id"], "status": "starting"})
        return self.outputs[key]

    def download(self, url: str, dest: Path) -> Path:
        if url in self.fail_download:
            raise ReplicateOutputExpired("expired")
        if url in self.flaky_download:
            raise RuntimeError("connection reset")
        source = self.files[url]
        if isinstance(source, bytes):
            Path(dest).write_bytes(source)
        else:
            shutil.copyfile(source, dest)
        return Path(dest)
