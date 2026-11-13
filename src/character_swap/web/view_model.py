"""Everything the job viewer shows, computed from a job folder. Pure: no HTTP, no templates."""

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
from character_swap.modes import GENERATION_STAGES, MODES, PROVIDER_NAMES
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
    PAID_STAGES,
    PREP_STAGES_BY_MODE,
    generator_key,
)
from character_swap.review import ReviewError, fingerprint

# Stage order per mode lives in pipeline.PREP_STAGES_BY_MODE (what prepare() runs).
STAGES_BY_MODE = PREP_STAGES_BY_MODE
SEEDANCE_PREP = ("depth", "face_mesh", "depth_mesh")
ALL_MODES = (*SEEDANCE_PREP, "direct")
# Modes whose draft/final can be generated (every mode since Plan 2b).
GENERATING_MODES = tuple(name for name, mode in MODES.items() if mode.generates)
ICONS = {"done": "✓", "pending": "◻", "running": "⟳", "failed": "✕"}
STATUS_WORDS = {"done": "done", "pending": "waiting", "running": "running", "failed": "failed"}
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
    stage: str  # the paid stage that wrote it


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    why: str
    modes: tuple[str, ...]
    stages: tuple[str, ...]
    assets: tuple[Asset, ...] = ()
    records: tuple[Record, ...] = ()
    retry: str = "resume {id}"  # after `charswap`; {id} = job id (one form: generate <id> --draft)
    seedance_title: str = ""  # used instead of title/why on Seedance jobs (draft/final)
    seedance_why: str = ""
    wavespeed_why: str = ""  # used instead of seedance_why on WaveSpeed jobs


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
        records=(Record(ON_REPLICATE, "demucs-prediction.json", "separate"),),
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
            Record("Transcript on Replicate ↗", "whisper-prediction.json", "transcribe"),
            Record("Scene on Replicate ↗", "describe-prediction.json", "describe"),
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
        records=(Record(ON_REPLICATE, "depth-prediction.json", "depth"),),
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
        records=(Record(ON_REPLICATE, "sam/sam-prediction.json", "masks"),),
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
        seedance_why=(
            "A cheaper 480p test run on PiAPI. Seedance redraws the disguised people as your "
            "characters, guided by the prompt, then your original soundtrack is put back "
            "unchanged."
        ),
        wavespeed_why=(
            "A cheaper 480p test run on WaveSpeed. Seedance redraws the disguised person as "
            "your character, guided by the prompt, then your original soundtrack is put back "
            "unchanged."
        ),
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
        seedance_title="Final",
        seedance_why=(
            "1080p upgrades the exact draft you watched. 720p is a cheaper fresh run with the "
            "same prompt, so it can differ a little. The original soundtrack is restored again."
        ),
        wavespeed_why=(
            "A new take at 720p (or 1080p if you choose it) with the same prompt and inputs, "
            "so it will look a little different from the draft. The original soundtrack is "
            "restored again."
        ),
    ),
)


# stage -> saved prediction record, derived from SECTIONS so links and cost rows cannot drift
PAID_RECORD: dict[str, str] = {
    record.stage: record.name for section in SECTIONS for record in section.records
}
RGB_REPAIR_RECORD = "rgb-repair/sam-prediction.json"


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
    multiline: bool = False  # keep the value's line breaks (a prompt.txt)


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
    stopped: bool = False  # Ctrl-C: show the resume hint instead of an error
    retry: str = ""  # the full command to show, e.g. `charswap generate <id> --draft`


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
    if state.get("generator") == "seedance" and section.id in GENERATION_STAGES:
        return []  # Seedance providers (PiAPI, WaveSpeed) have no public page per task
    if section.id == "mask" and state.get("mask_source") == "rgb":
        pairs = [
            ("First attempt ↗", PAID_RECORD["masks"]),
            ("Repair ↗", RGB_REPAIR_RECORD),
        ]
    else:
        pairs = [(record.label, record.name) for record in section.records]
    links = []
    for label, record in pairs:
        url = prediction_url(job, record)
        if url:
            links.append(Link(label, url))
    return links


INTERRUPTED = "interrupted"  # what pipeline._tracked records on Ctrl-C


def _error(state: dict[str, Any], section: Section) -> str | None:
    for name in section.stages:
        entry = state["stages"].get(name)
        if isinstance(entry, dict) and entry.get("status") == "failed":
            return tilde(str(entry.get("error") or "This step failed."))
    return None


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


# ---- the two pages -------------------------------------------------------------
def sections_for(mode: str) -> tuple[Section, ...]:
    return tuple(section for section in SECTIONS if mode in section.modes)


def overall_status(
    state_progress: dict[str, Any], decision: dict[str, Any], *, stale: bool = False
) -> str:
    generation = state_progress["generation"]
    if "failed" in state_progress["stages"].values() or "failed" in generation.values():
        return "Failed"
    if "running" in generation.values():
        return "Generating"
    if state_progress["active"]:
        return "Preparing"
    if stale:  # a draft/final made from inputs that are no longer approved is never "done"
        return "Out of date"
    if generation["final"] == "done":
        return "Finished"
    if generation["draft"] == "done":
        return "Draft ready"
    return {"approved": "Approved", "rejected": "Rejected"}.get(
        str(decision.get("status")), "Ready for review"
    )


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


def _is_seedance(key: str) -> bool:
    return key.startswith("seedance")


def section_text(section: Section, key: str) -> tuple[str, str]:
    """The section's (title, why) for a job's generator_key."""
    if not _is_seedance(key):
        return section.title, section.why
    why = section.seedance_why or section.why
    if key == "seedance:wavespeed" and section.wavespeed_why:
        why = section.wavespeed_why
    return section.seedance_title or section.title, why


def _stale_texts(job: Job, decision: dict[str, Any]) -> list[TextItem]:
    note = "Made from inputs that are no longer approved."
    if decision.get("status") != "approved":
        note += " Review the new files in the Review section above, then run:"
    return [
        TextItem("Out of date", note),
        TextItem("Make a new draft", f"charswap generate {job.id} --draft", code=True),
    ]


def _section_view(
    job: Job,
    state: dict[str, Any],
    section: Section,
    number: int,
    decision: dict[str, Any],
    stale: frozenset[str],
    key: str,
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
        elif section.id in stale:
            texts = _stale_texts(job, decision)
        if section.id in GENERATION_STAGES and _is_seedance(key):
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


def job_view(job: Job, runs_dir: Path) -> JobView:
    state = read_state(job)
    mode = state["mode"]
    decision = review_status(job)
    stale = stale_stages(job, state) if MODES[mode].generates else frozenset()
    key = generator_key(job, state)
    sections = tuple(
        _section_view(job, state, section, number, decision, stale, key)
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
    if stale and state_progress["poll"] == "off":
        state_progress["poll"] = "slow"  # a new draft (from the terminal) still shows up live
    return JobView(
        id=job.id,
        short_id=job.id[:8],
        generator=str(state.get("generator", "?")),
        mode=mode,
        status=overall_status(state_progress, decision, stale=bool(stale)),
        cost_usd=costs.completed_usd,
        nav=nav,
        sections=sections,
        costs=costs,
        progress=state_progress,
    )


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")


def _summarize(runs_dir: Path, folder: Path) -> JobSummary | None:
    try:
        job = Job.load(runs_dir, folder.name)
    except JobError:
        return None  # not a job folder (spend log, stray files, half-created jobs)
    try:
        state = read_state(job)
    except ViewError as exc:
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
