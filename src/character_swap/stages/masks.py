"""Segment the subject with SAM 3 and save one raw PNG mask per frame."""

from __future__ import annotations

import zipfile
from pathlib import Path

from character_swap.stages import StageError, bad_output, download_or_fail

SAM_VERSION = "408f82fc20c300aac5d61d2e34ddb34cd0181e810e9f609d250aed14d5f81269"


def segment(video: Path, out_dir: Path, subject: str, replicate) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = replicate.upload(video)
    record = out_dir / "sam-prediction.json"
    output = replicate.run(
        version=SAM_VERSION,
        input={"video": url, "prompt": subject, "mask_only": True, "return_zip": True},
        record=record,
    )
    if not isinstance(output, str):
        raise bad_output(record, "SAM 3", output)
    archive = download_or_fail(
        replicate, output, out_dir / "masks.zip", record=record, what="SAM 3", cost="~$0.002"
    )
    raw = out_dir / "raw-masks"
    raw.mkdir(exist_ok=True)
    written = 0
    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            for name in names:
                base = Path(name).name
                if base.startswith("mask_") and base.endswith(".png"):
                    (raw / base).write_bytes(zf.read(name))
                    written += 1
    except zipfile.BadZipFile as exc:
        raise StageError(
            f"SAM 3 result is not a valid zip ({exc}). "
            f"Delete {record} and resume to regenerate it (costs ~$0.002)."
        ) from exc
    if not written:
        raise StageError(
            f"SAM 3 returned no mask_*.png files (entries: {names[:5]}). "
            f"Delete {record} and resume to regenerate it (costs ~$0.002)."
        )
    return raw
