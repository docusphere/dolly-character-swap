# Plan 2a — Wan Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Wan job goes end to end: `charswap prepare clip.mp4 --generator wan --character me.jpg` → review → `charswap generate <id> --draft` (480p, estimate + confirmation) → `charswap generate <id> --final` (720p, same seed) → `runs/<id>/final.mp4` with the clip's original soundtrack restored byte for byte. Along the way, fix what the first live run found (Replicate rate limits, Whisper skipping lyrics, viewer not noticing a resume) and land the cheap Plan 2 prerequisites that generation needs.

**Architecture:** A provider-neutral `Generator` protocol (`providers/generators/base.py`) with one implementation, `WanGenerator` (`providers/generators/wan_replicate.py`), built on the existing money-safe `ReplicateClient.run()` (exclusive "creating" marker before the POST, prediction record saved before waiting, resume from the record). `pipeline.generate()` owns the paid draft/final stages: it gates on the hashed review, logs one spend entry the moment a paid prediction is created, downloads once, and restores the original soundtrack (`media/audio.py`). Generation stages live in their own group (`modes.GENERATION_STAGES`), separate from preparation, so the read-only viewer can show Draft/Final sections without treating a prepared job as "running". One `modes.py` table replaces the four mode tables.

**Tech Stack:** Python 3.12, uv, httpx (+ respx in tests), PyAV/NumPy, FastAPI/Jinja2 viewer, vanilla JS; pytest + pytest-cov (≥ 85 % enforced), ruff.

---

**Spec:** `docs/superpowers/specs/2026-10-22-character-swap-v1-design.md` (§5 data flow, §6.1 Generator interface, §6.4 Wan, §7 costs, §10 errors, §12 testing).

**Builds on:** Plan 1 + the job viewer as merged (400 tests, 97 % coverage). All code below targets the current APIs: `Job.create/load/path/update/mark_stage/append_cost`, `ReplicateClient.run(input=, record=, version=, model=)`, `review.require_approved/fingerprint/decide`, `costs.Estimate/Line/record_spend`, `pipeline._tracked/_wrap/_notify`, `cli.Env/main()`, `web.view_model.SECTIONS/progress()`.

