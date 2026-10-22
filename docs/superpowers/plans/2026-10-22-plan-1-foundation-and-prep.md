# Character Swap — Plan 1: Foundation + Preparation Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested `character_swap` Python package and `charswap` CLI that takes a video from upload to "prepared and approved, ready to generate" for all four modes (depth, face_mesh, depth_mesh, direct).

**Architecture:** `src/` layout package. Only `providers/replicate.py` touches the network; stages receive the client (dependency injection) so tests use fakes. Every job lives in `runs/<id>/` with a `state.json` that makes every stage resumable. Processing logic is ported from the legacy flat files (proven in production) into small, readable modules.

**Tech Stack:** Python 3.12, uv, PyAV, NumPy, Pillow, SciPy, httpx, MediaPipe, gradio-client; pytest + pytest-cov (≥ 85 % enforced), respx, ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-character-swap-v1-design.md`

**Follow-on plans:** Plan 2 = generation (hosting, PiAPI Seedance, Wan, face check, audio restore wiring). Plan 3 = web UI, skill, docs, legacy removal.

## Conventions for every task

- Run commands from the repo root: `/Users/franciscojosejimeneznillardkam/Documents/Frank DOCS/Frank Projects/youtube/hf-genjutsu/character-swap` (quote it — the path has spaces).
- Tests: `uv run pytest <path> -q --no-cov` while iterating a single file (the coverage gate applies to the full suite); full suite `uv run pytest`.
- Lint/format before every commit: `uv run ruff format src tests && uv run ruff check --fix src tests` (`--fix` sorts imports: `helpers`/`fakes` count as first-party).
- Stage files **by name**. Never `git add -A` / `git add .`. No attribution lines in commit messages.
- Never commit anything under `_legacy/`, `.env`, `runs/`, `models/`.

## File map (created in this plan)

| File | Responsibility |
|---|---|
| `pyproject.toml`, `.python-version`, `uv.lock` | deps, tools, `charswap` entry point |
| `CLAUDE.md`, `activity.md`, `docs/scratchpad.md` | project harness (Frank's guide) |
| `src/character_swap/__init__.py` | version |
| `src/character_swap/config.py` | settings from env + `.env`, fail-fast `require()` |
| `src/character_swap/jobs.py` | job folders, `state.json`, stage tracking |
| `src/character_swap/media/video.py` | frame IO, `VideoWriter`, `normalize`, `middle_frame` |
| `src/character_swap/media/audio.py` | read/write audio, embed samples, copy audio, restore original soundtrack |
| `src/character_swap/media/composite.py` | mask cleanup + depth-subject-over-background composite |
| `src/character_swap/providers/replicate.py` | the only Replicate client |
| `src/character_swap/stages/separate.py` | Demucs vocals/music |
| `src/character_swap/stages/pitch.py` | Rubber Band +3 semitones |
| `src/character_swap/stages/transcribe.py` | Whisper transcript |
| `src/character_swap/stages/describe.py` | scene description (no people) |
| `src/character_swap/stages/depth.py` | depth video (Replicate / HF Space) |
| `src/character_swap/stages/masks.py` | SAM 3 raw masks |
| `src/character_swap/stages/face_mesh.py` | MediaPipe tracking + mesh drawing |
| `src/character_swap/review.py` | hash-locked approval gate |
| `src/character_swap/costs.py` | price table, estimates, spend log |
| `src/character_swap/prompts.py` | builds the editable generation prompt |
| `src/character_swap/pipeline.py` | runs stages per mode; resumable; RGB repair |
| `src/character_swap/cli.py` | `charswap prepare/status/review/repair/prompt/doctor` |
| `tests/helpers.py`, `tests/fakes.py`, `tests/conftest.py` | synthetic media + fake providers |
| `tests/test_*.py` | one per module |

---

### Task 1: Scaffold the package, park legacy code, wire tooling

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/character_swap/__init__.py`, `tests/test_smoke.py`
- Move: all legacy files into `_legacy/` (kept untracked until Plan 3 deletes them)
- Modify: `.gitignore`

- [ ] **Step 1: Park the legacy code** so it can't collide with the new package or pytest discovery.

```bash
mkdir -p _legacy
mv app.py audio_workflow.py config.py doctor.py face_mesh.py launch.py mask_review.py media_host.py \
   mesh_worker.py pipeline.py raw_masks.py seedance_bridge.py webhook.py workflow.py test_composite.py \
   requirements.txt setup.sh setup-face-mesh.sh start.command AGENTS.md THIRD-PARTY.md static tests .agents _legacy/
find docs -maxdepth 1 -name '*.md' ! -name 'ui-ux-best-practices.md' -exec mv {} _legacy/ \;  # old top-level docs
mv .env.example _legacy/env.example.old
rm -rf .venv
```

- [ ] **Step 2: Replace `.gitignore`**

```gitignore
# secrets
.env
.env.*
!.env.example
*.pem
*.key

# environments and caches
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/

# runtime data
runs/
models/
*.log
.DS_Store

# legacy code being ported (deleted in Plan 3, never committed)
_legacy/
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "character-swap"
version = "0.1.0"
description = "Swap a person in a short video for your own character, keeping motion, timing and soundtrack."
readme = "README.md"
license = "MIT"
requires-python = ">=3.12,<3.13"
dependencies = [
  "av>=17,<18",
  "numpy>=2.2",
  "pillow>=12",
  "scipy>=1.15",
  "httpx>=0.28",
  "mediapipe>=1.1",
  "gradio-client>=2.7",
]

[project.scripts]
charswap = "character_swap.cli:main"

[dependency-groups]
dev = ["pytest>=9", "pytest-cov>=7", "respx>=0.23", "ruff>=0.17"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/character_swap"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not live' --cov=character_swap --cov-branch --cov-report=term-missing --cov-fail-under=85"
markers = [
  "live: calls real paid APIs; never part of the default run",
  "rubberband: needs the rubberband binary on PATH",
]

[tool.coverage.run]
source = ["character_swap"]
```

- [ ] **Step 4: Package + smoke test**

`src/character_swap/__init__.py`:
```python
"""Character Swap: replace a person in a short video while keeping motion, timing and audio."""

__version__ = "0.1.0"
```

`tests/test_smoke.py`:
```python
import character_swap


def test_package_has_version():
    assert character_swap.__version__ == "0.1.0"
```

- [ ] **Step 5: Install and run**

```bash
uv python pin 3.12
uv sync
uv run pytest -q
```
Expected: `1 passed`, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add .gitignore .python-version pyproject.toml uv.lock src/character_swap/__init__.py tests/test_smoke.py
git commit -m "build: scaffold character_swap package with uv, ruff and pytest coverage gate"
```

---

### Task 2: Project harness docs (CLAUDE.md, activity.md, scratchpad)

**Files:** Create `CLAUDE.md`, `activity.md`, `docs/scratchpad.md`, `.env.example`

- [ ] **Step 1: `CLAUDE.md`**

```markdown
# Character Swap

Local app + CLI that swaps a person in a short video for a chosen character, keeping motion, timing and the original soundtrack. Generators: PiAPI Seedance 2.5 (real faces, 1080p) and Wan 2.2 Animate on Replicate (open source).

## Stack
Python 3.12 · uv · PyAV/NumPy/Pillow · httpx · MediaPipe · pytest (+cov ≥ 85 %) · ruff. FastAPI UI arrives in Plan 3.

## Commands
- Install: `uv sync`
- Tests (with coverage gate): `uv run pytest`
- Lint/format: `uv run ruff format src tests && uv run ruff check --fix src tests`
- CLI: `uv run charswap --help`

## Layout
- `src/character_swap/providers/` is the ONLY code that calls the network.
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
- Jobs live in `runs/<id>/` with `state.json` (resumable).

## Hard rules
- Never auto-retry a paid request. Persist provider IDs before waiting.
- Every paid action shows an estimate and needs confirmation.
- Tests never call paid APIs (use `tests/fakes.py`); live tests are `@pytest.mark.live`.
- Stage files by name; never `git add -A`. No attribution lines in commits.
- Never copy anything from `_legacy/` assets or mention the original author's brands.

## Docs (read on demand)
Spec: `docs/superpowers/specs/` · Plans: `docs/superpowers/plans/` · Journal: `activity.md` · Checklist: `docs/scratchpad.md` · UI guide: `docs/ui-ux-best-practices.md`
```

- [ ] **Step 2: `activity.md`**

```markdown
# Activity journal

## 2026-10-22 — Session 1: fork, design, plan
- **Goal:** Turn a friend's MIT character-swap workflow into Frank's own project.
- **Decisions:** PiAPI Seedance 2.5 less-restriction as main generator (real faces via reviewed asset library, draft→1080p promote); Wan 2.2 Animate on Replicate as open-source option; Seedance-on-Replicate dropped (blocks real faces). R2 presigned hosting + tmpfiles fallback. All three prep modes kept. uv + src layout + 85 % coverage gate.
- **Lessons:** Real-face access is granted per provider account by ByteDance ("Advanced Creation Rights", enterprise only); resellers expose it under different names.
```

- [ ] **Step 3: `docs/scratchpad.md`**

```markdown
# Scratchpad

## Active work
- [ ] Plan 1 — foundation + preparation pipeline
- [ ] Plan 2 — generation (hosting, PiAPI, Wan, face check)
- [ ] Plan 3 — web UI, skill, docs, legacy removal

## Backlog
- BytePlus direct provider (if enterprise access granted)
- Hosted demo inside Dolly (separate spec)
- Multi-person face mesh
```

- [ ] **Step 4: `.env.example`**

```dotenv
# Copy to .env and fill in. Never share a filled .env.
# Preparation steps + Wan generator: https://replicate.com/account/api-tokens
REPLICATE_API_TOKEN=
# Seedance generator (Plan 2): https://piapi.ai
PIAPI_API_KEY=
# Optional: Hugging Face depth alternative
HF_TOKEN=
# Optional private hosting (Plan 2). Leave blank to use tmpfiles (public, ~60 min).
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md activity.md docs/scratchpad.md .env.example
git commit -m "docs: add project harness (CLAUDE.md, journal, scratchpad, env template)"
```

---

### Task 3: Test helpers and fakes

**Files:** Create `tests/helpers.py`, `tests/fakes.py`, `tests/conftest.py`

- [ ] **Step 1: `tests/helpers.py`** — synthetic media used by many tests

```python
"""Synthetic media builders for tests. Everything is tiny so tests stay fast."""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

RATE = 44100


def solid(color, size=(64, 64)) -> np.ndarray:
    width, height = size
    return np.full((height, width, 3), color, dtype=np.uint8)


def tone(seconds: float, freq: float = 440.0) -> np.ndarray:
    n = int(RATE * seconds)
    wave = (np.sin(np.arange(n) * 2 * np.pi * freq / RATE) * 0.1).astype(np.float32)
    return np.tile(wave, (2, 1))


def write_video(path: Path, frames, fps: int = 24, audio_seconds: float | None = None) -> Path:
    frames = list(frames)
    height, width = frames[0].shape[:2]
    with av.open(str(path), "w") as out:
        video = out.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        audio = None
        if audio_seconds:
            audio = out.add_stream("aac", rate=RATE)
            audio.layout = "stereo"
        for i, array in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            frame.pts, frame.time_base = i, Fraction(1, fps)
            for packet in video.encode(frame):
                out.mux(packet)
        for packet in video.encode():
            out.mux(packet)
        if audio:
            samples = tone(audio_seconds)
            for i in range(0, samples.shape[1], 1024):
                chunk = np.ascontiguousarray(samples[:, i : i + 1024])
                frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                frame.sample_rate, frame.pts = RATE, i
                for packet in audio.encode(frame):
                    out.mux(packet)
            for packet in audio.encode():
                out.mux(packet)
    return path


def write_wav(path: Path, samples: np.ndarray) -> Path:
    with av.open(str(path), "w") as out:
        stream = out.add_stream("pcm_s16le", rate=RATE)
        stream.layout = "stereo"
        for i in range(0, samples.shape[1], 4096):
            chunk = np.ascontiguousarray(samples[:, i : i + 4096].astype(np.float32))
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate, frame.pts = RATE, i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    return path


