import pytest

from character_swap.jobs import Job
from character_swap.review import (
    ReviewError,
    clear,
    decide,
    fingerprint,
    require_approved,
    review_files,
    status,
)


@pytest.fixture
def depth_job(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    for name in review_files(job):
        job.path(name).write_bytes(name.encode())
    return job


def test_files_per_mode(runs_dir, tmp_path):
    video, face = tmp_path / "in.mp4", tmp_path / "me.png"
    video.write_bytes(b"v")
    face.write_bytes(b"p")
    mesh = Job.create(runs_dir, video, generator="seedance", mode="face_mesh")
    assert "face-landmarks.json" in review_files(mesh) and "mask.mp4" not in review_files(mesh)
    both = Job.create(runs_dir, video, generator="seedance", mode="depth_mesh")
    assert {"mask.mp4", "face-landmarks.json", "depth-composite-silent.mp4"} <= set(
        review_files(both)
    )
    wan = Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert review_files(wan) == ["original.mp4", "character-primary.png"]


def test_unavailable_before_prepare(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert status(job)["status"] == "unavailable"


def test_approve_then_change_invalidates(depth_job):
    assert status(depth_job)["status"] == "pending"
    decide(depth_job, approved=True, note="watched all", fingerprint=fingerprint(depth_job))
    require_approved(depth_job)
    depth_job.path("mask.mp4").write_bytes(b"changed")
    assert status(depth_job)["status"] == "pending"
    with pytest.raises(ReviewError, match="Review required"):
        require_approved(depth_job)


def test_stale_fingerprint_refused(depth_job):
    old = fingerprint(depth_job)
    depth_job.path("prepared.mp4").write_bytes(b"new")
    with pytest.raises(ReviewError, match="changed"):
        decide(depth_job, approved=True, note="x", fingerprint=old)


def test_reject_and_clear(depth_job):
    decide(depth_job, approved=False, note="hand missing", fingerprint=fingerprint(depth_job))
    assert status(depth_job)["status"] == "rejected"
    clear(depth_job)
    assert status(depth_job)["status"] == "pending"
