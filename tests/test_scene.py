import av
import numpy as np
import pytest
from PIL import Image

from character_swap.media import scene
from character_swap.media.audio import RATE, add_original_soundtrack, read_audio
from character_swap.media.video import count_frames, iter_frames, normalize, stream_seconds
from helpers import (
    SCENE_RATE,
    solid,
    tone,
    write_mask_pngs,
    write_shots,
    write_video,
    write_wav,
)

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


# ---- cutting the part --------------------------------------------------------------
def _cut(clip, tmp_path, start, end):
    plan = scene.plan_part(clip, start, end)
    crop = plan.segment["crop"]
    box = (crop["x"], crop["y"], crop["w"], crop["h"])
    part = tmp_path / "part.mp4"
    frame_map = scene.cut_segment(clip, plan.segment["start"], plan.segment["end"], box, part)
    return plan.segment, frame_map, part


def test_the_part_is_cropped_24_fps_and_whole_seconds(three_shots, tmp_path, small_boxes):
    segment, frame_map, part = _cut(three_shots, tmp_path, 1.1, 6.4)
    frames = list(iter_frames(part))
    assert len(frames) == 6 * 24 and frames[0].shape == (64, 64, 3)
    assert stream_seconds(part, "video") == pytest.approx(6.0, abs=1e-6)
    assert len(frame_map) == 121  # ceil(5.005 s x 24)
    assert (frame_map[0], frame_map[1], frame_map[2], frame_map[-1]) == (30, 30, 31, 149)
    for frame in frames:  # only the face shot: no police frame, no bars
        assert abs(frame.astype(int) - BLUE).mean() < 6
    last_real = frames[len(frame_map) - 1].astype(int)
    for held in frames[len(frame_map) :]:
        assert abs(held.astype(int) - last_real).max() <= 2


def test_the_parts_sound_is_the_scenes_padded_with_silence(three_shots, tmp_path):
    _segment, _frame_map, part = _cut(three_shots, tmp_path, 1.1, 6.4)
    assert stream_seconds(part, "audio") == pytest.approx(6.0, abs=0.05)
    sound = read_audio(part)
    assert abs(sound[:, RATE : 2 * RATE]).max() > 0.05  # the tone
    assert abs(sound[:, round(5.2 * RATE) : round(5.9 * RATE)]).max() < 1e-3  # the padding


def test_normalize_keeps_every_frame_of_the_part(three_shots, tmp_path):
    _segment, _frame_map, part = _cut(three_shots, tmp_path, 1.1, 6.4)
    facts = normalize(
        part, tmp_path / "original.mp4", min_seconds=4, max_seconds=30, whole_seconds=True
    )
    assert facts["frames"] == 6 * 24 and count_frames(tmp_path / "original.mp4") == 6 * 24


def test_a_silent_scene_gives_a_silent_part(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 30), (BLUE, 90)])
    _segment, _frame_map, part = _cut(clip, tmp_path, 1.2, 4.5)
    assert count_frames(part) == 4 * 24
    with pytest.raises(ValueError, match="no audio stream"):
        stream_seconds(part, "audio")


def test_the_frame_map_at_23_976_fps(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(BLUE, 310)])
    part = tmp_path / "part.mp4"
    frame_map = scene.cut_segment(clip, 0.0, at(310), (0, 0, 64, 48), part)
    assert frame_map[:4] == [0, 0, 1, 2]  # job frame k shows scene frame k - 1
    assert len(frame_map) == 311 and frame_map[-1] == 309
    assert count_frames(part) == 13 * 24
    made_from = scene.job_frames(frame_map, 0, 310)
    assert made_from[0] == 0 and made_from[1] == 2 and made_from[300] == 301
    assert made_from[-1] == 310


def test_the_frame_map_at_24_fps_is_one_to_one(tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(BLUE, 96)], fps=24)
    frame_map = scene.cut_segment(clip, 0.0, 4.0, (0, 0, 64, 48), tmp_path / "part.mp4")
    assert frame_map == list(range(96))
    assert scene.job_frames(frame_map, 0, 96) == list(range(96))


def test_job_frames_holds_the_last_one_past_the_map():
    assert scene.job_frames([5, 6, 7], 5, 10) == [0, 1, 2, 2, 2]


# ---- the pasted region -------------------------------------------------------------
def _box_mask(top, bottom, left, right, size=(400, 600)):
    mask = np.zeros(size, dtype=bool)
    mask[top:bottom, left:right] = True
    return mask