def write_mask_pngs(folder: Path, count: int, box=(16, 16, 48, 48), size=(64, 64)) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = box
    for i in range(count):
        mask = np.zeros((size[1], size[0]), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255
        Image.fromarray(mask).save(folder / f"mask_{i:05d}.png")
    return folder
```

- [ ] **Step 2: `tests/fakes.py`** — stand-in for `ReplicateClient`

```python
"""Fake providers. They record calls and return canned outputs; no network, no cost."""

import json
import shutil
from pathlib import Path


class FakeReplicate:
    def __init__(self, outputs=None, files=None, schema=None):
        self.outputs = outputs or {}  # key: version or model -> output
        self.files = files or {}  # url -> local path or bytes to "download"
        self.schema = schema or {"video": {"type": "string", "format": "uri"}}
        self.uploads: list[Path] = []
        self.runs: list[dict] = []

    def upload(self, path: Path) -> str:
        self.uploads.append(Path(path))
        return f"https://fake.replicate/{Path(path).name}"

    def latest_version(self, model: str):
        return f"{model}-latest", self.schema

    def run(self, *, input, record: Path, version=None, model=None):
        key = version or model
        self.runs.append({"key": key, "input": input})
        record.write_text(json.dumps({"id": f"pred-{len(self.runs)}", "status": "succeeded"}))
        return self.outputs[key]

    def download(self, url: str, dest: Path) -> Path:
        source = self.files[url]
        if isinstance(source, bytes):
            Path(dest).write_bytes(source)
        else:
            shutil.copyfile(source, dest)
        return Path(dest)
```

- [ ] **Step 3: `tests/conftest.py`**

```python
import shutil

import pytest

from fakes import FakeReplicate


@pytest.fixture
def fake_replicate():
    return FakeReplicate()


@pytest.fixture
def runs_dir(tmp_path):
    path = tmp_path / "runs"
    path.mkdir()
    return path


def pytest_collection_modifyitems(config, items):
    if shutil.which("rubberband"):
        return
    skip = pytest.mark.skip(reason="rubberband binary not installed")
    for item in items:
        if "rubberband" in item.keywords:
            item.add_marker(skip)
```

- [ ] **Step 4: Verify collection still works** — `uv run pytest -q` → `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/helpers.py tests/fakes.py tests/conftest.py
git commit -m "test: add synthetic media helpers and fake Replicate client"
```

---

### Task 4: `config.py` — settings with fail-fast keys

**Files:** Create `src/character_swap/config.py`, `tests/test_config.py`

- [ ] **Step 1: Failing tests**

```python
import pytest

from character_swap.config import MissingSettingError, Settings, read_env_file


def test_reads_env_file_ignoring_comments_and_quotes(tmp_path):
    (tmp_path / ".env").write_text('# comment\nA=1\nB="two"\n\nC=\'three\'\nBROKEN\n')
    assert read_env_file(tmp_path / ".env") == {"A": "1", "B": "two", "C": "three"}


def test_missing_env_file_is_empty(tmp_path):
    assert read_env_file(tmp_path / ".env") == {}


def test_environment_overrides_env_file(tmp_path):
    (tmp_path / ".env").write_text("REPLICATE_API_TOKEN=from-file\n")
    settings = Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "from-env"})
    assert settings.require("REPLICATE_API_TOKEN") == "from-env"


def test_require_missing_names_variable_and_where_to_get_it(tmp_path):
    settings = Settings.load(root=tmp_path, environ={})
    with pytest.raises(MissingSettingError, match="REPLICATE_API_TOKEN.*replicate.com"):
        settings.require("REPLICATE_API_TOKEN")


def test_runs_dir_defaults_to_project_runs_and_can_be_overridden(tmp_path):
    assert Settings.load(root=tmp_path, environ={}).runs_dir == tmp_path / "runs"
    custom = Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(tmp_path / "x")})
    assert custom.runs_dir == tmp_path / "x"


def test_hosting_mode_is_r2_only_when_all_r2_keys_present(tmp_path):
    keys = {"R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "b", "R2_SECRET_ACCESS_KEY": "c"}
    assert Settings.load(root=tmp_path, environ=keys).hosting_mode == "tmpfiles"
    keys["R2_BUCKET"] = "d"
    assert Settings.load(root=tmp_path, environ=keys).hosting_mode == "r2"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py -q --no-cov` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/character_swap/config.py`**

```python
"""Settings from the environment and the project's .env file. Environment wins."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
R2_KEYS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
WHERE_TO_GET = {
    "REPLICATE_API_TOKEN": "https://replicate.com/account/api-tokens",
    "PIAPI_API_KEY": "https://piapi.ai (Workspace > API Keys)",
    "HF_TOKEN": "https://huggingface.co/settings/tokens",
}


class MissingSettingError(RuntimeError):
    """A required key is not configured."""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    values: Mapping[str, str]
    runs_dir: Path
    root: Path

    @classmethod
    def load(cls, root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None) -> Settings:
        merged = read_env_file(root / ".env")
        merged.update({k: v for k, v in (os.environ if environ is None else environ).items() if v})
        runs_dir = Path(merged.get("CHARSWAP_RUNS_DIR") or root / "runs")
        return cls(values=merged, runs_dir=runs_dir, root=root)

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name) or default

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            where = WHERE_TO_GET.get(name, "your provider dashboard")
            raise MissingSettingError(f"{name} is not set. Add it to .env (get it at {where}).")
        return value

    @property
    def hosting_mode(self) -> str:
        return "r2" if all(self.get(key) for key in R2_KEYS) else "tmpfiles"
```

- [ ] **Step 4: Run** `uv run pytest tests/test_config.py -q --no-cov` → `6 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/config.py tests/test_config.py
git commit -m "feat: add settings loader with fail-fast key checks"
```

---

### Task 5: `jobs.py` — job folders and resumable state

**Files:** Create `src/character_swap/jobs.py`, `tests/test_jobs.py`

- [ ] **Step 1: Failing tests**

```python
import json

import pytest

from character_swap.jobs import Job, JobError


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "in.mp4"
    path.write_bytes(b"video")
    return path


def test_create_copies_upload_and_writes_state(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth", subject="person")
    assert len(job.id) == 32
    assert job.path("upload.mp4").read_bytes() == b"video"
    state = json.loads(job.path("state.json").read_text())
    assert state["generator"] == "seedance" and state["mode"] == "depth"
    assert state["stages"] == {}
    assert isinstance(state["seed"], int)


def test_wan_requires_direct_mode_and_character(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="does not work with wan"):
        Job.create(runs_dir, video, generator="wan", mode="depth")
    with pytest.raises(JobError, match="needs a character image"):
        Job.create(runs_dir, video, generator="wan", mode="direct")
    face = tmp_path / "me.jpg"
    face.write_bytes(b"jpg")
    job = Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert job.state["character_primary"] == "character-primary.jpg"
    assert job.path("character-primary.jpg").read_bytes() == b"jpg"


def test_unknown_generator_and_missing_video(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="Unknown generator"):
        Job.create(runs_dir, video, generator="sora", mode="depth")
    with pytest.raises(JobError, match="Video not found"):
        Job.create(runs_dir, tmp_path / "nope.mp4", generator="seedance", mode="depth")


def test_load_validates_id_and_existence(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="face_mesh")
    assert Job.load(runs_dir, job.id).mode == "face_mesh"
    with pytest.raises(JobError, match="32 lowercase hex"):
        Job.load(runs_dir, "../etc")
    with pytest.raises(JobError, match="No job"):
        Job.load(runs_dir, "0" * 32)


def test_stage_tracking_and_update(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert not job.stage_done("normalize")
    job.mark_stage("normalize", frames=48)
    assert job.stage_done("normalize")
    assert job.state["stages"]["normalize"]["frames"] == 48
    job.reset_stages("normalize")
    assert not job.stage_done("normalize")
    job.update(transcript="hello")
    assert job.state["transcript"] == "hello"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_jobs.py -q --no-cov` → FAIL.

- [ ] **Step 3: Implement `src/character_swap/jobs.py`**

```python
"""Job folders: one folder per video holding every artifact plus state.json."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODES_BY_GENERATOR = {"seedance": ("depth", "face_mesh", "depth_mesh"), "wan": ("direct",)}
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class JobError(ValueError):
    """The job request or job folder is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    id: str
    root: Path

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        source: Path,
        *,
        generator: str,
        mode: str,
        subject: str = "person",
        depth_provider: str = "replicate",
        character: Path | None = None,
    ) -> Job:
        if generator not in MODES_BY_GENERATOR:
            choices = ", ".join(MODES_BY_GENERATOR)
            raise JobError(f"Unknown generator {generator!r}. Use one of: {choices}.")
        if mode not in MODES_BY_GENERATOR[generator]:
            choices = ", ".join(MODES_BY_GENERATOR[generator])
            raise JobError(f"Mode {mode!r} does not work with {generator}. Choose: {choices}.")
        if generator == "wan" and character is None:
            raise JobError("Wan needs a character image when the job is created.")
        source = Path(source)
        if not source.is_file():
            raise JobError(f"Video not found: {source}")

        job_id = uuid.uuid4().hex
        root = Path(runs_dir) / job_id
        root.mkdir(parents=True)
        shutil.copy2(source, root / "upload.mp4")
        state: dict[str, Any] = {
            "id": job_id,
            "generator": generator,
            "mode": mode,
            "subject": subject,
            "depth_provider": depth_provider,
            "seed": secrets.randbelow(2**31),
            "created_at": _now(),
            "stages": {},
        }
        if character is not None:
            name = f"character-primary{Path(character).suffix.lower()}"
            shutil.copy2(character, root / name)
            state["character_primary"] = name
        job = cls(job_id, root)
        job._write(state)
        return job

    @classmethod
    def load(cls, runs_dir: Path, job_id: str) -> Job:
        if not _JOB_ID.match(job_id):
            raise JobError("Job IDs are 32 lowercase hex characters.")
        root = Path(runs_dir) / job_id
        if not (root / "state.json").is_file():
            raise JobError(f"No job {job_id} in {runs_dir}.")
        return cls(job_id, root)

    def path(self, name: str) -> Path:
        return self.root / name

    @property
    def state(self) -> dict[str, Any]:
        return json.loads((self.root / "state.json").read_text())

    @property
    def generator(self) -> str:
        return self.state["generator"]

    @property
    def mode(self) -> str:
        return self.state["mode"]

    def update(self, **changes: Any) -> dict[str, Any]:
        data = self.state
        data.update(changes)
        self._write(data)
        return data

    def stage_done(self, name: str) -> bool:
        return self.state["stages"].get(name, {}).get("status") == "done"

    def mark_stage(self, name: str, status: str = "done", **info: Any) -> None:
        data = self.state
        data["stages"][name] = {"status": status, "at": _now(), **info}
        self._write(data)

    def reset_stages(self, *names: str) -> None:
        data = self.state
        for name in names:
            data["stages"].pop(name, None)
        self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        handle, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(handle, "w") as file:
            json.dump(data, file, indent=2)
        os.replace(temporary, self.root / "state.json")
```

- [ ] **Step 4: Run** → `5 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/jobs.py tests/test_jobs.py
git commit -m "feat: add job folders with atomic, resumable state"
```

---

### Task 6: `media/video.py` — frames, writer, normalize

**Files:** Create `src/character_swap/media/__init__.py` (empty docstring), `src/character_swap/media/video.py`, `tests/test_video.py`

- [ ] **Step 1: Failing tests**

```python
import numpy as np
import pytest
from helpers import solid, write_video

from character_swap.media.video import (
    ClipLengthError,
    VideoWriter,
    count_frames,
    iter_frames,
    middle_frame,
    normalize,
)


def test_writer_roundtrip(tmp_path):
    with VideoWriter(tmp_path / "out.mp4", 64, 64) as writer:
        for _ in range(5):
            writer.write(solid((200, 10, 10)))
    assert count_frames(tmp_path / "out.mp4") == 5
    first = next(iter_frames(tmp_path / "out.mp4"))
    assert first[32, 32, 0] > 180


def test_normalize_resamples_to_24fps_and_caps_size(tmp_path):
    src = write_video(tmp_path / "src.mp4", [solid((90, 90, 90), (1280, 720))] * 60, fps=30)
    meta = normalize(src, tmp_path / "norm.mp4", min_seconds=1, max_seconds=30)
    assert meta == {"frames": 48, "fps": 24, "width": 960, "height": 540, "seconds": 2.0}
    assert count_frames(tmp_path / "norm.mp4") == 48


def test_normalize_rejects_out_of_range_lengths(tmp_path):
    src = write_video(tmp_path / "src.mp4", [solid((1, 1, 1))] * 48)
    with pytest.raises(ClipLengthError, match="at most 1"):
        normalize(src, tmp_path / "a.mp4", min_seconds=0, max_seconds=1)
    with pytest.raises(ClipLengthError, match="at least 4"):
        normalize(src, tmp_path / "b.mp4", min_seconds=4, max_seconds=30)


def test_middle_frame_returns_image(tmp_path):
    frames = [solid((0, 0, 0))] * 3 + [solid((250, 250, 250))] * 3
    src = write_video(tmp_path / "src.mp4", frames)
    image = middle_frame(src)
    assert np.asarray(image)[10, 10, 0] > 200
```

- [ ] **Step 2: Run** `uv run pytest tests/test_video.py -q --no-cov` → FAIL.

- [ ] **Step 3: Implement `src/character_swap/media/video.py`**

```python
"""Video helpers: decode frames, encode H.264, and normalize sources to 24 fps."""

from __future__ import annotations

from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

FPS = 24
MAX_SIDE = 960


class ClipLengthError(ValueError):
    """The clip is too short or too long for the chosen generator."""


def iter_frames(path: Path) -> Iterator[np.ndarray]:
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            yield frame.to_ndarray(format="rgb24")


def count_frames(path: Path) -> int:
    return sum(1 for _ in iter_frames(path))


def middle_frame(path: Path) -> Image.Image:
    frames = list(iter_frames(path))
    return Image.fromarray(frames[len(frames) // 2])


class VideoWriter:
    """Write RGB frames to an H.264 MP4. Use as a context manager."""

    def __init__(self, path: Path, width: int, height: int, fps: int = FPS):
        self._container = av.open(str(path), "w")
        self._stream = self._container.add_stream("libx264", rate=fps)
        self._stream.width, self._stream.height = width, height
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = {"crf": "18", "preset": "fast"}
        self._time_base = Fraction(1, fps)
        self._index = 0

    def write(self, frame: np.ndarray) -> None:
        video_frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
        video_frame.pts, video_frame.time_base = self._index, self._time_base
        for packet in self._stream.encode(video_frame):
            self._container.mux(packet)
        self._index += 1

    def close(self) -> None:
        for packet in self._stream.encode():
            self._container.mux(packet)
        self._container.close()

    def __enter__(self) -> VideoWriter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _seconds(container, stream) -> float:
    if stream.duration:
        return float(stream.duration * stream.time_base)
    return float(container.duration / av.time_base)


def normalize(src: Path, dst: Path, *, min_seconds: float, max_seconds: float) -> dict:
    """Re-time to 24 fps (holding the nearest earlier frame) and fit within 960 px."""
    with av.open(str(src)) as container:
        stream = container.streams.video[0]
        seconds = _seconds(container, stream)
        if seconds > max_seconds + 0.05:
            raise ClipLengthError(
                f"Clip is {seconds:.1f}s; this generator accepts at most {max_seconds:g}s. Trim it."
            )
        if seconds < min_seconds - 0.05:
            raise ClipLengthError(
                f"Clip is {seconds:.1f}s; this generator needs at least {min_seconds:g}s."
            )
        decoded = container.decode(video=0)
        current = next(decoded)
        start = float(current.time or 0)
        upcoming = next(decoded, None)
        scale = min(1.0, MAX_SIDE / max(current.width, current.height))
        width = max(2, int(current.width * scale) // 2 * 2)
        height = max(2, int(current.height * scale) // 2 * 2)
        count = round(seconds * FPS)
        with VideoWriter(dst, width, height) as writer:
            for index in range(count):
                moment = start + index / FPS
                while upcoming is not None and float(upcoming.time) <= moment:
                    current, upcoming = upcoming, next(decoded, None)
                image = current.to_image().resize((width, height), Image.Resampling.LANCZOS)
                writer.write(np.asarray(image))
    return {"frames": count, "fps": FPS, "width": width, "height": height, "seconds": count / FPS}
```

`src/character_swap/media/__init__.py`:
```python
"""Local video and audio processing (no network)."""
```

- [ ] **Step 4: Run** → `4 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/media/__init__.py src/character_swap/media/video.py tests/test_video.py
git commit -m "feat: add video IO and 24 fps normalization with length limits"
```

---

### Task 7: `media/audio.py` — audio IO, embedding, original-soundtrack restore

**Files:** Create `src/character_swap/media/audio.py`, `tests/test_audio.py`

- [ ] **Step 1: Failing tests** (ports the legacy byte-exact restore test)

```python
import av
import pytest
from helpers import solid, tone, write_video

from character_swap.media.audio import (
    DurationMismatchError,
    NoAudioError,
    embed_samples,
    read_audio,
    restore_original_audio,
    write_wav,
)


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def test_wav_roundtrip(tmp_path):
    samples = tone(0.5)
    write_wav(tmp_path / "a.wav", samples)
    back = read_audio(tmp_path / "a.wav")
    assert back.shape[0] == 2 and abs(back.shape[1] - samples.shape[1]) < 1100


def test_read_audio_without_track_raises(tmp_path):
    silent = write_video(tmp_path / "v.mp4", [solid((0, 0, 0))] * 4)
    with pytest.raises(NoAudioError):
        read_audio(silent)


def test_embed_samples_adds_aac_track(tmp_path):
    video = write_video(tmp_path / "v.mp4", [solid((10, 10, 10))] * 24)
    out = embed_samples(video, tone(1.0), tmp_path / "out.mp4")
    with av.open(str(out)) as container:
        assert container.streams.audio and container.streams.video


def test_restore_copies_original_audio_packets_exactly(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 24)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original)


def test_restore_holds_last_frame_for_small_gap(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original)


def test_restore_refuses_large_mismatch(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 48, audio_seconds=2)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 24)
    with pytest.raises(DurationMismatchError):
        restore_original_audio(generated, original, tmp_path / "final.mp4")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/media/audio.py`**

```python
"""Audio helpers: read/write stereo float audio, embed it in videos, restore originals."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from character_swap.media.video import iter_frames

RATE = 44100
HOLD_GAP = (0.25, 0.5)  # seconds: short provider rounding gaps fixed by holding the last frame
MAX_MISMATCH = 0.25


class NoAudioError(ValueError):
    """The file has no audio track."""


class DurationMismatchError(ValueError):
    """Generated video length differs too much from the original audio."""


def read_audio(path: Path) -> np.ndarray:
    """Return float32 stereo samples shaped (2, n) at 44.1 kHz."""
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
    parts = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise NoAudioError(f"{Path(path).name} has no audio track.")
        for frame in container.decode(audio=0):
            parts.extend(chunk.to_ndarray() for chunk in resampler.resample(frame))
        parts.extend(chunk.to_ndarray() for chunk in resampler.resample(None))
    return np.concatenate(parts, axis=1)


def _encode_samples(container, stream, samples: np.ndarray) -> None:
    for start in range(0, samples.shape[1], 1024):
        chunk = np.ascontiguousarray(samples[:, start : start + 1024].astype(np.float32))
        frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
        frame.sample_rate, frame.pts = RATE, start
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)


def write_wav(path: Path, samples: np.ndarray) -> Path:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("pcm_s16le", rate=RATE)
        stream.layout = "stereo"
        _encode_samples(container, stream, samples)
    return Path(path)


def _copy_video_packets(source, output):
    stream = output.add_stream_from_template(source.streams.video[0])
    for packet in source.demux(video=0):
        if packet.dts is not None:
            packet.stream = stream
            output.mux(packet)


def embed_samples(video: Path, samples: np.ndarray, dst: Path) -> Path:
    """Copy the video stream untouched and add `samples` as an AAC track."""
    with av.open(str(video)) as source, av.open(str(dst), "w") as output:
        audio = output.add_stream("aac", rate=RATE)
        audio.layout, audio.bit_rate = "stereo", 192000
        _copy_video_packets(source, output)
        _encode_samples(output, audio, samples)
    return Path(dst)


def _video_seconds(path: Path) -> float:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration:
            return float(stream.duration * stream.time_base)
        return float(container.duration / av.time_base)


def _audio_seconds(path: Path) -> float:
    with av.open(str(path)) as container:
        return float(container.duration / av.time_base)


def _hold_last_frame(generated: Path, seconds: float, dst: Path) -> Path:
    frames = list(iter_frames(generated))
    with av.open(str(generated)) as probe:
        rate = probe.streams.video[0].average_rate or Fraction(24, 1)
    height, width = frames[0].shape[:2]
    with av.open(str(dst), "w") as output:
        stream = output.add_stream("libx264", rate=rate)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"crf": "18"}
        for index in range(round(seconds * float(rate))):
            frame = av.VideoFrame.from_ndarray(frames[min(index, len(frames) - 1)], format="rgb24")
            frame.pts, frame.time_base = index, 1 / rate
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return dst


def restore_original_audio(generated: Path, original: Path, dst: Path) -> Path:
    """Replace the generated audio with the original's audio packets, byte for byte."""
    gap = _audio_seconds(original) - _video_seconds(generated)
    if HOLD_GAP[0] < gap <= HOLD_GAP[1]:
        generated = _hold_last_frame(generated, _audio_seconds(original), dst.with_suffix(".held.mp4"))
        gap = _audio_seconds(original) - _video_seconds(generated)
    if abs(gap) > MAX_MISMATCH:
        raise DurationMismatchError(
            f"Generated video differs from the original audio by {gap:+.2f}s; "
            "refusing to trim, loop or stretch the soundtrack."
        )
    with av.open(str(generated)) as video, av.open(str(original)) as source, av.open(
        str(dst), "w"
    ) as output:
        if not source.streams.audio:
            raise NoAudioError("The original video has no audio track.")
        audio = output.add_stream_from_template(source.streams.audio[0])
        _copy_video_packets(video, output)
        for packet in source.demux(audio=0):
            if packet.dts is not None:
                packet.stream = audio
                output.mux(packet)
    return Path(dst)
```

- [ ] **Step 4: Run** → `6 passed`. If `test_restore_holds_last_frame_for_small_gap` fails because the AAC container duration differs slightly from `34/24`, print both durations and adjust the frame counts so `gap` lands at ~0.33 s; do not loosen `MAX_MISMATCH`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/media/audio.py tests/test_audio.py
git commit -m "feat: add audio IO, vocal embedding and byte-exact soundtrack restore"
```

---

### Task 8: `media/composite.py` — mask cleanup + compositing

**Files:** Create `src/character_swap/media/composite.py`, `tests/test_composite.py`

- [ ] **Step 1: Failing tests**

```python
import numpy as np
import pytest
from helpers import solid, write_mask_pngs, write_video
from PIL import Image

from character_swap.media.composite import (
    CompositeError,
    clean_border_fragments,
    composite_depth_subject,
)
from character_swap.media.video import iter_frames


def test_depth_subject_over_original_background(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((180, 40, 40))] * 4)
    depth = write_video(tmp_path / "depth.mp4", [solid((100, 100, 100))] * 4)
    masks = write_mask_pngs(tmp_path / "masks", 4)
    result = composite_depth_subject(original, depth, masks, tmp_path)
    assert result.frames == 4 and result.mask_pixels > 0
    frame = next(iter_frames(tmp_path / "composite-silent.mp4"))
    assert np.max(np.abs(frame[32, 32].astype(int) - 100)) < 10  # subject = depth
    assert frame[2, 2, 0] > 160 and frame[2, 2, 1] < 60  # background = original
    assert (tmp_path / "mask.mp4").exists()


