"""Run the preparation stages for a job's mode. Every stage is skipped if already done."""

from __future__ import annotations

import shutil
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from character_swap.jobs import Job
from character_swap.media.audio import embed_samples, read_audio
from character_swap.media.composite import clean_border_fragments, composite_depth_subject
from character_swap.media.video import normalize
from character_swap.review import clear as clear_review
from character_swap.stages import StageError
from character_swap.stages.depth import depth_huggingface, depth_replicate
from character_swap.stages.describe import describe_scene
from character_swap.stages.face_mesh import FaceDetector, track_and_draw
from character_swap.stages.masks import segment
from character_swap.stages.pitch import pitch_vocals
from character_swap.stages.separate import separate_vocals
from character_swap.stages.transcribe import transcribe

# (min_seconds, max_seconds) per generator; see docs/research/2026-10-22-provider-api-facts.md.
GENERATOR_LIMITS = {"seedance": (4, 30), "wan": (1, 30)}
Status = Callable[[str], None]
PAID_STAGES = {
    "depth": ["separate", "transcribe", "describe", "depth", "masks"],
    "depth_mesh": ["separate", "transcribe", "describe", "depth", "masks"],
    "face_mesh": ["separate", "transcribe", "describe"],
    "direct": [],
}

# Stage order per mode, exactly as prepare() runs them (the viewer and review gate read this).
# test_viewer_stage_table_matches_what_prepare_runs keeps it in sync.
PREP_STAGES_BY_MODE: dict[str, tuple[str, ...]] = {
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


def remaining_paid_stages(job: Job) -> list[str]:
    """Paid stages this job's mode needs that are not finished yet."""
    return [name for name in PAID_STAGES[job.mode] if not job.stage_done(name)]


class ReplicateLike(Protocol):
    def upload(self, path: Path) -> str: ...

    def latest_version(self, model: str): ...

    def run(
        self, *, input: dict, record: Path, version: str | None = None, model: str | None = None
    ): ...

    def download(self, url: str, dest: Path) -> Path: ...


@dataclass
class Services:
    replicate: ReplicateLike
    face_detector: Callable[[], FaceDetector]
    rubberband: str = "rubberband"
    hf_token: str | None = None


def _notify(status: Status, message: str) -> None:
    """A UI bug in the callback must never abort a (possibly paid) stage."""
    try:
        status(message)
    except Exception as exc:
        warnings.warn(f"status callback failed: {exc}", stacklevel=2)


@contextmanager
def _wrap(label: str) -> Iterator[None]:
    """One error type for callers: StageError passes through, anything else is wrapped."""
    try:
        yield
    except StageError:
        raise
    except Exception as exc:
        raise StageError(f"{label} failed: {exc}") from exc


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
    except BaseException as exc:
        error = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc) or type(exc).__name__
        )
        try:
            job.mark_stage(name, "failed", started_at=started_at, error=error)
        except Exception as mark_exc:
            # Best-effort: the primary error matters more than recording it, so never let a
            # failing state write (disk full, ...) replace the exception being raised.
            warnings.warn(f"Could not record failure of stage {name}: {mark_exc}", stacklevel=2)
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


def prepare(job: Job, services: Services, status: Status = print) -> None:
    """Run every unfinished stage. `status` runs on the caller's (worker) thread; keep it quick."""
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
        _notify(status, "Ready for review")
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
    if job.mode in ("face_mesh", "depth_mesh"):
        _stage(job, "face_mesh", "Tracking the face mesh", lambda: _mesh(job, services), status)
    _stage(job, "embed", "Embedding pitched vocals", lambda: _embed(job), status)
    _notify(status, "Ready for review")


def repair_with_rgb(job: Job, services: Services, status: Status = print) -> None:
    """Re-segment on the original colors (depth can lose detail), keep depth colors, re-review."""
    if job.mode not in ("depth", "depth_mesh"):
        raise StageError("This mode has no SAM mask to repair. Prepare the face tracking again.")
    redo = ["composite", "embed", *(["face_mesh"] if job.mode == "depth_mesh" else [])]
    job.reset_stages(*redo)  # a crash below leaves these un-done so prepare() heals the job
    _notify(status, "Re-running SAM 3 on the original colors")
    repair_dir = job.path("rgb-repair")
    if repair_dir.exists():
        # Deliberate: every repair is a fresh, confirmed, paid SAM run (~$0.002).
        shutil.rmtree(repair_dir)
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
    clear_review(job)
    job.update(mask_source="rgb")
    _notify(status, "Repaired. Review the whole clip again.")


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
