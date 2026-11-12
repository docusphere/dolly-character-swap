# WaveSpeed Seedance Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WaveSpeed becomes the default Seedance provider, so a viewer of the YouTube tool can sign up, get $1 of free credit and run:
1. `charswap prepare clip.mp4 --character me.jpg` (mode `depth_mesh`, a real face as a plain photo, after a yes/no rights question);
2. review the prepared clip; optionally `charswap prompt <id>`;
3. `charswap generate <id> --draft` (480p, about $0.88 for 4 s);
4. `charswap generate <id> --final` (a new 720p take) or `--final --resolution 1080p` (a new 1080p take).

PiAPI keeps working unchanged as `--provider piapi` (saved faces, two people, the 1080p draft upgrade). Old jobs keep the provider they were made with.

**Architecture:**
- A new `providers/wavespeed.py` (`WaveSpeedClient`) is the only code that calls WaveSpeed. It mirrors `PiAPIClient.run()`: an exclusive "creating" marker before the one paid POST, the prediction id saved before waiting, a saved prediction resumed and never bought again.
- A new `providers/generators/seedance_wavespeed.py` (`SeedanceWaveSpeedGenerator`) implements the `Generator` protocol for WaveSpeed's `bytedance/seedance-2.5/video-edit`.
- The job keeps `generator: "seedance"` and gains a separate `provider` field. `pipeline.generator_key(job)` (`wan`, `seedance:wavespeed`, `seedance:piapi`) keys the services registry, the API key table, the unconfirmed-marker messages, the final resolutions and the viewer texts. Old jobs without `provider` are resolved by a pure rule; `charswap prompt` / `charswap generate` write the resolved value before any paid create.
- What is really PiAPI's (the 1080p promote, the face library, "upgrades the exact draft") is gated on two new generator attributes, `finals` and `promotes`, never on `generator == "seedance"`.
- Prompts are built and checked with the provider's input names (`@Video 1` / `@Image 1` for WaveSpeed, `@video1` / `@image1` / `@image2` for PiAPI).
- The PiAPI download moves into a shared `common.download_now` helper that both clients use.

**Tech Stack:** Python 3.12, uv, httpx (+ respx in tests), PyAV/NumPy/Pillow, FastAPI/Jinja2 viewer; pytest + pytest-cov (≥ 85 % enforced), ruff.

---

**Spec:** `docs/superpowers/specs/2026-11-12-wavespeed-seedance-design.md` (decisions W1–W6, the money-safety rules 1–8, and every section). This plan implements exactly that spec; the few places where the code needed a choice the spec does not make are listed under "Spec deviations and gap fills" at the end.

**Builds on:** branch `feat/wavespeed` as committed (780 tests passing). The code below targets these existing APIs:
- `Generator` protocol (`name`, `provider`, `model`, `estimate`, `generate(request, stage, *, record, dest, on_created)`), `GenerationRequest`, `CharacterRef`
- `SeedanceGenerator` (`FINALS`, `PROMOTE`, `promote_body`, `resolution`, `estimate`), `WanGenerator`
- `PiAPIClient` (`run`, `download`, `_send`, `_free`, `_load`, `_create`, `_keep`, `_release`, `_wait`, `_output`), `piapi.unconfirmed_message`
- `common.retry_free`, `claim`, `release`, `save`, `announce`
- `pipeline.generation_files`, `already_paid`, `generator_for`, `final_resolution`, `build_request`, `generation_prompt`, `default_prompt`, `write_prompt_file`, `draft_prompt_changed`, `generate`, `_check_placeholders`, `_write_atomically`, `FINAL_RESOLUTIONS`
- `prompts.build_prompt`, `validate_prompt`, `PromptError`, `BASE`, `MODE_NOTES`, `SECONDARY`
- `costs.PRICES`, `Price`, `cost_line`, `Estimate`
- `media.upload.fit_for_upload`, `UploadTooLarge`
- `faces.CONSENT`, `check_photo`
- `cli.KEYS`, `UNCONFIRMED`, `build_services`, `_prepare`, `_characters`, `_prompt`, `_generate`, `_after_draft_hint`, `_doctor`, `EPILOG`, `Env.proceed`
- `config.WHERE_TO_GET`
- `web.view_model.SECTIONS`, `Section`, `read_state`, `cost_rows`, `_generation_name`, `_seedance_texts`, `_section_view`
- tests: `tests/fakes.py` (`FakePiAPI`), `tests/test_generation.py` (`FakeGenerator`, `seedance` fixture), `tests/test_cli.py` (`run`, `_photo`, `_approved_seedance_job`, `seedance_services`, `piapi_settings`), `tests/viewer_jobs.py` (`make_job`, `finish_stage`)

**Dry run:** every code block in this plan was applied in order to a scratch copy of `feat/wavespeed` and checked: after Task 12 the suite has 932 passing tests, 96.5 % coverage, ruff clean. Task 14 then adds the live test, which the default run deselects (`1 deselected`). Line numbers below are from the branch before Task 1; later tasks shift them, so anchor on the quoted code.

**WaveSpeed facts (verified 2026-11-12 from WaveSpeed's docs and 3 live runs; recorded in Task 1):**
- **Auth:** header `Authorization: Bearer <key>`; key in `WAVESPEED_API_KEY`.
- **Upload (free):** `POST https://api.wavespeed.ai/api/v3/media/uploads` with JSON `{filename, size}` returns `data.download_url` and `data.upload = {method: "PUT", url, headers}`. Then `PUT` the raw bytes to `data.upload.url` with `data.upload.headers` and **no** Authorization header. Limit 200 MB (209 715 200 bytes). Files are kept 7 days.
- **Submit (paid):** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.5/video-edit` with JSON `{prompt, video, reference_images: [...], resolution: "480p"|"720p"|"1080p", generate_audio: false}` returns `{code, message, data: {id, status, outputs, error, ...}}`. Live runs answered `created` first, then `processing`. Ids are 32 hex characters.
- **Poll (free):** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result` returns `data.status`: `completed`, `failed`, `cancelled`, `timeout`, `deleted`, or a still-running word (`created`, `processing`, ...). `data.outputs[0]` is the video URL (a string; the docs also allow an object with `url`). `data.error` holds the failure text.
- **Input names in the prompt:** `@Video 1`, `@Image 1`.
- **No draft upgrade and no seed:** every final is a fresh run.
- **Prices (per second, billed on input seconds + output seconds; input clamped to 4–30 s):** 480p $0.11, 720p $0.22, 1080p $0.55. A 4 s 480p draft is (4 + 4) × 0.11 = $0.88.
- **Speed:** about 190 s for a 4 s 480p draft.

## Decisions taken in this plan

| # | Decision | Why |
|---|---|---|
| P1 | `generator_key`, `pin_provider` and `seedance_provider` live in `pipeline.py`, next to `generation_files` (the old-job rule reads the record files). `generator_key(job, state=None)` accepts the state the caller already read. | The viewer renders one state per page; it passes that state instead of reading `state.json` again. Still a pure read of the job folder. |
| P2 | `pipeline.GENERATOR_CLASSES` maps each key to its generator **class**; `FINAL_RESOLUTIONS` and the promote check read the class attributes. | `build_request`, `final_resolution` and the viewer run without built services (no API keys needed to price or render). |
| P3 | "Promote" = a generator with `promotes = True` making its **default** final (`finals[0]`). Helper: `pipeline.is_promote(job, stage, resolution)`. | That is exactly PiAPI's 1080p upgrade today; WaveSpeed (`promotes = False`) never promotes. |
| P4 | The placeholder check moves from `pipeline._check_placeholders` to `prompts.check_placeholders(text, *, provider, has_secondary)`. | The WaveSpeed generator runs the same free check before uploading (spec: "free checks first: ... has WaveSpeed's names"). |
| P5 | WaveSpeed create: only 400/401/403/404/422 (clear refusal) and 429 (busy) release the marker. Any other answer (other 4xx such as 402/409, 5xx, timeout, unreadable body, 2xx without `data.id`) keeps it. A refusal whose body still names a prediction id saves that prediction instead. | Money-safety rule 3: an unclear result keeps the marker. Saving a named id keeps the paid prediction resumable, as the PiAPI client does. |
| P6 | WaveSpeed polls start at 5 s and grow ×1.5 to 15 s; at most 720 polls (about 3 hours), then "still running, run the same command later; no new charge". | Spec: back-off 5 s to 15 s. A draft takes about 3 minutes; 3 hours covers a busy queue. |
| P7 | The rights question on WaveSpeed is "Do you have the right to use this person's face?", printed after the provider-neutral sentence `faces.PHOTO_CONSENT`. Declining prints "Cancelled. Nothing was charged." | Spec: provider-neutral consent before the cost question; `--yes` answers both; `faces.CONSENT` keeps its PiAPI wording. |
| P8 | An unknown `provider` value in `state.json` is an error (`JobError` in the pipeline, `ViewError` in the viewer), never guessed. | Fail fast on damaged state; the viewer already validates `mode` the same way. |
| P9 | The viewer's prompt line is labelled "Prompt sent to PiAPI" / "Prompt sent to WaveSpeed"; the Draft and Final sections get a `wavespeed_why` text. | Spec: the prompt line and cost rows carry the provider's name; no PiAPI or "upgrades the exact draft" wording on WaveSpeed jobs. |
| P10 | `--character` photos still go through `faces.check_photo` (free: JPEG, 300+ px, aspect 0.4–2.5); its two messages say "Seedance" instead of "PiAPI". | Free refusal before any question; the old wording would blame PiAPI on a WaveSpeed job. |

## Conventions for every task

- Run commands from the repo root: `"/Users/franciscojosejimeneznillardkam/Documents/Frank DOCS/Frank Projects/youtube/hf-genjutsu/character-swap"` (the path contains spaces; quote it).
- Branch: `feat/wavespeed` (already checked out; the spec and this plan are committed on it).
- Tests: `uv run pytest <path> -q --no-cov` while iterating; the full suite with the coverage gate is `uv run pytest`.
- Lint and format before every commit: `uv run ruff format src tests && uv run ruff check --fix src tests`.
- Stage files **by name**; never `git add -A` / `git add .`. No attribution / Co-Authored-By lines in commit messages. Never commit `.env`, `runs/`, `models/`, `inputs/`, `faces/`, `outputs/`, `.superpowers/`.
- Tests never call the network or a paid API: use `tests/fakes.py` (`FakePiAPI`, `FakeWaveSpeed`) and `respx`. The one live test (Task 14) is `@pytest.mark.live`, deselected by default, and only Frank runs it.
- The viewer stays GET-only and never changes job state (it never writes `provider`).
- User-facing copy is plain English. No em dashes in viewer "Why this step?" text (test enforced).
- Never mention the original project's author or brands; never copy from `_legacy/` or `reference-original/`.
- The WaveSpeed key is never logged, printed or put in a message.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/character_swap/providers/wavespeed.py` | Create | the only WaveSpeed client: free upload (ticket + PUT), paid create once with the claim marker, poll with back-off, status mapping, error mapping, download |
| `src/character_swap/providers/generators/seedance_wavespeed.py` | Create | `SeedanceWaveSpeedGenerator`: resolutions, estimate, request body, free checks, upload, run, download |
| `src/character_swap/providers/common.py` | Modify | + `download_now`, the shared keyless download with a provider-named expiry message |
| `src/character_swap/providers/piapi.py` | Modify | `download` uses `common.download_now` (same messages and error types) |
| `src/character_swap/providers/generators/base.py` | Modify | `finals` and `promotes` on the `Generator` protocol |
| `src/character_swap/providers/generators/seedance_piapi.py` | Modify | `finals = FINALS`, `promotes = True` |
| `src/character_swap/providers/generators/wan_replicate.py` | Modify | `finals = ("720p",)`, `promotes = False` |
| `src/character_swap/costs.py` | Modify | WaveSpeed price keys |
| `src/character_swap/prompts.py` | Modify | per-provider input names, templates, `build_prompt(provider=...)`, `check_placeholders` |
| `src/character_swap/jobs.py` | Modify | `SEEDANCE_PROVIDERS`; `Job.create(provider=...)` saves and validates it |
| `src/character_swap/pipeline.py` | Modify | `GENERATOR_CLASSES`, `FINAL_RESOLUTIONS` by key, `generator_key`, `pin_provider`, `seedance_provider`, `is_promote`, `rename_old_prompt_names`; registry, finals, prompts and promote gating use them |
| `src/character_swap/faces.py` | Modify | `PHOTO_CONSENT`; provider-neutral photo-check messages |
| `src/character_swap/cli.py` | Modify | `--provider`, `depth_mesh` default, WaveSpeed refusals and rights question, wording; `KEYS`/`UNCONFIRMED`/services by key; pin + rename in `prompt`/`generate`; final notes and after-draft hint by generator; doctor |
| `src/character_swap/config.py` | Modify | `WHERE_TO_GET["WAVESPEED_API_KEY"]` |
| `src/character_swap/web/view_model.py` | Modify | provider-aware cost rows, prompt label, Draft/Final texts, default final resolution; validates `provider` |
| `tests/fakes.py` | Modify | `FakeWaveSpeed` |
| `tests/test_wavespeed.py`, `tests/test_seedance_wavespeed.py`, `tests/test_live_wavespeed.py` | Create | client tests, generator tests, the live test |
| `tests/test_costs.py`, `test_common.py`, `test_prompts.py`, `test_seedance.py`, `test_wan.py`, `test_jobs.py`, `test_generation.py`, `test_cli.py`, `test_config.py`, `test_view_model.py`, `test_web_app.py` | Modify | tests per task |
| `.env.example`, `README.md`, `CLAUDE.md`, `activity.md`, `docs/scratchpad.md` | Modify | WaveSpeed key and usage first, PiAPI under "Other providers", journal, checklist |
| `docs/research/2026-10-22-provider-api-facts.md` | Modify | the verified WaveSpeed facts (Task 1) |

---

### Task 1: Record the WaveSpeed facts and prices

**Files:** Modify `docs/research/2026-10-22-provider-api-facts.md`, `src/character_swap/costs.py` (the `PRICES` dict, lines 29–77), `tests/test_costs.py`.

- [ ] **Step 0: Check the branch**

```bash
git branch --show-current   # expected: feat/wavespeed
uv run pytest -q --no-cov   # expected: 780 passed
```

- [ ] **Step 1: Research doc.** Add a new top-level section right before `## Replicate` (line 87):

```markdown
## WaveSpeed (Seedance 2.5 video-edit) — verified 2026-11-12 (for the WaveSpeed plan)

Sources: https://wavespeed.ai/models/bytedance/seedance-2.5/video-edit (model page and
prices), WaveSpeed's API docs (media uploads, submit, prediction result), and three live
runs on 2026-11-12 (480p, 4 s clip, about 3 minutes and about $0.88 each).

- Auth: `Authorization: Bearer <WAVESPEED_API_KEY>`.
- Upload (free): `POST https://api.wavespeed.ai/api/v3/media/uploads` with `{filename, size}`
  returns `data.download_url` and `data.upload {method: "PUT", url, headers}`. PUT the raw
  bytes to `data.upload.url` with `data.upload.headers` and no Authorization header.
  Limit 200 MB (209 715 200 bytes); files are kept 7 days.
- Submit (paid): `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.5/video-edit`
  with `{prompt, video, reference_images: [...], resolution: "480p"|"720p"|"1080p",
  generate_audio: false}` returns `{code, message, data: {id, status, outputs, error}}`.
  Live runs answered `created`, then `processing`. Ids are 32 hex characters.
- Poll (free): `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`. Final
  statuses: `completed`, `failed`, `cancelled`, `timeout`, `deleted`; anything else is still
  running. `data.outputs[0]` is the video URL (a string; the docs also allow `{url}`);
  `data.error` is the failure text.
- Prompt input names: `@Video 1`, `@Image 1`. A real face works as a plain photo (no face
  library).
- No draft upgrade and no seed: every final is a fresh run.
- Prices per second, billed on input seconds (clamped to 4–30) plus output seconds:
  480p $0.11, 720p $0.22, 1080p $0.55. A 4 s draft costs (4 + 4) × 0.11 = $0.88.
- How long result links stay valid is not documented: download right away.
```

- [ ] **Step 2: Failing test.** Append to `tests/test_costs.py`:

```python
def test_wavespeed_prices_bill_input_and_output_seconds():
    rates = {"480": 0.11, "720": 0.22, "1080": 0.55}
    for resolution, usd in rates.items():
        for key in (f"wavespeed-{resolution}", f"wavespeed-{resolution}-input"):
            assert PRICES[key].unit == "second" and PRICES[key].checked_on == "2026-11-12"
            assert PRICES[key].usd == pytest.approx(usd)
            assert PRICES[key].source == "wavespeed.ai/models/bytedance/seedance-2.5/video-edit"
    seconds = 4
    draft = Estimate(
        lines=(cost_line("wavespeed-480", seconds), cost_line("wavespeed-480-input", seconds))
    )
    assert draft.total == pytest.approx(0.88)
```

- [ ] **Step 3: Run it and watch it fail.** `uv run pytest tests/test_costs.py -q --no-cov`. Expected: `KeyError: 'wavespeed-480'`.

- [ ] **Step 4: Add the prices.** In `src/character_swap/costs.py`, add this block at the end of `PRICES`, right after the PiAPI `**{...},` block and before the closing `}`:

```python
    # WaveSpeed Seedance 2.5 video-edit, per second (wavespeed.ai model page, 2026-11-12).
    # Billed on the input video's seconds (clamped to 4-30) plus the output's seconds.
    **{
        key: Price(
            item, usd, "second", "2026-11-12", "wavespeed.ai/models/bytedance/seedance-2.5/video-edit"
        )
        for key, item, usd in (
            ("wavespeed-480", "Seedance draft (480p)", 0.11),
            ("wavespeed-480-input", "Seedance input video (480p)", 0.11),
            ("wavespeed-720", "Seedance final (720p)", 0.22),
            ("wavespeed-720-input", "Seedance input video (720p)", 0.22),
            ("wavespeed-1080", "Seedance final (1080p)", 0.55),
            ("wavespeed-1080-input", "Seedance input video (1080p)", 0.55),
        )
    },
```

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_costs.py -q --no-cov`. Expected: PASS (including `test_every_price_has_a_checked_date`).

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add docs/research/2026-10-22-provider-api-facts.md src/character_swap/costs.py tests/test_costs.py
git commit -m "docs: verified WaveSpeed Seedance facts; WaveSpeed prices"
```

---

### Task 2: Generators expose `finals` and `promotes`; the WaveSpeed generator's pure half

**Files:** Modify `src/character_swap/providers/generators/base.py` (protocol, lines 35–44), `src/character_swap/providers/generators/seedance_piapi.py` (class attributes, lines 34–40), `src/character_swap/providers/generators/wan_replicate.py` (class attributes, lines 23–29), `tests/test_seedance.py`, `tests/test_wan.py`. Create `src/character_swap/providers/generators/seedance_wavespeed.py`, `tests/test_seedance_wavespeed.py`.

The final resolutions and "does the default final upgrade the draft?" become attributes of each generator, so later tasks never ask `generator == "seedance"`. The WaveSpeed generator gets everything that needs no network here (resolutions, estimate, request body); `generate` comes in Task 6, after the client.

- [ ] **Step 1: Failing tests.** Append to `tests/test_seedance.py`:

```python
def test_piapi_finals_and_the_draft_upgrade():
    assert SeedanceGenerator.finals == ("1080p", "720p") and SeedanceGenerator.promotes is True
```

Append to `tests/test_wan.py`:

```python
def test_wan_has_one_final_and_never_upgrades():
    assert WanGenerator.finals == ("720p",) and WanGenerator.promotes is False
```

Create `tests/test_seedance_wavespeed.py`:

```python
import pytest

from character_swap.providers.errors import ProviderError
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator

PROMPT = "Replace the person in @Video 1 with the character in @Image 1."


def request(tmp_path, *, seconds=4.0, **extra):
    video = tmp_path / "prepared.mp4"
    video.write_bytes(b"v")
    image = tmp_path / "character-primary.jpg"
    image.write_bytes(b"i")
    return GenerationRequest(
        video=video, primary=CharacterRef(image), seconds=seconds, seed=1, prompt=PROMPT, **extra
    )


def test_estimates_bill_input_and_output_seconds(tmp_path):
    gen = SeedanceWaveSpeedGenerator(None)
    assert gen.estimate(request(tmp_path), "draft").total == pytest.approx(0.88)
    assert gen.estimate(request(tmp_path), "final").total == pytest.approx(1.76)
    req = request(tmp_path, final_resolution="1080p")
    assert gen.estimate(req, "final").total == pytest.approx(4.40)
    assert "input video" in gen.estimate(req, "final").describe()


