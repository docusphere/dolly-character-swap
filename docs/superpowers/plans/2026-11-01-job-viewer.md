# Character Swap — Job Viewer (read-only local UI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run charswap view` opens a local, read-only page per job that shows every preparation step's output in order, a plain-English "Why this step?" line per step, live progress while a job prepares, a link from each paid step to its exact Replicate run, and a costs summary per job and across jobs.

**Architecture:** A new `character_swap.web` package. `view_model.py` holds all logic as pure functions over a `Job` folder (one section table, statuses, links, costs) and is unit-tested without HTTP. `app.py` is a FastAPI app factory `create_app(settings)` with GET-only routes that render Jinja2 templates and serve allowlisted media files through `Job.path` containment; vanilla JS adds click-to-scroll, scroll-spy and 3 s polling. The pipeline gains `running`/`failed` stage statuses (spec decision V6) so the page can show live progress. `charswap view` binds to 127.0.0.1 only.

**Tech Stack:** Python 3.12, uv, FastAPI (Starlette 1.x), uvicorn, Jinja2, vanilla JS/CSS; pytest + pytest-cov (≥ 85 % enforced) with FastAPI `TestClient` (uses the existing `httpx` dependency), ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-job-viewer-design.md`

**Builds on:** Plan 1 (`docs/superpowers/plans/2026-10-22-plan-1-foundation-and-prep.md`), as hardened after review. All code below targets the current APIs: `Job.mark_stage(name, status="done", **info)`, `Job.path()` containment, `review.status()`, `costs.estimate_stages()`/`total_spend()`, `pipeline.PAID_STAGES`, `cli.Env`/`main()`.

## Conventions for every task

- Run commands from the repo root: `/Users/franciscojosejimeneznillardkam/Documents/Frank DOCS/Frank Projects/youtube/hf-genjutsu/character-swap` (quote it — the path has spaces).
- Tests: `uv run pytest <path> -q --no-cov` while iterating a single file (the coverage gate applies to the full suite); full suite `uv run pytest`.
- Lint/format before every commit: `uv run ruff format src tests && uv run ruff check --fix src tests` (format first; `--fix` sorts imports: `helpers`/`fakes`/`viewer_jobs` count as first-party).
- Stage files **by name**. Never `git add -A` / `git add .`. No attribution lines in commit messages.
- Never commit anything under `_legacy/`, `.env`, `runs/`, `models/`, `.superpowers/`.
- The viewer never changes job state and never spends money: GET routes only, no buttons that act. Approve/repair/generate stay in the CLI (Plan 3).

## File map

| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | add `fastapi`, `uvicorn`, `jinja2` (Task 1) |
| `src/character_swap/pipeline.py` | `_tracked()` records `running` → `done`/`failed` for every stage and for repair (Task 2) |
| `src/character_swap/web/__init__.py` | package marker |
| `src/character_swap/web/view_model.py` | the section table + pure functions: statuses, progress, links, costs, job list (Task 3) |
| `src/character_swap/web/app.py` | `create_app(settings)`: routes, 404/405 pages, file serving with Range, local-host guard (Task 4) |
| `src/character_swap/web/templates/base.html` | page shell, skip link, CSS/JS includes |
| `src/character_swap/web/templates/jobs.html` | home: jobs table + empty state |
| `src/character_swap/web/templates/job.html` | job page: fixed navigator + tall scrolling sections + costs |
| `src/character_swap/web/templates/message.html` | 404 / "cannot be shown" page with a link home |
| `src/character_swap/web/static/viewer.css` | design tokens (light/dark), layout, focus, mobile |
| `src/character_swap/web/static/viewer.js` | click-to-scroll, scroll-spy, polling with stop rules |
| `src/character_swap/cli.py` | `charswap view [--port] [--no-browser]`; `Env.serve`/`Env.open_browser` (Task 5) |
| `tests/viewer_jobs.py` | builds job folders with real tiny artifacts + fake prediction records |
| `tests/conftest.py` | `media` fixture |
| `tests/test_smoke.py`, `tests/test_pipeline.py`, `tests/test_view_model.py`, `tests/test_web_app.py`, `tests/test_cli.py` | tests per task |
| `CLAUDE.md`, `activity.md`, `docs/scratchpad.md` | stack line, journal, checklist (Task 6) |

**Note on templates:** the spec lists `base`, `jobs`, `job`; this plan adds `message.html` so the 404 page, the 405 page and the "damaged state.json" page share one template instead of three inline HTML strings.

---

### Task 1: Add the web dependencies

**Files:** Modify `pyproject.toml`, `uv.lock` (by `uv sync`), `tests/test_smoke.py`

- [ ] **Step 1: Failing test** — append to `tests/test_smoke.py`:

```python
def test_web_stack_is_installed():
    import jinja2
    import uvicorn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    assert TestClient(app).get("/ping").json() == {"ok": True}
    assert jinja2.__version__ and uvicorn.__version__
```

- [ ] **Step 2: Run** `uv run pytest tests/test_smoke.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'jinja2'` (or `fastapi`).

- [ ] **Step 3: Add the dependencies.** In `pyproject.toml`, `[project].dependencies` becomes (three lines added at the end; `httpx`, which `TestClient` needs, is already there):

```toml
dependencies = [
  "av>=17,<18",
  "numpy>=2.2",
  "pillow>=12",
  "scipy>=1.15",
  "httpx>=0.28",
  "mediapipe>=1.1",
  "gradio-client>=2.7",
  "fastapi>=0.162.0",
  "uvicorn>=0.54.0",
  "jinja2>=3.1.6",
]
```

Then install: `uv sync` (updates `uv.lock`; resolved at plan time: fastapi 0.162, starlette 1.10, uvicorn 0.54, jinja2 3.1.6).

- [ ] **Step 4: Run** `uv run pytest tests/test_smoke.py -q --no-cov` → `2 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add pyproject.toml uv.lock tests/test_smoke.py
git commit -m "build: add fastapi, uvicorn and jinja2 for the job viewer"
```

---

### Task 2: Pipeline records `running` and `failed` stage statuses (spec V6)

**Files:** Modify `src/character_swap/pipeline.py`, `tests/test_pipeline.py`

Rules (from the spec): a stage is marked `running` (with `started_at`) before its work, `done` after, `failed` with the error message when it raises — `BaseException` is caught for the marking so Ctrl-C records `"interrupted"`, then everything is re-raised. `stage_done()` is unchanged (only `done` counts), so resume still re-runs `running`/`failed` stages. `repair_with_rgb` does the same for composite / face_mesh / embed.

**Interpretation:** the repair's new SAM run (`Repair: masks`) is tracked under `composite`, because the stage table has no separate repair stage and that cut only exists to feed the composite. Without this, a failed repair SAM call would leave composite/embed silently "pending" (they are reset first) and the viewer would keep polling.

- [ ] **Step 1: Failing tests** — append to `tests/test_pipeline.py`:

```python
# ---- stage status tracking (viewer V6) -----------------------------------------
def _direct_job(make_job, tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"png")
    return make_job("direct", generator="wan", character=face)


def test_stage_is_running_while_its_work_runs(make_job, services, tmp_path, monkeypatch):
    job = _direct_job(make_job, tmp_path)
    seen = {}
    real = pipeline.normalize

    def watching_normalize(*args, **kwargs):
        seen.update(job.state["stages"]["normalize"])
        return real(*args, **kwargs)

    monkeypatch.setattr("character_swap.pipeline.normalize", watching_normalize)
    prepare(job, services, status=lambda _m: None)
    assert seen["status"] == "running" and seen["started_at"]
    entry = job.state["stages"]["normalize"]
    assert entry["status"] == "done" and entry["started_at"] == seen["started_at"]


def test_failed_stage_records_its_error(make_job, services, tmp_path, monkeypatch):
    job = _direct_job(make_job, tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("character_swap.pipeline.normalize", broken)
    with pytest.raises(StageError, match="disk full"):
        prepare(job, services, status=lambda _m: None)
    entry = job.state["stages"]["normalize"]
    assert entry["status"] == "failed"
    assert entry["error"] == "Normalizing video to 24 fps failed: disk full"
    assert not job.stage_done("normalize")


def test_interrupted_stage_is_marked_failed_and_reraised(make_job, services, tmp_path, monkeypatch):
    job = _direct_job(make_job, tmp_path)

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("character_swap.pipeline.normalize", stop)
    with pytest.raises(KeyboardInterrupt):
        prepare(job, services, status=lambda _m: None)
    entry = job.state["stages"]["normalize"]
    assert entry["status"] == "failed" and entry["error"] == "interrupted"


@pytest.mark.parametrize("leftover", ["running", "failed"])
def test_resume_reruns_running_or_failed_stages(make_job, services, tmp_path, leftover):
    job = _direct_job(make_job, tmp_path)
    job.mark_stage("normalize", leftover, error="x")
    prepare(job, services, status=lambda _m: None)
    assert job.stage_done("normalize") and job.path("original.mp4").exists()


def test_failed_stage_keeps_earlier_stages_done(make_job, services, monkeypatch):
    job = make_job("face_mesh")

    def broken(*args, **kwargs):
        raise RuntimeError("whisper down")

    monkeypatch.setattr("character_swap.pipeline.transcribe", broken)
    with pytest.raises(StageError):
        prepare(job, services, status=lambda _m: None)
    stages = job.state["stages"]
    assert [stages[n]["status"] for n in ("normalize", "separate", "pitch")] == ["done"] * 3
    assert stages["transcribe"]["status"] == "failed" and "describe" not in stages


def test_repair_failure_marks_composite_failed(make_job, services, monkeypatch):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)

    def broken(*args, **kwargs):
        raise RuntimeError("sam down")

    monkeypatch.setattr("character_swap.pipeline.segment", broken)
    with pytest.raises(StageError, match="Repair: masks failed: sam down"):
        repair_with_rgb(job, services, status=lambda _m: None)
    stages = job.state["stages"]
    assert stages["composite"]["status"] == "failed"
    assert "sam down" in stages["composite"]["error"]
    assert "embed" not in stages  # reset by repair, never started


def test_repair_marks_running_then_done(make_job, services, monkeypatch):
    job = make_job("depth_mesh")
    prepare(job, services, status=lambda _m: None)
    seen = []
    real = pipeline.embed_samples

    def watching_embed(*args, **kwargs):
        seen.append(job.state["stages"]["embed"]["status"])
        return real(*args, **kwargs)

    monkeypatch.setattr("character_swap.pipeline.embed_samples", watching_embed)
    repair_with_rgb(job, services, status=lambda _m: None)
    assert seen == ["running"]
    stages = job.state["stages"]
    assert all(stages[n]["status"] == "done" for n in ("composite", "face_mesh", "embed"))
```

- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py -q --no-cov`
Expected: FAIL — `6 failed, 22 passed`. The two `test_resume_reruns_running_or_failed_stages` cases already pass: they guard the unchanged resume semantics.

- [ ] **Step 3: Implement.** In `src/character_swap/pipeline.py`:

(a) add the import next to the other stdlib imports:

```python
from datetime import UTC, datetime
```

(b) replace the whole `_stage` function with `_tracked` + the new `_stage`:

```python
@contextmanager
def _tracked(job: Job, name: str) -> Iterator[None]:
    """Record running -> done/failed in state.json so viewers can show live progress.

    BaseException on purpose: Ctrl-C records "interrupted" instead of leaving the stage
    "running". Only "done" counts for stage_done(), so resume re-runs running/failed stages.
    """
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    job.mark_stage(name, "running", started_at=started_at)
    try:
        yield
    except KeyboardInterrupt:
        job.mark_stage(name, "failed", started_at=started_at, error="interrupted")
        raise
    except BaseException as exc:
        error = str(exc) or type(exc).__name__
        job.mark_stage(name, "failed", started_at=started_at, error=error)
        raise
    job.mark_stage(name, started_at=started_at)


def _stage(job: Job, name: str, label: str, work: Callable[[], object], status: Status) -> None:
    if job.stage_done(name):
        _notify(status, f"{label} · already done")
        return
    _notify(status, label)
    clear_review(job)  # any new work invalidates a previous approval
    with _tracked(job, name), _wrap(label):
        work()
```

(c) in `repair_with_rgb`, replace this block:

```python
    with _wrap("Repair: masks"):
        _masks(job, services.replicate, "original.mp4", folder="rgb-repair")
    with _wrap("Repair: composite"):
        _composite(job, folder="rgb-repair")
    job.mark_stage("composite")
    if job.mode == "depth_mesh":
        with _wrap("Repair: mesh"):
            job.path("depth-composite-silent.mp4").unlink(missing_ok=True)
            _mesh(job, services)
        job.mark_stage("face_mesh")
    with _wrap("Repair: embed"):
        _embed(job)
    job.mark_stage("embed")
```

