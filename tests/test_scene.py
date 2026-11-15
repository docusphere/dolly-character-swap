import numpy as np
import pytest

from character_swap.media import scene
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 160, 30)


def at(frame: int) -> float:
    """The time of a frame at 23.976 fps."""
    return float(frame / SCENE_RATE)


@pytest.fixture
def small_boxes(monkeypatch):
    monkeypatch.setattr(scene, "MIN_BOX", 16)  # the test clips are far under 300 px


# ---- shot cuts -----------------------------------------------------------------
def test_cuts_are_the_first_frames_of_each_new_shot(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 40), (GREEN, 20)])
    assert scene.find_cuts(clip) == pytest.approx([at(30), at(70)], abs=1e-4)


def test_a_slow_fade_is_not_a_cut(tmp_path):
    frames = [solid((4 * i, 4 * i, 4 * i)) for i in range(48)]  # 4 of 255 a frame
    clip = write_video(tmp_path / "fade.mp4", frames, fps=SCENE_RATE)
    assert scene.find_cuts(clip) == []


def test_a_busy_shot_needs_a_change_well_above_its_own(tmp_path):
    rng = np.random.default_rng(1)
    frames = [rng.integers(0, 256, (48, 64, 3), dtype="uint8") for _ in range(48)]
    clip = write_video(tmp_path / "noise.mp4", frames, fps=SCENE_RATE)
    assert scene.find_cuts(clip) == []  # every frame changes a lot, so none stands out


def test_snap_moves_to_the_nearest_cut_within_the_window():
    cuts = [2.628, 15.432]
    assert scene.snap(2.6, cuts) == 2.628
    assert scene.snap(15.4, cuts) == 15.432
    assert scene.snap(9.0, cuts) == 9.0
    assert scene.snap(2.95, cuts) == 2.95  # 0.322 s away: used as typed
    assert scene.snap(2.6, []) == 2.6


def test_a_clip_without_video_is_refused(tmp_path):
    sound = write_wav(tmp_path / "a.wav", tone(1))
    with pytest.raises(scene.SceneError, match="a.wav has no video frames"):
        scene.find_cuts(sound)


# ---- black bars ----------------------------------------------------------------
def test_bars_are_found_and_left_out(tmp_path, small_boxes):
    clip = write_shots(tmp_path / "b.mp4", [(RED, 24)], size=(64, 96), bars=16)
    assert scene.find_bars(clip) == (0, 16, 64, 64)


def test_a_bright_object_entering_a_bar_keeps_that_bar(tmp_path, small_boxes):
    frames = [solid(RED, (64, 96)) for _ in range(24)]
    for frame in frames:
        frame[:16] = 0
        frame[-16:] = 0
    frames[10][:16, 16:32] = 255  # something bright crosses the top bar once
    clip = write_video(tmp_path / "b.mp4", frames, fps=SCENE_RATE)
    assert scene.find_bars(clip) == (0, 0, 64, 80)


def test_only_the_frames_of_the_part_count(tmp_path, small_boxes):
    frames = [solid(RED, (64, 96)) for _ in range(48)]
    for frame in frames[24:]:  # the second shot has bars, the first does not
        frame[:16] = 0
        frame[-16:] = 0
    clip = write_video(tmp_path / "b.mp4", frames, fps=SCENE_RATE)
    assert scene.find_bars(clip) == (0, 0, 64, 96)
    assert scene.find_bars(clip, at(24), at(48)) == (0, 16, 64, 64)


def test_a_picture_under_the_minimum_is_never_cropped(tmp_path):
    clip = write_shots(tmp_path / "b.mp4", [(RED, 24)], size=(64, 96), bars=16)
    assert scene.find_bars(clip) == (0, 0, 64, 96)  # 64 x 64 is under MIN_BOX (300 px)


def test_a_black_scene_is_never_cropped(tmp_path, small_boxes):
    clip = write_shots(tmp_path / "b.mp4", [((0, 0, 0), 24)])
    assert scene.find_bars(clip) == (0, 0, 64, 48)


def test_a_window_without_frames_is_refused(tmp_path):
    clip = write_shots(tmp_path / "b.mp4", [(RED, 24)])
    with pytest.raises(scene.SceneError, match="no frames between 5.00 s and 6.00 s"):
        scene.find_bars(clip, 5.0, 6.0)