**Verified before writing (2026-11-03, free GET requests only, nothing was bought):**
- `wan-video/wan-2.2-animate-replace` is an **official** model → call `POST /v1/models/wan-video/wan-2.2-animate-replace/predictions` (no version pin; latest version `33ec6b98…1ba1`, 2026-01-28).
- Inputs: `video` (uri, required), `character_image` (uri, required), `resolution` enum **strings** `"480"`/`"720"` (default `"720"`), `seed` (int, nullable), `go_fast` (bool, default false), `merge_audio` (bool, **default true** → we send `false`), `refert_num` and `frames_per_second` (both deprecated; output is always **30 fps**). Output: one uri string to an mp4.
- Public example: 8.0 s / 24 fps input → 7.9 s / 30 fps output (so the soundtrack restore must accept Wan's 30 fps and a small length difference), predict time 83 s at 720p.
- **Price NOT verified:** the model page, API tab and README show no price today. We keep $0.02 (480p) / $0.05 (720p) per output second from the 2026-10-22 research and confirm against billing in the live test (Task 15).
- Max input length is unpublished ("long videos process in segments"). We keep the 30 s cap.
- Whisper pinned version `80996966…d16e` has `condition_on_previous_text` (bool, default true), `language` (enum, default `"auto"`), `temperature` (0) with `temperature_increment_on_fallback` (0.2), `no_speech_threshold` (0.6), `logprob_threshold` (-1), `compression_ratio_threshold` (2.4); no `model` input.
- Full notes: `docs/research/2026-10-22-provider-api-facts.md` → "Wan 2.2 Animate Replace (verified 2026-11-03)".

## Decisions taken in this plan

| # | Decision | Why |
|---|---|---|
| P1 | Generator interface = `estimate(request, stage)` + `generate(request, stage, *, record, dest, on_created)` instead of the spec's `submit_draft/submit_final/poll` | The record file is what makes "submit once, resume, never double charge" work (marker before POST, id saved before waiting). Splitting submit and poll would duplicate that logic per provider. Seedance (2b) implements the same two methods with its own client; `stage="final"` lets it promote. |
| P2 | Files live in `providers/generators/` (spec §4 layout), not `providers/wan_replicate.py` | Seedance joins the same folder in 2b. |
| P3 | `GenerationRequest` is rebuilt from `state.json` (normalized clip, character, seed, clip length) instead of being saved whole | Everything it needs is already in state; nothing to keep in sync. Revisit in 2b if Seedance's promote needs more. |
| P4 | The final needs a finished draft and a still-approved review; the "watch the draft first" check is the cost confirmation (no second hashed gate) | Keeps 2a small; a hashed draft approval belongs with the Plan 3 UI. |
| P5 | Soundtrack restore: the generated video may be up to 0.5 s shorter **or longer** than the original audio; it is fitted (last frame held / extra end frames dropped) and the audio is copied untouched. Beyond 0.5 s → refuse with a clear error. A clip with no audio gives a silent final. A free pre-flight before the cost question refuses clips whose sound and picture already differ by more than 0.4 s, so nobody pays for a video whose soundtrack can't be restored. | Wan always outputs 30 fps and its example was 0.1 s short; we never trim, loop or stretch the soundtrack. |
| P6 | A 429 on the paid create is retried (max 3, honoring Retry-After, capped 60 s). On each 429 the run **releases** its "creating" marker, sleeps, and **re-claims it exclusively** before posting again; if someone else claimed it meanwhile, it stops without posting | Replicate answers 429 when it throttles the request *before* creating a prediction: nothing exists, nothing is billed. Releasing the marker means a waiting run never holds a marker someone could be told to delete; the exclusive re-claim means two runs can never both POST. Every other answer (2xx, other 4xx, 5xx, timeout, bad JSON) keeps today's rules. |
| P7 | Polling backs off 5 s → ×1.5 → 30 s ceiling | Low-balance accounts get ~6 requests/min; fixed 5 s polling used 12/min and starved the next create. After the first minute this polls ~2/min. |
| P8 | Whisper: `condition_on_previous_text: false` always; `language` from the job (`--language`, default `"auto"`) | "en" would break non-English clips. Other thresholds stay at their defaults (temperature fallback is already on). |
| P9 | One spend entry per paid **generation** request, written by an `on_created` hook the moment Replicate confirms the prediction (provider, model, task id, estimate). Preparation keeps its single "prepare" entry. | Matches the spec's "log when the paid request is created" for the expensive calls; prep stages cost cents and would need job/runs plumbing in every stage (deferred). |
| P10 | `original.mp4` is **not** renamed in 2a | Touches 13 files, invalidates existing approvals (file names are hashed), viewer must stay read-only so it cannot migrate old jobs. Cosmetic; deferred to Plan 3. |
| P11 | No per-job file lock in 2a | Money safety already holds across processes: the exclusive "creating" marker stops a second POST, and a second `generate` on a saved record only polls. The remaining race (prepare vs review) is the Plan 3 run-lock item. |

## Conventions for every task

- Run commands from the repo root: the repo root (the path contains spaces; quote it).
- Tests: `uv run pytest <path> -q --no-cov` while iterating (the coverage gate applies to the full suite); full suite `uv run pytest`.
- Lint/format before every commit: `uv run ruff format src tests && uv run ruff check --fix src tests`.
- Stage files **by name**. Never `git add -A` / `git add .`. No attribution / Co-Authored-By lines in commit messages. Never commit `_legacy/`, `.env`, `runs/`, `models/`, `inputs/`, `.superpowers/`. Frank commits only after his OK: if the session rules say "never auto-commit", stop at the `git add` line and ask.
- Tests never call paid APIs: use `tests/fakes.py` and `respx`. `@pytest.mark.live` tests are not part of this plan.
- The viewer stays GET-only and never changes job state.
- User-facing copy: plain English, no em dashes in "Why this step?" text (test enforced).

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/character_swap/errors.py` | Create | `CharSwapError`: base of every error the CLI prints as a plain message (exit 2) |
| `src/character_swap/providers/errors.py` | Create | `ProviderError`, `OutputExpired` (provider-neutral) |
| `src/character_swap/modes.py` | Create | the one mode table (`MODES`), `MODES_BY_GENERATOR`, `GENERATOR_LIMITS`, `GENERATION_STAGES` |
| `src/character_swap/providers/generators/__init__.py` | Create | package marker |
| `src/character_swap/providers/generators/base.py` | Create | `CharacterRef`, `GenerationRequest`, `Generator` protocol |
| `src/character_swap/providers/generators/wan_replicate.py` | Create | `WanGenerator`: input building, estimate, create/resume, download |
| `src/character_swap/providers/replicate.py` | Modify | errors subclass the neutral ones; polling backoff; safe 429 create retry; `on_created` hook |
| `src/character_swap/stages/__init__.py` | Modify | `StageError` is a `CharSwapError`; expiry detected by type |
| `src/character_swap/stages/transcribe.py` | Modify | Whisper inputs (`language`, `condition_on_previous_text`) |
| `src/character_swap/jobs.py` | Modify | `JobError` base; `language` in state; mode table from `modes` |
| `src/character_swap/config.py`, `review.py`, `prompts.py` | Modify | error bases (`PromptError`); review files from `modes` |
| `src/character_swap/costs.py` | Modify | Wan prices; public `cost_line`; spend-log provider/model/task id columns |
| `src/character_swap/media/audio.py` | Modify | fit ±0.5 s (hold or trim), `copy_video_only`, `add_original_soundtrack` |
| `src/character_swap/pipeline.py` | Modify | tables from `modes`; normalize facts in state; `clip_seconds`; `generate()`, `check_can_generate`, `needs_new_charge`, `next_step` |
| `src/character_swap/cli.py` | Modify | `CharSwapError` handling; `generate` command; `status.next`; `--language`; Wan "next" hint; retry-aware Ctrl-C hint |
| `src/character_swap/web/view_model.py` | Modify | progress `generation` + `poll`; Draft/Final sections, statuses, cost rows, retry command |
| `src/character_swap/web/app.py`, `templates/job.html`, `static/viewer.js` | Modify | `data-poll`, generation-aware change detection, slow polling, retry command in error box |
| `tests/fakes.py`, `tests/viewer_jobs.py` | Modify | `on_created` in the fake; neutral expiry; draft/final artifacts |
| `tests/test_errors.py`, `tests/test_modes.py`, `tests/test_wan.py`, `tests/test_generation.py` | Create | tests for the new modules |
| `tests/test_*.py` (cli, replicate, costs, jobs, text_stages, audio, pipeline, view_model, web_app) | Modify | tests per task |
| `README.md`, `CLAUDE.md`, `activity.md`, `docs/scratchpad.md` | Modify | Wan usage, commands, journal, checklist (Task 14) |
| `docs/research/2026-10-22-provider-api-facts.md` | Modified while planning | verified Wan + Whisper facts |

---

### Task 1: One error base; providers' errors are neutral; no more class-name matching

**Files:** Create `src/character_swap/errors.py`, `src/character_swap/providers/errors.py`, `tests/test_errors.py`. Modify `src/character_swap/providers/replicate.py`, `src/character_swap/stages/__init__.py`, `src/character_swap/jobs.py`, `src/character_swap/config.py`, `src/character_swap/review.py`, `src/character_swap/prompts.py`, `src/character_swap/cli.py`, `tests/fakes.py`, `tests/test_cli.py`.

Today `download_or_fail` recognizes an expired link by the class *name* `ReplicateOutputExpired`, and a `ReplicateError` that escapes a stage reaches the CLI as "Unexpected error" (exit 1). After this task every user-fixable error is a `CharSwapError` (exit 2, message on stderr) and expiry is a type check. Each subclass keeps its old builtin base (`ValueError`/`RuntimeError`) so existing `except`/`pytest.raises` keep working.

- [ ] **Step 1: Failing tests** — create `tests/test_errors.py`:

```python
import pytest

from character_swap.config import MissingSettingError
from character_swap.errors import CharSwapError
from character_swap.jobs import JobError
from character_swap.prompts import PromptError, validate_prompt
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.replicate import ReplicateError, ReplicateOutputExpired
from character_swap.review import ReviewError
from character_swap.stages import StageError, download_or_fail


@pytest.mark.parametrize(
    "cls",
    [MissingSettingError, JobError, PromptError, ProviderError, ReviewError, StageError,
     ReplicateError],
)
def test_user_facing_errors_share_one_base(cls):
    assert issubclass(cls, CharSwapError)


def test_replicate_errors_are_the_neutral_ones():
    assert issubclass(ReplicateError, ProviderError)
    assert issubclass(ReplicateOutputExpired, OutputExpired)
    assert issubclass(ReplicateOutputExpired, ReplicateError)


def test_old_builtin_bases_still_hold():
    assert issubclass(JobError, ValueError) and issubclass(ReviewError, ValueError)
    assert issubclass(StageError, RuntimeError) and issubclass(MissingSettingError, RuntimeError)
    with pytest.raises(ValueError, match="limit is 4000"):
        validate_prompt("x" * 4001)


class SomeProviderExpired(OutputExpired):
    """Any provider's expiry, whatever the class is called."""


class Downloader:
    def __init__(self, error):
        self.error = error

    def download(self, url, dest):
        raise self.error


def test_expiry_is_detected_by_type_not_by_name(tmp_path):
    client = Downloader(SomeProviderExpired("gone"))
    with pytest.raises(StageError, match=r"result expired.*costs ~\$0\.04"):
        download_or_fail(
            client, "u", tmp_path / "f.mp4", record="r.json", what="depth", cost="~$0.04"
        )


def test_other_download_failures_say_no_new_charge(tmp_path):
    client = Downloader(RuntimeError("connection reset"))
    with pytest.raises(StageError, match="no new charge"):
        download_or_fail(client, "u", tmp_path / "f.mp4", record="r.json", what="x", cost="~$1")
```

Append to `tests/test_cli.py`:

```python
def test_provider_errors_are_friendly(settings, video, monkeypatch):
    from character_swap.providers.replicate import ReplicateError

    def throttled(job, services, status):
        raise ReplicateError("Rate limited, nothing was charged; wait a minute and retry.")

    monkeypatch.setattr(cli, "prepare_job", throttled)
    code, out = run(["prepare", str(video), "--yes"], settings, services_factory=lambda s: object())
    assert code == 2 and "Error: Rate limited, nothing was charged" in out.err.text


def test_prompt_errors_are_friendly(settings, video, monkeypatch):
    from character_swap.prompts import PromptError

    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")

    def too_long(**kwargs):
        raise PromptError("Prompt is 5000 characters; the limit is 4000.")

    monkeypatch.setattr(cli, "build_prompt", too_long)
    code, out = run(["prompt", job.id], settings)
    assert code == 2 and "the limit is 4000" in out.err.text
```

- [ ] **Step 2: Run** `uv run pytest tests/test_errors.py tests/test_cli.py -q --no-cov`
Expected: FAIL — `tests/test_errors.py` errors at collection with `ModuleNotFoundError: No module named 'character_swap.errors'`; in `test_cli.py` `test_provider_errors_are_friendly` fails (exit code 1, "Unexpected error") and `test_prompt_errors_are_friendly` fails with `ImportError: cannot import name 'PromptError'`.

- [ ] **Step 3: Implement**

(a) Create `src/character_swap/errors.py`:

```python
"""The base of every error the CLI shows as a plain message (exit code 2)."""


class CharSwapError(Exception):
    """Something the user can fix; the message says what to do."""
```

(b) Create `src/character_swap/providers/errors.py`:

```python
"""Provider-neutral errors, so stages never depend on one provider's classes."""

from __future__ import annotations

from character_swap.errors import CharSwapError


class ProviderError(CharSwapError, RuntimeError):
    """A provider rejected, failed or has not finished a request."""


class OutputExpired(ProviderError):
    """A finished result's download link is gone; only a new (paid) run recovers it."""
```

(c) `src/character_swap/providers/replicate.py` — add the import below `import httpx` (after a blank line, first-party block):

```python
from character_swap.providers.errors import OutputExpired, ProviderError
```

and replace the two class definitions:

```python
class ReplicateError(ProviderError):
    """Replicate rejected, failed or has not finished a prediction."""


class ReplicateOutputExpired(ReplicateError, OutputExpired):
    """The finished prediction's output link is gone; only a new (paid) run recovers it."""
```

(d) `src/character_swap/stages/__init__.py` — replace the whole file:

```python
"""Pipeline steps. Each module does one step and receives its provider client."""

from character_swap.errors import CharSwapError
from character_swap.providers.errors import OutputExpired


class StageError(CharSwapError, RuntimeError):
    """A pipeline step could not complete; the message says what to do."""


def download_or_fail(replicate, url, dest, *, record, what: str, cost: str):
    """Download a result; on failure say exactly which record to delete to regenerate it."""
    try:
        return replicate.download(url, dest)
    except OutputExpired as exc:
        raise StageError(
            f"The {what} result expired ({exc}). Delete {record} and resume "
            f"to regenerate it (costs {cost})."
        ) from exc
    except Exception as exc:
        raise StageError(
            f"Could not download the {what} result ({exc}). Resume the job to retry; no new charge."
        ) from exc


def bad_output(record, what: str, output) -> StageError:
    return StageError(
        f"{what} returned an unexpected output: {output!r}. "
        f"Delete {record} and resume to regenerate it."
    )
```

(e) `src/character_swap/jobs.py` — add `from character_swap.errors import CharSwapError` after the stdlib imports (own block) and change the class line to `class JobError(CharSwapError, ValueError):`.

(f) `src/character_swap/config.py` — add `from character_swap.errors import CharSwapError` after the stdlib imports and change to `class MissingSettingError(CharSwapError, RuntimeError):`.

(g) `src/character_swap/review.py` — add `from character_swap.errors import CharSwapError` next to the `jobs` import and change to `class ReviewError(CharSwapError, ValueError):`.

(h) `src/character_swap/prompts.py` — after `from __future__ import annotations` add:

```python

from character_swap.errors import CharSwapError
```

below `SECONDARY = ...` add:

```python


class PromptError(CharSwapError, ValueError):
    """The prompt cannot be sent as it is (for example it is too long)."""
```

and in `validate_prompt` raise `PromptError(...)` instead of `ValueError(...)` (same message).

(i) `src/character_swap/cli.py`:
- replace `from character_swap.config import MissingSettingError, Settings` with `from character_swap.config import Settings` and add `from character_swap.errors import CharSwapError` below it;
- replace `from character_swap.review import ReviewError, decide` with `from character_swap.review import decide`;
- in `main()` replace `except (JobError, ReviewError, StageError, MissingSettingError) as error:` with `except CharSwapError as error:`.
(`JobError` and `StageError` stay imported: `_prepare`, `_prompt` and `_repair` raise them.)

(j) `tests/fakes.py` — replace the stand-in class:

```python
from character_swap.providers.errors import OutputExpired


class ReplicateOutputExpired(OutputExpired):
    """Stands in for the real client's expiry error."""
```

(put the import with the other imports at the top of the file).

- [ ] **Step 4: Run** `uv run pytest tests/test_errors.py tests/test_cli.py tests/test_audio_stages.py tests/test_vision_stages.py tests/test_prompts.py tests/test_replicate.py -q --no-cov`
Expected: PASS (the existing expiry tests in `test_audio_stages.py`/`test_vision_stages.py` now pass through the type check).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/errors.py src/character_swap/providers/errors.py src/character_swap/providers/replicate.py src/character_swap/stages/__init__.py src/character_swap/jobs.py src/character_swap/config.py src/character_swap/review.py src/character_swap/prompts.py src/character_swap/cli.py tests/fakes.py tests/test_errors.py tests/test_cli.py
git commit -m "refactor: add CharSwapError base and provider-neutral errors"
```

---

### Task 2: `modes.py`, the one mode table

**Files:** Create `src/character_swap/modes.py`, `tests/test_modes.py`. Modify `src/character_swap/pipeline.py`, `src/character_swap/costs.py`, `src/character_swap/review.py`, `src/character_swap/jobs.py`.

Four tables describe modes today (`jobs.MODES_BY_GENERATOR`, `pipeline.PREP_STAGES_BY_MODE`/`PAID_STAGES`/`GENERATOR_LIMITS`, `costs.PREP_STEPS`, `review.DEPTH/MESH/DEPTH_MESH`). They become views of one table. The old names stay (derived) so callers and tests don't churn. The table also says which modes can generate in this version and holds `GENERATION_STAGES`, kept apart from the prep stages.

- [ ] **Step 1: Failing test** — create `tests/test_modes.py`:

```python
from character_swap import costs, jobs, pipeline, review
from character_swap.jobs import Job
from character_swap.modes import GENERATION_STAGES, GENERATOR_LIMITS, MODES, MODES_BY_GENERATOR


def test_every_derived_table_reads_the_one_mode_table():
    for name, mode in MODES.items():
        assert pipeline.PREP_STAGES_BY_MODE[name] == mode.prep_stages
        assert pipeline.PAID_STAGES[name] == list(mode.paid_stages)
        assert costs.PREP_STEPS[name] == [costs.STAGE_PRICE[s] for s in mode.paid_stages]
    assert jobs.MODES_BY_GENERATOR is MODES_BY_GENERATOR
    assert pipeline.GENERATOR_LIMITS is GENERATOR_LIMITS


def test_modes_by_generator_keeps_the_cli_order():
    assert MODES_BY_GENERATOR == {
        "seedance": ("depth", "face_mesh", "depth_mesh"),
        "wan": ("direct",),
    }


def test_paid_stages_are_prep_stages_and_generation_is_separate():
    assert GENERATION_STAGES == ("draft", "final")
    for mode in MODES.values():
        assert mode.prep_stages[0] == "normalize"
        assert set(mode.paid_stages) <= set(mode.prep_stages)
        assert not set(GENERATION_STAGES) & set(mode.prep_stages)


def test_only_wan_generates_in_this_version():
    assert [name for name, mode in MODES.items() if mode.generates] == ["direct"]
    assert MODES["direct"].generator == "wan"


def test_review_files_come_from_the_table(runs_dir, tmp_path):
    video, face = tmp_path / "in.mp4", tmp_path / "me.png"
    video.write_bytes(b"v")
    face.write_bytes(b"i")
    mesh = Job.create(runs_dir, video, generator="seedance", mode="depth_mesh")
    assert review.review_files(mesh) == list(MODES["depth_mesh"].review_files)
    wan = Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert review.review_files(wan) == ["original.mp4", "character-primary.png"]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_modes.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'character_swap.modes'`.

- [ ] **Step 3: Implement**

(a) Create `src/character_swap/modes.py`:

```python
"""The one table of modes: generator, prep stages, paid stages and what review hashes."""

from __future__ import annotations

from dataclasses import dataclass

# Paid generation runs after review. It is tracked apart from preparation so a prepared but
# not yet generated job never looks "running".
GENERATION_STAGES: tuple[str, ...] = ("draft", "final")

# (min_seconds, max_seconds) per generator; see docs/research/2026-10-22-provider-api-facts.md.
GENERATOR_LIMITS: dict[str, tuple[int, int]] = {"seedance": (4, 30), "wan": (1, 30)}


@dataclass(frozen=True)
class Mode:
    name: str
    generator: str
    prep_stages: tuple[str, ...]  # exactly what pipeline.prepare() runs, in order
    paid_stages: tuple[str, ...]  # prep stages that call a paid provider
    review_files: tuple[str, ...]  # hashed by the review gate (Wan adds the character image)
    uses_depth: bool
    uses_mesh: bool
    generates: bool  # draft/final available in this version (Seedance arrives in Plan 2b)


_TEXT = ("separate", "transcribe", "describe")
_DEPTH = ("depth", "masks")

MODES: dict[str, Mode] = {
    "depth": Mode(
        name="depth",
        generator="seedance",
        prep_stages=(
            "normalize", "separate", "pitch", "transcribe", "describe",
            "depth", "masks", "composite", "embed",
        ),
        paid_stages=(*_TEXT, *_DEPTH),
        review_files=(
            "original.mp4", "depth.mp4", "mask.mp4", "composite-silent.mp4", "prepared.mp4",
        ),
        uses_depth=True,
        uses_mesh=False,
        generates=False,
    ),
    "face_mesh": Mode(
        name="face_mesh",
        generator="seedance",
        prep_stages=(
            "normalize", "separate", "pitch", "transcribe", "describe", "face_mesh", "embed",
        ),
        paid_stages=_TEXT,
        review_files=(
            "original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4",
        ),
        uses_depth=False,
        uses_mesh=True,
        generates=False,
    ),
    "depth_mesh": Mode(
        name="depth_mesh",
        generator="seedance",
        prep_stages=(
            "normalize", "separate", "pitch", "transcribe", "describe",
            "depth", "masks", "composite", "face_mesh", "embed",
        ),
        paid_stages=(*_TEXT, *_DEPTH),
        review_files=(
            "original.mp4", "depth.mp4", "mask.mp4", "depth-composite-silent.mp4",
            "face-landmarks.json", "composite-silent.mp4", "prepared.mp4",
        ),
        uses_depth=True,
        uses_mesh=True,
        generates=False,
    ),
    "direct": Mode(
        name="direct",
        generator="wan",
        prep_stages=("normalize",),
        paid_stages=(),
        review_files=("original.mp4",),
        uses_depth=False,
        uses_mesh=False,
        generates=True,
    ),
}

MODES_BY_GENERATOR: dict[str, tuple[str, ...]] = {
    generator: tuple(name for name, mode in MODES.items() if mode.generator == generator)
    for generator in GENERATOR_LIMITS
}
```

(`ruff format` will put the tuple items one per line; that is fine.)

(b) `src/character_swap/jobs.py` — delete the line `MODES_BY_GENERATOR = {"seedance": ("depth", "face_mesh", "depth_mesh"), "wan": ("direct",)}` and add `from character_swap.modes import MODES_BY_GENERATOR` next to the `errors` import.

(c) `src/character_swap/pipeline.py`:
- add `from character_swap.modes import GENERATOR_LIMITS, MODES` to the first-party imports;
- delete the `GENERATOR_LIMITS = ...` line with its comment, the `PAID_STAGES = {...}` literal and the `PREP_STAGES_BY_MODE` literal with its two comment lines; in their place (after `Status = Callable[[str], None]`) put:

```python
# Derived from modes.MODES (the one table); kept under these names for callers and tests.
PAID_STAGES: dict[str, list[str]] = {name: list(mode.paid_stages) for name, mode in MODES.items()}
PREP_STAGES_BY_MODE: dict[str, tuple[str, ...]] = {
    name: mode.prep_stages for name, mode in MODES.items()
}
```

- in `prepare()` replace `if job.mode in ("depth", "depth_mesh"):` with `if MODES[job.mode].uses_depth:` and `if job.mode in ("face_mesh", "depth_mesh"):` with `if MODES[job.mode].uses_mesh:`;
- in `repair_with_rgb()` replace `if job.mode not in ("depth", "depth_mesh"):` with `if not MODES[job.mode].uses_depth:`.

(d) `src/character_swap/costs.py` — add `from character_swap.modes import MODES` below the `jobs` import and replace the `PREP_STEPS = {...}` literal with:

```python
PREP_STEPS: dict[str, list[str]] = {
    name: [STAGE_PRICE[stage] for stage in mode.paid_stages] for name, mode in MODES.items()
}
```

(e) `src/character_swap/review.py` — delete the `DEPTH`, `MESH` and `DEPTH_MESH` lists, add `from character_swap.modes import MODES`, and replace `review_files` and `_preparation_finished`:

```python
def review_files(job: Job) -> list[str]:
    mode = MODES.get(job.mode)
    if mode is None:
        raise ReviewError(f"Unknown mode {job.mode!r}; cannot review.")
    files = list(mode.review_files)
    if mode.generator == "wan":  # Wan receives the character image, so it is reviewed too
        character = job.state.get("character_primary")
        if not character:
            raise ReviewError("This job has no character image to review.")
        files.append(character)
    return files


def _preparation_finished(job: Job) -> bool:
    """True when every stage of the job's mode is done (unknown modes fail in review_files)."""
    mode = MODES.get(job.mode)
    return mode is None or all(job.stage_done(name) for name in mode.prep_stages)
```

The file order and names are unchanged, so existing fingerprints (and `test_fingerprint_hash_format_is_pinned`) stay identical.

- [ ] **Step 4: Run** `uv run pytest tests/test_modes.py tests/test_review.py tests/test_pipeline.py tests/test_costs.py tests/test_jobs.py tests/test_cli.py tests/test_view_model.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/modes.py src/character_swap/pipeline.py src/character_swap/costs.py src/character_swap/review.py src/character_swap/jobs.py tests/test_modes.py
git commit -m "refactor: derive every mode table from modes.MODES"
```

---

### Task 3: Save the normalized clip's facts in state.json

**Files:** Modify `src/character_swap/pipeline.py`, `tests/test_pipeline.py`.

`normalize()` already returns `{"frames", "fps", "width", "height", "seconds"}` and the pipeline throws it away. Wan is billed per output second, so the estimate needs the real length. `clip_seconds(job)` reads the saved value and measures the file for jobs made before this change.

- [ ] **Step 1: Failing tests** — append to `tests/test_pipeline.py` (`_direct_job` already exists in this file):

```python
# ---- normalize facts (Plan 2a) ---------------------------------------------------
def test_normalize_saves_the_clip_facts(make_job, services, tmp_path):
    job = _direct_job(make_job, tmp_path)
    prepare(job, services, status=lambda _m: None)
    assert job.state["video"] == {
        "frames": 96,
        "fps": 24,
        "width": 64,
        "height": 64,
        "seconds": 4.0,
    }
    assert pipeline.clip_seconds(job) == 4.0


def test_clip_seconds_measures_jobs_without_saved_facts(make_job, services, tmp_path):
    job = _direct_job(make_job, tmp_path)
    prepare(job, services, status=lambda _m: None)
    job.update(video=None)
    assert pipeline.clip_seconds(job) == pytest.approx(4.0, abs=0.05)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py -q --no-cov -k "facts or clip_seconds"`
Expected: FAIL — `KeyError: 'video'` and `AttributeError: module 'character_swap.pipeline' has no attribute 'clip_seconds'`.

- [ ] **Step 3: Implement** in `src/character_swap/pipeline.py`:

- change the media import to `from character_swap.media.video import normalize, stream_seconds`;
- in `prepare()` replace the normalize lambda with:

```python
        lambda: job.update(
            video=normalize(
                job.path("upload.mp4"), job.path("original.mp4"), min_seconds=low, max_seconds=high
            )
        ),
```

- add below `remaining_paid_stages`:

```python
def clip_seconds(job: Job) -> float:
    """Length of the normalized clip: saved by normalize, measured for older jobs."""
    facts = job.state.get("video")
    if isinstance(facts, dict) and isinstance(facts.get("seconds"), int | float):
        return float(facts["seconds"])
    return stream_seconds(job.path("original.mp4"), "video")
```

- [ ] **Step 4: Run** `uv run pytest tests/test_pipeline.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/pipeline.py tests/test_pipeline.py
git commit -m "feat: keep normalized clip facts in state.json"
```

---

### Task 4: Replicate polling backs off (5 s → 30 s)

**Files:** Modify `src/character_swap/providers/replicate.py`, `tests/test_replicate.py`.

- [ ] **Step 1: Failing test** — append to `tests/test_replicate.py`:

```python
# ---- rate limits (first live run) -----------------------------------------------
@respx.mock
def test_polling_backs_off_to_a_ceiling(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing"}))
    running = [httpx.Response(200, json={"id": "p1", "status": "processing"}) for _ in range(5)]
    done = httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 1})
    respx.get(f"{API}/predictions/p1").mock(side_effect=[*running, done])
    sleeps = []
    slow = ReplicateClient("tok", poll_seconds=5, max_poll_seconds=20, sleep=sleeps.append)
    assert slow.run(version="v1", input={}, record=record) == 1
    assert sleeps == [5, 7.5, 11.25, 16.875, 20, 20]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_replicate.py -q --no-cov -k backs_off`
Expected: FAIL — `TypeError: ReplicateClient.__init__() got an unexpected keyword argument 'max_poll_seconds'`.

- [ ] **Step 3: Implement** in `src/character_swap/providers/replicate.py`:

- below `TERMINAL = {...}` add:

```python
POLL_GROWTH = 1.5  # each wait is 1.5x the last, up to max_poll_seconds
```

- `__init__` gets `max_poll_seconds: float = 30.0,` after `poll_seconds: float = 5.0,` and stores `self._max_poll_seconds = max_poll_seconds`;
- in `_wait()` replace `polls = 0` with:

```python
        polls, delay = 0, self._poll_seconds
```

  and replace `self._sleep(self._poll_seconds)` with:

```python
            # Back off: low-balance accounts get ~6 requests/min, and a fixed 5 s poll
            # starved the next paid create of its quota (first live run).
            self._sleep(delay)
            delay = min(delay * POLL_GROWTH, self._max_poll_seconds)
```

`max_polls=720` now covers about 6 hours of waiting.

- [ ] **Step 4: Run** `uv run pytest tests/test_replicate.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/replicate.py tests/test_replicate.py
git commit -m "fix: back off Replicate polling to stay under low-balance rate limits"
```

---

### Task 5: A 429 on the paid create waits and retries, and can never cause a second POST

**Files:** Modify `src/character_swap/providers/replicate.py`, `tests/test_replicate.py`, `CLAUDE.md`.

Why this is safe (decision P6): Replicate returns 429 when it throttles a request *before* doing anything with it. No prediction exists and nothing is billed. So on a 429 we **give up our "creating" marker** (it is ours, and nothing was created), sleep, and then **re-claim it with the same exclusive create** (`open(record, "x")`) before posting again. If the claim fails, another run started meanwhile (or a record now exists): we stop **without posting** and say so. This way a run that is waiting on a rate limit never holds a marker that someone could be told to delete, and two runs can never both POST. Only 429 repeats; 2xx, other 4xx, 5xx, 408, timeouts and unreadable JSON follow exactly today's rules (5xx/408/timeout/bad JSON keep the marker and say "may have been charged"). Ctrl-C during the wait leaves nothing behind (the marker was already released).

The "unconfirmed" message also changes: before telling the user to delete a marker it now asks them to make sure no other charswap command is running for the job.

- [ ] **Step 1: Failing tests** — append to `tests/test_replicate.py`:

```python
@respx.mock
def test_create_429_waits_and_retries(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429),
            httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 3}),
        ]
    )
    sleeps = []
    patient = ReplicateClient("tok", poll_seconds=0, sleep=sleeps.append)
    assert patient.run(version="v1", input={}, record=record) == 3
    assert route.call_count == 3 and sleeps == [7.0, 10.0]
    assert json.loads(record.read_text())["id"] == "p"


@respx.mock
def test_marker_is_released_while_waiting_on_429(tmp_path):
    record = tmp_path / "r.json"
    seen = []
    respx.post(f"{API}/predictions").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 1}),
        ]
    )

    def sleep(_seconds):
        seen.append(record.exists())

    ReplicateClient("tok", poll_seconds=0, sleep=sleep).run(version="v1", input={}, record=record)
    assert seen == [False]


@respx.mock
@pytest.mark.parametrize(
    "other", [{"status": "creating", "created_at": "x"}, {"id": "p2", "status": "processing"}]
)
def test_run_started_elsewhere_during_the_wait_is_never_posted_twice(tmp_path, other):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(429))

    def another_run_claims_it(_seconds):
        record.write_text(json.dumps(other))

    with pytest.raises(ReplicateError, match="Another charswap run"):
        ReplicateClient("tok", poll_seconds=0, sleep=another_run_claims_it).run(
            version="v1", input={}, record=record
        )
    assert route.call_count == 1
    assert json.loads(record.read_text()) == other  # the other run's marker/record is untouched


@respx.mock
def test_create_429_gives_up_after_three_retries(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "999"})
    )
    sleeps = []
    with pytest.raises(ReplicateError, match="Rate limited, nothing was charged"):
        ReplicateClient("tok", poll_seconds=0, sleep=sleeps.append).run(
            version="v1", input={}, record=record
        )
    assert route.call_count == 4 and sleeps == [60.0, 60.0, 60.0]
    assert not record.exists()


@respx.mock
def test_ctrl_c_while_waiting_on_429_leaves_no_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(429))

    def stop(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ReplicateClient("tok", sleep=stop).run(version="v1", input={}, record=record)
    assert not record.exists()


@respx.mock
def test_5xx_after_a_429_still_keeps_the_marker(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        side_effect=[httpx.Response(429), httpx.Response(502)]
    )
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 2
    assert json.loads(record.read_text())["status"] == "creating"


def test_unconfirmed_message_warns_about_other_runs(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "created_at": "x"}))
    with pytest.raises(ReplicateError, match="no other charswap command is running"):
        client().run(version="v1", input={}, record=record)


@pytest.mark.parametrize(("header", "wait"), [("", 10.0), ("abc", 10.0), ("0", 1.0), ("nan", 10.0)])
def test_retry_after_parsing(header, wait):
    from character_swap.providers.replicate import _retry_after

    assert _retry_after(httpx.Response(429, headers={"Retry-After": header})) == wait
```

- [ ] **Step 2: Run** `uv run pytest tests/test_replicate.py -q --no-cov -k "429 or retry_after or other_runs or elsewhere"`
Expected: FAIL — the first create test raises `ReplicateError: Rate limited...` after one POST; the "elsewhere" and "other runs" messages don't exist; `_retry_after` cannot be imported. (The 5xx test may already pass.)

- [ ] **Step 3: Implement** in `src/character_swap/providers/replicate.py`:

- add `import math` to the stdlib imports;
- below `POLL_GROWTH` add:

```python
CREATE_429_RETRIES = 3  # a throttled create (429) was refused before anything was created
RATE_LIMIT_WAIT = 10.0  # seconds, when Retry-After is missing or unreadable
MAX_RATE_LIMIT_WAIT = 60.0
```

- move the marker writing out of `_create()` into a static method, so the retry can re-claim it the same way. In `_create()` replace

```python
        record.parent.mkdir(parents=True, exist_ok=True)
        marker = {"status": "creating", "created_at": datetime.now(UTC).isoformat()}
        try:
            with open(record, "x") as file:  # exclusive: a second runner cannot also pay
                try:
                    json.dump(marker, file)
                except BaseException:
                    file.close()
                    record.unlink(missing_ok=True)  # POST was never sent
                    raise
        except FileExistsError as error:
            raise ReplicateError(_unconfirmed(record)) from error
        try:
            response = self._http.post(url, headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise ReplicateError(_unconfirmed(record)) from error
        code = response.status_code
```

  with

```python
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._claim(record)
        except FileExistsError as error:
            raise ReplicateError(_unconfirmed(record)) from error
        response = self._post_create(url, body, record)
        code = response.status_code
```

- add these two methods directly below `_create()`:

```python
    @staticmethod
    def _claim(record: Path) -> None:
        """Write the "creating" marker exclusively; FileExistsError if anyone else has it."""
        marker = {"status": "creating", "created_at": datetime.now(UTC).isoformat()}
        with open(record, "x") as file:  # exclusive: a second runner cannot also pay
            try:
                json.dump(marker, file)
            except BaseException:
                file.close()
                record.unlink(missing_ok=True)  # POST was never sent
                raise

    def _post_create(self, url: str, body: dict, record: Path) -> httpx.Response:
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
                raise ReplicateError(_unconfirmed(record)) from error
            if response.status_code != 429 or attempt == CREATE_429_RETRIES:
                return response
            record.unlink(missing_ok=True)  # ours, and the 429 means nothing was created
            self._sleep(_retry_after(response))
            try:
                self._claim(record)
            except FileExistsError as error:
                raise ReplicateError(
                    f"Another charswap run started this request while this one waited for "
                    f"Replicate's rate limit; this run sent nothing more. Let the other run "
                    f"finish, or run the command again to resume what it saved in {record}."
                ) from error
        raise AssertionError("unreachable")
```

- replace `_unconfirmed` and add `_retry_after` next to it:

```python
def _unconfirmed(record: Path) -> str:
    return (
        "The request was sent but not confirmed — it may have been charged, or another "
        "charswap command for this job is still sending it. First make sure no other "
        "charswap command is running for this job. Then check https://replicate.com/predictions; "
        f"if nothing is there, delete {record} and run again."
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
```

A final 429 still reaches the existing 4xx branch in `_create()` (we re-claimed the marker before that last POST): the marker is deleted and `_rejection(429)` says "Rate limited, nothing was charged; wait a minute and retry." The existing tests that match `"may have been charged"` still pass with the new wording; `test_marker_write_failure_removes_marker` (patches `replicate.json.dump`) and `test_marker_race_blocks_post` keep working because `_claim` keeps the same `open(record, "x")` + cleanup behavior.

- update the first hard rule in `CLAUDE.md` to:

```markdown
- Never auto-retry a paid request. Only exception: a create rejected with 429 (Replicate throttled it, nothing was created) is retried after re-claiming the exclusive "creating" marker. Persist provider IDs before waiting.
```

- [ ] **Step 4: Run** `uv run pytest tests/test_replicate.py -q --no-cov` → PASS (all the existing double-charge tests included).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/replicate.py tests/test_replicate.py CLAUDE.md
git commit -m "fix: retry a throttled (429) paid create without ever posting twice"
```

---

### Task 6: Log a spend entry the moment a paid prediction exists (`on_created`)

**Files:** Modify `src/character_swap/providers/replicate.py`, `src/character_swap/costs.py`, `src/character_swap/pipeline.py` (protocol only), `tests/fakes.py`, `tests/test_replicate.py`, `tests/test_costs.py`.

`ReplicateClient.run()` gets an optional `on_created(prediction)` callback, called once, right after a **new** prediction is confirmed and its record saved (never on resume, so a resumed run is never logged twice). A failing callback only warns: losing a log line must never lose track of a paid prediction. `record_spend()` gains `provider`, `model`, `task_id` and `units` (spec §7: e.g. `"8.0s"` of output video, or `"1 run"`); the CSV gets four new columns **at the end**, so `total_spend()` (which reads `usd_micro` by name) keeps adding up old logs whose header has only five columns.

- [ ] **Step 1: Failing tests** — append to `tests/test_replicate.py`:

```python
# ---- spend hook ------------------------------------------------------------------
@respx.mock
def test_on_created_runs_once_for_a_new_prediction_only(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p9", "status": "succeeded", "output": 1})
    )
    seen = []
    client().run(version="v1", input={}, record=record, on_created=seen.append)
    client().run(version="v1", input={}, record=record, on_created=seen.append)  # resumed
    assert [prediction["id"] for prediction in seen] == ["p9"]


@respx.mock
def test_on_created_failure_never_loses_the_prediction(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p9", "status": "succeeded", "output": 1})
    )

    def broken(_prediction):
        raise OSError("disk full")

    with pytest.warns(UserWarning, match="p9"):
        assert client().run(version="v1", input={}, record=record, on_created=broken) == 1
    assert json.loads(record.read_text())["id"] == "p9"
```

Append to `tests/test_costs.py`:

```python
def test_spend_entry_names_provider_model_and_task(runs_dir, tmp_path):
    from character_swap.costs import Line

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(
        job,
        "draft",
        Estimate(lines=(Line("Wan draft (480p)", 0.1),)),
        runs_dir,
        provider="replicate",
        model="wan-video/wan-2.2-animate-replace",
        task_id="p9",
        units="8.0s",
    )
    row = next(csv.DictReader((runs_dir / "spend-log.csv").open()))
    assert (row["provider"], row["model"], row["task_id"], row["units"]) == (
        "replicate",
        "wan-video/wan-2.2-animate-replace",
        "p9",
        "8.0s",
    )
    entry = job.state["costs"][-1]
    assert entry["step"] == "draft" and entry["task_id"] == "p9" and entry["usd"] == 0.1
    assert entry["units"] == "8.0s"


def test_old_five_column_spend_logs_keep_adding_up(runs_dir, tmp_path):
    from character_swap.costs import Line

    (runs_dir / "spend-log.csv").write_text(
        "at,job,step,usd_estimate,usd_micro\n2026-11-02T10:00:00+00:00,j,prepare,0.0600,60000\n"
    )
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(job, "draft", Estimate(lines=(Line("x", 0.1),)), runs_dir, task_id="p9")
    assert total_spend(runs_dir) == pytest.approx(0.16)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_replicate.py tests/test_costs.py -q --no-cov -k "on_created or spend_entry or five_column"`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'on_created'` / `'provider'` / `'task_id'` / `'units'`.

- [ ] **Step 3: Implement**

(a) `src/character_swap/providers/replicate.py`:
- add `import warnings` to the stdlib imports;
- change the `run` signature and body's create branch:

```python
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
        `on_created(prediction)` runs once, right after a NEW prediction is saved.
        """
```

  and replace `prediction = self._create(input=input, version=version, model=model, record=record)` with:

```python
            prediction = self._create(input=input, version=version, model=model, record=record)
            if on_created is not None:
                _announce(on_created, prediction)
```

- add next to `_unconfirmed`:

```python
def _announce(callback: Callable[[dict], None], prediction: dict) -> None:
    """Report a new paid prediction; a failing logger must never lose track of it."""
    try:
        callback(prediction)
    except Exception as exc:
        warnings.warn(
            f"Could not log paid prediction {prediction.get('id')}: {exc}", stacklevel=3
        )
```

(b) `src/character_swap/costs.py` — replace `record_spend`:

```python
# New columns go at the end: old logs keep their 5-column header and still add up.
SPEND_LOG_COLUMNS = (
    "at", "job", "step", "usd_estimate", "usd_micro", "provider", "model", "task_id", "units",
)


def record_spend(
    job: Job,
    step: str,
    estimate: Estimate,
    runs_dir: Path,
    *,
    provider: str = "",
    model: str = "",
    task_id: str = "",
    units: str = "",
) -> None:
    at = datetime.now(UTC).isoformat(timespec="seconds")
    entry: dict[str, object] = {"step": step, "usd": estimate.total, "at": at}
    details = {"provider": provider, "model": model, "task_id": task_id, "units": units}
    entry.update({key: value for key, value in details.items() if value})
    job.append_cost(entry)
    log = Path(runs_dir) / "spend-log.csv"
    with _LOG_LOCK:
        new = not log.exists() or log.stat().st_size == 0
        with log.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if new:
                writer.writerow(SPEND_LOG_COLUMNS)
            writer.writerow(
                [
                    at,
                    job.id,
                    step,
                    f"{estimate.total:.4f}",
                    round(estimate.total * 1_000_000),
                    provider,
                    model,
                    task_id,
                    units,
                ]
            )
```

(c) `src/character_swap/pipeline.py` — in `ReplicateLike` replace the `run` stub with:

```python
    def run(
        self,
        *,
        input: dict,
        record: Path,
        version: str | None = None,
        model: str | None = None,
        on_created: Callable[[dict], None] | None = None,
    ): ...
```

(d) `tests/fakes.py` — replace `FakeReplicate.run`:

```python
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
```

- [ ] **Step 4: Run** `uv run pytest tests/test_replicate.py tests/test_costs.py tests/test_cli.py tests/test_view_model.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/replicate.py src/character_swap/costs.py src/character_swap/pipeline.py tests/fakes.py tests/test_replicate.py tests/test_costs.py
git commit -m "feat: log each paid prediction with provider, model and task id when created"
```

---

### Task 7: Whisper keeps transcribing (`condition_on_previous_text: false`, job language)

**Files:** Modify `src/character_swap/stages/transcribe.py`, `src/character_swap/jobs.py`, `src/character_swap/cli.py`, `tests/test_text_stages.py`, `tests/test_jobs.py`, `tests/test_cli.py`.

- [ ] **Step 1: Failing tests**

In `tests/test_text_stages.py` change the expected input in `test_transcribe_saves_text` to:

```python
    assert fake.runs[0]["input"] == {
        "audio": "https://fake.replicate/vocals.wav",
        "transcription": "plain text",
        "language": "auto",
        "condition_on_previous_text": False,
    }
```

and append:

```python
def test_transcribe_uses_the_job_language(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    job.update(language="es")
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": "hola"}})
    transcribe(job, fake)
    assert fake.runs[0]["input"]["language"] == "es"


def test_jobs_from_before_the_language_option_use_auto(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    job.update(language=None)
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": "hi"}})
    transcribe(job, fake)
    assert fake.runs[0]["input"]["language"] == "auto"
```

Append to `tests/test_jobs.py`:

```python
def test_language_is_saved_and_checked(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth", language="en")
    assert job.state["language"] == "en"
    default = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert default.state["language"] == "auto"
    with pytest.raises(JobError, match="language code"):
        Job.create(runs_dir, video, generator="seedance", mode="depth", language="English please")
```

Append to `tests/test_cli.py`:

```python
def test_prepare_passes_the_language(settings, video, monkeypatch):
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: None)
    code, _ = run(
        ["prepare", str(video), "--mode", "depth", "--language", "en", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    job_id = next(p.name for p in settings.runs_dir.iterdir() if p.is_dir())
    assert code == 0 and Job.load(settings.runs_dir, job_id).state["language"] == "en"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_text_stages.py tests/test_jobs.py tests/test_cli.py -q --no-cov -k "transcribe or language"`
Expected: FAIL — input dict mismatch; `TypeError: Job.create() got an unexpected keyword argument 'language'`; `test_prepare_passes_the_language` errors with `SystemExit: 2` raised by argparse (`unrecognized arguments: --language en`); the `run()` helper in `tests/test_cli.py` does not catch `SystemExit`.

- [ ] **Step 3: Implement**

(a) `src/character_swap/stages/transcribe.py` — replace the `replicate.run(...)` call:

```python
    output = replicate.run(
        version=WHISPER_VERSION,
        input=whisper_input(url, job.state.get("language")),
        record=record,
    )
```

and add above `transcribe`:

```python
def whisper_input(url: str, language: str | None) -> dict:
    return {
        "audio": url,
        "transcription": "plain text",
        "language": language or "auto",
        # On by default: one misheard window (rap over a beat) made Whisper skip the rest
        # of the first live clip ("Let's get it." only). Off, the lyrics came through.
        "condition_on_previous_text": False,
    }
```

(b) `src/character_swap/jobs.py`:
- below `_JOB_ID = ...` add `_LANGUAGE = re.compile(r"auto|[a-z]{2,3}")  # Whisper codes: en, es, haw, ...`;
- `Job.create` gets a keyword parameter `language: str = "auto",` after `character: Path | None = None,`;
- after the `character ... is_file()` check add:

```python
        if not _LANGUAGE.fullmatch(language):
            raise JobError(
                f"{language!r} is not a Whisper language code (for example en, es, or auto)."
            )
```

- add `"language": language,` to the `state` dict after `"depth_provider": depth_provider,`.

(c) `src/character_swap/cli.py`:
- in `_parser()` after the `--character` line add:

```python
    prep.add_argument(
        "--language",
        default="auto",
        help="spoken language code for the transcript, e.g. en (default: detect it)",
    )
```

- in `_prepare()` pass `language=args.language,` to `Job.create(...)` after `character=args.character,`.

- [ ] **Step 4: Run** `uv run pytest tests/test_text_stages.py tests/test_jobs.py tests/test_cli.py tests/test_pipeline.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/stages/transcribe.py src/character_swap/jobs.py src/character_swap/cli.py tests/test_text_stages.py tests/test_jobs.py tests/test_cli.py
git commit -m "fix: stop Whisper skipping lyrics; add --language (default auto)"
```

---

### Task 8: Soundtrack restore for Wan output (30 fps, ±0.5 s, silent clips)

**Files:** Modify `src/character_swap/media/audio.py`, `tests/test_audio.py`.

Rules (decision P5): the soundtrack source is `upload.mp4` (never modified; `original.mp4` is silent after normalize). The generated video keeps its own frame rate (Wan: 30 fps). If its length differs from the original audio by more than 0.25 s and at most 0.5 s, it is re-encoded to the audio's length: the last frame is held when it is short, extra frames at the end are dropped when it is long. More than 0.5 s → `DurationMismatchError` (never trim, loop or stretch the soundtrack). An upload without audio gives a silent result (Wan's own audio is never kept).

