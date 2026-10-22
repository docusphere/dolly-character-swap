"""`charswap` command line. Thin layer over jobs, pipeline, review and prompts."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import av

from character_swap.config import MissingSettingError, Settings
from character_swap.costs import (
    PRICES,
    STAGE_PRICE,
    estimate_prepare,
    estimate_repair,
    record_spend,
)
from character_swap.jobs import Job, JobError
from character_swap.pipeline import Services, remaining_paid_stages, repair_with_rgb
from character_swap.pipeline import prepare as prepare_job
from character_swap.prompts import build_prompt
from character_swap.review import ReviewError, decide
from character_swap.review import status as review_status
from character_swap.stages import StageError

Out = Callable[[str], None]


def build_services(settings: Settings) -> Services:  # pragma: no cover - wires real clients
    from character_swap.providers.replicate import ReplicateClient
    from character_swap.stages.face_mesh import MediaPipeDetector, ensure_model

    model_dir = settings.root / "models"
    return Services(
        replicate=ReplicateClient(settings.require("REPLICATE_API_TOKEN")),
        face_detector=lambda: MediaPipeDetector(ensure_model(model_dir)),
        hf_token=settings.get("HF_TOKEN") or None,
    )


def _stderr(text: str = "") -> None:
    print(text, file=sys.stderr)


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
    retry: str | None = None  # command name to suggest after a failure
    extra: dict = field(default_factory=dict)

    def track(self, job: Job, retry: str) -> None:
        self.job_id, self.retry = job.id, retry

    def proceed(self, question: str = "Continue?") -> bool:
        if self.confirm(question):
            return True
        self.err("Cancelled. Nothing was charged.")
        return False


EPILOG = """example:
  charswap prepare clip.mp4 --mode depth
  charswap status <id>
  charswap review <id> --approve --note "watched all" --fingerprint <fp>
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
    sub.add_parser("doctor", help="check this machine's setup")
    return parser


def _hint(env: Env) -> None:
    if env.job_id:
        env.err(f"Fix the issue, then run: charswap {env.retry} {env.job_id}")


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: Out = print,
    err: Out = _stderr,
    confirm: Callable[[str], bool] | None = None,
    services_factory: Callable[[Settings], Services] = build_services,
) -> int:
    args = _parser().parse_args(argv)
    settings = settings or Settings.load()
    env = Env(out, err, confirm or (lambda q: _ask(q, err)), services_factory)
    try:
        return COMMANDS[args.command](args, settings, env)
    except (JobError, ReviewError, StageError, MissingSettingError) as error:
        err(f"Error: {error}")
        _hint(env)
        return 2
    except KeyboardInterrupt:
        where = f" {env.job_id}" if env.job_id else " <job>"
        err(
            f"Interrupted. Run `charswap resume{where}` to continue; "
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
    )
    env.track(job, "resume")
    env.out(f"JOB {job.id}")
    prepare_job(job, env.services_factory(settings), status=env.out)
    record_spend(job, "prepare", estimate, settings.runs_dir)
    env.out(f"Next: watch {job.path('prepared.mp4')} then run `charswap status {job.id}`.")
    return 0


def _resume(args, settings, env) -> int:
    job = Job.load(settings.runs_dir, args.job)
    env.track(job, "resume")
    if job.mode != "direct":
        settings.require("REPLICATE_API_TOKEN")
    remaining = remaining_paid_stages(job)
    if remaining:
        total = sum(PRICES[STAGE_PRICE[name]].usd for name in remaining)
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
    state = job.state
    report = {
        "job": job.id,
        "mode": job.mode,
        "stages": state["stages"],
        "review": review_status(job),
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
    env.track(job, "repair")
    if job.mode not in ("depth", "depth_mesh"):
        raise StageError("This mode has no SAM mask to repair. Prepare the face tracking again.")
    settings.require("REPLICATE_API_TOKEN")
    env.out("Repair re-runs SAM 3:\n" + estimate_repair().describe())
    if not args.yes and not env.proceed():
        return 1
    repair_with_rgb(job, env.services_factory(settings), status=env.out)
    record_spend(job, "repair", estimate_repair(), settings.runs_dir)
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


COMMANDS = {
    "prepare": _prepare,
    "resume": _resume,
    "status": _status,
    "review": _review,
    "prompt": _prompt,
    "repair": _repair,
    "doctor": _doctor,
}
