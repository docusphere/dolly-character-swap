import io
import zipfile

import pytest

from character_swap import pipeline
from character_swap.jobs import Job
from character_swap.pipeline import GENERATOR_LIMITS, Services, prepare, repair_with_rgb
from character_swap.review import decide, fingerprint, review_files, status
from character_swap.stages import StageError
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.face_mesh import MeshStyle
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION
from fakes import FakeReplicate
from helpers import solid, tone, write_mask_pngs, write_video, write_wav

FRAMES = 96  # 4 s at 24 fps (Seedance minimum)


class FakeDetector:
    style = MeshStyle(lines=[(((0, 1),), (255, 0, 0), 1)])

    def detect(self, frame, timestamp_ms):
        return [[(0.4, 0.4), (0.6, 0.6)]]

    def close(self):
        pass


def zipped_masks(tmp_path, count):
    folder = write_mask_pngs(tmp_path / "zipsrc", count)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for png in folder.iterdir():
            archive.writestr(png.name, png.read_bytes())
    return buffer.getvalue()


@pytest.fixture
def services(tmp_path):
    vocals = write_wav(tmp_path / "v.wav", tone(4, 300))
    drums = write_wav(tmp_path / "d.wav", tone(4, 80))
    bass = write_wav(tmp_path / "b.wav", tone(4, 60))
    other = write_wav(tmp_path / "o.wav", tone(4, 500))
    depth = write_video(tmp_path / "depth.mp4", [solid((100, 100, 100))] * FRAMES)
    fake = FakeReplicate(
        outputs={
            DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d", "bass": "u/b", "other": "u/o"},
            WHISPER_VERSION: {"transcription": "hello"},
            DESCRIBE_MODEL: ["A studio."],
            f"{DEPTH_MODEL}-latest": "u/depth",
            SAM_VERSION: "u/zip",
        },
        files={
            "u/v": vocals,
            "u/d": drums,
            "u/b": bass,
            "u/o": other,
            "u/depth": depth,
            "u/zip": zipped_masks(tmp_path, FRAMES),
        },
    )
    return Services(replicate=fake, face_detector=FakeDetector, rubberband="cat-not-used")


@pytest.fixture
def make_job(runs_dir, tmp_path):
    def _make(mode, generator="seedance", frames=FRAMES, character=None):
        video = write_video(
            tmp_path / f"in-{mode}.mp4", [solid((180, 40, 40))] * frames, audio_seconds=frames / 24
        )
        return Job.create(runs_dir, video, generator=generator, mode=mode, character=character)

    return _make


@pytest.fixture(autouse=True)
def no_rubberband(monkeypatch):
    def fake_pitch(vocals, dst, **kwargs):
        dst.write_bytes(vocals.read_bytes())
        return dst

    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)