- [ ] **Step 1: Failing tests** — in `tests/test_audio.py` change the import block to:

```python
from character_swap.media.audio import (
    DurationMismatchError,
    NoAudioError,
    add_original_soundtrack,
    copy_video_only,
    embed_samples,
    read_audio,
    restore_original_audio,
    write_wav,
)
```

and append:

```python
# ---- Wan output (Plan 2a) ----------------------------------------------------------
def video_rate(path):
    with av.open(str(path)) as container:
        return round(float(container.streams.video[0].average_rate))


def test_restore_trims_a_slightly_long_generated_video(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 40, fps=30)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original)
    expected = round(stream_seconds(original, "audio") * 30)
    assert abs(count_frames(result) - expected) <= 1 and video_rate(result) == 30


def test_restore_refuses_a_much_longer_generated_video(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 60, fps=30)
    with pytest.raises(DurationMismatchError):
        restore_original_audio(generated, original, tmp_path / "final.mp4")


def test_wan_like_30fps_output_keeps_its_rate_and_the_original_audio(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 48, audio_seconds=2)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 60, fps=30)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original) and video_rate(result) == 30


def test_soundtrack_replaces_the_generated_audio(tmp_path):
    upload = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(
        tmp_path / "gen.mp4", [solid((150, 150, 150))] * 30, fps=30, audio_seconds=0.5
    )
    result = add_original_soundtrack(generated, upload, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(upload)
    with av.open(str(result)) as container:
        assert len(container.streams.audio) == 1


def test_silent_upload_gives_a_silent_result(tmp_path):
    upload = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24)
    generated = write_video(
        tmp_path / "gen.mp4", [solid((150, 150, 150))] * 30, fps=30, audio_seconds=1
    )
    result = add_original_soundtrack(generated, upload, tmp_path / "final.mp4")
    with av.open(str(result)) as container:
        assert container.streams.video and not container.streams.audio
    assert count_frames(result) == 30


def test_copy_video_only_drops_audio(tmp_path):
    source = write_video(tmp_path / "v.mp4", [solid((10, 10, 10))] * 12, audio_seconds=0.5)
    result = copy_video_only(source, tmp_path / "silent.mp4")
    with av.open(str(result)) as container:
        assert not container.streams.audio
```

