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


class FakePiAPI:
    """Stands in for PiAPIClient: canned links, assets that turn Active, one task per body."""

    def __init__(self, output="https://cdn.piapi.ai/out.mp4", files=None, assets=None):
        self.output = output
        self.files = files if files is not None else {output: b"mp4"}
        self.assets = dict(assets or {})  # asset id -> status PiAPI reports on the next GET
        self.uploads: list[Path] = []
        self.bodies: list[dict] = []
        self.asset_checks: list[str] = []
        self.created = 0  # faces created by this fake (ids asset-1, asset-2, ...)

    def upload_temp(self, path: Path) -> str:
        self.uploads.append(Path(path))
        return f"https://img.theapi.app/temp/{Path(path).name}"

    def create_asset(self, url: str, name: str) -> dict:
        self.created += 1
        asset_id = f"asset-{self.created}"
        self.assets.setdefault(asset_id, "Active")  # preset statuses (e.g. Processing) win
        return {"asset_id": asset_id, "status": "Processing"}

    def get_asset(self, asset_id: str) -> dict | None:
        self.asset_checks.append(asset_id)
        status = self.assets.get(asset_id)
        return None if status is None else {"asset_id": asset_id, "status": status}

    def run(self, *, body, record: Path, on_created=None):
        if record.exists() and json.loads(record.read_text()).get("status") == "succeeded":
            return self.output  # resumed from the record: no new task, no new cost
        self.bodies.append(body)
        task = {"id": f"task-{len(self.bodies)}", "status": "succeeded"}
        record.write_text(json.dumps(task))
        if on_created is not None:
            on_created({"id": task["id"], "status": "starting"})
        return self.output

    def download(self, url: str, dest: Path) -> Path:
        source = self.files[url]
        if isinstance(source, bytes):
            Path(dest).write_bytes(source)
        else:
            shutil.copyfile(source, dest)
        return Path(dest)


class FakeWaveSpeed:
    """Stands in for WaveSpeedClient: canned links, one prediction per body."""

    def __init__(self, output="https://cdn.wavespeed.ai/out.mp4", files=None):
        self.output = output
        self.files = files if files is not None else {output: b"mp4"}
        self.uploads: list[Path] = []
        self.bodies: list[dict] = []

    def upload(self, path: Path) -> str:
        self.uploads.append(Path(path))
        return f"https://cdn.wavespeed.ai/uploads/{Path(path).name}"

    def run(self, *, body, record: Path, on_created=None):
        if record.exists():
            status = json.loads(record.read_text()).get("status")
            if status not in ("creating", "failed"):
                # Like the real client: any live or finished saved prediction is re-checked
                # (and here, finishes); never a new prediction, never a new cost.
                return self.output
        self.bodies.append(body)
        prediction = {"id": f"pred-{len(self.bodies)}", "status": "succeeded"}
        record.write_text(json.dumps(prediction))
        if on_created is not None:
            on_created({"id": prediction["id"], "status": "starting"})
        return self.output

    def download(self, url: str, dest: Path) -> Path:
        source = self.files[url]
        if isinstance(source, bytes):
            Path(dest).write_bytes(source)
        else:
            shutil.copyfile(source, dest)
        return Path(dest)