@pytest.mark.parametrize(("seconds", "billed"), [(2.0, 4), (4.2, 5), (12.0, 12), (31.0, 30)])
def test_billed_seconds_round_up_and_stay_between_4_and_30(tmp_path, seconds, billed):
    estimate = SeedanceWaveSpeedGenerator(None).estimate(request(tmp_path, seconds=seconds), "draft")
    assert estimate.total == pytest.approx(billed * 2 * 0.11)


def test_the_draft_is_480p_and_the_default_final_is_720p(tmp_path):
    gen = SeedanceWaveSpeedGenerator
    assert gen.resolution(request(tmp_path), "draft") == "480p"
    assert gen.resolution(request(tmp_path), "final") == "720p"
    assert gen.resolution(request(tmp_path, final_resolution="1080p"), "final") == "1080p"


@pytest.mark.parametrize("bad", ["480p", "4k"])
def test_other_final_resolutions_are_refused(tmp_path, bad):
    with pytest.raises(ProviderError, match="720p or 1080p, not .*Nothing was charged"):
        SeedanceWaveSpeedGenerator(None).estimate(request(tmp_path, final_resolution=bad), "final")


def test_build_input_is_the_video_edit_body(tmp_path):
    body = SeedanceWaveSpeedGenerator.build_input(
        request(tmp_path), video_url="https://v", image_urls=["https://i"], resolution="480p"
    )
    assert body == {
        "prompt": PROMPT,
        "video": "https://v",
        "reference_images": ["https://i"],
        "resolution": "480p",
        "generate_audio": False,
    }


def test_capabilities():
    gen = SeedanceWaveSpeedGenerator
    assert (gen.name, gen.provider) == ("seedance", "wavespeed")
    assert gen.model == "bytedance/seedance-2.5/video-edit"
    assert (gen.min_seconds, gen.max_seconds) == (4, 30)
    assert gen.supports_real_faces is True and gen.supports_secondary is False
    assert gen.finals == ("720p", "1080p") and gen.promotes is False
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_seedance.py tests/test_wan.py tests/test_seedance_wavespeed.py -q --no-cov`. Expected: `AttributeError: ... has no attribute 'finals'` and `ModuleNotFoundError: ... seedance_wavespeed`.

- [ ] **Step 3: Protocol.** In `src/character_swap/providers/generators/base.py`, inside `class Generator(Protocol):`, after `supports_secondary: bool` add:

```python
    finals: tuple[str, ...]  # final resolutions this generator offers; the first is the default
    promotes: bool  # True: the default final upgrades the draft's own task (PiAPI 1080p)
```

- [ ] **Step 4: PiAPI and Wan values.** In `seedance_piapi.py`, in `class SeedanceGenerator`, after `supports_secondary = True` add:

```python
    finals = FINALS  # 1080p (default) upgrades the draft; 720p is a fresh run
    promotes = True
```

In `wan_replicate.py`, in `class WanGenerator`, after `supports_secondary = False` add:

```python
    finals = ("720p",)
    promotes = False
```

- [ ] **Step 5: The WaveSpeed generator (pure half).** Create `src/character_swap/providers/generators/seedance_wavespeed.py`:

```python
"""Seedance 2.5 on WaveSpeed: redraws the disguised person as your character."""

from __future__ import annotations

import math

from character_swap.costs import Estimate, cost_line
from character_swap.modes import GENERATOR_LIMITS
from character_swap.providers.errors import ProviderError
from character_swap.providers.generators.base import GenerationRequest, Stage

MODEL = "bytedance/seedance-2.5/video-edit"
DRAFT = "480p"
# First = default. Every final is a fresh run: WaveSpeed has no draft upgrade and no seed.
FINALS = ("720p", "1080p")
PRICE = {"480p": "wavespeed-480", "720p": "wavespeed-720", "1080p": "wavespeed-1080"}
BILLED_SECONDS = (4, 30)  # WaveSpeed bills at least 4 and at most 30 seconds of input video


class SeedanceWaveSpeedGenerator:
    name = "seedance"
    provider = "wavespeed"
    model = MODEL
    min_seconds, max_seconds = GENERATOR_LIMITS["seedance"]
    supports_real_faces = True  # a plain photo; no face library
    supports_secondary = False  # two people stay on PiAPI for now
    finals = FINALS
    promotes = False

    def __init__(self, wavespeed) -> None:
        self._wavespeed = wavespeed

    @staticmethod
    def resolution(request: GenerationRequest, stage: Stage) -> str:
        if stage == "draft":
            return DRAFT
        chosen = request.final_resolution or FINALS[0]
        if chosen not in FINALS:
            raise ProviderError(
                f"WaveSpeed finals are {' or '.join(FINALS)}, not {chosen}. Nothing was charged."
            )
        return chosen

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate:
        key = PRICE[self.resolution(request, stage)]
        low, high = BILLED_SECONDS
        seconds = min(max(math.ceil(request.seconds), low), high)  # only ever over-quote
        return Estimate(lines=(cost_line(key, seconds), cost_line(f"{key}-input", seconds)))

    @staticmethod
    def build_input(
        request: GenerationRequest, *, video_url: str, image_urls: list[str], resolution: str
    ) -> dict:
        return {
            "prompt": request.prompt,
            "video": video_url,
            "reference_images": list(image_urls),  # @Image 1
            "resolution": resolution,
            "generate_audio": False,  # the original soundtrack is restored locally afterwards
        }
```

- [ ] **Step 6: Run tests.** `uv run pytest tests/test_seedance.py tests/test_wan.py tests/test_seedance_wavespeed.py -q --no-cov`. Expected: PASS.

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/providers/generators/base.py src/character_swap/providers/generators/seedance_piapi.py src/character_swap/providers/generators/wan_replicate.py src/character_swap/providers/generators/seedance_wavespeed.py tests/test_seedance.py tests/test_wan.py tests/test_seedance_wavespeed.py
git commit -m "feat: generators expose finals and promotes; WaveSpeed generator prices and body"
```

---

### Task 3: Prompts use the provider's input names

**Files:** Modify `src/character_swap/prompts.py` (whole file), `src/character_swap/pipeline.py` (`default_prompt` lines 661–668, `generation_prompt` line 732, delete `_check_placeholders` lines 736–746, import line 31). Replace `tests/test_prompts.py`.

`build_prompt` takes the provider and fills `BASE`, `MODE_NOTES` and `SECONDARY` with its names; the placeholder check moves here as `check_placeholders` so the WaveSpeed generator can reuse it (decision P4). In this task the pipeline still passes `"piapi"` everywhere, so behaviour is unchanged; Task 7 switches it to the job's provider.

- [ ] **Step 1: Failing tests.** Replace `tests/test_prompts.py` with:

```python
import re

import pytest

from character_swap.prompts import (
    MAX_PROMPT,
    PromptError,
    build_prompt,
    check_placeholders,
    validate_prompt,
)


def test_depth_prompt_mentions_depth_and_references():
    text = build_prompt(provider="piapi", mode="depth")
    assert "@video1" in text and "@image1" in text and "depth" in text.lower()
    assert "@image2" not in text


def test_piapi_wording_is_unchanged():
    assert build_prompt(provider="piapi", mode="depth").startswith(
        "Replace the person in @video1 with the character in @image1 in every frame, including "
        "face, hair, hands, clothing and body shape."
    )


def test_wavespeed_prompts_use_wavespeeds_input_names():
    for mode in ("depth", "face_mesh", "depth_mesh"):
        text = build_prompt(provider="wavespeed", mode=mode, transcript="hi")
        assert "@Video 1" in text and "@Image 1" in text
        assert "@video1" not in text and "@image1" not in text


def test_an_unknown_provider_is_refused():
    with pytest.raises(PromptError, match="Unknown Seedance provider 'sora'"):
        build_prompt(provider="sora", mode="depth")


def test_face_mesh_prompt_removes_mesh_lines():
    assert "remove every mesh line" in build_prompt(provider="piapi", mode="face_mesh").lower()


def test_depth_mesh_mentions_both():
    text = build_prompt(provider="wavespeed", mode="depth_mesh").lower()
    assert "depth" in text and "mesh" in text


def test_scene_transcript_and_secondary_are_included():
    text = build_prompt(
        provider="piapi", mode="depth", scene="A loft.", transcript="Hi there", has_secondary=True
    )
    assert "Setting: A loft." in text
    assert '"Hi there"' in text
    assert "@image2" in text


def test_blank_scene_and_transcript_are_skipped():
    text = build_prompt(provider="piapi", mode="depth", scene="  ", transcript="")
    assert "Setting:" not in text and "spoken words" not in text


@pytest.mark.parametrize(
    ("provider", "names"),
    [("piapi", {"@video1", "@image1"}), ("wavespeed", {"@video 1", "@image 1"})],
)
def test_untrusted_text_cannot_inject_reference_tokens(provider, names):
    text = build_prompt(
        provider=provider,
        mode="depth",
        scene="see @Image3 and @Image 3",
        transcript="say @image9 and @Video2 and @@image9 and @@@video2 and @Video 2 now",
    )
    assert "@image9" not in text and "@image3" not in text.lower() and "@video2" not in text.lower()
    assert set(re.findall(r"@(?:image|video|audio) ?\d", text.lower())) == names


def test_quotes_and_newlines_are_neutralized():
    text = build_prompt(provider="piapi", mode="depth", transcript='He said "hi"\n\nthere')
    assert "\"He said 'hi' there\"" in text


def test_long_transcript_is_truncated_to_limit():
    text = build_prompt(
        provider="piapi",
        mode="depth",
        scene="x" * 1000,
        transcript="word " * 2000,
        has_secondary=True,
    )
    assert len(text) <= MAX_PROMPT
    assert "…" in text


def test_validate_prompt_limit():
    assert validate_prompt("a" * 4000) == "a" * 4000
    with pytest.raises(ValueError, match="Prompt is 4001 characters; the limit is 4000."):
        validate_prompt("a" * 4001)


@pytest.mark.parametrize(
    ("provider", "text", "secondary"),
    [
        ("wavespeed", "Put @Image 1 into @Video 1.", False),
        ("piapi", "Put @image1 into @video1.", False),
        ("piapi", "Put @image1 and @image2 into @video1.", True),
    ],
)
def test_check_placeholders_accepts_the_providers_names(provider, text, secondary):
    check_placeholders(text, provider=provider, has_secondary=secondary)


@pytest.mark.parametrize(
    ("provider", "text", "secondary", "words"),
    [
        ("wavespeed", "Swap @video1 for @image1.", False, "must mention @Video 1"),
        ("wavespeed", "Swap @Video 1, nothing else.", False, "must mention @Image 1"),
        ("wavespeed", "@Video 1 @Image 1 @Image 2", False, "mentions @Image 2"),
        ("piapi", "Swap @video1 please.", False, "must mention @image1"),
        ("piapi", "@video1 @image1", True, "must mention @image2"),
        ("piapi", "@video1 @image1 and @image2", False, "mentions @image2"),
    ],
)
def test_check_placeholders_refuses_names_that_do_not_match(provider, text, secondary, words):
    with pytest.raises(PromptError, match=words):
        check_placeholders(text, provider=provider, has_secondary=secondary)
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_prompts.py -q --no-cov`. Expected: `ImportError: cannot import name 'check_placeholders'`.

- [ ] **Step 3: Prompts module.** Replace `src/character_swap/prompts.py` with:

```python
"""Build the Seedance prompt the user reviews (and can edit) before generating."""

from __future__ import annotations

from character_swap.errors import CharSwapError

MAX_PROMPT = 4000
MAX_SCENE = 300

# How each provider names the inputs inside the prompt. The text sent must use these exactly.
NAMES: dict[str, dict[str, str]] = {
    "wavespeed": {"video": "@Video 1", "image1": "@Image 1", "image2": "@Image 2"},
    "piapi": {"video": "@video1", "image1": "@image1", "image2": "@image2"},
}

BASE = (
    "Replace the person in {video} with the character in {image1} in every frame, including "
    "face, hair, hands, clothing and body shape. Keep the original camera framing, motion, "
    "gestures, timing and lighting. Use the vocals embedded in {video} only for lip "
    "synchronization and performance; do not add new dialogue."
)
MODE_NOTES = {
    "depth": (
        "{video} shows the people as a colored depth map over the real background. Render them "
        "as fully realistic people and remove all depth colors."
    ),
    "face_mesh": (
        "{video} has a face-mesh wireframe drawn over the face. Use it only as expression and "
        "lip-motion guidance and remove every mesh line."
    ),
    "depth_mesh": (
        "{video} shows the people as a colored depth map with a face-mesh wireframe on top. Use "
        "both only as motion guidance; render realistic people and remove every depth color and "
        "mesh line."
    ),
}
SECONDARY = "{image2} is a second character; use them for the second person in the scene."


class PromptError(CharSwapError, ValueError):
    """The prompt cannot be sent as it is (for example it is too long)."""


def _names(provider: str) -> dict[str, str]:
    try:
        return NAMES[provider]
    except KeyError:
        raise PromptError(f"Unknown Seedance provider {provider!r}.") from None


def _clean(text: str) -> str:
    """Neutralize untrusted text: no "@" at all, no double quotes, one line."""
    text = text.replace("@", "").replace('"', "'")
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1]
    if " " in cut and text[limit - 1] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def validate_prompt(text: str) -> str:
    if len(text) > MAX_PROMPT:
        raise PromptError(f"Prompt is {len(text)} characters; the limit is {MAX_PROMPT}.")
    return text


def check_placeholders(text: str, *, provider: str, has_secondary: bool) -> None:
    """The provider refuses (or misreads) prompts whose input names don't match what is sent."""
    names = _names(provider)
    for needed in (names["video"], names["image1"]):
        if needed not in text:
            raise PromptError(f"The prompt must mention {needed} (the clip and your character).")
    second = names["image2"]
    if has_secondary and second not in text:
        raise PromptError(f"This job has a second character: the prompt must mention {second}.")
    if not has_secondary and second in text:
        raise PromptError(
            f"The prompt mentions {second} but this job has no second character; remove it."
        )


def build_prompt(
    *,
    provider: str,
    mode: str,
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
    words = _clean(transcript or "")
    if words:
        template = 'The spoken words are: "{}". Match the lip movements to these words.'
        used = len("\n\n".join(parts)) + 2 + len(template.format(""))
        parts.append(template.format(_truncate(words, MAX_PROMPT - used)))
    return validate_prompt("\n\n".join(parts))
```

- [ ] **Step 4: Pipeline uses it (still PiAPI names everywhere).** In `src/character_swap/pipeline.py`:
  - Change the import line `from character_swap.prompts import PromptError, build_prompt, validate_prompt` to:

    ```python
    from character_swap.prompts import PromptError, build_prompt, check_placeholders, validate_prompt
    ```

  - In `default_prompt`, add `provider="piapi",  # Task 7: the job's provider` as the first argument of `build_prompt(`, so it reads:

    ```python
    def default_prompt(job: Job) -> str:
        state = job.state
        return build_prompt(
            provider="piapi",  # Task 7: the job's provider
            mode=job.mode,
            scene=state.get("scene"),
            transcript=state.get("transcript"),
            has_secondary=bool(state.get("character_secondary")),
        )
    ```

  - In `generation_prompt`, replace
    `_check_placeholders(text, has_secondary=bool(job.state.get("character_secondary")))` with:

    ```python
        check_placeholders(
            text,
            provider="piapi",  # Task 7: the job's provider
            has_secondary=bool(job.state.get("character_secondary")),
        )
    ```

  - Delete the whole `def _check_placeholders(...)` function at the end of the file.

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_prompts.py tests/test_generation.py tests/test_cli.py -q --no-cov`. Expected: PASS (the PiAPI prompt text is byte-for-byte what it was).

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/prompts.py src/character_swap/pipeline.py tests/test_prompts.py
git commit -m "refactor: prompts take the provider's input names; placeholder check in prompts"
```

---

### Task 4: One shared download helper (`common.download_now`)

**Files:** Modify `src/character_swap/providers/common.py`, `src/character_swap/providers/piapi.py` (`download`, lines 199–222, and the `os` import), `tests/test_common.py`.

PiAPI's keyless download moves into `common` so the WaveSpeed client (Task 5) uses the same code. The expiry message names the provider and how long it keeps results; PiAPI's message stays exactly "This video link has expired (PiAPI keeps results 24 hours)." and still raises `PiAPIOutputExpired`.

- [ ] **Step 1: Failing tests.** In `tests/test_common.py`, add `import respx` after `import pytest`, add `from character_swap.providers.errors import OutputExpired, ProviderError` after the `common` import, and append:

```python
def _download(tmp_path, url, **kw):
    options = {
        "http": httpx.Client(),
        "sleep": lambda _s: None,
        "provider": "WaveSpeed",
        "keeps": "does not say how long it keeps results",
    }
    options.update(kw)
    return common.download_now(url, tmp_path / "raw.mp4", **options)


@respx.mock
def test_download_now_writes_the_file_without_any_key(tmp_path):
    route = respx.get("https://cdn.example/out.mp4").respond(200, content=b"mp4")
    dest = _download(tmp_path, "https://cdn.example/out.mp4")
    assert dest.read_bytes() == b"mp4"
    sent = route.calls[0].request.headers
    assert "authorization" not in sent and "x-api-key" not in sent
    assert [p.name for p in tmp_path.iterdir()] == ["raw.mp4"]  # no .part left behind


@pytest.mark.parametrize("code", [403, 404, 410])
@respx.mock
def test_download_now_names_the_provider_when_the_link_expired(tmp_path, code):
    respx.get("https://cdn.example/old.mp4").respond(code)
    with pytest.raises(OutputExpired, match=r"expired \(PiAPI keeps results 24 hours\)\."):
        _download(
            tmp_path, "https://cdn.example/old.mp4", provider="PiAPI", keeps="keeps results 24 hours"
        )


@respx.mock
def test_download_now_retries_5xx_then_raises_the_given_error(tmp_path):
    class Mine(ProviderError):
        pass

    route = respx.get("https://cdn.example/out.mp4").respond(503)
    with pytest.raises(Mine, match=r"Download failed \(503\)"):
        _download(tmp_path, "https://cdn.example/out.mp4", error=Mine)
    assert route.call_count == 3 and not (tmp_path / "raw.mp4").exists()


@respx.mock
def test_download_now_reports_a_lost_connection(tmp_path):
    respx.get("https://cdn.example/out.mp4").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ProviderError, match="Download failed"):
        _download(tmp_path, "https://cdn.example/out.mp4")
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_common.py -q --no-cov`. Expected: `AttributeError: module 'character_swap.providers.common' has no attribute 'download_now'`.

- [ ] **Step 3: The helper.** In `src/character_swap/providers/common.py`, add after `import httpx`:

```python

from character_swap.providers.errors import OutputExpired, ProviderError
```

After `FREE_ATTEMPTS = 3` add:

```python
EXPIRED_CODES = (403, 404, 410)  # a result link that is gone or no longer signed
```

Append at the end of the file:

```python
def download_now(
    url: str,
    dest: Path,
    *,
    http: httpx.Client,
    sleep: Callable[[float], None],
    provider: str,
    keeps: str,
    expired: type[OutputExpired] = OutputExpired,
    error: type[Exception] = ProviderError,
) -> Path:
    """Download a finished video right away, without any API key (the link is the access).

    `keeps` finishes the expiry message, e.g. "keeps results 24 hours". The file is written
    next to `dest` first and then moved into place, so a crash never leaves half a video.
    """

    def fetch() -> bytes:
        response = http.get(url, follow_redirects=True)
        response.raise_for_status()
        return response.content

    dest = Path(dest)
    try:
        data = retry_free(fetch, sleep=sleep)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in EXPIRED_CODES:
            raise expired(f"This video link has expired ({provider} {keeps}).") from exc
        raise error(f"Download failed ({exc.response.status_code}): {url}") from exc
    except httpx.TransportError as exc:
        raise error(f"Download failed: {exc!r}") from exc
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(data)
    os.replace(part, dest)
    return dest
```

- [ ] **Step 4: PiAPI uses it.** In `src/character_swap/providers/piapi.py`, replace the whole `def download(self, url: str, dest: Path) -> Path:` method with:

```python
    def download(self, url: str, dest: Path) -> Path:
        """Download a finished video without the API key (it is not an API call)."""
        return common.download_now(
            url,
            dest,
            http=self._http,
            sleep=self._sleep,
            provider="PiAPI",
            keeps="keeps results 24 hours",
            expired=PiAPIOutputExpired,
            error=PiAPIError,
        )
```

