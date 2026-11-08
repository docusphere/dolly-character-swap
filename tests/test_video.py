import shutil
import subprocess

import av
import numpy as np
import pytest

from character_swap.media.video import (
    ClipLengthError,
    VideoWriter,
    count_frames,
    iter_frames,
    middle_frame,
    normalize,
    stream_seconds,
)
from helpers import solid, write_video


def test_writer_roundtrip(tmp_path):
    with VideoWriter(tmp_path / "out.mp4", 64, 64) as writer:
        for _ in range(5):
            writer.write(solid((200, 10, 10)))
    assert count_frames(tmp_path / "out.mp4") == 5
    first = next(iter_frames(tmp_path / "out.mp4"))
    assert first[32, 32, 0] > 180


def test_normalize_resamples_to_24fps_and_caps_size(tmp_path):
    src = write_video(tmp_path / "src.mp4", [solid((90, 90, 90), (1280, 720))] * 60, fps=30)
    meta = normalize(src, tmp_path / "norm.mp4", min_seconds=1, max_seconds=30)
    assert meta == {"frames": 48, "fps": 24, "width": 960, "height": 540, "seconds": 2.0}
    assert count_frames(tmp_path / "norm.mp4") == 48


def test_normalize_rejects_out_of_range_lengths(tmp_path):
    src = write_video(tmp_path / "src.mp4", [solid((1, 1, 1))] * 48)
    with pytest.raises(ClipLengthError, match="at most 1"):
        normalize(src, tmp_path / "a.mp4", min_seconds=0, max_seconds=1)
    with pytest.raises(ClipLengthError, match="at least 4"):
        normalize(src, tmp_path / "b.mp4", min_seconds=4, max_seconds=30)


def test_middle_frame_returns_image(tmp_path):
    frames = [solid((0, 0, 0))] * 3 + [solid((250, 250, 250))] * 3
    src = write_video(tmp_path / "src.mp4", frames)
    image = middle_frame(src)
    assert np.asarray(image)[10, 10, 0] > 200


def _empty_video(path):
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        out.start_encoding()  # writes the header even though no frame follows
    return path


def test_writer_exception_propagates_and_removes_partial_file(tmp_path):
    target = tmp_path / "partial.mp4"
    with pytest.raises(RuntimeError, match="boom"), VideoWriter(target, 64, 64) as writer:
        writer.write(solid((1, 2, 3)))
        raise RuntimeError("boom")
    assert not target.exists()


def test_writer_close_is_idempotent(tmp_path):
    writer = VideoWriter(tmp_path / "out.mp4", 64, 64)
    writer.write(solid((1, 2, 3)))
    writer.close()
    writer.close()
    assert count_frames(tmp_path / "out.mp4") == 1


def test_normalize_zero_frames_is_a_clear_error(tmp_path):
    src = _empty_video(tmp_path / "empty.mp4")
    with pytest.raises(ClipLengthError, match="no video frames"):
        normalize(src, tmp_path / "n.mp4", min_seconds=0, max_seconds=30)


def test_stream_seconds_measures_video_and_audio_streams(tmp_path):
    src = write_video(tmp_path / "v.mp4", [solid((1, 1, 1))] * 24, audio_seconds=1)
    assert abs(stream_seconds(src, "video") - 1.0) < 0.05
    assert abs(stream_seconds(src, "audio") - 1.0) < 0.1


def test_stream_seconds_rejects_missing_stream(tmp_path):
    src = write_video(tmp_path / "v.mp4", [solid((1, 1, 1))] * 4)
    with pytest.raises(ValueError, match="no audio"):
        stream_seconds(src, "audio")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg CLI not installed")
def test_normalize_applies_display_rotation(tmp_path):
    frame = solid((0, 0, 0), (64, 32))
    frame[:, :10] = (255, 0, 0)  # red band on the left edge of a landscape frame
    src = write_video(tmp_path / "src.mp4", [frame] * 24)
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-display_rotation", "90", "-i", str(src)]
        + ["-c", "copy", str(rotated)],
        check=True,
    )
    meta = normalize(rotated, tmp_path / "n.mp4", min_seconds=0, max_seconds=30)
    assert (meta["width"], meta["height"]) == (32, 64)
    out = next(iter_frames(tmp_path / "n.mp4"))
    # 90 degrees counter-clockwise display rotation: the left edge ends up at the bottom.
    assert out[60, 16, 0] > 180 and out[3, 16, 0] < 60


def test_normalize_24fps_identity_keeps_every_frame(tmp_path):
    colors = [(i * 10, 255 - i * 10, 40) for i in range(12)]
    src = write_video(tmp_path / "src.mp4", [solid(c) for c in colors], fps=24)
    normalize(src, tmp_path / "n.mp4", min_seconds=0, max_seconds=30)
    out = [f[32, 32, 0] for f in iter_frames(tmp_path / "n.mp4")]
    assert len(out) == 12
    assert all(abs(int(a) - c[0]) < 12 for a, c in zip(out, colors, strict=True))
    assert len({int(v) // 8 for v in out}) == 12


def test_middle_frame_picks_the_middle_of_an_odd_count(tmp_path):
    frames = [solid((0, 0, 0))] * 3 + [solid((250, 250, 250))] * 4
    src = write_video(tmp_path / "src.mp4", frames)
    assert np.asarray(middle_frame(src))[10, 10, 0] > 200


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg CLI not installed")
def test_normalize_nonzero_start_keeps_every_frame(tmp_path):
    colors = [(i * 5, 255 - i * 5, 40) for i in range(48)]
    src = write_video(tmp_path / "src.mp4", [solid(c) for c in colors], fps=24)
    shifted = tmp_path / "shifted.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-itsoffset", "0.3", "-i", str(src)]
        + ["-c", "copy", str(shifted)],
        check=True,
    )
    meta = normalize(shifted, tmp_path / "n.mp4", min_seconds=0, max_seconds=30)
    out = [int(f[32, 32, 0]) for f in iter_frames(tmp_path / "n.mp4")]
    assert meta["frames"] == len(out) == 48
    assert all(abs(a - c[0]) <= 2 for a, c in zip(out, colors, strict=True))
    assert all(
        b - a >= 2 for a, b in zip(out, out[1:], strict=False)
    )  # strictly advancing: no duplicates


def test_normalize_whole_seconds_drops_the_tail(tmp_path):
    src = write_video(tmp_path / "in.mp4", [solid((10, 20, 30))] * 60)  # 2.5 s at 24 fps
    facts = normalize(src, tmp_path / "out.mp4", min_seconds=1, max_seconds=30, whole_seconds=True)
    assert facts["frames"] == 48 and facts["seconds"] == 2.0
    assert count_frames(tmp_path / "out.mp4") == 48


def test_normalize_whole_seconds_still_checks_the_minimum(tmp_path):
    src = write_video(tmp_path / "in.mp4", [solid((10, 20, 30))] * 108)  # 4.5 s
    with pytest.raises(ClipLengthError, match="whole seconds it is 4s.*at least 5s"):
        normalize(src, tmp_path / "out.mp4", min_seconds=4.5, max_seconds=30, whole_seconds=True)
