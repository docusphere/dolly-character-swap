"""Build the Seedance prompt the user reviews (and can edit) before generating."""

from __future__ import annotations

BASE = (
    "Replace the person in @video1 with the character in @image1 in every frame, including "
    "face, hair, hands, clothing and body shape. Keep the original camera framing, motion, "
    "gestures, timing and lighting. Use the vocals embedded in @video1 only for lip "
    "synchronization and performance; do not add new dialogue."
)
MODE_NOTES = {
    "depth": (
        "@video1 shows the people as a colored depth map over the real background. Render them "
        "as fully realistic people and remove all depth colors."
    ),
    "face_mesh": (
        "@video1 has a face-mesh wireframe drawn over the face. Use it only as expression and "
        "lip-motion guidance and remove every mesh line."
    ),
    "depth_mesh": (
        "@video1 shows the people as a colored depth map with a face-mesh wireframe on top. Use "
        "both only as motion guidance; render realistic people and remove every depth color and "
        "mesh line."
    ),
}
SECONDARY = "@image2 is a second character; use them for the second person in the scene."


def build_prompt(
    *,
    mode: str,
    scene: str | None = None,
    transcript: str | None = None,
    has_secondary: bool = False,
) -> str:
    parts = [BASE, MODE_NOTES[mode]]
    if has_secondary:
        parts.append(SECONDARY)
    if scene and scene.strip():
        parts.append(f"Setting: {scene.strip()}")
    if transcript and transcript.strip():
        parts.append(
            f'The spoken words are: "{transcript.strip()}". Match the lip movements to these words.'
        )
    return "\n\n".join(parts)
