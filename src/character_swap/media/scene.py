"""Scene swaps: find shot cuts and black bars, cut one part out, lay the AI's person back.

Local and free: nothing here calls the network.
"""

from __future__ import annotations

import math
import shutil
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, label, maximum_filter

from character_swap.errors import CharSwapError
from character_swap.media.audio import MAX_MISMATCH, RATE, embed_samples, read_audio
from character_swap.media.video import FPS, VideoWriter, stream_seconds

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


MIN_PART, MAX_PART = 1.0, 30.0  # seconds the part to swap may last
MIN_SECONDS = 4  # Seedance's shortest clip; the part is padded to whole seconds, at least this


def check_sound(path: Path) -> None:
    """Refuse a scene whose sound and picture lengths differ by more than MAX_MISMATCH."""
    with av.open(str(path)) as probe:
        if not probe.streams.audio:
            return  # no sound at all: the result is silent too
    sound, picture = stream_seconds(path, "audio"), stream_seconds(path, "video")
    if abs(sound - picture) > MAX_MISMATCH:
        raise SceneError(
            f"The scene's sound lasts {sound:.1f} s but its picture {picture:.1f} s, so the "
            "original soundtrack could not be put back. Re-export the scene so both have the "
            "same length. Nothing was charged."
        )


def padded_seconds(seconds: float) -> int:
    """Whole seconds the AI gets: the part rounded up, at least MIN_SECONDS."""
    return max(MIN_SECONDS, math.ceil(seconds - 1e-6))


@dataclass(frozen=True)
class Plan:
    segment: dict  # saved in state.json as "segment"; cut_segment's frame map is added to it
    scene_seconds: float
    moved: tuple[bool, bool]  # --from and --to sit on a shot cut
    inside: tuple[float, ...]  # shot cuts inside the part, more than SNAP from both edges


def _nearest(edges: list[float], moment: float) -> int:
    return min(range(len(edges)), key=lambda index: abs(edges[index] - moment))


def plan_part(scene: Path, start: float, end: float) -> Plan:
    """Free checks and facts for swapping start..end of `scene` (seconds), before any question.

    Each edge moves to a shot cut within SNAP seconds, else to the nearest frame.
    """
    if not Path(scene).is_file():
        raise SceneError(f"Video not found: {scene}")
    scan = _scan(scene)
    picture = stream_seconds(scene, "video")
    if not 0 <= start < end <= picture + 0.05:
        raise SceneError(
            f"The scene is {picture:.1f} s; pick --from and --to inside it, --from first. "
            "Nothing was charged."
        )
    edges = [*scan.times, picture]  # the part ends where the next frame (or the scene) starts
    first, last = snap(start, scan.cuts), snap(end, scan.cuts)
    start_frame, end_frame = _nearest(edges, first), _nearest(edges, last)
    begin, finish = edges[start_frame], edges[end_frame]
    seconds = finish - begin
    if not MIN_PART - 1e-6 <= seconds <= MAX_PART + 1e-6:
        raise SceneError(
            f"The part to swap is {seconds:.1f} s; it must be {MIN_PART:g} to {MAX_PART:g} s. "
            "Nothing was charged."
        )
    check_sound(scene)
    x, y, w, h = find_bars(scene, begin, finish)
    segment = {
        "start": round(begin, 6),
        "end": round(finish, 6),
        "start_frame": start_frame,
        "end_frame": end_frame,  # the first frame after the part
        "seconds": round(seconds, 6),
        "padded_seconds": padded_seconds(seconds),
        "fps": str(scan.rate),
        "width": scan.width,
        "height": scan.height,
        "crop": {"x": x, "y": y, "w": w, "h": h},
        "cuts": [round(cut, 6) for cut in scan.cuts],
    }
    inside = tuple(cut for cut in scan.cuts if begin + SNAP < cut < finish - SNAP)
    return Plan(segment, picture, (first in scan.cuts, last in scan.cuts), inside)


