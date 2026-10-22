import av
import numpy as np
import pytest
from PIL import Image

from character_swap.media.composite import (
    CompositeError,
    clean_border_fragments,
    composite_depth_subject,
)
from character_swap.media.video import iter_frames
from helpers import solid, write_mask_pngs, write_video


def test_depth_subject_over_original_background(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((180, 40, 40))] * 4)
    depth = write_video(tmp_path / "depth.mp4", [solid((100, 100, 100))] * 4)
    masks = write_mask_pngs(tmp_path / "masks", 4)
    result = composite_depth_subject(original, depth, masks, tmp_path)
    assert result.frames == 4 and result.mask_pixels > 0
    frame = next(iter_frames(tmp_path / "composite-silent.mp4"))
    assert np.max(np.abs(frame[32, 32].astype(int) - 100)) < 10  # subject = depth
    assert frame[2, 2, 0] > 160 and frame[2, 2, 1] < 60  # background = original
    assert (tmp_path / "mask.mp4").exists()


def test_misaligned_inputs_refused(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 3)
    depth = write_video(tmp_path / "depth.mp4", [solid((0, 0, 0))] * 3)
    masks = write_mask_pngs(tmp_path / "masks", 2)
    with pytest.raises(CompositeError, match="Frame alignment"):
        composite_depth_subject(original, depth, masks, tmp_path)


def test_empty_masks_refused(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 2)
    depth = write_video(tmp_path / "depth.mp4", [solid((0, 0, 0))] * 2)
    masks = write_mask_pngs(tmp_path / "masks", 2, box=(0, 0, 0, 0))
    with pytest.raises(CompositeError, match="no subject"):
        composite_depth_subject(original, depth, masks, tmp_path)


def test_clean_removes_thin_edge_slivers_but_keeps_large_subjects(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:20, 0:2] = 255  # thin sliver touching left edge -> removed
    mask[40:90, 60:100] = 255  # big subject touching right edge -> kept
    Image.fromarray(mask).save(raw / "mask_00000.png")
    out = tmp_path / "clean"
    report = clean_border_fragments(raw, out)
    cleaned = np.asarray(Image.open(out / "mask_00000.png"))
    assert cleaned[15, 1] == 0 and cleaned[60, 99] == 255
    assert report[0]["removed_border_pixels"] == 20


def test_zero_frames_refused(tmp_path):
    original = tmp_path / "original.mp4"
    with av.open(str(original), "w") as out:
        stream = out.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        out.start_encoding()
    (tmp_path / "masks").mkdir()
    with pytest.raises(CompositeError, match="no video frames"):
        composite_depth_subject(original, original, tmp_path / "masks", tmp_path)


def test_unparseable_mask_names_refused(tmp_path):
    original = write_video(tmp_path / "original.mp4", [solid((0, 0, 0))] * 1)
    depth = write_video(tmp_path / "depth.mp4", [solid((0, 0, 0))] * 1)
    masks = write_mask_pngs(tmp_path / "masks", 1)
    (masks / "mask_00000.png").rename(masks / "mask_final.png")
    with pytest.raises(CompositeError, match="mask_final"):
        composite_depth_subject(original, depth, masks, tmp_path)
