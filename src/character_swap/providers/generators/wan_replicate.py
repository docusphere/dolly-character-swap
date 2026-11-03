"""Wan 2.2 Animate Replace on Replicate: puts the character into the original video."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from character_swap.costs import Estimate, cost_line
from character_swap.modes import GENERATOR_LIMITS
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators.base import GenerationRequest, OnCreated, Stage

if TYPE_CHECKING:  # type only: pipeline will import this module (avoid an import cycle)
    from character_swap.pipeline import ReplicateLike

# Official model: called through the model endpoint (no version pin). Inputs verified
# 2026-11-03; see docs/research/2026-10-22-provider-api-facts.md.
WAN_MODEL = "wan-video/wan-2.2-animate-replace"
RESOLUTION: dict[str, str] = {"draft": "480", "final": "720"}
PRICE_KEY: dict[str, str] = {"draft": "wan-480", "final": "wan-720"}


class WanGenerator:
    name = "wan"
    provider = "replicate"
    model = WAN_MODEL
    min_seconds, max_seconds = GENERATOR_LIMITS["wan"]
    supports_real_faces = True  # open model, no face filter
    supports_secondary = False

    def __init__(self, replicate: ReplicateLike) -> None:
        self._replicate = replicate

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate:
        return Estimate(lines=(cost_line(PRICE_KEY[stage], request.seconds),))

    @staticmethod
    def build_input(
        request: GenerationRequest, stage: Stage, *, video_url: str, image_url: str
    ) -> dict:
        return {
            "video": video_url,
            "character_image": image_url,
            "resolution": RESOLUTION[stage],
            "seed": request.seed,
            "merge_audio": False,  # the original soundtrack is restored locally, byte for byte
            "go_fast": False,
        }

    def generate(
        self,
        request: GenerationRequest,
        stage: Stage,
        *,
        record: Path,
        dest: Path,
        on_created: OnCreated | None = None,
    ) -> Path:
        if record.exists():  # resume: run() reads the saved prediction and ignores the input
            payload: dict = {}
        else:
            payload = self.build_input(
                request,
                stage,
                video_url=self._replicate.upload(request.video),
                image_url=self._replicate.upload(request.primary.image_path),
            )
        output = self._replicate.run(
            model=WAN_MODEL, input=payload, record=record, on_created=on_created
        )
        if not isinstance(output, str) or not output.startswith("https://"):
            raise ProviderError(
                f"Wan returned an unexpected output: {output!r}. Check the prediction saved in "
                f"{record} on replicate.com before deleting it (a new run costs money)."
            )
        try:
            return self._replicate.download(output, dest)
        except OutputExpired as exc:
            rebuy = self.estimate(request, stage).total
            raise ProviderError(
                f"The Wan {stage} finished but its download link expired ({exc}). "
                f"Delete {record} and run the same command again to make a new one "
                f"(costs about ${rebuy:.2f})."
            ) from exc
