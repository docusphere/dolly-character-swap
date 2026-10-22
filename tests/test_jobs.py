import json

import pytest

from character_swap.jobs import Job, JobError


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "in.mp4"
    path.write_bytes(b"video")
    return path


def test_create_copies_upload_and_writes_state(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth", subject="person")
    assert len(job.id) == 32
    assert job.path("upload.mp4").read_bytes() == b"video"
    state = json.loads(job.path("state.json").read_text())
    assert state["generator"] == "seedance" and state["mode"] == "depth"
    assert state["stages"] == {}
    assert isinstance(state["seed"], int)


def test_wan_requires_direct_mode_and_character(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="does not work with wan"):
        Job.create(runs_dir, video, generator="wan", mode="depth")
    with pytest.raises(JobError, match="needs a character image"):
        Job.create(runs_dir, video, generator="wan", mode="direct")
    face = tmp_path / "me.jpg"
    face.write_bytes(b"jpg")
    job = Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert job.state["character_primary"] == "character-primary.jpg"
    assert job.path("character-primary.jpg").read_bytes() == b"jpg"


def test_unknown_generator_and_missing_video(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="Unknown generator"):
        Job.create(runs_dir, video, generator="sora", mode="depth")
    with pytest.raises(JobError, match="Video not found"):
        Job.create(runs_dir, tmp_path / "nope.mp4", generator="seedance", mode="depth")


def test_load_validates_id_and_existence(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="face_mesh")
    assert Job.load(runs_dir, job.id).mode == "face_mesh"
    with pytest.raises(JobError, match="32 lowercase hex"):
        Job.load(runs_dir, "../etc")
    with pytest.raises(JobError, match="No job"):
        Job.load(runs_dir, "0" * 32)


def test_stage_tracking_and_update(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert not job.stage_done("normalize")
    job.mark_stage("normalize", frames=48)
    assert job.stage_done("normalize")
    assert job.state["stages"]["normalize"]["frames"] == 48
    job.reset_stages("normalize")
    assert not job.stage_done("normalize")
    job.update(transcript="hello")
    assert job.state["transcript"] == "hello"