def test_the_region_covers_a_and_feathers_its_edge():
    a = _box_mask(150, 250, 250, 350)  # 400 px high: grow 6 px, feather sigma 4 px
    alpha = scene.lay_alpha(a, None)
    assert alpha[200, 300] > 0.99  # inside A
    assert 0.9 < alpha[150, 300] < 1.0  # on A's edge: inside the grown region, feathered
    assert alpha[150 - 8, 300] < 0.5  # 8 px out: past the 6 px growth
    assert alpha[50, 50] == 0.0  # far away: exactly the scene


def test_b_next_to_a_is_added_but_someone_far_away_is_not():
    a = _box_mask(150, 250, 250, 350)
    hair = _box_mask(140, 250, 250, 350)  # SAM 3 on the result: 10 px more on top
    shoulder = _box_mask(300, 380, 500, 580)  # another person the AI redrew, far from A
    alpha = scene.lay_alpha(a, hair | shoulder)
    assert alpha[150 - 8, 300] > 0.9  # the hairline is pasted
    assert alpha[340, 540] == 0.0  # the shoulder is not
    assert scene.lay_alpha(a, np.zeros_like(a)).tolist() == scene.lay_alpha(a, None).tolist()


def _write_masks(folder, masks):
    folder.mkdir(parents=True, exist_ok=True)
    for index, mask in enumerate(masks):
        Image.fromarray(mask.astype(np.uint8) * 255).save(folder / f"mask_{index:05d}.png")
    return folder


def test_crowded_frames_count_a_second_person(tmp_path):
    one = _box_mask(10, 40, 10, 40, size=(64, 96))
    two = one | _box_mask(10, 40, 60, 90, size=(64, 96))  # the same size again
    speck = one | _box_mask(50, 54, 60, 64, size=(64, 96))  # 16 px: under 25 % of 900
    empty = np.zeros((64, 96), dtype=bool)
    folder = _write_masks(tmp_path / "masks", [one, two, speck, empty])
    assert scene.crowded_frames(folder) == (1, 4)


# ---- laying the AI's person back -----------------------------------------------------
YELLOW, MAGENTA = (220, 220, 40), (220, 40, 220)


@pytest.fixture
def lay(tmp_path, small_boxes):
    """A 120-frame scene (bars of 16 px) whose middle shot (frames 24 to 95) is swapped.

    The crop box is 128 x 64 at y = 16; mask A is the square x 48-80, y 16-48 of it.
    """
    clip = write_shots(
        tmp_path / "scene.mp4",
        [(RED, 24), (BLUE, 72), (GREEN, 24)],
        size=(128, 96),
        bars=16,
        audio_seconds=at(120),
    )
    plan = scene.plan_part(clip, 1.0, 4.0)
    crop = plan.segment["crop"]
    box = (crop["x"], crop["y"], crop["w"], crop["h"])
    frame_map = scene.cut_segment(
        clip, plan.segment["start"], plan.segment["end"], box, tmp_path / "part.mp4"
    )
    masks_a = write_mask_pngs(tmp_path / "a", 96, box=(48, 16, 80, 48), size=(128, 64))
    return clip, {**plan.segment, "frame_map": frame_map}, masks_a


def _ai(path, frames=96, size=(256, 256), bars=64):
    """The AI's clip: its own bars, a yellow room and the new person (magenta) where A is."""
    picture = solid(YELLOW, size)
    picture[:bars] = 0
    picture[size[1] - bars :] = 0
    picture[96:160, 96:160] = MAGENTA  # x 48-80, y 16-48 of the 128 x 64 box, at twice the size
    return write_video(path, [picture] * frames)


def _masks_b(folder, frames=96):
    """SAM 3 "person" on the AI clip, at half its size: the person and a far-away shoulder."""
    folder.mkdir(parents=True)
    for index in range(frames):
        mask = np.zeros((128, 128), dtype=np.uint8)
        mask[48:80, 48:80] = 255  # the new person
        mask[40:60, 100:124] = 255  # someone else the AI redrew
        Image.fromarray(mask).save(folder / f"mask_{index:05d}.png")
    return folder


def _near(frame, x, y, color):
    patch = frame[y - 2 : y + 2, x - 2 : x + 2].astype(int)
    return abs(patch - color).mean() < 16


