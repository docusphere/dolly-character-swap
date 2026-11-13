import json

import av
import pytest
from PIL import Image

from character_swap import pipeline
from character_swap.costs import Estimate, Line
from character_swap.faces import FaceError, FaceLibrary
from character_swap.jobs import Job, JobError
from character_swap.media.video import stream_seconds
from character_swap.modes import MODES
from character_swap.pipeline import (
    Services,
    check_can_generate,
    final_resolution,
    generate,
    generation_prompt,
    needs_new_charge,
    next_step,
    prepare,
    write_prompt_file,
)
from character_swap.prompts import PromptError
from character_swap.review import ReviewError, decide, fingerprint
from character_swap.stages import StageError
from fakes import FakePiAPI
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
    # what an interrupted run leaves: the step failed, its approved inputs recorded
    wan_job.mark_stage("draft", "failed", fingerprint=fingerprint(wan_job))
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


def _make_stale(job, services, runs_dir):
    """A finished draft whose inputs no longer match the approved ones."""
    approve(job, services)
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    draft = job.state["stages"]["draft"]
    job.mark_stage("draft", **{**draft, "fingerprint": "an older approval"})


def test_stale_draft_is_detected(wan_job, services, runs_dir):
    assert not pipeline.stale_draft(wan_job)
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert not pipeline.stale_draft(wan_job)
    draft = wan_job.state["stages"]["draft"]
    wan_job.mark_stage("draft", **{k: v for k, v in draft.items() if k != "fingerprint"})
    assert pipeline.stale_draft(wan_job)  # no fingerprint: cannot prove it matches