- [ ] **Step 2: Run** `uv run pytest tests/test_audio.py -q --no-cov`
Expected: FAIL at import — `ImportError: cannot import name 'add_original_soundtrack'`.

- [ ] **Step 3: Implement** in `src/character_swap/media/audio.py`:

- replace `HOLD_GAP = (0.25, 0.5)  # seconds: ...` with:

```python
FIT_LIMIT = 0.5  # seconds: provider length differences we fix by holding/dropping end frames
```

  (keep `MAX_MISMATCH = 0.25`);

- replace `_hold_last_frame` with:

```python
def _fit_video(generated: Path, seconds: float, dst: Path) -> Path:
    """Re-encode `generated` to `seconds` at its own frame rate (streamed, O(1) memory).

    Extra frames at the end are dropped; a short video repeats its last frame.
    """
    with av.open(str(generated)) as probe:
        rate = int(round(float(probe.streams.video[0].average_rate or 24)))
    target = round(seconds * rate)
    last, written = None, 0
    first = next(iter_frames(generated))
    with VideoWriter(dst, first.shape[1], first.shape[0], fps=rate) as writer:
        for last in iter_frames(generated):
            if written == target:
                break
            writer.write(last)
            written += 1
        while written < target:
            writer.write(last)
            written += 1
    return dst
```

- in `restore_original_audio` replace

```python
        if HOLD_GAP[0] < gap <= HOLD_GAP[1]:
            generated = _hold_last_frame(generated, audio_seconds, Path(scratch) / "held.mp4")
```

  with

```python
        if MAX_MISMATCH < abs(gap) <= FIT_LIMIT:
            generated = _fit_video(generated, audio_seconds, Path(scratch) / "fitted.mp4")
```

- append:

```python
def copy_video_only(video: Path, dst: Path) -> Path:
    """Copy the video stream untouched and leave every audio stream out."""
    with av.open(str(video)) as source, av.open(str(dst), "w") as output:
        _copy_video_packets(source, output)
    return Path(dst)


def add_original_soundtrack(generated: Path, upload: Path, dst: Path) -> Path:
    """Generated pictures + the upload's untouched audio; silent if the upload had none."""
    with av.open(str(upload)) as probe:
        has_audio = bool(probe.streams.audio)
    if not has_audio:
        return copy_video_only(generated, dst)
    return restore_original_audio(generated, upload, dst)
```

- [ ] **Step 4: Run** `uv run pytest tests/test_audio.py -q --no-cov` → PASS (the existing hold-last-frame tests still pass through `_fit_video`).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/media/audio.py tests/test_audio.py
git commit -m "feat: fit 30 fps generated video to the original soundtrack (hold or trim 0.5 s)"
```

---

### Task 9: Generator interface and the Wan generator

**Files:** Create `src/character_swap/providers/generators/__init__.py`, `src/character_swap/providers/generators/base.py`, `src/character_swap/providers/generators/wan_replicate.py`, `tests/test_wan.py`. Modify `src/character_swap/costs.py`, `tests/test_costs.py`.

Interface per decision P1. `WanGenerator` uploads the normalized clip and the character image privately to Replicate (`ReplicateClient.upload`, no public hosting), creates one prediction through the model endpoint, resumes a saved record without re-uploading, and downloads the result. Prices are per output second; the 2026-10-22 figures stay until billing confirms them (Task 15).

- [ ] **Step 1: Failing tests** — create `tests/test_wan.py`:

```python
import json

import pytest

from character_swap.providers.errors import ProviderError
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.wan_replicate import WAN_MODEL, WanGenerator
from fakes import FakeReplicate

OUT = "https://o/wan.mp4"


def request(tmp_path, seconds=4.0):
    video = tmp_path / "original.mp4"
    video.write_bytes(b"v")
    face = tmp_path / "character-primary.png"
    face.write_bytes(b"i")
    return GenerationRequest(video=video, primary=CharacterRef(face), seconds=seconds, seed=1234)


def fake():
    return FakeReplicate(outputs={WAN_MODEL: OUT}, files={OUT: b"mp4"})


def test_estimate_is_per_output_second_by_resolution(tmp_path):
    wan = WanGenerator(fake())
    req = request(tmp_path, seconds=10.0)
    assert wan.estimate(req, "draft").total == pytest.approx(0.20)
    assert wan.estimate(req, "final").total == pytest.approx(0.50)
    assert "480p" in wan.estimate(req, "draft").describe()


@pytest.mark.parametrize(("stage", "resolution"), [("draft", "480"), ("final", "720")])
def test_generate_sends_one_request_with_the_job_seed(tmp_path, stage, resolution):
    replicate = fake()
    dest = tmp_path / f"{stage}-raw.mp4"
    out = WanGenerator(replicate).generate(
        request(tmp_path), stage, record=tmp_path / f"{stage}.json", dest=dest
    )
    assert out == dest and dest.read_bytes() == b"mp4"
    assert [path.name for path in replicate.uploads] == ["original.mp4", "character-primary.png"]
    assert replicate.runs == [
        {
            "key": WAN_MODEL,
            "input": {
                "video": "https://fake.replicate/original.mp4",
                "character_image": "https://fake.replicate/character-primary.png",
                "resolution": resolution,
                "seed": 1234,
                "merge_audio": False,
                "go_fast": False,
            },
        }
    ]


def test_resume_reuses_the_saved_prediction_without_uploading(tmp_path):
    replicate = fake()
    record = tmp_path / "draft.json"
    record.write_text(json.dumps({"id": "p1", "status": "succeeded"}))
    WanGenerator(replicate).generate(
        request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
    )
    assert replicate.uploads == [] and replicate.runs == []


def test_on_created_reports_the_new_prediction(tmp_path):
    seen = []
    WanGenerator(fake()).generate(
        request(tmp_path),
        "draft",
        record=tmp_path / "r.json",
        dest=tmp_path / "raw.mp4",
        on_created=seen.append,
    )
    assert seen == [{"id": "pred-1", "status": "starting"}]


@pytest.mark.parametrize("bad", [None, ["https://o/a.mp4"], "not-a-url", {"video": OUT}])
def test_unexpected_output_is_a_provider_error(tmp_path, bad):
    replicate = FakeReplicate(outputs={WAN_MODEL: bad})
    with pytest.raises(ProviderError, match="unexpected output"):
        WanGenerator(replicate).generate(
            request(tmp_path), "draft", record=tmp_path / "r.json", dest=tmp_path / "raw.mp4"
        )


def test_expired_download_names_the_record_and_the_rebuy_cost(tmp_path):
    replicate = FakeReplicate(outputs={WAN_MODEL: OUT}, fail_download={OUT})
    record = tmp_path / "draft-prediction.json"
    with pytest.raises(ProviderError, match=r"Delete .*draft-prediction\.json.*about \$0\.08"):
        WanGenerator(replicate).generate(
            request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
        )
    assert record.exists()  # never deleted for the user: deleting it means paying again


