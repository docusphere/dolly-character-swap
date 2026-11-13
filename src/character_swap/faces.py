"""Saved faces: a photo PiAPI checks once and keeps in your private library, reused by name."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from character_swap.errors import CharSwapError

FOLDER = "faces"  # at the project root; git-ignored
CONSENT = (
    "Only add a face you have the right to use: your own, or someone who agreed to it. "
    "PiAPI checks the photo and keeps it in your private face library."
)
# Asked by `charswap prepare` when a plain photo is sent (WaveSpeed); provider-neutral.
PHOTO_CONSENT = (
    "You confirm you have the right to use this person's face: your own, or someone who "
    "agreed to it. The photo is sent with your clip to the video provider. If it's a "
    "made-up character, answer yes."
)
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
MIN_SIDE = 300  # PiAPI: 300-6000 px per side, aspect 0.4-2.5, under 30 MB
MIN_RATIO, MAX_RATIO = 0.4, 2.5
FIT_SIDE = 3000  # bigger photos are scaled to this longest side (keeps uploads small)
MAX_BYTES = 7_000_000  # PiAPI's temporary upload (providers/piapi.py)
POLLS, POLL_SECONDS = 60, 5.0  # wait up to ~5 minutes for PiAPI's face check
ACTIVE, PROCESSING, FAILED, GONE = "Active", "Processing", "Failed", "Gone"
_STATUS = {"active": ACTIVE, "processing": PROCESSING, "failed": FAILED}


class FaceError(CharSwapError, ValueError):
    """A face cannot be saved or used as it is; the message says what to do."""


class AssetsLike(Protocol):
    def upload_temp(self, path: Path) -> str: ...

    def create_asset(self, url: str, name: str) -> dict: ...

    def get_asset(self, asset_id: str) -> dict | None: ...


@dataclass(frozen=True)
class Face:
    name: str
    image: str  # file name inside faces/
    asset_id: str
    status: str  # Active | Processing | Failed | Gone
    created_at: str
    error: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_photo(photo: Path, dest: Path) -> Path:
    """Check a photo against the Seedance limits (PiAPI's) and save it as an upright RGB JPEG."""
    photo = Path(photo)
    try:
        with Image.open(photo) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except FileNotFoundError:
        raise FaceError(f"Photo not found: {photo}") from None
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise FaceError(
            f"{photo.name} isn't a photo this tool can read. Export it as JPEG or PNG "
            "(iPhone photos: select it in Photos, then File > Export)."
        ) from error
    width, height = image.size
    if not MIN_RATIO <= width / height <= MAX_RATIO:
        raise FaceError(
            f"{photo.name} is {width}x{height} px: too wide or too narrow for Seedance. Crop it "
            "closer to a portrait or square shape."
        )
    if min(width, height) < MIN_SIDE:
        raise FaceError(
            f"{photo.name} is {width}x{height} px; Seedance needs at least {MIN_SIDE} px on each "
            "side. Use the original photo, not a screenshot."
        )
    if max(width, height) > FIT_SIDE:
        image.thumbnail((FIT_SIDE, FIT_SIDE), Image.Resampling.LANCZOS)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, (255, 255, 255))
        rgb.paste(rgba, mask=rgba.getchannel("A"))
    else:
        rgb = image.convert("RGB")
    for quality in (92, 80):
        rgb.save(dest, "JPEG", quality=quality)
        if Path(dest).stat().st_size <= MAX_BYTES:
            return Path(dest)
    Path(dest).unlink(missing_ok=True)
    raise FaceError(f"{photo.name} is too large to upload even as a JPEG; use a smaller photo.")


