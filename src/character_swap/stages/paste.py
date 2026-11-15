"""Scene swaps: lay the AI's person back into the full scene (SAM 3 on the result, then paste)."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from character_swap.costs import estimate_repair, record_spend
from character_swap.jobs import Job
from character_swap.media.audio import add_original_soundtrack
from character_swap.media.composite import clean_border_fragments
from character_swap.media.scene import SceneError, ai_clip, paste_back
from character_swap.stages import StageError
from character_swap.stages.masks import NoMasks, segment

SCENE = "full-scene.mp4"
NOBODY = "SAM 3 found nobody on the AI clip; only the original outline is used."
MASK_SLACK = 2  # SAM 3 decodes the AI clip itself: a frame or two more or fewer is laid back


def _blank(folder: Path) -> bool:
    return not any(
        np.asarray(Image.open(path).convert("L")).max() > 127
        for path in sorted(folder.glob("mask_*.png"))
    )


def lay_back(
    job: Job,
    stage: str,
    replicate,
    dst: Path,
    *,
    runs_dir: Path,
    status: Callable[[str], None],
) -> Path:
    """{stage}-raw.mp4's new person laid into full-scene.mp4, with its own sound, at `dst`.

    Paid: one SAM 3 run on the AI clip (~$0.002), logged once as "{stage}-paste" when it is
    created. Its saved record is resumed, never bought again; the raw clip is never deleted.
    """
    raw = job.path(f"{stage}-raw.mp4")
    facts = job.state["segment"]
    try:
        clip = ai_clip(raw, facts)  # free: refuse before paying for SAM
    except SceneError as exc:
        raise StageError(str(exc)) from exc
    folder = job.path(f"{stage}-sam")
    if not (folder / "sam-prediction.json").exists():
        shutil.rmtree(folder, ignore_errors=True)  # a new SAM run: no masks left from an old one
    estimate = estimate_repair()  # one SAM 3 run

    def log(prediction: dict) -> None:
        record_spend(
            job,
            f"{stage}-paste",
            estimate,
            runs_dir,
            provider="replicate",
            model="sam3",
            task_id=str(prediction.get("id", "")),
        )

    masks_b: Path | None
    try:
        found = segment(raw, folder, "person", replicate, on_created=log)
    except NoMasks:
        masks_b = None
    else:
        masks_b = folder / "clean-masks"
        clean_border_fragments(found, masks_b)
        count = len(list(masks_b.glob("mask_*.png")))
        if abs(count - clip.frames) > MASK_SLACK:
            raise StageError(
                f"SAM 3 gave {count} masks for the AI clip's {clip.frames} frames. Delete "
                f"{folder / 'sam-prediction.json'} and run the same command again (~$0.002). "
                "If it says this again after that, don't pay again: it needs a fix in the tool."
            )
        if _blank(masks_b):
            masks_b = None
    if masks_b is None:
        status(NOBODY)
    with tempfile.TemporaryDirectory() as scratch:
        silent = Path(scratch) / "pasted.mp4"
        try:
            paste_back(
                job.path(SCENE), raw, job.path("sam") / "clean-masks", masks_b, facts, silent
            )
        except SceneError as exc:
            raise StageError(str(exc)) from exc
        add_original_soundtrack(silent, job.path(SCENE), dst, fit=False)
    return Path(dst)
