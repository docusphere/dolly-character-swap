# Scene Swap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap one person in one shot of a longer scene: `charswap prepare scene.mp4 --from S --to S --subject "..."` sends only that shot (cropped, padded to whole seconds) to Seedance, and the draft and final lay the AI's person back over the untouched scene with its original soundtrack.

**Architecture:**
- All local, free work lives in one new module, `media/scene.py`: shot cuts and snapping, black bars, the plan of the part (with every refusal before a question), the cut part with its frame map, the pasted region (A, B near A, grown, feathered), the one-person count and the frame-by-frame paste-back. It is built and tested first (Tasks 2 to 6), then checked on the real Harry job for free (Task 7).
- The job keeps the scene as `full-scene.mp4` and the cut part as `upload.mp4`, so the whole existing prep pipeline runs unchanged on the part; `pipeline.is_scene_job` guards every new branch (SAM on colors, the one-person check, the lay-back), so single-shot jobs take exactly today's code.
- The paid lay-back (`stages/paste.py`: SAM 3 "person" on the AI result, then the paste-back and the original sound) runs inside `generate`'s own tracked step, so resume and never-pay-twice keep working through the same records; its SAM run is in the price and logged once.

**Tech Stack:** Python 3.12, uv, PyAV (decode/encode, packet copy), NumPy, Pillow (Lanczos), SciPy (`maximum_filter`, `gaussian_filter`, `label`), FastAPI + Jinja2 (the walkthrough app), pytest + pytest-cov (≥ 85 % enforced), ruff.

---

**Spec:** `docs/superpowers/specs/2026-11-14-scene-swap-design.md` (decisions S1 to S14 and every section). This plan implements that spec; the places where the code needed a choice the spec does not make are listed under "Spec deviations and gap fills" at the end.

**Builds on:** branch `feat/scene-swap` at 62b7635 (main plus the spec; 1019 tests passing, 96.95 % coverage). The code below targets these existing APIs:
- `media.video`: `VideoWriter`, `iter_frames`, `count_frames`, `middle_frame`, `normalize`, `stream_seconds`, `FPS`; `media.audio`: `read_audio`, `embed_samples`, `restore_original_audio`, `add_original_soundtrack`, `_fit_video`, `RATE`, `FIT_LIMIT`, `MAX_MISMATCH`, `DurationMismatchError`; `media.composite.clean_border_fragments`
- `jobs.Job` (`create`, `load`, `path`, `state`, `update`, `mark_stage`, `stage_done`, `append_cost`), `JobError`; `errors.CharSwapError`; `costs.estimate_prepare`, `estimate_repair`, `cost_line`, `record_spend`, `usd_text`, `Estimate`, `Line`, `STAGE_PRICE`
- `pipeline.prepare`, `generate`, `_stage`, `_tracked`, `_wrap`, `_notify`, `_masks`, `_composite`, `_check_soundtrack_fits`, `_write_atomically`, `archive_stale_draft`, `build_request`, `remaining_paid_stages`, `generation_files`, `already_paid`, `resumes_without_charge`, `needs_new_charge`, `Services`, `PAID_STAGES`
- `stages.masks.segment`, `SAM_VERSION`; `stages.describe.describe_scene`, `DESCRIBE_MODEL`; `stages.StageError`; `providers.errors.ProviderError`; `providers.generators.seedance_wavespeed.SeedanceWaveSpeedGenerator`
- `cli.main`, `_prepare`, `_resume`, `_repair`, `_generate`, `_after_draft_hint`, `_provider_for`, `RIGHTS_QUESTION`, `Env.proceed`
- `web.pages.job_page`, `JobPage`, `Wipe`; `web.view_model.cost_rows`, `CostRow`, `prediction_id`, `prediction_url`, `file_if_present`; `web.steps`
- tests: `tests/fakes.py` (`FakeReplicate`, `FakeWaveSpeed`), `tests/helpers.py` (`solid`, `tone`, `write_video`, `write_wav`, `write_mask_pngs`), `tests/viewer_jobs.py` (`make_job`, `finish_stage`, `write_record`, `Media`), `tests/conftest.py` (`runs_dir`, `media`)

**Dry run:** every code block in this plan was applied in order, by a script that reads the **Create** / **Replace** / **Append** / **Delete** blocks exactly as written, to a scratch worktree of `feat/scene-swap` at 62b7635. After each task the task's own tests, `ruff format --check` and `ruff check` ran, then the task's own `git add` and `git commit` lines, and the tree was checked clean. Every task passed. The full suite ran after Tasks 6, 12, 14 and 15: 1019 → **1067** → **1122** → **1128 passing tests (2 live tests deselected), 97.05 % coverage**, ruff clean. Task 7's paste-back script was also run on the real Harry job while writing this plan (115 frames out, the new person exactly where Harry was, no visible seam), and `plan_part` + `cut_segment` were run on the test clip (cuts at frames 63 and 370, crop 1920x800 at y = 140, 308 real frames padded to 312, `normalize` keeps all 312 at 960x400; pasting the whole 17 s scene at 1080p takes about 36 s).

## Decisions taken in this plan

| # | Decision | Why |
|---|---|---|
| P1 | Order: the local media work first (Tasks 1 to 6), a free checkpoint on the real Harry job (Task 7), then the stage hooks, the pipeline, the CLI, the app, the docs, and last the live paid draft (Task 15). | The paste-back is the risky part; it is tested on synthetic clips and seen on real footage before anything is wired to a paid call. |
| P2 | `media/scene.py` raises `SceneError(CharSwapError)`, and `plan_part` returns the segment plus the summary facts (`scene_seconds`, `moved`, `inside`). | The CLI shows every refusal as a plain message (exit 2) with no extra wrapping; one free pass decides the whole part before any question. |
| P3 | The part is cut **after** both questions (it takes a few seconds on 1080p); `plan_part` (cuts, bars, sound check, summary) runs before them. | Every refusal still comes first, and a "no" costs no encoding time. |
| P4 | `lay_back` checks the AI clip (length, aspect) before buying SAM on it. | A clip that can't be laid back costs nothing more; the spec's error says "nothing more was charged". |
| P5 | Tests use synthetic scenes at 24000/1001 fps (`write_shots`), with `MIN_BOX` monkeypatched for the tiny clips, and `tests/scene_jobs.py` shares one synthetic scene job between the pipeline and CLI tests. | Real timing (23.976 vs 24) in every test, still fast; no copy of the same builder in two files. |
| P6 | The app keys on the presence of `full-scene.mp4` (the "before" side and the line), not on `pipeline.is_scene_job`. | `pages.py` stays a pure reader of files; a single-shot job has no such file. |

## Conventions for every task

- Run commands from the repo root: `"/Users/franciscojosejimeneznillardkam/Documents/Frank DOCS/Frank Projects/youtube/hf-genjutsu/character-swap"` (the path contains spaces; quote it). Branch: `feat/scene-swap`.
- How to read the code steps: **Create** `path` writes a new file; **Replace the whole file** `path` overwrites it; **Replace** in `path` swaps the first block for the second (the first block appears exactly once in the file at that point; tasks apply in order); **Append** to `path` adds two blank lines (one in Markdown) and then the block at the end of the file; **Delete** `path` is a `git rm`.
- Tests: `uv run pytest <path> -q --no-cov` while iterating; the full suite with the coverage gate is `uv run pytest`. Lint and format before every commit.
- Stage files **by name**; never `git add -A` / `git add .`. No attribution lines. Never commit `.env`, `runs/`, `inputs/`, `faces/`, `outputs/`, `models/`.
- Tests never call the network or a paid API: they use `tests/fakes.py` and the synthetic media in `tests/helpers.py`. Only Task 15 spends money, run by the controller with Frank's OK.
- User-facing text: plain English, short, no em dashes; every refusal before payment ends with "Nothing was charged.".
- If you start a server to look at something, say so and stop it when done (one is already running on port 8770; leave it).

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/character_swap/media/scene.py` | Create | shot cuts, snapping, black bars, `plan_part`, `cut_segment` + frame map, `job_frames`, `lay_alpha`, `crowded_frames`, `ai_clip`, `paste_back` (local, free) |
| `src/character_swap/stages/paste.py` | Create | the lay-back inside generate: SAM 3 on the result (paid, logged once), then paste-back and the original sound |
| `src/character_swap/media/video.py` | Modify | `VideoWriter(fps=Fraction, crf=)` |
| `src/character_swap/media/audio.py` | Modify | `restore_original_audio(fit=False)`, `add_original_soundtrack(fit=False)` |
| `src/character_swap/stages/masks.py` | Modify | `segment(on_created=)`, `NoMasks` |
| `src/character_swap/stages/describe.py` | Modify | a refused description names `resume --setting`; `use_setting` |
| `src/character_swap/costs.py` | Modify | `estimate_prepare(skip=)` |
| `src/character_swap/jobs.py` | Modify | `Job.create(scene=, segment=, setting=)`, `clean_setting` |
| `src/character_swap/pipeline.py` | Modify | `is_scene_job`, `prep_skip`, SAM on colors, the one-person check, the setting, the scene's sound check, `generation_estimate`, `needs_lay_back_charge`, the lay-back in `generate`, archiving `{stage}-sam` |
| `src/character_swap/cli.py` | Modify | `prepare --from/--to/--setting`, the summary, `resume --setting`, the price with the lay-back, the lay-back-only question, the repair refusal |
| `src/character_swap/web/pages.py`, `view_model.py`, `steps.py`, `templates/job.html` | Modify | the full scene as "before", `SCENE_LINE`, the cost rows |
| `tests/test_scene.py`, `tests/scene_jobs.py`, `tests/test_scene_pipeline.py`, `tests/test_scene_cli.py`, `tests/test_live_scene.py` | Create | tests per task (`scene_jobs.py` is a shared builder, `test_live_scene.py` is deselected by default) |
| `tests/helpers.py`, `tests/fakes.py` | Modify | `SCENE_RATE`, `write_shots`; `RefusingReplicate` |
| `tests/test_video.py`, `test_audio.py`, `test_vision_stages.py`, `test_text_stages.py`, `test_costs.py`, `test_pages.py`, `test_view_model.py`, `test_web_app.py` | Modify | tests per task |
| `README.md`, `CLAUDE.md` | Modify | the scene-swap section and command |

---

### Task 1: `VideoWriter` takes a fractional rate and a CRF; `fit=False` never re-times

The paste-back writes the scene at its own rate (24000/1001) and CRF 16 (S9, S10), and puts
the soundtrack back without ever re-encoding the picture at a whole-number rate. The defaults
(24 fps, CRF 18, `fit=True`) keep every existing caller exactly as it is.

**Files:**
- Modify: `src/character_swap/media/video.py`
- Modify: `src/character_swap/media/audio.py`
- Test: `tests/test_video.py`, `tests/test_audio.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_video.py`:

````python
import shutil
import subprocess

import av
````

with:

````python
import shutil
import subprocess
from fractions import Fraction

import av
````

**Append** to `tests/test_video.py`:

````python
def test_writer_keeps_a_fractional_rate_and_a_crf(tmp_path):
    rate = Fraction(24000, 1001)
    path = tmp_path / "scene.mp4"
    with VideoWriter(path, 64, 64, fps=rate, crf=16) as writer:
        for _ in range(48):
            writer.write(solid((120, 60, 30)))
    with av.open(str(path)) as container:
        assert container.streams.video[0].average_rate == rate
    assert count_frames(path) == 48
    assert stream_seconds(path, "video") == pytest.approx(48 / float(rate), abs=1e-3)


def test_writer_defaults_are_unchanged(tmp_path):
    path = tmp_path / "out.mp4"
    with VideoWriter(path, 64, 64) as writer:
        writer.write(solid((10, 10, 10)))
    with av.open(str(path)) as container:
        assert container.streams.video[0].average_rate == 24
````

**Replace** in `tests/test_audio.py`:

````python
from character_swap.media.audio import (
    DurationMismatchError,
````

with:

````python
from character_swap.media import audio
from character_swap.media.audio import (
    DurationMismatchError,
````

**Append** to `tests/test_audio.py`:

````python
def test_restore_without_fit_never_retimes_the_picture(tmp_path, monkeypatch):
    original = write_video(
        tmp_path / "scene.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24
    )
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)  # 0.33 s short
    monkeypatch.setattr(audio, "_fit_video", lambda *a: pytest.fail("the picture was re-timed"))
    with pytest.raises(DurationMismatchError):
        restore_original_audio(generated, original, tmp_path / "out.mp4", fit=False)


def test_restore_without_fit_copies_the_sound_when_it_fits(tmp_path):
    original = write_video(tmp_path / "scene.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 24)
    result = restore_original_audio(generated, original, tmp_path / "out.mp4", fit=False)
    assert audio_packets(result) == audio_packets(original)


def test_soundtrack_passes_fit_through(tmp_path, monkeypatch):
    upload = write_video(tmp_path / "scene.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)
    monkeypatch.setattr(audio, "_fit_video", lambda *a: pytest.fail("the picture was re-timed"))
    with pytest.raises(DurationMismatchError):
        add_original_soundtrack(generated, upload, tmp_path / "out.mp4", fit=False)
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_video.py tests/test_audio.py -q --no-cov`
Expected: the new tests FAIL (`unexpected keyword argument 'crf'` / `'fit'`); the old ones pass.

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/media/video.py`:

````python
    def __init__(self, path: Path, width: int, height: int, fps: int = FPS):
        self._path = Path(path)
        self._container = av.open(str(path), "w")
        self._stream = self._container.add_stream("libx264", rate=fps)
        self._stream.width, self._stream.height = width, height
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = {"crf": "18", "preset": "fast"}
        self._time_base = Fraction(1, fps)
````

with:

````python
    def __init__(
        self, path: Path, width: int, height: int, fps: int | Fraction = FPS, crf: int = 18
    ):
        self._path = Path(path)
        self._container = av.open(str(path), "w")
        self._stream = self._container.add_stream("libx264", rate=fps)
        self._stream.width, self._stream.height = width, height
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = {"crf": str(crf), "preset": "fast"}
        self._time_base = 1 / Fraction(fps)
````

**Replace** in `src/character_swap/media/audio.py`:

````python
def restore_original_audio(
    generated: Path, original: Path, dst: Path, *, seconds: float | None = None
) -> Path:
    """Replace the generated audio with the original's audio packets, byte for byte.

    With seconds, the original sound stops there (later packets are left out, never re-encoded).
    """
````

with:

````python
def restore_original_audio(
    generated: Path,
    original: Path,
    dst: Path,
    *,
    seconds: float | None = None,
    fit: bool = True,
) -> Path:
    """Replace the generated audio with the original's audio packets, byte for byte.

    With seconds, the original sound stops there (later packets are left out, never re-encoded).
    fit=False never re-times the picture (a scene keeps its own frame rate): a gap over
    MAX_MISMATCH raises instead.
    """
````

**Replace** in `src/character_swap/media/audio.py`:

````python
        if MAX_MISMATCH < abs(gap) <= FIT_LIMIT:
````

with:

````python
        if fit and MAX_MISMATCH < abs(gap) <= FIT_LIMIT:
````

**Replace** in `src/character_swap/media/audio.py`:

````python
def add_original_soundtrack(
    generated: Path, upload: Path, dst: Path, *, seconds: float | None = None
) -> Path:
    """Generated pictures + the upload's untouched audio; silent if the upload had none."""
    with av.open(str(upload)) as probe:
        has_audio = bool(probe.streams.audio)
    if not has_audio:
        return copy_video_only(generated, dst)
    return restore_original_audio(generated, upload, dst, seconds=seconds)
````

with:

````python
def add_original_soundtrack(
    generated: Path, upload: Path, dst: Path, *, seconds: float | None = None, fit: bool = True
) -> Path:
    """Generated pictures + the upload's untouched audio; silent if the upload had none."""
    with av.open(str(upload)) as probe:
        has_audio = bool(probe.streams.audio)
    if not has_audio:
        return copy_video_only(generated, dst)
    return restore_original_audio(generated, upload, dst, seconds=seconds, fit=fit)
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_video.py tests/test_audio.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/media/video.py src/character_swap/media/audio.py tests/test_video.py tests/test_audio.py
git commit -m "feat: VideoWriter takes a fractional rate and a CRF; soundtrack without re-timing"
````

---

### Task 2: Shot cuts and black bars (`media/scene.py`)

The free detection everything else stands on (S1, S4). A cut is a frame whose picture
changes (gray, 64 px wide, mean absolute difference) by at least 12 of 255 **and** by at
least 4 times the median change of the 24 frames around it, so a fade or a busy shot never
counts. Times are the frames' own timestamps, so a variable frame rate still works. A bar is
an edge row or column that stays at most 24 bright in **every** frame of the part; the box is
moved inward to even numbers, and a box under 300 px either way means "no bars" (a dark
scene loses nothing).

Checked on the test clip while writing this plan: cuts at frames 63 (2.628 s) and 370
(15.432 s); bars leave 1920x800 at y = 140 for frames 63 to 369 (row 940 is a soft edge at
brightness 57, so the inward rounding is what gives exactly 800 rows).

**Files:**
- Create: `src/character_swap/media/scene.py`
- Modify: `tests/helpers.py` (`SCENE_RATE`, `write_shots`)
- Test: `tests/test_scene.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/helpers.py`:

````python
RATE = 44100
````

with:

````python
RATE = 44100
SCENE_RATE = Fraction(24000, 1001)  # a film scene's rate (23.976 fps)
````

**Append** to `tests/helpers.py`:

````python
def write_shots(path: Path, shots, *, size=(64, 48), bars=0, fps=SCENE_RATE, audio_seconds=None):
    """A scene of solid-color shots, e.g. [((200, 30, 30), 30), ((30, 30, 200), 20)].

    bars: black rows at the top and at the bottom; the picture is between them.
    """
    frames = []
    for color, count in shots:
        frame = solid(color, size)
        if bars:
            frame[:bars] = 0
            frame[-bars:] = 0
        frames.extend([frame] * count)
    return write_video(path, frames, fps=fps, audio_seconds=audio_seconds)
````

**Create** `tests/test_scene.py`:

````python
import numpy as np
import pytest

from character_swap.media import scene
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 160, 30)


def at(frame: int) -> float:
    """The time of a frame at 23.976 fps."""
    return float(frame / SCENE_RATE)


@pytest.fixture
def small_boxes(monkeypatch):
    monkeypatch.setattr(scene, "MIN_BOX", 16)  # the test clips are far under 300 px


# ---- shot cuts -----------------------------------------------------------------
def test_cuts_are_the_first_frames_of_each_new_shot(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 40), (GREEN, 20)])
    assert scene.find_cuts(clip) == pytest.approx([at(30), at(70)], abs=1e-4)


def test_a_slow_fade_is_not_a_cut(tmp_path):
    frames = [solid((4 * i, 4 * i, 4 * i)) for i in range(48)]  # 4 of 255 a frame
    clip = write_video(tmp_path / "fade.mp4", frames, fps=SCENE_RATE)
    assert scene.find_cuts(clip) == []


def test_a_busy_shot_needs_a_change_well_above_its_own(tmp_path):
    rng = np.random.default_rng(1)
    frames = [rng.integers(0, 256, (48, 64, 3), dtype="uint8") for _ in range(48)]
    clip = write_video(tmp_path / "noise.mp4", frames, fps=SCENE_RATE)
    assert scene.find_cuts(clip) == []  # every frame changes a lot, so none stands out


def test_snap_moves_to_the_nearest_cut_within_the_window():
    cuts = [2.628, 15.432]
    assert scene.snap(2.6, cuts) == 2.628
    assert scene.snap(15.4, cuts) == 15.432
    assert scene.snap(9.0, cuts) == 9.0
    assert scene.snap(2.95, cuts) == 2.95  # 0.322 s away: used as typed
    assert scene.snap(2.6, []) == 2.6


def test_a_clip_without_video_is_refused(tmp_path):
    sound = write_wav(tmp_path / "a.wav", tone(1))
    with pytest.raises(scene.SceneError, match="a.wav has no video frames"):
        scene.find_cuts(sound)


# ---- black bars ----------------------------------------------------------------
def test_bars_are_found_and_left_out(tmp_path, small_boxes):
    clip = write_shots(tmp_path / "b.mp4", [(RED, 24)], size=(64, 96), bars=16)
    assert scene.find_bars(clip) == (0, 16, 64, 64)


def test_a_bright_object_entering_a_bar_keeps_that_bar(tmp_path, small_boxes):
    frames = [solid(RED, (64, 96)) for _ in range(24)]
    for frame in frames:
        frame[:16] = 0
        frame[-16:] = 0
    frames[10][:16, 16:32] = 255  # something bright crosses the top bar once
    clip = write_video(tmp_path / "b.mp4", frames, fps=SCENE_RATE)
    assert scene.find_bars(clip) == (0, 0, 64, 80)


def test_only_the_frames_of_the_part_count(tmp_path, small_boxes):
    frames = [solid(RED, (64, 96)) for _ in range(48)]
    for frame in frames[24:]:  # the second shot has bars, the first does not
        frame[:16] = 0
        frame[-16:] = 0
    clip = write_video(tmp_path / "b.mp4", frames, fps=SCENE_RATE)
    assert scene.find_bars(clip) == (0, 0, 64, 96)
    assert scene.find_bars(clip, at(24), at(48)) == (0, 16, 64, 64)


def test_a_picture_under_the_minimum_is_never_cropped(tmp_path):
    clip = write_shots(tmp_path / "b.mp4", [(RED, 24)], size=(64, 96), bars=16)
    assert scene.find_bars(clip) == (0, 0, 64, 96)  # 64 x 64 is under MIN_BOX (300 px)


def test_a_black_scene_is_never_cropped(tmp_path, small_boxes):
    clip = write_shots(tmp_path / "b.mp4", [((0, 0, 0), 24)])
    assert scene.find_bars(clip) == (0, 0, 64, 48)


def test_a_window_without_frames_is_refused(tmp_path):
    clip = write_shots(tmp_path / "b.mp4", [(RED, 24)])
    with pytest.raises(scene.SceneError, match="no frames between 5.00 s and 6.00 s"):
        scene.find_bars(clip, 5.0, 6.0)
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: FAIL at collection (`cannot import name 'scene'`).

- [ ] **Step 3: Write the code**

**Create** `src/character_swap/media/scene.py`:

````python
"""Scene swaps: find shot cuts and black bars, cut one part out, lay the AI's person back.

Local and free: nothing here calls the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from character_swap.errors import CharSwapError
from character_swap.media.video import FPS

CUT_MIN = 12.0  # a cut changes the picture by at least this much (mean, of 255) ...
CUT_RATIO = 4.0  # ... and by at least 4 times the median change of the frames around it
CUT_AROUND = 12  # frames on each side for that median (24 around it)
SNAP = 0.3  # seconds: --from and --to move to a shot cut this close
BAR_MAX = 24  # an edge row or column at most this bright (of 255) in every frame is a bar
MIN_BOX = 300  # px: a picture smaller than this either way is not cropped (a dark scene)


class SceneError(CharSwapError, ValueError):
    """The scene, the chosen part or the AI clip can't be used; the message says why."""


def _video_stream(container, path: Path):
    if not container.streams.video:
        raise SceneError(f"{Path(path).name} has no video frames.")
    stream = container.streams.video[0]
    stream.thread_type = "AUTO"
    return stream


@dataclass(frozen=True)
class _Scan:
    times: tuple[float, ...]  # every frame's time, in decode order
    cuts: tuple[float, ...]  # the time of the first frame of each new shot
    rate: Fraction
    width: int
    height: int


def _scan(path: Path) -> _Scan:
    """One pass: frame times, and each frame's change from the one before (gray, 64 px wide)."""
    times, changes, previous = [], [], None
    with av.open(str(path)) as container:
        stream = _video_stream(container, path)
        rate = Fraction(stream.average_rate or FPS)
        width, height = stream.width, stream.height
        small = max(2, round(64 * height / width))
        for frame in container.decode(stream):
            gray = frame.reformat(width=64, height=small, format="gray").to_ndarray()
            gray = gray.astype(np.int16)
            times.append(float(frame.time))
            changes.append(0.0 if previous is None else float(np.abs(gray - previous).mean()))
            previous = gray
    cuts = []
    for index in range(1, len(changes)):
        around = changes[max(1, index - CUT_AROUND) : index + CUT_AROUND + 1]
        change = changes[index]
        if change >= CUT_MIN and change >= CUT_RATIO * float(np.median(around)):
            cuts.append(times[index])
    return _Scan(tuple(times), tuple(cuts), rate, width, height)


def find_cuts(path: Path) -> list[float]:
    """Hard shot cuts (never fades): the time of the first frame of each new shot."""
    return list(_scan(path).cuts)


def snap(moment: float, cuts, window: float = SNAP) -> float:
    """The shot cut nearest to `moment` within `window` seconds, else `moment` itself."""
    near = [cut for cut in cuts if abs(cut - moment) <= window + 1e-9]
    return min(near, key=lambda cut: abs(cut - moment)) if near else moment


def _luma_extent(path: Path, start: float, end: float) -> tuple[np.ndarray, np.ndarray, int]:
    """The brightest value of every row and every column over the frames in [start, end),
    and how many frames that was."""
    rows = columns = None
    count = 0
    with av.open(str(path)) as container:
        stream = _video_stream(container, path)
        for frame in container.decode(stream):
            moment = float(frame.time)
            if moment < start - 1e-6 or moment >= end - 1e-6:
                continue
            luma = frame.to_ndarray(format="gray")
            row, column = luma.max(axis=1), luma.max(axis=0)
            rows = row if rows is None else np.maximum(rows, row)
            columns = column if columns is None else np.maximum(columns, column)
            count += 1
    if rows is None or columns is None:
        raise SceneError(f"{Path(path).name} has no frames between {start:.2f} s and {end:.2f} s.")
    return rows, columns, count


def _inward(lit: np.ndarray) -> tuple[int, int]:
    """The first and one-past-the-last lit index, moved inward to even numbers."""
    first, stop = int(lit[0]), int(lit[-1]) + 1
    return first + first % 2, stop - stop % 2


def _box(rows: np.ndarray, columns: np.ndarray) -> tuple[int, int, int, int]:
    height, width = len(rows), len(columns)
    whole = (0, 0, width // 2 * 2, height // 2 * 2)
    lit_rows, lit_columns = np.flatnonzero(rows > BAR_MAX), np.flatnonzero(columns > BAR_MAX)
    if not len(lit_rows) or not len(lit_columns):
        return whole
    top, bottom = _inward(lit_rows)
    left, right = _inward(lit_columns)
    if right - left < MIN_BOX or bottom - top < MIN_BOX:
        return whole
    return left, top, right - left, bottom - top


def find_bars(path: Path, start: float = 0.0, end: float = math.inf) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the picture inside black bars, in even numbers.

    A bar is an edge row or column that stays near-black in every frame between start and
    end; anything bright that ever enters it keeps it. No bars: the whole frame.
    """
    rows, columns, _ = _luma_extent(path, start, end)
    return _box(rows, columns)
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: 11 passed.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/media/scene.py tests/helpers.py tests/test_scene.py
git commit -m "feat: find shot cuts and black bars in a scene"
````

---

### Task 3: Plan the part: snap, frames, crop and the sound check (`plan_part`)

Everything `prepare` needs to know before any question (S1, S2, S4, Errors): each edge
snaps to a shot cut within 0.3 s, else goes to the nearest frame; the part must be 1 to 30 s
and inside the scene, `--from` first; the scene's sound and picture may differ by at most
0.25 s; a cut inside the part is reported (a warning, not a stop). The result's `segment` is
what `state.json` keeps (`end_frame` is the first frame **after** the part; Task 4 adds the
frame map to it).

**Files:**
- Modify: `src/character_swap/media/scene.py`
- Test: `tests/test_scene.py`

- [ ] **Step 1: Write the failing tests**

**Append** to `tests/test_scene.py`:

````python
# ---- planning the part -----------------------------------------------------------
@pytest.fixture
def three_shots(tmp_path):
    """Police (30 frames), the face (120 frames), a masked man (30 frames); bars of 16 px."""
    return write_shots(
        tmp_path / "scene.mp4",
        [(RED, 30), (BLUE, 120), (GREEN, 30)],
        size=(64, 96),
        bars=16,
        audio_seconds=at(180),
    )


def test_edges_snap_to_the_shot_cuts_nearby(three_shots, small_boxes):
    plan = scene.plan_part(three_shots, 1.1, 6.4)
    segment = plan.segment
    assert (segment["start_frame"], segment["end_frame"]) == (30, 150)
    assert segment["start"] == pytest.approx(at(30), abs=1e-5)
    assert segment["end"] == pytest.approx(at(150), abs=1e-5)
    assert segment["seconds"] == pytest.approx(at(120), abs=1e-5)  # 5.005 s
    assert segment["padded_seconds"] == 6
    assert segment["crop"] == {"x": 0, "y": 16, "w": 64, "h": 64}
    assert (segment["fps"], segment["width"], segment["height"]) == ("24000/1001", 64, 96)
    assert segment["cuts"] == pytest.approx([at(30), at(150)], abs=1e-5)
    assert plan.moved == (True, True) and plan.inside == ()
    assert plan.scene_seconds == pytest.approx(at(180), abs=1e-3)


def test_without_a_cut_nearby_the_typed_time_goes_to_the_nearest_frame(three_shots):
    plan = scene.plan_part(three_shots, 2.0, 4.0)
    assert (plan.segment["start_frame"], plan.segment["end_frame"]) == (48, 96)
    assert plan.segment["padded_seconds"] == 4  # 2.0 s: Seedance's minimum is 4
    assert plan.moved == (False, False)


def test_a_cut_inside_the_part_is_reported(three_shots):
    plan = scene.plan_part(three_shots, 0.0, 7.5)
    assert (plan.segment["start_frame"], plan.segment["end_frame"]) == (0, 180)
    assert plan.inside == pytest.approx((at(30), at(150)), abs=1e-5)


@pytest.mark.parametrize(("start", "end"), [(3.0, 3.0), (4.0, 3.0), (-1.0, 3.0), (1.0, 9.0)])
def test_times_outside_the_scene_or_in_the_wrong_order_are_refused(three_shots, start, end):
    with pytest.raises(scene.SceneError, match=r"The scene is 7\.5 s; pick --from and --to"):
        scene.plan_part(three_shots, start, end)


def test_a_part_under_a_second_is_refused(three_shots):
    with pytest.raises(scene.SceneError, match=r"The part to swap is 0\.5 s; it must be 1 to 30 s"):
        scene.plan_part(three_shots, 2.0, 2.5)


def test_a_part_over_the_limit_is_refused(three_shots, monkeypatch):
    monkeypatch.setattr(scene, "MAX_PART", 3.0)
    with pytest.raises(scene.SceneError, match=r"is 5\.0 s; it must be 1 to 3 s"):
        scene.plan_part(three_shots, 1.1, 6.4)


def test_a_scene_whose_sound_is_much_shorter_is_refused(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 90)], audio_seconds=4.0)
    with pytest.raises(scene.SceneError, match=r"sound lasts 4\.0 s but its picture 5\.0 s"):
        scene.plan_part(clip, 1.2, 4.5)


def test_a_silent_scene_passes_the_sound_check(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 90)])
    assert scene.plan_part(clip, 1.2, 4.5).segment["start_frame"] == 30


def test_a_missing_scene_is_refused(tmp_path):
    with pytest.raises(scene.SceneError, match="Video not found"):
        scene.plan_part(tmp_path / "nope.mp4", 1.0, 2.0)


@pytest.mark.parametrize(("seconds", "padded"), [(1.0, 4), (4.0, 4), (4.0001, 5), (12.8, 13)])
def test_the_part_is_padded_to_whole_seconds(seconds, padded):
    assert scene.padded_seconds(seconds) == padded
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: the new tests FAIL (`has no attribute 'plan_part'`).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/media/scene.py`:

