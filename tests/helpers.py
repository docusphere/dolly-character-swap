"""Synthetic media builders for tests. Everything is tiny so tests stay fast."""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

RATE = 44100


def solid(color, size=(64, 64)) -> np.ndarray:
    width, height = size
    return np.full((height, width, 3), color, dtype=np.uint8)


def tone(seconds: float, freq: float = 440.0) -> np.ndarray:
    n = int(RATE * seconds)
    wave = (np.sin(np.arange(n) * 2 * np.pi * freq / RATE) * 0.1).astype(np.float32)
    return np.tile(wave, (2, 1))


def write_video(path: Path, frames, fps: int = 24, audio_seconds: float | None = None) -> Path:
    frames = list(frames)
    height, width = frames[0].shape[:2]
    with av.open(str(path), "w") as out:
        video = out.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        audio = None
        if audio_seconds:
            audio = out.add_stream("aac", rate=RATE)
            audio.layout = "stereo"
        for i, array in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            frame.pts, frame.time_base = i, Fraction(1, fps)
            for packet in video.encode(frame):
                out.mux(packet)
        for packet in video.encode():
            out.mux(packet)
        if audio:
            samples = tone(audio_seconds)
            for i in range(0, samples.shape[1], 1024):
                chunk = np.ascontiguousarray(samples[:, i : i + 1024])
                frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                frame.sample_rate, frame.pts = RATE, i
                for packet in audio.encode(frame):
                    out.mux(packet)
            for packet in audio.encode():
                out.mux(packet)
    return path


def write_wav(path: Path, samples: np.ndarray) -> Path:
    with av.open(str(path), "w") as out:
        stream = out.add_stream("pcm_s16le", rate=RATE)
        stream.layout = "stereo"
        for i in range(0, samples.shape[1], 4096):
            chunk = np.ascontiguousarray(samples[:, i : i + 4096].astype(np.float32))
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate, frame.pts = RATE, i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    return path


def write_mask_pngs(folder: Path, count: int, box=(16, 16, 48, 48), size=(64, 64)) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = box
    for i in range(count):
        mask = np.zeros((size[1], size[0]), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255
        Image.fromarray(mask).save(folder / f"mask_{i:05d}.png")
    return folder
