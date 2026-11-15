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


# ---- planning the part -----------------------------------------------------------
@pytest.fixture
def three_shots(tmp_path):
    """Police (30 frames), the face (120 frames), a masked man (30 frames); bars of 16 px."""
    return write_shots(
        tmp_path / "scene.mp4",
        [(RED, 30), (BLUE, 120), (GREEN, 30)],
        size=(64, 96),
        bars=16,
        audio_seconds=at(180),
    )


def test_edges_snap_to_the_shot_cuts_nearby(three_shots, small_boxes):
    plan = scene.plan_part(three_shots, 1.1, 6.4)
    segment = plan.segment
    assert (segment["start_frame"], segment["end_frame"]) == (30, 150)
    assert segment["start"] == pytest.approx(at(30), abs=1e-5)
    assert segment["end"] == pytest.approx(at(150), abs=1e-5)
    assert segment["seconds"] == pytest.approx(at(120), abs=1e-5)  # 5.005 s
    assert segment["padded_seconds"] == 6
    assert segment["crop"] == {"x": 0, "y": 16, "w": 64, "h": 64}
    assert (segment["fps"], segment["width"], segment["height"]) == ("24000/1001", 64, 96)
    assert segment["cuts"] == pytest.approx([at(30), at(150)], abs=1e-5)
    assert plan.moved == (True, True) and plan.inside == ()
    assert plan.scene_seconds == pytest.approx(at(180), abs=1e-3)


def test_without_a_cut_nearby_the_typed_time_goes_to_the_nearest_frame(three_shots):
    plan = scene.plan_part(three_shots, 2.0, 4.0)
    assert (plan.segment["start_frame"], plan.segment["end_frame"]) == (48, 96)
    assert plan.segment["padded_seconds"] == 4  # 2.0 s: Seedance's minimum is 4
    assert plan.moved == (False, False)


def test_a_cut_inside_the_part_is_reported(three_shots):
    plan = scene.plan_part(three_shots, 0.0, 7.5)
    assert (plan.segment["start_frame"], plan.segment["end_frame"]) == (0, 180)
    assert plan.inside == pytest.approx((at(30), at(150)), abs=1e-5)


@pytest.mark.parametrize(("start", "end"), [(3.0, 3.0), (4.0, 3.0), (-1.0, 3.0), (1.0, 9.0)])
def test_times_outside_the_scene_or_in_the_wrong_order_are_refused(three_shots, start, end):
    with pytest.raises(scene.SceneError, match=r"The scene is 7\.5 s; pick --from and --to"):
        scene.plan_part(three_shots, start, end)


def test_a_part_under_a_second_is_refused(three_shots):
    with pytest.raises(scene.SceneError, match=r"The part to swap is 0\.5 s; it must be 1 to 30 s"):
        scene.plan_part(three_shots, 2.0, 2.5)


def test_a_part_over_the_limit_is_refused(three_shots, monkeypatch):
    monkeypatch.setattr(scene, "MAX_PART", 3.0)
    with pytest.raises(scene.SceneError, match=r"is 5\.0 s; it must be 1 to 3 s"):
        scene.plan_part(three_shots, 1.1, 6.4)


def test_a_scene_whose_sound_is_much_shorter_is_refused(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 90)], audio_seconds=4.0)
    with pytest.raises(scene.SceneError, match=r"sound lasts 4\.0 s but its picture 5\.0 s"):
        scene.plan_part(clip, 1.2, 4.5)


def test_a_silent_scene_passes_the_sound_check(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 90)])
    assert scene.plan_part(clip, 1.2, 4.5).segment["start_frame"] == 30


def test_a_missing_scene_is_refused(tmp_path):
    with pytest.raises(scene.SceneError, match="Video not found"):
        scene.plan_part(tmp_path / "nope.mp4", 1.0, 2.0)


@pytest.mark.parametrize(("seconds", "padded"), [(1.0, 4), (4.0, 4), (4.0001, 5), (12.8, 13)])
def test_the_part_is_padded_to_whole_seconds(seconds, padded):
    assert scene.padded_seconds(seconds) == padded
