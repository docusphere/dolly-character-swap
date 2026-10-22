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


def test_review_json_is_written_atomically_and_corrupt_is_pending(depth_job):
    decide(depth_job, approved=True, note="ok", fingerprint=fingerprint(depth_job))
    assert not list(depth_job.root.glob("*.tmp"))
    depth_job.path("review.json").write_text("{not json")
    assert status(depth_job)["status"] == "pending"
    with pytest.raises(ReviewError, match="Review required"):
        require_approved(depth_job)


def test_fingerprint_framing_distinguishes_boundaries(depth_job):
    before = fingerprint(depth_job)
    # moving bytes between adjacent files must change the hash
    depth_job.path("original.mp4").write_bytes(b"original.mp4" + b"d")
    depth_job.path("depth.mp4").write_bytes(b"epth.mp4")
    assert fingerprint(depth_job) != before


def test_missing_character_or_unknown_mode_raises_review_error(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    job.update(mode="direct")
    with pytest.raises(ReviewError, match="character"):
        review_files(job)
    job.update(mode="bogus")
    with pytest.raises(ReviewError, match="Unknown mode"):
        review_files(job)


def test_deleted_reviewed_file_is_unavailable(depth_job):
    decide(depth_job, approved=True, note="ok", fingerprint=fingerprint(depth_job))
    depth_job.path("depth.mp4").unlink()
    assert status(depth_job)["status"] == "unavailable"
    with pytest.raises(ReviewError, match="Review required"):
        require_approved(depth_job)
