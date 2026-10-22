"""Describe the setting (never the people) so the generator keeps the real location."""

from __future__ import annotations

from character_swap.jobs import Job
from character_swap.media.video import middle_frame

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
    output = replicate.run(
        model=DESCRIBE_MODEL,
        input={"prompt": PROMPT, "image_input": [url], "max_completion_tokens": 200},
        record=job.path("describe-prediction.json"),
    )
    text = "".join(output) if isinstance(output, list) else str(output)
    text = " ".join(text.split())
    job.update(scene=text)
    return text
