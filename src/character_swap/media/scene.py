"""Scene swaps: find shot cuts and black bars, cut one part out, lay the AI's person back.

Local and free: nothing here calls the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from character_swap.errors import CharSwapError
from character_swap.media.video import FPS

CUT_MIN = 12.0  # a cut changes the picture by at least this much (mean, of 255) ...
CUT_RATIO = 4.0  # ... and by at least 4 times the median change of the frames around it
CUT_AROUND = 12  # frames on each side for that median (24 around it)
SNAP = 0.3  # seconds: --from and --to move to a shot cut this close
BAR_MAX = 24  # an edge row or column at most this bright (of 255) in every frame is a bar
MIN_BOX = 300  # px: a picture smaller than this either way is not cropped (a dark scene)


class SceneError(CharSwapError, ValueError):
    """The scene, the chosen part or the AI clip can't be used; the message says why."""


def _video_stream(container, path: Path):
    if not container.streams.video:
        raise SceneError(f"{Path(path).name} has no video frames.")
    stream = container.streams.video[0]
    stream.thread_type = "AUTO"
    return stream


@dataclass(frozen=True)
class _Scan:
    times: tuple[float, ...]  # every frame's time, in decode order
    cuts: tuple[float, ...]  # the time of the first frame of each new shot
    rate: Fraction
    width: int
    height: int


def _scan(path: Path) -> _Scan:
    """One pass: frame times, and each frame's change from the one before (gray, 64 px wide)."""
    times, changes, previous = [], [], None
    with av.open(str(path)) as container:
        stream = _video_stream(container, path)
        rate = Fraction(stream.average_rate or FPS)
        width, height = stream.width, stream.height
        small = max(2, round(64 * height / width))
        for frame in container.decode(stream):
            gray = frame.reformat(width=64, height=small, format="gray").to_ndarray()
            gray = gray.astype(np.int16)
            times.append(float(frame.time))
            changes.append(0.0 if previous is None else float(np.abs(gray - previous).mean()))
            previous = gray
    cuts = []
    for index in range(1, len(changes)):
        around = changes[max(1, index - CUT_AROUND) : index + CUT_AROUND + 1]
        change = changes[index]
        if change >= CUT_MIN and change >= CUT_RATIO * float(np.median(around)):
            cuts.append(times[index])
    return _Scan(tuple(times), tuple(cuts), rate, width, height)


def find_cuts(path: Path) -> list[float]:
    """Hard shot cuts (never fades): the time of the first frame of each new shot."""
    return list(_scan(path).cuts)


def snap(moment: float, cuts, window: float = SNAP) -> float:
    """The shot cut nearest to `moment` within `window` seconds, else `moment` itself."""
    near = [cut for cut in cuts if abs(cut - moment) <= window + 1e-9]
    return min(near, key=lambda cut: abs(cut - moment)) if near else moment


def _luma_extent(path: Path, start: float, end: float) -> tuple[np.ndarray, np.ndarray, int]:
    """The brightest value of every row and every column over the frames in [start, end),
    and how many frames that was."""
    rows = columns = None
    count = 0
    with av.open(str(path)) as container:
        stream = _video_stream(container, path)
        for frame in container.decode(stream):
            moment = float(frame.time)
            if moment < start - 1e-6 or moment >= end - 1e-6:
                continue
            luma = frame.to_ndarray(format="gray")
            row, column = luma.max(axis=1), luma.max(axis=0)
            rows = row if rows is None else np.maximum(rows, row)
            columns = column if columns is None else np.maximum(columns, column)
            count += 1
    if rows is None or columns is None:
        raise SceneError(f"{Path(path).name} has no frames between {start:.2f} s and {end:.2f} s.")
    return rows, columns, count


def _inward(lit: np.ndarray) -> tuple[int, int]:
    """The first and one-past-the-last lit index, moved inward to even numbers."""
    first, stop = int(lit[0]), int(lit[-1]) + 1
    return first + first % 2, stop - stop % 2


def _box(rows: np.ndarray, columns: np.ndarray) -> tuple[int, int, int, int]:
    height, width = len(rows), len(columns)
    whole = (0, 0, width // 2 * 2, height // 2 * 2)
    lit_rows, lit_columns = np.flatnonzero(rows > BAR_MAX), np.flatnonzero(columns > BAR_MAX)
    if not len(lit_rows) or not len(lit_columns):
        return whole
    top, bottom = _inward(lit_rows)
    left, right = _inward(lit_columns)
    if right - left < MIN_BOX or bottom - top < MIN_BOX:
        return whole
    return left, top, right - left, bottom - top


def find_bars(path: Path, start: float = 0.0, end: float = math.inf) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the picture inside black bars, in even numbers.

    A bar is an edge row or column that stays near-black in every frame between start and
    end; anything bright that ever enters it keeps it. No bars: the whole frame.
    """
    rows, columns, _ = _luma_extent(path, start, end)
    return _box(rows, columns)
