"""Cost estimates shown before paid steps, and a spend log written after them."""

from __future__ import annotations

import csv
import threading
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from character_swap.jobs import Job

_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class Price:
    item: str
    usd: float
    unit: str  # "run" or "second"
    checked_on: str
    source: str


# Replicate figures are the "costs approximately $X to run" values on each model page.
PRICES: dict[str, Price] = {
    "demucs": Price(
        "Demucs vocal separation", 0.017, "run", "2026-10-22", "replicate.com/cjwbw/demucs"
    ),
    "whisper": Price(
        "Whisper transcript", 0.00075, "run", "2026-10-22", "replicate.com/openai/whisper"
    ),
    "describe": Price(
        "Scene description",
        0.001,
        "run",
        "2026-10-22",
        "replicate.com/openai/gpt-4o-mini (per token, ~1 image)",
    ),
    "depth": Price(
        "Depth video", 0.04, "run", "2026-10-22", "replicate.com/lucataco/depth-anything-video"
    ),
    "sam3": Price("SAM 3 masks", 0.002, "run", "2026-10-22", "replicate.com/lucataco/sam3-video"),
}

PREP_STEPS = {
    "depth": ["demucs", "whisper", "describe", "depth", "sam3"],
    "face_mesh": ["demucs", "whisper", "describe"],
    "depth_mesh": ["demucs", "whisper", "describe", "depth", "sam3"],
    "direct": [],
}


@dataclass(frozen=True)
class Line:
    item: str
    usd: float


@dataclass(frozen=True)
class Estimate:
    lines: tuple[Line, ...]

    @property
    def total(self) -> float:
        return sum(line.usd for line in self.lines)

    def describe(self) -> str:
        rows = [f"  {line.item:<28} ~${line.usd:.4f}" for line in self.lines]
        rows.append(f"  {'Total':<28} ~${self.total:.4f}")
        rows.append("  (estimate; check your provider billing page for exact charges)")
        return "\n".join(rows)


def _cost(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
    return Line(price.item, round(usd, 4))


def estimate_prepare(mode: str, seconds: float) -> Estimate:
    return Estimate(lines=tuple(_cost(key, seconds) for key in PREP_STEPS[mode]))


def estimate_repair() -> Estimate:
    return Estimate(lines=(_cost("sam3", 0),))


def record_spend(job: Job, step: str, estimate: Estimate, runs_dir: Path) -> None:
    at = datetime.now(UTC).isoformat(timespec="seconds")
    job.append_cost({"step": step, "usd": estimate.total, "at": at})
    log = Path(runs_dir) / "spend-log.csv"
    with _LOG_LOCK:
        new = not log.exists() or log.stat().st_size == 0
        with log.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if new:
                writer.writerow(["at", "job", "step", "usd_estimate", "usd_micro"])
            writer.writerow(
                [at, job.id, step, f"{estimate.total:.4f}", round(estimate.total * 1_000_000)]
            )


def total_spend(runs_dir: Path) -> float:
    log = Path(runs_dir) / "spend-log.csv"
    if not log.exists():
        return 0.0
    micro = 0
    with log.open(encoding="utf-8", newline="") as file:
        for number, row in enumerate(csv.DictReader(file), start=2):
            try:
                micro += int(row["usd_micro"])
            except (KeyError, TypeError, ValueError):
                warnings.warn(f"Skipping unreadable spend-log.csv row {number}.", stacklevel=2)
    return micro / 1_000_000
