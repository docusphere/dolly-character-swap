"""Build the Seedance prompt the user reviews (and can edit) before generating."""

from __future__ import annotations

from character_swap.errors import CharSwapError

MAX_PROMPT = 4000
MAX_SCENE = 300

# How each provider names the inputs inside the prompt. The text sent must use these exactly.
NAMES: dict[str, dict[str, str]] = {
    "wavespeed": {"video": "@Video 1", "image1": "@Image 1", "image2": "@Image 2"},
    "piapi": {"video": "@video1", "image1": "@image1", "image2": "@image2"},
}

BASE = (
    "Replace the person in {video} with the character in {image1} in every frame, including "
    "face, hair, hands, clothing and body shape. Keep the original camera framing, motion, "
    "gestures, timing and lighting. Use the vocals embedded in {video} only for lip "
    "synchronization and performance; do not add new dialogue."
)
MODE_NOTES = {
    "depth": (
        "{video} shows the people as a colored depth map over the real background. Render them "
        "as fully realistic people and remove all depth colors."
    ),
    "face_mesh": (
        "{video} has a face-mesh wireframe drawn over the face. Use it only as expression and "
        "lip-motion guidance and remove every mesh line."
    ),
    "depth_mesh": (
        "{video} shows the people as a colored depth map with a face-mesh wireframe on top. Use "
        "both only as motion guidance; render realistic people and remove every depth color and "
        "mesh line."
    ),
}
SECONDARY = "{image2} is a second character; use them for the second person in the scene."


class PromptError(CharSwapError, ValueError):
    """The prompt cannot be sent as it is (for example it is too long)."""


def _names(provider: str) -> dict[str, str]:
    try:
        return NAMES[provider]
    except KeyError:
        raise PromptError(f"Unknown Seedance provider {provider!r}.") from None


def _clean(text: str) -> str:
    """Neutralize untrusted text: no "@" at all, no double quotes, one line."""
    text = text.replace("@", "").replace('"', "'")
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1]
    if " " in cut and text[limit - 1] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def validate_prompt(text: str) -> str:
    if len(text) > MAX_PROMPT:
        raise PromptError(f"Prompt is {len(text)} characters; the limit is {MAX_PROMPT}.")
    return text


def check_placeholders(text: str, *, provider: str, has_secondary: bool) -> None:
    """The provider refuses (or misreads) prompts whose input names don't match what is sent."""
    names = _names(provider)
    for needed in (names["video"], names["image1"]):
        if needed not in text:
            raise PromptError(f"The prompt must mention {needed} (the clip and your character).")
    second = names["image2"]
    if has_secondary and second not in text:
        raise PromptError(f"This job has a second character: the prompt must mention {second}.")
    if not has_secondary and second in text:
        raise PromptError(
            f"The prompt mentions {second} but this job has no second character; remove it."
        )


def build_prompt(
    *,
    provider: str,
    mode: str,
    scene: str | None = None,
    transcript: str | None = None,
    has_secondary: bool = False,
) -> str:
    names = _names(provider)
    parts = [BASE.format(**names), MODE_NOTES[mode].format(**names)]
    if has_secondary:
        parts.append(SECONDARY.format(**names))
    scene = _clean(scene or "")
    if scene:
        parts.append(f"Setting: {_truncate(scene, MAX_SCENE)}")
    words = _clean(transcript or "")
    if words:
        template = 'The spoken words are: "{}". Match the lip movements to these words.'
        used = len("\n\n".join(parts)) + 2 + len(template.format(""))
        parts.append(template.format(_truncate(words, MAX_PROMPT - used)))
    return validate_prompt("\n\n".join(parts))