def test_limits_and_capabilities():
    assert (WanGenerator.min_seconds, WanGenerator.max_seconds) == (1, 30)
    assert WanGenerator.supports_secondary is False and WanGenerator.provider == "replicate"
    assert WanGenerator.model == WAN_MODEL == "wan-video/wan-2.2-animate-replace"
```

Append to `tests/test_costs.py`:

```python
def test_wan_prices_are_per_output_second():
    assert PRICES["wan-480"].unit == PRICES["wan-720"].unit == "second"
    assert PRICES["wan-480"].usd < PRICES["wan-720"].usd
```

- [ ] **Step 2: Run** `uv run pytest tests/test_wan.py tests/test_costs.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'character_swap.providers.generators'` and `KeyError: 'wan-480'`.

- [ ] **Step 3: Implement**

(a) `src/character_swap/costs.py`:
- add to `PRICES` (after `"sam3"`):

```python
    # Wan is billed per second of output video. Not shown on the model page on 2026-11-03;
    # these are the 2026-10-22 figures. Confirm against replicate.com billing after a run.
    "wan-480": Price(
        "Wan draft (480p)",
        0.02,
        "second",
        "2026-10-22",
        "replicate.com/wan-video/wan-2.2-animate-replace (per output second)",
    ),
    "wan-720": Price(
        "Wan final (720p)",
        0.05,
        "second",
        "2026-10-22",
        "replicate.com/wan-video/wan-2.2-animate-replace (per output second)",
    ),
```

- rename `_cost` to `cost_line` (definition and its three uses in `estimate_prepare`, `estimate_stages`, `estimate_repair`).

(b) Create `src/character_swap/providers/generators/__init__.py`:

```python
"""Video generators: one module per provider, all behind generators.base.Generator."""
```

(c) Create `src/character_swap/providers/generators/base.py`:

```python
"""What every video generator offers, so the pipeline and CLI never depend on one provider."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from character_swap.costs import Estimate

Stage = Literal["draft", "final"]
OnCreated = Callable[[dict], None]


@dataclass(frozen=True)
class CharacterRef:
    image_path: Path  # local copy in the job folder (Wan uploads this file)
    face_asset_id: str | None = None  # PiAPI face-library id (Plan 2b)


@dataclass(frozen=True)
class GenerationRequest:
    video: Path  # Seedance: the prepared composite; Wan: the normalized original
    primary: CharacterRef
    seconds: float  # normalized clip length, for estimates
    seed: int  # chosen once per job; the final reuses it
    prompt: str = ""  # Wan has no prompt input
    secondary: CharacterRef | None = None  # Seedance only


class Generator(Protocol):
    name: str
    provider: str
    model: str
    min_seconds: int
    max_seconds: int
    supports_real_faces: bool
    supports_secondary: bool

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate: ...

    def generate(
        self,
        request: GenerationRequest,
        stage: Stage,
        *,
        record: Path,
        dest: Path,
        on_created: OnCreated | None = None,
    ) -> Path:
        """Create the paid request once (or resume `record`), wait, and download to `dest`.

        The provider id is saved in `record` before waiting; an existing record is resumed,
        never re-bought. `on_created` runs once when a NEW paid request exists.
        """
        ...
```

(d) Create `src/character_swap/providers/generators/wan_replicate.py`:

```python
"""Wan 2.2 Animate Replace on Replicate: puts the character into the original video."""

from __future__ import annotations

from pathlib import Path

from character_swap.costs import Estimate, cost_line
from character_swap.modes import GENERATOR_LIMITS
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators.base import GenerationRequest, OnCreated, Stage

# Official model: called through the model endpoint (no version pin). Inputs verified
# 2026-11-03; see docs/research/2026-10-22-provider-api-facts.md.
WAN_MODEL = "wan-video/wan-2.2-animate-replace"
RESOLUTION: dict[str, str] = {"draft": "480", "final": "720"}
PRICE_KEY: dict[str, str] = {"draft": "wan-480", "final": "wan-720"}


class WanGenerator:
    name = "wan"
    provider = "replicate"
    model = WAN_MODEL
    min_seconds, max_seconds = GENERATOR_LIMITS["wan"]
    supports_real_faces = True  # open model, no face filter
    supports_secondary = False

    def __init__(self, replicate) -> None:
        self._replicate = replicate

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate:
        return Estimate(lines=(cost_line(PRICE_KEY[stage], request.seconds),))

    @staticmethod
    def build_input(
        request: GenerationRequest, stage: Stage, *, video_url: str, image_url: str
    ) -> dict:
        return {
            "video": video_url,
            "character_image": image_url,
            "resolution": RESOLUTION[stage],
            "seed": request.seed,
            "merge_audio": False,  # the original soundtrack is restored locally, byte for byte
            "go_fast": False,
        }

    def generate(
        self,
        request: GenerationRequest,
        stage: Stage,
        *,
        record: Path,
        dest: Path,
        on_created: OnCreated | None = None,
    ) -> Path:
        if record.exists():  # resume: run() reads the saved prediction and ignores the input
            payload: dict = {}
        else:
            payload = self.build_input(
                request,
                stage,
                video_url=self._replicate.upload(request.video),
                image_url=self._replicate.upload(request.primary.image_path),
            )
        output = self._replicate.run(
            model=WAN_MODEL, input=payload, record=record, on_created=on_created
        )
        if not isinstance(output, str) or not output.startswith("https://"):
            raise ProviderError(
                f"Wan returned an unexpected output: {output!r}. Check the prediction saved in "
                f"{record} on replicate.com before deleting it (a new run costs money)."
            )
        try:
            return self._replicate.download(output, dest)
        except OutputExpired as exc:
            rebuy = self.estimate(request, stage).total
            raise ProviderError(
                f"The Wan {stage} finished but its download link expired ({exc}). "
                f"Delete {record} and run the same command again to make a new one "
                f"(costs about ${rebuy:.2f})."
            ) from exc
```

- [ ] **Step 4: Run** `uv run pytest tests/test_wan.py tests/test_costs.py tests/test_cli.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/costs.py src/character_swap/providers/generators/__init__.py src/character_swap/providers/generators/base.py src/character_swap/providers/generators/wan_replicate.py tests/test_wan.py tests/test_costs.py
git commit -m "feat: add Generator interface and Wan 2.2 Animate Replace generator"
```

---

### Task 10: `pipeline.generate()` — paid draft/final with review gate, spend log and soundtrack

**Files:** Modify `src/character_swap/pipeline.py`. Create `tests/test_generation.py`.

Flow for `generate(job, services, stage, runs_dir=..., status=...)`:
1. `check_can_generate`: the mode must generate in this version (Wan only), the review must be approved for the *current* files (hash check), the final needs a finished draft, and (free pre-flight) the upload's soundtrack must be close enough in length to the normalized clip that it can be put back later (`abs(audio - clip) <= FIT_LIMIT - 0.1` = 0.4 s, leaving 0.1 s for Wan's own rounding). All of this runs before any cost question (the CLI calls it first too), so a clip whose sound could never be restored is refused before anything is bought.
2. A stage that is already done returns its file (no provider call).
3. The stage is tracked (`running` → `done`/`failed`/`interrupted`) like a prep stage, but it does **not** clear the review.
4. If `draft-raw.mp4` already exists (downloads are atomic), the provider is not asked again: a failure in the soundtrack step resumes for free even after the provider's link expired.
5. Otherwise the generator creates or resumes the prediction (`draft-prediction.json`); `on_created` writes one spend entry with the provider task id.
6. `add_original_soundtrack(raw, upload.mp4, draft.mp4)`.

`next_step()` tells `charswap status` (and agents) the one command to run next.

- [ ] **Step 1: Failing tests** — create `tests/test_generation.py`:

```python
import json

import av
import pytest

from character_swap import pipeline
from character_swap.costs import Estimate, Line
from character_swap.jobs import Job
from character_swap.pipeline import (
    Services,
    check_can_generate,
    generate,
    needs_new_charge,
    next_step,
    prepare,
)
from character_swap.review import ReviewError, decide, fingerprint
from character_swap.stages import StageError
from helpers import solid, write_video

QUIET = {"status": lambda _m: None}


class FakeGenerator:
    name, provider, model = "wan", "replicate", "fake/wan"
    min_seconds, max_seconds = 1, 30
    supports_real_faces, supports_secondary = True, False

    def __init__(self, video):
        self.video = video  # what the "provider" returns: 2 s, 30 fps, silent
        self.calls = []

    def estimate(self, request, stage):
        return Estimate(lines=(Line(f"Fake {stage}", 0.25),))

    def generate(self, request, stage, *, record, dest, on_created=None):
        self.calls.append((stage, request))
        if not record.exists():
            record.write_text(json.dumps({"id": f"p-{stage}", "status": "succeeded"}))
            if on_created is not None:
                on_created({"id": f"p-{stage}", "status": "starting"})
        dest.write_bytes(self.video.read_bytes())
        return dest


@pytest.fixture
def wan_job(runs_dir, tmp_path):
    upload = write_video(tmp_path / "clip.mp4", [solid((180, 40, 40))] * 48, audio_seconds=2)
    face = tmp_path / "me.png"
    face.write_bytes(b"png")
    return Job.create(runs_dir, upload, generator="wan", mode="direct", character=face)


@pytest.fixture
def services(tmp_path):
    output = write_video(tmp_path / "wan-out.mp4", [solid((20, 200, 20))] * 60, fps=30)
    return Services(
        replicate=None, face_detector=lambda: None, generators={"wan": FakeGenerator(output)}
    )


def approve(job, services):
    prepare(job, services, **QUIET)
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def test_draft_needs_an_approved_review(wan_job, services, runs_dir):
    prepare(wan_job, services, **QUIET)
    with pytest.raises(ReviewError, match="Review required"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "draft" not in wan_job.state["stages"]


def test_final_needs_a_draft(wan_job, services, runs_dir):
    approve(wan_job, services)
    with pytest.raises(StageError, match="draft first"):
        generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)


def test_seedance_jobs_cannot_generate_yet(runs_dir, tmp_path):
    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth")
    with pytest.raises(StageError, match="next version"):
        check_can_generate(job, "draft")


def test_draft_restores_the_original_soundtrack(wan_job, services, runs_dir):
    approve(wan_job, services)
    result = generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert result == wan_job.path("draft.mp4") and wan_job.stage_done("draft")
    assert audio_packets(result) == audio_packets(wan_job.path("upload.mp4"))


def test_draft_and_final_send_the_same_seed_clip_and_character(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)
    calls = services.generators["wan"].calls
    assert [stage for stage, _ in calls] == ["draft", "final"]
    assert {request.seed for _, request in calls} == {wan_job.state["seed"]}
    request = calls[0][1]
    assert request.video == wan_job.path("original.mp4")
    assert request.primary.image_path == wan_job.path("character-primary.png")
    assert request.seconds == 2.0
    assert wan_job.path("final.mp4").exists()


def test_each_paid_request_is_logged_once_when_created(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)  # already done
    costs = wan_job.state["costs"]
    assert [(c["step"], c["task_id"], c["usd"], c["model"], c["units"]) for c in costs] == [
        ("draft", "p-draft", 0.25, "fake/wan", "2.0s")
    ]
    assert "p-draft" in (runs_dir / "spend-log.csv").read_text()


def test_downloaded_result_is_never_requested_again(wan_job, services, runs_dir):
    approve(wan_job, services)
    fake = services.generators["wan"]
    wan_job.path("draft-raw.mp4").write_bytes(fake.video.read_bytes())
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert fake.calls == [] and wan_job.stage_done("draft")


def test_soundtrack_failure_resumes_without_a_new_request(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "add_original_soundtrack", broken)
    with pytest.raises(StageError, match="disk full"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert wan_job.state["stages"]["draft"]["status"] == "failed"
    monkeypatch.undo()
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert len(services.generators["wan"].calls) == 1 and wan_job.stage_done("draft")


def test_interrupted_generation_is_recorded(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(services.generators["wan"], "generate", stop)
    with pytest.raises(KeyboardInterrupt):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    entry = wan_job.state["stages"]["draft"]
    assert entry["status"] == "failed" and entry["error"] == "interrupted"


def test_generation_does_not_clear_the_review(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert wan_job.path("review.json").exists()


def test_generation_is_tracked_apart_from_preparation(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert tuple(wan_job.state["stages"]) == ("normalize", "draft")


def test_soundtrack_that_cannot_fit_is_refused_before_paying(runs_dir, services, tmp_path):
    # 2 s of picture but 3 s of sound: the original audio could never be put back.
    upload = write_video(tmp_path / "long.mp4", [solid((9, 9, 9))] * 48, audio_seconds=3)
    face = tmp_path / "me2.png"
    face.write_bytes(b"png")
    job = Job.create(runs_dir, upload, generator="wan", mode="direct", character=face)
    approve(job, services)
    with pytest.raises(StageError, match="soundtrack"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "costs" not in job.state
    assert "draft" not in job.state["stages"]


def test_silent_upload_passes_the_soundtrack_check(runs_dir, services, tmp_path):
    upload = write_video(tmp_path / "mute.mp4", [solid((9, 9, 9))] * 48)
    face = tmp_path / "me3.png"
    face.write_bytes(b"png")
    job = Job.create(runs_dir, upload, generator="wan", mode="direct", character=face)
    approve(job, services)
    check_can_generate(job, "draft")  # no error: the result will simply be silent


def test_unreadable_sound_length_is_refused_before_paying(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def unknown(path, kind):
        raise ValueError("Cannot determine the audio duration of upload.mp4.")

    monkeypatch.setattr(pipeline, "stream_seconds", unknown)
    with pytest.raises(StageError, match="Could not read the length of the clip's sound"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "costs" not in wan_job.state


def test_unconfirmed_saved_request_is_detected(wan_job):
    assert not pipeline.saved_request_unconfirmed(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text(json.dumps({"status": "creating"}))
    assert pipeline.saved_request_unconfirmed(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text(json.dumps({"id": "p", "status": "processing"}))
    assert not pipeline.saved_request_unconfirmed(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text("{")
    assert not pipeline.saved_request_unconfirmed(wan_job, "draft")  # run() reports damage


def test_needs_new_charge(wan_job):
    assert needs_new_charge(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text("{}")
    assert not needs_new_charge(wan_job, "draft")
    assert needs_new_charge(wan_job, "final")
    wan_job.path("final-raw.mp4").write_bytes(b"x")
    assert not needs_new_charge(wan_job, "final")


def test_missing_generator_is_explained(wan_job, services, runs_dir):
    approve(wan_job, services)
    services.generators.clear()
    with pytest.raises(StageError, match="No wan generator"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)


def test_next_step_walks_the_whole_flow(wan_job, services, runs_dir):
    assert next_step(wan_job, approved=False) == f"charswap resume {wan_job.id}"
    prepare(wan_job, services, **QUIET)
    assert "charswap review" in next_step(wan_job, approved=False)
    decide(wan_job, approved=True, note="ok", fingerprint=fingerprint(wan_job))
    assert next_step(wan_job, approved=True) == f"charswap generate {wan_job.id} --draft"
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert next_step(wan_job, approved=True) == f"charswap generate {wan_job.id} --final"
    generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)
    assert next_step(wan_job, approved=True) == f"done: {wan_job.path('final.mp4')}"


def test_next_step_for_seedance_waits_for_the_next_version(runs_dir, tmp_path):
    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    job = Job.create(runs_dir, upload, generator="seedance", mode="face_mesh")
    for name in pipeline.PREP_STAGES_BY_MODE["face_mesh"]:
        job.mark_stage(name)
    assert "next version" in next_step(job, approved=True)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_generation.py -q --no-cov`
Expected: FAIL at import — `ImportError: cannot import name 'check_can_generate' from 'character_swap.pipeline'`.

- [ ] **Step 3: Implement** in `src/character_swap/pipeline.py`:

- imports: change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`; add `import json` to the stdlib imports and `import av` to the third-party imports; change the audio import to `from character_swap.media.audio import FIT_LIMIT, add_original_soundtrack, embed_samples, read_audio`; change the modes import to `from character_swap.modes import GENERATION_STAGES, GENERATOR_LIMITS, MODES`; add

```python
from character_swap.costs import record_spend
from character_swap.providers.generators.base import CharacterRef, GenerationRequest, Generator
from character_swap.review import require_approved
```

  (keep `from character_swap.review import clear as clear_review`).

- below the `PREP_STAGES_BY_MODE` table add:

```python
GENERATION_LABELS = {"draft": "Generating the 480p draft", "final": "Generating the 720p final"}
```

- add the field to `Services` (last):

```python
    generators: dict[str, Generator] = field(default_factory=dict)
```

- append at the end of the file:

```python
# ---- generation (paid, after review) ---------------------------------------------
def generation_files(stage: str) -> tuple[str, str, str]:
    """(saved provider record, downloaded raw video, video with the original soundtrack)."""
    return f"{stage}-prediction.json", f"{stage}-raw.mp4", f"{stage}.mp4"


def check_can_generate(job: Job, stage: str) -> None:
    """Refuse before any cost question: right mode, approved review, and a draft first."""
    if stage not in GENERATION_STAGES:
        raise StageError(f"Unknown generation step {stage!r}; use draft or final.")
    if not MODES[job.mode].generates:
        raise StageError(
            "Generating Seedance jobs arrives in the next version; Wan jobs can generate now."
        )
    require_approved(job)
    if stage == "final" and not job.stage_done("draft"):
        raise StageError(
            f"Make and watch the draft first: charswap generate {job.id} --draft"
        )
    _check_soundtrack_fits(job)


def _check_soundtrack_fits(job: Job) -> None:
    """Free pre-flight: refuse to pay for a video whose original sound can't be put back."""
    upload = job.path("upload.mp4")
    with av.open(str(upload)) as probe:
        if not probe.streams.audio:
            return  # no audio stream at all: the result will simply be silent
    try:
        sound = stream_seconds(upload, "audio")
    except ValueError as exc:  # has audio, but its length is unknown: can't promise a restore
        raise StageError(
            f"Could not read the length of the clip's sound ({exc}), so the original "
            "soundtrack might not be put back. Re-export the clip and prepare a new job. "
            "Nothing was charged."
        ) from exc
    picture = clip_seconds(job)
    if abs(sound - picture) > FIT_LIMIT - 0.1:  # leave 0.1 s for the generator's rounding
        raise StageError(
            f"The clip's sound lasts {sound:.1f} s but its picture {picture:.1f} s, so the "
            "original soundtrack could not be put back after generating. Trim the clip so "
            "both have the same length and prepare a new job. Nothing was charged."
        )


