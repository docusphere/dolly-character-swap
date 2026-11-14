# Click-through App Implementation Plan (Plan 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app, `charswap app`, that does a whole character swap by clicking, built for Frank to film for the YouTube video: a home page, a step bar and five step screens (Upload, Prepare, Check, Draft, Final), each with one short line, technique tags and a before/after wipe player, then buttons that run the existing `charswap` commands with every money-safety rule intact.

**Architecture:**
- The read-only visual walkthrough lands first (Tasks 1 to 4) over existing job files, with no POST route at all, and Task 5 is a browser checkpoint on Frank's real job before any action is built.
- Pure page models (`web/pages.py`, `web/view_model.py`, `web/actions.py`) build everything a screen shows from the job folder; templates read their words only from `web/steps.py`; one script (`static/app.js`) drives the wipe player, live polling, the forms and the price dialog.
- Long work (prepare, resume, repair, draft, final) runs as the same `charswap` commands in child processes (`web/runner.py`), so the CLI's tested rules apply unchanged; quick actions (approve, prompt save) call the CLI's Python functions in-process.
- Money: a cross-process `run.lock` per job (`lock.py`), and `--confirmed-usd` so the command itself refuses unless what it is about to charge is exactly the price the person confirmed; `pipeline.planned_charge` and `pipeline.generate_refusal` are shared by the CLI and the app so the two can't disagree.
- Every POST needs the per-launch token and the app's own Origin; read-only jobs (PiAPI, Wan, `face_mesh`, two people) get no buttons; GET routes never change a job and `/api/jobs/{id}` never hashes.

**Tech Stack:** Python 3.12, uv, FastAPI + Jinja2 (+ python-multipart for uploads), Starlette TestClient, `fcntl.flock`, `subprocess.Popen(start_new_session=True)`, vanilla JS (no build step, no inline scripts), pytest + pytest-cov (≥ 85 % enforced), ruff.

---

**Spec:** `docs/superpowers/specs/2026-11-13-click-through-app-design.md` (decisions A1 to A15 and every section). This plan implements that spec; the places where the code needed a choice the spec does not make are listed under "Spec deviations and gap fills" at the end.

**Builds on:** branch `feat/click-through-app` as committed (main plus the spec; 1003 tests passing, 96.94 % coverage). The code below targets these existing APIs:
- `cli.main(argv, *, settings, out, err, confirm, services_factory, serve, open_browser, piapi_factory)`, `Env` (`proceed`, `track`), `KEYS`, `RENAMED`, `_parser`, `_prepare`, `_resume`, `_repair`, `_generate`, `_view`, `_port`, `_port_free`, `COMMANDS`, `build_services`, `run_server`
- `pipeline.prepare`, `repair_with_rgb`, `generate`, `_tracked`, `_wrap`, `_write_atomically`, `generation_files`, `already_paid`, `needs_new_charge`, `resumes_without_charge`, `saved_request_unconfirmed`, `stale_draft`, `stale_generation`, `draft_prompt_changed`, `refuse_another_final`, `check_can_generate`, `final_resolution`, `build_request`, `generation_prompt`, `generator_for`, `generator_key`, `pin_provider`, `seedance_provider`, `is_promote`, `remaining_paid_stages`, `default_prompt`, `write_prompt_file`, `GENERATOR_CLASSES`, `FINAL_RESOLUTIONS`, `PROMPT_FILE`, `PAID_STAGES`, `PREP_STAGES_BY_MODE`, `Services`
- `review.fingerprint`, `review.decide`, `review.status`, `ReviewError`; `jobs.Job` (`create`, `load`, `path`, `state`, `mark_stage`, `update`), `JobError`, `SEEDANCE_PROVIDERS`
- `prompts.build_prompt`, `check_placeholders`, `validate_prompt`, `_clean`, `_truncate`, `PromptError`, `MAX_PROMPT`
- `costs.estimate_prepare`, `estimate_stages`, `estimate_repair`, `Estimate`, `PRICES`, `record_spend`, `total_spend`
- `modes.MODES`, `GENERATOR_LIMITS`, `GENERATION_STAGES`, `PROVIDER_NAMES`; `config.Settings`, `WHERE_TO_GET`; `faces.check_photo`, `FaceError`, `FaceLibrary`
- `providers.errors.OutputExpired`, `ProviderError`; `providers.wavespeed.unconfirmed_message`, `providers.replicate.unconfirmed_message`, `providers.piapi.unconfirmed_message`; `SeedanceWaveSpeedGenerator` (`estimate`, `finals`)
- `media.video.stream_seconds`, `FPS`
- `web.app.create_app`, `SECURITY_HEADERS`, `ALLOWED_SUFFIXES`, `LOCAL_HOSTS`; `web.view_model.read_state`, `stage_status`, `combine`, `progress`, `prediction_id`, `prediction_url`, `file_url`, `composite_name`, `tilde`, `video_facts`, `review_status`, `cost_rows`, `costs_view`, `completed_cost`, `logged_cost`, `stale_stages`, `list_jobs`, `JobSummary`
- tests: `tests/fakes.py` (`FakeReplicate`, `FakeWaveSpeed`), `tests/viewer_jobs.py` (`Media`, `make_job`, `finish_stage`, `write_record`, `RECORDS`), `tests/helpers.py` (`solid`, `tone`, `write_video`, `write_wav`, `write_mask_pngs`), `tests/conftest.py` (`runs_dir`, `media`), `tests/test_cli.py` (`run`, `Recorder`, `FakeServer`, `free_port`, `_photo`, `_prepare_ids`, `_finish_stages`, `_interrupted_draft`, `_approved_wavespeed_job`, `wavespeed_services`, `ws_settings`, `prep_settings`, `settings`, `video`)

**Dry run:** every code block in this plan was applied in order, by a script that reads the **Create** / **Replace** / **Append** / **Delete** blocks exactly as written, to a scratch worktree of `feat/click-through-app`; after each task the task's tests, `ruff format --check`, `ruff check` and the full suite ran. Every task passed; after Task 14 the suite has **1128 passing tests (1 live test deselected), 96.83 % coverage**, ruff clean (the full suite was run after Tasks 4, 9 and 14; every other task ran its own tests and ruff). The full suite grows 1003 → 1016 → 1001 (Task 2 removes the old section tests) → 1004 → 1006 → 1023 → 1035 → 1049 → 1064 → 1086 → 1093 → 1127 → 1128.

## Decisions taken in this plan

| # | Decision | Why |
|---|---|---|
| P1 | Order: the read-only screens and the wipe player first (Tasks 1 to 4), a browser checkpoint on Frank's real job (Task 5), then the CLI foundations (lock, `--describe`, `--id-file`, `expired`, `--confirmed-usd`) and only then the runner and the POST routes. | The visual walkthrough is what the video needs; it can be shown with zero risk of a charge because no POST route exists yet. Each CLI piece is testable alone before the app uses it. |
| P2 | Prepare tabs are plain links (`/jobs/{id}/step/2?tab=depth`), rendered on the server; with no tab asked for, the newest ready tab is shown. | No tab widget in JS; one wipe player per page (less video loading); the page "fills in live" as each reload lands on the newest result. |
| P3 | `web/pages.py` builds the step screens; `web/view_model.py` keeps the shared helpers and the cost table (`PAID_RECORD` written out); `SECTIONS`, `job_view`, `jobs.html`, the old `job.html` and `viewer.js` go. `viewer.css` keeps its name and token system. | Spec §4.8; small focused files; one stylesheet to theme. |
| P4 | `cli.main` takes the job lock for `resume`, `review`, `prompt`, `repair` and `generate` before the command runs; `prepare` takes it right after `Job.create` and writes `--id-file` inside it. The holder clears `run.lock`'s content before letting go. | One place for every command; a busy job exits 2 before anything runs. Clearing matters because the server itself takes the lock (approve, prompt): its pid stays alive, so leftover content would read as "running" forever. |
| P5 | A process's start time comes from `ps -o lstart= -p <pid>`. | Works on macOS and Linux with no new dependency; the lock is macOS/Linux only anyway. |
| P6 | `planned_charge(job, command, resolution="", *, services=None)` plus `planned_generation(...)` (the request and its estimate, or `None`) and `remake_reasons(job, stage)`; without services the app prices with `GENERATOR_CLASSES[key](None)`. | The CLI needs the request to print the prompt it sends; the app has no clients (no keys needed to price). The estimate methods never touch the client. |
| P7 | `generate_refusal` returns one message; `charswap generate` prints it on stderr and exits 2 (before building services or asking anything). | One function, one order, used by both; the CLI's texts are unchanged, only the unconfirmed-marker intro moves from stdout to stderr. |
| P8 | The runner keeps its children in memory; a new swap's start id is its `runs/_incoming/<id>` folder name; that prepare's log is appended to `runs/<id>/app.log` when the child exits. | Spec §4.1; the log ends up with the job; after a server restart the job simply shows on the home list. |
| P9 | The POST guard (token + Origin) arrives with the first POST route (Task 10), and the read-only/busy 409 with the first job POST (Task 11), rather than in a last "security" task. | No POST route ever exists unguarded, even between tasks. |
| P10 | Every POST is `fetch` with JSON (FormData for the upload) and answers JSON, including errors. | The page shows the message next to the field or button; no full-page form posts. |
| P11 | In the wipe player the "after" video leads (it carries the sound) and "before" follows; its `ended` event fires `wipe:ended`, which unlocks Approve on step 3. | Corrections never jump the audible side; one event serves the approve gate. |
| P12 | "Continue (no new charge)" posts `confirm_usd` 0 with no dialog; prep's Try again/Continue goes to `/retry` (resume), a draft or final's to `/draft` / `/final`. | `X = 0` means "no new charge allowed": if anything would be charged, the command refuses. |
| P13 | A step page embeds exactly the keys and values `/api/jobs/{id}` returns (`stages`, `generation`, `running`, `failure`, `decision`) and reloads when any changes. | The page follows the job without parsing output; equal values mean it can never reload in a loop. |
| P14 | Money is shown with `costs.usd_text` ("$0.88"; three decimals under a cent, "$0.002"); confirmed prices are passed as `--confirmed-usd` with six decimals. | Small charges never read as "$0.00"; six decimals carry micro-dollars exactly. |

## Conventions for every task

- Run commands from the repo root: `"/Users/franciscojosejimeneznillardkam/Documents/Frank DOCS/Frank Projects/youtube/hf-genjutsu/character-swap"` (the path contains spaces; quote it). Branch: `feat/click-through-app`.
- How to read the code steps: **Create** `path` writes a new file; **Replace the whole file** `path` overwrites it; **Replace** in `path` swaps the first block for the second (the first block appears exactly once in the file at that point; tasks apply in order); **Append** to `path` adds the block at the end; **Delete** `path` is a `git rm`.
- Tests: `uv run pytest <path> -q --no-cov` while iterating; the full suite with the coverage gate is `uv run pytest`. Lint and format before every commit.
- Stage files **by name**; never `git add -A` / `git add .`. No attribution lines. Never commit `.env`, `runs/`, `inputs/`, `faces/`, `outputs/`, `models/`.
- Tests never call the network or a paid API: they use `tests/fakes.py`, and the app's tests use `FakeProcesses` (a `popen` that starts nothing) or `tests/fake_cli.py` (the real CLI on fakes). Nothing in this plan runs `charswap prepare/generate` against real keys.
- User-facing copy: plain English, short, no em dashes, no provider names outside the small price line (A11); no inline scripts or styles (the CSP forbids them); colors and spacing only from the tokens in `viewer.css` (light and dark); buttons rounded, never pills (docs/ui-ux-best-practices.md).
- If you start a server to look at something, say so and stop it when done.
- Never mention the original project's author or brands; never copy from `_legacy/`.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/character_swap/web/steps.py` | Create | every on-screen step text: titles, one-line explanations, technique tags, button labels, the file each Prepare tab shows |
| `src/character_swap/web/pages.py` | Create | pure page models: the home page, the step bar, each step screen (`job_page`) |
| `src/character_swap/web/actions.py` | Create | §4.4: what each step offers when idle (buttons with prices, "needs your decision", refusals) |
| `src/character_swap/web/runner.py` | Create | starts `charswap` commands as child processes, follows them, keeps `app.log` |
| `src/character_swap/web/uploads.py` | Create | the free checks on a new swap's clip and photo |
| `src/character_swap/web/view_model.py` | Rewrite | shared helpers, the cost table, `current_step`, `is_app_job`, the home list |
| `src/character_swap/web/app.py` | Modify | the routes: screens, files, polling, estimate, the POSTs and their guard |
| `src/character_swap/web/templates/*.html` | Rewrite/Create | base, home, new, job + one partial per step, bar, macros, message |
| `src/character_swap/web/static/viewer.css` | Rewrite | tokens (light, dark), layout, the wipe player, the dialog |
| `src/character_swap/web/static/app.js` | Create (replaces `viewer.js`) | the wipe player, polling, the upload, approve gate, prompt editor, price dialog |
| `src/character_swap/lock.py` | Create | the cross-process job lock (`run.lock`) |
| `src/character_swap/__main__.py` | Create | `python -m character_swap` |
| `src/character_swap/cli.py` | Modify | `app` (+ `view` alias), the lock, `--describe`, `--id-file`, `--confirmed-usd`, `_generate` on the shared refusals |
| `src/character_swap/pipeline.py` | Modify | `planned_charge`, `planned_generation`, `remake_reasons`, `generate_refusal`, `UNCONFIRMED`, `save_prompt`, the `expired` flag, the description in `default_prompt` |
| `src/character_swap/prompts.py` | Modify | `clean_description`, the description line in `build_prompt` |
| `src/character_swap/jobs.py` | Modify | `Job.create(description=...)` |
| `src/character_swap/costs.py` | Modify | `usd_text`, `micro_usd`, `price_changed` |
| `tests/fakes.py` | Modify | `FakeProcesses`, `FakeDetector`, `prep_replicate`, file logs for the fakes |
| `tests/fake_cli.py` | Create | the real CLI on fakes, for the integration test |
| `tests/test_steps.py`, `test_pages.py`, `test_actions.py`, `test_lock.py`, `test_runner.py`, `test_click_through.py` | Create | tests per task |
| `tests/test_view_model.py`, `test_web_app.py` | Rewrite | the viewer's tests, moved to the app |
| `tests/test_cli.py`, `test_prompts.py`, `test_jobs.py`, `test_pipeline.py`, `viewer_jobs.py` | Modify | tests per task |
| `pyproject.toml`, `uv.lock` | Modify | `python-multipart` |
| `CLAUDE.md`, `README.md`, `activity.md`, `docs/scratchpad.md` | Modify | the app in the docs, journal, checklist |

---

### Task 1: Every step text in one file (`web/steps.py`)

The wording comes first, so every screen built later reads its texts from one place (A9,
A10). Plain English, one short line per step, technique tags with a one-sentence tip, the
button labels, the short messages (the price question, "needs your decision", …), and which
file each Prepare tab shows. Nothing reads this file yet.

**Files:**
- Create: `src/character_swap/web/steps.py`
- Test: `tests/test_steps.py`

- [ ] **Step 1: Write the tests**

**Create** `tests/test_steps.py`:

````python
import pytest

from character_swap.modes import MODES
from character_swap.web import steps

SEEDANCE_MODES = ("depth", "face_mesh", "depth_mesh")


def _texts():
    for item in steps.STEPS:
        yield item.title
        yield item.heading
        yield item.line
        for tag in item.tags:
            yield tag.tip
    for tab in steps.TABS:
        yield tab.title
        yield tab.line
        for tag in tab.tags:
            yield tag.tip
    yield from steps.HOW_IT_WORKS
    for _mode, label, hint in steps.LOOKS:
        yield label
        yield hint
    for name in dir(steps):
        value = getattr(steps, name)
        if name.isupper() and isinstance(value, str):
            yield value


def test_five_steps_in_order():
    assert [s.number for s in steps.STEPS] == [1, 2, 3, 4, 5]
    assert [s.title for s in steps.STEPS] == ["Upload", "Prepare", "Check", "Draft", "Final"]
    assert steps.step(4).id == "draft"
    with pytest.raises(ValueError, match="no step 6"):
        steps.step(6)


def test_every_text_is_short_plain_and_has_no_em_dash():
    for text in _texts():
        assert text and "—" not in text, text
    for item in steps.STEPS:
        assert len(item.line) <= 90, item.line  # one short line per step
    for tab in steps.TABS:
        assert len(tab.line) <= 90, tab.line


def test_every_tag_has_a_one_sentence_tip():
    tags = [tag for item in steps.STEPS for tag in item.tags]
    tags += [tag for tab in steps.TABS for tag in tab.tags]
    names = {tag.name for tag in tags}
    assert {"SAM 3", "Depth Anything", "MediaPipe Face Mesh", "Whisper", "Demucs"} <= names
    for tag in tags:
        assert tag.tip.endswith(".") and tag.tip.count(". ") == 0, tag.name


@pytest.mark.parametrize(
    ("mode", "ids"),
    [
        ("depth", ["depth", "cutout", "prepared", "voice"]),
        ("depth_mesh", ["depth", "cutout", "face", "prepared", "voice"]),
        ("face_mesh", ["face", "prepared", "voice"]),
        ("direct", []),
    ],
)
def test_tabs_per_mode(mode, ids):
    assert [tab.id for tab in steps.tabs_for(mode)] == ids


@pytest.mark.parametrize("mode", SEEDANCE_MODES)
def test_tabs_cover_the_modes_prep_stages(mode):
    covered = {stage for tab in steps.tabs_for(mode) for stage in tab.stages}
    # normalize makes the "before" side of every wipe; describe feeds the prompt only
    assert covered | {"normalize", "describe"} == set(MODES[mode].prep_stages)


def test_tab_files():
    by_id = {tab.id: tab for tab in steps.TABS}
    assert by_id["depth"].after == "depth.mp4"
    assert by_id["cutout"].after == "@composite"  # depth-composite-silent.mp4 in depth_mesh
    assert by_id["face"].after == "composite-silent.mp4"
    assert by_id["prepared"].after == "prepared.mp4"
    assert by_id["voice"].after == ""  # two audio players, no wipe


def test_looks_default_to_lip_sync():
    assert [mode for mode, _label, _hint in steps.LOOKS] == ["depth_mesh", "depth"]
    assert steps.LOOKS[0][1] == "Better lip sync" and steps.LOOKS[1][1] == "Better likeness"


def test_the_price_question_names_the_amount_and_who_charges_it():
    assert steps.CHARGE.format(usd="$0.88", provider="WaveSpeed") == (
        "Charge about $0.88 on WaveSpeed?"
    )
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_steps.py -q --no-cov`  
Expected: FAIL. `ImportError: cannot import name 'steps' from 'character_swap.web'` (a collection error).

- [ ] **Step 3: Write the code**

**Create** `src/character_swap/web/steps.py`:

````python
"""Every word the app shows for a step: titles, one-line explanations, technique tags, buttons.

Templates read their texts only from here, so the wording can be copied to another app.
Plain English, one short line per step, no em dashes (docs/ui-ux-best-practices.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tag:
    name: str  # the technique, e.g. "SAM 3"
    tip: str  # one sentence, shown as a tooltip


@dataclass(frozen=True)
class Tab:
    id: str
    title: str
    line: str
    tags: tuple[Tag, ...]
    stages: tuple[str, ...]  # the tab is ready once all of these are done
    after: str  # the "after" side of the wipe; "@composite" = view_model.composite_name; "" = audio
    modes: tuple[str, ...]  # the modes that show this tab


@dataclass(frozen=True)
class Step:
    number: int
    id: str
    title: str  # the step bar's label
    heading: str
    line: str
    tags: tuple[Tag, ...] = ()


FFMPEG = Tag("FFmpeg", "Sets the clip to 24 frames a second and puts the voice back in.")
DEMUCS = Tag("Demucs", "Separates the voice from the music and background noise.")
RUBBER_BAND = Tag("Rubber Band", "Raises the voice 3 semitones, so the AI hears the words only.")
WHISPER = Tag("Whisper", "Writes down the spoken words for the prompt.")
GPT_VISION = Tag("GPT-4o mini (vision)", "Describes the room, never the people, for the prompt.")
DEPTH_ANYTHING = Tag(
    "Depth Anything", "Colors everything by distance: the motion stays, the face goes."
)
SAM3 = Tag("SAM 3", "Finds the person in every frame and cuts them out.")
FACE_MESH = Tag("MediaPipe Face Mesh", "Tracks 478 points on the face for lip sync.")
SEEDANCE = Tag("Seedance 2.5", "Redraws every frame of the clip with your character.")

STEPS: tuple[Step, ...] = (
    Step(
        1,
        "upload",
        "Upload",
        "Upload",
        "A short clip of one person, and a photo of who they become.",
    ),
    Step(
        2,
        "prepare",
        "Prepare",
        "Prepare",
        "We hide who is in the clip, but keep how they move and what they say.",
    ),
    Step(3, "check", "Check", "Check", "This is all the AI will see. Watch it to the end."),
    Step(4, "draft", "Draft", "Draft", "A cheap 480p test run.", (SEEDANCE,)),
    Step(
        5,
        "final",
        "Final",
        "Final",
        "The final is a new take, so it will look a little different from the draft.",
        (SEEDANCE,),
    ),
)

DEPTH_MODES = ("depth", "depth_mesh")
SEEDANCE_MODES = ("depth", "face_mesh", "depth_mesh")
TABS: tuple[Tab, ...] = (
    Tab(
        "depth",
        "Depth",
        "Everything becomes colors by distance, so nobody can tell who it is.",
        (DEPTH_ANYTHING,),
        ("depth",),
        "depth.mp4",
        DEPTH_MODES,
    ),
    Tab(
        "cutout",
        "Cut-out",
        "Only the person is swapped for depth; the real room stays.",
        (SAM3,),
        ("masks", "composite"),
        "@composite",
        DEPTH_MODES,
    ),
    Tab(
        "face",
        "Face lines",
        "Face lines on top keep every lip movement.",
        (FACE_MESH,),
        ("face_mesh",),
        "composite-silent.mp4",
        ("face_mesh", "depth_mesh"),
    ),
    Tab(
        "prepared",
        "What the AI gets",
        "The motion and a raised voice, but not who it was.",
        (FFMPEG, GPT_VISION),
        ("embed",),
        "prepared.mp4",
        SEEDANCE_MODES,
    ),
    Tab(
        "voice",
        "Voice",
        "The voice is lifted out and raised 3 semitones. The words stay the same.",
        (DEMUCS, RUBBER_BAND, WHISPER),
        ("separate", "pitch", "transcribe"),
        "",
        SEEDANCE_MODES,
    ),
)

# Home page: how a swap works, in six short steps.
HOW_IT_WORKS: tuple[str, ...] = (
    "Your clip",
    "Hide the person",
    "Keep the voice for lip sync",
    "Show the AI your photo",
    "AI redraws every frame",
    "Original sound back",
)

# Upload choices: the "Look" radio button (value -> mode, label, hint).
LOOKS: tuple[tuple[str, str, str], ...] = (
    ("depth_mesh", "Better lip sync", "Face lines guide the mouth."),
    ("depth", "Better likeness", "The face looks more like your photo."),
)
RIGHTS = "I have the right to use this face. If it's a made-up character, tick it."
DESCRIBE = "Describe the person (optional)"
DESCRIBE_HINT = "Face shape, hair, age. It is added to the prompt."

# Buttons and short messages.
NEW_SWAP = "New swap"
PREPARE = "Prepare"
NEXT_CHECK = "Next: check it"
APPROVE = "Looks good, approve"
WATCH_FIRST = "Watch it to the end first."
REPAIR = "Redo the cut-out"
START_OVER = "Start over"
NEXT_DRAFT = "Next: draft"
MAKE_DRAFT = "Make draft"
ANOTHER_DRAFT = "Make another draft"
NEXT_FINAL = "Next: final"
DOWNLOAD = "Download"
TRY_AGAIN = "Try again"
CONTINUE_FREE = "Continue (no new charge)"
SAVE = "Save"
RESET = "Reset"
DRAFT_TIME = "Usually about 4 minutes. You can leave this page; it keeps going."
PREPARE_TIME = "About 2 minutes. You can leave this page; it keeps going."
KEPT_AS_OLD = "The current draft (and final, if any) are kept as draft-old-* / final-old-*."
READ_ONLY = "Made with the command line. Continue there: charswap status {id}"
DIRECT_PREPARE = "Wan copies the motion straight from your clip, so nothing is hidden."
WAITING = "Not made yet."
RUNNING = "Working on it…"
FILE_MISSING = "File missing."
NEW_RESULTS = "New results are ready."
REFRESH = "Refresh"
LAST_STOPPED = "The last command stopped:"
STALE_DRAFT = "This draft was made from files that are no longer approved."
NEEDS_DECISION = "Needs your decision."
DECISION_HOW = "Nothing will be bought again from here; do this from the command line:"
CHARGE = "Charge about {usd} on {provider}?"  # the price dialog's question
CHARGE_YES = "Yes, charge it"
CHARGE_NO = "Cancel"


def step(number: int) -> Step:
    """The step with this number (1 to 5)."""
    for item in STEPS:
        if item.number == number:
            return item
    raise ValueError(f"There is no step {number}.")


def tabs_for(mode: str) -> tuple[Tab, ...]:
    """The prepare tabs a job's mode shows, in page order."""
    return tuple(tab for tab in TABS if mode in tab.modes)
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_steps.py -q --no-cov`  
Expected: PASS (13 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1016 passed, 1 deselected; coverage above 96 %
git add src/character_swap/web/steps.py tests/test_steps.py
git commit -m "feat: one file holds every step text (web/steps.py)"
````

---

### Task 2: The five step screens and the home page (read-only)

The visual walkthrough over a job's existing files, with no POST route anywhere. This
replaces the old one-page viewer:

- `web/view_model.py` keeps only the shared helpers and the small cost table (`PAID_RECORD`
  is now an explicit dict, spec §4.8). `SECTIONS`, `Section`, `job_view` and their long "why"
  texts are removed. New: `prep_done`, `saved_review` (reads `review.json`, never hashes),
  `current_step` (which step a job opens on), `is_app_job` (A3), `file_if_present`, and three
  more `JobSummary` fields for the home list (`step`, `thumb`, `read_only`).
- New `web/pages.py` builds each step screen (`job_page`), the step bar and the home page.
  Pure: it never writes. Step 3 is the only place the review fingerprint is computed.
- New templates: `home.html`, `new.html` (the upload form; it posts nothing yet), `job.html`
  plus one partial per step, `bar.html` and `macros.html` (tags, the wipe player's markup,
  placeholders). The old `jobs.html` and `viewer.js` are deleted (`job.html` is rewritten).
- `viewer.css` is rewritten on the same token system (dark and light), with the wipe player's
  CSS (two stacked videos, the top one clipped at `--split`; side by side under
  `prefers-reduced-motion`).
- `costs.usd_text` formats money ("$0.88"; "$0.002" under a cent).
- `tests/viewer_jobs.make_job` gains `provider=` ("wavespeed" adds a character photo).

The routes: `GET /`, `/new`, `/jobs/{id}` (303 to the job's current step),
`/jobs/{id}/step/{n}` (a later step than the job has reached redirects back),
`/jobs/{id}/files/{name}` and `/api/jobs/{id}` (unchanged).

**Files:**
- Modify: `src/character_swap/costs.py`
- Modify: `src/character_swap/web/app.py`
- Create: `src/character_swap/web/pages.py`
- Modify: `src/character_swap/web/static/viewer.css`
- Delete: `src/character_swap/web/static/viewer.js`
- Modify: `src/character_swap/web/steps.py`
- Create: `src/character_swap/web/templates/bar.html`
- Modify: `src/character_swap/web/templates/base.html`
- Create: `src/character_swap/web/templates/home.html`
- Modify: `src/character_swap/web/templates/job.html`
- Delete: `src/character_swap/web/templates/jobs.html`
- Create: `src/character_swap/web/templates/macros.html`
- Modify: `src/character_swap/web/templates/message.html`
- Create: `src/character_swap/web/templates/new.html`
- Create: `src/character_swap/web/templates/step-1.html`
- Create: `src/character_swap/web/templates/step-2.html`
- Create: `src/character_swap/web/templates/step-3.html`
- Create: `src/character_swap/web/templates/step-4.html`
- Create: `src/character_swap/web/templates/step-5.html`
- Modify: `src/character_swap/web/view_model.py`
- Modify: `tests/test_cli.py`
- Test: `tests/test_pages.py`
- Modify: `tests/test_view_model.py`
- Modify: `tests/test_web_app.py`
- Modify: `tests/viewer_jobs.py`

- [ ] **Step 1: Write the tests**

`tests/test_view_model.py` and `tests/test_web_app.py` are rewritten: the section tests go
with the sections; every cost, progress, file-route and security test is kept.

**Replace** in `tests/test_cli.py`:

````python
    assert call["host"] == "127.0.0.1" and call["port"] == port
    assert f"Job viewer: http://127.0.0.1:{port}" in out.text and "Viewer stopped." in out.text
    page = TestClient(call["app"], base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and "No jobs yet" in page.text


def test_view_has_no_host_option(settings):
````

with:

````python
    assert call["host"] == "127.0.0.1" and call["port"] == port
    assert f"Job viewer: http://127.0.0.1:{port}" in out.text and "Viewer stopped." in out.text
    page = TestClient(call["app"], base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and "No swaps yet" in page.text


def test_view_has_no_host_option(settings):
````

**Create** `tests/test_pages.py`:

````python
import pytest

from character_swap.config import Settings
from character_swap.review import decide, fingerprint
from character_swap.web import pages
from viewer_jobs import finish_stage, make_job


def _approved(runs_dir, media, mode="depth_mesh"):
    job = make_job(runs_dir, media, mode, provider="wavespeed")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    return job


def _tabs(page):
    return {tab.id: tab for tab in page.tabs}


# ---- the step bar --------------------------------------------------------------
def test_step_bar_links_only_the_steps_a_job_has_reached():
    bar = pages.step_bar("j" * 32, 2, 3)
    assert [(item.number, item.state) for item in bar] == [
        (1, "done"),
        (2, "current"),
        (3, "open"),
        (4, "locked"),
        (5, "locked"),
    ]
    assert [item.url for item in bar][:3] == [f"/jobs/{'j' * 32}/step/{n}" for n in (1, 2, 3)]
    assert bar[3].url is None and bar[4].url is None


def test_a_new_swap_bar_has_no_links():
    assert all(item.url is None for item in pages.step_bar(None, 1, 1))


# ---- home ----------------------------------------------------------------------
def test_home_names_missing_keys_but_never_their_values(tmp_path, runs_dir):
    empty = Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir)})
    page = pages.home_page(runs_dir, empty)
    assert [key.name for key in page.missing] == ["REPLICATE_API_TOKEN", "WAVESPEED_API_KEY"]
    assert "wavespeed.ai" in page.missing[1].where
    full = Settings.load(
        root=tmp_path, environ={"REPLICATE_API_TOKEN": "secret-r", "WAVESPEED_API_KEY": "secret-w"}
    )
    assert pages.missing_keys(full) == ()


# ---- step 1 and 2 ------------------------------------------------------------------
def test_upload_step_shows_the_clip_the_photo_and_the_look(runs_dir, media):
    job = make_job(runs_dir, media, "depth", provider="wavespeed")
    job.update(character_description="Short dark hair.")
    page = pages.job_page(job, runs_dir, 1)
    assert page.upload == f"/jobs/{job.id}/files/upload.mp4"
    assert page.photo == f"/jobs/{job.id}/files/character-primary.png"
    assert page.look == "Better likeness" and page.description == "Short dark hair."
    assert page.read_only is False and page.step.title == "Upload"


def test_prepare_tabs_fill_in_as_stages_finish(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=6, running=True, provider="wavespeed")
    page = pages.job_page(job, runs_dir, 2)  # depth done, masks running
    tabs = _tabs(page)
    assert list(tabs) == ["depth", "cutout", "face", "prepared", "voice"]
    assert [tab.state for tab in page.tabs] == ["ready", "running", "waiting", "waiting", "ready"]
    assert page.tab.id == "voice"  # the newest ready tab when none is asked for
    depth = tabs["depth"].wipe
    assert depth.before == f"/jobs/{job.id}/files/original.mp4"
    assert depth.after == f"/jobs/{job.id}/files/depth.mp4" and depth.sound is False
    assert page.state == "running" and page.prep_done is False
    assert pages.job_page(job, runs_dir, 2, tab="depth").tab.id == "depth"
    assert pages.job_page(job, runs_dir, 2, tab="cutout").tab.id == "voice"  # not ready yet


def test_finished_prepare_tabs_use_each_modes_files(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    tabs = _tabs(pages.job_page(job, runs_dir, 2))
    assert tabs["cutout"].wipe.after.endswith("/depth-composite-silent.mp4")
    assert tabs["face"].wipe.after.endswith("/composite-silent.mp4")
    assert tabs["prepared"].wipe.after.endswith("/prepared.mp4")
    assert tabs["prepared"].texts == (("Room", "A bright studio with plants."),)
    voice = tabs["voice"]
    assert voice.wipe is None and [label for label, _url in voice.audio] == [
        "Voice only",
        "Raised 3 semitones (what the AI hears)",
    ]
    assert voice.texts == (("Words", "hello there"),)
    depth = make_job(runs_dir, media, "depth", provider="wavespeed")
    tabs = _tabs(pages.job_page(depth, runs_dir, 2))
    assert "face" not in tabs and tabs["cutout"].wipe.after.endswith("/composite-silent.mp4")


def test_a_tab_whose_file_is_gone_says_missing(runs_dir, media):
    job = make_job(runs_dir, media, "depth", provider="wavespeed")
    job.path("depth.mp4").unlink()
    assert _tabs(pages.job_page(job, runs_dir, 2))["depth"].state == "missing"


def test_a_failed_stage_shows_its_error(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, failed="Generating depth video failed: x")
    page = pages.job_page(job, runs_dir, 2)
    assert page.error == "Generating depth video failed: x" and page.state == "failed"
    assert _tabs(page)["depth"].state == "failed"
    stopped = make_job(runs_dir, media, "depth", done=5, failed="interrupted")
    assert pages.job_page(stopped, runs_dir, 2).error == "Stopped before it finished."


def test_a_wan_job_has_no_prepare_tabs(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    page = pages.job_page(job, runs_dir, 2)
    assert page.tabs == () and page.tab is None and page.read_only
    assert page.original == f"/jobs/{job.id}/files/original.mp4"


# ---- step 3: check -------------------------------------------------------------
def test_check_step_compares_the_original_with_what_the_ai_gets(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    page = pages.job_page(job, runs_dir, 3)
    assert page.wipe.after.endswith("/prepared.mp4") and page.wipe.after_label == "What the AI gets"
    assert page.fingerprint == fingerprint(job) and page.approved is False
    decide(job, approved=True, note="ok", fingerprint=page.fingerprint)
    assert pages.job_page(job, runs_dir, 3).approved is True
    job.path("prepared.mp4").write_bytes(b"changed")  # the approval no longer matches
    assert pages.job_page(job, runs_dir, 3).approved is False


def test_check_step_before_preparing_has_no_player(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, provider="wavespeed")
    page = pages.job_page(job, runs_dir, 3)
    assert page.wipe is None and page.fingerprint == "" and page.state == "waiting"


# ---- steps 4 and 5 ---------------------------------------------------------------
def test_draft_step_shows_the_default_prompt_without_writing_it(runs_dir, media):
    job = _approved(runs_dir, media)
    page = pages.job_page(job, runs_dir, 4)
    assert "@Video 1" in page.prompt and "hello there" in page.prompt
    assert not job.path("prompt.txt").exists()  # rendering never writes
    job.path("prompt.txt").write_text("My own words for @Image 1 and @Video 1.")
    assert pages.job_page(job, runs_dir, 4).prompt == "My own words for @Image 1 and @Video 1."
    assert page.wipe is None and page.state == "waiting"


def test_a_running_draft_shows_when_it_started(runs_dir, media):
    job = _approved(runs_dir, media)
    job.mark_stage("draft", "running", started_at="2026-11-13T10:00:00+00:00")
    page = pages.job_page(job, runs_dir, 4)
    assert page.state == "running" and page.started_at == "2026-11-13T10:00:00+00:00"


def test_a_finished_draft_plays_with_sound(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    page = pages.job_page(job, runs_dir, 4)
    assert page.wipe.after.endswith("/draft.mp4") and page.wipe.sound is True
    assert page.state == "ready" and page.stale is False
    job.mark_stage("draft", fingerprint="older-inputs")
    assert pages.job_page(job, runs_dir, 4).stale is True


def test_a_failed_draft_shows_the_error(runs_dir, media):
    job = _approved(runs_dir, media)
    job.mark_stage("draft", "failed", error="Generating the 480p draft failed: boom")
    page = pages.job_page(job, runs_dir, 4)
    assert page.state == "failed" and page.error.endswith("boom") and page.wipe is None


def test_a_finished_final_can_be_downloaded(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft"})
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint=fingerprint(job), resolution="720p")
    page = pages.job_page(job, runs_dir, 5)
    assert page.download == f"/jobs/{job.id}/files/final.mp4" and page.resolution == "720p"
    assert page.wipe.after_label == "Final" and page.wipe.sound
    assert page.spent_usd == pytest.approx(0.0608 + 0.88)


def test_a_final_file_that_is_gone_says_missing(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    job.path("final.mp4").unlink()
    page = pages.job_page(job, runs_dir, 5)
    assert page.state == "missing" and page.download is None


def test_pages_never_write(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh")  # no provider saved: read as WaveSpeed's
    before = sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*"))
    for number in (1, 2, 3, 4, 5):
        pages.job_page(job, runs_dir, number)
    assert sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*")) == before
````

**Replace the whole file** `tests/test_view_model.py` with:

````python
import json

import pytest

from character_swap.costs import Estimate, Line, estimate_prepare, estimate_stages, record_spend
from character_swap.pipeline import PAID_STAGES
from character_swap.review import decide, fingerprint
from character_swap.web import view_model as vm
from viewer_jobs import RECORDS, finish_stage, make_job, write_record


# ---- tables ----------------------------------------------------------------------
@pytest.mark.parametrize(("mode", "count"), [("depth", 9), ("depth_mesh", 10), ("face_mesh", 7)])
def test_stage_counts_match_spec(mode, count):
    assert len(vm.STAGES_BY_MODE[mode]) == count
    assert vm.STAGES_BY_MODE["direct"] == ("normalize",)


@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_paid_stages_are_a_subset_of_the_mode(mode):
    assert set(PAID_STAGES[mode]) <= set(vm.STAGES_BY_MODE[mode])


def test_the_cost_table_names_every_paid_record():
    assert vm.PAID_RECORD == RECORDS  # the files the stages really write (tests/viewer_jobs.py)


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
        "generation": {"draft": "pending", "final": "pending"},
        "done_count": 0,
        "total_count": 9,
        "active": True,
        "poll": "fast",
    }


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


def test_progress_running_and_finished(runs_dir, media):
    running = make_job(runs_dir, media, "face_mesh", done=3, running=True)
    info = vm.progress(running.state)
    assert info["stages"]["transcribe"] == "running" and info["done_count"] == 3
    assert info["active"] is True
    finished = make_job(runs_dir, media, "face_mesh")
    assert vm.progress(finished.state)["active"] is False
    assert vm.progress(finished.state)["done_count"] == 7


def test_unknown_stage_status_counts_as_pending(runs_dir, media):
    job = make_job(runs_dir, media, "direct", done=0)
    job.mark_stage("normalize", "weird")
    assert vm.progress(job.state)["stages"] == {"normalize": "pending"}


# ---- which step a job is on ----------------------------------------------------------
def test_current_step_follows_the_job(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=4, provider="wavespeed")
    assert vm.current_step(job, job.state) == 2  # preparing
    for name in vm.STAGES_BY_MODE["depth_mesh"][4:]:
        finish_stage(job, media, name)
    assert vm.current_step(job, job.state) == 3  # prepared, not approved
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    assert vm.current_step(job, job.state) == 4  # approved, no draft
    finish_stage(job, media, "draft")
    assert vm.current_step(job, job.state) == 5
    job.mark_stage("draft", fingerprint="older-inputs")
    assert vm.current_step(job, job.state) == 4  # the draft is out of date


def test_current_step_never_hashes(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "depth")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    assert vm.current_step(job, job.state) == 4
    assert vm.saved_review(job)["approved"] is True


@pytest.mark.parametrize("content", ["{", "[]"])
def test_a_damaged_review_reads_as_not_approved(runs_dir, media, content):
    job = make_job(runs_dir, media, "depth")
    job.path("review.json").write_text(content)
    assert vm.saved_review(job) == {} and vm.current_step(job, job.state) == 3


@pytest.mark.parametrize(
    ("mode", "provider", "second", "expected"),
    [
        ("depth_mesh", "wavespeed", False, True),
        ("depth", "wavespeed", False, True),
        ("face_mesh", "wavespeed", False, False),
        ("depth", "piapi", False, False),
        ("depth", "wavespeed", True, False),
        ("direct", "", False, False),
    ],
)
def test_only_one_person_wavespeed_depth_jobs_get_buttons(
    runs_dir, media, mode, provider, second, expected
):
    job = make_job(runs_dir, media, mode, done=0, provider=provider)
    if second:
        job.update(character_secondary="character-secondary.png")
    state = job.state
    key = vm.generator_key(job, state)
    assert vm.is_app_job(state, key) is expected


# ---- links and files -------------------------------------------------------------
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


def test_file_url_quotes_names(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.path("my face.png").write_bytes(b"x")
    assert vm.file_if_present(job, "my face.png") == f"/jobs/{job.id}/files/my%20face.png"
    assert vm.file_url("j", "sam/a b.json") == "/jobs/j/files/sam/a%20b.json"


@pytest.mark.parametrize("name", ["../outside.png", None, 42, "", "missing.png"])
def test_file_if_present_refuses_odd_or_missing_names(runs_dir, media, name):
    job = make_job(runs_dir, media, "direct")
    assert vm.file_if_present(job, name) is None


def test_depth_mesh_composite_fallback(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=8)  # composite done, face_mesh not
    assert vm.composite_name(job, job.state) == "composite-silent.mp4"
    job.mark_stage("face_mesh", "running")
    assert vm.composite_name(job, job.state) == "depth-composite-silent.mp4"
    assert vm.composite_name(job, {**job.state, "mode": "depth"}) == "composite-silent.mp4"


def test_unreadable_video_is_reported_not_raised(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.path("original.mp4").write_bytes(b"not a video" * 20_000)
    assert vm.video_facts(job.path("original.mp4")) == "unreadable video · 0.2 MB"
    assert vm.video_facts(job.path("upload.mp4")).startswith("1.0 s · ")


def test_video_facts_treats_a_vanished_file_as_missing(tmp_path, monkeypatch):
    gone = tmp_path / "gone.mp4"
    assert vm.video_facts(gone) == "file missing"
    monkeypatch.setattr(vm, "stream_seconds", lambda *_a: 1.0)
    assert vm.video_facts(gone) == "file missing"


def test_review_read_errors_show_unavailable(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "direct")

    def broken(_job):
        raise OSError("permission denied")

    monkeypatch.setattr(vm.review, "status", broken)
    assert vm.review_status(job)["status"] == "unavailable"


# ---- costs ---------------------------------------------------------------------
def test_huggingface_depth_is_free(runs_dir, media):
    job = make_job(runs_dir, media, "depth", depth_provider="huggingface")
    costs = vm.costs_view(job, job.state, runs_dir)
    depth_row = next(row for row in costs.rows if row.step == "Depth video")
    assert depth_row == vm.CostRow("Depth video", "Hugging Face · free", 0.0, None)
    expected = round(estimate_prepare("depth", 30).total - 0.04, 4)
    assert costs.completed_usd == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_finished_job_costs_match_the_prepare_estimate(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    costs = vm.costs_view(job, job.state, runs_dir)
    assert len(costs.rows) == len(PAID_STAGES[mode])
    assert all(row.provider == "Replicate" for row in costs.rows)
    assert all(row.link for row in costs.rows)
    assert costs.completed_usd == pytest.approx(round(estimate_prepare(mode, 30).total, 4))


def test_costs_count_only_completed_steps(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="whisper down")
    costs = vm.costs_view(job, job.state, runs_dir)
    assert [row.step for row in costs.rows] == ["Separate vocals"]
    assert costs.rows[0].link == "https://replicate.com/p/pred-separate"
    assert costs.completed_usd == pytest.approx(0.017)


def test_logged_and_all_jobs_spend(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    other = make_job(runs_dir, media, "face_mesh")
    assert vm.costs_view(job, job.state, runs_dir).logged_usd == 0
    record_spend(job, "prepare", Estimate((Line("a", 0.0188),)), runs_dir)
    record_spend(other, "prepare", Estimate((Line("a", 0.02),)), runs_dir)
    job.append_cost({"step": "odd", "usd": "not a number"})
    job.append_cost("garbage")
    costs = vm.costs_view(job, job.state, runs_dir)
    assert costs.logged_usd == pytest.approx(0.0188)
    assert costs.all_jobs_usd == pytest.approx(0.0388)


def test_logged_cost_ignores_a_damaged_costs_field(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.update(costs="oops")
    assert vm.logged_cost(job.state) == 0.0


@pytest.mark.parametrize("value", ["nan", "inf", float("nan"), float("-inf")])
def test_non_finite_logged_costs_count_as_zero(value):
    state = {"costs": [{"usd": value}, {"usd": 0.5}]}
    assert vm.logged_cost(state) == 0.5


def test_wan_draft_row_links_to_replicate(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-draft"})
    costs = vm.costs_view(job, job.state, runs_dir)
    rows = [(row.step, row.provider, row.usd, row.link) for row in costs.rows]
    assert rows == [("Draft (480p)", "Replicate", 0.08, "https://replicate.com/p/pred-draft")]


def test_remade_draft_row_counts_only_the_current_prediction(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-old"})
    job.append_cost({"step": "draft", "usd": 0.08, "at": "y", "task_id": "pred-draft"})
    costs = vm.costs_view(job, job.state, runs_dir)
    assert [row.usd for row in costs.rows] == [0.08]
    assert costs.logged_usd == pytest.approx(0.16)  # every charge is still logged


def test_unmatched_current_prediction_never_counts_old_charges(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")  # current prediction: pred-draft
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-old"})
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.0]
    job.append_cost({"step": "draft", "usd": 0.05, "at": "y"})  # older log line without an id
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.05]


def test_wavespeed_rows_name_wavespeed_and_the_final_resolution(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft"})
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint=fingerprint(job), resolution="1080p")
    rows = {r.step: r for r in vm.cost_rows(job, job.state)}
    assert rows["Draft (480p)"].provider == "WaveSpeed" and rows["Draft (480p)"].usd == 0.88
    assert rows["Final (1080p)"].link is None and rows["Separate vocals"].provider == "Replicate"


@pytest.mark.parametrize(("mode", "step"), [("depth", "Final (720p)"), ("direct", "Final (720p)")])
def test_final_row_without_a_saved_resolution_uses_the_generator_default(
    runs_dir, media, mode, step
):
    job = make_job(runs_dir, media, mode, provider="wavespeed" if mode == "depth" else "")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    rows = vm.cost_rows(job, job.state)
    assert [r.step for r in rows if r.step.startswith("Final")] == [step]


# ---- out-of-date work ------------------------------------------------------------------
def test_stale_stages(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    assert vm.stale_stages(job, job.state) == frozenset()
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    assert vm.stale_stages(job, job.state) == frozenset()
    job.mark_stage("final", fingerprint="made-from-older-inputs")
    assert vm.stale_stages(job, job.state) == {"final"}
    job.path("original.mp4").unlink()  # nothing saved can be proven current (as the CLI says)
    assert vm.stale_stages(job, job.state) == {"draft", "final"}


def test_stale_check_read_error_says_nothing(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")

    def broken(target):
        raise OSError("disk hiccup")

    monkeypatch.setattr(vm, "fingerprint", broken)
    assert vm.stale_stages(job, job.state) == frozenset()


# ---- damaged state and the job list ----------------------------------------------
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "could not be read"),
        ("[]", "not a JSON object"),
        (json.dumps({"mode": "nope", "stages": {}}), "unknown mode"),
        (json.dumps({"mode": "depth", "stages": []}), "no stages table"),
        (json.dumps({"mode": "depth", "stages": {}, "provider": "sora"}), "unknown provider"),
    ],
)
def test_damaged_state_raises_view_error(runs_dir, media, content, message):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text(content)
    with pytest.raises(vm.ViewError, match=message):
        vm.read_state(job)
    [summary] = vm.list_jobs(runs_dir)
    assert summary.id == job.id and message in summary.error


@pytest.mark.parametrize(
    "content",
    [
        {"mode": [], "stages": {}},
        {"mode": {"a": 1}, "stages": {}},
        {"mode": None, "stages": {}},
        {"mode": "depth", "stages": "oops"},
        {"mode": "depth", "stages": None},
    ],
)
def test_corrupt_mode_or_stages_never_break_the_home_list(runs_dir, media, content):
    bad = make_job(runs_dir, media, "depth", done=0)
    good = make_job(runs_dir, media, "direct")
    bad.path("state.json").write_text(json.dumps(content))
    summaries = {s.id: s for s in vm.list_jobs(runs_dir)}
    assert summaries[bad.id].error and summaries[good.id].error is None


def test_list_jobs_newest_first_with_step_thumb_and_cost(runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)  # separate + transcribe are paid
    new = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    wan = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T10:00:00+00:00")
    wan.update(created_at="2026-10-01T10:00:00+00:00")
    (runs_dir / "spend-log.csv").write_text("at,job,step,usd_estimate,usd_micro\n")
    (runs_dir / "not-a-job").mkdir()
    (runs_dir / ("a" * 32)).mkdir()  # half-created: no state.json
    (runs_dir / "_incoming").mkdir()  # the app's uploads in progress
    summaries = vm.list_jobs(runs_dir)
    assert [s.id for s in summaries] == [new.id, old.id, wan.id]
    assert (summaries[1].done_count, summaries[1].total_count) == (4, 9)
    paid = estimate_stages(["separate", "transcribe"], 30).total
    assert summaries[1].cost_usd == pytest.approx(paid)
    assert summaries[1].short_id == old.id[:8] and summaries[1].error is None
    assert [s.step for s in summaries] == [3, 2, 3]
    assert [s.read_only for s in summaries] == [False, False, True]  # old: depth, no provider
    assert summaries[0].thumb == f"/jobs/{new.id}/files/scene.jpg"
    assert summaries[1].thumb is None  # describe not done yet, no photo
    assert summaries[2].thumb == f"/jobs/{wan.id}/files/character-primary.png"


def test_list_jobs_without_runs_folder(tmp_path):
    assert vm.list_jobs(tmp_path / "missing") == []


def test_folder_removed_mid_scan_is_skipped(runs_dir, media, monkeypatch):
    keep = make_job(runs_dir, media, "direct")
    gone = make_job(runs_dir, media, "direct")
    gone.update(created_at=None)  # forces the mtime fallback
    real = vm._mtime_iso

    def flaky(path):
        if path.name == gone.id:
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(vm, "_mtime_iso", flaky)
    assert [s.id for s in vm.list_jobs(runs_dir)] == [keep.id]


def test_the_home_list_never_writes_the_provider(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh")  # no provider saved: read as WaveSpeed's
    before = job.path("state.json").read_bytes()
    [summary] = vm.list_jobs(runs_dir)
    assert summary.read_only is False  # resolved as WaveSpeed's, never written
    assert job.path("state.json").read_bytes() == before


def test_a_record_written_by_hand_is_read_safely(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    write_record(job, "rgb-repair/sam-prediction.json", {"id": "pred-repair"})
    assert vm.prediction_url(job, vm.RGB_REPAIR_RECORD) == "https://replicate.com/p/pred-repair"
````

**Replace the whole file** `tests/test_web_app.py` with:

````python
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from character_swap.config import Settings
from character_swap.costs import Estimate, Line, record_spend
from character_swap.review import decide, fingerprint
from character_swap.web import steps
from character_swap.web.app import create_app
from viewer_jobs import finish_stage, make_job

KEYS = {"REPLICATE_API_TOKEN": "r-secret", "WAVESPEED_API_KEY": "w-secret"}


@pytest.fixture
def settings(tmp_path, runs_dir):
    return Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir), **KEYS})


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def _app_job(runs_dir, media, **kwargs):
    return make_job(runs_dir, media, "depth_mesh", provider="wavespeed", **kwargs)


def _approved(runs_dir, media):
    job = _app_job(runs_dir, media)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    return job


# ---- home ----------------------------------------------------------------------
def test_home_empty_state_explains_new_swap(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No swaps yet" in response.text and 'href="/new"' in response.text
    assert "Spent on all swaps: <strong>$0.00</strong>" in response.text
    assert "Hide the person" in response.text and "Original sound back" in response.text


def test_home_lists_swaps_newest_first(client, runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)
    new = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T09:30:00+00:00")
    record_spend(new, "prepare", Estimate((Line("x", 0.25),)), runs_dir)
    text = client.get("/").text
    assert text.index(new.id[:8]) < text.index(old.id[:8])
    assert "Step 2 · Prepare" in text and "Step 3 · Check" in text
    assert "2026-11-01 09:30" in text and "Spent on all swaps: <strong>$0.25</strong>" in text
    assert f'href="/jobs/{old.id}"' in text
    assert text.count("Made with the command line") == 1  # the Wan job


def test_home_marks_unreadable_jobs(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{broken")
    response = client.get("/")
    assert response.status_code == 200
    assert "Unreadable: state.json could not be read" in response.text


def test_a_missing_key_is_named_and_new_swap_is_disabled(tmp_path, runs_dir):
    settings = Settings.load(
        root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir), "REPLICATE_API_TOKEN": "r"}
    )
    client = TestClient(create_app(settings), base_url="http://127.0.0.1")
    text = client.get("/").text
    assert "<code>WAVESPEED_API_KEY</code> is missing" in text and "<code>.env</code>" in text
    assert "wavespeed.ai" in text and 'href="/new"' not in text
    assert 'aria-disabled="true">New swap' in text
    assert "REPLICATE_API_TOKEN</code> is missing" not in text
    new = client.get("/new").text
    assert "WAVESPEED_API_KEY</code> is missing" in new and " disabled>Prepare" in new


def test_the_app_re_reads_the_keys_on_each_request(tmp_path, runs_dir):
    current = {"values": {"CHARSWAP_RUNS_DIR": str(runs_dir)}}

    def reload():
        return Settings.load(root=tmp_path, environ=current["values"])

    client = TestClient(create_app(reload(), reload_settings=reload), base_url="http://127.0.0.1")
    assert "is missing" in client.get("/").text
    current["values"] = {**current["values"], **KEYS}
    assert "is missing" not in client.get("/").text


# ---- step 1: new swap ------------------------------------------------------------
def test_new_swap_form_shows_prices_and_choices(client):
    text = client.get("/new").text
    assert "Step 1 of 5" in text and 'name="clip"' in text and 'name="photo"' in text
    assert 'name="describe" maxlength="300"' in text
    assert 'value="depth_mesh" checked' in text and "Better likeness" in text
    assert 'name="rights" value="yes" required' in text
    assert "Prepare · about $0.06" in text
    assert 'data-draft-per-second="0.2200"' in text  # WaveSpeed bills input + output seconds


# ---- the step screens -------------------------------------------------------------
def test_job_link_opens_the_current_step(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=3, running=True)
    response = client.get(f"/jobs/{job.id}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("/step/2")
    done = _approved(runs_dir, media)
    location = client.get(f"/jobs/{done.id}", follow_redirects=False).headers["location"]
    assert location == f"/jobs/{done.id}/step/4"


def test_a_later_step_than_the_job_has_reached_redirects(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    response = client.get(f"/jobs/{job.id}/step/5", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("/step/3")
    assert client.get(f"/jobs/{job.id}/step/1").status_code == 200  # earlier steps stay open


@pytest.mark.parametrize("number", ["0", "6", "x"])
def test_unknown_step_numbers(client, runs_dir, media, number):
    job = _app_job(runs_dir, media)
    assert client.get(f"/jobs/{job.id}/step/{number}").status_code in (404, 422)


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5])
def test_every_step_has_the_bar_its_line_and_no_inline_code(client, runs_dir, media, number):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    text = client.get(f"/jobs/{job.id}/step/{number}").text
    assert f"Step {number} of 5" in text and 'aria-label="Steps"' in text
    assert 'aria-current="step"' in text
    assert steps.step(number).line.replace("'", "&#39;") in text
    assert " style=" not in text and "<style" not in text and "<script>" not in text


def test_prepare_step_shows_tabs_and_the_wipe_player(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=6, running=True)  # masks running
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert f'href="/jobs/{job.id}/step/2?tab=depth"' in text
    assert 'class="tab tab-running" aria-disabled="true"' in text
    text = client.get(f"/jobs/{job.id}/step/2?tab=depth").text
    assert 'class="wipe" data-wipe' in text and "Depth Anything" in text
    assert f'src="/jobs/{job.id}/files/original.mp4"' in text
    assert f'src="/jobs/{job.id}/files/depth.mp4"' in text
    assert "Next: check it" not in text and "About 2 minutes" in text


def test_finished_prepare_offers_the_next_step(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/2?tab=voice").text
    assert f'href="/jobs/{job.id}/step/3"' in text and "Next: check it" in text
    assert f'<audio controls preload="metadata" src="/jobs/{job.id}/files/vocals.wav">' in text
    assert "hello there" in text and "Demucs" in text


def test_check_step_compares_with_what_the_ai_gets(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/3").text
    assert f'src="/jobs/{job.id}/files/prepared.mp4"' in text and "What the AI gets" in text


def test_draft_step_shows_the_prompt_read_only_for_now(client, runs_dir, media):
    job = _approved(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "@Video 1" in text and "readonly" in text and "Seedance 2.5" in text
    assert not job.path("prompt.txt").exists()


def test_final_step_offers_the_download(client, runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    text = client.get(f"/jobs/{job.id}/step/5").text
    assert f'href="/jobs/{job.id}/files/final.mp4" download=' in text
    assert "Spent on this swap" in text


def test_a_read_only_job_says_where_to_continue(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert f"Continue there: charswap status {job.id}" in text


def test_failed_step_shows_the_error_escaped(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=3, failed="Transcribing speech failed: <boom>")
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert "Transcribing speech failed: &lt;boom&gt;" in text


def test_untrusted_text_is_escaped(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    job.update(transcript="<script>alert(1)</script>")
    text = client.get(f"/jobs/{job.id}/step/2?tab=voice").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_job_page_with_damaged_state_shows_the_error(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("[]")
    for path in (f"/jobs/{job.id}", f"/jobs/{job.id}/step/1"):
        response = client.get(path)
        assert response.status_code == 500
        assert "state.json is not a JSON object." in response.text and 'href="/"' in response.text


@pytest.mark.parametrize("job_id", ["0" * 32, "not-a-job", "A" * 32])
def test_unknown_job_is_a_404_page(client, job_id):
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 404
    assert "There is no swap with that id." in response.text and 'href="/"' in response.text


def test_home_directory_is_hidden_in_errors(client, runs_dir, media):
    home = str(Path.home())
    job = _app_job(runs_dir, media, done=3, failed=f"cannot read {home}/clips/a.mp4")
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert home not in text and "cannot read ~/clips/a.mp4" in text
    job.path("state.json").write_text(f"[] {home}")
    assert home not in client.get(f"/jobs/{job.id}/step/2").text


def test_step_pages_carry_the_progress_to_poll(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=5, running=True)
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert 'data-poll="fast"' in text and f'data-job-id="{job.id}"' in text
    shown = json.loads(text.split('data-progress="')[1].split('"')[0].replace("&#34;", '"'))
    assert shown["stages"]["depth"] == "running" and shown["generation"]["draft"] == "pending"
    assert 'id="new-results"' in text and 'id="refresh-button"' in text


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
        "scene.jpg%00.png",
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
    assert {"stages", "generation", "done_count", "total_count", "active", "poll"} <= set(data)
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
    job = _approved(runs_dir, media)
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    assert client.get(f"/api/jobs/{job.id}").json()["active"] is False
    assert client.get(f"/jobs/{job.id}", follow_redirects=False).status_code == 303


def test_api_errors_are_json(client, runs_dir, media):
    assert client.get(f"/api/jobs/{'0' * 32}").json() == {"error": "There is no swap with that id."}
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{")
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 500 and "could not be read" in response.json()["error"]


# ---- local-only, read-only GETs ----------------------------------------------------
def test_get_routes_never_write(client, runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*"))
    for path in ["/", "/new", f"/jobs/{job.id}", f"/api/jobs/{job.id}"]:
        assert client.get(path).status_code == 200
    for number in range(1, 6):
        assert client.get(f"/jobs/{job.id}/step/{number}").status_code == 200
    assert sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*")) == before


def test_static_assets_are_served(client):
    css = client.get("/static/viewer.css")
    assert css.status_code == 200 and "prefers-color-scheme: dark" in css.text
    assert "prefers-reduced-motion" in css.text and ".wipe-after" in css.text
    assert client.get("/static/nope.css").status_code == 404


def test_api_docs_are_disabled(client):
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_requests_for_other_hosts_are_refused(settings):
    remote = TestClient(create_app(settings), base_url="http://evil.example")
    assert remote.get("/").status_code == 400
    local = TestClient(create_app(settings), base_url="http://localhost:8770")
    assert local.get("/").status_code == 200


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}


def test_security_headers_on_pages_media_and_api(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    page = client.get(f"/jobs/{job.id}/step/2")
    video = client.get(f"/jobs/{job.id}/files/prepared.mp4", headers={"Range": "bytes=0-9"})
    api = client.get(f"/api/jobs/{job.id}")
    missing = client.get("/jobs/nope")
    for response in (page, video, api, missing, client.get("/"), client.get("/new")):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value, (name, response.url)
    assert video.status_code == 206 and video.headers["accept-ranges"] == "bytes"
    assert page.headers["cache-control"] == "no-store"
    assert video.headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-store"
````

**Replace** in `tests/viewer_jobs.py`:

````python
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
````

with:

````python
    running: bool = False,
    failed: str | None = None,
    depth_provider: str = "replicate",
    provider: str = "",
) -> Job:
    """A job whose first `done` stages are finished (all when None).

    The next stage is marked running when `running`, or failed with message `failed`.
    provider="wavespeed" makes the kind of job the app's buttons work for (with a photo).
    """
    generator = "wan" if mode == "direct" else "seedance"
    character = media.image if mode == "direct" or provider else None
    job = Job.create(
        runs_dir,
        media.video,
````

**Replace** in `tests/viewer_jobs.py`:

````python
        mode=mode,
        depth_provider=depth_provider,
        character=character,
    )
    stages = STAGES_BY_MODE[mode]
    count = len(stages) if done is None else done
````

with:

````python
        mode=mode,
        depth_provider=depth_provider,
        character=character,
        provider=provider,
    )
    stages = STAGES_BY_MODE[mode]
    count = len(stages) if done is None else done
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_cli.py tests/test_pages.py tests/test_view_model.py tests/test_web_app.py -q --no-cov`  
Expected: FAIL. `ImportError: cannot import name 'pages' from 'character_swap.web'` (a collection error).

- [ ] **Step 3: Write the code**

Delete the two files the new screens replace, then write the rest.

**Replace** in `src/character_swap/costs.py`:

````python
        return "\n".join(rows)


def cost_line(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
````

with:

````python
        return "\n".join(rows)


def usd_text(usd: float) -> str:
    """'$0.88'. Amounts under a cent keep three decimals ('$0.002'), so they never read as free."""
    return f"${usd:.2f}" if usd >= 0.01 or usd == 0 else f"${usd:.3f}"


def cost_line(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
````

**Replace** in `src/character_swap/web/app.py`:

````python
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
    tilde,
````

with:

````python
"""The click-through app (FastAPI + Jinja2). `charswap app` binds it to 127.0.0.1."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    ViewError,
    current_step,
    progress,
    read_state,
    tilde,
````

**Replace** in `src/character_swap/web/app.py`:

````python
        "script-src 'self'; frame-ancestors 'none'"
    ),
}


def create_app(settings: Settings) -> FastAPI:
    runs_dir = settings.runs_dir
    app = FastAPI(title="Character Swap viewer", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
````

with:

````python
        "script-src 'self'; frame-ancestors 'none'"
    ),
}
# WaveSpeed bills the input seconds plus the output seconds: the draft hint on step 1.
DRAFT_PER_SECOND = PRICES["wavespeed-480"].usd + PRICES["wavespeed-480-input"].usd


def create_app(
    settings: Settings, *, reload_settings: Callable[[], Settings] | None = None
) -> FastAPI:
    """`reload_settings` re-reads .env on each request (A4); tests pass fixed settings."""
    runs_dir = settings.runs_dir
    current_settings = reload_settings or (lambda: settings)
    app = FastAPI(title="Character Swap", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
````

**Replace** in `src/character_swap/web/app.py`:

````python
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(icons=ICONS, status_words=STATUS_WORDS)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
````

with:

````python
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(steps=steps, money=usd_text)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
````

**Replace** in `src/character_swap/web/app.py`:

````python
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
        shown = {"stages": view.progress["stages"], "generation": view.progress["generation"]}
        context = {"job": view, "stages_json": json.dumps(shown)}
        return templates.TemplateResponse(request, "job.html", context)

    @app.get("/jobs/{job_id}/files/{name:path}")
````

with:

````python
        try:
            return Job.load(runs_dir, job_id)
        except JobError:
            raise HTTPException(404, "There is no swap with that id.") from None

    def damaged(request: Request, job_id: str, exc: Exception) -> Response:
        return message(request, f"Swap {job_id[:8]} cannot be shown", str(exc), 500)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        page = home_page(runs_dir, current_settings())
        return templates.TemplateResponse(request, "home.html", {"page": page})

    @app.get("/new", response_class=HTMLResponse)
    def new_swap(request: Request) -> Response:
        context = {
            "step": steps.step(1),
            "bar": step_bar(None, 1, 1),
            "missing": missing_keys(current_settings()),
            "prepare_usd": estimate_prepare("depth_mesh", seconds=ESTIMATE_SECONDS).total,
            "draft_per_second": f"{DRAFT_PER_SECOND:.4f}",
        }
        return templates.TemplateResponse(request, "new.html", context)

    @app.get("/jobs/{job_id}")
    def job_home(request: Request, job_id: str) -> Response:
        job = load(job_id)
        try:
            number = current_step(job, read_state(job))
        except ViewError as exc:
            return damaged(request, job_id, exc)
        return RedirectResponse(step_url(job.id, number), status_code=303)

    @app.get("/jobs/{job_id}/step/{number}", response_class=HTMLResponse)
    def step_page(request: Request, job_id: str, number: int, tab: str = "") -> Response:
        job = load(job_id)
        if not 1 <= number <= 5:
            raise HTTPException(404, NOT_FOUND)
        try:
            reachable = current_step(job, read_state(job))
            if number > reachable:  # a later step is not open yet
                return RedirectResponse(step_url(job.id, reachable), status_code=303)
            page = job_page(job, runs_dir, number, tab=tab)
        except ViewError as exc:
            return damaged(request, job_id, exc)
        shown = {"stages": page.progress["stages"], "generation": page.progress["generation"]}
        context = {"page": page, "bar": page.bar, "progress_json": json.dumps(shown)}
        return templates.TemplateResponse(request, "job.html", context)

    @app.get("/jobs/{job_id}/files/{name:path}")
````

**Create** `src/character_swap/web/pages.py`:

````python
"""What each of the five step screens shows, built from a job folder.

Pure: no HTTP, no templates, and never a write (GET routes never change a job).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from character_swap.config import WHERE_TO_GET, Settings
from character_swap.costs import total_spend
from character_swap.jobs import Job
from character_swap.pipeline import PROMPT_FILE, default_prompt, generator_key
from character_swap.review import ReviewError, fingerprint
from character_swap.web import steps
from character_swap.web.view_model import (
    JobSummary,
    combine,
    completed_cost,
    composite_name,
    cost_rows,
    current_step,
    file_if_present,
    is_app_job,
    list_jobs,
    prep_done,
    progress,
    read_state,
    saved_review,
    stage_status,
    stale_stages,
    tilde,
)

APP_KEYS = ("REPLICATE_API_TOKEN", "WAVESPEED_API_KEY")  # what a WaveSpeed swap needs (A4)
INTERRUPTED = "interrupted"  # what pipeline._tracked records on Ctrl-C
STOPPED = "Stopped before it finished."


@dataclass(frozen=True)
class Wipe:
    """One before/after player: the original on the left, a transformation on the right."""

    before: str
    after: str
    after_label: str
    sound: bool = False  # the after side carries sound (draft and final)


@dataclass(frozen=True)
class TabView:
    id: str
    title: str
    line: str
    tags: tuple[steps.Tag, ...]
    state: str  # "ready" | "running" | "waiting" | "failed" | "missing"
    url: str
    wipe: Wipe | None = None
    audio: tuple[tuple[str, str], ...] = ()  # (label, url)
    texts: tuple[tuple[str, str], ...] = ()  # (label, value)


@dataclass(frozen=True)
class BarItem:
    number: int
    title: str
    state: str  # "done" | "current" | "open" | "locked"
    url: str | None


@dataclass(frozen=True)
class MissingKey:
    name: str
    where: str


@dataclass(frozen=True)
class JobPage:
    id: str
    short_id: str
    step: steps.Step
    bar: tuple[BarItem, ...]
    mode: str
    read_only: bool
    progress: dict[str, Any]
    reachable: int  # the furthest step this job can show
    original: str | None = None  # the normalized clip: the "before" side of every wipe
    upload: str | None = None
    photo: str | None = None
    look: str = ""
    description: str = ""
    tabs: tuple[TabView, ...] = ()
    tab: TabView | None = None
    wipe: Wipe | None = None
    state: str = "waiting"  # the step's own work: "ready" | "running" | "waiting" | "failed"
    error: str | None = None
    started_at: str = ""  # a running draft/final (for the elapsed timer)
    prep_done: bool = False
    approved: bool = False
    fingerprint: str = ""  # step 3 only: the review fingerprint, computed on this render
    stale: bool = False  # step 4: the draft was made from inputs no longer approved
    prompt: str = ""
    resolution: str = ""
    spent_usd: float = 0.0
    download: str | None = None


@dataclass(frozen=True)
class HomePage:
    jobs: tuple[JobSummary, ...]
    all_jobs_usd: float
    missing: tuple[MissingKey, ...]


def missing_keys(settings: Settings) -> tuple[MissingKey, ...]:
    """The keys a WaveSpeed swap needs that .env does not set (names only, never values)."""
    return tuple(
        MissingKey(name, WHERE_TO_GET[name]) for name in APP_KEYS if not settings.get(name)
    )


def home_page(runs_dir: Path, settings: Settings) -> HomePage:
    return HomePage(tuple(list_jobs(runs_dir)), total_spend(runs_dir), missing_keys(settings))


def step_url(job_id: str, number: int) -> str:
    return f"/jobs/{job_id}/step/{number}"


def step_bar(job_id: str | None, current: int, reachable: int) -> tuple[BarItem, ...]:
    """1 Upload · 2 Prepare · 3 Check · 4 Draft · 5 Final; earlier steps can be revisited."""
    items = []
    for item in steps.STEPS:
        if item.number == current:
            state = "current"
        elif item.number < reachable:
            state = "done"
        elif item.number == reachable:
            state = "open"
        else:
            state = "locked"
        url = step_url(job_id, item.number) if job_id and state != "locked" else None
        items.append(BarItem(item.number, item.title, state, url))
    return tuple(items)


def _state_word(status: str) -> str:
    return {"done": "ready", "pending": "waiting"}.get(status, status)


def _error(state: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        entry = state["stages"].get(name)
        if isinstance(entry, dict) and entry.get("status") == "failed":
            error = str(entry.get("error") or "This step failed.")
            return STOPPED if error == INTERRUPTED else tilde(error)
    return None


def _tab(job: Job, state: dict[str, Any], tab: steps.Tab, original: str | None) -> TabView:
    status = _state_word(combine([stage_status(state, name) for name in tab.stages]))
    head = (tab.id, tab.title, tab.line, tab.tags)
    url = f"{step_url(job.id, 2)}?tab={tab.id}"
    if status != "ready":
        return TabView(*head, status, url)
    if not tab.after:  # the voice: two audio players and the transcript
        audio = tuple(
            (label, found)
            for label, name in (
                ("Voice only", "vocals.wav"),
                ("Raised 3 semitones (what the AI hears)", "vocals-plus3.wav"),
            )
            if (found := file_if_present(job, name))
        )
        words = str(state.get("transcript") or "(no speech found)")
        return TabView(*head, "ready", url, audio=audio, texts=(("Words", words),))
    name = composite_name(job, state) if tab.after == "@composite" else tab.after
    after = file_if_present(job, name)
    if after is None or original is None:
        return TabView(*head, "missing", url)
    texts: tuple[tuple[str, str], ...] = ()
    if tab.id == "prepared" and stage_status(state, "describe") == "done":
        texts = (("Room", str(state.get("scene") or "(no description)")),)
    return TabView(*head, "ready", url, wipe=Wipe(original, after, tab.title), texts=texts)


def _pick_tab(tabs: tuple[TabView, ...], wanted: str) -> TabView | None:
    """The requested tab if it is ready, else the newest ready one, else the first."""
    ready = [tab for tab in tabs if tab.state == "ready"]
    for tab in ready:
        if tab.id == wanted:
            return tab
    if ready:
        return ready[-1]
    return tabs[0] if tabs else None


def _look(mode: str) -> str:
    for value, label, _hint in steps.LOOKS:
        if value == mode:
            return label
    return mode


def _prompt(job: Job, state: dict[str, Any]) -> str:
    """prompt.txt if it exists, else the default. Rendering never writes the file."""
    if state.get("generator") != "seedance":
        return ""
    path = job.path(PROMPT_FILE)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return default_prompt(job)


def _generation(job: Job, state: dict[str, Any], stage: str, original: str | None) -> dict:
    """The draft or final screen's player, status and error."""
    status = _state_word(stage_status(state, stage))
    entry = state["stages"].get(stage)
    entry = entry if isinstance(entry, dict) else {}
    found = {
        "state": status,
        "error": _error(state, (stage,)),
        "started_at": str(entry.get("started_at") or "") if status == "running" else "",
    }
    after = file_if_present(job, f"{stage}.mp4") if status == "ready" else None
    if after and original:
        label = "Draft" if stage == "draft" else "Final"
        found["wipe"] = Wipe(original, after, label, sound=True)
    elif status == "ready":
        found["state"] = "missing"
    return found


def job_page(job: Job, runs_dir: Path, number: int, *, tab: str = "") -> JobPage:
    """Everything one step screen shows. Raises ViewError for a damaged state.json."""
    state = read_state(job)
    key = generator_key(job, state)
    reachable = current_step(job, state)
    original = (
        file_if_present(job, "original.mp4") if stage_status(state, "normalize") == "done" else None
    )
    common: dict[str, Any] = {
        "id": job.id,
        "short_id": job.id[:8],
        "step": steps.step(number),
        "bar": step_bar(job.id, number, reachable),
        "mode": state["mode"],
        "read_only": not is_app_job(state, key),
        "progress": progress(state),
        "reachable": reachable,
        "original": original,
        "prep_done": prep_done(state),
        "spent_usd": completed_cost(cost_rows(job, state)),
    }
    decision = saved_review(job)
    if number == 1:
        return JobPage(
            **common,
            state="ready",
            upload=file_if_present(job, "upload.mp4"),
            photo=file_if_present(job, state.get("character_primary")),
            look=_look(state["mode"]),
            description=str(state.get("character_description") or ""),
        )
    if number == 2:
        tabs = tuple(_tab(job, state, item, original) for item in steps.tabs_for(state["mode"]))
        names = tuple(common["progress"]["stages"])
        return JobPage(
            **common,
            tabs=tabs,
            tab=_pick_tab(tabs, tab),
            state=_state_word(combine([stage_status(state, name) for name in names])),
            error=_error(state, names),
        )
    if number == 3:
        try:
            current = fingerprint(job)  # hashed on this page only (never by /api)
        except ReviewError:
            current = ""
        approved = bool(decision.get("approved")) and decision.get("fingerprint") == current
        prepared = file_if_present(job, "prepared.mp4") if common["prep_done"] else None
        wipe = Wipe(original, prepared, "What the AI gets") if original and prepared else None
        return JobPage(
            **common,
            wipe=wipe,
            state="ready" if wipe else "waiting",
            approved=approved,
            fingerprint=current,
        )
    stage = "draft" if number == 4 else "final"
    shown = _generation(job, state, stage, original)
    final = state["stages"].get("final")
    resolution = final.get("resolution", "") if isinstance(final, dict) else ""
    wipe = shown.get("wipe")
    return JobPage(
        **common,
        approved=bool(decision.get("approved")),
        stale=number == 4 and "draft" in stale_stages(job, state),
        prompt=_prompt(job, state) if number == 4 else "",
        resolution=resolution if isinstance(resolution, str) else "",
        download=wipe.after if number == 5 and wipe else None,
        **shown,
    )
````

**Replace the whole file** `src/character_swap/web/static/viewer.css` with:

````css
/* Character Swap app. All colors, sizes and spacing come from the tokens below. */
:root {
  color-scheme: light dark;
  /* surfaces: tonal layers instead of border lines */
  --bg: #f6f5f2;
  --surface: #ffffff;
  --surface-2: #eceae4;
  /* text (muted is >= 4.5:1 on every surface) */
  --text: #1d1c1a;
  --text-muted: #57554f;
  --on-accent: #ffffff;
  /* meaning */
  --accent: #2456c7;
  --accent-soft: #dfe7fb;
  --ok: #1d7a3e;
  --running: #8f5400;
  --bad: #b3261e;
  --bad-bg: #fbe9e7;
  --focus: #2456c7;
  --shadow: 0 8px 32px rgb(29 28 26 / 4%);
  --video-bg: #000000;
  --wipe-line: #ffffff;
  --wipe-label-bg: rgb(0 0 0 / 55%);
  --wipe-label-text: #ffffff;
  /* type */
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --text-xs: 0.75rem;
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
  --radius-pill: 999px;
  --topbar-height: 3.5rem;
  --target: 44px;
  --page-width: 64rem;
  --thumb-height: 9rem;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #151514;
    --surface: #1f1f1d;
    --surface-2: #2a2a27;
    --text: #ecebe7;
    --text-muted: #b3b1aa;
    --on-accent: #10172b;
    --accent: #8fb0ff;
    --accent-soft: #24304d;
    --ok: #6fd08f;
    --running: #f0b35a;
    --bad: #ff8a80;
    --bad-bg: #3a1f1c;
    --focus: #8fb0ff;
    --shadow: 0 8px 32px rgb(0 0 0 / 30%);
  }
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: var(--text-base) / 1.55 var(--font);
}
a { color: var(--accent); }
code, pre { font-family: var(--mono); font-size: var(--text-sm); }
h1 { font-size: var(--text-2xl); line-height: 1.2; margin: 0 0 var(--space-2); }
h2 { font-size: var(--text-xl); line-height: 1.25; margin: 0 0 var(--space-2); }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 4px; }
[hidden] { display: none !important; }
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

/* shared blocks */
.page { max-width: var(--page-width); margin: 0 auto; padding: var(--space-8) var(--space-6) var(--space-12); }
.page-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-4); margin: var(--space-6) 0; }
.meta { color: var(--text-muted); margin: 0; font-size: var(--text-sm); }
.total { margin-top: var(--space-6); }
.card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: var(--space-8);
}
.bad { color: var(--bad); }
.ok { color: var(--ok); font-weight: 600; margin: 0; }

/* buttons: rounded-xl, never pills (UI guide) */
.button {
  display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2);
  min-height: var(--target); padding: 0 var(--space-6);
  border: 1px solid var(--surface-2); border-radius: var(--radius);
  background: transparent; color: var(--text);
  font: inherit; font-weight: 600; text-decoration: none; white-space: nowrap; cursor: pointer;
}
.button:hover { background: var(--surface-2); }
.button.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.button.primary:hover { filter: brightness(1.08); }
.button:disabled, .button[aria-disabled="true"] { opacity: 0.5; cursor: not-allowed; filter: none; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-top: var(--space-6); }

/* home: how it works, the list of swaps */
.how {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-wrap: wrap; gap: var(--space-2);
  counter-reset: how;
}
.how li {
  counter-increment: how;
  background: var(--surface-2); border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3); font-size: var(--text-sm);
}
.how li::before { content: counter(how) ". "; color: var(--text-muted); }
.swaps {
  list-style: none; margin: 0; padding: 0;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: var(--space-6);
}
.swap { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); padding: var(--space-3); }
.swap p { margin-top: var(--space-1); }
.swap-link { display: block; color: var(--text); text-decoration: none; }
.swap-name { display: block; font-weight: 600; margin-top: var(--space-2); }
.thumb {
  display: block; width: 100%; height: var(--thumb-height);
  object-fit: cover; border-radius: var(--radius-sm); background: var(--surface-2);
}
.label-cli { font-style: italic; }
.empty h2 { margin-top: 0; }

/* the step bar */
.bar ol { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-1); }
.bar-item a, .bar-item > span {
  display: inline-flex; align-items: center; gap: var(--space-2);
  min-height: var(--target); padding: 0 var(--space-3);
  border-radius: var(--radius-sm); text-decoration: none; color: var(--text);
}
.bar-item a:hover { background: var(--surface-2); }
.bar-num {
  display: inline-grid; place-items: center; width: 1.75rem; height: 1.75rem;
  border-radius: var(--radius-pill); background: var(--surface-2); font-size: var(--text-sm); font-weight: 700;
}
.bar-done .bar-num { background: var(--ok); color: var(--on-accent); }
.bar-current a, .bar-current > span { background: var(--surface); box-shadow: var(--shadow); font-weight: 600; }
.bar-current .bar-num { background: var(--accent); color: var(--on-accent); }
.bar-locked > span { color: var(--text-muted); }

/* one step screen */
.step-head { margin: var(--space-6) 0; }
.step-count { color: var(--text-muted); font-size: var(--text-sm); margin: 0 0 var(--space-1); }
.line { font-size: var(--text-lg); max-width: 60ch; margin: 0 0 var(--space-3); }
.tags { list-style: none; margin: 0 0 var(--space-4); padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-2); }
.tag {
  background: var(--accent-soft); color: var(--text);
  border-radius: var(--radius-pill); padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs); font-weight: 600; cursor: help;
}
.tabs { display: flex; flex-wrap: wrap; gap: var(--space-1); margin-bottom: var(--space-4); }
.tab {
  display: inline-flex; align-items: center; gap: var(--space-2);
  min-height: var(--target); padding: 0 var(--space-4);
  border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); text-decoration: none;
}
.tab-selected { background: var(--accent); color: var(--on-accent); font-weight: 600; }
.tab-waiting, .tab-missing { color: var(--text-muted); cursor: not-allowed; }
.tab-running { color: var(--running); cursor: progress; }
.tab-failed { color: var(--bad); cursor: not-allowed; }
.panel { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); padding: var(--space-6); }

.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: var(--space-6); }
.media { margin: 0; }
.media figcaption { color: var(--text-muted); font-size: var(--text-sm); margin-top: var(--space-2); }
video, img {
  display: block; width: 100%; max-height: 70vh;
  object-fit: contain; background: var(--video-bg); border-radius: var(--radius-sm);
}
.audio-list { display: grid; gap: var(--space-4); }
audio { width: 100%; }
.placeholder {
  display: grid; place-items: center; min-height: 12rem;
  background: var(--surface-2); color: var(--text-muted);
  border-radius: var(--radius-sm); padding: var(--space-4); text-align: center;
}
.placeholder-missing, .placeholder-failed { color: var(--bad); }
.placeholder-running { color: var(--running); }
.spinner {
  display: inline-block; width: 1em; height: 1em; vertical-align: -0.125em;
  border: 2px solid currentColor; border-right-color: transparent; border-radius: var(--radius-pill);
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.running { background: var(--surface-2); border-radius: var(--radius-sm); padding: var(--space-4); color: var(--running); }
.running .meta { margin-top: var(--space-2); }

.facts { display: grid; grid-template-columns: max-content 1fr; gap: var(--space-2) var(--space-6); margin: var(--space-4) 0 0; }
.facts dt { color: var(--text-muted); }
.facts dd { margin: 0; overflow-wrap: anywhere; }

/* forms */
.field { margin-top: var(--space-4); }
.field > label, .looks legend { display: block; font-weight: 600; margin-bottom: var(--space-1); }
textarea, input[type="file"] {
  width: 100%; font: inherit; color: var(--text);
  background: var(--surface-2); border: 0; border-bottom: 2px solid transparent;
  border-radius: var(--radius-sm); padding: var(--space-3);
}
textarea:focus { border-bottom-color: var(--accent); outline: none; }
.prompt { font-family: var(--mono); font-size: var(--text-sm); min-height: 10rem; }
.drops { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: var(--space-6); }
.drop { display: grid; gap: var(--space-1); }
.drop-title { font-weight: 600; }
.looks { border: 0; padding: 0; margin: var(--space-4) 0 0; }
.look, .check { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); min-height: var(--target); }
.field-error { color: var(--bad); margin: var(--space-1) 0 0; font-size: var(--text-sm); }
.field-error:empty { display: none; }

/* messages */
.error, .notice {
  background: var(--bad-bg); color: var(--text);
  border-radius: var(--radius-sm); padding: var(--space-3) var(--space-4);
  margin: 0 0 var(--space-6);
}
.error p, .notice p { margin: 0 0 var(--space-2); }
.error p:last-child, .notice p:last-child { margin: 0; }
.notice.info { background: var(--surface-2); }
.notice button {
  font: inherit; color: var(--accent); background: none; border: 0;
  min-height: var(--target); padding: 0 var(--space-2); text-decoration: underline; cursor: pointer;
}

/* the before/after wipe player: two stacked videos, the top one clipped at the divider */
.wipe { margin-top: var(--space-2); }
.wipe-stage {
  --split: 50%;
  position: relative; overflow: hidden; aspect-ratio: 16 / 9;
  background: var(--video-bg); border-radius: var(--radius-sm); touch-action: none; cursor: ew-resize;
}
.wipe-stage video {
  position: absolute; inset: 0; width: 100%; height: 100%; max-height: none;
  object-fit: contain; border-radius: 0;
}
.wipe-after { clip-path: inset(0 0 0 var(--split)); }
.wipe-line {
  position: absolute; top: 0; bottom: 0; left: var(--split); width: 2px;
  margin-left: -1px; background: var(--wipe-line); pointer-events: none;
}
.wipe-label {
  position: absolute; bottom: var(--space-2); padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm); background: var(--wipe-label-bg); color: var(--wipe-label-text);
  font-size: var(--text-xs); pointer-events: none;
}
.wipe-label-before { left: var(--space-2); }
.wipe-label-after { right: var(--space-2); }
.wipe-controls { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-top: var(--space-3); }
.wipe-seek { flex: 1 1 12rem; min-height: var(--target); }
.wipe-divider { flex: 0 1 8rem; min-height: var(--target); }

@media (max-width: 768px) {
  .page { padding: var(--space-4) var(--space-4) var(--space-8); }
  .card, .panel { padding: var(--space-4); }
  .bar-item a, .bar-item > span { padding: 0 var(--space-2); }
  .facts { grid-template-columns: minmax(0, 1fr); }
}

@media (prefers-reduced-motion: reduce) {
  .spinner { animation: none; }
  /* side by side instead of a moving divider */
  .wipe-stage { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-1); aspect-ratio: 32 / 9; cursor: default; }
  .wipe-stage video { position: static; clip-path: none; }
  .wipe-line, .wipe-divider { display: none; }
}
````

**Delete** `src/character_swap/web/static/viewer.js`: run `git rm -q src/character_swap/web/static/viewer.js` (it is staged as deleted).

**Replace** in `src/character_swap/web/steps.py`:

````python
# Buttons and short messages.
NEW_SWAP = "New swap"
PREPARE = "Prepare"
NEXT_CHECK = "Next: check it"
APPROVE = "Looks good, approve"
WATCH_FIRST = "Watch it to the end first."
````

with:

````python
# Buttons and short messages.
NEW_SWAP = "New swap"
PREPARE = "Prepare"
NEXT_PREPARE = "Next: prepare"
NEXT_CHECK = "Next: check it"
APPROVE = "Looks good, approve"
WATCH_FIRST = "Watch it to the end first."
````

**Create** `src/character_swap/web/templates/bar.html`:

````html
<nav class="bar" aria-label="Steps">
  <ol>
    {% for item in bar %}
    <li class="bar-item bar-{{ item.state }}">
      {% if item.url %}
      <a href="{{ item.url }}"{% if item.state == "current" %} aria-current="step"{% endif %}><span class="bar-num">{{ item.number }}</span> {{ item.title }}</a>
      {% else %}
      <span{% if item.state == "current" %} aria-current="step"{% endif %}><span class="bar-num">{{ item.number }}</span> {{ item.title }}</span>
      {% endif %}
    </li>
    {% endfor %}
  </ol>
</nav>
````

**Replace the whole file** `src/character_swap/web/templates/base.html` with:

````html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{% block title %}Swaps{% endblock %} · Character Swap</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/static/viewer.css">
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="topbar">
    <a class="brand" href="/">Character Swap</a>
  </header>
  {% block body %}{% endblock %}
</body>
</html>
````

**Create** `src/character_swap/web/templates/home.html`:

````html
{% extends "base.html" %}
{% block title %}Swaps{% endblock %}
{% block body %}
<main id="main" class="page">
  <ol class="how" aria-label="How it works">
    {% for item in steps.HOW_IT_WORKS %}<li>{{ item }}</li>{% endfor %}
  </ol>
  {% if page.missing %}
  <div class="notice" role="alert">
    {% for key in page.missing %}
    <p><code>{{ key.name }}</code> is missing. Add it to the <code>.env</code> file in this folder, then reload. Get it at {{ key.where }}.</p>
    {% endfor %}
  </div>
  {% endif %}
  <div class="page-head">
    <h1>Your swaps</h1>
    {% if page.missing %}
    <span class="button primary" aria-disabled="true">{{ steps.NEW_SWAP }}</span>
    {% else %}
    <a class="button primary" href="/new">{{ steps.NEW_SWAP }}</a>
    {% endif %}
  </div>
  {% if page.jobs %}
  <ul class="swaps">
    {% for job in page.jobs %}
    <li class="swap">
      <a class="swap-link" href="/jobs/{{ job.id }}">
        {% if job.thumb %}<img class="thumb" src="{{ job.thumb }}" alt="" loading="lazy">{% else %}<span class="thumb thumb-empty" aria-hidden="true"></span>{% endif %}
        <span class="swap-name">Swap <code>{{ job.short_id }}</code></span>
      </a>
      {% if job.error %}
      <p class="bad">Unreadable: {{ job.error }}</p>
      {% else %}
      <p class="meta">Step {{ job.step }} · {{ steps.step(job.step).title }} · {{ money(job.cost_usd) }}</p>
      {% if job.read_only %}<p class="meta label-cli">Made with the command line</p>{% endif %}
      {% endif %}
      <p class="meta"><time datetime="{{ job.created_at }}">{{ job.created_at[:16]|replace("T", " ") }}</time></p>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <div class="card empty">
    <h2>No swaps yet</h2>
    <p>Press {{ steps.NEW_SWAP }} to start with a short clip and a photo.</p>
  </div>
  {% endif %}
  <p class="meta total">Spent on all swaps: <strong>{{ money(page.all_jobs_usd) }}</strong> · estimates</p>
</main>
{% endblock %}
````

**Replace the whole file** `src/character_swap/web/templates/job.html` with:

````html
{% extends "base.html" %}
{% from "macros.html" import tags %}
{% block title %}Step {{ page.step.number }} · {{ page.step.title }}{% endblock %}
{% block body %}
<main id="main" class="page" data-job-id="{{ page.id }}" data-poll="{{ page.progress.poll }}" data-progress="{{ progress_json }}">
  {% include "bar.html" %}
  <div class="step-head">
    <p class="step-count">Step {{ page.step.number }} of 5 · Swap <code>{{ page.short_id }}</code></p>
    <h1>{{ page.step.heading }}</h1>
    <p class="line">{{ page.step.line }}</p>
    {{ tags(page.step.tags) }}
  </div>
  <p id="new-results" class="notice info" role="status" hidden>
    {{ steps.NEW_RESULTS }}
    <button type="button" id="refresh-button">{{ steps.REFRESH }}</button>
  </p>
  {% if page.read_only %}
  <p class="notice info">{{ steps.READ_ONLY.format(id=page.id) }}</p>
  {% endif %}
  {% if page.error %}
  <div class="error" role="alert"><p>{{ page.error }}</p></div>
  {% endif %}
  {% include "step-%d.html" % page.step.number %}
</main>
{% endblock %}
````

**Delete** `src/character_swap/web/templates/jobs.html`: run `git rm -q src/character_swap/web/templates/jobs.html` (it is staged as deleted).

**Create** `src/character_swap/web/templates/macros.html`:

````html
{% macro tags(items) %}
{% if items %}
<ul class="tags" aria-label="Techniques">
  {% for tag in items %}
  <li class="tag" title="{{ tag.tip }}">{{ tag.name }}<span class="sr-only">: {{ tag.tip }}</span></li>
  {% endfor %}
</ul>
{% endif %}
{% endmacro %}

{% macro wipe(w) %}
<div class="wipe" data-wipe>
  <div class="wipe-stage">
    <video class="wipe-before" muted playsinline preload="auto" src="{{ w.before }}"></video>
    <video class="wipe-after" muted playsinline preload="auto" src="{{ w.after }}"></video>
    <span class="wipe-line" aria-hidden="true"></span>
    <span class="wipe-label wipe-label-before">Original</span>
    <span class="wipe-label wipe-label-after">{{ w.after_label }}</span>
  </div>
  <div class="wipe-controls">
    <button type="button" class="button wipe-play">Play</button>
    <input type="range" class="wipe-seek" min="0" max="1000" value="0" step="1" aria-label="Position in the clip">
    <input type="range" class="wipe-divider" min="0" max="100" value="50" aria-label="Divider: move it to compare">
    {% if w.sound %}<button type="button" class="button wipe-sound" aria-pressed="false">Sound off</button>{% endif %}
  </div>
</div>
{% endmacro %}

{% macro placeholder(state) %}
<div class="placeholder placeholder-{{ state }}">
  {% if state == "running" %}<span class="spinner" aria-hidden="true"></span> {{ steps.RUNNING }}
  {% elif state == "missing" %}{{ steps.FILE_MISSING }}
  {% elif state == "failed" %}This step failed.
  {% else %}{{ steps.WAITING }}{% endif %}
</div>
{% endmacro %}
````

**Replace** in `src/character_swap/web/templates/message.html`:

````html
  <div class="card message">
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    <p><a href="/">Back to all jobs</a></p>
  </div>
</main>
{% endblock %}
````

with:

````html
  <div class="card message">
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    <p><a href="/">Back to your swaps</a></p>
  </div>
</main>
{% endblock %}
````

**Create** `src/character_swap/web/templates/new.html`:

````html
{% extends "base.html" %}
{% block title %}New swap{% endblock %}
{% block body %}
<main id="main" class="page">
  {% include "bar.html" %}
  <div class="step-head">
    <p class="step-count">Step 1 of 5</p>
    <h1>{{ step.heading }}</h1>
    <p class="line">{{ step.line }}</p>
  </div>
  {% if missing %}
  <div class="notice" role="alert">
    {% for key in missing %}
    <p><code>{{ key.name }}</code> is missing. Add it to the <code>.env</code> file in this folder, then reload. Get it at {{ key.where }}.</p>
    {% endfor %}
  </div>
  {% endif %}
  <form class="card upload" id="new-swap" data-post="/jobs" data-draft-per-second="{{ draft_per_second }}" novalidate>
    <div class="drops">
      <div class="field">
        <label class="drop" for="clip"><span class="drop-title">Your clip</span><span class="meta">One person, 4 to 30 seconds, mp4 or mov</span></label>
        <input id="clip" name="clip" type="file" accept="video/mp4,video/quicktime,.mp4,.mov" required>
        <p class="field-error" data-error-for="clip" role="alert"></p>
      </div>
      <div class="field">
        <label class="drop" for="photo"><span class="drop-title">Your photo</span><span class="meta">jpg, png or webp, face clearly visible</span></label>
        <input id="photo" name="photo" type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" required>
        <p class="field-error" data-error-for="photo" role="alert"></p>
      </div>
    </div>
    <div class="field">
      <label for="describe">{{ steps.DESCRIBE }}</label>
      <textarea id="describe" name="describe" maxlength="300" rows="2"></textarea>
      <p class="meta">{{ steps.DESCRIBE_HINT }}</p>
      <p class="field-error" data-error-for="describe" role="alert"></p>
    </div>
    <fieldset class="field looks">
      <legend>Look</legend>
      {% for value, label, hint in steps.LOOKS %}
      <label class="look"><input type="radio" name="look" value="{{ value }}"{% if loop.first %} checked{% endif %}> <span>{{ label }}</span> <span class="meta">{{ hint }}</span></label>
      {% endfor %}
    </fieldset>
    <div class="field">
      <label class="check"><input type="checkbox" name="rights" value="yes" required> {{ steps.RIGHTS }}</label>
      <p class="field-error" data-error-for="rights" role="alert"></p>
    </div>
    <p class="field-error" data-error-for="form" role="alert"></p>
    <div class="actions">
      <button type="submit" class="button primary" data-usd="{{ prepare_usd }}"{% if missing %} disabled{% endif %}>{{ steps.PREPARE }} · about {{ money(prepare_usd) }}</button>
      <p class="meta" id="draft-hint" hidden></p>
    </div>
  </form>
</main>
{% endblock %}
````

**Create** `src/character_swap/web/templates/step-1.html`:

````html
<div class="pair">
  <figure class="media">
    {% if page.upload %}<video controls preload="metadata" playsinline src="{{ page.upload }}"></video>{% else %}<div class="placeholder">{{ steps.FILE_MISSING }}</div>{% endif %}
    <figcaption>Your clip</figcaption>
  </figure>
  <figure class="media">
    {% if page.photo %}<img src="{{ page.photo }}" alt="Your photo">{% else %}<div class="placeholder">No photo.</div>{% endif %}
    <figcaption>Your photo</figcaption>
  </figure>
</div>
<dl class="facts">
  <dt>Look</dt><dd>{{ page.look }}</dd>
  {% if page.description %}<dt>Described as</dt><dd>{{ page.description }}</dd>{% endif %}
</dl>
<div class="actions">
  <a class="button primary" href="/jobs/{{ page.id }}/step/2">{{ steps.NEXT_PREPARE }}</a>
</div>
````

**Create** `src/character_swap/web/templates/step-2.html`:

````html
{% from "macros.html" import tags, wipe, placeholder %}
{% if page.tabs %}
<nav class="tabs" aria-label="What preparing made">
  {% for tab in page.tabs %}
  {% if tab.state == "ready" %}
  <a class="tab{% if tab.id == page.tab.id %} tab-selected{% endif %}" href="{{ tab.url }}"{% if tab.id == page.tab.id %} aria-current="page"{% endif %}>{{ tab.title }}</a>
  {% else %}
  <span class="tab tab-{{ tab.state }}" aria-disabled="true">{% if tab.state == "running" %}<span class="spinner" aria-hidden="true"></span> {% endif %}{{ tab.title }}<span class="sr-only"> ({{ tab.state }})</span></span>
  {% endif %}
  {% endfor %}
</nav>
{% set tab = page.tab %}
<section class="panel" aria-labelledby="tab-title">
  <h2 id="tab-title">{{ tab.title }}</h2>
  <p class="line">{{ tab.line }}</p>
  {{ tags(tab.tags) }}
  {% if tab.wipe %}
  {{ wipe(tab.wipe) }}
  {% elif tab.audio %}
  <div class="audio-list">
    {% for label, url in tab.audio %}
    <figure class="media"><audio controls preload="metadata" src="{{ url }}"></audio><figcaption>{{ label }}</figcaption></figure>
    {% endfor %}
  </div>
  {% else %}
  {{ placeholder(tab.state) }}
  {% endif %}
  {% if tab.texts %}
  <dl class="facts">
    {% for label, value in tab.texts %}<dt>{{ label }}</dt><dd>{{ value }}</dd>{% endfor %}
  </dl>
  {% endif %}
</section>
{% else %}
<p class="line">{{ steps.DIRECT_PREPARE }}</p>
{% if page.original %}<video controls preload="metadata" playsinline src="{{ page.original }}"></video>{% endif %}
{% endif %}
<div class="actions">
  {% if page.prep_done %}
  <a class="button primary" href="/jobs/{{ page.id }}/step/3">{{ steps.NEXT_CHECK }}</a>
  {% elif page.state == "running" %}
  <p class="meta">{{ steps.PREPARE_TIME }}</p>
  {% endif %}
</div>
````

**Create** `src/character_swap/web/templates/step-3.html`:

````html
{% from "macros.html" import wipe, placeholder %}
{% if page.wipe %}
{{ wipe(page.wipe) }}
{% else %}
{{ placeholder("waiting") }}
{% endif %}
<div class="actions">
  {% if page.approved %}
  <p class="ok">Approved.</p>
  <a class="button primary" href="/jobs/{{ page.id }}/step/4">{{ steps.NEXT_DRAFT }}</a>
  {% elif not page.prep_done %}
  <a class="button" href="/jobs/{{ page.id }}/step/2">{{ steps.NEXT_PREPARE }}</a>
  {% endif %}
</div>
````

**Create** `src/character_swap/web/templates/step-4.html`:

````html
{% from "macros.html" import wipe, placeholder %}
{% if page.prompt %}
<div class="field">
  <label for="prompt">Prompt</label>
  <textarea id="prompt" class="prompt" rows="8" readonly>{{ page.prompt }}</textarea>
</div>
{% endif %}
{% if page.state == "running" %}
<div class="running" role="status">
  <span class="spinner" aria-hidden="true"></span> Making the draft <span class="elapsed" data-started="{{ page.started_at }}"></span>
  <p class="meta">{{ steps.DRAFT_TIME }}</p>
</div>
{% elif page.wipe %}
{{ wipe(page.wipe) }}
{% if page.stale %}<p class="notice">{{ steps.STALE_DRAFT }}</p>{% endif %}
{% else %}
{{ placeholder(page.state) }}
{% endif %}
<div class="actions">
  {% if page.wipe and not page.stale %}
  <a class="button primary" href="/jobs/{{ page.id }}/step/5">{{ steps.NEXT_FINAL }}</a>
  {% endif %}
</div>
````

**Create** `src/character_swap/web/templates/step-5.html`:

````html
{% from "macros.html" import wipe, placeholder %}
{% if page.state == "running" %}
<div class="running" role="status">
  <span class="spinner" aria-hidden="true"></span> Making the {{ page.resolution }} final <span class="elapsed" data-started="{{ page.started_at }}"></span>
  <p class="meta">{{ steps.DRAFT_TIME }}</p>
</div>
{% elif page.wipe %}
{{ wipe(page.wipe) }}
<div class="actions">
  <a class="button primary" href="{{ page.download }}" download="final-{{ page.short_id }}.mp4">{{ steps.DOWNLOAD }}</a>
</div>
{% else %}
{{ placeholder(page.state) }}
{% endif %}
<p class="meta total">Spent on this swap: <strong>{{ money(page.spent_usd) }}</strong> · estimate</p>
````

**Replace the whole file** `src/character_swap/web/view_model.py` with:

````python
"""Shared facts the app shows, computed from a job folder. Pure: no HTTP, no templates."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import av

from character_swap import review
from character_swap.costs import estimate_stages, total_spend
from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError
from character_swap.media.video import stream_seconds
from character_swap.modes import GENERATION_STAGES, PROVIDER_NAMES
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
    PAID_STAGES,
    PREP_STAGES_BY_MODE,
    generator_key,
)
from character_swap.review import ReviewError, fingerprint

# Stage order per mode lives in pipeline.PREP_STAGES_BY_MODE (what prepare() runs).
STAGES_BY_MODE = PREP_STAGES_BY_MODE
APP_MODES = ("depth", "depth_mesh")  # the modes the app's buttons work for (A3)
ESTIMATE_SECONDS = 30  # per-run prices ignore length; matches the CLI's "up to 30 s" estimate
STEP_NAMES = {
    "separate": "Separate vocals",
    "transcribe": "Transcript",
    "describe": "Scene description",
    "depth": "Depth video",
    "masks": "SAM 3 mask",
    "draft": "Draft (480p)",
    "final": "Final (720p)",
}
# The small cost table: each paid stage's saved provider record (feeds "money spent").
PAID_RECORD: dict[str, str] = {
    "separate": "demucs-prediction.json",
    "transcribe": "whisper-prediction.json",
    "describe": "describe-prediction.json",
    "depth": "depth-prediction.json",
    "masks": "sam/sam-prediction.json",
    "draft": "draft-prediction.json",
    "final": "final-prediction.json",
}
RGB_REPAIR_RECORD = "rgb-repair/sam-prediction.json"
_PREDICTION_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")


class ViewError(ValueError):
    """The job folder cannot be shown (for example a damaged state.json)."""


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
    step: int = 0  # the step its page opens on (1 to 5); 0 when unreadable
    thumb: str | None = None  # a frame of the clip (scene.jpg) or the photo
    read_only: bool = False  # made with the command line: no buttons (A3)


# ---- small pure helpers --------------------------------------------------------
def read_state(job: Job) -> dict[str, Any]:
    """state.json, validated enough to render. Raises ViewError with a readable message."""
    try:
        state = job.state
    except (OSError, ValueError) as exc:
        raise ViewError(f"state.json could not be read: {exc}") from exc
    if not isinstance(state, dict):
        raise ViewError("state.json is not a JSON object.")
    mode = state.get("mode")
    if not isinstance(mode, str) or mode not in STAGES_BY_MODE:
        raise ViewError(f"state.json has an unknown mode: {state.get('mode')!r}.")
    if not isinstance(state.get("stages"), dict):
        raise ViewError("state.json has no stages table.")
    provider = state.get("provider")
    if provider is not None and provider not in SEEDANCE_PROVIDERS:
        raise ViewError(f"state.json has an unknown provider: {provider!r}.")
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


def prep_done(state: dict[str, Any]) -> bool:
    return all(stage_status(state, name) == "done" for name in STAGES_BY_MODE[state["mode"]])


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


def saved_review(job: Job) -> dict[str, Any]:
    """review.json as saved: read, never recomputed (no hashing). {} when missing or damaged."""
    try:
        data = json.loads(job.path("review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def current_step(job: Job, state: dict[str, Any]) -> int:
    """The step a job's page opens on. Cheap: state and the saved review only."""
    if not prep_done(state):
        return 2
    decision = saved_review(job)
    if not decision.get("approved"):
        return 3
    draft = state["stages"].get("draft")
    if stage_status(state, "draft") != "done" or not isinstance(draft, dict):
        return 4
    if draft.get("fingerprint") != decision.get("fingerprint"):
        return 4  # the approved inputs changed since the draft: a new draft is next
    return 5


def is_app_job(state: dict[str, Any], key: str) -> bool:
    """A3: the app's buttons only work for one-person WaveSpeed jobs in a depth mode."""
    return (
        key == "seedance:wavespeed"
        and state.get("mode") in APP_MODES
        and not state.get("character_secondary")
    )


def prediction_id(job: Job, record: str) -> str | None:
    """The confirmed id in a saved prediction record; None when there is none."""
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = data.get("id") if isinstance(data, dict) else None
    if not isinstance(found, str) or not _PREDICTION_ID.fullmatch(found):
        return None  # includes the {"status": "creating"} marker written before a paid POST
    return found


def prediction_url(job: Job, record: str) -> str | None:
    """Link to a saved Replicate prediction; None when there is no confirmed prediction id."""
    found = prediction_id(job, record)
    return f"https://replicate.com/p/{found}" if found else None


def file_url(job_id: str, name: str) -> str:
    return f"/jobs/{job_id}/files/{quote(name)}"


def file_if_present(job: Job, name: object) -> str | None:
    """The file's URL when `name` is a file inside the job folder, else None."""
    if not isinstance(name, str) or not name:
        return None
    try:
        present = job.path(name).is_file()
    except JobError:
        return None
    return file_url(job.id, name) if present else None


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


def tilde(text: str) -> str:
    """Hide the user's home directory in text shown on screen."""
    return text.replace(str(Path.home()), "~")


def _size_text(path: Path) -> str:
    size = path.stat().st_size
    return f"{size / 1_000_000:.1f} MB" if size >= 100_000 else f"{size / 1000:.0f} KB"


def video_facts(path: Path) -> str:
    """'8.0 s · 1.2 MB'; a damaged or partial file is reported, never raised."""
    try:
        size = _size_text(path)
    except OSError:
        return "file missing"  # vanished since the folder scan
    try:
        seconds = stream_seconds(path, "video")
    except (OSError, ValueError, av.error.FFmpegError):
        return f"unreadable video · {size}"
    return f"{seconds:.1f} s · {size}"


def review_status(job: Job) -> dict[str, Any]:
    """review.status() hashes every reviewed file; any read problem shows as unavailable."""
    try:
        decision = review.status(job)
    except (OSError, ValueError) as exc:
        return {"approved": False, "status": "unavailable", "note": str(exc)}
    return decision if isinstance(decision, dict) else {"status": "unavailable"}


# ---- costs -------------------------------------------------------------------
def _usd(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _cost_entries(state: dict[str, Any]) -> list[dict[str, Any]]:
    entries = state.get("costs", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _generation_cost(job: Job, state: dict[str, Any], stage: str) -> float:
    """The estimate logged when this stage's current prediction was created.

    Matched by prediction id, so a draft remade after new inputs never counts the old
    charges (every charge still shows in "Logged for this job"). When nothing matches, only
    older log lines without an id are used.
    """
    entries = [entry for entry in _cost_entries(state) if entry.get("step") == stage]
    current = prediction_id(job, PAID_RECORD[stage])
    matching = [entry for entry in entries if current and entry.get("task_id") == current]
    if not matching:
        matching = [entry for entry in entries if not entry.get("task_id")]
    return round(sum(_usd(entry.get("usd")) for entry in matching), 4)


def _is_seedance(key: str) -> bool:
    return key.startswith("seedance")


def cost_rows(job: Job, state: dict[str, Any]) -> tuple[CostRow, ...]:
    """One row per paid stage that is done. Derived from state, never stored."""
    rows = []
    key = generator_key(job, state)
    seedance = _is_seedance(key)
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
        rows.append(
            CostRow(
                _generation_name(state, stage, key),
                PROVIDER_NAMES.get(key, "Replicate"),
                _generation_cost(job, state, stage),
                None if seedance else prediction_url(job, PAID_RECORD[stage]),
            )
        )
    return tuple(rows)


def _generation_name(state: dict[str, Any], stage: str, key: str) -> str:
    """'Final (1080p)': the resolution the final used, else the generator's default final."""
    if stage != "final":
        return STEP_NAMES[stage]
    entry = state.get("stages", {}).get("final")
    resolution = entry.get("resolution") if isinstance(entry, dict) else None
    if not isinstance(resolution, str) or not resolution:
        resolution = FINAL_RESOLUTIONS.get(key, ("720p",))[0]
    return f"Final ({resolution})"


def completed_cost(rows: tuple[CostRow, ...]) -> float:
    return round(sum(row.usd for row in rows), 4)


def logged_cost(state: dict[str, Any]) -> float:
    return round(sum(_usd(entry.get("usd")) for entry in _cost_entries(state)), 4)


def costs_view(job: Job, state: dict[str, Any], runs_dir: Path) -> CostsView:
    rows = cost_rows(job, state)
    return CostsView(rows, completed_cost(rows), logged_cost(state), total_spend(runs_dir))


_UNKNOWN = object()  # the inputs could not be read this time: say nothing about staleness


def stale_stages(job: Job, state: dict[str, Any]) -> frozenset[str]:
    """Finished draft/final made from inputs that are no longer the approved ones.

    Same rule as pipeline.stale_generation, but the inputs are hashed once per page.
    """
    done = [stage for stage in GENERATION_STAGES if stage_status(state, stage) == "done"]
    if not done:
        return frozenset()
    try:
        current: object = fingerprint(job)
    except ReviewError:
        current = None  # the reviewed files are gone: nothing saved can be proven current
    except (OSError, ValueError):
        current = _UNKNOWN
    if current is _UNKNOWN:
        return frozenset()
    return frozenset(
        stage
        for stage in done
        if current is None or state["stages"][stage].get("fingerprint") != current
    )


# ---- the home list -------------------------------------------------------------
def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")


def _thumb(job: Job, state: dict[str, Any]) -> str | None:
    """A frame of the clip (the scene description's still), else the character photo."""
    return file_if_present(job, "scene.jpg") or file_if_present(job, state.get("character_primary"))


def _summarize(runs_dir: Path, folder: Path) -> JobSummary | None:
    try:
        job = Job.load(runs_dir, folder.name)
    except JobError:
        return None  # not a job folder (spend log, stray files, half-created jobs)
    try:
        state = read_state(job)
        key = generator_key(job, state)
    except (ViewError, JobError) as exc:
        return JobSummary(
            job.id, job.id[:8], "?", "?", 0, 0, 0.0, _mtime_iso(folder), tilde(str(exc))
        )
    info = progress(state)
    return JobSummary(
        id=job.id,
        short_id=job.id[:8],
        generator=str(state.get("generator", "?")),
        mode=state["mode"],
        done_count=info["done_count"],
        total_count=info["total_count"],
        cost_usd=completed_cost(cost_rows(job, state)),
        created_at=str(state.get("created_at") or _mtime_iso(folder)),
        step=current_step(job, state),
        thumb=_thumb(job, state),
        read_only=not is_app_job(state, key),
    )


def list_jobs(runs_dir: Path) -> list[JobSummary]:
    """Every job folder, newest first. Never hashes files (no review status here)."""
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    summaries = []
    for folder in runs_dir.iterdir():
        try:
            summary = _summarize(runs_dir, folder)
        except OSError:
            continue  # folder removed or unreadable mid-scan
        if summary:
            summaries.append(summary)
    return sorted(summaries, key=lambda summary: summary.created_at, reverse=True)
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_cli.py tests/test_pages.py tests/test_view_model.py tests/test_web_app.py -q --no-cov`  
Expected: PASS (334 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1001 passed, 1 deselected; coverage above 96 %
git add src/character_swap/costs.py src/character_swap/web/app.py src/character_swap/web/pages.py src/character_swap/web/static/viewer.css src/character_swap/web/steps.py src/character_swap/web/templates/bar.html src/character_swap/web/templates/base.html src/character_swap/web/templates/home.html src/character_swap/web/templates/job.html src/character_swap/web/templates/macros.html src/character_swap/web/templates/message.html src/character_swap/web/templates/new.html src/character_swap/web/templates/step-1.html src/character_swap/web/templates/step-2.html src/character_swap/web/templates/step-3.html src/character_swap/web/templates/step-4.html src/character_swap/web/templates/step-5.html src/character_swap/web/view_model.py tests/test_cli.py tests/test_pages.py tests/test_view_model.py tests/test_web_app.py tests/viewer_jobs.py
git commit -m "feat: the five step screens over a job's files (read-only)"
````

---

### Task 3: The before/after wipe player and live progress (`static/app.js`)

One script for every page (no inline scripts: the CSP allows only `/static`). It drives each
wipe player (one play/pause and one seek bar for both videos; the "after" video leads and the
"before" one is put back in step when they drift by more than 0.1 s; a range input or a drag
on the video moves the divider; the sound toggle unmutes the "after" side), shows the elapsed
time of a running draft or final, and polls `/api/jobs/{id}` (2 s while work runs, 10 s while
the job waits). The page reloads when what it was rendered with changes, unless a video is
playing; then it offers a Refresh button. When "what the AI gets" plays to the end it fires
`wipe:ended`, which Task 11's approve gate listens for.

**Files:**
- Create: `src/character_swap/web/static/app.js`
- Modify: `src/character_swap/web/templates/base.html`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write the tests**

**Append** to `tests/test_web_app.py`:

````python


# ---- the wipe player and live progress (static/app.js) ---------------------------------
def test_every_page_loads_the_one_app_script(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    for path in ["/", "/new", f"/jobs/{job.id}/step/2"]:
        assert '<script src="/static/app.js" defer></script>' in client.get(path).text


def test_app_script_drives_the_wipe_player(client):
    script = client.get("/static/app.js").text
    assert 'setProperty("--split"' in script  # CSSOM, allowed by the CSP (no inline styles)
    assert "DRIFT = 0.1" in script and "before.currentTime = after.currentTime" in script
    assert "pointerdown" in script and 'divider.addEventListener("input"' in script
    assert "after.muted = !after.muted" in script  # the sound plays the "after" side
    assert '"wipe:ended"' in script  # step 3's approve gate listens for it


def test_app_script_polls_and_never_interrupts_playback(client):
    script = client.get("/static/app.js").text
    assert "fast: 2000" in script and "slow: 10000" in script
    assert "Object.keys(shown)" in script  # compares only what the page was rendered with
    assert "if (!playing())" in script and 'getElementById("new-results").hidden = false' in script
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_web_app.py -q --no-cov`  
Expected: FAIL. 3 failed, 51 passed: `app.js` does not exist yet (404) and no page loads it.

- [ ] **Step 3: Write the code**

**Create** `src/character_swap/web/static/app.js`:

````javascript
// Character Swap app: the before/after wipe player, live progress and the elapsed timer.
// No inline scripts anywhere (the CSP allows only files from /static).
(() => {
  "use strict";
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DRIFT = 0.1; // seconds the "before" video may drift before it is put back in step

  // 1. The wipe player: two videos in one box, the top one clipped at a draggable divider.
  function setupWipe(root) {
    const stage = root.querySelector(".wipe-stage");
    const before = root.querySelector(".wipe-before");
    const after = root.querySelector(".wipe-after"); // leads: it may carry the sound
    const play = root.querySelector(".wipe-play");
    const seek = root.querySelector(".wipe-seek");
    const divider = root.querySelector(".wipe-divider");
    const sound = root.querySelector(".wipe-sound");

    function split(percent) {
      const value = Math.min(100, Math.max(0, percent));
      stage.style.setProperty("--split", `${value}%`); // CSSOM: allowed by the CSP
      divider.value = String(Math.round(value));
    }
    divider.addEventListener("input", () => split(Number(divider.value)));
    let dragging = false;
    function fromPointer(event) {
      const box = stage.getBoundingClientRect();
      if (box.width > 0) split(((event.clientX - box.left) / box.width) * 100);
    }
    stage.addEventListener("pointerdown", (event) => {
      if (reduceMotion) return; // side by side: there is no divider to drag
      dragging = true;
      stage.setPointerCapture(event.pointerId);
      fromPointer(event);
    });
    stage.addEventListener("pointermove", (event) => {
      if (dragging) fromPointer(event);
    });
    ["pointerup", "pointercancel"].forEach((name) =>
      stage.addEventListener(name, () => {
        dragging = false;
      })
    );

    function inStep() {
      if (Math.abs(before.currentTime - after.currentTime) > DRIFT) {
        before.currentTime = after.currentTime;
      }
    }
    function showState() {
      play.textContent = after.paused ? "Play" : "Pause";
    }
    play.addEventListener("click", () => {
      if (after.paused) {
        if (after.ended) after.currentTime = 0;
        inStep();
        after.play().catch(() => {});
        before.play().catch(() => {});
      } else {
        after.pause();
        before.pause();
      }
    });
    after.addEventListener("play", showState);
    after.addEventListener("pause", showState);
    after.addEventListener("ended", () => {
      before.pause();
      showState();
      root.dispatchEvent(new CustomEvent("wipe:ended", { bubbles: true }));
    });
    after.addEventListener("timeupdate", () => {
      inStep();
      if (after.duration) {
        seek.value = String(Math.round((after.currentTime / after.duration) * 1000));
      }
    });
    seek.addEventListener("input", () => {
      if (!after.duration) return;
      after.currentTime = (Number(seek.value) / 1000) * after.duration;
      before.currentTime = after.currentTime;
    });
    if (sound) {
      sound.addEventListener("click", () => {
        after.muted = !after.muted; // the draft and final carry the original sound
        sound.setAttribute("aria-pressed", String(!after.muted));
        sound.textContent = after.muted ? "Sound off" : "Sound on";
      });
    }
    split(50);
  }
  document.querySelectorAll("[data-wipe]").forEach(setupWipe);

  // 2. How long a running draft or final has been going.
  document.querySelectorAll(".elapsed[data-started]").forEach((node) => {
    const started = Date.parse(node.dataset.started);
    if (Number.isNaN(started)) return;
    function tick() {
      const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const minutes = Math.floor(seconds / 60);
      node.textContent = `· ${minutes}:${String(seconds % 60).padStart(2, "0")}`;
    }
    tick();
    window.setInterval(tick, 1000);
  });

  // 3. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
  let mode = main.dataset.poll;
  if (!Object.hasOwn(POLL_MS, mode)) return;
  const shown = JSON.parse(main.dataset.progress);
  const url = `/api/jobs/${main.dataset.jobId}`;
  const refresh = document.getElementById("refresh-button");
  if (refresh) refresh.addEventListener("click", () => location.reload());

  function playing() {
    return [...document.querySelectorAll("video, audio")].some((m) => !m.paused && !m.ended);
  }
  function changed(data) {
    // Only the keys the page was rendered with are compared (the server picks them).
    return Object.keys(shown).some((key) => JSON.stringify(data[key]) !== JSON.stringify(shown[key]));
  }
  async function poll() {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        if (changed(data)) {
          if (!playing()) {
            location.reload();
            return;
          }
          document.getElementById("new-results").hidden = false; // never interrupt playback
        }
        mode = data.poll === "off" ? "slow" : data.poll;
        if (!Object.hasOwn(POLL_MS, mode)) mode = "slow";
      }
    } catch (error) {
      // the app may be restarting; keep trying
    }
    window.setTimeout(poll, POLL_MS[mode]);
  }
  window.setTimeout(poll, POLL_MS[mode]);
})();
````

**Replace** in `src/character_swap/web/templates/base.html`:

````html
    <a class="brand" href="/">Character Swap</a>
  </header>
  {% block body %}{% endblock %}
</body>
</html>
````

with:

````html
    <a class="brand" href="/">Character Swap</a>
  </header>
  {% block body %}{% endblock %}
  <script src="/static/app.js" defer></script>
</body>
</html>
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_web_app.py -q --no-cov`  
Expected: PASS (54 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1004 passed, 1 deselected; coverage above 96 %
git add src/character_swap/web/static/app.js src/character_swap/web/templates/base.html tests/test_web_app.py
git commit -m "feat: before/after wipe player and live progress"
````

---

### Task 4: `charswap app` (`charswap view` stays as its old name)

A14: the app replaces the viewer. `charswap app` and `charswap view` run the same function
(`_app`, which replaces `_view`); the app re-reads `.env` on each request (A4) through
`reload_settings`. The existing view tests now drive `app`.

**Files:**
- Modify: `src/character_swap/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the tests**

**Replace** in `tests/test_cli.py`:

````python
    server = FakeServer()
    opened = []
    code, out = run(
        ["view", "--port", str(port), "--no-browser"],
        settings,
        serve=server,
        open_browser=opened.append,
````

with:

````python
    server = FakeServer()
    opened = []
    code, out = run(
        ["app", "--port", str(port), "--no-browser"],
        settings,
        serve=server,
        open_browser=opened.append,
````

**Replace** in `tests/test_cli.py`:

````python
    assert code == 0 and opened == []
    [call] = server.calls
    assert call["host"] == "127.0.0.1" and call["port"] == port
    assert f"Job viewer: http://127.0.0.1:{port}" in out.text and "Viewer stopped." in out.text
    page = TestClient(call["app"], base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and "No swaps yet" in page.text


def test_view_has_no_host_option(settings):
    with pytest.raises(SystemExit):
        run(["view", "--host", "0.0.0.0", "--no-browser"], settings, serve=FakeServer())


def test_view_port_check_binds_localhost_only(settings, monkeypatch):
````

with:

````python
    assert code == 0 and opened == []
    [call] = server.calls
    assert call["host"] == "127.0.0.1" and call["port"] == port
    assert f"Character Swap app: http://127.0.0.1:{port}" in out.text and "App stopped." in out.text
    page = TestClient(call["app"], base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and "No swaps yet" in page.text


def test_view_has_no_host_option(settings):
    for command in ("app", "view"):
        with pytest.raises(SystemExit):
            run([command, "--host", "0.0.0.0", "--no-browser"], settings, serve=FakeServer())


def test_view_port_check_binds_localhost_only(settings, monkeypatch):
````

**Replace** in `tests/test_cli.py`:

````python
def test_view_default_port(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    server = FakeServer()
    code, _ = run(["view", "--no-browser"], settings, serve=server)
    assert code == 0 and server.calls[0]["port"] == 8770


````

with:

````python
def test_view_default_port(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    server = FakeServer()
    code, _ = run(["app", "--no-browser"], settings, serve=server)
    assert code == 0 and server.calls[0]["port"] == 8770


````

**Replace** in `tests/test_cli.py`:

````python
        opened.set()

    code, _ = run(
        ["view", "--port", str(port)],
        settings,
        serve=FakeServer(wait_for=opened),
        open_browser=open_browser,
````

with:

````python
        opened.set()

    code, _ = run(
        ["app", "--port", str(port)],
        settings,
        serve=FakeServer(wait_for=opened),
        open_browser=open_browser,
````

**Replace** in `tests/test_cli.py`:

````python
    urls = []
    with pytest.raises(OSError):
        run(
            ["--debug", "view", "--port", str(free_port())],
            settings,
            serve=FakeServer(raise_on_start=OSError("bind failed")),
            open_browser=urls.append,
````

with:

````python
    urls = []
    with pytest.raises(OSError):
        run(
            ["--debug", "app", "--port", str(free_port())],
            settings,
            serve=FakeServer(raise_on_start=OSError("bind failed")),
            open_browser=urls.append,
````

**Replace** in `tests/test_cli.py`:

````python


def test_view_ctrl_c_stops_cleanly(settings):
    # real uvicorn normally handles SIGINT itself and returns, so "Viewer stopped." prints via
    # the normal path; this test covers the fallback.
    server = FakeServer(raise_on_start=KeyboardInterrupt())
    code, out = run(["view", "--port", str(free_port()), "--no-browser"], settings, serve=server)
    assert code == 0 and "Viewer stopped." in out.text
    assert "Interrupted" not in out.err.text and "resume" not in out.err.text


def test_view_bind_failure_at_startup_is_explained(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)  # lost the race after the check
    server = FakeServer(raise_on_start=SystemExit(1))  # what real uvicorn raises on bind failure
    code, out = run(["view", "--port", "8771", "--no-browser"], settings, serve=server)
    assert code == 2 and "Viewer stopped." not in out.text
    assert "Port 8771 is busy; try `charswap view --port 8772`" in out.err.text


def test_port_free_sees_a_wildcard_listener():
````

with:

````python


def test_view_ctrl_c_stops_cleanly(settings):
    # real uvicorn normally handles SIGINT itself and returns, so "App stopped." prints via
    # the normal path; this test covers the fallback.
    server = FakeServer(raise_on_start=KeyboardInterrupt())
    code, out = run(["app", "--port", str(free_port()), "--no-browser"], settings, serve=server)
    assert code == 0 and "App stopped." in out.text
    assert "Interrupted" not in out.err.text and "resume" not in out.err.text


def test_view_bind_failure_at_startup_is_explained(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)  # lost the race after the check
    server = FakeServer(raise_on_start=SystemExit(1))  # what real uvicorn raises on bind failure
    code, out = run(["app", "--port", "8771", "--no-browser"], settings, serve=server)
    assert code == 2 and "App stopped." not in out.text
    assert "Port 8771 is busy; try `charswap app --port 8772`" in out.err.text


def test_port_free_sees_a_wildcard_listener():
````

**Replace** in `tests/test_cli.py`:

````python
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


def test_provider_errors_are_friendly(tmp_path, prep_settings, video, monkeypatch):
````

with:

````python
        busy.listen()
        port = busy.getsockname()[1]
        server = FakeServer()
        code, out = run(["app", "--port", str(port), "--no-browser"], settings, serve=server)
    assert code == 2 and server.calls == []
    assert f"Port {port} is busy; try `charswap app --port {port + 1}`" in out.err.text


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_view_rejects_bad_ports(settings, port):
    with pytest.raises(SystemExit):
        run(["app", "--port", port], settings, serve=FakeServer())


def test_help_lists_the_app_and_its_old_name(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    assert "open the click-through app" in text and "the same as app" in text
    assert "charswap app" in text


def test_view_is_the_same_as_app(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    server = FakeServer()
    code, out = run(["view", "--no-browser"], settings, serve=server)
    assert code == 0 and "Character Swap app: http://127.0.0.1:8770" in out.text
    assert "App stopped. Work that was already running keeps going." in out.text


def test_the_app_re_reads_dot_env_on_each_request(tmp_path, monkeypatch):
    for name in ("REPLICATE_API_TOKEN", "WAVESPEED_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    server = FakeServer()
    run(["app", "--no-browser"], Settings.load(root=tmp_path, environ={}), serve=server)
    client = TestClient(server.calls[0]["app"], base_url="http://127.0.0.1")
    assert "<code>WAVESPEED_API_KEY</code> is missing" in client.get("/").text
    (tmp_path / ".env").write_text("REPLICATE_API_TOKEN=r\nWAVESPEED_API_KEY=w\n")
    assert "is missing" not in client.get("/").text


def test_provider_errors_are_friendly(tmp_path, prep_settings, video, monkeypatch):
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_cli.py -q --no-cov`  
Expected: FAIL. 10 failed, 177 passed: `charswap: error: argument command: invalid choice: 'app'`.

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/cli.py`:

````python

Out = Callable[[str], None]

VIEW_HOST = "127.0.0.1"  # the viewer is never reachable from the network
BROWSER_DELAY = 1.0  # seconds; lets the server start listening before the browser asks
# Keyed by pipeline.generator_key(job): each generator's paid key and unconfirmed-marker help.
KEYS = {
````

with:

````python

Out = Callable[[str], None]

VIEW_HOST = "127.0.0.1"  # the app is never reachable from the network
BROWSER_DELAY = 1.0  # seconds; lets the server start listening before the browser asks
# Keyed by pipeline.generator_key(job): each generator's paid key and unconfirmed-marker help.
KEYS = {
````

**Replace** in `src/character_swap/cli.py`:

````python
  charswap generate <id> --draft
  charswap generate <id> --final
  charswap generate <id> --final --resolution 1080p
  charswap view

other providers:
  charswap face add frank me.jpg
````

with:

````python
  charswap generate <id> --draft
  charswap generate <id> --final
  charswap generate <id> --final --resolution 1080p
  charswap app

other providers:
  charswap face add frank me.jpg
````

**Replace** in `src/character_swap/cli.py`:

````python
    face_sub.add_parser(
        "renew", help="upload the same saved photo again after PiAPI removed it"
    ).add_argument("name")
    view = sub.add_parser("view", help="open the read-only job viewer in your browser")
    view.add_argument("--port", type=_port, default=8770)
    view.add_argument("--no-browser", action="store_true", help="only print the URL")
    return parser


````

with:

````python
    face_sub.add_parser(
        "renew", help="upload the same saved photo again after PiAPI removed it"
    ).add_argument("name")
    for name, text in (
        ("app", "open the click-through app in your browser"),
        ("view", "the same as app (its old name)"),
    ):
        app = sub.add_parser(name, help=text)
        app.add_argument("--port", type=_port, default=8770)
        app.add_argument("--no-browser", action="store_true", help="only print the URL")
    return parser


````

**Replace** in `src/character_swap/cli.py`:

````python
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
````

with:

````python
    return True


def _app(args, settings, env) -> int:
    from character_swap.web.app import create_app  # keeps other commands fast to start

    port = args.port
    busy = f"Port {port} is busy; try `charswap app --port {port + 1}`"
    if not _port_free(port):
        env.err(busy)
        return 2
    url = f"http://{VIEW_HOST}:{port}"
    # A4: keys stay in .env; the app re-reads it on each request.
    app = create_app(settings, reload_settings=lambda: Settings.load(root=settings.root))
    env.out(f"Character Swap app: {url}  (Ctrl-C to stop)")
    opener = None
    if not args.no_browser:
        opener = threading.Timer(BROWSER_DELAY, env.open_browser, args=[url])
````

**Replace** in `src/character_swap/cli.py`:

````python
    try:
        env.serve(app, host=VIEW_HOST, port=port)
    except KeyboardInterrupt:
        pass  # Ctrl-C is how the viewer is meant to stop; no resume hint here
    except SystemExit as error:  # uvicorn exits with status 1 when it cannot bind
        if error.code in (None, 0):
            raise
        env.err(f"Port {port} is busy; try `charswap view --port {port + 1}`")
        return 2
    finally:
        if opener is not None:
            opener.cancel()
    env.out("Viewer stopped.")
    return 0


````

with:

````python
    try:
        env.serve(app, host=VIEW_HOST, port=port)
    except KeyboardInterrupt:
        pass  # Ctrl-C is how the app is meant to stop; no resume hint here
    except SystemExit as error:  # uvicorn exits with status 1 when it cannot bind
        if error.code in (None, 0):
            raise
        env.err(busy)
        return 2
    finally:
        if opener is not None:
            opener.cancel()
    env.out("App stopped. Work that was already running keeps going.")
    return 0


````

**Replace** in `src/character_swap/cli.py`:

````python
    "generate": _generate,
    "doctor": _doctor,
    "face": _face,
    "view": _view,
}
````

with:

````python
    "generate": _generate,
    "doctor": _doctor,
    "face": _face,
    "app": _app,
    "view": _app,  # the old name of the read-only viewer, which the app replaced
}
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_cli.py -q --no-cov`  
Expected: PASS (187 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1006 passed, 1 deselected; coverage 96.89 %
git add src/character_swap/cli.py tests/test_cli.py
git commit -m "feat: charswap app opens the click-through app (view stays as an alias)"
````

---

### Task 5: Checkpoint: the visual walkthrough on Frank's real job (controller, in a browser)

No code. This is the moment to look at what the video will show before any action is built.
At this point the app has **no POST route at all**, so nothing on the page can start, change
or charge anything; it is safe to open on Frank's real `runs/` folder.

- [ ] **Step 1: Start the app** (from the repo root, on the branch with Tasks 1 to 4):

````bash
uv run charswap app --no-browser --port 8770
# expected: "Character Swap app: http://127.0.0.1:8770  (Ctrl-C to stop)"
````

Run it in the background, and tell Frank it is running.

- [ ] **Step 2: Check it with Playwright** on the finished WaveSpeed depth_mesh job with a draft,
`ea899aff1cc246d8889137286c6be8b9`:
  - `/`: the "how it works" strip, the swap cards (thumbnail, step, money spent), "Made with
    the command line" on the PiAPI/Wan jobs, the total. No "is missing" notice when both
    keys are in `.env` (look at the page, never print the values).
  - `/jobs/ea899aff1cc246d8889137286c6be8b9` redirects to step 5 (the draft is done and matches
    the approval).
  - Step 1: the clip, the photo, "Better lip sync".
  - Step 2: every tab (Depth, Cut-out, Face lines, What the AI gets, Voice). On each video tab
    press Play: both videos move together; drag the divider and use the arrow keys on it.
    Voice: two audio players and the words.
  - Step 3: the wipe original against what the AI gets. Step 4: the prompt and the wipe with
    the draft; the Sound button plays the draft's sound. Step 5: "Not made yet."
  - At 375 px wide nothing overflows sideways; with `prefers-reduced-motion: reduce` emulated,
    the two videos sit side by side; in dark mode the text stays readable.
  - The browser console shows no errors (in particular no CSP violations).

- [ ] **Step 3: Stop the app** (Ctrl-C, or stop the background task) and say so. Fix anything
found in Tasks 2 and 3 before going on; each fix is its own small commit.

---

### Task 6: One cross-process lock per job (`run.lock`)

A7. `lock.job_lock(job, command)` holds `fcntl.flock(LOCK_EX | LOCK_NB)` on
`runs/<id>/run.lock` for the whole block, retrying for up to 2 s, then raises `JobBusy`
("This swap is busy (generate since …). Try again when it finishes. Nothing was charged.",
exit 2 through `cli.main`'s usual `CharSwapError` path). The holder writes
`{pid, command, started_at, process_start}` after locking and clears it before letting go.
`running_holder(job)` only reads the file (it never locks): a live pid whose start time (from
`ps -o lstart=`) matches. `cli.main` takes the lock for `resume`, `review`, `prompt`, `repair`
and `generate` before the command runs; `prepare` takes it itself right after `Job.create`.

**Files:**
- Modify: `src/character_swap/cli.py`
- Create: `src/character_swap/lock.py`
- Test: `tests/test_lock.py`

- [ ] **Step 1: Write the tests**

**Create** `tests/test_lock.py`:

````python
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from character_swap import cli, lock
from character_swap.config import Settings
from character_swap.jobs import Job
from helpers import solid, write_video

CHILD = """
import sys
from pathlib import Path
from character_swap.jobs import Job
from character_swap.lock import job_lock
job = Job.load(Path(sys.argv[1]), sys.argv[2])
with job_lock(job, "generate"):
    print("locked", flush=True)
    sys.stdin.readline()  # hold it until the test is done or kills us
"""


@pytest.fixture
def job(runs_dir, tmp_path):
    video = write_video(tmp_path / "in.mp4", [solid((9, 9, 9))] * 24)
    return Job.create(runs_dir, video, generator="seedance", mode="depth")


def _child(job):
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(job.root.parent), job.id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "locked"
    return child


def test_a_second_holder_is_refused_with_who_holds_it(job):
    with lock.job_lock(job, "generate"):
        holder = lock.read_holder(job)
        assert holder["pid"] == os.getpid() and holder["command"] == "generate"
        assert holder["process_start"] == lock.process_start(os.getpid()) != ""
        busy = pytest.raises(lock.JobBusy, match=r"This swap is busy \(generate since 20")
        with busy, lock.job_lock(job, "review", wait=0.1):
            pytest.fail("got the lock twice")
    assert lock.read_holder(job) is None  # cleared on release: nobody reads it as running


def test_the_lock_is_released_on_error(job):
    with pytest.raises(RuntimeError), lock.job_lock(job, "resume"):
        raise RuntimeError("boom")
    with lock.job_lock(job, "resume", wait=0):
        pass
    assert job.path("run.lock").exists()  # never deleted


def test_another_process_holds_it_until_it_dies(job):
    child = _child(job)
    try:
        holder = lock.running_holder(job)
        assert holder["pid"] == child.pid and holder["command"] == "generate"
        with pytest.raises(lock.JobBusy), lock.job_lock(job, "review", wait=0.1):
            pass
    finally:
        child.kill()
        child.wait()
    assert lock.running_holder(job) is None  # the OS freed it; the content names a dead pid
    with lock.job_lock(job, "review", wait=0):
        pass


def test_a_reused_pid_is_not_running(job):
    info = {"pid": os.getpid(), "command": "generate", "process_start": "Mon Jan  1 00:00:00 2001"}
    job.path("run.lock").write_text(json.dumps(info))
    assert lock.read_holder(job)["pid"] == os.getpid()
    assert lock.running_holder(job) is None


@pytest.mark.parametrize("content", ["", "{", "[]", '{"pid": "12"}'])
def test_damaged_lock_content_reads_as_nobody(job, content):
    job.path("run.lock").write_text(content)
    assert lock.read_holder(job) is None and lock.running_holder(job) is None


def test_a_brief_overlap_is_absorbed(job):
    taken = threading.Event()

    def hold():
        with lock.job_lock(job, "prompt"):
            taken.set()
            time.sleep(0.3)

    worker = threading.Thread(target=hold)
    worker.start()
    taken.wait(5)
    with lock.job_lock(job, "review", wait=2):  # retries until the other one lets go
        assert lock.read_holder(job)["command"] == "review"
    worker.join()


def test_process_start_of_a_missing_process_is_unknown(monkeypatch):
    assert lock.process_start(2**22 + 12345) == ""

    def broken(*args, **kwargs):
        raise OSError("no ps")

    monkeypatch.setattr(lock.subprocess, "run", broken)
    assert lock.process_start(os.getpid()) == ""


def test_busy_message_without_details():
    assert lock.busy_message(None).startswith("This swap is busy (another command).")


@pytest.mark.parametrize(
    "argv",
    [
        ["resume", "{id}", "--yes"],
        ["review", "{id}", "--approve", "--note", "x", "--fingerprint", "f"],
        ["prompt", "{id}"],
        ["repair", "{id}", "--yes"],
        ["generate", "{id}", "--draft", "--yes"],
    ],
)
def test_commands_that_change_a_job_refuse_while_it_is_busy(tmp_path, job, argv, monkeypatch):
    monkeypatch.setattr(lock, "WAIT_SECONDS", 0.1)
    settings = Settings.load(
        root=tmp_path,
        environ={"CHARSWAP_RUNS_DIR": str(job.root.parent), "REPLICATE_API_TOKEN": "t"},
    )
    out, err = [], []
    child = _child(job)
    try:
        code = cli.main(
            [part.format(id=job.id) for part in argv],
            settings=settings,
            out=out.append,
            err=err.append,
            services_factory=lambda s: pytest.fail("must not start"),
        )
    finally:
        child.kill()
        child.wait()
    assert code == 2 and "This swap is busy (generate since" in "\n".join(err)
    assert "Fix the issue" not in "\n".join(err)


def test_status_never_waits_for_the_lock(tmp_path, job):
    settings = Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(job.root.parent)})
    out = []
    with lock.job_lock(job, "generate"):
        assert cli.main(["status", job.id], settings=settings, out=out.append) == 0
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_lock.py -q --no-cov`  
Expected: FAIL. `ImportError: cannot import name 'lock' from 'character_swap'` (a collection error).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/cli.py`:

````python
import threading
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

````

with:

````python
import threading
import webbrowser
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

````

**Replace** in `src/character_swap/cli.py`:

````python
    check_photo,
)
from character_swap.jobs import SEEDANCE_PROVIDERS, WHISPER_LANGUAGES, Job, JobError
from character_swap.modes import MODES, generator_name
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
````

with:

````python
    check_photo,
)
from character_swap.jobs import SEEDANCE_PROVIDERS, WHISPER_LANGUAGES, Job, JobError
from character_swap.lock import job_lock
from character_swap.modes import MODES, generator_name
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
````

**Replace** in `src/character_swap/cli.py`:

````python
    return port


def _hint(env: Env) -> None:
    if env.job_id:
        env.err(f"Fix the issue, then run: {env.retry_command()}")
````

with:

````python
    return port


# Commands that change an existing job hold its lock for their whole run (A7). prepare takes
# it itself, once the new job exists.
LOCKED = ("resume", "review", "prompt", "repair", "generate")


def _command_lock(args, settings: Settings) -> AbstractContextManager:
    if args.command not in LOCKED:
        return nullcontext()
    return job_lock(Job.load(settings.runs_dir, args.job), args.command)


def _hint(env: Env) -> None:
    if env.job_id:
        env.err(f"Fix the issue, then run: {env.retry_command()}")
````

**Replace** in `src/character_swap/cli.py`:

````python
        piapi_factory=piapi_factory or build_piapi,
    )
    try:
        return COMMANDS[args.command](args, settings, env)
    except CharSwapError as error:
        err(f"Error: {error}")
        _hint(env)
````

with:

````python
        piapi_factory=piapi_factory or build_piapi,
    )
    try:
        with _command_lock(args, settings):  # a busy job: exit 2 before anything runs
            return COMMANDS[args.command](args, settings, env)
    except CharSwapError as error:
        err(f"Error: {error}")
        _hint(env)
````

**Replace** in `src/character_swap/cli.py`:

````python
            language=args.language,
            provider=provider,
        )
        env.track(job, "resume {id}")
        env.out(f"JOB {job.id}")
        prepare_job(job, env.services_factory(settings), status=env.out)
        if not free:
            record_spend(job, "prepare", estimate, settings.runs_dir)
        watch = "original.mp4" if job.mode == "direct" else "prepared.mp4"
        env.out(f"Next: watch {job.path(watch)} then run `charswap status {job.id}`.")
        return 0
````

with:

````python
            language=args.language,
            provider=provider,
        )
        with job_lock(job, "prepare"):  # nobody else can know the new id yet: never busy
            env.track(job, "resume {id}")
            env.out(f"JOB {job.id}")
            prepare_job(job, env.services_factory(settings), status=env.out)
            if not free:
                record_spend(job, "prepare", estimate, settings.runs_dir)
        watch = "original.mp4" if job.mode == "direct" else "prepared.mp4"
        env.out(f"Next: watch {job.path(watch)} then run `charswap status {job.id}`.")
        return 0
````

**Create** `src/character_swap/lock.py`:

````python
"""One lock per job across processes: fcntl.flock on runs/<id>/run.lock (decision A7).

Every command that changes a job takes it, and so do the app's approve and prompt save.
The OS releases a flock when its process exits or dies, so a crash never leaves a stuck
lock. run.lock itself is never deleted: deleting a held flock file would let two processes
lock two different files. The holder writes {pid, command, started_at, process_start}
into it for display and clears that again when it lets go. macOS and Linux only.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from character_swap.errors import CharSwapError
from character_swap.jobs import Job

LOCK_FILE = "run.lock"
WAIT_SECONDS = 2.0  # a brief overlap (one command ending, the next starting) is absorbed
RETRY_SECONDS = 0.05


class JobBusy(CharSwapError, RuntimeError):
    """Another command holds this job's lock. Nothing was started or charged."""


def busy_message(holder: dict[str, Any] | None) -> str:
    holder = holder or {}
    command = str(holder.get("command") or "another command")
    since = str(holder.get("started_at") or "")[:19].replace("T", " ")
    doing = f"{command} since {since} UTC" if since else command
    return f"This swap is busy ({doing}). Try again when it finishes. Nothing was charged."


def process_start(pid: int) -> str:
    """When the process started, as `ps` reports it ("" when unknown). Tells reused pids apart."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(result.stdout.split())


def read_holder(job: Job) -> dict[str, Any] | None:
    """What run.lock says about its last holder. Reads only: never takes the lock."""
    try:
        data = json.loads(job.path(LOCK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it is just not ours
    return True


def running_holder(job: Job) -> dict[str, Any] | None:
    """The holder when its process is still alive (a command started from a terminal)."""
    holder = read_holder(job)
    if holder is None or not _alive(holder["pid"]):
        return None
    saved = holder.get("process_start")
    if saved and saved != process_start(holder["pid"]):
        return None  # the pid was reused by an unrelated process
    return holder


@contextmanager
def job_lock(job: Job, command: str, *, wait: float | None = None) -> Iterator[None]:
    """Hold the job's lock for the whole block, or raise JobBusy (exit 2, nothing charged)."""
    handle = job.path(LOCK_FILE).open("a+", encoding="utf-8")  # "a+": never truncate unlocked
    try:
        deadline = time.monotonic() + (WAIT_SECONDS if wait is None else wait)
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise JobBusy(busy_message(read_holder(job))) from None
                time.sleep(RETRY_SECONDS)
        info = {
            "pid": os.getpid(),
            "command": command,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "process_start": process_start(os.getpid()),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(info))
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()  # still locked: nobody reads a finished command as running
            handle.flush()
    finally:
        handle.close()  # closing releases the flock
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_lock.py -q --no-cov`  
Expected: PASS (17 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1023 passed, 1 deselected; coverage above 96 %
git add src/character_swap/cli.py src/character_swap/lock.py tests/test_lock.py
git commit -m "feat: one cross-process lock per job (run.lock)"
````

---

### Task 7: CLI foundations: `--describe`, `--id-file`, `python -m character_swap`, the `expired` flag

Small, separate pieces the app needs from the CLI:

- `prepare --describe "…"` (A12, §4.9): cleaned by `prompts.clean_description` (no "@" or
  quotes, one line, at most 300 characters), stored as `character_description`, refused for Wan
  before any question. `build_prompt(..., description=)` adds one line,
  `The character in @Image 1 looks like this: <text>.`, with the provider's input name, before
  the transcript (which is the part truncated to fit).
- `prepare --id-file PATH` writes the new job's id (atomically) once the job exists and its
  lock is held (§4.1).
- `src/character_swap/__main__.py`, so the app can run `python -m character_swap …`.
- `pipeline._tracked` saves `expired: true` on a failed stage when an `OutputExpired` is
  anywhere in the error's `__cause__` chain (no text matching).

**Files:**
- Create: `src/character_swap/__main__.py`
- Modify: `src/character_swap/cli.py`
- Modify: `src/character_swap/jobs.py`
- Modify: `src/character_swap/pipeline.py`
- Modify: `src/character_swap/prompts.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_jobs.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Write the tests**

**Replace** in `tests/test_cli.py`:

````python
    assert "Making a 720p take as well isn't supported yet. Nothing was charged." in out.err.text


# ---- prepare: WaveSpeed is the default ----------------------------------------------------
def test_prepare_defaults_to_wavespeed_and_depth_mesh(tmp_path, prep_settings, video, monkeypatch):
    ids = _prepare_ids(monkeypatch)
````

with:

````python
    assert "Making a 720p take as well isn't supported yet. Nothing was charged." in out.err.text


# ---- prepare: --describe and --id-file (the app's foundations) ---------------------------
def test_prepare_stores_a_cleaned_description_for_the_prompt(
    tmp_path, prep_settings, video, monkeypatch
):
    ids = _prepare_ids(monkeypatch)
    code, _ = run(
        [
            "prepare",
            str(video),
            "--character",
            str(_photo(tmp_path / "me.jpg")),
            "--describe",
            'Round face, "short" @hair.',
            "--yes",
        ],
        prep_settings,
        services_factory=lambda s: object(),
    )
    job = Job.load(prep_settings.runs_dir, ids[0])
    assert code == 0 and job.state["character_description"] == "Round face, 'short' hair"
    from character_swap.pipeline import default_prompt

    assert "@Image 1 looks like this: Round face, 'short' hair." in default_prompt(job)


def test_describe_is_refused_for_wan_before_any_question(tmp_path, settings, video):
    face = tmp_path / "face.png"
    face.write_bytes(b"x")
    asked = []
    code, out = run(
        ["prepare", str(video), "--generator", "wan", "--character", str(face), "--describe", "x"],
        settings,
        confirm=asked.append,
    )
    assert code == 2 and asked == [] and "--describe is only for Seedance" in out.err.text


def test_the_id_file_is_written_once_the_job_exists_and_is_locked(
    tmp_path, prep_settings, video, monkeypatch
):
    from character_swap.lock import read_holder

    id_file = tmp_path / "incoming" / "job-id"
    id_file.parent.mkdir()
    seen = {}

    def fake_prepare(job, services, status):
        seen["id"] = id_file.read_text()
        seen["holder"] = read_holder(job)

    monkeypatch.setattr(cli, "prepare_job", fake_prepare)
    code, out = run(
        [
            "prepare",
            str(video),
            "--character",
            str(_photo(tmp_path / "me.jpg")),
            "--yes",
            "--id-file",
            str(id_file),
        ],
        prep_settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and f"JOB {seen['id']}" in out.text
    assert seen["holder"]["command"] == "prepare"  # written after the lock was taken
    assert not list(id_file.parent.glob("*.part"))


def test_the_cli_runs_as_a_module(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "character_swap", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "charswap" in result.stdout


# ---- prepare: WaveSpeed is the default ----------------------------------------------------
def test_prepare_defaults_to_wavespeed_and_depth_mesh(tmp_path, prep_settings, video, monkeypatch):
    ids = _prepare_ids(monkeypatch)
````

**Append** to `tests/test_jobs.py`:

````python


def test_a_description_is_stored_for_seedance_only(runs_dir, video, tmp_path):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth", description="Bald.")
    assert job.state["character_description"] == "Bald."
    plain = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert "character_description" not in plain.state
    face = tmp_path / "face.png"
    face.write_bytes(b"x")
    with pytest.raises(JobError, match="--describe is only for Seedance"):
        Job.create(runs_dir, video, generator="wan", mode="direct", character=face, description="x")
````

**Append** to `tests/test_pipeline.py`:

````python


def test_a_lost_result_link_is_marked_expired(make_job):
    from character_swap.providers.errors import OutputExpired, ProviderError

    job = make_job("depth")
    with pytest.raises(StageError), pipeline._tracked(job, "draft"), pipeline._wrap("Draft"):
        try:
            raise OutputExpired("link gone")
        except OutputExpired as exc:
            raise ProviderError("The draft finished but its download link expired.") from exc
    entry = job.state["stages"]["draft"]
    assert entry["status"] == "failed" and entry["expired"] is True


def test_other_failures_are_not_marked_expired(make_job):
    job = make_job("depth")
    with pytest.raises(StageError), pipeline._tracked(job, "draft"), pipeline._wrap("Draft"):
        raise RuntimeError("expired, says the text, but it is not a lost link")
    assert "expired" not in job.state["stages"]["draft"]
````

**Append** to `tests/test_prompts.py`:

````python


# ---- the person description (prepare --describe) ------------------------------------
@pytest.mark.parametrize(("provider", "name"), [("wavespeed", "@Image 1"), ("piapi", "@image1")])
def test_a_description_is_one_line_with_the_providers_image_name(provider, name):
    text = build_prompt(provider=provider, mode="depth_mesh", description="Round face, short hair.")
    assert f"The character in {name} looks like this: Round face, short hair." in text
    check_placeholders(text, provider=provider, has_secondary=False)


def test_a_description_cannot_add_input_names_or_quotes():
    text = build_prompt(provider="wavespeed", mode="depth", description='Use "@Image 2"\nnow.')
    assert "looks like this: Use 'Image 2' now." in text
    check_placeholders(text, provider="wavespeed", has_secondary=False)


def test_a_description_is_cleaned_and_trimmed_to_300_characters():
    from character_swap.prompts import MAX_DESCRIPTION, clean_description

    assert clean_description("  a  @b \n c. ") == "a b c"
    long = clean_description("word " * 200)
    assert len(long) <= MAX_DESCRIPTION == 300 and long.endswith("…")
    assert clean_description("") == "" and "looks like" not in build_prompt(
        provider="wavespeed", mode="depth", description="  "
    )


def test_the_transcript_is_truncated_before_the_description():
    words = "talk " * 2000
    text = build_prompt(
        provider="wavespeed", mode="depth", transcript=words, description="Red curly hair."
    )
    assert len(text) <= MAX_PROMPT and "looks like this: Red curly hair." in text
    assert text.index("looks like this") < text.index("The spoken words")
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_cli.py tests/test_jobs.py tests/test_pipeline.py tests/test_prompts.py -q --no-cov`  
Expected: FAIL. 11 failed, 287 passed, e.g. `TypeError: build_prompt() got an unexpected keyword argument 'description'`.

- [ ] **Step 3: Write the code**

**Create** `src/character_swap/__main__.py`:

````python
"""`python -m character_swap ...` runs the same CLI (the app starts its work this way)."""

from character_swap.cli import main

raise SystemExit(main())
````

**Replace** in `src/character_swap/cli.py`:

````python
    FaceLibrary,
    check_photo,
)
from character_swap.jobs import SEEDANCE_PROVIDERS, WHISPER_LANGUAGES, Job, JobError
from character_swap.lock import job_lock
from character_swap.modes import MODES, generator_name
from character_swap.pipeline import (
````

with:

````python
    FaceLibrary,
    check_photo,
)
from character_swap.jobs import (
    DESCRIBE_ON_WAN,
    SEEDANCE_PROVIDERS,
    WHISPER_LANGUAGES,
    Job,
    JobError,
)
from character_swap.lock import job_lock
from character_swap.modes import MODES, generator_name
from character_swap.pipeline import (
````

**Replace** in `src/character_swap/cli.py`:

````python
)
from character_swap.pipeline import generate as generate_stage
from character_swap.pipeline import prepare as prepare_job
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.piapi import unconfirmed_message as piapi_unconfirmed
````

with:

````python
)
from character_swap.pipeline import generate as generate_stage
from character_swap.pipeline import prepare as prepare_job
from character_swap.prompts import clean_description
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.piapi import unconfirmed_message as piapi_unconfirmed
````

**Replace** in `src/character_swap/cli.py`:

````python
        help="spoken language code for the transcript, e.g. en or es (default: auto, detect it)",
    )
    prep.add_argument(
        "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
    )

    sub.add_parser("status", help="show stages and review fingerprint as JSON").add_argument("job")
````

with:

````python
        help="spoken language code for the transcript, e.g. en or es (default: auto, detect it)",
    )
    prep.add_argument(
        "--describe",
        metavar="TEXT",
        help="Seedance: what the person looks like (face shape, hair; up to 300 characters); "
        "added to the prompt",
    )
    prep.add_argument(
        "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
    )
    prep.add_argument(
        "--id-file",
        type=Path,
        help="write the new job's id to this file once the job exists (used by the app)",
    )

    sub.add_parser("status", help="show stages and review fingerprint as JSON").add_argument("job")
````

**Replace** in `src/character_swap/cli.py`:

````python
        )
    if args.generator == "wan" and (args.character2 or args.face2):
        raise JobError("Wan takes one character; a second one only works with Seedance.")
    if provider == "wavespeed":
        if args.face:
            raise JobError(FACE_ON_WAVESPEED)
````

with:

````python
        )
    if args.generator == "wan" and (args.character2 or args.face2):
        raise JobError("Wan takes one character; a second one only works with Seedance.")
    if args.generator == "wan" and args.describe:
        raise JobError(DESCRIBE_ON_WAN)
    if provider == "wavespeed":
        if args.face:
            raise JobError(FACE_ON_WAVESPEED)
````

**Replace** in `src/character_swap/cli.py`:

````python
            faces=faces,
            language=args.language,
            provider=provider,
        )
        with job_lock(job, "prepare"):  # nobody else can know the new id yet: never busy
            env.track(job, "resume {id}")
            env.out(f"JOB {job.id}")
            prepare_job(job, env.services_factory(settings), status=env.out)
````

with:

````python
            faces=faces,
            language=args.language,
            provider=provider,
            description=clean_description(args.describe or ""),
        )
        with job_lock(job, "prepare"):  # nobody else can know the new id yet: never busy
            if args.id_file:  # the app waits for this: the job exists and is locked
                _write_id_file(args.id_file, job.id)
            env.track(job, "resume {id}")
            env.out(f"JOB {job.id}")
            prepare_job(job, env.services_factory(settings), status=env.out)
````

**Replace** in `src/character_swap/cli.py`:

````python
        watch = "original.mp4" if job.mode == "direct" else "prepared.mp4"
        env.out(f"Next: watch {job.path(watch)} then run `charswap status {job.id}`.")
        return 0


def _resume(args, settings, env) -> int:
````

with:

````python
        watch = "original.mp4" if job.mode == "direct" else "prepared.mp4"
        env.out(f"Next: watch {job.path(watch)} then run `charswap status {job.id}`.")
        return 0


def _write_id_file(path: Path, job_id: str) -> None:
    part = path.with_name(path.name + ".part")
    part.write_text(job_id, encoding="utf-8")
    part.replace(path)  # the reader sees the whole id or nothing


def _resume(args, settings, env) -> int:
````

**Replace** in `src/character_swap/jobs.py`:

````python
)
# fmt: on
SEEDANCE_PROVIDERS: tuple[str, ...] = ("wavespeed", "piapi")  # the first is the default
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

````

with:

````python
)
# fmt: on
SEEDANCE_PROVIDERS: tuple[str, ...] = ("wavespeed", "piapi")  # the first is the default
DESCRIBE_ON_WAN = "--describe is only for Seedance; Wan takes no text prompt."
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

````

**Replace** in `src/character_swap/jobs.py`:

````python
        faces: dict[str, str] | None = None,  # slot ("primary"/"secondary") -> saved face name
        language: str = "auto",
        provider: str = "",  # Seedance: "wavespeed" | "piapi"; "" = decided later (old jobs)
    ) -> Job:
        if generator not in MODES_BY_GENERATOR:
            choices = ", ".join(MODES_BY_GENERATOR)
````

with:

````python
        faces: dict[str, str] | None = None,  # slot ("primary"/"secondary") -> saved face name
        language: str = "auto",
        provider: str = "",  # Seedance: "wavespeed" | "piapi"; "" = decided later (old jobs)
        description: str = "",  # Seedance: what the person looks like (already cleaned)
    ) -> Job:
        if generator not in MODES_BY_GENERATOR:
            choices = ", ".join(MODES_BY_GENERATOR)
````

**Replace** in `src/character_swap/jobs.py`:

````python
            )
        if provider and generator != "seedance":
            raise JobError("--provider is only for Seedance; Wan always runs on Replicate.")
        if provider and provider not in SEEDANCE_PROVIDERS:
            raise JobError(
                f"Unknown provider {provider!r}. Use one of: {', '.join(SEEDANCE_PROVIDERS)}."
````

with:

````python
            )
        if provider and generator != "seedance":
            raise JobError("--provider is only for Seedance; Wan always runs on Replicate.")
        if description and generator != "seedance":
            raise JobError(DESCRIBE_ON_WAN)
        if provider and provider not in SEEDANCE_PROVIDERS:
            raise JobError(
                f"Unknown provider {provider!r}. Use one of: {', '.join(SEEDANCE_PROVIDERS)}."
````

**Replace** in `src/character_swap/jobs.py`:

````python
            }
            if provider:
                state["provider"] = provider
            for slot, image in (("primary", character), ("secondary", character2)):
                if image is None:
                    continue
````

with:

````python
            }
            if provider:
                state["provider"] = provider
            if description:
                state["character_description"] = description
            for slot, image in (("primary", character), ("secondary", character2)):
                if image is None:
                    continue
````

**Replace** in `src/character_swap/pipeline.py`:

````python
    generator_name,
)
from character_swap.prompts import PromptError, build_prompt, check_placeholders, validate_prompt
from character_swap.providers.generators.base import CharacterRef, GenerationRequest, Generator
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
````

with:

````python
    generator_name,
)
from character_swap.prompts import PromptError, build_prompt, check_placeholders, validate_prompt
from character_swap.providers.errors import OutputExpired
from character_swap.providers.generators.base import CharacterRef, GenerationRequest, Generator
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
````

**Replace** in `src/character_swap/pipeline.py`:

````python
        raise StageError(f"{label} failed: {exc}") from exc


@contextmanager
def _tracked(job: Job, name: str, **info: object) -> Iterator[None]:
    """Record running -> done/failed in state.json so viewers can show live progress.
````

with:

````python
        raise StageError(f"{label} failed: {exc}") from exc


def _expired(exc: BaseException | None) -> bool:
    """True when an OutputExpired is anywhere in the error's __cause__ chain.

    The generator wraps it in a ProviderError and _wrap wraps that in a StageError, so the
    app can tell "the result link expired" apart without matching any text.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, OutputExpired):
            return True
        seen.add(id(exc))
        exc = exc.__cause__
    return False


@contextmanager
def _tracked(job: Job, name: str, **info: object) -> Iterator[None]:
    """Record running -> done/failed in state.json so viewers can show live progress.
````

**Replace** in `src/character_swap/pipeline.py`:

````python
        error = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc) or type(exc).__name__
        )
        try:
            job.mark_stage(name, "failed", started_at=started_at, error=error, **info)
        except Exception as mark_exc:
            # Best-effort: the primary error matters more than recording it, so never let a
            # failing state write (disk full, ...) replace the exception being raised.
````

with:

````python
        error = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc) or type(exc).__name__
        )
        expired = {"expired": True} if _expired(exc) else {}
        try:
            job.mark_stage(name, "failed", started_at=started_at, error=error, **expired, **info)
        except Exception as mark_exc:
            # Best-effort: the primary error matters more than recording it, so never let a
            # failing state write (disk full, ...) replace the exception being raised.
````

**Replace** in `src/character_swap/pipeline.py`:

````python
        scene=state.get("scene"),
        transcript=state.get("transcript"),
        has_secondary=bool(state.get("character_secondary")),
    )


````

with:

````python
        scene=state.get("scene"),
        transcript=state.get("transcript"),
        has_secondary=bool(state.get("character_secondary")),
        description=state.get("character_description"),
    )


````

**Replace** in `src/character_swap/prompts.py`:

````python

MAX_PROMPT = 4000
MAX_SCENE = 300

# How each provider names the inputs inside the prompt. The text sent must use these exactly.
NAMES: dict[str, dict[str, str]] = {
````

with:

````python

MAX_PROMPT = 4000
MAX_SCENE = 300
MAX_DESCRIPTION = 300  # prepare --describe / the app's "Describe the person"

# How each provider names the inputs inside the prompt. The text sent must use these exactly.
NAMES: dict[str, dict[str, str]] = {
````

**Replace** in `src/character_swap/prompts.py`:

````python
}
ROLES = {"video": "the clip", "image1": "your character", "image2": "the second character"}
SECONDARY = "{image2} is a second character; use them for the second person in the scene."


class PromptError(CharSwapError, ValueError):
````

with:

````python
}
ROLES = {"video": "the clip", "image1": "your character", "image2": "the second character"}
SECONDARY = "{image2} is a second character; use them for the second person in the scene."
DESCRIPTION = "The character in {image1} looks like this: {text}."


class PromptError(CharSwapError, ValueError):
````

**Replace** in `src/character_swap/prompts.py`:

````python
    if " " in cut and text[limit - 1] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def validate_prompt(text: str) -> str:
````

with:

````python
    if " " in cut and text[limit - 1] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def clean_description(text: str) -> str:
    """A person description, safe to put in the prompt: no "@" or quotes, one line, 300 max."""
    return _truncate(_clean(text).rstrip(" ."), MAX_DESCRIPTION)


def validate_prompt(text: str) -> str:
````

**Replace** in `src/character_swap/prompts.py`:

````python
    scene: str | None = None,
    transcript: str | None = None,
    has_secondary: bool = False,
) -> str:
    names = _names(provider)
    parts = [BASE.format(**names), MODE_NOTES[mode].format(**names)]
    if has_secondary:
        parts.append(SECONDARY.format(**names))
    scene = _clean(scene or "")
    if scene:
        parts.append(f"Setting: {_truncate(scene, MAX_SCENE)}")
````

with:

````python
    scene: str | None = None,
    transcript: str | None = None,
    has_secondary: bool = False,
    description: str | None = None,
) -> str:
    names = _names(provider)
    parts = [BASE.format(**names), MODE_NOTES[mode].format(**names)]
    if has_secondary:
        parts.append(SECONDARY.format(**names))
    looks = clean_description(description or "")
    if looks:  # before the transcript, which is the part truncated to fit
        parts.append(DESCRIPTION.format(image1=names["image1"], text=looks))
    scene = _clean(scene or "")
    if scene:
        parts.append(f"Setting: {_truncate(scene, MAX_SCENE)}")
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_cli.py tests/test_jobs.py tests/test_pipeline.py tests/test_prompts.py -q --no-cov`  
Expected: PASS (298 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1035 passed, 1 deselected; coverage above 96 %
git add src/character_swap/__main__.py src/character_swap/cli.py src/character_swap/jobs.py src/character_swap/pipeline.py src/character_swap/prompts.py tests/test_cli.py tests/test_jobs.py tests/test_pipeline.py tests/test_prompts.py
git commit -m "feat: prepare --describe and --id-file; python -m character_swap; expired results are flagged"
````

---

### Task 8: `--confirmed-usd`, `planned_charge` and `generate_refusal`

A6, §4.3: the page's price is only a display; the command has the last word.

- `pipeline.planned_charge(job, command, resolution="", *, services=None)` says what
  `resume`, `repair`, `draft` or `final` would charge now (`None`: nothing new). For a draft or
  final it uses `planned_generation`, which builds the request exactly as `generate` will and
  prices it with the job's generator (the CLI passes its services; the app prices with the
  generator class, no key needed).
- `pipeline.generate_refusal(job, stage, resolution="")` returns the CLI's free refusals in the
  CLI's order: a final still in progress, a stale final, another final, then (unless the step
  is simply already done) the approval/mode/draft-first/soundtrack checks, the final's
  resolution, an unconfirmed marker (`UNCONFIRMED` moves here from `cli.py`), and the prompt's
  input names. `cli._generate` now calls it first and prints its message on stderr (exit 2).
- `--confirmed-usd USD` on `prepare`, `resume`, `repair` and `generate` replaces the cost
  question (`cli._agree_to`): compared in whole micro-dollars (`costs.micro_usd`); a different
  amount, or any charge when it is 0, exits 2 with "The price is now about $Y, not $X. Nothing
  was charged." No charge needed (a resume) runs whatever X is.

Money-safety: the order of the refusals and the remake/archive steps in `_generate` is
unchanged; only where they live moved.

**Files:**
- Modify: `src/character_swap/cli.py`
- Modify: `src/character_swap/costs.py`
- Modify: `src/character_swap/pipeline.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the tests**

Three existing tests change: the unconfirmed-marker intro is now on stderr, and the services
are never built when the command refuses (so `.last` is replaced by a factory that fails).

**Replace** in `tests/test_cli.py`:

````python
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 2 and asked == [] and seen == [] and "no new charge" not in out.text
    assert (
        "A request for this step was sent but never confirmed. It may have been charged; "
        "follow the steps below before running again."
    ) in out.text
    assert "replicate.com/predictions" in out.err.text and str(record) in out.err.text
    assert "draft" not in job.state["stages"]  # not marked failed


def test_generate_unconfirmed_marker_end_to_end_makes_no_request(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    with respx.mock(assert_all_called=False):  # no routes: any request would fail the test
        code, out = run(["generate", job.id, "--draft"], settings, confirm=asked.append)
    assert code == 2 and asked == []
````

with:

````python
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 2 and asked == [] and seen == [] and "no new charge" not in out.text
    assert (
        "A request for this step was sent but never confirmed. It may have been charged; "
        "follow the steps below before running again."
    ) in out.err.text
    assert "replicate.com/predictions" in out.err.text and str(record) in out.err.text
    assert "draft" not in job.state["stages"]  # not marked failed


def test_generate_unconfirmed_marker_end_to_end_makes_no_request(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    with respx.mock(assert_all_called=False):  # no routes: any request would fail the test
        code, out = run(["generate", job.id, "--draft"], settings, confirm=asked.append)
    assert code == 2 and asked == []
````

**Replace** in `tests/test_cli.py`:

````python
    assert code == 2 and "PIAPI_API_KEY" in out.err.text


def test_seedance_unconfirmed_marker_points_at_piapi(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and seedance_services.last.bodies == []
    assert "PiAPI" in out.err.text and "replicate.com" not in out.err.text
    assert str(record) in out.err.text


def test_resolution_only_with_final(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft", "--resolution", "720p"],
        piapi_settings,
        confirm=asked.append,
        services_factory=lambda s: pytest.fail("no services before the refusal"),
````

with:

````python
    assert code == 2 and "PIAPI_API_KEY" in out.err.text


def test_seedance_unconfirmed_marker_points_at_piapi(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=lambda s: pytest.fail("refused before any service is built"),
    )
    assert code == 2 and asked == []
    assert "PiAPI" in out.err.text and "replicate.com" not in out.err.text
    assert str(record) in out.err.text


def test_resolution_only_with_final(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft", "--resolution", "720p"],
        piapi_settings,
        confirm=asked.append,
        services_factory=lambda s: pytest.fail("no services before the refusal"),
````

**Replace** in `tests/test_cli.py`:

````python
    assert code == 2 and "WAVESPEED_API_KEY" in out.err.text


def test_a_wavespeed_unconfirmed_marker_points_at_wavespeed(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 2 and asked == [] and wavespeed_services.last.bodies == []
    assert "WaveSpeed dashboard" in out.err.text and "PiAPI" not in out.err.text
    assert str(record) in out.err.text


def test_an_interrupted_wavespeed_draft_resumes_on_wavespeed(
    ws_settings, video, tmp_path, monkeypatch
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path, provider="")  # an old job

    def stopped(self, url, dest):
        raise KeyboardInterrupt

````

with:

````python
    assert code == 2 and "WAVESPEED_API_KEY" in out.err.text


def test_a_wavespeed_unconfirmed_marker_points_at_wavespeed(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=lambda s: pytest.fail("refused before any service is built"),
    )
    assert code == 2 and asked == []
    assert "WaveSpeed dashboard" in out.err.text and "PiAPI" not in out.err.text
    assert str(record) in out.err.text


def test_an_interrupted_wavespeed_draft_resumes_on_wavespeed(
    ws_settings, video, tmp_path, monkeypatch
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path, provider="")  # an old job

    def stopped(self, url, dest):
        raise KeyboardInterrupt

````

**Replace** in `tests/test_cli.py`:

````python
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p", "status": status}))
    job.mark_stage("draft", "failed", fingerprint=review_fingerprint(job))  # e.g. Ctrl-C
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text, out.err.text
    assert wavespeed_services.last.bodies == [] and job.stage_done("draft")
````

with:

````python
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p", "status": status}))
    job.mark_stage("draft", "failed", fingerprint=review_fingerprint(job))  # e.g. Ctrl-C
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text, out.err.text
    assert wavespeed_services.last.bodies == [] and job.stage_done("draft")


# ---- --confirmed-usd, planned_charge and generate_refusal (the app's money checks) --------
NO_QUESTION = {"confirm": lambda q: pytest.fail(f"asked {q!r}")}


def test_a_confirmed_draft_price_runs_without_asking(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    code, out = run(
        ["generate", job.id, "--draft", "--confirmed-usd", "0.88"],
        ws_settings,
        services_factory=wavespeed_services,
        **NO_QUESTION,
    )
    assert code == 0 and job.stage_done("draft") and len(wavespeed_services.last.bodies) == 1


@pytest.mark.parametrize(("confirmed", "shown"), [("0.5", "$0.50"), ("0", "$0.00")])
def test_a_different_price_is_refused_before_anything(
    ws_settings, video, tmp_path, confirmed, shown
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    code, out = run(
        ["generate", job.id, "--draft", "--confirmed-usd", confirmed],
        ws_settings,
        services_factory=wavespeed_services,
        **NO_QUESTION,
    )
    assert code == 2 and wavespeed_services.last.bodies == []
    assert f"The price is now about $0.88, not {shown}. Nothing was charged." in out.err.text
    assert "draft" not in job.state["stages"]


def test_prices_are_compared_in_whole_micro_dollars(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    code, _ = run(
        ["generate", job.id, "--draft", "--confirmed-usd", "0.880001"],
        ws_settings,
        services_factory=wavespeed_services,
    )
    assert code == 2 and wavespeed_services.last.bodies == []
    code, _ = run(
        ["generate", job.id, "--draft", "--confirmed-usd", "0.8800004"],
        ws_settings,
        services_factory=wavespeed_services,
    )
    assert code == 0 and len(wavespeed_services.last.bodies) == 1


def test_zero_still_lets_a_saved_request_resume_for_free(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p1", "status": "processing"}))
    _interrupted_draft(job)
    code, out = run(
        ["generate", job.id, "--draft", "--confirmed-usd", "0"],
        ws_settings,
        services_factory=wavespeed_services,
        **NO_QUESTION,
    )
    assert code == 0 and "no new charge" in out.text and wavespeed_services.last.bodies == []


def test_prepare_checks_the_confirmed_price_before_creating_the_job(
    tmp_path, prep_settings, video, monkeypatch
):
    ids = _prepare_ids(monkeypatch)
    photo = str(_photo(tmp_path / "me.jpg"))
    argv = ["prepare", str(video), "--character", photo, "--yes", "--confirmed-usd"]
    code, out = run([*argv, "0.05"], prep_settings, services_factory=lambda s: object())
    assert code == 2 and "The price is now about $0.06, not $0.05" in out.err.text
    assert ids == [] and not any(prep_settings.runs_dir.glob("*/state.json"))
    code, _ = run([*argv, "0.0608"], prep_settings, services_factory=lambda s: object())
    assert code == 0 and len(ids) == 1


def test_resume_and_repair_check_their_confirmed_price(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    monkeypatch.setattr(cli, "prepare_job", lambda j, s, status: None)
    monkeypatch.setattr(cli, "repair_with_rgb", lambda j, s, status: None)
    factory = {"services_factory": lambda s: object(), **NO_QUESTION}
    code, out = run(["resume", job.id, "--confirmed-usd", "0"], settings, **factory)
    assert code == 2 and "not $0.00" in out.err.text  # every paid prep step is still to do
    code, _ = run(["resume", job.id, "--confirmed-usd", "0.0608"], settings, **factory)
    assert code == 0
    _finish_stages(job)
    code, _ = run(["resume", job.id, "--confirmed-usd", "0"], settings, **factory)
    assert code == 0  # nothing left to buy: no new charge, whatever was confirmed
    code, out = run(["repair", job.id, "--confirmed-usd", "0"], settings, **factory)
    assert code == 2 and "The price is now about $0.002, not $0.00" in out.err.text
    assert run(["repair", job.id, "--confirmed-usd", "0.002"], settings, **factory)[0] == 0


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "cheap"])
def test_a_bad_confirmed_price_is_refused(settings, value):
    with pytest.raises(SystemExit):
        run(["generate", "0" * 32, "--draft", "--confirmed-usd", value], settings)


def test_planned_charge_gives_the_prices_the_cli_shows(ws_settings, video, tmp_path):
    from character_swap.pipeline import planned_charge

    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    assert planned_charge(job, "draft").total == pytest.approx(0.88)
    assert planned_charge(job, "resume") is None  # prepared: nothing left to buy
    assert planned_charge(job, "repair").total == pytest.approx(0.002)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    assert planned_charge(job, "draft") is None  # done
    assert planned_charge(job, "final").total == pytest.approx(1.76)
    assert planned_charge(job, "final", "1080p").total == pytest.approx(4.40)
    job.path("prompt.txt").write_text("Another @Image 1 in @Video 1.")
    assert planned_charge(job, "draft").total == pytest.approx(0.88)  # a remake
    with pytest.raises(ValueError, match="Unknown command"):
        planned_charge(job, "upscale")


def test_generate_refusal_says_what_the_cli_says_and_never_writes(ws_settings, video, tmp_path):
    from character_swap.pipeline import generate_refusal

    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    before = job.path("state.json").read_bytes()
    assert generate_refusal(job, "draft") is None
    assert "Make and watch the draft first" in generate_refusal(job, "final")
    job.path("prompt.txt").write_text("Put @image1 into @Video 1.")
    assert "@image1, PiAPI's name" in generate_refusal(job, "draft")
    job.path("prompt.txt").unlink()
    job.path("draft-prediction.json").write_text(json.dumps({"status": "creating"}))
    refusal = generate_refusal(job, "draft")
    assert refusal.startswith("A request for this step was sent but never confirmed")
    assert "WaveSpeed dashboard" in refusal
    job.path("draft-prediction.json").unlink()
    job.path("review.json").unlink()
    assert "Review required" in generate_refusal(job, "draft")
    assert job.path("state.json").read_bytes() == before


def test_generate_refusal_for_finals(ws_settings, video, tmp_path):
    from character_swap.pipeline import generate_refusal

    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    run(["generate", job.id, "--final", "--yes"], ws_settings, services_factory=wavespeed_services)
    assert generate_refusal(job, "final") is None  # done: "already done", not a refusal
    assert "isn't supported yet" in generate_refusal(job, "final", "1080p")
    job.mark_stage("final", fingerprint="older-inputs", resolution="720p")
    assert "no longer approved" in generate_refusal(job, "final")
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_cli.py -q --no-cov`  
Expected: FAIL. 13 failed, 192 passed: `--confirmed-usd` is unknown and `planned_charge` / `generate_refusal` cannot be imported.

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/cli.py`:

````python

import argparse
import json
import shutil
import socket
import sys
````

with:

````python

import argparse
import json
import math
import shutil
import socket
import sys
````

**Replace** in `src/character_swap/cli.py`:

````python

from character_swap.config import Settings
from character_swap.costs import (
    estimate_prepare,
    estimate_repair,
    estimate_stages,
    record_spend,
)
from character_swap.errors import CharSwapError
````

with:

````python

from character_swap.config import Settings
from character_swap.costs import (
    Estimate,
    estimate_prepare,
    estimate_repair,
    micro_usd,
    price_changed,
    record_spend,
)
from character_swap.errors import CharSwapError
````

**Replace** in `src/character_swap/cli.py`:

````python
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
    GENERATOR_CLASSES,
    Services,
    already_paid,
    archive_stale_draft,
    build_request,
    check_can_generate,
    draft_prompt_changed,
    final_resolution,
    generation_files,
    generation_label,
    generation_prompt,
````

with:

````python
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
    GENERATOR_CLASSES,
    PREP_ESTIMATE_SECONDS,
    Services,
    already_paid,
    archive_stale_draft,
    build_request,
    draft_prompt_changed,
    final_resolution,
    generate_refusal,
    generation_files,
    generation_label,
    generation_prompt,
````

**Replace** in `src/character_swap/cli.py`:

````python
    generator_key,
    generator_promotes,
    is_promote,
    needs_new_charge,
    next_step,
    pin_provider,
    refuse_another_final,
    remaining_paid_stages,
    rename_old_prompt_names,
    repair_with_rgb,
    resumes_without_charge,
    saved_request_unconfirmed,
    stale_draft,
    stale_generation,
    write_prompt_file,
)
from character_swap.pipeline import generate as generate_stage
````

with:

````python
    generator_key,
    generator_promotes,
    is_promote,
    next_step,
    pin_provider,
    planned_charge,
    planned_generation,
    remaining_paid_stages,
    remake_reasons,
    rename_old_prompt_names,
    repair_with_rgb,
    resumes_without_charge,
    stale_draft,
    write_prompt_file,
)
from character_swap.pipeline import generate as generate_stage
````

**Replace** in `src/character_swap/cli.py`:

````python
from character_swap.prompts import clean_description
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.piapi import unconfirmed_message as piapi_unconfirmed
from character_swap.providers.replicate import unconfirmed_message as replicate_unconfirmed
from character_swap.providers.wavespeed import unconfirmed_message as wavespeed_unconfirmed
from character_swap.review import decide
from character_swap.review import status as review_status
from character_swap.stages import StageError
````

with:

````python
from character_swap.prompts import clean_description
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.review import decide
from character_swap.review import status as review_status
from character_swap.stages import StageError
````

**Replace** in `src/character_swap/cli.py`:

````python

VIEW_HOST = "127.0.0.1"  # the app is never reachable from the network
BROWSER_DELAY = 1.0  # seconds; lets the server start listening before the browser asks
# Keyed by pipeline.generator_key(job): each generator's paid key and unconfirmed-marker help.
KEYS = {
    "wan": "REPLICATE_API_TOKEN",
    "seedance:wavespeed": "WAVESPEED_API_KEY",
    "seedance:piapi": "PIAPI_API_KEY",
}
UNCONFIRMED = {
    "wan": replicate_unconfirmed,
    "seedance:wavespeed": wavespeed_unconfirmed,
    "seedance:piapi": piapi_unconfirmed,
}
RENAMED = "Updated prompt.txt to WaveSpeed's input names."

````

with:

````python

VIEW_HOST = "127.0.0.1"  # the app is never reachable from the network
BROWSER_DELAY = 1.0  # seconds; lets the server start listening before the browser asks
# Keyed by pipeline.generator_key(job): each generator's paid key. (The unconfirmed-marker
# help is pipeline.UNCONFIRMED, used by pipeline.generate_refusal.)
KEYS = {
    "wan": "REPLICATE_API_TOKEN",
    "seedance:wavespeed": "WAVESPEED_API_KEY",
    "seedance:piapi": "PIAPI_API_KEY",
}
RENAMED = "Updated prompt.txt to WaveSpeed's input names."

````

**Replace** in `src/character_swap/cli.py`:

````python
    prep.add_argument(
        "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
    )
    prep.add_argument(
        "--id-file",
        type=Path,
````

with:

````python
    prep.add_argument(
        "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
    )
    _confirmed_usd(prep)
    prep.add_argument(
        "--id-file",
        type=Path,
````

**Replace** in `src/character_swap/cli.py`:

````python
    )
    resume.add_argument("job")
    resume.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    review = sub.add_parser("review", help="approve or reject the prepared clip")
    review.add_argument("job")
    decision = review.add_mutually_exclusive_group(required=True)
````

with:

````python
    )
    resume.add_argument("job")
    resume.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    _confirmed_usd(resume)
    review = sub.add_parser("review", help="approve or reject the prepared clip")
    review.add_argument("job")
    decision = review.add_mutually_exclusive_group(required=True)
````

**Replace** in `src/character_swap/cli.py`:

````python
    repair = sub.add_parser("repair", help="re-cut the subject using original colors")
    repair.add_argument("job")
    repair.add_argument("--yes", action="store_true")
    gen = sub.add_parser("generate", help="make the 480p draft or the final (paid; asks first)")
    gen.add_argument("job")
    which = gen.add_mutually_exclusive_group(required=True)
````

with:

````python
    repair = sub.add_parser("repair", help="re-cut the subject using original colors")
    repair.add_argument("job")
    repair.add_argument("--yes", action="store_true")
    _confirmed_usd(repair)
    gen = sub.add_parser("generate", help="make the 480p draft or the final (paid; asks first)")
    gen.add_argument("job")
    which = gen.add_mutually_exclusive_group(required=True)
````

**Replace** in `src/character_swap/cli.py`:

````python
        "(default, upgrades the draft) or 720p (cheaper fresh run)",
    )
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    sub.add_parser("doctor", help="check this machine's setup")
    face = sub.add_parser("face", help="save and check faces in your PiAPI face library (free)")
    face_sub = face.add_subparsers(dest="face_command", required=True)
````

with:

````python
        "(default, upgrades the draft) or 720p (cheaper fresh run)",
    )
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    _confirmed_usd(gen)
    sub.add_parser("doctor", help="check this machine's setup")
    face = sub.add_parser("face", help="save and check faces in your PiAPI face library (free)")
    face_sub = face.add_subparsers(dest="face_command", required=True)
````

**Replace** in `src/character_swap/cli.py`:

````python
    return parser


def _port(text: str) -> int:
    port = int(text)
    if not 1 <= port <= 65535:
````

with:

````python
    return parser


def _confirmed_usd(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--confirmed-usd",
        type=_usd,
        metavar="USD",
        help="the price you agreed to (instead of the question): refuse unless what this "
        "would charge now is exactly that; 0 = no new charge allowed",
    )


def _usd(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("give the price in dollars, e.g. 0.88") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("the price must be 0 or more")
    return value


def _port(text: str) -> int:
    port = int(text)
    if not 1 <= port <= 65535:
````

**Replace** in `src/character_swap/cli.py`:

````python
    if args.command not in LOCKED:
        return nullcontext()
    return job_lock(Job.load(settings.runs_dir, args.job), args.command)


def _hint(env: Env) -> None:
````

with:

````python
    if args.command not in LOCKED:
        return nullcontext()
    return job_lock(Job.load(settings.runs_dir, args.job), args.command)


def _agree_to(args, env: Env, estimate: Estimate | None) -> int | None:
    """None = go ahead with the charge; otherwise the exit code.

    --confirmed-usd replaces the cost question (A6): the page's price is only a display, so
    this command refuses unless what it is about to charge is exactly what was confirmed.
    """
    if estimate is None:
        return None  # nothing new is charged (a resume), whatever was confirmed
    confirmed = getattr(args, "confirmed_usd", None)
    if confirmed is not None:
        if micro_usd(estimate.total) == micro_usd(confirmed):
            return None
        env.err(price_changed(estimate.total, confirmed))
        return 2
    if args.yes or env.proceed():
        return None
    return 1


def _hint(env: Env) -> None:
````

**Replace** in `src/character_swap/cli.py`:

````python
            env.out(PHOTO_CONSENT)
            if not args.yes and not env.proceed(RIGHTS_QUESTION):
                return 1
        estimate = estimate_prepare(mode, seconds=30)
        free = estimate.total == 0
        if free:
            env.out("Preparing is free for this mode (nothing is sent to a paid service).")
        else:
            env.out("Preparation cost (up to a 30 s clip):\n" + estimate.describe())
            if not args.yes and not env.proceed():
                return 1
        job = Job.create(
            settings.runs_dir,
            args.video,
````

with:

````python
            env.out(PHOTO_CONSENT)
            if not args.yes and not env.proceed(RIGHTS_QUESTION):
                return 1
        estimate = estimate_prepare(mode, seconds=PREP_ESTIMATE_SECONDS)
        free = estimate.total == 0
        if free:
            env.out("Preparing is free for this mode (nothing is sent to a paid service).")
        else:
            env.out("Preparation cost (up to a 30 s clip):\n" + estimate.describe())
            if (code := _agree_to(args, env, estimate)) is not None:
                return code
        job = Job.create(
            settings.runs_dir,
            args.video,
````

**Replace** in `src/character_swap/cli.py`:

````python
    env.track(job, "resume {id}")
    if job.mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    remaining = remaining_paid_stages(job)
    if remaining:
        total = estimate_stages(remaining, seconds=30).total
        env.out(f"Remaining paid steps: {', '.join(remaining)} (up to ${total:.4f})")
        if not args.yes and not env.proceed():
            return 1
    env.out(f"Resuming {job.id}; finished steps are skipped and saved predictions are re-checked.")
    prepare_job(job, env.services_factory(settings), status=env.out)
    if not any(c["step"] == "prepare" for c in job.state.get("costs", [])):
        record_spend(job, "prepare", estimate_prepare(job.mode, seconds=30), settings.runs_dir)
    return 0


````

with:

````python
    env.track(job, "resume {id}")
    if job.mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    charge = planned_charge(job, "resume")
    if charge is not None:
        remaining = ", ".join(remaining_paid_stages(job))
        env.out(f"Remaining paid steps: {remaining} (up to ${charge.total:.4f})")
        if (code := _agree_to(args, env, charge)) is not None:
            return code
    env.out(f"Resuming {job.id}; finished steps are skipped and saved predictions are re-checked.")
    prepare_job(job, env.services_factory(settings), status=env.out)
    if not any(c["step"] == "prepare" for c in job.state.get("costs", [])):
        estimate = estimate_prepare(job.mode, seconds=PREP_ESTIMATE_SECONDS)
        record_spend(job, "prepare", estimate, settings.runs_dir)
    return 0


````

**Replace** in `src/character_swap/cli.py`:

````python
    if job.mode not in ("depth", "depth_mesh"):
        raise StageError("This mode has no SAM mask to repair. Prepare the face tracking again.")
    settings.require("REPLICATE_API_TOKEN")
    env.out("Repair re-runs SAM 3:\n" + estimate_repair().describe())
    if not args.yes and not env.proceed():
        return 1
    repair_with_rgb(job, env.services_factory(settings), status=env.out)
    record_spend(job, "repair", estimate_repair(), settings.runs_dir)
    return 0
````

with:

````python
    if job.mode not in ("depth", "depth_mesh"):
        raise StageError("This mode has no SAM mask to repair. Prepare the face tracking again.")
    settings.require("REPLICATE_API_TOKEN")
    charge = planned_charge(job, "repair")
    env.out("Repair re-runs SAM 3:\n" + charge.describe())
    if (code := _agree_to(args, env, charge)) is not None:
        return code
    repair_with_rgb(job, env.services_factory(settings), status=env.out)
    record_spend(job, "repair", estimate_repair(), settings.runs_dir)
    return 0
````

**Replace** in `src/character_swap/cli.py`:

````python
    key = pin_provider(job)  # before any paid create: see pipeline.pin_provider
    if rename_old_prompt_names(job):  # free, before draft_prompt_changed and any question
        env.out(RENAMED)
    inputs_changed = stage == "draft" and stale_draft(job)  # approved inputs changed
    # Seedance: a finished draft whose prompt.txt was edited since (the prompt is deliberately
    # not part of the review, so editing it never clears the approval).
    prompt_changed = stage == "draft" and not inputs_changed and draft_prompt_changed(job)
    remake = inputs_changed or prompt_changed
    if prompt_changed and already_paid(job, "final") and not job.stage_done("final"):
        # A remake archives the final too; a paid final still running (or unconfirmed)
        # would be orphaned and bought again. Refuse before any question.
        env.err(
            "A final is still in progress for this job. Pick it up first with "
            f"`charswap generate {job.id} --final` (no new charge), then make a new draft. "
            "Nothing was charged."
        )
        return 2
    if stage == "final" and stale_generation(job, "final"):
        env.err(
            "This final was made from inputs that are no longer approved; run "
            f"`charswap generate {job.id} --draft` to make a new draft first."
        )
        return 2
    refuse_another_final(job, stage, args.resolution or "")  # before "already done"
    if job.stage_done(stage) and not remake:
        env.out(f"The {stage} is already done: {job.path(f'{stage}.mp4')}")
        return 0
    check_can_generate(job, stage)  # mode, approved review, draft first: before any cost
    resolution = final_resolution(job, args.resolution or "") if stage == "final" else ""
    env.track(job, f"generate {{id}} --{stage}")  # after the refusals: their text says what to do
    settings.require(KEYS[key])
    services = env.services_factory(settings)
    if saved_request_unconfirmed(job, stage):
        env.out(
            "A request for this step was sent but never confirmed. It may have been charged; "
            "follow the steps below before running again."
        )
        env.err(UNCONFIRMED[key](job.path(generation_files(stage)[0])))
        return 2
    if remake or needs_new_charge(job, stage):
        # Built exactly as pipeline.generate will build it (a remake: once the old draft is
        # archived), so the prompt and price shown are what is sent and charged.
        request = build_request(
            job,
            faces=services.faces,
            stage=stage,
            final_resolution=resolution,
            replacing_stale=remake,
        )
        if is_promote(job, stage, resolution):
            SeedanceGenerator.promote_body(request.draft_task_id)  # refuses with no task id
        estimate = generator_for(job, services).estimate(request, stage)
        if stage == "draft" and request.prompt:
            env.out(
                f"Prompt sent to Seedance (change it with `charswap prompt {job.id}`):\n"
````

with:

````python
    key = pin_provider(job)  # before any paid create: see pipeline.pin_provider
    if rename_old_prompt_names(job):  # free, before draft_prompt_changed and any question
        env.out(RENAMED)
    refusal = generate_refusal(job, stage, args.resolution or "")  # before any question
    if refusal:
        env.err(refusal)
        return 2
    inputs_changed, prompt_changed = remake_reasons(job, stage)
    remake = inputs_changed or prompt_changed
    if job.stage_done(stage) and not remake:
        env.out(f"The {stage} is already done: {job.path(f'{stage}.mp4')}")
        return 0
    resolution = final_resolution(job, args.resolution or "") if stage == "final" else ""
    env.track(job, f"generate {{id}} --{stage}")  # after the refusals: their text says what to do
    settings.require(KEYS[key])
    services = env.services_factory(settings)
    planned = planned_generation(job, stage, resolution, services=services)
    if planned is not None:
        request, estimate = planned
        if stage == "draft" and request.prompt:
            env.out(
                f"Prompt sent to Seedance (change it with `charswap prompt {job.id}`):\n"
````

**Replace** in `src/character_swap/cli.py`:

````python
            and not is_promote(job, stage, resolution)
        ):
            env.out(_fresh_final_note(job))
        if not args.yes and not env.proceed():
            return 1
        if remake:
            archive_stale_draft(job)
    elif resumes_without_charge(job, stage):
````

with:

````python
            and not is_promote(job, stage, resolution)
        ):
            env.out(_fresh_final_note(job))
        if (code := _agree_to(args, env, estimate)) is not None:
            return code
        if remake:
            archive_stale_draft(job)
    elif resumes_without_charge(job, stage):
````

**Replace** in `src/character_swap/costs.py`:

````python
    return f"${usd:.2f}" if usd >= 0.01 or usd == 0 else f"${usd:.3f}"


def cost_line(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
````

with:

````python
    return f"${usd:.2f}" if usd >= 0.01 or usd == 0 else f"${usd:.3f}"


def micro_usd(usd: float) -> int:
    """Whole micro-dollars: how a confirmed price is compared with a charge (no float drift)."""
    return round(usd * 1_000_000)


def price_changed(now: float, confirmed: float) -> str:
    return (
        f"The price is now about {usd_text(now)}, not {usd_text(confirmed)}. Nothing was charged."
    )


def cost_line(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
````

**Replace** in `src/character_swap/pipeline.py`:

````python

import av

from character_swap.costs import record_spend
from character_swap.faces import FaceError
from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError
from character_swap.media.audio import (
````

with:

````python

import av

from character_swap.costs import Estimate, estimate_repair, estimate_stages, record_spend
from character_swap.errors import CharSwapError
from character_swap.faces import FaceError
from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError
from character_swap.media.audio import (
````

**Replace** in `src/character_swap/pipeline.py`:

````python
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.generators.wan_replicate import WanGenerator
from character_swap.review import ReviewError, fingerprint, require_approved
from character_swap.review import clear as clear_review
from character_swap.stages import StageError
````

with:

````python
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.generators.wan_replicate import WanGenerator
from character_swap.providers.piapi import unconfirmed_message as piapi_unconfirmed
from character_swap.providers.replicate import unconfirmed_message as replicate_unconfirmed
from character_swap.providers.wavespeed import unconfirmed_message as wavespeed_unconfirmed
from character_swap.review import ReviewError, fingerprint, require_approved
from character_swap.review import clear as clear_review
from character_swap.stages import StageError
````

**Replace** in `src/character_swap/pipeline.py`:

````python
        raise


def next_step(job: Job, *, approved: bool) -> str:
    """The one thing to do next (shown by `charswap status`)."""
    mode = MODES[job.mode]
````

with:

````python
        raise


# ---- what a command would refuse or charge right now (shared by the CLI and the app) -----
PREP_ESTIMATE_SECONDS = 30  # per-run prep prices ignore length: "up to a 30 s clip"
# Keyed by generator_key(job): help when a record holds only the "creating" marker.
UNCONFIRMED = {
    "wan": replicate_unconfirmed,
    "seedance:wavespeed": wavespeed_unconfirmed,
    "seedance:piapi": piapi_unconfirmed,
}
UNCONFIRMED_INTRO = (
    "A request for this step was sent but never confirmed. It may have been charged; "
    "follow the steps below before running again."
)
FINAL_IN_PROGRESS = (
    "A final is still in progress for this job. Pick it up first with "
    "`charswap generate {id} --final` (no new charge), then make a new draft. "
    "Nothing was charged."
)
STALE_FINAL = (
    "This final was made from inputs that are no longer approved; run "
    "`charswap generate {id} --draft` to make a new draft first."
)


def remake_reasons(job: Job, stage: str) -> tuple[bool, bool]:
    """(the approved inputs changed, prompt.txt changed) since a draft: a new draft is wanted."""
    inputs_changed = stage == "draft" and stale_draft(job)
    # Seedance: a finished draft whose prompt.txt was edited since (the prompt is deliberately
    # not part of the review, so editing it never clears the approval).
    prompt_changed = stage == "draft" and not inputs_changed and draft_prompt_changed(job)
    return inputs_changed, prompt_changed


def generate_refusal(job: Job, stage: str, resolution: str = "") -> str | None:
    """Why `charswap generate` refuses now, before any cost question; None = it may go on.

    Free and read-only: the app runs it before starting a command (which checks again),
    and the CLI runs it first. "Already done" is not a refusal (it charges nothing).
    """
    inputs_changed, prompt_changed = remake_reasons(job, stage)
    remake = inputs_changed or prompt_changed
    if prompt_changed and already_paid(job, "final") and not job.stage_done("final"):
        # A remake archives the final too; a paid final still running (or unconfirmed)
        # would be orphaned and bought again.
        return FINAL_IN_PROGRESS.format(id=job.id)
    if stage == "final" and stale_generation(job, "final"):
        return STALE_FINAL.format(id=job.id)
    try:
        refuse_another_final(job, stage, resolution)  # before "already done"
        if job.stage_done(stage) and not remake:
            return None
        check_can_generate(job, stage)  # mode, approved review, draft first, soundtrack
        chosen = final_resolution(job, resolution) if stage == "final" else ""
        if saved_request_unconfirmed(job, stage):
            record = job.path(generation_files(stage)[0])
            return f"{UNCONFIRMED_INTRO}\n{UNCONFIRMED[generator_key(job)](record)}"
        if job.generator == "seedance" and (remake or needs_new_charge(job, stage)):
            # The prompt a new draft sends: its input names are checked for free.
            generation_prompt(job, stage, chosen, replacing_stale=remake)
    except CharSwapError as error:
        return str(error)
    return None


def planned_generation(
    job: Job, stage: str, resolution: str = "", *, services: Services | None = None
) -> tuple[GenerationRequest, Estimate] | None:
    """The request a draft/final would send and what it would charge; None = no new charge.

    Built exactly as generate() builds it (a remake: once the old draft is archived), so
    the prompt and price shown are what is sent and charged. Without services (the app),
    the generator class prices it; no key is needed to price.
    """
    inputs_changed, prompt_changed = remake_reasons(job, stage)
    remake = inputs_changed or prompt_changed
    if job.stage_done(stage) and not remake:
        return None
    if not (remake or needs_new_charge(job, stage)):
        return None  # a saved request or a downloaded result: it resumes free
    chosen = final_resolution(job, resolution) if stage == "final" else ""
    request = build_request(
        job,
        faces=services.faces if services else None,
        stage=stage,
        final_resolution=chosen,
        replacing_stale=remake,
    )
    if is_promote(job, stage, chosen):
        SeedanceGenerator.promote_body(request.draft_task_id)  # refuses with no task id
    if services is not None:
        generator = generator_for(job, services)
    else:
        generator = GENERATOR_CLASSES[generator_key(job)](None)  # pricing only, never run
    return request, generator.estimate(request, stage)


def planned_charge(
    job: Job, command: str, resolution: str = "", *, services: Services | None = None
) -> Estimate | None:
    """What `charswap <command>` would charge now; None when it charges nothing new.

    command: "resume", "repair", "draft" or "final". The CLI asks with it and checks
    --confirmed-usd against it; the app's price dialog shows it. The two can't disagree,
    and the command (the child process) has the last word.
    """
    if command == "resume":
        remaining = remaining_paid_stages(job)
        return estimate_stages(remaining, seconds=PREP_ESTIMATE_SECONDS) if remaining else None
    if command == "repair":
        return estimate_repair()  # every repair is a fresh SAM 3 run
    if command not in GENERATION_STAGES:
        raise ValueError(f"Unknown command {command!r}.")
    planned = planned_generation(job, command, resolution, services=services)
    return planned[1] if planned else None


def next_step(job: Job, *, approved: bool) -> str:
    """The one thing to do next (shown by `charswap status`)."""
    mode = MODES[job.mode]
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_cli.py -q --no-cov`  
Expected: PASS (205 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1049 passed, 1 deselected; coverage above 96 %
git add src/character_swap/cli.py src/character_swap/costs.py src/character_swap/pipeline.py tests/test_cli.py
git commit -m "feat: --confirmed-usd; planned_charge and generate_refusal shared by the CLI and the app"
````

---

### Task 9: The runner: commands as child processes (`web/runner.py`)

A5, §4.1. `Runner(command=None, *, cwd, env=None, popen=subprocess.Popen)` starts
`[sys.executable, "-m", "character_swap", ...]` with `start_new_session=True`, stdin
`DEVNULL`, stdout and stderr appended to `runs/<id>/app.log` after a `=== <command> <time> ===`
line. One live child per job (`RunnerBusy`). `start(incoming, args)` starts a new swap's
prepare; `start_status(start)` is `running` until the id file exists (`ready`), or the child
exits without one (`failed`, with the last lines of its log). When a new swap's child exits,
its log is appended to the job's `app.log` and `runs/_incoming/<start>` is deleted. Children
are reaped on each progress request and on shutdown; the server never kills one.

`/api/jobs/{id}` gains `running` (a live child here, or a live `run.lock` holder from a
terminal), `holder`, `review` (as saved: approved and fingerprint, never recomputed),
`records` (the draft and final records' status words) and `failure` (the last lines when this
server's last command failed). New `GET /api/starts/{start}`. Step pages show "Working on it…"
and the failure, and embed `running`/`failure` so they reload when either changes.
`tests/fakes.py` gains `FakeProcesses`, a `popen` that records commands and starts nothing.

**Files:**
- Modify: `src/character_swap/web/app.py`
- Modify: `src/character_swap/web/pages.py`
- Create: `src/character_swap/web/runner.py`
- Modify: `src/character_swap/web/static/viewer.css`
- Modify: `src/character_swap/web/templates/job.html`
- Modify: `src/character_swap/web/view_model.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_runner.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write the tests**

**Append** to `tests/fakes.py`:

````python


class FakeProcess:
    """Stands in for subprocess.Popen: the test decides when it ends and what it printed."""

    def __init__(self, args, kwargs, pid):
        self.args = list(args)
        self.kwargs = kwargs
        self.pid = pid
        self.returncode = None
        self.log = Path(kwargs["stdout"].name)  # the app.log the runner opened for it

    def poll(self):
        return self.returncode

    def finish(self, code=0, output=""):
        if output:
            with self.log.open("a", encoding="utf-8") as file:
                file.write(output)
        self.returncode = code


class FakeProcesses:
    """A popen for web.runner.Runner that records every command and starts nothing."""

    def __init__(self):
        self.started: list[FakeProcess] = []

    def __call__(self, args, **kwargs):
        process = FakeProcess(args, kwargs, pid=90000 + len(self.started))
        self.started.append(process)
        return process

    @property
    def last(self) -> FakeProcess:
        return self.started[-1]
````

**Create** `tests/test_runner.py`:

````python
import subprocess
import sys
import time

import pytest

from character_swap.web.runner import LOG_FILE, Runner, RunnerBusy, tail
from fakes import FakeProcesses
from viewer_jobs import make_job


@pytest.fixture
def processes():
    return FakeProcesses()


@pytest.fixture
def runner(tmp_path, processes):
    return Runner(["charswap-test"], cwd=tmp_path, popen=processes)


def test_a_command_runs_in_its_own_session_and_logs_to_the_job(runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth", provider="wavespeed")
    runner.run(job, ["generate", job.id, "--draft", "--confirmed-usd", "0.88"])
    child = processes.last
    assert child.args == ["charswap-test", "generate", job.id, "--draft", "--confirmed-usd", "0.88"]
    assert child.kwargs["start_new_session"] is True
    assert child.kwargs["stdin"] == subprocess.DEVNULL
    assert child.kwargs["stderr"] == subprocess.STDOUT
    assert child.log == job.path(LOG_FILE) and child.kwargs["cwd"] == runner.cwd
    assert job.path(LOG_FILE).read_text().strip().startswith("=== generate 20")
    assert runner.alive(job.id) is True


def test_one_command_per_job_at_a_time(runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    runner.run(job, ["resume", job.id])
    with pytest.raises(RunnerBusy):
        runner.run(job, ["resume", job.id])
    processes.last.finish(0)
    runner.run(job, ["resume", job.id])  # reaped: free again
    assert len(processes.started) == 2


def test_a_failed_command_shows_the_last_lines_of_its_own_run(runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    runner.run(job, ["resume", job.id])
    processes.last.finish(2, "Old run said this.\n")
    runner.run(job, ["generate", job.id, "--draft"])
    assert runner.failure(job) is None  # a new run clears the old failure
    processes.last.finish(2, "Resuming...\nThe price is now about $0.88, not $0.50.\n")
    assert runner.alive(job.id) is False
    text = runner.failure(job)
    assert text.endswith("The price is now about $0.88, not $0.50.")
    assert "Old run" not in text and "===" not in text


def test_a_command_that_succeeds_shows_no_failure(runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    runner.run(job, ["resume", job.id])
    processes.last.finish(0, "done\n")
    runner.reap()
    assert runner.failure(job) is None


def test_a_failure_with_an_empty_log_still_says_so(runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    runner.run(job, ["resume", job.id])
    processes.last.finish(1)
    assert runner.failure(job) == "The command stopped with exit code 1."


def test_a_new_swap_is_ready_once_the_id_file_exists(runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth")  # what prepare will have created
    incoming = runs_dir / "_incoming" / ("a" * 32)
    incoming.mkdir(parents=True)
    start = runner.start(incoming, ["prepare", "clip.mp4", "--id-file", str(incoming / "job-id")])
    assert start == "a" * 32 and runner.start_status(start) == {"state": "running"}
    (incoming / "job-id").write_text(job.id)
    assert runner.start_status(start) == {"state": "ready", "job": job.id}
    assert runner.alive(job.id) is True  # the prepare child now counts for its job
    processes.last.finish(0, "Normalizing video to 24 fps\n")
    assert runner.start_status(start) == {"state": "ready", "job": job.id}
    assert not incoming.exists()  # the uploads were copied into the job: deleted
    assert "Normalizing video" in job.path(LOG_FILE).read_text()  # the log moved with it


def test_a_new_swap_that_fails_before_the_job_exists(runner, processes, runs_dir):
    incoming = runs_dir / "_incoming" / ("b" * 32)
    incoming.mkdir(parents=True)
    start = runner.start(incoming, ["prepare", "clip.mp4"])
    processes.last.finish(2, "Error: WAVESPEED_API_KEY is not set.\n")
    status = runner.start_status(start)
    assert status == {"state": "failed", "error": "Error: WAVESPEED_API_KEY is not set."}
    assert not incoming.exists()
    assert runner.start_status("c" * 32) == {"state": "unknown"}


def test_tail_of_a_missing_or_plain_log(tmp_path):
    assert tail(tmp_path / "missing.log") == ""
    log = tmp_path / "app.log"
    log.write_text("\n".join(f"line {n}" for n in range(30)))
    assert tail(log, 3) == "line 27\nline 28\nline 29"


def test_the_default_command_is_this_python_running_the_cli(tmp_path):
    assert Runner(cwd=tmp_path).command == [sys.executable, "-m", "character_swap"]


def test_a_real_child_is_reaped_and_its_output_kept(tmp_path, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    script = "import sys; print('hello from', sys.argv[1])"
    runner = Runner([sys.executable, "-c", script], cwd=tmp_path)
    runner.run(job, ["resume"])
    deadline = time.monotonic() + 30
    while runner.alive(job.id) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.alive(job.id) is False and runner.failure(job) is None
    assert "hello from resume" in job.path(LOG_FILE).read_text()
````

**Replace** in `tests/test_web_app.py`:

````python
from character_swap.review import decide, fingerprint
from character_swap.web import steps
from character_swap.web.app import create_app
from viewer_jobs import finish_stage, make_job

KEYS = {"REPLICATE_API_TOKEN": "r-secret", "WAVESPEED_API_KEY": "w-secret"}
````

with:

````python
from character_swap.review import decide, fingerprint
from character_swap.web import steps
from character_swap.web.app import create_app
from character_swap.web.runner import Runner
from fakes import FakeProcesses
from viewer_jobs import finish_stage, make_job

KEYS = {"REPLICATE_API_TOKEN": "r-secret", "WAVESPEED_API_KEY": "w-secret"}
````

**Replace** in `tests/test_web_app.py`:

````python


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def _app_job(runs_dir, media, **kwargs):
````

with:

````python


@pytest.fixture
def processes():
    return FakeProcesses()  # every command the app starts is recorded; nothing really runs


@pytest.fixture
def runner(tmp_path, processes):
    return Runner(["charswap"], cwd=tmp_path, popen=processes)


@pytest.fixture
def client(settings, runner):
    return TestClient(create_app(settings, runner=runner), base_url="http://127.0.0.1")


def _app_job(runs_dir, media, **kwargs):
````

**Replace** in `tests/test_web_app.py`:

````python
    assert "fast: 2000" in script and "slow: 10000" in script
    assert "Object.keys(shown)" in script  # compares only what the page was rendered with
    assert "if (!playing())" in script and 'getElementById("new-results").hidden = false' in script
````

with:

````python
    assert "fast: 2000" in script and "slow: 10000" in script
    assert "Object.keys(shown)" in script  # compares only what the page was rendered with
    assert "if (!playing())" in script and 'getElementById("new-results").hidden = false' in script


# ---- what is running (the runner and run.lock) --------------------------------------------
def test_api_reports_running_review_and_records_without_hashing(
    client, runner, processes, runs_dir, media, monkeypatch
):
    job = _approved(runs_dir, media)
    job.path("draft-prediction.json").write_text(json.dumps({"status": "creating"}))
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    data = client.get(f"/api/jobs/{job.id}").json()
    assert data["running"] is False and data["holder"] is None and data["failure"] is None
    assert data["review"]["approved"] is True and len(data["review"]["fingerprint"]) == 64
    assert data["records"] == {"draft": "creating", "final": ""}
    runner.run(job, ["generate", job.id, "--draft"])
    assert client.get(f"/api/jobs/{job.id}").json()["running"] is True
    processes.last.finish(2, "The price is now about $0.88, not $0.50. Nothing was charged.\n")
    data = client.get(f"/api/jobs/{job.id}").json()
    assert data["running"] is False and data["failure"].startswith("The price is now about")


def test_a_command_from_a_terminal_counts_as_running(client, runs_dir, media):
    import os

    from character_swap.lock import process_start

    job = _app_job(runs_dir, media)
    holder = {
        "pid": os.getpid(),
        "command": "resume",
        "started_at": "2026-11-13T10:00:00+00:00",
        "process_start": process_start(os.getpid()),
    }
    job.path("run.lock").write_text(json.dumps(holder))
    data = client.get(f"/api/jobs/{job.id}").json()
    assert data["running"] is True and data["holder"]["command"] == "resume"
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert "Working on it" in text and "&#34;running&#34;: true" in text


def test_a_failed_command_shows_its_last_lines_on_the_page(
    client, runner, processes, runs_dir, media
):
    job = _approved(runs_dir, media)
    runner.run(job, ["generate", job.id, "--draft"])
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "Working on it" in text
    processes.last.finish(2, f"Error: cannot read {Path.home()}/clip.mp4 <here>\n")
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "The last command stopped" in text and "cannot read ~/clip.mp4 &lt;here&gt;" in text


def test_start_progress_follows_a_new_swap(client, runner, processes, runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=0, provider="wavespeed")
    incoming = runs_dir / "_incoming" / ("d" * 32)
    incoming.mkdir(parents=True)
    start = runner.start(incoming, ["prepare", "clip.mp4"])
    assert client.get(f"/api/starts/{start}").json() == {"state": "running"}
    (incoming / "job-id").write_text(job.id)
    data = client.get(f"/api/starts/{start}").json()
    assert data == {"state": "ready", "job": job.id, "redirect": f"/jobs/{job.id}/step/2"}


def test_start_progress_of_a_failed_or_unknown_start(client, runner, processes, runs_dir):
    incoming = runs_dir / "_incoming" / ("e" * 32)
    incoming.mkdir(parents=True)
    start = runner.start(incoming, ["prepare", "clip.mp4"])
    processes.last.finish(2, f"Error: {Path.home()}/clip.mp4 is too short.\n")
    assert client.get(f"/api/starts/{start}").json() == {
        "state": "failed",
        "error": "Error: ~/clip.mp4 is too short.",
    }
    assert client.get(f"/api/starts/{'f' * 32}").status_code == 404
    assert client.get("/api/starts/not-a-start").status_code == 404
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_runner.py tests/test_web_app.py -q --no-cov`  
Expected: FAIL. `ModuleNotFoundError: No module named 'character_swap.web.runner'` (2 collection errors).

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/web/app.py`:

````python
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
````

with:

````python
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
````

**Replace** in `src/character_swap/web/app.py`:

````python
from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    ViewError,
    current_step,
    progress,
    read_state,
    tilde,
)

````

with:

````python
from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.lock import running_holder
from character_swap.modes import GENERATION_STAGES
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import Runner
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    PAID_RECORD,
    ViewError,
    current_step,
    progress,
    read_state,
    record_status,
    saved_review,
    tilde,
)

````

**Replace** in `src/character_swap/web/app.py`:

````python
}
# WaveSpeed bills the input seconds plus the output seconds: the draft hint on step 1.
DRAFT_PER_SECOND = PRICES["wavespeed-480"].usd + PRICES["wavespeed-480-input"].usd


def create_app(
    settings: Settings, *, reload_settings: Callable[[], Settings] | None = None
) -> FastAPI:
    """`reload_settings` re-reads .env on each request (A4); tests pass fixed settings."""
    runs_dir = settings.runs_dir
    current_settings = reload_settings or (lambda: settings)
    app = FastAPI(title="Character Swap", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
````

with:

````python
}
# WaveSpeed bills the input seconds plus the output seconds: the draft hint on step 1.
DRAFT_PER_SECOND = PRICES["wavespeed-480"].usd + PRICES["wavespeed-480-input"].usd
START_ID = re.compile(r"[0-9a-f]{32}")


def create_app(
    settings: Settings,
    *,
    reload_settings: Callable[[], Settings] | None = None,
    runner: Runner | None = None,
) -> FastAPI:
    """`reload_settings` re-reads .env on each request (A4); tests pass fixed settings.

    `runner` starts the charswap commands; tests pass one whose processes are fakes.
    """
    runs_dir = settings.runs_dir
    current_settings = reload_settings or (lambda: settings)
    runner = runner or Runner(cwd=settings.root)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        runner.reap()  # collect finished children; running ones keep going on their own

    app = FastAPI(
        title="Character Swap", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
````

**Replace** in `src/character_swap/web/app.py`:

````python
    def damaged(request: Request, job_id: str, exc: Exception) -> Response:
        return message(request, f"Swap {job_id[:8]} cannot be shown", str(exc), 500)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        page = home_page(runs_dir, current_settings())
````

with:

````python
    def damaged(request: Request, job_id: str, exc: Exception) -> Response:
        return message(request, f"Swap {job_id[:8]} cannot be shown", str(exc), 500)

    def running(job: Job) -> bool:
        """A live child of this server, or a live command from a terminal (run.lock).

        The lock is only read, never taken: taking it to check could make a starting
        command fail with "busy".
        """
        return runner.alive(job.id) or running_holder(job) is not None

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        page = home_page(runs_dir, current_settings())
````

**Replace** in `src/character_swap/web/app.py`:

````python
            reachable = current_step(job, read_state(job))
            if number > reachable:  # a later step is not open yet
                return RedirectResponse(step_url(job.id, reachable), status_code=303)
            page = job_page(job, runs_dir, number, tab=tab)
        except ViewError as exc:
            return damaged(request, job_id, exc)
        shown = {"stages": page.progress["stages"], "generation": page.progress["generation"]}
        context = {"page": page, "bar": page.bar, "progress_json": json.dumps(shown)}
        return templates.TemplateResponse(request, "job.html", context)

````

with:

````python
            reachable = current_step(job, read_state(job))
            if number > reachable:  # a later step is not open yet
                return RedirectResponse(step_url(job.id, reachable), status_code=303)
            page = job_page(
                job, runs_dir, number, tab=tab, running=running(job), failure=runner.failure(job)
            )
        except ViewError as exc:
            return damaged(request, job_id, exc)
        shown = {
            "stages": page.progress["stages"],
            "generation": page.progress["generation"],
            "running": page.running,
            "failure": page.failure,
        }
        context = {"page": page, "bar": page.bar, "progress_json": json.dumps(shown)}
        return templates.TemplateResponse(request, "job.html", context)

````

**Replace** in `src/character_swap/web/app.py`:

````python
            state = read_state(job)
        except ViewError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(progress(state), headers={"Cache-Control": "no-store"})

    return app
````

with:

````python
            state = read_state(job)
        except ViewError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        data = progress(state)  # cheap: no hashing, no review recomputed
        holder = running_holder(job)
        decision = saved_review(job)
        failure = runner.failure(job)
        data.update(
            running=runner.alive(job.id) or holder is not None,
            holder={"command": holder.get("command"), "started_at": holder.get("started_at")}
            if holder
            else None,
            review={
                "approved": bool(decision.get("approved")),
                "fingerprint": str(decision.get("fingerprint") or ""),
            },
            records={stage: record_status(job, PAID_RECORD[stage]) for stage in GENERATION_STAGES},
            failure=tilde(failure) if failure else None,
        )
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/api/starts/{start}")
    def start_progress(start: str) -> JSONResponse:
        """A new swap's prepare: running, then ready (the job exists) or failed (why)."""
        if not START_ID.fullmatch(start):
            raise HTTPException(404, NOT_FOUND)
        found = runner.start_status(start)
        if found["state"] == "unknown":
            raise HTTPException(404, "This upload is not running here any more. See your swaps.")
        if found["state"] == "ready":
            found["redirect"] = step_url(found["job"], 2)
        if found["state"] == "failed":
            found["error"] = tilde(found["error"])
        return JSONResponse(found, headers={"Cache-Control": "no-store"})

    return app
````

**Replace** in `src/character_swap/web/pages.py`:

````python
    read_only: bool
    progress: dict[str, Any]
    reachable: int  # the furthest step this job can show
    original: str | None = None  # the normalized clip: the "before" side of every wipe
    upload: str | None = None
    photo: str | None = None
````

with:

````python
    read_only: bool
    progress: dict[str, Any]
    reachable: int  # the furthest step this job can show
    running: bool = False  # a command is working on this job (from the app or a terminal)
    failure: str | None = None  # the last lines of the app's last command, when it failed
    original: str | None = None  # the normalized clip: the "before" side of every wipe
    upload: str | None = None
    photo: str | None = None
````

**Replace** in `src/character_swap/web/pages.py`:

````python
    return found


def job_page(job: Job, runs_dir: Path, number: int, *, tab: str = "") -> JobPage:
    """Everything one step screen shows. Raises ViewError for a damaged state.json."""
    state = read_state(job)
    key = generator_key(job, state)
    reachable = current_step(job, state)
````

with:

````python
    return found


def job_page(
    job: Job,
    runs_dir: Path,
    number: int,
    *,
    tab: str = "",
    running: bool = False,
    failure: str | None = None,
) -> JobPage:
    """Everything one step screen shows. Raises ViewError for a damaged state.json.

    `running` and `failure` come from the server (its children and run.lock).
    """
    state = read_state(job)
    key = generator_key(job, state)
    reachable = current_step(job, state)
````

**Replace** in `src/character_swap/web/pages.py`:

````python
        "read_only": not is_app_job(state, key),
        "progress": progress(state),
        "reachable": reachable,
        "original": original,
        "prep_done": prep_done(state),
        "spent_usd": completed_cost(cost_rows(job, state)),
````

with:

````python
        "read_only": not is_app_job(state, key),
        "progress": progress(state),
        "reachable": reachable,
        "running": running,
        "failure": tilde(failure) if failure else None,
        "original": original,
        "prep_done": prep_done(state),
        "spent_usd": completed_cost(cost_rows(job, state)),
````

**Create** `src/character_swap/web/runner.py`:

````python
"""Start the long-running `charswap` commands as child processes (decision A5).

prepare, resume, repair, draft and final run as the same CLI commands the terminal uses,
so every tested money-safety rule is reused. Each child gets its own session (it outlives
the server and a closed browser), no stdin, and appends its output to runs/<id>/app.log
after a `=== <command> <time> ===` line. The page never parses that output; it only shows
the last lines when a child fails. The server never kills a child.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from character_swap.jobs import Job

LOG_FILE = "app.log"
ID_FILE = "job-id"
TAIL_LINES = 12
HEADER = "=== "


class RunnerBusy(RuntimeError):
    """This server already has a live command for the job."""


@dataclass
class Child:
    process: Any  # subprocess.Popen
    log: Path
    job_id: str | None
    incoming: Path | None = None  # a new swap's upload folder (runs/_incoming/<start>)


def tail(log: Path, lines: int = TAIL_LINES) -> str:
    """The last lines of the latest run in an app.log ("" when there is none)."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = text.splitlines()
    starts = [number for number, row in enumerate(rows) if row.startswith(HEADER)]
    latest = rows[starts[-1] + 1 :] if starts else rows
    return "\n".join([row for row in latest if row.strip()][-lines:])


def _header(log: Path, args: Sequence[str]) -> None:
    started = datetime.now(UTC).isoformat(timespec="seconds")
    with log.open("a", encoding="utf-8") as file:
        file.write(f"\n{HEADER}{args[0]} {started} ===\n")


class Runner:
    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.command = list(command) if command else [sys.executable, "-m", "character_swap"]
        self.cwd = Path(cwd)
        self.env = dict(env) if env is not None else None
        self._popen = popen
        self._guard = threading.Lock()
        self._children: dict[str, Child] = {}  # job id, or "start:<id>" for a new swap
        self._exits: dict[str, int] = {}  # job id -> the last child's exit code
        self._starts: dict[str, dict[str, str]] = {}  # finished new swaps: start id -> result

    def _spawn(self, args: Sequence[str], log: Path) -> Any:
        log.parent.mkdir(parents=True, exist_ok=True)
        _header(log, args)
        with log.open("a", encoding="utf-8") as output:
            return self._popen(
                [*self.command, *args],
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # survives the server and a closed browser
            )

    def run(self, job: Job, args: Sequence[str]) -> None:
        """Start `charswap <args>` for an existing job. RunnerBusy if one already runs here."""
        with self._guard:
            self._reap()
            if self._alive(job.id):
                raise RunnerBusy(job.id)
            self._exits.pop(job.id, None)
            process = self._spawn(args, job.path(LOG_FILE))
            self._children[job.id] = Child(process, job.path(LOG_FILE), job.id)

    def start(self, incoming: Path, args: Sequence[str]) -> str:
        """Start `charswap prepare ...` for a new swap; returns the start id to poll."""
        start = incoming.name
        with self._guard:
            process = self._spawn(args, incoming / LOG_FILE)
            self._children[f"start:{start}"] = Child(process, incoming / LOG_FILE, None, incoming)
        return start

    def start_status(self, start: str) -> dict[str, str]:
        """{"state": "running" | "ready" | "failed" | "unknown", "job": id, "error": text}."""
        with self._guard:
            self._reap()
            if start in self._starts:
                return dict(self._starts[start])
            child = self._children.get(f"start:{start}")
            if child is None:
                return {"state": "unknown"}
            if child.job_id:
                return {"state": "ready", "job": child.job_id}
            return {"state": "running"}

    def alive(self, job_id: str) -> bool:
        """This server has a live child for the job (after reaping finished ones)."""
        with self._guard:
            self._reap()
            return self._alive(job_id)

    def failure(self, job: Job) -> str | None:
        """The last lines of the log when this server's latest child for the job failed."""
        with self._guard:
            self._reap()
            code = self._exits.get(job.id)
        if not code:
            return None
        return tail(job.path(LOG_FILE)) or f"The command stopped with exit code {code}."

    def reap(self) -> None:
        """Collect finished children (each progress request, and on shutdown). Never kills."""
        with self._guard:
            self._reap()

    def _alive(self, job_id: str) -> bool:
        return any(
            child.job_id == job_id and child.process.poll() is None
            for child in self._children.values()
        )

    def _reap(self) -> None:
        for key, child in list(self._children.items()):
            if child.incoming is not None and child.job_id is None:
                child.job_id = _read_id(child.incoming / ID_FILE)
            code = child.process.poll()
            if code is None:
                continue
            del self._children[key]
            if child.incoming is not None:
                self._finish_start(key.removeprefix("start:"), child, code)
            elif child.job_id:
                self._exits[child.job_id] = code

    def _finish_start(self, start: str, child: Child, code: int) -> None:
        log = child.log
        if child.job_id:
            self._starts[start] = {"state": "ready", "job": child.job_id}
            self._exits[child.job_id] = code
            job_log = child.incoming.parent.parent / child.job_id / LOG_FILE  # runs/<id>/app.log
            try:
                with job_log.open("a", encoding="utf-8") as file:
                    file.write(log.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass  # the job folder is gone: nothing to keep the log for
        else:
            error = tail(log) or f"The command stopped with exit code {code}."
            self._starts[start] = {"state": "failed", "error": error}
        shutil.rmtree(child.incoming, ignore_errors=True)  # the uploads were copied into the job


def _read_id(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
````

**Replace** in `src/character_swap/web/static/viewer.css`:

````css
.error p, .notice p { margin: 0 0 var(--space-2); }
.error p:last-child, .notice p:last-child { margin: 0; }
.notice.info { background: var(--surface-2); }
.notice button {
  font: inherit; color: var(--accent); background: none; border: 0;
  min-height: var(--target); padding: 0 var(--space-2); text-decoration: underline; cursor: pointer;
````

with:

````css
.error p, .notice p { margin: 0 0 var(--space-2); }
.error p:last-child, .notice p:last-child { margin: 0; }
.notice.info { background: var(--surface-2); }
.log { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; font-size: var(--text-sm); }
.notice button {
  font: inherit; color: var(--accent); background: none; border: 0;
  min-height: var(--target); padding: 0 var(--space-2); text-decoration: underline; cursor: pointer;
````

**Replace** in `src/character_swap/web/templates/job.html`:

````html
  {% if page.read_only %}
  <p class="notice info">{{ steps.READ_ONLY.format(id=page.id) }}</p>
  {% endif %}
  {% if page.error %}
  <div class="error" role="alert"><p>{{ page.error }}</p></div>
  {% endif %}
````

with:

````html
  {% if page.read_only %}
  <p class="notice info">{{ steps.READ_ONLY.format(id=page.id) }}</p>
  {% endif %}
  {% if page.running %}
  <p class="notice info" role="status"><span class="spinner" aria-hidden="true"></span> {{ steps.RUNNING }}</p>
  {% endif %}
  {% if page.failure %}
  <div class="error" role="alert"><p>{{ steps.LAST_STOPPED }}</p><pre class="log">{{ page.failure }}</pre></div>
  {% endif %}
  {% if page.error %}
  <div class="error" role="alert"><p>{{ page.error }}</p></div>
  {% endif %}
````

**Replace** in `src/character_swap/web/view_model.py`:

````python
    if not isinstance(found, str) or not _PREDICTION_ID.fullmatch(found):
        return None  # includes the {"status": "creating"} marker written before a paid POST
    return found


def prediction_url(job: Job, record: str) -> str | None:
````

with:

````python
    if not isinstance(found, str) or not _PREDICTION_ID.fullmatch(found):
        return None  # includes the {"status": "creating"} marker written before a paid POST
    return found


def record_status(job: Job, record: str) -> str:
    """A saved provider record's status word ("creating", "processing", "failed", ...), or ""."""
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) else ""


def prediction_url(job: Job, record: str) -> str | None:
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_runner.py tests/test_web_app.py -q --no-cov`  
Expected: PASS (69 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1064 passed, 1 deselected; coverage 96.98 %
git add src/character_swap/web/app.py src/character_swap/web/pages.py src/character_swap/web/runner.py src/character_swap/web/static/viewer.css src/character_swap/web/templates/job.html src/character_swap/web/view_model.py tests/fakes.py tests/test_runner.py tests/test_web_app.py
git commit -m "feat: the app starts charswap commands as child processes and follows them"
````

---

### Task 10: New swap upload; every POST needs the launch token and our Origin

The first POST route, so the guard (§4.6) comes with it and every later POST is born guarded:
a per-launch random token (in a `<meta>` tag, sent by `app.js` as `X-Charswap-Token`) **and**
an `Origin` of `http://127.0.0.1:<port>` or `http://localhost:<port>`, else 403 ("The app was
restarted. Reload the page." for a stale token). `create_app` gains `port`; `charswap app`
passes it. A POST error is JSON.

`POST /jobs` (step 1): refused before it is read when `Content-Length` is over 2 × 200 MB +
1 MB; both keys must be set; then the free checks, each shown next to its field: the tick box,
the look, the description (300 characters at most), the confirmed price, each file saved as
`clip.<ext>` / `photo.<ext>` (never the user's file name) and counted while written (refused
past 200 MB), the clip opens in PyAV and has 4 to 30 whole seconds, the photo passes
`faces.check_photo`. Then it starts `charswap prepare <clip> --character <photo> --mode <look>
--yes --confirmed-usd <price> --id-file <incoming>/job-id [--describe …]` and returns the start
id; the page polls `/api/starts/{start}` and goes to step 2. In the browser, the clip's length
is read locally for "Draft later: about $X" (whole seconds, at least 4, times $0.22).
New dependency `python-multipart`; `media-src` allows `blob:` for that local read.

**Prepare** is paid, so it opens the price dialog first ("Charge about $0.06 on Replicate?",
spec §3): the `<dialog id="confirm">` is a macro in `macros.html` (Task 12's job pages reuse
it, and `app.js`'s `ask()` serves both). The description goes to the command as one
`--describe=<text>` item, so a description starting with "-" can never become an option.
When the app starts it deletes `runs/_incoming/<start>` folders untouched for a day (uploads
a restarted server will never follow; copies only, never a job record).

**Files:**
- Modify: `src/character_swap/cli.py`
- Modify: `src/character_swap/web/app.py`
- Modify: `src/character_swap/web/static/app.js`
- Modify: `src/character_swap/web/static/viewer.css`
- Modify: `src/character_swap/web/templates/base.html`
- Modify: `src/character_swap/web/templates/macros.html`
- Modify: `src/character_swap/web/templates/new.html`
- Create: `src/character_swap/web/uploads.py`
- Modify (by `uv add`): `pyproject.toml`, `uv.lock`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Add the dependency**

````bash
uv add "python-multipart>=0.0.20"
````

It adds the line to `pyproject.toml` and updates `uv.lock` (uv may also rewrite the lock
file's format; commit it as uv wrote it).

- [ ] **Step 2: Write the tests**

**Replace** in `tests/test_web_app.py`:

````python
from character_swap.web.app import create_app
from character_swap.web.runner import Runner
from fakes import FakeProcesses
from viewer_jobs import finish_stage, make_job

KEYS = {"REPLICATE_API_TOKEN": "r-secret", "WAVESPEED_API_KEY": "w-secret"}
````

with:

````python
from character_swap.web.app import create_app
from character_swap.web.runner import Runner
from fakes import FakeProcesses
from helpers import solid, write_video
from viewer_jobs import finish_stage, make_job

KEYS = {"REPLICATE_API_TOKEN": "r-secret", "WAVESPEED_API_KEY": "w-secret"}
````

**Replace** in `tests/test_web_app.py`:

````python
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}
````

with:

````python
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}
````

**Replace** in `tests/test_web_app.py`:

````python
    }
    assert client.get(f"/api/starts/{'f' * 32}").status_code == 404
    assert client.get("/api/starts/not-a-start").status_code == 404
````

with:

````python
    }
    assert client.get(f"/api/starts/{'f' * 32}").status_code == 404
    assert client.get("/api/starts/not-a-start").status_code == 404


# ---- POST safety: the per-launch token and our own Origin ---------------------------------
ORIGIN = "http://127.0.0.1:8770"


def _token(client):
    return client.get("/").text.split('name="charswap-token" content="')[1].split('"')[0]


def _post(client, path, *, headers=None, **kwargs):
    sent = {"Origin": ORIGIN, "X-Charswap-Token": _token(client)}
    sent.update(headers or {})
    return client.post(path, headers={k: v for k, v in sent.items() if v is not None}, **kwargs)


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"X-Charswap-Token": None}, "The app was restarted. Reload the page."),
        ({"X-Charswap-Token": "old-token"}, "The app was restarted. Reload the page."),
        ({"Origin": "https://evil.example"}, "did not come from the app"),
        ({"Origin": "http://127.0.0.1:9999"}, "did not come from the app"),
        ({"Origin": None}, "did not come from the app"),
    ],
)
def test_a_post_without_the_token_or_from_another_site_is_refused(
    client, processes, headers, message
):
    response = _post(client, "/jobs", headers=headers, data={"rights": "yes"})
    assert response.status_code == 403 and message in response.json()["error"]
    assert processes.started == []


def test_each_launch_has_its_own_token(settings, runner):
    first = TestClient(create_app(settings, runner=runner), base_url="http://127.0.0.1")
    second = TestClient(create_app(settings, runner=runner), base_url="http://127.0.0.1")
    assert _token(first) != _token(second)
    response = second.post(
        "/jobs", headers={"Origin": ORIGIN, "X-Charswap-Token": _token(first)}, data={}
    )
    assert response.status_code == 403  # a page left open across a restart


def test_localhost_is_our_origin_too(client, processes):
    response = _post(client, "/jobs", headers={"Origin": "http://localhost:8770"}, data={})
    assert response.status_code == 422  # past the guard: the form itself is incomplete


# ---- step 1: the upload starts prepare ------------------------------------------------
@pytest.fixture
def clip(tmp_path):
    return write_video(tmp_path / "my clip.mp4", [solid((9, 9, 9))] * 96, audio_seconds=4)


@pytest.fixture
def photo(tmp_path):
    from PIL import Image

    path = tmp_path / "me.jpg"
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(path, "JPEG")
    return path


def _upload(client, clip, photo, **fields):
    data = {"rights": "yes", "look": "depth_mesh", "confirm_usd": "0.0608", **fields}
    files = {
        "clip": (clip.name, clip.read_bytes(), "video/mp4"),
        "photo": (photo.name, photo.read_bytes(), "image/jpeg"),
    }
    return _post(client, "/jobs", data=data, files=files)


def test_an_upload_starts_prepare_with_the_confirmed_price(
    client, processes, runs_dir, clip, photo
):
    response = _upload(client, clip, photo, describe="  Short   dark hair. ", look="depth")
    assert response.status_code == 202
    start = response.json()["start"]
    incoming = runs_dir / "_incoming" / start
    assert sorted(p.name for p in incoming.iterdir()) == [
        "app.log",
        "clip.mp4",
        "photo-checked.jpg",
        "photo.jpg",
    ]  # the user's file names are never used as paths
    args = processes.last.args
    assert args[:2] == ["charswap", "prepare"] and args[2] == str((incoming / "clip.mp4").resolve())
    assert args[3:] == [
        "--character",
        str((incoming / "photo.jpg").resolve()),
        "--mode",
        "depth",
        "--yes",
        "--confirmed-usd",
        "0.060800",
        "--id-file",
        str((incoming / "job-id").resolve()),
        "--describe=Short dark hair.",
    ]
    assert client.get(f"/api/starts/{start}").json() == {"state": "running"}


@pytest.mark.parametrize(
    ("fields", "field", "words"),
    [
        ({"rights": ""}, "rights", "Tick this box"),
        ({"look": "face_mesh"}, "form", "Choose a look"),
        ({"describe": "x" * 301}, "describe", "under 300 characters"),
        ({"confirm_usd": ""}, "form", "Confirm the price first"),
        ({"confirm_usd": "-1"}, "form", "Confirm the price first"),
    ],
)
def test_free_checks_on_the_form(client, processes, runs_dir, clip, photo, fields, field, words):
    response = _upload(client, clip, photo, **fields)
    assert response.status_code == 422 and words in response.json()["errors"][field]
    assert processes.started == [] and not any((runs_dir / "_incoming").iterdir())


def test_free_checks_on_the_clip(client, processes, tmp_path, photo, media):
    short = media.video  # 1 s
    response = _upload(client, short, photo)
    assert "needs at least 4 whole seconds" in response.json()["errors"]["clip"]
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert "can't be opened" in _upload(client, broken, photo).json()["errors"]["clip"]
    avi = tmp_path / "clip.avi"
    avi.write_bytes(b"x")
    assert "Use a mp4 or mov file" in _upload(client, avi, photo).json()["errors"]["clip"]
    long = write_video(tmp_path / "long.mp4", [solid((9, 9, 9))] * 24 * 31)
    assert "30 s at most" in _upload(client, long, photo).json()["errors"]["clip"]
    assert processes.started == []


def test_free_checks_on_the_photo(client, processes, tmp_path, clip, media):
    response = _upload(client, clip, media.image)  # 8x8 png: too small for Seedance
    assert response.status_code == 422 and "photo" in response.json()["errors"]
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert "empty" in _upload(client, clip, empty).json()["errors"]["photo"]
    assert processes.started == []


def test_a_file_over_200_mb_is_refused_while_it_is_written(
    client, processes, clip, photo, monkeypatch
):
    monkeypatch.setattr("character_swap.web.uploads.MAX_FILE", 1000)
    response = _upload(client, clip, photo)
    assert "over 200 MB" in response.json()["errors"]["clip"] and processes.started == []


def test_a_request_too_large_is_refused_before_it_is_read(
    client, processes, clip, photo, monkeypatch
):
    monkeypatch.setattr("character_swap.web.app.MAX_REQUEST", 1000)
    response = _upload(client, clip, photo)
    assert response.status_code == 413 and "200 MB each" in response.json()["error"]


def test_an_upload_needs_both_keys(tmp_path, runs_dir, runner, processes, clip, photo):
    settings = Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir)})
    client = TestClient(create_app(settings, runner=runner), base_url="http://127.0.0.1")
    response = _upload(client, clip, photo)
    assert response.status_code == 409
    assert "REPLICATE_API_TOKEN and WAVESPEED_API_KEY" in response.json()["errors"]["form"]
    assert processes.started == []


def test_the_upload_script_reads_the_clip_length_and_posts_the_shown_price(client):
    script = client.get("/static/app.js").text
    assert "URL.createObjectURL(file)" in script and "Math.floor(probe.duration)" in script
    assert 'body.set("confirm_usd", button.dataset.usd)' in script
    assert '"X-Charswap-Token": token' in script and "/api/starts/" in script


def test_a_description_starting_with_a_dash_is_never_an_option(client, processes, clip, photo):
    response = _upload(client, clip, photo, describe="--yes is not a flag")
    assert response.status_code == 202
    assert processes.last.args[-1] == "--describe=--yes is not a flag"


def test_prepare_asks_with_the_price_dialog_first(client):
    text = client.get("/new").text
    assert '<dialog id="confirm"' in text and "Yes, charge it" in text
    assert 'data-question="Charge about $0.06 on Replicate?"' in text
    assert "about $0.06 · Replicate" in text
    script = client.get("/static/app.js").text
    submit = script.split('form.addEventListener("submit"')[1]
    assert submit.index("await ask(button.dataset.question)") < submit.index("await post(")


def test_old_upload_folders_are_swept_when_the_app_starts(settings, runner, runs_dir):
    import os
    import time

    old = runs_dir / "_incoming" / ("a" * 32)
    new = runs_dir / "_incoming" / ("b" * 32)
    for folder in (old, new):
        folder.mkdir(parents=True)
        (folder / "clip.mp4").write_bytes(b"x")
    day_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (day_ago, day_ago))
    create_app(settings, runner=runner)
    assert not old.exists() and new.exists()  # a prepare still starting is never swept
````

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/test_web_app.py -q --no-cov`  
Expected: FAIL. 23 failed, 58 passed: `POST /jobs` answers 405 and there is no `charswap-token` meta tag.

- [ ] **Step 4: Write the code**

**Replace** in `src/character_swap/cli.py`:

````python
        return 2
    url = f"http://{VIEW_HOST}:{port}"
    # A4: keys stay in .env; the app re-reads it on each request.
    app = create_app(settings, reload_settings=lambda: Settings.load(root=settings.root))
    env.out(f"Character Swap app: {url}  (Ctrl-C to stop)")
    opener = None
    if not args.no_browser:
````

with:

````python
        return 2
    url = f"http://{VIEW_HOST}:{port}"
    # A4: keys stay in .env; the app re-reads it on each request.
    app = create_app(settings, reload_settings=lambda: Settings.load(root=settings.root), port=port)
    env.out(f"Character Swap app: {url}  (Ctrl-C to stop)")
    opener = None
    if not args.no_browser:
````

**Replace** in `src/character_swap/web/app.py`:

````python
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
````

with:

````python
from __future__ import annotations

import json
import math
import re
import secrets
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
````

**Replace** in `src/character_swap/web/app.py`:

````python
from character_swap.modes import GENERATION_STAGES
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import Runner
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    PAID_RECORD,
````

with:

````python
from character_swap.modes import GENERATION_STAGES
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import ID_FILE, Runner
from character_swap.web.uploads import (
    CLIP_SUFFIXES,
    INCOMING,
    MAX_REQUEST,
    PHOTO_SUFFIXES,
    UploadError,
    check_clip,
    check_photo_file,
    save,
    sweep_incoming,
)
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    PAID_RECORD,
````

**Replace** in `src/character_swap/web/app.py`:

````python
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}
# WaveSpeed bills the input seconds plus the output seconds: the draft hint on step 1.
DRAFT_PER_SECOND = PRICES["wavespeed-480"].usd + PRICES["wavespeed-480-input"].usd
START_ID = re.compile(r"[0-9a-f]{32}")


def create_app(
````

with:

````python
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}
# WaveSpeed bills the input seconds plus the output seconds: the draft hint on step 1.
DRAFT_PER_SECOND = PRICES["wavespeed-480"].usd + PRICES["wavespeed-480-input"].usd
START_ID = re.compile(r"[0-9a-f]{32}")
TOKEN_HEADER = "X-Charswap-Token"
RESTARTED = "The app was restarted. Reload the page."
OTHER_SITE = "This request did not come from the app, so nothing was started."
NO_PRICE = "Confirm the price first."
MAX_DESCRIBE = 300


def create_app(
````

**Replace** in `src/character_swap/web/app.py`:

````python
    *,
    reload_settings: Callable[[], Settings] | None = None,
    runner: Runner | None = None,
) -> FastAPI:
    """`reload_settings` re-reads .env on each request (A4); tests pass fixed settings.

````

with:

````python
    *,
    reload_settings: Callable[[], Settings] | None = None,
    runner: Runner | None = None,
    port: int = 8770,
) -> FastAPI:
    """`reload_settings` re-reads .env on each request (A4); tests pass fixed settings.

````

**Replace** in `src/character_swap/web/app.py`:

````python
    runs_dir = settings.runs_dir
    current_settings = reload_settings or (lambda: settings)
    runner = runner or Runner(cwd=settings.root)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
````

with:

````python
    runs_dir = settings.runs_dir
    current_settings = reload_settings or (lambda: settings)
    runner = runner or Runner(cwd=settings.root)
    sweep_incoming(runs_dir)  # uploads a restarted server will never follow
    # Every POST needs this launch's token (from the page's <meta>) and our own Origin, so
    # another website open in the same browser can never start a paid step.
    token = secrets.token_urlsafe(32)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
````

**Replace** in `src/character_swap/web/app.py`:

````python
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(steps=steps, money=usd_text)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
````

with:

````python
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(steps=steps, money=usd_text, token=token)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
````

**Replace** in `src/character_swap/web/app.py`:

````python

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
````

with:

````python

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/") or request.method == "POST":
            return JSONResponse(
                {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
````

**Replace** in `src/character_swap/web/app.py`:

````python

    def damaged(request: Request, job_id: str, exc: Exception) -> Response:
        return message(request, f"Swap {job_id[:8]} cannot be shown", str(exc), 500)

    def running(job: Job) -> bool:
        """A live child of this server, or a live command from a terminal (run.lock).
````

with:

````python

    def damaged(request: Request, job_id: str, exc: Exception) -> Response:
        return message(request, f"Swap {job_id[:8]} cannot be shown", str(exc), 500)

    def check_post(request: Request) -> None:
        """403 unless the POST comes from one of this app's own pages (A6 security)."""
        if request.headers.get("origin") not in origins:
            raise HTTPException(403, OTHER_SITE)
        if not secrets.compare_digest(request.headers.get(TOKEN_HEADER, ""), token):
            raise HTTPException(403, RESTARTED)

    def running(job: Job) -> bool:
        """A live child of this server, or a live command from a terminal (run.lock).
````

**Replace** in `src/character_swap/web/app.py`:

````python
            "draft_per_second": f"{DRAFT_PER_SECOND:.4f}",
        }
        return templates.TemplateResponse(request, "new.html", context)

    @app.get("/jobs/{job_id}")
    def job_home(request: Request, job_id: str) -> Response:
````

with:

````python
            "draft_per_second": f"{DRAFT_PER_SECOND:.4f}",
        }
        return templates.TemplateResponse(request, "new.html", context)

    @app.post("/jobs")
    async def new_job(request: Request) -> JSONResponse:
        """Save the uploads, run the free checks, then start `charswap prepare` (paid)."""
        check_post(request)
        length = request.headers.get("content-length", "")
        if not length.isdigit():
            raise HTTPException(411, "The upload has no length; try another browser.")
        if int(length) > MAX_REQUEST:  # refused before it is read
            raise HTTPException(413, "The files are too large: 200 MB each at most.")
        missing = missing_keys(current_settings())
        if missing:
            names = " and ".join(key.name for key in missing)
            return JSONResponse({"errors": {"form": f"Add {names} to .env first."}}, 409)
        form = await request.form(max_files=2, max_fields=10)
        incoming = runs_dir / INCOMING / secrets.token_hex(16)
        incoming.mkdir(parents=True)
        try:
            args = await _prepare_args(form, incoming)
        except UploadError as error:
            shutil.rmtree(incoming, ignore_errors=True)
            return JSONResponse({"errors": {error.field: str(error)}}, 422)
        except BaseException:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
        start = runner.start(incoming, args)  # it deletes `incoming` when the child exits
        return JSONResponse({"start": start}, 202)

    @app.get("/jobs/{job_id}")
    def job_home(request: Request, job_id: str) -> Response:
````

**Replace** in `src/character_swap/web/app.py`:

````python
        return JSONResponse(found, headers={"Cache-Control": "no-store"})

    return app
````

with:

````python
        return JSONResponse(found, headers={"Cache-Control": "no-store"})

    return app


def confirmed_usd(value: object) -> float:
    """The price the dialog showed, as the page sent it (A6). UploadError when missing."""
    try:
        usd = float(str(value))
    except ValueError:
        raise UploadError("form", NO_PRICE) from None
    if not math.isfinite(usd) or usd < 0:
        raise UploadError("form", NO_PRICE)
    return usd


async def _prepare_args(form, incoming: Path) -> list[str]:
    """Check every free thing, then build `charswap prepare ...` (free checks first)."""
    if form.get("rights") != "yes":
        raise UploadError("rights", "Tick this box to go on.")
    look = form.get("look")
    if look not in [value for value, _label, _hint in steps.LOOKS]:
        raise UploadError("form", "Choose a look.")
    describe = " ".join(str(form.get("describe") or "").split())
    if len(describe) > MAX_DESCRIBE:
        raise UploadError("describe", f"Keep it under {MAX_DESCRIBE} characters.")
    usd = confirmed_usd(form.get("confirm_usd"))
    clip = await save(form.get("clip"), incoming, "clip", CLIP_SUFFIXES)
    photo = await save(form.get("photo"), incoming, "photo", PHOTO_SUFFIXES)
    check_clip(clip)
    check_photo_file(photo)
    args = ["prepare", str(clip.resolve()), "--character", str(photo.resolve())]
    args += ["--mode", str(look), "--yes", "--confirmed-usd", f"{usd:.6f}"]
    args += ["--id-file", str((incoming / ID_FILE).resolve())]
    if describe:
        args.append(f"--describe={describe}")  # one argv item, never an option or a shell
    return args
````

**Replace** in `src/character_swap/web/static/app.js`:

````javascript
    window.setInterval(tick, 1000);
  });

  // 3. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
````

with:

````javascript
    window.setInterval(tick, 1000);
  });

  // 3. Every POST carries this launch's token; the browser adds its Origin (both checked).
  const token = (document.querySelector('meta[name="charswap-token"]') || {}).content || "";
  async function post(url, body) {
    const headers = { "X-Charswap-Token": token };
    let payload = body;
    if (!(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body || {});
    }
    try {
      const response = await fetch(url, { method: "POST", headers, body: payload });
      const data = await response.json().catch(() => ({}));
      return { ok: response.ok, status: response.status, data };
    } catch (error) {
      return { ok: false, status: 0, data: { error: "The app is not answering. Is it running?" } };
    }
  }

  // The price dialog: every paid button asks first (spec §3); true only for "Yes, charge it".
  const dialog = document.getElementById("confirm");
  const ask = (text, note) =>
    new Promise((resolve) => {
      document.getElementById("confirm-question").textContent = text;
      document.getElementById("confirm-note").textContent = note || "";
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
      dialog.returnValue = "";
      dialog.showModal();
    });

  // 4. Step 1: the draft hint from the clip's length, then upload and start preparing.
  const form = document.getElementById("new-swap");
  if (form) {
    const button = form.querySelector('button[type="submit"]');
    const label = button.textContent;
    const hint = document.getElementById("draft-hint");
    const clip = form.querySelector("#clip");
    const errors = (name, text) => {
      const node = form.querySelector(`[data-error-for="${name}"]`);
      if (node) node.textContent = text || "";
    };
    clip.addEventListener("change", () => {
      hint.hidden = true;
      const file = clip.files[0];
      if (!file) return;
      const probe = document.createElement("video");
      const link = URL.createObjectURL(file); // read locally; nothing is uploaded yet
      probe.preload = "metadata";
      probe.addEventListener("loadedmetadata", () => {
        // whole seconds, rounded down as preparing cuts them, and at least 4
        const seconds = Math.max(4, Math.floor(probe.duration));
        const usd = seconds * Number(form.dataset.draftPerSecond);
        hint.textContent = `Draft later: about $${usd.toFixed(2)}`;
        hint.hidden = false;
        URL.revokeObjectURL(link);
      });
      probe.src = link;
    });
    const follow = async (start) => {
      const response = await fetch(`/api/starts/${start}`, { cache: "no-store" }).catch(() => null);
      const data = response ? await response.json().catch(() => ({})) : {};
      if (data.state === "ready") {
        location.assign(data.redirect);
        return;
      }
      if (data.state === "failed" || (response && !response.ok)) {
        errors("form", data.error || "Preparing could not start.");
        button.disabled = false;
        button.textContent = label;
        return;
      }
      window.setTimeout(() => follow(start), 2000);
    };
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      form.querySelectorAll("[data-error-for]").forEach((node) => (node.textContent = ""));
      if (!(await ask(button.dataset.question))) return; // Prepare is paid: ask first
      const body = new FormData(form);
      body.set("confirm_usd", button.dataset.usd); // the price shown on the button
      button.disabled = true;
      button.textContent = "Starting…";
      const result = await post(form.dataset.post, body);
      if (!result.ok) {
        const found = result.data.errors || { form: result.data.error || "Something went wrong." };
        Object.entries(found).forEach(([name, text]) => errors(name, text));
        button.disabled = false;
        button.textContent = label;
        return;
      }
      follow(result.data.start);
    });
  }

  // 5. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
````

**Replace** in `src/character_swap/web/static/viewer.css`:

````css
  min-height: var(--target); padding: 0 var(--space-2); text-decoration: underline; cursor: pointer;
}

/* the before/after wipe player: two stacked videos, the top one clipped at the divider */
.wipe { margin-top: var(--space-2); }
.wipe-stage {
````

with:

````css
  min-height: var(--target); padding: 0 var(--space-2); text-decoration: underline; cursor: pointer;
}

/* paid buttons and the price dialog */
.offer { display: inline-flex; flex-direction: column; gap: var(--space-1); }
.dialog {
  border: 0; border-radius: var(--radius); padding: var(--space-6);
  background: var(--surface); color: var(--text); box-shadow: var(--shadow); max-width: 28rem;
}
.dialog::backdrop { background: rgb(0 0 0 / 45%); }
.dialog .actions { justify-content: flex-end; }

/* the before/after wipe player: two stacked videos, the top one clipped at the divider */
.wipe { margin-top: var(--space-2); }
.wipe-stage {
````

**Replace** in `src/character_swap/web/templates/base.html`:

````html
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{% block title %}Swaps{% endblock %} · Character Swap</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/static/viewer.css">
````

with:

````html
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="charswap-token" content="{{ token }}">
  <title>{% block title %}Swaps{% endblock %} · Character Swap</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/static/viewer.css">
````

**Append** to `src/character_swap/web/templates/macros.html`:

````html

{% macro confirm_dialog() %}
<dialog id="confirm" class="dialog" aria-labelledby="confirm-question">
  <form method="dialog">
    <p id="confirm-question" class="line"></p>
    <p id="confirm-note" class="meta"></p>
    <div class="actions">
      <button value="cancel" class="button">{{ steps.CHARGE_NO }}</button>
      <button value="ok" class="button primary">{{ steps.CHARGE_YES }}</button>
    </div>
  </form>
</dialog>
{% endmacro %}
````

**Replace** in `src/character_swap/web/templates/new.html`:

````html
{% extends "base.html" %}
{% block title %}New swap{% endblock %}
{% block body %}
<main id="main" class="page">
````

with:

````html
{% extends "base.html" %}
{% from "macros.html" import confirm_dialog %}
{% block title %}New swap{% endblock %}
{% block body %}
<main id="main" class="page">
````

**Replace** in `src/character_swap/web/templates/new.html`:

````html
    </div>
    <p class="field-error" data-error-for="form" role="alert"></p>
    <div class="actions">
      <button type="submit" class="button primary" data-usd="{{ prepare_usd }}"{% if missing %} disabled{% endif %}>{{ steps.PREPARE }} · about {{ money(prepare_usd) }}</button>
      <p class="meta" id="draft-hint" hidden></p>
    </div>
  </form>
</main>
{% endblock %}
````

with:

````html
    </div>
    <p class="field-error" data-error-for="form" role="alert"></p>
    <div class="actions">
      <span class="offer">
        <button type="submit" class="button primary" data-usd="{{ prepare_usd }}" data-question="{{ steps.CHARGE.format(usd=money(prepare_usd), provider='Replicate') }}"{% if missing %} disabled{% endif %}>{{ steps.PREPARE }} · about {{ money(prepare_usd) }}</button>
        <span class="meta">about {{ money(prepare_usd) }} · Replicate</span>
      </span>
      <p class="meta" id="draft-hint" hidden></p>
    </div>
  </form>
  {{ confirm_dialog() }}
</main>
{% endblock %}
````

**Create** `src/character_swap/web/uploads.py`:

````python
"""The free checks on a new swap's uploads, before anything is started or charged."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import av
from starlette.datastructures import UploadFile

from character_swap.faces import FaceError, check_photo
from character_swap.media.video import FPS, stream_seconds
from character_swap.modes import GENERATOR_LIMITS

MAX_FILE = 200 * 1024 * 1024  # each upload
MAX_REQUEST = 2 * MAX_FILE + 1024 * 1024  # refused before it is read
CHUNK = 1024 * 1024
CLIP_SUFFIXES = (".mp4", ".mov")
PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


INCOMING = "_incoming"  # runs/_incoming/<start>: a new swap's uploads until prepare copies them
STALE_SECONDS = 24 * 3600


def sweep_incoming(runs_dir: Path, *, older_than: float = STALE_SECONDS) -> list[Path]:
    """Delete upload folders nobody touched for a day (left by a server restart).

    They hold copies of the uploads only, never a job record; a prepare that is still
    starting touched its folder minutes ago, so it is never swept.
    """
    removed = []
    folder = Path(runs_dir) / INCOMING
    if not folder.is_dir():
        return removed
    cutoff = time.time() - older_than
    for start in folder.iterdir():
        try:
            stale = start.is_dir() and start.stat().st_mtime < cutoff
        except OSError:
            continue
        if stale:
            shutil.rmtree(start, ignore_errors=True)
            removed.append(start)
    return removed


class UploadError(ValueError):
    """One field's problem, shown next to that field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


async def save(upload: object, folder: Path, stem: str, suffixes: tuple[str, ...]) -> Path:
    """Copy one uploaded file to folder/<stem><suffix>, counting bytes as it goes.

    The user's file name is only used for its extension, never as a path.
    """
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise UploadError(stem, "Choose a file.")
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in suffixes:
        raise UploadError(stem, f"Use a {' or '.join(s.lstrip('.') for s in suffixes)} file.")
    dest = folder / f"{stem}{suffix}"
    size = 0
    with dest.open("wb") as out:
        while chunk := await upload.read(CHUNK):
            size += len(chunk)
            if size > MAX_FILE:
                raise UploadError(stem, "This file is over 200 MB. Use a shorter or smaller one.")
            out.write(chunk)
    if size == 0:
        raise UploadError(stem, "This file is empty.")
    return dest


def check_clip(path: Path) -> None:
    """The clip opens and its whole-second length fits Seedance (4 to 30 s), as normalize cuts."""
    try:
        seconds = stream_seconds(path, "video")
    except (OSError, ValueError, av.error.FFmpegError):
        raise UploadError("clip", "This clip can't be opened. Export it as mp4.") from None
    low, high = GENERATOR_LIMITS["seedance"]
    if seconds > high + 0.05:
        raise UploadError("clip", f"The clip is {seconds:.1f} s; it can be {high} s at most.")
    if round(seconds * FPS) // FPS < low:  # whole seconds only, like normalize
        raise UploadError(
            "clip", f"The clip is {seconds:.1f} s; it needs at least {low} whole seconds."
        )


def check_photo_file(path: Path) -> None:
    """The same free photo check `charswap prepare` runs (it runs it again)."""
    try:
        check_photo(path, path.with_name("photo-checked.jpg"))
    except FaceError as error:
        raise UploadError("photo", str(error)) from None
````

- [ ] **Step 5: Run the task's tests**

Run: `uv run pytest tests/test_web_app.py -q --no-cov`  
Expected: PASS (81 passed).

- [ ] **Step 6: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1086 passed, 1 deselected; coverage above 96 %
git add src/character_swap/cli.py src/character_swap/web/app.py src/character_swap/web/static/app.js src/character_swap/web/static/viewer.css src/character_swap/web/templates/base.html src/character_swap/web/templates/macros.html src/character_swap/web/templates/new.html src/character_swap/web/uploads.py tests/test_web_app.py pyproject.toml uv.lock
git commit -m "feat: new swap upload with free checks; POSTs need the launch token and our Origin"
````

---

### Task 11: Approve and the prompt editor (in-process, under the job lock)

The two quick actions call the same Python functions the CLI uses (A5), in a worker thread,
holding the same `run.lock` every command takes:

- `POST /jobs/{id}/approve` with the fingerprint step 3 was rendered with:
  `review.decide(approved=True, note="approved in the app", fingerprint=…)`. Files changed
  since → 409 with the "changed since you looked" text and a Reload button.
- `POST /jobs/{id}/prompt` `{action: "save", text}` checks the text like
  `charswap generate` will (`check_placeholders` with WaveSpeed's names, `validate_prompt`,
  not empty) and writes `prompt.txt` (`pipeline.save_prompt`); `{action: "reset"}` writes
  `default_prompt`. Both pin the provider first, as `charswap prompt` does.
- `actionable(job_id)` (409 "Continue this job with the command line." for read-only jobs,
  409 busy while something runs) guards every job POST from here on.

Step 3's **Looks good, approve** starts disabled with "Watch it to the end first." and
unlocks on `wipe:ended`; **Start over** links to `/new`. Step 4's prompt box becomes editable
with Save and Reset.

**Files:**
- Modify: `src/character_swap/pipeline.py`
- Modify: `src/character_swap/web/app.py`
- Modify: `src/character_swap/web/static/app.js`
- Modify: `src/character_swap/web/templates/step-3.html`
- Modify: `src/character_swap/web/templates/step-4.html`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write the tests**

**Replace** in `tests/test_web_app.py`:

````python
    assert f'src="/jobs/{job.id}/files/prepared.mp4"' in text and "What the AI gets" in text


def test_draft_step_shows_the_prompt_read_only_for_now(client, runs_dir, media):
    job = _approved(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "@Video 1" in text and "readonly" in text and "Seedance 2.5" in text
    assert not job.path("prompt.txt").exists()


def test_final_step_offers_the_download(client, runs_dir, media):
````

with:

````python
    assert f'src="/jobs/{job.id}/files/prepared.mp4"' in text and "What the AI gets" in text


def test_draft_step_shows_the_default_prompt_without_writing_it(client, runs_dir, media):
    job = _approved(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "@Video 1" in text and "Seedance 2.5" in text
    assert f'data-post="/jobs/{job.id}/prompt"' in text and 'data-prompt="reset"' in text
    assert not job.path("prompt.txt").exists()
    readonly = make_job(runs_dir, media, "face_mesh")
    decide(readonly, approved=True, note="ok", fingerprint=fingerprint(readonly))
    assert "readonly" in client.get(f"/jobs/{readonly.id}/step/4").text


def test_final_step_offers_the_download(client, runs_dir, media):
````

**Replace** in `tests/test_web_app.py`:

````python
    os.utime(old, (day_ago, day_ago))
    create_app(settings, runner=runner)
    assert not old.exists() and new.exists()  # a prepare still starting is never swept
````

with:

````python
    os.utime(old, (day_ago, day_ago))
    create_app(settings, runner=runner)
    assert not old.exists() and new.exists()  # a prepare still starting is never swept


# ---- approve and the prompt (in-process, under the job lock) --------------------------
def test_approve_uses_the_fingerprint_the_page_was_rendered_with(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    page = client.get(f"/jobs/{job.id}/step/3").text
    assert 'id="approve"' in page and "disabled>Looks good, approve" in page
    assert "Watch it to the end first." in page and 'href="/new"' in page
    seen = page.split('data-fingerprint="')[1].split('"')[0]
    response = _post(client, f"/jobs/{job.id}/approve", json={"fingerprint": seen})
    assert response.json() == {"ok": True, "redirect": f"/jobs/{job.id}/step/4"}
    saved = json.loads(job.path("review.json").read_text())
    assert saved["approved"] is True and saved["note"] == "approved in the app"
    assert "Approved." in client.get(f"/jobs/{job.id}/step/3").text


def test_approve_refuses_a_stale_fingerprint(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    response = _post(client, f"/jobs/{job.id}/approve", json={"fingerprint": "0" * 64})
    assert response.status_code == 409 and response.json()["reload"] is True
    assert "changed since you looked" in response.json()["error"]
    assert not job.path("review.json").exists()
    assert _post(client, f"/jobs/{job.id}/approve", json={}).status_code == 422


def test_actions_are_refused_on_read_only_jobs(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    response = _post(client, f"/jobs/{job.id}/approve", json={"fingerprint": fingerprint(job)})
    assert response.status_code == 409
    assert response.json()["error"] == "Continue this job with the command line."
    assert 'id="approve"' not in client.get(f"/jobs/{job.id}/step/3").text


def test_actions_are_refused_while_the_job_is_busy(client, runner, processes, runs_dir, media):
    job = _app_job(runs_dir, media)
    runner.run(job, ["resume", job.id])
    response = _post(client, f"/jobs/{job.id}/approve", json={"fingerprint": fingerprint(job)})
    assert response.status_code == 409 and "This swap is busy" in response.json()["error"]
    assert 'id="approve"' not in client.get(f"/jobs/{job.id}/step/3").text
    processes.last.finish(0)
    from character_swap.lock import job_lock

    with job_lock(job, "generate"):  # held from "another process" (a terminal)
        response = _post(client, f"/jobs/{job.id}/prompt", json={"action": "reset"})
    assert response.status_code == 409 and "busy (generate since" in response.json()["error"]


def test_prompt_save_checks_the_names_and_writes_prompt_txt(client, runs_dir, media):
    job = _approved(runs_dir, media)
    path = f"/jobs/{job.id}/prompt"
    good = "Put the person in @Image 1 into @Video 1, keep the room."
    response = _post(client, path, json={"action": "save", "text": f"  {good}\n"})
    assert response.json() == {"ok": True, "text": good, "remake": False}
    assert job.path("prompt.txt").read_text() == good
    bad = _post(client, path, json={"action": "save", "text": "Put @image1 into @video1."})
    assert bad.status_code == 422 and "PiAPI's name" in bad.json()["error"]
    assert _post(client, path, json={"action": "save", "text": " "}).status_code == 422
    assert _post(client, path, json={"action": "delete"}).status_code == 422
    assert job.path("prompt.txt").read_text() == good  # refusals change nothing


def test_prompt_reset_writes_the_default_and_says_when_a_new_draft_follows(client, runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    job.mark_stage("draft", fingerprint=fingerprint(job), prompt="The old @Image 1 @Video 1.")
    response = _post(client, f"/jobs/{job.id}/prompt", json={"action": "reset"})
    assert response.json()["remake"] is True and "@Video 1" in response.json()["text"]
    assert job.path("prompt.txt").read_text() == response.json()["text"]


def test_the_app_script_gates_approve_and_saves_the_prompt(client):
    script = client.get("/static/app.js").text
    assert (
        'document.addEventListener("wipe:ended"' in script and "approve.disabled = false" in script
    )
    assert "fingerprint: approve.dataset.fingerprint" in script
    assert "action: button.dataset.prompt" in script
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_web_app.py -q --no-cov`  
Expected: FAIL. 8 failed, 80 passed: the approve and prompt routes answer 405 and step 3 has no approve button.

- [ ] **Step 3: Write the code**

**Replace** in `src/character_swap/pipeline.py`:

````python
    return path


# PiAPI's input names that an old prompt.txt may still use on a WaveSpeed job.
_OLD_NAMES = (
    (re.compile(r"@video1(?!\d)"), "@Video 1"),
````

with:

````python
    return path


def save_prompt(job: Job, text: str) -> Path:
    """Write prompt.txt (the app's Save and Reset). The caller checked the text."""
    path = job.path(PROMPT_FILE)
    _write_atomically(path, lambda part: part.write_text(text, encoding="utf-8"))
    return path


# PiAPI's input names that an old prompt.txt may still use on a WaveSpeed job.
_OLD_NAMES = (
    (re.compile(r"@video1(?!\d)"), "@Video 1"),
````

**Replace** in `src/character_swap/web/app.py`:

````python
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.lock import running_holder
from character_swap.modes import GENERATION_STAGES
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import ID_FILE, Runner
````

with:

````python
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.lock import JobBusy, busy_message, job_lock, running_holder
from character_swap.modes import GENERATION_STAGES
from character_swap.pipeline import (
    default_prompt,
    draft_prompt_changed,
    generator_key,
    pin_provider,
    save_prompt,
    seedance_provider,
)
from character_swap.prompts import PromptError, check_placeholders, validate_prompt
from character_swap.review import ReviewError, decide
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import ID_FILE, Runner
````

**Replace** in `src/character_swap/web/app.py`:

````python
    PAID_RECORD,
    ViewError,
    current_step,
    progress,
    read_state,
    record_status,
````

with:

````python
    PAID_RECORD,
    ViewError,
    current_step,
    is_app_job,
    progress,
    read_state,
    record_status,
````

**Replace** in `src/character_swap/web/app.py`:

````python
RESTARTED = "The app was restarted. Reload the page."
OTHER_SITE = "This request did not come from the app, so nothing was started."
NO_PRICE = "Confirm the price first."
MAX_DESCRIBE = 300


````

with:

````python
RESTARTED = "The app was restarted. Reload the page."
OTHER_SITE = "This request did not come from the app, so nothing was started."
NO_PRICE = "Confirm the price first."
READ_ONLY_POST = "Continue this job with the command line."
APPROVED_NOTE = "approved in the app"
MAX_DESCRIBE = 300


````

**Replace** in `src/character_swap/web/app.py`:

````python
        """
        return runner.alive(job.id) or running_holder(job) is not None

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        page = home_page(runs_dir, current_settings())
````

with:

````python
        """
        return runner.alive(job.id) or running_holder(job) is not None

    def actionable(job_id: str) -> Job:
        """The job, when the app may act on it: 409 for read-only jobs (A3) and busy ones."""
        job = load(job_id)
        try:
            state = read_state(job)
            key = generator_key(job, state)
        except (ViewError, JobError) as exc:
            raise HTTPException(409, str(exc)) from None
        if not is_app_job(state, key):
            raise HTTPException(409, READ_ONLY_POST)
        if running(job):
            raise HTTPException(409, busy_message(running_holder(job)))
        return job

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        page = home_page(runs_dir, current_settings())
````

**Replace** in `src/character_swap/web/app.py`:

````python
            raise
        start = runner.start(incoming, args)  # it deletes `incoming` when the child exits
        return JSONResponse({"start": start}, 202)

    @app.get("/jobs/{job_id}")
    def job_home(request: Request, job_id: str) -> Response:
````

with:

````python
            raise
        start = runner.start(incoming, args)  # it deletes `incoming` when the child exits
        return JSONResponse({"start": start}, 202)

    @app.post("/jobs/{job_id}/approve")
    async def approve(request: Request, job_id: str) -> JSONResponse:
        """Looks good: review.decide with the fingerprint the page was rendered with."""
        check_post(request)
        job = actionable(job_id)
        seen = (await _json(request)).get("fingerprint")
        if not isinstance(seen, str) or not seen:
            raise HTTPException(422, "Reload the page and watch the clip again.")

        def work() -> None:
            with job_lock(job, "review"):  # the same lock every command takes
                decide(job, approved=True, note=APPROVED_NOTE, fingerprint=seen)

        try:
            await run_in_threadpool(work)
        except JobBusy as error:
            raise HTTPException(409, str(error)) from None
        except ReviewError as error:  # "changed since you looked": reload and watch again
            return JSONResponse({"error": str(error), "reload": True}, 409)
        return JSONResponse({"ok": True, "redirect": step_url(job.id, 4)})

    @app.post("/jobs/{job_id}/prompt")
    async def prompt(request: Request, job_id: str) -> JSONResponse:
        """Save (checked like charswap generate checks it) or Reset prompt.txt. Free."""
        check_post(request)
        job = actionable(job_id)
        body = await _json(request)

        def work() -> str:
            with job_lock(job, "prompt"):
                pin_provider(job)  # as `charswap prompt` does, before reading the names
                if body.get("action") == "reset":
                    text = default_prompt(job)
                elif body.get("action") == "save":
                    text = str(body.get("text") or "").strip()
                    if not text:
                        raise PromptError("Write what Seedance should do, or press Reset.")
                    check_placeholders(text, provider=seedance_provider(job), has_secondary=False)
                    validate_prompt(text)
                else:
                    raise PromptError("Choose Save or Reset.")
                save_prompt(job, text)
                return text

        try:
            text = await run_in_threadpool(work)
        except JobBusy as error:
            raise HTTPException(409, str(error)) from None
        except PromptError as error:
            raise HTTPException(422, str(error)) from None
        return JSONResponse({"ok": True, "text": text, "remake": draft_prompt_changed(job)})

    @app.get("/jobs/{job_id}")
    def job_home(request: Request, job_id: str) -> Response:
````

**Replace** in `src/character_swap/web/app.py`:

````python
    return app


def confirmed_usd(value: object) -> float:
    """The price the dialog showed, as the page sent it (A6). UploadError when missing."""
    try:
````

with:

````python
    return app


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def confirmed_usd(value: object) -> float:
    """The price the dialog showed, as the page sent it (A6). UploadError when missing."""
    try:
````

**Replace** in `src/character_swap/web/static/app.js`:

````javascript
    });
  }

  // 5. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
````

with:

````javascript
    });
  }

  // 5. Step 3: approve unlocks only once "what the AI gets" has played to the end here.
  const showError = (text, reload) => {
    const node = document.getElementById("action-error");
    if (!node) return;
    node.textContent = text;
    if (reload) {
      const again = document.createElement("button");
      again.type = "button";
      again.className = "button";
      again.textContent = "Reload";
      again.addEventListener("click", () => location.reload());
      node.append(" ", again);
    }
  };
  const approve = document.getElementById("approve");
  if (approve) {
    document.addEventListener("wipe:ended", () => {
      approve.disabled = false;
      const hint = document.getElementById("approve-hint");
      if (hint) hint.hidden = true;
    });
    approve.addEventListener("click", async () => {
      approve.disabled = true;
      const result = await post(approve.dataset.post, { fingerprint: approve.dataset.fingerprint });
      if (result.ok) {
        location.assign(result.data.redirect);
        return;
      }
      showError(result.data.error || "Could not approve.", result.data.reload);
    });
  }

  // 6. Step 4: save or reset the prompt (free; checked on the server).
  const prompt = document.querySelector("textarea[data-post]");
  if (prompt) {
    document.querySelectorAll("[data-prompt]").forEach((button) =>
      button.addEventListener("click", async () => {
        const status = document.getElementById("prompt-status");
        const result = await post(prompt.dataset.post, {
          action: button.dataset.prompt,
          text: prompt.value,
        });
        if (!result.ok) {
          status.textContent = result.data.error || "Not saved.";
          return;
        }
        location.reload(); // the draft button follows the saved prompt
      })
    );
  }

  // 7. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
````

**Replace** in `src/character_swap/web/templates/step-3.html`:

````html
  <a class="button primary" href="/jobs/{{ page.id }}/step/4">{{ steps.NEXT_DRAFT }}</a>
  {% elif not page.prep_done %}
  <a class="button" href="/jobs/{{ page.id }}/step/2">{{ steps.NEXT_PREPARE }}</a>
  {% endif %}
</div>
````

with:

````html
  <a class="button primary" href="/jobs/{{ page.id }}/step/4">{{ steps.NEXT_DRAFT }}</a>
  {% elif not page.prep_done %}
  <a class="button" href="/jobs/{{ page.id }}/step/2">{{ steps.NEXT_PREPARE }}</a>
  {% elif not page.read_only and not page.running %}
  <button type="button" class="button primary" id="approve" data-post="/jobs/{{ page.id }}/approve" data-fingerprint="{{ page.fingerprint }}" disabled>{{ steps.APPROVE }}</button>
  <a class="button" href="/new">{{ steps.START_OVER }}</a>
  <p class="meta" id="approve-hint">{{ steps.WATCH_FIRST }}</p>
  {% endif %}
</div>
<p class="field-error" id="action-error" role="alert"></p>
````

**Replace** in `src/character_swap/web/templates/step-4.html`:

````html
{% if page.prompt %}
<div class="field">
  <label for="prompt">Prompt</label>
  <textarea id="prompt" class="prompt" rows="8" readonly>{{ page.prompt }}</textarea>
</div>
{% endif %}
{% if page.state == "running" %}
````

with:

````html
{% if page.prompt %}
<div class="field">
  <label for="prompt">Prompt</label>
  {% if page.read_only or page.running %}
  <textarea id="prompt" class="prompt" rows="8" readonly>{{ page.prompt }}</textarea>
  {% else %}
  <textarea id="prompt" class="prompt" rows="8" data-post="/jobs/{{ page.id }}/prompt">{{ page.prompt }}</textarea>
  <div class="actions">
    <button type="button" class="button" data-prompt="save">{{ steps.SAVE }}</button>
    <button type="button" class="button" data-prompt="reset">{{ steps.RESET }}</button>
    <p class="meta" id="prompt-status" role="status"></p>
  </div>
  {% endif %}
</div>
{% endif %}
{% if page.state == "running" %}
````

**Replace** in `src/character_swap/web/templates/step-4.html`:

````html
  <a class="button primary" href="/jobs/{{ page.id }}/step/5">{{ steps.NEXT_FINAL }}</a>
  {% endif %}
</div>
````

with:

````html
  <a class="button primary" href="/jobs/{{ page.id }}/step/5">{{ steps.NEXT_FINAL }}</a>
  {% endif %}
</div>
<p class="field-error" id="action-error" role="alert"></p>
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_web_app.py -q --no-cov`  
Expected: PASS (88 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1093 passed, 1 deselected; coverage above 96 %
git add src/character_swap/pipeline.py src/character_swap/web/app.py src/character_swap/web/static/app.js src/character_swap/web/templates/step-3.html src/character_swap/web/templates/step-4.html tests/test_web_app.py
git commit -m "feat: approve and the prompt editor in the app, under the job lock"
````

---

### Task 12: Paid buttons, the price dialog and "needs your decision" (§4.4)

New `web/actions.py` decides, only when nothing is running, what each step offers:

| Situation | Offer |
|---|---|
| Preparation not finished | **Try again** (`resume`) at `planned_charge(resume)`, or **Continue (no new charge)** |
| A prep stage's Replicate record is a leftover marker or failed | no button; resume's own "delete … to try again (this costs money)" text |
| Step 3, prepared | **Redo the cut-out** (repair, about $0.002), hidden while `rgb-repair/sam-prediction.json` is a marker or a running prediction |
| Draft/final: a saved request that resumes free | **Continue (no new charge)** (posts `confirm_usd` 0, no dialog) |
| Draft/final: an unconfirmed marker, a failed record, an expired result | no button; the CLI's text ("needs your decision") |
| A free refusal (`generate_refusal`) | the message instead of the button |
| A finished draft the CLI would remake (prompt or inputs changed) | **Make another draft**, whose dialog says the old files are kept as `draft-old-*` / `final-old-*` |
| Step 5 | **720p · about $1.76** and **1080p · about $4.40**; once one is started, the other is replaced by the CLI's message |

The paid POSTs (`/jobs/{id}/retry`, `/repair`, `/draft`, `/final`) need `confirm_usd`, run
the free refusals first, and start the command with `--confirmed-usd`. `GET
/api/jobs/{id}/estimate?action=…` gives the dialog its price and question ("Charge about $0.88
on WaveSpeed?") through the same `ask()` Prepare uses; the provider is named only in that
small price line (A11). Paid buttons are
disabled, with a notice, while a key is missing (A4). `/api/jobs/{id}` adds `decision`, and
step pages embed exactly the same keys and values the API returns, so a page never reloads in
a loop.

**Files:**
- Create: `src/character_swap/web/actions.py`
- Modify: `src/character_swap/web/app.py`
- Modify: `src/character_swap/web/static/app.js`
- Modify: `src/character_swap/web/static/viewer.css`
- Modify: `src/character_swap/web/templates/job.html`
- Modify: `src/character_swap/web/templates/macros.html`
- Modify: `src/character_swap/web/templates/step-2.html`
- Modify: `src/character_swap/web/templates/step-3.html`
- Modify: `src/character_swap/web/templates/step-4.html`
- Modify: `src/character_swap/web/templates/step-5.html`
- Test: `tests/test_actions.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write the tests**

**Create** `tests/test_actions.py`:

````python
"""The §4.4 table: what each step offers when nothing is running."""

import json

import pytest

from character_swap.review import decide, fingerprint
from character_swap.web import actions, steps
from viewer_jobs import finish_stage, make_job, write_record


def _approved(runs_dir, media, mode="depth_mesh"):
    job = make_job(runs_dir, media, mode, provider="wavespeed")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    return job


def _drafted(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    job.mark_stage("draft", fingerprint=fingerprint(job), prompt="A @Image 1 @Video 1 prompt.")
    return job


def _offers(job, number):
    return actions.offers(job, job.state, number)


def _buttons(found):
    return [(b.action, b.label, b.usd, b.resolution) for b in found.buttons]


# ---- step 2: preparation not finished --------------------------------------------------
def test_an_unfinished_prepare_offers_try_again_with_its_price(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=3, failed="boom", provider="wavespeed")
    found = _offers(job, 2)
    [(action, label, usd, _res)] = _buttons(found)
    assert (action, label) == ("resume", "Try again") and usd == pytest.approx(0.0438)
    assert found.buttons[0].price == "about $0.04 · Replicate" and found.buttons[0].route == "retry"


def test_a_prepare_with_nothing_left_to_buy_continues_for_free(runs_dir, media):
    job = make_job(
        runs_dir, media, "depth_mesh", done=9, failed="interrupted", provider="wavespeed"
    )
    assert _buttons(_offers(job, 2)) == [("resume", "Continue (no new charge)", None, "")]
    assert _offers(job, 2).buttons[0].price == ""


def test_a_finished_prepare_offers_nothing_but_next(runs_dir, media):
    assert _offers(_approved(runs_dir, media), 2) == actions.Offers()


@pytest.mark.parametrize(
    ("record", "words"),
    [
        ({"status": "creating"}, "may have been charged"),
        ({"id": "p1", "status": "failed", "error": "GPU fell over"}, "GPU fell over"),
    ],
)
def test_a_prep_record_left_behind_needs_your_decision(runs_dir, media, record, words):
    job = make_job(runs_dir, media, "depth_mesh", done=6, failed="boom", provider="wavespeed")
    write_record(job, "sam/sam-prediction.json", record)  # masks: not done
    found = _offers(job, 2)
    assert found.buttons == () and words in found.decision
    assert "sam-prediction.json" in found.decision
    if record["status"] == "failed":
        assert "to try again (this costs money)" in found.decision
    assert actions.decision(job, job.state) == found.decision


# ---- step 3: redo the cut-out ---------------------------------------------------------------
def test_check_offers_redo_the_cut_out_unless_one_may_be_on_its_way(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    [offer] = _offers(job, 3).buttons
    assert (offer.action, offer.label, offer.primary) == ("repair", "Redo the cut-out", False)
    assert offer.usd == pytest.approx(0.002) and offer.price == "about $0.002 · Replicate"
    for status in ("creating", "starting", "processing"):
        write_record(job, "rgb-repair/sam-prediction.json", {"id": "r1", "status": status})
        assert _offers(job, 3).buttons == (), status
    write_record(job, "rgb-repair/sam-prediction.json", {"id": "r1", "status": "succeeded"})
    assert len(_offers(job, 3).buttons) == 1  # a finished earlier repair is redone (confirmed)


# ---- step 4: the draft --------------------------------------------------------------------
def test_draft_offers_make_draft_with_the_price(runs_dir, media):
    job = _approved(runs_dir, media)
    [offer] = _offers(job, 4).buttons
    assert (offer.label, offer.usd, offer.remake) == ("Make draft", pytest.approx(0.88), False)
    assert offer.price == "about $0.88 · WaveSpeed"
    assert actions.question(offer.usd, offer.provider) == "Charge about $0.88 on WaveSpeed?"


def test_draft_needs_approval_first(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    assert _offers(job, 4) == actions.Offers()


def test_a_saved_draft_request_continues_for_free(runs_dir, media):
    job = _approved(runs_dir, media)
    write_record(job, "draft-prediction.json", {"id": "p1", "status": "processing"})
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job))  # what a stopped run leaves
    assert _buttons(_offers(job, 4)) == [("draft", "Continue (no new charge)", None, "")]


def test_an_unconfirmed_draft_needs_your_decision(runs_dir, media):
    job = _approved(runs_dir, media)
    write_record(job, "draft-prediction.json", {"status": "creating"})
    found = _offers(job, 4)
    assert found.buttons == () and "WaveSpeed dashboard" in found.decision
    assert found.decision.startswith("A request for this step was sent but never confirmed")


def test_a_failed_or_expired_draft_needs_your_decision(runs_dir, media):
    job = _approved(runs_dir, media)
    write_record(job, "draft-prediction.json", {"id": "p1", "status": "failed"})
    job.mark_stage("draft", "failed", error="WaveSpeed prediction p1 failed: blurry. Delete it.")
    found = _offers(job, 4)
    assert found.buttons == () and "failed: blurry" in found.decision
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p1", "status": "succeeded"}))
    job.mark_stage("draft", "failed", error="The link expired. Delete the record.", expired=True)
    assert "link expired" in _offers(job, 4).decision
    job.mark_stage("draft", "failed", error="Upload failed: connection reset")
    job.path("draft-prediction.json").unlink()  # failed before anything was bought
    assert _buttons(_offers(job, 4))[0][1] == "Make draft"


def test_a_finished_draft_offers_another_only_when_the_cli_would_remake_it(runs_dir, media):
    job = _drafted(runs_dir, media)
    assert _offers(job, 4) == actions.Offers()  # Next: final
    job.path("prompt.txt").write_text("A different @Image 1 @Video 1 prompt.")
    [offer] = _offers(job, 4).buttons
    assert (offer.label, offer.remake, offer.primary) == ("Make another draft", True, False)
    write_record(job, "final-prediction.json", {"id": "f1", "status": "processing"})
    job.mark_stage("final", "running", fingerprint=fingerprint(job), resolution="720p")
    found = _offers(job, 4)
    assert found.buttons == () and "A final is still in progress" in found.notes[0]


def test_a_refused_draft_shows_the_message_instead_of_the_button(runs_dir, media):
    job = _approved(runs_dir, media)
    job.path("prompt.txt").write_text("Put @image1 into @video1.")  # PiAPI's names
    found = _offers(job, 4)
    assert found.buttons == () and "PiAPI's name" in found.notes[0]


# ---- step 5: the final ------------------------------------------------------------------
def test_final_offers_both_resolutions_with_their_prices(runs_dir, media):
    job = _drafted(runs_dir, media)
    assert _buttons(_offers(job, 5)) == [
        ("final", "720p", pytest.approx(1.76), "720p"),
        ("final", "1080p", pytest.approx(4.40), "1080p"),
    ]


def test_a_started_final_continues_and_hides_the_other_resolution(runs_dir, media):
    job = _drafted(runs_dir, media)
    write_record(job, "final-prediction.json", {"id": "f1", "status": "processing"})
    job.mark_stage("final", "failed", fingerprint=fingerprint(job), resolution="1080p")
    found = _offers(job, 5)
    assert _buttons(found) == [("final", "1080p · Continue (no new charge)", None, "1080p")]
    assert "A 1080p final was already started" in found.notes[0]


def test_a_finished_or_unconfirmed_final(runs_dir, media):
    job = _drafted(runs_dir, media)
    write_record(job, "final-prediction.json", {"status": "creating"})
    found = _offers(job, 5)
    assert found.buttons == () and "never confirmed" in found.decision
    assert actions.decision(job, job.state) == found.decision  # the API says the same
    job.path("final-prediction.json").unlink()
    finish_stage(job, media, "final")
    assert _offers(job, 5) == actions.Offers()


def test_steps_without_offers(runs_dir, media):
    job = _drafted(runs_dir, media)
    assert _offers(job, 1) == actions.Offers()
    assert actions.decision(job, job.state) is None
    assert steps.KEPT_AS_OLD.startswith("The current draft")
````

**Append** to `tests/test_web_app.py`:

````python


# ---- paid actions: the price dialog, then a command that checks the price itself -------
def test_draft_step_shows_the_button_with_its_price_and_the_dialog(client, runs_dir, media):
    job = _approved(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert 'data-action="draft"' in text and "Make draft · about $0.88" in text
    assert "about $0.88 · WaveSpeed" in text and '<dialog id="confirm"' in text
    assert "WaveSpeed" not in text.split("about $0.88 · WaveSpeed")[0]  # A11: only there


def test_estimate_is_the_price_the_command_will_check(client, runs_dir, media):
    job = _approved(runs_dir, media)
    before = job.path("state.json").read_bytes()
    data = client.get(f"/api/jobs/{job.id}/estimate", params={"action": "draft"}).json()
    assert data == {
        "usd": pytest.approx(0.88),
        "charge": True,
        "price": "about $0.88 · WaveSpeed",
        "question": "Charge about $0.88 on WaveSpeed?",
    }
    repair = client.get(f"/api/jobs/{job.id}/estimate", params={"action": "repair"}).json()
    assert repair["question"] == "Charge about $0.002 on Replicate?"
    resume = client.get(f"/api/jobs/{job.id}/estimate", params={"action": "resume"}).json()
    assert resume["charge"] is False and resume["usd"] == 0
    final = client.get(f"/api/jobs/{job.id}/estimate", params={"action": "final"})
    assert final.status_code == 409 and "Make and watch the draft first" in final.json()["error"]
    assert client.get(f"/api/jobs/{job.id}/estimate", params={"action": "x"}).status_code == 404
    assert job.path("state.json").read_bytes() == before  # a GET never writes


def test_a_paid_post_starts_the_command_with_the_confirmed_price(
    client, processes, runs_dir, media
):
    job = _approved(runs_dir, media)
    response = _post(client, f"/jobs/{job.id}/draft", json={"confirm_usd": 0.88})
    assert response.status_code == 202
    assert response.json() == {"ok": True, "redirect": f"/jobs/{job.id}/step/4"}
    assert processes.last.args == [
        "charswap",
        "generate",
        job.id,
        "--draft",
        "--confirmed-usd",
        "0.880000",
    ]
    again = _post(client, f"/jobs/{job.id}/draft", json={"confirm_usd": 0.88})
    assert again.status_code == 409 and "This swap is busy" in again.json()["error"]
    assert len(processes.started) == 1
    assert 'data-action="draft"' not in client.get(f"/jobs/{job.id}/step/4").text  # running


def test_a_paid_post_needs_the_confirmed_price(client, processes, runs_dir, media):
    job = _approved(runs_dir, media)
    for body in ({}, {"confirm_usd": "abc"}, {"confirm_usd": -1}):
        response = _post(client, f"/jobs/{job.id}/draft", json=body)
        assert (
            response.status_code == 422 and response.json()["error"] == "Confirm the price first."
        )
    assert processes.started == []


def test_a_free_refusal_shows_at_once_and_starts_nothing(client, processes, runs_dir, media):
    job = _app_job(runs_dir, media)  # not approved
    response = _post(client, f"/jobs/{job.id}/draft", json={"confirm_usd": 0.88})
    assert response.status_code == 409 and "Review required" in response.json()["error"]
    approved = _approved(runs_dir, media)
    final = _post(
        client, f"/jobs/{approved.id}/final", json={"confirm_usd": 1.76, "resolution": "4k"}
    )
    assert final.status_code == 422 and "720p or 1080p" in final.json()["error"]
    assert processes.started == []


def test_final_repair_and_retry_commands(client, processes, runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    job.mark_stage("draft", fingerprint=fingerprint(job), prompt="The @Image 1 @Video 1 prompt.")
    response = _post(
        client, f"/jobs/{job.id}/final", json={"confirm_usd": 4.4, "resolution": "1080p"}
    )
    assert response.json()["redirect"] == f"/jobs/{job.id}/step/5"
    assert processes.last.args[1:] == [
        "generate",
        job.id,
        "--final",
        "--resolution",
        "1080p",
        "--confirmed-usd",
        "4.400000",
    ]
    processes.last.finish(0)
    other = _app_job(runs_dir, media)
    _post(client, f"/jobs/{other.id}/repair", json={"confirm_usd": 0.002})
    assert processes.last.args[1:] == ["repair", other.id, "--confirmed-usd", "0.002000"]
    processes.last.finish(0)
    _post(client, f"/jobs/{other.id}/retry", json={"confirm_usd": 0})
    assert processes.last.args[1:] == ["resume", other.id, "--confirmed-usd", "0.000000"]


def test_repair_is_refused_while_a_cut_may_be_on_its_way(client, processes, runs_dir, media):
    from viewer_jobs import write_record

    job = _app_job(runs_dir, media)
    write_record(job, "rgb-repair/sam-prediction.json", {"status": "creating"})
    response = _post(client, f"/jobs/{job.id}/repair", json={"confirm_usd": 0.002})
    assert response.status_code == 409 and "may still be on its way" in response.json()["error"]
    assert 'data-action="repair"' not in client.get(f"/jobs/{job.id}/step/3").text
    assert processes.started == []


@pytest.mark.parametrize("route", ["retry", "repair", "draft", "final", "prompt", "approve"])
def test_every_post_is_refused_on_a_read_only_job(client, processes, runs_dir, media, route):
    job = make_job(runs_dir, media, "depth", provider="piapi")
    body = {"confirm_usd": 1, "resolution": "720p", "action": "reset", "fingerprint": "x"}
    response = _post(client, f"/jobs/{job.id}/{route}", json=body)
    assert response.status_code == 409
    assert response.json()["error"] == "Continue this job with the command line."
    assert processes.started == []


def test_paid_buttons_are_off_while_a_key_is_missing(tmp_path, runs_dir, runner, processes, media):
    settings = Settings.load(
        root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir), "REPLICATE_API_TOKEN": "r"}
    )
    client = TestClient(create_app(settings, runner=runner), base_url="http://127.0.0.1")
    job = _approved(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "<code>WAVESPEED_API_KEY</code> is missing, so paid buttons are off" in text
    assert 'data-free="false" disabled>Make draft' in text
    response = _post(client, f"/jobs/{job.id}/draft", json={"confirm_usd": 0.88})
    assert (
        response.status_code == 409 and "Add WAVESPEED_API_KEY to .env" in response.json()["error"]
    )
    assert processes.started == []


def test_needs_your_decision_shows_the_cli_text_and_no_button(client, runs_dir, media):
    from viewer_jobs import write_record

    job = _approved(runs_dir, media)
    write_record(job, "draft-prediction.json", {"status": "creating"})
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "Needs your decision." in text and "WaveSpeed dashboard" in text
    assert 'data-action="draft"' not in text
    api = client.get(f"/api/jobs/{job.id}").json()
    shown = json.loads(text.split('data-progress="')[1].split('"')[0].replace("&#34;", '"'))
    assert shown["decision"] == api["decision"] and "never confirmed" in api["decision"]


def test_the_page_and_the_api_agree_so_the_page_never_reloads_in_a_loop(client, runs_dir, media):
    job = _approved(runs_dir, media)
    for number in (2, 3, 4):
        text = client.get(f"/jobs/{job.id}/step/{number}").text
        shown = json.loads(text.split('data-progress="')[1].split('"')[0].replace("&#34;", '"'))
        api = client.get(f"/api/jobs/{job.id}").json()
        assert {key: api[key] for key in shown} == shown


def test_the_app_script_asks_before_charging(client):
    script = client.get("/static/app.js").text
    assert "/estimate?" in script and "dialog.showModal()" in script
    assert "confirm_usd: usd" in script and 'button.dataset.free !== "true"' in script
````

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_actions.py tests/test_web_app.py -q --no-cov`  
Expected: FAIL. `ImportError: cannot import name 'actions' from 'character_swap.web'` (a collection error).

- [ ] **Step 3: Write the code**

**Create** `src/character_swap/web/actions.py`:

````python
"""What a step offers when nothing is running: buttons, or "needs your decision" (spec §4.4).

Read-only: it never deletes or edits a provider record, claim marker or result (A15). The
situations where a click could buy something twice show the CLI's own instructions instead
of a button. Every price comes from pipeline.planned_charge, the same function the command
checks --confirmed-usd against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from character_swap.costs import usd_text
from character_swap.errors import CharSwapError
from character_swap.jobs import Job
from character_swap.modes import PROVIDER_NAMES
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
    UNCONFIRMED,
    UNCONFIRMED_INTRO,
    generate_refusal,
    generation_files,
    generator_key,
    planned_charge,
)
from character_swap.providers.replicate import unconfirmed_message as replicate_unconfirmed
from character_swap.web import steps
from character_swap.web.view_model import (
    PAID_RECORD,
    RGB_REPAIR_RECORD,
    STAGES_BY_MODE,
    prep_done,
    record_status,
    saved_review,
    stage_status,
    tilde,
)

REPLICATE = "Replicate"
ROUTES = {"resume": "retry", "repair": "repair", "draft": "draft", "final": "final"}
# A repair deletes rgb-repair/ and buys a new cut: never while that cut may still exist.
REPAIR_IN_FLIGHT = ("creating", "starting", "processing")


@dataclass(frozen=True)
class Offer:
    action: str  # "resume" | "repair" | "draft" | "final"
    label: str
    usd: float | None  # None: no new charge (no price dialog; it posts confirm_usd 0)
    provider: str
    resolution: str = ""
    remake: bool = False  # a new draft that moves the old one aside (said in the dialog)
    primary: bool = True

    @property
    def route(self) -> str:
        return ROUTES[self.action]

    @property
    def price(self) -> str:
        """The small price line, the only place a provider is named (A11)."""
        return "" if self.usd is None else f"about {usd_text(self.usd)} · {self.provider}"


@dataclass(frozen=True)
class Offers:
    buttons: tuple[Offer, ...] = ()
    decision: str | None = None  # needs your decision: the CLI's text, no button
    notes: tuple[str, ...] = ()  # a free refusal: the message instead of the button


def question(usd: float, provider: str) -> str:
    return steps.CHARGE.format(usd=usd_text(usd), provider=provider)


def _load(job: Job, name: str) -> dict[str, Any]:
    try:
        data = json.loads(job.path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def prep_decision(job: Job, state: dict[str, Any]) -> str | None:
    """A prep stage whose Replicate record is a leftover marker or failed: resume's own text.

    (Try again would only fail the same way each time.)
    """
    for stage in STAGES_BY_MODE[state["mode"]]:
        if stage not in PAID_RECORD or stage_status(state, stage) == "done":
            continue
        record = job.path(PAID_RECORD[stage])
        found = _load(job, PAID_RECORD[stage])
        status = found.get("status")
        if status == "creating":
            return replicate_unconfirmed(record)
        if status in ("failed", "canceled"):
            reason = found.get("error") or "no reason given"
            return (
                f"Earlier prediction {found.get('id')} {status}: {reason}. "
                f"Delete {record} to try again (this costs money)."
            )
    return None


def stage_decision(job: Job, state: dict[str, Any], stage: str) -> str | None:
    """A draft/final that needs your decision: unconfirmed, failed at the provider, expired."""
    record_name = generation_files(stage)[0]
    record = job.path(record_name)
    status = record_status(job, record_name)
    if status == "creating":
        return f"{UNCONFIRMED_INTRO}\n{UNCONFIRMED[generator_key(job, state)](record)}"
    entry = state["stages"].get(stage)
    entry = entry if isinstance(entry, dict) else {}
    if status == "failed" or (entry.get("status") == "failed" and entry.get("expired") is True):
        # The command's own message, saved when the stage failed ("delete ... and run the
        # same command again" is a new charge: done from the command line).
        error = entry.get("error") if entry.get("status") == "failed" else None
        return str(error or f"The provider says this {stage} failed. See {record}.")
    return None


def decision(job: Job, state: dict[str, Any]) -> str | None:
    """The job's "needs your decision" text, cheap (no hashing): for /api/jobs/{id}."""
    if not prep_done(state):
        text = prep_decision(job, state)
    else:
        text = stage_decision(job, state, "final") or stage_decision(job, state, "draft")
    return tilde(text) if text else None


def _charge(job: Job, action: str, resolution: str = "") -> tuple[float | None, str | None]:
    """(usd or None for no new charge, a refusal) for one button."""
    try:
        charge = planned_charge(job, action, resolution)
    except CharSwapError as error:
        return None, str(error)
    return (charge.total if charge else None), None


def _prepare_offers(job: Job, state: dict[str, Any]) -> Offers:
    if prep_done(state):
        return Offers()
    text = prep_decision(job, state)
    if text:
        return Offers(decision=tilde(text))
    usd, _refusal = _charge(job, "resume")
    label = steps.CONTINUE_FREE if usd is None else steps.TRY_AGAIN
    return Offers(buttons=(Offer("resume", label, usd, REPLICATE),))


def _check_offers(job: Job, state: dict[str, Any]) -> Offers:
    if not prep_done(state) or record_status(job, RGB_REPAIR_RECORD) in REPAIR_IN_FLIGHT:
        return Offers()  # Redo the cut-out is hidden while its last cut may still exist
    usd, _refusal = _charge(job, "repair")
    return Offers(buttons=(Offer("repair", steps.REPAIR, usd, REPLICATE, primary=False),))


def _draft_offers(job: Job, state: dict[str, Any], key: str) -> Offers:
    if not prep_done(state) or not saved_review(job).get("approved"):
        return Offers()
    text = stage_decision(job, state, "draft")
    if text:
        return Offers(decision=tilde(text))
    refusal = generate_refusal(job, "draft")
    if refusal:
        return Offers(notes=(tilde(refusal),))
    usd, refusal = _charge(job, "draft")
    if refusal:
        return Offers(notes=(tilde(refusal),))
    done = stage_status(state, "draft") == "done"
    if done and usd is None:
        return Offers()  # nothing to remake: Next: final
    provider = PROVIDER_NAMES[key]
    if done:
        offer = Offer("draft", steps.ANOTHER_DRAFT, usd, provider, remake=True, primary=False)
    elif usd is None:
        offer = Offer("draft", steps.CONTINUE_FREE, None, provider)
    else:
        offer = Offer("draft", steps.MAKE_DRAFT, usd, provider)
    return Offers(buttons=(offer,))


def _final_offers(job: Job, state: dict[str, Any], key: str) -> Offers:
    if stage_status(state, "draft") != "done" or stage_status(state, "final") == "done":
        return Offers()
    text = stage_decision(job, state, "final")
    if text:
        return Offers(decision=tilde(text))
    buttons, notes = [], []
    for resolution in FINAL_RESOLUTIONS[key]:
        refusal = generate_refusal(job, "final", resolution)
        usd = None
        if not refusal:
            usd, refusal = _charge(job, "final", resolution)
        if refusal:
            if refusal not in notes:
                notes.append(refusal)  # e.g. the other resolution once one was started
            continue
        label = f"{resolution} · {steps.CONTINUE_FREE}" if usd is None else resolution
        buttons.append(Offer("final", label, usd, PROVIDER_NAMES[key], resolution=resolution))
    return Offers(buttons=tuple(buttons), notes=tuple(tilde(note) for note in notes))


def offers(job: Job, state: dict[str, Any], number: int) -> Offers:
    """What step `number` offers. Call it only when nothing is running (§4.4)."""
    key = generator_key(job, state)
    if number == 2:
        return _prepare_offers(job, state)
    if number == 3:
        return _check_offers(job, state)
    if number == 4:
        return _draft_offers(job, state, key)
    if number == 5:
        return _final_offers(job, state, key)
    return Offers()
````

**Replace** in `src/character_swap/web/app.py`:

````python

from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.lock import JobBusy, busy_message, job_lock, running_holder
from character_swap.modes import GENERATION_STAGES
from character_swap.pipeline import (
    default_prompt,
    draft_prompt_changed,
    generator_key,
    pin_provider,
    save_prompt,
    seedance_provider,
)
from character_swap.prompts import PromptError, check_placeholders, validate_prompt
from character_swap.review import ReviewError, decide
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import ID_FILE, Runner
from character_swap.web.uploads import (
    CLIP_SUFFIXES,
    INCOMING,
````

with:

````python

from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.errors import CharSwapError
from character_swap.jobs import Job, JobError
from character_swap.lock import JobBusy, busy_message, job_lock, running_holder
from character_swap.modes import GENERATION_STAGES
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
    default_prompt,
    draft_prompt_changed,
    generate_refusal,
    generator_key,
    pin_provider,
    planned_charge,
    save_prompt,
    seedance_provider,
)
from character_swap.prompts import PromptError, check_placeholders, validate_prompt
from character_swap.review import ReviewError, decide
from character_swap.web import steps
from character_swap.web.actions import REPAIR_IN_FLIGHT, REPLICATE, Offers, decision, offers
from character_swap.web.actions import question as price_question
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.runner import ID_FILE, Runner, RunnerBusy
from character_swap.web.uploads import (
    CLIP_SUFFIXES,
    INCOMING,
````

**Replace** in `src/character_swap/web/app.py`:

````python
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    PAID_RECORD,
    ViewError,
    current_step,
    is_app_job,
````

with:

````python
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    PAID_RECORD,
    RGB_REPAIR_RECORD,
    ViewError,
    current_step,
    is_app_job,
````

**Replace** in `src/character_swap/web/app.py`:

````python
NO_PRICE = "Confirm the price first."
READ_ONLY_POST = "Continue this job with the command line."
APPROVED_NOTE = "approved in the app"
MAX_DESCRIBE = 300


````

with:

````python
NO_PRICE = "Confirm the price first."
READ_ONLY_POST = "Continue this job with the command line."
APPROVED_NOTE = "approved in the app"
PAID_ACTIONS = ("resume", "repair", "draft", "final")
NEXT_STEP = {"resume": 2, "repair": 2, "draft": 4, "final": 5}  # where each one is watched
REPAIRING = "A cut-out redo may still be on its way. Wait for it, or check charswap status."
MAX_DESCRIBE = 300


````

**Replace** in `src/character_swap/web/app.py`:

````python
        start = runner.start(incoming, args)  # it deletes `incoming` when the child exits
        return JSONResponse({"start": start}, 202)

    @app.post("/jobs/{job_id}/approve")
    async def approve(request: Request, job_id: str) -> JSONResponse:
        """Looks good: review.decide with the fingerprint the page was rendered with."""
````

with:

````python
        start = runner.start(incoming, args)  # it deletes `incoming` when the child exits
        return JSONResponse({"start": start}, 202)

    @app.get("/api/jobs/{job_id}/estimate")
    def estimate(job_id: str, action: str, resolution: str = "") -> JSONResponse:
        """What the price dialog shows: pipeline.planned_charge, as the command checks it."""
        job = load(job_id)
        if action not in PAID_ACTIONS:
            raise HTTPException(404, NOT_FOUND)
        if action in GENERATION_STAGES:
            refusal = generate_refusal(job, action, resolution)
            if refusal:
                raise HTTPException(409, tilde(refusal))
        try:
            charge = planned_charge(job, action, resolution)
        except CharSwapError as error:
            raise HTTPException(409, tilde(str(error))) from None
        usd = charge.total if charge else 0.0
        provider = REPLICATE if action in ("resume", "repair") else "WaveSpeed"
        return JSONResponse(
            {
                "usd": usd,
                "charge": charge is not None,
                "price": f"about {usd_text(usd)} · {provider}",
                "question": price_question(usd, provider),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def start_paid(request: Request, job_id: str, action: str) -> JSONResponse:
        """Start a paid command with the price the dialog showed (the child checks it)."""
        check_post(request)
        job = actionable(job_id)
        body = await _json(request)
        try:
            usd = confirmed_usd(body.get("confirm_usd"))
        except UploadError:
            raise HTTPException(422, NO_PRICE) from None
        missing = missing_keys(current_settings())
        if missing:
            names = " and ".join(key.name for key in missing)
            raise HTTPException(409, f"Add {names} to .env first.")
        args: list[str]
        if action in GENERATION_STAGES:
            resolution = str(body.get("resolution") or "") if action == "final" else ""
            if action == "final" and resolution not in FINAL_RESOLUTIONS["seedance:wavespeed"]:
                raise HTTPException(422, "Choose 720p or 1080p.")
            refusal = generate_refusal(job, action, resolution)  # free, shown at once
            if refusal:
                raise HTTPException(409, tilde(refusal))
            args = ["generate", job.id, f"--{action}"]
            args += ["--resolution", resolution] if resolution else []
        elif action == "repair":
            if record_status(job, RGB_REPAIR_RECORD) in REPAIR_IN_FLIGHT:
                raise HTTPException(409, REPAIRING)
            args = ["repair", job.id]
        else:
            args = ["resume", job.id]
        try:
            runner.run(job, [*args, "--confirmed-usd", f"{usd:.6f}"])
        except RunnerBusy:
            raise HTTPException(409, busy_message(running_holder(job))) from None
        return JSONResponse({"ok": True, "redirect": step_url(job.id, NEXT_STEP[action])}, 202)

    @app.post("/jobs/{job_id}/retry")
    async def retry(request: Request, job_id: str) -> JSONResponse:
        return await start_paid(request, job_id, "resume")

    @app.post("/jobs/{job_id}/repair")
    async def repair(request: Request, job_id: str) -> JSONResponse:
        return await start_paid(request, job_id, "repair")

    @app.post("/jobs/{job_id}/draft")
    async def draft(request: Request, job_id: str) -> JSONResponse:
        return await start_paid(request, job_id, "draft")

    @app.post("/jobs/{job_id}/final")
    async def final(request: Request, job_id: str) -> JSONResponse:
        return await start_paid(request, job_id, "final")

    @app.post("/jobs/{job_id}/approve")
    async def approve(request: Request, job_id: str) -> JSONResponse:
        """Looks good: review.decide with the fingerprint the page was rendered with."""
````

**Replace** in `src/character_swap/web/app.py`:

````python
        if not 1 <= number <= 5:
            raise HTTPException(404, NOT_FOUND)
        try:
            reachable = current_step(job, read_state(job))
            if number > reachable:  # a later step is not open yet
                return RedirectResponse(step_url(job.id, reachable), status_code=303)
            page = job_page(
````

with:

````python
        if not 1 <= number <= 5:
            raise HTTPException(404, NOT_FOUND)
        try:
            state = read_state(job)
            reachable = current_step(job, state)
            if number > reachable:  # a later step is not open yet
                return RedirectResponse(step_url(job.id, reachable), status_code=303)
            page = job_page(
````

**Replace** in `src/character_swap/web/app.py`:

````python
            )
        except ViewError as exc:
            return damaged(request, job_id, exc)
        shown = {
            "stages": page.progress["stages"],
            "generation": page.progress["generation"],
            "running": page.running,
            "failure": page.failure,
        }
        context = {"page": page, "bar": page.bar, "progress_json": json.dumps(shown)}
        return templates.TemplateResponse(request, "job.html", context)

    @app.get("/jobs/{job_id}/files/{name:path}")
````

with:

````python
            )
        except ViewError as exc:
            return damaged(request, job_id, exc)
        found = Offers()
        if not page.read_only and not page.running:  # §4.4 applies only when nothing runs
            found = offers(job, state, number)
        shown = {  # the same values /api/jobs/{id} returns: the page reloads when one changes
            "stages": page.progress["stages"],
            "generation": page.progress["generation"],
            "running": page.running,
            "failure": page.failure,
            "decision": None if page.running else decision(job, state),
        }
        context = {
            "page": page,
            "bar": page.bar,
            "offers": found,
            "missing": missing_keys(current_settings()),
            "progress_json": json.dumps(shown),
        }
        return templates.TemplateResponse(request, "job.html", context)

    @app.get("/jobs/{job_id}/files/{name:path}")
````

**Replace** in `src/character_swap/web/app.py`:

````python
            return JSONResponse({"error": str(exc)}, status_code=500)
        data = progress(state)  # cheap: no hashing, no review recomputed
        holder = running_holder(job)
        decision = saved_review(job)
        failure = runner.failure(job)
        data.update(
            running=runner.alive(job.id) or holder is not None,
            holder={"command": holder.get("command"), "started_at": holder.get("started_at")}
            if holder
            else None,
            review={
                "approved": bool(decision.get("approved")),
                "fingerprint": str(decision.get("fingerprint") or ""),
            },
            records={stage: record_status(job, PAID_RECORD[stage]) for stage in GENERATION_STAGES},
            failure=tilde(failure) if failure else None,
        )
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

````

with:

````python
            return JSONResponse({"error": str(exc)}, status_code=500)
        data = progress(state)  # cheap: no hashing, no review recomputed
        holder = running_holder(job)
        reviewed = saved_review(job)
        failure = runner.failure(job)
        busy = runner.alive(job.id) or holder is not None
        data.update(
            running=busy,
            holder={"command": holder.get("command"), "started_at": holder.get("started_at")}
            if holder
            else None,
            review={
                "approved": bool(reviewed.get("approved")),
                "fingerprint": str(reviewed.get("fingerprint") or ""),
            },
            records={stage: record_status(job, PAID_RECORD[stage]) for stage in GENERATION_STAGES},
            failure=tilde(failure) if failure else None,
            decision=None if busy else decision(job, state),  # §4.4: only when idle
        )
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

````

**Replace** in `src/character_swap/web/static/app.js`:

````javascript
    );
  }

  // 7. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
````

with:

````javascript
    );
  }

  // 7. Paid buttons: the price dialog first (a fresh estimate), then the POST with that
  //    price. The command started on the server refuses unless it still costs exactly that.
  document.querySelectorAll("button[data-action]").forEach((button) =>
    button.addEventListener("click", async () => {
      const main = document.querySelector("main[data-job-id]");
      const resolution = button.dataset.resolution || "";
      let usd = 0; // "Continue (no new charge)": the command refuses any charge
      if (button.dataset.free !== "true") {
        const query = new URLSearchParams({ action: button.dataset.action, resolution });
        const response = await fetch(`/api/jobs/${main.dataset.jobId}/estimate?${query}`, {
          cache: "no-store",
        }).catch(() => null);
        const data = response ? await response.json().catch(() => ({})) : {};
        if (!response || !response.ok) {
          showError(data.error || "Could not get the price.");
          return;
        }
        if (!(await ask(data.question, button.dataset.note))) return;
        usd = data.usd;
      }
      button.disabled = true;
      const result = await post(button.dataset.post, { confirm_usd: usd, resolution });
      if (result.ok) {
        location.assign(result.data.redirect);
        return;
      }
      button.disabled = false;
      showError(result.data.error || "Could not start.");
    })
  );

  // 8. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
````

**Replace** in `src/character_swap/web/static/viewer.css`:

````css

/* paid buttons and the price dialog */
.offer { display: inline-flex; flex-direction: column; gap: var(--space-1); }
.dialog {
  border: 0; border-radius: var(--radius); padding: var(--space-6);
  background: var(--surface); color: var(--text); box-shadow: var(--shadow); max-width: 28rem;
````

with:

````css

/* paid buttons and the price dialog */
.offer { display: inline-flex; flex-direction: column; gap: var(--space-1); }
.decision .log { margin-top: var(--space-2); }
.dialog {
  border: 0; border-radius: var(--radius); padding: var(--space-6);
  background: var(--surface); color: var(--text); box-shadow: var(--shadow); max-width: 28rem;
````

**Replace** in `src/character_swap/web/templates/job.html`:

````html
{% extends "base.html" %}
{% from "macros.html" import tags %}
{% block title %}Step {{ page.step.number }} · {{ page.step.title }}{% endblock %}
{% block body %}
<main id="main" class="page" data-job-id="{{ page.id }}" data-poll="{{ page.progress.poll }}" data-progress="{{ progress_json }}">
````

with:

````html
{% extends "base.html" %}
{% from "macros.html" import tags, offer_list, confirm_dialog %}
{% block title %}Step {{ page.step.number }} · {{ page.step.title }}{% endblock %}
{% block body %}
<main id="main" class="page" data-job-id="{{ page.id }}" data-poll="{{ page.progress.poll }}" data-progress="{{ progress_json }}">
````

**Replace** in `src/character_swap/web/templates/job.html`:

````html
  {% if page.error %}
  <div class="error" role="alert"><p>{{ page.error }}</p></div>
  {% endif %}
  {% include "step-%d.html" % page.step.number %}
</main>
{% endblock %}
````

with:

````html
  {% if page.error %}
  <div class="error" role="alert"><p>{{ page.error }}</p></div>
  {% endif %}
  {% if missing and offers.buttons %}
  <div class="notice" role="alert">
    {% for key in missing %}
    <p><code>{{ key.name }}</code> is missing, so paid buttons are off. Add it to the <code>.env</code> file, then reload. Get it at {{ key.where }}.</p>
    {% endfor %}
  </div>
  {% endif %}
  {% include "step-%d.html" % page.step.number %}
  <p class="field-error" id="action-error" role="alert"></p>
  {{ confirm_dialog() }}
</main>
{% endblock %}
````

**Append** to `src/character_swap/web/templates/macros.html`:

````html

{% macro offer_list(offers, job_id, missing) %}
{% if offers.decision %}
<div class="notice decision" role="alert">
  <p><strong>{{ steps.NEEDS_DECISION }}</strong> {{ steps.DECISION_HOW }}</p>
  <pre class="log">{{ offers.decision }}</pre>
</div>
{% endif %}
{% for note in offers.notes %}
<p class="notice info">{{ note }}</p>
{% endfor %}
{% if offers.buttons %}
<div class="actions offers">
  {% for offer in offers.buttons %}
  <span class="offer">
    <button type="button" class="button{% if offer.primary %} primary{% endif %}" data-action="{{ offer.action }}" data-post="/jobs/{{ job_id }}/{{ offer.route }}" data-resolution="{{ offer.resolution }}" data-free="{{ 'true' if offer.usd is none else 'false' }}"{% if offer.remake %} data-note="{{ steps.KEPT_AS_OLD }}"{% endif %}{% if missing %} disabled{% endif %}>{{ offer.label }}{% if offer.usd is not none %} · about {{ money(offer.usd) }}{% endif %}</button>
    {% if offer.price %}<span class="meta">{{ offer.price }}</span>{% endif %}
  </span>
  {% endfor %}
</div>
{% endif %}
{% endmacro %}
````

**Replace** in `src/character_swap/web/templates/step-2.html`:

````html
{% from "macros.html" import tags, wipe, placeholder %}
{% if page.tabs %}
<nav class="tabs" aria-label="What preparing made">
  {% for tab in page.tabs %}
````

with:

````html
{% from "macros.html" import tags, wipe, placeholder, offer_list %}
{% if page.tabs %}
<nav class="tabs" aria-label="What preparing made">
  {% for tab in page.tabs %}
````

**Replace** in `src/character_swap/web/templates/step-2.html`:

````html
<p class="line">{{ steps.DIRECT_PREPARE }}</p>
{% if page.original %}<video controls preload="metadata" playsinline src="{{ page.original }}"></video>{% endif %}
{% endif %}
<div class="actions">
  {% if page.prep_done %}
  <a class="button primary" href="/jobs/{{ page.id }}/step/3">{{ steps.NEXT_CHECK }}</a>
````

with:

````html
<p class="line">{{ steps.DIRECT_PREPARE }}</p>
{% if page.original %}<video controls preload="metadata" playsinline src="{{ page.original }}"></video>{% endif %}
{% endif %}
{{ offer_list(offers, page.id, missing) }}
<div class="actions">
  {% if page.prep_done %}
  <a class="button primary" href="/jobs/{{ page.id }}/step/3">{{ steps.NEXT_CHECK }}</a>
````

**Replace** in `src/character_swap/web/templates/step-3.html`:

````html
{% from "macros.html" import wipe, placeholder %}
{% if page.wipe %}
{{ wipe(page.wipe) }}
{% else %}
````

with:

````html
{% from "macros.html" import wipe, placeholder, offer_list %}
{% if page.wipe %}
{{ wipe(page.wipe) }}
{% else %}
````

**Replace** in `src/character_swap/web/templates/step-3.html`:

````html
  <p class="meta" id="approve-hint">{{ steps.WATCH_FIRST }}</p>
  {% endif %}
</div>
<p class="field-error" id="action-error" role="alert"></p>
````

with:

````html
  <p class="meta" id="approve-hint">{{ steps.WATCH_FIRST }}</p>
  {% endif %}
</div>
{% if not page.approved %}{{ offer_list(offers, page.id, missing) }}{% endif %}
````

**Replace** in `src/character_swap/web/templates/step-4.html`:

````html
{% from "macros.html" import wipe, placeholder %}
{% if page.prompt %}
<div class="field">
  <label for="prompt">Prompt</label>
````

with:

````html
{% from "macros.html" import wipe, placeholder, offer_list %}
{% if page.prompt %}
<div class="field">
  <label for="prompt">Prompt</label>
````

**Replace** in `src/character_swap/web/templates/step-4.html`:

````html
{% else %}
{{ placeholder(page.state) }}
{% endif %}
<div class="actions">
  {% if page.wipe and not page.stale %}
  <a class="button primary" href="/jobs/{{ page.id }}/step/5">{{ steps.NEXT_FINAL }}</a>
  {% endif %}
</div>
<p class="field-error" id="action-error" role="alert"></p>
````

with:

````html
{% else %}
{{ placeholder(page.state) }}
{% endif %}
{{ offer_list(offers, page.id, missing) }}
<div class="actions">
  {% if page.wipe and not page.stale %}
  <a class="button primary" href="/jobs/{{ page.id }}/step/5">{{ steps.NEXT_FINAL }}</a>
  {% endif %}
</div>
````

**Replace the whole file** `src/character_swap/web/templates/step-5.html` with:

````html
{% from "macros.html" import wipe, placeholder, offer_list %}
{% if page.state == "running" %}
<div class="running" role="status">
  <span class="spinner" aria-hidden="true"></span> Making the {{ page.resolution }} final <span class="elapsed" data-started="{{ page.started_at }}"></span>
  <p class="meta">{{ steps.DRAFT_TIME }}</p>
</div>
{% elif page.wipe %}
{{ wipe(page.wipe) }}
<div class="actions">
  <a class="button primary" href="{{ page.download }}" download="final-{{ page.short_id }}.mp4">{{ steps.DOWNLOAD }}</a>
</div>
{% else %}
{{ placeholder(page.state) }}
{{ offer_list(offers, page.id, missing) }}
{% endif %}
<p class="meta total">Spent on this swap: <strong>{{ money(page.spent_usd) }}</strong> · estimate</p>
````

- [ ] **Step 4: Run the task's tests**

Run: `uv run pytest tests/test_actions.py tests/test_web_app.py -q --no-cov`  
Expected: PASS (122 passed).

- [ ] **Step 5: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1127 passed, 1 deselected; coverage above 96 %
git add src/character_swap/web/actions.py src/character_swap/web/app.py src/character_swap/web/static/app.js src/character_swap/web/static/viewer.css src/character_swap/web/templates/job.html src/character_swap/web/templates/macros.html src/character_swap/web/templates/step-2.html src/character_swap/web/templates/step-3.html src/character_swap/web/templates/step-4.html src/character_swap/web/templates/step-5.html tests/test_actions.py tests/test_web_app.py
git commit -m "feat: paid buttons with a price dialog; needs-your-decision instead of a button"
````

---

### Task 13: One full click-through on the real CLI (fake providers)

§7 integration: `tests/fake_cli.py` runs the real `cli.main` in a child process with a
`services_factory` built from `tests/fakes.py` (it puts `tests/` on `sys.path`, stubs
`pipeline.pitch_vocals` like the `no_rubberband` fixture, and reads neither the real `.env` nor
the real `runs/`). `FakeDetector` moves to `tests/fakes.py`; `prep_replicate` builds a
`FakeReplicate` that answers every prep stage; `FakeReplicate` and `FakeWaveSpeed` can append
each paid create to a log file, so counts survive across child processes. The test drives the
app through upload, prepare, approve, draft and final, and checks the files, the costs, and
that each paid create happened exactly once. Everything it drives exists already, so its tests
pass as soon as they are written; a failure here is a real bug in Tasks 1 to 12.

**Files:**
- Test: `tests/fake_cli.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_click_through.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the tests**

**Create** `tests/fake_cli.py`:

````python
"""The real `charswap` CLI on fake providers: what the app's integration test runs as a child.

Usage (as the app's runner): python tests/fake_cli.py <charswap arguments...>
CHARSWAP_FAKE_DIR names a temporary folder: runs/ lives there, and so do the fakes' logs
(replicate.jsonl, wavespeed.jsonl), so counts survive across child processes. No network,
no cost, and neither the real .env nor the real runs/ folder is read.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the tests' `from fakes import ...`

from character_swap import cli, pipeline  # noqa: E402
from character_swap.config import Settings  # noqa: E402
from character_swap.faces import FaceLibrary  # noqa: E402
from character_swap.pipeline import Services  # noqa: E402
from character_swap.providers.generators.seedance_wavespeed import (  # noqa: E402
    SeedanceWaveSpeedGenerator,
)
from fakes import FakeDetector, FakeWaveSpeed, prep_replicate  # noqa: E402
from helpers import solid, write_video  # noqa: E402

FAKE_KEYS = {"REPLICATE_API_TOKEN": "fake", "WAVESPEED_API_KEY": "fake"}


def _pitch(vocals, dst, **kwargs):  # like the no_rubberband fixture: no binary needed
    dst.write_bytes(vocals.read_bytes())
    return dst


def main(argv: list[str]) -> int:
    folder = Path(os.environ["CHARSWAP_FAKE_DIR"])
    pipeline.pitch_vocals = _pitch
    settings = Settings.load(
        root=folder, environ={"CHARSWAP_RUNS_DIR": str(folder / "runs"), **FAKE_KEYS}
    )

    def services(_settings: Settings) -> Services:
        output = folder / "wavespeed-out.mp4"
        if not output.exists():
            write_video(output, [solid((20, 200, 20))] * 96)
        wavespeed = FakeWaveSpeed(
            files={"https://cdn.wavespeed.ai/out.mp4": output}, log=folder / "wavespeed.jsonl"
        )
        return Services(
            replicate=prep_replicate(folder / "media", log=folder / "replicate.jsonl"),
            face_detector=FakeDetector,
            rubberband="unused",
            generators={"seedance:wavespeed": SeedanceWaveSpeedGenerator(wavespeed)},
            faces=FaceLibrary(folder),
        )

    return cli.main(argv, settings=settings, services_factory=services)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
````

**Replace** in `tests/fakes.py`:

````python
"""Fake providers. They record calls and return canned outputs; no network, no cost."""

import json
import shutil
from pathlib import Path

from character_swap.providers.errors import OutputExpired


class ReplicateOutputExpired(OutputExpired):
````

with:

````python
"""Fake providers. They record calls and return canned outputs; no network, no cost."""

import io
import json
import shutil
import zipfile
from pathlib import Path

from character_swap.providers.errors import OutputExpired
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.face_mesh import MeshStyle
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION
from helpers import solid, tone, write_mask_pngs, write_video, write_wav


def _log(path: Path | None, entry: dict) -> None:
    """Paid creates are also appended to a file, so a count survives across processes."""
    if path is not None:
        with Path(path).open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, default=str) + "\n")


class FakeDetector:
    """Stands in for MediaPipe's face mesh: one face, two points, on every frame."""

    style = MeshStyle(lines=[(((0, 1),), (255, 0, 0), 1)])

    def detect(self, frame, timestamp_ms):
        return [[(0.4, 0.4), (0.6, 0.6)]]

    def close(self):
        pass


class ReplicateOutputExpired(OutputExpired):
````

**Replace** in `tests/fakes.py`:

````python


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
````

with:

````python


class FakeReplicate:
    def __init__(
        self, outputs=None, files=None, schema=None, fail_download=(), flaky_download=(), log=None
    ):
        self.outputs = outputs or {}  # key: version or model -> output
        self.files = files or {}  # url -> local path or bytes to "download"
        self.schema = schema or {"video": {"type": "string", "format": "uri"}}
        self.fail_download = set(fail_download)  # urls whose download raises (expired)
        self.flaky_download = set(flaky_download)  # urls whose download fails transiently
        self.log = log  # a file that also records every paid run (see _log)
        self.latest_calls = 0
        self.uploads: list[Path] = []
        self.runs: list[dict] = []
````

**Replace** in `tests/fakes.py`:

````python
        if record.exists() and json.loads(record.read_text()).get("status") == "succeeded":
            return self.outputs[key]  # resumed from the record: no new run, no new cost
        self.runs.append({"key": key, "input": input})
        prediction = {"id": f"pred-{len(self.runs)}", "status": "succeeded"}
        record.write_text(json.dumps(prediction))
        if on_created is not None:
````

with:

````python
        if record.exists() and json.loads(record.read_text()).get("status") == "succeeded":
            return self.outputs[key]  # resumed from the record: no new run, no new cost
        self.runs.append({"key": key, "input": input})
        _log(self.log, {"key": key})
        prediction = {"id": f"pred-{len(self.runs)}", "status": "succeeded"}
        record.write_text(json.dumps(prediction))
        if on_created is not None:
````

**Replace** in `tests/fakes.py`:

````python
class FakeWaveSpeed:
    """Stands in for WaveSpeedClient: canned links, one prediction per body."""

    def __init__(self, output="https://cdn.wavespeed.ai/out.mp4", files=None):
        self.output = output
        self.files = files if files is not None else {output: b"mp4"}
        self.uploads: list[Path] = []
        self.bodies: list[dict] = []

````

with:

````python
class FakeWaveSpeed:
    """Stands in for WaveSpeedClient: canned links, one prediction per body."""

    def __init__(self, output="https://cdn.wavespeed.ai/out.mp4", files=None, log=None):
        self.output = output
        self.files = files if files is not None else {output: b"mp4"}
        self.log = log  # a file that also records every paid prediction (see _log)
        self.uploads: list[Path] = []
        self.bodies: list[dict] = []

````

**Replace** in `tests/fakes.py`:

````python
                # (and here, finishes); never a new prediction, never a new cost.
                return self.output
        self.bodies.append(body)
        prediction = {"id": f"pred-{len(self.bodies)}", "status": "succeeded"}
        record.write_text(json.dumps(prediction))
        if on_created is not None:
````

with:

````python
                # (and here, finishes); never a new prediction, never a new cost.
                return self.output
        self.bodies.append(body)
        _log(self.log, {"resolution": body.get("resolution"), "prompt": body.get("prompt")})
        prediction = {"id": f"pred-{len(self.bodies)}", "status": "succeeded"}
        record.write_text(json.dumps(prediction))
        if on_created is not None:
````

**Replace** in `tests/fakes.py`:

````python
    @property
    def last(self) -> FakeProcess:
        return self.started[-1]
````

with:

````python
    @property
    def last(self) -> FakeProcess:
        return self.started[-1]


def prep_replicate(folder: Path, frames: int = 96, log=None) -> FakeReplicate:
    """A FakeReplicate that answers every prep stage with real tiny files (4 s at 24 fps)."""
    folder.mkdir(parents=True, exist_ok=True)
    stems = {
        name: write_wav(folder / f"{name}.wav", tone(frames / 24, freq))
        for name, freq in (("v", 300), ("d", 80), ("b", 60), ("o", 500))
    }
    depth = write_video(folder / "depth.mp4", [solid((100, 100, 100))] * frames)
    masks = write_mask_pngs(folder / "masks", frames)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for png in sorted(masks.iterdir()):
            archive.writestr(png.name, png.read_bytes())
    return FakeReplicate(
        outputs={
            DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d", "bass": "u/b", "other": "u/o"},
            WHISPER_VERSION: {"transcription": "hello"},
            DESCRIBE_MODEL: ["A studio."],
            f"{DEPTH_MODEL}-latest": "u/depth",
            SAM_VERSION: "u/zip",
        },
        files={
            **{f"u/{name}": path for name, path in stems.items()},
            "u/depth": depth,
            "u/zip": buffer.getvalue(),
        },
        log=log,
    )
````

**Create** `tests/test_click_through.py`:

````python
"""One full click-through: the app starts the real CLI (on fakes) as child processes."""

import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from character_swap.config import Settings
from character_swap.jobs import Job
from character_swap.web.app import create_app
from character_swap.web.runner import Runner
from helpers import solid, write_video

REPO = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8770"


def _wait(check, what, folder, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.2)
    logs = "\n".join(p.read_text() for p in folder.rglob("app.log"))
    raise AssertionError(f"timed out waiting for {what}\n{logs[-3000:]}")


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_upload_prepare_approve_draft_final(tmp_path):
    folder = tmp_path / "fake"
    runs = folder / "runs"
    runs.mkdir(parents=True)
    settings = Settings.load(
        root=folder,
        environ={
            "CHARSWAP_RUNS_DIR": str(runs),
            "REPLICATE_API_TOKEN": "f",
            "WAVESPEED_API_KEY": "f",
        },
    )
    runner = Runner(
        [sys.executable, "tests/fake_cli.py"],
        cwd=REPO,
        env={**os.environ, "CHARSWAP_FAKE_DIR": str(folder)},
    )
    client = TestClient(create_app(settings, runner=runner), base_url="http://127.0.0.1")
    token = client.get("/").text.split('name="charswap-token" content="')[1].split('"')[0]
    headers = {"Origin": ORIGIN, "X-Charswap-Token": token}

    def post(path, **kwargs):
        response = client.post(path, headers=headers, **kwargs)
        assert response.status_code in (200, 202), response.text
        return response.json()

    def idle(job_id):
        data = client.get(f"/api/jobs/{job_id}").json()
        return None if data["running"] else data

    # 1. Upload: a 4 s clip and a photo; the app starts `charswap prepare` (paid, confirmed).
    clip = write_video(tmp_path / "clip.mp4", [solid((180, 40, 40))] * 96, audio_seconds=4)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(photo, "JPEG")
    price = client.get("/new").text.split('data-usd="')[1].split('"')[0]
    start = post(
        "/jobs",
        data={"rights": "yes", "look": "depth_mesh", "confirm_usd": price, "describe": "Bald."},
        files={
            "clip": ("clip.mp4", clip.read_bytes(), "video/mp4"),
            "photo": ("me.jpg", photo.read_bytes(), "image/jpeg"),
        },
    )["start"]
    ready = _wait(
        lambda: (s := client.get(f"/api/starts/{start}").json())["state"] != "running" and s,
        "the job to exist",
        folder,
    )
    assert ready["state"] == "ready", ready
    job_id = ready["job"]

    # 2. Prepare fills in; then every prep stage is done.
    data = _wait(lambda: idle(job_id), "prepare to finish", folder)
    assert set(data["stages"].values()) == {"done"}, data
    assert not (runs / "_incoming" / start).exists()

    # 3. Check: approve with the fingerprint the page was rendered with.
    page = client.get(f"/jobs/{job_id}/step/3").text
    seen = page.split('data-fingerprint="')[1].split('"')[0]
    post(f"/jobs/{job_id}/approve", json={"fingerprint": seen})

    # 4. Draft: the dialog's price, then the paid command, which checks it.
    usd = client.get(f"/api/jobs/{job_id}/estimate", params={"action": "draft"}).json()["usd"]
    post(f"/jobs/{job_id}/draft", json={"confirm_usd": usd})
    data = _wait(lambda: idle(job_id), "the draft", folder)
    assert data["generation"]["draft"] == "done" and data["failure"] is None, data

    # 5. Final at 720p.
    query = {"action": "final", "resolution": "720p"}
    usd = client.get(f"/api/jobs/{job_id}/estimate", params=query).json()["usd"]
    post(f"/jobs/{job_id}/final", json={"confirm_usd": usd, "resolution": "720p"})
    data = _wait(lambda: idle(job_id), "the final", folder)
    assert data["generation"]["final"] == "done" and data["failure"] is None, data

    job = Job.load(runs, job_id)
    assert job.path("final.mp4").is_file() and job.path("draft.mp4").is_file()
    assert job.state["character_description"] == "Bald"
    assert [cost["step"] for cost in job.state["costs"]] == ["prepare", "draft", "final"]
    log = job.path("app.log").read_text()
    assert log.count("=== prepare ") == 1 and log.count("=== generate ") == 2
    # each paid create happened exactly once
    assert len(_lines(folder / "replicate.jsonl")) == 5  # demucs, whisper, scene, depth, SAM 3
    bodies = _lines(folder / "wavespeed.jsonl")
    assert [body["resolution"] for body in bodies] == ["480p", "720p"]
    assert "looks like this: Bald." in bodies[0]["prompt"]
    assert "Download" in client.get(f"/jobs/{job_id}/step/5").text
````

**Replace** in `tests/test_pipeline.py`:

````python
from character_swap.stages import StageError
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.face_mesh import MeshStyle
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION
from fakes import FakeReplicate
from helpers import solid, tone, write_mask_pngs, write_video, write_wav

FRAMES = 96  # 4 s at 24 fps (Seedance minimum)


class FakeDetector:
    style = MeshStyle(lines=[(((0, 1),), (255, 0, 0), 1)])

    def detect(self, frame, timestamp_ms):
        return [[(0.4, 0.4), (0.6, 0.6)]]

    def close(self):
        pass


def zipped_masks(tmp_path, count):
````

with:

````python
from character_swap.stages import StageError
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION
from fakes import FakeDetector, FakeReplicate
from helpers import solid, tone, write_mask_pngs, write_video, write_wav

FRAMES = 96  # 4 s at 24 fps (Seedance minimum)


def zipped_masks(tmp_path, count):
````

- [ ] **Step 2: Run the task's tests**

Run: `uv run pytest tests/test_click_through.py tests/test_pipeline.py -q --no-cov`  
Expected: PASS (39 passed).

- [ ] **Step 3: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1128 passed, 1 deselected; coverage above 96 %
git add tests/fake_cli.py tests/fakes.py tests/test_click_through.py tests/test_pipeline.py
git commit -m "test: one full click-through on the real CLI with fake providers"
````

---

### Task 14: Docs and the final check

§6: `CLAUDE.md` says `web/` is the click-through app (GET routes stay read-only) and names the
lock and `--confirmed-usd` rules; the README's `charswap view` lines point at `charswap app`
(the full README rewrite is Plan 3b); the journal and Frank's checklist are updated.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `activity.md`
- Modify: `docs/scratchpad.md`

- [ ] **Step 1: Write the code**

**Replace the whole file** `CLAUDE.md` with:

````markdown
# Character Swap

Local app + CLI that swaps a person in a short video for a chosen character, keeping motion, timing and the original soundtrack. Generators: Seedance 2.5 on WaveSpeed (default; real faces as a plain photo) or on PiAPI (saved faces, two people, 1080p upgrade), and Wan 2.2 Animate on Replicate (open source).

## Stack
Python 3.12 · uv · PyAV/NumPy/Pillow · httpx · MediaPipe · FastAPI/Jinja2 + python-multipart (the click-through app: `charswap app`) · pytest (+cov ≥ 85 %) · ruff.

## Commands
- Install: `uv sync`
- Tests (with coverage gate): `uv run pytest`
- Lint/format: `uv run ruff format src tests && uv run ruff check --fix src tests`
- CLI: `uv run charswap --help`
- Faces (free): `uv run charswap face add|list|check|renew`
- Prompt (Seedance): `uv run charswap prompt <job>`
- Generate (paid, asks first): `uv run charswap generate <job> --draft|--final [--resolution 720p|1080p]`
- App: `uv run charswap app` (`charswap view` is its old name)

## Layout
- `src/character_swap/providers/` is the ONLY code that calls the network.
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
- `web/` = the click-through app (FastAPI + Jinja2, localhost only). GET routes never change a job. Every POST needs the launch token and our own Origin; long work runs as `charswap` child processes (`web/runner.py`), paid ones with `--confirmed-usd`. Buttons only for one-person WaveSpeed jobs in `depth`/`depth_mesh`; other jobs are read-only. Every on-screen step text lives in `web/steps.py`.
- Every command that changes a job holds `runs/<id>/run.lock` (`lock.py`, `fcntl.flock`); the app's approve and prompt save take it too. Never delete `run.lock`.
- A job's Seedance provider (`provider` in `state.json`) is fixed at prepare; old jobs are resolved by `pipeline.generator_key`.
- Jobs live in `runs/<id>/` with `state.json` (resumable). Saved faces live in `faces/` (`faces.json` + photos).
- `inputs/`, `faces/` and `runs/` are git-ignored and never committed.

## Hard rules
- Never auto-retry a paid request. Only exception: a Replicate create rejected with 429 (nothing was created) is retried after re-claiming the exclusive "creating" marker. PiAPI and WaveSpeed 429s are not retried. Persist provider IDs before waiting.
- Every paid action shows an estimate and needs confirmation. In the app: a price dialog, then the command refuses unless the price is still exactly the one confirmed (`--confirmed-usd`, `pipeline.planned_charge`).
- The app never deletes or edits provider records, claim markers or results; those cases show the CLI's instructions ("needs your decision").
- Tests never call paid APIs (use `tests/fakes.py`); live tests are `@pytest.mark.live`.
- Stage files by name; never `git add -A`. No attribution lines in commits.
- Never copy anything from `_legacy/` assets or mention the original author's brands.

## Docs (read on demand)
Spec: `docs/superpowers/specs/` · Plans: `docs/superpowers/plans/` · Journal: `activity.md` · Checklist: `docs/scratchpad.md` · UI guide: `docs/ui-ux-best-practices.md`
````

**Replace** in `README.md`:

````markdown
```bash
uv sync                                                      # once: installs the tool
uv run charswap prepare inputs/clip.mp4 --character inputs/me.jpg  # asks about rights, then ~$0.06
# watch it in `uv run charswap view`, then approve with the command shown there
uv run charswap prompt <id>                                  # optional: edit prompt.txt
uv run charswap generate <id> --draft                        # 480p, shows the price, asks first
uv run charswap generate <id> --final                        # a new 720p take
````

with:

````markdown
```bash
uv sync                                                      # once: installs the tool
uv run charswap prepare inputs/clip.mp4 --character inputs/me.jpg  # asks about rights, then ~$0.06
# or do every step by clicking: uv run charswap app
uv run charswap prompt <id>                                  # optional: edit prompt.txt
uv run charswap generate <id> --draft                        # 480p, shows the price, asks first
uv run charswap generate <id> --final                        # a new 720p take
````

**Replace** in `README.md`:

````markdown
1. `uv sync`
2. `uv run charswap prepare inputs/clip.mp4 --generator wan --mode direct --character inputs/me.jpg`
   Use a clip of up to 30 s with **one** person, and a clear, well-lit photo of **one** person. Preparing is free for Wan (nothing is sent to a paid service), so it asks nothing. (`--language` is for Seedance modes only; Wan has no transcript step.)
3. `uv run charswap view` opens the viewer so you can watch the normalized clip and your photo. It keeps running until you press Ctrl-C in that terminal. Then copy the fingerprint from `uv run charswap status <id>` and approve: `uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>`. `status` also tells you the next step.
4. `uv run charswap generate <id> --draft` makes a 480p test (about $0.02 per second of video). It shows the estimate and asks you to confirm before any money is spent (`--yes` skips the question).
5. Watch the draft in the viewer. Happy with it? `uv run charswap generate <id> --final` makes the 720p version with the same seed (about $0.05 per second), again with a cost question.

The final is only allowed for the exact inputs you watched in the draft. The draft becomes out of date only when the clip or photo files change (approving the same files again does not). There is no command to change the photo: to use a different one, start a new job with `uv run charswap prepare … --character <new photo>` (recommended) rather than editing files under `runs/`. An out-of-date draft can be remade (the cost is asked again and the old files are kept as `draft-old-*` / `final-old-*`).

````

with:

````markdown
1. `uv sync`
2. `uv run charswap prepare inputs/clip.mp4 --generator wan --mode direct --character inputs/me.jpg`
   Use a clip of up to 30 s with **one** person, and a clear, well-lit photo of **one** person. Preparing is free for Wan (nothing is sent to a paid service), so it asks nothing. (`--language` is for Seedance modes only; Wan has no transcript step.)
3. `uv run charswap app` opens the app (read-only for Wan jobs) so you can watch the normalized clip and your photo. It keeps running until you press Ctrl-C in that terminal. Then copy the fingerprint from `uv run charswap status <id>` and approve: `uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>`. `status` also tells you the next step.
4. `uv run charswap generate <id> --draft` makes a 480p test (about $0.02 per second of video). It shows the estimate and asks you to confirm before any money is spent (`--yes` skips the question).
5. Watch the draft in the app. Happy with it? `uv run charswap generate <id> --final` makes the 720p version with the same seed (about $0.05 per second), again with a cost question.

The final is only allowed for the exact inputs you watched in the draft. The draft becomes out of date only when the clip or photo files change (approving the same files again does not). There is no command to change the photo: to use a different one, start a new job with `uv run charswap prepare … --character <new photo>` (recommended) rather than editing files under `runs/`. An out-of-date draft can be remade (the cost is asked again and the old files are kept as `draft-old-*` / `final-old-*`).

````

**Replace** in `README.md`:

````markdown
```bash
uv run charswap face add me me.jpg                         # once, free: PiAPI checks the photo
uv run charswap prepare clip.mp4 --provider piapi --face me # ~$0.06, default mode depth_mesh
# watch it in `uv run charswap view`, then approve with the command shown there
uv run charswap prompt <id>                                # optional: edit prompt.txt
uv run charswap generate <id> --draft                      # 480p, shows the price, asks first
uv run charswap generate <id> --final                      # 1080p upgrade of that draft
````

with:

````markdown
```bash
uv run charswap face add me me.jpg                         # once, free: PiAPI checks the photo
uv run charswap prepare clip.mp4 --provider piapi --face me # ~$0.06, default mode depth_mesh
# watch it in `uv run charswap app` (read-only for PiAPI jobs), then: charswap status <id>
# and charswap review <id> --approve --note "watched all" --fingerprint <fp>
uv run charswap prompt <id>                                # optional: edit prompt.txt
uv run charswap generate <id> --draft                      # 480p, shows the price, asks first
uv run charswap generate <id> --final                      # 1080p upgrade of that draft
````

**Append** to `activity.md`:

````markdown

## 2026-11-13 — Session 8: Plan 3a, the click-through app
- **Goal:** a local app that does a whole swap by clicking, to film for the YouTube video.
- **Completed:** `charswap app` (old name `view`): home, five step screens, the before/after wipe player, technique tags, live progress; upload, approve, prompt save, paid buttons with a price dialog; "needs your decision" instead of a button where a click could buy twice. CLI: `run.lock`, `--confirmed-usd`, `--describe`, `--id-file`, `python -m character_swap`, the `expired` flag.
- **Decisions:** see the plan's P1–P14 table (docs/superpowers/plans/2026-11-13-click-through-app.md).
- **Lessons:**
  - The page and `/api/jobs/{id}` must embed exactly the same values, or the page reloads in a loop.
  - A lock holder clears `run.lock` when it lets go; otherwise a long-lived server pid reads as "running" forever.
- **Not done:** Frank's browser checklist on a real job (docs/scratchpad.md); Plan 3b.
````

**Replace** in `docs/scratchpad.md`:

````markdown
- [x] WaveSpeed Seedance generator (default provider; 968 tests, 96.92 % coverage; branch feat/wavespeed)
- [x] S7 — Live WaveSpeed draft through charswap (Task 14): job ea899aff…, 227 s, ~$0.88, clean 4 s result
- [x] S7 — Real WaveSpeed charge checked: dashboard shows $3.564 for about four 4 s 480p runs, so $0.88 per run holds
- [ ] Plan 3 — web UI, skill, docs, legacy removal

## Do first in Plan 2 (from final Plan 1 review)
- [x] S5 — (2b) Decide whole-second clip length: PiAPI `duration` is an int 4–30, `normalize` keeps fractional seconds, and `restore_original_audio` refuses > 0.25 s longer / > 0.5 s shorter. Snap video+audio to whole seconds at normalize (and use that as the soundtrack source) or round up and trim the generated video. Add a normalize → restore test with an integer-length generated video.
````

with:

````markdown
- [x] WaveSpeed Seedance generator (default provider; 968 tests, 96.92 % coverage; branch feat/wavespeed)
- [x] S7 — Live WaveSpeed draft through charswap (Task 14): job ea899aff…, 227 s, ~$0.88, clean 4 s result
- [x] S7 — Real WaveSpeed charge checked: dashboard shows $3.564 for about four 4 s 480p runs, so $0.88 per run holds
- [x] Plan 3a — click-through app (`charswap app`): five step screens, wipe player, run.lock, `--confirmed-usd`, paid buttons with a price dialog
- [ ] Frank's browser checklist before filming (spec §7): each step on a real job and the wipe player on every tab; the approve gate and the price dialog; a refresh mid-draft and closing the browser mid-draft; a missing key
- [ ] Plan 3b — Claude skill, README and setup docs, `_legacy/` removal, Dolly waitlist link

## Do first in Plan 2 (from final Plan 1 review)
- [x] S5 — (2b) Decide whole-second clip length: PiAPI `duration` is an int 4–30, `normalize` keeps fractional seconds, and `restore_original_audio` refuses > 0.25 s longer / > 0.5 s shorter. Snap video+audio to whole seconds at normalize (and use that as the soundtrack source) or round up and trim the generated video. Add a normalize → restore test with an integer-length generated video.
````

**Replace** in `docs/scratchpad.md`:

````markdown
- Validate character image with PIL at job create; reject silent clips early; validate mode/generator before the cost question.
- `doctor`: required vs optional checks, HF_TOKEN, face model present.
- Symlink rejection in fingerprinting; per-job "running" lock for prepare vs decide.
- CSRF for any state-changing route: Origin / Sec-Fetch-Site same-origin check + per-session token or custom header; never a GET that spends money.
- Cross-process run lock per job (lock file with pid + heartbeat) → real "is it running?" answer; background threads never get KeyboardInterrupt.
- Cost confirmation dialog before every paid button (mirror CLI confirm).
- Viewer polish: slow-poll (~10 s) failed jobs so `charswap resume` shows up live; repair SAM cost row; check nav scroll on phone width before recording; direct mode shows original.mp4 twice.

## Before charging users (platform)
````

with:

````markdown
- Validate character image with PIL at job create; reject silent clips early; validate mode/generator before the cost question.
- `doctor`: required vs optional checks, HF_TOKEN, face model present.
- Symlink rejection in fingerprinting; per-job "running" lock for prepare vs decide.
- Done in Plan 3a: Origin + per-launch token on every POST; the cross-process run lock (`run.lock`); a price dialog before every paid button, checked again by `--confirmed-usd`.
- Viewer polish: slow-poll (~10 s) failed jobs so `charswap resume` shows up live; repair SAM cost row; check nav scroll on phone width before recording; direct mode shows original.mp4 twice.

## Before charging users (platform)
````

- [ ] **Step 2: Lint, full suite, commit**

````bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run ruff format --check src tests
uv run pytest    # expected: 1128 passed, 1 deselected; coverage 96.83 %
git add CLAUDE.md README.md activity.md docs/scratchpad.md
git commit -m "docs: the click-through app in CLAUDE.md, README, journal and checklist"
````

- [ ] **Step 3: Final verification**

````bash
uv run pytest                      # expected: 1128 passed, 1 deselected; Total coverage: 96.83%
uv run ruff check src tests        # expected: All checks passed!
uv run ruff format --check src tests
git status --short                 # expected: nothing but the untracked outputs/ folder
````

Then hand Frank the browser checklist in `docs/scratchpad.md` (spec §7). Nothing paid was run
by this plan; the first real paid click is his.

---
## Spec deviations and gap fills

The spec fit the code with these adjustments; none changes a decision or a money-safety rule.

1. **CSP `media-src 'self' blob:`.** The step 1 "Draft later: about $X" hint reads the chosen clip's length in the browser from a local `blob:` URL, which `media-src 'self'` would block. That is the only header change; nothing is uploaded for it.
2. **No clip name on the home list.** The job folder does not keep the user's file name (the app saves uploads as `clip.<ext>`, never the user's name), so a card says "Swap <short id>" with its date, step, money spent and a thumbnail (`scene.jpg`, else the photo).
3. **`run.lock` is cleared on release.** The spec says the holder overwrites the content after locking. It also clears it before letting go (still holding the lock), because the server takes the lock for approve and prompt save and its own pid stays alive: leftover content would read as "running" forever. A crash leaves content behind, but then the pid is dead (or reused, which the start time catches).
4. **`prompt` holds the lock for its whole run**, not only "when it writes": it nearly always writes (`pin_provider`, a renamed or a recreated `prompt.txt`).
5. **`planned_charge` has a `services` keyword**, and two helpers sit next to it: `planned_generation` (the CLI prints the prompt it sends) and `remake_reasons`. The app prices with the generator class and no client.
6. **The unconfirmed-marker intro is now on stderr.** It is part of `generate_refusal`'s one message; the words are unchanged. `UNCONFIRMED` moved from `cli.py` to `pipeline.py` with it.
7. **Security arrives with the first POST** (Task 10) and the read-only/busy 409 with the first job POST (Task 11), not in a last task (decision P9). The 403 for a wrong Origin says "This request did not come from the app, so nothing was started."
8. **Routes for "Continue (no new charge)".** `/jobs/{id}/retry` is prep's resume; a draft or final that resumes free goes to `/draft` or `/final` with `confirm_usd` 0 (P12).
9. **"Needs your decision" for prep records** is built in `web/actions.py` with the same words `ReplicateClient.run` uses ("Earlier prediction … Delete <record> to try again (this costs money).") and `replicate.unconfirmed_message`, so the page can show it before running `resume`. A failed or expired draft/final shows the stage's saved error, which is the command's own message.
10. **`decision` in `/api/jobs/{id}` is `null` while something runs** (§4.4 applies only when nothing is running); the step page embeds the same value (P13).
11. **The description loses a trailing full stop** when cleaned ("Bald." is stored as "Bald"), because the prompt line ends with its own.
12. **The upload's checks run in this order:** the tick box, the look, the description, the price, then each file is saved and checked (clip, then photo). The first problem is shown next to its field.
13. **The Prepare price** shown at step 1 is `estimate_prepare("depth_mesh")`, the same for both looks (both modes buy the same paid steps); the child checks it again for the chosen mode.
14. **Step 1 of an existing job** shows what was uploaded (the clip, the photo, the look, the description) with "Next: prepare"; the spec describes step 1 only for a new swap.
15. **Step 3 "Start over"** links to `/new`; the old job stays in the list.
16. **A failed command's last lines** are remembered by the server that started it, until that job's next command; after a restart the page relies on the saved stage errors.
17. **`python-multipart`** is added with `uv add`; uv 0.6.6 also rewrites `uv.lock`'s format (`revision = 1`). That is uv's own output and is committed as written.
18. **`charswap app` prints "App stopped. Work that was already running keeps going."** on Ctrl-C, because children have their own session.
19. **Housekeeping beyond the spec's `CLAUDE.md` item:** the README's `charswap view` lines point at `charswap app`, and the journal and checklist are updated (Task 14). The full README and setup docs stay in Plan 3b.
20. **Stale upload folders are swept on start.** `create_app` deletes `runs/_incoming/<start>` folders untouched for 24 hours (a server restart leaves them behind). They are copies of uploads, never job records or provider files, so A15 is unaffected; a prepare that is still starting touched its folder minutes ago and is never swept.
21. **Texts in templates.** The messages a viewer reads on screen ("New results are ready.", "Needs your decision.", "Yes, charge it", "The last command stopped:", the stale-draft notice, "File missing.", the price question) live in `steps.py` (A10). A few labels stay in the templates (headings such as "Your swaps", form labels such as "Your clip", "Prompt", and the missing-key notice, which mixes text with `<code>` tags); moving them is a small follow-up.