Then delete `import os` at the top of `piapi.py` (nothing else uses it; ruff flags it otherwise).

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_common.py tests/test_piapi.py tests/test_seedance.py -q --no-cov`. Expected: PASS (`test_download_has_no_key_and_expiry_is_typed` in `test_piapi.py` still sees "24 hours").

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/providers/common.py src/character_swap/providers/piapi.py tests/test_common.py
git commit -m "refactor: shared keyless download_now helper; PiAPI uses it"
```

---

### Task 5: The WaveSpeed client (`providers/wavespeed.py`) and `FakeWaveSpeed`

**Files:** Create `src/character_swap/providers/wavespeed.py`, `tests/test_wavespeed.py`. Modify `tests/fakes.py`.

Same money-safe shape as `PiAPIClient.run()`:
- the `creating` marker is claimed exclusively **before** the one POST;
- the prediction `{id, status, task}` is saved before waiting and re-saved on every poll;
- a saved record is resumed (`creating` → the unconfirmed message, `failed` → the failure message, anything else → poll); a resume never POSTs;
- the POST is never retried, not even on 429; free GETs (upload ticket, PUT, poll, download) are retried on 429/5xx.

Error mapping on the create POST (decision P5):

| Answer | Result | Marker |
|---|---|---|
| 400 / 401 / 403 / 404 / 422 | `WaveSpeedRejected(code)`: "WaveSpeed rejected the request (...); nothing was charged" (401 names `WAVESPEED_API_KEY`) | released |
| 429 | `WaveSpeedRejected(429)`: "WaveSpeed is busy (429); nothing was charged, try again in a minute" | released |
| one of those, but the body names a prediction id | that prediction is saved; error says it may be charged and to check the dashboard | replaced by the prediction |
| 5xx, other 4xx (e.g. 402), unreadable body, 2xx without `data.id` | ambiguous: "WaveSpeed answered <code>: <first 200 characters of its answer, on one line>." plus "check your WaveSpeed dashboard (Predictions) before running again" (never the key or our request headers) | kept |
| timeout or lost connection (no answer) | ambiguous: the same dashboard message | kept |

- [ ] **Step 1: The fake.** Append to `tests/fakes.py`:

```python
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
        if record.exists() and json.loads(record.read_text()).get("status") == "succeeded":
            return self.output  # resumed from the record: no new prediction, no new cost
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
```

- [ ] **Step 2: Failing tests.** Create `tests/test_wavespeed.py`:

```python
import json

import httpx
import pytest
import respx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired
from character_swap.providers.wavespeed import (
    API,
    MAX_UPLOAD_BYTES,
    SUBMIT_URL,
    UPLOAD_URL,
    WaveSpeedClient,
    WaveSpeedError,
    WaveSpeedRejected,
    prediction_status,
)

KEY = "ws-secret-key-123"
BODY = {
    "prompt": "Replace the person in @Video 1 with the character in @Image 1.",
    "video": "https://cdn.wavespeed.ai/media/clip.mp4",
    "reference_images": ["https://cdn.wavespeed.ai/media/me.jpg"],
    "resolution": "480p",
    "generate_audio": False,
}
VIDEO = "https://cdn.wavespeed.ai/outputs/out.mp4"
RESULT = f"{API}/predictions/p1/result"
PUT_URL = "https://storage.example/upload/a"


def client(**kw):
    return WaveSpeedClient(KEY, poll_seconds=0, sleep=lambda _s: None, **kw)


def answer(status, pid="p1", **extra):
    return {"code": 200, "message": "success", "data": {"id": pid, "status": status, **extra}}


@pytest.mark.parametrize(
    ("raw", "ours"),
    [
        ("created", "starting"),
        ("queued", "starting"),
        ("pending", "starting"),
        (None, "starting"),
        ("processing", "processing"),
        ("something-new", "processing"),
        ("completed", "succeeded"),
        ("COMPLETED", "succeeded"),
        ("failed", "failed"),
        ("cancelled", "failed"),
        ("timeout", "failed"),
        ("deleted", "failed"),
    ],
)
def test_prediction_status_maps_to_the_pipelines_words(raw, ours):
    assert prediction_status(raw) == ours


# ---- upload (free) -------------------------------------------------------------------
def _ticket(**upload):
    target = {"method": "PUT", "url": PUT_URL, "headers": {"Content-Type": "video/mp4"}}
    target.update(upload)
    return {
        "code": 200,
        "data": {"download_url": "https://cdn.wavespeed.ai/media/a.mp4", "upload": target},
    }


@respx.mock
def test_upload_asks_for_a_ticket_then_puts_the_bytes_without_the_key(tmp_path):
    clip = tmp_path / "prepared.mp4"
    clip.write_bytes(b"video-bytes")
    ticket = respx.post(UPLOAD_URL).respond(200, json=_ticket())
    put = respx.put(PUT_URL).respond(200)
    assert client().upload(clip) == "https://cdn.wavespeed.ai/media/a.mp4"
    asked = ticket.calls[0].request
    assert asked.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(asked.content) == {"filename": "prepared.mp4", "size": 11}
    stored = put.calls[0].request
    assert stored.content == b"video-bytes" and stored.headers["content-type"] == "video/mp4"
    assert "authorization" not in stored.headers


@respx.mock
def test_upload_refuses_a_file_over_200_mb_before_sending(tmp_path):
    big = tmp_path / "big.mp4"
    with big.open("wb") as file:
        file.truncate(MAX_UPLOAD_BYTES + 1)  # sparse: no real 200 MB written
    with pytest.raises(WaveSpeedError, match="under 200 MB. Nothing was charged"):
        client().upload(big)
    assert respx.calls.call_count == 0


@pytest.mark.parametrize(
    "ticket",
    [
        {"code": 200, "data": {}},
        _ticket(url="http://insecure.example/a"),
        _ticket(method="POST"),
        {"code": 200, "data": {"upload": _ticket()["data"]["upload"]}},
    ],
)
@respx.mock
def test_an_upload_answer_without_usable_links_is_an_error(tmp_path, ticket):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(200, json=ticket)
    with pytest.raises(WaveSpeedError, match="no usable link"):
        client().upload(photo)


@respx.mock
def test_a_failing_put_is_retried_then_reported(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(200, json=_ticket())
    put = respx.put(PUT_URL).respond(503)
    with pytest.raises(WaveSpeedError, match="503 while uploading me.jpg.*Nothing was charged"):
        client().upload(photo)
    assert put.call_count == 3


@respx.mock
def test_a_rejected_key_names_the_setting_but_never_the_key(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(401)
    with pytest.raises(WaveSpeedError, match="WAVESPEED_API_KEY") as caught:
        client().upload(photo)
    assert KEY not in str(caught.value)


# ---- paid run ----------------------------------------------------------------------------
@respx.mock
def test_run_creates_once_saves_before_waiting_and_returns_the_video(tmp_path):
    record = tmp_path / "draft-prediction.json"
    create = respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    seen_while_waiting = []

    def poll(request):
        seen_while_waiting.append(json.loads(record.read_text())["id"])
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json=answer("completed", outputs=[VIDEO]))

    respx.get(RESULT).mock(side_effect=poll)
    created = []
    assert client().run(body=BODY, record=record, on_created=created.append) == VIDEO
    assert create.call_count == 1 and json.loads(create.calls[0].request.content) == BODY
    assert create.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    assert seen_while_waiting == ["p1"]  # the id was on disk before the first poll
    assert created == [{"id": "p1", "status": "starting", "task": {"id": "p1", "status": "created"}}]
    saved = json.loads(record.read_text())
    assert saved["id"] == "p1" and saved["status"] == "succeeded"
    assert saved["task"]["outputs"] == [VIDEO]


@respx.mock
def test_an_output_object_with_a_url_works_too(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("processing"))
    respx.get(RESULT).respond(200, json=answer("completed", outputs=[{"url": VIDEO}]))
    assert client().run(body=BODY, record=tmp_path / "r.json") == VIDEO


@respx.mock
def test_a_saved_prediction_is_resumed_never_posted_again(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing", "task": {}}))
    create = respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(200, json=answer("completed", outputs=[VIDEO]))
    assert client().run(body={}, record=record) == VIDEO
    assert create.call_count == 0


@respx.mock
def test_a_finished_record_returns_without_any_call(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(
        json.dumps({"id": "p1", "status": "succeeded", "task": {"outputs": [VIDEO]}})
    )
    assert client().run(body={}, record=record) == VIDEO
    assert respx.calls.call_count == 0


@respx.mock
def test_an_unconfirmed_marker_refuses_without_posting(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "owner": "x"}))
    with pytest.raises(WaveSpeedError, match="may have been charged.*WaveSpeed dashboard"):
        client().run(body=BODY, record=record)
    assert respx.calls.call_count == 0


@respx.mock
def test_a_failed_record_raises_without_posting_or_polling(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(
        json.dumps({"id": "p1", "status": "failed", "task": {"error": "content flagged"}})
    )
    with pytest.raises(WaveSpeedError, match="content flagged.*may be charged.*dashboard"):
        client().run(body=BODY, record=record)
    assert respx.calls.call_count == 0


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
@respx.mock
def test_a_clear_refusal_releases_the_marker_and_says_nothing_was_charged(tmp_path, code):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(code, json={"code": code, "message": "no"})
    with pytest.raises(WaveSpeedRejected, match="(?i)nothing was charged") as caught:
        client().run(body=BODY, record=record)
    assert caught.value.status_code == code and not record.exists()
    assert KEY not in str(caught.value)


@respx.mock
def test_429_is_busy_not_retried_and_releases_the_marker(tmp_path):
    record = tmp_path / "r.json"
    create = respx.post(SUBMIT_URL).respond(429)
    with pytest.raises(WaveSpeedRejected, match="busy.*nothing was charged.*in a minute"):
        client().run(body=BODY, record=record)
    assert create.call_count == 1 and not record.exists()


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(402, json={"code": 402, "message": "insufficient balance"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"code": 200, "data": {"status": "created"}}),
    ],
)
@respx.mock
def test_unclear_answers_keep_the_marker_and_point_at_the_dashboard(tmp_path, reply):
    record = tmp_path / "r.json"
    create = respx.post(SUBMIT_URL).mock(return_value=reply)
    with pytest.raises(WaveSpeedError, match="may have been charged.*WaveSpeed dashboard") as caught:
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "creating" and create.call_count == 1
    assert f"WaveSpeed answered {reply.status_code}: " in str(caught.value)
    assert KEY not in str(caught.value) and "Bearer" not in str(caught.value)


@respx.mock
def test_an_unclear_answer_shows_one_short_line_of_what_wavespeed_said(tmp_path):
    respx.post(SUBMIT_URL).respond(402, text="insufficient\n   balance " + "x" * 500)
    with pytest.raises(WaveSpeedError) as caught:
        client().run(body=BODY, record=tmp_path / "r.json")
    message = str(caught.value)
    said = message.split("WaveSpeed answered 402: ", 1)[1].split(". The request was sent", 1)[0]
    assert said.startswith("insufficient balance x")  # whitespace and line breaks collapsed
    assert "\n" not in message and len(said) <= 200 and said.endswith("…")
    assert KEY not in message


@respx.mock
def test_a_timeout_keeps_the_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(WaveSpeedError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "creating"


@respx.mock
def test_a_refusal_that_still_names_a_prediction_is_saved_not_released(tmp_path):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(
        400, json={"code": 400, "message": "odd", "data": {"id": "p9", "status": "created"}}
    )
    created = []
    with pytest.raises(WaveSpeedError, match=r"prediction p9.*may be charged") as caught:
        client().run(body=BODY, record=record, on_created=created.append)
    assert not isinstance(caught.value, WaveSpeedRejected) and str(record) in str(caught.value)
    saved = json.loads(record.read_text())
    assert saved["id"] == "p9" and saved["status"] == "starting"
    assert [c["id"] for c in created] == ["p9"]


def test_an_empty_body_is_refused_before_anything_is_sent(tmp_path):
    with pytest.raises(WaveSpeedError, match="no input"):
        client().run(body={}, record=tmp_path / "r.json")
    assert not (tmp_path / "r.json").exists()


@respx.mock
def test_a_failed_prediction_says_it_may_be_charged_and_keeps_its_record(tmp_path):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    poll = respx.get(RESULT).respond(200, json=answer("failed", error="content flagged"))
    with pytest.raises(
        WaveSpeedError, match=r"content flagged.*may be charged.*dashboard.*delete .*r\.json"
    ):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "failed"  # kept: never re-bought silently
    with pytest.raises(WaveSpeedError, match="failed"):
        client().run(body=BODY, record=record)  # no second POST, no polling
    assert poll.call_count == 1


@respx.mock
def test_finished_without_a_video_link_is_an_error(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(200, json=answer("completed", outputs=[]))
    with pytest.raises(WaveSpeedError, match="without a video link"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_a_busy_poll_is_retried_for_free(tmp_path):
    create = respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, json=answer("completed", outputs=[VIDEO])),
        ]
    )
    assert client().run(body=BODY, record=tmp_path / "r.json") == VIDEO
    assert create.call_count == 1


@respx.mock
def test_still_running_after_max_polls_is_resumable(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(200, json=answer("processing"))
    with pytest.raises(WaveSpeedError, match="still running.*no new charge"):
        client(max_polls=2).run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_lost_connection_while_waiting_is_resumable(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WaveSpeedError, match="no new charge"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_a_poll_answer_for_another_prediction_leaves_the_record_alone(tmp_path):
    record = tmp_path / "r.json"
    first = {"id": "p1", "status": "processing", "task": {"id": "p1"}}
    record.write_text(json.dumps(first))
    respx.get(RESULT).respond(200, json=answer("completed", pid="p2", outputs=[VIDEO]))
    with pytest.raises(WaveSpeedError, match="no new charge"):
        client().run(body={}, record=record)
    assert json.loads(record.read_text()) == first


@respx.mock
def test_polling_backs_off_from_5_to_15_seconds(tmp_path):
    waits = []
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    answers = [answer("processing")] * 4 + [answer("completed", outputs=[VIDEO])]
    respx.get(RESULT).mock(side_effect=[httpx.Response(200, json=a) for a in answers])
    WaveSpeedClient(KEY, sleep=waits.append).run(body=BODY, record=tmp_path / "r.json")
    assert waits == [5, 7.5, 11.25, 15, 15]


@respx.mock
def test_record_save_failure_still_reports_the_paid_prediction(tmp_path, monkeypatch):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    monkeypatch.setattr(common, "save", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    created = []
    with pytest.raises(WaveSpeedError, match="p1"):
        client().run(body=BODY, record=tmp_path / "r.json", on_created=created.append)
    assert [c["id"] for c in created] == ["p1"]


@respx.mock
def test_a_failing_logger_warning_points_at_the_caller(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("completed", outputs=[VIDEO]))

    def broken(_prediction):
        raise OSError("disk full")

    with pytest.warns(UserWarning, match="Could not log") as caught:
        assert client().run(body=BODY, record=tmp_path / "r.json", on_created=broken) == VIDEO
    assert caught[0].filename == __file__


@respx.mock
def test_a_damaged_record_is_never_replaced(tmp_path):
    record = tmp_path / "r.json"
    record.write_text("{not json")
    with pytest.raises(WaveSpeedError, match="damaged.*dashboard"):
        client().run(body=BODY, record=record)
    assert record.read_text() == "{not json" and respx.calls.call_count == 0


# ---- download ----------------------------------------------------------------------------
@respx.mock
def test_download_has_no_key_and_expiry_is_typed(tmp_path):
    route = respx.get(VIDEO).respond(200, content=b"mp4")
    dest = client().download(VIDEO, tmp_path / "raw.mp4")
    assert dest.read_bytes() == b"mp4" and "authorization" not in route.calls[0].request.headers
    respx.get("https://cdn.wavespeed.ai/outputs/old.mp4").respond(404)
    with pytest.raises(OutputExpired, match="WaveSpeed"):
        client().download("https://cdn.wavespeed.ai/outputs/old.mp4", tmp_path / "old.mp4")
```

- [ ] **Step 3: Run them and watch them fail.** `uv run pytest tests/test_wavespeed.py -q --no-cov`. Expected: `ModuleNotFoundError: No module named 'character_swap.providers.wavespeed'`.

- [ ] **Step 4: The client.** Create `src/character_swap/providers/wavespeed.py`:

