import json

import av
import pytest

from character_swap import pipeline
from character_swap.costs import Estimate, Line
from character_swap.jobs import Job
from character_swap.pipeline import (
    Services,
    check_can_generate,
    generate,
    needs_new_charge,
    next_step,
    prepare,
)
from character_swap.review import ReviewError, decide, fingerprint
from character_swap.stages import StageError
from helpers import solid, write_video

QUIET = {"status": lambda _m: None}


class FakeGenerator:
    name, provider, model = "wan", "replicate", "fake/wan"
    min_seconds, max_seconds = 1, 30
    supports_real_faces, supports_secondary = True, False

    def __init__(self, video):
        self.video = video  # what the "provider" returns: 2 s, 30 fps, silent
        self.calls = []

    def estimate(self, request, stage):
        return Estimate(lines=(Line(f"Fake {stage}", 0.25),))

    def generate(self, request, stage, *, record, dest, on_created=None):
        self.calls.append((stage, request))
        if not record.exists():
            record.write_text(json.dumps({"id": f"p-{stage}", "status": "succeeded"}))
            if on_created is not None:
                on_created({"id": f"p-{stage}", "status": "starting"})
        dest.write_bytes(self.video.read_bytes())
        return dest


@pytest.fixture
def wan_job(runs_dir, tmp_path):
    upload = write_video(tmp_path / "clip.mp4", [solid((180, 40, 40))] * 48, audio_seconds=2)
    face = tmp_path / "me.png"
    face.write_bytes(b"png")
    return Job.create(runs_dir, upload, generator="wan", mode="direct", character=face)


@pytest.fixture
def services(tmp_path):
    output = write_video(tmp_path / "wan-out.mp4", [solid((20, 200, 20))] * 60, fps=30)
    return Services(
        replicate=None, face_detector=lambda: None, generators={"wan": FakeGenerator(output)}
    )


def approve(job, services):
    prepare(job, services, **QUIET)
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def test_draft_needs_an_approved_review(wan_job, services, runs_dir):
    prepare(wan_job, services, **QUIET)
    with pytest.raises(ReviewError, match="Review required"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "draft" not in wan_job.state["stages"]


def test_final_needs_a_draft(wan_job, services, runs_dir):
    approve(wan_job, services)
    with pytest.raises(StageError, match="draft first"):
        generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)


def test_seedance_jobs_cannot_generate_yet(runs_dir, tmp_path):
    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth")
    with pytest.raises(StageError, match="next version"):
        check_can_generate(job, "draft")


def test_draft_restores_the_original_soundtrack(wan_job, services, runs_dir):
    approve(wan_job, services)
    result = generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert result == wan_job.path("draft.mp4") and wan_job.stage_done("draft")
    assert audio_packets(result) == audio_packets(wan_job.path("upload.mp4"))


def test_draft_and_final_send_the_same_seed_clip_and_character(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)
    calls = services.generators["wan"].calls
    assert [stage for stage, _ in calls] == ["draft", "final"]
    assert {request.seed for _, request in calls} == {wan_job.state["seed"]}
    request = calls[0][1]
    assert request.video == wan_job.path("original.mp4")
    assert request.primary.image_path == wan_job.path("character-primary.png")
    assert request.seconds == 2.0
    assert wan_job.path("final.mp4").exists()


def test_each_paid_request_is_logged_once_when_created(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)  # already done
    costs = wan_job.state["costs"]
    assert [(c["step"], c["task_id"], c["usd"], c["model"], c["units"]) for c in costs] == [
        ("draft", "p-draft", 0.25, "fake/wan", "2.0s")
    ]
    assert "p-draft" in (runs_dir / "spend-log.csv").read_text()


def test_downloaded_result_is_never_requested_again(wan_job, services, runs_dir):
    approve(wan_job, services)
    fake = services.generators["wan"]
    wan_job.path("draft-raw.mp4").write_bytes(fake.video.read_bytes())
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert fake.calls == [] and wan_job.stage_done("draft")


