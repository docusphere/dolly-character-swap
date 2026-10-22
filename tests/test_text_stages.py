import pytest

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


@pytest.mark.parametrize(
    "bad", ["just text", ["a"], {"transcription": 5}, {"transcription": ["x"]}]
)
def test_transcribe_rejects_bad_output_shape(runs_dir, tmp_path, bad):
    from character_swap.stages import StageError

    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={WHISPER_VERSION: bad})
    with pytest.raises(StageError, match="whisper-prediction.json"):
        transcribe(job, fake)


@pytest.mark.parametrize("bad", [None, [1, 2], "   ", {"a": 1}, [""]])
def test_describe_rejects_bad_output(runs_dir, tmp_path, bad):
    from character_swap.stages import StageError

    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={DESCRIBE_MODEL: bad})
    with pytest.raises(StageError, match="describe-prediction.json"):
        describe_scene(job, fake)


def test_transcribe_none_output_is_bad_shape(runs_dir, tmp_path):
    from character_swap.stages import StageError

    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(outputs={WHISPER_VERSION: None})
    with pytest.raises(StageError, match="whisper-prediction.json"):
        transcribe(job, fake)


def test_text_stages_resume_without_second_run(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = FakeReplicate(
        outputs={WHISPER_VERSION: {"transcription": "hi"}, DESCRIBE_MODEL: ["A room."]}
    )
    for _ in range(2):
        transcribe(job, fake)
        describe_scene(job, fake)
    assert len(fake.runs) == 2