def test_generate_never_reuses_a_stale_draft(wan_job, services, runs_dir):
    _make_stale(wan_job, services, runs_dir)
    old = wan_job.path("draft-raw.mp4").read_bytes()
    with pytest.raises(ReviewError, match="new draft"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    wan_job.mark_stage("draft", "failed", fingerprint="an older approval")  # not done, raw kept
    with pytest.raises(ReviewError, match="new draft"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert len(services.generators["wan"].calls) == 1  # only the first, real draft
    assert wan_job.path("draft-raw.mp4").read_bytes() == old


def test_a_failed_generation_remembers_its_inputs(wan_job, services, runs_dir):
    approve(wan_job, services)
    generator = services.generators["wan"]

    def boom(request, stage, *, record, dest, on_created=None):
        record.write_text(json.dumps({"id": "p", "status": "processing"}))
        raise RuntimeError("network down")

    generator.generate = boom
    with pytest.raises(StageError):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    draft = wan_job.state["stages"]["draft"]
    assert draft["status"] == "failed" and draft["fingerprint"] == fingerprint(wan_job)
    assert not pipeline.stale_draft(wan_job)  # resuming that saved request stays allowed


def test_archive_stale_draft_keeps_the_old_files(wan_job, services, runs_dir):
    _make_stale(wan_job, services, runs_dir)
    old = {name: wan_job.path(name).read_bytes() for name in pipeline.generation_files("draft")}
    moved = pipeline.archive_stale_draft(wan_job)
    assert len(moved) == 3 and all(path.name.startswith("draft-old-") for path in moved)
    assert sorted(path.read_bytes() for path in moved) == sorted(old.values())
    assert not any(wan_job.path(name).exists() for name in old)
    assert "draft" not in wan_job.state["stages"]
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert len(services.generators["wan"].calls) == 2 and not pipeline.stale_draft(wan_job)


def test_resumes_without_charge(wan_job):
    record = wan_job.path("draft-prediction.json")
    assert not pipeline.resumes_without_charge(wan_job, "draft")
    for status, free in [
        ("starting", True),
        ("processing", True),
        ("succeeded", True),
        ("failed", False),
        ("canceled", False),
        ("creating", False),
    ]:
        record.write_text(json.dumps({"id": "p", "status": status}))
        assert pipeline.resumes_without_charge(wan_job, "draft") is free, status
    record.write_text("{")
    assert not pipeline.resumes_without_charge(wan_job, "draft")
    wan_job.path("draft-raw.mp4").write_bytes(b"video")
    assert pipeline.resumes_without_charge(wan_job, "draft")


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


def test_next_step_asks_for_a_new_draft_when_the_inputs_changed(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    stages = wan_job.state["stages"]
    wan_job.mark_stage("draft", **{**stages["draft"], "fingerprint": "an older approval"})
    assert next_step(wan_job, approved=True) == f"charswap generate {wan_job.id} --draft"


def test_next_step_asks_for_a_new_draft_when_the_final_is_stale(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)
    for name in pipeline.generation_files("draft"):
        wan_job.path(name).unlink(missing_ok=True)  # draft files removed by hand
    final = wan_job.state["stages"]["final"]
    wan_job.mark_stage("final", **{**final, "fingerprint": "an older approval"})
    assert pipeline.stale_generation(wan_job, "final")
    assert next_step(wan_job, approved=True) == f"charswap generate {wan_job.id} --draft"


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


def test_final_refuses_when_inputs_changed_since_the_draft(wan_job, services, runs_dir):
    approve(wan_job, services)
    generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert wan_job.state["stages"]["draft"]["fingerprint"] == fingerprint(wan_job)
    wan_job.path("character-primary.png").write_bytes(b"another character")
    decide(wan_job, approved=True, note="new face", fingerprint=fingerprint(wan_job))
    with pytest.raises(ReviewError, match="changed since the draft"):
        generate(wan_job, services, "final", runs_dir=runs_dir, **QUIET)
    assert [stage for stage, _ in services.generators["wan"].calls] == ["draft"]
    assert "final" not in wan_job.state["stages"]


def test_final_refuses_an_old_draft_without_a_fingerprint(wan_job, services, runs_dir):
    approve(wan_job, services)
    wan_job.mark_stage("draft")  # made before drafts recorded their inputs
    with pytest.raises(ReviewError, match="Make a new draft first"):
        check_can_generate(wan_job, "final")


def test_failed_soundtrack_restore_leaves_no_partial_result(
    wan_job, services, runs_dir, monkeypatch
):
    approve(wan_job, services)

    def half_written(generated, upload, dst):
        dst.write_bytes(b"partial")
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "add_original_soundtrack", half_written)
    with pytest.raises(StageError, match="disk full"):
        generate(wan_job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert not wan_job.path("draft.mp4").exists()
    assert not wan_job.path("draft.part.mp4").exists()


def _seedance_job(runs_dir, tmp_path, *, picture, sound):
    upload = write_video(
        tmp_path / "s.mp4", [solid((1, 1, 1))] * round(picture * 24), audio_seconds=sound
    )
    me = tmp_path / "me.jpg"
    me.write_bytes(b"me")
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth", character=me)
    job.update(video={"seconds": float(int(picture))})  # what a whole-second normalize saves
    return job


def test_seedance_sound_longer_than_the_whole_second_picture_is_fine(runs_dir, tmp_path):
    job = _seedance_job(runs_dir, tmp_path, picture=4.9, sound=4.9)  # picture becomes 4 s
    pipeline._check_soundtrack_fits(job)  # no error: the sound is cut at 4 s


def test_seedance_sound_shorter_than_the_picture_is_still_refused(runs_dir, tmp_path):
    job = _seedance_job(runs_dir, tmp_path, picture=5.0, sound=4.0)
    with pytest.raises(StageError, match="Nothing was charged"):
        pipeline._check_soundtrack_fits(job)


def test_embed_cuts_the_pitched_vocals_to_the_picture(runs_dir, tmp_path):
    from character_swap.media.audio import write_wav
    from character_swap.media.video import stream_seconds
    from helpers import tone

    job = _seedance_job(runs_dir, tmp_path, picture=2.0, sound=2.0)
    write_video(job.path("composite-silent.mp4"), [solid((3, 3, 3))] * 48)
    write_wav(job.path("vocals-plus3.wav"), tone(2.6))
    pipeline._embed(job)
    assert stream_seconds(job.path("prepared.mp4"), "audio") <= 2.05


# ---- Seedance (Plan 2b) -------------------------------------------------------------
@pytest.fixture
def seedance(runs_dir, tmp_path):
    """An approved depth job with a saved face, as if prepare() had run."""
    upload = write_video(tmp_path / "talk.mp4", [solid((90, 60, 30))] * 108, audio_seconds=4.5)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(photo, "JPEG")
    library = FaceLibrary(tmp_path, sleep=lambda _s: None)
    face = library.add("frank", photo, FakePiAPI())
    job = Job.create(
        runs_dir,
        upload,
        generator="seedance",
        mode="depth",
        character=library.image_path(face),
        faces={"primary": "frank"},
    )
    for name in MODES["depth"].review_files:
        write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in MODES["depth"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="lets get it", scene="A hotel lobby.")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    output = write_video(tmp_path / "seedance-out.mp4", [solid((20, 200, 20))] * 96)
    services = Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:piapi": FakeGenerator(output)},
        faces=library,
    )
    return job, services


def test_seedance_draft_sends_the_prepared_video_face_and_default_prompt(seedance, runs_dir):
    job, services = seedance
    result = generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    ((stage, request),) = services.generators["seedance:piapi"].calls
    assert stage == "draft" and request.video == job.path("prepared.mp4")
    assert request.primary.face_asset_id == "asset-1" and request.primary.name == "frank"
    assert "lets get it" in request.prompt and "@image1" in request.prompt
    assert job.state["stages"]["draft"]["prompt"] == request.prompt
    assert stream_seconds(result, "audio") <= 4.05  # the 4.5 s sound was cut to the picture


def test_an_edited_prompt_file_is_what_is_sent(seedance, runs_dir):
    job, services = seedance
    path = write_prompt_file(job)
    path.write_text("Make the person in @video1 look like @image1. Keep everything else.")
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    request = services.generators["seedance:piapi"].calls[0][1]
    assert request.prompt == "Make the person in @video1 look like @image1. Keep everything else."


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("", "empty"),
        ("Swap @video1 please.", "@image1"),
        ("@video1 @image1 and @image2", "@image2"),
    ],
)
def test_prompt_file_problems_are_plain_errors(seedance, text, words):
    job, _ = seedance
    write_prompt_file(job).write_text(text)
    with pytest.raises(PromptError, match=words):
        generation_prompt(job, "draft")


