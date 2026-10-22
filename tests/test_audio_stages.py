import numpy as np
import pytest

from character_swap.jobs import Job
from character_swap.media.audio import read_audio
from character_swap.stages import StageError
from character_swap.stages.pitch import pitch_vocals
from character_swap.stages.separate import DEMUCS_VERSION, separate_vocals
from fakes import FakeReplicate
from helpers import solid, tone, write_video, write_wav


@pytest.fixture
def job(runs_dir, tmp_path):
    video = write_video(tmp_path / "in.mp4", [solid((1, 1, 1))] * 24, audio_seconds=1)
    return Job.create(runs_dir, video, generator="seedance", mode="depth")


def stem_files(tmp_path):
    tones = {"vocals": 300, "drums": 80, "bass": 60, "other": 500}
    paths = {n: write_wav(tmp_path / f"{n}.wav", tone(1, f) * 0.2) for n, f in tones.items()}
    return paths, {n: f"u/{n}" for n in paths}, {f"u/{n}": p for n, p in paths.items()}


def test_separate_writes_vocals_and_music(job, tmp_path):
    paths, urls, files = stem_files(tmp_path)
    fake = FakeReplicate(outputs={DEMUCS_VERSION: urls}, files=files)
    separate_vocals(job, fake)
    assert fake.runs[0]["input"]["model_name"] == "htdemucs"
    music = read_audio(job.path("music.wav"))
    expected = sum(read_audio(paths[n]) for n in ("drums", "bass", "other"))
    assert music.shape[0] == 2
    n = expected.shape[1]  # music is padded to the original track's (AAC-padded) length
    assert np.abs(music[:, :n] - expected).max() < 1e-3


@pytest.mark.parametrize("missing", ["vocals", "drums"])
def test_separate_rejects_incomplete_stems(job, tmp_path, missing):
    _, urls, files = stem_files(tmp_path)
    urls[missing] = None
    fake = FakeReplicate(outputs={DEMUCS_VERSION: urls}, files=files)
    with pytest.raises(StageError, match="incomplete"):
        separate_vocals(job, fake)


def test_separate_rejects_non_dict_output(job):
    fake = FakeReplicate(outputs={DEMUCS_VERSION: "https://x/y.zip"})
    with pytest.raises(StageError, match="demucs-prediction.json"):
        separate_vocals(job, fake)


def test_separate_ignores_unexpected_stem_names(job, tmp_path):
    _, urls, files = stem_files(tmp_path)
    urls["../../evil"] = "u/vocals"
    fake = FakeReplicate(outputs={DEMUCS_VERSION: urls}, files=files)
    separate_vocals(job, fake)
    assert not (job.path("..") / "evil.wav").exists()
    assert not job.path("stem-../../evil.wav").exists()


def test_separate_expired_download_names_record(job, tmp_path):
    _, urls, files = stem_files(tmp_path)
    fake = FakeReplicate(outputs={DEMUCS_VERSION: urls}, files=files, fail_download={"u/bass"})
    with pytest.raises(StageError, match="Delete .*demucs-prediction.json"):
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


def fake_rubberband(tmp_path, body):
    script = tmp_path / "rb"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return str(script)


def test_pitch_reports_rubberband_failure(tmp_path, monkeypatch):
    source = write_wav(tmp_path / "vocals.wav", tone(0.2))
    binary = fake_rubberband(tmp_path, "echo boom-detail >&2; exit 1")
    monkeypatch.setattr("shutil.which", lambda _name: binary)
    with pytest.raises(StageError, match="Rubber Band failed.*boom-detail"):
        pitch_vocals(source, tmp_path / "p.wav")


def test_pitch_timeout_is_stage_error(tmp_path, monkeypatch):
    import subprocess

    source = write_wav(tmp_path / "vocals.wav", tone(0.2))

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("shutil.which", lambda _name: "/bin/rb")
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(StageError, match="timed out"):
        pitch_vocals(source, tmp_path / "p.wav")


def test_pitch_clips_to_full_scale(tmp_path, monkeypatch):
    source = write_wav(tmp_path / "vocals.wav", tone(0.2))
    loud = write_wav(tmp_path / "loud.wav", tone(0.2) * 0.9)
    binary = fake_rubberband(tmp_path, f'cp {loud} "$7"')
    monkeypatch.setattr("shutil.which", lambda _name: binary)
    out = pitch_vocals(source, tmp_path / "p.wav")
    assert np.abs(read_audio(out)).max() <= 1.0