def test_misaligned_inputs_refused(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    depth = write_video(tmp_path / "depth.mp4", [solid((0, 0, 0))] * 3)
    masks = write_mask_pngs(tmp_path / "masks", 2)
    with pytest.raises(CompositeError, match="Frame alignment"):
        composite_depth_subject(original, depth, masks, tmp_path)


def test_empty_masks_refused(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    depth = write_video(tmp_path / "depth.mp4", [solid((0, 0, 0))] * 2)
    masks = write_mask_pngs(tmp_path / "masks", 2, box=(0, 0, 0, 0))
    with pytest.raises(CompositeError, match="no subject"):
        composite_depth_subject(original, depth, masks, tmp_path)


def test_clean_removes_thin_edge_slivers_but_keeps_large_subjects(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:20, 0:2] = 255  # thin sliver touching left edge -> removed
    mask[40:90, 60:100] = 255  # big subject touching right edge -> kept
    Image.fromarray(mask).save(raw / "mask_00000.png")
    out = tmp_path / "clean"
    report = clean_border_fragments(raw, out)
    cleaned = np.asarray(Image.open(out / "mask_00000.png"))
    assert cleaned[15, 1] == 0 and cleaned[60, 99] == 255
    assert report[0]["removed_border_pixels"] == 20
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/media/composite.py`**

```python
"""Composite the depth-rendered subject over the original background using raw masks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import label

from character_swap.media.video import VideoWriter, count_frames, iter_frames


class CompositeError(ValueError):
    """Inputs cannot be composited safely."""


@dataclass(frozen=True)
class CompositeResult:
    frames: int
    mask_pixels: int


def clean_border_fragments(src_dir: Path, dst_dir: Path) -> list[dict]:
    """Drop tiny, narrow blobs touching the left/right edge; keep large edge subjects."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for path in sorted(src_dir.glob("mask_*.png")):
        mask = np.asarray(Image.open(path).convert("L")) > 127
        labels, _ = label(mask)
        height, width = mask.shape
        removed = 0
        edge_ids = (set(labels[:, 0]) | set(labels[:, -1])) - {0}
        for ident in edge_ids:
            ys, xs = np.where(labels == ident)
            small = len(xs) < height * width * 0.03
            narrow = xs.max() - xs.min() + 1 <= max(6, int(width * 0.05))
            if small and narrow:
                mask[labels == ident] = False
                removed += len(xs)
        Image.fromarray(mask.astype(np.uint8) * 255).save(dst_dir / path.name)
        report.append(
            {"frame": path.stem, "removed_border_pixels": removed, "mask_pixels": int(mask.sum())}
        )
    return report


def composite_depth_subject(
    original: Path, depth: Path, masks_dir: Path, out_dir: Path
) -> CompositeResult:
    """Write mask.mp4 and composite-silent.mp4 into out_dir."""
    mask_files = sorted(masks_dir.glob("mask_*.png"))
    counts = [count_frames(original), count_frames(depth), len(mask_files)]
    if len(set(counts)) != 1:
        raise CompositeError(f"Frame alignment failed (original/depth/masks): {counts}.")
    indices = [int(p.stem.split("_")[-1]) for p in mask_files]
    if indices != list(range(counts[0])):
        raise CompositeError("Frame alignment failed: missing or shifted mask frame numbers.")

    first = next(iter_frames(original))
    height, width = first.shape[:2]
    area = 0
    with (
        VideoWriter(out_dir / "mask.mp4", width, height) as mask_out,
        VideoWriter(out_dir / "composite-silent.mp4", width, height) as comp_out,
    ):
        for rgb, depth_frame, mask_path in zip(
            iter_frames(original), iter_frames(depth), mask_files, strict=True
        ):
            mask = np.asarray(Image.open(mask_path).convert("L"))
            mask = np.asarray(Image.fromarray(mask).resize((width, height), Image.Resampling.NEAREST))
            subject = mask > 127
            area += int(subject.sum())
            depth_rgb = np.asarray(
                Image.fromarray(depth_frame).resize((width, height), Image.Resampling.BILINEAR)
            )
            comp_out.write(np.where(subject[:, :, None], depth_rgb, rgb).astype(np.uint8))
            mask_out.write(np.repeat((subject * 255).astype(np.uint8)[:, :, None], 3, axis=2))
    if area == 0:
        raise CompositeError(
            "SAM 3 found no subject. Try a simpler subject such as 'person', or repair with RGB."
        )
    return CompositeResult(frames=counts[0], mask_pixels=area)
```

- [ ] **Step 4: Run** → `4 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/media/composite.py tests/test_composite.py
git commit -m "feat: add mask cleanup and depth-subject compositing"
```

---

### Task 9: `providers/replicate.py` — the only Replicate client

**Files:** Create `src/character_swap/providers/__init__.py`, `src/character_swap/providers/replicate.py`, `tests/test_replicate.py`

Behavior contract (from the spec): paid `POST` is sent **once**; the prediction JSON is written to `record` **before** waiting; if `record` already exists the client **resumes** instead of creating a new paid prediction; free calls (upload, GET, download) retry up to 3 times on network errors / 5xx.

- [ ] **Step 1: Failing tests**

```python
import json

import httpx
import pytest
import respx

from character_swap.providers.replicate import API, ReplicateClient, ReplicateError


def client(**kw):
    return ReplicateClient("tok", poll_seconds=0, sleep=lambda _s: None, **kw)


@respx.mock
def test_run_creates_once_persists_and_polls(tmp_path):
    record = tmp_path / "pred.json"
    create = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(
            201, json={"id": "p1", "status": "starting", "urls": {"get": f"{API}/predictions/p1"}}
        )
    )
    respx.get(f"{API}/predictions/p1").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"id": "p1", "status": "processing", "urls": {"get": f"{API}/predictions/p1"}},
            ),
            httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": "https://o"}),
        ]
    )
    assert client().run(version="v1", input={"a": 1}, record=record) == "https://o"
    assert create.call_count == 1
    assert json.loads(create.calls[0].request.content) == {"version": "v1", "input": {"a": 1}}
    assert json.loads(record.read_text())["status"] == "succeeded"


