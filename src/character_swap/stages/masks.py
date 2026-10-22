"""Segment the subject with SAM 3 and save one raw PNG mask per frame."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

SAM_VERSION = "408f82fc20c300aac5d61d2e34ddb34cd0181e810e9f609d250aed14d5f81269"


def segment(video: Path, out_dir: Path, subject: str, replicate) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = replicate.upload(video)
    output = replicate.run(
        version=SAM_VERSION,
        input={"video": url, "prompt": subject, "mask_only": True, "return_zip": True},
        record=out_dir / "sam-prediction.json",
    )
    archive_path = replicate.download(output, out_dir / "masks.zip")
    raw = out_dir / "raw-masks"
    raw.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_path.read_bytes())) as archive:
        for name in archive.namelist():
            base = Path(name).name
            if base.startswith("mask_") and base.endswith(".png"):
                (raw / base).write_bytes(archive.read(name))
    return raw
