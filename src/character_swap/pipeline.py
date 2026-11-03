"""Run the preparation stages for a job's mode. Every stage is skipped if already done."""

from __future__ import annotations

import json
import os
import shutil
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import av

from character_swap.costs import record_spend
from character_swap.jobs import Job
from character_swap.media.audio import FIT_LIMIT, add_original_soundtrack, embed_samples, read_audio
from character_swap.media.composite import clean_border_fragments, composite_depth_subject
from character_swap.media.video import normalize, stream_seconds
from character_swap.modes import GENERATION_STAGES, GENERATOR_LIMITS, MODES
from character_swap.providers.generators.base import CharacterRef, GenerationRequest, Generator
from character_swap.review import ReviewError, fingerprint, require_approved
from character_swap.review import clear as clear_review
from character_swap.stages import StageError
from character_swap.stages.depth import depth_huggingface, depth_replicate
from character_swap.stages.describe import describe_scene
from character_swap.stages.face_mesh import FaceDetector, track_and_draw
from character_swap.stages.masks import segment
from character_swap.stages.pitch import pitch_vocals
from character_swap.stages.separate import separate_vocals
from character_swap.stages.transcribe import transcribe

Status = Callable[[str], None]
# Derived from modes.MODES (the one table); kept under these names for callers and tests.
PAID_STAGES: dict[str, list[str]] = {name: list(mode.paid_stages) for name, mode in MODES.items()}
PREP_STAGES_BY_MODE: dict[str, tuple[str, ...]] = {
    name: mode.prep_stages for name, mode in MODES.items()
}
GENERATION_LABELS = {"draft": "Generating the 480p draft", "final": "Generating the 720p final"}


def remaining_paid_stages(job: Job) -> list[str]:
    """Paid stages this job's mode needs that are not finished yet."""
    return [name for name in PAID_STAGES[job.mode] if not job.stage_done(name)]


def clip_seconds(job: Job) -> float:
    """Length of the normalized clip: saved by normalize, measured for older jobs."""
    facts = job.state.get("video")
    if isinstance(facts, dict) and isinstance(facts.get("seconds"), int | float):
        return float(facts["seconds"])
    return stream_seconds(job.path("original.mp4"), "video")


class ReplicateLike(Protocol):
    def upload(self, path: Path) -> str: ...

    def latest_version(self, model: str): ...

    def run(
        self,
        *,
        input: dict,
        record: Path,
        version: str | None = None,
        model: str | None = None,
        on_created: Callable[[dict], None] | None = None,
    ): ...

    def download(self, url: str, dest: Path) -> Path: ...


