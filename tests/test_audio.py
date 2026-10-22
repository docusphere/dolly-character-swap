import av
import pytest

from character_swap.media.audio import (
    DurationMismatchError,
    NoAudioError,
    embed_samples,
    read_audio,
    restore_original_audio,
    write_wav,
)
from helpers import solid, tone, write_video


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def test_wav_roundtrip(tmp_path):
    samples = tone(0.5)
    write_wav(tmp_path / "a.wav", samples)
    back = read_audio(tmp_path / "a.wav")
    assert back.shape[0] == 2 and abs(back.shape[1] - samples.shape[1]) < 1100


def test_read_audio_without_track_raises(tmp_path):
    silent = write_video(tmp_path / "v.mp4", [solid((0, 0, 0))] * 4)
    with pytest.raises(NoAudioError):
        read_audio(silent)


def test_embed_samples_adds_aac_track(tmp_path):
    video = write_video(tmp_path / "v.mp4", [solid((10, 10, 10))] * 24)
    out = embed_samples(video, tone(1.0), tmp_path / "out.mp4")
    with av.open(str(out)) as container:
        assert container.streams.audio and container.streams.video


def test_restore_copies_original_audio_packets_exactly(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 24)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original)


def test_restore_holds_last_frame_for_small_gap(tmp_path):
    original = write_video(
        tmp_path / "upload.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24
    )
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original)


def test_restore_refuses_large_mismatch(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 48, audio_seconds=2)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 24)
    with pytest.raises(DurationMismatchError):
        restore_original_audio(generated, original, tmp_path / "final.mp4")