@respx.mock
def test_official_model_uses_model_endpoint(tmp_path):
    route = respx.post(f"{API}/models/openai/whisper/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p", "status": "succeeded", "output": {"t": 1}})
    )
    assert client().run(model="openai/whisper", input={}, record=tmp_path / "r.json") == {"t": 1}
    assert route.called


@respx.mock
def test_existing_record_resumes_without_paying_again(tmp_path):
    record = tmp_path / "pred.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing", "urls": {"get": f"{API}/predictions/p1"}}))
    create = respx.post(f"{API}/predictions")
    respx.get(f"{API}/predictions/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 7})
    )
    assert client().run(version="v1", input={}, record=record) == 7
    assert not create.called


def test_existing_succeeded_record_returns_output(tmp_path):
    record = tmp_path / "pred.json"
    record.write_text(json.dumps({"id": "p1", "status": "succeeded", "output": [1]}))
    assert client().run(version="v1", input={}, record=record) == [1]


def test_existing_failed_record_explains_how_to_retry(tmp_path):
    record = tmp_path / "pred.json"
    record.write_text(json.dumps({"id": "p1", "status": "failed", "error": "boom"}))
    with pytest.raises(ReplicateError, match="Delete .*pred.json to try again"):
        client().run(version="v1", input={}, record=record)


@respx.mock
def test_rejected_create_is_not_retried(tmp_path):
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(500, text="nope"))
    with pytest.raises(ReplicateError, match="rejected the request \\(500\\)"):
        client().run(version="v1", input={}, record=tmp_path / "r.json")
    assert route.call_count == 1
    assert not (tmp_path / "r.json").exists()


@respx.mock
def test_failed_prediction_raises_with_error(tmp_path):
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p", "status": "failed", "error": "bad input"})
    )
    with pytest.raises(ReplicateError, match="bad input"):
        client().run(version="v1", input={}, record=tmp_path / "r.json")


@respx.mock
def test_timeout_keeps_record_for_resume(tmp_path):
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(
            201, json={"id": "p", "status": "starting", "urls": {"get": f"{API}/predictions/p"}}
        )
    )
    respx.get(f"{API}/predictions/p").mock(
        return_value=httpx.Response(200, json={"id": "p", "status": "processing", "urls": {"get": f"{API}/predictions/p"}})
    )
    with pytest.raises(ReplicateError, match="Resume the job"):
        client(max_polls=2).run(version="v1", input={}, record=tmp_path / "r.json")
    assert (tmp_path / "r.json").exists()


@respx.mock
def test_upload_retries_transient_errors(tmp_path):
    file = tmp_path / "a.wav"
    file.write_bytes(b"x")
    route = respx.post(f"{API}/files").mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(201, json={"urls": {"get": "https://f"}})]
    )
    assert client().upload(file) == "https://f"
    assert route.call_count == 2


@respx.mock
def test_client_errors_are_not_retried(tmp_path):
    file = tmp_path / "a.wav"
    file.write_bytes(b"x")
    route = respx.post(f"{API}/files").mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        client().upload(file)
    assert route.call_count == 1


@respx.mock
def test_latest_version_and_download(tmp_path):
    respx.get(f"{API}/models/a/b").mock(
        return_value=httpx.Response(
            200,
            json={"latest_version": {"id": "v9", "openapi_schema": {"components": {"schemas": {"Input": {"properties": {"video": {"format": "uri"}}}}}}}},
        )
    )
    respx.get("https://out/file.mp4").mock(return_value=httpx.Response(200, content=b"data"))
    version, props = client().latest_version("a/b")
    assert version == "v9" and "video" in props
    assert client().download("https://out/file.mp4", tmp_path / "f.mp4").read_bytes() == b"data"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/providers/replicate.py`**

```python
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
                    f"{API}/files", headers=self._headers, files={"content": (Path(path).name, file)}
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
            ident, state, error = prediction.get("id"), prediction["status"], prediction.get("error")
            raise ReplicateError(f"Prediction {ident} {state}: {error}")
        return prediction.get("output")
```

Note: `test_timeout_keeps_record_for_resume` mocks the GET to keep returning a `urls.get` so the loop can continue; `_wait` reads the poll URL from the latest prediction each loop.

`src/character_swap/providers/__init__.py`:
```python
"""Network clients. Nothing outside this package calls external APIs."""
```

- [ ] **Step 4: Run** `uv run pytest tests/test_replicate.py -q --no-cov` → `11 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/__init__.py src/character_swap/providers/replicate.py tests/test_replicate.py
git commit -m "feat: add Replicate client with persist-before-wait and resume"
```

---

### Task 10: `stages/separate.py` + `stages/pitch.py` — vocals

**Files:** Create `src/character_swap/stages/__init__.py`, `src/character_swap/stages/separate.py`, `src/character_swap/stages/pitch.py`, `tests/test_audio_stages.py`

- [ ] **Step 1: Failing tests**

```python
import pytest
from fakes import FakeReplicate
from helpers import solid, tone, write_video, write_wav

from character_swap.jobs import Job
from character_swap.media.audio import read_audio
from character_swap.stages.pitch import pitch_vocals
from character_swap.stages.separate import DEMUCS_VERSION, StageError, separate_vocals


@pytest.fixture
def job(runs_dir, tmp_path):
    video = write_video(tmp_path / "in.mp4", [solid((1, 1, 1))] * 24, audio_seconds=1)
    return Job.create(runs_dir, video, generator="seedance", mode="depth")