@dataclass
class Services:
    replicate: ReplicateLike
    face_detector: Callable[[], FaceDetector]
    rubberband: str = "rubberband"
    hf_token: str | None = None
    generators: dict[str, Generator] = field(default_factory=dict)


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
def _tracked(job: Job, name: str, **info: object) -> Iterator[None]:
    """Record running -> done/failed in state.json so viewers can show live progress.

    BaseException on purpose: Ctrl-C records "interrupted" instead of leaving the stage
    "running". Only "done" counts for stage_done(), so resume re-runs running/failed stages.
    """
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    job.mark_stage(name, "running", started_at=started_at, **info)
    try:
        yield
    except BaseException as exc:
        error = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc) or type(exc).__name__
        )
        try:
            job.mark_stage(name, "failed", started_at=started_at, error=error, **info)
        except Exception as mark_exc:
            # Best-effort: the primary error matters more than recording it, so never let a
            # failing state write (disk full, ...) replace the exception being raised.
            warnings.warn(f"Could not record failure of stage {name}: {mark_exc}", stacklevel=2)
        raise
    job.mark_stage(name, started_at=started_at, **info)


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
        lambda: job.update(
            video=normalize(
                job.path("upload.mp4"), job.path("original.mp4"), min_seconds=low, max_seconds=high
            )
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
    if MODES[job.mode].uses_mesh:
        _stage(job, "face_mesh", "Tracking the face mesh", lambda: _mesh(job, services), status)
    _stage(job, "embed", "Embedding pitched vocals", lambda: _embed(job), status)
    _notify(status, "Ready for review")


def repair_with_rgb(job: Job, services: Services, status: Status = print) -> None:
    """Re-segment on the original colors (depth can lose detail), keep depth colors, re-review."""
    if not MODES[job.mode].uses_depth:
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
    if stage == "final":
        if not job.stage_done("draft"):
            raise StageError(f"Make and watch the draft first: charswap generate {job.id} --draft")
        # The final must use exactly the inputs the watched draft was made from. A draft
        # without a stored fingerprint (older jobs) cannot prove that, so it is refused too.
        if job.state["stages"]["draft"].get("fingerprint") != fingerprint(job):
            raise ReviewError(
                "The approved inputs changed since the draft was made. Make a new draft "
                "first; nothing was charged."
            )
    _check_soundtrack_fits(job)


def _readable_clip_seconds(job: Job) -> float:
    """clip_seconds, but a damaged or missing normalized clip becomes a plain StageError."""
    try:
        return clip_seconds(job)
    except (OSError, ValueError, av.error.FFmpegError) as exc:
        raise StageError(
            f"Could not read the length of the prepared clip ({exc}). Prepare a new job. "
            "Nothing was charged."
        ) from exc


def _check_soundtrack_fits(job: Job) -> None:
    """Free pre-flight: refuse to pay for a video whose original sound can't be put back."""
    upload = job.path("upload.mp4")
    try:
        with av.open(str(upload)) as probe:
            has_audio = bool(probe.streams.audio)
    except (OSError, ValueError, av.error.FFmpegError) as exc:
        raise StageError(
            f"Could not read the clip's sound ({exc}), so the original soundtrack might not "
            "be put back. Re-export the clip and prepare a new job. Nothing was charged."
        ) from exc
    if not has_audio:
        return  # no audio stream at all: the result will simply be silent
    try:
        sound = stream_seconds(upload, "audio")
    except (OSError, ValueError, av.error.FFmpegError) as exc:
        # Has audio, but its length is unknown: we can't promise to restore it.
        raise StageError(
            f"Could not read the length of the clip's sound ({exc}), so the original "
            "soundtrack might not be put back. Re-export the clip and prepare a new job. "
            "Nothing was charged."
        ) from exc
    picture = _readable_clip_seconds(job)
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


RESUMABLE = ("starting", "processing", "succeeded")  # a saved request that can be re-checked free


def resumes_without_charge(job: Job, stage: str) -> bool:
    """True when a downloaded result or a live/finished saved request can be picked up free."""
    record, raw, _ = generation_files(stage)
    if job.path(raw).exists():
        return True
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "id" in data and data.get("status") in RESUMABLE


def _has_outputs(job: Job, stage: str) -> bool:
    record, raw, result = generation_files(stage)
    return job.stage_done(stage) or any(job.path(n).exists() for n in (record, raw, result))


def _stale(job: Job, stage: str) -> bool:
    """True when this step has saved work made from inputs that are not the approved ones."""
    if not _has_outputs(job, stage):
        return False
    try:
        current = fingerprint(job)
    except ReviewError:
        current = None  # the reviewed files are gone: nothing saved can be proven current
    saved = job.state["stages"].get(stage, {}).get("fingerprint")
    return current is None or saved != current


def stale_generation(job: Job, stage: str) -> bool:
    """Saved draft/final work exists, but not for the inputs approved now."""
    return _stale(job, stage)


def stale_draft(job: Job) -> bool:
    """A new draft is needed: the draft, or a final made from an old draft, is stale."""
    return any(_stale(job, stage) for stage in GENERATION_STAGES)


def archive_stale_draft(job: Job) -> list[Path]:
    """Move the old draft (and any final made from it) aside, never deleting, and reset them.

    Files become e.g. draft-old-20261103T120000Z-raw.mp4. Called only after the user agreed
    to pay for a new draft.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    moves = []
    for stage in GENERATION_STAGES:
        for name in generation_files(stage):
            source = job.path(name)
            if source.exists():
                target = job.path(f"{stage}-old-{stamp}{name[len(stage) :]}")
                if target.exists():
                    raise StageError(f"{target} already exists; wait a second and try again.")
                moves.append((source, target))
    for source, target in moves:
        source.rename(target)
    job.reset_stages(*GENERATION_STAGES)
    return [target for _, target in moves]


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
        seconds=_readable_clip_seconds(job),
        seed=int(state["seed"]),
    )


def generate(
    job: Job, services: Services, stage: str, *, runs_dir: Path, status: Status = print
) -> Path:
    """Make the draft or final video. Paid: the caller showed the estimate and got a yes."""
    check_can_generate(job, stage)
    if _stale(job, stage):  # never relabel an old video with the newly approved inputs
        raise ReviewError(
            f"The saved {stage} was made from inputs that are no longer approved. Make a new "
            f"draft: charswap generate {job.id} --draft (it costs money; the old files are "
            "kept). Nothing was charged."
        )
    label = GENERATION_LABELS[stage]
    record, raw, result = (job.path(name) for name in generation_files(stage))
    if job.stage_done(stage):
        _notify(status, f"{label} · already done")
        return result
    generator = generator_for(job, services)
    request = build_request(job)
    estimate = generator.estimate(request, stage)
    inputs = fingerprint(job)  # what was approved; the final checks the draft used the same

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
    # No clear_review: generating changes no input.
    with _tracked(job, stage, fingerprint=inputs), _wrap(label):
        if not raw.exists():  # downloaded already: never ask the provider again
            generator.generate(request, stage, record=record, dest=raw, on_created=log_charge)
        _notify(status, "Putting the original soundtrack back")
        _write_atomically(
            result, lambda part: add_original_soundtrack(raw, job.path("upload.mp4"), part)
        )
    _notify(status, f"{stage.capitalize()} ready: {result}")
    return result


def _write_atomically(dest: Path, write: Callable[[Path], object]) -> None:
    """Write via a sibling temp file (keeps .mp4 so PyAV picks the muxer), then replace."""
    part = dest.with_name(f"{dest.stem}.part{dest.suffix}")
    try:
        write(part)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def next_step(job: Job, *, approved: bool) -> str:
    """The one thing to do next (shown by `charswap status`)."""
    mode = MODES[job.mode]
    if not all(job.stage_done(name) for name in mode.prep_stages):
        return f"charswap resume {job.id}"
    if not approved:
        return "watch the prepared files, then run charswap review with the fingerprint above"
    if not mode.generates:
        return "generating this mode arrives in the next version"
    if stale_draft(job):  # the final would be refused: the approved inputs changed
        return f"charswap generate {job.id} --draft"
    for stage in GENERATION_STAGES:
        if not job.stage_done(stage):
            return f"charswap generate {job.id} --{stage}"
    return f"done: {job.path('final.mp4')}"
