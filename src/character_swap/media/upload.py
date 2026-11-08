"""A copy of a video small enough to upload, with its audio packets untouched."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av

QUALITIES = (23, 28, 33)  # x264 CRF: higher = smaller file, softer picture


class UploadTooLarge(ValueError):
    """Even the most compressed copy is over the upload limit."""


def fit_for_upload(src: Path, dst: Path, max_bytes: int) -> Path:
    """`src` itself when it is small enough; otherwise a re-encoded copy at `dst`.

    The copy keeps every frame, the frame rate, the size and the audio packets exactly;
    only the picture is compressed harder. Input must be constant frame rate (prepared
    clips always are); frames are renumbered at its average rate.
    """
    src, dst = Path(src), Path(dst)
    if src.stat().st_size <= max_bytes:
        return src
    try:
        for crf in QUALITIES:
            _reencode(src, dst, crf)
            if dst.stat().st_size <= max_bytes:
                return dst
    except BaseException:
        dst.unlink(missing_ok=True)  # never leave a half-written copy behind
        raise
    dst.unlink(missing_ok=True)
    raise UploadTooLarge(
        f"{src.name} is still over {max_bytes / 1e6:.0f} MB after compressing. Use a shorter clip."
    )


def _reencode(src: Path, dst: Path, crf: int) -> None:
    with av.open(str(src)) as source, av.open(str(dst), "w") as output:
        video_in = source.streams.video[0]
        fps = int(round(float(video_in.average_rate or 24)))
        video = output.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = video_in.width, video_in.height, "yuv420p"
        video.options = {"crf": str(crf), "preset": "medium"}
        audio_in = source.streams.audio[0] if source.streams.audio else None
        audio = output.add_stream_from_template(audio_in) if audio_in else None
        index = 0
        for packet in source.demux(*[s for s in (video_in, audio_in) if s is not None]):
            if packet.stream.type == "video":
                for frame in packet.decode():
                    frame.pts, frame.time_base = index, Fraction(1, fps)
                    index += 1
                    for out in video.encode(frame):
                        output.mux(out)
            elif packet.dts is not None:
                packet.stream = audio
                output.mux(packet)
        for out in video.encode():
            output.mux(out)
