"""Video helpers: decode frames, encode H.264, and normalize sources to 24 fps."""

from __future__ import annotations

from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

FPS = 24
MAX_SIDE = 960


class ClipLengthError(ValueError):
    """The clip is too short or too long for the chosen generator."""


def iter_frames(path: Path) -> Iterator[np.ndarray]:
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            yield frame.to_ndarray(format="rgb24")


def count_frames(path: Path) -> int:
    return sum(1 for _ in iter_frames(path))


def middle_frame(path: Path) -> Image.Image:
    frames = list(iter_frames(path))
    return Image.fromarray(frames[len(frames) // 2])


class VideoWriter:
    """Write RGB frames to an H.264 MP4. Use as a context manager."""

    def __init__(self, path: Path, width: int, height: int, fps: int = FPS):
        self._container = av.open(str(path), "w")
        self._stream = self._container.add_stream("libx264", rate=fps)
        self._stream.width, self._stream.height = width, height
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = {"crf": "18", "preset": "fast"}
        self._time_base = Fraction(1, fps)
        self._index = 0

    def write(self, frame: np.ndarray) -> None:
        video_frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
        video_frame.pts, video_frame.time_base = self._index, self._time_base
        for packet in self._stream.encode(video_frame):
            self._container.mux(packet)
        self._index += 1

    def close(self) -> None:
        for packet in self._stream.encode():
            self._container.mux(packet)
        self._container.close()

    def __enter__(self) -> VideoWriter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _seconds(container, stream) -> float:
    if stream.duration:
        return float(stream.duration * stream.time_base)
    return float(container.duration / av.time_base)


def normalize(src: Path, dst: Path, *, min_seconds: float, max_seconds: float) -> dict:
    """Re-time to 24 fps (holding the nearest earlier frame) and fit within 960 px."""
    with av.open(str(src)) as container:
        stream = container.streams.video[0]
        seconds = _seconds(container, stream)
        if seconds > max_seconds + 0.05:
            raise ClipLengthError(
                f"Clip is {seconds:.1f}s; this generator accepts at most {max_seconds:g}s. Trim it."
            )
        if seconds < min_seconds - 0.05:
            raise ClipLengthError(
                f"Clip is {seconds:.1f}s; this generator needs at least {min_seconds:g}s."
            )
        decoded = container.decode(video=0)
        current = next(decoded)
        start = float(current.time or 0)
        upcoming = next(decoded, None)
        scale = min(1.0, MAX_SIDE / max(current.width, current.height))
        width = max(2, int(current.width * scale) // 2 * 2)
        height = max(2, int(current.height * scale) // 2 * 2)
        count = round(seconds * FPS)
        with VideoWriter(dst, width, height) as writer:
            for index in range(count):
                moment = start + index / FPS
                while upcoming is not None and float(upcoming.time) <= moment:
                    current, upcoming = upcoming, next(decoded, None)
                image = current.to_image().resize((width, height), Image.Resampling.LANCZOS)
                writer.write(np.asarray(image))
    return {"frames": count, "fps": FPS, "width": width, "height": height, "seconds": count / FPS}
