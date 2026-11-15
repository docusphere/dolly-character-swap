"""Scene jobs through the pipeline: prepare (Task 9) and generate (Task 10)."""

import json

import pytest

from character_swap import pipeline
from character_swap.jobs import Job, JobError, clean_setting
from character_swap.media.scene import SceneError
from character_swap.pipeline import is_scene_job, prepare, remaining_paid_stages
from character_swap.stages import StageError
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from fakes import RefusingReplicate
from helpers import solid, write_shots, write_video
from scene_jobs import BLUE, RED, TWO, fake_pitch, make_scene_job, prep_services

QUIET = {"status": lambda _m: None}


@pytest.fixture(autouse=True)
def no_rubberband(monkeypatch):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)


def sam_inputs(services):
    return [run["input"]["video"] for run in services.replicate.runs if run["key"] == SAM_VERSION]


# ---- the job folder ----------------------------------------------------------------
def test_a_scene_job_keeps_the_scene_the_part_and_the_segment(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, setting="  A dim\n room.  ")
    state = job.state
    assert job.path("full-scene.mp4").read_bytes() == (tmp_path / "scene.mp4").read_bytes()
    assert job.path("upload.mp4").read_bytes() == (tmp_path / "part.mp4").read_bytes()
    assert state["segment"]["start_frame"] == 24 and len(state["segment"]["frame_map"]) == 96
    assert state["setting"] == "A dim room." and "scene" not in state
    assert is_scene_job(job)


def test_a_single_shot_job_is_not_a_scene_job(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth")
    assert not is_scene_job(job) and "segment" not in job.state and "setting" not in job.state
    assert not job.path("full-scene.mp4").exists()


def test_a_scene_needs_its_segment(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 24)
    with pytest.raises(JobError, match="both the scene and its segment"):
        Job.create(runs_dir, clip, generator="seedance", mode="depth", scene=clip)
    with pytest.raises(JobError, match="Video not found"):
        Job.create(
            runs_dir,
            clip,
            generator="seedance",
            mode="depth",
            scene=tmp_path / "no.mp4",
            segment={},
        )
    assert not any(runs_dir.iterdir())


@pytest.mark.parametrize("text", ["", "   ", "x" * 301])
def test_a_setting_must_be_1_to_300_characters(text):
    with pytest.raises(JobError, match="Describe the setting in 1 to 300 characters"):
        clean_setting(text)


# ---- prepare -------------------------------------------------------------------------
def test_a_scene_job_cuts_out_its_subject_on_the_colors(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    services = prep_services(tmp_path)
    prepare(job, services, **QUIET)
    assert sam_inputs(services) == ["https://fake.replicate/original.mp4"]
    assert job.state["mask_source"] == "rgb" and job.path("prepared.mp4").exists()


def test_a_single_shot_job_still_cuts_out_on_the_depth_video(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth")
    services = prep_services(tmp_path, boxes=TWO)  # two people: no check on a single shot
    prepare(job, services, **QUIET)
    assert sam_inputs(services) == ["https://fake.replicate/depth.mp4"]
    assert "mask_source" not in job.state and job.stage_done("embed")


def test_two_people_stop_a_scene_job_before_the_face_mesh(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, mode="depth_mesh")
    services = prep_services(tmp_path, boxes=TWO)
    with pytest.raises(StageError, match="more than one person in 96 of 96 frames") as info:
        prepare(job, services, **QUIET)
    message = str(info.value)
    assert str(job.path("mask.mp4")) in message and job.path("mask.mp4").exists()
    assert '--subject "man with white face paint"' in message and "about $0.06" in message
    assert job.state["stages"]["composite"]["status"] == "failed"
    assert "face_mesh" not in job.state["stages"]
    runs = len(services.replicate.runs)
    with pytest.raises(StageError, match="more than one person"):  # a resume checks again
        prepare(job, services, **QUIET)
    assert len(services.replicate.runs) == runs  # the saved masks: nothing bought again


def test_a_setting_you_wrote_replaces_the_paid_description(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, setting="A dim room.")
    assert "describe" not in remaining_paid_stages(job)
    services = prep_services(tmp_path)
    prepare(job, services, **QUIET)
    assert DESCRIBE_MODEL not in [run["key"] for run in services.replicate.runs]
    assert job.state["scene"] == "A dim room." and job.path("scene.jpg").exists()
    assert job.state["stages"]["describe"]["source"] == "you"


def test_after_a_refused_description_a_setting_finishes_for_free(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    services = prep_services(tmp_path, cls=RefusingReplicate, refuse=DESCRIBE_MODEL)
    with pytest.raises(StageError, match=f"charswap resume {job.id} --setting"):
        prepare(job, services, **QUIET)
    paid = [run["key"] for run in services.replicate.runs]
    job.update(setting="A dim room.")  # what `charswap resume <id> --setting` saves
    prepare(job, services, **QUIET)
    assert [run["key"] for run in services.replicate.runs][: len(paid)] == paid
    assert DESCRIBE_MODEL not in [run["key"] for run in services.replicate.runs[len(paid) :]]
    assert job.state["stages"]["describe"]["source"] == "you" and job.stage_done("embed")


def test_a_scene_whose_sound_no_longer_fits_is_refused_before_paying(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    job.update(video={"seconds": 4.0})
    write_shots(job.path("full-scene.mp4"), [(BLUE, 143)], audio_seconds=3.0)
    with pytest.raises(SceneError, match=r"sound lasts 3\.0 s but its picture 6\.0 s"):
        pipeline._check_soundtrack_fits(job)


def test_the_segment_is_saved_as_json(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    saved = json.loads(job.path("state.json").read_text())["segment"]
    assert saved["fps"] == "24000/1001" and saved["crop"] == {"x": 0, "y": 0, "w": 64, "h": 48}
