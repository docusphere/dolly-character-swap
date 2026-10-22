import wave

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
from character_swap.media.video import count_frames, stream_seconds
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


def _held_setup(tmp_path):
    original = write_video(
        tmp_path / "upload.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24
    )
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)
    return original, generated


def test_held_restore_leaves_no_temp_files(tmp_path):
    original, generated = _held_setup(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    restore_original_audio(generated, original, out_dir / "final.mp4")
    assert [p.name for p in out_dir.iterdir()] == ["final.mp4"]


def test_held_restore_extends_video_to_audio_length(tmp_path):
    original, generated = _held_setup(tmp_path)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    expected = round(stream_seconds(original, "audio") * 24)
    assert abs(count_frames(result) - expected) <= 1


def test_restore_without_original_audio_fails_fast(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 34)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(NoAudioError):
        restore_original_audio(generated, original, out_dir / "final.mp4")
    assert list(out_dir.iterdir()) == []


def test_read_audio_with_zero_audio_frames_raises(tmp_path):
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
    with pytest.raises(NoAudioError):
        read_audio(path)
