"""Split the original audio into vocals and music with Demucs (Replicate)."""

from __future__ import annotations

import numpy as np

from character_swap.jobs import Job
from character_swap.media.audio import read_audio, write_wav
from character_swap.stages import StageError

DEMUCS_VERSION = "25a173108cff36ef9f80f854c162d01df9e6528be175794b81158fa03836d953"

__all__ = ["DEMUCS_VERSION", "StageError", "separate_vocals"]


def separate_vocals(job: Job, replicate) -> None:
    original = read_audio(job.path("upload.mp4"))
    write_wav(job.path("original-audio.wav"), original)
    url = replicate.upload(job.path("original-audio.wav"))
    output = replicate.run(
        version=DEMUCS_VERSION,
        input={"audio": url, "model_name": "htdemucs", "output_format": "wav"},
        record=job.path("demucs-prediction.json"),
    )
    stems = {}
    for name, stem_url in output.items():
        if stem_url:
            path = replicate.download(stem_url, job.path(f"stem-{name}.wav"))
            stems[name] = read_audio(path)
    if "vocals" not in stems or len(stems) < 2:
        raise StageError("Vocal separation returned incomplete stems. Re-run prepare to retry.")
    write_wav(job.path("vocals.wav"), stems.pop("vocals"))
    music = np.zeros_like(original)
    for samples in stems.values():
        n = min(music.shape[1], samples.shape[1])
        music[:, :n] += samples[:, :n]
    write_wav(job.path("music.wav"), np.clip(music, -1, 1))
