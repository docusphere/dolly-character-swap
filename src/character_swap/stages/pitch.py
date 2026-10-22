"""Shift isolated vocals up with Rubber Band, keeping exact duration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

from character_swap.media.audio import read_audio, write_wav
from character_swap.stages import StageError

INSTALL_HINT = "macOS: brew install rubberband · Ubuntu/WSL: sudo apt install rubberband-cli"


def pitch_vocals(
    vocals: Path, dst: Path, *, semitones: int = 3, rubberband: str = "rubberband"
) -> Path:
    binary = shutil.which(rubberband)
    if not binary:
        raise StageError(f"Rubber Band is not installed. {INSTALL_HINT}")
    try:
        subprocess.run(
            [binary, "--fine", "--pitch", str(semitones), "--time", "1", str(vocals), str(dst)],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        raise StageError(f"Rubber Band failed: {(exc.stderr or '').strip()[-500:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise StageError("Rubber Band timed out after 600 seconds.") from exc
    length = read_audio(vocals).shape[1]
    shifted = read_audio(dst)
    shifted = np.pad(shifted, ((0, 0), (0, max(0, length - shifted.shape[1]))))[:, :length]
    write_wav(dst, np.clip(shifted, -1, 1))
    return dst
