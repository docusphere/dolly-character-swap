"""`charswap` command line. Thin layer over jobs, pipeline, review and prompts."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import threading
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import av

from character_swap.config import Settings
from character_swap.costs import (
    estimate_prepare,
    estimate_repair,
    estimate_stages,
    record_spend,
)
from character_swap.errors import CharSwapError
from character_swap.faces import ACTIVE, CONSENT, FAILED, PROCESSING, FaceLibrary, check_photo
from character_swap.jobs import WHISPER_LANGUAGES, Job, JobError
from character_swap.modes import MODES
from character_swap.pipeline import (
    FINAL_RESOLUTIONS,
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
    generator_for,
    generator_key,
    generator_promotes,
    is_promote,
    needs_new_charge,
    next_step,
    pin_provider,
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
from character_swap.pipeline import prepare as prepare_job
from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.providers.piapi import unconfirmed_message as piapi_unconfirmed
from character_swap.providers.replicate import unconfirmed_message as replicate_unconfirmed
from character_swap.providers.wavespeed import unconfirmed_message as wavespeed_unconfirmed
from character_swap.review import decide
from character_swap.review import status as review_status
from character_swap.stages import StageError

Out = Callable[[str], None]

VIEW_HOST = "127.0.0.1"  # the viewer is never reachable from the network
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


def build_services(settings: Settings) -> Services:
    from character_swap.providers.generators.wan_replicate import WanGenerator
    from character_swap.providers.replicate import ReplicateClient
    from character_swap.stages.face_mesh import MediaPipeDetector, ensure_model

    model_dir = settings.root / "models"
    replicate = ReplicateClient(
        settings.require("REPLICATE_API_TOKEN"),
        on_wait=lambda *wait: _stderr(rate_limit_message(*wait)),
    )
    generators = {"wan": WanGenerator(replicate)}
    if settings.get("WAVESPEED_API_KEY"):
        generators["seedance:wavespeed"] = SeedanceWaveSpeedGenerator(build_wavespeed(settings))
    if settings.get("PIAPI_API_KEY"):
        generators["seedance:piapi"] = SeedanceGenerator(build_piapi(settings))
    return Services(
        replicate=replicate,
        face_detector=lambda: MediaPipeDetector(ensure_model(model_dir)),  # pragma: no cover
        hf_token=settings.get("HF_TOKEN") or None,
        generators=generators,
        faces=FaceLibrary(settings.root),
    )


def run_server(app, *, host: str, port: int) -> None:  # pragma: no cover - real server
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


def _stderr(text: str = "") -> None:
    print(text, file=sys.stderr)


def rate_limit_message(seconds: float, attempt: int, max_attempts: int) -> str:
    return (
        f"Replicate is busy (rate limit); waiting {seconds:.0f} s before retrying "
        f"({attempt}/{max_attempts}). Nothing charged yet."
    )


def _ask(question: str, err: Out = _stderr) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        err("No input available; pass --yes to confirm non-interactively.")
        return False


def build_piapi(settings: Settings):
    from character_swap.providers.piapi import PiAPIClient

    return PiAPIClient(settings.require("PIAPI_API_KEY"))


def build_wavespeed(settings: Settings):
    from character_swap.providers.wavespeed import WaveSpeedClient

    return WaveSpeedClient(settings.require("WAVESPEED_API_KEY"))


@dataclass
class Env:
    out: Out
    err: Out
    confirm: Callable[[str], bool]
    services_factory: Callable[[Settings], Services]
    job_id: str | None = None
    retry: str | None = None  # command template to suggest after a failure, e.g. "resume {id}"
    extra: dict = field(default_factory=dict)
    serve: Callable[..., object] = run_server
    open_browser: Callable[[str], object] = webbrowser.open
    piapi_factory: Callable[[Settings], object] = build_piapi

    def track(self, job: Job, retry: str) -> None:
        self.job_id, self.retry = job.id, retry

    def retry_command(self) -> str:
        return "charswap " + (self.retry or "resume {id}").format(id=self.job_id or "<job>")

    def proceed(self, question: str = "Continue?") -> bool:
        if self.confirm(question):
            return True
        self.err("Cancelled. Nothing was charged.")
        return False


EPILOG = """example:
  charswap face add frank me.jpg
  charswap prepare clip.mp4 --mode depth --face frank
  charswap prepare clip.mp4 --generator wan --character me.jpg
  charswap status <id>
  charswap review <id> --approve --note "watched all" --fingerprint <fp>
  charswap prompt <id>
  charswap generate <id> --draft
  charswap generate <id> --final
  charswap generate <id> --final --resolution 720p
  charswap view
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="charswap",
        description="Character Swap workflow",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", help="show full tracebacks")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="prepare a video for review")
    prep.add_argument("video", type=Path)
    prep.add_argument("--generator", choices=["seedance", "wan"], default="seedance")
    prep.add_argument("--mode", choices=["depth", "face_mesh", "depth_mesh", "direct"])
    prep.add_argument("--subject", default="person", help="what SAM 3 should cut out")
    prep.add_argument("--depth-provider", choices=["replicate", "huggingface"], default="replicate")
    prep.add_argument("--face", help="a saved face to put in the video (see charswap face add)")
    prep.add_argument(
        "--character",
        type=Path,
        help="character image: Wan, any picture; Seedance, a character that is not a real person",
    )
    prep.add_argument("--face2", help="Seedance: a second saved face, for a second person")
    prep.add_argument("--character2", type=Path, help="Seedance: a second character image")
    prep.add_argument(
        "--language",
        default="auto",
        choices=WHISPER_LANGUAGES,
        metavar="CODE",
        help="spoken language code for the transcript, e.g. en or es (default: auto, detect it)",
    )
    prep.add_argument("--yes", action="store_true", help="skip the cost confirmation")

    sub.add_parser("status", help="show stages and review fingerprint as JSON").add_argument("job")
    sub.add_parser(
        "prompt", help="show and save the editable Seedance prompt (prompt.txt)"
    ).add_argument("job")
    resume = sub.add_parser(
        "resume",
        help="continue an interrupted job "
        "(saved provider predictions are re-checked, not re-bought)",
    )
    resume.add_argument("job")
    resume.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    review = sub.add_parser("review", help="approve or reject the prepared clip")
    review.add_argument("job")
    decision = review.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    review.add_argument("--note", required=True)
    review.add_argument("--fingerprint", required=True, help="copy from `charswap status`")
    repair = sub.add_parser("repair", help="re-cut the subject using original colors")
    repair.add_argument("job")
    repair.add_argument("--yes", action="store_true")
    gen = sub.add_parser("generate", help="make the 480p draft or the final (paid; asks first)")
    gen.add_argument("job")
    which = gen.add_mutually_exclusive_group(required=True)
    which.add_argument("--draft", action="store_true", help="480p test run")
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
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    sub.add_parser("doctor", help="check this machine's setup")
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
    view = sub.add_parser("view", help="open the read-only job viewer in your browser")
    view.add_argument("--port", type=_port, default=8770)
    view.add_argument("--no-browser", action="store_true", help="only print the URL")
    return parser


