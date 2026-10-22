"""Mandatory review: approval is bound to a hash of exactly what was reviewed."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

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
        return ["original.mp4", job.state["character_primary"]]
    return {"depth": DEPTH, "face_mesh": MESH, "depth_mesh": DEPTH_MESH}[mode]


def _compute_fingerprint(job: Job) -> str:
    digest = hashlib.sha256(job.mode.encode())
    for name in review_files(job):
        path = job.path(name)
        if not path.exists():
            raise ReviewError("Prepare the video before reviewing it.")
        digest.update(name.encode())
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def fingerprint(job: Job) -> str:
    """SHA-256 over the mode and the exact bytes of every file under review."""
    return _compute_fingerprint(job)


def status(job: Job) -> dict:
    try:
        current = _compute_fingerprint(job)
    except ReviewError:
        return {"approved": False, "status": "unavailable", "note": "Prepare the video first."}
    saved_path = job.path("review.json")
    saved = json.loads(saved_path.read_text()) if saved_path.exists() else {}
    if saved.get("fingerprint") != current:
        return {"approved": False, "status": "pending", "fingerprint": current}
    return saved


def decide(job: Job, *, approved: bool, note: str, fingerprint: str) -> dict:
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
    job.path("review.json").write_text(json.dumps(data, indent=2))
    return data


def require_approved(job: Job) -> None:
    if not status(job).get("approved"):
        raise ReviewError("Review required: watch the whole prepared clip and approve it first.")


def clear(job: Job) -> None:
    job.path("review.json").unlink(missing_ok=True)
