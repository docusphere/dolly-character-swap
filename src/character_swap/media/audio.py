"""Audio helpers: read/write stereo float audio, embed it in videos, restore originals."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from character_swap.media.video import iter_frames

RATE = 44100
HOLD_GAP = (0.25, 0.5)  # seconds: short provider rounding gaps fixed by holding the last frame
MAX_MISMATCH = 0.25


class NoAudioError(ValueError):
    """The file has no audio track."""


class DurationMismatchError(ValueError):
    """Generated video length differs too much from the original audio."""


def read_audio(path: Path) -> np.ndarray:
    """Return float32 stereo samples shaped (2, n) at 44.1 kHz."""
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
    parts = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise NoAudioError(f"{Path(path).name} has no audio track.")
        for frame in container.decode(audio=0):
            parts.extend(chunk.to_ndarray() for chunk in resampler.resample(frame))
        parts.extend(chunk.to_ndarray() for chunk in resampler.resample(None))
    return np.concatenate(parts, axis=1)


def _encode_samples(container, stream, samples: np.ndarray) -> None:
    for start in range(0, samples.shape[1], 1024):
        chunk = np.ascontiguousarray(samples[:, start : start + 1024].astype(np.float32))
        frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
        frame.sample_rate, frame.pts = RATE, start
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)


def write_wav(path: Path, samples: np.ndarray) -> Path:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("pcm_s16le", rate=RATE)
        stream.layout = "stereo"
        _encode_samples(container, stream, samples)
    return Path(path)


def _copy_video_packets(source, output):
    stream = output.add_stream_from_template(source.streams.video[0])
    for packet in source.demux(video=0):
        if packet.dts is not None:
            packet.stream = stream
            output.mux(packet)


def embed_samples(video: Path, samples: np.ndarray, dst: Path) -> Path:
    """Copy the video stream untouched and add `samples` as an AAC track."""
    with av.open(str(video)) as source, av.open(str(dst), "w") as output:
        audio = output.add_stream("aac", rate=RATE)
        audio.layout, audio.bit_rate = "stereo", 192000
        _copy_video_packets(source, output)
        _encode_samples(output, audio, samples)
    return Path(dst)


def _video_seconds(path: Path) -> float:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration:
            return float(stream.duration * stream.time_base)
        return float(container.duration / av.time_base)


def _audio_seconds(path: Path) -> float:
    with av.open(str(path)) as container:
        return float(container.duration / av.time_base)


def _hold_last_frame(generated: Path, seconds: float, dst: Path) -> Path:
    frames = list(iter_frames(generated))
    with av.open(str(generated)) as probe:
        rate = probe.streams.video[0].average_rate or Fraction(24, 1)
    height, width = frames[0].shape[:2]
    with av.open(str(dst), "w") as output:
        stream = output.add_stream("libx264", rate=rate)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"crf": "18"}
        for index in range(round(seconds * float(rate))):
            frame = av.VideoFrame.from_ndarray(frames[min(index, len(frames) - 1)], format="rgb24")
            frame.pts, frame.time_base = index, 1 / rate
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return dst


def restore_original_audio(generated: Path, original: Path, dst: Path) -> Path:
    """Replace the generated audio with the original's audio packets, byte for byte."""
    gap = _audio_seconds(original) - _video_seconds(generated)
    if HOLD_GAP[0] < gap <= HOLD_GAP[1]:
        generated = _hold_last_frame(
            generated, _audio_seconds(original), dst.with_suffix(".held.mp4")
        )
        gap = _audio_seconds(original) - _video_seconds(generated)
    if abs(gap) > MAX_MISMATCH:
        raise DurationMismatchError(
            f"Generated video differs from the original audio by {gap:+.2f}s; "
            "refusing to trim, loop or stretch the soundtrack."
        )
    with (
        av.open(str(generated)) as video,
        av.open(str(original)) as source,
        av.open(str(dst), "w") as output,
    ):
        if not source.streams.audio:
            raise NoAudioError("The original video has no audio track.")
        audio = output.add_stream_from_template(source.streams.audio[0])
        _copy_video_packets(video, output)
        for packet in source.demux(audio=0):
            if packet.dts is not None:
                packet.stream = audio
                output.mux(packet)
    return Path(dst)