```python
"""The only module that talks to WaveSpeed. Paid predictions are sent once and persisted."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired, ProviderError

API = "https://api.wavespeed.ai/api/v3"
MODEL = "bytedance/seedance-2.5/video-edit"
SUBMIT_URL = f"{API}/{MODEL}"
UPLOAD_URL = f"{API}/media/uploads"
MAX_UPLOAD_BYTES = 209_715_200  # 200 MB; WaveSpeed keeps uploads 7 days
POLL_GROWTH = 1.5  # each wait is 1.5x the last, up to max_poll_seconds
DASHBOARD = "your WaveSpeed dashboard (https://wavespeed.ai, Predictions)"
# A clear refusal of the paid create: no prediction exists and nothing was charged.
REJECTED = (400, 401, 403, 404, 422)
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")

KEY_MESSAGE = (
    "Your WAVESPEED_API_KEY was rejected. Check .env (get it at https://wavespeed.ai, "
    "Dashboard > API Keys)."
)
BUSY_MESSAGE = (
    "WaveSpeed is busy (429); nothing was charged, try again in a minute with the same command."
)


class WaveSpeedError(ProviderError):
    """WaveSpeed rejected, failed or has not finished a request."""


class WaveSpeedRejected(WaveSpeedError):
    """WaveSpeed refused the paid create outright: no prediction exists, nothing was charged."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class WaveSpeedOutputExpired(WaveSpeedError, OutputExpired):
    """The finished prediction's video link is gone; only a new (paid) run recovers it."""


def prediction_status(raw: object) -> str:
    """WaveSpeed's status words, mapped to the pipeline's (Replicate's) words."""
    word = str(raw or "").strip().lower()
    if word == "completed":
        return "succeeded"
    if word in ("failed", "cancelled", "canceled", "timeout", "deleted"):
        return "failed"
    if word in ("", "created", "queued", "pending"):
        return "starting"
    return "processing"  # processing, or a word WaveSpeed adds later: still running


def _data(body: object) -> dict:
    """WaveSpeed wraps every answer as {code, message, data}."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return {}


def _envelope_error(body: object) -> str | None:
    """The message of an answer whose own `code` says it failed (HTTP can still be 200)."""
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if isinstance(code, int) and code >= 400:
        return str(body.get("message") or f"code {code}")
    return None


def _prediction_in(response: httpx.Response) -> dict | None:
    """The prediction a response names, if its body is JSON with a valid data.id."""
    try:
        data = _data(response.json())
    except ValueError:
        return None
    prediction_id = data.get("id")
    if not isinstance(prediction_id, str) or not _ID.fullmatch(prediction_id):
        return None
    return {"id": prediction_id, "status": prediction_status(data.get("status")), "task": data}


class WaveSpeedClient:
    def __init__(
        self,
        key: str,
        *,
        http: httpx.Client | None = None,
        poll_seconds: float = 5.0,
        max_poll_seconds: float = 15.0,
        max_polls: int = 720,  # ~3 h at the 15 s ceiling; a 4 s draft takes ~3 minutes
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = http or httpx.Client(timeout=httpx.Timeout(180, connect=15))
        self._headers = {"Authorization": f"Bearer {key}"}  # sent per call, never logged
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
            raise WaveSpeedError(f"WaveSpeed sent an unreadable answer for {url}.") from error

    def _free(self, call: Callable[[], Any], what: str) -> Any:
        """A retried free call whose final failure is a WaveSpeedError."""
        try:
            return common.retry_free(call, sleep=self._sleep)
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if code == 401:
                raise WaveSpeedError(KEY_MESSAGE) from error
            raise WaveSpeedError(
                f"WaveSpeed returned {code} while {what}: {error.response.text[:200]}. "
                "Nothing was charged."
            ) from error
        except httpx.TransportError as error:
            raise WaveSpeedError(
                f"Could not reach WaveSpeed while {what}: {error!r}. Nothing was charged."
            ) from error

    def upload(self, path: Path) -> str:
        """Upload a file WaveSpeed can read for 7 days; returns its link. Free."""
        path = Path(path)
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise WaveSpeedError(
                f"{path.name} is {size / 2**20:.0f} MB; WaveSpeed uploads must stay under "
                f"{MAX_UPLOAD_BYTES // 2**20} MB. Nothing was charged."
            )
        ticket = self._free(
            lambda: self._send("POST", UPLOAD_URL, json={"filename": path.name, "size": size}),
            "asking for an upload link",
        )
        problem = _envelope_error(ticket)
        if problem:
            raise WaveSpeedError(
                f"WaveSpeed refused the upload of {path.name}: {problem}. Nothing was charged."
            )
        data = _data(ticket)
        link = data.get("download_url")
        target = data.get("upload") if isinstance(data.get("upload"), dict) else {}
        put_url, headers = target.get("url"), target.get("headers") or {}
        if not (
            isinstance(link, str)
            and link.startswith("https://")
            and isinstance(put_url, str)
            and put_url.startswith("https://")
            and str(target.get("method") or "PUT").upper() == "PUT"
            and isinstance(headers, dict)
        ):
            raise WaveSpeedError(
                f"WaveSpeed's upload answer for {path.name} had no usable link. "
                "Nothing was charged."
            )
        content = path.read_bytes()
        put_headers = {str(k): str(v) for k, v in headers.items()}  # signed URL: no API key

        def put() -> None:
            self._http.request("PUT", put_url, headers=put_headers, content=content).raise_for_status()

        self._free(put, f"uploading {path.name}")
        return link

    def download(self, url: str, dest: Path) -> Path:
        """Download a finished video right away, without the API key."""
        return common.download_now(
            url,
            dest,
            http=self._http,
            sleep=self._sleep,
            provider="WaveSpeed",
            keeps="does not say how long it keeps results",
            expired=WaveSpeedOutputExpired,
            error=WaveSpeedError,
        )

    # ---- paid call (never retried) -------------------------------------------
    def run(
        self,
        *,
        body: dict,
        record: Path,
        on_created: Callable[[dict], None] | None = None,
    ) -> str:
        """Create a prediction once, save it to `record`, wait, and return the video link.

        A marker is created exclusively BEFORE the paid POST, so a crash or a timeout can
        never lead to a second charge. If `record` exists, it is resumed instead.
        `on_created(prediction)` runs once, right after WaveSpeed confirms a NEW prediction.
        """
        record = Path(record)
        prediction = None
        if record.exists():
            try:
                prediction = self._load(record)
            except FileNotFoundError:
                prediction = None  # removed since the check; the exclusive claim still guards
        if prediction is None:
            prediction = self._create(body, record, on_created)
        elif prediction["status"] == "creating":
            raise WaveSpeedError(unconfirmed_message(record))
        elif prediction["status"] == "failed":
            raise WaveSpeedError(failed_message(prediction, record))
        return self._output(self._wait(prediction, record), record)

    @staticmethod
    def _load(record: Path) -> dict:
        damaged = WaveSpeedError(
            f"{record} is damaged. The prediction may still exist: check {DASHBOARD} before "
            "deleting it (deleting and running again costs money)."
        )
        try:
            data = json.loads(record.read_text())
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise damaged from error
        if not isinstance(data, dict) or "status" not in data:
            raise damaged
        if data["status"] != "creating" and not _ID.fullmatch(str(data.get("id", ""))):
            raise damaged
        return data

    def _create(self, body: dict, record: Path, on_created: Callable[[dict], None] | None) -> dict:
        if not body:
            # Resumes pass {} because the record exists; if it vanished since, posting {}
            # would buy a prediction with no input. Nothing sent, no marker written.
            raise WaveSpeedError(
                f"Refusing to create a paid prediction with no input; {record} vanished during "
                "resume. Run the command again."
            )
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker = common.claim(record)
        except FileExistsError as error:
            raise WaveSpeedError(unconfirmed_message(record)) from error
        try:
            response = self._http.post(SUBMIT_URL, headers=self._headers, json=body)  # once
        except httpx.HTTPError as error:
            raise WaveSpeedError(unconfirmed_message(record)) from error
        code = response.status_code
        if code in REJECTED or code == 429:
            found = _prediction_in(response)
            if found is not None:  # a refusal that still names a prediction: it may be billed
                self._keep(found, record, on_created)
                raise WaveSpeedError(
                    f"WaveSpeed refused the request ({code}) but still returned prediction "
                    f"{found['id']}; it may be charged. It is saved in {record}; check "
                    f"{DASHBOARD} before deleting it."
                )
            message = BUSY_MESSAGE if code == 429 else _rejection(code, response.text)
            self._release(record, marker, message)
            raise WaveSpeedRejected(message, code)
        if response.is_error:  # 5xx or another 4xx (e.g. 402): a prediction may exist
            raise WaveSpeedError(_ambiguous(response, record))
        prediction = _prediction_in(response)
        if prediction is None:  # unreadable, or no id: it may still have been created
            raise WaveSpeedError(_ambiguous(response, record))
        self._keep(prediction, record, on_created)
        return prediction

    @staticmethod
    def _keep(prediction: dict, record: Path, on_created: Callable[[dict], None] | None) -> None:
        """Save a newly created (paid) prediction and announce it, even if saving fails."""
        what = "paid WaveSpeed prediction"
        try:
            common.save(record, prediction)
        except OSError as error:
            common.announce(on_created, prediction, what=what, stacklevel=5)  # paid: tell anyway
            raise WaveSpeedError(
                f"Paid WaveSpeed prediction {prediction['id']} was created but its record could "
                f"not be saved ({error}). Note this id; check {DASHBOARD}."
            ) from error
        common.announce(on_created, prediction, what=what, stacklevel=5)

    @staticmethod
    def _release(record: Path, marker: dict, nothing_charged: str) -> None:
        common.release(
            record,
            marker,
            nothing_charged=nothing_charged,
            taken_over=_taken_over(record),
            error=WaveSpeedError,
        )

    def _wait(self, prediction: dict, record: Path) -> dict:
        polls, delay = 0, self._poll_seconds
        while prediction["status"] not in ("succeeded", "failed"):
            if polls >= self._max_polls:
                raise WaveSpeedError(
                    f"WaveSpeed prediction {prediction['id']} is still running (saved in "
                    f"{record}). Run the same command later to keep waiting; no new charge."
                )
            self._sleep(delay)
            delay = min(delay * POLL_GROWTH, self._max_poll_seconds)
            # Built from the saved id: the key never goes to a URL taken from a response.
            url = f"{API}/predictions/{prediction['id']}/result"
            try:
                answer = common.retry_free(lambda: self._send("GET", url), sleep=self._sleep)  # noqa: B023
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if code == 401:
                    raise WaveSpeedError(KEY_MESSAGE) from error
                if code == 404:
                    raise WaveSpeedError(
                        f"WaveSpeed no longer recognizes prediction {prediction['id']}; check "
                        f"{DASHBOARD}."
                    ) from error
                raise self._lost(prediction, record) from error
            except (httpx.TransportError, WaveSpeedError) as error:
                raise self._lost(prediction, record) from error
            data = _data(answer)
            if (
                _envelope_error(answer)
                or "status" not in data
                or data.get("id") not in (None, prediction["id"])
            ):
                raise self._lost(prediction, record)  # before saving: the record stays as it was
            prediction = {
                "id": prediction["id"],
                "status": prediction_status(data.get("status")),
                "task": data,
            }
            try:
                common.save(record, prediction)
            except OSError as error:
                raise self._lost(prediction, record) from error
            polls += 1
        if prediction["status"] == "failed":
            raise WaveSpeedError(failed_message(prediction, record))
        return prediction

    @staticmethod
    def _output(prediction: dict, record: Path) -> str:
        outputs = (prediction.get("task") or {}).get("outputs")
        first = outputs[0] if isinstance(outputs, list) and outputs else None
        url = first.get("url") if isinstance(first, dict) else first
        if not isinstance(url, str) or not url.startswith("https://"):
            raise WaveSpeedError(
                f"WaveSpeed prediction {prediction['id']} finished without a video link. Check "
                f"{DASHBOARD} before deleting {record} (a new run costs money)."
            )
        return url

    @staticmethod
    def _lost(prediction: dict, record: Path) -> WaveSpeedError:
        return WaveSpeedError(
            f"Lost connection while waiting; WaveSpeed prediction {prediction['id']} is saved in "
            f"{record}. Run the same command again to keep waiting; no new charge."
        )


def unconfirmed_message(record: Path) -> str:
    """Guidance when a record holds only our "creating" marker (POST never confirmed)."""
    return (
        "The request was sent to WaveSpeed but not confirmed. It may have been charged, or "
        "another charswap command for this job is still sending it. First make sure no other "
        f"charswap command is running for this job. Then check {DASHBOARD} before running "
        f"again; if nothing is there, delete {record} and run again."
    )


def failed_message(prediction: dict, record: Path) -> str:
    error = (prediction.get("task") or {}).get("error")
    reason = str(error).strip() if error else ""
    return (
        f"WaveSpeed prediction {prediction.get('id')} failed: {reason or 'no reason given'}. "
        f"It was created, so it may be charged; check {DASHBOARD}. To try again, delete "
        f"{record} and run the same command (it shows the cost and asks first)."
    )


def _taken_over(record: Path) -> str:
    return (
        f"Another charswap run took over {record} while this one was talking to WaveSpeed; "
        "this run sent nothing more and was not charged for it. Let the other run finish, "
        "or run the command again to resume what it saved."
    )


def _excerpt(text: str, limit: int = 200) -> str:
    """One line of a response body, for messages (WaveSpeed's answer only: never our headers)."""
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _ambiguous(response: httpx.Response, record: Path) -> str:
    """An unclear create answer: show what WaveSpeed said (e.g. a low balance), keep the marker."""
    said = _excerpt(response.text) or "(empty answer)"
    return f"WaveSpeed answered {response.status_code}: {said}. {unconfirmed_message(record)}"


def _rejection(code: int, body: str) -> str:
    if code == 401:
        return f"{KEY_MESSAGE} Nothing was charged."
    return f"WaveSpeed rejected the request ({code}); nothing was charged: {body[:300]}"
```

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_wavespeed.py -q --no-cov`. Expected: PASS. If `test_a_failing_logger_warning_points_at_the_caller` points at the wrong file, the call depth changed: `run → _create → _keep → announce` must stay exactly as written (stacklevel 5 lands on the test).

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/providers/wavespeed.py tests/test_wavespeed.py tests/fakes.py
git commit -m "feat: WaveSpeed client: upload, one paid create with claim marker, poll, download"
```

---

### Task 6: The WaveSpeed generator makes drafts and finals

**Files:** Modify `src/character_swap/providers/generators/seedance_wavespeed.py`, `tests/test_seedance_wavespeed.py`.

`generate` runs the free checks first (prompt not empty, WaveSpeed's names, no second person), then uploads the video (a compressed copy if needed, deleted afterwards) and the photo, then `run`, then `download`. An existing record resumes without uploading. A final is always a fresh run: `draft_task_id` is never sent.

- [ ] **Step 1: Failing tests.** In `tests/test_seedance_wavespeed.py`, replace the import block at the top with:

```python
import dataclasses
import json

import pytest

from character_swap.errors import CharSwapError
from character_swap.media.upload import UploadTooLarge
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators import seedance_wavespeed
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from fakes import FakeWaveSpeed
```

and append:

```python
def generate(gen, req, stage, tmp_path, **kw):
    return gen.generate(
        req,
        stage,
        record=tmp_path / f"{stage}-prediction.json",
        dest=tmp_path / f"{stage}-raw.mp4",
        **kw,
    )


def test_a_draft_uploads_the_clip_and_photo_and_sends_one_body(tmp_path):
    fake = FakeWaveSpeed()
    created = []
    gen = SeedanceWaveSpeedGenerator(fake)
    out = generate(gen, request(tmp_path), "draft", tmp_path, on_created=created.append)
    assert out.read_bytes() == b"mp4" and [c["id"] for c in created] == ["pred-1"]
    assert [p.name for p in fake.uploads] == ["prepared.mp4", "character-primary.jpg"]
    assert fake.bodies == [
        {
            "prompt": PROMPT,
            "video": "https://cdn.wavespeed.ai/uploads/prepared.mp4",
            "reference_images": ["https://cdn.wavespeed.ai/uploads/character-primary.jpg"],
            "resolution": "480p",
            "generate_audio": False,
        }
    ]


@pytest.mark.parametrize(("chosen", "sent"), [("", "720p"), ("1080p", "1080p")])
def test_a_final_is_a_fresh_run_never_an_upgrade(tmp_path, chosen, sent):
    fake = FakeWaveSpeed()
    req = request(tmp_path, final_resolution=chosen, draft_task_id="pred-1")
    generate(SeedanceWaveSpeedGenerator(fake), req, "final", tmp_path)
    (body,) = fake.bodies
    assert body["resolution"] == sent and body["prompt"] == PROMPT
    assert "draft_task_id" not in body and len(fake.uploads) == 2


@pytest.mark.parametrize(
    ("prompt", "words"),
    [
        ("  ", "charswap prompt"),
        ("Swap @video1 for @image1.", "must mention @Video 1"),
        ("Swap @Video 1, nothing else.", "must mention @Image 1"),
    ],
)
def test_prompt_problems_stop_before_any_upload(tmp_path, prompt, words):
    fake = FakeWaveSpeed()
    req = dataclasses.replace(request(tmp_path), prompt=prompt)
    with pytest.raises(CharSwapError, match=words):
        generate(SeedanceWaveSpeedGenerator(fake), req, "draft", tmp_path)
    assert fake.uploads == [] and fake.bodies == []


def test_a_second_person_stops_before_any_upload(tmp_path):
    fake = FakeWaveSpeed()
    second = tmp_path / "character-secondary.jpg"
    second.write_bytes(b"2")
    req = request(tmp_path, secondary=CharacterRef(second))
    with pytest.raises(ProviderError, match="--provider piapi.*Nothing was charged"):
        generate(SeedanceWaveSpeedGenerator(fake), req, "draft", tmp_path)
    assert fake.uploads == [] and fake.bodies == []


def test_resume_reuses_the_saved_prediction_without_uploading(tmp_path):
    fake = FakeWaveSpeed()
    (tmp_path / "draft-prediction.json").write_text(json.dumps({"id": "p1", "status": "succeeded"}))
    generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert fake.bodies == [] and fake.uploads == []


def test_a_big_prepared_video_is_uploaded_as_a_smaller_copy(tmp_path, monkeypatch):
    seen = []

    def fake_fit(src, dst, max_bytes):
        seen.append((src.name, dst.name, max_bytes))
        dst.write_bytes(b"small")
        return dst

    monkeypatch.setattr(seedance_wavespeed, "fit_for_upload", fake_fit)
    fake = FakeWaveSpeed()
    generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert seen == [("prepared.mp4", "upload-copy.mp4", 209_715_200)]
    assert [p.name for p in fake.uploads] == ["upload-copy.mp4", "character-primary.jpg"]
    assert not (tmp_path / "upload-copy.mp4").exists()  # private media is removed after upload


def test_a_video_too_big_even_compressed_stops_before_paying(tmp_path, monkeypatch):
    def too_big(src, dst, max_bytes):
        raise UploadTooLarge("prepared.mp4 is still over 210 MB after compressing.")

    monkeypatch.setattr(seedance_wavespeed, "fit_for_upload", too_big)
    fake = FakeWaveSpeed()
    with pytest.raises(ProviderError, match="after compressing.*Nothing was charged"):
        generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert fake.bodies == [] and fake.uploads == []
    assert not (tmp_path / "draft-prediction.json").exists()


def test_an_expired_download_names_the_record_and_the_rebuy_cost(tmp_path):
    class Expiring(FakeWaveSpeed):
        def download(self, url, dest):
            raise OutputExpired("gone")

    record = tmp_path / "draft-prediction.json"
    with pytest.raises(ProviderError, match=r"expired .*Delete .*draft-prediction\.json.*\$0\.88"):
        SeedanceWaveSpeedGenerator(Expiring()).generate(
            request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
        )
    assert record.exists()
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_seedance_wavespeed.py -q --no-cov`. Expected: `AttributeError: 'SeedanceWaveSpeedGenerator' object has no attribute 'generate'` (and `fit_for_upload` missing for the monkeypatch tests).

- [ ] **Step 3: `generate`.** In `seedance_wavespeed.py`, replace the import block with:

```python
from __future__ import annotations

import math
from pathlib import Path

from character_swap.costs import Estimate, cost_line
from character_swap.media.upload import UploadTooLarge, fit_for_upload
from character_swap.modes import GENERATOR_LIMITS
from character_swap.prompts import check_placeholders
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators.base import GenerationRequest, OnCreated, Stage
from character_swap.providers.wavespeed import MAX_UPLOAD_BYTES
```

Add after `BILLED_SECONDS = ...`:

```python
UPLOAD_COPY = "upload-copy.mp4"
```

Append these methods to `class SeedanceWaveSpeedGenerator`:

```python
    def generate(
        self,
        request: GenerationRequest,
        stage: Stage,
        *,
        record: Path,
        dest: Path,
        on_created: OnCreated | None = None,
    ) -> Path:
        resolution = self.resolution(request, stage)
        if record.exists():  # resume: run() reads the saved prediction and never posts again
            body: dict = {}
        else:
            self._check(request)  # free, before any upload
            body = self.build_input(
                request,
                video_url=self._upload_video(request, record.parent),
                image_urls=[self._wavespeed.upload(request.primary.image_path)],
                resolution=resolution,
            )
        url = self._wavespeed.run(body=body, record=record, on_created=on_created)
        try:
            return self._wavespeed.download(url, dest)
        except OutputExpired as exc:
            rebuy = self.estimate(request, stage).total
            raise ProviderError(
                f"The Seedance {stage} finished but its download link expired ({exc}). Delete "
                f"{record} and run the same command again to make a new one (costs about "
                f"${rebuy:.2f})."
            ) from exc

    @staticmethod
    def _check(request: GenerationRequest) -> None:
        if not request.prompt.strip():
            raise ProviderError(
                "Seedance needs a prompt; see charswap prompt <job>. Nothing was charged."
            )
        if request.secondary is not None:
            raise ProviderError("Two people need --provider piapi for now. Nothing was charged.")
        check_placeholders(request.prompt, provider="wavespeed", has_secondary=False)

    def _upload_video(self, request: GenerationRequest, folder: Path) -> str:
        try:
            video = fit_for_upload(request.video, folder / UPLOAD_COPY, MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            raise ProviderError(f"{exc} Nothing was charged.") from exc
        try:
            return self._wavespeed.upload(video)
        finally:
            if video != request.video:  # the compressed copy is private media: don't keep it
                video.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_seedance_wavespeed.py -q --no-cov`. Expected: PASS.

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/providers/generators/seedance_wavespeed.py tests/test_seedance_wavespeed.py
git commit -m "feat: WaveSpeed Seedance generator: free checks, uploads, one fresh run per step"
```

---

### Task 7: The job's provider: `generator_key`, the old-job rule, the registry by key

**Files:** Modify `src/character_swap/jobs.py` (`Job.create`, lines 59–125), `src/character_swap/pipeline.py` (imports lines 20–34, `generator_for` lines 433–437, `FINAL_RESOLUTIONS` lines 446–447, `final_resolution` line 452, `generate` line 578, `default_prompt`, `generation_prompt`), `src/character_swap/cli.py` (imports, `KEYS`/`UNCONFIRMED` lines 66–67, `build_services` lines 70–89, `build_piapi`, `_prompt`, `_generate`), `src/character_swap/web/view_model.py` (`_generation_name`, `cost_rows`). Tests: `tests/test_jobs.py`, `tests/test_generation.py`, `tests/test_cli.py`, `tests/test_seedance.py`.

After this task:
- `Job.create(provider=...)` saves `provider` for Seedance jobs (prepare passes it in Task 10).
- `pipeline.generator_key(job)` is `wan`, `seedance:wavespeed` or `seedance:piapi`. A Seedance job without `provider` is PiAPI's when it has a saved face, a second person, a draft/final record or raw file, a done draft/final, or a logged charge with `provider: "piapi"`; otherwise WaveSpeed's. It never writes.
- `pin_provider(job)` writes the resolved provider once; `charswap prompt`, `charswap generate` and `pipeline.generate` call it right after loading the job, before any paid create.
- The services registry, `KEYS`, `UNCONFIRMED` and `FINAL_RESOLUTIONS` are keyed by `generator_key`. `build_services` builds each Seedance generator only when its key is set.
- The default prompt and the placeholder check use the job's provider.

- [ ] **Step 1: Rename the registry keys in existing tests (mechanical).**

```bash
sed -i '' -e 's/generators\["seedance"\]/generators["seedance:piapi"]/g' \
  -e 's/generators={"seedance": /generators={"seedance:piapi": /g' \
  tests/test_generation.py tests/test_cli.py
```

These tests use saved faces, so the old-job rule makes them PiAPI's.

- [ ] **Step 2: Failing tests: jobs.** Append to `tests/test_jobs.py`:

```python
def test_seedance_jobs_save_their_provider(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth", provider="wavespeed")
    assert job.state["provider"] == "wavespeed"
    old_style = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert "provider" not in old_style.state  # decided later by the old-job rule


def test_provider_is_refused_for_wan_and_unknown_names(runs_dir, video, tmp_path):
    face = tmp_path / "me.jpg"
    face.write_bytes(b"jpg")
    with pytest.raises(JobError, match="only for Seedance"):
        Job.create(
            runs_dir, video, generator="wan", mode="direct", character=face, provider="piapi"
        )
    with pytest.raises(JobError, match="Unknown provider 'sora'"):
        Job.create(runs_dir, video, generator="seedance", mode="depth", provider="sora")
    assert not any(runs_dir.iterdir())
```

- [ ] **Step 3: Failing tests: pipeline.** In `tests/test_generation.py`, change `from character_swap.jobs import Job` to `from character_swap.jobs import Job, JobError`, and append:

```python
# ---- the job's provider (WaveSpeed plan) ---------------------------------------------
def _old_seedance_job(runs_dir, tmp_path):
    """A Seedance job made before WaveSpeed: no provider saved."""
    upload = tmp_path / "old-clip.mp4"
    upload.write_bytes(b"v")
    return Job.create(runs_dir, upload, generator="seedance", mode="depth")


PIAPI_SIGNS = {
    "saved face": lambda job: job.update(face_primary="frank"),
    "second saved face": lambda job: job.update(face_secondary="sam"),
    "second character": lambda job: job.update(character_secondary="character-secondary.jpg"),
    "draft record": lambda job: job.path("draft-prediction.json").write_text("{}"),
    "final record": lambda job: job.path("final-prediction.json").write_text("{}"),
    "draft video": lambda job: job.path("draft-raw.mp4").write_bytes(b"v"),
    "final video": lambda job: job.path("final-raw.mp4").write_bytes(b"v"),
    "finished draft": lambda job: job.mark_stage("draft"),
    "finished final": lambda job: job.mark_stage("final"),
    "PiAPI charge": lambda job: job.append_cost({"step": "draft", "usd": 0.99, "provider": "piapi"}),
}


@pytest.mark.parametrize("sign", list(PIAPI_SIGNS))
def test_an_old_seedance_job_with_piapi_work_is_piapi(runs_dir, tmp_path, sign):
    job = _old_seedance_job(runs_dir, tmp_path)
    PIAPI_SIGNS[sign](job)
    assert pipeline.generator_key(job) == "seedance:piapi"


def test_an_old_seedance_job_without_piapi_work_is_wavespeed(runs_dir, tmp_path):
    job = _old_seedance_job(runs_dir, tmp_path)
    job.mark_stage("draft", "failed", error="x")  # failed before any record: not PiAPI work
    job.append_cost({"step": "prepare", "usd": 0.06})  # prep charges name no provider
    job.append_cost({"step": "draft", "usd": 0.88, "provider": "wavespeed"})
    assert pipeline.generator_key(job) == "seedance:wavespeed"


def test_a_saved_provider_wins_and_wan_is_wan(runs_dir, tmp_path, wan_job):
    job = _old_seedance_job(runs_dir, tmp_path)
    job.update(provider="wavespeed")
    job.path("draft-prediction.json").write_text("{}")
    assert pipeline.generator_key(job) == "seedance:wavespeed"
    assert pipeline.generator_key(wan_job) == "wan"


def test_generator_key_never_writes(runs_dir, tmp_path):
    job = _old_seedance_job(runs_dir, tmp_path)
    before = job.path("state.json").read_bytes()
    assert pipeline.generator_key(job) == "seedance:wavespeed"
    assert job.path("state.json").read_bytes() == before


def test_pin_provider_saves_the_resolved_provider_once(runs_dir, tmp_path, wan_job):
    job = _old_seedance_job(runs_dir, tmp_path)
    assert pipeline.pin_provider(job) == "seedance:wavespeed"
    assert job.state["provider"] == "wavespeed"
    job.path("draft-prediction.json").write_text('{"id": "p1", "status": "processing"}')
    assert pipeline.pin_provider(job) == "seedance:wavespeed"  # its own record never flips it
    assert pipeline.pin_provider(wan_job) == "wan" and "provider" not in wan_job.state


def test_an_unknown_saved_provider_is_an_error(runs_dir, tmp_path):
    job = _old_seedance_job(runs_dir, tmp_path)
    job.update(provider="sora")
    with pytest.raises(JobError, match="unknown Seedance provider 'sora'"):
        pipeline.generator_key(job)


def test_finals_come_from_each_generator():
    assert pipeline.FINAL_RESOLUTIONS == {
        "wan": ("720p",),
        "seedance:wavespeed": ("720p", "1080p"),
        "seedance:piapi": ("1080p", "720p"),
    }


@pytest.fixture
def wavespeed(runs_dir, tmp_path):
    """An approved depth_mesh job with a plain character photo and no saved provider."""
    upload = write_video(tmp_path / "talk.mp4", [solid((90, 60, 30))] * 108, audio_seconds=4.5)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(photo, "JPEG")
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth_mesh", character=photo)
    for name in MODES["depth_mesh"].review_files:
        if name.endswith(".json"):  # face-landmarks.json
            job.path(name).write_text('{"fps": 24, "frames": []}')
        else:
            write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in MODES["depth_mesh"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="lets get it", scene="A hotel lobby.")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    output = write_video(tmp_path / "wavespeed-out.mp4", [solid((20, 200, 20))] * 96)
    services = Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": FakeGenerator(output)},
    )
    return job, services


def test_generate_saves_the_provider_before_the_paid_create(wavespeed, runs_dir):
    job, services = wavespeed
    fake = services.generators["seedance:wavespeed"]
    seen = []
    original = fake.generate

    def spy(request, stage, *, record, dest, on_created=None):
        seen.append(json.loads((record.parent / "state.json").read_text()).get("provider"))
        return original(request, stage, record=record, dest=dest, on_created=on_created)

    fake.generate = spy
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert seen == ["wavespeed"]
    assert pipeline.generator_key(job) == "seedance:wavespeed"  # its record exists now


def test_a_missing_provider_generator_names_its_key(wavespeed, runs_dir):
    job, services = wavespeed
    services.generators.clear()
    with pytest.raises(StageError, match="No seedance:wavespeed generator"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)


def test_a_wavespeed_job_prompt_uses_wavespeeds_names(wavespeed):
    job, _ = wavespeed
    text = write_prompt_file(job).read_text()
    assert "@Video 1" in text and "@Image 1" in text and "@video1" not in text
    assert "lets get it" in text and generation_prompt(job, "draft") == text


def test_a_wavespeed_prompt_with_piapi_names_is_refused(wavespeed):
    job, _ = wavespeed
    write_prompt_file(job).write_text("Swap @video1 for @image1.")
    with pytest.raises(PromptError, match="must mention @Video 1"):
        generation_prompt(job, "draft")
```

- [ ] **Step 4: Failing tests: CLI.** In `tests/test_cli.py`:
  - change `from fakes import FakePiAPI` to `from fakes import FakePiAPI, FakeWaveSpeed`;
  - in `test_prompt_errors_are_friendly`, the job has no saved face, so it is WaveSpeed's: change its prompt line to
    `job.path("prompt.txt").write_text("Put @Image 1 into @Video 1. " + "x" * MAX_PROMPT)`;
  - replace the whole `test_real_services_include_seedance_when_the_key_is_set` test with:

```python
def test_real_services_build_each_seedance_provider_whose_key_is_set(tmp_path, settings):
    from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
    from character_swap.providers.generators.seedance_wavespeed import (
        SeedanceWaveSpeedGenerator,
    )

    both = Settings.load(
        root=tmp_path,
        environ={"REPLICATE_API_TOKEN": "t", "PIAPI_API_KEY": "k", "WAVESPEED_API_KEY": "w"},
    )
    services = cli.build_services(both)
    assert isinstance(services.generators["seedance:piapi"], SeedanceGenerator)
    assert isinstance(services.generators["seedance:wavespeed"], SeedanceWaveSpeedGenerator)
    assert services.faces is not None
    assert set(cli.build_services(settings).generators) == {"wan"}
```

  - append this section at the end of the file:

```python
# ---- WaveSpeed (the default Seedance provider) ------------------------------------------
@pytest.fixture
def ws_settings(tmp_path):
    return Settings.load(
        root=tmp_path, environ={"REPLICATE_API_TOKEN": "t", "WAVESPEED_API_KEY": "k"}
    )


def _approved_wavespeed_job(settings, video, tmp_path, *, provider="wavespeed"):
    """An approved depth_mesh job with a plain photo. provider="" = an old job (none saved)."""
    job = Job.create(
        settings.runs_dir,
        video,
        generator="seedance",
        mode="depth_mesh",
        character=_photo(tmp_path / "me.jpg"),
        provider=provider,
    )
    for name in _MODES["depth_mesh"].review_files:
        if name.endswith(".json"):  # face-landmarks.json
            job.path(name).write_text('{"fps": 24, "frames": []}')
        else:
            write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in _MODES["depth_mesh"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="hello there", scene="A studio.")
    approve_review(job, approved=True, note="ok", fingerprint=review_fingerprint(job))
    return job


def wavespeed_services(settings):
    """The real WaveSpeed generator on a fake WaveSpeed that returns a real 4 s video."""
    from character_swap.providers.generators.seedance_wavespeed import (
        SeedanceWaveSpeedGenerator,
    )

    output = write_video(settings.root / "wavespeed-out.mp4", [solid((20, 200, 20))] * 96)
    fake = FakeWaveSpeed(files={"https://cdn.wavespeed.ai/out.mp4": output})
    wavespeed_services.last = fake
    return Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": SeedanceWaveSpeedGenerator(fake)},
        faces=FaceLibrary(settings.root),
    )