def saved_request_unconfirmed(job: Job, stage: str) -> bool:
    """True when the saved record is only the "creating" marker (POST never confirmed)."""
    record, _, _ = generation_files(stage)
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # missing, or damaged: the client reports damage with its own message
    return isinstance(data, dict) and data.get("status") == "creating"


def needs_new_charge(job: Job, stage: str) -> bool:
    """False when a saved provider request or a downloaded result exists (resume is free)."""
    record, raw, _ = generation_files(stage)
    return not job.path(record).exists() and not job.path(raw).exists()


def generator_for(job: Job, services: Services) -> Generator:
    try:
        return services.generators[job.generator]
    except KeyError:
        raise StageError(f"No {job.generator} generator is set up.") from None


def build_request(job: Job) -> GenerationRequest:
    state = job.state
    character = state.get("character_primary")
    if not character:
        raise StageError("This job has no character image. Prepare a new job with --character.")
    return GenerationRequest(
        video=job.path("original.mp4"),
        primary=CharacterRef(job.path(character)),
        seconds=clip_seconds(job),
        seed=int(state["seed"]),
    )


def generate(
    job: Job, services: Services, stage: str, *, runs_dir: Path, status: Status = print
) -> Path:
    """Make the draft or final video. Paid: the caller showed the estimate and got a yes."""
    check_can_generate(job, stage)
    label = GENERATION_LABELS[stage]
    record, raw, result = (job.path(name) for name in generation_files(stage))
    if job.stage_done(stage):
        _notify(status, f"{label} · already done")
        return result
    generator = generator_for(job, services)
    request = build_request(job)
    estimate = generator.estimate(request, stage)

    def log_charge(prediction: dict) -> None:
        record_spend(
            job,
            stage,
            estimate,
            runs_dir,
            provider=generator.provider,
            model=generator.model,
            task_id=str(prediction.get("id", "")),
            units=f"{request.seconds:.1f}s",
        )

    _notify(status, label)
    with _tracked(job, stage), _wrap(label):  # no clear_review: generating changes no input
        if not raw.exists():  # downloaded already: never ask the provider again
            generator.generate(request, stage, record=record, dest=raw, on_created=log_charge)
        _notify(status, "Putting the original soundtrack back")
        add_original_soundtrack(raw, job.path("upload.mp4"), result)
    _notify(status, f"{stage.capitalize()} ready: {result}")
    return result


def next_step(job: Job, *, approved: bool) -> str:
    """The one thing to do next (shown by `charswap status`)."""
    mode = MODES[job.mode]
    if not all(job.stage_done(name) for name in mode.prep_stages):
        return f"charswap resume {job.id}"
    if not approved:
        return "watch the prepared files, then run charswap review with the fingerprint above"
    if not mode.generates:
        return "generating this mode arrives in the next version"
    for stage in GENERATION_STAGES:
        if not job.stage_done(stage):
            return f"charswap generate {job.id} --{stage}"
    return f"done: {job.path('final.mp4')}"
```

Notes for the implementer:
- `review.py` no longer imports `pipeline` (Task 2), so `pipeline` importing `review.require_approved` creates no cycle.
- `test_interrupted_generation_is_recorded` relies on `_tracked` catching `BaseException` (already true).

- [ ] **Step 4: Run** `uv run pytest tests/test_generation.py tests/test_pipeline.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/pipeline.py tests/test_generation.py
git commit -m "feat: paid draft/final generation with review gate, spend log and original audio"
```

---

### Task 11: `charswap generate <job> --draft|--final`, status `next`, Wan hints

**Files:** Modify `src/character_swap/cli.py`, `tests/test_cli.py`.

Order inside the command (money safety first): load job → already done? (print path, exit 0) → `check_can_generate` (mode, review, draft) → token → estimate + confirmation **only if** a new paid request will be created (a saved prediction or downloaded result resumes for free, without asking) → `pipeline.generate`. Exit codes stay: 0 ok, 1 declined/unexpected, 2 user-fixable error (stderr), 130 Ctrl-C. Failure and Ctrl-C hints name the generate command (`charswap generate <id> --draft|--final`, the one form used everywhere), not `resume`. The soundtrack pre-flight from Task 10 runs inside `check_can_generate`, so an unfixable clip is refused before the cost question. An unconfirmed `creating` marker gets guidance, not "no new charge".

- [ ] **Step 1: Failing tests** — append to `tests/test_cli.py`:

First add these imports at the top of `tests/test_cli.py`, with the other first-party imports (aliases avoid clashing with the local `fingerprint` variable in `test_status_review_prompt_flow`):

```python
from character_swap.costs import Estimate, Line
from character_swap.pipeline import Services
from character_swap.review import decide as approve_review
from character_swap.review import fingerprint as review_fingerprint
```

Then append:

```python
# ---- generate (Plan 2a) ----------------------------------------------------------
class FakeWan:
    name, provider, model = "wan", "replicate", "fake/wan"

    def estimate(self, request, stage):
        return Estimate(lines=(Line(f"Wan {stage}", 0.08),))


def wan_services(_settings):
    return Services(replicate=None, face_detector=lambda: None, generators={"wan": FakeWan()})


def _wan_job(settings, video, tmp_path):
    face = tmp_path / "face.png"
    face.write_bytes(b"x")
    return Job.create(settings.runs_dir, video, generator="wan", mode="direct", character=face)


def _approved_wan_job(settings, video, tmp_path):
    job = _wan_job(settings, video, tmp_path)
    job.path("original.mp4").write_bytes(b"normalized")
    job.mark_stage("normalize")
    job.update(video={"frames": 96, "fps": 24, "width": 64, "height": 64, "seconds": 4.0})
    approve_review(job, approved=True, note="watched all", fingerprint=review_fingerprint(job))
    return job


def _fake_generate(seen):
    def fake(job, services, stage, *, runs_dir, status):
        seen.append(stage)
        return job.path(f"{stage}.mp4")

    return fake


def test_generate_draft_shows_the_cost_and_can_be_declined(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda _q: False,
        services_factory=wan_services,
    )
    assert code == 1 and "Wan draft" in out.text and "$0.0800" in out.text
    assert "Cancelled. Nothing was charged." in out.err.text and seen == []


def test_generate_draft_runs_after_confirmation(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda _q: True,
        services_factory=wan_services,
    )
    assert code == 0 and seen == ["draft"]
    assert f"charswap generate {job.id} --final" in out.text


def test_generate_needs_approval_before_any_cost_question(settings, video, tmp_path):
    job = _wan_job(settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"], settings, confirm=asked.append, services_factory=wan_services
    )
    assert code == 2 and asked == [] and "Review required" in out.err.text


def test_generate_resume_does_not_ask_again(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p", "status": "processing"}))
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"], settings, confirm=asked.append, services_factory=wan_services
    )
    assert code == 0 and asked == [] and seen == ["draft"] and "no new charge" in out.text


def test_generate_on_an_unconfirmed_marker_gives_guidance_not_comfort(
    settings, video, tmp_path, monkeypatch
):
    job = _approved_wan_job(settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"status": "creating"}))
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"], settings, confirm=asked.append, services_factory=wan_services
    )
    assert asked == [] and "no new charge" not in out.text
    assert (
        "A request for this step was sent but never confirmed. It may have been charged; "
        "follow the steps below before running again."
    ) in out.text


