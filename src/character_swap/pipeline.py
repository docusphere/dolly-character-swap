"""Run the preparation stages for a job's mode. Every stage is skipped if already done."""

from __future__ import annotations

import json
import os
import re
import shutil
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import av

from character_swap.costs import record_spend
from character_swap.faces import FaceError
from character_swap.jobs import SEEDANCE_PROVIDERS, Job, JobError
from character_swap.media.audio import (
    FIT_LIMIT,
    RATE,
    add_original_soundtrack,
    embed_samples,
    read_audio,
)
from character_swap.media.composite import clean_border_fragments, composite_depth_subject
from character_swap.media.video import normalize, stream_seconds
from character_swap.modes import (
    GENERATION_STAGES,
    GENERATOR_LIMITS,
    MODES,
    PROVIDER_NAMES,
    WHOLE_SECONDS,
)
from character_swap.prompts import PromptError, build_prompt, check_placeholders, validate_prompt
from character_swap.providers.generators.base import CharacterRef, GenerationRequest, Generator
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.generators.wan_replicate import WanGenerator
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
    faces: Any = None  # faces.FaceLibrary (Seedance jobs with saved faces)


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
                job.path("upload.mp4"),
                job.path("original.mp4"),
                min_seconds=low,
                max_seconds=high,
                whole_seconds=job.generator in WHOLE_SECONDS,
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
    samples = read_audio(job.path("vocals-plus3.wav"))
    if job.generator in WHOLE_SECONDS:  # the picture was cut to whole seconds; so is the voice
        samples = samples[:, : round(clip_seconds(job) * RATE)]
    embed_samples(job.path("composite-silent.mp4"), samples, job.path("prepared.mp4"))


# ---- generation (paid, after review) ---------------------------------------------
def generation_files(stage: str) -> tuple[str, str, str]:
    """(saved provider record, downloaded raw video, video with the original soundtrack)."""
    return f"{stage}-prediction.json", f"{stage}-raw.mp4", f"{stage}.mp4"


def check_can_generate(job: Job, stage: str) -> None:
    """Refuse before any cost question: right mode, approved review, and a draft first."""
    if stage not in GENERATION_STAGES:
        raise StageError(f"Unknown generation step {stage!r}; use draft or final.")
    if not MODES[job.mode].generates:
        raise StageError("This mode can't generate.")
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
    gap = sound - picture
    if job.generator in WHOLE_SECONDS and gap >= 0:
        return  # longer sound than the whole-second picture: it is cut to match after generating
    if abs(gap) > FIT_LIMIT - 0.1:  # leave 0.1 s for the generator's rounding
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
    return not already_paid(job, stage)


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
            f"{job.path('state.json')} names an unknown Seedance provider {provider!r}; use "
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
    key = generator_key(job)
    if not key.startswith("seedance:"):
        raise ValueError(f"This is a {key} job; only Seedance jobs have a provider.")
    return key.partition(":")[2]


def pin_provider(job: Job) -> str:
    """Save the resolved Seedance provider in state.json (once) and return the job's key.

    `charswap prompt` and `charswap generate` call this right after loading the job,
    before any paid create. Otherwise a WaveSpeed draft interrupted after its record was
    saved would read as PiAPI's under the old-job rule.
    """
    key = generator_key(job)
    if key.startswith("seedance:") and job.state.get("provider") is None:
        job.update(provider=key.partition(":")[2])
    return key


def generator_for(job: Job, services: Services) -> Generator:
    key = generator_key(job)
    try:
        return services.generators[key]
    except KeyError:
        raise StageError(f"No {key} generator is set up.") from None


def generation_label(job: Job, stage: str, resolution: str = "") -> str:
    """e.g. "Generating the 480p draft on WaveSpeed": every paid step names who charges it."""
    where = PROVIDER_NAMES[generator_key(job)]
    if stage == "draft":
        return f"Generating the 480p draft on {where}"
    return f"Generating the {resolution or final_resolution(job, '')} final on {where}"


# One entry per generator_key(job). Finals and promotes are the generator classes' own.
GENERATOR_CLASSES: dict[str, type] = {
    "wan": WanGenerator,
    "seedance:wavespeed": SeedanceWaveSpeedGenerator,
    "seedance:piapi": SeedanceGenerator,
}
FINAL_RESOLUTIONS: dict[str, tuple[str, ...]] = {  # the first is the default
    key: generator.finals for key, generator in GENERATOR_CLASSES.items()
}


def generator_promotes(job: Job) -> bool:
    """True when this job's generator can upgrade the draft's own task (PiAPI)."""
    return GENERATOR_CLASSES[generator_key(job)].promotes


def is_promote(job: Job, stage: str, resolution: str) -> bool:
    """True when this final upgrades the draft's own task (PiAPI's default 1080p final).

    Only a generator with `promotes` does that, and only for its default final. Every other
    final is a fresh run that resends the draft's saved prompt and the reviewed inputs.
    """
    if stage != "final":
        return False
    return generator_promotes(job) and resolution == FINAL_RESOLUTIONS[generator_key(job)][0]


