"""Split the original audio into vocals and music with Demucs (Replicate)."""

from __future__ import annotations

import numpy as np

from character_swap.jobs import Job
from character_swap.media.audio import read_audio, write_wav
from character_swap.stages import StageError, bad_output, download_or_fail

DEMUCS_VERSION = "25a173108cff36ef9f80f854c162d01df9e6528be175794b81158fa03836d953"
STEMS = frozenset({"vocals", "drums", "bass", "other"})


def separate_vocals(job: Job, replicate) -> None:
    original = read_audio(job.path("upload.mp4"))
    write_wav(job.path("original-audio.wav"), original)
    url = replicate.upload(job.path("original-audio.wav"))
    record = job.path("demucs-prediction.json")
    output = replicate.run(
        version=DEMUCS_VERSION,
        input={"audio": url, "model_name": "htdemucs", "output_format": "wav"},
        record=record,
    )
    if not isinstance(output, dict):
        raise bad_output(record, "Demucs", output)
    if not all(output.get(name) for name in STEMS):
        raise StageError(
            "Vocal separation returned incomplete stems. "
            f"Delete {record} and resume to regenerate it (costs ~$0.017)."
        )
    stems = {}
    for name in sorted(STEMS):
        path = download_or_fail(
            replicate,
            output[name],
            job.path(f"stem-{name}.wav"),
            record=record,
            what="Demucs",
            cost="~$0.017",
        )
        stems[name] = read_audio(path)
    write_wav(job.path("vocals.wav"), stems.pop("vocals"))
    music = np.zeros_like(original)
    for samples in stems.values():
        n = min(music.shape[1], samples.shape[1])
        music[:, :n] += samples[:, :n]
    write_wav(job.path("music.wav"), np.clip(music, -1, 1))
