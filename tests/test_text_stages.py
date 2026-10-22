from character_swap.jobs import Job
from character_swap.stages.describe import DESCRIBE_MODEL, describe_scene
from character_swap.stages.transcribe import WHISPER_VERSION, transcribe
from fakes import FakeReplicate
from helpers import solid, tone, write_video, write_wav


def make_job(runs_dir, tmp_path):
    video = write_video(tmp_path / "in.mp4", [solid((5, 5, 5))] * 24, audio_seconds=1)
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    write_video(job.path("original.mp4"), [solid((5, 5, 5))] * 24)
    write_wav(job.path("vocals.wav"), tone(1))
    return job


def test_transcribe_saves_text(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(
        outputs={WHISPER_VERSION: {"transcription": "  hello world ", "segments": []}}
    )
    assert transcribe(job, fake) == "hello world"
    assert job.state["transcript"] == "hello world"
    assert fake.uploads[0].name == "vocals.wav"
    assert fake.runs[0]["input"] == {
        "audio": "https://fake.replicate/vocals.wav",
        "transcription": "plain text",
    }


def test_transcribe_handles_missing_text(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": None}})
    assert transcribe(job, fake) == ""


def test_describe_scene_joins_streamed_tokens(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={DESCRIBE_MODEL: ["A bright ", "studio ", "with plants."]})
    assert describe_scene(job, fake) == "A bright studio with plants."
    assert job.path("scene.jpg").exists()
    sent = fake.runs[0]["input"]
    assert "people" in sent["prompt"].lower()
    assert sent["image_input"] == ["https://fake.replicate/scene.jpg"]
    assert fake.runs[0]["key"] == DESCRIBE_MODEL


def test_describe_scene_accepts_plain_string(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={DESCRIBE_MODEL: "A  quiet   office."})
    assert describe_scene(job, fake) == "A quiet office."