def _port(text: str) -> int:
    port = int(text)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _hint(env: Env) -> None:
    if env.job_id:
        env.err(f"Fix the issue, then run: {env.retry_command()}")


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: Out = print,
    err: Out = _stderr,
    confirm: Callable[[str], bool] | None = None,
    services_factory: Callable[[Settings], Services] = build_services,
    serve: Callable[..., object] | None = None,
    open_browser: Callable[[str], object] | None = None,
    piapi_factory: Callable[[Settings], object] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    settings = settings or Settings.load()
    env = Env(
        out,
        err,
        confirm or (lambda q: _ask(q, err)),
        services_factory,
        serve=serve or run_server,
        open_browser=open_browser or webbrowser.open,
        piapi_factory=piapi_factory or build_piapi,
    )
    try:
        return COMMANDS[args.command](args, settings, env)
    except CharSwapError as error:
        err(f"Error: {error}")
        _hint(env)
        return 2
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
    except Exception as error:
        if args.debug:
            raise
        err(f"Unexpected error: {error}. Run with --debug for details.")
        return 1


def _characters(args, settings, env, scratch: Path) -> tuple[Path | None, Path | None, dict]:
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
            if not noted:
                env.out(
                    "Note: Seedance takes --character images only for characters that are not a "
                    "real person (PiAPI rejects real faces sent this way). For a real person, "
                    "use --face."
                )
                noted = True
            images[slot] = check_photo(image, scratch / f"character-{slot}.jpg")
        else:
            images[slot] = image
    return images.get("primary"), images.get("secondary"), faces


def _prepare(args, settings, env) -> int:
    mode = args.mode or ("direct" if args.generator == "wan" else "depth")
    if args.generator == "wan" and args.character is None and args.face is None:
        raise JobError(
            "Wan needs --character IMAGE or --face NAME (your face or character picture)."
        )
    if args.generator == "wan" and (args.character2 or args.face2):
        raise JobError("Wan takes one character; a second one only works with Seedance.")
    if args.generator == "seedance" and not (args.face or args.character):
        raise JobError(
            "Seedance needs --face NAME (a saved face; see charswap face add) or --character "
            "IMAGE (a character that is not a real person)."
        )
    if mode == "direct" and args.language != "auto":
        raise JobError("--language only matters for Seedance modes (Wan has no transcript step).")
    if mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    with tempfile.TemporaryDirectory() as scratch:
        # Free checks first, so a bad photo or face is refused before any cost question.
        character, character2, faces = _characters(args, settings, env, Path(scratch))
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
        )
        env.track(job, "resume {id}")
        env.out(f"JOB {job.id}")
        prepare_job(job, env.services_factory(settings), status=env.out)
        if not free:
            record_spend(job, "prepare", estimate, settings.runs_dir)
        watch = "original.mp4" if job.mode == "direct" else "prepared.mp4"
        env.out(f"Next: watch {job.path(watch)} then run `charswap status {job.id}`.")
        return 0