````python
from character_swap.errors import CharSwapError
from character_swap.media.video import FPS
````

with:

````python
from character_swap.errors import CharSwapError
from character_swap.media.audio import MAX_MISMATCH
from character_swap.media.video import FPS, stream_seconds
````

**Append** to `src/character_swap/media/scene.py`:

````python
MIN_PART, MAX_PART = 1.0, 30.0  # seconds the part to swap may last
MIN_SECONDS = 4  # Seedance's shortest clip; the part is padded to whole seconds, at least this


def check_sound(path: Path) -> None:
    """Refuse a scene whose sound and picture lengths differ by more than MAX_MISMATCH."""
    with av.open(str(path)) as probe:
        if not probe.streams.audio:
            return  # no sound at all: the result is silent too
    sound, picture = stream_seconds(path, "audio"), stream_seconds(path, "video")
    if abs(sound - picture) > MAX_MISMATCH:
        raise SceneError(
            f"The scene's sound lasts {sound:.1f} s but its picture {picture:.1f} s, so the "
            "original soundtrack could not be put back. Re-export the scene so both have the "
            "same length. Nothing was charged."
        )


def padded_seconds(seconds: float) -> int:
    """Whole seconds the AI gets: the part rounded up, at least MIN_SECONDS."""
    return max(MIN_SECONDS, math.ceil(seconds - 1e-6))


@dataclass(frozen=True)
class Plan:
    segment: dict  # saved in state.json as "segment"; cut_segment's frame map is added to it
    scene_seconds: float
    moved: tuple[bool, bool]  # --from and --to sit on a shot cut
    inside: tuple[float, ...]  # shot cuts inside the part, more than SNAP from both edges


def _nearest(edges: list[float], moment: float) -> int:
    return min(range(len(edges)), key=lambda index: abs(edges[index] - moment))


def plan_part(scene: Path, start: float, end: float) -> Plan:
    """Free checks and facts for swapping start..end of `scene` (seconds), before any question.

    Each edge moves to a shot cut within SNAP seconds, else to the nearest frame.
    """
    if not Path(scene).is_file():
        raise SceneError(f"Video not found: {scene}")
    scan = _scan(scene)
    picture = stream_seconds(scene, "video")
    if not 0 <= start < end <= picture + 0.05:
        raise SceneError(
            f"The scene is {picture:.1f} s; pick --from and --to inside it, --from first. "
            "Nothing was charged."
        )
    edges = [*scan.times, picture]  # the part ends where the next frame (or the scene) starts
    first, last = snap(start, scan.cuts), snap(end, scan.cuts)
    start_frame, end_frame = _nearest(edges, first), _nearest(edges, last)
    begin, finish = edges[start_frame], edges[end_frame]
    seconds = finish - begin
    if not MIN_PART - 1e-6 <= seconds <= MAX_PART + 1e-6:
        raise SceneError(
            f"The part to swap is {seconds:.1f} s; it must be {MIN_PART:g} to {MAX_PART:g} s. "
            "Nothing was charged."
        )
    check_sound(scene)
    x, y, w, h = find_bars(scene, begin, finish)
    segment = {
        "start": round(begin, 6),
        "end": round(finish, 6),
        "start_frame": start_frame,
        "end_frame": end_frame,  # the first frame after the part
        "seconds": round(seconds, 6),
        "padded_seconds": padded_seconds(seconds),
        "fps": str(scan.rate),
        "width": scan.width,
        "height": scan.height,
        "crop": {"x": x, "y": y, "w": w, "h": h},
        "cuts": [round(cut, 6) for cut in scan.cuts],
    }
    inside = tuple(cut for cut in scan.cuts if begin + SNAP < cut < finish - SNAP)
    return Plan(segment, picture, (first in scan.cuts, last in scan.cuts), inside)
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/media/scene.py tests/test_scene.py
git commit -m "feat: plan the part of a scene to swap (snap to cuts, crop, sound check)"
````

---

### Task 4: Cut the part for the AI, and the frame map (`cut_segment`, `job_frames`)

S2, S3: the part becomes the job's `upload.mp4`: cropped to the box, 24 fps, exactly N x 24
frames (N = whole seconds, at least 4), the last real frame held to N s, the scene's sound
from start to end as stereo AAC padded with silence. Job frame k shows the latest scene frame
at or before `start + k/24`, the same rule `normalize` uses, so `normalize` keeps every frame
(it is already 24 fps and whole seconds). The frame map (one scene frame index per real job
frame) is saved, so the paste-back never recomputes it. `job_frames` turns it around: for each
scene frame, the job frame that shows it (`k(i)` in the spec). At 23.976 fps job frame k shows
scene frame k - 1 for most k, which is why `round((t - start) x 24)` would be a frame late.

On the test clip (checked while writing this plan): frames 63 to 369 give 308 real job frames
(`[63, 63, 64, 65, ...]`), padded to 312; `normalize` keeps all 312 at 960x400.

**Files:**
- Modify: `src/character_swap/media/scene.py`
- Test: `tests/test_scene.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_scene.py`:

````python
from character_swap.media import scene
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav
````

with:

````python
from character_swap.media import scene
from character_swap.media.audio import RATE, read_audio
from character_swap.media.video import count_frames, iter_frames, normalize, stream_seconds
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav
````

**Append** to `tests/test_scene.py`:

````python
# ---- cutting the part --------------------------------------------------------------
def _cut(clip, tmp_path, start, end):
    plan = scene.plan_part(clip, start, end)
    crop = plan.segment["crop"]
    box = (crop["x"], crop["y"], crop["w"], crop["h"])
    part = tmp_path / "part.mp4"
    frame_map = scene.cut_segment(clip, plan.segment["start"], plan.segment["end"], box, part)
    return plan.segment, frame_map, part


def test_the_part_is_cropped_24_fps_and_whole_seconds(three_shots, tmp_path, small_boxes):
    segment, frame_map, part = _cut(three_shots, tmp_path, 1.1, 6.4)
    frames = list(iter_frames(part))
    assert len(frames) == 6 * 24 and frames[0].shape == (64, 64, 3)
    assert stream_seconds(part, "video") == pytest.approx(6.0, abs=1e-6)
    assert len(frame_map) == 121  # ceil(5.005 s x 24)
    assert (frame_map[0], frame_map[1], frame_map[2], frame_map[-1]) == (30, 30, 31, 149)
    for frame in frames:  # only the face shot: no police frame, no bars
        assert abs(frame.astype(int) - BLUE).mean() < 6
    last_real = frames[len(frame_map) - 1].astype(int)
    for held in frames[len(frame_map) :]:
        assert abs(held.astype(int) - last_real).max() <= 2


def test_the_parts_sound_is_the_scenes_padded_with_silence(three_shots, tmp_path):
    _segment, _frame_map, part = _cut(three_shots, tmp_path, 1.1, 6.4)
    assert stream_seconds(part, "audio") == pytest.approx(6.0, abs=0.05)
    sound = read_audio(part)
    assert abs(sound[:, RATE : 2 * RATE]).max() > 0.05  # the tone
    assert abs(sound[:, round(5.2 * RATE) : round(5.9 * RATE)]).max() < 1e-3  # the padding


def test_normalize_keeps_every_frame_of_the_part(three_shots, tmp_path):
    _segment, _frame_map, part = _cut(three_shots, tmp_path, 1.1, 6.4)
    facts = normalize(
        part, tmp_path / "original.mp4", min_seconds=4, max_seconds=30, whole_seconds=True
    )
    assert facts["frames"] == 6 * 24 and count_frames(tmp_path / "original.mp4") == 6 * 24


def test_a_silent_scene_gives_a_silent_part(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 90)])
    _segment, _frame_map, part = _cut(clip, tmp_path, 1.2, 4.5)
    assert count_frames(part) == 4 * 24
    with pytest.raises(ValueError, match="no audio stream"):
        stream_seconds(part, "audio")


def test_the_frame_map_at_23_976_fps(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(BLUE, 310)])
    part = tmp_path / "part.mp4"
    frame_map = scene.cut_segment(clip, 0.0, at(310), (0, 0, 64, 48), part)
    assert frame_map[:4] == [0, 0, 1, 2]  # job frame k shows scene frame k - 1
    assert len(frame_map) == 311 and frame_map[-1] == 309
    assert count_frames(part) == 13 * 24
    made_from = scene.job_frames(frame_map, 0, 310)
    assert made_from[0] == 0 and made_from[1] == 2 and made_from[300] == 301
    assert made_from[-1] == 310


def test_the_frame_map_at_24_fps_is_one_to_one(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(BLUE, 96)], fps=24)
    frame_map = scene.cut_segment(clip, 0.0, 4.0, (0, 0, 64, 48), tmp_path / "part.mp4")
    assert frame_map == list(range(96))
    assert scene.job_frames(frame_map, 0, 96) == list(range(96))