def test_1080p_final_carries_the_draft_task_and_records_the_resolution(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    generate(job, services, "final", runs_dir=runs_dir, **QUIET)
    request = services.generators["seedance:piapi"].calls[1][1]
    assert request.draft_task_id == "p-draft" and request.final_resolution == "1080p"
    assert job.state["stages"]["final"]["resolution"] == "1080p"


def test_720p_final_reuses_the_drafts_prompt(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    sent = job.state["stages"]["draft"]["prompt"]
    write_prompt_file(job).write_text("@video1 @image1 changed after the draft")
    generate(job, services, "final", runs_dir=runs_dir, final_resolution="720p", **QUIET)
    request = services.generators["seedance:piapi"].calls[1][1]
    assert request.prompt == sent and request.final_resolution == "720p"


def test_a_started_final_keeps_its_resolution(seedance):
    job, _ = seedance
    job.mark_stage("final", "failed", resolution="1080p", error="lost connection")
    job.path("final-prediction.json").write_text('{"id": "t9", "status": "processing"}')
    with pytest.raises(StageError, match="--resolution 1080p"):
        final_resolution(job, "720p")
    assert final_resolution(job, "") == "1080p"


def test_wan_finals_are_720p_only(wan_job):
    assert final_resolution(wan_job, "") == "720p"
    with pytest.raises(StageError, match="720p"):
        final_resolution(wan_job, "1080p")


def test_next_step_for_an_approved_seedance_job_is_the_draft(seedance):
    job, _ = seedance
    assert next_step(job, approved=True) == f"charswap generate {job.id} --draft"


def test_a_deleted_saved_face_is_a_plain_error(seedance, runs_dir):
    job, services = seedance
    (services.faces.folder / "faces.json").write_text("[]")
    with pytest.raises(FaceError, match="charswap face add frank"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["seedance:piapi"].calls == []


def test_a_resumed_draft_keeps_the_prompt_it_was_created_with(seedance, runs_dir):
    job, services = seedance
    sent = "Put @image1 on the person in @video1."
    # an interrupted draft: the task was created with `sent`, then the user edited prompt.txt
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job), prompt=sent, error="x")
    job.path("draft-prediction.json").write_text('{"id": "p-draft", "status": "succeeded"}')
    write_prompt_file(job).write_text("@video1 @image1 edited after the task was created")
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert job.state["stages"]["draft"]["prompt"] == sent
    generate(job, services, "final", runs_dir=runs_dir, final_resolution="720p", **QUIET)
    assert services.generators["seedance:piapi"].calls[-1][1].prompt == sent


def _interrupted_draft(job, sent):
    """A draft whose paid task exists (record saved) but which never finished here."""
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job), prompt=sent, error="x")
    job.path("draft-prediction.json").write_text('{"id": "p-draft", "status": "succeeded"}')


