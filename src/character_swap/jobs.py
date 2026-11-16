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
SEEDANCE_PROVIDERS: tuple[str, ...] = ("wavespeed", "piapi")  # the first is the default
MAX_SETTING = 300  # characters of a setting you write (--setting); the prompt keeps 300
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(job_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(job_id, threading.Lock())


class JobError(CharSwapError, ValueError):
    """The job request or job folder is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_setting(text: str) -> str:
    """A setting you wrote (--setting), on one line: 1 to MAX_SETTING characters."""
    cleaned = " ".join(text.split())
    if not 1 <= len(cleaned) <= MAX_SETTING:
        raise JobError(
            f"Describe the setting in 1 to {MAX_SETTING} characters. Nothing was charged."
        )
    return cleaned


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
        character2: Path | None = None,
        faces: dict[str, str] | None = None,  # slot ("primary"/"secondary") -> saved face name
        language: str = "auto",
        provider: str = "",  # Seedance: "wavespeed" | "piapi"; "" = decided later (old jobs)
        scene: Path | None = None,  # a scene swap: the whole scene; `source` is its cut part
        segment: dict[str, Any] | None = None,  # media.scene.Plan.segment + "frame_map"
        setting: str = "",  # a setting you wrote: skips the paid description
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
        if generator == "wan" and character2 is not None:
            raise JobError("Wan takes one character; a second one only works with Seedance.")
        if character2 is not None and not Path(character2).is_file():
            raise JobError(f"Second character image not found: {character2}")
        if language not in WHISPER_LANGUAGES:
            raise JobError(
                f"{language!r} is not a Whisper language code (for example en, es, or auto)."
            )
        if provider and generator != "seedance":
            raise JobError("--provider is only for Seedance; Wan always runs on Replicate.")
        if provider and provider not in SEEDANCE_PROVIDERS:
            raise JobError(
                f"Unknown provider {provider!r}. Use one of: {', '.join(SEEDANCE_PROVIDERS)}."
            )
        if (scene is None) != (segment is None):
            raise JobError("A scene swap needs both the scene and its segment.")
        if scene is not None and not Path(scene).is_file():
            raise JobError(f"Video not found: {scene}")
        if setting:
            setting = clean_setting(setting)

        job_id = uuid.uuid4().hex
        root = Path(runs_dir) / job_id
        root.mkdir(parents=True)
        try:
            shutil.copy2(source, root / "upload.mp4")
            if scene is not None:
                shutil.copy2(scene, root / "full-scene.mp4")
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
            if provider:
                state["provider"] = provider
            if segment is not None:
                state["segment"] = segment  # "scene" is already the setting's text
            if setting:
                state["setting"] = setting
            for slot, image in (("primary", character), ("secondary", character2)):
                if image is None:
                    continue
                name = f"character-{slot}{Path(image).suffix.lower()}"
                shutil.copy2(image, root / name)
                state[f"character_{slot}"] = name
            for slot, face_name in (faces or {}).items():
                state[f"face_{slot}"] = face_name
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
