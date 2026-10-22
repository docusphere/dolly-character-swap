import json

import numpy as np
import pytest

from character_swap.media.video import iter_frames
from character_swap.stages import StageError
from character_swap.stages.face_mesh import MeshStyle, ensure_model, track_and_draw
from helpers import solid, write_video

TRIANGLE = [(0.3, 0.3), (0.7, 0.3), (0.5, 0.7)]
STYLE = MeshStyle(lines=[(((0, 1), (1, 2), (2, 0)), (255, 0, 0), 2)])


class FakeDetector:
    def __init__(self, miss=()):
        self.miss = set(miss)
        self.calls = 0

    def detect(self, frame, timestamp_ms):
        index = self.calls
        self.calls += 1
        return [] if index in self.miss else [TRIANGLE]


def test_draws_mesh_and_saves_landmarks(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    report = track_and_draw(src, tmp_path / "mesh.mp4", FakeDetector(), STYLE, tmp_path)
    assert report == {"frames": 3, "tracked": 3, "missing_frames": []}
    frame = next(iter_frames(tmp_path / "mesh.mp4"))
    assert frame[:, :, 0].max() > 150  # red lines drawn
    landmarks = json.loads((tmp_path / "face-landmarks.json").read_text())
    assert landmarks["frames"][0]["faces"][0][0] == {"x": 0.3, "y": 0.3}


def test_draws_on_background_when_given(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    bg = write_video(tmp_path / "bg.mp4", [solid((0, 0, 200))] * 2)
    track_and_draw(src, tmp_path / "mesh.mp4", FakeDetector(), STYLE, tmp_path, background=bg)
    frame = next(iter_frames(tmp_path / "mesh.mp4"))
    assert frame[2, 2, 2] > 150  # background kept


def test_background_mismatch_refused(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    bg = write_video(tmp_path / "bg.mp4", [solid((0, 0, 0))] * 2)
    with pytest.raises(StageError, match="frame count"):
        track_and_draw(src, tmp_path / "m.mp4", FakeDetector(), STYLE, tmp_path, background=bg)


def test_tracking_gaps_raise_with_frames(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    with pytest.raises(StageError, match=r"\[1\]"):
        track_and_draw(src, tmp_path / "m.mp4", FakeDetector(miss={1}), STYLE, tmp_path)
    report = json.loads((tmp_path / "face-tracking.json").read_text())
    assert report["missing_frames"] == [1]


def test_ensure_model_downloads_once(tmp_path):
    calls = []

    def fake_download(url, target):
        calls.append(url)
        np.zeros(1).tofile(target)

    first = ensure_model(tmp_path, download=fake_download)
    second = ensure_model(tmp_path, download=fake_download)
    assert first == second and first.exists() and len(calls) == 1
