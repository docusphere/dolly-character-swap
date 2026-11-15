"""Describe the setting (never the people) so the generator keeps the real location."""

from __future__ import annotations

import json
from pathlib import Path

from character_swap.jobs import Job
from character_swap.media.video import middle_frame
from character_swap.providers.errors import ProviderError
from character_swap.stages import StageError, bad_output

DESCRIBE_MODEL = "openai/gpt-4o-mini"  # official model: called via the model endpoint
PROMPT = (
    "Describe only the setting of this video frame in 2-3 sentences: location, background "
    "objects, colors, lighting and camera framing. Do not describe any people, faces, "
    "clothing or identities."
)


def describe_scene(job: Job, replicate) -> str:
    frame = job.path("scene.jpg")
    middle_frame(job.path("original.mp4")).save(frame, quality=90)
    url = replicate.upload(frame)
    record = job.path("describe-prediction.json")
    try:
        output = replicate.run(
            model=DESCRIBE_MODEL,
            input={"prompt": PROMPT, "image_input": [url], "max_completion_tokens": 200},
            record=record,
        )
    except ProviderError as exc:
        said = _refusal(record)
        if said is None:
            raise  # unconfirmed, damaged, still running: the client's own message says what to do
        raise StageError(
            f"GPT-4o mini would not describe this setting (it said: {said}). Describe it "
            f'yourself and continue for free: charswap resume {job.id} --setting "a dim room '
            f'with ...". (Deleting {record.name} and resuming asks it again, about $0.001.)'
        ) from exc
    if isinstance(output, str):
        parts = [output]
    elif isinstance(output, list) and all(isinstance(p, str) for p in output):
        parts = output
    else:
        raise bad_output(record, "Scene description", output)
    text = " ".join("".join(parts).split())
    if not text:
        raise bad_output(record, "Scene description", output)
    job.update(scene=text)
    return text


def _refusal(record: Path) -> str | None:
    """What a failed or canceled description said; None for any other saved record."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in ("failed", "canceled"):
        return None
    return str(data.get("error") or "no reason given")


def use_setting(job: Job, text: str) -> str:
    """The setting you wrote (--setting): no call, nothing paid. scene.jpg is still saved."""
    middle_frame(job.path("original.mp4")).save(job.path("scene.jpg"), quality=90)
    job.update(scene=text)
    return text
