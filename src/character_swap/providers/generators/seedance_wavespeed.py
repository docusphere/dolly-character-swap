"""Seedance 2.5 on WaveSpeed: redraws the disguised person as your character."""

from __future__ import annotations

import math

from character_swap.costs import Estimate, cost_line
from character_swap.modes import GENERATOR_LIMITS
from character_swap.providers.errors import ProviderError
from character_swap.providers.generators.base import GenerationRequest, Stage

MODEL = "bytedance/seedance-2.5/video-edit"
DRAFT = "480p"
# First = default. Every final is a fresh run: WaveSpeed has no draft upgrade and no seed.
FINALS = ("720p", "1080p")
PRICE = {"480p": "wavespeed-480", "720p": "wavespeed-720", "1080p": "wavespeed-1080"}
BILLED_SECONDS = (4, 30)  # WaveSpeed bills at least 4 and at most 30 seconds of input video


class SeedanceWaveSpeedGenerator:
    name = "seedance"
    provider = "wavespeed"
    model = MODEL
    min_seconds, max_seconds = GENERATOR_LIMITS["seedance"]
    supports_real_faces = True  # a plain photo; no face library
    supports_secondary = False  # two people stay on PiAPI for now
    finals = FINALS
    promotes = False

    def __init__(self, wavespeed) -> None:
        self._wavespeed = wavespeed

    @staticmethod
    def resolution(request: GenerationRequest, stage: Stage) -> str:
        if stage == "draft":
            return DRAFT
        chosen = request.final_resolution or FINALS[0]
        if chosen not in FINALS:
            raise ProviderError(
                f"WaveSpeed finals are {' or '.join(FINALS)}, not {chosen}. Nothing was charged."
            )
        return chosen

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate:
        key = PRICE[self.resolution(request, stage)]
        low, high = BILLED_SECONDS
        seconds = min(max(math.ceil(request.seconds), low), high)  # only ever over-quote
        return Estimate(lines=(cost_line(key, seconds), cost_line(f"{key}-input", seconds)))

    @staticmethod
    def build_input(
        request: GenerationRequest, *, video_url: str, image_urls: list[str], resolution: str
    ) -> dict:
        return {
            "prompt": request.prompt,
            "video": video_url,
            "reference_images": list(image_urls),  # @Image 1
            "resolution": resolution,
            "generate_audio": False,  # the original soundtrack is restored locally afterwards
        }
