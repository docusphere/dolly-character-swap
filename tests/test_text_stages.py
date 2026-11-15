import pytest

from character_swap.jobs import Job
from character_swap.providers.errors import ProviderError
from character_swap.stages import StageError
from character_swap.stages.describe import DESCRIBE_MODEL, describe_scene, use_setting
from character_swap.stages.transcribe import WHISPER_VERSION, transcribe
from fakes import FakeReplicate, RefusingReplicate
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
        "language": "auto",
        "condition_on_previous_text": False,
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


def test_transcribe_uses_the_job_language(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    job.update(language="es")
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": "hola"}})
    transcribe(job, fake)
    assert fake.runs[0]["input"]["language"] == "es"


def test_jobs_from_before_the_language_option_use_auto(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    job.update(language=None)
    fake = FakeReplicate(outputs={WHISPER_VERSION: {"transcription": "hi"}})
    transcribe(job, fake)
    assert fake.runs[0]["input"]["language"] == "auto"


def test_a_refused_description_names_the_free_way_on(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    fake = RefusingReplicate(refuse=DESCRIBE_MODEL)
    with pytest.raises(StageError, match=r"it said: User Input Error\)") as info:
        describe_scene(job, fake)
    message = str(info.value)
    assert f'charswap resume {job.id} --setting "a dim room with ..."' in message
    assert "Deleting describe-prediction.json and resuming asks it again" in message
    with pytest.raises(StageError, match="--setting"):  # a resume says it again, buying nothing
        describe_scene(job, fake)
    assert len(fake.runs) == 1


def test_other_description_errors_keep_their_own_message(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    job.path("describe-prediction.json").write_text('{"status": "creating"}')

    class Unconfirmed(FakeReplicate):
        def run(self, **kwargs):
            raise ProviderError("The request was sent but not confirmed")

    with pytest.raises(ProviderError, match="not confirmed"):
        describe_scene(job, Unconfirmed())


def test_a_setting_you_wrote_needs_no_call(runs_dir, tmp_path):
    job = make_job(runs_dir, tmp_path)
    assert use_setting(job, "A dim room.") == "A dim room."
    assert job.state["scene"] == "A dim room." and job.path("scene.jpg").exists()
