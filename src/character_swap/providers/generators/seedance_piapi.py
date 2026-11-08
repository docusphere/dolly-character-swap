"""Seedance 2.5 on PiAPI: redraws the disguised people in the prepared video as your characters."""

from __future__ import annotations

import math
from pathlib import Path

from character_swap.costs import Estimate, cost_line
from character_swap.media.upload import UploadTooLarge, fit_for_upload
from character_swap.modes import GENERATOR_LIMITS
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators.base import (
    CharacterRef,
    GenerationRequest,
    OnCreated,
    Stage,
)
from character_swap.providers.piapi import MAX_UPLOAD_BYTES, PiAPIRejected

TASK_TYPE = "seedance-2.5-less-restriction"
# "edit" changes the people in @video1 and keeps its length and framing. PiAPI fails a task
# after submission when its own reading of the request differs, so the live test checks it.
TASK_KIND = "edit"
DRAFT = "480p"
PROMOTE = "1080p"  # upgrades the exact draft
FINALS = (PROMOTE, "720p")  # 720p is a fresh run with the same prompt and inputs
PRICE = {"480p": "seedance-480", "720p": "seedance-720", "1080p": "seedance-1080"}
UPLOAD_COPY = "upload-copy.mp4"
# PiAPI refuses a promote with one of these when the draft can't be upgraded (e.g. too old).
PROMOTE_REFUSALS = (400, 404, 409, 422)
PROMOTE_HINT = "Drafts can be upgraded for about 7 days; use --resolution 720p for a fresh run."


class SeedanceGenerator:
    name = "seedance"
    provider = "piapi"
    model = TASK_TYPE
    min_seconds, max_seconds = GENERATOR_LIMITS["seedance"]
    supports_real_faces = True  # through saved faces (PiAPI's face library)
    supports_secondary = True

    def __init__(self, piapi) -> None:
        self._piapi = piapi

    @staticmethod
    def resolution(request: GenerationRequest, stage: Stage) -> str:
        if stage == "draft":
            return DRAFT
        chosen = request.final_resolution or PROMOTE
        if chosen not in FINALS:
            raise ProviderError(
                f"Seedance finals are {' or '.join(FINALS)}, not {chosen}. Nothing was charged."
            )
        return chosen

    def estimate(self, request: GenerationRequest, stage: Stage) -> Estimate:
        key = PRICE[self.resolution(request, stage)]
        seconds = math.ceil(request.seconds)  # PiAPI bills whole seconds: only ever over-quote
        return Estimate(lines=(cost_line(key, seconds), cost_line(f"{key}-input", seconds)))

    @staticmethod
    def build_input(
        request: GenerationRequest,
        *,
        video_url: str,
        image_urls: list[str],
        resolution: str,
        draft: bool,
    ) -> dict:
        task = {
            "prompt": request.prompt,
            "mode": "omni_reference",
            "omni_reference_task_type": TASK_KIND,
            "video_urls": [video_url],
            "image_urls": list(image_urls),  # @image1, @image2 in this order
            "aspect_ratio": "adaptive",  # an edit keeps the clip's own shape...
            "duration": -1,  # ...and its own (whole-second) length
            "resolution": resolution,
            "auto_upload_assets": False,  # PiAPI's documented defaults disagree: always say it
        }
        if draft:
            task["draft"] = True
        return {"model": "seedance", "task_type": TASK_TYPE, "input": task}

    @staticmethod
    def promote_body(draft_task_id: str) -> dict:
        if not draft_task_id:
            raise ProviderError(
                "The draft's PiAPI task id is missing, so it can't be upgraded to 1080p. Make a "
                "new draft, or use --resolution 720p. Nothing was charged."
            )
        return {
            "model": "seedance",
            "task_type": TASK_TYPE,
            "input": {"draft_task_id": draft_task_id, "resolution": PROMOTE},
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
        if record.exists():  # resume: run() reads the saved task and never posts again
            body: dict = {}
        elif stage == "final" and resolution == PROMOTE:
            body = self.promote_body(request.draft_task_id)
        else:
            if not request.prompt.strip():
                raise ProviderError(
                    "Seedance needs a prompt; see charswap prompt <job>. Nothing was charged."
                )
            self._check_faces(request)
            body = self.build_input(
                request,
                video_url=self._upload_video(request, record.parent),
                image_urls=self._image_urls(request),
                resolution=resolution,
                draft=stage == "draft",
            )
        try:
            url = self._piapi.run(body=body, record=record, on_created=on_created)
        except PiAPIRejected as exc:
            promote = bool(body) and "draft_task_id" in body.get("input", {})
            if promote and exc.status_code in PROMOTE_REFUSALS:
                raise PiAPIRejected(f"{exc} {PROMOTE_HINT}", exc.status_code) from exc
            raise
        try:
            return self._piapi.download(url, dest)
        except OutputExpired as exc:
            rebuy = self.estimate(request, stage).total
            raise ProviderError(
                f"The Seedance {stage} finished but its download link expired (PiAPI keeps "
                f"results 24 hours; {exc}). Delete {record} and run the same command again to "
                f"make a new one (costs about ${rebuy:.2f})."
            ) from exc

    @staticmethod
    def _refs(request: GenerationRequest) -> list[CharacterRef]:
        return [ref for ref in (request.primary, request.secondary) if ref is not None]

    def _check_faces(self, request: GenerationRequest) -> None:
        """Free: every saved face must be Active (this also keeps PiAPI from purging it)."""
        for ref in self._refs(request):
            if not ref.face_asset_id:
                continue
            asset = self._piapi.get_asset(ref.face_asset_id)
            status = (asset or {}).get("status") or "removed"
            if str(status).lower() != "active":
                name = ref.name or ref.face_asset_id
                raise ProviderError(
                    f"Your saved face {name!r} isn't usable at PiAPI right now ({status}). Run "
                    f"charswap face check {name}, or charswap face renew {name} if PiAPI removed "
                    "it. Nothing was charged."
                )

    def _image_urls(self, request: GenerationRequest) -> list[str]:
        return [
            f"asset://{ref.face_asset_id}"
            if ref.face_asset_id
            else self._piapi.upload_temp(ref.image_path)
            for ref in self._refs(request)
        ]

    def _upload_video(self, request: GenerationRequest, folder: Path) -> str:
        try:
            video = fit_for_upload(request.video, folder / UPLOAD_COPY, MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            raise ProviderError(f"{exc} Nothing was charged.") from exc
        try:
            return self._piapi.upload_temp(video)
        finally:
            if video != request.video:  # the compressed copy is private media: don't keep it
                video.unlink(missing_ok=True)
