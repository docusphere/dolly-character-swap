import pytest

from character_swap.jobs import Job
from character_swap.media.audio import read_audio
from character_swap.stages.pitch import pitch_vocals
from character_swap.stages.separate import DEMUCS_VERSION, StageError, separate_vocals
from fakes import FakeReplicate
from helpers import solid, tone, write_video, write_wav


@pytest.fixture
def job(runs_dir, tmp_path):
    video = write_video(tmp_path / "in.mp4", [solid((1, 1, 1))] * 24, audio_seconds=1)
    return Job.create(runs_dir, video, generator="seedance", mode="depth")


def test_separate_writes_vocals_and_music(job, tmp_path):
    vocals = write_wav(tmp_path / "v.wav", tone(1, 300))
    drums = write_wav(tmp_path / "d.wav", tone(1, 80))
    fake = FakeReplicate(
        outputs={DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d", "bass": None}},
        files={"u/v": vocals, "u/d": drums},
    )
    separate_vocals(job, fake)
    assert job.path("vocals.wav").exists() and job.path("music.wav").exists()
    assert fake.runs[0]["input"]["model_name"] == "htdemucs"
    assert read_audio(job.path("music.wav")).shape[0] == 2


def test_separate_rejects_incomplete_stems(job, tmp_path):
    vocals = write_wav(tmp_path / "v.wav", tone(1))
    fake = FakeReplicate(outputs={DEMUCS_VERSION: {"vocals": "u/v"}}, files={"u/v": vocals})
    with pytest.raises(StageError, match="incomplete"):
        separate_vocals(job, fake)


@pytest.mark.rubberband
def test_pitch_keeps_length(tmp_path):
    source = write_wav(tmp_path / "vocals.wav", tone(1, 300))
    out = pitch_vocals(source, tmp_path / "pitched.wav", semitones=3)
    assert read_audio(out).shape == read_audio(source).shape


def test_pitch_without_binary_explains_install(tmp_path):
    source = write_wav(tmp_path / "vocals.wav", tone(0.2))
    with pytest.raises(StageError, match="brew install rubberband"):
        pitch_vocals(source, tmp_path / "p.wav", rubberband="definitely-not-installed")