def _resume(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
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


def _review(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    result = decide(job, approved=args.approve, note=args.note, fingerprint=args.fingerprint)
    env.out(f"Review {result['status']}.")
    return 0


def _prompt(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    if job.generator != "seedance":
        raise JobError("Prompts are only for Seedance jobs; Wan takes no text prompt.")
    if not all(job.stage_done(name) for name in MODES[job.mode].prep_stages):
        raise JobError(
            "Finish preparing first (the prompt uses the transcript and the scene): "
            f"charswap resume {job.id}"
        )
    pin_provider(job)  # before anything reads which provider's names to use
    if rename_old_prompt_names(job):
        env.out(RENAMED)
    path = write_prompt_file(job)
    # The next draft replaces the old one (inputs or prompt.txt changed): it sends prompt.txt.
    remake = stale_draft(job) or draft_prompt_changed(job)
    env.out(generation_prompt(job, "draft", replacing_stale=remake))
    again = f"`charswap generate {job.id} --draft`"
    if already_paid(job, "draft") and not remake:
        env.out(
            f"\nThe draft was already sent with this prompt. To try another, edit {path} "
            f"and run {again}: that makes a new paid draft (it shows the cost and asks "
            "first; the old one is kept as draft-old-*)."
        )
    elif remake:
        env.out(
            f"\nSaved in {path}. {again} makes a new paid draft with it, exactly as it is "
            "(it shows the cost and asks first; the old one is kept as draft-old-*)."
        )
    else:
        env.out(
            f"\nSaved in {path}. Edit that file to change what Seedance is told; the draft "
            "sends it exactly as it is."
        )
    return 0


def _repair(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    env.track(job, "repair {id}")
    if job.mode not in ("depth", "depth_mesh"):
        raise StageError("This mode has no SAM mask to repair. Prepare the face tracking again.")
    settings.require("REPLICATE_API_TOKEN")
    env.out("Repair re-runs SAM 3:\n" + estimate_repair().describe())
    if not args.yes and not env.proceed():
        return 1
    repair_with_rgb(job, env.services_factory(settings), status=env.out)
    record_spend(job, "repair", estimate_repair(), settings.runs_dir)
    return 0


def _generate(args, settings, env) -> int:
    stage = "final" if args.final else "draft"
    if args.resolution and stage == "draft":
        raise JobError("--resolution is only for --final (drafts are always 480p).")
    job = Job.load(settings.runs_dir, args.job)
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
                f"{request.prompt}\n"
            )
        env.out(f"{generation_label(job, stage, resolution)} (paid):\n" + estimate.describe())
        if inputs_changed:
            env.out(
                "This makes a new draft because the approved inputs changed since the last "
                "one, and it costs money. The old draft files are kept as draft-old-*."
            )
        elif prompt_changed:
            env.out(
                "prompt.txt changed since the draft: this makes a new paid draft with the "
                "prompt above. The old draft files are kept as draft-old-*."
            )
        if remake and _has_final_files(job):
            what = "finished final" if job.stage_done("final") else "earlier final files"
            env.out(f"Your {what} will also be moved to final-old-* (kept, not deleted).")
        if stage == "final":
            env.out(f"Only continue if you watched {job.path('draft.mp4')} and liked it.")
        if (
            stage == "final"
            and job.generator == "seedance"
            and not is_promote(job, stage, resolution)
        ):
            env.out(_fresh_final_note(job))
        if not args.yes and not env.proceed():
            return 1
        if remake:
            archive_stale_draft(job)
    elif resumes_without_charge(job, stage):
        env.out(f"Found a saved {stage} request; resuming it (no new charge).")
    result = generate_stage(
        job,
        services,
        stage,
        runs_dir=settings.runs_dir,
        status=env.out,
        final_resolution=resolution,
    )
    env.out(f"Watch: {result}")
    if stage == "draft":
        env.out(_after_draft_hint(job, services))
    return 0


def _has_final_files(job: Job) -> bool:
    """Anything archive_stale_draft would move aside for the final."""
    return job.stage_done("final") or any(
        job.path(name).exists() for name in generation_files("final")
    )


def _fresh_final_note(job: Job) -> str:
    """What a Seedance final that is a new run (not an upgrade of the draft) can look like."""
    if generator_promotes(job):  # PiAPI: only its 720p final is a fresh run
        return (
            "720p is a fresh run with the same prompt and inputs, so it can differ a little "
            "from the draft. 1080p (the default) upgrades the exact draft."
        )
    return (
        "The final is a new take at higher resolution; it will look a little different from "
        "the draft."
    )


def _after_draft_hint(job: Job, services: Services) -> str:
    plain = f"Happy with it? Run `charswap generate {job.id} --final`."
    finals = FINAL_RESOLUTIONS[generator_key(job)]
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
    if generator_promotes(job):
        return (
            f"{run_final} makes the {default} upgrade (~${price[default]:.2f}); add "
            f"`--resolution {other}` for a cheaper fresh {other} run (~${price[other]:.2f})."
        )
    return (
        f"{run_final} makes a new {default} take (~${price[default]:.2f}); add "
        f"`--resolution {other}` for a new {other} take (~${price[other]:.2f})."
    )


def _doctor(args, settings, env) -> int:
    out = env.out
    ok = True
    out(f"python: {sys.version.split()[0]}")
    out(f"pyav (ffmpeg): {av.__version__}")
    for binary in ("rubberband",):
        found = shutil.which(binary)
        ok &= bool(found)
        out(f"{binary}: {'found' if found else 'MISSING (macOS: brew install rubberband)'}")
    for key in ("REPLICATE_API_TOKEN", "PIAPI_API_KEY"):
        out(f"{key}: {'set' if settings.get(key) else 'missing'}")
    out("hosting: PiAPI temporary upload (Seedance; needs the PiAPI Creator plan)")
    try:
        out(f"saved faces: {len(FaceLibrary(settings.root).all())}")
    except CharSwapError as error:
        out(f"saved faces: unreadable ({error})")
    return 0 if ok else 1


def _port_free(port: int) -> bool:
    """Same bind uvicorn will do (SO_REUSEADDR), so a just-stopped viewer is not 'busy'."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        if probe.connect_ex((VIEW_HOST, port)) == 0:
            return False  # something already answers (e.g. a 0.0.0.0 listener we could shadow)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((VIEW_HOST, port))
        except OSError:
            return False
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
        opener.daemon = True
        opener.start()
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


def _face_line(face) -> str:
    if face.status == ACTIVE:
        return (
            f"Face {face.name} is ready (PiAPI id {face.asset_id}). Use it with: "
            f"charswap prepare clip.mp4 --face {face.name}"
        )
    if face.status == PROCESSING:
        return f"PiAPI is still checking {face.name}. Run: charswap face check {face.name}"
    if face.status == FAILED:
        return f"PiAPI rejected {face.name}: {face.error or 'no reason given'}."
    return f"PiAPI removed {face.name} after it went unused. Run: charswap face renew {face.name}"


def _face(args, settings, env) -> int:
    library = FaceLibrary(settings.root)
    if args.face_command == "list":
        faces = library.all()
        if not faces:
            env.out("No saved faces yet. Add one: charswap face add NAME photo.jpg")
        for face in faces:
            env.out(
                f"{face.name:<16} {face.status:<11} {face.asset_id}  {library.image_path(face)}"
            )
        return 0
    if args.face_command == "add":
        library.validate_new_name(args.name)
        env.out(CONSENT)
        if not args.yes and not env.confirm("Do you have the right to use this face?"):
            env.err("Cancelled. Nothing was uploaded.")
            return 1
        piapi = env.piapi_factory(settings)
        env.out("Uploading, then waiting for PiAPI's face check (free, up to ~5 minutes)...")
        action = lambda: library.add(args.name, args.photo, piapi)  # noqa: E731
    elif args.face_command == "renew":
        piapi = env.piapi_factory(settings)
        action = lambda: library.renew(args.name, piapi)  # noqa: E731
    else:
        piapi = env.piapi_factory(settings)
        action = lambda: library.check(args.name, piapi)  # noqa: E731
    try:
        face = action()
    except KeyboardInterrupt:
        if args.face_command == "add":
            env.err(
                "Stopped waiting. The face is saved if PiAPI accepted the upload; "
                f"run: charswap face check {args.name}"
            )
        else:
            env.err(f"Stopped waiting; run: charswap face check {args.name}")
        return 130
    env.out(_face_line(face))
    return 0 if face.status in (ACTIVE, PROCESSING) else 2


COMMANDS = {
    "prepare": _prepare,
    "resume": _resume,
    "status": _status,
    "review": _review,
    "prompt": _prompt,
    "repair": _repair,
    "generate": _generate,
    "doctor": _doctor,
    "face": _face,
    "view": _view,
}