def test_prompt_saves_the_provider_and_uses_wavespeeds_names(settings, video, tmp_path):
    job = _approved_wavespeed_job(settings, video, tmp_path, provider="")
    code, out = run(["prompt", job.id], settings)
    assert code == 0 and job.state["provider"] == "wavespeed"
    assert "@Video 1" in out.text and "@Video 1" in job.path("prompt.txt").read_text()


def test_wavespeed_generate_needs_the_wavespeed_key(settings, video, tmp_path):
    job = _approved_wavespeed_job(settings, video, tmp_path)
    code, out = run(["generate", job.id, "--draft"], settings, services_factory=wavespeed_services)
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

    monkeypatch.setattr(FakeWaveSpeed, "download", stopped)
    code, _ = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 130 and job.path("draft-prediction.json").exists()
    assert job.state["provider"] == "wavespeed"  # saved before the paid create
    monkeypatch.undo()
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text, out.err.text
    assert wavespeed_services.last.bodies == [] and job.stage_done("draft")
```

  In `tests/test_seedance.py`, replace `test_finals_are_defined_once` with:

```python
def test_finals_are_defined_once():
    from character_swap import pipeline

    assert pipeline.FINAL_RESOLUTIONS["seedance:piapi"] is SeedanceGenerator.finals
    assert SeedanceGenerator.finals is seedance_piapi.FINALS
    assert pipeline.PROMOTE is seedance_piapi.PROMOTE
```

- [ ] **Step 5: Run them and watch them fail.** `uv run pytest tests/test_jobs.py tests/test_generation.py tests/test_cli.py tests/test_seedance.py -q --no-cov`. Expected: `TypeError: Job.create() got an unexpected keyword argument 'provider'`, `AttributeError: ... no attribute 'generator_key'`, and `StageError: No seedance generator is set up.` in the renamed tests.

- [ ] **Step 6: Jobs.** In `src/character_swap/jobs.py`:
  - after the `# fmt: on` line add:

    ```python
    SEEDANCE_PROVIDERS: tuple[str, ...] = ("wavespeed", "piapi")  # the first is the default
    ```

  - in `Job.create`, add the keyword parameter after `language: str = "auto",`:

    ```python
            provider: str = "",  # Seedance: "wavespeed" | "piapi"; "" = decided later (old jobs)
    ```

  - after the `if language not in WHISPER_LANGUAGES:` check (before `job_id = uuid.uuid4().hex`) add:

    ```python
            if provider and generator != "seedance":
                raise JobError("--provider is only for Seedance; Wan always runs on Replicate.")
            if provider and provider not in SEEDANCE_PROVIDERS:
                raise JobError(
                    f"Unknown provider {provider!r}. Use one of: {', '.join(SEEDANCE_PROVIDERS)}."
                )
    ```

  - after the `state: dict[str, Any] = {...}` literal (before the `for slot, image in ...` loop) add:

    ```python
                if provider:
                    state["provider"] = provider
    ```

- [ ] **Step 7: Pipeline.** In `src/character_swap/pipeline.py`:
  - Replace `from character_swap.jobs import Job` with `from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError`.
  - Replace the two lines

    ```python
    from character_swap.providers.generators.seedance_piapi import FINALS as SEEDANCE_FINALS
    from character_swap.providers.generators.seedance_piapi import PROMOTE
    ```

    with

    ```python
    from character_swap.providers.generators.seedance_piapi import PROMOTE, SeedanceGenerator
    from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
    from character_swap.providers.generators.wan_replicate import WanGenerator
    ```

  - Replace the whole `def generator_for(...)` function with:

    ```python
    # A Seedance job made before WaveSpeed has no saved provider. It was PiAPI's when it has
    # anything only PiAPI allowed or made; otherwise it is WaveSpeed's (the default).
    _PIAPI_ONLY = ("face_primary", "face_secondary", "character_secondary")


    def generator_key(job: Job, state: dict[str, Any] | None = None) -> str:
        """'wan', 'seedance:wavespeed' or 'seedance:piapi'. Reads the job folder, never writes.

        `state` is the job's state.json when the caller already read it (the viewer).
        """
        state = job.state if state is None else state
        generator = state.get("generator")
        if generator != "seedance":
            return str(generator)
        provider = state.get("provider")
        if provider is None:
            provider = _old_job_provider(job, state)
        if provider not in SEEDANCE_PROVIDERS:
            raise JobError(
                f"state.json names an unknown Seedance provider {provider!r}; use "
                f"{' or '.join(SEEDANCE_PROVIDERS)}."
            )
        return f"seedance:{provider}"


    def _old_job_provider(job: Job, state: dict[str, Any]) -> str:
        if any(state.get(name) for name in _PIAPI_ONLY):
            return "piapi"
        costs = state.get("costs")
        if isinstance(costs, list) and any(
            isinstance(entry, dict) and entry.get("provider") == "piapi" for entry in costs
        ):
            return "piapi"  # a logged PiAPI charge
        stages = state.get("stages")
        stages = stages if isinstance(stages, dict) else {}
        for stage in GENERATION_STAGES:
            entry = stages.get(stage)
            if isinstance(entry, dict) and entry.get("status") == "done":
                return "piapi"
            record, raw, _ = generation_files(stage)
            if job.path(record).exists() or job.path(raw).exists():
                return "piapi"
        return SEEDANCE_PROVIDERS[0]


    def seedance_provider(job: Job) -> str:
        """'wavespeed' or 'piapi' for a Seedance job: whose input names its prompt uses."""
        return generator_key(job).partition(":")[2]


    def pin_provider(job: Job) -> str:
        """Save the resolved Seedance provider in state.json (once) and return the job's key.

        `charswap prompt` and `charswap generate` call this right after loading the job,
        before any paid create. Otherwise a WaveSpeed draft interrupted after its record was
        saved would read as PiAPI's under the old-job rule.
        """
        key = generator_key(job)
        if key.startswith("seedance:") and "provider" not in job.state:
            job.update(provider=key.partition(":")[2])
        return key


    def generator_for(job: Job, services: Services) -> Generator:
        key = generator_key(job)
        try:
            return services.generators[key]
        except KeyError:
            raise StageError(f"No {key} generator is set up.") from None
    ```

  - Replace the two lines

    ```python
    # Seedance's PROMOTE final upgrades the draft's own task (sends no face/prompt).
    FINAL_RESOLUTIONS = {"seedance": SEEDANCE_FINALS, "wan": ("720p",)}  # first = default
    ```

    with

    ```python
    # One entry per generator_key(job). Finals and promotes are the generator classes' own.
    GENERATOR_CLASSES: dict[str, type] = {
        "wan": WanGenerator,
        "seedance:wavespeed": SeedanceWaveSpeedGenerator,
        "seedance:piapi": SeedanceGenerator,
    }
    FINAL_RESOLUTIONS: dict[str, tuple[str, ...]] = {  # the first is the default
        key: generator.finals for key, generator in GENERATOR_CLASSES.items()
    }
    ```

  - In `final_resolution`, change `choices = FINAL_RESOLUTIONS[job.generator]` to `choices = FINAL_RESOLUTIONS[generator_key(job)]`.
  - In `generate`, make the first line of the body (right after the docstring):

    ```python
        pin_provider(job)  # before any paid create (see pin_provider)
    ```

  - In `default_prompt` and in `generation_prompt`, replace each `provider="piapi",  # Task 7: the job's provider` with `provider=seedance_provider(job),`.

- [ ] **Step 8: CLI.** In `src/character_swap/cli.py`:
  - add `pin_provider,` to the `from character_swap.pipeline import (...)` list (keep it sorted);
  - after `from character_swap.providers.generators.seedance_piapi import SeedanceGenerator` add
    `from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator`;
  - after `from character_swap.providers.replicate import unconfirmed_message as replicate_unconfirmed` add
    `from character_swap.providers.wavespeed import unconfirmed_message as wavespeed_unconfirmed`;
  - replace the `KEYS = ...` and `UNCONFIRMED = ...` lines with:

    ```python
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
    ```

  - in `build_services`, replace

    ```python
        generators = {"wan": WanGenerator(replicate)}
        if settings.get("PIAPI_API_KEY"):
            generators["seedance"] = SeedanceGenerator(build_piapi(settings))
    ```

    with

    ```python
        generators = {"wan": WanGenerator(replicate)}
        if settings.get("WAVESPEED_API_KEY"):
            generators["seedance:wavespeed"] = SeedanceWaveSpeedGenerator(build_wavespeed(settings))
        if settings.get("PIAPI_API_KEY"):
            generators["seedance:piapi"] = SeedanceGenerator(build_piapi(settings))
    ```

  - after `def build_piapi(...)` add:

    ```python
    def build_wavespeed(settings: Settings):
        from character_swap.providers.wavespeed import WaveSpeedClient

        return WaveSpeedClient(settings.require("WAVESPEED_API_KEY"))
    ```

  - in `_prompt`, right after the `if job.generator != "seedance": raise JobError(...)` block add:

    ```python
        pin_provider(job)  # before anything else reads which provider's names to use
    ```

  - in `_generate`, right after `job = Job.load(settings.runs_dir, args.job)` add:

    ```python
        key = pin_provider(job)  # before any paid create: see pipeline.pin_provider
    ```

    and change `settings.require(KEYS[job.generator])` to `settings.require(KEYS[key])` and
    `env.err(UNCONFIRMED[job.generator](job.path(generation_files(stage)[0])))` to
    `env.err(UNCONFIRMED[key](job.path(generation_files(stage)[0])))`.

- [ ] **Step 9: Viewer default final (keeps the viewer tests green).** `FINAL_RESOLUTIONS` is now keyed by `generator_key`, so the viewer's "Final (…)" fallback must use the job's key. In `src/character_swap/web/view_model.py`:
  - change `from character_swap.pipeline import FINAL_RESOLUTIONS, PAID_STAGES, PREP_STAGES_BY_MODE` to
    `from character_swap.pipeline import FINAL_RESOLUTIONS, PAID_STAGES, PREP_STAGES_BY_MODE, generator_key`;
  - replace `_generation_name` with:

    ```python
    def _generation_name(state: dict[str, Any], stage: str, key: str) -> str:
        """'Final (1080p)': the resolution the final used, else the generator's default final."""
        if stage != "final":
            return STEP_NAMES[stage]
        entry = state.get("stages", {}).get("final")
        resolution = entry.get("resolution") if isinstance(entry, dict) else None
        if not isinstance(resolution, str) or not resolution:
            resolution = FINAL_RESOLUTIONS.get(key, ("720p",))[0]
        return f"Final ({resolution})"
    ```

  - in `cost_rows`, change `_generation_name(state, stage),` to `_generation_name(state, stage, generator_key(job, state)),`.

  `test_final_row_without_a_saved_resolution_uses_the_generator_default` covers it: its depth job has a done draft and final, so it is PiAPI's ("Final (1080p)").

