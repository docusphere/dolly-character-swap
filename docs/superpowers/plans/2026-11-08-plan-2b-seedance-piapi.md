# Plan 2b — Seedance 2.5 on PiAPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Seedance job goes end to end with a real face:
1. Save a face once, free: `charswap face add frank me.jpg`.
2. Prepare the clip: `charswap prepare clip.mp4 --mode depth --face frank`. This is the existing depth disguise, about $0.06.
3. Review the prepared clip.
4. Optionally edit the prompt: `charswap prompt <id>`.
5. Make the draft: `charswap generate <id> --draft` (480p).
6. Make the final: `charswap generate <id> --final` (1080p promote) or `--final --resolution 720p`.
7. The result is `runs/<id>/final.mp4`, with the clip's original soundtrack put back.

Two-person clips work through `--subject` (what SAM cuts out) and a second face slot (`--face2` / `--character2`).

**Architecture:**
- A new PiAPI client (`providers/piapi.py`) mirrors the money-safe pattern of `ReplicateClient.run()`:
  - an exclusive "creating" marker is written before the paid POST;
  - the task id is saved before waiting;
  - a saved task is resumed, never bought again.
- The marker and save helpers move to a shared `providers/common.py`, so both clients use the same code.
- `SeedanceGenerator` (`providers/generators/seedance_piapi.py`) implements the existing `Generator` protocol. Draft is 480p with `draft: true`. The 1080p final promotes the draft with `draft_task_id`. The 720p final is a fresh run with the same prompt and inputs.
- Files reach PiAPI through its own 24-hour temporary upload, so no Cloudflare R2 and no tmpfiles.
- Faces live in a small local library (`faces/faces.json` plus a JPEG copy of each photo). Each entry holds the PiAPI asset id.

**Tech Stack:** Python 3.12, uv, httpx (+ respx in tests), PyAV/NumPy/Pillow, FastAPI/Jinja2 viewer; pytest + pytest-cov (≥ 85 % enforced), ruff.

---

**Spec:** `docs/superpowers/specs/2026-10-22-character-swap-v1-design.md`, sections:
- §5 data flow and prompt review
- §6.1 interface
- §6.1a character references
- §6.2 PiAPI Seedance
- §6.3 face assets
- §6.5 hosting
- §8 face check
- §10 errors

Decisions Q2, Q3 and Q5 below change the spec where they say so.

**Builds on:** Plan 2a as merged (571 tests, 97.6 % coverage). The code below targets these existing APIs:
- `Generator` protocol (`estimate`, `generate(request, stage, *, record, dest, on_created)`)
- `GenerationRequest`, `CharacterRef`
- `pipeline.generate`, `build_request`, `check_can_generate`, `_check_soundtrack_fits`, `generation_files`, `_tracked`, `_write_atomically`
- `ReplicateClient._claim`, `_release`, `_save`, `_create`, `_post_create`, `_wait`
- `costs.PRICES`, `cost_line`, `Estimate`
- `media.audio.add_original_soundtrack`, `restore_original_audio`
- `media.video.normalize`
- `cli.Env`, `main()`, `build_services`
- `web.view_model.SECTIONS`, `cost_rows`, `prediction_url`

**Verified before writing (2026-11-08, public PiAPI docs only, no API calls). Sources are in `docs/research/2026-10-22-provider-api-facts.md`, updated in Task 1.**
- **Auth, create and poll:**
  - Header `x-api-key`. Create with `POST https://api.piapi.ai/api/v1/task`. Poll with `GET https://api.piapi.ai/api/v1/task/{task_id}`.
  - The response is `{code, data:{task_id, status, output:{video | video_url}, error:{code, message, raw_message}}, message}`.
  - Status casing differs between docs, so compare lowercase: `completed`/`success` mean done, `failed` is final, anything else means still running.
  - The output URL lasts **24 h**.
- **Real faces:** `seedance-2.5-less-restriction` accepts real faces **only** as `asset://<id>` from the Private Asset Library once the asset is `Active`. Raw real-face URLs are rejected.
  - The library needs the **Creator plan ($15/month)** or higher.
  - `POST /api/v1/asset/upload {url, name}` returns 202 `{asset_id, status:"Processing"}`. `GET /api/v1/asset/{id}` returns `Processing`/`Active`/`Failed` and refreshes the asset's idle lifetime (3–15 days by plan).
  - Images must be under 30 MB, 300–6000 px per side, aspect 0.4–2.5.
  - `auto_upload_assets` exists, but the docs disagree on its default, so **always send it explicitly as `false`**.
- **Draft body:** `{"model":"seedance","task_type":"seedance-2.5-less-restriction","input":{prompt, mode:"omni_reference", omni_reference_task_type, video_urls, image_urls, resolution:"480p", draft:true, auto_upload_assets:false}}`.
  - `omni_reference_task_type` can be `auto`, `reference`, `edit` or `extend`. `edit` needs `aspect_ratio:"adaptive"` and `duration:-1`, with a 4–30 s source.
  - Placeholders are lowercase `@video1`, `@image1`, `@image2`.
  - `seed` and `audio` are **not** in the 2.5 schema, so we don't send them.
- **Promote:** `{"model":"seedance","task_type":"seedance-2.5-less-restriction","input":{"draft_task_id":..., "resolution":"1080p"}}`.
  - Any other field (prompt, urls, duration, aspect_ratio, seed, audio, omni_reference_task_type, draft) gives a 400.
  - The draft must be completed, under 7 days old and the same task_type.
- **Prices (less-restriction, per second):** 480p $0.165, 720p $0.385, 1080p $0.88.
  - Input video is billed at half the output rate.
  - A promote bills the draft's input video again at half the 1080p rate.
  - So 12 s costs about: draft $2.97, 1080p promote $15.84, 720p re-run $6.93.
- **Temporary upload:** `POST https://upload.theapi.app/api/ephemeral_resource {file_name, file_data: base64}` returns `{code, data:{url}}`.
  - Types: jpg/jpeg/png/webp/mp4/wav/mp3, **no mov**.
  - **10 MB** limit; the docs don't say whether that's before or after base64, so we keep files ≤ 7 MB.
  - Files are deleted after 24 h. Needs Creator or higher (403 below that).
- **Errors:** bad parameters, placeholders, draft or asset problems fail **at create** (400/403/404/409/422/429) before any billing. A content rejection arrives later as `failed`, and **is refunded**. If PiAPI's own guess of the task type disagrees with ours, the task fails after submission.
- **Concurrency:** Creator plan runs 5 jobs at once. Queues can take hours at peak time (09–15 GMT).

## Decisions taken in this plan

| # | Decision | Why |
|---|---|---|
| Q1 | PiAPI first; BytePlus (ByteDance direct) later as a second generator file | PiAPI is the only provider with a documented real-face path on Seedance 2.5 today. Frank's BytePlus business verification and real-person access are pending. The `Generator` protocol keeps the swap to one new file. |
| Q2 | **Hosting = PiAPI's own temporary upload only.** R2 and tmpfiles are dropped (supersedes spec D9 / §6.5) | The Creator plan is required for faces anyway and includes the upload. It's private-ish, lasts 24 h (enough for asset ingestion and a task), and means no Cloudflare account for Frank or viewers. `R2_*` settings and `hosting_mode` are removed. |
| Q3 | **Characters are chosen at `prepare`** for every generator: `--face NAME` (saved face) or `--character IMAGE` (non-real character), plus optional `--face2`/`--character2` for Seedance. The review gate hashes them (supersedes spec §5 "collected at draft") | One rule for Wan and Seedance. The final can only use exactly what was reviewed (the draft fingerprint check already enforces this). |
| Q4 | Face library in `faces/` at the project root (git-ignored): `faces.json` + `faces/<name>.jpg`. Commands: `charswap face add NAME PHOTO`, `charswap face list`, `charswap face check NAME`, `charswap face renew NAME` | Free calls only. `renew` re-uploads the same saved photo when PiAPI has purged an idle asset, so the identity never silently changes. Jobs store the face **name**; the current asset id is looked up at generate time and checked `Active` (a free GET, which also refreshes its lifetime) before any new charge. |
| Q5 | **Whole-second clips for Seedance:** `normalize` drops the tail to a whole number of seconds; the embedded pitched vocals and the restored original soundtrack are cut at that length. Audio packets are copied untouched (just fewer) | PiAPI durations are whole seconds. Cutting the sound at the picture length keeps it in sync. Wan jobs are unchanged. |
| Q6 | `omni_reference_task_type: "edit"` (with `aspect_ratio:"adaptive"`, `duration:-1`), set as one constant | "Replace the person in @video1" is an edit. The live test (Task 14) confirms it; if PiAPI fails it, a one-line follow-up switches to `reference`. |
| Q7 | Final: **1080p promote (default)** or **`--resolution 720p`** (a fresh 720p run with the same prompt and inputs, no `draft`) | 1080p is an exact upgrade of the watched draft but costs about $16 for 12 s; 720p is about $7, and since `seed` isn't supported it can differ a little from the draft. Every estimate includes the input-video charge. |
| Q8 | Prompt review: `charswap prompt <id>` writes `prompt.txt` in the job (if missing) and prints it. `generate --draft` prints the full prompt before the cost question and sends `prompt.txt`. The text sent is saved with the draft stage; a 720p final reuses it | Spec §5 "the confirmed text is what is sent". Not part of the review fingerprint, so editing it never clears the review. |
| Q9 | Upload copy: if `prepared.mp4` is over 7 MB, a smaller `upload-copy.mp4` is re-encoded (CRF 23 → 28 → 33) with the **audio packets copied untouched**; over 7 MB even then → refuse before paying | PiAPI's upload limit is 10 MB. The pitched vocals must survive unchanged (lip sync). |
| Q10 | A 429 on PiAPI's paid create is **not** retried: the marker is released and the message says "busy, nothing charged, run again" | The CLAUDE.md exception covers Replicate only; keep the hard rule simple for a new provider. |
| Q11 | A task that fails after creation (for example a content rejection) keeps its record; the message says PiAPI refunds content rejections and how to try again (delete the record → a new paid run, after a new cost question) | Never re-buy without the user's yes. |
| Q12 | No paid "face check" generation (spec §8 step 2). The library's `Active` status is the check; the first draft is the real test | YAGNI; the cheapest Seedance run is ~$1. |

## Conventions for every task

- Run commands from the repo root: `"/Users/franciscojosejimeneznillardkam/Documents/Frank DOCS/Frank Projects/youtube/hf-genjutsu/character-swap"` (the path contains spaces; quote it).
- Branch: `feat/plan-2b-seedance` (create from `main` before Task 1).
- Tests: `uv run pytest <path> -q --no-cov` while iterating (the coverage gate applies to the full suite); full suite `uv run pytest`.
- Lint and format before every commit: `uv run ruff format src tests && uv run ruff check --fix src tests`.
- Stage files **by name**; never `git add -A` / `git add .`. No attribution / Co-Authored-By lines in commit messages. Never commit `.env`, `runs/`, `models/`, `inputs/`, `faces/`, `.superpowers/`.
- Tests never call paid APIs: use `tests/fakes.py` and `respx`. No `@pytest.mark.live` tests in this plan.
- The viewer stays GET-only and never changes job state.
- User-facing copy is plain English. No em dashes in viewer "Why this step?" text (test enforced).
- Never mention the original project's author or brands.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/character_swap/providers/common.py` | Create | shared by provider clients: retried free calls, exclusive "creating" marker, atomic record save, `on_created` announce |
| `src/character_swap/providers/replicate.py` | Modify | use `common` (behaviour unchanged) |
| `src/character_swap/providers/piapi.py` | Create | the only PiAPI client: temp upload, face assets, paid task create/resume/poll, download |
| `src/character_swap/faces.py` | Create | face library: photo checks, `faces.json`, add/check/renew |
| `src/character_swap/media/upload.py` | Create | `fit_for_upload`: a ≤ 7 MB copy of a video with its audio untouched |
| `src/character_swap/media/video.py` | Modify | `normalize(..., whole_seconds=True)` |
| `src/character_swap/media/audio.py` | Modify | restore can cut the original audio at a length |
| `src/character_swap/providers/generators/base.py` | Modify | `CharacterRef.name`, `GenerationRequest.final_resolution` / `draft_task_id` |
| `src/character_swap/providers/generators/seedance_piapi.py` | Create | `SeedanceGenerator`: estimate, draft/promote/720p bodies, face check, upload, download |
| `src/character_swap/modes.py` | Modify | Seedance modes generate; `WHOLE_SECONDS` |
| `src/character_swap/costs.py` | Modify | Seedance prices |
| `src/character_swap/jobs.py` | Modify | primary/secondary characters and face names in state |
| `src/character_swap/review.py` | Modify | review hashes the job's character images (any generator) |
| `src/character_swap/pipeline.py` | Modify | whole-second normalize/embed/restore, Seedance request (prompt, faces, draft id, resolution), `prompt.txt` |
| `src/character_swap/config.py` | Modify | drop R2 / `hosting_mode` |
| `src/character_swap/cli.py` | Modify | `face` commands, prepare face/character options, `prompt` writes `prompt.txt`, generate `--resolution`, per-generator keys, doctor |
| `src/character_swap/web/view_model.py` | Modify | Seedance Draft/Final sections and cost rows (PiAPI, no Replicate link) |
| `tests/fakes.py` | Modify | `FakePiAPI` |
| `tests/test_common.py`, `tests/test_piapi.py`, `tests/test_faces.py`, `tests/test_upload.py`, `tests/test_seedance.py` | Create | tests for the new modules |
| `tests/test_*.py` (replicate, video, audio, jobs, review, modes, costs, generation, pipeline, cli, config, view_model) | Modify | tests per task |
| `.gitignore`, `.env.example`, `README.md`, `CLAUDE.md`, `activity.md`, `docs/scratchpad.md` | Modify | `faces/`, keys, Seedance usage, journal, checklist (Task 13) |
| `docs/research/2026-10-22-provider-api-facts.md` | Modify | 2026-11-08 PiAPI facts (Task 1) |

---

### Task 1: Record the verified PiAPI facts and the Seedance prices

**Files:** Modify `docs/research/2026-10-22-provider-api-facts.md`, `src/character_swap/costs.py`, `tests/test_costs.py`.

- [ ] **Step 0: Branch**

```bash
git checkout main && git pull --ff-only && git checkout -b feat/plan-2b-seedance
```

- [ ] **Step 1: Research doc.** Under `## PiAPI (Seedance)`, add a subsection `### Re-verified 2026-11-08 (for Plan 2b)` that lists exactly the bullets in this plan's "Verified before writing" block, with these sources:
  - https://piapi.ai/docs/seedance-api/seedance-25.md
  - https://piapi.ai/docs/seedance-api/private-asset.md
  - https://piapi.ai/docs/seedance-api/get-task.md
  - https://piapi.ai/docs/tools/file-upload.md
  - https://piapi.ai/seedance-2-5
  - https://piapi.ai/blogs/seedance-2-5-less-restriction
  - https://piapi.ai/pricing

  Then change the heading `### asset:// on 2.5-less-restriction — CONFLICTING` to `### asset:// on 2.5-less-restriction — RESOLVED 2026-11-08: yes, after the asset is Active (raw real-face URLs are rejected)`.

- [ ] **Step 2: Failing test.** Append to `tests/test_costs.py`:

```python
from character_swap.costs import PRICES, Estimate, cost_line


def test_seedance_prices_are_per_second_and_include_the_input_video():
    seconds = 12
    draft = Estimate(lines=(cost_line("seedance-480", seconds), cost_line("seedance-480-input", seconds)))
    final_1080 = Estimate(
        lines=(cost_line("seedance-1080", seconds), cost_line("seedance-1080-input", seconds))
    )
    final_720 = Estimate(
        lines=(cost_line("seedance-720", seconds), cost_line("seedance-720-input", seconds))
    )
    assert draft.total == pytest.approx(2.97)
    assert final_1080.total == pytest.approx(15.84)
    assert final_720.total == pytest.approx(6.93)
    for key in ("seedance-480", "seedance-720", "seedance-1080"):
        assert PRICES[key].unit == "second" and PRICES[key].checked_on == "2026-11-08"
        assert PRICES[f"{key}-input"].usd == pytest.approx(PRICES[key].usd / 2)
```