def test_separate_writes_vocals_and_music(job, tmp_path):
    vocals = write_wav(tmp_path / "v.wav", tone(1, 300))
    drums = write_wav(tmp_path / "d.wav", tone(1, 80))
    fake = FakeReplicate(
        outputs={DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d", "bass": None}},
        files={"u/v": vocals, "u/d": drums},
    )
    separate_vocals(job, fake)
    assert job.path("vocals.wav").exists() and job.path("music.wav").exists()
    assert fake.runs[0]["input"]["model_name"] == "htdemucs"
    assert read_audio(job.path("music.wav")).shape[0] == 2


def test_separate_rejects_incomplete_stems(job, tmp_path):
    vocals = write_wav(tmp_path / "v.wav", tone(1))
    fake = FakeReplicate(outputs={DEMUCS_VERSION: {"vocals": "u/v"}}, files={"u/v": vocals})
    with pytest.raises(StageError, match="incomplete"):
        separate_vocals(job, fake)


@pytest.mark.rubberband
def test_pitch_keeps_length(tmp_path):
    source = write_wav(tmp_path / "vocals.wav", tone(1, 300))
    out = pitch_vocals(source, tmp_path / "pitched.wav", semitones=3)
    assert read_audio(out).shape == read_audio(source).shape


def test_pitch_without_binary_explains_install(tmp_path):
    source = write_wav(tmp_path / "vocals.wav", tone(0.2))
    with pytest.raises(StageError, match="brew install rubberband"):
        pitch_vocals(source, tmp_path / "p.wav", rubberband="definitely-not-installed")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

`src/character_swap/stages/__init__.py`:
```python
"""Pipeline steps. Each module does one step and receives its provider client."""


class StageError(RuntimeError):
    """A pipeline step could not complete; the message says what to do."""
```

`src/character_swap/stages/separate.py`:
```python
"""Split the original audio into vocals and music with Demucs (Replicate)."""

from __future__ import annotations

import numpy as np

from character_swap.jobs import Job
from character_swap.media.audio import read_audio, write_wav
from character_swap.stages import StageError

DEMUCS_VERSION = "25a173108cff36ef9f80f854c162d01df9e6528be175794b81158fa03836d953"

__all__ = ["DEMUCS_VERSION", "StageError", "separate_vocals"]


def separate_vocals(job: Job, replicate) -> None:
    original = read_audio(job.path("upload.mp4"))
    write_wav(job.path("original-audio.wav"), original)
    url = replicate.upload(job.path("original-audio.wav"))
    output = replicate.run(
        version=DEMUCS_VERSION,
        input={"audio": url, "model_name": "htdemucs", "output_format": "wav"},
        record=job.path("demucs-prediction.json"),
    )
    stems = {}
    for name, stem_url in output.items():
        if stem_url:
            path = replicate.download(stem_url, job.path(f"stem-{name}.wav"))
            stems[name] = read_audio(path)
    if "vocals" not in stems or len(stems) < 2:
        raise StageError("Vocal separation returned incomplete stems. Re-run prepare to retry.")
    write_wav(job.path("vocals.wav"), stems.pop("vocals"))
    music = np.zeros_like(original)
    for samples in stems.values():
        n = min(music.shape[1], samples.shape[1])
        music[:, :n] += samples[:, :n]
    write_wav(job.path("music.wav"), np.clip(music, -1, 1))
```

`src/character_swap/stages/pitch.py`:
```python
"""Shift isolated vocals up with Rubber Band, keeping exact duration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

from character_swap.media.audio import read_audio, write_wav
from character_swap.stages import StageError

INSTALL_HINT = "macOS: brew install rubberband · Ubuntu/WSL: sudo apt install rubberband-cli"


def pitch_vocals(
    vocals: Path, dst: Path, *, semitones: int = 3, rubberband: str = "rubberband"
) -> Path:
    binary = shutil.which(rubberband)
    if not binary:
        raise StageError(f"Rubber Band is not installed. {INSTALL_HINT}")
    subprocess.run(
        [binary, "--fine", "--pitch", str(semitones), "--time", "1", str(vocals), str(dst)],
        check=True,
        capture_output=True,
    )
    length = read_audio(vocals).shape[1]
    shifted = read_audio(dst)
    shifted = np.pad(shifted, ((0, 0), (0, max(0, length - shifted.shape[1]))))[:, :length]
    write_wav(dst, shifted)
    return dst
```

- [ ] **Step 4: Run** → `4 passed` (pitch test skipped only if rubberband missing).

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/stages/__init__.py src/character_swap/stages/separate.py src/character_swap/stages/pitch.py tests/test_audio_stages.py
git commit -m "feat: add Demucs vocal separation and Rubber Band pitch stages"
```

---

### Task 11: `stages/transcribe.py` + `stages/describe.py`

**Files:** Create `src/character_swap/stages/transcribe.py`, `src/character_swap/stages/describe.py`, `tests/test_text_stages.py`

> Model slugs and output shapes below come from the API verification done during planning (see "Verified provider facts" at the end of this plan). If a slug changed, update the constant and the fake output in the test together.

- [ ] **Step 1: Failing tests**

```python
from fakes import FakeReplicate
from helpers import solid, tone, write_video, write_wav

from character_swap.jobs import Job
from character_swap.stages.describe import DESCRIBE_MODEL, describe_scene
from character_swap.stages.transcribe import WHISPER_VERSION, transcribe


def make_job(runs_dir, tmp_path):
    video = write_video(tmp_path / "in.mp4", [solid((5, 5, 5))] * 24, audio_seconds=1)
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    write_video(job.path("original.mp4"), [solid((5, 5, 5))] * 24)
    write_wav(job.path("vocals.wav"), tone(1))
    return job


def test_transcribe_saves_text(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": "  hello world ", "segments": []}})
    assert transcribe(job, fake) == "hello world"
    assert job.state["transcript"] == "hello world"
    assert fake.uploads[0].name == "vocals.wav"
    assert fake.runs[0]["input"] == {"audio": "https://fake.replicate/vocals.wav", "transcription": "plain text"}


def test_transcribe_handles_missing_text(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": None}})
    assert transcribe(job, fake) == ""


def test_describe_scene_joins_streamed_tokens(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={DESCRIBE_MODEL: ["A bright ", "studio ", "with plants."]})
    assert describe_scene(job, fake) == "A bright studio with plants."
    assert job.path("scene.jpg").exists()
    sent = fake.runs[0]["input"]
    assert "people" in sent["prompt"].lower()
    assert sent["image_input"] == ["https://fake.replicate/scene.jpg"]
    assert fake.runs[0]["key"] == DESCRIBE_MODEL


def test_describe_scene_accepts_plain_string(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={DESCRIBE_MODEL: "A  quiet   office."})
    assert describe_scene(job, fake) == "A quiet office."
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

`src/character_swap/stages/transcribe.py`:
```python
"""Transcribe the isolated vocals so the prompt can quote the exact words."""

from __future__ import annotations

from character_swap.jobs import Job

# openai/whisper (large-v3) is a community model, so it is called by pinned version.
WHISPER_VERSION = "8099696689d249cf8b122d833c36ac3f75505c666a395ca40ef26f68e7d3d16e"


def transcribe(job: Job, replicate) -> str:
    url = replicate.upload(job.path("vocals.wav"))
    output = replicate.run(
        version=WHISPER_VERSION,
        input={"audio": url, "transcription": "plain text"},
        record=job.path("whisper-prediction.json"),
    )
    text = str(output.get("transcription") or "").strip()
    job.update(transcript=text)
    return text
```

`src/character_swap/stages/describe.py`:
```python
"""Describe the setting (never the people) so the generator keeps the real location."""

from __future__ import annotations

from character_swap.jobs import Job
from character_swap.media.video import middle_frame

DESCRIBE_MODEL = "openai/gpt-4o-mini"  # official model: called via the model endpoint
PROMPT = (
    "Describe only the setting of this video frame in 2-3 sentences: location, background "
    "objects, colors, lighting and camera framing. Do not describe any people, faces, "
    "clothing or identities."
)


def describe_scene(job: Job, replicate) -> str:
    frame = job.path("scene.jpg")
    middle_frame(job.path("original.mp4")).save(frame, quality=90)
    url = replicate.upload(frame)
    output = replicate.run(
        model=DESCRIBE_MODEL,
        input={"prompt": PROMPT, "image_input": [url], "max_completion_tokens": 200},
        record=job.path("describe-prediction.json"),
    )
    text = "".join(output) if isinstance(output, list) else str(output)
    text = " ".join(text.split())
    job.update(scene=text)
    return text
```

- [ ] **Step 4: Run** → `4 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/stages/transcribe.py src/character_swap/stages/describe.py tests/test_text_stages.py
git commit -m "feat: add Whisper transcript and scene description stages"
```

---

### Task 12: `stages/depth.py` + `stages/masks.py`

**Files:** Create `src/character_swap/stages/depth.py`, `src/character_swap/stages/masks.py`, `tests/test_vision_stages.py`

- [ ] **Step 1: Failing tests**

```python
import io
import zipfile

import pytest
from fakes import FakeReplicate

from character_swap.stages import StageError
from character_swap.stages.depth import DEPTH_MODEL, depth_huggingface, depth_replicate, pick_depth_url
from character_swap.stages.masks import SAM_VERSION, segment


def test_depth_replicate_resolves_schema_and_downloads(tmp_path):
    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    fake = FakeReplicate(
        outputs={f"{DEPTH_MODEL}-latest": "https://o/depth.mp4"},
        files={"https://o/depth.mp4": b"depth"},
        schema={"input_video": {"format": "uri"}, "model_size": {"type": "string"}},
    )
    depth_replicate(src, tmp_path / "depth.mp4", fake, tmp_path / "rec.json")
    assert (tmp_path / "depth.mp4").read_bytes() == b"depth"
    assert "input_video" in fake.runs[0]["input"]


def test_depth_replicate_without_video_input_fails(tmp_path):
    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    fake = FakeReplicate(schema={"image": {"format": "uri"}})
    with pytest.raises(StageError, match="video input"):
        depth_replicate(src, tmp_path / "d.mp4", fake, tmp_path / "r.json")


@pytest.mark.parametrize(
    ("output", "expected"),
    [("u", "u"), (["a", "b"], "a"), ({"rgb": "x", "depth_video": "d"}, "d")],
)
def test_pick_depth_url(output, expected):
    assert pick_depth_url(output) == expected


def test_pick_depth_url_rejects_unknown_shape():
    with pytest.raises(StageError):
        pick_depth_url({"rgb": "x"})


def test_depth_huggingface_uses_injected_client(tmp_path):
    result_file = tmp_path / "hf.mp4"
    result_file.write_bytes(b"hf")

    class FakeClient:
        def predict(self, *args, **kwargs):
            return [None, {"video": str(result_file)}]

    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    depth_huggingface(src, tmp_path / "depth.mp4", client_factory=lambda: FakeClient())
    assert (tmp_path / "depth.mp4").read_bytes() == b"hf"


def test_segment_extracts_mask_pngs(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("out/mask_00000.png", b"png0")
        archive.writestr("out/mask_00001.png", b"png1")
        archive.writestr("out/readme.txt", b"ignore")
    video = tmp_path / "depth.mp4"
    video.write_bytes(b"v")
    fake = FakeReplicate(outputs={SAM_VERSION: "https://o/m.zip"}, files={"https://o/m.zip": buffer.getvalue()})
    raw = segment(video, tmp_path / "sam", "person", fake)
    assert sorted(p.name for p in raw.iterdir()) == ["mask_00000.png", "mask_00001.png"]
    assert fake.runs[0]["input"] == {"video": "https://fake.replicate/depth.mp4", "prompt": "person", "mask_only": True, "return_zip": True}
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

`src/character_swap/stages/depth.py`:
```python
"""Turn the normalized video into a colored depth video (identity removed, motion kept)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from character_swap.stages import StageError

DEPTH_MODEL = "lucataco/depth-anything-video"
HF_SPACE = "depth-anything/Video-Depth-Anything"


def pick_depth_url(output) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output:
        return output[0]
    if isinstance(output, dict):
        for key, value in output.items():
            if "depth" in key and value:
                return value
    raise StageError(f"Depth model returned an unexpected output: {output!r}")


def depth_replicate(src: Path, dst: Path, replicate, record: Path) -> Path:
    version, props = replicate.latest_version(DEPTH_MODEL)
    key = next((k for k, v in props.items() if "video" in k and v.get("format") == "uri"), None)
    if key is None:
        raise StageError(f"{DEPTH_MODEL} no longer has a video input; check its schema.")
    url = replicate.upload(src)
    output = replicate.run(version=version, input={key: url}, record=record)
    return replicate.download(pick_depth_url(output), dst)


def _default_hf_client(token: str | None):  # pragma: no cover - network
    from gradio_client import Client

    return Client(HF_SPACE, token=token or None, verbose=False)


def depth_huggingface(
    src: Path, dst: Path, *, token: str | None = None, client_factory: Callable | None = None
) -> Path:
    from gradio_client import handle_file

    client = client_factory() if client_factory else _default_hf_client(token)
    result = client.predict(
        {"video": handle_file(str(src))},
        max_len=-1,
        target_fps=24,
        max_res=960,
        grayscale=False,
        api_name="/infer_video_depth",
    )
    produced = result[1]
    path = produced.get("video") or produced.get("path") if isinstance(produced, dict) else produced
    shutil.copyfile(path, dst)
    return dst
```

`src/character_swap/stages/masks.py`:
```python
"""Segment the subject with SAM 3 and save one raw PNG mask per frame."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

SAM_VERSION = "408f82fc20c300aac5d61d2e34ddb34cd0181e810e9f609d250aed14d5f81269"


def segment(video: Path, out_dir: Path, subject: str, replicate) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = replicate.upload(video)
    output = replicate.run(
        version=SAM_VERSION,
        input={"video": url, "prompt": subject, "mask_only": True, "return_zip": True},
        record=out_dir / "sam-prediction.json",
    )
    archive_path = replicate.download(output, out_dir / "masks.zip")
    raw = out_dir / "raw-masks"
    raw.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_path.read_bytes())) as archive:
        for name in archive.namelist():
            base = Path(name).name
            if base.startswith("mask_") and base.endswith(".png"):
                (raw / base).write_bytes(archive.read(name))
    return raw
```

- [ ] **Step 4: Run** → `8 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/stages/depth.py src/character_swap/stages/masks.py tests/test_vision_stages.py
git commit -m "feat: add depth (Replicate/HF) and SAM 3 mask stages"
```

---

### Task 13: `stages/face_mesh.py` — tracking + mesh drawing (single environment)

**Files:** Create `src/character_swap/stages/face_mesh.py`, `tests/test_face_mesh.py`

Design: drawing and tracking logic take an injected `FaceDetector`, so tests use a fake. `MediaPipeDetector` wraps the real model (crop logic ported from legacy `mesh_worker.py`) and is exercised by the live smoke test only.

- [ ] **Step 1: Failing tests**

```python
import json

import numpy as np
import pytest
from helpers import solid, write_video

from character_swap.stages import StageError
from character_swap.stages.face_mesh import MeshStyle, ensure_model, track_and_draw
from character_swap.media.video import iter_frames

TRIANGLE = [(0.3, 0.3), (0.7, 0.3), (0.5, 0.7)]
STYLE = MeshStyle(lines=[(((0, 1), (1, 2), (2, 0)), (255, 0, 0), 2)])


class FakeDetector:
    def __init__(self, miss=()):
        self.miss = set(miss)
        self.calls = 0

    def detect(self, frame, timestamp_ms):
        index = self.calls
        self.calls += 1
        return [] if index in self.miss else [TRIANGLE]


def test_draws_mesh_and_saves_landmarks(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    report = track_and_draw(src, tmp_path / "mesh.mp4", FakeDetector(), STYLE, tmp_path)
    assert report == {"frames": 3, "tracked": 3, "missing_frames": []}
    frame = next(iter_frames(tmp_path / "mesh.mp4"))
    assert frame[:, :, 0].max() > 150  # red lines drawn
    landmarks = json.loads((tmp_path / "face-landmarks.json").read_text())
    assert landmarks["frames"][0]["faces"][0][0] == {"x": 0.3, "y": 0.3}


def test_draws_on_background_when_given(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    bg = write_video(tmp_path / "bg.mp4", [solid((0, 0, 200))] * 2)
    track_and_draw(src, tmp_path / "mesh.mp4", FakeDetector(), STYLE, tmp_path, background=bg)
    frame = next(iter_frames(tmp_path / "mesh.mp4"))
    assert frame[2, 2, 2] > 150  # background kept


def test_background_mismatch_refused(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    bg = write_video(tmp_path / "bg.mp4", [solid((0, 0, 0))] * 2)
    with pytest.raises(StageError, match="frame count"):
        track_and_draw(src, tmp_path / "m.mp4", FakeDetector(), STYLE, tmp_path, background=bg)


def test_tracking_gaps_raise_with_frames(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    with pytest.raises(StageError, match=r"\[1\]"):
        track_and_draw(src, tmp_path / "m.mp4", FakeDetector(miss={1}), STYLE, tmp_path)
    report = json.loads((tmp_path / "face-tracking.json").read_text())
    assert report["missing_frames"] == [1]


def test_ensure_model_downloads_once(tmp_path):
    calls = []

    def fake_download(url, target):
        calls.append(url)
        np.zeros(1).tofile(target)

    first = ensure_model(tmp_path, download=fake_download)
    second = ensure_model(tmp_path, download=fake_download)
    assert first == second and first.exists() and len(calls) == 1
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/stages/face_mesh.py`**

```python
"""Track the face with MediaPipe and draw a mesh as motion guidance for the generator."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw

from character_swap.media.video import FPS, VideoWriter, count_frames, iter_frames
from character_swap.stages import StageError

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
Point = tuple[float, float]  # normalized (x, y) in the full frame


class FaceDetector(Protocol):
    def detect(self, frame: np.ndarray, timestamp_ms: int) -> list[list[Point]]: ...


@dataclass(frozen=True)
class MeshStyle:
    """Each entry: (edges as index pairs, RGB color, line width)."""

    lines: Sequence[tuple[Sequence[tuple[int, int]], tuple[int, int, int], int]]


def ensure_model(models_dir: Path, download: Callable = urllib.request.urlretrieve) -> Path:
    model = models_dir / "face_landmarker.task"
    if not model.exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        partial = model.with_suffix(".download")
        download(MODEL_URL, str(partial))
        partial.replace(model)
    return model


def _draw(image: Image.Image, faces: list[list[Point]], style: MeshStyle) -> None:
    pen = ImageDraw.Draw(image)
    for face in faces:
        points = [(round(x * image.width), round(y * image.height)) for x, y in face]
        for edges, color, width in style.lines:
            for start, end in edges:
                pen.line([points[start], points[end]], fill=color, width=width)


def track_and_draw(
    source: Path,
    out_video: Path,
    detector: FaceDetector,
    style: MeshStyle,
    report_dir: Path,
    *,
    background: Path | None = None,
) -> dict:
    """Write the mesh video plus face-landmarks.json and face-tracking.json in report_dir."""
    total = count_frames(source)
    backgrounds = iter_frames(background) if background else None
    if background and count_frames(background) != total:
        raise StageError("Depth composite and original differ in frame count; rebuild depth first.")

    first = next(iter_frames(source))
    height, width = first.shape[:2]
    saved, counts = [], []
    with VideoWriter(out_video, width, height) as writer:
        for index, frame in enumerate(iter_frames(source)):
            faces = detector.detect(frame, round(index * 1000 / FPS))
            counts.append(len(faces))
            saved.append(
                {
                    "frame": index,
                    "time": index / FPS,
                    "faces": [[{"x": x, "y": y} for x, y in face] for face in faces],
                }
            )
            canvas = Image.fromarray(next(backgrounds) if backgrounds else frame)
            _draw(canvas, faces, style)
            writer.write(np.asarray(canvas))

    missing = [i for i, n in enumerate(counts) if n == 0]
    report = {"frames": len(counts), "tracked": len(counts) - len(missing), "missing_frames": missing}
    (report_dir / "face-landmarks.json").write_text(json.dumps({"fps": FPS, "frames": saved}))
    (report_dir / "face-tracking.json").write_text(json.dumps(report, indent=2))
    if missing:
        raise StageError(
            f"Face tracking lost the face on frames {missing}. Use a clip with one centered, "
            "visible face, or choose depth mode."
        )
    return report


class MediaPipeDetector:  # pragma: no cover - exercised by the live smoke test
    """Real detector. Crops the centre/top region like the original worker for stability."""

    def __init__(self, model_path: Path):
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.15,
            min_face_presence_confidence=0.15,
            min_tracking_confidence=0.15,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    @property
    def style(self) -> MeshStyle:
        conn = self._mp.tasks.vision.FaceLandmarksConnections

        def pairs(group):
            return [(c.start, c.end) for c in group]

        return MeshStyle(
            lines=[
                (pairs(conn.FACE_LANDMARKS_TESSELATION), (50, 170, 205), 1),
                (pairs(conn.FACE_LANDMARKS_LIPS), (255, 135, 100), 2),
                (pairs(conn.FACE_LANDMARKS_LEFT_EYE), (130, 255, 150), 2),
                (pairs(conn.FACE_LANDMARKS_RIGHT_EYE), (130, 255, 150), 2),
            ]
        )

    def detect(self, frame: np.ndarray, timestamp_ms: int) -> list[list[Point]]:
        height, width = frame.shape[:2]
        x0, crop_w, crop_h = int(width * 0.25), int(width * 0.5), int(height * 0.65)
        crop = np.ascontiguousarray(frame[0:crop_h, x0 : x0 + crop_w])
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=crop)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        return [
            [((x0 + p.x * crop_w) / width, (p.y * crop_h) / height) for p in face]
            for face in result.face_landmarks
        ]

    def close(self) -> None:
        self._landmarker.close()
```

- [ ] **Step 4: Run** → `5 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/stages/face_mesh.py tests/test_face_mesh.py
git commit -m "feat: add face mesh tracking with injectable detector (single environment)"
```

---

### Task 14: `review.py` — hash-locked approval gate

**Files:** Create `src/character_swap/review.py`, `tests/test_review.py`

- [ ] **Step 1: Failing tests**

```python
import pytest

from character_swap.jobs import Job
from character_swap.review import (
    ReviewError,
    clear,
    decide,
    fingerprint,
    require_approved,
    review_files,
    status,
)


@pytest.fixture
def depth_job(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    for name in review_files(job):
        job.path(name).write_bytes(name.encode())
    return job


def test_files_per_mode(runs_dir, tmp_path):
    video, face = tmp_path / "in.mp4", tmp_path / "me.png"
    video.write_bytes(b"v")
    face.write_bytes(b"p")
    mesh = Job.create(runs_dir, video, generator="seedance", mode="face_mesh")
    assert "face-landmarks.json" in review_files(mesh) and "mask.mp4" not in review_files(mesh)
    both = Job.create(runs_dir, video, generator="seedance", mode="depth_mesh")
    assert {"mask.mp4", "face-landmarks.json", "depth-composite-silent.mp4"} <= set(review_files(both))
    wan = Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert review_files(wan) == ["original.mp4", "character-primary.png"]


def test_unavailable_before_prepare(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert status(job)["status"] == "unavailable"


def test_approve_then_change_invalidates(depth_job):
    assert status(depth_job)["status"] == "pending"
    decide(depth_job, approved=True, note="watched all", fingerprint=fingerprint(depth_job))
    require_approved(depth_job)
    depth_job.path("mask.mp4").write_bytes(b"changed")
    assert status(depth_job)["status"] == "pending"
    with pytest.raises(ReviewError, match="Review required"):
        require_approved(depth_job)


def test_stale_fingerprint_refused(depth_job):
    old = fingerprint(depth_job)
    depth_job.path("prepared.mp4").write_bytes(b"new")
    with pytest.raises(ReviewError, match="changed"):
        decide(depth_job, approved=True, note="x", fingerprint=old)


def test_reject_and_clear(depth_job):
    decide(depth_job, approved=False, note="hand missing", fingerprint=fingerprint(depth_job))
    assert status(depth_job)["status"] == "rejected"
    clear(depth_job)
    assert status(depth_job)["status"] == "pending"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/review.py`**

```python
"""Mandatory review: approval is bound to a hash of exactly what was reviewed."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from character_swap.jobs import Job

DEPTH = ["original.mp4", "depth.mp4", "mask.mp4", "composite-silent.mp4", "prepared.mp4"]
MESH = ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]
DEPTH_MESH = [
    "original.mp4",
    "depth.mp4",
    "mask.mp4",
    "depth-composite-silent.mp4",
    "face-landmarks.json",
    "composite-silent.mp4",
    "prepared.mp4",
]


class ReviewError(ValueError):
    """Generation is blocked until the current artifacts are approved."""


def review_files(job: Job) -> list[str]:
    mode = job.mode
    if mode == "direct":
        return ["original.mp4", job.state["character_primary"]]
    return {"depth": DEPTH, "face_mesh": MESH, "depth_mesh": DEPTH_MESH}[mode]


def fingerprint(job: Job) -> str:
    digest = hashlib.sha256(job.mode.encode())
    for name in review_files(job):
        path = job.path(name)
        if not path.exists():
            raise ReviewError("Prepare the video before reviewing it.")
        digest.update(name.encode())
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def status(job: Job) -> dict:
    try:
        current = fingerprint(job)
    except ReviewError:
        return {"approved": False, "status": "unavailable", "note": "Prepare the video first."}
    saved_path = job.path("review.json")
    saved = json.loads(saved_path.read_text()) if saved_path.exists() else {}
    if saved.get("fingerprint") != current:
        return {"approved": False, "status": "pending", "fingerprint": current}
    return saved


def decide(job: Job, *, approved: bool, note: str, fingerprint: str) -> dict:
    current = globals()["fingerprint"](job)
    if current != fingerprint:
        raise ReviewError("The prepared files changed since you looked. Reload and review again.")
    data = {
        "approved": approved,
        "status": "approved" if approved else "rejected",
        "note": note,
        "fingerprint": current,
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    job.path("review.json").write_text(json.dumps(data, indent=2))
    return data


def require_approved(job: Job) -> None:
    if not status(job).get("approved"):
        raise ReviewError("Review required: watch the whole prepared clip and approve it first.")


def clear(job: Job) -> None:
    job.path("review.json").unlink(missing_ok=True)
```

> The `fingerprint` parameter in `decide` shadows the function; `globals()["fingerprint"]` keeps the public keyword name the spec/UI use. If a reviewer prefers, rename the module function to `compute_fingerprint` and update tests — do it in this task, not later.

- [ ] **Step 4: Run** → `5 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/review.py tests/test_review.py
git commit -m "feat: add hash-locked review gate per mode"
```

---

### Task 15: `costs.py` — price table, estimates, spend log

**Files:** Create `src/character_swap/costs.py`, `tests/test_costs.py`

- [ ] **Step 1: Failing tests**

```python
import csv

import pytest

from character_swap.costs import PRICES, Estimate, estimate_prepare, record_spend, total_spend
from character_swap.jobs import Job


def test_depth_prep_includes_every_paid_step():
    estimate = estimate_prepare("depth", seconds=10)
    items = [line.item for line in estimate.lines]
    assert items == ["Demucs vocal separation", "Whisper transcript", "Scene description", "Depth video", "SAM 3 masks"]
    assert estimate.total == pytest.approx(sum(line.usd for line in estimate.lines))


def test_face_mesh_skips_depth_and_masks():
    items = [line.item for line in estimate_prepare("face_mesh", seconds=10).lines]
    assert "Depth video" not in items and "SAM 3 masks" not in items


def test_repair_is_one_sam_run():
    from character_swap.costs import estimate_repair

    assert [line.item for line in estimate_repair().lines] == ["SAM 3 masks"]


def test_direct_prep_is_free():
    assert estimate_prepare("direct", seconds=10).total == 0


def test_describe_text_lists_lines_and_disclaimer():
    text = estimate_prepare("depth", seconds=5).describe()
    assert "Depth video" in text and "estimate" in text.lower()


def test_every_price_has_a_checked_date():
    assert all(price.checked_on for price in PRICES.values())


def test_record_spend_writes_job_and_global_log(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(job, "prepare", estimate_prepare("depth", 10), runs_dir)
    record_spend(job, "prepare", Estimate(lines=()), runs_dir)
    rows = list(csv.DictReader((runs_dir / "spend-log.csv").open()))
    assert len(rows) == 2 and rows[0]["job"] == job.id
    assert total_spend(runs_dir) == pytest.approx(estimate_prepare("depth", 10).total)
    assert len(job.state["costs"]) == 2


def test_total_spend_without_log_is_zero(runs_dir):
    assert total_spend(runs_dir) == 0
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/costs.py`**

```python
"""Cost estimates shown before paid steps, and a spend log written after them."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from character_swap.jobs import Job


@dataclass(frozen=True)
class Price:
    item: str
    usd: float
    unit: str  # "run" or "second"
    checked_on: str
    source: str


# Replicate figures are the "costs approximately $X to run" values on each model page.
PRICES: dict[str, Price] = {
    "demucs": Price("Demucs vocal separation", 0.017, "run", "2026-10-22", "replicate.com/cjwbw/demucs"),
    "whisper": Price("Whisper transcript", 0.00075, "run", "2026-10-22", "replicate.com/openai/whisper"),
    "describe": Price("Scene description", 0.001, "run", "2026-10-22", "replicate.com/openai/gpt-4o-mini (per token, ~1 image)"),
    "depth": Price("Depth video", 0.04, "run", "2026-10-22", "replicate.com/lucataco/depth-anything-video"),
    "sam3": Price("SAM 3 masks", 0.002, "run", "2026-10-22", "replicate.com/lucataco/sam3-video"),
}

PREP_STEPS = {
    "depth": ["demucs", "whisper", "describe", "depth", "sam3"],
    "face_mesh": ["demucs", "whisper", "describe"],
    "depth_mesh": ["demucs", "whisper", "describe", "depth", "sam3"],
    "direct": [],
}


@dataclass(frozen=True)
class Line:
    item: str
    usd: float


@dataclass(frozen=True)
class Estimate:
    lines: tuple[Line, ...]

    @property
    def total(self) -> float:
        return sum(line.usd for line in self.lines)

    def describe(self) -> str:
        rows = [f"  {line.item:<28} ~${line.usd:.3f}" for line in self.lines]
        rows.append(f"  {'Total':<28} ~${self.total:.3f}")
        rows.append("  (estimate; check your provider billing page for exact charges)")
        return "\n".join(rows)


def _cost(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
    return Line(price.item, round(usd, 4))


def estimate_prepare(mode: str, seconds: float) -> Estimate:
    return Estimate(lines=tuple(_cost(key, seconds) for key in PREP_STEPS[mode]))


def estimate_repair() -> Estimate:
    return Estimate(lines=(_cost("sam3", 0),))


def record_spend(job: Job, step: str, estimate: Estimate, runs_dir: Path) -> None:
    at = datetime.now(UTC).isoformat(timespec="seconds")
    costs = job.state.get("costs", [])
    costs.append({"step": step, "usd": estimate.total, "at": at})
    job.update(costs=costs)
    log = Path(runs_dir) / "spend-log.csv"
    new = not log.exists()
    with log.open("a", newline="") as file:
        writer = csv.writer(file)
        if new:
            writer.writerow(["at", "job", "step", "usd_estimate"])
        writer.writerow([at, job.id, step, f"{estimate.total:.4f}"])


def total_spend(runs_dir: Path) -> float:
    log = Path(runs_dir) / "spend-log.csv"
    if not log.exists():
        return 0.0
    with log.open() as file:
        return sum(float(row["usd_estimate"]) for row in csv.DictReader(file))
```

- [ ] **Step 4: Run** → `8 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/costs.py tests/test_costs.py
git commit -m "feat: add cost estimates and spend log"
```

---

### Task 16: `prompts.py` — editable generation prompt

**Files:** Create `src/character_swap/prompts.py`, `tests/test_prompts.py`

- [ ] **Step 1: Failing tests**

```python
from character_swap.prompts import build_prompt


def test_depth_prompt_mentions_depth_and_references():
    text = build_prompt(mode="depth")
    assert "@video1" in text and "@image1" in text and "depth" in text.lower()
    assert "@image2" not in text


def test_face_mesh_prompt_removes_mesh_lines():
    assert "remove every mesh line" in build_prompt(mode="face_mesh").lower()


def test_depth_mesh_mentions_both():
    text = build_prompt(mode="depth_mesh").lower()
    assert "depth" in text and "mesh" in text


def test_scene_transcript_and_secondary_are_included():
    text = build_prompt(mode="depth", scene="A loft.", transcript="Hi there", has_secondary=True)
    assert "Setting: A loft." in text
    assert '"Hi there"' in text
    assert "@image2" in text


def test_blank_scene_and_transcript_are_skipped():
    text = build_prompt(mode="depth", scene="  ", transcript="")
    assert "Setting:" not in text and "spoken words" not in text
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/prompts.py`**

```python
"""Build the Seedance prompt the user reviews (and can edit) before generating."""

from __future__ import annotations

BASE = (
    "Replace the person in @video1 with the character in @image1 in every frame, including "
    "face, hair, hands, clothing and body shape. Keep the original camera framing, motion, "
    "gestures, timing and lighting. Use the vocals embedded in @video1 only for lip "
    "synchronization and performance; do not add new dialogue."
)
MODE_NOTES = {
    "depth": (
        "@video1 shows the people as a colored depth map over the real background. Render them "
        "as fully realistic people and remove all depth colors."
    ),
    "face_mesh": (
        "@video1 has a face-mesh wireframe drawn over the face. Use it only as expression and "
        "lip-motion guidance and remove every mesh line."
    ),
    "depth_mesh": (
        "@video1 shows the people as a colored depth map with a face-mesh wireframe on top. Use "
        "both only as motion guidance; render realistic people and remove every depth color and "
        "mesh line."
    ),
}
SECONDARY = "@image2 is a second character; use them for the second person in the scene."


def build_prompt(
    *, mode: str, scene: str | None = None, transcript: str | None = None, has_secondary: bool = False
) -> str:
    parts = [BASE, MODE_NOTES[mode]]
    if has_secondary:
        parts.append(SECONDARY)
    if scene and scene.strip():
        parts.append(f"Setting: {scene.strip()}")
    if transcript and transcript.strip():
        parts.append(
            f'The spoken words are: "{transcript.strip()}". Match the lip movements to these words.'
        )
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run** → `5 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/prompts.py tests/test_prompts.py
git commit -m "feat: add prompt builder with mode notes, scene and transcript"
```

---

### Task 17: `pipeline.py` — run stages per mode, resumable, RGB repair

**Files:** Create `src/character_swap/pipeline.py`, `tests/test_pipeline.py`

- [ ] **Step 1: Failing tests** (integration with fakes; real local media work, fake network)

```python
import io
import zipfile

import pytest
from fakes import FakeReplicate
from helpers import solid, tone, write_mask_pngs, write_video, write_wav

from character_swap.jobs import Job
from character_swap.pipeline import GENERATOR_LIMITS, Services, prepare, repair_with_rgb
from character_swap.review import decide, fingerprint, status
from character_swap.stages import StageError
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.face_mesh import MeshStyle
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION

FRAMES = 96  # 4 s at 24 fps (Seedance minimum)


class FakeDetector:
    style = MeshStyle(lines=[(((0, 1),), (255, 0, 0), 1)])

    def detect(self, frame, timestamp_ms):
        return [[(0.4, 0.4), (0.6, 0.6)]]

    def close(self):
        pass


def zipped_masks(tmp_path, count):
    folder = write_mask_pngs(tmp_path / "zipsrc", count)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for png in folder.iterdir():
            archive.writestr(png.name, png.read_bytes())
    return buffer.getvalue()


@pytest.fixture
def services(tmp_path):
    vocals = write_wav(tmp_path / "v.wav", tone(4, 300))
    drums = write_wav(tmp_path / "d.wav", tone(4, 80))
    depth = write_video(tmp_path / "depth.mp4", [solid((100, 100, 100))] * FRAMES)
    fake = FakeReplicate(
        outputs={
            DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d"},
            WHISPER_VERSION: {"transcription": "hello"},
            DESCRIBE_MODEL: ["A studio."],
            f"{DEPTH_MODEL}-latest": "u/depth",
            SAM_VERSION: "u/zip",
        },
        files={"u/v": vocals, "u/d": drums, "u/depth": depth, "u/zip": zipped_masks(tmp_path, FRAMES)},
    )
    return Services(replicate=fake, face_detector=FakeDetector, rubberband="cat-not-used")


@pytest.fixture
def make_job(runs_dir, tmp_path):
    def _make(mode, generator="seedance", frames=FRAMES, character=None):
        video = write_video(tmp_path / f"in-{mode}.mp4", [solid((180, 40, 40))] * frames, audio_seconds=frames / 24)
        return Job.create(runs_dir, video, generator=generator, mode=mode, character=character)

    return _make


@pytest.fixture(autouse=True)
def no_rubberband(monkeypatch):
    def fake_pitch(vocals, dst, **kwargs):
        dst.write_bytes(vocals.read_bytes())
        return dst

    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)


def test_depth_mode_produces_reviewable_prepared_video(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    for name in ["original.mp4", "depth.mp4", "mask.mp4", "composite-silent.mp4", "prepared.mp4"]:
        assert job.path(name).exists(), name
    assert job.state["transcript"] == "hello" and job.state["scene"] == "A studio."
    assert status(job)["status"] == "pending"


def test_prepare_is_resumable_and_does_not_rerun_paid_stages(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    runs_before = len(services.replicate.runs)
    prepare(job, services, status=lambda _m: None)
    assert len(services.replicate.runs) == runs_before


def test_failed_stage_resumes_without_repaying_earlier_stages(make_job, services, monkeypatch):
    job = make_job("depth")
    boom = {"on": True}
    real_segment = __import__("character_swap.pipeline", fromlist=["segment"]).segment

    def flaky_segment(*args, **kwargs):
        if boom["on"]:
            raise RuntimeError("simulated timeout")
        return real_segment(*args, **kwargs)

    monkeypatch.setattr("character_swap.pipeline.segment", flaky_segment)
    with pytest.raises(RuntimeError, match="simulated timeout"):
        prepare(job, services, status=lambda _m: None)
    paid_before = [r["key"] for r in services.replicate.runs]
    boom["on"] = False
    prepare(job, services, status=lambda _m: None)
    paid_after = [r["key"] for r in services.replicate.runs]
    assert paid_after[: len(paid_before)] == paid_before
    assert paid_after[len(paid_before) :] == [SAM_VERSION]
    assert job.path("prepared.mp4").exists()


def test_face_mesh_mode_skips_depth(make_job, services):
    job = make_job("face_mesh")
    prepare(job, services, status=lambda _m: None)
    keys = [run["key"] for run in services.replicate.runs]
    assert SAM_VERSION not in keys and f"{DEPTH_MODEL}-latest" not in keys
    assert job.path("face-landmarks.json").exists() and job.path("prepared.mp4").exists()


def test_depth_mesh_mode_keeps_depth_composite_and_adds_mesh(make_job, services):
    job = make_job("depth_mesh")
    prepare(job, services, status=lambda _m: None)
    assert job.path("depth-composite-silent.mp4").exists()
    assert job.path("face-landmarks.json").exists()


def test_direct_mode_only_normalizes(make_job, services, tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"png")
    job = make_job("direct", generator="wan", character=face)
    prepare(job, services, status=lambda _m: None)
    assert job.path("original.mp4").exists()
    assert services.replicate.runs == []


def test_too_short_for_seedance_fails_before_paid_calls(make_job, services):
    job = make_job("depth", frames=48)
    with pytest.raises(Exception, match="at least 4"):
        prepare(job, services, status=lambda _m: None)
    assert services.replicate.runs == []


def test_repair_reruns_sam_on_rgb_and_clears_approval(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    sam_runs = sum(1 for r in services.replicate.runs if r["key"] == SAM_VERSION)
    repair_with_rgb(job, services, status=lambda _m: None)
    assert sum(1 for r in services.replicate.runs if r["key"] == SAM_VERSION) == sam_runs + 1
    assert services.replicate.uploads[-1].name == "original.mp4"
    assert status(job)["status"] == "pending"
    assert job.state["mask_source"] == "rgb"


def test_repair_not_available_for_face_mesh(make_job, services):
    job = make_job("face_mesh")
    with pytest.raises(StageError, match="no SAM mask"):
        repair_with_rgb(job, services, status=lambda _m: None)


def test_limits_cover_both_generators():
    assert set(GENERATOR_LIMITS) == {"seedance", "wan"}
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/pipeline.py`**

```python
"""Run the preparation stages for a job's mode. Every stage is skipped if already done."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from character_swap.jobs import Job
from character_swap.media.audio import embed_samples, read_audio
from character_swap.media.composite import clean_border_fragments, composite_depth_subject
from character_swap.media.video import normalize
from character_swap.review import clear as clear_review
from character_swap.stages import StageError
from character_swap.stages.depth import depth_huggingface, depth_replicate
from character_swap.stages.describe import describe_scene
from character_swap.stages.face_mesh import track_and_draw
from character_swap.stages.masks import segment
from character_swap.stages.pitch import pitch_vocals
from character_swap.stages.separate import separate_vocals
from character_swap.stages.transcribe import transcribe

# (min_seconds, max_seconds) per generator; see "Verified provider facts".
GENERATOR_LIMITS = {"seedance": (4, 30), "wan": (1, 30)}
Status = Callable[[str], None]


@dataclass
class Services:
    replicate: object
    face_detector: Callable[[], object]  # returns an object with .detect, .style, .close
    rubberband: str = "rubberband"
    hf_token: str | None = None


def _stage(job: Job, name: str, label: str, work: Callable[[], object], status: Status) -> None:
    if job.stage_done(name):
        status(f"{label} · already done")
        return
    status(label)
    work()
    job.mark_stage(name)


def prepare(job: Job, services: Services, status: Status = print) -> None:
    low, high = GENERATOR_LIMITS[job.generator]
    _stage(
        job,
        "normalize",
        "Normalizing video to 24 fps",
        lambda: normalize(
            job.path("upload.mp4"), job.path("original.mp4"), min_seconds=low, max_seconds=high
        ),
        status,
    )
    if job.mode == "direct":
        status("Ready for review")
        return

    r = services.replicate
    _stage(job, "separate", "Separating vocals from music", lambda: separate_vocals(job, r), status)
    _stage(
        job,
        "pitch",
        "Pitching vocals +3 semitones",
        lambda: pitch_vocals(
            job.path("vocals.wav"), job.path("vocals-plus3.wav"), rubberband=services.rubberband
        ),
        status,
    )
    _stage(job, "transcribe", "Transcribing speech", lambda: transcribe(job, r), status)
    _stage(job, "describe", "Describing the setting", lambda: describe_scene(job, r), status)

    if job.mode in ("depth", "depth_mesh"):
        _stage(job, "depth", "Generating depth video", lambda: _depth(job, services), status)
        _stage(job, "masks", "Cutting out the subject (SAM 3)", lambda: _masks(job, r, "depth.mp4"), status)
        _stage(job, "composite", "Compositing depth subject over background", lambda: _composite(job), status)
    if job.mode in ("face_mesh", "depth_mesh"):
        _stage(job, "face_mesh", "Tracking the face mesh", lambda: _mesh(job, services), status)
    _stage(job, "embed", "Embedding pitched vocals", lambda: _embed(job), status)
    status("Ready for review")


def repair_with_rgb(job: Job, services: Services, status: Status = print) -> None:
    """Re-segment on the original colors (depth can lose detail), keep depth colors, re-review."""
    if job.mode not in ("depth", "depth_mesh"):
        raise StageError("This mode has no SAM mask to repair. Prepare the face tracking again.")
    status("Re-running SAM 3 on the original colors")
    repair_dir = job.path("rgb-repair")
    if repair_dir.exists():
        # Deliberate: every repair is a fresh, confirmed, paid SAM run (~$0.002).
        shutil.rmtree(repair_dir)
    _masks(job, services.replicate, "original.mp4", folder="rgb-repair")
    _composite(job, folder="rgb-repair")
    if job.mode == "depth_mesh":
        job.path("depth-composite-silent.mp4").unlink(missing_ok=True)
        job.reset_stages("face_mesh")
        _mesh(job, services)
    _embed(job)
    clear_review(job)
    job.update(mask_source="rgb")
    status("Repaired. Review the whole clip again.")


# ---- stage bodies -------------------------------------------------------------
def _depth(job: Job, services: Services) -> None:
    src, dst = job.path("original.mp4"), job.path("depth.mp4")
    if job.state["depth_provider"] == "huggingface":
        depth_huggingface(src, dst, token=services.hf_token)
    else:
        depth_replicate(src, dst, services.replicate, job.path("depth-prediction.json"))


def _masks(job: Job, replicate, video: str, folder: str = "sam") -> Path:
    raw = segment(job.path(video), job.path(folder), job.state["subject"], replicate)
    clean = job.path(folder) / "clean-masks"
    clean_border_fragments(raw, clean)
    return clean


def _composite(job: Job, folder: str = "sam") -> None:
    composite_depth_subject(
        job.path("original.mp4"), job.path("depth.mp4"), job.path(folder) / "clean-masks", job.root
    )


def _mesh(job: Job, services: Services) -> None:
    background = None
    if job.mode == "depth_mesh":
        background = job.path("depth-composite-silent.mp4")
        if not background.exists():  # a retry must not copy an already-meshed video
            shutil.copyfile(job.path("composite-silent.mp4"), background)
    detector = services.face_detector()
    try:
        track_and_draw(
            job.path("original.mp4"),
            job.path("composite-silent.mp4"),
            detector,
            detector.style,
            job.root,
            background=background,
        )
    finally:
        detector.close()


def _embed(job: Job) -> None:
    embed_samples(
        job.path("composite-silent.mp4"),
        read_audio(job.path("vocals-plus3.wav")),
        job.path("prepared.mp4"),
    )
```

Note for the implementer: in `face_mesh` mode `composite-silent.mp4` is the mesh drawn on the original; in `depth_mesh` it is the mesh drawn on `depth-composite-silent.mp4`. Both review sets in `review.py` match these names.

- [ ] **Step 4: Run** `uv run pytest tests/test_pipeline.py -q --no-cov` → `10 passed`. These tests do real encoding of 96-frame 64×64 clips; expect a few seconds.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/pipeline.py tests/test_pipeline.py
git commit -m "feat: add resumable preparation pipeline for all modes with RGB repair"
```

---

### Task 18: `cli.py` — `charswap` commands

**Files:** Create `src/character_swap/cli.py`, `tests/test_cli.py`

Commands: `prepare`, `resume`, `status`, `review`, `repair`, `prompt`, `doctor`. Paid commands print an estimate and ask for confirmation unless `--yes`.

- [ ] **Step 1: Failing tests**

```python
import json

import pytest
from helpers import solid, write_video

from character_swap import cli
from character_swap.config import Settings
from character_swap.jobs import Job


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(str(text))

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def settings(tmp_path):
    return Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "t"})


@pytest.fixture
def video(tmp_path):
    return write_video(tmp_path / "in.mp4", [solid((9, 9, 9))] * 96, audio_seconds=4)


def run(argv, settings, **kw):
    out = Recorder()
    code = cli.main(argv, settings=settings, out=out, **kw)
    return code, out


def test_prepare_declined_creates_nothing(settings, video):
    code, out = run(["prepare", str(video), "--mode", "depth"], settings, confirm=lambda _q: False)
    assert code == 1 and "Depth video" in out.text
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


def test_prepare_runs_pipeline_and_prints_job(settings, video, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: calls.append(job.id))
    code, out = run(
        ["prepare", str(video), "--mode", "depth", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and calls and f"JOB {calls[0]}" in out.text


def test_prepare_wan_needs_character(settings, video):
    code, out = run(["prepare", str(video), "--generator", "wan", "--yes"], settings)
    assert code == 2 and "character" in out.text.lower()


def test_status_review_prompt_flow(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
    job.update(transcript="hi", scene="A room.")

    code, out = run(["status", job.id], settings)
    data = json.loads(out.text)
    assert code == 0 and data["review"]["status"] == "pending"

    code, out = run(["review", job.id, "--approve", "--note", "watched", "--fingerprint", data["review"]["fingerprint"]], settings)
    assert code == 0 and "approved" in out.text

    code, out = run(["prompt", job.id], settings)
    assert code == 0 and "mesh" in out.text.lower() and '"hi"' in out.text


def test_errors_are_friendly(settings):
    code, out = run(["status", "0" * 32], settings)
    assert code == 2 and "No job" in out.text


def test_missing_key_is_reported(tmp_path, video):
    empty = Settings.load(root=tmp_path, environ={})
    code, out = run(["prepare", str(video), "--yes"], empty)
    assert code == 2 and "REPLICATE_API_TOKEN" in out.text


def test_resume_continues_existing_job(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    calls = []
    monkeypatch.setattr(cli, "prepare_job", lambda j, s, status: calls.append(j.id))
    code, out = run(["resume", job.id], settings, services_factory=lambda s: object())
    assert code == 0 and calls == [job.id] and "Resuming" in out.text
    assert len([p for p in settings.runs_dir.iterdir() if p.is_dir()]) == 1  # no new job
    assert job.state["costs"][-1]["step"] == "prepare"  # spend logged once on completion


def test_repair_calls_pipeline(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    called = []
    monkeypatch.setattr(cli, "repair_with_rgb", lambda j, s, status: called.append(j.id))
    code, _ = run(["repair", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert code == 0 and called == [job.id]
    assert job.state["costs"][-1]["step"] == "repair"


def test_doctor_reports_checks(settings, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    code, out = run(["doctor"], settings)
    assert "rubberband" in out.text and "REPLICATE_API_TOKEN: set" in out.text
    assert "pyav (ffmpeg):" in out.text
    assert "PIAPI_API_KEY: missing" in out.text and "hosting: tmpfiles" in out.text
    assert code == 0
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `src/character_swap/cli.py`**

```python
"""`charswap` command line. Thin layer over jobs, pipeline, review and prompts."""

from __future__ import annotations

import argparse
import json
import shutil
import sys

import av
from collections.abc import Callable, Sequence
from pathlib import Path

from character_swap.config import MissingSettingError, Settings
from character_swap.costs import estimate_prepare, estimate_repair, record_spend
from character_swap.jobs import Job, JobError
from character_swap.media.video import ClipLengthError
from character_swap.pipeline import Services, prepare as prepare_job, repair_with_rgb
from character_swap.prompts import build_prompt
from character_swap.review import ReviewError, decide, status as review_status
from character_swap.stages import StageError

Out = Callable[[str], None]


def build_services(settings: Settings) -> Services:  # pragma: no cover - wires real clients
    from character_swap.providers.replicate import ReplicateClient
    from character_swap.stages.face_mesh import MediaPipeDetector, ensure_model

    model_dir = settings.root / "models"
    return Services(
        replicate=ReplicateClient(settings.require("REPLICATE_API_TOKEN")),
        face_detector=lambda: MediaPipeDetector(ensure_model(model_dir)),
        hf_token=settings.get("HF_TOKEN") or None,
    )


def _ask(question: str) -> bool:  # pragma: no cover - interactive
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="charswap", description="Character Swap workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="prepare a video for review")
    prep.add_argument("video", type=Path)
    prep.add_argument("--generator", choices=["seedance", "wan"], default="seedance")
    prep.add_argument("--mode", choices=["depth", "face_mesh", "depth_mesh", "direct"])
    prep.add_argument("--subject", default="person", help="what SAM 3 should cut out")
    prep.add_argument("--depth-provider", choices=["replicate", "huggingface"], default="replicate")
    prep.add_argument("--character", type=Path, help="character image (required for wan)")
    prep.add_argument("--yes", action="store_true", help="skip the cost confirmation")

    for name in ("status", "prompt"):
        sub.add_parser(name).add_argument("job")
    resume = sub.add_parser("resume", help="continue an interrupted job (no repeat charges)")
    resume.add_argument("job")
    review = sub.add_parser("review", help="approve or reject the prepared clip")
    review.add_argument("job")
    decision = review.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    review.add_argument("--note", required=True)
    review.add_argument("--fingerprint", required=True)
    repair = sub.add_parser("repair", help="re-cut the subject using original colors")
    repair.add_argument("job")
    repair.add_argument("--yes", action="store_true")
    sub.add_parser("doctor", help="check this machine's setup")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: Out = print,
    confirm: Callable[[str], bool] = _ask,
    services_factory: Callable[[Settings], Services] = build_services,
) -> int:
    args = _parser().parse_args(argv)
    settings = settings or Settings.load()
    try:
        return COMMANDS[args.command](args, settings, out, confirm, services_factory)
    except (JobError, ReviewError, StageError, ClipLengthError, MissingSettingError) as error:
        out(f"Error: {error}")
        return 2


def _prepare(args, settings, out, confirm, services_factory) -> int:
    mode = args.mode or ("direct" if args.generator == "wan" else "depth")
    if args.generator == "wan" and args.character is None:
        raise JobError("Wan needs --character IMAGE (your face or character picture).")
    if mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    estimate = estimate_prepare(mode, seconds=30)
    out("Preparation cost (up to a 30 s clip):\n" + estimate.describe())
    if not args.yes and not confirm("Continue?"):
        out("Cancelled. Nothing was charged.")
        return 1
    job = Job.create(
        settings.runs_dir,
        args.video,
        generator=args.generator,
        mode=mode,
        subject=args.subject,
        depth_provider=args.depth_provider,
        character=args.character,
    )
    out(f"JOB {job.id}")
    prepare_job(job, services_factory(settings), status=out)
    record_spend(job, "prepare", estimate, settings.runs_dir)
    out(f"Next: watch {job.path('prepared.mp4')} then run `charswap status {job.id}`.")
    return 0


def _resume(args, settings, out, confirm, services_factory):
    job = Job.load(settings.runs_dir, args.job)
    if job.mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    out(f"Resuming {job.id}; finished steps are skipped and saved predictions are re-checked.")
    prepare_job(job, services_factory(settings), status=out)
    if not any(c["step"] == "prepare" for c in job.state.get("costs", [])):
        record_spend(job, "prepare", estimate_prepare(job.mode, seconds=30), settings.runs_dir)
    return 0


def _status(args, settings, out, *_):
    job = Job.load(settings.runs_dir, args.job)
    state = job.state
    out(json.dumps({"job": job.id, "mode": job.mode, "stages": state["stages"], "review": review_status(job)}, indent=2))
    return 0


def _review(args, settings, out, *_):
    job = Job.load(settings.runs_dir, args.job)
    result = decide(job, approved=args.approve, note=args.note, fingerprint=args.fingerprint)
    out(f"Review {result['status']}.")
    return 0


def _prompt(args, settings, out, *_):
    job = Job.load(settings.runs_dir, args.job)
    state = job.state
    out(build_prompt(mode=job.mode, scene=state.get("scene"), transcript=state.get("transcript")))
    return 0


def _repair(args, settings, out, confirm, services_factory):
    job = Job.load(settings.runs_dir, args.job)
    out("Repair re-runs SAM 3:\n" + estimate_repair().describe())
    if not args.yes and not confirm("Continue?"):
        return 1
    repair_with_rgb(job, services_factory(settings), status=out)
    record_spend(job, "repair", estimate_repair(), settings.runs_dir)
    return 0


def _doctor(args, settings, out, *_):
    ok = True
    out(f"python: {sys.version.split()[0]}")
    out(f"pyav (ffmpeg): {av.__version__}")
    for binary in ("rubberband",):
        found = shutil.which(binary)
        ok &= bool(found)
        out(f"{binary}: {'found' if found else 'MISSING (macOS: brew install rubberband)'}")
    for key in ("REPLICATE_API_TOKEN", "PIAPI_API_KEY"):
        out(f"{key}: {'set' if settings.get(key) else 'missing'}")
    out(f"hosting: {settings.hosting_mode}")
    return 0 if ok else 1


COMMANDS = {
    "prepare": _prepare,
    "resume": _resume,
    "status": _status,
    "review": lambda a, s, o, *r: _review(a, s, o) if a.approve or a.reject else 2,
    "prompt": _prompt,
    "repair": _repair,
    "doctor": _doctor,
}
```

- [ ] **Step 4: Run** `uv run pytest tests/test_cli.py -q --no-cov` → `9 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/cli.py tests/test_cli.py
git commit -m "feat: add charswap CLI (prepare, resume, status, review, repair, prompt, doctor)"
```

---

### Task 19: Full verification, coverage gate, journal

- [ ] **Step 1: Full suite with coverage**

Run: `uv run pytest`
Expected: all tests pass; `Required test coverage of 85% reached`. If below 85 %, add tests for the uncovered lines shown in `term-missing` (do not add `pragma: no cover` except on real-network/interactive code already marked).

- [ ] **Step 2: Lint/format check**

Run: `uv run ruff format --check src tests && uv run ruff check src tests` → no findings (run `ruff check --fix` first if imports were hand-written).

- [ ] **Step 3: CLI smoke test (free)**

Run: `uv run charswap doctor` → shows python 3.12, rubberband found, key status, hosting mode.
Run: `uv run charswap --help` → lists the seven commands.

- [ ] **Step 4: Optional live smoke (costs ~$0.07, ask Frank first)** — with a real 5 s clip and `REPLICATE_API_TOKEN` set: `uv run charswap prepare clip.mp4 --mode depth`, then open `runs/<id>/prepared.mp4`. Also run `--mode face_mesh` (free model download, ~$0.03 Replicate) to exercise `MediaPipeDetector`.

- [ ] **Step 5: Update `activity.md` and `docs/scratchpad.md`** (mark Plan 1 done, record any surprises), then commit:

```bash
git add activity.md docs/scratchpad.md
git commit -m "docs: log Plan 1 completion"
```

---

## Verified provider facts

Verified 2026-10-22 from provider docs/model pages (used in Tasks 11, 15, 17):

- **Whisper:** `openai/whisper` (large-v3) is a *community* model → call by version `8099696689d249cf8b122d833c36ac3f75505c666a395ca40ef26f68e7d3d16e`; inputs `audio`, `transcription="plain text"`; text at `output.transcription`; ≈ $0.00075/run.
- **Scene description:** `openai/gpt-4o-mini` (official → `/v1/models/{model}/predictions`); inputs `prompt`, `image_input` (array), `max_completion_tokens`; output is an array of string tokens; $0.15/M input, $0.60/M output tokens (≈ $0.001 per call).
- **Demucs** `cjwbw/demucs` pinned version is still latest (≈ $0.017/run). **SAM 3** `lucataco/sam3-video` pinned version is still latest (≈ $0.002/run). **Depth** `lucataco/depth-anything-video` latest `9143691405afc64c7952499a1e81e3f779535a8916c8da7154a9995f145d5e6d` (≈ $0.040/run); resolved at run time.
- **Wan 2.2 Animate Replace:** max input length not published ("long videos process in segments"); keep `GENERATOR_LIMITS["wan"] = (1, 30)` and revisit after the first live run. Billed per output second: 480p $0.02/s, 720p $0.05/s (used in Plan 2).
- **Seedance 2.5 via PiAPI** (Plan 2): raw video refs 2–30 s; assets/auto-upload video 2–15 s. Keep Seedance at (4, 30) for prep; Plan 2 re-checks against the request path actually used.