def _forget_faces(services):
    (services.faces.folder / "faces.json").write_text("[]")


def test_a_paid_draft_resumes_even_with_a_broken_prompt_file(seedance, runs_dir):
    job, services = seedance
    sent = "Put @image1 on the person in @video1."
    _interrupted_draft(job, sent)
    write_prompt_file(job).write_text("")  # would be refused for a new draft
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert job.stage_done("draft") and job.state["stages"]["draft"]["prompt"] == sent
    assert services.generators["seedance:piapi"].calls[0][1].prompt == sent


def test_a_paid_draft_resumes_even_after_its_face_was_removed(seedance, runs_dir):
    job, services = seedance
    _interrupted_draft(job, "Put @image1 on the person in @video1.")
    _forget_faces(services)
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    request = services.generators["seedance:piapi"].calls[0][1]
    assert job.stage_done("draft")
    assert request.primary.face_asset_id is None and request.primary.name == "frank"


def test_a_removed_face_does_not_block_the_1080p_promote(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    _forget_faces(services)
    generate(job, services, "final", runs_dir=runs_dir, **QUIET)
    request = services.generators["seedance:piapi"].calls[1][1]
    assert request.final_resolution == "1080p" and request.draft_task_id == "p-draft"
    assert request.primary.face_asset_id is None and job.stage_done("final")


def test_a_removed_face_still_blocks_a_new_720p_final(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    _forget_faces(services)
    with pytest.raises(FaceError, match="charswap face add frank"):
        generate(job, services, "final", runs_dir=runs_dir, final_resolution="720p", **QUIET)
    assert len(services.generators["seedance:piapi"].calls) == 1  # only the draft


def test_a_720p_final_needs_the_drafts_saved_prompt(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    draft = job.state["stages"]["draft"]
    job.mark_stage("draft", fingerprint=draft["fingerprint"])  # an older state: no prompt saved
    with pytest.raises(PromptError, match="prompt wasn't saved"):
        generate(job, services, "final", runs_dir=runs_dir, final_resolution="720p", **QUIET)
    assert len(services.generators["seedance:piapi"].calls) == 1


def test_a_new_draft_after_archiving_sends_the_edited_prompt(seedance, runs_dir):
    job, services = seedance
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    pipeline.archive_stale_draft(job)
    edited = "Turn the person in @video1 into @image1, nothing else."
    write_prompt_file(job).write_text(edited)
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.generators["seedance:piapi"].calls[1][1].prompt == edited
    assert job.state["stages"]["draft"]["prompt"] == edited


# ---- the job's provider (WaveSpeed plan) ---------------------------------------------
def _old_seedance_job(runs_dir, tmp_path):
    """A Seedance job made before WaveSpeed: no provider saved."""
    upload = tmp_path / "old-clip.mp4"
    upload.write_bytes(b"v")
    return Job.create(runs_dir, upload, generator="seedance", mode="depth")


PIAPI_SIGNS = {
    "saved face": lambda job: job.update(face_primary="frank"),
    "second saved face": lambda job: job.update(face_secondary="sam"),
    "second character": lambda job: job.update(character_secondary="character-secondary.jpg"),
    "draft record": lambda job: job.path("draft-prediction.json").write_text("{}"),
    "final record": lambda job: job.path("final-prediction.json").write_text("{}"),
    "draft video": lambda job: job.path("draft-raw.mp4").write_bytes(b"v"),
    "final video": lambda job: job.path("final-raw.mp4").write_bytes(b"v"),
    "finished draft": lambda job: job.mark_stage("draft"),
    "finished final": lambda job: job.mark_stage("final"),
    "PiAPI charge": lambda job: job.append_cost(
        {"step": "draft", "usd": 0.99, "provider": "piapi"}
    ),
}


@pytest.mark.parametrize("sign", list(PIAPI_SIGNS))
def test_an_old_seedance_job_with_piapi_work_is_piapi(runs_dir, tmp_path, sign):
    job = _old_seedance_job(runs_dir, tmp_path)
    PIAPI_SIGNS[sign](job)
    assert pipeline.generator_key(job) == "seedance:piapi"


def test_an_old_seedance_job_without_piapi_work_is_wavespeed(runs_dir, tmp_path):
    job = _old_seedance_job(runs_dir, tmp_path)
    job.mark_stage("draft", "failed", error="x")  # failed before any record: not PiAPI work
    job.append_cost({"step": "prepare", "usd": 0.06})  # prep charges name no provider
    job.append_cost({"step": "draft", "usd": 0.88, "provider": "wavespeed"})
    assert pipeline.generator_key(job) == "seedance:wavespeed"


def test_a_saved_provider_wins_and_wan_is_wan(runs_dir, tmp_path, wan_job):
    job = _old_seedance_job(runs_dir, tmp_path)
    job.update(provider="wavespeed")
    job.path("draft-prediction.json").write_text("{}")
    assert pipeline.generator_key(job) == "seedance:wavespeed"
    assert pipeline.generator_key(wan_job) == "wan"


def test_generator_key_never_writes(runs_dir, tmp_path):
    job = _old_seedance_job(runs_dir, tmp_path)
    before = job.path("state.json").read_bytes()
    assert pipeline.generator_key(job) == "seedance:wavespeed"
    assert job.path("state.json").read_bytes() == before


def test_pin_provider_saves_the_resolved_provider_once(runs_dir, tmp_path, wan_job):
    job = _old_seedance_job(runs_dir, tmp_path)
    assert pipeline.pin_provider(job) == "seedance:wavespeed"
    assert job.state["provider"] == "wavespeed"
    job.path("draft-prediction.json").write_text('{"id": "p1", "status": "processing"}')
    assert pipeline.pin_provider(job) == "seedance:wavespeed"  # its own record never flips it
    assert pipeline.pin_provider(wan_job) == "wan" and "provider" not in wan_job.state


def test_an_unknown_saved_provider_is_an_error(runs_dir, tmp_path):
    job = _old_seedance_job(runs_dir, tmp_path)
    job.update(provider="sora")
    with pytest.raises(JobError, match="unknown Seedance provider 'sora'"):
        pipeline.generator_key(job)


def test_finals_come_from_each_generator():
    assert pipeline.FINAL_RESOLUTIONS == {
        "wan": ("720p",),
        "seedance:wavespeed": ("720p", "1080p"),
        "seedance:piapi": ("1080p", "720p"),
    }


@pytest.fixture
def wavespeed(runs_dir, tmp_path):
    """An approved depth_mesh job with a plain character photo and no saved provider."""
    upload = write_video(tmp_path / "talk.mp4", [solid((90, 60, 30))] * 108, audio_seconds=4.5)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(photo, "JPEG")
    job = Job.create(runs_dir, upload, generator="seedance", mode="depth_mesh", character=photo)
    for name in MODES["depth_mesh"].review_files:
        if name.endswith(".json"):  # face-landmarks.json
            job.path(name).write_text('{"fps": 24, "frames": []}')
        else:
            write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in MODES["depth_mesh"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="lets get it", scene="A hotel lobby.")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    output = write_video(tmp_path / "wavespeed-out.mp4", [solid((20, 200, 20))] * 96)
    services = Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": FakeGenerator(output)},
    )
    return job, services


def test_generate_saves_the_provider_before_the_paid_create(wavespeed, runs_dir):
    job, services = wavespeed
    fake = services.generators["seedance:wavespeed"]
    seen = []
    original = fake.generate

    def spy(request, stage, *, record, dest, on_created=None):
        seen.append(json.loads((record.parent / "state.json").read_text()).get("provider"))
        return original(request, stage, record=record, dest=dest, on_created=on_created)

    fake.generate = spy
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert seen == ["wavespeed"]
    assert pipeline.generator_key(job) == "seedance:wavespeed"  # its record exists now


def test_a_missing_provider_generator_names_its_key(wavespeed, runs_dir):
    job, services = wavespeed
    services.generators.clear()
    with pytest.raises(StageError, match="No seedance:wavespeed generator"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)


def test_a_wavespeed_job_prompt_uses_wavespeeds_names(wavespeed):
    job, _ = wavespeed
    text = write_prompt_file(job).read_text()
    assert "@Video 1" in text and "@Image 1" in text and "@video1" not in text
    assert "lets get it" in text and generation_prompt(job, "draft") == text


def test_a_wavespeed_prompt_with_piapi_names_is_refused(wavespeed):
    job, _ = wavespeed
    write_prompt_file(job).write_text("Swap @video1 for @image1.")
    with pytest.raises(PromptError, match="must mention @Video 1"):
        generation_prompt(job, "draft")
