import json
import threading

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
    for bad in ("../etc", "a" * 32 + "\n", "a/b", "/etc/passwd"):
        with pytest.raises(JobError, match="32 lowercase hex"):
            Job.load(runs_dir, bad)
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


def test_failed_write_leaves_no_temp_and_keeps_state(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    before = job.path("state.json").read_text()
    with pytest.raises(TypeError):
        job.update(x=object())
    assert list(job.root.glob("*.tmp")) == []
    assert job.path("state.json").read_text() == before


def test_concurrent_updates_are_not_lost(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    threads = [threading.Thread(target=job.update, kwargs={f"k{i}": i}) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(job.state[f"k{i}"] == i for i in range(20))


def test_missing_character_creates_no_folder(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="Character image not found"):
        Job.create(runs_dir, video, generator="wan", mode="direct", character=tmp_path / "none.jpg")
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_failure_after_mkdir_cleans_up(runs_dir, video, tmp_path, monkeypatch):
    face = tmp_path / "me.jpg"
    face.write_bytes(b"jpg")
    monkeypatch.setattr("character_swap.jobs.secrets.randbelow", lambda n: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert list(runs_dir.iterdir()) == []


@pytest.mark.parametrize("name", ["../x", "/etc/passwd", "a/../../x"])
def test_path_must_stay_inside_job(runs_dir, video, name):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    with pytest.raises(JobError, match="outside"):
        job.path(name)


def test_language_is_saved_and_checked(runs_dir, video):
    job = Job.create(runs_dir, video, generator="seedance", mode="depth", language="en")
    assert job.state["language"] == "en"
    default = Job.create(runs_dir, video, generator="seedance", mode="depth")
    assert default.state["language"] == "auto"
    with pytest.raises(JobError, match="language code"):
        Job.create(runs_dir, video, generator="seedance", mode="depth", language="English please")


@pytest.mark.parametrize("language", ["eng", "zz", "English", "EN"])
def test_unknown_language_codes_are_rejected(runs_dir, video, language):
    with pytest.raises(JobError, match="language code"):
        Job.create(runs_dir, video, generator="seedance", mode="depth", language=language)


def test_whisper_language_list_has_auto_and_three_letter_codes():
    from character_swap.jobs import WHISPER_LANGUAGES

    assert WHISPER_LANGUAGES[0] == "auto"
    assert {"en", "es", "haw", "yue"} <= set(WHISPER_LANGUAGES)
    assert len(WHISPER_LANGUAGES) == len(set(WHISPER_LANGUAGES)) == 101


def test_seedance_job_keeps_two_characters_and_face_names(runs_dir, video, tmp_path):
    me, friend = tmp_path / "me.jpg", tmp_path / "friend.png"
    me.write_bytes(b"me")
    friend.write_bytes(b"friend")
    job = Job.create(
        runs_dir,
        video,
        generator="seedance",
        mode="depth",
        character=me,
        character2=friend,
        faces={"primary": "frank"},
    )
    state = job.state
    assert state["character_primary"] == "character-primary.jpg"
    assert state["character_secondary"] == "character-secondary.png"
    assert state["face_primary"] == "frank" and "face_secondary" not in state
    assert job.path("character-secondary.png").read_bytes() == b"friend"


def test_wan_takes_only_one_character(runs_dir, video, tmp_path):
    me = tmp_path / "me.jpg"
    me.write_bytes(b"me")
    with pytest.raises(JobError, match="one character"):
        Job.create(runs_dir, video, generator="wan", mode="direct", character=me, character2=me)


def test_missing_second_character_image_is_refused(runs_dir, video, tmp_path):
    with pytest.raises(JobError, match="not found"):
        Job.create(
            runs_dir, video, generator="seedance", mode="depth", character2=tmp_path / "nope.jpg"
        )