- [ ] **Step 10: Run tests.** `uv run pytest tests/test_jobs.py tests/test_generation.py tests/test_cli.py tests/test_seedance.py tests/test_view_model.py tests/test_web_app.py -q --no-cov`. Expected: PASS.

- [ ] **Step 11: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/jobs.py src/character_swap/pipeline.py src/character_swap/cli.py src/character_swap/web/view_model.py tests/test_jobs.py tests/test_generation.py tests/test_cli.py tests/test_seedance.py
git commit -m "feat: jobs carry their Seedance provider; registry, keys and finals by generator key"
```

---

### Task 8: An old `prompt.txt` with PiAPI names is renamed for WaveSpeed

**Files:** Modify `src/character_swap/pipeline.py` (new `rename_old_prompt_names`, after `write_prompt_file`), `src/character_swap/cli.py` (`_prompt`, `_generate`). Tests: `tests/test_generation.py`, `tests/test_cli.py`.

On a job that resolves to WaveSpeed, `charswap prompt` and `charswap generate` rewrite `@video1` → `@Video 1` and `@image1` → `@Image 1` in `prompt.txt` and print "Updated prompt.txt to WaveSpeed's input names." It is free, happens before any question, and runs before `draft_prompt_changed` is read, so a rename alone never makes a new paid draft.

- [ ] **Step 1: Failing tests: pipeline.** Append to `tests/test_generation.py`:

```python
def test_old_piapi_names_are_renamed_on_a_wavespeed_job(wavespeed):
    job, _ = wavespeed
    write_prompt_file(job).write_text("Put @image1 into @video1, then @image10 stays.\n")
    assert pipeline.rename_old_prompt_names(job) is True
    assert job.path("prompt.txt").read_text() == (
        "Put @Image 1 into @Video 1, then @image10 stays.\n"
    )
    assert pipeline.rename_old_prompt_names(job) is False  # nothing left to rename


def test_piapi_jobs_and_missing_files_are_left_alone(seedance, wavespeed):
    piapi_job, _ = seedance
    write_prompt_file(piapi_job).write_text("Put @image1 into @video1.")
    assert pipeline.rename_old_prompt_names(piapi_job) is False
    assert piapi_job.path("prompt.txt").read_text() == "Put @image1 into @video1."
    wavespeed_job, _ = wavespeed
    assert pipeline.rename_old_prompt_names(wavespeed_job) is False  # no prompt.txt
```

(The `seedance` and `wavespeed` fixtures both write `talk.mp4` and `me.jpg` in `tmp_path`; each job copies them at creation, so using both in one test is fine.)

- [ ] **Step 2: Failing tests: CLI.** Append to `tests/test_cli.py`:

```python
RENAMED = "Updated prompt.txt to WaveSpeed's input names."


def test_prompt_renames_old_piapi_names_for_wavespeed(settings, video, tmp_path):
    job = _approved_wavespeed_job(settings, video, tmp_path, provider="")
    job.path("prompt.txt").write_text("Put @image1 into @video1.")
    code, out = run(["prompt", job.id], settings)
    assert code == 0 and RENAMED in out.text and "Put @Image 1 into @Video 1." in out.text
    assert job.path("prompt.txt").read_text() == "Put @Image 1 into @Video 1."


def test_generate_renames_before_the_cost_question(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("prompt.txt").write_text("Put @image1 into @video1.")
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=lambda _q: False,
        services_factory=wavespeed_services,
    )
    assert code == 1 and out.text.index(RENAMED) < out.text.index("~$0.8800")
    assert "Put @Image 1 into @Video 1." in out.text


