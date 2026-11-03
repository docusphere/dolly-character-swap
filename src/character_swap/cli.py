"""`charswap` command line. Thin layer over jobs, pipeline, review and prompts."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
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
from character_swap.jobs import WHISPER_LANGUAGES, Job, JobError
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
from character_swap.prompts import build_prompt
from character_swap.review import decide
from character_swap.review import status as review_status
from character_swap.stages import StageError

Out = Callable[[str], None]

VIEW_HOST = "127.0.0.1"  # the viewer is never reachable from the network
BROWSER_DELAY = 1.0  # seconds; lets the server start listening before the browser asks


def build_services(settings: Settings) -> Services:
    from character_swap.providers.generators.wan_replicate import WanGenerator
    from character_swap.providers.replicate import ReplicateClient
    from character_swap.stages.face_mesh import MediaPipeDetector, ensure_model

    model_dir = settings.root / "models"
    replicate = ReplicateClient(
        settings.require("REPLICATE_API_TOKEN"),
        on_wait=lambda *wait: _stderr(rate_limit_message(*wait)),
    )
    return Services(
        replicate=replicate,
        face_detector=lambda: MediaPipeDetector(ensure_model(model_dir)),  # pragma: no cover
        hf_token=settings.get("HF_TOKEN") or None,
        generators={"wan": WanGenerator(replicate)},
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
  charswap prepare clip.mp4 --mode depth
  charswap prepare clip.mp4 --generator wan --character me.jpg
  charswap status <id>
  charswap review <id> --approve --note "watched all" --fingerprint <fp>
  charswap generate <id> --draft
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
    prep.add_argument("--character", type=Path, help="character image (required for wan)")
    prep.add_argument(
        "--language",
        default="auto",
        choices=WHISPER_LANGUAGES,
        metavar="CODE",
        help="spoken language code for the transcript, e.g. en or es (default: auto, detect it)",
    )
    prep.add_argument("--yes", action="store_true", help="skip the cost confirmation")

    sub.add_parser("status", help="show stages and review fingerprint as JSON").add_argument("job")
    sub.add_parser("prompt", help="print the editable Seedance prompt").add_argument("job")
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
    gen = sub.add_parser(
        "generate", help="make the 480p draft or the 720p final (paid; asks first)"
    )
    gen.add_argument("job")
    which = gen.add_mutually_exclusive_group(required=True)
    which.add_argument("--draft", action="store_true", help="480p test run")
    which.add_argument("--final", action="store_true", help="720p, same seed as the draft")
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    sub.add_parser("doctor", help="check this machine's setup")
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


def _prepare(args, settings, env) -> int:
    mode = args.mode or ("direct" if args.generator == "wan" else "depth")
    if args.generator == "wan" and args.character is None:
        raise JobError("Wan needs --character IMAGE (your face or character picture).")
    if mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    estimate = estimate_prepare(mode, seconds=30)
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
        character=args.character,
        language=args.language,
    )
    env.track(job, "resume {id}")
    env.out(f"JOB {job.id}")
    prepare_job(job, env.services_factory(settings), status=env.out)
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
    state = job.state
    env.out(
        build_prompt(mode=job.mode, scene=state.get("scene"), transcript=state.get("transcript"))
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
    out(f"hosting: {settings.hosting_mode}")
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


COMMANDS = {
    "prepare": _prepare,
    "resume": _resume,
    "status": _status,
    "review": _review,
    "prompt": _prompt,
    "repair": _repair,
    "generate": _generate,
    "doctor": _doctor,
    "view": _view,
}
