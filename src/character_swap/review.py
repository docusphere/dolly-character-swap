"""Mandatory review: approval is bound to a hash of exactly what was reviewed."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from character_swap.jobs import Job

DEPTH = ["original.mp4", "depth.mp4", "mask.mp4", "composite-silent.mp4", "prepared.mp4"]
MESH = ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]
DEPTH_MESH = [
    "original.mp4",
    "depth.mp4",
    "mask.mp4",
    "depth-composite-silent.mp4",
    "face-landmarks.json",
    "composite-silent.mp4",
    "prepared.mp4",
]


class ReviewError(ValueError):
    """Generation is blocked until the current artifacts are approved."""


def review_files(job: Job) -> list[str]:
    mode = job.mode
    if mode == "direct":
        character = job.state.get("character_primary")
        if not character:
            raise ReviewError("This job has no character image to review.")
        return ["original.mp4", character]
    files = {"depth": DEPTH, "face_mesh": MESH, "depth_mesh": DEPTH_MESH}.get(mode)
    if files is None:
        raise ReviewError(f"Unknown mode {mode!r}; cannot review.")
    return files


def _preparation_finished(job: Job) -> bool:
    """True when every stage of the job's mode is done (unknown modes fail in review_files)."""
    from character_swap.pipeline import PREP_STAGES_BY_MODE  # lazy: pipeline imports review

    names = PREP_STAGES_BY_MODE.get(job.mode)
    return names is None or all(job.stage_done(name) for name in names)


def _compute_fingerprint(job: Job) -> str:
    digest = hashlib.sha256(job.mode.encode())
    for name in review_files(job):
        path = job.path(name)
        if not path.exists():
            raise ReviewError("Prepare the video before reviewing it.")
        digest.update(f"{name}\0{path.stat().st_size}\0".encode())
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def fingerprint(job: Job) -> str:
    """SHA-256 over the mode and the exact bytes of every file under review."""
    return _compute_fingerprint(job)


def status(job: Job) -> dict:
    if not _preparation_finished(job):
        return {"approved": False, "status": "unavailable", "note": "Prepare the video first."}
    try:
        current = _compute_fingerprint(job)
    except ReviewError:
        return {"approved": False, "status": "unavailable", "note": "Prepare the video first."}
    saved_path = job.path("review.json")
    try:
        saved = json.loads(saved_path.read_text(encoding="utf-8")) if saved_path.exists() else {}
    except (OSError, ValueError):
        saved = {}  # unreadable review.json: fail closed
    if not isinstance(saved, dict) or saved.get("fingerprint") != current:
        return {"approved": False, "status": "pending", "fingerprint": current}
    return saved


def decide(job: Job, *, approved: bool, note: str, fingerprint: str) -> dict:
    if not _preparation_finished(job):
        raise ReviewError("Preparation isn't finished — resume the job first.")
    current = _compute_fingerprint(job)
    if current != fingerprint:
        raise ReviewError("The prepared files changed since you looked. Reload and review again.")
    data = {
        "approved": approved,
        "status": "approved" if approved else "rejected",
        "note": note,
        "fingerprint": current,
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    handle, temporary = tempfile.mkstemp(dir=job.root, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temporary, job.path("review.json"))
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return data


def require_approved(job: Job) -> None:
    if not status(job).get("approved"):
        raise ReviewError("Review required: watch the whole prepared clip and approve it first.")


def clear(job: Job) -> None:
    job.path("review.json").unlink(missing_ok=True)
