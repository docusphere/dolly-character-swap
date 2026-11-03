"""Job folders: one folder per video holding every artifact plus state.json."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from character_swap.errors import CharSwapError
from character_swap.modes import MODES_BY_GENERATOR

_JOB_ID = re.compile(r"[0-9a-f]{32}")
# The codes openai/whisper accepts (its `language` enum, checked 2026-11-03 via
# GET /v1/models/openai/whisper/versions/8099696689d2...). It also takes names;
# we keep codes only.
# fmt: off
WHISPER_LANGUAGES: tuple[str, ...] = (
    "auto", "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs", "ca", "cs",
    "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha",
    "haw", "he", "hi", "hr", "ht", "hu", "hy", "id", "is", "it", "ja", "jw", "ka", "kk", "km",
    "kn", "ko", "la", "lb", "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms",
    "mt", "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru", "sa", "sd",
    "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw", "ta", "te", "tg", "th", "tk",
    "tl", "tr", "tt", "uk", "ur", "uz", "vi", "yi", "yo", "yue", "zh",
)
# fmt: on
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(job_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(job_id, threading.Lock())


class JobError(CharSwapError, ValueError):
    """The job request or job folder is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    id: str
    root: Path

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        source: Path,
        *,
        generator: str,
        mode: str,
        subject: str = "person",
        depth_provider: str = "replicate",
        character: Path | None = None,
        language: str = "auto",
    ) -> Job:
        if generator not in MODES_BY_GENERATOR:
            choices = ", ".join(MODES_BY_GENERATOR)
            raise JobError(f"Unknown generator {generator!r}. Use one of: {choices}.")
        if mode not in MODES_BY_GENERATOR[generator]:
            choices = ", ".join(MODES_BY_GENERATOR[generator])
            raise JobError(f"Mode {mode!r} does not work with {generator}. Choose: {choices}.")
        if generator == "wan" and character is None:
            raise JobError("Wan needs a character image when the job is created.")
        source = Path(source)
        if not source.is_file():
            raise JobError(f"Video not found: {source}")
        if character is not None and not Path(character).is_file():
            raise JobError(f"Character image not found: {character}")
        if language not in WHISPER_LANGUAGES:
            raise JobError(
                f"{language!r} is not a Whisper language code (for example en, es, or auto)."
            )

        job_id = uuid.uuid4().hex
        root = Path(runs_dir) / job_id
        root.mkdir(parents=True)
        try:
            shutil.copy2(source, root / "upload.mp4")
            state: dict[str, Any] = {
                "id": job_id,
                "generator": generator,
                "mode": mode,
                "subject": subject,
                "depth_provider": depth_provider,
                "language": language,
                "seed": secrets.randbelow(2**31),
                "created_at": _now(),
                "stages": {},
            }
            if character is not None:
                name = f"character-primary{Path(character).suffix.lower()}"
                shutil.copy2(character, root / name)
                state["character_primary"] = name
            job = cls(job_id, root)
            job._write(state)
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return job

    @classmethod
    def load(cls, runs_dir: Path, job_id: str) -> Job:
        if not _JOB_ID.fullmatch(job_id):
            raise JobError("Job IDs are 32 lowercase hex characters.")
        root = Path(runs_dir) / job_id
        if not (root / "state.json").is_file():
            raise JobError(f"No job {job_id} in {runs_dir}.")
        return cls(job_id, root)

    def path(self, name: str) -> Path:
        resolved = (self.root / name).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise JobError(f"{name!r} is outside the job folder.")
        return self.root / name

    def append_cost(self, entry: dict[str, Any]) -> None:
        with _lock_for(self.id):
            data = self.state
            data.setdefault("costs", []).append(entry)
            self._write(data)

    @property
    def state(self) -> dict[str, Any]:
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))

    @property
    def generator(self) -> str:
        return self.state["generator"]

    @property
    def mode(self) -> str:
        return self.state["mode"]

    def update(self, **changes: Any) -> dict[str, Any]:
        with _lock_for(self.id):
            data = self.state
            data.update(changes)
            self._write(data)
            return data

    def stage_done(self, name: str) -> bool:
        return self.state["stages"].get(name, {}).get("status") == "done"

    def mark_stage(self, name: str, status: str = "done", **info: Any) -> None:
        with _lock_for(self.id):
            data = self.state
            data["stages"][name] = {"status": status, "at": _now(), **info}
            self._write(data)

    def reset_stages(self, *names: str) -> None:
        with _lock_for(self.id):
            data = self.state
            for name in names:
                data["stages"].pop(name, None)
            self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        handle, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
            os.replace(temporary, self.root / "state.json")
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