(If `pytest` or these names are already imported at the top of the file, don't import them twice.)

- [ ] **Step 3: Run it and watch it fail.** `uv run pytest tests/test_costs.py -q --no-cov`. Expected: `KeyError: 'seedance-480'`.

- [ ] **Step 4: Add the prices.** In `src/character_swap/costs.py`, add these entries at the end of `PRICES` (after `"wan-720"`):

```python
    # PiAPI Seedance 2.5 less-restriction, per second (piapi.ai/pricing and the 2.5 docs,
    # 2026-11-08). The input video is billed at half the output rate; a 1080p promote
    # bills the draft's input video again at half the 1080p rate.
    **{
        key: Price(item, usd, "second", "2026-11-08", "piapi.ai/docs/seedance-api/seedance-25")
        for key, item, usd in (
            ("seedance-480", "Seedance draft (480p)", 0.165),
            ("seedance-480-input", "Seedance input video (480p)", 0.0825),
            ("seedance-720", "Seedance final (720p)", 0.385),
            ("seedance-720-input", "Seedance input video (720p)", 0.1925),
            ("seedance-1080", "Seedance final (1080p)", 0.88),
            ("seedance-1080-input", "Seedance input video (1080p)", 0.44),
        )
    },
```

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_costs.py -q --no-cov`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add docs/research/2026-10-22-provider-api-facts.md src/character_swap/costs.py tests/test_costs.py
git commit -m "docs: re-verified PiAPI Seedance facts; Seedance prices"
```

---

### Task 2: Shared money-safe helpers (`providers/common.py`)

**Files:** Create `src/character_swap/providers/common.py`, `tests/test_common.py`. Modify `src/character_swap/providers/replicate.py`.

The exclusive "creating" marker, the owner-checked release, the atomic record save, the `on_created` announce and the retried free call move into one module, so the PiAPI client (Task 4) uses **the same** code as Replicate. This is a pure move: every existing Replicate test must pass unchanged.

- [ ] **Step 1: Failing tests.** Create `tests/test_common.py`:

```python
import json
import warnings

import httpx
import pytest

from character_swap.providers import common


class Boom(Exception):
    pass


def test_claim_is_exclusive_and_carries_an_owner(tmp_path):
    record = tmp_path / "draft-prediction.json"
    marker = common.claim(record)
    assert marker["status"] == "creating" and len(marker["owner"]) == 32
    assert json.loads(record.read_text()) == marker
    with pytest.raises(FileExistsError):
        common.claim(record)


def test_release_removes_only_our_own_marker(tmp_path):
    record = tmp_path / "r.json"
    marker = common.claim(record)
    common.release(record, marker, nothing_charged="busy", taken_over="other run", error=Boom)
    assert not record.exists()


def test_release_leaves_someone_elses_record_and_stops(tmp_path):
    record = tmp_path / "r.json"
    marker = common.claim(record)
    record.write_text(json.dumps({"id": "theirs", "status": "starting"}))
    with pytest.raises(Boom, match="other run"):
        common.release(record, marker, nothing_charged="busy", taken_over="other run", error=Boom)
    assert json.loads(record.read_text())["id"] == "theirs"


def test_release_of_a_vanished_record_is_quiet(tmp_path):
    common.release(
        tmp_path / "gone.json", {"status": "creating"}, nothing_charged="x", taken_over="y", error=Boom
    )


def test_save_replaces_atomically_and_leaves_no_temp_files(tmp_path):
    record = tmp_path / "r.json"
    common.save(record, {"id": "a", "status": "starting"})
    common.save(record, {"id": "a", "status": "succeeded"})
    assert json.loads(record.read_text())["status"] == "succeeded"
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_announce_never_raises(tmp_path):
    def broken(_data):
        raise RuntimeError("disk full")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        common.announce(broken, {"id": "t1"}, what="paid PiAPI task")
    assert "Could not log paid PiAPI task t1" in str(caught[0].message)
    common.announce(None, {"id": "t1"})  # no callback: nothing happens


def _status_error(code):
    request = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError("e", request=request, response=httpx.Response(code, request=request))


def test_retry_free_retries_5xx_then_succeeds():
    answers = [_status_error(503), "ok"]
    waits = []

    def call():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert common.retry_free(call, sleep=waits.append) == "ok" and waits == [1]


def test_retry_free_never_retries_a_4xx():
    calls = []

    def call():
        calls.append(1)
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        common.retry_free(call, sleep=lambda _s: None)
    assert calls == [1]
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_common.py -q --no-cov`. Expected: `ImportError: cannot import name 'common'`.

- [ ] **Step 3: Create `src/character_swap/providers/common.py`.** The bodies are moved from `replicate.py` (`_retry`, `_claim`, `_release`, `_save`, `_announce`); only the error class and messages become parameters.

```python
"""Shared by every provider client: retried free calls and the money-safe record file.

Every paid request is guarded the same way: an exclusive "creating" marker is written
BEFORE the paid POST, so a crash, a timeout or a second run can never pay twice.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FREE_ATTEMPTS = 3


def retry_free(
    call: Callable[[], Any], *, sleep: Callable[[float], None], attempts: int = FREE_ATTEMPTS
) -> Any:
    """Retry a FREE call on transport errors, 5xx and 429. Raises the last raw httpx error."""
    for attempt in range(attempts):
        delay: float = 2**attempt
        try:
            return call()
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if (code < 500 and code != 429) or attempt == attempts - 1:
                raise
            retry_after = error.response.headers.get("Retry-After", "")
            if code == 429 and retry_after.isdigit():
                delay = min(int(retry_after), 60)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        sleep(delay)
    raise AssertionError("unreachable")


def claim(record: Path) -> dict:
    """Write our "creating" marker exclusively and return it.

    FileExistsError if anyone else has the record. The random owner lets this run
    recognize its own marker later, so it never deletes another run's.
    """
    marker = {
        "status": "creating",
        "created_at": datetime.now(UTC).isoformat(),
        "owner": uuid.uuid4().hex,
    }
    with open(record, "x") as file:  # exclusive: a second runner cannot also pay
        try:
            json.dump(marker, file)
        except BaseException:
            file.close()
            Path(record).unlink(missing_ok=True)  # the POST was never sent
            raise
    return marker


def release(
    record: Path,
    marker: dict,
    *,
    nothing_charged: str,
    taken_over: str,
    error: type[Exception],
) -> None:
    """Delete the record only if it is still exactly our marker.

    Only called when the provider confirmed nothing was created (`nothing_charged` says
    why). If another run replaced the record, it is left alone and `error(taken_over)`
    is raised.
    """
    record = Path(record)
    try:
        current = json.loads(record.read_text())
    except FileNotFoundError:
        return  # already gone; nothing of ours to remove
    except (OSError, ValueError) as exc:
        raise error(taken_over) from exc
    if current != marker:
        raise error(taken_over)
    try:
        record.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise error(
            f"{nothing_charged} But the marker {record} could not be removed ({exc}), "
            "so it is still there. Make sure no other charswap command is running for "
            "this job, then delete it and run again."
        ) from exc


def save(record: Path, data: dict) -> None:
    """Replace `record` atomically (a crash never leaves half a file)."""
    record = Path(record)
    fd, tmp = tempfile.mkstemp(dir=record.parent, prefix=record.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
        os.replace(tmp, record)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def announce(
    callback: Callable[[dict], None] | None, data: dict, *, what: str = "paid prediction"
) -> None:
    """Report a new paid request; a failing logger must never lose track of it."""
    if callback is None:
        return
    try:
        callback(data)
    except Exception as exc:
        warnings.warn(f"Could not log {what} {data.get('id')}: {exc}", stacklevel=3)
```

- [ ] **Step 4: Use it from `replicate.py`.** Keep the method names (tests monkeypatch `ReplicateClient._save`), but make them delegate:
  1. Add `from character_swap.providers import common`.
  2. Replace the body of `_retry` with `return common.retry_free(call, sleep=self._sleep, attempts=attempts)`.
  3. Replace `_save`, `_claim` and `_release` with:

```python
    @staticmethod
    def _save(record: Path, data: dict) -> None:
        common.save(record, data)

    @staticmethod
    def _claim(record: Path) -> dict:
        return common.claim(record)

    @staticmethod
    def _release(record: Path, marker: dict, nothing_charged: str) -> None:
        common.release(
            record,
            marker,
            nothing_charged=nothing_charged,
            taken_over=_taken_over(record),
            error=ReplicateError,
        )
```

  4. Replace the body of the module function `_announce` with `common.announce(callback, prediction, what="paid prediction")`.
  5. Delete any imports nothing uses any more (`uuid`, `datetime`, `tempfile`, `os` if unused; ruff will tell you).

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_common.py tests/test_replicate.py tests/test_wan.py tests/test_generation.py -q --no-cov`. Expected: all PASS, with **no** changes to `tests/test_replicate.py`. If a test checks a warning's `stacklevel`, use `stacklevel=4` in `announce` so the warning still points at the caller.

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/common.py src/character_swap/providers/replicate.py tests/test_common.py
git commit -m "refactor: shared money-safe record helpers for provider clients"
```

---

### Task 3: PiAPI client, free calls (temp upload, face assets, download)

**Files:** Create `src/character_swap/providers/piapi.py`, `tests/test_piapi.py`.

- [ ] **Step 1: Failing tests.** Create `tests/test_piapi.py`:

```python
import base64
import json

import httpx
import pytest
import respx

from character_swap.providers.errors import OutputExpired
from character_swap.providers.piapi import (
    API,
    MAX_UPLOAD_BYTES,
    UPLOAD_URL,
    PiAPIClient,
    PiAPIError,
    task_status,
)


def client(**kw):
    return PiAPIClient("key", poll_seconds=0, sleep=lambda _s: None, **kw)


@pytest.mark.parametrize(
    ("raw", "ours"),
    [
        ("Completed", "succeeded"),
        ("success", "succeeded"),
        ("FAILED", "failed"),
        ("pending", "starting"),
        ("Staged", "starting"),
        (None, "starting"),
        ("processing", "processing"),
        ("retry", "processing"),
        ("something-new", "processing"),
    ],
)
def test_task_status_maps_every_casing(raw, ours):
    assert task_status(raw) == ours


@respx.mock
def test_upload_temp_sends_base64_with_the_key_and_returns_the_link(tmp_path):
    clip = tmp_path / "prepared.mp4"
    clip.write_bytes(b"video-bytes")
    route = respx.post(UPLOAD_URL).respond(
        200, json={"code": 200, "data": {"url": "https://img.theapi.app/temp/a.mp4"}}
    )
    assert client().upload_temp(clip) == "https://img.theapi.app/temp/a.mp4"
    sent = json.loads(route.calls[0].request.content)
    assert route.calls[0].request.headers["x-api-key"] == "key"
    assert sent == {"file_name": "prepared.mp4", "file_data": base64.b64encode(b"video-bytes").decode()}


def test_upload_temp_refuses_mov_and_big_files_before_sending(tmp_path):
    mov = tmp_path / "clip.mov"
    mov.write_bytes(b"x")
    with pytest.raises(PiAPIError, match=r"\.mov"):
        client().upload_temp(mov)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(PiAPIError, match="under 7 MB"):
        client().upload_temp(big)


@respx.mock
def test_upload_temp_403_explains_the_plan(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(403, json={"code": 403, "message": "plan"})
    with pytest.raises(PiAPIError, match="Creator plan"):
        client().upload_temp(photo)


@respx.mock
def test_upload_temp_envelope_error_is_reported(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(200, json={"code": 400, "message": "bad file"})
    with pytest.raises(PiAPIError, match="bad file"):
        client().upload_temp(photo)


@respx.mock
def test_create_and_get_asset():
    respx.post(f"{API}/asset/upload").respond(
        202, json={"code": 200, "data": {"asset_id": "as_1", "status": "Processing"}}
    )
    respx.get(f"{API}/asset/as_1").respond(
        200, json={"code": 200, "data": {"asset_id": "as_1", "status": "Active"}}
    )
    assert client().create_asset("https://x/me.jpg", "frank")["asset_id"] == "as_1"
    assert client().get_asset("as_1")["status"] == "Active"


@respx.mock
def test_get_asset_returns_none_when_purged():
    respx.get(f"{API}/asset/as_1").respond(404, json={"code": 404, "message": "not found"})
    assert client().get_asset("as_1") is None


def test_get_asset_refuses_odd_ids():
    with pytest.raises(PiAPIError, match="not a PiAPI face id"):
        client().get_asset("../task")


@respx.mock
def test_free_calls_retry_5xx_then_give_a_plain_error(tmp_path):
    route = respx.get(f"{API}/asset/as_1").respond(503)
    with pytest.raises(PiAPIError, match="503"):
        client().get_asset("as_1")
    assert route.call_count == 3


@respx.mock
def test_401_names_the_key():
    respx.get(f"{API}/asset/as_1").respond(401)
    with pytest.raises(PiAPIError, match="PIAPI_API_KEY"):
        client().get_asset("as_1")


@respx.mock
def test_download_has_no_key_and_expiry_is_typed(tmp_path):
    route = respx.get("https://cdn.piapi.ai/out.mp4").respond(200, content=b"mp4")
    dest = client().download("https://cdn.piapi.ai/out.mp4", tmp_path / "raw.mp4")
    assert dest.read_bytes() == b"mp4" and "x-api-key" not in route.calls[0].request.headers
    respx.get("https://cdn.piapi.ai/old.mp4").respond(404)
    with pytest.raises(OutputExpired, match="24 hours"):
        client().download("https://cdn.piapi.ai/old.mp4", tmp_path / "old.mp4")
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_piapi.py -q --no-cov`. Expected: `ModuleNotFoundError: character_swap.providers.piapi`.

- [ ] **Step 3: Create `src/character_swap/providers/piapi.py`** with the free part. Task 4 adds the paid part to the same file.

```python
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
```

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_piapi.py -q --no-cov`. Expected: PASS. If a match fails only on message wording, fix the code's message, not the test. The tests hold the user-facing words.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/piapi.py tests/test_piapi.py
git commit -m "feat: PiAPI client free calls (temporary upload, face assets, download)"
```

---

### Task 4: PiAPI paid task: create once, save, wait, resume

**Files:** Modify `src/character_swap/providers/piapi.py`, `tests/test_piapi.py`.

Same money rules as `ReplicateClient.run()`:
- exclusive marker before the POST;
- a 4xx means nothing was created, so the marker is released;
- a 5xx, timeout or unreadable answer keeps the marker (it may have been charged);
- the task is saved before waiting;
- a saved task is resumed, never posted again;
- `on_created` runs once for a new task.

The record format matches what `pipeline` already reads: `{"id", "status": starting|processing|succeeded|failed, "task": <PiAPI data>}`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_piapi.py`:

```python
BODY = {"model": "seedance", "task_type": "seedance-2.5-less-restriction", "input": {"prompt": "p"}}
VIDEO = "https://cdn.piapi.ai/out.mp4"


def task_json(status, task_id="t1", **extra):
    return {"code": 200, "data": {"task_id": task_id, "status": status, **extra}, "message": "ok"}


@respx.mock
def test_run_creates_once_saves_before_waiting_and_returns_the_video(tmp_path):
    record = tmp_path / "draft-prediction.json"
    create = respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    seen_while_waiting = []

    def poll(request):
        seen_while_waiting.append(json.loads(record.read_text())["id"])
        return httpx.Response(200, json=task_json("Completed", output={"video": VIDEO}))

    respx.get(f"{API}/task/t1").mock(side_effect=poll)
    created = []
    assert client().run(body=BODY, record=record, on_created=created.append) == VIDEO
    assert create.call_count == 1 and json.loads(create.calls[0].request.content) == BODY
    assert seen_while_waiting == ["t1"]  # the id was on disk before the first poll
    assert created == [{"id": "t1", "status": "starting", "task": {"task_id": "t1", "status": "pending"}}]
    saved = json.loads(record.read_text())
    assert saved["status"] == "succeeded" and saved["task"]["output"]["video"] == VIDEO


@respx.mock
def test_saved_task_is_resumed_never_posted_again(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "t1", "status": "processing", "task": {}}))
    create = respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(200, json=task_json("success", output={"video_url": VIDEO}))
    assert client().run(body={}, record=record) == VIDEO
    assert create.call_count == 0