def test_soundtrack_failure_resumes_without_a_new_request(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "add_original_soundtrack", broken)
    with pytest.raises(StageError, match="disk full"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert wan_job.state["stages"]["draft"]["status"] == "failed"
    monkeypatch.undo()
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert len(services.generators["wan"].calls) == 1 and wan_job.stage_done("draft")


def test_interrupted_generation_is_recorded(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(services.generators["wan"], "generate", stop)
    with pytest.raises(KeyboardInterrupt):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    entry = wan_job.state["stages"]["draft"]
    assert entry["status"] == "failed" and entry["error"] == "interrupted"


def test_generation_does_not_clear_the_review(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert wan_job.path("review.json").exists()


def test_generation_is_tracked_apart_from_preparation(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert tuple(wan_job.state["stages"]) == ("normalize", "draft")


def test_soundtrack_that_cannot_fit_is_refused_before_paying(runs_dir, services, tmp_path):
    # 2 s of picture but 3 s of sound: the original audio could never be put back.
    upload = write_video(tmp_path / "long.mp4", [solid((9, 9, 9))] * 48, audio_seconds=3)
    face = tmp_path / "me2.png"
    face.write_bytes(b"png")
    job = Job.create(runs_dir, upload, generator="wan", mode="direct", character=face)
    approve(job, services)
    with pytest.raises(StageError, match="soundtrack"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "costs" not in job.state
    assert "draft" not in job.state["stages"]


def test_silent_upload_passes_the_soundtrack_check(runs_dir, services, tmp_path):
    upload = write_video(tmp_path / "mute.mp4", [solid((9, 9, 9))] * 48)
    face = tmp_path / "me3.png"
    face.write_bytes(b"png")
    job = Job.create(runs_dir, upload, generator="wan", mode="direct", character=face)
    approve(job, services)
    check_can_generate(job, "draft")  # no error: the result will simply be silent


def test_unreadable_sound_length_is_refused_before_paying(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def unknown(path, kind):
        raise ValueError("Cannot determine the audio duration of upload.mp4.")

    monkeypatch.setattr(pipeline, "stream_seconds", unknown)
    with pytest.raises(StageError, match="Could not read the length of the clip's sound"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "costs" not in wan_job.state


def test_unconfirmed_saved_request_is_detected(wan_job):
    assert not pipeline.saved_request_unconfirmed(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text(json.dumps({"status": "creating"}))
    assert pipeline.saved_request_unconfirmed(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text(
        json.dumps({"id": "p", "status": "processing"})
    )
    assert not pipeline.saved_request_unconfirmed(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text("{")
    assert not pipeline.saved_request_unconfirmed(wan_job, "draft")  # run() reports damage


def test_needs_new_charge(wan_job):
    assert needs_new_charge(wan_job, "draft")
    wan_job.path("draft-prediction.json").write_text("{}")
    assert not needs_new_charge(wan_job, "draft")
    assert needs_new_charge(wan_job, "final")
    wan_job.path("final-raw.mp4").write_bytes(b"x")
    assert not needs_new_charge(wan_job, "final")


def test_missing_generator_is_explained(wan_job, services, runs_dir):
    approve(wan_job, services)
    services.generators.clear()
    with pytest.raises(StageError, match="No wan generator"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)


def test_next_step_walks_the_whole_flow(wan_job, services, runs_dir):
    assert next_step(wan_job, approved=False) == f"charswap resume {wan_job.id}"
    prepare(wan_job, services, **QUIET)
    assert "charswap review" in next_step(wan_job, approved=False)
    decide(wan_job, approved=True, note="ok", fingerprint=fingerprint(wan_job))
    assert next_step(wan_job, approved=True) == f"charswap generate {wan_job.id} --draft"
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert next_step(wan_job, approved=True) == f"charswap generate {wan_job.id} --final"
    generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)
    assert next_step(wan_job, approved=True) == f"done: {wan_job.path('final.mp4')}"


def test_next_step_for_seedance_waits_for_the_next_version(runs_dir, tmp_path):
    upload = write_video(tmp_path / "s.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    job = Job.create(runs_dir, upload, generator="seedance", mode="face_mesh")
    for name in pipeline.PREP_STAGES_BY_MODE["face_mesh"]:
        job.mark_stage(name)
    assert "next version" in next_step(job, approved=True)


def test_unreadable_upload_is_refused_before_paying(wan_job, services, runs_dir):
    approve(wan_job, services)
    wan_job.path("upload.mp4").write_bytes(b"not a video")  # not hashed by review
    with pytest.raises(StageError, match="Could not read the clip's sound"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "costs" not in wan_job.state
    assert "draft" not in wan_job.state["stages"]


def test_unreadable_clip_length_is_a_plain_error(wan_job, services, runs_dir, monkeypatch):
    approve(wan_job, services)

    def broken(job):
        raise av.error.InvalidDataError(1094995529, "Invalid data found when processing input")

    monkeypatch.setattr(pipeline, "clip_seconds", broken)
    with pytest.raises(StageError, match="Could not read the length of the prepared clip"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["wan"].calls == [] and "costs" not in wan_job.state