def test_the_ai_person_lands_exactly_on_a_and_nothing_else_changes(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "draft-raw.mp4")
    out = tmp_path / "pasted.mp4"
    scene.paste_back(clip, raw, masks_a, _masks_b(tmp_path / "b"), segment, out)
    frames, originals = list(iter_frames(out)), list(iter_frames(clip))
    assert len(frames) == len(originals) == 120
    with av.open(str(out)) as container:
        assert container.streams.video[0].average_rate == SCENE_RATE
    for index in (0, 23, 96, 119):  # outside the part: the scene as decoded
        assert abs(frames[index].astype(int) - originals[index]).mean() < 2
    for index in (24, 60, 95):  # the first, a middle and the last frame of the part
        frame = frames[index]
        assert _near(frame, 64, 48, MAGENTA)  # A's middle (the box starts at y = 16)
        assert _near(frame, 51, 35, MAGENTA) and _near(frame, 77, 61, MAGENTA)  # A's corners
        assert _near(frame, 36, 48, BLUE)  # beside A: the scene, not the AI's room
        assert _near(frame, 112, 34, BLUE)  # the far-away shoulder is not pasted
        assert frame[:12].max() <= 24 and frame[84:].max() <= 24  # the bars stay black


def test_the_spliced_scene_keeps_its_sound_byte_for_byte(lay, tmp_path):
    clip, segment, masks_a = lay
    silent = tmp_path / "pasted.mp4"
    scene.paste_back(clip, _ai(tmp_path / "raw.mp4"), masks_a, None, segment, silent)
    final = add_original_soundtrack(silent, clip, tmp_path / "draft.mp4", fit=False)

    def packets(path):
        with av.open(str(path)) as container:
            return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]

    assert packets(final) == packets(clip)
    assert count_frames(final) == 120


def _gray(patch):
    return float(patch.astype(int).mean())


def test_every_frame_of_the_part_shows_the_ai_frame_made_from_it(lay, tmp_path):
    clip, segment, _masks_a = lay
    left = _box_mask(8, 56, 8, 56, size=(64, 128))  # A on even job frames ...
    right = _box_mask(8, 56, 72, 120, size=(64, 128))  # ... and on odd ones
    masks_a = _write_masks(tmp_path / "a", [right if k % 2 else left for k in range(96)])
    # AI frame k is gray 10 + 3k (the part uses job frames 0 to 72; later ones are spare)
    shades = [solid((10 + 3 * min(k, 80),) * 3, (128, 64)) for k in range(96)]
    raw = write_video(tmp_path / "raw.mp4", shades)
    out = tmp_path / "out.mp4"
    scene.paste_back(clip, raw, masks_a, None, segment, out)
    first, stop = segment["start_frame"], segment["end_frame"]
    made_from = scene.job_frames(segment["frame_map"], first, stop)
    assert made_from != list(range(stop - first))  # 23.976 fps: some job frames are skipped
    frames = list(iter_frames(out))
    for index in range(first, stop):
        k = made_from[index - first]
        pasted, kept = (96, 32) if k % 2 else (32, 96)  # x of A's middle, and of the other box
        frame = frames[index]
        shade = (_gray(frame[46:50, pasted - 2 : pasted + 2]) - 10) / 3
        assert abs(shade - k) < 0.5, f"scene frame {index} shows AI frame {shade:.1f}, not {k}"
        assert _near(frame, kept, 48, BLUE), f"scene frame {index} used the wrong mask"


def test_a_missed_subject_mask_holds_the_last_one_for_two_frames(lay, tmp_path):
    clip, segment, _masks_a = lay
    first, stop = segment["start_frame"], segment["end_frame"]
    made_from = scene.job_frames(segment["frame_map"], first, stop)
    missed = {made_from[index - first] for index in (40, 60, 61, 62, 63)}  # SAM lost the subject
    subject = _box_mask(16, 48, 48, 80, size=(64, 128))
    masks = [np.zeros_like(subject) if k in missed else subject for k in range(96)]
    out = tmp_path / "pasted.mp4"
    scene.paste_back(
        clip, _ai(tmp_path / "raw.mp4"), _write_masks(tmp_path / "a", masks), None, segment, out
    )
    frames = list(iter_frames(out))
    for index in (39, 40, 41, 60, 61, 64):  # one or two missed frames: the last A is held
        assert _near(frames[index], 64, 48, MAGENTA), f"scene frame {index} lost the person"
    for index in (62, 63):  # a third missed frame in a row: nothing is pasted
        assert _near(frames[index], 64, 48, BLUE), f"scene frame {index} pasted a stale A"