def test_depth_mode_produces_reviewable_prepared_video(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    for name in ["original.mp4", "depth.mp4", "mask.mp4", "composite-silent.mp4", "prepared.mp4"]:
        assert job.path(name).exists(), name
    assert job.state["transcript"] == "hello" and job.state["scene"] == "A studio."
    assert status(job)["status"] == "pending"


def test_prepare_is_resumable_and_does_not_rerun_paid_stages(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    runs_before = len(services.replicate.runs)
    prepare(job, services, status=lambda _m: None)
    assert len(services.replicate.runs) == runs_before


def test_failed_stage_resumes_without_repaying_earlier_stages(make_job, services, monkeypatch):
    job = make_job("depth")
    boom = {"on": True}
    real_segment = pipeline.segment

    def flaky_segment(*args, **kwargs):
        if boom["on"]:
            raise RuntimeError("simulated timeout")
        return real_segment(*args, **kwargs)

    monkeypatch.setattr("character_swap.pipeline.segment", flaky_segment)
    with pytest.raises(StageError, match="simulated timeout") as info:
        prepare(job, services, status=lambda _m: None)
    assert isinstance(info.value.__cause__, RuntimeError)
    paid_before = [r["key"] for r in services.replicate.runs]
    boom["on"] = False
    prepare(job, services, status=lambda _m: None)
    paid_after = [r["key"] for r in services.replicate.runs]
    assert paid_after[: len(paid_before)] == paid_before
    assert paid_after[len(paid_before) :] == [SAM_VERSION]
    assert job.path("prepared.mp4").exists()


def test_face_mesh_mode_skips_depth(make_job, services):
    job = make_job("face_mesh")
    prepare(job, services, status=lambda _m: None)
    keys = [run["key"] for run in services.replicate.runs]
    assert SAM_VERSION not in keys and f"{DEPTH_MODEL}-latest" not in keys
    assert job.path("face-landmarks.json").exists() and job.path("prepared.mp4").exists()


def test_depth_mesh_mode_keeps_depth_composite_and_adds_mesh(make_job, services):
    job = make_job("depth_mesh")
    prepare(job, services, status=lambda _m: None)
    assert job.path("depth-composite-silent.mp4").exists()
    assert job.path("face-landmarks.json").exists()


def test_direct_mode_only_normalizes(make_job, services, tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"png")
    job = make_job("direct", generator="wan", character=face)
    prepare(job, services, status=lambda _m: None)
    assert job.path("original.mp4").exists()
    assert services.replicate.runs == []


def test_too_short_for_seedance_fails_before_paid_calls(make_job, services):
    job = make_job("depth", frames=48)
    with pytest.raises(Exception, match="at least 4"):
        prepare(job, services, status=lambda _m: None)
    assert services.replicate.runs == []


def test_repair_reruns_sam_on_rgb_and_clears_approval(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    sam_runs = sum(1 for r in services.replicate.runs if r["key"] == SAM_VERSION)
    repair_with_rgb(job, services, status=lambda _m: None)
    assert sum(1 for r in services.replicate.runs if r["key"] == SAM_VERSION) == sam_runs + 1
    assert services.replicate.uploads[-1].name == "original.mp4"
    assert status(job)["status"] == "pending"
    assert job.state["mask_source"] == "rgb"


def test_repair_not_available_for_face_mesh(make_job, services):
    job = make_job("face_mesh")
    with pytest.raises(StageError, match="no SAM mask"):
        repair_with_rgb(job, services, status=lambda _m: None)


def test_limits_cover_both_generators():
    assert set(GENERATOR_LIMITS) == {"seedance", "wan"}


@pytest.mark.parametrize("mode", ["depth", "face_mesh", "depth_mesh"])
def test_prepare_produces_every_review_file(make_job, services, mode):
    job = make_job(mode)
    prepare(job, services, status=lambda _m: None)
    for name in review_files(job):
        assert job.path(name).exists(), name


def test_direct_mode_produces_every_review_file(make_job, services, tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"png")
    job = make_job("direct", generator="wan", character=face)
    prepare(job, services, status=lambda _m: None)
    for name in review_files(job):
        assert job.path(name).exists(), name


def test_crash_during_repair_is_healed_by_prepare(make_job, services, monkeypatch):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    before = job.path("prepared.mp4").stat().st_mtime_ns
    real = pipeline.composite_depth_subject

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("character_swap.pipeline.composite_depth_subject", boom)
    with pytest.raises(StageError, match="Repair: composite failed: disk full") as info:
        repair_with_rgb(job, services, status=lambda _m: None)
    assert isinstance(info.value.__cause__, RuntimeError)
    assert not job.stage_done("composite") and not job.stage_done("embed")
    monkeypatch.setattr("character_swap.pipeline.composite_depth_subject", real)
    prepare(job, services, status=lambda _m: None)
    for name in review_files(job):
        assert job.path(name).exists(), name
    assert (
        job.path("prepared.mp4").stat().st_mtime_ns != before
    )  # rebuilt (output is deterministic)


def test_depth_mesh_repair_leaves_stages_done_and_prepare_is_noop(make_job, services):
    job = make_job("depth_mesh")
    prepare(job, services, status=lambda _m: None)
    repair_with_rgb(job, services, status=lambda _m: None)
    assert all(job.stage_done(s) for s in ("composite", "face_mesh", "embed"))
    runs = len(services.replicate.runs)
    stamp = job.path("composite-silent.mp4").stat().st_mtime_ns
    prepare(job, services, status=lambda _m: None)
    assert len(services.replicate.runs) == runs
    assert job.path("composite-silent.mp4").stat().st_mtime_ns == stamp


def test_prepare_on_complete_job_keeps_approval(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    prepare(job, services, status=lambda _m: None)
    assert status(job)["status"] == "approved"


def test_prepare_that_runs_work_clears_approval(make_job, services):
    job = make_job("depth")
    prepare(job, services, status=lambda _m: None)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    job.reset_stages("embed")
    prepare(job, services, status=lambda _m: None)
    assert status(job)["status"] == "pending"


def test_status_callback_errors_do_not_abort_stages(make_job, services):
    job = make_job("depth")

    def bad(_m):
        raise ValueError("ui bug")

    with pytest.warns(UserWarning, match="ui bug"):
        prepare(job, services, status=bad)
    assert job.path("prepared.mp4").exists()


def test_clip_length_error_is_wrapped_but_readable(make_job, services):
    job = make_job("depth", frames=48)
    with pytest.raises(StageError, match="at least 4"):
        prepare(job, services, status=lambda _m: None)
