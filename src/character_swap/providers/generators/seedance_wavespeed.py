"""Seedance 2.5 on WaveSpeed: redraws the disguised person as your character."""

from __future__ import annotations

import math
from pathlib import Path

from character_swap.costs import Estimate, cost_line
from character_swap.media.upload import UploadTooLarge, fit_for_upload
from character_swap.modes import GENERATOR_LIMITS
from character_swap.prompts import check_placeholders
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators.base import GenerationRequest, OnCreated, Stage
from character_swap.providers.wavespeed import MAX_UPLOAD_BYTES

MODEL = "bytedance/seedance-2.5/video-edit"
DRAFT = "480p"
# First = default. Every final is a fresh run: WaveSpeed has no draft upgrade and no seed.
FINALS = ("720p", "1080p")
PRICE = {"480p": "wavespeed-480", "720p": "wavespeed-720", "1080p": "wavespeed-1080"}
BILLED_SECONDS = (4, 30)  # WaveSpeed bills at least 4 and at most 30 seconds of input video
UPLOAD_COPY = "upload-copy.mp4"


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

    def generate(
        self,
        request: GenerationRequest,
        stage: Stage,
        *,
        record: Path,
        dest: Path,
        on_created: OnCreated | None = None,
    ) -> Path:
        resolution = self.resolution(request, stage)
        if record.exists():  # resume: run() reads the saved prediction and never posts again
            body: dict = {}
        else:
            self._check(request)  # free, before any upload
            body = self.build_input(
                request,
                video_url=self._upload_video(request, record.parent),
                image_urls=[self._wavespeed.upload(request.primary.image_path)],
                resolution=resolution,
            )
        url = self._wavespeed.run(body=body, record=record, on_created=on_created)
        try:
            return self._wavespeed.download(url, dest)
        except OutputExpired as exc:
            rebuy = self.estimate(request, stage).total
            raise ProviderError(
                f"The Seedance {stage} finished but its download link expired ({exc}). Delete "
                f"{record} and run the same command again to make a new one (costs about "
                f"${rebuy:.2f})."
            ) from exc

    @staticmethod
    def _check(request: GenerationRequest) -> None:
        if not request.prompt.strip():
            raise ProviderError(
                "Seedance needs a prompt; see charswap prompt <job>. Nothing was charged."
            )
        if request.secondary is not None:
            raise ProviderError("Two people need --provider piapi for now. Nothing was charged.")
        photo = request.primary.image_path
        if not Path(photo).is_file():
            raise ProviderError(f"Your character photo {photo} is missing. Nothing was charged.")
        check_placeholders(request.prompt, provider="wavespeed", has_secondary=False)

    def _upload_video(self, request: GenerationRequest, folder: Path) -> str:
        try:
            video = fit_for_upload(request.video, folder / UPLOAD_COPY, MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            raise ProviderError(f"{exc} Nothing was charged.") from exc
        try:
            return self._wavespeed.upload(video)
        finally:
            if video != request.video:  # the compressed copy is private media: don't keep it
                video.unlink(missing_ok=True)