with:

```python
    with _tracked(job, "composite"):  # the new SAM cut belongs to the composite it feeds
        with _wrap("Repair: masks"):
            _masks(job, services.replicate, "original.mp4", folder="rgb-repair")
        with _wrap("Repair: composite"):
            _composite(job, folder="rgb-repair")
    if job.mode == "depth_mesh":
        with _tracked(job, "face_mesh"), _wrap("Repair: mesh"):
            job.path("depth-composite-silent.mp4").unlink(missing_ok=True)
            _mesh(job, services)
    with _tracked(job, "embed"), _wrap("Repair: embed"):
        _embed(job)
```

The `job.reset_stages(*redo)` line above it stays: it still runs first, so a crash before the composite starts leaves those stages un-done.

- [ ] **Step 4: Run** `uv run pytest tests/test_pipeline.py tests/test_cli.py -q --no-cov` → `28 passed` for the pipeline file and all CLI tests still pass (the existing `test_crash_during_repair_is_healed_by_prepare` now sees `composite` as `failed`, which is still "not done").

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/pipeline.py tests/test_pipeline.py
git commit -m "feat: record running and failed stage statuses (incl. interrupt) in state.json"
```

---

### Task 3: `web/view_model.py` — sections, statuses, links, costs

**Files:** Create `src/character_swap/web/__init__.py`, `src/character_swap/web/view_model.py`, `tests/viewer_jobs.py`, `tests/test_view_model.py`; modify `tests/conftest.py`, `tests/test_pipeline.py`

Key rules implemented here (all from the spec):
- **Stages per mode** (`STAGES_BY_MODE`, defines "N/M steps"): depth 9 · depth_mesh 10 · face_mesh 7 · direct 1. A pipeline test runs `prepare()` for every mode and asserts the recorded stage order equals this table, so the two can never drift.
- **Section status:** failed if any of its stages failed; running if any running; done if all done; otherwise pending. Review comes from `review.status(job)`: approved → ✓ done, rejected → ✕ failed, otherwise ◻ pending.
- **Active job** (drives polling): any stage running, or not all done and none failed.
- **Media per file:** a file whose stage is not done shows "Waiting for this step…"; a done stage with a missing file shows "File missing" (the page never breaks).
- **depth_mesh composite:** `depth-composite-silent.mp4` once it exists (or once face_mesh has started, since the mesh step creates it first); otherwise `composite-silent.mp4`. It never falls back to the meshed video once face_mesh is done.
- **Replicate links** from saved records (`demucs-`, `whisper-`, `describe-`, `depth-prediction.json`, `sam/sam-prediction.json`); after an RGB repair (`mask_source == "rgb"`) the mask section links "First attempt ↗" and "Repair ↗" (`rgb-repair/sam-prediction.json`). Missing/unreadable records, the `{"status": "creating"}` marker without an `id`, or an id with unexpected characters → no link. Hugging Face depth writes no record → no link.
- **Costs:** one row per *done* paid stage of the mode (`pipeline.PAID_STAGES`), estimate via `costs.estimate_stages([stage], 30)`; Hugging Face depth is a special-cased $0 row ("Hugging Face · free"). Footer: completed-steps sum, logged `state["costs"]` sum, all-jobs `costs.total_spend(runs_dir)`.
- **Home list** reads `state.json` only (no review hashing). Damaged `state.json` → listed as "Unreadable: …".

**Interpretations:**
- Copy: the spec's draft "why" lines are used verbatim, except the direct-mode line, which loses its em dash ("…onto your character, so no disguise is needed.") per `docs/ui-ux-best-practices.md` (no em dashes in body copy). A test enforces no em dashes in any "why" text.
- Section numbers are the order shown on the page (1…n per mode), not the spec's table numbers (which skip numbers per mode).
- The review section shows status, note, a 12-character fingerprint and, unless approved, the exact copy-ready `charswap review <id> --approve --note "watched all" --fingerprint <full>` command.
- "Seconds, size" on the upload section = duration from `media.video.stream_seconds` and file size; an unreadable video says "unreadable video · <size>" instead of raising.

- [ ] **Step 1: Test job builder** — `tests/viewer_jobs.py` (real tiny artifacts via `tests/helpers.py`, stages marked done via `Job.mark_stage`, fake prediction records):

```python
"""Build job folders with real tiny artifacts for the viewer tests. No pipeline, no network."""

import json
import shutil
from pathlib import Path

from PIL import Image

from character_swap.jobs import Job
from character_swap.web.view_model import STAGES_BY_MODE
from helpers import solid, tone, write_video, write_wav

# Files each stage leaves in the job folder (what the viewer looks for).
ARTIFACTS = {
    "normalize": ["original.mp4"],
    "separate": ["vocals.wav"],
    "pitch": ["vocals-plus3.wav"],
    "describe": ["scene.jpg"],
    "depth": ["depth.mp4"],
    "composite": ["mask.mp4", "composite-silent.mp4"],
    "face_mesh": ["composite-silent.mp4"],
    "embed": ["prepared.mp4"],
}
RECORDS = {
    "separate": "demucs-prediction.json",
    "transcribe": "whisper-prediction.json",
    "describe": "describe-prediction.json",
    "depth": "depth-prediction.json",
    "masks": "sam/sam-prediction.json",
}


class Media:
    """One encoded clip, one wav and one image per test, copied wherever a stage needs them."""

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.video = write_video(folder / "clip.mp4", [solid((180, 40, 40))] * 24, audio_seconds=1)
        self.wav = write_wav(folder / "voice.wav", tone(1))
        self.image = folder / "face.png"
        Image.new("RGB", (8, 8), (200, 150, 120)).save(self.image)
        jpg = folder / "scene.jpg"
        Image.new("RGB", (8, 8), (90, 90, 90)).save(jpg)
        self.jpg = jpg

    def copy(self, name: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".wav"):
            shutil.copyfile(self.wav, dest)
        elif name.endswith(".jpg"):
            shutil.copyfile(self.jpg, dest)
        else:
            shutil.copyfile(self.video, dest)


def write_record(job: Job, name: str, data: dict) -> None:
    path = job.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def finish_stage(job: Job, media: Media, name: str) -> None:
    """Leave exactly what the real stage leaves, then mark it done."""
    for artifact in ARTIFACTS.get(name, []):
        media.copy(artifact, job.path(artifact))
    if name == "face_mesh":
        if job.mode == "depth_mesh":
            media.copy("x.mp4", job.path("depth-composite-silent.mp4"))
        job.path("face-landmarks.json").write_text(json.dumps({"fps": 24, "frames": []}))
        report = {"frames": 24, "tracked": 24, "missing_frames": []}
        job.path("face-tracking.json").write_text(json.dumps(report))
    if name == "transcribe":
        job.update(transcript="hello there")
    if name == "describe":
        job.update(scene="A bright studio with plants.")
    if name in RECORDS and not (name == "depth" and job.state["depth_provider"] == "huggingface"):
        write_record(job, RECORDS[name], {"id": f"pred-{name}", "status": "succeeded"})
    job.mark_stage(name)


def make_job(
    runs_dir: Path,
    media: Media,
    mode: str = "depth",
    *,
    done: int | None = None,
    running: bool = False,
    failed: str | None = None,
    depth_provider: str = "replicate",
) -> Job:
    """A job whose first `done` stages are finished (all when None).

    The next stage is marked running when `running`, or failed with message `failed`.
    """
    generator = "wan" if mode == "direct" else "seedance"
    character = media.image if mode == "direct" else None
    job = Job.create(
        runs_dir,
        media.video,
        generator=generator,
        mode=mode,
        depth_provider=depth_provider,
        character=character,
    )
    stages = STAGES_BY_MODE[mode]
    count = len(stages) if done is None else done
    for name in stages[:count]:
        finish_stage(job, media, name)
    if count < len(stages):
        if running:
            job.mark_stage(stages[count], "running", started_at="2026-11-01T10:00:00+00:00")
        elif failed is not None:
            job.mark_stage(stages[count], "failed", error=failed)
    return job
```

- [ ] **Step 2: Fixture** — in `tests/conftest.py` add the import below `from fakes import FakeReplicate`:

```python
from viewer_jobs import Media
```

and this fixture after `runs_dir`:

```python
@pytest.fixture
def media(tmp_path):
    """Tiny real clip/wav/images for job-viewer tests (see tests/viewer_jobs.py)."""
    return Media(tmp_path / "media")
```

- [ ] **Step 3: Failing tests** — `tests/test_view_model.py`:

```python
import json

import pytest

from character_swap.costs import Estimate, Line, estimate_prepare, estimate_stages, record_spend
from character_swap.pipeline import PAID_STAGES
from character_swap.review import decide, fingerprint
from character_swap.web import view_model as vm
from viewer_jobs import make_job, write_record

SECTION_IDS = {
    "depth": ["upload", "vocals", "transcript", "depth", "mask", "prepared", "review"],
    "depth_mesh": [
        "upload",
        "vocals",
        "transcript",
        "depth",
        "mask",
        "face-mesh",
        "prepared",
        "review",
    ],
    "face_mesh": ["upload", "vocals", "transcript", "face-mesh", "prepared", "review"],
    "direct": ["upload", "source", "review"],
}


def sections(view):
    return {section.id: section for section in view.sections}


# ---- tables ----------------------------------------------------------------------
@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_sections_per_mode(mode):
    assert [section.id for section in vm.sections_for(mode)] == SECTION_IDS[mode]


@pytest.mark.parametrize(("mode", "count"), [("depth", 9), ("depth_mesh", 10), ("face_mesh", 7)])
def test_stage_counts_match_spec(mode, count):
    assert len(vm.STAGES_BY_MODE[mode]) == count
    assert vm.STAGES_BY_MODE["direct"] == ("normalize",)


@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_sections_cover_every_stage_of_the_mode(mode):
    covered = {stage for section in vm.sections_for(mode) for stage in section.stages}
    assert covered == set(vm.STAGES_BY_MODE[mode])


@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_paid_stages_are_a_subset_of_the_mode(mode):
    assert set(PAID_STAGES[mode]) <= set(vm.STAGES_BY_MODE[mode])


def test_every_section_explains_why():
    for section in vm.SECTIONS:
        assert section.title and len(section.why) > 40, section.id
        assert "—" not in section.why  # UI guide: no em dashes in body copy


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["done", "failed"], "failed"),
        (["running", "failed"], "failed"),
        (["done", "running"], "running"),
        (["done", "done"], "done"),
        (["done", "pending"], "pending"),
        ([], "pending"),
    ],
)
def test_combine(statuses, expected):
    assert vm.combine(statuses) == expected


