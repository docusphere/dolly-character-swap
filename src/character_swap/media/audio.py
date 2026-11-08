"""Audio helpers: read/write stereo float audio, embed it in videos, restore originals."""

from __future__ import annotations

import tempfile
from pathlib import Path

import av
import numpy as np

from character_swap.media.video import VideoWriter, iter_frames, stream_seconds

RATE = 44100
FIT_LIMIT = 0.5  # seconds: provider length differences we fix by holding/dropping end frames
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
    if not parts:
        raise NoAudioError(f"{Path(path).name} has no decodable audio.")
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


def _fit_video(generated: Path, seconds: float, dst: Path) -> Path:
    """Re-encode `generated` to `seconds` at its own frame rate (streamed, O(1) memory).

    Extra frames at the end are dropped; a short video repeats its last frame.
    """
    with av.open(str(generated)) as probe:
        rate = int(round(float(probe.streams.video[0].average_rate or 24)))
    target = round(seconds * rate)
    last, written = None, 0
    first = next(iter_frames(generated))
    with VideoWriter(dst, first.shape[1], first.shape[0], fps=rate) as writer:
        for last in iter_frames(generated):
            if written == target:
                break
            writer.write(last)
            written += 1
        while written < target:
            writer.write(last)
            written += 1
    return dst


def restore_original_audio(
    generated: Path, original: Path, dst: Path, *, seconds: float | None = None
) -> Path:
    """Replace the generated audio with the original's audio packets, byte for byte.

    With seconds, the original sound stops there (later packets are left out, never re-encoded).
    """
    with av.open(str(original)) as probe:
        if not probe.streams.audio:
            raise NoAudioError("The original video has no audio track.")
    audio_seconds = stream_seconds(original, "audio")
    if seconds is not None:
        audio_seconds = min(audio_seconds, seconds)
    gap = audio_seconds - stream_seconds(generated, "video")
    with tempfile.TemporaryDirectory() as scratch:
        if MAX_MISMATCH < abs(gap) <= FIT_LIMIT:
            generated = _fit_video(generated, audio_seconds, Path(scratch) / "fitted.mp4")
            gap = audio_seconds - stream_seconds(generated, "video")
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
            audio = output.add_stream_from_template(source.streams.audio[0])
            _copy_video_packets(video, output)
            first = None
            for packet in source.demux(audio=0):
                if packet.dts is None:
                    continue
                if seconds is not None and packet.pts is not None:
                    moment = float(packet.pts * packet.time_base)
                    first = moment if first is None else first
                    if moment - first >= seconds:
                        break
                packet.stream = audio
                output.mux(packet)
    return Path(dst)


def copy_video_only(video: Path, dst: Path) -> Path:
    """Copy the video stream untouched and leave every audio stream out."""
    with av.open(str(video)) as source, av.open(str(dst), "w") as output:
        _copy_video_packets(source, output)
    return Path(dst)


def add_original_soundtrack(
    generated: Path, upload: Path, dst: Path, *, seconds: float | None = None
) -> Path:
    """Generated pictures + the upload's untouched audio; silent if the upload had none."""
    with av.open(str(upload)) as probe:
        has_audio = bool(probe.streams.audio)
    if not has_audio:
        return copy_video_only(generated, dst)
    return restore_original_audio(generated, upload, dst, seconds=seconds)
