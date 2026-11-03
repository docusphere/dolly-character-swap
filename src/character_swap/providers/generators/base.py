"""What every video generator offers, so the pipeline and CLI never depend on one provider."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from character_swap.costs import Estimate

Stage = Literal["draft", "final"]
OnCreated = Callable[[dict], None]


@dataclass(frozen=True)
class CharacterRef:
    image_path: Path  # local copy in the job folder (Wan uploads this file)
    face_asset_id: str | None = None  # PiAPI face-library id (Plan 2b)


@dataclass(frozen=True)
class GenerationRequest:
    video: Path  # Seedance: the prepared composite; Wan: the normalized original
    primary: CharacterRef
    seconds: float  # normalized clip length, for estimates
    seed: int  # chosen once per job; the final reuses it
    prompt: str = ""  # Wan has no prompt input
    secondary: CharacterRef | None = None  # Seedance only


class Generator(Protocol):
    name: str
    provider: str
    model: str
    min_seconds: int
    max_seconds: int
    supports_real_faces: bool
    supports_secondary: bool

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate: ...

    def generate(
        self,
        request: GenerationRequest,
        stage: Stage,
        *,
        record: Path,
        dest: Path,
        on_created: OnCreated | None = None,
    ) -> Path:
        """Create the paid request once (or resume `record`), wait, and download to `dest`.

        The provider id is saved in `record` before waiting; an existing record is resumed,
        never re-bought. `on_created` runs once when a NEW paid request exists.
        """
        ...