def cut_segment(
    scene: Path, start: float, end: float, crop: tuple[int, int, int, int], dst: Path
) -> list[int]:
    """Write the part the AI gets: cropped, 24 fps, exactly N x 24 frames, sound padded.

    Job frame k shows the latest scene frame at or before start + k/24 (normalize's rule).
    After the last real frame, that frame is held to N seconds; the sound is the scene's from
    start to end (counted from its first audio packet), as stereo AAC, padded with silence.
    Returns the frame map: the scene frame index of every real job frame.
    """
    x, y, w, h = crop
    padded = padded_seconds(end - start)
    real = math.ceil((end - start) * FPS - 1e-6)
    frame_map: list[int] = []
    with tempfile.TemporaryDirectory() as scratch:
        silent = Path(scratch) / "part.mp4"
        with av.open(str(scene)) as container, VideoWriter(silent, w, h) as writer:
            stream = _video_stream(container, scene)
            frames = enumerate(container.decode(stream))
            current, upcoming, picture = None, next(frames, None), None
            for k in range(real):
                moment = start + k / FPS
                while (
                    upcoming is not None
                    and float(upcoming[1].time) <= moment + 1e-6
                    and float(upcoming[1].time) < end - 1e-6
                ):
                    current, upcoming = upcoming, next(frames, None)
                if current is None:
                    raise SceneError(f"{Path(scene).name} has no frame at {start:.2f} s.")
                if not frame_map or frame_map[-1] != current[0]:
                    picture = current[1].to_ndarray(format="rgb24")[y : y + h, x : x + w]
                frame_map.append(current[0])
                writer.write(picture)
            for _ in range(padded * FPS - real):
                writer.write(picture)
        with av.open(str(scene)) as probe:
            has_audio = bool(probe.streams.audio)
        if not has_audio:
            shutil.copyfile(silent, dst)
            return frame_map
        sound = read_audio(scene)[:, round(start * RATE) : round(end * RATE)]
        samples = np.zeros((2, padded * RATE), dtype=np.float32)
        samples[:, : sound.shape[1]] = sound[:, : padded * RATE]
        embed_samples(silent, samples, dst)
    return frame_map


def job_frames(frame_map: list[int], first: int, stop: int) -> list[int]:
    """For each scene frame first..stop-1, the job frame made from it: the first k whose
    scene frame is at or after it (the last real job frame when none is)."""
    found, k, last = [], 0, len(frame_map) - 1
    for index in range(first, stop):
        while k < last and frame_map[k] < index:
            k += 1
        found.append(k)
    return found


NEAR = 0.04  # B counts only this close to A (share of the picture height)
GROW = 0.015  # the pasted region reaches this far past A and B
FEATHER = 0.01  # its edge is blurred this much (Gaussian sigma)
SECOND = 0.25  # a separate region at least this share of the largest is another person
CROWDED = 0.10  # more than this share of such frames stops a scene job's prepare


def _grow(mask: np.ndarray, radius: float) -> np.ndarray:
    size = 2 * max(1, round(radius)) + 1
    return maximum_filter(mask.astype(np.uint8), size=size) > 0


def lay_alpha(a: np.ndarray, b: np.ndarray | None) -> np.ndarray:
    """The pasted region's alpha (0 to 1): dilate(A + (B near A)), feathered.

    a: the original subject mask; b: SAM 3 "person" on the AI result, or None. Both are
    booleans the size of the crop box. B away from A (someone else the AI redrew) is left out.
    """
    height = a.shape[0]
    union = a.copy()
    if b is not None:
        union |= b & _grow(a, NEAR * height)
    region = _grow(union, GROW * height)
    return gaussian_filter(region.astype(np.float32), sigma=FEATHER * height)


