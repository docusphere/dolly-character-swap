"""Scene swaps: lay the AI's person back into the full scene (SAM 3 on the result, then paste)."""

from __future__ import annotations

import os
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
from character_swap.media.scene import EDGES, AiClip, Edge, SceneError, ai_clip, paste_back
from character_swap.stages import StageError
from character_swap.stages.masks import NoMasks, segment

SCENE = "full-scene.mp4"
NOBODY = "SAM 3 found nobody on the AI clip; only the original outline is used."
AGAIN = "run the same command again"  # the lay-back runs inside generate, not resume
MASK_SLACK = 2  # SAM 3 decodes the AI clip itself: a frame or two more or fewer is laid back


def _blank(folder: Path) -> bool:
    return not any(
        np.asarray(Image.open(path).convert("L")).max() > 127
        for path in sorted(folder.glob("mask_*.png"))
    )


def area_name(stage: str) -> str:
    """The swapped-area view of a scene job's draft or final (the app shows it)."""
    return f"{stage}-area.mp4"


def chosen_edge(job: Job) -> Edge:
    """The edge width saved by `charswap edge` (normal when none was chosen)."""
    name = job.state.get("edge", "normal")
    if name not in EDGES:
        raise StageError(
            f"state.json has an unknown edge {name!r}. Choose one (free): "
            f"charswap edge {job.id} normal"
        )
    return EDGES[name]


def has_saved_lay_back(job: Job, stage: str) -> bool:
    """Everything a free lay-back reads is saved: the AI clip and SAM 3's run on it."""
    folder = job.path(f"{stage}-sam")
    return (
        job.path(f"{stage}-raw.mp4").is_file()
        and (folder / "sam-prediction.json").is_file()
        and ((folder / "clean-masks").is_dir() or (folder / "raw-masks").is_dir())
    )


def _has_masks(folder: Path) -> bool:
    return any(folder.glob("mask_*.png"))


def _checked(folder: Path, clip: AiClip) -> Path | None:
    """{stage}-sam/clean-masks checked against the AI clip: None when they are all blank."""
    masks_b = folder / "clean-masks"
    count = len(list(masks_b.glob("mask_*.png")))
    if abs(count - clip.frames) > MASK_SLACK:
        raise StageError(
            f"SAM 3 gave {count} masks for the AI clip's {clip.frames} frames. Delete "
            f"{folder / 'sam-prediction.json'} and run the same command again (~$0.002). "
            "If it says this again after that, don't pay again: it needs a fix in the tool."
        )
    return None if _blank(masks_b) else masks_b


def _clip(raw: Path, facts: dict) -> AiClip:
    try:
        return ai_clip(raw, facts)
    except SceneError as exc:
        raise StageError(str(exc)) from exc


def _lay(job: Job, stage: str, masks_b: Path | None, dst: Path, status, edge: Edge) -> Path:
    """Paste and add the scene's sound at `dst`; {stage}-area.mp4 is replaced only on success."""
    if masks_b is None:
        status(NOBODY)
    area = job.path(area_name(stage))
    part = area.with_name(f"{area.stem}.part{area.suffix}")
    try:
        with tempfile.TemporaryDirectory() as scratch:
            silent = Path(scratch) / "pasted.mp4"
            try:
                paste_back(
                    job.path(SCENE),
                    job.path(f"{stage}-raw.mp4"),
                    job.path("sam") / "clean-masks",
                    masks_b,
                    job.state["segment"],
                    silent,
                    edge=edge,
                    area=part,
                )
            except SceneError as exc:
                raise StageError(str(exc)) from exc
            add_original_soundtrack(silent, job.path(SCENE), dst, fit=False)
        os.replace(part, area)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return Path(dst)


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

    Also writes {stage}-area.mp4, the swapped-area view. Paid: one SAM 3 run on the AI clip
    (~$0.002), logged once as "{stage}-paste" when it is created. Its saved record is
    resumed, never bought again; the raw clip is never deleted.
    """
    raw = job.path(f"{stage}-raw.mp4")
    edge = chosen_edge(job)  # free: refuse before paying for SAM
    clip = _clip(raw, job.state["segment"])  # free: refuse before paying for SAM
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

    try:
        found = segment(raw, folder, "person", replicate, on_created=log, again=AGAIN)
    except NoMasks:
        masks_b = None
    else:
        clean_border_fragments(found, folder / "clean-masks")
        masks_b = _checked(folder, clip)
    return _lay(job, stage, masks_b, dst, status, edge)


def lay_back_saved(job: Job, stage: str, dst: Path, *, status: Callable[[str], None]) -> Path:
    """lay_back from the saved files only, with the job's chosen edge: free, no network.

    The caller checked has_saved_lay_back. B is the cleaned masks, or the raw ones cleaned now
    (a lay-back that stopped in between), or none when SAM 3 found nobody.
    """
    edge = chosen_edge(job)
    clip = _clip(job.path(f"{stage}-raw.mp4"), job.state["segment"])
    folder = job.path(f"{stage}-sam")
    clean, raw = folder / "clean-masks", folder / "raw-masks"
    if not _has_masks(clean) and _has_masks(raw):
        clean_border_fragments(raw, clean)
    masks_b = _checked(folder, clip) if _has_masks(clean) else None
    return _lay(job, stage, masks_b, dst, status, edge)