def test_a_slightly_short_ai_clip_holds_its_last_frame(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "raw.mp4", frames=64)  # 2.67 s for a 3.0 s part: 0.34 s short
    out = tmp_path / "pasted.mp4"
    scene.paste_back(clip, raw, masks_a, None, segment, out)
    assert _near(list(iter_frames(out))[95], 64, 48, MAGENTA)


def test_a_much_shorter_ai_clip_is_refused(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "draft-raw.mp4", frames=48)  # 2.0 s for a 3.0 s part
    with pytest.raises(scene.SceneError, match=r"256x256, 2\.0 s; it can't be laid back into"):
        scene.paste_back(clip, raw, masks_a, None, segment, tmp_path / "out.mp4")
    assert not (tmp_path / "out.mp4").exists()


def test_an_ai_clip_with_another_aspect_is_refused(lay, tmp_path):
    clip, segment, masks_a = lay
    raw = _ai(tmp_path / "draft-raw.mp4", bars=0)  # square: the AI cropped the picture
    with pytest.raises(scene.SceneError, match=r"the 3\.0 s shot\. It is kept at .*draft-raw"):
        scene.paste_back(clip, raw, masks_a, None, segment, tmp_path / "out.mp4")


# ---- the AI clip's own bars ------------------------------------------------------------
WIDE = {"crop": {"x": 0, "y": 140, "w": 1920, "h": 800}, "seconds": 1.0}  # a 2.4:1 crop box


def _framed(path, size, bars, *, frames=24, edge=0, specks=0.0, seed=0):
    """A picture letterboxed by `bars` rows each side of a `size` frame. `edge`: the picture's
    own top rows that are dark; `specks`: the share of bar pixels that flicker bright."""
    rng = np.random.default_rng(seed)
    width, height = size
    pictures = []
    for _ in range(frames):
        picture = solid(YELLOW, size)
        picture[:bars] = 0
        picture[height - bars :] = 0
        picture[bars : bars + edge] = 8
        if specks:
            for band in (slice(0, bars), slice(height - bars, height)):
                lit = rng.random((bars, width)) < specks
                picture[band][lit] = 90
        pictures.append(picture)
    return write_video(path, pictures)


@pytest.mark.parametrize(
    ("size", "bars", "box"),
    [((640, 480), 106, (0, 106, 640, 268)), ((480, 480), 140, (0, 140, 480, 200))],
)
def test_a_wide_picture_letterboxed_in_a_480p_frame_is_found(tmp_path, size, bars, box):
    raw = _framed(tmp_path / "raw.mp4", size, bars)  # the picture is under 300 rows
    assert scene.ai_clip(raw, WIDE).box == box


def test_noisy_ai_bars_still_count_as_bars(tmp_path, small_boxes):
    raw = _framed(tmp_path / "raw.mp4", (480, 480), 140, specks=0.002)
    assert scene.ai_clip(raw, WIDE).box == (0, 140, 480, 200)


def test_a_dark_top_edge_of_the_ai_picture_is_not_cut_off(tmp_path, small_boxes):
    raw = _framed(tmp_path / "raw.mp4", (480, 480), 140, edge=6)  # trims the top only: 2.47:1
    assert scene.ai_clip(raw, WIDE).box == (0, 140, 480, 200)


def test_an_ai_clip_without_bars_keeps_its_dark_edge(tmp_path, small_boxes):
    raw = _framed(tmp_path / "raw.mp4", (480, 200), 0, edge=6)  # already 2.4:1
    assert scene.ai_clip(raw, WIDE).box == (0, 0, 480, 200)


def test_bars_that_leave_another_aspect_are_still_refused(tmp_path):
    raw = _framed(tmp_path / "raw.mp4", (480, 480), 60)  # 480 x 360: 4:3, cropped by the AI
    with pytest.raises(scene.SceneError, match=r"480x480, 1\.0 s; it can't be laid back"):
        scene.ai_clip(raw, WIDE)


def test_too_few_subject_masks_are_refused(lay, tmp_path):
    clip, segment, _masks_a = lay
    few = write_mask_pngs(tmp_path / "few", 10, box=(48, 16, 80, 48), size=(128, 64))
    with pytest.raises(scene.SceneError, match="10 masks for the part's 73 frames"):
        scene.paste_back(clip, _ai(tmp_path / "raw.mp4"), few, None, segment, tmp_path / "o.mp4")