def crowded_frames(mask_dir: Path) -> tuple[int, int]:
    """(frames with a second separate region at least SECOND the size of the largest, frames)."""
    files = sorted(Path(mask_dir).glob("mask_*.png"))
    crowded = 0
    for path in files:
        labels, count = label(np.asarray(Image.open(path).convert("L")) > 127)
        if count < 2:
            continue
        sizes = np.sort(np.bincount(labels.ravel())[1:])
        if sizes[-2] >= SECOND * sizes[-1]:
            crowded += 1
    return crowded, len(files)


SHORT = 0.5  # an AI clip this much shorter than the part holds its last frame
ASPECT = 0.05  # the AI picture's aspect may differ this much from the crop box's
CRF = 16  # the one re-encode of the scene: visually lossless
AI_LIT = 0.02  # an AI clip's edge row or column with more of its pixels above BAR_MAX is picture
AI_MIN_SHARE = 0.4  # the AI picture inside its bars is at least this share of each side
TRIM_GAIN = 0.01  # removing AI bars must bring the aspect at least this much closer to the box's


@dataclass(frozen=True)
class AiClip:
    width: int
    height: int
    frames: int
    rate: Fraction  # read from the file, never assumed
    box: tuple[int, int, int, int]  # its picture inside any black bars the AI added