def test_generate_refuses_a_clip_whose_sound_cannot_be_restored(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    job.update(video={"frames": 48, "fps": 24, "width": 64, "height": 64, "seconds": 2.0})
    asked = []  # the upload has 4 s of sound, the clip 2 s of picture
    code, out = run(
        ["generate", job.id, "--draft"], settings, confirm=asked.append, services_factory=wan_services
    )
    assert code == 2 and asked == [] and "soundtrack" in out.err.text
    assert "costs" not in job.state


def test_repair_ctrl_c_does_not_promise_a_free_rerun(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "repair_with_rgb", stop)
    code, out = run(["repair", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert code == 130 and f"charswap resume {job.id}" in out.err.text
    assert "not charged again" not in out.err.text and "buys a new SAM 3 cut" in out.err.text


def test_generate_final_reminds_to_watch_the_draft(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    job.mark_stage("draft")
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(["generate", job.id, "--final", "--yes"], settings, services_factory=wan_services)
    assert code == 0 and seen == ["final"]
    assert "Wan final" in out.text and str(job.path("draft.mp4")) in out.text


def test_generate_finished_stage_is_a_no_op(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    job.mark_stage("draft")
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(["generate", job.id, "--draft"], settings, services_factory=wan_services)
    assert code == 0 and seen == [] and "already done" in out.text


def test_generate_failure_hints_the_generate_command(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)

    def boom(*args, **kwargs):
        raise StageError("Generating the 480p draft failed: boom")

    monkeypatch.setattr(cli, "generate_stage", boom)
    code, out = run(["generate", job.id, "--draft", "--yes"], settings, services_factory=wan_services)
    assert code == 2 and f"charswap generate {job.id} --draft" in out.err.text


def test_generate_ctrl_c_hints_the_generate_command(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "generate_stage", stop)
    code, out = run(["generate", job.id, "--final", "--yes"], settings, services_factory=wan_services)
    assert code == 2 and "draft first" in out.err.text  # no draft yet: refused before Ctrl-C
    job.mark_stage("draft")
    code, out = run(["generate", job.id, "--final", "--yes"], settings, services_factory=wan_services)
    assert code == 130 and f"charswap generate {job.id} --final" in out.err.text


def test_generate_needs_the_replicate_token(tmp_path, video):
    empty = Settings.load(root=tmp_path, environ={})
    job = _approved_wan_job(empty, video, tmp_path)
    code, out = run(["generate", job.id, "--draft"], empty, services_factory=wan_services)
    assert code == 2 and "REPLICATE_API_TOKEN" in out.err.text


def test_seedance_jobs_cannot_generate_yet(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    code, out = run(["generate", job.id, "--draft"], settings)
    assert code == 2 and "next version" in out.err.text


def test_status_says_what_to_do_next(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    data = json.loads(run(["status", job.id], settings)[1].text)
    assert data["next"] == f"charswap generate {job.id} --draft"


def test_prepare_wan_points_at_the_normalized_clip(tmp_path, video, monkeypatch):
    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: None)
    code, out = run(
        ["prepare", str(video), "--generator", "wan", "--character", str(character), "--yes"],
        Settings.load(root=tmp_path, environ={}),
        services_factory=lambda s: object(),
    )
    assert code == 0 and "original.mp4" in out.text and "prepared.mp4" not in out.text


def test_help_lists_generate(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    assert "generate" in text and "charswap generate <id> --draft" in text
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -q --no-cov -k "generate or next or normalized_clip"`
Expected: FAIL — the generate tests error with `SystemExit: 2` from argparse (`invalid choice: 'generate'`; `run()` does not catch it), `test_status_says_what_to_do_next` fails with `KeyError: 'next'`, the Wan prepare hint still names `prepared.mp4`, and the repair Ctrl-C test still sees "not charged again".

- [ ] **Step 3: Implement** in `src/character_swap/cli.py`:

(a) Imports — replace the two `pipeline` import lines with:

```python
from character_swap.pipeline import (
    GENERATION_LABELS,
    Services,
    build_request,
    check_can_generate,
    generator_for,
    needs_new_charge,
    next_step,
    remaining_paid_stages,
    repair_with_rgb,
    saved_request_unconfirmed,
)
from character_swap.pipeline import generate as generate_stage
from character_swap.pipeline import prepare as prepare_job
```

(b) `build_services` — wire the Wan generator to the same Replicate client:

```python
def build_services(settings: Settings) -> Services:  # pragma: no cover - wires real clients
    from character_swap.providers.generators.wan_replicate import WanGenerator
    from character_swap.providers.replicate import ReplicateClient
    from character_swap.stages.face_mesh import MediaPipeDetector, ensure_model

    model_dir = settings.root / "models"
    replicate = ReplicateClient(settings.require("REPLICATE_API_TOKEN"))
    return Services(
        replicate=replicate,
        face_detector=lambda: MediaPipeDetector(ensure_model(model_dir)),
        hf_token=settings.get("HF_TOKEN") or None,
        generators={"wan": WanGenerator(replicate)},
    )
```

(c) `EPILOG`:

```python
EPILOG = """example:
  charswap prepare clip.mp4 --mode depth
  charswap prepare clip.mp4 --generator wan --character me.jpg
  charswap status <id>
  charswap review <id> --approve --note "watched all" --fingerprint <fp>
  charswap generate <id> --draft
  charswap view
"""
```

(d) `_parser()` — after the `repair` parser add:

```python
    gen = sub.add_parser("generate", help="make the 480p draft or the 720p final (paid; asks first)")
    gen.add_argument("job")
    which = gen.add_mutually_exclusive_group(required=True)
    which.add_argument("--draft", action="store_true", help="480p test run")
    which.add_argument("--final", action="store_true", help="720p, same seed as the draft")
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation")
```

(e) Retry hints use one command form everywhere (`charswap generate <id> --draft`). `Env.retry` becomes a template with an `{id}` placeholder:
- in `Env` add the method:

```python
    def retry_command(self) -> str:
        return "charswap " + (self.retry or "resume {id}").format(id=self.job_id or "<job>")
```

- `_hint()` becomes:

```python
def _hint(env: Env) -> None:
    if env.job_id:
        env.err(f"Fix the issue, then run: {env.retry_command()}")
```

- update the existing calls: `_prepare` and `_resume` use `env.track(job, "resume {id}")`, `_repair` uses `env.track(job, "repair {id}")` (printed text is unchanged: `charswap resume <id>` / `charswap repair <id>`);
- `main()`'s Ctrl-C branch names the command that was running, except after a repair, which must not promise a free re-run (a new repair buys a new SAM 3 cut):

```python
    except KeyboardInterrupt:
        if (env.retry or "").startswith("repair"):
            err(
                f"Interrupted. Run `charswap resume {env.job_id}` to finish the job. "
                "Running repair again buys a new SAM 3 cut (~$0.002)."
            )
        else:
            err(
                f"Interrupted. Run `{env.retry_command()}` to continue; "
                "paid steps already started are not charged again."
            )
        return 130
```

(f) `_prepare()` — replace the last `env.out(f"Next: watch ...")` line with:

```python
    watch = "original.mp4" if job.mode == "direct" else "prepared.mp4"
    env.out(f"Next: watch {job.path(watch)} then run `charswap status {job.id}`.")
```

(g) `_status()` — the report gets the next step:

```python
def _status(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    review = review_status(job)
    report = {
        "job": job.id,
        "mode": job.mode,
        "stages": job.state["stages"],
        "review": review,
        "next": next_step(job, approved=bool(review.get("approved"))),
    }
    env.out(json.dumps(report, indent=2))
    return 0
```

(h) New command, placed after `_repair`:

```python
def _generate(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    stage = "final" if args.final else "draft"
    env.track(job, f"generate {{id}} --{stage}")
    if job.stage_done(stage):
        env.out(f"The {stage} is already done: {job.path(f'{stage}.mp4')}")
        return 0
    check_can_generate(job, stage)  # mode, approved review, draft first: before any cost
    settings.require("REPLICATE_API_TOKEN")
    services = env.services_factory(settings)
    if saved_request_unconfirmed(job, stage):
        env.out(
            "A request for this step was sent but never confirmed. It may have been charged; "
            "follow the steps below before running again."
        )
    elif needs_new_charge(job, stage):
        estimate = generator_for(job, services).estimate(build_request(job), stage)
        env.out(f"{GENERATION_LABELS[stage]} (paid):\n" + estimate.describe())
        if stage == "final":
            env.out(f"Only continue if you watched {job.path('draft.mp4')} and liked it.")
        if not args.yes and not env.proceed():
            return 1
    else:
        env.out(f"Found a saved {stage} request; resuming it (no new charge).")
    result = generate_stage(job, services, stage, runs_dir=settings.runs_dir, status=env.out)
    env.out(f"Watch: {result}")
    if stage == "draft":
        env.out(f"Happy with it? Run `charswap generate {job.id} --final`.")
    return 0
```

(i) Register it: add `"generate": _generate,` to `COMMANDS` after `"repair": _repair,`.

- [ ] **Step 4: Run** `uv run pytest tests/test_cli.py -q --no-cov` → PASS (including the existing interrupt and repair-hint tests: `prepare` tracks `resume {id}` and `repair` tracks `repair {id}`, so their hints print the same text).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/cli.py tests/test_cli.py
git commit -m "feat: add charswap generate <id> --draft|--final with cost confirmation"
```

---

### Task 12: Viewer progress knows about generation; failed and waiting jobs slow-poll

**Files:** Modify `src/character_swap/web/view_model.py`, `src/character_swap/web/app.py`, `src/character_swap/web/templates/job.html`, `src/character_swap/web/static/viewer.js`, `tests/test_view_model.py`, `tests/test_web_app.py`.

The polling payload gains `generation` (draft/final statuses, never counted as "preparing") and `poll`: `"fast"` (3 s) while a step runs, `"slow"` (10 s) while the job can still change (failed, waiting for review, waiting to generate), `"off"` once the final exists. Slow polling is how a `charswap resume` or `charswap generate` started in a terminal shows up live. Only `fast` has the 10-minute stall notice, and never while a generation stage is running (Wan's 720p final can take longer than 10 minutes). The stall notice therefore only ever appears during preparation, where `charswap resume <id>` is the right command; failed generation steps show their own `charswap generate <id> --draft|--final` hint in the section (Task 13).

- [ ] **Step 1: Failing tests**

In `tests/test_view_model.py` replace the expected dict in `test_progress_of_fresh_job_is_active`:

```python
    assert info == {
        "stages": dict.fromkeys(vm.STAGES_BY_MODE["depth"], "pending"),
        "generation": {"draft": "pending", "final": "pending"},
        "done_count": 0,
        "total_count": 9,
        "active": True,
        "poll": "fast",
    }
```

and append:

```python
def test_progress_keeps_generation_apart_from_preparation(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    info = vm.progress(job.state)
    assert info["stages"] == {"normalize": "done"}
    assert info["generation"] == {"draft": "pending", "final": "pending"}
    assert info["active"] is False and info["poll"] == "slow"  # waiting for the user
    job.mark_stage("draft", "running")
    info = vm.progress(job.state)
    assert info["active"] is True and info["poll"] == "fast"
    assert (info["done_count"], info["total_count"]) == (1, 1)
    job.mark_stage("draft")
    job.mark_stage("final")
    assert vm.progress(job.state)["poll"] == "off"


def test_failed_jobs_keep_slow_polling(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=2, failed="boom")
    info = vm.progress(job.state)
    assert info["active"] is False and info["poll"] == "slow"
    wan = make_job(runs_dir, media, "direct")
    wan.mark_stage("draft", "failed", error="boom")
    assert vm.progress(wan.state)["poll"] == "slow"
```

In `tests/test_web_app.py`:
- in `test_api_progress_shape` replace the keys assertion with `assert set(data) == {"stages", "generation", "done_count", "total_count", "active", "poll"}`;
- in `test_live_job_page_polls_and_shows_running_step` replace the `stages = ...` line and the assertion below it with:

```python
    shown = json.loads(text.split('data-stages="')[1].split('"')[0].replace("&#34;", '"'))
    assert shown["stages"]["depth"] == "running" and shown["stages"]["normalize"] == "done"
    assert shown["generation"] == {"draft": "pending", "final": "pending"}
    assert 'data-poll="fast"' in text
```

and append:

```python
def test_failed_job_page_slow_polls_for_a_resume(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="boom")
    text = client.get(f"/jobs/{job.id}").text
    assert 'data-active="false"' in text and 'data-poll="slow"' in text


def test_viewer_script_polls_by_mode(client):
    script = client.get("/static/viewer.js").text
    assert "slow: 10000" in script and "fast: 3000" in script
    assert "data.generation" in script
    assert 'pollMode === "fast" && !generating' in script  # no stall notice while generating
```

- [ ] **Step 2: Run** `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov`
Expected: FAIL — missing `generation`/`poll` keys, no `data-poll` attribute, script text not found.

- [ ] **Step 3: Implement**

(a) `src/character_swap/web/view_model.py` — add `from character_swap.modes import GENERATION_STAGES` to the imports and replace `progress()`:

```python
def progress(state: dict[str, Any]) -> dict[str, Any]:
    """The cheap polling payload: reads state only (no hashing, no review).

    Generation (draft/final) is reported apart from preparation: a prepared job that has
    not been generated is waiting for the user, not running.
    """
    stages = {name: stage_status(state, name) for name in STAGES_BY_MODE[state["mode"]]}
    generation = {name: stage_status(state, name) for name in GENERATION_STAGES}
    values = list(stages.values())
    done = values.count("done")
    failed = "failed" in values or "failed" in generation.values()
    preparing = "running" in values or (done < len(values) and "failed" not in values)
    active = preparing or "running" in generation.values()
    if active:
        poll = "fast"
    elif generation["final"] == "done" and not failed:
        poll = "off"
    else:
        poll = "slow"  # failed or waiting for the user: a resume/generate still shows up live
    return {
        "stages": stages,
        "generation": generation,
        "done_count": done,
        "total_count": len(values),
        "active": active,
        "poll": poll,
    }
```

(b) `src/character_swap/web/app.py` — in `job_page` replace the `context = ...` line with:

```python
        shown = {"stages": view.progress["stages"], "generation": view.progress["generation"]}
        context = {"job": view, "stages_json": json.dumps(shown)}
```

(c) `src/character_swap/web/templates/job.html` — add the attribute after `data-active=...`:

```html
     data-poll="{{ job.progress.poll }}"
```

(d) `src/character_swap/web/static/viewer.js` — delete the line `const POLL_MS = 3000;` near the top and replace everything from the comment `// 3. Live progress: ...` down to (not including) the final `})();` with:

```js
  // 3. Live progress. "fast" while a step runs, "slow" while the job can still change
  //    (failed, waiting for review or generation), "off" once the final video exists.
  const POLL_MS = { fast: 3000, slow: 10000 };
  const pollMode = layout.dataset.poll;
  if (!Object.hasOwn(POLL_MS, pollMode)) return;
  const url = `/api/jobs/${layout.dataset.jobId}`;
  const initial = JSON.parse(layout.dataset.stages);
  const shown = JSON.stringify(initial);
  const startedWaiting = Date.now();
  // A running generation (Wan's 720p final can take over 10 minutes) is never "stalled",
  // and the stall hint below only fits preparation, where `resume` is the right command.
  let generating = Object.values(initial.generation || {}).includes("running");

  document.getElementById("refresh-button").addEventListener("click", () => location.reload());

  function playing() {
    return [...document.querySelectorAll("video, audio")].some((m) => !m.paused && !m.ended);
  }

  function stalled() {
    document.getElementById("stalled").textContent =
      "No progress for 10 minutes. The job may have stopped. Run " +
      `charswap resume ${layout.dataset.jobId}.`;
  }

  async function tick() {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        generating = Object.values(data.generation || {}).includes("running");
        const now = JSON.stringify({ stages: data.stages, generation: data.generation });
        if (now !== shown) {
          if (!playing()) {
            location.reload();
            return;
          }
          // never interrupt playback: offer a refresh and keep polling
          document.getElementById("new-results").hidden = false;
        }
        if (data.poll === "off") return; // final video exists: nothing more to watch
      }
    } catch (error) {
      // the viewer may be restarting; keep trying
    }
    if (pollMode === "fast" && !generating && Date.now() - startedWaiting > STALL_MS) {
      stalled();
      return;
    }
    window.setTimeout(tick, POLL_MS[pollMode]);
  }
  window.setTimeout(tick, POLL_MS[pollMode]);
```

(`JSON.stringify` keeps key order, and the server writes `stages` then `generation` in the same order, so an unchanged job compares equal.)

- [ ] **Step 4: Run** `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/web/view_model.py src/character_swap/web/app.py src/character_swap/web/templates/job.html src/character_swap/web/static/viewer.js tests/test_view_model.py tests/test_web_app.py
git commit -m "fix: viewer tracks generation separately and slow-polls failed or waiting jobs"
```

---

### Task 13: Viewer Draft and Final sections, statuses and cost rows

**Files:** Modify `src/character_swap/web/view_model.py`, `src/character_swap/web/templates/job.html`, `tests/viewer_jobs.py`, `tests/test_view_model.py`, `tests/test_web_app.py`.

Two new sections after Review, shown for modes that generate in this version (Wan direct): video, "Why this step?", "View on Replicate ↗" (from the saved prediction record), and a cost row per finished stage using the estimate logged when the prediction was created. Header statuses gain "Generating", "Draft ready" and "Finished". A failed draft/final tells the user to run the generate command, not `resume`. The viewer stays GET-only.

- [ ] **Step 1: Failing tests**

`tests/viewer_jobs.py` — add the generation artifacts so `finish_stage` works for them:

```python
ARTIFACTS = {
    "normalize": ["original.mp4"],
    "separate": ["vocals.wav"],
    "pitch": ["vocals-plus3.wav"],
    "describe": ["scene.jpg"],
    "depth": ["depth.mp4"],
    "composite": ["mask.mp4", "composite-silent.mp4"],
    "face_mesh": ["composite-silent.mp4"],
    "embed": ["prepared.mp4"],
    "draft": ["draft.mp4"],
    "final": ["final.mp4"],
}
RECORDS = {
    "separate": "demucs-prediction.json",
    "transcribe": "whisper-prediction.json",
    "describe": "describe-prediction.json",
    "depth": "depth-prediction.json",
    "masks": "sam/sam-prediction.json",
    "draft": "draft-prediction.json",
    "final": "final-prediction.json",
}
```

`tests/test_view_model.py`:
- add `from character_swap.modes import GENERATION_STAGES` and change the `viewer_jobs` import to `from viewer_jobs import finish_stage, make_job, write_record`;
- `SECTION_IDS["direct"]` becomes `["upload", "source", "review", "draft", "final"]`;
- in `test_sections_cover_every_stage_of_the_mode` the assertion becomes `assert covered - set(GENERATION_STAGES) == set(vm.STAGES_BY_MODE[mode])`;
- in `test_finished_job_page` replace the loop with:

```python
    for section in view.sections:
        if section.id in ("review", "draft", "final"):
            continue  # waiting for the user, not part of preparation
        assert section.status == "done", section.id
        assert all(item.state == "ready" for item in section.media), section.id
```

- append:

```python
# ---- generation sections (Plan 2a) ---------------------------------------------------
def test_direct_job_shows_draft_and_final(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert draft.status == "pending" and draft.media[0].state == "waiting" and draft.links == ()
    assert view.status == "Ready for review"
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-draft"})
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert draft.status == "done" and draft.media[0].url == f"/jobs/{job.id}/files/draft.mp4"
    assert [link.url for link in draft.links] == ["https://replicate.com/p/pred-draft"]
    assert view.status == "Draft ready"
    rows = [(row.step, row.provider, row.usd, row.link) for row in view.costs.rows]
    assert rows == [("Draft (480p)", "Replicate", 0.08, "https://replicate.com/p/pred-draft")]
    assert view.costs.completed_usd == pytest.approx(0.08)


def test_generating_and_finished_statuses(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.mark_stage("draft", "running")
    assert vm.job_view(job, runs_dir).status == "Generating"
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    view = vm.job_view(job, runs_dir)
    assert view.status == "Finished" and sections(view)["final"].status == "done"


def test_failed_draft_says_which_command_to_run(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.mark_stage("draft", "failed", error="Generating the 480p draft failed: boom")
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert view.status == "Failed"
    assert draft.error == "Generating the 480p draft failed: boom"
    assert draft.retry == f"charswap generate {job.id} --draft"
    assert sections(view)["upload"].retry == f"charswap resume {job.id}"


def test_seedance_jobs_have_no_generation_sections_yet():
    for mode in ("depth", "face_mesh", "depth_mesh"):
        assert not {"draft", "final"} & {s.id for s in vm.sections_for(mode)}
```

`tests/test_web_app.py` — append:

```python
def test_failed_draft_page_names_the_generate_command(client, runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.mark_stage("draft", "failed", error="boom")
    text = client.get(f"/jobs/{job.id}").text
    assert f"Fix the issue, then run <code>charswap generate {job.id} --draft</code>" in text
    assert 'class="badge status-failed" id="job-status"' in text


def test_finished_wan_job_page(client, runs_dir, media):
    from viewer_jobs import finish_stage

    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    text = client.get(f"/jobs/{job.id}").text
    assert 'class="badge status-done" id="job-status">Finished' in text
    assert f"/jobs/{job.id}/files/final.mp4" in text and 'data-poll="off"' in text
```

- [ ] **Step 2: Run** `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov`
Expected: FAIL — no `draft`/`final` sections, `SectionView` has no `retry`, statuses unchanged, template has no badge class for "Finished" (Jinja `KeyError`/undefined → empty class).

- [ ] **Step 3: Implement**

(a) `src/character_swap/web/view_model.py`:
- change the modes import to `from character_swap.modes import GENERATION_STAGES, MODES`;
- below `ALL_MODES = ...` add:

```python
# Modes whose draft/final can be generated in this version (Seedance joins in Plan 2b).
GENERATING_MODES = tuple(name for name, mode in MODES.items() if mode.generates)
```

- add to `STEP_NAMES`: `"draft": "Draft (480p)",` and `"final": "Final (720p)",`;
- `Section` gets a last field:

```python
    retry: str = "resume {id}"  # after `charswap`; {id} = job id (one form: generate <id> --draft)
```

- append two sections to `SECTIONS`, after the `review` section:

```python
    Section(
        id="draft",
        title="Draft (480p)",
        why=(
            "A cheaper 480p test run on Replicate. Wan copies the motion of your clip onto "
            "the character, then your original soundtrack is put back unchanged."
        ),
        modes=GENERATING_MODES,
        stages=("draft",),
        assets=(Asset("Draft with the original sound", "draft.mp4", "video", "draft"),),
        records=(Record(ON_REPLICATE, "draft-prediction.json", "draft"),),
        retry="generate {id} --draft",
    ),
    Section(
        id="final",
        title="Final (720p)",
        why=(
            "The same request with the same seed at 720p, so it matches the draft you liked. "
            "The original soundtrack is restored again."
        ),
        modes=GENERATING_MODES,
        stages=("final",),
        assets=(Asset("Final with the original sound", "final.mp4", "video", "final"),),
        records=(Record(ON_REPLICATE, "final-prediction.json", "final"),),
        retry="generate {id} --final",
    ),
```

- `SectionView` gets a last field `retry: str = ""` (the full command to show, e.g. `charswap generate <id> --draft`), and `_section_view` passes `retry="charswap " + section.retry.format(id=job.id),` in its `SectionView(...)` call;
- replace `logged_cost` and extend `cost_rows`:

```python
def _cost_entries(state: dict[str, Any]) -> list[dict[str, Any]]:
    entries = state.get("costs", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def logged_cost(state: dict[str, Any]) -> float:
    return round(sum(_usd(entry.get("usd")) for entry in _cost_entries(state)), 4)


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
            CostRow(STEP_NAMES[stage], "Replicate", usd, prediction_url(job, PAID_RECORD[stage]))
        )
    for stage in GENERATION_STAGES:  # priced by length: use the estimate logged at creation
        if stage_status(state, stage) != "done":
            continue
        usd = round(
            sum(_usd(e.get("usd")) for e in _cost_entries(state) if e.get("step") == stage), 4
        )
        rows.append(
            CostRow(STEP_NAMES[stage], "Replicate", usd, prediction_url(job, PAID_RECORD[stage]))
        )
    return tuple(rows)
```

- replace `overall_status`:

```python
def overall_status(state_progress: dict[str, Any], decision: dict[str, Any]) -> str:
    generation = state_progress["generation"]
    if "failed" in state_progress["stages"].values() or "failed" in generation.values():
        return "Failed"
    if "running" in generation.values():
        return "Generating"
    if state_progress["active"]:
        return "Preparing"
    if generation["final"] == "done":
        return "Finished"
    if generation["draft"] == "done":
        return "Draft ready"
    return {"approved": "Approved", "rejected": "Rejected"}.get(
        str(decision.get("status")), "Ready for review"
    )
```

(b) `src/character_swap/web/templates/job.html`:
- the `badge_class` line becomes:

```html
{% set badge_class = {"Failed": "status-failed", "Rejected": "status-failed", "Preparing": "status-running", "Generating": "status-running", "Ready for review": "status-done", "Approved": "status-done", "Draft ready": "status-done", "Finished": "status-done"} %}
```

- in the error box use the section's command:

```html
        {% if section.stopped %}
        <p>Stopped (Ctrl-C). Run <code>{{ section.retry }}</code> to continue.</p>
        {% else %}
        <p>{{ section.error }}</p>
        <p>Fix the issue, then run <code>{{ section.retry }}</code>.</p>
        {% endif %}
```

- [ ] **Step 4: Run** `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov` → PASS (the "Why this step?" test also checks the new copy: > 40 characters, no em dashes).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/web/view_model.py src/character_swap/web/templates/job.html tests/viewer_jobs.py tests/test_view_model.py tests/test_web_app.py
git commit -m "feat: show Wan draft and final in the job viewer"
```

---

### Task 14: Full verification and docs

**Files:** Modify `README.md`, `CLAUDE.md`, `docs/scratchpad.md`, `activity.md`.

- [ ] **Step 1: Full suite with coverage** — `uv run pytest`
Expected: all pass (≈ 490 tests; was 400), `Required test coverage of 85% reached` (≈ 97 %). New modules should be ≥ 95 %: `modes.py`, `errors.py`, `providers/errors.py`, `providers/generators/*`. If below 85 %, add tests for the `term-missing` lines; only `build_services` and `run_server` may carry `pragma: no cover`.

- [ ] **Step 2: Lint check** — `uv run ruff format --check src tests && uv run ruff check src tests` → no findings.

- [ ] **Step 3: Free smoke check** (no network, no money):

```bash
uv run charswap --help | grep -n "generate"
uv run charswap generate --help
```

Expected: `generate` listed with "(paid; asks first)"; its help shows `--draft`, `--final`, `--yes`.

- [ ] **Step 4: README** — replace the `**Status:**` paragraph and add a usage section before `## License`:

```markdown
**Status:** work in progress. The open-source Wan path works end to end; Seedance (PiAPI) is next. The design is in [docs/superpowers/specs/](docs/superpowers/specs/2026-10-22-character-swap-v1-design.md).

## Try it with Wan (open source, on Replicate)

You need [uv](https://docs.astral.sh/uv/), Rubber Band (`brew install rubberband`) and a [Replicate API token](https://replicate.com/account/api-tokens) in `.env` as `REPLICATE_API_TOKEN=...`.

1. `uv sync`
2. `uv run charswap prepare clip.mp4 --generator wan --character me.jpg`
   Use a clip of up to 30 s with **one** person, and a clear, well-lit photo of **one** person. This step is free.
3. `uv run charswap view` and watch the normalized clip and your photo. Then copy the fingerprint from `uv run charswap status <id>` and approve: `uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>`.
4. `uv run charswap generate <id> --draft` makes a 480p test (about $0.02 per second of video; you see the estimate and confirm first).
5. Happy with it? `uv run charswap generate <id> --final` makes the 720p version with the same seed (about $0.05 per second).

The result is `runs/<id>/final.mp4`, with your clip's original soundtrack put back unchanged. Prices are estimates; check replicate.com billing for exact charges. If a step stops, run the same command again: saved requests are resumed, never paid twice.
```

- [ ] **Step 5: CLAUDE.md** — under Commands add `- Generate (paid, asks first): \`uv run charswap generate <job> --draft|--final\``. (The hard-rule wording was already updated in Task 5.)

- [ ] **Step 6: docs/scratchpad.md**
  - Active work: replace the Plan 2 line with `- [x] Plan 2a — Wan generation (draft/final, original audio), live-run fixes, prerequisites` and add `- [ ] Plan 2b — hosting (R2/tmpfiles), PiAPI Seedance 2.5 + face library, face check`.
  - "Do first in Plan 2": tick normalize metadata, `providers/errors.py` + `CharSwapError`, single `modes.py` table, pipeline owns stage order with a separate generation group; tick "Spend log: one entry per paid call" only for generation and add "(prep still logs one 'prepare' entry; HF depth still counted, Plan 3)"; leave whole-second clip length (2b), rename `original.mp4` (Plan 3, decision P10), per-job file lock (Plan 3, decision P11) unticked with those notes.
  - "Found in the first live run": tick rate limits, Whisper, viewer slow-poll.
  - Before Plan 3: add "Hashed draft approval before the final (2a uses the cost confirmation)" and "Upload audio that cannot be stream-copied into mp4 (e.g. PCM in .mov): re-encode to AAC fallback".

- [ ] **Step 7: activity.md** — append an entry: date, goal (Plan 2a), what was built (test count + coverage from Step 1), decisions P1, P5, P6, P7, P8, P10, P11 in one line each, "Not done: live Wan test (Task 15, needs Frank's OK)".

Also stage `.gitignore`: its pending change adds `inputs/` so third-party clips and photos are never committed. Keep that line.

```bash
git add .gitignore README.md CLAUDE.md docs/scratchpad.md activity.md docs/research/2026-10-22-provider-api-facts.md docs/superpowers/plans/2026-11-03-plan-2a-wan-generation.md
git commit -m "docs: Wan usage, Plan 2a journal and checklist"
```

---

### Task 15: Live Wan test (Frank; paid; needs his explicit OK)

Not for a subagent. Ask Frank before every paid command and show the estimate. Expected total for an 8 s clip: draft ≈ $0.16, final ≈ $0.40.

The existing job `runs/c0fcbc108641467386fe5306d5e9f4af` is not suitable (two people, Seedance depth mode). Direct mode needs a new job.

- [ ] **Step 1: Frank picks the inputs** (put them in `inputs/`, which git ignores): a clip of about 5–10 s with **one** person, steady camera, clear audio; and a well-lit photo of **one** person (his own face, or a character he has the rights to). Short clip = cheaper test.

- [ ] **Step 2: Prepare (free).**

```bash
uv run charswap prepare "inputs/<clip>" --generator wan --mode direct --character "inputs/<photo>"
```

Expected: `JOB <id>`, "Normalizing video to 24 fps", "Ready for review", then the hint to watch `original.mp4`. The cost list is empty (direct mode makes no paid prep calls); answer `y`.

- [ ] **Step 3: Review in the viewer.** `uv run charswap view` → open the job: "Source & character" shows the normalized clip and the photo; Draft/Final sections say "Waiting for this step…"; the header says "Ready for review". Then:

```bash
uv run charswap status <id>        # copy review.fingerprint; "next" says to review
uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>
uv run charswap status <id>        # "next": charswap generate <id> --draft
```

- [ ] **Step 4: Draft (paid, ask Frank first).** `uv run charswap generate <id> --draft` → read the estimate to Frank, confirm only on his yes. Keep the viewer open: Draft goes "running" (header "Generating") within ~10 s, then "done" with the video and a "View on Replicate ↗" link. Watch `runs/<id>/draft.mp4`: is it his character, does the motion match, is the original sound in sync?

- [ ] **Step 5: Record the real numbers.** On replicate.com open the prediction (link in the viewer): note the actual charge, predict time, and the version id that ran. Probe the raw output:

```bash
uv run python -c "import av,sys; c=av.open(sys.argv[1]); s=c.streams.video[0]; print(s.average_rate, s.frames, float(s.duration*s.time_base), s.codec_context.width, s.codec_context.height)" "runs/<id>/draft-raw.mp4"
```

If the charge differs from the estimate, update `PRICES["wan-480"]`/`["wan-720"]` in `src/character_swap/costs.py` with today's `checked_on` date and the Wan section of the research doc (tests read `PRICES`, so they keep passing).

- [ ] **Step 6: Final (paid, ask Frank first)** — only if he liked the draft: `uv run charswap generate <id> --final`. Check `runs/<id>/final.mp4` (720p, same look as the draft, original sound). Note the charge.

- [ ] **Step 7: Stop the viewer** (Ctrl-C) and tell Frank it's stopped. Log real cost, run time, output fps/length and any soundtrack-fit message in `activity.md`.

If something fails: a `DurationMismatchError` means Wan's output length differed by more than 0.5 s (record the numbers; widen the rule only after looking at the video). A rate-limit message means wait a minute and run the same command again (nothing was charged). Any "may have been charged" message: check replicate.com/predictions before deleting the named record.

---

## Deferred to Plan 2b / Plan 3

| Item | Where | Why not now |
|---|---|---|
| File hosting (R2 presigned + tmpfiles fallback) | 2b | Wan uses Replicate's private file upload; only PiAPI needs public URLs |
| PiAPI Seedance 2.5 generator (`providers/generators/seedance_piapi.py`), draft→1080p promote, face library (`faces.json`), `charswap check-face` | 2b | Separate provider; plugs into the Generator interface from Task 9 and the Draft/Final sections (add `generates=True` to the Seedance modes) |
| Whole-second clip length (PiAPI `duration` is an int) | 2b | Wan takes any length; the restore now tolerates ±0.5 s either way |
| Prompt review before a Seedance draft | 2b | Wan has no prompt |
| Rename `original.mp4` → `normalized.mp4` | Plan 3 | Decision P10 |
| Per-job file lock / cross-process run lock with heartbeat | Plan 3 | Decision P11 |
| Per-call spend entries for prep stages; don't count depth when it ran on Hugging Face | Plan 3 | Prep costs cents and logs one "prepare" entry; viewer already shows HF depth as free |
| `web/sections.py` with a per-section `texts` hook (remove id branching) | Plan 3 | Pure refactor; 2a only adds two table rows |
| Hashed approval of the draft before the final | Plan 3 (UI) | 2a uses the cost confirmation plus "watch the draft" text (decision P4) |
| Persisting the full `GenerationRequest` in state.json | 2b if needed | Rebuilt from state today (decision P3) |
| `charswap cancel <job>` for a running prediction | Backlog | Not needed for the first runs |
| Wan prep without `REPLICATE_API_TOKEN`, shared `services.py`, closing `httpx.Client` | Plan 3 | Already in "Before Plan 3" |
| Re-encode-to-AAC fallback when the upload's audio can't be copied into mp4 | Plan 3 | Seen only with unusual containers; restore fails with a clear error today |
| depth_mesh writes `depth-composite-silent.mp4` directly; stage messages read prices from `costs` | Plan 3 | Seedance-only cleanups |
| SAM arm-leak item in the review checklist | Plan 3 | Seedance depth modes only |
| Owner uuid in the "creating" marker, so a 429 retry only ever removes its own marker | Plan 3 (with the per-job lock) | 2a releases/re-claims with exclusive create, which already blocks a second POST |
| A run that loses the 429 re-claim race marks the stage "failed" while the winning run is still running | Plan 3 (per-job lock) | Cosmetic state glitch; no money risk (the loser never posts) |