class FaceLibrary:
    def __init__(
        self,
        root: Path,
        *,
        sleep: Callable[[float], None] = time.sleep,
        polls: int = POLLS,
        poll_seconds: float = POLL_SECONDS,
    ):
        self.folder = Path(root) / FOLDER
        self.index = self.folder / "faces.json"
        self._sleep = sleep
        self._polls = polls
        self._poll_seconds = poll_seconds

    # ---- reading (local, free) -------------------------------------------------
    def all(self) -> list[Face]:
        if not self.index.exists():
            return []
        try:
            rows = json.loads(self.index.read_text(encoding="utf-8"))
            known = {f.name for f in fields(Face)}
            return [Face(**{k: v for k, v in row.items() if k in known}) for row in rows]
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise FaceError(
                f"{self.index} is damaged. Fix or delete it (your faces stay in your PiAPI "
                "library; add them again with charswap face add)."
            ) from error

    def get(self, name: str) -> Face:
        for face in self.all():
            if face.name == name:
                return face
        raise FaceError(f"No saved face {name!r}. Add it first: charswap face add {name} photo.jpg")

    def image_path(self, face: Face) -> Path:
        return self.folder / face.image

    # ---- PiAPI (free calls) --------------------------------------------------------
    def validate_new_name(self, name: str) -> None:
        if not NAME.fullmatch(name):
            raise FaceError(
                "Face names are 1-40 lowercase letters, digits, - or _ (for example: frank)."
            )
        if any(face.name == name for face in self.all()):
            raise FaceError(
                f"A face called {name!r} is already saved. Pick another name, or run "
                f"charswap face renew {name} if PiAPI removed it."
            )

    def add(self, name: str, photo: Path, piapi: AssetsLike) -> Face:
        self.validate_new_name(name)
        self.folder.mkdir(parents=True, exist_ok=True)
        image = check_photo(Path(photo), self.folder / f"{name}.jpg")
        return self._upload(name, image.name, piapi)

    def renew(self, name: str, piapi: AssetsLike) -> Face:
        """Upload the same saved photo again (after PiAPI purged an unused face)."""
        face = self.get(name)
        if not self.image_path(face).exists():
            raise FaceError(
                f"The saved photo {self.image_path(face)} is missing; add the face again "
                "under a new name."
            )
        face = self._refresh(face, piapi)
        if face.status == FAILED:
            raise FaceError(
                f"PiAPI rejected face {name!r}: {face.error or 'no reason given'}. Add a "
                "clearer photo under a new name: charswap face add NAME photo.jpg"
            )
        if face.status != GONE:
            return face  # still in your library: nothing to upload
        return self._upload(name, face.image, piapi)

    def check(self, name: str, piapi: AssetsLike) -> Face:
        """Ask PiAPI for the face's status (this also keeps it from being purged)."""
        return self._refresh(self.get(name), piapi)

    # ---- internals ----------------------------------------------------------------
    def _upload(self, name: str, image: str, piapi: AssetsLike) -> Face:
        url = piapi.upload_temp(self.folder / image)
        asset = piapi.create_asset(url, name)
        face = Face(
            name=name,
            image=image,
            asset_id=str(asset["asset_id"]),
            status=_status(asset.get("status")),
            created_at=_now(),
        )
        self._put(face)  # saved before waiting: the id is never lost
        for attempt in range(self._polls):
            if face.status in (ACTIVE, FAILED):
                break
            if attempt:  # the first check is immediate; later ones wait in between
                self._sleep(self._poll_seconds)
            face = self._refresh(face, piapi)
            if face.status == GONE:  # a brief 404 right after creating: still checking
                face = replace(face, status=PROCESSING)
                self._put(face)
        return face

    def _refresh(self, face: Face, piapi: AssetsLike) -> Face:
        record = piapi.get_asset(face.asset_id)
        if record is None:
            updated = replace(face, status=GONE, error="")
        else:
            error = record.get("error")
            message = error.get("message", "") if isinstance(error, dict) else str(error or "")
            updated = replace(face, status=_status(record.get("status")), error=message)
        self._put(updated)
        return updated

    def _put(self, face: Face) -> None:
        faces = [other for other in self.all() if other.name != face.name] + [face]
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump([asdict(item) for item in faces], file, indent=2)
            os.replace(tmp, self.index)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _status(raw: object) -> str:
    return _STATUS.get(str(raw or "").strip().lower(), PROCESSING)