def final_resolution(job: Job, requested: str = "") -> str:
    """The final's resolution: the one a started final used, else the request, else default."""
    choices = FINAL_RESOLUTIONS[generator_key(job)]
    saved = job.state["stages"].get("final", {}).get("resolution", "")
    # A saved choice counts only once that final really started (a record or a download):
    # an attempt that failed before PiAPI took it must not steer a plain --final to 720p.
    started = already_paid(job, "final")
    chosen = requested or (saved if started else "") or choices[0]
    if chosen not in choices:
        raise StageError(f"{job.generator} finals can be {' or '.join(choices)}, not {chosen}.")
    if saved and chosen != saved and started:
        record_path = job.path(generation_files("final")[0])
        if _record_status(job, "final") == "failed":
            raise StageError(
                f"The {saved} final's task failed. To make a {chosen} final instead, delete "
                f"{record_path} and run the same command again (it shows the cost and asks "
                "first). This command charged nothing."
            )
        raise StageError(
            f"A {saved} final was already started for this job; run the same command with "
            f"--resolution {saved} to pick it up (no new charge)."
        )
    return chosen


def refuse_another_final(job: Job, stage: str, requested: str) -> None:
    """A done final, and --resolution asks for another: refuse instead of saying "done"."""
    if stage != "final" or not requested or not job.stage_done("final"):
        return
    saved = job.state["stages"]["final"].get("resolution", "")
    if saved and requested != saved:
        raise StageError(
            f"A {saved} final is already done ({job.path('final.mp4')}). Making a {requested} "
            "take as well isn't supported yet. Nothing was charged."
        )


def final_resolution_for(job: Job, stage: str, requested: str) -> str:
    return final_resolution(job, requested) if stage == "final" else ""


def _record_status(job: Job, stage: str) -> str:
    """The saved provider record's status word, or "" when missing or unreadable."""
    record, _, _ = generation_files(stage)
    try:
        data = json.loads(job.path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) else ""


def already_paid(job: Job, stage: str) -> bool:
    """The step's paid task exists (record) or its video is downloaded: it resumes free."""
    record, raw, _ = generation_files(stage)
    return job.path(record).exists() or job.path(raw).exists()


def _character(job: Job, slot: str, faces: Any, *, face_needed: bool) -> CharacterRef | None:
    """The slot's character. A saved face is looked up by name (its current asset id).

    face_needed=False (a resume or a 1080p promote: nothing about the face is sent) lets a
    face that left the library since become asset id None instead of an error.
    """
    state = job.state
    image = state.get(f"character_{slot}")
    if not image:
        return None
    face_name = state.get(f"face_{slot}")
    asset_id = None
    if face_name and job.generator == "seedance":  # Wan sends the image; no library needed
        try:
            if faces is None:
                raise StageError("The face library is not set up for this command.")
            asset_id = faces.get(face_name).asset_id  # the current id (charswap face renew)
        except (FaceError, StageError):
            if face_needed:
                raise
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
    job: Job,
    *,
    faces: Any = None,
    stage: str = "draft",
    final_resolution: str = "",
    replacing_stale: bool = False,
) -> GenerationRequest:
    """What the provider is sent for this step.

    replacing_stale=True: the request a new draft will send once the stale one is archived
    (the cost question for a remake is asked before archiving, so it must ignore the old
    draft's saved prompt and record).
    """
    state = job.state
    seedance = job.generator == "seedance"
    promote = is_promote(job, stage, final_resolution)  # sends no face and no prompt
    paid = already_paid(job, stage) and not replacing_stale
    face_needed = not (paid or promote)
    primary = _character(job, "primary", faces, face_needed=face_needed)
    if primary is None:
        raise StageError(
            "This job has no character image. Prepare a new job with --face NAME or "
            "--character IMAGE."
        )
    return GenerationRequest(
        video=job.path("prepared.mp4" if seedance else "original.mp4"),
        primary=primary,
        secondary=_character(job, "secondary", faces, face_needed=face_needed),
        seconds=_readable_clip_seconds(job),
        seed=int(state["seed"]),
        prompt=generation_prompt(job, stage, final_resolution, replacing_stale=replacing_stale)
        if seedance
        else "",
        final_resolution=final_resolution if stage == "final" else "",
        draft_task_id=_draft_task_id(job) if stage == "final" else "",
    )


