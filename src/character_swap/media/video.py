"""Video helpers: decode frames, encode H.264, and normalize sources to 24 fps."""

from __future__ import annotations

import math
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Literal

import av
import numpy as np
from PIL import Image

FPS = 24
MAX_SIDE = 960


class ClipLengthError(ValueError):
    """The clip is too short or too long for the chosen generator."""


def iter_frames(path: Path) -> Iterator[np.ndarray]:
    with av.open(str(path)) as container:
        if not container.streams.video:
            return
        for frame in container.decode(video=0):
            yield frame.to_ndarray(format="rgb24")


def count_frames(path: Path) -> int:
    return sum(1 for _ in iter_frames(path))


def middle_frame(path: Path) -> Image.Image:
    """Return the middle frame without holding the whole clip in memory."""
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ClipLengthError(f"{Path(path).name} has no video frames.")
        total = container.streams.video[0].frames
    if not total:
        total = count_frames(path)
    target, last = total // 2, None
    for index, frame in enumerate(iter_frames(path)):
        last = frame
        if index == target:
            break
    if last is None:
        raise ClipLengthError(f"{Path(path).name} has no video frames.")
    return Image.fromarray(last)


class VideoWriter:
    """Write RGB frames to an H.264 MP4. Use as a context manager."""

    def __init__(
        self, path: Path, width: int, height: int, fps: int | Fraction = FPS, crf: int = 18
    ):
        self._path = Path(path)
        self._container = av.open(str(path), "w")
        self._stream = self._container.add_stream("libx264", rate=fps)
        self._stream.width, self._stream.height = width, height
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = {"crf": str(crf), "preset": "fast"}
        self._time_base = 1 / Fraction(fps)
        self._index = 0
        self._closed = False

    def write(self, frame: np.ndarray) -> None:
        video_frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
        video_frame.pts, video_frame.time_base = self._index, self._time_base
        for packet in self._stream.encode(video_frame):
            self._container.mux(packet)
        self._index += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for packet in self._stream.encode():
                self._container.mux(packet)
        finally:
            self._container.close()

    def _abort(self) -> None:
        """Close without flushing the encoder and drop the partial file."""
        if not self._closed:
            self._closed = True
            self._container.close()
        self._path.unlink(missing_ok=True)

    def __enter__(self) -> VideoWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self._abort()
        else:
            self.close()


def stream_seconds(path: Path, kind: Literal["video", "audio"]) -> float:
    """Duration of the first video/audio stream, falling back to the container duration."""
    with av.open(str(path)) as container:
        streams = container.streams.video if kind == "video" else container.streams.audio
        if not streams:
            raise ValueError(f"{Path(path).name} has no {kind} stream.")
        stream = streams[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if container.duration is not None:
            return float(container.duration / av.time_base)
    raise ValueError(f"Cannot determine the {kind} duration of {Path(path).name}.")


def _rotation_transpose(rotation: float) -> Image.Transpose | None:
    """Map a display rotation (counter-clockwise degrees, as PyAV reports it) to a transpose."""
    return {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }.get(round(rotation) % 360)


def normalize(
    src: Path, dst: Path, *, min_seconds: float, max_seconds: float, whole_seconds: bool = False
) -> dict:
    """Re-time to 24 fps (holding the nearest earlier frame), upright it, fit within 960 px.

    With whole_seconds, drop the tail to a whole number of seconds.
    """
    name = Path(src).name
    with av.open(str(src)) as container:
        if not container.streams.video:
            raise ClipLengthError(f"{name} has no video frames.")
    seconds = stream_seconds(src, "video")
    if seconds > max_seconds + 0.05:
        raise ClipLengthError(
            f"Clip is {seconds:.1f}s; this generator accepts at most {max_seconds:g}s. Trim it."
        )
    if seconds < min_seconds - 0.05:
        raise ClipLengthError(
            f"Clip is {seconds:.1f}s; this generator needs at least {min_seconds:g}s."
        )
    count = round(seconds * FPS)
    if whole_seconds:
        count -= count % FPS  # whole seconds only: drop the tail
        if count < min_seconds * FPS:
            raise ClipLengthError(
                f"Clip is {seconds:.1f}s; trimmed to whole seconds it is {count // FPS}s, and "
                f"this generator needs at least {math.ceil(min_seconds):g}s."
            )
    with av.open(str(src)) as container:
        decoded = container.decode(video=0)
        current = next(decoded, None)
        if current is None:
            raise ClipLengthError(f"{name} has no video frames.")
        start = float(current.time or 0)
        upcoming = next(decoded, None)
        transpose = _rotation_transpose(getattr(current, "rotation", 0) or 0)
        src_w, src_h = current.width, current.height
        if transpose is not None and round(getattr(current, "rotation", 0)) % 180 == 90:
            src_w, src_h = src_h, src_w
        scale = min(1.0, MAX_SIDE / max(src_w, src_h))
        width = max(2, int(src_w * scale) // 2 * 2)
        height = max(2, int(src_h * scale) // 2 * 2)
        with VideoWriter(dst, width, height) as writer:
            for index in range(count):
                moment = start + index / FPS
                while upcoming is not None and float(upcoming.time) <= moment + 1e-6:
                    current, upcoming = upcoming, next(decoded, None)
                image = current.to_image()
                if transpose is not None:
                    image = image.transpose(transpose)
                image = image.resize((width, height), Image.Resampling.LANCZOS)
                writer.write(np.asarray(image))
    return {"frames": count, "fps": FPS, "width": width, "height": height, "seconds": count / FPS}
