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

from character_swap.costs import estimate_stages
from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError
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
LAY_BACK = "Lay back (SAM 3)"  # a scene swap's SAM 3 run on the AI result, per draft/final
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
class JobSummary:
    id: str
    short_id: str
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
    charges (every charge stays in state.json's cost log). When nothing matches, only
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
        if stage == "describe" and state["stages"]["describe"].get("source") == "you":
            continue  # you wrote the setting: nothing was paid
        if stage == "depth" and state.get("depth_provider") == "huggingface":
            rows.append(CostRow(STEP_NAMES[stage], "Hugging Face · free", 0.0, None))
            continue
        usd = estimate_stages([stage], ESTIMATE_SECONDS).total
        rows.append(
            CostRow(STEP_NAMES[stage], "Replicate", usd, prediction_url(job, PAID_RECORD[stage]))
        )
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
        return JobSummary(job.id, job.id[:8], 0.0, _mtime_iso(folder), tilde(str(exc)))
    return JobSummary(
        id=job.id,
        short_id=job.id[:8],
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