@respx.mock
def test_finished_record_returns_without_any_call(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(
        json.dumps({"id": "t1", "status": "succeeded", "task": {"output": {"video": VIDEO}}})
    )
    assert client().run(body={}, record=record) == VIDEO
    assert respx.calls.call_count == 0


@respx.mock
def test_unconfirmed_marker_refuses_without_posting(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "owner": "x"}))
    create = respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    with pytest.raises(PiAPIError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert create.call_count == 0


@pytest.mark.parametrize(
    ("code", "words"),
    [(400, "nothing was charged"), (402, "credit"), (422, "nothing was charged"), (429, "busy")],
)
@respx.mock
def test_4xx_releases_the_marker_and_says_nothing_was_charged(tmp_path, code, words):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").respond(code, json={"code": code, "message": "no"})
    with pytest.raises(PiAPIError, match=words):
        client().run(body=BODY, record=record)
    assert not record.exists()


@respx.mock
def test_429_is_not_retried(tmp_path):
    create = respx.post(f"{API}/task").respond(429)
    with pytest.raises(PiAPIError):
        client().run(body=BODY, record=tmp_path / "r.json")
    assert create.call_count == 1


@pytest.mark.parametrize("answer", [httpx.Response(500), httpx.Response(200, text="not json")])
@respx.mock
def test_unclear_answers_keep_the_marker(tmp_path, answer):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").mock(return_value=answer)
    with pytest.raises(PiAPIError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "creating"


@respx.mock
def test_timeout_keeps_the_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PiAPIError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert record.exists()


@respx.mock
def test_envelope_error_without_a_task_releases_the_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").respond(200, json={"code": 400, "message": "bad placeholder", "data": {}})
    with pytest.raises(PiAPIError, match="bad placeholder"):
        client().run(body=BODY, record=record)
    assert not record.exists()


def test_empty_body_is_refused_before_anything_is_sent(tmp_path):
    with pytest.raises(PiAPIError, match="no input"):
        client().run(body={}, record=tmp_path / "r.json")
    assert not (tmp_path / "r.json").exists()


@respx.mock
def test_failed_task_says_refunded_and_how_to_retry(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(
        200, json=task_json("failed", error={"code": 1, "message": "content violation"})
    )
    with pytest.raises(PiAPIError, match=r"content violation.*refunds.*delete .*r\.json"):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "failed"  # kept: never re-bought silently
    with pytest.raises(PiAPIError, match="failed"):
        client().run(body=BODY, record=record)  # a second run does not post again


@respx.mock
def test_finished_without_a_video_link_is_an_error(tmp_path):
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(200, json=task_json("completed", output={}))
    with pytest.raises(PiAPIError, match="without a video link"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_still_running_after_max_polls_is_resumable(tmp_path):
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(200, json=task_json("processing"))
    with pytest.raises(PiAPIError, match="still running.*no new charge"):
        client(max_polls=2).run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_lost_connection_while_waiting_is_resumable(tmp_path):
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(PiAPIError, match="no new charge"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_record_save_failure_still_reports_the_paid_task(tmp_path, monkeypatch):
    from character_swap.providers import common

    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    monkeypatch.setattr(common, "save", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    created = []
    with pytest.raises(PiAPIError, match="t1"):
        client().run(body=BODY, record=tmp_path / "r.json", on_created=created.append)
    assert [c["id"] for c in created] == ["t1"]


@respx.mock
def test_polling_backs_off_to_the_ceiling(tmp_path):
    waits = []
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    answers = [task_json("processing")] * 4 + [task_json("completed", output={"video": VIDEO})]
    respx.get(f"{API}/task/t1").mock(side_effect=[httpx.Response(200, json=a) for a in answers])
    PiAPIClient("key", poll_seconds=10, max_poll_seconds=20, sleep=waits.append).run(
        body=BODY, record=tmp_path / "r.json"
    )
    assert waits == [10, 15, 20, 20, 20]
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_piapi.py -q --no-cov`. Expected: `AttributeError: 'PiAPIClient' object has no attribute 'run'`.

- [ ] **Step 3: Add the paid part** to `PiAPIClient` (after `download`), plus the module-level message helpers at the end of the file:

```python
    # ---- paid call (never retried) -------------------------------------------
    def run(
        self,
        *,
        body: dict,
        record: Path,
        on_created: Callable[[dict], None] | None = None,
    ) -> str:
        """Create a task once, save it to `record`, wait, and return the video link.

        A marker is created exclusively BEFORE the paid POST, so a crash or a timeout can
        never lead to a second charge. If `record` exists, it is resumed instead.
        `on_created(task)` runs once, right after PiAPI confirms a NEW task.
        """
        record = Path(record)
        task = None
        if record.exists():
            try:
                task = self._load(record)
            except FileNotFoundError:
                task = None  # removed since the check; the exclusive claim still guards
        if task is None:
            task = self._create(body, record, on_created)
        elif task["status"] == "creating":
            raise PiAPIError(unconfirmed_message(record))
        elif task["status"] == "failed":
            raise PiAPIError(failed_message(task, record))
        return self._output(self._wait(task, record), record)

    @staticmethod
    def _load(record: Path) -> dict:
        damaged = PiAPIError(
            f"{record} is damaged. The task may still exist: check your task list at "
            f"{WORKSPACE} before deleting it (deleting and running again costs money)."
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

    def _create(
        self, body: dict, record: Path, on_created: Callable[[dict], None] | None
    ) -> dict:
        if not body:
            # Resumes pass {} because the record exists; if it vanished since, posting {}
            # would buy a task with no input. Nothing sent, no marker written.
            raise PiAPIError(
                f"Refusing to create a paid task with no input; {record} vanished during "
                "resume. Run the command again."
            )
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker = common.claim(record)
        except FileExistsError as error:
            raise PiAPIError(unconfirmed_message(record)) from error
        try:
            response = self._http.post(f"{API}/task", headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise PiAPIError(unconfirmed_message(record)) from error
        code = response.status_code
        if 400 <= code < 500 and code != 408:
            rejection = _rejection(code, response.text)
            self._release(record, marker, rejection)
            raise PiAPIError(rejection)
        if response.is_error:
            raise PiAPIError(unconfirmed_message(record))
        try:
            answer = response.json()
        except ValueError as error:
            raise PiAPIError(unconfirmed_message(record)) from error
        data = _data(answer)
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            problem = _envelope_error(answer)
            if problem is not None:  # PiAPI said no and gave no task: nothing exists
                rejection = f"PiAPI rejected the request (nothing was charged): {problem[:300]}"
                self._release(record, marker, rejection)
                raise PiAPIError(rejection)
            raise PiAPIError(unconfirmed_message(record))
        task = {"id": task_id, "status": task_status(data.get("status")), "task": data}
        try:
            common.save(record, task)
        except OSError as error:
            common.announce(on_created, task, what="paid PiAPI task")  # paid all the same
            raise PiAPIError(
                f"Paid PiAPI task {task_id} was created but its record could not be saved "
                f"({error}). Note this id; check {WORKSPACE}."
            ) from error
        common.announce(on_created, task, what="paid PiAPI task")
        return task

    @staticmethod
    def _release(record: Path, marker: dict, nothing_charged: str) -> None:
        common.release(
            record,
            marker,
            nothing_charged=nothing_charged,
            taken_over=_taken_over(record),
            error=PiAPIError,
        )

    def _wait(self, task: dict, record: Path) -> dict:
        polls, delay = 0, self._poll_seconds
        while task["status"] not in ("succeeded", "failed"):
            if polls >= self._max_polls:
                raise PiAPIError(
                    f"PiAPI task {task['id']} is still running (saved in {record}). PiAPI "
                    "queues can take hours at busy times. Run the same command later to keep "
                    "waiting; no new charge."
                )
            self._sleep(delay)
            delay = min(delay * POLL_GROWTH, self._max_poll_seconds)
            # Built from the saved id: the key never goes to a URL taken from a response.
            url = f"{API}/task/{task['id']}"
            try:
                answer = common.retry_free(lambda: self._send("GET", url), sleep=self._sleep)  # noqa: B023
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if code == 401:
                    raise PiAPIError(KEY_MESSAGE) from error
                if code == 404:
                    raise PiAPIError(
                        f"PiAPI no longer recognizes task {task['id']}; check {WORKSPACE}."
                    ) from error
                raise self._lost(task, record) from error
            except (httpx.TransportError, PiAPIError) as error:
                raise self._lost(task, record) from error
            data = _data(answer)
            if data.get("task_id") not in (None, task["id"]):
                raise self._lost(task, record)
            task = {"id": task["id"], "status": task_status(data.get("status")), "task": data}
            common.save(record, task)
            polls += 1
        if task["status"] == "failed":
            raise PiAPIError(failed_message(task, record))
        return task

    @staticmethod
    def _output(task: dict, record: Path) -> str:
        output = (task.get("task") or {}).get("output")
        url = (output.get("video") or output.get("video_url")) if isinstance(output, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise PiAPIError(
                f"PiAPI task {task['id']} finished without a video link. Check {WORKSPACE} "
                f"before deleting {record} (a new run costs money)."
            )
        return url

    @staticmethod
    def _lost(task: dict, record: Path) -> PiAPIError:
        return PiAPIError(
            f"Lost connection while waiting; PiAPI task {task['id']} is saved in {record}. "
            "Run the same command again to keep waiting; no new charge."
        )


def unconfirmed_message(record: Path) -> str:
    """Guidance when a record holds only our "creating" marker (POST never confirmed)."""
    return (
        "The request was sent to PiAPI but not confirmed. It may have been charged, or "
        "another charswap command for this job is still sending it. First make sure no "
        f"other charswap command is running for this job. Then check your task list at "
        f"{WORKSPACE}; if nothing is there, delete {record} and run again."
    )


def failed_message(task: dict, record: Path) -> str:
    error = (task.get("task") or {}).get("error") or {}
    reason = (error.get("message") or error.get("raw_message")) if isinstance(error, dict) else None
    return (
        f"PiAPI task {task.get('id')} failed: {reason or 'no reason given'}. PiAPI refunds "
        "tasks it rejects for content. To try again, delete "
        f"{record} and run the same command (it shows the cost and asks first)."
    )


def _taken_over(record: Path) -> str:
    return (
        f"Another charswap run took over {record} while this one was talking to PiAPI; "
        "this run sent nothing more and was not charged for it. Let the other run finish, "
        "or run the command again to resume what it saved."
    )


def _rejection(code: int, body: str) -> str:
    if code == 401:
        return KEY_MESSAGE
    if code == 402:
        return "Add credit to your PiAPI account (nothing was charged)."
    if code == 403:
        return f"PiAPI refused the request (403; nothing was charged): {body[:300]}. {PLAN_MESSAGE}"
    if code == 429:
        return "PiAPI is busy (429). Nothing was charged; wait a minute and run the same command again."
    return f"PiAPI rejected the request ({code}); nothing was charged: {body[:300]}"
```

Add `import json` to the imports at the top.

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_piapi.py tests/test_common.py -q --no-cov`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/piapi.py tests/test_piapi.py
git commit -m "feat: PiAPI paid tasks are created once, saved before waiting and resumed"
```

---

### Task 5: Face library and `charswap face add | list | check | renew`

**Files:** Create `src/character_swap/faces.py`, `tests/test_faces.py`. Modify `tests/fakes.py`, `src/character_swap/cli.py`, `tests/test_cli.py`, `.gitignore`.

A face is a photo that PiAPI checks once and keeps in the user's private library. Locally:
- `faces/faces.json` holds one row per face: name, image file, PiAPI asset id, status, created, error.
- `faces/<name>.jpg` is an upright JPEG copy of the photo, checked against PiAPI's limits.

Every call here is **free**.

- [ ] **Step 1: Add `FakePiAPI`** to `tests/fakes.py`. It is used by this task and Task 9.

```python
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
```

- [ ] **Step 2: Failing tests.** Create `tests/test_faces.py`:

```python
import json

import pytest
from PIL import Image

from character_swap.faces import ACTIVE, FAILED, GONE, PROCESSING, FaceError, FaceLibrary, check_photo
from fakes import FakePiAPI


def photo(path, size=(828, 1086), mode="RGB", fmt="JPEG"):
    Image.new(mode, size, (200, 150, 120)).save(path, fmt)
    return path


def library(tmp_path, **kw):
    return FaceLibrary(tmp_path, sleep=lambda _s: None, **kw)


def test_check_photo_saves_an_upright_jpeg(tmp_path):
    out = check_photo(photo(tmp_path / "me.png", fmt="PNG", mode="RGBA"), tmp_path / "out.jpg")
    with Image.open(out) as image:
        assert image.format == "JPEG" and image.mode == "RGB" and image.size == (828, 1086)


@pytest.mark.parametrize(
    ("size", "words"),
    [((156, 226), "at least 300 px"), ((3000, 600), "too wide or too narrow")],
)
def test_check_photo_rejects_what_piapi_would(tmp_path, size, words):
    with pytest.raises(FaceError, match=words):
        check_photo(photo(tmp_path / "me.jpg", size=size), tmp_path / "out.jpg")


def test_check_photo_scales_down_huge_photos(tmp_path):
    out = check_photo(photo(tmp_path / "big.jpg", size=(4000, 5000)), tmp_path / "out.jpg")
    with Image.open(out) as image:
        assert max(image.size) == 3000


def test_check_photo_explains_unreadable_files(tmp_path):
    heic = tmp_path / "IMG_1.heic"
    heic.write_bytes(b"not an image")
    with pytest.raises(FaceError, match="Export it as JPEG"):
        check_photo(heic, tmp_path / "out.jpg")


def test_add_uploads_saves_the_id_first_and_waits_for_active(tmp_path):
    piapi = FakePiAPI()
    face = library(tmp_path).add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert face.status == ACTIVE and face.asset_id == "asset-1"
    assert [p.name for p in piapi.uploads] == ["frank.jpg"]
    rows = json.loads((tmp_path / "faces" / "faces.json").read_text())
    assert rows[0]["name"] == "frank" and rows[0]["asset_id"] == "asset-1"
    assert (tmp_path / "faces" / "frank.jpg").exists()


def test_add_refuses_a_taken_name_and_bad_names(tmp_path):
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), FakePiAPI())
    with pytest.raises(FaceError, match="already saved"):
        lib.add("frank", photo(tmp_path / "me2.jpg"), FakePiAPI())
    with pytest.raises(FaceError, match="lowercase"):
        lib.add("Frank Jr!", photo(tmp_path / "me3.jpg"), FakePiAPI())


def test_add_stops_waiting_and_keeps_processing(tmp_path):
    piapi = FakePiAPI(assets={"asset-1": "Processing"})
    face = library(tmp_path, polls=2).add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert face.status == PROCESSING and len(piapi.asset_checks) == 2


def test_check_refreshes_and_records_a_purged_face(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    piapi.assets.clear()  # PiAPI purged it after idle days
    assert lib.check("frank", piapi).status == GONE
    assert lib.get("frank").status == GONE


def test_require_active_explains_each_state(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert lib.require_active("frank", piapi).status == ACTIVE
    piapi.assets["asset-1"] = "Failed"
    with pytest.raises(FaceError, match="rejected"):
        lib.require_active("frank", piapi)
    piapi.assets.clear()
    with pytest.raises(FaceError, match="charswap face renew frank"):
        lib.require_active("frank", piapi)


def test_renew_reuploads_the_same_saved_photo(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    piapi.assets.clear()
    renewed = lib.renew("frank", piapi)
    assert renewed.status == ACTIVE and renewed.asset_id != "" and len(lib.all()) == 1
    assert [p.name for p in piapi.uploads] == ["frank.jpg", "frank.jpg"]


def test_unknown_and_damaged_library(tmp_path):
    lib = library(tmp_path)
    assert lib.all() == []
    with pytest.raises(FaceError, match="charswap face add"):
        lib.get("nobody")
    (tmp_path / "faces").mkdir()
    (tmp_path / "faces" / "faces.json").write_text("{nope")
    with pytest.raises(FaceError, match="damaged"):
        lib.all()


def test_failed_status_keeps_piapis_reason(tmp_path):
    class Rejecting(FakePiAPI):
        def get_asset(self, asset_id):
            return {"asset_id": asset_id, "status": "Failed", "error": {"message": "no face found"}}

    face = library(tmp_path).add("frank", photo(tmp_path / "me.jpg"), Rejecting())
    assert face.status == FAILED and face.error == "no face found"
```

- [ ] **Step 3: Run them and watch them fail.** `uv run pytest tests/test_faces.py -q --no-cov`. Expected: `ModuleNotFoundError: character_swap.faces`.

- [ ] **Step 4: Create `src/character_swap/faces.py`:**

```python
"""Saved faces: a photo PiAPI checks once and keeps in your private library, reused by name."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from character_swap.errors import CharSwapError

FOLDER = "faces"  # at the project root; git-ignored
CONSENT = (
    "Only add a face you have the right to use: your own, or someone who agreed to it. "
    "PiAPI checks the photo and keeps it in your private face library."
)
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
MIN_SIDE = 300  # PiAPI: 300-6000 px per side, aspect 0.4-2.5, under 30 MB
MIN_RATIO, MAX_RATIO = 0.4, 2.5
FIT_SIDE = 3000  # bigger photos are scaled to this longest side (keeps uploads small)
MAX_BYTES = 7_000_000  # PiAPI's temporary upload (providers/piapi.py)
POLLS, POLL_SECONDS = 60, 5.0  # wait up to ~5 minutes for PiAPI's face check
ACTIVE, PROCESSING, FAILED, GONE = "Active", "Processing", "Failed", "Gone"
_STATUS = {"active": ACTIVE, "processing": PROCESSING, "failed": FAILED}


class FaceError(CharSwapError, ValueError):
    """A face cannot be saved or used as it is; the message says what to do."""


class AssetsLike(Protocol):
    def upload_temp(self, path: Path) -> str: ...

    def create_asset(self, url: str, name: str) -> dict: ...

    def get_asset(self, asset_id: str) -> dict | None: ...


@dataclass(frozen=True)
class Face:
    name: str
    image: str  # file name inside faces/
    asset_id: str
    status: str  # Active | Processing | Failed | Gone
    created_at: str
    error: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_photo(photo: Path, dest: Path) -> Path:
    """Check a photo against PiAPI's limits and save it as an upright RGB JPEG at `dest`."""
    photo = Path(photo)
    try:
        with Image.open(photo) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except FileNotFoundError:
        raise FaceError(f"Photo not found: {photo}") from None
    except (UnidentifiedImageError, OSError) as error:
        raise FaceError(
            f"{photo.name} isn't a photo this tool can read. Export it as JPEG or PNG "
            "(iPhone photos: select it in Photos, then File > Export)."
        ) from error
    width, height = image.size
    if not MIN_RATIO <= width / height <= MAX_RATIO:
        raise FaceError(
            f"{photo.name} is {width}x{height} px: too wide or too narrow for PiAPI. Crop it "
            "closer to a portrait or square shape."
        )
    if min(width, height) < MIN_SIDE:
        raise FaceError(
            f"{photo.name} is {width}x{height} px; PiAPI needs at least {MIN_SIDE} px on each "
            "side. Use the original photo, not a screenshot."
        )
    if max(width, height) > FIT_SIDE:
        image.thumbnail((FIT_SIDE, FIT_SIDE), Image.Resampling.LANCZOS)
    rgb = image.convert("RGB")
    for quality in (92, 80):
        rgb.save(dest, "JPEG", quality=quality)
        if Path(dest).stat().st_size <= MAX_BYTES:
            return Path(dest)
    Path(dest).unlink(missing_ok=True)
    raise FaceError(f"{photo.name} is too large to upload even as a JPEG; use a smaller photo.")


class FaceLibrary:
    def __init__(
        self,
        root: Path,
        *,
        sleep: Callable[[float], None] = time.sleep,
        polls: int = POLLS,
        poll_seconds: float = POLL_SECONDS,
    ):
        self.folder = Path(root) / FOLDER
        self.index = self.folder / "faces.json"
        self._sleep = sleep
        self._polls = polls
        self._poll_seconds = poll_seconds

    # ---- reading (local, free) -------------------------------------------------
    def all(self) -> list[Face]:
        if not self.index.exists():
            return []
        try:
            rows = json.loads(self.index.read_text(encoding="utf-8"))
            return [Face(**row) for row in rows]
        except (OSError, ValueError, TypeError) as error:
            raise FaceError(
                f"{self.index} is damaged. Fix or delete it (your faces stay in your PiAPI "
                "library; add them again with charswap face add)."
            ) from error

    def get(self, name: str) -> Face:
        for face in self.all():
            if face.name == name:
                return face
        raise FaceError(f"No saved face {name!r}. Add it first: charswap face add {name} photo.jpg")

    def image_path(self, face: Face) -> Path:
        return self.folder / face.image

    # ---- PiAPI (free calls) --------------------------------------------------------
    def add(self, name: str, photo: Path, piapi: AssetsLike) -> Face:
        if not NAME.fullmatch(name):
            raise FaceError(
                "Face names are 1-40 lowercase letters, digits, - or _ (for example: frank)."
            )
        if any(face.name == name for face in self.all()):
            raise FaceError(
                f"A face called {name!r} is already saved. Pick another name, or run "
                f"charswap face renew {name} if PiAPI removed it."
            )
        self.folder.mkdir(parents=True, exist_ok=True)
        image = check_photo(Path(photo), self.folder / f"{name}.jpg")
        return self._upload(name, image.name, piapi)

    def renew(self, name: str, piapi: AssetsLike) -> Face:
        """Upload the same saved photo again (after PiAPI purged an unused face)."""
        face = self.get(name)
        if not self.image_path(face).exists():
            raise FaceError(
                f"The saved photo {self.image_path(face)} is missing; add the face again "
                "under a new name."
            )
        return self._upload(name, face.image, piapi)

    def check(self, name: str, piapi: AssetsLike) -> Face:
        """Ask PiAPI for the face's status (this also keeps it from being purged)."""
        return self._refresh(self.get(name), piapi)

    def require_active(self, name: str, piapi: AssetsLike) -> Face:
        face = self.check(name, piapi)
        if face.status == ACTIVE:
            return face
        if face.status == PROCESSING:
            raise FaceError(
                f"PiAPI is still checking face {name!r}. Run charswap face check {name} in a "
                "minute."
            )
        if face.status == FAILED:
            raise FaceError(
                f"PiAPI rejected face {name!r}: {face.error or 'no reason given'}. Try a "
                "clearer, front-facing photo under a new name: charswap face add NAME photo.jpg"
            )
        raise FaceError(
            f"PiAPI removed face {name!r} after it went unused. Run charswap face renew "
            f"{name} (free), then try again."
        )

    # ---- internals ----------------------------------------------------------------
    def _upload(self, name: str, image: str, piapi: AssetsLike) -> Face:
        url = piapi.upload_temp(self.folder / image)
        asset = piapi.create_asset(url, name)
        face = Face(
            name=name,
            image=image,
            asset_id=str(asset["asset_id"]),
            status=_status(asset.get("status")),
            created_at=_now(),
        )
        self._put(face)  # saved before waiting: the id is never lost
        for attempt in range(self._polls):
            if face.status in (ACTIVE, FAILED):
                break
            if attempt:  # the first check is immediate; later ones wait in between
                self._sleep(self._poll_seconds)
            face = self._refresh(face, piapi)
        return face

    def _refresh(self, face: Face, piapi: AssetsLike) -> Face:
        record = piapi.get_asset(face.asset_id)
        if record is None:
            updated = replace(face, status=GONE, error="")
        else:
            error = record.get("error")
            message = error.get("message", "") if isinstance(error, dict) else str(error or "")
            updated = replace(face, status=_status(record.get("status")), error=message)
        self._put(updated)
        return updated

    def _put(self, face: Face) -> None:
        faces = [other for other in self.all() if other.name != face.name] + [face]
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump([asdict(item) for item in faces], file, indent=2)
            os.replace(tmp, self.index)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _status(raw: object) -> str:
    return _STATUS.get(str(raw or "").strip().lower(), PROCESSING)
```

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_faces.py -q --no-cov`. Expected: PASS.

- [ ] **Step 6: Failing CLI tests.** Append to `tests/test_cli.py`:

```python
from PIL import Image

from character_swap.faces import FaceLibrary
from fakes import FakePiAPI


def _photo(path):
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(path, "JPEG")
    return path


def test_face_add_shows_consent_asks_and_saves(settings, tmp_path):
    asked = []
    code, out = run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda q: asked.append(q) or True,
        piapi_factory=lambda s: FakePiAPI(),
    )
    assert code == 0 and asked and "right to use" in out.text
    assert "charswap prepare clip.mp4 --face frank" in out.text
    assert FaceLibrary(settings.root).get("frank").asset_id == "asset-1"


def test_face_add_declined_uploads_nothing(settings, tmp_path):
    piapi = FakePiAPI()
    code, out = run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda _q: False,
        piapi_factory=lambda s: piapi,
    )
    assert code == 1 and piapi.uploads == [] and "Nothing was uploaded" in out.err.text


def test_face_list_and_check(settings, tmp_path):
    piapi = FakePiAPI()
    run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg")), "--yes"],
        settings,
        piapi_factory=lambda s: piapi,
    )
    code, out = run(["face", "list"], settings)
    assert code == 0 and "frank" in out.text and "Active" in out.text
    piapi.assets.clear()
    code, out = run(["face", "check", "frank"], settings, piapi_factory=lambda s: piapi)
    assert code == 2 and "charswap face renew frank" in out.text


def test_face_list_when_empty(settings):
    code, out = run(["face", "list"], settings)
    assert code == 0 and "No saved faces yet" in out.text


def test_face_commands_need_the_piapi_key(settings, tmp_path):
    code, out = run(["face", "add", "frank", str(_photo(tmp_path / "me.jpg")), "--yes"], settings)
    assert code == 2 and "PIAPI_API_KEY" in out.err.text
```

- [ ] **Step 7: Run them and watch them fail.** `uv run pytest tests/test_cli.py -q --no-cov -k face`. Expected: argparse error `invalid choice: 'face'`.

- [ ] **Step 8: Wire the CLI.** In `src/character_swap/cli.py`:
  1. Add the imports: `from character_swap.faces import ACTIVE, CONSENT, PROCESSING, FaceLibrary`.
  2. Add `build_piapi` **above** the `Env` class (Env uses it as a default):

```python
def build_piapi(settings: Settings):
    from character_swap.providers.piapi import PiAPIClient

    return PiAPIClient(settings.require("PIAPI_API_KEY"))
```

  3. `Env` gets a field `piapi_factory: Callable[[Settings], object] = build_piapi`. `main()` gets the keyword `piapi_factory: Callable[[Settings], object] | None = None` and passes `piapi_factory=piapi_factory or build_piapi` when building `Env`.
  4. In `_parser()`, after `doctor`:

```python
    face = sub.add_parser("face", help="save and check faces in your PiAPI face library (free)")
    face_sub = face.add_subparsers(dest="face_command", required=True)
    add = face_sub.add_parser("add", help="save a photo as a face")
    add.add_argument("name", help="short name, e.g. frank")
    add.add_argument("photo", type=Path)
    add.add_argument("--yes", action="store_true", help="confirm you have the right to use it")
    face_sub.add_parser("list", help="show saved faces")
    face_sub.add_parser("check", help="ask PiAPI for a face's status").add_argument("name")
    face_sub.add_parser(
        "renew", help="upload the same saved photo again after PiAPI removed it"
    ).add_argument("name")
```

  5. The handler, registered as `"face": _face` in `COMMANDS`:

```python
def _face_line(face, library: FaceLibrary) -> str:
    if face.status == ACTIVE:
        return (
            f"Face {face.name} is ready (PiAPI id {face.asset_id}). Use it with: "
            f"charswap prepare clip.mp4 --face {face.name}"
        )
    if face.status == PROCESSING:
        return f"PiAPI is still checking {face.name}. Run: charswap face check {face.name}"
    if face.status == "Failed":
        return f"PiAPI rejected {face.name}: {face.error or 'no reason given'}."
    return f"PiAPI removed {face.name} after it went unused. Run: charswap face renew {face.name}"


def _face(args, settings, env) -> int:
    library = FaceLibrary(settings.root)
    if args.face_command == "list":
        faces = library.all()
        if not faces:
            env.out("No saved faces yet. Add one: charswap face add NAME photo.jpg")
        for face in faces:
            env.out(f"{face.name:<16} {face.status:<11} {face.asset_id}  {library.image_path(face)}")
        return 0
    if args.face_command == "add":
        env.out(CONSENT)
        if not args.yes and not env.confirm("Do you have the right to use this face?"):
            env.err("Cancelled. Nothing was uploaded.")
            return 1
        piapi = env.piapi_factory(settings)
        env.out("Uploading, then waiting for PiAPI's face check (free, up to ~5 minutes)...")
        face = library.add(args.name, args.photo, piapi)
    elif args.face_command == "renew":
        face = library.renew(args.name, env.piapi_factory(settings))
    else:
        face = library.check(args.name, env.piapi_factory(settings))
    env.out(_face_line(face, library))
    return 0 if face.status in (ACTIVE, PROCESSING) else 2
```

  6. Add `charswap face add frank me.jpg` as the first line of `EPILOG`.

- [ ] **Step 9: Git-ignore the library.** Append to `.gitignore`:

```
# saved faces (personal photos + PiAPI ids, never committed)
faces/
```

- [ ] **Step 10: Run tests.** `uv run pytest tests/test_faces.py tests/test_cli.py -q --no-cov`. Expected: PASS.

- [ ] **Step 11: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/faces.py src/character_swap/cli.py tests/fakes.py tests/test_faces.py tests/test_cli.py .gitignore
git commit -m "feat: face library with charswap face add/list/check/renew (free)"
```

---

### Task 6: Characters are chosen at `prepare` (primary and secondary) and reviewed

**Files:** Modify `src/character_swap/jobs.py`, `src/character_swap/review.py`, `src/character_swap/cli.py`, `tests/test_jobs.py`, `tests/test_review.py`, `tests/test_cli.py`.

Job state gains up to four keys:
- `character_primary` (exists today) and `character_secondary`: image files copied into the job.
- `face_primary` and `face_secondary`: the saved **face name**, when the image came from the face library.

The review fingerprint covers every character image the job has, for any generator. For Wan nothing changes: its image was already reviewed.

- [ ] **Step 1: Failing tests.** Append to `tests/test_jobs.py`:

```python
def test_seedance_job_keeps_two_characters_and_face_names(runs_dir, video, tmp_path):
    me, friend = tmp_path / "me.jpg", tmp_path / "friend.png"
    me.write_bytes(b"me")
    friend.write_bytes(b"friend")
    job = Job.create(
        runs_dir,
        video,
        generator="seedance",
        mode="depth",
        character=me,
        character2=friend,
        faces={"primary": "frank"},
    )
    state = job.state
    assert state["character_primary"] == "character-primary.jpg"
    assert state["character_secondary"] == "character-secondary.png"
    assert state["face_primary"] == "frank" and "face_secondary" not in state
    assert job.path("character-secondary.png").read_bytes() == b"friend"


def test_wan_takes_only_one_character(runs_dir, video, tmp_path):
    me = tmp_path / "me.jpg"
    me.write_bytes(b"me")
    with pytest.raises(JobError, match="one character"):
        Job.create(runs_dir, video, generator="wan", mode="direct", character=me, character2=me)


def test_missing_second_character_image_is_refused(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="not found"):
        Job.create(
            runs_dir, video, generator="seedance", mode="depth", character2=tmp_path / "nope.jpg"
        )
```

(Use whatever video fixture `tests/test_jobs.py` already has; if it is named differently, use that.)

Append to `tests/test_review.py`:

```python
def test_seedance_review_covers_the_character_images(runs_dir, tmp_path):
    from character_swap.review import review_files

    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    me, friend = tmp_path / "me.jpg", tmp_path / "friend.jpg"
    me.write_bytes(b"me")
    friend.write_bytes(b"friend")
    job = Job.create(
        runs_dir, upload, generator="seedance", mode="depth", character=me, character2=friend
    )
    files = review_files(job)
    assert files[-2:] == ["character-primary.jpg", "character-secondary.jpg"]


def test_seedance_review_without_characters_is_unchanged(runs_dir, tmp_path):
    from character_swap.modes import MODES
    from character_swap.review import review_files

    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth")
    assert review_files(job) == list(MODES["depth"].review_files)
```

(Import `write_video`, `solid` from `helpers` and `Job` if the file doesn't already.)

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_jobs.py tests/test_review.py -q --no-cov`. Expected: `TypeError: ... unexpected keyword argument 'character2'`.

- [ ] **Step 3: `jobs.py`.** Change the `Job.create` signature and body:

```python
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
        character2: Path | None = None,
        faces: dict[str, str] | None = None,  # slot ("primary"/"secondary") -> saved face name
        language: str = "auto",
    ) -> Job:
```

After the existing `character` checks, add:

```python
        if generator == "wan" and character2 is not None:
            raise JobError("Wan takes one character; a second one only works with Seedance.")
        if character2 is not None and not Path(character2).is_file():
            raise JobError(f"Second character image not found: {character2}")
```

Replace the existing `if character is not None:` copy block with:

```python
            for slot, image in (("primary", character), ("secondary", character2)):
                if image is None:
                    continue
                name = f"character-{slot}{Path(image).suffix.lower()}"
                shutil.copy2(image, root / name)
                state[f"character_{slot}"] = name
            for slot, face_name in (faces or {}).items():
                state[f"face_{slot}"] = face_name
```

- [ ] **Step 4: `review.py`.** Replace the Wan special case in `review_files` with:

```python
CHARACTER_SLOTS = ("primary", "secondary")


def review_files(job: Job) -> list[str]:
    mode = MODES.get(job.mode)
    if mode is None:
        raise ReviewError(f"Unknown mode {job.mode!r}; cannot review.")
    files = list(mode.review_files)
    state = job.state
    if mode.generator == "wan" and not state.get("character_primary"):
        raise ReviewError("This job has no character image to review.")
    # The generator receives these images, so they are reviewed too (any generator).
    files.extend(state[f"character_{slot}"] for slot in CHARACTER_SLOTS if state.get(f"character_{slot}"))
    return files
```

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_jobs.py tests/test_review.py tests/test_generation.py -q --no-cov`. Expected: PASS. Wan fingerprints are unchanged: same files, same order.

- [ ] **Step 6: Failing CLI tests.** Append to `tests/test_cli.py`:

```python
def _saved_face(settings, tmp_path, name="frank"):
    return FaceLibrary(settings.root, sleep=lambda _s: None).add(
        name, _photo(tmp_path / f"{name}.jpg"), FakePiAPI()
    )


def _prepare_ids(monkeypatch):
    ids = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: ids.append(job.id))
    return ids


def test_prepare_seedance_with_a_saved_face(settings, video, tmp_path, monkeypatch):
    _saved_face(settings, tmp_path)
    ids = _prepare_ids(monkeypatch)
    code, _ = run(
        ["prepare", str(video), "--mode", "depth", "--face", "frank", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["face_primary"] == "frank"
    assert state["character_primary"] == "character-primary.jpg"


def test_prepare_seedance_needs_a_character(settings, video):
    code, out = run(["prepare", str(video), "--mode", "depth", "--yes"], settings)
    assert code == 2 and "--face NAME" in out.err.text and "--character IMAGE" in out.err.text


def test_prepare_refuses_face_and_character_together(settings, video, tmp_path):
    _saved_face(settings, tmp_path)
    code, out = run(
        ["prepare", str(video), "--face", "frank", "--character", str(_photo(tmp_path / "c.jpg")), "--yes"],
        settings,
    )
    assert code == 2 and "not both" in out.err.text


def test_prepare_refuses_a_face_that_is_not_active(settings, video, tmp_path):
    lib = FaceLibrary(settings.root, sleep=lambda _s: None, polls=0)
    lib.add("frank", _photo(tmp_path / "me.jpg"), FakePiAPI(assets={"asset-1": "Processing"}))
    code, out = run(["prepare", str(video), "--face", "frank", "--yes"], settings)
    assert code == 2 and "charswap face check frank" in out.err.text


def test_prepare_seedance_character_image_is_checked_and_warned(settings, video, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "toon.png")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and "not a real person" in out.text
    assert Job.load(settings.runs_dir, ids[0]).state["character_primary"] == "character-primary.jpg"


def test_prepare_two_people(settings, video, tmp_path, monkeypatch):
    _saved_face(settings, tmp_path, "frank")
    _saved_face(settings, tmp_path, "sam")
    ids = _prepare_ids(monkeypatch)
    code, _ = run(
        ["prepare", str(video), "--face", "frank", "--face2", "sam", "--subject", "two men", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["face_secondary"] == "sam" and state["subject"] == "two men"


def test_prepare_wan_refuses_a_second_character(settings, video, tmp_path):
    code, out = run(
        _wan_args(video, _photo(tmp_path / "a.jpg"), "--character2", str(_photo(tmp_path / "b.jpg"))),
        settings,
    )
    assert code == 2 and "one character" in out.err.text
```

- [ ] **Step 7: Run them and watch them fail.** `uv run pytest tests/test_cli.py -q --no-cov -k prepare`. Expected: argparse errors for `--face`, `--face2`, `--character2`.

- [ ] **Step 8: `cli.py` prepare.** Add the options next to `--character`:

```python
    prep.add_argument("--face", help="a saved face to put in the video (see charswap face add)")
    prep.add_argument(
        "--character",
        type=Path,
        help="character image: Wan, any picture; Seedance, a character that is not a real person",
    )
    prep.add_argument("--face2", help="Seedance: a second saved face, for a second person")
    prep.add_argument("--character2", type=Path, help="Seedance: a second character image")
```

Replace the first lines of `_prepare` (before the cost estimate) with:

```python
def _characters(args, settings, env, scratch: Path) -> tuple[Path | None, Path | None, dict]:
    """Resolve --face/--character (and the second slot) to image files + face names. Free."""
    library = FaceLibrary(settings.root)
    images: dict[str, Path | None] = {}
    faces: dict[str, str] = {}
    for slot, face_name, image in (
        ("primary", args.face, args.character),
        ("secondary", args.face2, args.character2),
    ):
        flag = "" if slot == "primary" else "2"
        if face_name and image:
            raise JobError(f"Use --face{flag} or --character{flag}, not both.")
        if face_name:
            face = library.get(face_name)
            if face.status != ACTIVE:
                raise JobError(
                    f"Face {face_name!r} is {face.status} at PiAPI. Run charswap face check "
                    f"{face_name} first."
                )
            images[slot], faces[slot] = library.image_path(face), face_name
        elif image and args.generator == "seedance":
            env.out(
                "Note: Seedance takes --character images only for characters that are not a real "
                "person (PiAPI rejects real faces sent this way). For a real person, use --face."
            )
            images[slot] = check_photo(image, scratch / f"character-{slot}.jpg")
        else:
            images[slot] = image
    return images.get("primary"), images.get("secondary"), faces


def _prepare(args, settings, env) -> int:
    mode = args.mode or ("direct" if args.generator == "wan" else "depth")
    if args.generator == "wan" and args.character is None and args.face is None:
        raise JobError("Wan needs --character IMAGE or --face NAME (your face or character picture).")
    if args.generator == "wan" and (args.character2 or args.face2):
        raise JobError("Wan takes one character; a second one only works with Seedance.")
    if args.generator == "seedance" and not (args.face or args.character):
        raise JobError(
            "Seedance needs --face NAME (a saved face; see charswap face add) or --character "
            "IMAGE (a character that is not a real person)."
        )
    if mode == "direct" and args.language != "auto":
        raise JobError("--language only matters for Seedance modes (Wan has no transcript step).")
    with tempfile.TemporaryDirectory() as scratch:
        character, character2, faces = _characters(args, settings, env, Path(scratch))
        # (the rest of today's _prepare body, unchanged, but Job.create gets
        #  character=character, character2=character2, faces=faces)
```

Then:
- Indent the rest of today's `_prepare` body inside the `with` block. `Job.create` copies the images before the temp folder is removed, so the body can stay as it is.
- Add `import tempfile` and `from character_swap.faces import check_photo`.
- The existing Wan error text changes, so check that `test_prepare_wan_needs_character` still matches "character".

- [ ] **Step 9: Run tests.** `uv run pytest tests/test_cli.py tests/test_jobs.py tests/test_review.py -q --no-cov`. Expected: PASS. Then fix the older tests that create Seedance prepares without a character: add `"--character", str(_photo(tmp_path / "c.jpg"))` to their argv (add `tmp_path` to their fixtures). Do **not** weaken what they assert. The tests are:
- `test_prepare_declined_creates_nothing`
- `test_prepare_runs_pipeline_and_prints_job`
- `test_prepare_seedance_still_asks`
- `test_missing_key_is_reported`
- `test_prepare_stage_error_is_friendly`
- `test_keyboard_interrupt_is_reported_with_job_id`
- `test_unexpected_error_is_summarised`
- `test_debug_reraises`
- `test_provider_errors_are_friendly`
- `test_prepare_passes_the_language`

- [ ] **Step 10: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/jobs.py src/character_swap/review.py src/character_swap/cli.py tests/test_jobs.py tests/test_review.py tests/test_cli.py
git commit -m "feat: choose faces/characters at prepare (two slots for Seedance); review hashes them"
```

---

### Task 7: Whole-second clips for Seedance (picture, pitched vocals and restored sound)

**Files:** Modify `src/character_swap/modes.py`, `src/character_swap/media/video.py`, `src/character_swap/media/audio.py`, `src/character_swap/pipeline.py`, `tests/test_video.py`, `tests/test_audio.py`, `tests/test_generation.py`.

PiAPI works in whole seconds. For Seedance jobs:
- `normalize` drops the tail so the picture is a whole number of seconds (12.05 s becomes 12 s).
- The pitched vocals embedded in `prepared.mp4` are cut to the same length.
- After generating, the original sound is cut at that length. Its packets are copied untouched; there are just fewer of them.

Wan jobs behave exactly as today.

- [ ] **Step 1: Failing tests.** Append to `tests/test_video.py`:

```python
def test_normalize_whole_seconds_drops_the_tail(tmp_path):
    src = write_video(tmp_path / "in.mp4", [solid((10, 20, 30))] * 60)  # 2.5 s at 24 fps
    facts = normalize(src, tmp_path / "out.mp4", min_seconds=1, max_seconds=30, whole_seconds=True)
    assert facts["frames"] == 48 and facts["seconds"] == 2.0
    assert count_frames(tmp_path / "out.mp4") == 48


def test_normalize_whole_seconds_still_checks_the_minimum(tmp_path):
    src = write_video(tmp_path / "in.mp4", [solid((10, 20, 30))] * 108)  # 4.5 s
    with pytest.raises(ClipLengthError, match="whole seconds it is 4s.*at least 5s"):
        normalize(src, tmp_path / "out.mp4", min_seconds=4.5, max_seconds=30, whole_seconds=True)
```

(Use the file's existing imports of `normalize`, `count_frames`, `ClipLengthError`, `write_video`, `solid`, adding any that are missing.)

Append to `tests/test_audio.py`:

```python
def test_restore_can_cut_the_original_sound_at_a_length(tmp_path):
    original = write_video(tmp_path / "orig.mp4", [solid((1, 1, 1))] * 72, audio_seconds=3)
    generated = write_video(tmp_path / "gen.mp4", [solid((2, 2, 2))] * 48)  # 2 s, silent
    result = restore_original_audio(generated, original, tmp_path / "out.mp4", seconds=2.0)
    kept = audio_packets(result)
    assert kept == audio_packets(original)[: len(kept)]  # the same packets, just fewer
    assert 1.95 <= stream_seconds(result, "audio") <= 2.05


def test_soundtrack_passes_the_cut_through(tmp_path):
    original = write_video(tmp_path / "orig.mp4", [solid((1, 1, 1))] * 72, audio_seconds=3)
    generated = write_video(tmp_path / "gen.mp4", [solid((2, 2, 2))] * 48)
    result = add_original_soundtrack(generated, original, tmp_path / "out.mp4", seconds=2.0)
    assert stream_seconds(result, "audio") <= 2.05
```

(If `audio_packets` isn't already a helper in `tests/test_audio.py`, add the same one `tests/test_generation.py` uses.)

Append to `tests/test_generation.py`:

```python
def _seedance_job(runs_dir, tmp_path, *, picture, sound):
    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * round(picture * 24), audio_seconds=sound)
    me = tmp_path / "me.jpg"
    me.write_bytes(b"me")
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth", character=me)
    job.update(video={"seconds": float(int(picture))})  # what a whole-second normalize saves
    return job


def test_seedance_sound_longer_than_the_whole_second_picture_is_fine(runs_dir, tmp_path):
    job = _seedance_job(runs_dir, tmp_path, picture=4.9, sound=4.9)  # picture becomes 4 s
    pipeline._check_soundtrack_fits(job)  # no error: the sound is cut at 4 s


def test_seedance_sound_shorter_than_the_picture_is_still_refused(runs_dir, tmp_path):
    job = _seedance_job(runs_dir, tmp_path, picture=5.0, sound=4.0)
    with pytest.raises(StageError, match="Nothing was charged"):
        pipeline._check_soundtrack_fits(job)


def test_embed_cuts_the_pitched_vocals_to_the_picture(runs_dir, tmp_path):
    from character_swap.media.audio import write_wav
    from character_swap.media.video import stream_seconds
    from helpers import tone

    job = _seedance_job(runs_dir, tmp_path, picture=2.0, sound=2.0)
    write_video(job.path("composite-silent.mp4"), [solid((3, 3, 3))] * 48)
    write_wav(job.path("vocals-plus3.wav"), tone(2.6))
    pipeline._embed(job)
    assert stream_seconds(job.path("prepared.mp4"), "audio") <= 2.05
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_video.py tests/test_audio.py tests/test_generation.py -q --no-cov`. Expected: `TypeError: unexpected keyword argument 'whole_seconds'` / `'seconds'`, and the embed/soundtrack tests failing.

- [ ] **Step 3: `modes.py`.** Below `GENERATOR_LIMITS` add:

```python
# Generators whose clips are cut to whole seconds (PiAPI durations are whole seconds).
WHOLE_SECONDS: frozenset[str] = frozenset({"seedance"})
```

- [ ] **Step 4: `media/video.py`, `normalize`.** Add the keyword `whole_seconds: bool = False`. Compute the frame count **before** opening the decoder, right after the max/min checks:

```python
    count = round(seconds * FPS)
    if whole_seconds:
        count -= count % FPS  # whole seconds only: drop the tail
        if count < min_seconds * FPS:
            raise ClipLengthError(
                f"Clip is {seconds:.1f}s; trimmed to whole seconds it is {count // FPS}s, and "
                f"this generator needs at least {math.ceil(min_seconds):g}s."
            )
```

Then delete the later `count = round(seconds * FPS)` line inside the `with` block, and add `import math`. Update the docstring: `"""Re-time to 24 fps (holding the nearest earlier frame), upright it, fit within 960 px. With whole_seconds, drop the tail to a whole number of seconds."""`.

- [ ] **Step 5: `media/audio.py`.** Give `restore_original_audio` the keyword `seconds: float | None = None`:
  1. Change the docstring to: `"""Replace the generated audio with the original's audio packets, byte for byte. With seconds, the original sound stops there (later packets are left out, never re-encoded)."""`.
  2. After `audio_seconds = stream_seconds(original, "audio")`, add:

```python
    if seconds is not None:
        audio_seconds = min(audio_seconds, seconds)
```

  3. Replace the audio copy loop with:

```python
            first = None
            for packet in source.demux(audio=0):
                if packet.dts is None:
                    continue
                if seconds is not None and packet.pts is not None:
                    moment = float(packet.pts * packet.time_base)
                    first = moment if first is None else first
                    if moment - first >= seconds:
                        break
                packet.stream = audio
                output.mux(packet)
```

  4. `add_original_soundtrack(generated, upload, dst, *, seconds: float | None = None)` passes `seconds=seconds` to `restore_original_audio`.

- [ ] **Step 6: `pipeline.py`.**
  1. Import `WHOLE_SECONDS` from `modes`, and `RATE` from `media.audio`.
  2. In `prepare()`, pass `whole_seconds=job.generator in WHOLE_SECONDS` to `normalize`.
  3. `_embed`:

```python
def _embed(job: Job) -> None:
    samples = read_audio(job.path("vocals-plus3.wav"))
    if job.generator in WHOLE_SECONDS:  # the picture was cut to whole seconds; so is the voice
        samples = samples[:, : round(clip_seconds(job) * RATE)]
    embed_samples(job.path("composite-silent.mp4"), samples, job.path("prepared.mp4"))
```

  4. `_check_soundtrack_fits`: replace the final comparison with:

```python
    picture = _readable_clip_seconds(job)
    gap = sound - picture
    if job.generator in WHOLE_SECONDS and gap >= 0:
        return  # longer sound than the whole-second picture: it is cut to match after generating
    if abs(gap) > FIT_LIMIT - 0.1:  # leave 0.1 s for the generator's rounding
```

  (Keep the existing `raise StageError(...)` below it.)

  5. In `generate()`, the soundtrack line becomes:

```python
        cut = request.seconds if job.generator in WHOLE_SECONDS else None
        _write_atomically(
            result,
            lambda part: add_original_soundtrack(raw, job.path("upload.mp4"), part, seconds=cut),
        )
```

- [ ] **Step 7: Run tests.** `uv run pytest tests/test_video.py tests/test_audio.py tests/test_generation.py tests/test_pipeline.py -q --no-cov`. Expected: PASS (Wan tests unchanged).

- [ ] **Step 8: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/modes.py src/character_swap/media/video.py src/character_swap/media/audio.py src/character_swap/pipeline.py tests/test_video.py tests/test_audio.py tests/test_generation.py
git commit -m "feat: whole-second clips for Seedance; the original sound is cut to match"
```

---

### Task 8: An upload copy under 7 MB, with the audio untouched

**Files:** Create `src/character_swap/media/upload.py`, `tests/test_upload.py`.

- [ ] **Step 1: Failing tests.** Create `tests/test_upload.py`:

```python
import av
import numpy as np
import pytest

from character_swap.media.audio import embed_samples
from character_swap.media.upload import UploadTooLarge, fit_for_upload
from character_swap.media.video import VideoWriter, count_frames
from helpers import tone


def noisy_clip(tmp_path):
    """Random pixels compress badly, so the CRF 18 original is big for its size."""
    rng = np.random.default_rng(1)
    silent = tmp_path / "silent.mp4"
    with VideoWriter(silent, 128, 128) as writer:
        for _ in range(48):
            writer.write(rng.integers(0, 255, (128, 128, 3), dtype=np.uint8))
    return embed_samples(silent, tone(2.0), tmp_path / "prepared.mp4")


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def test_small_file_is_used_as_it_is(tmp_path):
    clip = noisy_clip(tmp_path)
    assert fit_for_upload(clip, tmp_path / "copy.mp4", clip.stat().st_size) == clip
    assert not (tmp_path / "copy.mp4").exists()


def test_big_file_gets_a_smaller_copy_with_the_same_frames_and_audio(tmp_path):
    clip = noisy_clip(tmp_path)
    limit = int(clip.stat().st_size * 0.8)
    copy = fit_for_upload(clip, tmp_path / "copy.mp4", limit)
    assert copy == tmp_path / "copy.mp4" and copy.stat().st_size <= limit
    assert count_frames(copy) == 48
    assert audio_packets(copy) == audio_packets(clip)  # lip-sync audio untouched


def test_too_big_even_compressed_is_refused_and_leaves_nothing(tmp_path):
    clip = noisy_clip(tmp_path)
    with pytest.raises(UploadTooLarge, match="shorter clip"):
        fit_for_upload(clip, tmp_path / "copy.mp4", 100)
    assert not (tmp_path / "copy.mp4").exists()
```

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_upload.py -q --no-cov`. Expected: `ModuleNotFoundError: character_swap.media.upload`.

- [ ] **Step 3: Create `src/character_swap/media/upload.py`:**

```python
"""A copy of a video small enough to upload, with its audio packets untouched."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av

QUALITIES = (23, 28, 33)  # x264 CRF: higher = smaller file, softer picture


class UploadTooLarge(ValueError):
    """Even the most compressed copy is over the upload limit."""


def fit_for_upload(src: Path, dst: Path, max_bytes: int) -> Path:
    """`src` itself when it is small enough; otherwise a re-encoded copy at `dst`.

    The copy keeps every frame, the frame rate, the size and the audio packets exactly;
    only the picture is compressed harder.
    """
    src, dst = Path(src), Path(dst)
    if src.stat().st_size <= max_bytes:
        return src
    for crf in QUALITIES:
        _reencode(src, dst, crf)
        if dst.stat().st_size <= max_bytes:
            return dst
    dst.unlink(missing_ok=True)
    raise UploadTooLarge(
        f"{src.name} is still over {max_bytes / 1e6:.0f} MB after compressing. Use a shorter clip."
    )


def _reencode(src: Path, dst: Path, crf: int) -> None:
    with av.open(str(src)) as source, av.open(str(dst), "w") as output:
        video_in = source.streams.video[0]
        fps = int(round(float(video_in.average_rate or 24)))
        video = output.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = video_in.width, video_in.height, "yuv420p"
        video.options = {"crf": str(crf), "preset": "medium"}
        audio_in = source.streams.audio[0] if source.streams.audio else None
        audio = output.add_stream_from_template(audio_in) if audio_in else None
        index = 0
        for packet in source.demux(*[s for s in (video_in, audio_in) if s is not None]):
            if packet.stream.type == "video":
                for frame in packet.decode():
                    frame.pts, frame.time_base = index, Fraction(1, fps)
                    index += 1
                    for out in video.encode(frame):
                        output.mux(out)
            elif packet.dts is not None:
                packet.stream = audio
                output.mux(packet)
        for out in video.encode():
            output.mux(out)
```

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_upload.py -q --no-cov`. Expected: PASS. If the 0.8 limit isn't reached at CRF 23, the loop tries 28 and 33. If the test still fails, check the frame count, the size and whether the audio packets survived before changing the qualities.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/media/upload.py tests/test_upload.py
git commit -m "feat: upload copy under the size limit with the audio untouched"
```

---

### Task 9: The Seedance generator

**Files:** Modify `src/character_swap/providers/generators/base.py`. Create `src/character_swap/providers/generators/seedance_piapi.py`, `tests/test_seedance.py`.

- [ ] **Step 1: `base.py`.** Extend the two dataclasses (all new fields have defaults, so Wan is untouched):

```python
@dataclass(frozen=True)
class CharacterRef:
    image_path: Path  # local copy in the job folder
    face_asset_id: str | None = None  # PiAPI face-library id (a saved face)
    name: str = ""  # the saved face's name, for messages


@dataclass(frozen=True)
class GenerationRequest:
    video: Path  # Seedance: the prepared composite; Wan: the normalized original
    primary: CharacterRef
    seconds: float  # normalized clip length, for estimates
    seed: int  # chosen once per job; the final reuses it (Wan)
    prompt: str = ""  # Wan has no prompt input
    secondary: CharacterRef | None = None  # Seedance only
    final_resolution: str = ""  # final only: "720p" | "1080p" (Seedance; Wan is always 720p)
    draft_task_id: str = ""  # final only: the finished draft's provider task (Seedance promote)
```

- [ ] **Step 2: Failing tests.** Create `tests/test_seedance.py`:

```python
import dataclasses
import json

import pytest

from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators import seedance_piapi
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.seedance_piapi import TASK_TYPE, SeedanceGenerator
from fakes import FakePiAPI

PROMPT = "Replace the person in @video1 with the character in @image1."


def request(tmp_path, *, seconds=12.0, face="a1", secondary=None, **extra):
    video = tmp_path / "prepared.mp4"
    video.write_bytes(b"v")
    image = tmp_path / "character-primary.jpg"
    image.write_bytes(b"i")
    primary = CharacterRef(image, face_asset_id=face, name="frank" if face else "")
    return GenerationRequest(
        video=video,
        primary=primary,
        secondary=secondary,
        seconds=seconds,
        seed=1,
        prompt=PROMPT,
        **extra,
    )


def generate(gen, req, stage, tmp_path):
    return gen.generate(req, stage, record=tmp_path / f"{stage}-prediction.json", dest=tmp_path / f"{stage}-raw.mp4")


def test_estimates_include_the_input_video(tmp_path):
    gen = SeedanceGenerator(FakePiAPI())
    assert gen.estimate(request(tmp_path), "draft").total == pytest.approx(2.97)
    assert gen.estimate(request(tmp_path), "final").total == pytest.approx(15.84)
    req = request(tmp_path, final_resolution="720p")
    assert gen.estimate(req, "final").total == pytest.approx(6.93)
    assert "input video" in gen.estimate(req, "final").describe()


def test_unknown_final_resolution_is_refused(tmp_path):
    with pytest.raises(ProviderError, match="1080p or 720p"):
        SeedanceGenerator(FakePiAPI()).estimate(request(tmp_path, final_resolution="4k"), "final")


def test_draft_sends_one_edit_task_with_the_saved_face(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    out = generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert out.read_bytes() == b"mp4" and piapi.asset_checks == ["a1"]
    assert [p.name for p in piapi.uploads] == ["prepared.mp4"]
    assert piapi.bodies == [
        {
            "model": "seedance",
            "task_type": TASK_TYPE,
            "input": {
                "prompt": PROMPT,
                "mode": "omni_reference",
                "omni_reference_task_type": "edit",
                "video_urls": ["https://img.theapi.app/temp/prepared.mp4"],
                "image_urls": ["asset://a1"],
                "aspect_ratio": "adaptive",
                "duration": -1,
                "resolution": "480p",
                "auto_upload_assets": False,
                "draft": True,
            },
        }
    ]


def test_a_character_image_without_a_face_is_uploaded(tmp_path):
    piapi = FakePiAPI()
    generate(SeedanceGenerator(piapi), request(tmp_path, face=None), "draft", tmp_path)
    assert piapi.bodies[0]["input"]["image_urls"] == ["https://img.theapi.app/temp/character-primary.jpg"]
    assert piapi.asset_checks == []


def test_two_characters_keep_their_order(tmp_path):
    second = tmp_path / "character-secondary.jpg"
    second.write_bytes(b"2")
    piapi = FakePiAPI(assets={"a1": "Active", "a2": "Active"})
    req = request(tmp_path, secondary=CharacterRef(second, face_asset_id="a2", name="sam"))
    generate(SeedanceGenerator(piapi), req, "draft", tmp_path)
    assert piapi.bodies[0]["input"]["image_urls"] == ["asset://a1", "asset://a2"]


def test_1080p_final_promotes_the_draft_and_sends_nothing_else(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    generate(SeedanceGenerator(piapi), request(tmp_path, draft_task_id="task-9"), "final", tmp_path)
    assert piapi.bodies == [
        {"model": "seedance", "task_type": TASK_TYPE, "input": {"draft_task_id": "task-9", "resolution": "1080p"}}
    ]
    assert piapi.uploads == [] and piapi.asset_checks == []


def test_720p_final_is_a_fresh_run_without_the_draft_flag(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    req = request(tmp_path, final_resolution="720p", draft_task_id="task-9")
    generate(SeedanceGenerator(piapi), req, "final", tmp_path)
    task = piapi.bodies[0]["input"]
    assert task["resolution"] == "720p" and "draft" not in task and task["prompt"] == PROMPT


def test_promote_without_a_draft_id_sends_nothing(tmp_path):
    piapi = FakePiAPI()
    with pytest.raises(ProviderError, match="--resolution 720p"):
        generate(SeedanceGenerator(piapi), request(tmp_path), "final", tmp_path)
    assert piapi.bodies == []


@pytest.mark.parametrize("status", ["Processing", "Failed", None])
def test_a_face_that_is_not_active_stops_before_paying(tmp_path, status):
    piapi = FakePiAPI(assets={} if status is None else {"a1": status})
    with pytest.raises(ProviderError, match="charswap face check frank.*Nothing was charged"):
        generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert piapi.bodies == [] and piapi.uploads == []


def test_an_empty_prompt_stops_before_paying(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    req = dataclasses.replace(request(tmp_path), prompt="  ")
    with pytest.raises(ProviderError, match="charswap prompt"):
        generate(SeedanceGenerator(piapi), req, "draft", tmp_path)
    assert piapi.bodies == []


def test_resume_reuses_the_saved_task_without_uploading(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    (tmp_path / "draft-prediction.json").write_text(json.dumps({"id": "t1", "status": "succeeded"}))
    generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert piapi.bodies == [] and piapi.uploads == [] and piapi.asset_checks == []


def test_big_prepared_video_is_uploaded_as_a_smaller_copy(tmp_path, monkeypatch):
    seen = []

    def fake_fit(src, dst, max_bytes):
        seen.append((src.name, dst.name, max_bytes))
        dst.write_bytes(b"small")
        return dst

    monkeypatch.setattr(seedance_piapi, "fit_for_upload", fake_fit)
    piapi = FakePiAPI(assets={"a1": "Active"})
    generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert seen == [("prepared.mp4", "upload-copy.mp4", 7_000_000)]
    assert [p.name for p in piapi.uploads] == ["upload-copy.mp4"]


def test_expired_download_names_the_record_and_the_rebuy_cost(tmp_path):
    class Expiring(FakePiAPI):
        def download(self, url, dest):
            raise OutputExpired("gone")

    record = tmp_path / "draft-prediction.json"
    with pytest.raises(ProviderError, match=r"24 hours.*Delete .*draft-prediction\.json.*\$2\.97"):
        SeedanceGenerator(Expiring(assets={"a1": "Active"})).generate(
            request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
        )
    assert record.exists()


def test_capabilities():
    assert (SeedanceGenerator.min_seconds, SeedanceGenerator.max_seconds) == (4, 30)
    assert SeedanceGenerator.supports_secondary and SeedanceGenerator.provider == "piapi"
    assert SeedanceGenerator.model == TASK_TYPE == "seedance-2.5-less-restriction"
```

- [ ] **Step 3: Run them and watch them fail.** `uv run pytest tests/test_seedance.py -q --no-cov`. Expected: `ModuleNotFoundError: ...seedance_piapi`.

- [ ] **Step 4: Create `src/character_swap/providers/generators/seedance_piapi.py`:**

```python
"""Seedance 2.5 on PiAPI: redraws the disguised people in the prepared video as your characters."""

from __future__ import annotations

from pathlib import Path

from character_swap.costs import Estimate, cost_line
from character_swap.media.upload import UploadTooLarge, fit_for_upload
from character_swap.modes import GENERATOR_LIMITS
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators.base import (
    CharacterRef,
    GenerationRequest,
    OnCreated,
    Stage,
)
from character_swap.providers.piapi import MAX_UPLOAD_BYTES

TASK_TYPE = "seedance-2.5-less-restriction"
# "edit" changes the people in @video1 and keeps its length and framing. PiAPI fails a task
# after submission when its own reading of the request differs, so the live test checks it.
TASK_KIND = "edit"
DRAFT = "480p"
PROMOTE = "1080p"  # upgrades the exact draft
FINALS = (PROMOTE, "720p")  # 720p is a fresh run with the same prompt and inputs
PRICE = {"480p": "seedance-480", "720p": "seedance-720", "1080p": "seedance-1080"}
UPLOAD_COPY = "upload-copy.mp4"


class SeedanceGenerator:
    name = "seedance"
    provider = "piapi"
    model = TASK_TYPE
    min_seconds, max_seconds = GENERATOR_LIMITS["seedance"]
    supports_real_faces = True  # through saved faces (PiAPI's face library)
    supports_secondary = True

    def __init__(self, piapi) -> None:
        self._piapi = piapi

    @staticmethod
    def resolution(request: GenerationRequest, stage: Stage) -> str:
        if stage == "draft":
            return DRAFT
        chosen = request.final_resolution or PROMOTE
        if chosen not in FINALS:
            raise ProviderError(f"Seedance finals are {' or '.join(FINALS)}, not {chosen}.")
        return chosen

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate:
        key = PRICE[self.resolution(request, stage)]
        return Estimate(
            lines=(cost_line(key, request.seconds), cost_line(f"{key}-input", request.seconds))
        )

    @staticmethod
    def build_input(
        request: GenerationRequest,
        *,
        video_url: str,
        image_urls: list[str],
        resolution: str,
        draft: bool,
    ) -> dict:
        task = {
            "prompt": request.prompt,
            "mode": "omni_reference",
            "omni_reference_task_type": TASK_KIND,
            "video_urls": [video_url],
            "image_urls": list(image_urls),  # @image1, @image2 in this order
            "aspect_ratio": "adaptive",  # an edit keeps the clip's own shape...
            "duration": -1,  # ...and its own (whole-second) length
            "resolution": resolution,
            "auto_upload_assets": False,  # PiAPI's documented defaults disagree: always say it
        }
        if draft:
            task["draft"] = True
        return {"model": "seedance", "task_type": TASK_TYPE, "input": task}

    @staticmethod
    def promote_body(draft_task_id: str) -> dict:
        if not draft_task_id:
            raise ProviderError(
                "The draft's PiAPI task id is missing, so it can't be upgraded to 1080p. Make a "
                "new draft, or use --resolution 720p. Nothing was charged."
            )
        return {
            "model": "seedance",
            "task_type": TASK_TYPE,
            "input": {"draft_task_id": draft_task_id, "resolution": PROMOTE},
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
        resolution = self.resolution(request, stage)
        if record.exists():  # resume: run() reads the saved task and never posts again
            body: dict = {}
        elif stage == "final" and resolution == PROMOTE:
            body = self.promote_body(request.draft_task_id)
        else:
            if not request.prompt.strip():
                raise ProviderError(
                    "Seedance needs a prompt; see charswap prompt <job>. Nothing was charged."
                )
            self._check_faces(request)
            body = self.build_input(
                request,
                video_url=self._upload_video(request, record.parent),
                image_urls=self._image_urls(request),
                resolution=resolution,
                draft=stage == "draft",
            )
        url = self._piapi.run(body=body, record=record, on_created=on_created)
        try:
            return self._piapi.download(url, dest)
        except OutputExpired as exc:
            rebuy = self.estimate(request, stage).total
            raise ProviderError(
                f"The Seedance {stage} finished but its download link expired (PiAPI keeps "
                f"results 24 hours; {exc}). Delete {record} and run the same command again to "
                f"make a new one (costs about ${rebuy:.2f})."
            ) from exc

    @staticmethod
    def _refs(request: GenerationRequest) -> list[CharacterRef]:
        return [ref for ref in (request.primary, request.secondary) if ref is not None]

    def _check_faces(self, request: GenerationRequest) -> None:
        """Free: every saved face must be Active (this also keeps PiAPI from purging it)."""
        for ref in self._refs(request):
            if not ref.face_asset_id:
                continue
            asset = self._piapi.get_asset(ref.face_asset_id)
            status = (asset or {}).get("status") or "removed"
            if str(status).lower() != "active":
                name = ref.name or ref.face_asset_id
                raise ProviderError(
                    f"Your saved face {name!r} isn't usable at PiAPI right now ({status}). Run "
                    f"charswap face check {name}, or charswap face renew {name} if PiAPI removed "
                    "it. Nothing was charged."
                )

    def _image_urls(self, request: GenerationRequest) -> list[str]:
        return [
            f"asset://{ref.face_asset_id}"
            if ref.face_asset_id
            else self._piapi.upload_temp(ref.image_path)
            for ref in self._refs(request)
        ]

    def _upload_video(self, request: GenerationRequest, folder: Path) -> str:
        try:
            video = fit_for_upload(request.video, folder / UPLOAD_COPY, MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            raise ProviderError(f"{exc} Nothing was charged.") from exc
        return self._piapi.upload_temp(video)
```

- [ ] **Step 5: Run tests.** `uv run pytest tests/test_seedance.py tests/test_wan.py -q --no-cov`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/providers/generators/base.py src/character_swap/providers/generators/seedance_piapi.py tests/test_seedance.py
git commit -m "feat: Seedance 2.5 generator on PiAPI (480p draft, 1080p promote or 720p final)"
```

---

### Task 10: The pipeline generates Seedance jobs (prompt file, faces, draft id, resolution)

**Files:** Modify `src/character_swap/modes.py`, `src/character_swap/pipeline.py`, `src/character_swap/cli.py` (one import line), `tests/test_modes.py`, `tests/test_generation.py`.

- [ ] **Step 1: Failing tests.** In `tests/test_modes.py`, replace `test_only_wan_generates_in_this_version` with:

```python
def test_every_mode_generates():
    assert all(mode.generates for mode in MODES.values())
```

In `tests/test_generation.py`, **delete** `test_seedance_jobs_cannot_generate_yet` and `test_next_step_for_seedance_waits_for_the_next_version`. In `tests/test_view_model.py`:
- add `"draft", "final"` to the end of the three Seedance lists in `SECTION_IDS`;
- delete `test_seedance_jobs_have_no_generation_sections_yet`.

Seedance pages now get the Draft/Final sections. Their wording becomes Seedance's in Task 12.

Then append to `tests/test_generation.py`:

```python
from PIL import Image

from character_swap.faces import FaceLibrary
from character_swap.modes import MODES
from character_swap.pipeline import final_resolution, generation_prompt, write_prompt_file
from character_swap.prompts import PromptError
from fakes import FakePiAPI


@pytest.fixture
def seedance(runs_dir, tmp_path):
    """An approved depth job with a saved face, as if prepare() had run."""
    upload = write_video(tmp_path / "talk.mp4", [solid((90, 60, 30))] * 108, audio_seconds=4.5)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(photo, "JPEG")
    library = FaceLibrary(tmp_path, sleep=lambda _s: None)
    face = library.add("frank", photo, FakePiAPI())
    job = Job.create(
        runs_dir,
        upload,
        generator="seedance",
        mode="depth",
        character=library.image_path(face),
        faces={"primary": "frank"},
    )
    for name in MODES["depth"].review_files:
        write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in MODES["depth"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="lets get it", scene="A hotel lobby.")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    output = write_video(tmp_path / "seedance-out.mp4", [solid((20, 200, 20))] * 96)
    services = Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance": FakeGenerator(output)},
        faces=library,
    )
    return job, services


def test_seedance_draft_sends_the_prepared_video_face_and_default_prompt(seedance, runs_dir):
    job, services = seedance
    result = generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    (stage, request), = services.generators["seedance"].calls
    assert stage == "draft" and request.video == job.path("prepared.mp4")
    assert request.primary.face_asset_id == "asset-1" and request.primary.name == "frank"
    assert "lets get it" in request.prompt and "@image1" in request.prompt
    assert job.state["stages"]["draft"]["prompt"] == request.prompt
    assert stream_seconds(result, "audio") <= 4.05  # the 4.5 s sound was cut to the picture


def test_an_edited_prompt_file_is_what_is_sent(seedance, runs_dir):
    job, services = seedance
    path = write_prompt_file(job)
    path.write_text("Make the person in @video1 look like @image1. Keep everything else.")
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    request = services.generators["seedance"].calls[0][1]
    assert request.prompt == "Make the person in @video1 look like @image1. Keep everything else."


@pytest.mark.parametrize(
    ("text", "words"),
    [("", "empty"), ("Swap @video1 please.", "@image1"), ("@video1 @image1 and @image2", "@image2")],
)
def test_prompt_file_problems_are_plain_errors(seedance, text, words):
    job, _ = seedance
    write_prompt_file(job).write_text(text)
    with pytest.raises(PromptError, match=words):
        generation_prompt(job, "draft")


def test_1080p_final_carries_the_draft_task_and_records_the_resolution(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(job, services, "final", runs_dir=runs_dir, **QUIET)
    request = services.generators["seedance"].calls[1][1]
    assert request.draft_task_id == "p-draft" and request.final_resolution == "1080p"
    assert job.state["stages"]["final"]["resolution"] == "1080p"


def test_720p_final_reuses_the_drafts_prompt(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    sent = job.state["stages"]["draft"]["prompt"]
    write_prompt_file(job).write_text("@video1 @image1 changed after the draft")
    generate(job, services, "final", runs_dir=runs_dir, final_resolution="720p", **QUIET)
    request = services.generators["seedance"].calls[1][1]
    assert request.prompt == sent and request.final_resolution == "720p"


def test_a_started_final_keeps_its_resolution(seedance):
    job, _ = seedance
    job.mark_stage("final", "failed", resolution="1080p", error="lost connection")
    job.path("final-prediction.json").write_text('{"id": "t9", "status": "processing"}')
    with pytest.raises(StageError, match="--resolution 1080p"):
        final_resolution(job, "720p")
    assert final_resolution(job, "") == "1080p"


def test_wan_finals_are_720p_only(wan_job):
    assert final_resolution(wan_job, "") == "720p"
    with pytest.raises(StageError, match="720p"):
        final_resolution(wan_job, "1080p")


def test_next_step_for_an_approved_seedance_job_is_the_draft(seedance):
    job, _ = seedance
    assert next_step(job, approved=True) == f"charswap generate {job.id} --draft"


def test_a_deleted_saved_face_is_a_plain_error(seedance, runs_dir):
    from character_swap.faces import FaceError

    job, services = seedance
    (services.faces.folder / "faces.json").write_text("[]")
    with pytest.raises(FaceError, match="charswap face add frank"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["seedance"].calls == []
```

Also add `from character_swap.media.video import stream_seconds` to the imports. Expected now: `ImportError` for `final_resolution`.

- [ ] **Step 2: `modes.py`.** Set `generates=True` for `depth`, `face_mesh` and `depth_mesh`. Change the `generates` field comment to `# draft/final available (every mode since Plan 2b)`.

- [ ] **Step 3: `pipeline.py`.** Add the imports `from typing import Any` and `from character_swap.prompts import PromptError, build_prompt, validate_prompt`. Then make these changes:

1. `Services` gets one more field: `faces: Any = None  # faces.FaceLibrary (Seedance jobs with saved faces)`.

2. Replace `GENERATION_LABELS` with a function. Keep the old name working for the viewer and the CLI until they switch, in this task:

```python
def generation_label(job: Job, stage: str, resolution: str = "") -> str:
    if stage == "draft":
        return "Generating the 480p draft"
    return f"Generating the {resolution or final_resolution(job, '')} final"
```

  In `cli.py`, replace the one use of `GENERATION_LABELS[stage]` with `generation_label(job, stage)`, and change the import.

3. Add the prompt helpers below `next_step`:

```python
PROMPT_FILE = "prompt.txt"  # Seedance: the editable prompt (charswap prompt <job>)


def default_prompt(job: Job) -> str:
    state = job.state
    return build_prompt(
        mode=job.mode,
        scene=state.get("scene"),
        transcript=state.get("transcript"),
        has_secondary=bool(state.get("character_secondary")),
    )


def write_prompt_file(job: Job) -> Path:
    """Write the default prompt to prompt.txt unless the user already has one."""
    path = job.path(PROMPT_FILE)
    if not path.exists():
        _write_atomically(path, lambda part: part.write_text(default_prompt(job), encoding="utf-8"))
    return path


def generation_prompt(job: Job, stage: str) -> str:
    """The text Seedance receives: the draft's saved prompt for a final, else prompt.txt."""
    if stage == "final":
        saved = job.state["stages"].get("draft", {}).get("prompt")
        if isinstance(saved, str) and saved:
            return saved
    path = job.path(PROMPT_FILE)
    text = (path.read_text(encoding="utf-8") if path.exists() else default_prompt(job)).strip()
    if not text:
        raise PromptError(
            f"{path} is empty. Write what Seedance should do, or delete the file to use the "
            "default prompt."
        )
    _check_placeholders(text, has_secondary=bool(job.state.get("character_secondary")))
    return validate_prompt(text)


def _check_placeholders(text: str, *, has_secondary: bool) -> None:
    """PiAPI refuses prompts whose @video1/@image1/@image2 don't match what is sent."""
    for needed in ("@video1", "@image1"):
        if needed not in text:
            raise PromptError(f"The prompt must mention {needed} (the clip and your character).")
    if has_secondary and "@image2" not in text:
        raise PromptError("This job has a second character: the prompt must mention @image2.")
    if not has_secondary and "@image2" in text:
        raise PromptError(
            "The prompt mentions @image2 but this job has no second character; remove it."
        )
```

4. Replace `build_request` with:

```python
def _character(job: Job, slot: str, faces: Any) -> CharacterRef | None:
    state = job.state
    image = state.get(f"character_{slot}")
    if not image:
        return None
    face_name = state.get(f"face_{slot}")
    asset_id = None
    if face_name:
        if faces is None:
            raise StageError("The face library is not set up for this command.")
        asset_id = faces.get(face_name).asset_id  # the current id (charswap face renew)
    return CharacterRef(job.path(image), face_asset_id=asset_id, name=face_name or "")


def _draft_task_id(job: Job) -> str:
    record, _, _ = generation_files("draft")
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    found = data.get("id") if isinstance(data, dict) else None
    return found if isinstance(found, str) else ""


def build_request(
    job: Job, *, faces: Any = None, stage: str = "draft", final_resolution: str = ""
) -> GenerationRequest:
    state = job.state
    primary = _character(job, "primary", faces)
    if primary is None:
        raise StageError(
            "This job has no character image. Prepare a new job with --face NAME or "
            "--character IMAGE."
        )
    seedance = job.generator == "seedance"
    return GenerationRequest(
        video=job.path("prepared.mp4" if seedance else "original.mp4"),
        primary=primary,
        secondary=_character(job, "secondary", faces),
        seconds=_readable_clip_seconds(job),
        seed=int(state["seed"]),
        prompt=generation_prompt(job, stage) if seedance else "",
        final_resolution=final_resolution if stage == "final" else "",
        draft_task_id=_draft_task_id(job) if stage == "final" else "",
    )
```

5. Add `final_resolution`:

```python
FINAL_RESOLUTIONS = {"seedance": ("1080p", "720p"), "wan": ("720p",)}  # first = default


def final_resolution(job: Job, requested: str = "") -> str:
    """The final's resolution: the one a started final used, else the request, else default."""
    choices = FINAL_RESOLUTIONS[job.generator]
    saved = job.state["stages"].get("final", {}).get("resolution", "")
    chosen = requested or saved or choices[0]
    if chosen not in choices:
        raise StageError(f"{job.generator} finals can be {' or '.join(choices)}, not {chosen}.")
    record, raw, _ = generation_files("final")
    started = job.path(record).exists() or job.path(raw).exists()
    if saved and chosen != saved and started:
        raise StageError(
            f"A {saved} final was already started for this job; run the same command with "
            f"--resolution {saved} to pick it up (no new charge)."
        )
    return chosen
```

6. In `generate()`:
  - Add the keyword `final_resolution: str = ""`. (Call the local variable `resolution` so it doesn't shadow the function.)
  - Replace `request = build_request(job)` with:

```python
    resolution = final_resolution_for(job, stage, final_resolution)
    request = build_request(
        job, faces=services.faces, stage=stage, final_resolution=resolution
    )
```

  - Add `final_resolution_for` next to `final_resolution`:

```python
def final_resolution_for(job: Job, stage: str, requested: str) -> str:
    return final_resolution(job, requested) if stage == "final" else ""
```

  - Use `label = generation_label(job, stage, resolution)`. The `resolution = ...` and `request = ...` lines move **above** the `label = ...` line (today `label` is computed first).
  - Replace the `_tracked(job, stage, fingerprint=inputs)` call with:

```python
    extra: dict[str, str] = {}
    if stage == "draft" and request.prompt:
        # The final (720p) reuses exactly the text PiAPI received: on a resume (record or
        # download already there) keep the prompt saved when the task was created.
        saved = job.state["stages"].get("draft", {}).get("prompt")
        sent_before = record.exists() or raw.exists()
        extra["prompt"] = saved if (sent_before and isinstance(saved, str) and saved) else request.prompt
    if stage == "final":
        extra["resolution"] = resolution
    with _tracked(job, stage, fingerprint=inputs, **extra), _wrap(label):
```

7. In `check_can_generate`, change the not-generating message to `"This mode can't generate."` The branch is now unreachable for real modes but stays as a guard. In `next_step`, change the `if not mode.generates:` return value to `"this mode can't generate"` (no "next version" text is left anywhere).

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_generation.py tests/test_modes.py tests/test_cli.py -q --no-cov`. Expected: PASS. If `tests/test_cli.py::test_seedance_jobs_cannot_generate_yet` now fails, that's expected: Task 11 replaces it, so delete it here.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/modes.py src/character_swap/pipeline.py src/character_swap/cli.py tests/test_modes.py tests/test_generation.py tests/test_cli.py
git commit -m "feat: pipeline generates Seedance jobs (prompt file, saved faces, promote or 720p)"
```

---

### Task 11: CLI: Seedance generate, `prompt` writes `prompt.txt`, keys per generator, doctor

**Files:** Modify `src/character_swap/cli.py`, `src/character_swap/config.py`, `.env.example`, `tests/test_cli.py`, `tests/test_config.py`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_cli.py`:

```python
from character_swap.modes import MODES as _MODES


def _approved_seedance_job(settings, video, tmp_path):
    _saved_face(settings, tmp_path)
    library = FaceLibrary(settings.root)
    job = Job.create(
        settings.runs_dir,
        video,
        generator="seedance",
        mode="depth",
        character=library.image_path(library.get("frank")),
        faces={"primary": "frank"},
    )
    for name in _MODES["depth"].review_files:
        write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in _MODES["depth"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="hello there", scene="A studio.")
    approve_review(job, approved=True, note="ok", fingerprint=review_fingerprint(job))
    return job


def seedance_services(settings):
    """The real Seedance generator on a fake PiAPI that returns a real 4 s video."""
    from character_swap.providers.generators.seedance_piapi import SeedanceGenerator

    output = write_video(settings.root / "seedance-out.mp4", [solid((20, 200, 20))] * 96)
    piapi = FakePiAPI(files={"https://cdn.piapi.ai/out.mp4": output}, assets={"asset-1": "Active"})
    return Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance": SeedanceGenerator(piapi)},
        faces=FaceLibrary(settings.root),
    )


@pytest.fixture
def piapi_settings(tmp_path):
    return Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "t", "PIAPI_API_KEY": "k"})


def test_seedance_draft_shows_the_prompt_and_the_price_then_asks(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=lambda q: asked.append(q) or False,
        services_factory=seedance_services,
    )
    assert code == 1 and asked
    assert "hello there" in out.text and f"charswap prompt {job.id}" in out.text
    assert "Seedance draft (480p)" in out.text and "~$0.9900" in out.text  # 4 s: 0.66 + 0.33


def test_seedance_generate_needs_the_piapi_key(settings, video, tmp_path):
    job = _approved_seedance_job(settings, video, tmp_path)
    code, out = run(["generate", job.id, "--draft"], settings, services_factory=seedance_services)
    assert code == 2 and "PIAPI_API_KEY" in out.err.text


def test_resolution_only_with_final(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    code, out = run(["generate", job.id, "--draft", "--resolution", "720p"], piapi_settings)
    assert code == 2 and "only for --final" in out.err.text


def test_after_the_draft_both_final_prices_are_shown(piapi_settings, video, tmp_path, monkeypatch):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    assert code == 0 and job.stage_done("draft")
    assert "1080p upgrade (~$5.28)" in out.text and "--resolution 720p" in out.text
    assert "(~$2.31)" in out.text


def test_seedance_720p_final_warns_it_is_a_fresh_run(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services)
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "720p"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and "fresh run" in out.text and "Seedance final (720p)" in out.text


def test_prompt_writes_an_editable_file(settings, video, tmp_path):
    job = _approved_seedance_job(settings, video, tmp_path)
    code, out = run(["prompt", job.id], settings)
    assert code == 0 and job.path("prompt.txt").exists()
    assert "hello there" in out.text and "prompt.txt" in out.text
    job.path("prompt.txt").write_text("Put @image1 into @video1.")
    assert "Put @image1 into @video1." in run(["prompt", job.id], settings)[1].text


def test_prompt_waits_for_preparation(settings, video, tmp_path):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    code, out = run(["prompt", job.id], settings)
    assert code == 2 and "charswap resume" in out.err.text and not job.path("prompt.txt").exists()


def test_doctor_reports_piapi_hosting_and_faces(settings, capsys):
    code, out = run(["doctor"], settings)
    assert "hosting: PiAPI temporary upload" in out.text and "saved faces: 0" in out.text


def test_real_services_include_seedance_when_the_key_is_set(piapi_settings):
    from character_swap.providers.generators.seedance_piapi import SeedanceGenerator

    services = cli.build_services(piapi_settings)
    assert isinstance(services.generators["seedance"], SeedanceGenerator)
    assert services.faces is not None
```

Delete `test_seedance_jobs_cannot_generate_yet` from `tests/test_cli.py` (if Task 10 hasn't already). In `tests/test_config.py`, delete `test_hosting_mode_is_r2_only_when_all_r2_keys_present`.

Update these existing CLI tests:
- `_fake_generate`: its fake must accept the new keyword. Give it `final_resolution=""` (or `**_kw`).
- `test_doctor_reports_checks`: assert `"hosting: PiAPI temporary upload"` instead of `"hosting: tmpfiles"`.
- `test_prompt_errors_are_friendly`: the new `_prompt` refuses unfinished jobs, and `build_prompt` is no longer imported by `cli`. Rewrite it so it:
  1. marks every prep stage of the job done;
  2. writes a `prompt.txt` longer than `prompts.MAX_PROMPT` characters (including `@video1` and `@image1`);
  3. asserts exit code 2 with "limit" in stderr.

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_cli.py -q --no-cov`. Expected: failures for `--resolution`, the prompt file, doctor and build_services.

- [ ] **Step 3: `config.py`.** Delete `R2_KEYS` and the `hosting_mode` property. Update `WHERE_TO_GET["PIAPI_API_KEY"]` to `"https://piapi.ai (Workspace > API Keys; saved faces need the Creator plan)"`.

- [ ] **Step 4: `.env.example`.** Replace the PiAPI and R2 lines with:

```
# Seedance generator: https://piapi.ai (Workspace > API Keys). Saved faces and file
# uploads need the Creator plan or higher.
PIAPI_API_KEY=
```

- [ ] **Step 5: `cli.py`.**

1. Imports: add `final_resolution`, `generation_label`, `write_prompt_file` and `generation_prompt` from `pipeline`. Add `from character_swap.providers.piapi import unconfirmed_message as piapi_unconfirmed`, and rename the Replicate import to `unconfirmed_message as replicate_unconfirmed`.

2. Add these module constants:

```python
KEYS = {"wan": "REPLICATE_API_TOKEN", "seedance": "PIAPI_API_KEY"}
UNCONFIRMED = {"wan": replicate_unconfirmed, "seedance": piapi_unconfirmed}
```

3. `build_services`:

```python
def build_services(settings: Settings) -> Services:
    from character_swap.faces import FaceLibrary
    from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
    from character_swap.providers.generators.wan_replicate import WanGenerator
    from character_swap.providers.piapi import PiAPIClient
    from character_swap.providers.replicate import ReplicateClient
    from character_swap.stages.face_mesh import MediaPipeDetector, ensure_model

    model_dir = settings.root / "models"
    replicate = ReplicateClient(
        settings.require("REPLICATE_API_TOKEN"),
        on_wait=lambda *wait: _stderr(rate_limit_message(*wait)),
    )
    generators = {"wan": WanGenerator(replicate)}
    if settings.get("PIAPI_API_KEY"):
        generators["seedance"] = SeedanceGenerator(PiAPIClient(settings.get("PIAPI_API_KEY")))
    return Services(
        replicate=replicate,
        face_detector=lambda: MediaPipeDetector(ensure_model(model_dir)),  # pragma: no cover
        hf_token=settings.get("HF_TOKEN") or None,
        generators=generators,
        faces=FaceLibrary(settings.root),
    )
```

4. Parser: help for `generate` becomes `"make the 480p draft or the final (paid; asks first)"`. `--final` help becomes `"Wan: 720p, same seed. Seedance: 1080p upgrade of the draft, or --resolution 720p"`. Add:

```python
    gen.add_argument(
        "--resolution",
        choices=["1080p", "720p"],
        help="Seedance final: 1080p (default, upgrades the draft) or 720p (cheaper fresh run)",
    )
```

  `prompt` help becomes `"show and save the editable Seedance prompt (prompt.txt)"`.

5. `_prompt`:

```python
def _prompt(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    if job.generator != "seedance":
        raise JobError("Prompts are only for Seedance jobs; Wan takes no text prompt.")
    if not all(job.stage_done(name) for name in MODES[job.mode].prep_stages):
        raise JobError(
            f"Finish preparing first (the prompt uses the transcript and the scene): "
            f"charswap resume {job.id}"
        )
    path = write_prompt_file(job)
    env.out(generation_prompt(job, "draft"))
    env.out(
        f"\nSaved in {path}. Edit that file to change what Seedance is told; the draft sends "
        "it exactly as it is."
    )
    return 0
```

  (Import `MODES` from `character_swap.modes`.)

6. `_generate`: apply these changes to today's function, keeping the rest as it is:
  - First line after `stage = ...`:

```python
    if args.resolution and stage == "draft":
        raise JobError("--resolution is only for --final (drafts are always 480p).")
```

  - Right after `check_can_generate(job, stage)`:

```python
    resolution = final_resolution(job, args.resolution or "") if stage == "final" else ""
```

  - `settings.require("REPLICATE_API_TOKEN")` becomes `settings.require(KEYS[job.generator])`.
  - `env.err(unconfirmed_message(...))` becomes `env.err(UNCONFIRMED[job.generator](job.path(generation_files(stage)[0])))`.
  - The estimate block becomes:

```python
        request = build_request(job, faces=services.faces, stage=stage, final_resolution=resolution)
        estimate = generator_for(job, services).estimate(request, stage)
        if stage == "draft" and request.prompt:
            env.out(
                f"Prompt sent to Seedance (change it with `charswap prompt {job.id}`):\n"
                f"{request.prompt}\n"
            )
        env.out(f"{generation_label(job, stage, resolution)} (paid):\n" + estimate.describe())
```

  - After the existing `Only continue if you watched…` line:

```python
        if job.generator == "seedance" and stage == "final" and resolution == "720p":
            env.out(
                "720p is a fresh run with the same prompt and inputs, so it can differ a little "
                "from the draft. 1080p (the default) upgrades the exact draft."
            )
```

  - Pass `final_resolution=resolution` to `generate_stage(...)`.
  - Replace the after-draft hint with `env.out(_after_draft_hint(job, services))`:

```python
def _after_draft_hint(job: Job, services: Services) -> str:
    if job.generator != "seedance":
        return f"Happy with it? Run `charswap generate {job.id} --final`."
    generator = generator_for(job, services)
    price = {
        res: generator.estimate(
            build_request(job, faces=services.faces, stage="final", final_resolution=res), "final"
        ).total
        for res in ("1080p", "720p")
    }
    return (
        f"Happy with it? `charswap generate {job.id} --final` makes the 1080p upgrade "
        f"(~${price['1080p']:.2f}); add `--resolution 720p` for a cheaper fresh 720p run "
        f"(~${price['720p']:.2f})."
    )
```

7. `_doctor`: replace the `hosting` line with:

```python
    out("hosting: PiAPI temporary upload (Seedance; needs the PiAPI Creator plan)")
    try:
        out(f"saved faces: {len(FaceLibrary(settings.root).all())}")
    except CharSwapError as error:
        out(f"saved faces: unreadable ({error})")
```

8. `EPILOG` gains `charswap prepare clip.mp4 --mode depth --face frank` and `charswap generate <id> --final --resolution 720p`.

- [ ] **Step 6: Run tests.** `uv run pytest tests/test_cli.py tests/test_config.py -q --no-cov`, then the full suite `uv run pytest`. Expected: PASS, coverage ≥ 85 %. The two expected prices for a 4 s clip are 1080p `4 × (0.88 + 0.44) = 5.28` and 720p `4 × (0.385 + 0.1925) = 2.31`.

- [ ] **Step 7: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/cli.py src/character_swap/config.py .env.example tests/test_cli.py tests/test_config.py
git commit -m "feat: charswap generate for Seedance (--resolution), prompt.txt, PiAPI key, doctor"
```

---

### Task 12: Viewer: Seedance Draft and Final sections

**Files:** Modify `src/character_swap/web/view_model.py`, `tests/test_view_model.py`, `tests/viewer_jobs.py`, `tests/test_web_app.py` (only if a test there lists section ids).

Seedance jobs already show the Draft and Final sections (since Task 10). This task gives them three differences:
- the explanation text is Seedance's;
- cost rows say "PiAPI", with no Replicate link (PiAPI has no public page per task);
- the Draft section shows the prompt that was sent, and the Final section shows its resolution.

- [ ] **Step 1: Failing tests.** Append to `tests/test_view_model.py`:

```python
def test_seedance_draft_shows_the_prompt_and_piapi_costs(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    finish_stage(job, media, "draft")
    job.mark_stage("draft", fingerprint=fingerprint(job), prompt="Put @image1 into @video1.")
    job.append_cost({"step": "draft", "usd": 0.99, "task_id": "pred-draft", "provider": "piapi"})
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert "Seedance" in draft.why and not draft.links
    assert {t.label: t.value for t in draft.texts}["Prompt sent"] == "Put @image1 into @video1."
    row = next(r for r in view.costs.rows if r.step.startswith("Draft"))
    assert row.provider == "PiAPI" and row.link is None and row.usd == 0.99


def test_seedance_final_shows_its_resolution(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint=fingerprint(job), resolution="720p")
    view = vm.job_view(job, runs_dir)
    texts = {t.label: t.value for t in sections(view)["final"].texts}
    assert texts["Resolution"] == "720p"
    assert any(r.step == "Final (720p)" for r in view.costs.rows)


def test_wan_sections_keep_their_replicate_wording(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    assert "Replicate" in sections(vm.job_view(job, runs_dir))["draft"].why
```

(`fingerprint` is imported from `character_swap.review`; add the import if the file doesn't have it.)

- [ ] **Step 2: Run them and watch them fail.** `uv run pytest tests/test_view_model.py -q --no-cov`.

- [ ] **Step 3: `view_model.py`.**

1. `GENERATING_MODES` now holds every mode (Task 10 already updated `SECTION_IDS`).

2. Section ids stay unique (`tests/test_web_app.py::test_every_section_title_is_unique`), so **keep one Draft and one Final section** and give them Seedance wording through two new optional `Section` fields:

```python
    seedance_title: str = ""  # used instead of title/why on Seedance jobs (draft/final)
    seedance_why: str = ""
```

  On the existing `draft` section set:
  - `seedance_why=("A cheaper 480p test run on PiAPI. Seedance redraws the disguised people as your characters, guided by the prompt, then your original soundtrack is put back unchanged.")`

  On the existing `final` section set:
  - `seedance_title="Final"`
  - `seedance_why=("1080p upgrades the exact draft you watched. 720p is a cheaper fresh run with the same prompt, so it can differ a little. The original soundtrack is restored again.")`

  Both sections keep `modes=GENERATING_MODES`, which is every mode since Task 10. In `_section_view`:

```python
    seedance = state.get("generator") == "seedance"
    title = (section.seedance_title or section.title) if seedance else section.title
    why = (section.seedance_why or section.why) if seedance else section.why
```

  Pass `title=title, why=why` to `SectionView`. In `_links`, return `[]` only when `state.get("generator") == "seedance" and section.id in GENERATION_STAGES` (PiAPI has no public page per task). Seedance **preparation** sections keep their Replicate links, and so does Wan.

3. Character asset: if `_resolve` maps `"@character"` only for Wan, make it return `state.get("character_primary")` for every generator.

4. Generation texts. In `_section_view`, after the existing `elif section.id in stale:` branch, add:

```python
        if section.id in ("draft", "final") and state.get("generator") == "seedance":
            texts = [*texts, *_seedance_texts(state, section.id)]
```

  with:

```python
def _seedance_texts(state: dict[str, Any], stage: str) -> list[TextItem]:
    entry = state.get("stages", {}).get(stage)
    if not isinstance(entry, dict):
        return []
    if stage == "draft" and isinstance(entry.get("prompt"), str):
        return [TextItem("Prompt sent", entry["prompt"])]
    if stage == "final" and isinstance(entry.get("resolution"), str):
        return [TextItem("Resolution", entry["resolution"])]
    return []
```

5. Cost rows. In `cost_rows`, the generation loop becomes:

```python
    seedance = state.get("generator") == "seedance"
    for stage in GENERATION_STAGES:  # priced by length: use the estimate logged at creation
        if stage_status(state, stage) != "done":
            continue
        rows.append(
            CostRow(
                _generation_name(state, stage),
                "PiAPI" if seedance else "Replicate",
                _generation_cost(job, state, stage),
                None if seedance else prediction_url(job, PAID_RECORD[stage]),
            )
        )
```

with:

```python
def _generation_name(state: dict[str, Any], stage: str) -> str:
    if stage == "final":
        entry = state.get("stages", {}).get("final")
        resolution = entry.get("resolution") if isinstance(entry, dict) else None
        return f"Final ({resolution or '720p'})"
    return STEP_NAMES[stage]
```

- [ ] **Step 4: Update two existing tests, then run.**
  - `tests/test_web_app.py::test_job_page_has_every_section_and_anchor` checks that each section's `why` is on the page. For `mode != "direct"`, expect `(section.seedance_why or section.why)`, and the same for the title with `seedance_title`.
  - `tests/test_view_model.py::test_every_section_explains_why`: also check every non-empty `seedance_why` (no em dash, like `why`).

  Run `uv run pytest tests/test_view_model.py tests/test_web_app.py -q --no-cov`. Expected: PASS.

- [ ] **Step 5: Look at it.** Start the viewer with `uv run charswap view --no-browser`. Tell Frank it is running and that you'll stop it after the check. Open a Seedance demo job (create one with `tests/viewer_jobs.py` helpers in a scratch `CHARSWAP_RUNS_DIR` if none exists). Check:
  - the Draft and Final sections render;
  - the prompt is shown;
  - the costs say PiAPI;
  - nothing overflows at phone width.

  Then stop the viewer.

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests && uv run ruff check --fix src tests
git add src/character_swap/web/view_model.py tests/test_view_model.py tests/viewer_jobs.py tests/test_web_app.py
git commit -m "feat: viewer shows Seedance draft/final with the prompt and PiAPI costs"
```

---

### Task 13: Full verification and docs

**Files:** Modify `README.md`, `CLAUDE.md`, `activity.md`, `docs/scratchpad.md`.

- [ ] **Step 1: Full checks.** Run `uv run ruff format --check src tests && uv run ruff check src tests && uv run pytest`. Expected: all pass, coverage ≥ 85 %. Record the test count and the coverage figure.

- [ ] **Step 2: Grep for leftovers.**
  - `grep -rn "R2_\|hosting_mode\|tmpfiles" src tests .env.example` should find nothing, except historical mentions in docs.
  - `grep -rn "next version" src tests` should find nothing.

- [ ] **Step 3: README.** After "Try it with Wan", add:

````markdown
## Try it with Seedance (best quality, real faces, on PiAPI)

You need a PiAPI account on the **Creator plan or higher** (saved faces and uploads), with
`PIAPI_API_KEY` in `.env`, plus `REPLICATE_API_TOKEN` for preparation.

```bash
uv run charswap face add me me.jpg                         # once, free: PiAPI checks the photo
uv run charswap prepare clip.mp4 --mode depth --face me    # ~$0.06: the depth disguise
# watch it in `uv run charswap view`, then approve with the command shown there
uv run charswap prompt <id>                                # optional: edit prompt.txt
uv run charswap generate <id> --draft                      # 480p, shows the price, asks first
uv run charswap generate <id> --final                      # 1080p upgrade of that draft
uv run charswap generate <id> --final --resolution 720p    # or: cheaper fresh 720p run
```

Prices for a 12 s clip, estimated: draft about $3, 1080p final about $16, 720p final about
$7 (PiAPI also bills the input video). Test with 4–5 s clips first.

Two people in the clip: `--subject` tells SAM what to cut out (default "person" = everyone),
and `--face2` / `--character2` gives the second person their own character. Say in the
prompt who becomes @image1 and who becomes @image2.

Clips are trimmed to whole seconds (PiAPI works in whole seconds); the original sound is
cut to match. `--character IMAGE` is only for characters that are not real people (PiAPI
rejects real faces sent that way); use a saved face for a real person. Only add faces you
have the right to use.
````

- [ ] **Step 4: CLAUDE.md.**
  - The intro line stays.
  - Add these commands: `Faces (free): uv run charswap face add|list|check|renew` and `Prompt (Seedance): uv run charswap prompt <job>`.
  - Under Hard rules, change the first bullet to: "Never auto-retry a paid request. Only exception: a Replicate create rejected with 429 (nothing was created) is retried after re-claiming the exclusive "creating" marker. PiAPI 429s are not retried. Persist provider IDs before waiting."
  - Add `faces/` to the list of things never committed (Layout or Hard rules).

- [ ] **Step 5: Journal and checklist.**
  - `activity.md`: add a Session entry. Cover what was built, decisions Q1–Q12 in one line each with their why, and the lessons.
  - `docs/scratchpad.md`:
    - tick the Plan 2b line with the test count;
    - tick the "(2b) Decide whole-second clip length" item;
    - tick the 2b items under "Deferred from Plan 2a" (hosting is replaced by the PiAPI upload; the 2.5 generator, face library and prompt review are done);
    - add a line `- [ ] Live Seedance test (Task 14, needs Frank's OK + PiAPI Creator plan)`;
    - add the new "Deferred from Plan 2b" items below.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md activity.md docs/scratchpad.md
git commit -m "docs: Seedance usage, Plan 2b journal and checklist"
```

- [ ] **Step 7: Finish the branch.** Use superpowers:finishing-a-development-branch. Frank's usual choice is merging to `main` and pushing, but he still gives the OK each time.

---

### Task 14: Live Seedance test (Frank; paid; needs his explicit OK)

Not run by subagents. Prerequisites:
- Frank has a PiAPI Creator plan and put `PIAPI_API_KEY` in `.env` himself.
- A **4–5 s** one-person clip is in `inputs/`; the existing `inputs/talking-head-12s.mp4` can be trimmed to 5 s with ffmpeg.
- A clear, front-facing, head-and-shoulders photo of Frank is in `inputs/`.

1. `uv run charswap doctor`: PIAPI_API_KEY should show as set.
2. `uv run charswap face add frank inputs/<photo>` (free). Record how long the check took and the final status. If it shows `Failed`, record PiAPI's reason and stop.
3. `uv run charswap prepare inputs/<clip-5s>.mp4 --mode depth --face frank` (about $0.06, asks first). Watch it in the viewer and approve.
4. `uv run charswap prompt <id>`: read the prompt together with Frank.
5. `uv run charswap generate <id> --draft` (about $1.24 for 5 s, asks first). Record:
   - did the `edit` task type work? If PiAPI fails the task because of the task type, switch `TASK_KIND` to `"reference"`. That needs `duration` (the whole seconds) and an `aspect_ratio` that is not "adaptive", so it is a small follow-up task. Record the error text.
   - the run time and the queue wait;
   - the output size, fps and length;
   - whether the sound lines up;
   - the real charge on PiAPI's billing page, compared with the estimate;
   - Frank's verdict on the likeness compared with Wan.
6. Only if Frank likes the draft and says yes: `--final --resolution 720p` (about $2.89), or `--final` for 1080p (about $6.60). Record the same facts.
7. Update `docs/research/2026-10-22-provider-api-facts.md` (real behaviour, prices) and `costs.py` if the prices differ. Then log the session.

## Deferred from Plan 2b

| Item | Where |
|---|---|
| `reference` task type (if the live test shows `edit` is wrong) | follow-up task after Task 14 |
| BytePlus ModelArk generator (ByteDance direct, cheaper; needs Frank's verified business account + real-person access) | own small plan once BytePlus confirms |
| Use `seed` / `audio` if PiAPI documents them for 2.5 | after the live test |
| Seedance 2.0 fallback (15 s, no promote) | only if 2.5 refuses faces |
| `build_services` without `REPLICATE_API_TOKEN` (a Seedance-only machine) | Plan 3 (`services.py`) |
| Delete PiAPI temporary uploads / assets from the CLI (`charswap face remove`) | Plan 3 |
| Viewer: face library panel, prompt editing in the browser | Plan 3 UI |
| PiAPI task cancel | Backlog (with `charswap cancel`) |
