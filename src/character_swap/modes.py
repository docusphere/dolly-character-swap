"""The one table of modes: generator, prep stages, paid stages and what review hashes."""

from __future__ import annotations

from dataclasses import dataclass

# Paid generation runs after review. It is tracked apart from preparation so a prepared but
# not yet generated job never looks "running".
GENERATION_STAGES: tuple[str, ...] = ("draft", "final")

# (min_seconds, max_seconds) per generator; see docs/research/2026-10-22-provider-api-facts.md.
GENERATOR_LIMITS: dict[str, tuple[int, int]] = {"seedance": (4, 30), "wan": (1, 30)}

# What each pipeline.generator_key's provider is called in messages and on the viewer page.
PROVIDER_NAMES: dict[str, str] = {
    "wan": "Replicate",
    "seedance:wavespeed": "WaveSpeed",
    "seedance:piapi": "PiAPI",
}


def generator_name(key: str) -> str:
    """e.g. "Seedance on WaveSpeed" for a pipeline.generator_key."""
    return f"{key.partition(':')[0].capitalize()} on {PROVIDER_NAMES[key]}"


# Generators whose clips are cut to whole seconds (PiAPI durations are whole seconds).
WHOLE_SECONDS: frozenset[str] = frozenset({"seedance"})


@dataclass(frozen=True)
class Mode:
    name: str
    generator: str
    prep_stages: tuple[str, ...]  # exactly what pipeline.prepare() runs, in order
    paid_stages: tuple[str, ...]  # prep stages that call a paid provider
    review_files: tuple[str, ...]  # hashed by the review gate (Wan adds the character image)
    uses_depth: bool
    uses_mesh: bool
    generates: bool  # draft/final available (every mode since Plan 2b)


_TEXT = ("separate", "transcribe", "describe")
_DEPTH = ("depth", "masks")

MODES: dict[str, Mode] = {
    "depth": Mode(
        name="depth",
        generator="seedance",
        prep_stages=(
            "normalize",
            "separate",
            "pitch",
            "transcribe",
            "describe",
            "depth",
            "masks",
            "composite",
            "embed",
        ),
        paid_stages=(*_TEXT, *_DEPTH),
        review_files=(
            "original.mp4",
            "depth.mp4",
            "mask.mp4",
            "composite-silent.mp4",
            "prepared.mp4",
        ),
        uses_depth=True,
        uses_mesh=False,
        generates=True,
    ),
    "face_mesh": Mode(
        name="face_mesh",
        generator="seedance",
        prep_stages=(
            "normalize",
            "separate",
            "pitch",
            "transcribe",
            "describe",
            "face_mesh",
            "embed",
        ),
        paid_stages=_TEXT,
        review_files=(
            "original.mp4",
            "face-landmarks.json",
            "composite-silent.mp4",
            "prepared.mp4",
        ),
        uses_depth=False,
        uses_mesh=True,
        generates=True,
    ),
    "depth_mesh": Mode(
        name="depth_mesh",
        generator="seedance",
        prep_stages=(
            "normalize",
            "separate",
            "pitch",
            "transcribe",
            "describe",
            "depth",
            "masks",
            "composite",
            "face_mesh",
            "embed",
        ),
        paid_stages=(*_TEXT, *_DEPTH),
        review_files=(
            "original.mp4",
            "depth.mp4",
            "mask.mp4",
            "depth-composite-silent.mp4",
            "face-landmarks.json",
            "composite-silent.mp4",
            "prepared.mp4",
        ),
        uses_depth=True,
        uses_mesh=True,
        generates=True,
    ),
    "direct": Mode(
        name="direct",
        generator="wan",
        prep_stages=("normalize",),
        paid_stages=(),
        review_files=("original.mp4",),
        uses_depth=False,
        uses_mesh=False,
        generates=True,
    ),
}

MODES_BY_GENERATOR: dict[str, tuple[str, ...]] = {
    generator: tuple(name for name, mode in MODES.items() if mode.generator == generator)
    for generator in GENERATOR_LIMITS
}