def test_job_frames_holds_the_last_one_past_the_map():
    assert scene.job_frames([5, 6, 7], 5, 10) == [0, 1, 2, 2, 2]
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: the new tests FAIL (`has no attribute 'cut_segment'`).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/media/scene.py`:

````python
import math
from dataclasses import dataclass
````

with:

````python
import math
import shutil
import tempfile
from dataclasses import dataclass
````

**Replace** in `src/character_swap/media/scene.py`:

````python
from character_swap.media.audio import MAX_MISMATCH
from character_swap.media.video import FPS, stream_seconds
````

with:

````python
from character_swap.media.audio import MAX_MISMATCH, RATE, embed_samples, read_audio
from character_swap.media.video import FPS, VideoWriter, stream_seconds
````

**Append** to `src/character_swap/media/scene.py`:

````python
def cut_segment(
    scene: Path, start: float, end: float, crop: tuple[int, int, int, int], dst: Path
) -> list[int]:
    """Write the part the AI gets: cropped, 24 fps, exactly N x 24 frames, sound padded.

    Job frame k shows the latest scene frame at or before start + k/24 (normalize's rule).
    After the last real frame, that frame is held to N seconds; the sound is the scene's from
    start to end (counted from its first audio packet), as stereo AAC, padded with silence.
    Returns the frame map: the scene frame index of every real job frame.
    """
    x, y, w, h = crop
    padded = padded_seconds(end - start)
    real = math.ceil((end - start) * FPS - 1e-6)
    frame_map: list[int] = []
    with tempfile.TemporaryDirectory() as scratch:
        silent = Path(scratch) / "part.mp4"
        with av.open(str(scene)) as container, VideoWriter(silent, w, h) as writer:
            stream = _video_stream(container, scene)
            frames = enumerate(container.decode(stream))
            current, upcoming, picture = None, next(frames, None), None
            for k in range(real):
                moment = start + k / FPS
                while (
                    upcoming is not None
                    and float(upcoming[1].time) <= moment + 1e-6
                    and float(upcoming[1].time) < end - 1e-6
                ):
                    current, upcoming = upcoming, next(frames, None)
                if current is None:
                    raise SceneError(f"{Path(scene).name} has no frame at {start:.2f} s.")
                if not frame_map or frame_map[-1] != current[0]:
                    picture = current[1].to_ndarray(format="rgb24")[y : y + h, x : x + w]
                frame_map.append(current[0])
                writer.write(picture)
            for _ in range(padded * FPS - real):
                writer.write(picture)
        with av.open(str(scene)) as probe:
            has_audio = bool(probe.streams.audio)
        if not has_audio:
            shutil.copyfile(silent, dst)
            return frame_map
        sound = read_audio(scene)[:, round(start * RATE) : round(end * RATE)]
        samples = np.zeros((2, padded * RATE), dtype=np.float32)
        samples[:, : sound.shape[1]] = sound[:, : padded * RATE]
        embed_samples(silent, samples, dst)
    return frame_map


def job_frames(frame_map: list[int], first: int, stop: int) -> list[int]:
    """For each scene frame first..stop-1, the job frame made from it: the first k whose
    scene frame is at or after it (the last real job frame when none is)."""
    found, k, last = [], 0, len(frame_map) - 1
    for index in range(first, stop):
        while k < last and frame_map[k] < index:
            k += 1
        found.append(k)
    return found
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/media/scene.py tests/test_scene.py
git commit -m "feat: cut the part of a scene for the AI, with its frame map"
````

---

### Task 5: The pasted region and the one-person count (`lay_alpha`, `crowded_frames`)

S8: the region is `dilate(A ∪ (B ∩ near(A)))` with a feathered edge. A is the original
subject mask, B is SAM 3 "person" on the AI result, `near(A)` is A grown by 4 % of the
picture height; the dilation is 1.5 %, the feather a Gaussian of 1 %. Growing uses a square
maximum filter (separable, fast at 1920x800); the Gaussian is truncated at 4 sigma, so far
from the region the alpha is exactly 0 and the scene's pixels are kept exactly. S6's count
lives here too: a frame is "crowded" when its mask has a second separate region at least
25 % the size of the largest.

**Files:**
- Modify: `src/character_swap/media/scene.py`
- Test: `tests/test_scene.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_scene.py`:

````python
import numpy as np
import pytest
````

with:

````python
import numpy as np
import pytest
from PIL import Image
````

**Append** to `tests/test_scene.py`:

````python
# ---- the pasted region -------------------------------------------------------------
def _box_mask(top, bottom, left, right, size=(400, 600)):
    mask = np.zeros(size, dtype=bool)
    mask[top:bottom, left:right] = True
    return mask


def test_the_region_covers_a_and_feathers_its_edge():
    a = _box_mask(150, 250, 250, 350)  # 400 px high: grow 6 px, feather sigma 4 px
    alpha = scene.lay_alpha(a, None)
    assert alpha[200, 300] > 0.99  # inside A
    assert 0.9 < alpha[150, 300] < 1.0  # on A's edge: inside the grown region, feathered
    assert alpha[150 - 8, 300] < 0.5  # 8 px out: past the 6 px growth
    assert alpha[50, 50] == 0.0  # far away: exactly the scene


def test_b_next_to_a_is_added_but_someone_far_away_is_not():
    a = _box_mask(150, 250, 250, 350)
    hair = _box_mask(140, 250, 250, 350)  # SAM 3 on the result: 10 px more on top
    shoulder = _box_mask(300, 380, 500, 580)  # another person the AI redrew, far from A
    alpha = scene.lay_alpha(a, hair | shoulder)
    assert alpha[150 - 8, 300] > 0.9  # the hairline is pasted
    assert alpha[340, 540] == 0.0  # the shoulder is not
    assert scene.lay_alpha(a, np.zeros_like(a)).tolist() == scene.lay_alpha(a, None).tolist()


def _write_masks(folder, masks):
    folder.mkdir(parents=True, exist_ok=True)
    for index, mask in enumerate(masks):
        Image.fromarray(mask.astype(np.uint8) * 255).save(folder / f"mask_{index:05d}.png")
    return folder


def test_crowded_frames_count_a_second_person(tmp_path):
    one = _box_mask(10, 40, 10, 40, size=(64, 96))
    two = one | _box_mask(10, 40, 60, 90, size=(64, 96))  # the same size again
    speck = one | _box_mask(50, 54, 60, 64, size=(64, 96))  # 16 px: under 25 % of 900
    empty = np.zeros((64, 96), dtype=bool)
    folder = _write_masks(tmp_path / "masks", [one, two, speck, empty])
    assert scene.crowded_frames(folder) == (1, 4)
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: the new tests FAIL (`has no attribute 'lay_alpha'`).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/media/scene.py`:

````python
import av
import numpy as np

from character_swap.errors import CharSwapError
````

with:

````python
import av
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, label, maximum_filter

from character_swap.errors import CharSwapError
````

**Append** to `src/character_swap/media/scene.py`:

````python
NEAR = 0.04  # B counts only this close to A (share of the picture height)
GROW = 0.015  # the pasted region reaches this far past A and B
FEATHER = 0.01  # its edge is blurred this much (Gaussian sigma)
SECOND = 0.25  # a separate region at least this share of the largest is another person
CROWDED = 0.10  # more than this share of such frames stops a scene job's prepare


def _grow(mask: np.ndarray, radius: float) -> np.ndarray:
    size = 2 * max(1, round(radius)) + 1
    return maximum_filter(mask.astype(np.uint8), size=size) > 0


def lay_alpha(a: np.ndarray, b: np.ndarray | None) -> np.ndarray:
    """The pasted region's alpha (0 to 1): dilate(A + (B near A)), feathered.

    a: the original subject mask; b: SAM 3 "person" on the AI result, or None. Both are
    booleans the size of the crop box. B away from A (someone else the AI redrew) is left out.
    """
    height = a.shape[0]
    union = a.copy()
    if b is not None:
        union |= b & _grow(a, NEAR * height)
    region = _grow(union, GROW * height)
    return gaussian_filter(region.astype(np.float32), sigma=FEATHER * height)


def crowded_frames(mask_dir: Path) -> tuple[int, int]:
    """(frames with a second separate region at least SECOND the size of the largest, frames)."""
    files = sorted(Path(mask_dir).glob("mask_*.png"))
    crowded = 0
    for path in files:
        labels, count = label(np.asarray(Image.open(path).convert("L")) > 127)
        if count < 2:
            continue
        sizes = np.sort(np.bincount(labels.ravel())[1:])
        if sizes[-2] >= SECOND * sizes[-1]:
            crowded += 1
    return crowded, len(files)
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/media/scene.py tests/test_scene.py
git commit -m "feat: the pasted region (A, B near A, grown and feathered) and the crowd count"
````

---

### Task 6: Lay the AI's person back into the scene, and splice the original sound (`paste_back`)

S7 to S10 and "Timing, position and honest limits". `paste_back` streams the scene one frame
at a time (O(1) memory). Outside the part every frame is written as decoded; inside it, scene
frame i takes job frame `k(i)` (Task 4), mask A `k(i)`, AI frame `round(k(i) x ai_fps / 24)`
(the AI's rate is read from its file, never assumed), held at its last frame. `ai_clip` finds
the AI clip's own black bars once (`find_bars` on the raw clip, bars included, which is also
what SAM saw) and cuts that box from every AI frame and every B mask before scaling to the
crop box (Lanczos for the picture). It refuses, before anything is paid, an AI clip more than
0.5 s shorter than the part or whose picture's aspect is more than 5 % off (the AI cropped).
The video is written at the scene's own rate, CRF 16; the sound goes back with
`add_original_soundtrack(..., fit=False)` (Task 1), so its packets are byte-for-byte the
scene's.

**Files:**
- Modify: `src/character_swap/media/scene.py`
- Test: `tests/test_scene.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_scene.py`:

````python
import numpy as np
import pytest
from PIL import Image
````

with:

````python
import av
import numpy as np
import pytest
from PIL import Image
````

**Replace** in `tests/test_scene.py`:

````python
from character_swap.media.audio import RATE, read_audio
````

with:

````python
from character_swap.media.audio import RATE, add_original_soundtrack, read_audio
````

**Replace** in `tests/test_scene.py`:

````python
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav
````

with:

````python
from helpers import (
    SCENE_RATE,
    solid,
    tone,
    write_mask_pngs,
    write_shots,
    write_video,
    write_wav,
)
````

**Append** to `tests/test_scene.py`:

````python
# ---- laying the AI's person back -----------------------------------------------------
YELLOW, MAGENTA = (220, 220, 40), (220, 40, 220)


@pytest.fixture
def lay(tmp_path, small_boxes):
    """A 120-frame scene (bars of 16 px) whose middle shot (frames 24 to 95) is swapped.

    The crop box is 128 x 64 at y = 16; mask A is the square x 48-80, y 16-48 of it.
    """
    clip = write_shots(
        tmp_path / "scene.mp4",
        [(RED, 24), (BLUE, 72), (GREEN, 24)],
        size=(128, 96),
        bars=16,
        audio_seconds=at(120),
    )
    plan = scene.plan_part(clip, 1.0, 4.0)
    crop = plan.segment["crop"]
    box = (crop["x"], crop["y"], crop["w"], crop["h"])
    frame_map = scene.cut_segment(
        clip, plan.segment["start"], plan.segment["end"], box, tmp_path / "part.mp4"
    )
    masks_a = write_mask_pngs(tmp_path / "a", 96, box=(48, 16, 80, 48), size=(128, 64))
    return clip, {**plan.segment, "frame_map": frame_map}, masks_a


def _ai(path, frames=96, size=(256, 256), bars=64):
    """The AI's clip: its own bars, a yellow room and the new person (magenta) where A is."""
    picture = solid(YELLOW, size)
    picture[:bars] = 0
    picture[size[1] - bars :] = 0
    picture[96:160, 96:160] = MAGENTA  # x 48-80, y 16-48 of the 128 x 64 box, at twice the size
    return write_video(path, [picture] * frames)


def _masks_b(folder, frames=96):
    """SAM 3 "person" on the AI clip, at half its size: the person and a far-away shoulder."""
    folder.mkdir(parents=True)
    for index in range(frames):
        mask = np.zeros((128, 128), dtype=np.uint8)
        mask[48:80, 48:80] = 255  # the new person
        mask[40:60, 100:124] = 255  # someone else the AI redrew
        Image.fromarray(mask).save(folder / f"mask_{index:05d}.png")
    return folder


def _near(frame, x, y, color):
    patch = frame[y - 2 : y + 2, x - 2 : x + 2].astype(int)
    return abs(patch - color).mean() < 16


def test_the_ai_person_lands_exactly_on_a_and_nothing_else_changes(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "draft-raw.mp4")
    out = tmp_path / "pasted.mp4"
    scene.paste_back(clip, raw, masks_a, _masks_b(tmp_path / "b"), segment, out)
    frames, originals = list(iter_frames(out)), list(iter_frames(clip))
    assert len(frames) == len(originals) == 120
    with av.open(str(out)) as container:
        assert container.streams.video[0].average_rate == SCENE_RATE
    for index in (0, 23, 96, 119):  # outside the part: the scene as decoded
        assert abs(frames[index].astype(int) - originals[index]).mean() < 2
    for index in (24, 60, 95):  # the first, a middle and the last frame of the part
        frame = frames[index]
        assert _near(frame, 64, 48, MAGENTA)  # A's middle (the box starts at y = 16)
        assert _near(frame, 51, 35, MAGENTA) and _near(frame, 77, 61, MAGENTA)  # A's corners
        assert _near(frame, 36, 48, BLUE)  # beside A: the scene, not the AI's room
        assert _near(frame, 112, 34, BLUE)  # the far-away shoulder is not pasted
        assert frame[:12].max() <= 24 and frame[84:].max() <= 24  # the bars stay black


def test_the_spliced_scene_keeps_its_sound_byte_for_byte(lay, tmp_path):
    clip, segment, masks_a = lay
    silent = tmp_path / "pasted.mp4"
    scene.paste_back(clip, _ai(tmp_path / "raw.mp4"), masks_a, None, segment, silent)
    final = add_original_soundtrack(silent, clip, tmp_path / "draft.mp4", fit=False)

    def packets(path):
        with av.open(str(path)) as container:
            return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]

    assert packets(final) == packets(clip)
    assert count_frames(final) == 120


def test_a_slightly_short_ai_clip_holds_its_last_frame(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "raw.mp4", frames=64)  # 2.67 s for a 3.0 s part: 0.34 s short
    out = tmp_path / "pasted.mp4"
    scene.paste_back(clip, raw, masks_a, None, segment, out)
    assert _near(list(iter_frames(out))[95], 64, 48, MAGENTA)


def test_a_much_shorter_ai_clip_is_refused(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "draft-raw.mp4", frames=48)  # 2.0 s for a 3.0 s part
    with pytest.raises(scene.SceneError, match=r"256x256, 2\.0 s; it can't be laid back into"):
        scene.paste_back(clip, raw, masks_a, None, segment, tmp_path / "out.mp4")
    assert not (tmp_path / "out.mp4").exists()


def test_an_ai_clip_with_another_aspect_is_refused(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "draft-raw.mp4", bars=0)  # square: the AI cropped the picture
    with pytest.raises(scene.SceneError, match=r"the 3\.0 s shot\. It is kept at .*draft-raw"):
        scene.paste_back(clip, raw, masks_a, None, segment, tmp_path / "out.mp4")


def test_too_few_subject_masks_are_refused(lay, tmp_path):
    clip, segment, _masks_a = lay
    few = write_mask_pngs(tmp_path / "few", 10, box=(48, 16, 80, 48), size=(128, 64))
    with pytest.raises(scene.SceneError, match="10 masks for the part's 73 frames"):
        scene.paste_back(clip, _ai(tmp_path / "raw.mp4"), few, None, segment, tmp_path / "o.mp4")
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: the new tests FAIL (`has no attribute 'paste_back'`).

- [ ] **Step 3: Write the code**

**Append** to `src/character_swap/media/scene.py`:

````python
SHORT = 0.5  # an AI clip this much shorter than the part holds its last frame
ASPECT = 0.05  # the AI picture's aspect may differ this much from the crop box's
CRF = 16  # the one re-encode of the scene: visually lossless


@dataclass(frozen=True)
class AiClip:
    width: int
    height: int
    frames: int
    rate: Fraction  # read from the file, never assumed
    box: tuple[int, int, int, int]  # its picture inside any black bars the AI added


def ai_clip(raw: Path, segment: dict) -> AiClip:
    """Facts about the AI's clip. Refuses one too short or with another aspect (cropped)."""
    with av.open(str(raw)) as probe:
        stream = _video_stream(probe, raw)
        rate = Fraction(stream.average_rate or FPS)
        width, height = stream.width, stream.height
    rows, columns, frames = _luma_extent(raw, 0.0, math.inf)
    box = _box(rows, columns)
    crop = segment["crop"]
    seconds = float(frames / rate)
    aspect = (box[2] / box[3]) / (crop["w"] / crop["h"])
    if seconds < segment["seconds"] - SHORT or abs(aspect - 1) > ASPECT:
        raise SceneError(
            f"The AI clip is {width}x{height}, {seconds:.1f} s; it can't be laid back into the "
            f"{segment['seconds']:.1f} s shot. It is kept at {raw}; nothing more was charged. "
            "A new draft would most likely come back the same, so don't buy one; this needs a "
            "fix in the tool."
        )
    return AiClip(width, height, frames, rate, box)


