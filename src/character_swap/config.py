"""Settings from the environment and the project's .env file. Environment wins."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from character_swap.errors import CharSwapError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WHERE_TO_GET = {
    "REPLICATE_API_TOKEN": "https://replicate.com/account/api-tokens",
    "WAVESPEED_API_KEY": "https://wavespeed.ai → Dashboard → API Keys",
    "PIAPI_API_KEY": "https://piapi.ai → Workspace → API Keys",
    "HF_TOKEN": "https://huggingface.co/settings/tokens",
}
KEY_NOTES = {  # one more sentence after where to get the key
    "WAVESPEED_API_KEY": "Already use PiAPI? Prepare with --provider piapi instead.",
    "PIAPI_API_KEY": "Saved faces need the Creator plan.",
}


class MissingSettingError(CharSwapError, RuntimeError):
    """A required key is not configured."""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    values: Mapping[str, str] = field(repr=False)
    runs_dir: Path
    root: Path

    @classmethod
    def load(cls, root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None) -> Settings:
        merged = read_env_file(root / ".env")
        merged.update({k: v for k, v in (os.environ if environ is None else environ).items() if v})
        runs_dir = Path(merged.get("CHARSWAP_RUNS_DIR") or root / "runs")
        return cls(values=merged, runs_dir=runs_dir, root=root)

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name) or default

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            where = WHERE_TO_GET.get(name, "your provider dashboard")
            note = f" {KEY_NOTES[name]}" if name in KEY_NOTES else ""
            raise MissingSettingError(
                f"{name} is not set. Add it to .env; get it at {where}.{note}"
            )
        return value
