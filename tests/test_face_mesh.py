import io
import json
from pathlib import Path

import numpy as np
import pytest

from character_swap.media.video import iter_frames
from character_swap.stages import StageError
from character_swap.stages.face_mesh import (
    MeshStyle,
    crop_box,
    ensure_model,
    to_full_frame,
    track_and_draw,
)
from helpers import solid, write_video

TRIANGLE = [(0.3, 0.3), (0.7, 0.3), (0.5, 0.7)]
STYLE = MeshStyle(lines=[(((0, 1), (1, 2), (2, 0)), (255, 0, 0), 2)])


class FakeDetector:
    style = STYLE

    def __init__(self, miss=(), points=None):
        self.miss = set(miss)
        self.points = points or TRIANGLE
        self.calls = 0
        self.timestamps = []

    def close(self):
        pass

    def detect(self, frame, timestamp_ms):
        index = self.calls
        self.calls += 1
        self.timestamps.append(timestamp_ms)
        return [] if index in self.miss else [self.points]


def test_draws_mesh_and_saves_landmarks(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    report = track_and_draw(src, tmp_path / "mesh.mp4", FakeDetector(), STYLE, tmp_path)
    assert report == {"frames": 3, "tracked": 3, "missing_frames": []}
    frame = next(iter_frames(tmp_path / "mesh.mp4"))
    assert frame[:, :, 0].max() > 150  # red lines drawn
    r, g, b = (int(v) for v in frame[19, 32])  # midpoint of the top edge on 64x64
    assert r > 150 and g < 100 and b < 100
    landmarks = json.loads((tmp_path / "face-landmarks.json").read_text())
    assert landmarks["frames"][0]["faces"][0][0] == {"x": 0.3, "y": 0.3}


def test_draws_on_background_when_given(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    bg = write_video(tmp_path / "bg.mp4", [solid((0, 0, 200))] * 2)
    track_and_draw(src, tmp_path / "mesh.mp4", FakeDetector(), STYLE, tmp_path, background=bg)
    frame = next(iter_frames(tmp_path / "mesh.mp4"))
    assert frame[2, 2, 2] > 150  # background kept
    assert frame[:, :, 0].max() > 150  # mesh drawn on top


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
    assert (tmp_path / "face-landmarks.json").exists()


def test_ensure_model_downloads_once(tmp_path):
    calls = []

    def fake_download(url, target):
        calls.append(url)
        np.zeros(1).tofile(target)

    first = ensure_model(tmp_path, download=fake_download)
    second = ensure_model(tmp_path, download=fake_download)
    assert first == second and first.exists() and len(calls) == 1


def test_timestamps_strictly_increase(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 4)
    detector = FakeDetector()
    track_and_draw(src, tmp_path / "mesh.mp4", detector, STYLE, tmp_path)
    assert all(b > a for a, b in zip(detector.timestamps, detector.timestamps[1:], strict=False))


def test_out_of_range_points_do_not_crash(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    detector = FakeDetector(points=[(-0.1, 0.5), (1.2, 0.5), (0.5, 1.2)])
    report = track_and_draw(src, tmp_path / "mesh.mp4", detector, STYLE, tmp_path)
    assert report["tracked"] == 2


def test_creates_missing_output_dirs(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    out = tmp_path / "a" / "b" / "mesh.mp4"
    track_and_draw(src, out, FakeDetector(), STYLE, tmp_path / "reports" / "x")
    assert out.exists() and (tmp_path / "reports" / "x" / "face-tracking.json").exists()


def test_empty_source_raises_stage_error(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(StageError, match="no video frames"):
        track_and_draw(empty, tmp_path / "m.mp4", FakeDetector(), STYLE, tmp_path)


def test_background_size_mismatch_refused(tmp_path):
    src = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    bg = write_video(tmp_path / "bg.mp4", [solid((0, 0, 0), size=(32, 32))] * 2)
    with pytest.raises(StageError, match=r"64x64.*32x32|32x32.*64x64"):
        track_and_draw(src, tmp_path / "m.mp4", FakeDetector(), STYLE, tmp_path, background=bg)


def test_ensure_model_download_failure_cleans_up(tmp_path):
    def broken(url, target):
        Path(target).write_bytes(b"partial")
        raise OSError("boom")

    with pytest.raises(StageError, match="Could not download the face model"):
        ensure_model(tmp_path, download=broken)
    assert not list(tmp_path.glob("face_landmarker*"))


def test_crop_box_odd_size():
    assert crop_box(101, 57) == (25, 50, 37)
    assert crop_box(1000, 800) == (250, 500, 520)


def test_to_full_frame_maps_crop_to_full():
    box = crop_box(200, 100)  # x0=50, w=100, h=65
    points = to_full_frame([(0.0, 0.0), (1.0, 1.0), (0.5, 0.5)], box, 200, 100)
    assert points[0] == (0.25, 0.0)
    assert points[1] == (0.75, 0.65)
    assert points[2] == (0.5, 0.325)


class _FakeResponse:
    def __init__(self, body: bytes, length: str | None):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Length": length} if length else {}

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_truncated_download_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url, timeout=None: _FakeResponse(b"x" * 40, "100")
    )
    with pytest.raises(StageError, match="truncated"):
        ensure_model(tmp_path)
    assert not list(tmp_path.glob("face_landmarker*"))


def test_complete_download_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url, timeout=None: _FakeResponse(b"x" * 40, "40")
    )
    assert ensure_model(tmp_path).read_bytes() == b"x" * 40