def test_a_rename_alone_never_makes_a_new_draft(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("prompt.txt").write_text("FIRST @Image 1 into @Video 1.")
    code, _ = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0 and job.stage_done("draft")
    job.path("prompt.txt").write_text("FIRST @image1 into @video1.")  # same text, old names
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and RENAMED in out.text and "already done" in out.text
    assert not list(job.root.glob("draft-old-*"))
```

- [ ] **Step 3: Run them and watch them fail.** `uv run pytest tests/test_generation.py tests/test_cli.py -q --no-cov -k "rename or renamed or old_piapi or left_alone"`. Expected: `AttributeError: module 'character_swap.pipeline' has no attribute 'rename_old_prompt_names'` and missing `RENAMED` lines.

- [ ] **Step 4: Pipeline.** In `src/character_swap/pipeline.py`, add `import re` to the standard-library imports, and add after `write_prompt_file`:

```python
# PiAPI's input names that an old prompt.txt may still use on a WaveSpeed job.
_OLD_NAMES = (
    (re.compile(r"@video1(?!\d)"), "@Video 1"),
    (re.compile(r"@image1(?!\d)"), "@Image 1"),
)


def rename_old_prompt_names(job: Job) -> bool:
    """A WaveSpeed job whose prompt.txt still uses PiAPI's names: rewrite them. Free.

    True when prompt.txt changed. Callers run this before reading draft_prompt_changed,
    so a rename alone never counts as an edited prompt.
    """
    if job.generator != "seedance" or seedance_provider(job) != "wavespeed":
        return False
    path = job.path(PROMPT_FILE)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    renamed = text
    for pattern, name in _OLD_NAMES:
        renamed = pattern.sub(name, renamed)
    if renamed == text:
        return False
    _write_atomically(path, lambda part: part.write_text(renamed, encoding="utf-8"))
    return True
```

- [ ] **Step 5: CLI.** In `src/character_swap/cli.py`:
  - add `rename_old_prompt_names,` to the pipeline import list;
  - after the `UNCONFIRMED = {...}` table add:

    ```python
    RENAMED = "Updated prompt.txt to WaveSpeed's input names."
    ```

  - in `_prompt`, right after `pin_provider(job)` add:

    ```python
        if rename_old_prompt_names(job):
            env.out(RENAMED)
    ```

  - in `_generate`, right after `key = pin_provider(job)  # ...` add the same two lines:

    ```python
        if rename_old_prompt_names(job):  # free, before draft_prompt_changed and any question
            env.out(RENAMED)
    ```

- [ ] **Step 6: Run tests.** `uv run pytest tests/test_generation.py tests/test_cli.py -q --no-cov`. Expected: PASS.

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/pipeline.py src/character_swap/cli.py tests/test_generation.py tests/test_cli.py
git commit -m "feat: an old prompt.txt with PiAPI names is renamed for WaveSpeed jobs"
```

---

### Task 9: Finals come from the generator; only PiAPI's default final is an upgrade

**Files:** Modify `src/character_swap/pipeline.py` (new `is_promote` after `FINAL_RESOLUTIONS`; `build_request` line 545; `generation_prompt` final branch and docstring; the `PROMOTE` import), `src/character_swap/cli.py` (imports, `--final`/`--resolution` help, `_generate` promote pre-check and fresh-final note, `_after_draft_hint`). Tests: `tests/test_generation.py`, `tests/test_cli.py`, `tests/test_seedance.py`.

Every place that assumed "a Seedance 1080p final = a PiAPI promote" now asks `is_promote(job, stage, resolution)`, which is true only for a generator with `promotes = True` making its default final:
- `build_request`: `promote` (and so `face_needed`);
- `generation_prompt`: every fresh-run final (any WaveSpeed final; a PiAPI 720p final) reuses the draft's saved prompt and is refused when it is missing;
- the CLI `promote_body` pre-check;
- the CLI note before the yes/no: PiAPI 720p keeps its "1080p (the default) upgrades the exact draft" line; a WaveSpeed final says "The final is a new take at higher resolution; it will look a little different from the draft.";
- `_after_draft_hint`: prices both finals of the job's generator, worded for upgrade or new take.

- [ ] **Step 1: Failing tests: pipeline.** Append to `tests/test_generation.py`:

```python
# ---- finals by generator (WaveSpeed plan) ----------------------------------------------
def test_wavespeed_finals_default_to_720p_and_refuse_480p(wavespeed):
    job, _ = wavespeed
    assert final_resolution(job, "") == "720p" and final_resolution(job, "1080p") == "1080p"
    with pytest.raises(StageError, match="720p or 1080p, not 480p"):
        final_resolution(job, "480p")


@pytest.mark.parametrize("resolution", ["720p", "1080p"])
def test_a_wavespeed_final_is_a_fresh_run_with_the_drafts_prompt(wavespeed, runs_dir, resolution):
    job, services = wavespeed
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    sent = job.state["stages"]["draft"]["prompt"]
    write_prompt_file(job).write_text("@Video 1 @Image 1 changed after the draft")
    generate(job, services, "final", runs_dir=runs_dir, final_resolution=resolution, **QUIET)
    request = services.generators["seedance:wavespeed"].calls[1][1]
    assert request.prompt == sent and request.final_resolution == resolution
    assert not pipeline.is_promote(job, "final", resolution)
    assert job.state["stages"]["final"]["resolution"] == resolution


@pytest.mark.parametrize("resolution", ["720p", "1080p"])
def test_a_wavespeed_final_needs_the_drafts_saved_prompt(wavespeed, runs_dir, resolution):
    job, services = wavespeed
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    draft = job.state["stages"]["draft"]
    job.mark_stage("draft", fingerprint=draft["fingerprint"])  # an older state: no prompt saved
    with pytest.raises(PromptError, match=f"prompt wasn't saved, so a {resolution} final"):
        generate(job, services, "final", runs_dir=runs_dir, final_resolution=resolution, **QUIET)
    assert len(services.generators["seedance:wavespeed"].calls) == 1


def test_only_piapis_default_final_upgrades_the_draft(seedance, wan_job):
    job, _ = seedance
    assert pipeline.is_promote(job, "final", "1080p")
    assert not pipeline.is_promote(job, "final", "720p")
    assert not pipeline.is_promote(job, "draft", "1080p")
    assert not pipeline.is_promote(wan_job, "final", "720p")
```

- [ ] **Step 2: Failing tests: CLI.** Append to `tests/test_cli.py`:

```python
def test_a_wavespeed_draft_shows_the_prompt_and_price_then_sends_one_body(
    ws_settings, video, tmp_path
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=lambda q: asked.append(q) or False,
        services_factory=wavespeed_services,
    )
    assert code == 1 and asked and wavespeed_services.last.bodies == []
    assert "@Video 1" in out.text and "hello there" in out.text and "~$0.8800" in out.text
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0 and job.stage_done("draft")
    (body,) = wavespeed_services.last.bodies
    assert body["resolution"] == "480p" and body["generate_audio"] is False
    assert body["prompt"] == job.state["stages"]["draft"]["prompt"]
    assert body["reference_images"] == ["https://cdn.wavespeed.ai/uploads/character-primary.jpg"]
    uploads = [p.name for p in wavespeed_services.last.uploads]
    assert uploads == ["prepared.mp4", "character-primary.jpg"]
    assert job.state["costs"][-1]["provider"] == "wavespeed"


def test_after_a_wavespeed_draft_both_new_take_prices_are_shown(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0
    assert "makes a new 720p take (~$1.76)" in out.text
    assert "`--resolution 1080p` for a new 1080p take (~$4.40)" in out.text
    assert "upgrade" not in out.text


def test_a_wavespeed_final_is_a_new_720p_take_by_default(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    code, out = run(
        ["generate", job.id, "--final"],
        ws_settings,
        confirm=lambda _q: False,
        services_factory=wavespeed_services,
    )
    assert code == 1 and "Seedance final (720p)" in out.text and "~$1.7600" in out.text
    assert "The final is a new take at higher resolution" in out.text
    assert "upgrades the exact draft" not in out.text and wavespeed_services.last.bodies == []


def test_a_wavespeed_1080p_final_sends_a_fresh_body_with_the_drafts_prompt(
    ws_settings, video, tmp_path
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    sent = job.state["stages"]["draft"]["prompt"]
    job.path("prompt.txt").write_text("@Video 1 @Image 1 edited after the draft")
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "1080p", "--yes"],
        ws_settings,
        services_factory=wavespeed_services,
    )
    assert code == 0 and job.stage_done("final") and "~$4.4000" in out.text
    (body,) = wavespeed_services.last.bodies
    assert body["resolution"] == "1080p" and body["prompt"] == sent
    assert "draft_task_id" not in body


def test_a_piapi_1080p_final_never_says_new_take(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    code, out = run(
        ["generate", job.id, "--final"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and "new take" not in out.text and "fresh run" not in out.text
```

In `tests/test_seedance.py::test_finals_are_defined_once`, delete the line `assert pipeline.PROMOTE is seedance_piapi.PROMOTE` (the pipeline no longer imports `PROMOTE`; the promote rule comes from `promotes` + `finals[0]`).

- [ ] **Step 3: Run them and watch them fail.** `uv run pytest tests/test_generation.py tests/test_cli.py -q --no-cov -k "wavespeed or upgrades or new_take"`. Expected: `AttributeError: ... no attribute 'is_promote'`, the WaveSpeed final asking for the old "720p ... upgrades" text, and the after-draft hint saying "1080p upgrade".

- [ ] **Step 4: Pipeline.** In `src/character_swap/pipeline.py`:
  - change `from character_swap.providers.generators.seedance_piapi import PROMOTE, SeedanceGenerator` to `from character_swap.providers.generators.seedance_piapi import SeedanceGenerator`;
  - after the `FINAL_RESOLUTIONS = {...}` block add:

    ```python
    def is_promote(job: Job, stage: str, resolution: str) -> bool:
        """True when this final upgrades the draft's own task (PiAPI's default 1080p final).

        Only a generator with `promotes` does that, and only for its default final. Every other
        final is a fresh run that resends the draft's saved prompt and the reviewed inputs.
        """
        if stage != "final":
            return False
        key = generator_key(job)
        return GENERATOR_CLASSES[key].promotes and resolution == FINAL_RESOLUTIONS[key][0]
    ```

  - in `build_request`, replace
    `promote = seedance and stage == "final" and final_resolution == PROMOTE`
    with
    `promote = is_promote(job, stage, final_resolution)  # sends no face and no prompt`;
  - in `generation_prompt`, replace the docstring's first bullet

    ```
        - final: the draft's saved prompt (a 720p run must match the watched draft; a 1080p
          promote sends no prompt, so it may be "").
    ```

    with

    ```
        - final: the draft's saved prompt (every fresh-run final must match the watched draft;
          a PiAPI 1080p promote sends no prompt, so it may be "").
    ```

    and replace the final branch

    ```python
        if stage == "final":
            if saved or resolution != "720p" or already_paid(job, "final"):
                return saved
            raise PromptError(
                "The draft's prompt wasn't saved, so a 720p final can't match it. Make a new draft "
                "first; nothing was charged."
            )
    ```

    with

    ```python
        if stage == "final":
            if saved or is_promote(job, stage, resolution) or already_paid(job, "final"):
                return saved
            raise PromptError(
                f"The draft's prompt wasn't saved, so a {resolution} final can't match it. Make a "
                "new draft first; nothing was charged."
            )
    ```

- [ ] **Step 5: CLI.** In `src/character_swap/cli.py`:
  - in the `from character_swap.pipeline import (...)` list, delete `PROMOTE,` and add `FINAL_RESOLUTIONS,`, `GENERATOR_CLASSES,`, `generator_key,` and `is_promote,` (sorted as ruff wants);
  - replace the `--final` and `--resolution` arguments with:

    ```python
        which.add_argument(
            "--final",
            action="store_true",
            help="Wan: 720p, same seed. Seedance on WaveSpeed: a new 720p take (or --resolution "
            "1080p). On PiAPI: the 1080p upgrade of the draft, or --resolution 720p",
        )
        gen.add_argument(
            "--resolution",
            choices=["1080p", "720p"],
            help="Seedance final. WaveSpeed: 720p (default) or 1080p, both new takes. PiAPI: 1080p "
            "(default, upgrades the draft) or 720p (cheaper fresh run)",
        )
    ```

  - in `_generate`, replace

    ```python
            if job.generator == "seedance" and stage == "final" and resolution == PROMOTE:
                SeedanceGenerator.promote_body(request.draft_task_id)  # refuses with no task id
    ```

    with

    ```python
            if is_promote(job, stage, resolution):
                SeedanceGenerator.promote_body(request.draft_task_id)  # refuses with no task id
    ```

    and replace

    ```python
            if job.generator == "seedance" and stage == "final" and resolution == "720p":
                env.out(
                    "720p is a fresh run with the same prompt and inputs, so it can differ a little "
                    "from the draft. 1080p (the default) upgrades the exact draft."
                )
    ```

    with

    ```python
            if stage == "final" and job.generator == "seedance" and not is_promote(
                job, stage, resolution
            ):
                env.out(_fresh_final_note(key))
    ```

  - add, right before `def _after_draft_hint`:

    ```python
    def _fresh_final_note(key: str) -> str:
        """What a Seedance final that is a new run (not an upgrade of the draft) can look like."""
        if GENERATOR_CLASSES[key].promotes:  # PiAPI: only its 720p final is a fresh run
            return (
                "720p is a fresh run with the same prompt and inputs, so it can differ a little "
                "from the draft. 1080p (the default) upgrades the exact draft."
            )
        return (
            "The final is a new take at higher resolution; it will look a little different from "
            "the draft."
        )
    ```

  - replace the whole `_after_draft_hint` function with:

    ```python
    def _after_draft_hint(job: Job, services: Services) -> str:
        plain = f"Happy with it? Run `charswap generate {job.id} --final`."
        key = generator_key(job)
        finals = FINAL_RESOLUTIONS[key]
        if len(finals) < 2:
            return plain  # Wan: one final, nothing to choose
        generator = generator_for(job, services)
        try:
            price = {
                res: generator.estimate(
                    build_request(job, faces=services.faces, stage="final", final_resolution=res),
                    "final",
                ).total
                for res in finals
            }
        except CharSwapError:
            # The draft is done and paid for; a price we can't work out now (e.g. a face that
            # left the library) must not turn that into an error. --final shows it and asks.
            return plain
        default, other = finals
        run_final = f"Happy with it? `charswap generate {job.id} --final`"
        if GENERATOR_CLASSES[key].promotes:
            return (
                f"{run_final} makes the {default} upgrade (~${price[default]:.2f}); add "
                f"`--resolution {other}` for a cheaper fresh {other} run (~${price[other]:.2f})."
            )
        return (
            f"{run_final} makes a new {default} take (~${price[default]:.2f}); add "
            f"`--resolution {other}` for a new {other} take (~${price[other]:.2f})."
        )
    ```

- [ ] **Step 6: Run tests.** `uv run pytest tests/test_generation.py tests/test_cli.py tests/test_seedance.py -q --no-cov`. Expected: PASS, including the PiAPI tests `test_after_the_draft_both_final_prices_are_shown` ("1080p upgrade (~$5.28)"), `test_seedance_720p_final_warns_it_is_a_fresh_run`, `test_a_removed_face_does_not_block_the_1080p_promote` and `test_a_720p_final_needs_the_drafts_saved_prompt`.

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/pipeline.py src/character_swap/cli.py tests/test_generation.py tests/test_cli.py tests/test_seedance.py
git commit -m "feat: finals and the draft upgrade come from the generator; WaveSpeed finals are new takes"
```

---

### Task 10: `charswap prepare`: WaveSpeed by default, `depth_mesh`, refusals and the rights question

**Files:** Modify `src/character_swap/cli.py` (`EPILOG` lines 149–160, the `prepare` parser lines 173–194, `_characters` lines 310–342, `_prepare` lines 345–392, imports), `src/character_swap/faces.py` (`PHOTO_CONSENT` after `CONSENT`; `check_photo` messages). Tests: `tests/test_cli.py`.

Behaviour:
- `--provider wavespeed|piapi` (Seedance only; default `wavespeed`) is saved on the job; `--provider` with `--generator wan` is refused.
- The default Seedance mode is `depth_mesh`.
- On WaveSpeed, before any question: `--face` is refused ("Saved faces are a PiAPI feature; use --character me.jpg with WaveSpeed, or --provider piapi."), and `--face2` / `--character2` are refused ("Two people need --provider piapi for now.").
- On WaveSpeed, after the free photo check and before the cost question: the provider-neutral `PHOTO_CONSENT` sentence is printed and the rights question is asked. `--yes` answers yes to both questions; the sentence is still printed.
- The PiAPI "real faces must use --face" note is shown only with `--provider piapi`.
- The "Seedance needs a character" error, the `--character`/`--face`/`--face2`/`--character2`/`--provider`/`--yes` help and the EPILOG (WaveSpeed first) are updated.

- [ ] **Step 1: Update existing tests to say `--provider piapi` where they test PiAPI behaviour.** In `tests/test_cli.py`, add `"--provider", "piapi",` right after `str(video),` in the argv of each of these tests:
  - `test_prepare_seedance_with_a_saved_face` (also add `and state["provider"] == "piapi"` to its first assert);
  - `test_prepare_seedance_needs_a_character`;
  - `test_prepare_refuses_face_and_character_together`;
  - `test_prepare_refuses_a_face_that_is_not_active`;
  - `test_prepare_seedance_character_image_is_checked_and_warned`;
  - `test_prepare_two_people`;
  - `test_seedance_still_refuses_a_processing_face`;
  - `test_real_person_note_prints_once_for_two_characters`.

  In `test_prepare_declined_creates_nothing`, the first question is now the rights question; to keep testing "decline the cost", change `confirm=lambda _q: False,` to `confirm=lambda q: q != "Continue?",`.

  Change `from character_swap.faces import FaceLibrary` to `from character_swap.faces import PHOTO_CONSENT, FaceLibrary`.

- [ ] **Step 2: Failing tests.** Append to `tests/test_cli.py`:

```python
# ---- prepare: WaveSpeed is the default ----------------------------------------------------
def test_prepare_defaults_to_wavespeed_and_depth_mesh(tmp_path, settings, video, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "me.jpg")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["provider"] == "wavespeed" and state["mode"] == "depth_mesh"
    assert state["character_primary"] == "character-primary.jpg"
    assert PHOTO_CONSENT in out.text  # --yes answers the question, the sentence still shows
    assert "not a real person" not in out.text


def test_wavespeed_asks_about_rights_before_the_cost(tmp_path, settings, video):
    asked = []
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda q: asked.append(q) or len(asked) == 1,  # yes to rights, no to the cost
    )
    assert code == 1 and asked == [cli.RIGHTS_QUESTION, "Continue?"]
    assert out.text.index(PHOTO_CONSENT) < out.text.index("Preparation cost")
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


def test_declining_the_rights_question_stops_before_the_cost(tmp_path, settings, video):
    asked = []
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda q: asked.append(q) or False,
    )
    assert code == 1 and asked == [cli.RIGHTS_QUESTION]
    assert "Preparation cost" not in out.text and "Cancelled. Nothing was charged." in out.err.text
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


@pytest.mark.parametrize("option", ["--face", "--face2", "--character2"])
def test_wavespeed_refuses_piapi_only_options_before_any_question(
    tmp_path, settings, video, option
):
    _saved_face(settings, tmp_path)  # even a real saved face is refused on WaveSpeed
    value = str(_photo(tmp_path / "b.jpg")) if option == "--character2" else "frank"
    argv = ["prepare", str(video), option, value]
    if option != "--face":
        argv += ["--character", str(_photo(tmp_path / "me.jpg"))]
    asked = []
    code, out = run(argv, settings, confirm=lambda q: asked.append(q) or True)
    words = (
        "Saved faces are a PiAPI feature; use --character me.jpg with WaveSpeed, or "
        "--provider piapi."
        if option == "--face"
        else "Two people need --provider piapi for now."
    )
    assert code == 2 and asked == [] and words in out.err.text
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


def test_provider_is_refused_with_wan(tmp_path, settings, video):
    asked = []
    code, out = run(
        _wan_args(video, _photo(tmp_path / "a.jpg"), "--provider", "piapi"),
        settings,
        confirm=lambda q: asked.append(q) or True,
    )
    assert code == 2 and asked == [] and "--provider is only for Seedance" in out.err.text


def test_wavespeed_needs_a_character_photo(settings, video):
    code, out = run(["prepare", str(video), "--yes"], settings)
    assert code == 2 and "--character IMAGE" in out.err.text and "--face" not in out.err.text


def test_piapi_with_a_character_image_asks_no_rights_question(
    tmp_path, settings, video, monkeypatch
):
    ids = _prepare_ids(monkeypatch)
    asked = []
    code, out = run(
        [
            "prepare",
            str(video),
            "--provider",
            "piapi",
            "--character",
            str(_photo(tmp_path / "toon.png")),
        ],
        settings,
        confirm=lambda q: asked.append(q) or True,
        services_factory=lambda s: object(),
    )
    assert code == 0 and asked == ["Continue?"] and PHOTO_CONSENT not in out.text
    assert Job.load(settings.runs_dir, ids[0]).state["provider"] == "piapi"


def test_help_lists_wavespeed_first(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    first = text.index("charswap prepare clip.mp4 --character me.jpg")
    assert first < text.index("charswap prepare clip.mp4 --provider piapi --face frank")
```

- [ ] **Step 3: Run them and watch them fail.** `uv run pytest tests/test_cli.py -q --no-cov`. Expected: `ImportError: cannot import name 'PHOTO_CONSENT'` (after adding it in Step 4 alone, the new tests fail on `--provider` being unknown: `SystemExit: 2`).

- [ ] **Step 4: Faces.** In `src/character_swap/faces.py`, after the `CONSENT = (...)` block add:

```python
# Asked by `charswap prepare` when a plain photo is sent (WaveSpeed); provider-neutral.
PHOTO_CONSENT = (
    "You confirm you have the right to use this person's face: your own, or someone who "
    "agreed to it. The photo is sent with your clip to the video provider."
)
```

In `check_photo`, change the docstring to `"""Check a photo against the Seedance limits (PiAPI's) and save it as an upright RGB JPEG."""`, change `"too wide or too narrow for PiAPI."` to `"too wide or too narrow for Seedance."`, and change `PiAPI needs at least {MIN_SIDE} px` to `Seedance needs at least {MIN_SIDE} px`.

- [ ] **Step 5: CLI.** In `src/character_swap/cli.py`:
  - change `from character_swap.faces import ACTIVE, CONSENT, FAILED, PROCESSING, FaceLibrary, check_photo` to
    `from character_swap.faces import ACTIVE, CONSENT, FAILED, PHOTO_CONSENT, PROCESSING, FaceLibrary, check_photo`
    (ruff wraps it);
  - change `from character_swap.jobs import WHISPER_LANGUAGES, Job, JobError` to
    `from character_swap.jobs import SEEDANCE_PROVIDERS, WHISPER_LANGUAGES, Job, JobError`;
  - replace `EPILOG` with:

    ```python
    EPILOG = """example:
      charswap prepare clip.mp4 --character me.jpg
      charswap status <id>
      charswap review <id> --approve --note "watched all" --fingerprint <fp>
      charswap prompt <id>
      charswap generate <id> --draft
      charswap generate <id> --final
      charswap generate <id> --final --resolution 1080p
      charswap view

    other providers:
      charswap face add frank me.jpg
      charswap prepare clip.mp4 --provider piapi --face frank
      charswap prepare clip.mp4 --generator wan --character me.jpg
    """
    ```

  - in `_parser`, replace the lines from `prep.add_argument("--generator", ...)` through `prep.add_argument("--yes", ...)` (keep `--subject`, `--depth-provider` and `--language` exactly as they are) so the `prepare` arguments read:

    ```python
        prep = sub.add_parser("prepare", help="prepare a video for review")
        prep.add_argument("video", type=Path)
        prep.add_argument("--generator", choices=["seedance", "wan"], default="seedance")
        prep.add_argument(
            "--provider",
            choices=list(SEEDANCE_PROVIDERS),
            help="Seedance only: wavespeed (default; pay per use, a real face as a plain photo) "
            "or piapi (saved faces, two people)",
        )
        prep.add_argument(
            "--mode",
            choices=["depth", "face_mesh", "depth_mesh", "direct"],
            help="default: depth_mesh for Seedance, direct for Wan",
        )
        prep.add_argument("--subject", default="person", help="what SAM 3 should cut out")
        prep.add_argument("--depth-provider", choices=["replicate", "huggingface"], default="replicate")
        prep.add_argument("--face", help="PiAPI: a saved face to put in the video (charswap face add)")
        prep.add_argument(
            "--character",
            type=Path,
            help="photo of the person or character to put in the video (WaveSpeed: real people "
            "too; PiAPI: only characters that are not a real person; Wan: any picture)",
        )
        prep.add_argument("--face2", help="PiAPI: a second saved face, for a second person")
        prep.add_argument("--character2", type=Path, help="PiAPI: a second character image")
        prep.add_argument(
            "--language",
            default="auto",
            choices=WHISPER_LANGUAGES,
            metavar="CODE",
            help="spoken language code for the transcript, e.g. en or es (default: auto, detect it)",
        )
        prep.add_argument(
            "--yes", action="store_true", help="answer yes to the questions (rights and cost)"
        )
    ```

  - replace the whole `_characters` function with:

    ```python
    PIAPI_CHARACTER_NOTE = (
        "Note: Seedance on PiAPI takes --character images only for characters that are not a "
        "real person (PiAPI rejects real faces sent this way). For a real person, use --face."
    )


    def _characters(
        args, settings, env, scratch: Path, provider: str
    ) -> tuple[Path | None, Path | None, dict]:
        """Resolve --face/--character (and the second slot) to image files + face names. Free."""
        library = FaceLibrary(settings.root)
        images: dict[str, Path | None] = {}
        faces: dict[str, str] = {}
        noted = False
        for slot, face_name, image in (
            ("primary", args.face, args.character),
            ("secondary", args.face2, args.character2),
        ):
            flag = "" if slot == "primary" else "2"
            if face_name and image:
                raise JobError(f"Use --face{flag} or --character{flag}, not both.")
            if face_name:
                face = library.get(face_name)
                if args.generator == "seedance" and face.status != ACTIVE:
                    raise JobError(
                        f"Face {face_name!r} is {face.status} at PiAPI. Run charswap face check "
                        f"{face_name} first."
                    )
                images[slot], faces[slot] = library.image_path(face), face_name
            elif image and args.generator == "seedance":
                if provider == "piapi" and not noted:
                    env.out(PIAPI_CHARACTER_NOTE)
                    noted = True
                images[slot] = check_photo(image, scratch / f"character-{slot}.jpg")
            else:
                images[slot] = image
        return images.get("primary"), images.get("secondary"), faces
    ```

  - replace the whole `_prepare` function with:

    ```python
    FACE_ON_WAVESPEED = (
        "Saved faces are a PiAPI feature; use --character me.jpg with WaveSpeed, or "
        "--provider piapi."
    )
    TWO_PEOPLE_ON_WAVESPEED = "Two people need --provider piapi for now."
    NEEDS_CHARACTER = {
        "wavespeed": (
            "Seedance needs --character IMAGE: a clear photo of the person (or character) to "
            "put in the video."
        ),
        "piapi": (
            "Seedance on PiAPI needs --face NAME (a saved face; see charswap face add) or "
            "--character IMAGE (a character that is not a real person)."
        ),
    }
    RIGHTS_QUESTION = "Do you have the right to use this person's face?"


    def _provider_for(args) -> str:
        """The new job's Seedance provider ("" for Wan). Free; refuses before any question."""
        if args.generator == "wan":
            if args.provider:
                raise JobError("--provider is only for Seedance; Wan always runs on Replicate.")
            return ""
        return args.provider or SEEDANCE_PROVIDERS[0]


    def _prepare(args, settings, env) -> int:
        mode = args.mode or ("direct" if args.generator == "wan" else "depth_mesh")
        provider = _provider_for(args)
        if args.generator == "wan" and args.character is None and args.face is None:
            raise JobError(
                "Wan needs --character IMAGE or --face NAME (your face or character picture)."
            )
        if args.generator == "wan" and (args.character2 or args.face2):
            raise JobError("Wan takes one character; a second one only works with Seedance.")
        if provider == "wavespeed":
            if args.face:
                raise JobError(FACE_ON_WAVESPEED)
            if args.face2 or args.character2:
                raise JobError(TWO_PEOPLE_ON_WAVESPEED)
        if args.generator == "seedance" and not (args.face or args.character):
            raise JobError(NEEDS_CHARACTER[provider])
        if mode == "direct" and args.language != "auto":
            raise JobError("--language only matters for Seedance modes (Wan has no transcript step).")
        if mode != "direct":
            settings.require("REPLICATE_API_TOKEN")
        with tempfile.TemporaryDirectory() as scratch:
            # Free checks first, so a bad photo or face is refused before any question.
            character, character2, faces = _characters(args, settings, env, Path(scratch), provider)
            if provider == "wavespeed":
                # A real face goes to WaveSpeed as a plain photo: rights first, then the cost.
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
                generator=args.generator,
                mode=mode,
                subject=args.subject,
                depth_provider=args.depth_provider,
                character=character,
                character2=character2,
                faces=faces,
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
    ```

- [ ] **Step 6: Run tests.** `uv run pytest tests/test_cli.py tests/test_faces.py -q --no-cov`. Expected: PASS (including the older prepare tests: `test_prepare_seedance_still_asks`, `test_missing_key_is_reported`, `test_prepare_passes_the_language`, `test_keyboard_interrupt_is_reported_with_job_id`).

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/cli.py src/character_swap/faces.py tests/test_cli.py
git commit -m "feat: prepare defaults to WaveSpeed and depth_mesh; PiAPI-only options refused; rights question"
```

---

### Task 11: `charswap doctor`, where to get the key, `.env.example`

**Files:** Modify `src/character_swap/config.py` (`WHERE_TO_GET`, lines 13–17), `src/character_swap/cli.py` (`_doctor`, lines 611–627), `.env.example`. Tests: `tests/test_config.py`, `tests/test_cli.py`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_config.py`:

```python
def test_require_wavespeed_names_where_to_get_it(tmp_path):
    settings = Settings.load(root=tmp_path, environ={})
    with pytest.raises(
        MissingSettingError, match=r"WAVESPEED_API_KEY.*wavespeed\.ai \(Dashboard > API Keys\)"
    ):
        settings.require("WAVESPEED_API_KEY")
```

In `tests/test_cli.py`:
  - in `test_doctor_reports_checks`, replace `assert "hosting: PiAPI temporary upload" in out.text` with:

    ```python
        assert "WAVESPEED_API_KEY: missing" in out.text
        assert "PIAPI_API_KEY: missing (optional; only for --provider piapi)" in out.text
        assert "hosting: WaveSpeed temporary upload" in out.text
    ```

  - replace `test_doctor_reports_piapi_hosting_and_faces` with:

    ```python
    def test_doctor_reports_both_uploads_and_faces(settings):
        code, out = run(["doctor"], settings)
        assert "WaveSpeed temporary upload (kept 7 days)" in out.text
        assert "PiAPI temporary upload (24 hours" in out.text and "saved faces: 0" in out.text


    def test_doctor_says_when_the_wavespeed_key_is_set(ws_settings):
        code, out = run(["doctor"], ws_settings)
        assert "WAVESPEED_API_KEY: set" in out.text
    ```

    (`ws_settings` is defined further down the file; pytest resolves fixtures by name, so the order does not matter. `test_doctor_never_prints_secret_values` stays the secrecy test.)

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_config.py tests/test_cli.py -q --no-cov -k "doctor or wavespeed_names"`. Expected: the doctor asserts fail (no `WAVESPEED_API_KEY` line) and `require` says "get it at your provider dashboard".

- [ ] **Step 3: Config.** In `src/character_swap/config.py`, add to `WHERE_TO_GET` right after the `REPLICATE_API_TOKEN` entry:

```python
    "WAVESPEED_API_KEY": "https://wavespeed.ai (Dashboard > API Keys)",
```

- [ ] **Step 4: Doctor.** In `src/character_swap/cli.py::_doctor`, replace

```python
    for key in ("REPLICATE_API_TOKEN", "PIAPI_API_KEY"):
        out(f"{key}: {'set' if settings.get(key) else 'missing'}")
    out("hosting: PiAPI temporary upload (Seedance; needs the PiAPI Creator plan)")
```

with

```python
    for key, note in (
        ("REPLICATE_API_TOKEN", ""),
        ("WAVESPEED_API_KEY", ""),
        ("PIAPI_API_KEY", " (optional; only for --provider piapi)"),
    ):
        out(f"{key}: {'set' if settings.get(key) else 'missing'}{note}")
    out(
        "hosting: WaveSpeed temporary upload (kept 7 days); PiAPI temporary upload (24 hours; "
        "--provider piapi, needs the PiAPI Creator plan)"
    )
```

- [ ] **Step 5: `.env.example`.** Replace its contents with:

```
# Copy to .env and fill in. Never share a filled .env.
# Preparation steps + Wan generator: https://replicate.com/account/api-tokens
REPLICATE_API_TOKEN=
# Seedance generator (default): https://wavespeed.ai (Dashboard > API Keys). Pay per use.
WAVESPEED_API_KEY=
# Optional, only for --provider piapi: https://piapi.ai (Workspace > API Keys). Saved faces
# and file uploads need the Creator plan or higher.
PIAPI_API_KEY=
# Optional: Hugging Face depth alternative
HF_TOKEN=
```

- [ ] **Step 6: Run tests.** `uv run pytest tests/test_config.py tests/test_cli.py -q --no-cov`. Expected: PASS (`test_doctor_never_prints_secret_values` still passes).

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/config.py src/character_swap/cli.py .env.example tests/test_config.py tests/test_cli.py
git commit -m "feat: doctor reports the WaveSpeed key and both uploads; where to get the key"
```

---

### Task 12: The viewer names the job's provider (read-only)

**Files:** Modify `src/character_swap/web/view_model.py` (imports, `STEP_NAMES` area, `Section`, the Draft and Final `SECTIONS` entries, `read_state`, `cost_rows`, `_seedance_texts`, `_section_view`, `job_view`, new `section_text`). Tests: `tests/test_view_model.py`, `tests/test_web_app.py`.

The viewer uses `generator_key(job, state)` with the state it already read; it never writes `provider` (the old-job rule is a pure read). Cost rows and the prompt line say "WaveSpeed" or "PiAPI"; WaveSpeed jobs get their own Draft and Final "Why this step?" texts (no "PiAPI", no "upgrades the exact draft"); the default "Final (…)" resolution comes from the generator's `finals` (done in Task 7). The page layout does not change.

- [ ] **Step 1: Failing tests.** In `tests/test_view_model.py`:
  - in `test_every_section_explains_why`, after the `seedance_why` check add:

    ```python
            if section.wavespeed_why:
                assert len(section.wavespeed_why) > 40 and "—" not in section.wavespeed_why
                assert "PiAPI" not in section.wavespeed_why and "upgrade" not in section.wavespeed_why
    ```

  - in `test_seedance_draft_shows_the_prompt_and_piapi_costs`, change `["Prompt sent"]` to `["Prompt sent to PiAPI"]`;
  - in `test_empty_or_odd_prompt_and_resolution_show_nothing`, change `"Prompt sent" not in` to `"Prompt sent to PiAPI" not in`;
  - in `test_only_the_prompt_keeps_its_line_breaks`, change `== ["Prompt sent"]` to `== ["Prompt sent to PiAPI"]`;
  - append:

```python
# ---- WaveSpeed jobs (WaveSpeed plan) ------------------------------------------------------
def _wavespeed_job(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh")
    job.update(provider="wavespeed")
    return job


def test_wavespeed_draft_and_final_name_wavespeed(runs_dir, media):
    job = _wavespeed_job(runs_dir, media)
    finish_stage(job, media, "draft")
    job.mark_stage("draft", fingerprint=fingerprint(job), prompt="Put @Image 1 into @Video 1.")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft", "provider": "wavespeed"})
    finish_stage(job, media, "final")
    view = vm.job_view(job, runs_dir)
    by_id = sections(view)
    assert "WaveSpeed" in by_id["draft"].why and "PiAPI" not in by_id["draft"].why
    assert "PiAPI" not in by_id["final"].why and "upgrade" not in by_id["final"].why
    assert by_id["final"].title == "Final" and not by_id["draft"].links
    texts = {t.label: t.value for t in by_id["draft"].texts}
    assert texts["Prompt sent to WaveSpeed"] == "Put @Image 1 into @Video 1."
    rows = {r.step: r for r in view.costs.rows}
    assert rows["Draft (480p)"].provider == "WaveSpeed" and rows["Draft (480p)"].usd == 0.88
    assert rows["Final (720p)"].provider == "WaveSpeed" and rows["Final (720p)"].link is None
    assert rows["Separate vocals"].provider == "Replicate"


def test_the_viewer_never_writes_the_provider(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh")  # no provider saved: read as WaveSpeed's
    before = job.path("state.json").read_bytes()
    view = vm.job_view(job, runs_dir)
    vm.list_jobs(runs_dir)
    assert "WaveSpeed" in sections(view)["draft"].why
    assert job.path("state.json").read_bytes() == before


def test_an_unknown_provider_is_a_view_error(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    job.update(provider="sora")
    with pytest.raises(vm.ViewError, match="unknown provider: 'sora'"):
        vm.job_view(job, runs_dir)
    (summary,) = vm.list_jobs(runs_dir)
    assert "unknown provider" in summary.error
```

  In `tests/test_web_app.py`:
  - add `from character_swap.pipeline import generator_key`, and change
    `from character_swap.web.view_model import SECTIONS, sections_for` to
    `from character_swap.web.view_model import SECTIONS, section_text, sections_for`;
  - in `test_job_page_has_every_section_and_anchor`, replace

    ```python
        text = response.text.replace("&#39;", "'")
        for section in sections_for(mode):
            seedance = mode != "direct"
            why = (section.seedance_why or section.why) if seedance else section.why
            title = (section.seedance_title or section.title) if seedance else section.title
            assert why in text and title.replace("&", "&amp;") in response.text, section.id
    ```

    with

    ```python
        text = response.text.replace("&#39;", "'")
        key = generator_key(job)  # a fresh Seedance job with no draft reads as WaveSpeed's
        for section in sections_for(mode):
            title, why = section_text(section, key)
            assert why in text and title.replace("&", "&amp;") in response.text, section.id
    ```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov`. Expected: `AttributeError: 'Section' object has no attribute 'wavespeed_why'`, `ImportError: cannot import name 'section_text'`, and the "Prompt sent to PiAPI" label missing.

- [ ] **Step 3: View model.** In `src/character_swap/web/view_model.py`:
  - change `from character_swap.jobs import Job, JobError` to `from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError`;
  - after the `STEP_NAMES = {...}` dict add:

    ```python
    # What each pipeline.generator_key is called on the page (cost rows, the prompt line).
    PROVIDER_NAMES = {"wan": "Replicate", "seedance:wavespeed": "WaveSpeed", "seedance:piapi": "PiAPI"}
    ```

  - in `class Section`, after `seedance_why: str = ""` add:

    ```python
        wavespeed_why: str = ""  # used instead of seedance_why on WaveSpeed jobs
    ```

  - in the `Section(id="draft", ...)` entry, after its `seedance_why=(...)` argument add:

    ```python
            wavespeed_why=(
                "A cheaper 480p test run on WaveSpeed. Seedance redraws the disguised person as "
                "your character, guided by the prompt, then your original soundtrack is put back "
                "unchanged."
            ),
    ```

  - in the `Section(id="final", ...)` entry, after its `seedance_why=(...)` argument add:

    ```python
            wavespeed_why=(
                "A new take at 720p (or 1080p if you choose it) with the same prompt and inputs, "
                "so it will look a little different from the draft. The original soundtrack is "
                "restored again."
            ),
    ```

  - in `read_state`, before `return state` add:

    ```python
        provider = state.get("provider")
        if provider is not None and provider not in SEEDANCE_PROVIDERS:
            raise ViewError(f"state.json has an unknown provider: {provider!r}.")
    ```

  - replace the whole `cost_rows` function with:

    ```python
    def cost_rows(job: Job, state: dict[str, Any]) -> tuple[CostRow, ...]:
        """One row per paid stage that is done. Derived from state, never stored."""
        rows = []
        key = generator_key(job, state)
        seedance = key.startswith("seedance")
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
    ```

  - replace `_seedance_texts` with:

    ```python
    def _seedance_texts(state: dict[str, Any], stage: str, key: str) -> list[TextItem]:
        entry = state.get("stages", {}).get(stage)
        if not isinstance(entry, dict):
            return []
        prompt, resolution = entry.get("prompt"), entry.get("resolution")
        if stage == "draft" and isinstance(prompt, str) and prompt:
            label = f"Prompt sent to {PROVIDER_NAMES.get(key, 'Seedance')}"
            return [TextItem(label, prompt, multiline=True)]
        # Saved when a final starts: a failed one says nothing (a plain --final may pick another)
        started = stage_status(state, "final") in ("done", "running")
        if stage == "final" and started and isinstance(resolution, str) and resolution:
            return [TextItem("Resolution", resolution)]
        return []


    def section_text(section: Section, key: str) -> tuple[str, str]:
        """The section's (title, why) for a job's generator_key."""
        if not key.startswith("seedance"):
            return section.title, section.why
        why = section.seedance_why or section.why
        if key == "seedance:wavespeed" and section.wavespeed_why:
            why = section.wavespeed_why
        return section.seedance_title or section.title, why
    ```

  - replace the whole `_section_view` function with:

    ```python
    def _section_view(
        job: Job,
        state: dict[str, Any],
        section: Section,
        number: int,
        decision: dict[str, Any],
        stale: frozenset[str] = frozenset(),
        key: str = "",
    ) -> SectionView:
        key = key or generator_key(job, state)
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
            elif section.id in stale:
                texts = _stale_texts(job, decision)
            if section.id in GENERATION_STAGES and key.startswith("seedance"):
                texts = [*texts, *_seedance_texts(state, section.id, key)]
        error = _error(state, section)
        title, why = section_text(section, key)
        return SectionView(
            id=section.id,
            number=number,
            title=title,
            why=why,
            status=status,
            media=tuple(_media(job, state, asset) for asset in section.assets),
            texts=tuple(texts),
            links=tuple(_links(job, state, section)),
            error=error,
            stopped=error == INTERRUPTED,
            retry="charswap " + section.retry.format(id=job.id),
        )
    ```

  - in `job_view`, after `stale = stale_stages(job, state) if MODES[mode].generates else frozenset()` add
    `key = generator_key(job, state)`, and change
    `_section_view(job, state, section, number, decision, stale)` to
    `_section_view(job, state, section, number, decision, stale, key)`.

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov`. Expected: PASS.

- [ ] **Step 5: Look at it.** Start the viewer with `uv run charswap view --no-browser`. Tell Frank it is running and that you'll stop it after the check. In a scratch `CHARSWAP_RUNS_DIR`, build one WaveSpeed demo job and one PiAPI demo job with the `tests/viewer_jobs.py` helpers (draft and final finished; `job.update(provider="wavespeed")` for the first, `job.update(face_primary="frank")` for the second). Check:
  - the WaveSpeed job's Draft/Final texts and cost rows say WaveSpeed, and its final row is "Final (720p)";
  - the PiAPI job still says PiAPI and "1080p upgrades the exact draft";
  - "Prompt sent to …" shows the prompt with its line breaks;
  - nothing overflows at phone width.

  Then stop the viewer and say so.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
uv run pytest -q --no-cov
git add src/character_swap/web/view_model.py tests/test_view_model.py tests/test_web_app.py
git commit -m "feat: viewer names the job's provider; WaveSpeed draft and final texts"
```

---

### Task 13: Full verification and docs

**Files:** Modify `README.md`, `CLAUDE.md`, `activity.md`, `docs/scratchpad.md`.

- [ ] **Step 1: Full checks.** Run `uv run ruff format --check src tests && uv run ruff check src tests && uv run pytest`. Expected: all pass, coverage ≥ 85 %. Record the test count and the coverage figure.

- [ ] **Step 2: Grep for leftovers.**
  - `grep -rn 'generator == "seedance"\|generator != "seedance"' src` may only show generator-level checks that are the same for both providers: `jobs.py` (`--provider` only for Seedance), `pipeline.py` (`generator_key`, `_character`, the prepared-video choice in `build_request`, `draft_prompt_changed`, `rename_old_prompt_names`), `cli.py` (`_characters`, `_prepare`, `_prompt`, the fresh-final note) and `view_model.py::_links`. None may decide a promote, the face library or PiAPI wording.
  - `grep -rn 'PROMOTE' src` should show only `providers/generators/seedance_piapi.py`.
  - `grep -rn 'generators\["seedance"\]\|KEYS\[job.generator\]\|UNCONFIRMED\[job.generator\]' src tests` should find nothing.
  - `grep -rn "WAVESPEED_API_KEY" src` must never show the key's value being printed (only its name and `settings.require`).

- [ ] **Step 3: README.**
  - Replace the first paragraph's end "uses Seedance 2.5 (via PiAPI) or the open-source Wan 2.2 Animate (via Replicate)." with "uses Seedance 2.5 (via WaveSpeed, or PiAPI) or the open-source Wan 2.2 Animate (via Replicate)."
  - Replace the **Status** line with: "**Status:** work in progress. Seedance on WaveSpeed is the default (three live drafts so far); Wan (one live draft) and Seedance on PiAPI (not yet tested live) also work. The design is in [docs/superpowers/specs/](docs/superpowers/specs/2026-11-12-wavespeed-seedance-design.md)."
  - Insert this section right before `## Try it with Wan (open source, on Replicate)`:

````markdown
## Try it with Seedance on WaveSpeed (best quality, real faces)

Sign up at [WaveSpeed](https://wavespeed.ai): pay per use, $1 of free credit, no
subscription. Put your key in `.env` as `WAVESPEED_API_KEY=...` (Dashboard > API Keys), plus
`REPLICATE_API_TOKEN` for preparation and Rubber Band (`brew install rubberband`).

```bash
uv run charswap prepare inputs/clip.mp4 --character inputs/me.jpg  # asks about rights, then ~$0.06
# watch it in `uv run charswap view`, then approve with the command shown there
uv run charswap prompt <id>                                  # optional: edit prompt.txt
uv run charswap generate <id> --draft                        # 480p, shows the price, asks first
uv run charswap generate <id> --final                        # a new 720p take
uv run charswap generate <id> --final --resolution 1080p     # or a new 1080p take
```

The default mode is `depth_mesh`: a depth disguise with a face mesh on top, which gave the
best lip sync in tests. Prices for a 4 s clip, estimated: draft about $0.88, 720p final about
$1.76, 1080p final about $4.40 (WaveSpeed bills the input video as well as the result, and at
least 4 s). A 4 s draft takes about 3 minutes. Test with 4–5 s clips first.

WaveSpeed cannot upgrade the draft itself, so the final is a new take: it will look a little
different from the draft. A real person's face works as a plain photo; only use faces you
have the right to use (prepare asks). One person per clip for now; two people need
`--provider piapi`. Clips are trimmed to whole seconds; the original sound is cut to match
and put back unchanged.
````

  - Replace the heading `## Try it with Seedance (best quality, real faces, on PiAPI)` with:

    ```markdown
    ## Other providers

    ### Seedance on PiAPI (saved faces, two people, the 1080p draft upgrade)
    ```

  - In that PiAPI section, change `uv run charswap prepare clip.mp4 --mode depth --face me    # ~$0.06: the depth disguise` to `uv run charswap prepare clip.mp4 --provider piapi --mode depth --face me  # ~$0.06`, and change "`--character IMAGE` is only for characters that are not real people (PiAPI rejects real faces sent that way); use a saved face for a real person." to "With `--provider piapi`, `--character IMAGE` is only for characters that are not real people (PiAPI rejects real faces sent that way); use a saved face for a real person."

- [ ] **Step 4: CLAUDE.md.**
  - Intro: "Generators: Seedance 2.5 on WaveSpeed (default; real faces as a plain photo) or on PiAPI (saved faces, two people, 1080p upgrade), and Wan 2.2 Animate on Replicate (open source)."
  - Commands: `Generate (paid, asks first): uv run charswap generate <job> --draft|--final [--resolution 720p|1080p]`.
  - Hard rules, first bullet: "... PiAPI and WaveSpeed 429s are not retried. Persist provider IDs before waiting."
  - Layout: add "A job's Seedance provider (`provider` in `state.json`) is fixed at prepare; old jobs are resolved by `pipeline.generator_key`."

- [ ] **Step 5: Journal and checklist.**
  - `activity.md`: add a session entry: what was built (WaveSpeed client and generator, provider per job, finals by generator, prompts per provider, viewer), decisions W1–W6 and P1–P10 in one line each with their why, the test count and coverage, and the lessons.
  - `docs/scratchpad.md`: under "Active work" add `- [x] WaveSpeed Seedance generator (default provider; <N> tests, <X> % coverage; branch feat/wavespeed)` and `- [ ] Live WaveSpeed draft through charswap (Task 14 of the WaveSpeed plan, ~$0.88, needs Frank's OK)`; add the "Deferred" items below.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md activity.md docs/scratchpad.md
git commit -m "docs: WaveSpeed first in README; CLAUDE.md, journal and checklist"
```

- [ ] **Step 7: Finish the branch.** Use superpowers:finishing-a-development-branch. Frank still gives the OK for merging or pushing each time.

---

### Task 14: Live WaveSpeed draft through `charswap generate` (Frank; paid; needs his explicit OK)

**Files:** Create `tests/test_live_wavespeed.py`.

Writing the test file is free and part of the plan; **running it costs about $0.88 and only happens after Frank says yes in that session.** Subagents never run it.

- [ ] **Step 1: The test.** Create `tests/test_live_wavespeed.py`:

```python
"""Live WaveSpeed draft (about $0.88). Never part of the default run; needs Frank's explicit OK.

Run:  CHARSWAP_LIVE_JOB=<id> uv run pytest tests/test_live_wavespeed.py -m live --no-cov -q
<id> is an approved 4 s WaveSpeed job (prepared with `charswap prepare ... --character ...`)
that has no draft yet. WAVESPEED_API_KEY must be in .env.
"""

import json
import os

import pytest

from character_swap import cli
from character_swap.config import Settings
from character_swap.jobs import Job
from character_swap.media.video import stream_seconds

pytestmark = pytest.mark.live

JOB = os.environ.get("CHARSWAP_LIVE_JOB", "")


@pytest.mark.skipif(not JOB, reason="set CHARSWAP_LIVE_JOB to an approved 4 s WaveSpeed job")
def test_a_4_second_480p_wavespeed_draft_end_to_end():
    settings = Settings.load()
    job = Job.load(settings.runs_dir, JOB)
    assert job.state.get("provider") == "wavespeed" and not job.stage_done("draft")
    assert job.state["video"]["seconds"] == 4.0
    assert cli.main(["generate", JOB, "--draft", "--yes"], settings=settings) == 0
    assert job.stage_done("draft")
    record = json.loads(job.path("draft-prediction.json").read_text())
    assert record["status"] == "succeeded" and record["id"]
    assert abs(stream_seconds(job.path("draft.mp4"), "video") - 4.0) < 0.1
    draft_costs = [c for c in job.state["costs"] if c["step"] == "draft"]
    assert draft_costs[-1]["usd"] == pytest.approx(0.88)
    assert draft_costs[-1]["provider"] == "wavespeed"
```

- [ ] **Step 2: Check it is deselected by default.** `uv run pytest -q --no-cov` shows the same pass count as before plus `1 deselected`; `uv run ruff check tests/test_live_wavespeed.py` is clean.

- [ ] **Step 3: Commit (free)**

```bash
git add tests/test_live_wavespeed.py
git commit -m "test: live WaveSpeed draft through charswap generate (marked live)"
```

- [ ] **Step 4: Frank's live run (paid; only with his yes).** Prerequisites: `WAVESPEED_API_KEY` in `.env` (Frank adds it himself), a **4 s** one-person clip and a clear front-facing photo in `inputs/`.
  1. `uv run charswap doctor`: `WAVESPEED_API_KEY: set`.
  2. `uv run charswap prepare inputs/<clip-4s>.mp4 --character inputs/<photo>.jpg` (rights question, then about $0.06). Watch it in the viewer and approve.
  3. `uv run charswap prompt <id>`: read the prompt together with Frank.
  4. Ask Frank for the OK (about $0.88). Then: `CHARSWAP_LIVE_JOB=<id> uv run pytest tests/test_live_wavespeed.py -m live --no-cov -q`.
  5. Record in `docs/research/2026-10-22-provider-api-facts.md`: run time, the first status words WaveSpeed returned, the output size/fps/length, whether the sound lines up, the real charge on WaveSpeed's dashboard compared with the $0.88 estimate, and Frank's verdict. Update `costs.py` if the price differs. Log the session.
  6. Only if Frank likes the draft and says yes: `uv run charswap generate <id> --final` (about $1.76) and record the same facts.
  7. Low balance: never trigger it on purpose (a POST with enough credit is a real charge). If a create is ever refused for low balance (for example once the free $1 is used up), record in the research doc the HTTP status and the one-line answer the error message shows ("WaveSpeed answered <code>: ..."), and check the dashboard that nothing was created. Once that is confirmed, a small follow-up adds that code to `REJECTED` in `providers/wavespeed.py` (released marker, "nothing was charged") with its own test.

---

## Deferred

| Item | Where |
|---|---|
| Two-person clips on WaveSpeed (W6) | after a live PiAPI two-person test |
| WaveSpeed webhooks or sync mode | backlog |
| Refuse a too-large upload before the cost question (today: inside `generate`, after the yes, before any upload or POST; see deviations) | Plan 3 (`services.py` pre-flight) |
| Viewer: show the provider in the job header | Plan 3 UI (layout changes are out of scope here) |
| Project SKILL file | Plan 3 |
| BytePlus generator | own plan, if ever |

## Spec deviations and gap fills

The spec fit the code with these small adjustments; none changes a decision or a money-safety rule.

1. **Placeholder check location.** The spec says `_check_placeholders` in `pipeline.py` takes the provider. It moved to `prompts.check_placeholders(text, *, provider, has_secondary)` (Task 3) so the WaveSpeed generator runs the same free check before uploading, as the spec's generator section requires. `pipeline.generation_prompt` calls it exactly where `_check_placeholders` was called.
2. **`generator_key(job, state=None)`.** The spec says it takes the `Job`, not a bare state dict. It still does; the optional `state` lets the viewer pass the `state.json` it already read and validated for the page. It remains a pure read of the job folder and never writes.
3. **`pipeline.generate` also pins the provider.** The spec names `charswap prompt` and `charswap generate` as the only writers. `pipeline.generate` is only reached through `charswap generate` and pins again (a no-op there) so the paid path is safe on its own; the viewer and every other command still never write `provider`.
4. **A too-large upload is refused after the yes, not before the cost question.** Rule 5 lists "an upload that is too large" among the refusals before the cost question. Checking that needs the compressed copy (`fit_for_upload`), which is slow local work, so it stays inside `generate`, as it is for PiAPI today: after the yes, but before any upload or POST, so nothing is charged. For a remake, the old draft has already been moved to `draft-old-*` (kept, never deleted). With WaveSpeed's 200 MB limit this practically never triggers. Listed under Deferred.
5. **WaveSpeed create answers the spec does not list (decision P5).** Other 4xx codes (for example 402 for no credit, 409) are treated as ambiguous and keep the marker, because the spec only names 400/401/403/404/422 as clean refusals. A refusal whose body still names a prediction id saves that prediction (resumable, "may be charged") instead of releasing the marker, as the PiAPI client does. Every ambiguous create message starts with the HTTP status and a one-line, 200-character excerpt of WaveSpeed's answer, so a low-balance reply is readable; Task 14 records the real low-balance code so it can move to the clean-refusal list.
6. **Poll limit.** The spec gives the back-off (5 s to 15 s) but no ceiling; this plan stops waiting after 720 polls (about 3 hours) with a resumable "still running; no new charge" message (decision P6).
7. **WaveSpeed expiry wording.** `download_now` says "This video link has expired (WaveSpeed does not say how long it keeps results)."; the generator adds "Delete <record> and run the same command again to make a new one (costs about $X)". PiAPI's message is unchanged ("PiAPI keeps results 24 hours").
8. **Old-job rule, one extra sign.** Besides the spec's signs, a logged charge with `provider: "piapi"` in `state["costs"]` also marks an old job as PiAPI's (a cheap check that survives a deleted record).
9. **Unknown `provider` in `state.json`** is an error in the pipeline (`JobError`) and the viewer (`ViewError`); the spec does not cover damaged values (decision P8).
10. **Photo-check wording.** `faces.check_photo` messages say "Seedance" instead of "PiAPI", because WaveSpeed jobs use the same free check (decision P10). Tests match only the unchanged parts.
11. **Housekeeping beyond the spec's README item:** `.env.example` gets `WAVESPEED_API_KEY`; `CLAUDE.md`, `activity.md` and `docs/scratchpad.md` are updated (Task 13), and the research facts doc records the WaveSpeed facts (Task 1).
12. **Price line names.** WaveSpeed estimate lines reuse PiAPI's item names ("Seedance draft (480p)", "Seedance input video (480p)", ...); the provider shows in the spend log, the cost rows and the viewer.
