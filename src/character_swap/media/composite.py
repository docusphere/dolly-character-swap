"""Composite the depth-rendered subject over the original background using raw masks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import label

from character_swap.media.video import VideoWriter, count_frames, iter_frames


class CompositeError(ValueError):
    """Inputs cannot be composited safely."""


@dataclass(frozen=True)
class CompositeResult:
    frames: int
    mask_pixels: int


def clean_border_fragments(src_dir: Path, dst_dir: Path) -> list[dict]:
    """Drop tiny, narrow blobs touching the left/right edge; keep large edge subjects."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for path in sorted(src_dir.glob("mask_*.png")):
        mask = np.asarray(Image.open(path).convert("L")) > 127
        labels, _ = label(mask)
        height, width = mask.shape
        removed = 0
        edge_ids = (set(labels[:, 0]) | set(labels[:, -1])) - {0}
        for ident in edge_ids:
            ys, xs = np.where(labels == ident)
            small = len(xs) < height * width * 0.03
            narrow = xs.max() - xs.min() + 1 <= max(6, int(width * 0.05))
            if small and narrow:
                mask[labels == ident] = False
                removed += len(xs)
        Image.fromarray(mask.astype(np.uint8) * 255).save(dst_dir / path.name)
        report.append(
            {"frame": path.stem, "removed_border_pixels": removed, "mask_pixels": int(mask.sum())}
        )
    return report


def composite_depth_subject(
    original: Path, depth: Path, masks_dir: Path, out_dir: Path
) -> CompositeResult:
    """Write mask.mp4 and composite-silent.mp4 into out_dir."""
    mask_files = sorted(masks_dir.glob("mask_*.png"))
    counts = [count_frames(original), count_frames(depth), len(mask_files)]
    if counts[0] == 0:
        raise CompositeError("The original video has no video frames.")
    if len(set(counts)) != 1:
        raise CompositeError(f"Frame alignment failed (original/depth/masks): {counts}.")
    indices = []
    for path in mask_files:
        try:
            indices.append(int(path.stem.split("_")[-1]))
        except ValueError:
            raise CompositeError(
                f"Frame alignment failed: cannot read a frame number from {path.name}; "
                "masks must be named mask_00000.png, mask_00001.png, ..."
            ) from None
    if indices != list(range(counts[0])):
        raise CompositeError("Frame alignment failed: missing or shifted mask frame numbers.")

    first = next(iter_frames(original))
    height, width = first.shape[:2]
    area = 0
    with (
        VideoWriter(out_dir / "mask.mp4", width, height) as mask_out,
        VideoWriter(out_dir / "composite-silent.mp4", width, height) as comp_out,
    ):
        for rgb, depth_frame, mask_path in zip(
            iter_frames(original), iter_frames(depth), mask_files, strict=True
        ):
            mask = np.asarray(Image.open(mask_path).convert("L"))
            mask = np.asarray(
                Image.fromarray(mask).resize((width, height), Image.Resampling.NEAREST)
            )
            subject = mask > 127
            area += int(subject.sum())
            depth_rgb = np.asarray(
                Image.fromarray(depth_frame).resize((width, height), Image.Resampling.BILINEAR)
            )
            comp_out.write(np.where(subject[:, :, None], depth_rgb, rgb).astype(np.uint8))
            mask_out.write(np.repeat((subject * 255).astype(np.uint8)[:, :, None], 3, axis=2))
    if area == 0:
        raise CompositeError(
            "SAM 3 found no subject. Try a simpler subject such as 'person', or repair with RGB."
        )
    return CompositeResult(frames=counts[0], mask_pixels=area)