def generate(
    job: Job,
    services: Services,
    stage: str,
    *,
    runs_dir: Path,
    status: Status = print,
    final_resolution: str = "",
) -> Path:
    """Make the draft or final video. Paid: the caller showed the estimate and got a yes."""
    pin_provider(job)  # before any paid create (see pin_provider)
    check_can_generate(job, stage)
    if _stale(job, stage):  # never relabel an old video with the newly approved inputs
        raise ReviewError(
            f"The saved {stage} was made from inputs that are no longer approved. Make a new "
            f"draft: charswap generate {job.id} --draft (it costs money; the old files are "
            "kept). Nothing was charged."
        )
    record, raw, result = (job.path(name) for name in generation_files(stage))
    refuse_another_final(job, stage, final_resolution)
    if job.stage_done(stage):
        _notify(status, f"{generation_label(job, stage)} · already done")
        return result
    # The keyword shadows the module function; final_resolution_for reaches the function.
    resolution = final_resolution_for(job, stage, final_resolution)
    request = build_request(job, faces=services.faces, stage=stage, final_resolution=resolution)
    label = generation_label(job, stage, resolution)
    generator = generator_for(job, services)
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
    extra: dict[str, str] = {}
    if stage == "draft" and request.prompt:
        extra["prompt"] = request.prompt  # a fresh-run final sends exactly this text again
    if stage == "final":
        extra["resolution"] = resolution
    with _tracked(job, stage, fingerprint=inputs, **extra), _wrap(label):
        if not raw.exists():  # downloaded already: never ask the provider again
            generator.generate(request, stage, record=record, dest=raw, on_created=log_charge)
        _notify(status, "Putting the original soundtrack back")
        # Wan keeps the exact call it always had; only whole-second jobs cut the sound.
        cut = {"seconds": request.seconds} if job.generator in WHOLE_SECONDS else {}
        _write_atomically(
            result,
            lambda part: add_original_soundtrack(raw, job.path("upload.mp4"), part, **cut),
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
        return "this mode can't generate"
    if stale_draft(job):  # the final would be refused: the approved inputs changed
        return f"charswap generate {job.id} --draft"
    for stage in GENERATION_STAGES:
        if not job.stage_done(stage):
            return f"charswap generate {job.id} --{stage}"
    return f"done: {job.path('final.mp4')}"


PROMPT_FILE = "prompt.txt"  # Seedance: the editable prompt (charswap prompt <job>)


def default_prompt(job: Job) -> str:
    state = job.state
    return build_prompt(
        provider=seedance_provider(job),
        mode=job.mode,
        scene=state.get("scene"),
        transcript=state.get("transcript"),
        has_secondary=bool(state.get("character_secondary")),
    )


def draft_prompt_changed(job: Job) -> bool:
    """A finished Seedance draft, and prompt.txt now says something else: a new draft is wanted.

    No prompt.txt, or a draft that isn't finished (a resume), never counts as changed.
    Compared as generation_prompt reads the file (stripped); a broken edit counts as changed,
    so the remake path reports the problem before any cost question.
    """
    if job.generator != "seedance" or not job.stage_done("draft"):
        return False
    saved = job.state["stages"]["draft"].get("prompt")
    path = job.path(PROMPT_FILE)
    if not (isinstance(saved, str) and saved) or not path.exists():
        return False
    return path.read_text(encoding="utf-8").strip() != saved


def write_prompt_file(job: Job) -> Path:
    """Write prompt.txt unless the user already has one.

    After a paid draft it holds the text that draft sent (so recreating the file never looks
    like an edit); otherwise the default prompt.
    """
    path = job.path(PROMPT_FILE)
    if not path.exists():
        saved = job.state["stages"].get("draft", {}).get("prompt")
        sent = saved if isinstance(saved, str) and saved and already_paid(job, "draft") else ""
        text = sent or default_prompt(job)
        _write_atomically(path, lambda part: part.write_text(text, encoding="utf-8"))
    return path


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


def generation_prompt(
    job: Job, stage: str, resolution: str = "", *, replacing_stale: bool = False
) -> str:
    """The text Seedance receives.

    - final: the draft's saved prompt (every fresh-run final must match the watched draft;
      a PiAPI 1080p promote sends no prompt, so it may be "").
    - draft already paid (record or download there): the prompt saved when it was created,
      so a resume never depends on today's prompt.txt.
    - new draft (or replacing_stale: a remake whose old draft is about to be archived):
      prompt.txt (or the default), checked.
    """
    saved = job.state["stages"].get("draft", {}).get("prompt")
    saved = saved if isinstance(saved, str) and saved else ""
    if stage == "final":
        if saved or is_promote(job, stage, resolution) or already_paid(job, "final"):
            return saved
        raise PromptError(
            f"The draft's prompt wasn't saved, so a {resolution} final can't match it. Make a "
            "new draft first; nothing was charged."
        )
    if saved and already_paid(job, "draft") and not replacing_stale:
        return saved
    path = job.path(PROMPT_FILE)
    text = (path.read_text(encoding="utf-8") if path.exists() else default_prompt(job)).strip()
    if not text:
        raise PromptError(
            f"{path} is empty. Write what Seedance should do, or delete the file to use the "
            "default prompt."
        )
    check_placeholders(
        text,
        provider=seedance_provider(job),
        has_secondary=bool(job.state.get("character_secondary")),
    )
    return validate_prompt(text)
