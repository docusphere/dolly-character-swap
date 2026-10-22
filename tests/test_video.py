import numpy as np
import pytest

from character_swap.media.video import (
    ClipLengthError,
    VideoWriter,
    count_frames,
    iter_frames,
    middle_frame,
    normalize,
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
