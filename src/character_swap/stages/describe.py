"""Describe the setting (never the people) so the generator keeps the real location."""

from __future__ import annotations

from character_swap.jobs import Job
from character_swap.media.video import middle_frame
from character_swap.stages import bad_output

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
    output = replicate.run(
        model=DESCRIBE_MODEL,
        input={"prompt": PROMPT, "image_input": [url], "max_completion_tokens": 200},
        record=record,
    )
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
