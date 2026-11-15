import wave

import av
import pytest

from character_swap.media import audio
from character_swap.media.audio import (
    DurationMismatchError,
    NoAudioError,
    add_original_soundtrack,
    copy_video_only,
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


# ---- Wan output (Plan 2a) ----------------------------------------------------------
def video_rate(path):
    with av.open(str(path)) as container:
        return round(float(container.streams.video[0].average_rate))


def test_restore_trims_a_slightly_long_generated_video(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 40, fps=30)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original)
    expected = round(stream_seconds(original, "audio") * 30)
    assert abs(count_frames(result) - expected) <= 1 and video_rate(result) == 30
    gap = stream_seconds(result, "video") - stream_seconds(result, "audio")
    assert abs(gap) <= 0.25


def test_restore_refuses_a_much_longer_generated_video(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 60, fps=30)
    with pytest.raises(DurationMismatchError):
        restore_original_audio(generated, original, tmp_path / "final.mp4")


def test_wan_like_30fps_output_keeps_its_rate_and_the_original_audio(tmp_path):
    original = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 48, audio_seconds=2)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 60, fps=30)
    result = restore_original_audio(generated, original, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(original) and video_rate(result) == 30
    assert count_frames(result) == 60


def test_soundtrack_replaces_the_generated_audio(tmp_path):
    upload = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(
        tmp_path / "gen.mp4", [solid((150, 150, 150))] * 30, fps=30, audio_seconds=0.5
    )
    result = add_original_soundtrack(generated, upload, tmp_path / "final.mp4")
    assert audio_packets(result) == audio_packets(upload)
    with av.open(str(result)) as container:
        assert len(container.streams.audio) == 1


def test_silent_upload_gives_a_silent_result(tmp_path):
    upload = write_video(tmp_path / "upload.mp4", [solid((40, 40, 40))] * 24)
    generated = write_video(
        tmp_path / "gen.mp4", [solid((150, 150, 150))] * 30, fps=30, audio_seconds=1
    )
    result = add_original_soundtrack(generated, upload, tmp_path / "final.mp4")
    with av.open(str(result)) as container:
        assert container.streams.video and not container.streams.audio
    assert count_frames(result) == 30 and video_rate(result) == 30


def test_copy_video_only_drops_audio(tmp_path):
    source = write_video(tmp_path / "v.mp4", [solid((10, 10, 10))] * 12, audio_seconds=0.5)
    result = copy_video_only(source, tmp_path / "silent.mp4")
    with av.open(str(result)) as container:
        assert not container.streams.audio
    assert count_frames(result) == 12


def test_restore_can_cut_the_original_sound_at_a_length(tmp_path):
    original = write_video(tmp_path / "orig.mp4", [solid((1, 1, 1))] * 72, audio_seconds=3)
    generated = write_video(tmp_path / "gen.mp4", [solid((2, 2, 2))] * 48)  # 2 s, silent
    result = restore_original_audio(generated, original, tmp_path / "out.mp4", seconds=2.0)
    kept = audio_packets(result)
    assert kept == audio_packets(original)[: len(kept)]  # the same packets, just fewer
    assert 1.95 <= stream_seconds(result, "audio") <= 2.05


def test_soundtrack_passes_the_cut_through(tmp_path):
    original = write_video(tmp_path / "orig.mp4", [solid((1, 1, 1))] * 72, audio_seconds=3)
    generated = write_video(tmp_path / "gen.mp4", [solid((2, 2, 2))] * 48)
    result = add_original_soundtrack(generated, original, tmp_path / "out.mp4", seconds=2.0)
    assert stream_seconds(result, "audio") <= 2.05


def test_restore_cut_works_when_the_original_audio_starts_late(tmp_path):
    base = write_video(tmp_path / "base.mp4", [solid((1, 1, 1))] * 96, audio_seconds=4)
    shifted = tmp_path / "shifted.mp4"
    with av.open(str(base)) as source, av.open(str(shifted), "w") as output:
        video_out = output.add_stream_from_template(source.streams.video[0])
        audio_out = output.add_stream_from_template(source.streams.audio[0])
        offset = round(1024 / source.streams.audio[0].rate / source.streams.audio[0].time_base)
        for packet in source.demux():
            if packet.dts is None:
                continue
            if packet.stream.type == "audio":
                packet.pts += offset
                packet.dts += offset
                packet.stream = audio_out
            else:
                packet.stream = video_out
            output.mux(packet)
    generated = write_video(tmp_path / "gen.mp4", [solid((2, 2, 2))] * 48)  # 2 s
    result = restore_original_audio(generated, shifted, tmp_path / "out.mp4", seconds=2.0)
    kept = audio_packets(result)
    assert kept == audio_packets(shifted)[: len(kept)]
    assert abs(stream_seconds(result, "audio") - 2.0) <= 0.03


def test_restore_without_fit_never_retimes_the_picture(tmp_path, monkeypatch):
    original = write_video(
        tmp_path / "scene.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24
    )
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)  # 0.33 s short
    monkeypatch.setattr(audio, "_fit_video", lambda *a: pytest.fail("the picture was re-timed"))
    with pytest.raises(DurationMismatchError):
        restore_original_audio(generated, original, tmp_path / "out.mp4", fit=False)


def test_restore_without_fit_copies_the_sound_when_it_fits(tmp_path):
    original = write_video(tmp_path / "scene.mp4", [solid((40, 40, 40))] * 24, audio_seconds=1)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 24)
    result = restore_original_audio(generated, original, tmp_path / "out.mp4", fit=False)
    assert audio_packets(result) == audio_packets(original)


def test_soundtrack_passes_fit_through(tmp_path, monkeypatch):
    upload = write_video(tmp_path / "scene.mp4", [solid((40, 40, 40))] * 34, audio_seconds=34 / 24)
    generated = write_video(tmp_path / "gen.mp4", [solid((150, 150, 150))] * 26)
    monkeypatch.setattr(audio, "_fit_video", lambda *a: pytest.fail("the picture was re-timed"))
    with pytest.raises(DurationMismatchError):
        add_original_soundtrack(generated, upload, tmp_path / "out.mp4", fit=False)