# ---- progress ------------------------------------------------------------------
def test_progress_of_fresh_job_is_active(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    info = vm.progress(job.state)
    assert info == {
        "stages": dict.fromkeys(vm.STAGES_BY_MODE["depth"], "pending"),
        "done_count": 0,
        "total_count": 9,
        "active": True,
    }


def test_progress_running_and_finished(runs_dir, media):
    running = make_job(runs_dir, media, "face_mesh", done=3, running=True)
    info = vm.progress(running.state)
    assert info["stages"]["transcribe"] == "running" and info["done_count"] == 3
    assert info["active"] is True
    finished = make_job(runs_dir, media, "face_mesh")
    assert vm.progress(finished.state)["active"] is False
    assert vm.progress(finished.state)["done_count"] == 7


def test_progress_stops_being_active_on_failure(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=2, failed="boom")
    info = vm.progress(job.state)
    assert info["stages"]["pitch"] == "failed" and info["active"] is False


def test_unknown_stage_status_counts_as_pending(runs_dir, media):
    job = make_job(runs_dir, media, "direct", done=0)
    job.mark_stage("normalize", "weird")
    assert vm.progress(job.state)["stages"] == {"normalize": "pending"}


# ---- job page: statuses ----------------------------------------------------------
@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_finished_job_page(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    view = vm.job_view(job, runs_dir)
    assert [s.id for s in view.sections] == SECTION_IDS[mode]
    assert [s.number for s in view.sections] == list(range(1, len(SECTION_IDS[mode]) + 1))
    for section in view.sections[:-1]:
        assert section.status == "done", section.id
        assert all(item.state == "ready" for item in section.media), section.id
    assert view.nav[-1] == vm.NavItem("costs", "Costs", "costs", "$", "summary")
    assert [item.id for item in view.nav[:-1]] == SECTION_IDS[mode]
    assert view.status == "Ready for review" and view.progress["active"] is False


def test_running_section_and_navigator(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=7, running=True)  # composite running
    view = vm.job_view(job, runs_dir)
    mask = sections(view)["mask"]
    assert mask.status == "running"
    assert [item.state for item in mask.media] == ["waiting", "waiting"]
    nav = {item.id: item for item in view.nav}
    assert nav["mask"].icon == "⟳" and nav["mask"].status_text == "running"
    assert nav["depth"].icon == "✓" and nav["prepared"].icon == "◻"
    assert view.status == "Preparing"


def test_failed_section_shows_error(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, failed="Generating depth video failed: x")
    view = vm.job_view(job, runs_dir)
    depth = sections(view)["depth"]
    assert depth.status == "failed" and depth.error == "Generating depth video failed: x"
    assert {item.id: item.icon for item in view.nav}["depth"] == "✕"
    assert view.status == "Failed"
    assert sections(view)["vocals"].error is None


def test_failed_stage_without_message_gets_generic_error(runs_dir, media):
    job = make_job(runs_dir, media, "direct", done=0)
    job.mark_stage("normalize", "failed")
    assert sections(vm.job_view(job, runs_dir))["upload"].error == "This step failed."


def test_partially_done_section_shows_finished_files(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=2)  # separate done, pitch not yet
    vocals = sections(vm.job_view(job, runs_dir))["vocals"]
    assert vocals.status == "pending"
    assert [item.state for item in vocals.media] == ["ready", "ready", "waiting"]
    assert vocals.media[1].url == f"/jobs/{job.id}/files/vocals.wav"
    assert [item.kind for item in vocals.media] == ["audio", "audio", "audio"]


# ---- review section ------------------------------------------------------------
def test_review_pending_shows_approve_command(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    view = vm.job_view(job, runs_dir)
    review = sections(view)["review"]
    texts = {text.label: text for text in review.texts}
    full = fingerprint(job)
    assert review.status == "pending" and texts["Status"].value == "pending"
    assert texts["Fingerprint"].value == full[:12]
    command = texts["Approve after watching"]
    assert command.code and f"charswap review {job.id} --approve" in command.value
    assert command.value.endswith(f"--fingerprint {full}")


def test_review_approved_and_rejected(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    view = vm.job_view(job, runs_dir)
    review = sections(view)["review"]
    assert review.status == "done" and view.status == "Approved"
    labels = [text.label for text in review.texts]
    assert "Note" in labels and "Approve after watching" not in labels
    decide(job, approved=False, note="mask flickers", fingerprint=fingerprint(job))
    view = vm.job_view(job, runs_dir)
    assert sections(view)["review"].status == "failed" and view.status == "Rejected"


def test_review_unavailable_before_prepare(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    review = sections(vm.job_view(job, runs_dir))["review"]
    assert review.status == "pending"
    assert [(t.label, t.value) for t in review.texts][:2] == [
        ("Status", "unavailable"),
        ("Note", "Prepare the video first."),
    ]


def test_review_read_errors_show_unavailable(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "direct")

    def broken(_job):
        raise OSError("permission denied")

    monkeypatch.setattr(vm.review, "status", broken)
    assert vm.review_status(job)["status"] == "unavailable"


# ---- replicate links -----------------------------------------------------------
def test_paid_sections_link_to_replicate(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    by_id = sections(vm.job_view(job, runs_dir))
    assert by_id["vocals"].links == (
        vm.Link("View on Replicate ↗", "https://replicate.com/p/pred-separate"),
    )
    assert [link.url for link in by_id["transcript"].links] == [
        "https://replicate.com/p/pred-transcribe",
        "https://replicate.com/p/pred-describe",
    ]
    assert by_id["depth"].links[0].url == "https://replicate.com/p/pred-depth"
    assert by_id["mask"].links[0].url == "https://replicate.com/p/pred-masks"
    assert by_id["prepared"].links == () and by_id["upload"].links == ()


@pytest.mark.parametrize(
    "content",
    [
        None,  # missing record
        "{not json",
        json.dumps({"status": "creating", "created_at": "2026-11-01T10:00:00"}),
        json.dumps(["pred-1"]),
        json.dumps({"id": "../../evil"}),
        json.dumps({"id": 42}),
    ],
)
def test_no_link_without_a_confirmed_prediction(runs_dir, media, content):
    job = make_job(runs_dir, media, "depth")
    record = job.path("demucs-prediction.json")
    if content is None:
        record.unlink()
    else:
        record.write_text(content)
    assert vm.prediction_url(job, "demucs-prediction.json") is None
    assert sections(vm.job_view(job, runs_dir))["vocals"].links == ()


def test_rgb_repair_links_both_attempts(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    write_record(job, "rgb-repair/sam-prediction.json", {"id": "pred-repair"})
    job.update(mask_source="rgb")
    links = sections(vm.job_view(job, runs_dir))["mask"].links
    assert links == (
        vm.Link("First attempt ↗", "https://replicate.com/p/pred-masks"),
        vm.Link("Repair ↗", "https://replicate.com/p/pred-repair"),
    )


def test_huggingface_depth_has_no_link_and_is_free(runs_dir, media):
    job = make_job(runs_dir, media, "depth", depth_provider="huggingface")
    view = vm.job_view(job, runs_dir)
    assert sections(view)["depth"].links == ()
    depth_row = next(row for row in view.costs.rows if row.step == "Depth video")
    assert depth_row == vm.CostRow("Depth video", "Hugging Face · free", 0.0, None)
    expected = round(estimate_prepare("depth", 30).total - 0.04, 4)
    assert view.costs.completed_usd == pytest.approx(expected)


# ---- costs ---------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_finished_job_costs_match_the_prepare_estimate(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    costs = vm.job_view(job, runs_dir).costs
    assert len(costs.rows) == len(PAID_STAGES[mode])
    assert all(row.provider == "Replicate" for row in costs.rows)
    assert costs.completed_usd == pytest.approx(round(estimate_prepare(mode, 30).total, 4))


def test_costs_count_only_completed_steps(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="whisper down")
    view = vm.job_view(job, runs_dir)
    assert [row.step for row in view.costs.rows] == ["Separate vocals"]
    assert view.costs.rows[0].link == "https://replicate.com/p/pred-separate"
    assert view.costs.completed_usd == pytest.approx(0.017)
    assert view.cost_usd == view.costs.completed_usd


def test_logged_and_all_jobs_spend(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    other = make_job(runs_dir, media, "face_mesh")
    assert vm.job_view(job, runs_dir).costs.logged_usd == 0
    record_spend(job, "prepare", Estimate((Line("a", 0.0188),)), runs_dir)
    record_spend(other, "prepare", Estimate((Line("a", 0.02),)), runs_dir)
    job.append_cost({"step": "odd", "usd": "not a number"})
    job.append_cost("garbage")
    costs = vm.job_view(job, runs_dir).costs
    assert costs.logged_usd == pytest.approx(0.0188)
    assert costs.all_jobs_usd == pytest.approx(0.0388)


def test_logged_cost_ignores_a_damaged_costs_field(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.update(costs="oops")
    assert vm.logged_cost(job.state) == 0.0


# ---- files and texts -------------------------------------------------------------
def test_done_step_with_missing_file_says_missing(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    job.path("depth.mp4").unlink()
    depth = sections(vm.job_view(job, runs_dir))["depth"]
    assert depth.status == "done"
    assert depth.media == (vm.MediaItem("Depth video", "video", None, "missing"),)


def test_depth_mesh_composite_fallback(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=8)  # composite done, face_mesh not
    composite = sections(vm.job_view(job, runs_dir))["mask"].media[1]
    assert composite.url == f"/jobs/{job.id}/files/composite-silent.mp4"
    job.mark_stage("face_mesh", "running")
    assert vm.composite_name(job, job.state) == "depth-composite-silent.mp4"

    done = make_job(runs_dir, media, "depth_mesh")
    composite = sections(vm.job_view(done, runs_dir))["mask"].media[1]
    assert composite.url == f"/jobs/{done.id}/files/depth-composite-silent.mp4"
    done.path("depth-composite-silent.mp4").unlink()
    composite = sections(vm.job_view(done, runs_dir))["mask"].media[1]
    assert composite.state == "missing"  # never falls back to the meshed video


def test_transcript_scene_and_upload_facts(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    by_id = sections(vm.job_view(job, runs_dir))
    texts = {text.label: text.value for text in by_id["transcript"].texts}
    assert texts == {"Transcript": "hello there", "Scene": "A bright studio with plants."}
    assert by_id["transcript"].media[0].kind == "image"
    upload = {text.label: text.value for text in by_id["upload"].texts}
    assert upload["Upload"].startswith("1.0 s · ") and upload["Normalized"].endswith("KB")


def test_empty_transcript_is_explained(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    job.update(transcript="", scene=None)
    texts = sections(vm.job_view(job, runs_dir))["transcript"].texts
    assert [t.value for t in texts] == ["(no speech found)", "(no description)"]


def test_unreadable_video_is_reported_not_raised(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.path("original.mp4").write_bytes(b"not a video" * 20_000)
    upload = {t.label: t.value for t in sections(vm.job_view(job, runs_dir))["upload"].texts}
    assert upload["Normalized"] == "unreadable video · 0.2 MB"


def test_face_tracking_summary(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    texts = sections(vm.job_view(job, runs_dir))["face-mesh"].texts
    assert texts == (vm.TextItem("Tracking", "Face found in 24 of 24 frames"),)
    report = {"frames": 24, "tracked": 12, "missing_frames": list(range(12, 24))}
    job.path("face-tracking.json").write_text(json.dumps(report))
    job.mark_stage("face_mesh", "failed", error="Face tracking lost the face")
    texts = sections(vm.job_view(job, runs_dir))["face-mesh"].texts
    assert texts[0].value.startswith("Face found in 12 of 24 frames · lost on frames 12, 13")
    assert texts[0].value.endswith(" …")
    job.path("face-tracking.json").write_text("{")
    texts = sections(vm.job_view(job, runs_dir))["face-mesh"].texts
    assert texts[0].value == "face-tracking.json missing or unreadable"


def test_direct_job_shows_character(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    source = sections(vm.job_view(job, runs_dir))["source"]
    assert [item.url for item in source.media] == [
        f"/jobs/{job.id}/files/original.mp4",
        f"/jobs/{job.id}/files/character-primary.png",
    ]
    job.update(character_primary="../outside.png")
    assert sections(vm.job_view(job, runs_dir))["source"].media[1].state == "missing"
    job.update(character_primary=None)
    assert sections(vm.job_view(job, runs_dir))["source"].media[1].state == "missing"


# ---- damaged state and the job list ----------------------------------------------
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "could not be read"),
        ("[]", "not a JSON object"),
        (json.dumps({"mode": "nope", "stages": {}}), "unknown mode"),
        (json.dumps({"mode": "depth", "stages": []}), "no stages table"),
    ],
)
def test_damaged_state_raises_view_error(runs_dir, media, content, message):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text(content)
    with pytest.raises(vm.ViewError, match=message):
        vm.job_view(job, runs_dir)
    [summary] = vm.list_jobs(runs_dir)
    assert summary.id == job.id and message in summary.error


def test_list_jobs_newest_first_with_progress(runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)  # separate + transcribe are paid
    new = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T10:00:00+00:00")
    (runs_dir / "spend-log.csv").write_text("at,job,step,usd_estimate,usd_micro\n")
    (runs_dir / "not-a-job").mkdir()
    (runs_dir / ("a" * 32)).mkdir()  # half-created: no state.json
    summaries = vm.list_jobs(runs_dir)
    assert [s.id for s in summaries] == [new.id, old.id]
    assert (summaries[1].done_count, summaries[1].total_count) == (4, 9)
    paid = estimate_stages(["separate", "transcribe"], 30).total
    assert summaries[1].cost_usd == pytest.approx(paid)
    assert summaries[1].short_id == old.id[:8] and summaries[1].error is None


def test_list_jobs_without_runs_folder(tmp_path):
    assert vm.list_jobs(tmp_path / "missing") == []
```

and append this table-sync test to `tests/test_pipeline.py` (it reuses the `_direct_job` helper from Task 2):

```python
@pytest.mark.parametrize("mode", ["depth", "face_mesh", "depth_mesh", "direct"])
def test_viewer_stage_table_matches_what_prepare_runs(make_job, services, tmp_path, mode):
    from character_swap.web.view_model import STAGES_BY_MODE

    job = _direct_job(make_job, tmp_path) if mode == "direct" else make_job(mode)
    prepare(job, services, status=lambda _m: None)
    assert tuple(job.state["stages"]) == STAGES_BY_MODE[mode]
```

- [ ] **Step 4: Run** `uv run pytest tests/test_view_model.py -q --no-cov`
Expected: FAIL — `ImportError while loading conftest` / `ModuleNotFoundError: No module named 'character_swap.web'` (every test file errors until the package exists, because `conftest.py` imports `viewer_jobs`).

- [ ] **Step 5: Implement.** `src/character_swap/web/__init__.py`:

```python
"""Read-only local job viewer (FastAPI + Jinja2)."""
```

`src/character_swap/web/view_model.py`:

```python
"""Everything the job viewer shows, computed from a job folder. Pure: no HTTP, no templates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import av

from character_swap import review
from character_swap.costs import estimate_stages, total_spend
from character_swap.jobs import Job, JobError
from character_swap.media.video import stream_seconds
from character_swap.pipeline import PAID_STAGES

# Stage order per mode, exactly as pipeline.prepare() runs them (a test keeps them in sync).
STAGES_BY_MODE: dict[str, tuple[str, ...]] = {
    "depth": (
        "normalize",
        "separate",
        "pitch",
        "transcribe",
        "describe",
        "depth",
        "masks",
        "composite",
        "embed",
    ),
    "depth_mesh": (
        "normalize",
        "separate",
        "pitch",
        "transcribe",
        "describe",
        "depth",
        "masks",
        "composite",
        "face_mesh",
        "embed",
    ),
    "face_mesh": ("normalize", "separate", "pitch", "transcribe", "describe", "face_mesh", "embed"),
    "direct": ("normalize",),
}
SEEDANCE_PREP = ("depth", "face_mesh", "depth_mesh")
ALL_MODES = (*SEEDANCE_PREP, "direct")
ICONS = {"done": "✓", "pending": "◻", "running": "⟳", "failed": "✕"}
STATUS_WORDS = {"done": "done", "pending": "waiting", "running": "running", "failed": "failed"}
ESTIMATE_SECONDS = 30  # per-run prices ignore length; matches the CLI's "up to 30 s" estimate
STEP_NAMES = {
    "separate": "Separate vocals",
    "transcribe": "Transcript",
    "describe": "Scene description",
    "depth": "Depth video",
    "masks": "SAM 3 mask",
}
_PREDICTION_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")


class ViewError(ValueError):
    """The job folder cannot be shown (for example a damaged state.json)."""


@dataclass(frozen=True)
class Asset:
    label: str
    name: str  # file inside the job folder; "@composite" / "@character" are resolved per job
    kind: str  # "video" | "audio" | "image": which player the page uses
    stage: str | None  # the stage that writes it; None = present from job creation


@dataclass(frozen=True)
class Record:
    label: str
    name: str  # saved prediction record inside the job folder


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    why: str
    modes: tuple[str, ...]
    stages: tuple[str, ...]
    assets: tuple[Asset, ...] = ()
    records: tuple[Record, ...] = ()


ON_REPLICATE = "View on Replicate ↗"

# The single source of truth for what the job page shows, in page order.
SECTIONS: tuple[Section, ...] = (
    Section(
        id="upload",
        title="Upload & normalize",
        why=(
            "Every clip is converted to 24 frames per second, upright and at most 960 px, "
            "so every later step lines up frame by frame."
        ),
        modes=ALL_MODES,
        stages=("normalize",),
        assets=(
            Asset("Your upload", "upload.mp4", "video", None),
            Asset("Normalized (24 fps, upright)", "original.mp4", "video", "normalize"),
        ),
    ),
    Section(
        id="vocals",
        title="Vocals",
        why=(
            "The voice is separated from the music, then raised 3 semitones. The AI still "
            "hears the exact words to move the lips, but not the original voice or song."
        ),
        modes=SEEDANCE_PREP,
        stages=("separate", "pitch"),
        assets=(
            Asset("Original audio", "upload.mp4", "audio", None),
            Asset("Isolated vocals", "vocals.wav", "audio", "separate"),
            Asset("Vocals +3 semitones (what the AI hears)", "vocals-plus3.wav", "audio", "pitch"),
        ),
        records=(Record(ON_REPLICATE, "demucs-prediction.json"),),
    ),
    Section(
        id="transcript",
        title="Transcript & scene",
        why=(
            "The spoken words and a description of the room (never the people) go into the "
            "prompt, so the AI keeps the setting and lip-syncs the right words."
        ),
        modes=SEEDANCE_PREP,
        stages=("transcribe", "describe"),
        assets=(Asset("Frame used for the description", "scene.jpg", "image", "describe"),),
        records=(
            Record("Transcript on Replicate ↗", "whisper-prediction.json"),
            Record("Scene on Replicate ↗", "describe-prediction.json"),
        ),
    ),
    Section(
        id="depth",
        title="Depth",
        why=(
            "Depth turns everything into colored shapes by distance. The AI can no longer "
            "tell who the person is, but can still see exactly how they move."
        ),
        modes=("depth", "depth_mesh"),
        stages=("depth",),
        assets=(Asset("Depth video", "depth.mp4", "video", "depth"),),
        records=(Record(ON_REPLICATE, "depth-prediction.json"),),
    ),
    Section(
        id="mask",
        title="Mask & composite",
        why=(
            "SAM 3 cuts out the person. Only the person is replaced by depth; the real "
            "background stays, so the AI doesn't just trace an outline."
        ),
        modes=("depth", "depth_mesh"),
        stages=("masks", "composite"),
        assets=(
            Asset("Mask", "mask.mp4", "video", "composite"),
            Asset("Composite", "@composite", "video", "composite"),
        ),
        records=(Record(ON_REPLICATE, "sam/sam-prediction.json"),),
    ),
    Section(
        id="face-mesh",
        title="Face mesh",
        why=(
            "A wireframe tracks 478 points on the face so the new character copies every "
            "expression and lip movement."
        ),
        modes=("face_mesh", "depth_mesh"),
        stages=("face_mesh",),
        assets=(Asset("Face mesh video", "composite-silent.mp4", "video", "face_mesh"),),
    ),
    Section(
        id="prepared",
        title="Prepared video",
        why=(
            "This is exactly what the AI receives: motion and pitched voice, without the "
            "original identity."
        ),
        modes=SEEDANCE_PREP,
        stages=("embed",),
        assets=(Asset("Prepared video", "prepared.mp4", "video", "embed"),),
    ),
    Section(
        id="source",
        title="Source & character",
        why=(
            "Wan copies motion straight from the original video onto your character, "
            "so no disguise is needed."
        ),
        modes=("direct",),
        stages=("normalize",),
        assets=(
            Asset("Original (normalized)", "original.mp4", "video", "normalize"),
            Asset("Character", "@character", "image", None),
        ),
    ),
    Section(
        id="review",
        title="Review",
        why=(
            "Nothing is sent to the paid generator until you've watched the whole prepared "
            "clip and approved it."
        ),
        modes=ALL_MODES,
        stages=(),
    ),
)


@dataclass(frozen=True)
class MediaItem:
    label: str
    kind: str  # "video" | "audio" | "image"
    url: str | None
    state: str  # "ready" | "missing" | "waiting"


@dataclass(frozen=True)
class TextItem:
    label: str
    value: str
    code: bool = False


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class SectionView:
    id: str
    number: int
    title: str
    why: str
    status: str
    media: tuple[MediaItem, ...]
    texts: tuple[TextItem, ...]
    links: tuple[Link, ...]
    error: str | None = None


@dataclass(frozen=True)
class NavItem:
    id: str
    label: str
    status: str  # done/pending/running/failed, or "costs"
    icon: str
    status_text: str


@dataclass(frozen=True)
class CostRow:
    step: str
    provider: str
    usd: float
    link: str | None


@dataclass(frozen=True)
class CostsView:
    rows: tuple[CostRow, ...]
    completed_usd: float
    logged_usd: float
    all_jobs_usd: float


@dataclass(frozen=True)
class JobView:
    id: str
    short_id: str
    generator: str
    mode: str
    status: str
    cost_usd: float
    nav: tuple[NavItem, ...]
    sections: tuple[SectionView, ...]
    costs: CostsView
    progress: dict[str, Any]


@dataclass(frozen=True)
class JobSummary:
    id: str
    short_id: str
    generator: str
    mode: str
    done_count: int
    total_count: int
    cost_usd: float
    created_at: str
    error: str | None = None


# ---- small pure helpers --------------------------------------------------------
def read_state(job: Job) -> dict[str, Any]:
    """state.json, validated enough to render. Raises ViewError with a readable message."""
    try:
        state = job.state
    except (OSError, ValueError) as exc:
        raise ViewError(f"state.json could not be read: {exc}") from exc
    if not isinstance(state, dict):
        raise ViewError("state.json is not a JSON object.")
    if state.get("mode") not in STAGES_BY_MODE:
        raise ViewError(f"state.json has an unknown mode: {state.get('mode')!r}.")
    if not isinstance(state.get("stages"), dict):
        raise ViewError("state.json has no stages table.")
    return state


def stage_status(state: dict[str, Any], name: str) -> str:
    entry = state["stages"].get(name)
    status = entry.get("status") if isinstance(entry, dict) else None
    return status if status in ("done", "running", "failed") else "pending"


def combine(statuses: list[str]) -> str:
    """failed if any failed; running if any running; done if all done; otherwise pending."""
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    if statuses and all(status == "done" for status in statuses):
        return "done"
    return "pending"


def progress(state: dict[str, Any]) -> dict[str, Any]:
    """The cheap polling payload: reads state only (no hashing, no review)."""
    stages = {name: stage_status(state, name) for name in STAGES_BY_MODE[state["mode"]]}
    values = list(stages.values())
    done = values.count("done")
    failed = "failed" in values
    active = "running" in values or (done < len(values) and not failed)
    return {"stages": stages, "done_count": done, "total_count": len(values), "active": active}


def prediction_url(job: Job, record: str) -> str | None:
    """Link to a saved Replicate prediction; None when there is no confirmed prediction id."""
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    prediction_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(prediction_id, str) or not _PREDICTION_ID.fullmatch(prediction_id):
        return None  # includes the {"status": "creating"} marker written before a paid POST
    return f"https://replicate.com/p/{prediction_id}"


def file_url(job_id: str, name: str) -> str:
    return f"/jobs/{job_id}/files/{name}"


def composite_name(job: Job, state: dict[str, Any]) -> str:
    """depth_mesh draws the mesh over composite-silent.mp4; the depth-only copy is kept aside."""
    if state["mode"] != "depth_mesh":
        return "composite-silent.mp4"
    if job.path("depth-composite-silent.mp4").exists() or stage_status(state, "face_mesh") in (
        "done",
        "running",
    ):
        return "depth-composite-silent.mp4"
    return "composite-silent.mp4"


def _resolve(job: Job, state: dict[str, Any], name: str) -> str | None:
    if name == "@composite":
        return composite_name(job, state)
    if name == "@character":
        character = state.get("character_primary")
        return character if isinstance(character, str) else None
    return name


def _media(job: Job, state: dict[str, Any], asset: Asset) -> MediaItem:
    if asset.stage is not None and stage_status(state, asset.stage) != "done":
        return MediaItem(asset.label, asset.kind, None, "waiting")
    name = _resolve(job, state, asset.name)
    try:
        exists = name is not None and job.path(name).is_file()
    except JobError:
        exists = False
    if not exists:
        return MediaItem(asset.label, asset.kind, None, "missing")
    return MediaItem(asset.label, asset.kind, file_url(job.id, name), "ready")


def _size_text(path: Path) -> str:
    size = path.stat().st_size
    return f"{size / 1_000_000:.1f} MB" if size >= 100_000 else f"{size / 1000:.0f} KB"


def video_facts(path: Path) -> str:
    """'8.0 s · 1.2 MB'; a damaged or partial file is reported, never raised."""
    try:
        seconds = stream_seconds(path, "video")
    except (OSError, ValueError, av.error.FFmpegError):
        return f"unreadable video · {_size_text(path)}"
    return f"{seconds:.1f} s · {_size_text(path)}"


def _upload_texts(job: Job, state: dict[str, Any]) -> list[TextItem]:
    texts = []
    files = (("Upload", "upload.mp4", None), ("Normalized", "original.mp4", "normalize"))
    for label, name, stage in files:
        if stage and stage_status(state, stage) != "done":
            continue
        path = job.path(name)
        if path.is_file():
            texts.append(TextItem(label, video_facts(path)))
    return texts


def _transcript_texts(state: dict[str, Any]) -> list[TextItem]:
    texts = []
    if stage_status(state, "transcribe") == "done":
        texts.append(TextItem("Transcript", str(state.get("transcript") or "(no speech found)")))
    if stage_status(state, "describe") == "done":
        texts.append(TextItem("Scene", str(state.get("scene") or "(no description)")))
    return texts


def _tracking_texts(job: Job, state: dict[str, Any]) -> list[TextItem]:
    if stage_status(state, "face_mesh") not in ("done", "failed"):
        return []
    try:
        report = json.loads(job.path("face-tracking.json").read_text(encoding="utf-8"))
        frames, tracked = int(report["frames"]), int(report["tracked"])
        missing = list(report.get("missing_frames", []))
    except (OSError, ValueError, KeyError, TypeError):
        return [TextItem("Tracking", "face-tracking.json missing or unreadable")]
    text = f"Face found in {tracked} of {frames} frames"
    if missing:
        shown = ", ".join(str(frame) for frame in missing[:10])
        text += f" · lost on frames {shown}{' …' if len(missing) > 10 else ''}"
    return [TextItem("Tracking", text)]


def review_texts(job: Job, decision: dict[str, Any]) -> list[TextItem]:
    status = str(decision.get("status", "unavailable"))
    texts = [TextItem("Status", status)]
    if decision.get("note"):
        texts.append(TextItem("Note", str(decision["note"])))
    fingerprint = decision.get("fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        texts.append(TextItem("Fingerprint", fingerprint[:12]))
        if status != "approved":
            command = (
                f'charswap review {job.id} --approve --note "watched all" '
                f"--fingerprint {fingerprint}"
            )
            texts.append(TextItem("Approve after watching", command, code=True))
    return texts


def review_status(job: Job) -> dict[str, Any]:
    """review.status() hashes every reviewed file; any read problem shows as unavailable."""
    try:
        decision = review.status(job)
    except (OSError, ValueError) as exc:
        return {"approved": False, "status": "unavailable", "note": str(exc)}
    return decision if isinstance(decision, dict) else {"status": "unavailable"}


def _review_section_status(decision: dict[str, Any]) -> str:
    return {"approved": "done", "rejected": "failed"}.get(str(decision.get("status")), "pending")


def _links(job: Job, state: dict[str, Any], section: Section) -> list[Link]:
    if section.id == "mask" and state.get("mask_source") == "rgb":
        pairs = [
            ("First attempt ↗", "sam/sam-prediction.json"),
            ("Repair ↗", "rgb-repair/sam-prediction.json"),
        ]
    else:
        pairs = [(record.label, record.name) for record in section.records]
    links = []
    for label, record in pairs:
        url = prediction_url(job, record)
        if url:
            links.append(Link(label, url))
    return links


def _error(state: dict[str, Any], section: Section) -> str | None:
    for name in section.stages:
        entry = state["stages"].get(name)
        if isinstance(entry, dict) and entry.get("status") == "failed":
            return str(entry.get("error") or "This step failed.")
    return None


# ---- costs -------------------------------------------------------------------
_PAID_RECORD = {
    "separate": "demucs-prediction.json",
    "transcribe": "whisper-prediction.json",
    "describe": "describe-prediction.json",
    "depth": "depth-prediction.json",
    "masks": "sam/sam-prediction.json",
}


def _usd(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cost_rows(job: Job, state: dict[str, Any]) -> tuple[CostRow, ...]:
    """One row per paid stage that is done. Derived from state, never stored."""
    rows = []
    for stage in PAID_STAGES[state["mode"]]:
        if stage_status(state, stage) != "done":
            continue
        if stage == "depth" and state.get("depth_provider") == "huggingface":
            rows.append(CostRow(STEP_NAMES[stage], "Hugging Face · free", 0.0, None))
            continue
        usd = estimate_stages([stage], ESTIMATE_SECONDS).total
        rows.append(
            CostRow(STEP_NAMES[stage], "Replicate", usd, prediction_url(job, _PAID_RECORD[stage]))
        )
    return tuple(rows)


def completed_cost(rows: tuple[CostRow, ...]) -> float:
    return round(sum(row.usd for row in rows), 4)


def logged_cost(state: dict[str, Any]) -> float:
    entries = state.get("costs", [])
    if not isinstance(entries, list):
        return 0.0
    return round(sum(_usd(entry.get("usd")) for entry in entries if isinstance(entry, dict)), 4)


def costs_view(job: Job, state: dict[str, Any], runs_dir: Path) -> CostsView:
    rows = cost_rows(job, state)
    return CostsView(rows, completed_cost(rows), logged_cost(state), total_spend(runs_dir))


# ---- the two pages -------------------------------------------------------------
def sections_for(mode: str) -> tuple[Section, ...]:
    return tuple(section for section in SECTIONS if mode in section.modes)


def overall_status(state_progress: dict[str, Any], decision: dict[str, Any]) -> str:
    if "failed" in state_progress["stages"].values():
        return "Failed"
    if state_progress["active"]:
        return "Preparing"
    return {"approved": "Approved", "rejected": "Rejected"}.get(
        str(decision.get("status")), "Ready for review"
    )


def _section_view(
    job: Job, state: dict[str, Any], section: Section, number: int, decision: dict[str, Any]
) -> SectionView:
    if section.id == "review":
        status = _review_section_status(decision)
        texts = review_texts(job, decision)
    else:
        status = combine([stage_status(state, name) for name in section.stages])
        texts = []
        if section.id == "upload":
            texts = _upload_texts(job, state)
        elif section.id == "transcript":
            texts = _transcript_texts(state)
        elif section.id == "face-mesh":
            texts = _tracking_texts(job, state)
    return SectionView(
        id=section.id,
        number=number,
        title=section.title,
        why=section.why,
        status=status,
        media=tuple(_media(job, state, asset) for asset in section.assets),
        texts=tuple(texts),
        links=tuple(_links(job, state, section)),
        error=_error(state, section),
    )


def job_view(job: Job, runs_dir: Path) -> JobView:
    state = read_state(job)
    mode = state["mode"]
    decision = review_status(job)
    sections = tuple(
        _section_view(job, state, section, number, decision)
        for number, section in enumerate(sections_for(mode), start=1)
    )
    nav = tuple(
        NavItem(
            view.id,
            f"{view.number} {view.title}",
            view.status,
            ICONS[view.status],
            STATUS_WORDS[view.status],
        )
        for view in sections
    ) + (NavItem("costs", "Costs", "costs", "$", "summary"),)
    costs = costs_view(job, state, runs_dir)
    state_progress = progress(state)
    return JobView(
        id=job.id,
        short_id=job.id[:8],
        generator=str(state.get("generator", "?")),
        mode=mode,
        status=overall_status(state_progress, decision),
        cost_usd=costs.completed_usd,
        nav=nav,
        sections=sections,
        costs=costs,
        progress=state_progress,
    )


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")


def list_jobs(runs_dir: Path) -> list[JobSummary]:
    """Every job folder, newest first. Never hashes files (no review status here)."""
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    summaries = []
    for folder in runs_dir.iterdir():
        try:
            job = Job.load(runs_dir, folder.name)
        except JobError:
            continue  # not a job folder (spend log, stray files, half-created jobs)
        try:
            state = read_state(job)
        except ViewError as exc:
            summaries.append(
                JobSummary(job.id, job.id[:8], "?", "?", 0, 0, 0.0, _mtime_iso(folder), str(exc))
            )
            continue
        info = progress(state)
        summaries.append(
            JobSummary(
                id=job.id,
                short_id=job.id[:8],
                generator=str(state.get("generator", "?")),
                mode=state["mode"],
                done_count=info["done_count"],
                total_count=info["total_count"],
                cost_usd=completed_cost(cost_rows(job, state)),
                created_at=str(state.get("created_at") or _mtime_iso(folder)),
            )
        )
    return sorted(summaries, key=lambda summary: summary.created_at, reverse=True)
```

- [ ] **Step 6: Run** `uv run pytest tests/test_view_model.py tests/test_pipeline.py -q --no-cov` → `67 passed` and `32 passed`.

- [ ] **Step 7: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/web/__init__.py src/character_swap/web/view_model.py \
  tests/viewer_jobs.py tests/conftest.py tests/test_view_model.py tests/test_pipeline.py
git commit -m "feat: add job viewer view model (sections, statuses, Replicate links, costs)"
```

---

### Task 4: `web/app.py` + templates + static (the pages)

**Files:** Create `src/character_swap/web/app.py`, `src/character_swap/web/templates/{base,message,jobs,job}.html`, `src/character_swap/web/static/{viewer.css,viewer.js}`, `tests/test_web_app.py`

Routes (GET only): `/` · `/jobs/{id}` · `/jobs/{id}/files/{name:path}` · `/api/jobs/{id}` · `/static/...`.
- Files: only `.mp4 .wav .jpg .jpeg .png .webp` (case-insensitive); resolved with `Job.path` (rejects `../`, absolute paths and symlinks that leave the job folder); missing file → 404; served by Starlette `FileResponse`, which answers `Range` requests with `206` (video seeking) and sends `Accept-Ranges: bytes`. `Cache-Control: no-cache` so a re-run step's new file is never shown stale.
- `/api/jobs/{id}` reads `state.json` only (never hashes) → `{stages, done_count, total_count, active}`, `Cache-Control: no-store`.
- Unknown/malformed id → 404 page with a link home (JSON `{"error": …}` under `/api/`). Damaged `state.json` → the job page shows the error (status 500) instead of a traceback.
- Any non-GET method → 405 (FastAPI/Starlette default, rendered with the message page).
- Extra hardening (small, no new deps): `/docs`, `/redoc`, `/openapi.json` are disabled, and `TrustedHostMiddleware` only answers `Host: 127.0.0.1`/`localhost`, so a web page using DNS rebinding cannot read job videos.

UI (per `docs/ui-ux-best-practices.md`): every color/size/space is a CSS custom property in one `:root` block, redefined under `prefers-color-scheme: dark`; tonal layers instead of 1px borders; ambient 32px/4 % shadows; muted text ≥ 4.5:1 on every surface; visible `:focus-visible` outline; 44px nav targets; 16px body / 18px "why" text; skip link; status icons are `aria-hidden` with screen-reader words; reduced-motion disables smooth scroll and the spinner; ≤ 768px the navigator becomes a sticky horizontal strip. Navigator is `position: sticky` on the left; sections carry ids; clicking smooth-scrolls and focuses the section; `IntersectionObserver` highlights the section in view (`aria-current`); polling every 3 s reloads on any stage change and stops when nothing is active, or after 10 minutes without change (then "No progress for 10 minutes. The job may have stopped. Run `charswap resume <id>`."). Reloading restarts the timer (the page only lives until the next change).

- [ ] **Step 1: Failing tests** — `tests/test_web_app.py`:

```python
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from character_swap.config import Settings
from character_swap.costs import Estimate, Line, record_spend
from character_swap.web.app import create_app
from character_swap.web.view_model import SECTIONS, sections_for
from viewer_jobs import make_job


@pytest.fixture
def settings(tmp_path, runs_dir):
    return Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir)})


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


# ---- home ----------------------------------------------------------------------
def test_home_empty_state_explains_prepare(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No jobs yet" in response.text and "charswap prepare &lt;video&gt;" in response.text
    assert "All jobs (spend log): <strong>$0.0000</strong>" in response.text


def test_home_lists_jobs_newest_first(client, runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)
    new = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T09:30:00+00:00")
    record_spend(new, "prepare", Estimate((Line("x", 0.25),)), runs_dir)
    text = client.get("/").text
    assert text.index(new.id[:8]) < text.index(old.id[:8])
    assert "4/9 steps" in text and "1/1 steps" in text
    assert "2026-11-01 09:30" in text and "$0.2500" in text
    assert f'href="/jobs/{old.id}"' in text


def test_home_marks_unreadable_jobs(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{broken")
    response = client.get("/")
    assert response.status_code == 200
    assert "Unreadable: state.json could not be read" in response.text


# ---- job page ------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_job_page_has_every_section_and_anchor(client, runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 200
    for section in [*sections_for(mode), None]:
        section_id = section.id if section else "costs"
        assert f'<section id="{section_id}"' in response.text
        assert f'href="#{section_id}"' in response.text
    for section in sections_for(mode):
        assert section.why in response.text.replace("&#39;", "'")
    assert 'data-active="false"' in response.text
    assert "Ready for review" in response.text


def test_job_page_links_media_and_replicate(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    text = client.get(f"/jobs/{job.id}").text
    assert (
        f'<video controls preload="metadata" playsinline src="/jobs/{job.id}/files/depth.mp4">'
        in text
    )
    assert f'<audio controls preload="metadata" src="/jobs/{job.id}/files/vocals.wav">' in text
    assert (
        'href="https://replicate.com/p/pred-depth" target="_blank" rel="noopener noreferrer"'
        in text
    )
    assert (
        "This job, steps completed so far" in text
        and "Exact charges: replicate.com billing" in text
    )
    assert f"charswap review {job.id} --approve" in text


def test_live_job_page_polls_and_shows_running_step(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, running=True)  # depth running
    text = client.get(f"/jobs/{job.id}").text
    assert 'data-active="true"' in text
    stages = json.loads(text.split('data-stages="')[1].split('"')[0].replace("&#34;", '"'))
    assert stages["depth"] == "running" and stages["normalize"] == "done"
    assert "Waiting for this step…" in text and "⟳" in text


def test_failed_step_shows_error_and_resume_hint(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="Transcribing speech failed: <boom>")
    text = client.get(f"/jobs/{job.id}").text
    assert "Transcribing speech failed: &lt;boom&gt;" in text  # escaped, never raw HTML
    assert f"Fix the issue, then run <code>charswap resume {job.id}</code>" in text
    assert 'data-active="false"' in text


def test_untrusted_text_is_escaped(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    job.update(transcript="<script>alert(1)</script>")
    text = client.get(f"/jobs/{job.id}").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_job_page_with_damaged_state_shows_the_error(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("[]")
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 500
    assert "state.json is not a JSON object." in response.text
    assert 'href="/"' in response.text


@pytest.mark.parametrize("job_id", ["0" * 32, "not-a-job", "A" * 32])
def test_unknown_job_is_a_404_page(client, job_id):
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 404
    assert "There is no job with that id." in response.text and 'href="/"' in response.text


# ---- files ---------------------------------------------------------------------
def test_file_route_serves_video_with_ranges(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    size = job.path("prepared.mp4").stat().st_size
    full = client.get(f"/jobs/{job.id}/files/prepared.mp4")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "video/mp4" and len(full.content) == size
    part = client.get(f"/jobs/{job.id}/files/prepared.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"] == f"bytes 0-99/{size}"


def test_file_route_serves_nested_audio_and_images(client, runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    assert client.get(f"/jobs/{job.id}/files/character-primary.png").status_code == 200
    job.path("sam").mkdir()
    job.path("sam/frame.JPG").write_bytes(b"jpg")
    assert client.get(f"/jobs/{job.id}/files/sam/frame.JPG").status_code == 200


@pytest.mark.parametrize(
    "name",
    [
        "state.json",
        ".env",
        "demucs-prediction.json",
        "notes.txt",
        "missing.mp4",
        "sam",
        "%2E%2E/%2E%2E/outside.mp4",
        "..%2F..%2Foutside.mp4",
    ],
)
def test_file_route_refuses_everything_else(client, runs_dir, media, name):
    job = make_job(runs_dir, media, "depth", done=0)
    (runs_dir.parent / "outside.mp4").write_bytes(b"secret")
    job.path(".env").write_text("REPLICATE_API_TOKEN=secret")
    response = client.get(f"/jobs/{job.id}/files/{name}")
    assert response.status_code == 404
    assert "secret" not in response.text


def test_file_route_refuses_absolute_paths_and_symlinks(client, runs_dir, media, tmp_path):
    job = make_job(runs_dir, media, "depth", done=0)
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    absolute = quote(str(outside), safe="")
    assert client.get(f"/jobs/{job.id}/files/{absolute}").status_code == 404
    job.path("link.mp4").symlink_to(outside)
    assert client.get(f"/jobs/{job.id}/files/link.mp4").status_code == 404


def test_file_route_for_unknown_job_is_404(client):
    assert client.get(f"/jobs/{'0' * 32}/files/prepared.mp4").status_code == 404


# ---- polling API -----------------------------------------------------------------
def test_api_progress_shape(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh", done=3, running=True)
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == {"stages", "done_count", "total_count", "active"}
    assert list(data["stages"]) == [
        "normalize",
        "separate",
        "pitch",
        "transcribe",
        "describe",
        "face_mesh",
        "embed",
    ]
    assert data["stages"]["transcribe"] == "running"
    assert (data["done_count"], data["total_count"], data["active"]) == (3, 7, True)


def test_api_never_hashes_files(client, runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "depth")
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    assert client.get(f"/api/jobs/{job.id}").json()["active"] is False


def test_api_errors_are_json(client, runs_dir, media):
    assert client.get(f"/api/jobs/{'0' * 32}").json() == {"error": "There is no job with that id."}
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{")
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 500 and "could not be read" in response.json()["error"]


# ---- read-only, local-only -------------------------------------------------------
@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_no_route_changes_anything(client, runs_dir, media, method):
    job = make_job(runs_dir, media, "depth")
    for path in [
        "/",
        f"/jobs/{job.id}",
        f"/jobs/{job.id}/files/prepared.mp4",
        f"/api/jobs/{job.id}",
        "/static/viewer.css",
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 405, (method, path)


def test_static_assets_are_served(client):
    css = client.get("/static/viewer.css")
    assert css.status_code == 200 and "prefers-color-scheme: dark" in css.text
    js = client.get("/static/viewer.js")
    assert js.status_code == 200 and "IntersectionObserver" in js.text
    assert client.get("/static/nope.css").status_code == 404


def test_api_docs_are_disabled(client):
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_requests_for_other_hosts_are_refused(settings):
    remote = TestClient(create_app(settings), base_url="http://evil.example")
    assert remote.get("/").status_code == 400
    local = TestClient(create_app(settings), base_url="http://localhost:8770")
    assert local.get("/").status_code == 200


def test_every_section_title_is_unique():
    assert len({section.id for section in SECTIONS}) == len(SECTIONS)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_web_app.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'character_swap.web.app'`.

- [ ] **Step 3: App factory** — `src/character_swap/web/app.py`:

```python
"""Read-only local job viewer. GET routes only; `charswap view` binds it to 127.0.0.1."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from character_swap.config import Settings
from character_swap.costs import total_spend
from character_swap.jobs import Job, JobError
from character_swap.web.view_model import (
    ICONS,
    STATUS_WORDS,
    ViewError,
    job_view,
    list_jobs,
    progress,
    read_state,
)

HERE = Path(__file__).parent
# JSON (state, prediction records, landmarks) is read server-side and never served.
ALLOWED_SUFFIXES = {".mp4", ".wav", ".jpg", ".jpeg", ".png", ".webp"}
# Only answer requests addressed to this machine (blocks DNS-rebinding pages from reading jobs).
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
NOT_FOUND = "Not found."


def create_app(settings: Settings) -> FastAPI:
    runs_dir = settings.runs_dir
    app = FastAPI(title="Character Swap viewer", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(icons=ICONS, status_words=STATUS_WORDS)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
        context = {"title": title, "message": text}
        return templates.TemplateResponse(request, "message.html", context, status_code=status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
        title = "Not found" if exc.status_code == 404 else "Not allowed"
        response = message(request, title, str(exc.detail), exc.status_code)
        response.headers.update(exc.headers or {})
        return response

    def load(job_id: str) -> Job:
        try:
            return Job.load(runs_dir, job_id)
        except JobError:
            raise HTTPException(404, "There is no job with that id.") from None

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        context = {"jobs": list_jobs(runs_dir), "all_jobs_usd": total_spend(runs_dir)}
        return templates.TemplateResponse(request, "jobs.html", context)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> Response:
        job = load(job_id)
        try:
            view = job_view(job, runs_dir)
        except ViewError as exc:
            return message(request, f"Job {job_id[:8]} cannot be shown", str(exc), 500)
        context = {"job": view, "stages_json": json.dumps(view.progress["stages"])}
        return templates.TemplateResponse(request, "job.html", context)

    @app.get("/jobs/{job_id}/files/{name:path}")
    def job_file(job_id: str, name: str) -> FileResponse:
        job = load(job_id)
        if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise HTTPException(404, NOT_FOUND)
        try:
            path = job.path(name)  # containment: nothing outside the job folder
        except JobError:
            raise HTTPException(404, NOT_FOUND) from None
        if not path.is_file():
            raise HTTPException(404, NOT_FOUND)
        # Range requests (video seeking) are handled by FileResponse.
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.get("/api/jobs/{job_id}")
    def job_progress(job_id: str) -> JSONResponse:
        job = load(job_id)
        try:
            state = read_state(job)
        except ViewError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(progress(state), headers={"Cache-Control": "no-store"})

    return app
```

- [ ] **Step 4: Templates** — `src/character_swap/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{% block title %}Jobs{% endblock %} · Character Swap viewer</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/static/viewer.css">
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="topbar">
    <a class="brand" href="/">Character Swap</a>
    <span class="topbar-note">Read-only viewer · actions stay in the terminal</span>
  </header>
  {% block body %}{% endblock %}
  <script src="/static/viewer.js" defer></script>
</body>
</html>
```

`src/character_swap/web/templates/message.html`:

```html
{% extends "base.html" %}
{% block title %}{{ title }}{% endblock %}
{% block body %}
<main id="main" class="page">
  <div class="card message">
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    <p><a href="/">Back to all jobs</a></p>
  </div>
</main>
{% endblock %}
```

`src/character_swap/web/templates/jobs.html`:

```html
{% extends "base.html" %}
{% block title %}Jobs{% endblock %}
{% block body %}
<main id="main" class="page">
  <div class="page-head">
    <h1>Jobs</h1>
    <p class="meta">All jobs (spend log): <strong>${{ "%.4f"|format(all_jobs_usd) }}</strong> · estimates</p>
  </div>
  {% if jobs %}
  <div class="card table-wrap">
    <table class="jobs">
      <caption class="sr-only">Jobs, newest first</caption>
      <thead>
        <tr>
          <th scope="col">Job</th>
          <th scope="col">Generator</th>
          <th scope="col">Mode</th>
          <th scope="col">Progress</th>
          <th scope="col">Cost so far</th>
          <th scope="col">Created (UTC)</th>
        </tr>
      </thead>
      <tbody>
        {% for job in jobs %}
        <tr>
          <td><a href="/jobs/{{ job.id }}"><code>{{ job.short_id }}</code></a></td>
          {% if job.error %}
          <td colspan="4" class="bad">Unreadable: {{ job.error }}</td>
          {% else %}
          <td>{{ job.generator }}</td>
          <td>{{ job.mode }}</td>
          <td>{{ job.done_count }}/{{ job.total_count }} steps</td>
          <td>${{ "%.4f"|format(job.cost_usd) }}</td>
          {% endif %}
          <td><time datetime="{{ job.created_at }}">{{ job.created_at[:16]|replace("T", " ") }}</time></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="card empty">
    <h2>No jobs yet</h2>
    <p>Prepare a clip in the terminal, then refresh this page:</p>
    <pre><code>uv run charswap prepare &lt;video&gt;</code></pre>
  </div>
  {% endif %}
</main>
{% endblock %}
```

`src/character_swap/web/templates/job.html`:

```html
{% extends "base.html" %}
{% block title %}Job {{ job.short_id }}{% endblock %}
{% block body %}
<div class="job-head">
  <h1>Job <code>{{ job.short_id }}</code></h1>
  <p class="meta">
    {{ job.generator }} · {{ job.mode }} mode ·
    <span class="badge" id="job-status">{{ job.status }}</span> ·
    estimated cost so far ≈ <strong>${{ "%.4f"|format(job.cost_usd) }}</strong>
  </p>
</div>
<div class="layout" data-job-id="{{ job.id }}"
     data-active="{{ 'true' if job.progress.active else 'false' }}"
     data-stages="{{ stages_json }}">
  <nav class="steps" aria-label="Steps">
    <ol>
      {% for item in job.nav %}
      <li>
        <a class="nav-link status-{{ item.status }}" href="#{{ item.id }}">
          <span class="icon" aria-hidden="true">{{ item.icon }}</span>
          <span class="label">{{ item.label }}</span>
          <span class="sr-only">({{ item.status_text }})</span>
        </a>
      </li>
      {% endfor %}
    </ol>
  </nav>

  <main id="main" class="content">
    <p id="stalled" class="notice" role="status" hidden>
      No progress for 10 minutes. The job may have stopped. Run
      <code>charswap resume {{ job.id }}</code>.
    </p>

    {% for section in job.sections %}
    <section id="{{ section.id }}" class="step" tabindex="-1" aria-labelledby="{{ section.id }}-title">
      <header class="step-head">
        <h2 id="{{ section.id }}-title"><span class="num">{{ section.number }}</span> {{ section.title }}</h2>
        <span class="badge status-{{ section.status }}">
          <span aria-hidden="true">{{ icons[section.status] }}</span> {{ status_words[section.status] }}
        </span>
        {% for link in section.links %}
        <a class="ext" href="{{ link.url }}" target="_blank" rel="noopener noreferrer">{{ link.label }}</a>
        {% endfor %}
      </header>
      <p class="why"><strong>Why this step?</strong> {{ section.why }}</p>

      {% if section.error %}
      <div class="error" role="alert">
        <p>{{ section.error }}</p>
        <p>Fix the issue, then run <code>charswap resume {{ job.id }}</code>.</p>
      </div>
      {% endif %}

      {% if section.media %}
      <div class="media-grid">
        {% for item in section.media %}
        <figure class="media media-{{ item.kind }}">
          {% if item.state == "ready" %}
            {% if item.kind == "video" %}
            <video controls preload="metadata" playsinline src="{{ item.url }}"></video>
            {% elif item.kind == "audio" %}
            <audio controls preload="metadata" src="{{ item.url }}"></audio>
            {% else %}
            <img src="{{ item.url }}" alt="{{ item.label }}" loading="lazy">
            {% endif %}
          {% elif item.state == "missing" %}
          <div class="placeholder missing">File missing</div>
          {% else %}
          <div class="placeholder">Waiting for this step…</div>
          {% endif %}
          <figcaption>{{ item.label }}</figcaption>
        </figure>
        {% endfor %}
      </div>
      {% elif section.status != "done" and section.id != "review" %}
      <div class="placeholder">Waiting for this step…</div>
      {% endif %}

      {% if section.texts %}
      <dl class="facts">
        {% for text in section.texts %}
        <dt>{{ text.label }}</dt>
        <dd>{% if text.code %}<code class="command">{{ text.value }}</code>{% else %}{{ text.value }}{% endif %}</dd>
        {% endfor %}
      </dl>
      {% endif %}
    </section>
    {% endfor %}

    <section id="costs" class="step" tabindex="-1" aria-labelledby="costs-title">
      <header class="step-head">
        <h2 id="costs-title"><span class="num" aria-hidden="true">$</span> Costs</h2>
      </header>
      {% if job.costs.rows %}
      <div class="table-wrap">
        <table class="costs">
          <caption class="sr-only">Paid steps finished so far</caption>
          <thead>
            <tr><th scope="col">Step</th><th scope="col">Provider</th><th scope="col">Estimate</th><th scope="col">Run</th></tr>
          </thead>
          <tbody>
            {% for row in job.costs.rows %}
            <tr>
              <td>{{ row.step }}</td>
              <td>{{ row.provider }}</td>
              <td>${{ "%.4f"|format(row.usd) }}</td>
              <td>{% if row.link %}<a href="{{ row.link }}" target="_blank" rel="noopener noreferrer">run ↗</a>{% endif %}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% else %}
      <p class="meta">No paid steps finished yet.</p>
      {% endif %}
      <dl class="facts totals">
        <dt>This job, steps completed so far</dt><dd>≈ ${{ "%.4f"|format(job.costs.completed_usd) }}</dd>
        <dt>Logged for this job</dt><dd>${{ "%.4f"|format(job.costs.logged_usd) }}</dd>
        <dt>All jobs (spend log)</dt><dd>${{ "%.4f"|format(job.costs.all_jobs_usd) }}</dd>
      </dl>
      <p class="meta">Estimates. Exact charges: replicate.com billing.</p>
    </section>
  </main>
</div>
{% endblock %}
```

- [ ] **Step 5: Static** — `src/character_swap/web/static/viewer.css`:

```css
/* Character Swap viewer. All colors, sizes and spacing come from the tokens below. */
:root {
  color-scheme: light dark;
  /* surfaces: tonal layers instead of border lines */
  --bg: #f6f5f2;
  --surface: #ffffff;
  --surface-2: #eceae4;
  /* text (muted is >= 4.5:1 on every surface) */
  --text: #1d1c1a;
  --text-muted: #57554f;
  /* meaning */
  --accent: #2456c7;
  --ok: #1d7a3e;
  --running: #8f5400;
  --bad: #b3261e;
  --bad-bg: #fbe9e7;
  --focus: #2456c7;
  --shadow: 0 8px 32px rgb(29 28 26 / 4%);
  --video-bg: #000000;
  /* type */
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-lg: 1.125rem;
  --text-xl: 1.5rem;
  --text-2xl: 2rem;
  /* space and shape */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-12: 3rem;
  --radius: 12px;
  --radius-sm: 8px;
  --nav-width: 17rem;
  --topbar-height: 3.5rem;
  --target: 44px;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #151514;
    --surface: #1f1f1d;
    --surface-2: #2a2a27;
    --text: #ecebe7;
    --text-muted: #b3b1aa;
    --accent: #8fb0ff;
    --ok: #6fd08f;
    --running: #f0b35a;
    --bad: #ff8a80;
    --bad-bg: #3a1f1c;
    --focus: #8fb0ff;
    --shadow: 0 8px 32px rgb(0 0 0 / 30%);
  }
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: var(--text-base) / 1.55 var(--font);
}
a { color: var(--accent); }
code, pre { font-family: var(--mono); font-size: var(--text-sm); }
h1 { font-size: var(--text-2xl); line-height: 1.2; margin: 0 0 var(--space-2); }
h2 { font-size: var(--text-xl); line-height: 1.25; margin: 0; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 4px; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
.skip-link { position: absolute; left: var(--space-4); top: -100px; z-index: 20; }
.skip-link:focus { top: var(--space-2); background: var(--surface); padding: var(--space-2) var(--space-4); }

/* top bar */
.topbar {
  position: sticky; top: 0; z-index: 10;
  height: var(--topbar-height);
  display: flex; align-items: center; gap: var(--space-4);
  padding: 0 var(--space-6);
  background: var(--surface-2);
}
.brand { color: var(--text); font-weight: 700; text-decoration: none; }
.topbar-note { color: var(--text-muted); font-size: var(--text-sm); }

/* shared blocks */
.page { max-width: 72rem; margin: 0 auto; padding: var(--space-8) var(--space-6) var(--space-12); }
.page-head, .job-head { margin-bottom: var(--space-6); }
.job-head { padding: var(--space-8) var(--space-6) 0; }
.meta { color: var(--text-muted); margin: 0; }
.card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: var(--space-8);
}
.badge {
  display: inline-block; padding: 0 var(--space-2);
  border-radius: var(--radius-sm); background: var(--surface-2);
  font-size: var(--text-sm); white-space: nowrap;
}
.bad, .status-failed { color: var(--bad); }
.status-done { color: var(--ok); }
.status-running { color: var(--running); }
.status-pending, .status-costs { color: var(--text-muted); }

/* tables: whitespace instead of divider lines */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: var(--text-sm); }
th, td { padding: var(--space-3) var(--space-4) var(--space-3) 0; vertical-align: top; }
.jobs td { white-space: nowrap; }
.jobs td.bad { white-space: normal; }
tbody tr:nth-child(even) { background: color-mix(in srgb, var(--surface-2) 45%, transparent); }

/* job page: fixed navigator + one tall scrolling column */
.layout {
  display: grid;
  grid-template-columns: var(--nav-width) minmax(0, 1fr);
  gap: var(--space-8);
  padding: 0 var(--space-6) var(--space-12);
  align-items: start;
}
.steps {
  position: sticky;
  top: calc(var(--topbar-height) + var(--space-4));
  max-height: calc(100vh - var(--topbar-height) - var(--space-8));
  overflow-y: auto;
  background: var(--surface-2);
  border-radius: var(--radius);
  padding: var(--space-3);
}
.steps ol { list-style: none; margin: 0; padding: 0; }
.nav-link {
  display: flex; align-items: center; gap: var(--space-3);
  min-height: var(--target);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  text-decoration: none;
}
.nav-link .label { color: var(--text); }
.nav-link:hover { background: var(--surface); }
.nav-link[aria-current="true"] {
  background: var(--surface);
  box-shadow: inset 3px 0 0 var(--accent);
}
.nav-link[aria-current="true"] .label { font-weight: 600; }
.steps li:last-child { margin-top: var(--space-2); padding-top: var(--space-2); }
.icon { display: inline-block; width: 1.25em; text-align: center; font-weight: 700; }
.status-running .icon { animation: spin 1.6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.content { min-width: 0; }
.step {
  scroll-margin-top: calc(var(--topbar-height) + var(--space-4));
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: var(--space-8);
  margin-bottom: var(--space-8);
}
.step:focus { outline: none; }
.step:focus-visible { outline: 3px solid var(--focus); }
.step-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); }
.num { color: var(--text-muted); margin-right: var(--space-1); }
.ext { margin-left: auto; font-size: var(--text-sm); white-space: nowrap; }
.why { color: var(--text-muted); font-size: var(--text-lg); max-width: 60ch; margin: 0 0 var(--space-6); }
.why strong { color: var(--text); }

.media-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: var(--space-6);
}
.media { margin: 0; }
.media figcaption { color: var(--text-muted); font-size: var(--text-sm); margin-top: var(--space-2); }
video, img {
  display: block; width: 100%; max-height: 70vh;
  object-fit: contain; background: var(--video-bg); border-radius: var(--radius-sm);
}
.media-audio { grid-column: 1 / -1; }
audio { width: 100%; }
.placeholder {
  display: grid; place-items: center; min-height: 10rem;
  background: var(--surface-2); color: var(--text-muted);
  border-radius: var(--radius-sm); padding: var(--space-4); text-align: center;
}
.media-audio .placeholder { min-height: 3rem; }
.placeholder.missing { color: var(--bad); }

.facts { display: grid; grid-template-columns: max-content 1fr; gap: var(--space-2) var(--space-6); margin: var(--space-6) 0 0; }
.facts dt { color: var(--text-muted); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.command { display: block; padding: var(--space-3); background: var(--surface-2); border-radius: var(--radius-sm); user-select: all; }
.totals dd { font-weight: 600; }
.totals + .meta { margin-top: var(--space-4); }

.error, .notice {
  background: var(--bad-bg); color: var(--text);
  border-radius: var(--radius-sm); padding: var(--space-3) var(--space-4);
  margin: 0 0 var(--space-6);
}
.error p { margin: 0 0 var(--space-2); }
.error p:last-child { margin: 0; }
.notice[hidden] { display: none; }

@media (max-width: 768px) {
  .layout { grid-template-columns: minmax(0, 1fr); gap: var(--space-4); padding: 0 var(--space-4) var(--space-8); }
  .job-head, .page { padding-left: var(--space-4); padding-right: var(--space-4); }
  .steps { top: var(--topbar-height); z-index: 5; max-height: none; overflow: hidden; padding: var(--space-2); border-radius: 0 0 var(--radius) var(--radius); }
  .steps ol { display: flex; gap: var(--space-1); overflow-x: auto; scrollbar-width: thin; }
  .steps li:last-child { margin-top: 0; padding-top: 0; }
  .nav-link { white-space: nowrap; }
  .card { padding: var(--space-4); }
  .step { padding: var(--space-4); scroll-margin-top: calc(var(--topbar-height) + 4.5rem); }
  .topbar-note { display: none; }
  .facts { grid-template-columns: minmax(0, 1fr); }
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  .status-running .icon { animation: none; }
}
```

`src/character_swap/web/static/viewer.js`:

```javascript
// Job page behaviour: click-to-scroll navigator, scroll-spy, live progress polling.
(() => {
  "use strict";
  const layout = document.querySelector(".layout[data-job-id]");
  if (!layout) return; // the jobs list needs no script

  const POLL_MS = 3000;
  const STALL_MS = 10 * 60 * 1000; // no change for 10 minutes: stop and say so
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const links = new Map(
    [...layout.querySelectorAll(".steps a[href^='#']")].map((a) => [a.hash.slice(1), a])
  );

  function setCurrent(id) {
    links.forEach((link, key) => {
      if (key === id) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  }

  // 1. Click a step: smooth-scroll to its section and move keyboard focus there.
  links.forEach((link, id) => {
    link.addEventListener("click", (event) => {
      const target = document.getElementById(id);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      target.focus({ preventScroll: true });
      history.replaceState(null, "", `#${id}`);
      setCurrent(id);
    });
  });

  // 2. Scroll-spy: highlight the first section crossing the top part of the screen.
  const visible = new Set();
  const spy = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      });
      const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 2;
      const ids = [...links.keys()];
      const current = atBottom ? ids[ids.length - 1] : ids.find((id) => visible.has(id));
      if (current) setCurrent(current);
    },
    { rootMargin: "-80px 0px -60% 0px" }
  );
  links.forEach((_, id) => {
    const section = document.getElementById(id);
    if (section) spy.observe(section);
  });
  window.addEventListener(
    "scroll",
    () => {
      if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 2) {
        setCurrent([...links.keys()].pop());
      }
    },
    { passive: true }
  );
  const linked = location.hash ? document.getElementById(location.hash.slice(1)) : null;
  if (linked && links.has(linked.id)) {
    linked.scrollIntoView({ block: "start" }); // a shared #section link opens on that step
    setCurrent(linked.id);
  } else {
    setCurrent(links.keys().next().value);
  }

  // 3. Live progress: poll while the job is active; reload when any stage changes.
  if (layout.dataset.active !== "true") return;
  const url = `/api/jobs/${layout.dataset.jobId}`;
  const shown = JSON.stringify(JSON.parse(layout.dataset.stages));
  const startedWaiting = Date.now();

  function stalled() {
    document.getElementById("stalled").hidden = false;
  }

  async function tick() {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        if (JSON.stringify(data.stages) !== shown) {
          location.reload();
          return;
        }
        if (!data.active) return; // finished or failed: nothing more to watch
      }
    } catch (error) {
      // the viewer may be restarting; keep trying until the stall timer runs out
    }
    if (Date.now() - startedWaiting > STALL_MS) {
      stalled();
      return;
    }
    window.setTimeout(tick, POLL_MS);
  }
  window.setTimeout(tick, POLL_MS);
})();
```

- [ ] **Step 6: Run** `uv run pytest tests/test_web_app.py -q --no-cov` → `38 passed`.

- [ ] **Step 7: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/web/app.py src/character_swap/web/templates/base.html \
  src/character_swap/web/templates/message.html src/character_swap/web/templates/jobs.html \
  src/character_swap/web/templates/job.html src/character_swap/web/static/viewer.css \
  src/character_swap/web/static/viewer.js tests/test_web_app.py
git commit -m "feat: add read-only job viewer pages, file serving and progress API"
```

---

### Task 5: `charswap view`

**Files:** Modify `src/character_swap/cli.py`, `tests/test_cli.py`

Behaviour: checks the port with the same bind uvicorn will do (`SO_REUSEADDR` on 127.0.0.1, so a just-stopped viewer is not reported busy); busy → stderr `Port 8770 is busy; try \`charswap view --port 8771\`` and exit 2 (returned directly, not raised). Prints the URL, opens the browser after `BROWSER_DELAY` (1 s, so the server is listening first) unless `--no-browser`, then runs `env.serve(app, host="127.0.0.1", port=port)`. `_view` catches `KeyboardInterrupt` itself → "Viewer stopped.", exit 0 (it must not reach `main`'s resume-hint handler). If the server fails to start, the pending browser open is cancelled.

**Interpretation:** the spec's `Env.serve` default is `uvicorn.run`; here it is a thin `run_server` wrapper that imports uvicorn lazily and quiets its access log (`log_level="warning"`), so `prepare`/`status` do not pay the FastAPI/uvicorn import cost. The browser opener is injected the same way (`Env.open_browser`, `main(open_browser=…)`) so tests never open a browser.

- [ ] **Step 1: Failing tests.** In `tests/test_cli.py` replace the import header with:

```python
import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from character_swap import cli
from character_swap.config import Settings
from character_swap.jobs import Job
from character_swap.stages import StageError
from helpers import solid, write_video
```

and append:

```python
# ---- view ----------------------------------------------------------------------
def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeServer:
    def __init__(self, raise_on_start=None, wait_for=None):
        self.calls = []
        self.raise_on_start = raise_on_start
        self.wait_for = wait_for

    def __call__(self, app, *, host, port):
        self.calls.append({"app": app, "host": host, "port": port})
        if self.wait_for is not None:
            self.wait_for.wait(timeout=5)  # "running" until the browser was opened
        if self.raise_on_start:
            raise self.raise_on_start


def test_view_serves_the_app_on_localhost_only(settings):
    port = free_port()
    server = FakeServer()
    opened = []
    code, out = run(
        ["view", "--port", str(port), "--no-browser"],
        settings,
        serve=server,
        open_browser=opened.append,
    )
    assert code == 0 and opened == []
    [call] = server.calls
    assert call["host"] == "127.0.0.1" and call["port"] == port
    assert f"Job viewer: http://127.0.0.1:{port}" in out.text and "Viewer stopped." in out.text
    page = TestClient(call["app"], base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and "No jobs yet" in page.text


def test_view_default_port(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    server = FakeServer()
    code, _ = run(["view", "--no-browser"], settings, serve=server)
    assert code == 0 and server.calls[0]["port"] == 8770


def test_view_opens_the_browser_once_the_server_runs(settings, monkeypatch):
    monkeypatch.setattr(cli, "BROWSER_DELAY", 0)
    port = free_port()
    opened = threading.Event()
    urls = []

    def open_browser(url):
        urls.append(url)
        opened.set()

    code, _ = run(
        ["view", "--port", str(port)],
        settings,
        serve=FakeServer(wait_for=opened),
        open_browser=open_browser,
    )
    assert code == 0 and urls == [f"http://127.0.0.1:{port}"]


def test_view_does_not_open_the_browser_if_the_server_fails(settings, monkeypatch):
    monkeypatch.setattr(cli, "BROWSER_DELAY", 0.2)
    urls = []
    with pytest.raises(OSError):
        run(
            ["--debug", "view", "--port", str(free_port())],
            settings,
            serve=FakeServer(raise_on_start=OSError("bind failed")),
            open_browser=urls.append,
        )
    time.sleep(0.4)
    assert urls == []


def test_view_ctrl_c_stops_cleanly(settings):
    server = FakeServer(raise_on_start=KeyboardInterrupt())
    code, out = run(["view", "--port", str(free_port()), "--no-browser"], settings, serve=server)
    assert code == 0 and "Viewer stopped." in out.text
    assert "Interrupted" not in out.err.text and "resume" not in out.err.text


def test_view_busy_port_is_explained(settings):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        server = FakeServer()
        code, out = run(["view", "--port", str(port), "--no-browser"], settings, serve=server)
    assert code == 2 and server.calls == []
    assert f"Port {port} is busy; try `charswap view --port {port + 1}`" in out.err.text


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_view_rejects_bad_ports(settings, port):
    with pytest.raises(SystemExit):
        run(["view", "--port", port], settings, serve=FakeServer())


def test_help_lists_view(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "open the read-only job viewer" in capsys.readouterr().out
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -q --no-cov`
Expected: FAIL — `10 failed, 36 passed` (`main() got an unexpected keyword argument 'serve'`, `invalid choice: 'view'`).

- [ ] **Step 3: Implement** in `src/character_swap/cli.py`:

(a) imports — the stdlib block becomes:

```python
import argparse
import json
import shutil
import socket
import sys
import threading
import webbrowser
```

(b) below `Out = Callable[[str], None]`:

```python
VIEW_HOST = "127.0.0.1"  # the viewer is never reachable from the network
BROWSER_DELAY = 1.0  # seconds; lets the server start listening before the browser asks
```

(c) after `build_services`:

```python
def run_server(app, *, host: str, port: int) -> None:  # pragma: no cover - real server
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
```

(d) two new `Env` fields after `extra`:

```python
    serve: Callable[..., object] = run_server
    open_browser: Callable[[str], object] = webbrowser.open
```

(e) add `  charswap view` as the last line of `EPILOG`, and in `_parser()` replace `sub.add_parser("doctor", help="check this machine's setup")\n    return parser` plus add `_port` after it:

```python
    sub.add_parser("doctor", help="check this machine's setup")
    view = sub.add_parser("view", help="open the read-only job viewer in your browser")
    view.add_argument("--port", type=_port, default=8770)
    view.add_argument("--no-browser", action="store_true", help="only print the URL")
    return parser


def _port(text: str) -> int:
    port = int(text)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port
```

(f) `main()` gets two keyword arguments after `services_factory` and builds `Env` with them:

```python
    serve: Callable[..., object] | None = None,
    open_browser: Callable[[str], object] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    settings = settings or Settings.load()
    env = Env(
        out,
        err,
        confirm or (lambda q: _ask(q, err)),
        services_factory,
        serve=serve or run_server,
        open_browser=open_browser or webbrowser.open,
    )
```

(g) before `COMMANDS`:

```python
def _port_free(port: int) -> bool:
    """Same bind uvicorn will do (SO_REUSEADDR), so a just-stopped viewer is not 'busy'."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((VIEW_HOST, port))
        except OSError:
            return False
    return True


def _view(args, settings, env) -> int:
    from character_swap.web.app import create_app  # keeps other commands fast to start

    port = args.port
    if not _port_free(port):
        env.err(f"Port {port} is busy; try `charswap view --port {port + 1}`")
        return 2
    url = f"http://{VIEW_HOST}:{port}"
    app = create_app(settings)
    env.out(f"Job viewer: {url}  (Ctrl-C to stop)")
    opener = None
    if not args.no_browser:
        opener = threading.Timer(BROWSER_DELAY, env.open_browser, args=[url])
        opener.daemon = True
        opener.start()
    try:
        env.serve(app, host=VIEW_HOST, port=port)
    except KeyboardInterrupt:
        pass  # Ctrl-C is how the viewer is meant to stop; no resume hint here
    finally:
        if opener is not None:
            opener.cancel()
    env.out("Viewer stopped.")
    return 0
```

(h) register it: add `"view": _view,` as the last entry of `COMMANDS`.

- [ ] **Step 4: Run** `uv run pytest tests/test_cli.py -q --no-cov` → `46 passed`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/cli.py tests/test_cli.py
git commit -m "feat: add charswap view (localhost-only job viewer)"
```

---

### Task 6: Full verification, smoke start, manual checklist, journal

- [ ] **Step 1: Full suite with coverage**

Run: `uv run pytest`
Expected: all pass (≈ 367 tests at plan time; was 239), `Required test coverage of 85% reached` (≈ 97 % at plan time; `web/app.py` 100 %, `web/view_model.py` ≈ 99 %). If below 85 %, add tests for the lines in `term-missing`; do not add `pragma: no cover` (only `run_server`, which starts a real server, has one).

- [ ] **Step 2: Lint/format check**

Run: `uv run ruff format --check src tests && uv run ruff check src tests` → no findings.

- [ ] **Step 3: Smoke start (free, no browser)**

```bash
.venv/bin/charswap view --no-browser --port 8770 > runs/charswap-view.log 2>&1 & VIEW_PID=$!
for i in $(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:8770/ && break; sleep 0.5; done
curl -s -o /dev/null -w "home %{http_code}\n" http://127.0.0.1:8770/
JOB=$(ls runs 2>/dev/null | grep -E '^[0-9a-f]{32}$' | head -1)
[ -n "$JOB" ] && curl -s -o /dev/null -w "job %{http_code}\n" "http://127.0.0.1:8770/jobs/$JOB"
[ -n "$JOB" ] && curl -s "http://127.0.0.1:8770/api/jobs/$JOB"; echo
curl -s -o /dev/null -w "other host %{http_code}\n" -H "Host: evil.example" http://127.0.0.1:8770/
kill -INT $VIEW_PID; wait $VIEW_PID; echo "exit $?"; cat runs/charswap-view.log
```

Expected: `home 200`, `job 200` and a JSON progress line (if a job exists), `other host 400`, `exit 0`, and the log shows `Job viewer: http://127.0.0.1:8770  (Ctrl-C to stop)` then `Viewer stopped.`. (`.venv/bin/charswap` is used instead of `uv run` so the signal reaches the viewer directly.) If 8770 is busy the command prints the busy-port message and exits 2; use another port.

- [ ] **Step 4: Manual browser checklist (Frank)** — `uv run charswap view`:
  1. The browser opens on the jobs list; newest job first; "N/M steps" and cost look right; with no jobs the page explains `charswap prepare <video>`.
  2. Open a finished job. Click every navigator entry, top to bottom and back: the page smooth-scrolls to that section and the entry highlights. Scroll by hand: the highlight follows the section in view; at the bottom "Costs" highlights.
  3. Press Tab from the top: "Skip to content" appears first; every nav entry shows a clear focus ring.
  4. Play and scrub `prepared.mp4` and the depth video (seeking must jump instantly: Range requests). Play the three audio players.
  5. Every "View on Replicate ↗" opens the matching prediction on replicate.com in a new tab.
  6. Switch macOS to Dark mode (System Settings → Appearance): the page follows, text stays readable.
  7. Narrow the window to phone width: the navigator becomes a sticky strip at the top; nothing overflows sideways.
  8. Live: in a terminal run `uv run charswap prepare <short clip> --mode face_mesh` (costs ≈ $0.02, confirm first), open the new job in the viewer and watch steps tick ◻ → ⟳ → ✓ without touching the page; polling stops once it is "Ready for review".
  9. Press Ctrl-C in the viewer terminal: it prints "Viewer stopped." (no resume hint).

- [ ] **Step 5: Update docs.** `CLAUDE.md` stack line becomes: `Python 3.12 · uv · PyAV/NumPy/Pillow · httpx · MediaPipe · FastAPI/Jinja2 (read-only job viewer: \`charswap view\`) · pytest (+cov ≥ 85 %) · ruff.` and add `- Viewer: \`uv run charswap view\`` under Commands. In `docs/scratchpad.md` tick the Plan 2 prerequisite "Stage status running/failed in state" and add "Job viewer (read-only)" as done under Active work. Append an `activity.md` entry (date, goal, what was built, test count/coverage, decisions: localhost-only + trusted-host guard, repair SAM tracked under composite, 1 s delayed browser open).

```bash
git add CLAUDE.md activity.md docs/scratchpad.md
git commit -m "docs: log job viewer completion"
```
