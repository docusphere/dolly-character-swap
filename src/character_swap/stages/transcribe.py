"""Transcribe the isolated vocals so the prompt can quote the exact words."""

from __future__ import annotations

from character_swap.jobs import Job
from character_swap.stages import bad_output

# openai/whisper (large-v3) is a community model, so it is called by pinned version.
WHISPER_VERSION = "8099696689d249cf8b122d833c36ac3f75505c666a395ca40ef26f68e7d3d16e"


def whisper_input(url: str, language: str | None) -> dict:
    return {
        "audio": url,
        "transcription": "plain text",
        "language": language or "auto",
        # On by default: one misheard window (rap over a beat) made Whisper skip the rest
        # of the first live clip ("Let's get it." only). Off, the lyrics came through.
        "condition_on_previous_text": False,
    }


def transcribe(job: Job, replicate) -> str:
    url = replicate.upload(job.path("vocals.wav"))
    record = job.path("whisper-prediction.json")
    output = replicate.run(
        version=WHISPER_VERSION,
        input=whisper_input(url, job.state.get("language")),
        record=record,
    )
    if not isinstance(output, dict):
        raise bad_output(record, "Whisper", output)
    text = output.get("transcription")
    if text is None:
        text = ""
    elif not isinstance(text, str):
        raise bad_output(record, "Whisper", output)
    text = text.strip()
    job.update(transcript=text)
    return text