def _mask(
    path: Path,
    size: tuple[int, int],
    *,
    frame: tuple[int, int] | None = None,
    box: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """A mask PNG as booleans of `size`: first scaled to `frame`, then cut to `box`."""
    image = Image.open(path).convert("L")
    if frame is not None and image.size != frame:
        image = image.resize(frame, Image.Resampling.NEAREST)
    if box is not None:
        left, top, width, height = box
        image = image.crop((left, top, left + width, top + height))
    if image.size != size:
        image = image.resize(size, Image.Resampling.BILINEAR)
    return np.asarray(image) > 127


def paste_back(
    scene: Path, raw: Path, masks_a: Path, masks_b: Path | None, segment: dict, dst: Path
) -> None:
    """Lay the AI's person back into the scene's own frames, one frame at a time (no sound).

    masks_a: the job's subject masks, one PNG per job frame. masks_b: SAM 3 "person" on the AI
    clip, one PNG per AI frame, or None. Outside the part every frame is written as decoded;
    inside it only lay_alpha's region changes, never outside the crop box. The video keeps the
    scene's size, frame count and rate. Add the sound with
    media.audio.add_original_soundtrack(..., fit=False).
    """
    clip = ai_clip(raw, segment)
    crop = segment["crop"]
    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    first, stop = segment["start_frame"], segment["end_frame"]
    frame_map = segment["frame_map"]
    made_from = job_frames(frame_map, first, stop)
    a_files = sorted(Path(masks_a).glob("mask_*.png"))
    b_files = sorted(Path(masks_b).glob("mask_*.png")) if masks_b else []
    if len(a_files) < len(frame_map):
        raise SceneError(
            f"The job has {len(a_files)} masks for the part's {len(frame_map)} frames. "
            "Prepare a new job."
        )
    left, top, width, height = clip.box
    with av.open(str(scene)) as container, av.open(str(raw)) as ai_container:
        stream = _video_stream(container, scene)
        ai_frames = ai_container.decode(_video_stream(ai_container, raw))
        ai_index, ai_frame = -1, None
        rate = Fraction(segment["fps"])
        with VideoWriter(dst, stream.width, stream.height, fps=rate, crf=CRF) as writer:
            for index, frame in enumerate(container.decode(stream)):
                picture = frame.to_ndarray(format="rgb24")
                if first <= index < stop:
                    k = made_from[index - first]
                    wanted = min(round(k * clip.rate / FPS), clip.frames - 1)
                    while ai_index < wanted:
                        following = next(ai_frames, None)
                        if following is None:
                            break
                        ai_frame, ai_index = following, ai_index + 1
                    a = _mask(a_files[k], (w, h))
                    b = None
                    if b_files:
                        b_file = b_files[min(ai_index, len(b_files) - 1)]
                        b = _mask(b_file, (w, h), frame=(clip.width, clip.height), box=clip.box)
                    alpha = lay_alpha(a, b) if a.any() else None
                    if alpha is not None and ai_frame is not None:
                        art = ai_frame.to_image().crop((left, top, left + width, top + height))
                        art = np.asarray(art.resize((w, h), Image.Resampling.LANCZOS))
                        part = picture[y : y + h, x : x + w].astype(np.float32)
                        mixed = part + alpha[:, :, None] * (art.astype(np.float32) - part)
                        picture = picture.copy()
                        picture[y : y + h, x : x + w] = np.rint(mixed).astype(np.uint8)
                writer.write(picture)
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/media/scene.py tests/test_scene.py
git commit -m "feat: lay the AI's person back into the scene's own frames"
````

---

### Task 7: Checkpoint: paste-back on the real Harry job (controller; free, no network)

No code and no commit. Before any wiring, look at the layer-back on real footage. The finished
WaveSpeed job `runs/ea899aff1cc246d8889137286c6be8b9` already has everything the paste-back
reads: `upload.mp4` (640x360, 24 fps, 115 frames, with sound) stands in for the scene,
`sam/clean-masks/` is mask A (96 frames), `draft-raw.mp4` (854x480, 96 frames) is the AI
clip. Its `original.mp4` is the first 96 frames of `upload.mp4` at 24 fps, so the part is
frames 0 to 95 with a one-to-one frame map. There is no B (that SAM 3 run would cost ~$0.002),
so this shows the honest worst case: A alone, grown 1.5 % and feathered. Nothing is written in
the job folder, nothing is sent anywhere, nothing is charged.

- [ ] **Step 1: Run the paste-back** (from the repo root, on the branch with Tasks 1 to 6):

````bash
mkdir -p /tmp/charswap-paste-check
uv run python - <<'EOF'
from pathlib import Path

import numpy as np
from PIL import Image

from character_swap.media import scene
from character_swap.media.audio import add_original_soundtrack
from character_swap.media.video import iter_frames

job = Path("runs/ea899aff1cc246d8889137286c6be8b9")
out = Path("/tmp/charswap-paste-check")
x, y, w, h = scene.find_bars(job / "original.mp4")
segment = {
    "start": 0.0, "end": 4.0, "start_frame": 0, "end_frame": 96, "seconds": 4.0,
    "padded_seconds": 4, "fps": "24", "width": 640, "height": 360,
    "crop": {"x": x, "y": y, "w": w, "h": h}, "cuts": [], "frame_map": list(range(96)),
}
scene.paste_back(
    job / "upload.mp4", job / "draft-raw.mp4", job / "sam" / "clean-masks", None, segment,
    out / "silent.mp4",
)
add_original_soundtrack(out / "silent.mp4", job / "upload.mp4", out / "harry-paste.mp4", fit=False)
pasted = list(iter_frames(out / "harry-paste.mp4"))
original = list(iter_frames(job / "upload.mp4"))
mask = list(iter_frames(job / "mask.mp4"))
ai = list(iter_frames(job / "draft-raw.mp4"))
for n in (10, 48, 90, 110):
    k = min(n, 95)
    ai_frame = np.asarray(Image.fromarray(ai[k]).resize((640, 360)))
    row = np.hstack([original[n], mask[k], pasted[n], ai_frame])
    Image.fromarray(row).save(out / f"compare-{n:03d}.png")
print(len(pasted), "frames; crop", (x, y, w, h))
EOF
````

Expected output: `115 frames; crop (0, 0, 640, 360)`, in a few seconds.

- [ ] **Step 2: Look at the output** (open the PNGs with the Read tool; play the mp4 in a
browser or QuickTime). Each `compare-NNN.png` is, left to right: the original, mask A, the
paste-back, the AI draft. The frame index is the decode order (PyAV), which is what the
paste-back uses; don't compare frames picked with ffmpeg's `select`, it can pick another one.
  - `compare-010/048/090.png`: the new person sits exactly where Harry was; around him the
    room is the original's (compare the shelves and the lamp with the leftmost image); the
    region's edge has no hard seam.
  - Where the AI's pose differs from Harry's (an arm raised elsewhere), the part of the AI
    person outside A plus its 1.5 % growth is cut off, and where Harry's arm was, the AI's
    room shows. Expected here, because this check has no B; in the real flow B (SAM 3 on
    the result) adds the AI person's own outline near A. Note how much is cut: it tells
    whether the 4 % `near(A)` is wide enough.
  - `compare-110.png`: frame 110 is after the part, so the paste-back is the original frame.
  - `/tmp/charswap-paste-check/harry-paste.mp4`: 4.8 s, 115 frames, with the original
    sound; the new person for 4.0 s, then Harry for the last 0.8 s (the rest of a scene is
    kept as it is).

- [ ] **Step 3: Decide.** If the edge or the placement looks wrong, fix `lay_alpha` or
`paste_back` (Tasks 5 and 6) before going on, each fix its own small commit with a test.
Otherwise go on. Delete `/tmp/charswap-paste-check` when done.

---

### Task 8: Stage hooks: SAM's `on_created` and `NoMasks`; a refused description; prep prices without it

Three small, independent changes the wiring needs:
- `stages.masks.segment(..., on_created=None)` passes `on_created` to `replicate.run` (the
  lay-back logs its SAM spend when the prediction is created); the "no mask_*.png" case raises
  `NoMasks(StageError)`, so the lay-back can tell "SAM found nobody" from a damaged zip.
- `stages.describe` (S11): when the prediction ended `failed` or `canceled` (a refusal such as
  "User Input Error"), the error names `resume --setting`; a "creating" or damaged record keeps
  today's message. `use_setting` is the free path: no call, and `scene.jpg` is still saved (the
  app's thumbnail).
- `costs.estimate_prepare(mode, seconds, skip=())` lets prepare and resume leave out `describe`.

**Files:**
- Modify: `src/character_swap/stages/masks.py`
- Modify: `src/character_swap/stages/describe.py`
- Modify: `src/character_swap/costs.py`
- Modify: `tests/fakes.py` (`RefusingReplicate`)
- Test: `tests/test_vision_stages.py`, `tests/test_text_stages.py`, `tests/test_costs.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/fakes.py`:

````python
from character_swap.providers.errors import OutputExpired
````

with:

````python
from character_swap.providers.errors import OutputExpired, ProviderError
````

**Append** to `tests/fakes.py`:

````python
class RefusingReplicate(FakeReplicate):
    """FakeReplicate whose `refuse` model always ends "failed", like a refusal.

    The first run saves the failed record (that run is counted); later runs read it and fail
    again without a new run, as the real client does.
    """

    def __init__(self, *args, refuse: str, reason: str = "User Input Error", **kwargs):
        super().__init__(*args, **kwargs)
        self.refuse, self.reason = refuse, reason

    def run(self, *, input, record: Path, version=None, model=None, on_created=None):
        if (version or model) != self.refuse:
            return super().run(
                input=input, record=record, version=version, model=model, on_created=on_created
            )
        if not record.exists():
            self.runs.append({"key": self.refuse, "input": input})
            failed = {"id": "refused-1", "status": "failed", "error": self.reason}
            record.write_text(json.dumps(failed))
        raise ProviderError(f"Prediction refused-1 failed: {self.reason}")
````

**Replace** in `tests/test_vision_stages.py`:

````python
from character_swap.stages.masks import SAM_VERSION, segment
````

with:

````python
from character_swap.stages.masks import SAM_VERSION, NoMasks, segment
````

**Append** to `tests/test_vision_stages.py`:

````python
def test_segment_reports_a_new_paid_run(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("mask_00000.png"))
    created = []
    segment(video, tmp_path / "sam", "person", fake, on_created=created.append)
    segment(video, tmp_path / "sam", "person", fake, on_created=created.append)  # resumed
    assert created == [{"id": "pred-1", "status": "starting"}]


def test_a_zip_without_masks_is_no_masks(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("a.txt"))
    with pytest.raises(NoMasks, match="no mask_"):
        segment(video, tmp_path / "sam", "person", fake)
    assert issubclass(NoMasks, StageError)


def test_a_damaged_zip_is_not_no_masks(tmp_path):
    video, fake = sam_fake(tmp_path, b"not a zip")
    with pytest.raises(StageError, match="not a valid zip") as info:
        segment(video, tmp_path / "sam", "person", fake)
    assert not isinstance(info.value, NoMasks)
````

**Replace** in `tests/test_text_stages.py`:

````python
from character_swap.stages.describe import DESCRIBE_MODEL, describe_scene
from character_swap.stages.transcribe import WHISPER_VERSION, transcribe
from fakes import FakeReplicate
````

with:

````python
from character_swap.providers.errors import ProviderError
from character_swap.stages import StageError
from character_swap.stages.describe import DESCRIBE_MODEL, describe_scene, use_setting
from character_swap.stages.transcribe import WHISPER_VERSION, transcribe
from fakes import FakeReplicate, RefusingReplicate
````

**Append** to `tests/test_text_stages.py`:

````python
def test_a_refused_description_names_the_free_way_on(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = RefusingReplicate(refuse=DESCRIBE_MODEL)
    with pytest.raises(StageError, match=r"it said: User Input Error\)") as info:
        describe_scene(job, fake)
    message = str(info.value)
    assert f'charswap resume {job.id} --setting "a dim room with ..."' in message
    assert "Deleting describe-prediction.json and resuming asks it again" in message
    with pytest.raises(StageError, match="--setting"):  # a resume says it again, buying nothing
        describe_scene(job, fake)
    assert len(fake.runs) == 1


def test_other_description_errors_keep_their_own_message(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    job.path("describe-prediction.json").write_text('{"status": "creating"}')

    class Unconfirmed(FakeReplicate):
        def run(self, **kwargs):
            raise ProviderError("The request was sent but not confirmed")

    with pytest.raises(ProviderError, match="not confirmed"):
        describe_scene(job, Unconfirmed())


def test_a_setting_you_wrote_needs_no_call(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    assert use_setting(job, "A dim room.") == "A dim room."
    assert job.state["scene"] == "A dim room." and job.path("scene.jpg").exists()
````

**Append** to `tests/test_costs.py`:

````python
def test_prepare_can_leave_out_the_description():
    full = estimate_prepare("depth_mesh", 30)
    mine = estimate_prepare("depth_mesh", 30, skip=("describe",))
    assert "Scene description" not in [line.item for line in mine.lines]
    assert mine.total == pytest.approx(full.total - 0.001)
    assert estimate_prepare("depth_mesh", 30, skip=()) == full
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_vision_stages.py tests/test_text_stages.py tests/test_costs.py -q --no-cov`
Expected: FAIL at collection (`cannot import name 'NoMasks'` / `'use_setting'`).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/stages/masks.py`:

````python
SAM_VERSION = "408f82fc20c300aac5d61d2e34ddb34cd0181e810e9f609d250aed14d5f81269"


def segment(video: Path, out_dir: Path, subject: str, replicate) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = replicate.upload(video)
    record = out_dir / "sam-prediction.json"
    output = replicate.run(
        version=SAM_VERSION,
        input={"video": url, "prompt": subject, "mask_only": True, "return_zip": True},
        record=record,
    )
````

with:

````python
SAM_VERSION = "408f82fc20c300aac5d61d2e34ddb34cd0181e810e9f609d250aed14d5f81269"


class NoMasks(StageError):
    """SAM 3 found nothing to cut out: its zip held no mask_*.png."""


def segment(video: Path, out_dir: Path, subject: str, replicate, on_created=None) -> Path:
    """One raw PNG mask per frame. `on_created` runs once when a NEW paid run is created."""
    out_dir.mkdir(parents=True, exist_ok=True)
    url = replicate.upload(video)
    record = out_dir / "sam-prediction.json"
    output = replicate.run(
        version=SAM_VERSION,
        input={"video": url, "prompt": subject, "mask_only": True, "return_zip": True},
        record=record,
        on_created=on_created,
    )
````

**Replace** in `src/character_swap/stages/masks.py`:

````python
    if not written:
        raise StageError(
````

with:

````python
    if not written:
        raise NoMasks(
````

**Replace** in `src/character_swap/stages/describe.py`:

````python
from __future__ import annotations

from character_swap.jobs import Job
from character_swap.media.video import middle_frame
from character_swap.stages import bad_output
````

with:

````python
from __future__ import annotations

import json
from pathlib import Path

from character_swap.jobs import Job
from character_swap.media.video import middle_frame
from character_swap.providers.errors import ProviderError
from character_swap.stages import StageError, bad_output
````

**Replace** in `src/character_swap/stages/describe.py`:

````python
    record = job.path("describe-prediction.json")
    output = replicate.run(
        model=DESCRIBE_MODEL,
        input={"prompt": PROMPT, "image_input": [url], "max_completion_tokens": 200},
        record=record,
    )
````

with:

````python
    record = job.path("describe-prediction.json")
    try:
        output = replicate.run(
            model=DESCRIBE_MODEL,
            input={"prompt": PROMPT, "image_input": [url], "max_completion_tokens": 200},
            record=record,
        )
    except ProviderError as exc:
        said = _refusal(record)
        if said is None:
            raise  # unconfirmed, damaged, still running: the client's own message says what to do
        raise StageError(
            f"GPT-4o mini would not describe this setting (it said: {said}). Describe it "
            f'yourself and continue for free: charswap resume {job.id} --setting "a dim room '
            f'with ...". (Deleting {record.name} and resuming asks it again, about $0.001.)'
        ) from exc
````

**Append** to `src/character_swap/stages/describe.py`:

````python
def _refusal(record: Path) -> str | None:
    """What a failed or canceled description said; None for any other saved record."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in ("failed", "canceled"):
        return None
    return str(data.get("error") or "no reason given")


def use_setting(job: Job, text: str) -> str:
    """The setting you wrote (--setting): no call, nothing paid. scene.jpg is still saved."""
    middle_frame(job.path("original.mp4")).save(job.path("scene.jpg"), quality=90)
    job.update(scene=text)
    return text
````

**Replace** in `src/character_swap/costs.py`:

````python
def estimate_prepare(mode: str, seconds: float) -> Estimate:
    return Estimate(lines=tuple(cost_line(key, seconds) for key in PREP_STEPS[mode]))
````

with:

````python
def estimate_prepare(mode: str, seconds: float, skip: tuple[str, ...] = ()) -> Estimate:
    """The mode's paid prep steps, leaving out the stages in `skip` (e.g. "describe")."""
    stages = [stage for stage in MODES[mode].paid_stages if stage not in skip]
    return Estimate(lines=tuple(cost_line(STAGE_PRICE[stage], seconds) for stage in stages))
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_vision_stages.py tests/test_text_stages.py tests/test_costs.py tests/test_pipeline.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/stages/masks.py src/character_swap/stages/describe.py src/character_swap/costs.py tests/fakes.py tests/test_vision_stages.py tests/test_text_stages.py tests/test_costs.py
git commit -m "feat: SAM reports its paid run, NoMasks, a refused description names --setting"
````

---

### Task 9: Scene jobs: the job folder and the prepare stages

S2, S5, S6, S11, S13 on the prepare side:
- `Job.create(..., scene=None, segment=None, setting="")`: with `scene`, the whole scene is
  copied to `full-scene.mp4` and `source` (the cut part) to `upload.mp4` as today; `segment`
  is saved (the key `scene` is already the setting text); `setting` is saved cleaned
  (`clean_setting`: one line, 1 to 300 characters).
- `pipeline.is_scene_job(job)`; every new branch checks it, so single-shot jobs run exactly
  today's code.
- Scene jobs cut out `--subject` with SAM 3 on `original.mp4` (colors), not on the depth video,
  and save `mask_source="rgb"`.
- The one-person check runs at the end of `composite` (so `mask.mp4` exists to look at): more
  than 10 % crowded frames fails `composite`, so a resume re-checks the same saved masks for
  free and stops again, before face mesh and review.
- With `state["setting"]` the describe stage makes no call, saves `scene = setting` and records
  `source: "you"`; `remaining_paid_stages` leaves `describe` out (so resume's question is right).
- `_check_soundtrack_fits` also refuses a scene whose own sound and picture differ by more than
  0.25 s (the same free check prepare runs, Task 11).

**Files:**
- Modify: `src/character_swap/jobs.py`
- Modify: `src/character_swap/pipeline.py`
- Create: `tests/scene_jobs.py` (a synthetic scene job and fake prep services, shared by the
  pipeline and CLI tests)
- Test: `tests/test_scene_pipeline.py`

- [ ] **Step 1: Write the failing tests**

**Create** `tests/scene_jobs.py`:

````python
"""Scene swap jobs for tests: a synthetic three-shot scene and fake prep services. No network."""

import io
import zipfile

import numpy as np
from PIL import Image

from character_swap.jobs import Job
from character_swap.media import scene
from character_swap.pipeline import Services
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION
from fakes import FakeReplicate
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 160, 30)
FRAMES = 96  # the default part: 95 scene frames (3.96 s) -> 96 job frames, 4 s for the AI
ONE = [(16, 8, 48, 40)]  # mask boxes (x0, y0, x1, y1) on the 64 x 48 job frames
TWO = [(4, 8, 28, 40), (36, 8, 60, 40)]  # two people of the same size


def fake_pitch(vocals, dst, **kwargs):
    """Stands in for Rubber Band (monkeypatch character_swap.pipeline.pitch_vocals)."""
    dst.write_bytes(vocals.read_bytes())
    return dst


def masks_zip(boxes, count=FRAMES, size=(64, 48)):
    """SAM 3's zip: `count` masks of `size`, each with `boxes` white."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(count):
            mask = np.zeros((size[1], size[0]), dtype=np.uint8)
            for x0, y0, x1, y1 in boxes:
                mask[y0:y1, x0:x1] = 255
            png = io.BytesIO()
            Image.fromarray(mask).save(png, "PNG")
            archive.writestr(f"masks/mask_{index:05d}.png", png.getvalue())
    return buffer.getvalue()


def prep_services(tmp_path, boxes=ONE, cls=FakeReplicate, **kwargs):
    """Fake Replicate answers for every paid prep step of a 4 s (96-frame) job."""
    stems = {
        name: write_wav(tmp_path / f"{name}.wav", tone(4, freq))
        for name, freq in (("v", 300), ("d", 80), ("b", 60), ("o", 500))
    }
    depth = write_video(tmp_path / "depth.mp4", [solid((100, 100, 100))] * FRAMES)
    fake = cls(
        outputs={
            DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d", "bass": "u/b", "other": "u/o"},
            WHISPER_VERSION: {"transcription": "hello"},
            DESCRIBE_MODEL: ["A studio."],
            f"{DEPTH_MODEL}-latest": "u/depth",
            SAM_VERSION: "u/zip",
        },
        files={
            **{f"u/{n}": path for n, path in stems.items()},
            "u/depth": depth,
            "u/zip": masks_zip(boxes),
        },
        **kwargs,
    )
    return Services(replicate=fake, face_detector=lambda: None, rubberband="unused")


def make_scene_job(runs_dir, tmp_path, *, mode="depth", setting="", face_frames=95):
    """A scene of three shots (64 x 48, 23.976 fps); the middle one is the part to swap.

    With the default 95 face frames the part is 1.00 s to 4.96 s: 96 job frames, 4 s.
    """
    total = 24 + face_frames + 24
    clip = write_shots(
        tmp_path / "scene.mp4",
        [(RED, 24), (BLUE, face_frames), (GREEN, 24)],
        audio_seconds=float(total / SCENE_RATE),
    )
    plan = scene.plan_part(clip, 1.0, float((24 + face_frames) / SCENE_RATE))
    crop = plan.segment["crop"]
    part = tmp_path / "part.mp4"
    frame_map = scene.cut_segment(
        clip,
        plan.segment["start"],
        plan.segment["end"],
        (crop["x"], crop["y"], crop["w"], crop["h"]),
        part,
    )
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (64, 64), (200, 150, 120)).save(photo, "JPEG")
    return Job.create(
        runs_dir,
        part,
        generator="seedance",
        mode=mode,
        character=photo,
        provider="wavespeed",
        scene=clip,
        segment={**plan.segment, "frame_map": frame_map},
        setting=setting,
    )
````

**Create** `tests/test_scene_pipeline.py`:

````python
"""Scene jobs through the pipeline: prepare (Task 9) and generate (Task 10)."""

import json

import pytest

from character_swap import pipeline
from character_swap.jobs import Job, JobError, clean_setting
from character_swap.media.scene import SceneError
from character_swap.pipeline import is_scene_job, prepare, remaining_paid_stages
from character_swap.stages import StageError
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from fakes import RefusingReplicate
from helpers import solid, write_shots, write_video
from scene_jobs import BLUE, RED, TWO, fake_pitch, make_scene_job, prep_services

QUIET = {"status": lambda _m: None}


@pytest.fixture(autouse=True)
def no_rubberband(monkeypatch):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)


def sam_inputs(services):
    return [run["input"]["video"] for run in services.replicate.runs if run["key"] == SAM_VERSION]


# ---- the job folder ----------------------------------------------------------------
def test_a_scene_job_keeps_the_scene_the_part_and_the_segment(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, setting="  A dim\n room.  ")
    state = job.state
    assert job.path("full-scene.mp4").read_bytes() == (tmp_path / "scene.mp4").read_bytes()
    assert job.path("upload.mp4").read_bytes() == (tmp_path / "part.mp4").read_bytes()
    assert state["segment"]["start_frame"] == 24 and len(state["segment"]["frame_map"]) == 96
    assert state["setting"] == "A dim room." and "scene" not in state
    assert is_scene_job(job)


def test_a_single_shot_job_is_not_a_scene_job(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth")
    assert not is_scene_job(job) and "segment" not in job.state and "setting" not in job.state
    assert not job.path("full-scene.mp4").exists()


def test_a_scene_needs_its_segment(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 24)
    with pytest.raises(JobError, match="both the scene and its segment"):
        Job.create(runs_dir, clip, generator="seedance", mode="depth", scene=clip)
    with pytest.raises(JobError, match="Video not found"):
        Job.create(
            runs_dir,
            clip,
            generator="seedance",
            mode="depth",
            scene=tmp_path / "no.mp4",
            segment={},
        )
    assert not any(runs_dir.iterdir())


@pytest.mark.parametrize("text", ["", "   ", "x" * 301])
def test_a_setting_must_be_1_to_300_characters(text):
    with pytest.raises(JobError, match="Describe the setting in 1 to 300 characters"):
        clean_setting(text)


# ---- prepare -------------------------------------------------------------------------
def test_a_scene_job_cuts_out_its_subject_on_the_colors(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    services = prep_services(tmp_path)
    prepare(job, services, **QUIET)
    assert sam_inputs(services) == ["https://fake.replicate/original.mp4"]
    assert job.state["mask_source"] == "rgb" and job.path("prepared.mp4").exists()


def test_a_single_shot_job_still_cuts_out_on_the_depth_video(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth")
    services = prep_services(tmp_path, boxes=TWO)  # two people: no check on a single shot
    prepare(job, services, **QUIET)
    assert sam_inputs(services) == ["https://fake.replicate/depth.mp4"]
    assert "mask_source" not in job.state and job.stage_done("embed")


def test_two_people_stop_a_scene_job_before_the_face_mesh(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, mode="depth_mesh")
    services = prep_services(tmp_path, boxes=TWO)
    with pytest.raises(StageError, match="more than one person in 96 of 96 frames") as info:
        prepare(job, services, **QUIET)
    message = str(info.value)
    assert str(job.path("mask.mp4")) in message and job.path("mask.mp4").exists()
    assert '--subject "man with white face paint"' in message and "about $0.06" in message
    assert job.state["stages"]["composite"]["status"] == "failed"
    assert "face_mesh" not in job.state["stages"]
    runs = len(services.replicate.runs)
    with pytest.raises(StageError, match="more than one person"):  # a resume checks again
        prepare(job, services, **QUIET)
    assert len(services.replicate.runs) == runs  # the saved masks: nothing bought again


def test_a_setting_you_wrote_replaces_the_paid_description(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, setting="A dim room.")
    assert "describe" not in remaining_paid_stages(job)
    services = prep_services(tmp_path)
    prepare(job, services, **QUIET)
    assert DESCRIBE_MODEL not in [run["key"] for run in services.replicate.runs]
    assert job.state["scene"] == "A dim room." and job.path("scene.jpg").exists()
    assert job.state["stages"]["describe"]["source"] == "you"


def test_after_a_refused_description_a_setting_finishes_for_free(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    services = prep_services(tmp_path, cls=RefusingReplicate, refuse=DESCRIBE_MODEL)
    with pytest.raises(StageError, match=f"charswap resume {job.id} --setting"):
        prepare(job, services, **QUIET)
    paid = [run["key"] for run in services.replicate.runs]
    job.update(setting="A dim room.")  # what `charswap resume <id> --setting` saves
    prepare(job, services, **QUIET)
    assert [run["key"] for run in services.replicate.runs][: len(paid)] == paid
    assert DESCRIBE_MODEL not in [run["key"] for run in services.replicate.runs[len(paid) :]]
    assert job.state["stages"]["describe"]["source"] == "you" and job.stage_done("embed")


def test_a_scene_whose_sound_no_longer_fits_is_refused_before_paying(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    job.update(video={"seconds": 4.0})
    write_shots(job.path("full-scene.mp4"), [(BLUE, 143)], audio_seconds=3.0)
    with pytest.raises(SceneError, match=r"sound lasts 3\.0 s but its picture 6\.0 s"):
        pipeline._check_soundtrack_fits(job)


def test_the_segment_is_saved_as_json(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    saved = json.loads(job.path("state.json").read_text())["segment"]
    assert saved["fps"] == "24000/1001" and saved["crop"] == {"x": 0, "y": 0, "w": 64, "h": 48}
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene_pipeline.py -q --no-cov`
Expected: FAIL at collection (`cannot import name 'clean_setting'`).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/jobs.py`:

````python
SEEDANCE_PROVIDERS: tuple[str, ...] = ("wavespeed", "piapi")  # the first is the default
````

with:

````python
SEEDANCE_PROVIDERS: tuple[str, ...] = ("wavespeed", "piapi")  # the first is the default
MAX_SETTING = 300  # characters of a setting you write (--setting); the prompt keeps 300
````

**Replace** in `src/character_swap/jobs.py`:

````python
def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
````

with:

````python
def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_setting(text: str) -> str:
    """A setting you wrote (--setting), on one line: 1 to MAX_SETTING characters."""
    cleaned = " ".join(text.split())
    if not 1 <= len(cleaned) <= MAX_SETTING:
        raise JobError(
            f"Describe the setting in 1 to {MAX_SETTING} characters. Nothing was charged."
        )
    return cleaned
````

**Replace** in `src/character_swap/jobs.py`:

````python
        provider: str = "",  # Seedance: "wavespeed" | "piapi"; "" = decided later (old jobs)
    ) -> Job:
````

with:

````python
        provider: str = "",  # Seedance: "wavespeed" | "piapi"; "" = decided later (old jobs)
        scene: Path | None = None,  # a scene swap: the whole scene; `source` is its cut part
        segment: dict[str, Any] | None = None,  # media.scene.Plan.segment + "frame_map"
        setting: str = "",  # a setting you wrote: skips the paid description
    ) -> Job:
````

**Replace** in `src/character_swap/jobs.py`:

````python
        if provider and provider not in SEEDANCE_PROVIDERS:
            raise JobError(
                f"Unknown provider {provider!r}. Use one of: {', '.join(SEEDANCE_PROVIDERS)}."
            )

        job_id = uuid.uuid4().hex
        root = Path(runs_dir) / job_id
        root.mkdir(parents=True)
        try:
            shutil.copy2(source, root / "upload.mp4")
````

with:

````python
        if provider and provider not in SEEDANCE_PROVIDERS:
            raise JobError(
                f"Unknown provider {provider!r}. Use one of: {', '.join(SEEDANCE_PROVIDERS)}."
            )
        if (scene is None) != (segment is None):
            raise JobError("A scene swap needs both the scene and its segment.")
        if scene is not None and not Path(scene).is_file():
            raise JobError(f"Video not found: {scene}")
        if setting:
            setting = clean_setting(setting)

        job_id = uuid.uuid4().hex
        root = Path(runs_dir) / job_id
        root.mkdir(parents=True)
        try:
            shutil.copy2(source, root / "upload.mp4")
            if scene is not None:
                shutil.copy2(scene, root / "full-scene.mp4")
````

**Replace** in `src/character_swap/jobs.py`:

````python
            if provider:
                state["provider"] = provider
````

with:

````python
            if provider:
                state["provider"] = provider
            if segment is not None:
                state["segment"] = segment  # "scene" is already the setting's text
            if setting:
                state["setting"] = setting
````

**Replace** in `src/character_swap/pipeline.py`:

````python
from character_swap.costs import record_spend
````

with:

````python
from character_swap.costs import estimate_prepare, record_spend, usd_text
````

**Replace** in `src/character_swap/pipeline.py`:

````python
from character_swap.media.composite import clean_border_fragments, composite_depth_subject
````

with:

````python
from character_swap.media.composite import clean_border_fragments, composite_depth_subject
from character_swap.media.scene import CROWDED, check_sound, crowded_frames
````

**Replace** in `src/character_swap/pipeline.py`:

````python
from character_swap.stages.describe import describe_scene
````

with:

````python
from character_swap.stages.describe import describe_scene, use_setting
````

**Replace** in `src/character_swap/pipeline.py`:

````python
def remaining_paid_stages(job: Job) -> list[str]:
    """Paid stages this job's mode needs that are not finished yet."""
    return [name for name in PAID_STAGES[job.mode] if not job.stage_done(name)]
````

with:

````python
SCENE = "full-scene.mp4"  # a scene swap's whole scene; upload.mp4 is the part the AI gets


def is_scene_job(job: Job, state: dict[str, Any] | None = None) -> bool:
    """True for a scene swap (state.json has a "segment"); single-shot jobs run as before."""
    state = job.state if state is None else state
    return isinstance(state.get("segment"), dict)


def prep_skip(job: Job) -> tuple[str, ...]:
    """Paid prep stages this job leaves out: the description, when you wrote the setting."""
    return ("describe",) if job.state.get("setting") else ()


def remaining_paid_stages(job: Job) -> list[str]:
    """Paid stages this job's mode needs that are not finished yet."""
    skip = prep_skip(job)
    return [name for name in PAID_STAGES[job.mode] if name not in skip and not job.stage_done(name)]
````

**Replace** in `src/character_swap/pipeline.py`:

````python
def _stage(job: Job, name: str, label: str, work: Callable[[], object], status: Status) -> None:
    if job.stage_done(name):
        _notify(status, f"{label} · already done")
        return
    _notify(status, label)
    clear_review(job)  # any new work invalidates a previous approval
    with _tracked(job, name), _wrap(label):
        work()
````

with:

````python
def _stage(
    job: Job,
    name: str,
    label: str,
    work: Callable[[], object],
    status: Status,
    **info: object,
) -> None:
    if job.stage_done(name):
        _notify(status, f"{label} · already done")
        return
    _notify(status, label)
    clear_review(job)  # any new work invalidates a previous approval
    with _tracked(job, name, **info), _wrap(label):
        work()
````

**Replace** in `src/character_swap/pipeline.py`:

````python
    _stage(job, "describe", "Describing the setting", lambda: describe_scene(job, r), status)

    if MODES[job.mode].uses_depth:
        _stage(job, "depth", "Generating depth video", lambda: _depth(job, services), status)
        _stage(
            job,
            "masks",
            "Cutting out the subject (SAM 3)",
            lambda: _masks(job, r, "depth.mp4"),
            status,
        )
        _stage(
            job,
            "composite",
            "Compositing depth subject over background",
            lambda: _composite(job),
            status,
        )
````

with:

````python
    setting = job.state.get("setting")
    if setting:
        _stage(
            job,
            "describe",
            "Using the setting you wrote",
            lambda: use_setting(job, setting),
            status,
            source="you",
        )
    else:
        _stage(job, "describe", "Describing the setting", lambda: describe_scene(job, r), status)

    if MODES[job.mode].uses_depth:
        _stage(job, "depth", "Generating depth video", lambda: _depth(job, services), status)
        _stage(
            job,
            "masks",
            "Cutting out the subject (SAM 3)",
            lambda: _subject_masks(job, r),
            status,
        )
        _stage(
            job,
            "composite",
            "Compositing depth subject over background",
            lambda: _composite_checked(job),
            status,
        )
````

**Replace** in `src/character_swap/pipeline.py`:

````python
def _composite(job: Job, folder: str = "sam") -> None:
    composite_depth_subject(
        job.path("original.mp4"), job.path("depth.mp4"), job.path(folder) / "clean-masks", job.root
    )
````

with:

````python
def _subject_masks(job: Job, replicate) -> None:
    if is_scene_job(job):  # S5: "man with white face paint" can't be seen in a depth map
        _masks(job, replicate, "original.mp4")
        job.update(mask_source="rgb")
    else:
        _masks(job, replicate, "depth.mp4")


def _composite(job: Job, folder: str = "sam") -> None:
    composite_depth_subject(
        job.path("original.mp4"), job.path("depth.mp4"), job.path(folder) / "clean-masks", job.root
    )


def _composite_checked(job: Job) -> None:
    _composite(job)
    if is_scene_job(job):
        _check_one_person(job)


def _check_one_person(job: Job) -> None:
    """S6: one photo means one person to hide. Runs after mask.mp4 exists, so it can be watched."""
    crowded, total = crowded_frames(job.path("sam") / "clean-masks")
    if total and crowded > CROWDED * total:
        again = usd_text(estimate_prepare(job.mode, 30, skip=prep_skip(job)).total)
        raise StageError(
            f"SAM 3 found more than one person in {crowded} of {total} frames (watch "
            f"{job.path('mask.mp4')}: white is what was cut out). Start a new job with "
            '--subject naming the one to swap, for example --subject "man with white face '
            f'paint" (preparing again costs about {again}).'
        )
````

**Replace** in `src/character_swap/pipeline.py`:

````python
def _check_soundtrack_fits(job: Job) -> None:
    """Free pre-flight: refuse to pay for a video whose original sound can't be put back."""
    upload = job.path("upload.mp4")
````

with:

````python
def _check_soundtrack_fits(job: Job) -> None:
    """Free pre-flight: refuse to pay for a video whose original sound can't be put back."""
    if is_scene_job(job):  # the scene's own sound goes back on the scene's own picture
        check_sound(job.path(SCENE))
    upload = job.path("upload.mp4")
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene_pipeline.py tests/test_pipeline.py tests/test_jobs.py tests/test_generation.py -q --no-cov`
Expected: all PASS (the existing pipeline, job and generation tests unchanged).

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/jobs.py src/character_swap/pipeline.py tests/scene_jobs.py tests/test_scene_pipeline.py
git commit -m "feat: scene jobs: the scene and segment in the job, SAM on colors, one-person check"
````

---

### Task 10: Lay the result back inside `generate` (`stages/paste.py`), its price and its archive

S7, S8 and the money rules: for a scene job, once `{stage}-raw.mp4` exists, `generate` runs
`lay_back` instead of `add_original_soundtrack`, inside the same `_tracked(stage)`, writing
`{stage}.mp4` through `_write_atomically`. `lay_back` checks the AI clip first (free; a clip
that can't be laid back costs no SAM run), then SAM 3 `"person"` on the raw clip into
`{stage}-sam/` (its record is resumed, never bought again; the spend is logged once, as step
`{stage}-paste`, when the prediction is created; a new run first clears any masks left in that
folder by an older one), `NoMasks` or all-blank masks mean B = ∅,
then `clean_border_fragments`, `paste_back` and the original sound with `fit=False`. A failure
never deletes `{stage}-raw.mp4`, so running the command again pays nothing twice.

`generation_estimate` is the generator's estimate plus a `sam3` line for a scene job whose
`{stage}-sam` record is missing, or always on a remake (`replacing_stale`: that folder is
archived after the question). `archive_stale_draft` moves `{stage}-sam/` aside too, so a new
draft never reuses the old result's masks. `log_charge` still logs the generator's estimate
only, so SAM is never logged twice.

**Files:**
- Create: `src/character_swap/stages/paste.py`
- Modify: `src/character_swap/pipeline.py`
- Test: `tests/test_scene_pipeline.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_scene_pipeline.py`:

````python
import json

import pytest

from character_swap import pipeline
from character_swap.jobs import Job, JobError, clean_setting
from character_swap.media.scene import SceneError
from character_swap.pipeline import is_scene_job, prepare, remaining_paid_stages
from character_swap.stages import StageError
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from fakes import RefusingReplicate
from helpers import solid, write_shots, write_video
from scene_jobs import BLUE, RED, TWO, fake_pitch, make_scene_job, prep_services
````

with:

````python
import json

import av
import pytest
from PIL import Image

from character_swap import pipeline
from character_swap.costs import Estimate, Line
from character_swap.jobs import Job, JobError, clean_setting
from character_swap.media.scene import SceneError
from character_swap.media.video import count_frames, iter_frames
from character_swap.pipeline import (
    Services,
    archive_stale_draft,
    build_request,
    generate,
    generation_estimate,
    is_scene_job,
    needs_lay_back_charge,
    prepare,
    remaining_paid_stages,
)
from character_swap.review import decide, fingerprint
from character_swap.stages import StageError
from character_swap.stages import paste as paste_stage
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from fakes import FakeReplicate, RefusingReplicate
from helpers import solid, write_shots, write_video
from scene_jobs import (
    BLUE,
    FRAMES,
    GREEN,
    RED,
    TWO,
    fake_pitch,
    make_scene_job,
    masks_zip,
    prep_services,
)
````

**Append** to `tests/test_scene_pipeline.py`:

````python
# ---- generate: the lay-back ----------------------------------------------------------
YELLOW, MAGENTA = (220, 220, 40), (220, 40, 220)


class FakeSeedance:
    """Stands in for the WaveSpeed generator: one paid create per record, a canned video."""

    name, provider, model = "seedance", "wavespeed", "fake/seedance"
    finals, promotes = ("720p", "1080p"), False

    def __init__(self, video):
        self.video, self.creates = video, 0

    def estimate(self, request, stage):
        return Estimate(lines=(Line(f"Fake {stage}", 0.88),))

    def generate(self, request, stage, *, record, dest, on_created=None):
        if not record.exists():
            self.creates += 1
            record.write_text(json.dumps({"id": f"p-{stage}", "status": "succeeded"}))
            if on_created is not None:
                on_created({"id": f"p-{stage}", "status": "starting"})
        dest.write_bytes(self.video.read_bytes())
        return dest


def ai_video(tmp_path, frames=FRAMES):
    """The AI's draft at twice the job's size: a yellow room, the new person where A is."""
    picture = solid(YELLOW, (128, 96))
    picture[16:80, 32:96] = MAGENTA  # ONE's box (16, 8, 48, 40), doubled
    return write_video(tmp_path / f"ai-{frames}.mp4", [picture] * frames)


def gen_services(tmp_path, *, frames=FRAMES, b_boxes=((32, 16, 96, 80),), b_count=FRAMES):
    fake = FakeReplicate(
        outputs={SAM_VERSION: "u/b"},
        files={"u/b": masks_zip(list(b_boxes), count=b_count, size=(128, 96))},
    )
    generator = FakeSeedance(ai_video(tmp_path, frames))
    return Services(
        replicate=fake,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": generator},
    )


def approved_scene_job(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    prepare(job, prep_services(tmp_path), **QUIET)
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    return job


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def _near(frame, x, y, color):
    patch = frame[y - 2 : y + 2, x - 2 : x + 2].astype(int)
    return abs(patch - color).mean() < 16


def test_a_scene_draft_is_the_whole_scene_with_the_new_person(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path)
    result = generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert result == job.path("draft.mp4") and job.stage_done("draft")
    frames = list(iter_frames(result))
    assert len(frames) == count_frames(job.path("full-scene.mp4")) == 143
    assert audio_packets(result) == audio_packets(job.path("full-scene.mp4"))
    assert _near(frames[10], 32, 24, RED) and _near(frames[130], 32, 24, GREEN)  # other shots
    assert _near(frames[70], 32, 24, MAGENTA) and _near(frames[70], 4, 44, BLUE)
    (run,) = services.replicate.runs
    assert run["input"]["prompt"] == "person"
    assert run["input"]["video"] == "https://fake.replicate/draft-raw.mp4"
    assert [(c["step"], c["usd"]) for c in job.state["costs"]] == [
        ("draft", 0.88),
        ("draft-paste", 0.002),
    ]
    assert job.state["costs"][1]["task_id"] == "pred-1"


def test_nobody_on_the_result_uses_the_original_outline(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path, b_count=0)  # a zip with no mask_*.png
    said = []
    generate(job, services, "draft", runs_dir=runs_dir, status=said.append)
    assert paste_stage.NOBODY in said and job.stage_done("draft")
    assert _near(list(iter_frames(job.path("draft.mp4")))[70], 32, 24, MAGENTA)


def test_blank_masks_on_the_result_also_mean_nobody(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    said = []
    services = gen_services(tmp_path, b_boxes=())  # SAM 3 returns only empty masks
    generate(job, services, "draft", runs_dir=runs_dir, status=said.append)
    assert paste_stage.NOBODY in said and job.stage_done("draft")


def test_a_crash_while_laying_back_buys_nothing_again(runs_dir, tmp_path, monkeypatch):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path)
    real, calls = paste_stage.paste_back, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(paste_stage, "paste_back", flaky)
    with pytest.raises(StageError, match="disk full"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert job.path("draft-raw.mp4").exists() and not job.path("draft.mp4").exists()
    assert not job.path("draft.part.mp4").exists()
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    generator = services.generators["seedance:wavespeed"]
    assert generator.creates == 1 and len(services.replicate.runs) == 1  # nothing bought twice
    assert [c["step"] for c in job.state["costs"]] == ["draft", "draft-paste"]
    assert job.stage_done("draft")


def test_result_masks_that_do_not_match_name_the_record(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    with pytest.raises(StageError, match=r"10 masks for the AI clip's 96 frames\. Delete .*"):
        generate(job, gen_services(tmp_path, b_count=10), "draft", runs_dir=runs_dir, **QUIET)
    error = job.state["stages"]["draft"]["error"]
    assert str(job.path("draft-sam") / "sam-prediction.json") in error


def test_a_new_sam_run_never_mixes_in_old_masks(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    stale = job.path("draft-sam/raw-masks")
    stale.mkdir(parents=True)
    for index in range(96, 120):  # left over from an earlier result whose record was deleted
        (stale / f"mask_{index:05d}.png").write_bytes(b"old")
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    clean = list((job.path("draft-sam") / "clean-masks").glob("mask_*.png"))
    assert job.stage_done("draft") and len(clean) == FRAMES


def test_an_ai_clip_that_cannot_be_laid_back_buys_no_sam(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path, frames=48)  # 2.0 s for a 3.96 s part
    with pytest.raises(StageError, match="can't be laid back into the 4.0 s shot"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.replicate.runs == [] and job.path("draft-raw.mp4").exists()


def test_the_price_includes_the_lay_back_until_it_is_bought(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    generator = FakeSeedance(ai_video(tmp_path))
    request = build_request(job, stage="draft")
    items = [line.item for line in generation_estimate(job, generator, request, "draft").lines]
    assert items == ["Fake draft", "SAM 3 masks"] and needs_lay_back_charge(job, "draft")
    job.path("draft-sam").mkdir()
    job.path("draft-sam/sam-prediction.json").write_text('{"id": "s", "status": "succeeded"}')
    estimate = generation_estimate(job, generator, request, "draft")
    assert [line.item for line in estimate.lines] == ["Fake draft"]
    assert not needs_lay_back_charge(job, "draft") and needs_lay_back_charge(job, "final")
    remake = generation_estimate(job, generator, request, "draft", replacing_stale=True)
    assert remake.total == pytest.approx(0.882)  # the old masks are archived first


def test_a_new_draft_never_reuses_the_old_results_masks(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    moved = archive_stale_draft(job)
    assert not job.path("draft-sam").exists()
    (old,) = [path for path in moved if path.name.endswith("-sam")]
    assert old.name.startswith("draft-old-") and (old / "sam-prediction.json").exists()


def test_a_single_shot_job_never_lays_back_or_reads_a_scene(runs_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "lay_back", lambda *a, **k: pytest.fail("laid back"))
    monkeypatch.setattr(pipeline, "check_sound", lambda *a: pytest.fail("read a scene"))
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (64, 64), (200, 150, 120)).save(photo, "JPEG")
    job = Job.create(
        runs_dir, clip, generator="seedance", mode="depth", character=photo, provider="wavespeed"
    )
    prepare(job, prep_services(tmp_path), **QUIET)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    services = gen_services(tmp_path)
    generator = services.generators["seedance:wavespeed"]
    request = build_request(job, stage="draft")
    assert generation_estimate(job, generator, request, "draft") == generator.estimate(
        request, "draft"
    )
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert job.stage_done("draft") and services.replicate.runs == []
    assert [c["step"] for c in job.state["costs"]] == ["draft"]
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene_pipeline.py -q --no-cov`
Expected: FAIL at collection (`cannot import name 'paste'` / `'generation_estimate'`).

- [ ] **Step 3: Write the code**

**Create** `src/character_swap/stages/paste.py`:

````python
"""Scene swaps: lay the AI's person back into the full scene (SAM 3 on the result, then paste)."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from character_swap.costs import estimate_repair, record_spend
from character_swap.jobs import Job
from character_swap.media.audio import add_original_soundtrack
from character_swap.media.composite import clean_border_fragments
from character_swap.media.scene import SceneError, ai_clip, paste_back
from character_swap.stages import StageError
from character_swap.stages.masks import NoMasks, segment

SCENE = "full-scene.mp4"
NOBODY = "SAM 3 found nobody on the AI clip; only the original outline is used."


def _blank(folder: Path) -> bool:
    return not any(
        np.asarray(Image.open(path).convert("L")).max() > 127
        for path in sorted(folder.glob("mask_*.png"))
    )


def lay_back(
    job: Job,
    stage: str,
    replicate,
    dst: Path,
    *,
    runs_dir: Path,
    status: Callable[[str], None],
) -> Path:
    """{stage}-raw.mp4's new person laid into full-scene.mp4, with its own sound, at `dst`.

    Paid: one SAM 3 run on the AI clip (~$0.002), logged once as "{stage}-paste" when it is
    created. Its saved record is resumed, never bought again; the raw clip is never deleted.
    """
    raw = job.path(f"{stage}-raw.mp4")
    facts = job.state["segment"]
    try:
        clip = ai_clip(raw, facts)  # free: refuse before paying for SAM
    except SceneError as exc:
        raise StageError(str(exc)) from exc
    folder = job.path(f"{stage}-sam")
    if not (folder / "sam-prediction.json").exists():
        shutil.rmtree(folder, ignore_errors=True)  # a new SAM run: no masks left from an old one
    estimate = estimate_repair()  # one SAM 3 run

    def log(prediction: dict) -> None:
        record_spend(
            job,
            f"{stage}-paste",
            estimate,
            runs_dir,
            provider="replicate",
            model="sam3",
            task_id=str(prediction.get("id", "")),
        )

    masks_b: Path | None
    try:
        found = segment(raw, folder, "person", replicate, on_created=log)
    except NoMasks:
        masks_b = None
    else:
        masks_b = folder / "clean-masks"
        clean_border_fragments(found, masks_b)
        count = len(list(masks_b.glob("mask_*.png")))
        if count != clip.frames:
            raise StageError(
                f"SAM 3 gave {count} masks for the AI clip's {clip.frames} frames. Delete "
                f"{folder / 'sam-prediction.json'} and run the same command again (~$0.002). "
                "If it says this again after that, don't pay again: it needs a fix in the tool."
            )
        if _blank(masks_b):
            masks_b = None
    if masks_b is None:
        status(NOBODY)
    with tempfile.TemporaryDirectory() as scratch:
        silent = Path(scratch) / "pasted.mp4"
        try:
            paste_back(
                job.path(SCENE), raw, job.path("sam") / "clean-masks", masks_b, facts, silent
            )
        except SceneError as exc:
            raise StageError(str(exc)) from exc
        add_original_soundtrack(silent, job.path(SCENE), dst, fit=False)
    return Path(dst)
````

**Replace** in `src/character_swap/pipeline.py`:

````python
from character_swap.costs import estimate_prepare, record_spend, usd_text
````

with:

````python
from character_swap.costs import Estimate, cost_line, estimate_prepare, record_spend, usd_text
````

**Replace** in `src/character_swap/pipeline.py`:

````python
from character_swap.stages.masks import segment
````

with:

````python
from character_swap.stages.masks import segment
from character_swap.stages.paste import lay_back
````

**Replace** in `src/character_swap/pipeline.py`:

````python
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    moves = []
    for stage in GENERATION_STAGES:
        for name in generation_files(stage):
````

with:

````python
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    moves = []
    for stage in GENERATION_STAGES:
        for name in (*generation_files(stage), f"{stage}-sam"):  # a scene job's lay-back masks
````

**Replace** in `src/character_swap/pipeline.py`:

````python
def needs_new_charge(job: Job, stage: str) -> bool:
    """False when a saved provider request or a downloaded result exists (resume is free)."""
    return not already_paid(job, stage)
````

with:

````python
def needs_new_charge(job: Job, stage: str) -> bool:
    """False when a saved provider request or a downloaded result exists (resume is free)."""
    return not already_paid(job, stage)


def paste_record(stage: str) -> str:
    """A scene job's saved SAM 3 run on its AI result (the lay-back)."""
    return f"{stage}-sam/sam-prediction.json"


def needs_lay_back_charge(job: Job, stage: str) -> bool:
    """A scene job whose lay-back SAM 3 run (~$0.002) was never created."""
    return is_scene_job(job) and not job.path(paste_record(stage)).exists()


def generation_estimate(
    job: Job,
    generator: Generator,
    request: GenerationRequest,
    stage: str,
    *,
    replacing_stale: bool = False,
) -> Estimate:
    """What this step charges: the generator's price, plus the lay-back's SAM 3 run on a scene
    job while it is still to be bought (always on a remake: the old masks are archived)."""
    estimate = generator.estimate(request, stage)
    if is_scene_job(job) and (replacing_stale or needs_lay_back_charge(job, stage)):
        return Estimate(lines=(*estimate.lines, cost_line("sam3", 0)))
    return estimate
````

**Replace** in `src/character_swap/pipeline.py`:

````python
        if not raw.exists():  # downloaded already: never ask the provider again
            generator.generate(request, stage, record=record, dest=raw, on_created=log_charge)
        _notify(status, "Putting the original soundtrack back")
        # Wan keeps the exact call it always had; only whole-second jobs cut the sound.
        cut = {"seconds": request.seconds} if job.generator in WHOLE_SECONDS else {}
        _write_atomically(
            result,
            lambda part: add_original_soundtrack(raw, job.path("upload.mp4"), part, **cut),
        )
````

with:

````python
        if not raw.exists():  # downloaded already: never ask the provider again
            generator.generate(request, stage, record=record, dest=raw, on_created=log_charge)
        if is_scene_job(job):
            _notify(status, "Laying the new person back into the scene")
            _write_atomically(
                result,
                lambda part: lay_back(
                    job,
                    stage,
                    services.replicate,
                    part,
                    runs_dir=runs_dir,
                    status=lambda text: _notify(status, text),
                ),
            )
        else:
            _notify(status, "Putting the original soundtrack back")
            # Wan keeps the exact call it always had; only whole-second jobs cut the sound.
            cut = {"seconds": request.seconds} if job.generator in WHOLE_SECONDS else {}
            _write_atomically(
                result,
                lambda part: add_original_soundtrack(raw, job.path("upload.mp4"), part, **cut),
            )
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene_pipeline.py tests/test_generation.py tests/test_cli.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/stages/paste.py src/character_swap/pipeline.py tests/test_scene_pipeline.py
git commit -m "feat: generate lays a scene job's result back into the full scene"
````

---

### Task 11: `charswap prepare --from --to --setting`

S1, S2, S11, S12 and the Errors table at the command line. Order, all before any question:
the flag refusals (`--from` and `--to` together; WaveSpeed and depth_mesh or depth only;
`--setting` 1 to 300 characters, not for Wan), the photo check, then `plan_part` (its own
refusals: times, part length, the scene's sound), then the plain-English summary, then the
rights question, then the prep cost question (without "Scene description" when `--setting` is
given). Only after both answers is the part cut (free, a few seconds) and the job created from
it. A shot cut inside the part is a warning in the summary, not a stop.

**Files:**
- Modify: `src/character_swap/cli.py`
- Test: `tests/test_scene_cli.py`

- [ ] **Step 1: Write the failing tests**

**Create** `tests/test_scene_cli.py`:

````python
"""charswap commands on a scene swap (--from/--to). No paid call: fakes and monkeypatches."""

import pytest
from PIL import Image

from character_swap import cli
from character_swap.config import Settings
from character_swap.faces import PHOTO_CONSENT
from character_swap.jobs import Job
from character_swap.media.video import count_frames
from helpers import SCENE_RATE, write_shots

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 160, 30)


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(str(text))

    @property
    def text(self):
        return "\n".join(self.lines)


def run(argv, settings, **kw):
    out = Recorder()
    out.err = Recorder()
    code = cli.main(argv, settings=settings, out=out, err=out.err, **kw)
    return code, out


@pytest.fixture
def settings(tmp_path):
    return Settings.load(
        root=tmp_path, environ={"REPLICATE_API_TOKEN": "t", "WAVESPEED_API_KEY": "w"}
    )


@pytest.fixture
def scene_clip(tmp_path):
    """Three shots: police (24 frames), the face (95 frames: 1.00 s to 4.96 s), a masked man."""
    return write_shots(
        tmp_path / "scene.mp4",
        [(RED, 24), (BLUE, 95), (GREEN, 24)],
        audio_seconds=float(143 / SCENE_RATE),
    )


def _photo(path):
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(path, "JPEG")
    return path


def _prepare_ids(monkeypatch):
    ids = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: ids.append(job.id))
    return ids


def scene_args(clip, tmp_path, *extra, start="1.0", end="5.0"):
    return [
        "prepare",
        str(clip),
        "--from",
        start,
        "--to",
        end,
        "--subject",
        "man with white face paint",
        "--character",
        str(_photo(tmp_path / "me.jpg")),
        *extra,
    ]


def _no_job(settings):
    return not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


# ---- prepare -------------------------------------------------------------------------
def test_the_summary_comes_before_the_rights_question(settings, scene_clip, tmp_path):
    asked = []
    code, out = run(
        scene_args(scene_clip, tmp_path),
        settings,
        confirm=lambda q: asked.append(q) and False,
    )
    assert code == 1 and asked == [cli.RIGHTS_QUESTION] and _no_job(settings)
    text = out.text
    assert "Scene: 6.0 s, 64x48. Shot cuts found at 1.00 s and 4.96 s." in text
    assert "Swapping 1.00 s to 4.96 s (4.0 s; moved to the shot cuts you were near)." in text
    assert "made 4 s long by holding its last frame" in text
    assert "No black bars found, so it sees 64x48." in text
    assert "never sent anywhere" in text
    assert text.index("Scene: 6.0 s") < text.index(PHOTO_CONSENT)


def test_a_scene_job_is_made_from_the_cut_part(settings, scene_clip, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--yes"), settings, services_factory=lambda s: object()
    )
    assert code == 0, out.err.text
    job = Job.load(settings.runs_dir, ids[0])
    state = job.state
    assert job.path("full-scene.mp4").read_bytes() == scene_clip.read_bytes()
    assert count_frames(job.path("upload.mp4")) == 96  # 95 scene frames -> 4 s at 24 fps
    segment = state["segment"]
    assert (segment["start_frame"], segment["end_frame"], segment["padded_seconds"]) == (24, 119, 4)
    assert len(segment["frame_map"]) == 96 and segment["frame_map"][0] == 24
    assert state["subject"] == "man with white face paint" and state["mode"] == "depth_mesh"
    assert "Scene description" in out.text and "setting" not in state


def test_a_setting_you_write_is_saved_and_not_priced(settings, scene_clip, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--setting", "A dim  room.", "--yes"),
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and "Scene description" not in out.text
    state = Job.load(settings.runs_dir, ids[0]).state
    assert state["setting"] == "A dim room."
    assert state["costs"][0]["usd"] == pytest.approx(0.0598)  # no $0.001 description


def test_a_setting_also_works_without_a_scene(settings, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    clip = write_shots(tmp_path / "c.mp4", [(BLUE, 96)], fps=24, audio_seconds=4)
    code, _ = run(
        ["prepare", str(clip), "--character", str(_photo(tmp_path / "me.jpg"))]
        + ["--setting", "A dim room.", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["setting"] == "A dim room." and "segment" not in state


def test_a_cut_inside_the_part_is_a_warning_not_a_stop(settings, scene_clip, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--yes", start="0", end="6"),
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and ids
    assert "There is a shot cut at 1.0 s inside the part; the AI will see two shots." in out.text
    assert "no shot cut near either time" in out.text


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda a: a[:4] + a[6:], "Use --from and --to together."),
        (lambda a: [*a, "--provider", "piapi"], "needs WaveSpeed and depth_mesh or depth mode"),
        (lambda a: [*a, "--mode", "face_mesh"], "needs WaveSpeed and depth_mesh or depth mode"),
        (lambda a: [*a, "--generator", "wan"], "needs WaveSpeed and depth_mesh or depth mode"),
        (lambda a: a[:3] + ["5"] + a[4:5] + ["2"] + a[6:], "pick --from and --to inside it"),
        (lambda a: a[:5] + ["9"] + a[6:], "The scene is 6.0 s; pick --from and --to inside it"),
        (lambda a: a[:3] + ["2"] + a[4:5] + ["2.5"] + a[6:], "The part to swap is 0.5 s"),
        (lambda a: [*a, "--setting", "x" * 301], "Describe the setting in 1 to 300 characters"),
    ],
)
def test_every_refusal_comes_before_any_question(settings, scene_clip, tmp_path, change, message):
    asked = []
    code, out = run(change(scene_args(scene_clip, tmp_path)), settings, confirm=asked.append)
    assert code == 2 and message in out.err.text and "Nothing was charged." in out.err.text
    assert asked == [] and _no_job(settings)


def test_a_scene_whose_sound_does_not_fit_is_refused_first(settings, tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 24), (BLUE, 95), (GREEN, 24)], audio_seconds=3)
    asked = []
    code, out = run(scene_args(clip, tmp_path), settings, confirm=asked.append)
    assert code == 2 and "Re-export the scene" in out.err.text and asked == []


def test_a_missing_scene_is_refused_first(settings, tmp_path):
    code, out = run(scene_args(tmp_path / "nope.mp4", tmp_path), settings)
    assert code == 2 and "Video not found" in out.err.text


def test_a_setting_is_refused_for_wan(settings, tmp_path):
    clip = write_shots(tmp_path / "c.mp4", [(BLUE, 48)], fps=24)
    face = _photo(tmp_path / "me.jpg")
    code, out = run(
        ["prepare", str(clip), "--generator", "wan", "--character", str(face), "--setting", "A"],
        settings,
    )
    assert code == 2 and "--setting only matters for Seedance modes" in out.err.text
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene_cli.py -q --no-cov`
Expected: FAIL (`unrecognized arguments: --from`).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/cli.py`:

````python
from character_swap.jobs import SEEDANCE_PROVIDERS, WHISPER_LANGUAGES, Job, JobError
````

with:

````python
from character_swap.jobs import SEEDANCE_PROVIDERS, WHISPER_LANGUAGES, Job, JobError, clean_setting
from character_swap.media.scene import Plan, cut_segment, plan_part
````

**Replace** in `src/character_swap/cli.py`:

````python
    prep.add_argument(
        "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
    )
````

with:

````python
    prep.add_argument(
        "--from",
        dest="start",
        type=float,
        metavar="SECONDS",
        help="swap one shot of a longer scene: where it starts (moves to a shot cut within 0.3 s)",
    )
    prep.add_argument(
        "--to",
        dest="end",
        type=float,
        metavar="SECONDS",
        help="where that shot ends (moves to a shot cut within 0.3 s)",
    )
    prep.add_argument(
        "--setting",
        metavar="TEXT",
        help="describe the setting yourself (1 to 300 characters); skips the paid description",
    )
    prep.add_argument(
        "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
    )
````

**Replace** in `src/character_swap/cli.py`:

````python
def _prepare(args, settings, env) -> int:
    mode = args.mode or ("direct" if args.generator == "wan" else "depth_mesh")
    provider = _provider_for(args)
````

with:

````python
SCENE_NEEDS = (
    "Swapping one part of a scene needs WaveSpeed and depth_mesh or depth mode. "
    "Nothing was charged."
)
SETTING_FOR_SEEDANCE = (
    "--setting only matters for Seedance modes (Wan has no setting description). "
    "Nothing was charged."
)
MODES_FOR_SCENES = ("depth_mesh", "depth")  # the paste-back needs SAM masks (S12)


def _is_scene_swap(args, mode: str, provider: str) -> bool:
    """--from and --to: swap one part of a scene. Refuses what it can't do, before any question."""
    if (args.start is None) != (args.end is None):
        raise JobError("Use --from and --to together. Nothing was charged.")
    if args.start is None:
        return False
    if args.generator != "seedance" or provider != "wavespeed" or mode not in MODES_FOR_SCENES:
        raise JobError(SCENE_NEEDS)
    return True


def _setting(text: str | None, mode: str) -> str:
    if text is None:
        return ""
    if mode == "direct":
        raise JobError(SETTING_FOR_SEEDANCE)
    return clean_setting(text)


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _edges(moved: tuple[bool, bool]) -> str:
    if all(moved):
        return "moved to the shot cuts you were near"
    if not any(moved):
        return "no shot cut near either time, so both are used as typed, to the nearest frame"
    snapped, typed = ("--from", "--to") if moved[0] else ("--to", "--from")
    return f"{snapped} moved to the shot cut you were near; {typed} is used as typed"


def scene_summary(plan: Plan) -> str:
    """What a scene swap will do, in plain English, before any question."""
    segment = plan.segment
    width, height, crop = segment["width"], segment["height"], segment["crop"]
    cuts = [f"{cut:.2f} s" for cut in segment["cuts"]]
    found = f"Shot cuts found at {_listed(cuts)}." if cuts else "No shot cuts found."
    padded = segment["padded_seconds"]
    if padded - segment["seconds"] > 0.02:
        length = f"made {padded} s long by holding its last frame"
    else:
        length = f"{padded} s long"
    sees = f"{crop['w']}x{crop['h']}"
    if crop["w"] >= width - 1 and crop["h"] >= height - 1:
        bars = f"No black bars found, so it sees {sees}."
    else:
        bars = f"Black bars are left out, so it sees {sees}."
    lines = [
        f"Scene: {plan.scene_seconds:.1f} s, {width}x{height}. {found}",
        f"Swapping {segment['start']:.2f} s to {segment['end']:.2f} s "
        f"({segment['seconds']:.1f} s; {_edges(plan.moved)}).",
        f"The AI only gets this part, {length}. {bars} The rest of the scene is kept as it is "
        "and is never sent anywhere.",
    ]
    lines += [
        f"There is a shot cut at {cut:.1f} s inside the part; the AI will see two shots. "
        "Check it in the app before approving."
        for cut in plan.inside
    ]
    return "\n".join(lines)


def _cut_part(args, plan: Plan, scratch: Path) -> tuple[Path, dict]:
    """The part the AI gets (free), and the scene and segment for Job.create."""
    crop = plan.segment["crop"]
    part = scratch / "part.mp4"
    frame_map = cut_segment(
        args.video,
        plan.segment["start"],
        plan.segment["end"],
        (crop["x"], crop["y"], crop["w"], crop["h"]),
        part,
    )
    return part, {"scene": args.video, "segment": {**plan.segment, "frame_map": frame_map}}


def _prepare(args, settings, env) -> int:
    mode = args.mode or ("direct" if args.generator == "wan" else "depth_mesh")
    provider = _provider_for(args)
    scene_swap = _is_scene_swap(args, mode, provider)
    setting = _setting(args.setting, mode)
````

**Replace** in `src/character_swap/cli.py`:

````python
        character, character2, faces = _characters(args, settings, env, Path(scratch), provider)
        key = f"seedance:{provider}" if provider else "wan"
````

with:

````python
        character, character2, faces = _characters(args, settings, env, Path(scratch), provider)
        plan = plan_part(args.video, args.start, args.end) if scene_swap else None
        if plan is not None:
            env.out(scene_summary(plan))
        key = f"seedance:{provider}" if provider else "wan"
````

**Replace** in `src/character_swap/cli.py`:

````python
        estimate = estimate_prepare(mode, seconds=30)
        free = estimate.total == 0
````

with:

````python
        estimate = estimate_prepare(mode, seconds=30, skip=("describe",) if setting else ())
        free = estimate.total == 0
````

**Replace** in `src/character_swap/cli.py`:

````python
        job = Job.create(
            settings.runs_dir,
            args.video,
            generator=args.generator,
````

with:

````python
        source, scene = args.video, {}
        if plan is not None:
            env.out("Cutting the part out of the scene (free)...")
            source, scene = _cut_part(args, plan, Path(scratch))
        job = Job.create(
            settings.runs_dir,
            source,
            generator=args.generator,
````

**Replace** in `src/character_swap/cli.py`:

````python
            language=args.language,
            provider=provider,
        )
        env.track(job, "resume {id}")
````

with:

````python
            language=args.language,
            provider=provider,
            setting=setting,
            **scene,
        )
        env.track(job, "resume {id}")
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene_cli.py tests/test_cli.py -q --no-cov`
Expected: all PASS (the existing CLI tests unchanged).

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/cli.py tests/test_scene_cli.py
git commit -m "feat: charswap prepare --from --to swaps one shot of a scene; --setting"
````

---

### Task 12: `resume --setting`, the draft price with the lay-back, and the refusals

The rest of the command line (S11, the generate bullets of "File and stage changes"):
- `charswap resume <id> --setting TEXT` saves the setting when the describe stage is not done
  (the stage then makes no call), else refuses ("the setting is already done"). The remaining
  paid steps it asks about, and the prep spend it logs, leave the description out.
- `charswap generate`: the price shown is `generation_estimate(..., replacing_stale=remake)`:
  the 13 s Seedance price plus one `sam3` line for a scene job. When Seedance resumes free (a
  saved record or `draft-raw.mp4`) but the lay-back's SAM record is missing, it says "The AI
  part resumes free; laying it back needs one SAM 3 run (~$0.002)" and asks (`--yes` skips).
  Only when both are saved does it say "resuming it (no new charge)" as today. The two final
  prices after a draft include the lay-back too.
- `charswap repair` on a scene job is refused (it already cuts out on the original colors).

**Files:**
- Modify: `src/character_swap/cli.py`
- Test: `tests/test_scene_cli.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_scene_cli.py`:

````python
import pytest
from PIL import Image

from character_swap import cli
from character_swap.config import Settings
from character_swap.faces import PHOTO_CONSENT
from character_swap.jobs import Job
from character_swap.media.video import count_frames
from helpers import SCENE_RATE, write_shots
````

with:

````python
import pytest
from PIL import Image

from character_swap import cli, pipeline
from character_swap.config import Settings
from character_swap.faces import PHOTO_CONSENT, FaceLibrary
from character_swap.jobs import Job
from character_swap.media.video import count_frames
from character_swap.modes import MODES
from character_swap.pipeline import Services
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.review import decide, fingerprint
from character_swap.stages.describe import DESCRIBE_MODEL
from fakes import FakeWaveSpeed, RefusingReplicate
from helpers import SCENE_RATE, solid, write_shots, write_video
from scene_jobs import fake_pitch, make_scene_job, prep_services
````

**Append** to `tests/test_scene_cli.py`:

````python
# ---- resume --setting ----------------------------------------------------------------
def test_resume_with_a_setting_skips_the_paid_description(settings, tmp_path, monkeypatch):
    job = make_scene_job(settings.runs_dir, tmp_path)
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        ["resume", job.id, "--setting", "A dim room.", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and ids == [job.id] and job.state["setting"] == "A dim room."
    assert "Remaining paid steps: separate, transcribe, depth, masks " in out.text
    assert job.state["costs"][-1]["usd"] == pytest.approx(0.0598)  # no $0.001 description


def test_resume_refuses_a_setting_once_the_description_is_done(settings, tmp_path, monkeypatch):
    job = make_scene_job(settings.runs_dir, tmp_path)
    job.mark_stage("describe")
    monkeypatch.setattr(cli, "prepare_job", lambda *a, **k: pytest.fail("must not run"))
    code, out = run(["resume", job.id, "--setting", "A dim room."], settings)
    assert code == 2 and "The setting is already done for this job" in out.err.text
    assert "setting" not in job.state


def test_a_refused_description_continues_for_free(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)
    job = make_scene_job(settings.runs_dir, tmp_path)
    services = prep_services(tmp_path, cls=RefusingReplicate, refuse=DESCRIBE_MODEL)
    code, out = run(["resume", job.id, "--yes"], settings, services_factory=lambda s: services)
    assert code == 2
    assert f'charswap resume {job.id} --setting "a dim room with ..."' in out.err.text
    code, out = run(
        ["resume", job.id, "--setting", "A dim room.", "--yes"],
        settings,
        services_factory=lambda s: services,
    )
    assert code == 0, out.err.text
    keys = [run["key"] for run in services.replicate.runs]
    assert keys.count(DESCRIBE_MODEL) == 1 and job.stage_done("embed")  # no new description


# ---- generate and repair ---------------------------------------------------------------
def ready_scene_job(settings, tmp_path):
    """An approved depth_mesh scene job whose part is 13 s (307 scene frames, padded).

    The prepared files are stand-ins; the review hashes whatever is there.
    """
    job = make_scene_job(settings.runs_dir, tmp_path, mode="depth_mesh", face_frames=307)
    for name in MODES["depth_mesh"].review_files:
        if name.endswith(".json"):  # face-landmarks.json
            job.path(name).write_text('{"fps": 24, "frames": []}')
        else:
            write_video(job.path(name), [solid((5, 5, 5))] * 24)
    for stage in MODES["depth_mesh"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 13.0}, transcript="hello there", scene="A studio.")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    return job


def ws_services(settings):
    """The real WaveSpeed generator on a fake WaveSpeed; ws_services.last is that fake."""
    output = write_video(settings.root / "wavespeed-out.mp4", [solid((20, 200, 20))] * 24)
    fake = FakeWaveSpeed(files={"https://cdn.wavespeed.ai/out.mp4": output})
    ws_services.last = fake
    return Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": SeedanceWaveSpeedGenerator(fake)},
        faces=FaceLibrary(settings.root),
    )


@pytest.fixture
def laid_back(monkeypatch):
    """The lay-back without SAM or paste: it saves its SAM record and copies the raw clip."""
    calls = []

    def fake_lay_back(job, stage, replicate, dst, *, runs_dir, status):
        calls.append(stage)
        folder = job.path(f"{stage}-sam")
        folder.mkdir(exist_ok=True)
        (folder / "sam-prediction.json").write_text('{"id": "s1", "status": "succeeded"}')
        dst.write_bytes(job.path(f"{stage}-raw.mp4").read_bytes())
        return dst

    monkeypatch.setattr(pipeline, "lay_back", fake_lay_back)
    return calls


def test_the_draft_price_is_the_padded_part_plus_the_lay_back(settings, tmp_path, laid_back):
    job = ready_scene_job(settings, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: asked.append(q) and False,
        services_factory=ws_services,
    )
    assert code == 1 and asked == ["Continue?"] and ws_services.last.bodies == []
    assert "Seedance draft (480p)        ~$1.4300" in out.text  # 13 s, never the 17 s scene
    assert "SAM 3 masks" in out.text and "Total                        ~$2.8620" in out.text
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=ws_services
    )
    assert code == 0 and laid_back == ["draft"] and len(ws_services.last.bodies) == 1
    assert "makes a new 720p take (~$5.72)" in out.text
    assert "`--resolution 1080p` for a new 1080p take (~$14.30)" in out.text


@pytest.mark.parametrize("saved", ["record", "raw"])
def test_a_free_resume_still_asks_before_buying_the_lay_back(settings, tmp_path, laid_back, saved):
    job = ready_scene_job(settings, tmp_path)
    if saved == "record":
        job.path("draft-prediction.json").write_text('{"id": "p", "status": "succeeded"}')
    else:
        write_video(job.path("draft-raw.mp4"), [solid((20, 200, 20))] * 24)
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job))  # e.g. Ctrl-C
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: asked.append(q) and False,
        services_factory=ws_services,
    )
    assert code == 1 and asked == ["Continue?"] and laid_back == []
    assert "The AI part resumes free; laying it back needs one SAM 3 run (~$0.002)." in out.text
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=ws_services
    )
    assert code == 0 and laid_back == ["draft"] and ws_services.last.bodies == []


def test_with_both_saved_the_resume_asks_nothing(settings, tmp_path, laid_back):
    job = ready_scene_job(settings, tmp_path)
    write_video(job.path("draft-raw.mp4"), [solid((20, 200, 20))] * 24)
    job.path("draft-sam").mkdir()
    job.path("draft-sam/sam-prediction.json").write_text('{"id": "s", "status": "succeeded"}')
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=ws_services,
    )
    assert code == 0 and asked == [] and "resuming it (no new charge)" in out.text


def test_a_remakes_price_includes_a_new_lay_back(settings, tmp_path, laid_back):
    job = ready_scene_job(settings, tmp_path)
    write_video(job.path("draft.mp4"), [solid((20, 200, 20))] * 24)
    job.path("draft-sam").mkdir()
    job.path("draft-sam/sam-prediction.json").write_text('{"id": "s", "status": "succeeded"}')
    job.mark_stage("draft", fingerprint="inputs that are no longer approved")
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: False,
        services_factory=ws_services,
    )
    assert code == 1 and "SAM 3 masks" in out.text and "~$2.8620" in out.text
    assert job.path("draft-sam/sam-prediction.json").exists()  # declined: nothing archived


def test_repair_is_refused_on_a_scene_job(settings, tmp_path, monkeypatch):
    job = make_scene_job(settings.runs_dir, tmp_path)
    monkeypatch.setattr(cli, "repair_with_rgb", lambda *a, **k: pytest.fail("must not run"))
    asked = []
    code, out = run(["repair", job.id], settings, confirm=asked.append)
    assert code == 2 and asked == []
    assert "already cuts out on the original colors" in out.err.text
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_scene_cli.py -q --no-cov`
Expected: the new tests FAIL (`unrecognized arguments: --setting` for resume; no `SAM 3 masks` line; repair runs).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/cli.py`:

````python
    final_resolution,
    generation_files,
    generation_label,
    generation_prompt,
    generator_for,
    generator_key,
    generator_promotes,
    is_promote,
    needs_new_charge,
    next_step,
    pin_provider,
````

with:

````python
    final_resolution,
    generation_estimate,
    generation_files,
    generation_label,
    generation_prompt,
    generator_for,
    generator_key,
    generator_promotes,
    is_promote,
    is_scene_job,
    needs_lay_back_charge,
    needs_new_charge,
    next_step,
    pin_provider,
    prep_skip,
````

**Replace** in `src/character_swap/cli.py`:

````python
    resume.add_argument("job")
    resume.add_argument("--yes", action="store_true", help="skip the cost confirmation")
````

with:

````python
    resume.add_argument("job")
    resume.add_argument(
        "--setting",
        metavar="TEXT",
        help="describe the setting yourself and go on for free (when the description failed)",
    )
    resume.add_argument("--yes", action="store_true", help="skip the cost confirmation")
````

**Replace** in `src/character_swap/cli.py`:

````python
def _resume(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    env.track(job, "resume {id}")
````

with:

````python
def _save_setting(job: Job, text: str) -> None:
    """resume --setting: the describe stage then uses it and makes no call."""
    setting = _setting(text, job.mode)
    if job.stage_done("describe"):
        raise JobError(
            "The setting is already done for this job, so --setting changes nothing. "
            "Nothing was charged."
        )
    job.update(setting=setting)


def _resume(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    env.track(job, "resume {id}")
    if args.setting is not None:
        _save_setting(job, args.setting)
````

**Replace** in `src/character_swap/cli.py`:

````python
    if not any(c["step"] == "prepare" for c in job.state.get("costs", [])):
        record_spend(job, "prepare", estimate_prepare(job.mode, seconds=30), settings.runs_dir)
    return 0
````

with:

````python
    if not any(c["step"] == "prepare" for c in job.state.get("costs", [])):
        estimate = estimate_prepare(job.mode, seconds=30, skip=prep_skip(job))
        record_spend(job, "prepare", estimate, settings.runs_dir)
    return 0
````

**Replace** in `src/character_swap/cli.py`:

````python
def _repair(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    env.track(job, "repair {id}")
````

with:

````python
def _repair(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    if is_scene_job(job):
        raise StageError(
            "This job already cuts out on the original colors, so there is nothing to repair. "
            "Nothing was charged."
        )
    env.track(job, "repair {id}")
````

**Replace** in `src/character_swap/cli.py`:

````python
        if is_promote(job, stage, resolution):
            SeedanceGenerator.promote_body(request.draft_task_id)  # refuses with no task id
        estimate = generator_for(job, services).estimate(request, stage)
````

with:

````python
        if is_promote(job, stage, resolution):
            SeedanceGenerator.promote_body(request.draft_task_id)  # refuses with no task id
        estimate = generation_estimate(
            job, generator_for(job, services), request, stage, replacing_stale=remake
        )
````

**Replace** in `src/character_swap/cli.py`:

````python
    elif resumes_without_charge(job, stage):
        env.out(f"Found a saved {stage} request; resuming it (no new charge).")
````

with:

````python
    elif resumes_without_charge(job, stage):
        if needs_lay_back_charge(job, stage):
            env.out(LAY_BACK_ONLY)
            if not args.yes and not env.proceed():
                return 1
        else:
            env.out(f"Found a saved {stage} request; resuming it (no new charge).")
````

**Replace** in `src/character_swap/cli.py`:

````python
def _has_final_files(job: Job) -> bool:
````

with:

````python
LAY_BACK_ONLY = "The AI part resumes free; laying it back needs one SAM 3 run (~$0.002)."


def _has_final_files(job: Job) -> bool:
````

**Replace** in `src/character_swap/cli.py`:

````python
    generator = generator_for(job, services)
    try:
        price = {
            res: generator.estimate(
                build_request(job, faces=services.faces, stage="final", final_resolution=res),
                "final",
            ).total
            for res in finals
        }
````

with:

````python
    generator = generator_for(job, services)
    try:
        price = {
            res: generation_estimate(
                job,
                generator,
                build_request(job, faces=services.faces, stage="final", final_resolution=res),
                "final",
            ).total
            for res in finals
        }
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene_cli.py tests/test_cli.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/cli.py tests/test_scene_cli.py
git commit -m "feat: resume --setting; scene drafts priced with the lay-back; repair refused"
````

---

### Task 13: The walkthrough app shows a scene swap (minimal)

S14: on a scene job's Draft and Final steps, the "before" side of the wipe is
`full-scene.mp4`, with one line under the step's own line: "Only the person you picked is laid
back into the scene; everything else is the original." Step 1 shows the full scene. Step 3
(Check) keeps comparing with what the AI gets, unchanged. The cost table has no describe row
when you wrote the setting, and a "Lay back (SAM 3)" row from the `{stage}-paste` entries
(matched to the current `{stage}-sam` record, like the draft's own row). No new tab, no button.

**Files:**
- Modify: `src/character_swap/web/steps.py`
- Modify: `src/character_swap/web/pages.py`
- Modify: `src/character_swap/web/view_model.py`
- Modify: `src/character_swap/web/templates/job.html`
- Test: `tests/test_pages.py`, `tests/test_view_model.py`, `tests/test_web_app.py`

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/test_pages.py`:

````python
from character_swap.web import pages
````

with:

````python
from character_swap.web import pages, steps
````

**Append** to `tests/test_pages.py`:

````python
# ---- a scene swap ------------------------------------------------------------------
def test_a_scene_swap_compares_the_draft_and_final_with_the_whole_scene(runs_dir, media):
    job = _approved(runs_dir, media)
    media.copy("full-scene.mp4", job.path("full-scene.mp4"))
    finish_stage(job, media, "draft")
    draft = pages.job_page(job, runs_dir, 4)
    assert draft.wipe.before == f"/jobs/{job.id}/files/full-scene.mp4"
    assert draft.wipe.after.endswith("/draft.mp4") and draft.scene_line == steps.SCENE_LINE
    finish_stage(job, media, "final")
    assert pages.job_page(job, runs_dir, 5).wipe.before.endswith("/full-scene.mp4")
    upload = pages.job_page(job, runs_dir, 1)
    assert upload.upload.endswith("/full-scene.mp4") and upload.scene_line == ""
    check = pages.job_page(job, runs_dir, 3)  # what the AI sees: the part, unchanged
    assert check.wipe.before.endswith("/original.mp4") and check.scene_line == ""


def test_a_single_shot_draft_still_compares_with_the_clip(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    page = pages.job_page(job, runs_dir, 4)
    assert page.wipe.before.endswith("/original.mp4") and page.scene_line == ""
    assert pages.job_page(job, runs_dir, 1).upload.endswith("/upload.mp4")
````

**Append** to `tests/test_view_model.py`:

````python
def test_a_setting_you_wrote_has_no_cost_row(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    job.mark_stage("describe", source="you")
    rows = vm.cost_rows(job, job.state)
    assert "Scene description" not in [row.step for row in rows]
    expected = round(estimate_prepare("depth", 30).total - 0.001, 4)
    assert vm.completed_cost(rows) == pytest.approx(expected)


def test_the_lay_back_is_its_own_cost_row(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft"})
    write_record(job, "draft-sam/sam-prediction.json", {"id": "sam-1", "status": "succeeded"})
    job.append_cost({"step": "draft-paste", "usd": 0.002, "task_id": "sam-old"})  # archived
    job.append_cost({"step": "draft-paste", "usd": 0.002, "task_id": "sam-1"})
    rows = vm.cost_rows(job, job.state)
    assert [row.step for row in rows][-2:] == ["Draft (480p)", "Lay back (SAM 3)"]
    assert rows[-1] == vm.CostRow(
        "Lay back (SAM 3)", "Replicate", 0.002, "https://replicate.com/p/sam-1"
    )


def test_no_lay_back_row_without_its_current_record(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    job.append_cost({"step": "draft-paste", "usd": 0.002, "task_id": "sam-old"})
    assert "Lay back (SAM 3)" not in [row.step for row in vm.cost_rows(job, job.state)]
````

**Append** to `tests/test_web_app.py`:

````python
def test_a_scene_swaps_draft_page_shows_the_whole_scene(client, runs_dir, media):
    job = _approved(runs_dir, media)
    media.copy("full-scene.mp4", job.path("full-scene.mp4"))
    finish_stage(job, media, "draft")
    text = client.get(f"/jobs/{job.id}/step/4").text
    before = f'class="wipe-before" muted playsinline preload="auto" src="/jobs/{job.id}/files/'
    assert before + 'full-scene.mp4"' in text
    assert steps.SCENE_LINE in text
    assert steps.SCENE_LINE not in client.get(f"/jobs/{job.id}/step/3").text
````

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_pages.py tests/test_view_model.py tests/test_web_app.py -q --no-cov`
Expected: the new tests FAIL (`has no attribute 'SCENE_LINE'`, a describe row, no lay-back row).

- [ ] **Step 3: Write the code**

**Append** to `src/character_swap/web/steps.py`:

````python
# A scene swap: only one shot of a longer scene is swapped (charswap prepare --from --to).
SCENE_LINE = (
    "Only the person you picked is laid back into the scene; everything else is the original."
)
````

**Replace** in `src/character_swap/web/pages.py`:

````python
    tabs: tuple[TabView, ...] = ()
    tab: TabView | None = None
    wipe: Wipe | None = None
````

with:

````python
    tabs: tuple[TabView, ...] = ()
    tab: TabView | None = None
    wipe: Wipe | None = None
    scene_line: str = ""  # a scene swap's draft and final: what was laid back (S14)
````

**Replace** in `src/character_swap/web/pages.py`:

````python
    original = (
        file_if_present(job, "original.mp4") if stage_status(state, "normalize") == "done" else None
    )
````

with:

````python
    original = (
        file_if_present(job, "original.mp4") if stage_status(state, "normalize") == "done" else None
    )
    scene = file_if_present(job, "full-scene.mp4")  # a scene swap: the whole scene (S14)
````

**Replace** in `src/character_swap/web/pages.py`:

````python
            state="ready",
            upload=file_if_present(job, "upload.mp4"),
````

with:

````python
            state="ready",
            upload=scene or file_if_present(job, "upload.mp4"),
````

**Replace** in `src/character_swap/web/pages.py`:

````python
    stage = "draft" if number == 4 else "final"
    shown = _generation(job, state, stage, original)
````

with:

````python
    stage = "draft" if number == 4 else "final"
    shown = _generation(job, state, stage, scene or original)
````

**Replace** in `src/character_swap/web/pages.py`:

````python
        download=wipe.after if number == 5 and wipe else None,
        **shown,
    )
````

with:

````python
        download=wipe.after if number == 5 and wipe else None,
        scene_line=steps.SCENE_LINE if scene else "",
        **shown,
    )
````

**Replace** in `src/character_swap/web/templates/job.html`:

````html
    <p class="line">{{ page.step.line }}</p>
````

with:

````html
    <p class="line">{{ page.step.line }}</p>
    {% if page.scene_line %}<p class="line">{{ page.scene_line }}</p>{% endif %}
````

**Replace** in `src/character_swap/web/view_model.py`:

````python
RGB_REPAIR_RECORD = "rgb-repair/sam-prediction.json"
````

with:

````python
RGB_REPAIR_RECORD = "rgb-repair/sam-prediction.json"
LAY_BACK = "Lay back (SAM 3)"  # a scene swap's SAM 3 run on the AI result, per draft/final
````

**Replace** in `src/character_swap/web/view_model.py`:

````python
    for stage in PAID_STAGES[state["mode"]]:
        if stage_status(state, stage) != "done":
            continue
        if stage == "depth" and state.get("depth_provider") == "huggingface":
````

with:

````python
    for stage in PAID_STAGES[state["mode"]]:
        if stage_status(state, stage) != "done":
            continue
        if stage == "describe" and state["stages"]["describe"].get("source") == "you":
            continue  # you wrote the setting: nothing was paid
        if stage == "depth" and state.get("depth_provider") == "huggingface":
````

**Replace** in `src/character_swap/web/view_model.py`:

````python
    for stage in GENERATION_STAGES:  # priced by length: use the estimate logged at creation
        if stage_status(state, stage) != "done":
            continue
        rows.append(
            CostRow(
                _generation_name(state, stage, key),
                PROVIDER_NAMES.get(key, "Replicate"),
                _generation_cost(job, state, stage),
                None if seedance else prediction_url(job, PAID_RECORD[stage]),
            )
        )
    return tuple(rows)
````

with:

````python
    for stage in GENERATION_STAGES:  # priced by length: use the estimate logged at creation
        if stage_status(state, stage) == "done":
            rows.append(
                CostRow(
                    _generation_name(state, stage, key),
                    PROVIDER_NAMES.get(key, "Replicate"),
                    _generation_cost(job, state, stage),
                    None if seedance else prediction_url(job, PAID_RECORD[stage]),
                )
            )
        lay_back = _lay_back_cost(job, state, stage)
        if lay_back is not None:
            record = f"{stage}-sam/sam-prediction.json"
            rows.append(CostRow(LAY_BACK, "Replicate", lay_back, prediction_url(job, record)))
    return tuple(rows)


def _lay_back_cost(job: Job, state: dict[str, Any], stage: str) -> float | None:
    """A scene swap's lay-back SAM 3 run for the current {stage}-sam record, or None."""
    current = prediction_id(job, f"{stage}-sam/sam-prediction.json")
    entries = [
        entry
        for entry in _cost_entries(state)
        if entry.get("step") == f"{stage}-paste" and current and entry.get("task_id") == current
    ]
    if not entries:
        return None
    return round(sum(_usd(entry.get("usd")) for entry in entries), 4)
````

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pages.py tests/test_view_model.py tests/test_web_app.py tests/test_steps.py -q --no-cov`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

````bash
uv run ruff format src tests && uv run ruff check src tests
git add src/character_swap/web/steps.py src/character_swap/web/pages.py src/character_swap/web/view_model.py src/character_swap/web/templates/job.html tests/test_pages.py tests/test_view_model.py tests/test_web_app.py
git commit -m "feat: the walkthrough shows a scene swap against the whole scene"
````

---

### Task 14: Docs: the README section and `CLAUDE.md`

The README gets a short "Swap one shot of a longer scene" section with the command (no film
names). `CLAUDE.md` names the new files in its layout line and the new flags in its command
list. Nothing else changes.

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the docs**

**Replace** in `README.md`:

````markdown
## Try it with Wan (open source, on Replicate)
````

with:

````markdown
## Swap one shot of a longer scene

When your clip has several shots and only one person in one of them should become you, give
the part with `--from` and `--to` (seconds, as your editor shows them) and say who to swap
with `--subject`:

```bash
uv run charswap prepare inputs/scene.mp4 --from 2.6 --to 15.4 \
  --subject "man with white face paint" --character inputs/me.jpg
```

Each time moves to a shot cut found within 0.3 s, so the part is exactly one shot; prepare
prints what it found before asking anything. Only that part goes to the AI, made whole
seconds long by holding its last frame, with any black bars left out. Every other frame and
the whole soundtrack stay the original's. The draft and final are the full scene: after the
AI is done, its new person is laid back over the untouched scene (one extra SAM 3 run on the
result, about $0.002, shown in the price). The price is for the part, never the whole scene:
a 13 s part costs about $2.86 for the draft.

Name the one person in `--subject`; if SAM 3 finds two people in the part, prepare stops and
says so. Scene swaps need WaveSpeed and the `depth_mesh` (default) or `depth` mode.

If GPT-4o mini refuses to describe the setting (it can, on film stills), describe it yourself
and go on for free: `uv run charswap resume <id> --setting "a dim room with ..."`. You can
also give `--setting` to `prepare` from the start, for any Seedance swap.

## Try it with Wan (open source, on Replicate)
````

**Replace** in `CLAUDE.md`:

````markdown
- Prompt (Seedance): `uv run charswap prompt <job>`
````

with:

````markdown
- Prompt (Seedance): `uv run charswap prompt <job>`
- One shot of a scene: `uv run charswap prepare scene.mp4 --from S --to S --subject "..." --character me.jpg` (`--setting TEXT` skips the paid description; also on `resume`)
````

**Replace** in `CLAUDE.md`:

````markdown
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
````

with:

````markdown
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
- Scene swaps (`segment` in `state.json`; `pipeline.is_scene_job`): `media/scene.py` finds cuts and bars, cuts the part, pastes back; `stages/paste.py` lays the result back inside generate. Single-shot jobs never take these branches.
````

- [ ] **Step 2: Check and commit**

Run: `uv run pytest` (the full suite with the coverage gate)
Expected: every test passes, coverage at or above 85 %.

````bash
uv run ruff format --check src tests && uv run ruff check src tests
git add README.md CLAUDE.md
git commit -m "docs: swap one shot of a longer scene"
````

---

### Task 15: The live scene draft on the test clip (controller; paid, about $2.92)

The spec's one live test. Step 1 adds it as code (never part of the default run); Steps 2 to
6 are run by the controller with Frank, in the repo root, on the branch with Tasks 1 to 14.
Money: prepare about $0.06 on Replicate (Demucs, Whisper, depth, SAM; no description because
of `--setting`), the 13 s draft $2.86 on WaveSpeed, the lay-back SAM run $0.002 on Replicate:
**about $2.92 through the draft**. The final (720p about $5.72, 1080p about $14.30) is not
part of this task and needs its own OK.

**Files:**
- Create: `tests/test_live_scene.py`

- [ ] **Step 1: Add the live test** (it only checks a draft made in Step 5; it never buys one)

**Create** `tests/test_live_scene.py`:

````python
"""Checks a live scene-swap draft. Never part of the default run, and never buys anything.

Run:  CHARSWAP_LIVE_SCENE_JOB=<id> uv run pytest tests/test_live_scene.py -m live --no-cov -q -s
<id> is a scene job (charswap prepare ... --from ... --to ...) whose draft was made with
`charswap generate <id> --draft` (about $2.86 + $0.002, with its price question).
"""

import os

import av
import pytest

from character_swap.config import Settings
from character_swap.jobs import Job
from character_swap.media.video import count_frames

pytestmark = pytest.mark.live

JOB = os.environ.get("CHARSWAP_LIVE_SCENE_JOB", "")


def _audio(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


@pytest.mark.skipif(not JOB, reason="set CHARSWAP_LIVE_SCENE_JOB to an approved scene job")
def test_a_scene_draft_is_the_whole_scene_with_its_own_sound():
    settings = Settings.load()
    job = Job.load(settings.runs_dir, JOB)
    assert "segment" in job.state and job.state.get("provider") == "wavespeed"
    assert job.stage_done("draft"), f"make the draft first: charswap generate {JOB} --draft"
    draft, scene = job.path("draft.mp4"), job.path("full-scene.mp4")
    with av.open(str(draft)) as made, av.open(str(scene)) as source:
        ours, theirs = made.streams.video[0], source.streams.video[0]
        assert (ours.width, ours.height) == (theirs.width, theirs.height)
        assert ours.average_rate == theirs.average_rate
    assert count_frames(draft) == count_frames(scene)
    assert _audio(draft) == _audio(scene)  # the soundtrack, byte for byte
    steps = [cost["step"] for cost in job.state["costs"]]
    assert steps.count("draft") == 1 and steps.count("draft-paste") == 1
    with av.open(str(job.path("draft-raw.mp4"))) as raw:
        stream = raw.streams.video[0]
        print(
            f"\nAI clip: {stream.width}x{stream.height}, {stream.average_rate} fps, "
            f"{count_frames(job.path('draft-raw.mp4'))} frames"
        )
````

Run: `uv run pytest tests/test_live_scene.py -q --no-cov`
Expected: `1 deselected` and exit code 5 (no test ran: the default run never selects `live`).

````bash
uv run ruff format src tests && uv run ruff check src tests
git add tests/test_live_scene.py
git commit -m "test: the live scene-swap draft (deselected by default)"
````

- [ ] **Step 2: Money check first (controller)**
  - WaveSpeed: the balance is about $2.14, under the $2.86 draft. Frank tops it up to at
    least $3.50 on the WaveSpeed dashboard (Billing); the controller checks the new balance
    there before Step 5. Do not start the draft on a low balance: a refused create leaves an
    unconfirmed marker to clear by hand.
  - Replicate: about $0.07 is needed (prepare plus the lay-back).
  - No other `charswap` command is running for this job; the app on port 8770 can stay up (it
    only reads).

- [ ] **Step 3: Prepare (about $0.06)**

````bash
uv run charswap prepare inputs/dark-knight-1m38-1m55.mp4 --from 2.6 --to 15.4 \
  --subject "man with white face paint" --character inputs/frank-swap-photo.jpg \
  --setting "A bare interrogation room with plain pale grey walls in cold, flat light; a tight close-up framed over a dark, out-of-focus shape in the left foreground." \
  --yes
````

Expected, before anything is paid:

````text
Scene: 17.0 s, 1920x1080. Shot cuts found at 2.63 s and 15.43 s.
Swapping 2.63 s to 15.43 s (12.8 s; moved to the shot cuts you were near).
The AI only gets this part, made 13 s long by holding its last frame. Black bars are left out, so it sees 1920x800. The rest of the scene is kept as it is and is never sent anywhere.
````

then the photo sentence, a prep price without "Scene description" (total about $0.0598),
"Cutting the part out of the scene (free)...", `JOB <id>`, and the stages, the describe one as
"Using the setting you wrote". Note the id. If it stops with "SAM 3 found more than one person
in N of 312 frames", watch `runs/<id>/mask.mp4` with Frank before anything else (the dark
shape in the left foreground is the likely second region); nothing more is paid.

- [ ] **Step 4: Check and approve (free)**
  - `runs/<id>/mask.mp4`: white is only the man in face paint, never the dark shape on the
    left. `runs/<id>/prepared.mp4`: 960x400, 13 s, the last 0.2 s a held frame.
  - Look at it in the app (`http://127.0.0.1:8770`, already running): steps 1 to 3.
  - Approve with the fingerprint from status:

````bash
uv run charswap status <id>
uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>
````

- [ ] **Step 5: The draft (about $2.86 + $0.002; asks first)**

Tell Frank first that one outcome is possible and costs nothing more: if the AI makes the dark
shape on the left near-black in every frame, those columns read as a bar, the aspect check
fails and the lay-back refuses the clip ("can't be laid back"). The SAM run is then not bought
and `draft-raw.mp4` is kept; the first live draft is where the spec settles WaveSpeed's
handling of a 2.4:1 picture.

````bash
uv run charswap generate <id> --draft
````

Expected price lines: `Seedance draft (480p) ~$1.4300`, `Seedance input video (480p)
~$1.4300`, `SAM 3 masks ~$0.0020`, `Total ~$2.8620` (13 s, never the 17 s scene). Answer `y`.
It prints "Laying the new person back into the scene" after the WaveSpeed part, then
`Watch: runs/<id>/draft.mp4` and the two final prices (about $5.72 and $14.30). Then check
the facts (free: the test never buys anything):

````bash
CHARSWAP_LIVE_SCENE_JOB=<id> uv run pytest tests/test_live_scene.py -m live --no-cov -q -s
````

Expected: `1 passed`, and the AI clip's size printed (the spec expects about 1152x480).

- [ ] **Step 6: Look by eye and write it down**
  - In the app, step 4 of the job: the wipe compares the draft with the full scene. Check the
    seam around the head and hair, the dark foreground shape (never pasted), the sharpness of
    the 480p face inside the 1080p scene, and that 0 to 2.6 s and 15.43 s to the end are the
    original.
  - Write the facts in `docs/research/` (the AI clip's size, rate and frame count, whether
    WaveSpeed padded or cropped the 2.4:1 picture, the time taken, the charges on both
    dashboards) and copy what matters for the platform to
    `dolly/docs/research/character-swap-learnings.md`.
  - If the paste-back refused the AI clip ("can't be laid back"), nothing more was paid: report
    the size it printed; that is a fix in the tool, not a new draft.

---

## Spec deviations and gap fills

The spec fit the code with these adjustments; none changes a decision or a money rule.

1. **`plan_part` and `Plan`.** The spec lists `find_cuts`, `snap`, `find_bars` and `cut_segment` but no function that turns `--from/--to` into the saved `segment`; `plan_part(scene, start, end)` does that (snap, nearest frame, the part's length, the scene's sound, the crop) and returns the summary facts too. `find_cuts` stays as the public one-liner over the same scan.
2. **`end_frame` is the first frame after the part** (370 on the test clip, whose part is frames 63 to 369), so `range(start_frame, end_frame)` is the part.
3. **Real job frames are `ceil(seconds x 24)`** (308 on the test clip, padded to 312). One fewer would leave the last scene frame of the part (369) shown by no job frame at 23.976 fps.
4. **Bars use each frame's luma** (`to_ndarray(format="gray")`, 4 times faster than RGB at 1080p) and the box moves **inward** to even numbers. On the test clip row 940 is a soft edge at brightness 57; inward rounding gives exactly the spec's 1920x800 at y = 140 (outward would give 802 rows). A box under 300 px either way means "no bars" (the whole frame).
5. **Growing is a square maximum filter**, not a disk: separable and fast at 1920x800 (the whole 17 s scene pastes in about 36 s); the Gaussian feather rounds the corners anyway.
6. **`lay_back(job, stage, replicate, dst, *, runs_dir, status)`**: `dst` because `_write_atomically` owns the final name, `runs_dir` for the spend log, `status` for the "found nobody" line. Mask A is the job's `sam/clean-masks` (after `clean_border_fragments`, as the composite used).
7. **`ai_clip` runs before SAM is bought** on the result (P4) and again inside `paste_back` (cheap), so `paste_back` stays safe on its own.
8. **The one-person message's price** is computed (`usd_text(estimate_prepare(...))`, "$0.06"), not written in.
9. **`--setting` is refused for Wan** (`direct` has no describe stage), on `prepare` and `resume`, instead of being ignored.
10. **Summary wording when an edge is not at a cut**, when there are no bars, or when the part is already whole seconds: "no shot cut near either time, so both are used as typed, to the nearest frame", "--from moved to the shot cut you were near; --to is used as typed", "No black bars found, so it sees WxH.", "N s long".
11. **`scene.jpg` is still saved with `--setting`** (free, local): the app's home card uses it as the thumbnail.
12. **A scene without sound** gives a silent part and a silent result (`add_original_soundtrack` already copies the picture only); the spec does not cover it.
13. **`fps` is `str(Fraction)`**: "24000/1001" on the test clip, "24" for a 24 fps scene.
14. **The repair refusal lives in the CLI only** (before any question); `pipeline.repair_with_rgb` is not reachable from the app (read-only).
15. **The app keys on `full-scene.mp4`** (P6); Step 3 (Check) keeps comparing with what the AI gets. The lay-back cost row counts only the `{stage}-paste` entry whose task id matches the current `{stage}-sam` record, like the draft's own row, so an archived draft's lay-back is not counted twice.
16. **Times are the scene's own frame timestamps.** A scene whose picture starts after 0 s (the Harry upload starts at 0.041 s) is planned in those times; the test clip starts at 0.
17. **The live test only checks the draft** (it fails if the draft is not made): the paid draft is made with `charswap generate --draft`, which shows the price and asks (Task 15), so the test can never spend without a question.
18. **A new lay-back SAM run clears its folder first** (`{stage}-sam/` with no saved record): `segment` and `clean_border_fragments` never empty their folders, so masks left by an older, longer result would otherwise keep the "masks don't match" error coming back on every paid rerun. That message also says not to pay a second time if it comes back.