def _lit_shares(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """The share of every row's and every column's pixels above BAR_MAX over all frames,
    and how many frames there were. Robust to a few noisy pixels in generated bars."""
    rows = columns = None
    frames = 0
    with av.open(str(path)) as container:
        stream = _video_stream(container, path)
        for frame in container.decode(stream):
            bright = frame.to_ndarray(format="gray") > BAR_MAX
            row, column = bright.mean(axis=1), bright.mean(axis=0)
            rows = row if rows is None else rows + row
            columns = column if columns is None else columns + column
            frames += 1
    if rows is None or columns is None:
        raise SceneError(f"{Path(path).name} has no video frames.")
    return rows / frames, columns / frames, frames


def _trims(lit: np.ndarray) -> list[tuple[int, int]]:
    """(first, stop) choices along one side, most preferred first: no trim, the same trim
    on both ends (AI bars are symmetric), the trim as found (even numbers)."""
    size = len(lit) // 2 * 2
    found = np.flatnonzero(lit)
    if not len(found):
        return [(0, size)]
    first, stop = _inward(found)
    side = min(first, size - stop)
    return [(0, size), (side, size - side), (first, stop)]


def _ai_box(rows: np.ndarray, columns: np.ndarray, aspect: float) -> tuple[int, int, int, int]:
    """The AI picture inside its bars: the box whose aspect is closest to `aspect`, at least
    AI_MIN_SHARE of each side. A trim is taken only when it is TRIM_GAIN closer than one
    with less trimming, so a clip of the right aspect keeps a dark edge of its picture."""
    height, width = len(rows), len(columns)
    options = sorted(
        (
            (rank_x + rank_y, (left, top, right - left, bottom - top))
            for rank_x, (left, right) in enumerate(_trims(columns > AI_LIT))
            for rank_y, (top, bottom) in enumerate(_trims(rows > AI_LIT))
        ),
        key=lambda option: option[0],
    )

    def off(box):
        return abs(math.log(box[2] / box[3] / aspect))

    best = options[0][1]
    for _rank, box in options[1:]:
        if box[2] < AI_MIN_SHARE * width or box[3] < AI_MIN_SHARE * height:
            continue
        if off(box) < off(best) - TRIM_GAIN:
            best = box
    return best


def ai_clip(raw: Path, segment: dict) -> AiClip:
    """Facts about the AI's clip. Refuses one too short or with another aspect (cropped)."""
    with av.open(str(raw)) as probe:
        stream = _video_stream(probe, raw)
        rate = Fraction(stream.average_rate or FPS)
        width, height = stream.width, stream.height
    crop = segment["crop"]
    rows, columns, frames = _lit_shares(raw)
    box = _ai_box(rows, columns, crop["w"] / crop["h"])
    seconds = float(frames / rate)
    aspect = (box[2] / box[3]) / (crop["w"] / crop["h"])
    if seconds < segment["seconds"] - SHORT or abs(aspect - 1) > ASPECT:
        raise SceneError(
            f"The AI clip is {width}x{height}, {seconds:.1f} s; it can't be laid back into the "
            f"{segment['seconds']:.1f} s shot. It is kept at {raw}; nothing more was charged. "
            "A new draft would most likely come back the same, so don't buy one; this needs a "
            "fix in the tool."
        )
    return AiClip(width, height, frames, rate, box)


def _mask(
    path: Path,
    size: tuple[int, int],
    *,
    frame: tuple[int, int] | None = None,
    box: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """A mask PNG as booleans of `size`: first scaled to `frame`, then cut to `box`."""
    image = Image.open(path).convert("L")
    if frame is not None and image.size != frame:
        image = image.resize(frame, Image.Resampling.NEAREST)
    if box is not None:
        left, top, width, height = box
        image = image.crop((left, top, left + width, top + height))
    if image.size != size:
        image = image.resize(size, Image.Resampling.BILINEAR)
    return np.asarray(image) > 127


def paste_back(
    scene: Path, raw: Path, masks_a: Path, masks_b: Path | None, segment: dict, dst: Path
) -> None:
    """Lay the AI's person back into the scene's own frames, one frame at a time (no sound).

    masks_a: the job's subject masks, one PNG per job frame. masks_b: SAM 3 "person" on the AI
    clip, one PNG per AI frame, or None. Outside the part every frame is written as decoded;
    inside it only lay_alpha's region changes, never outside the crop box. The video keeps the
    scene's size, frame count and rate. Add the sound with
    media.audio.add_original_soundtrack(..., fit=False).
    """
    clip = ai_clip(raw, segment)
    crop = segment["crop"]
    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    first, stop = segment["start_frame"], segment["end_frame"]
    frame_map = segment["frame_map"]
    made_from = job_frames(frame_map, first, stop)
    a_files = sorted(Path(masks_a).glob("mask_*.png"))
    b_files = sorted(Path(masks_b).glob("mask_*.png")) if masks_b else []
    if len(a_files) < len(frame_map):
        raise SceneError(
            f"The job has {len(a_files)} masks for the part's {len(frame_map)} frames. "
            "Prepare a new job."
        )
    left, top, width, height = clip.box
    with av.open(str(scene)) as container, av.open(str(raw)) as ai_container:
        stream = _video_stream(container, scene)
        ai_frames = ai_container.decode(_video_stream(ai_container, raw))
        ai_index, ai_frame = -1, None
        rate = Fraction(segment["fps"])
        with VideoWriter(dst, stream.width, stream.height, fps=rate, crf=CRF) as writer:
            for index, frame in enumerate(container.decode(stream)):
                picture = frame.to_ndarray(format="rgb24")
                if first <= index < stop:
                    k = made_from[index - first]
                    wanted = min(round(k * clip.rate / FPS), clip.frames - 1)
                    while ai_index < wanted:
                        following = next(ai_frames, None)
                        if following is None:
                            break
                        ai_frame, ai_index = following, ai_index + 1
                    a = _mask(a_files[k], (w, h))
                    b = None
                    if b_files:
                        b_file = b_files[min(ai_index, len(b_files) - 1)]
                        b = _mask(b_file, (w, h), frame=(clip.width, clip.height), box=clip.box)
                    alpha = lay_alpha(a, b) if a.any() else None
                    if alpha is not None and ai_frame is not None:
                        art = ai_frame.to_image().crop((left, top, left + width, top + height))
                        art = np.asarray(art.resize((w, h), Image.Resampling.LANCZOS))
                        part = picture[y : y + h, x : x + w].astype(np.float32)
                        mixed = part + alpha[:, :, None] * (art.astype(np.float32) - part)
                        picture = picture.copy()
                        picture[y : y + h, x : x + w] = np.rint(mixed).astype(np.uint8)
                writer.write(picture)
