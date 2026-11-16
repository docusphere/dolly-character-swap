"""Cost estimates shown before paid steps, and a spend log written after them."""

from __future__ import annotations

import csv
import os
import threading
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from character_swap.jobs import Job
from character_swap.modes import MODES

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
    # Wan is billed per second of output video. Not shown on the model page on 2026-11-03;
    # these are the 2026-10-22 figures. Confirm against replicate.com billing after a run.
    "wan-480": Price(
        "Wan draft (480p)",
        0.02,
        "second",
        "2026-10-22",
        "replicate.com/wan-video/wan-2.2-animate-replace (per output second)",
    ),
    "wan-720": Price(
        "Wan final (720p)",
        0.05,
        "second",
        "2026-10-22",
        "replicate.com/wan-video/wan-2.2-animate-replace (per output second)",
    ),
    # PiAPI Seedance 2.5 less-restriction, per second (piapi.ai/pricing and the 2.5 docs,
    # 2026-11-08). The input video is billed at half the output rate; a 1080p promote
    # bills the draft's input video again at half the 1080p rate.
    **{
        key: Price(item, usd, "second", "2026-11-08", "piapi.ai/docs/seedance-api/seedance-25")
        for key, item, usd in (
            ("seedance-480", "Seedance draft (480p)", 0.165),
            ("seedance-480-input", "Seedance input video (480p)", 0.0825),
            ("seedance-720", "Seedance final (720p)", 0.385),
            ("seedance-720-input", "Seedance input video (720p)", 0.1925),
            ("seedance-1080", "Seedance final (1080p)", 0.88),
            ("seedance-1080-input", "Seedance input video (1080p)", 0.44),
        )
    },
    # WaveSpeed Seedance 2.5 video-edit, per second (wavespeed.ai model page, 2026-11-12).
    # Billed on the input video's seconds (clamped to 4-30) plus the output's seconds.
    **{
        key: Price(
            item,
            usd,
            "second",
            "2026-11-12",
            "wavespeed.ai/models/bytedance/seedance-2.5/video-edit",
        )
        for key, item, usd in (
            ("wavespeed-480", "Seedance draft (480p)", 0.11),
            ("wavespeed-480-input", "Seedance input video (480p)", 0.11),
            ("wavespeed-720", "Seedance final (720p)", 0.22),
            ("wavespeed-720-input", "Seedance input video (720p)", 0.22),
            ("wavespeed-1080", "Seedance final (1080p)", 0.55),
            ("wavespeed-1080-input", "Seedance input video (1080p)", 0.55),
        )
    },
}

STAGE_PRICE = {
    "separate": "demucs",
    "transcribe": "whisper",
    "describe": "describe",
    "depth": "depth",
    "masks": "sam3",
}

PREP_STEPS: dict[str, list[str]] = {
    name: [STAGE_PRICE[stage] for stage in mode.paid_stages] for name, mode in MODES.items()
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


def usd_text(usd: float) -> str:
    """'$0.88'. Amounts under a cent keep three decimals ('$0.002'), so they never read as free."""
    return f"${usd:.2f}" if usd >= 0.01 or usd == 0 else f"${usd:.3f}"


def cost_line(key: str, seconds: float) -> Line:
    price = PRICES[key]
    usd = price.usd * seconds if price.unit == "second" else price.usd
    return Line(price.item, round(usd, 4))


def estimate_prepare(mode: str, seconds: float, skip: tuple[str, ...] = ()) -> Estimate:
    """The mode's paid prep steps, leaving out the stages in `skip` (e.g. "describe")."""
    stages = [stage for stage in MODES[mode].paid_stages if stage not in skip]
    return Estimate(lines=tuple(cost_line(STAGE_PRICE[stage], seconds) for stage in stages))


def estimate_stages(stage_names: list[str], seconds: float) -> Estimate:
    return Estimate(lines=tuple(cost_line(STAGE_PRICE[name], seconds) for name in stage_names))


def estimate_repair() -> Estimate:
    return Estimate(lines=(cost_line("sam3", 0),))


# New columns go at the end: old logs keep their 5-column header and still add up.
SPEND_LOG_COLUMNS = (
    "at",
    "job",
    "step",
    "usd_estimate",
    "usd_micro",
    "provider",
    "model",
    "task_id",
    "units",
)


_OLD_SPEND_LOG_COLUMNS = SPEND_LOG_COLUMNS[:5]


def _upgrade_old_header(log: Path) -> None:
    """Rewrite a 5-column log once with the full header, padding old rows. Hold _LOG_LOCK."""
    with log.open(encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file))
    if not rows or tuple(rows[0]) != _OLD_SPEND_LOG_COLUMNS:
        return
    width = len(SPEND_LOG_COLUMNS)
    body = [row + [""] * (width - len(row)) for row in rows[1:]]
    part = log.with_suffix(log.suffix + ".part")
    with part.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(SPEND_LOG_COLUMNS)
        writer.writerows(body)
    os.replace(part, log)


def record_spend(
    job: Job,
    step: str,
    estimate: Estimate,
    runs_dir: Path,
    *,
    provider: str = "",
    model: str = "",
    task_id: str = "",
    units: str = "",
) -> None:
    at = datetime.now(UTC).isoformat(timespec="seconds")
    entry: dict[str, object] = {"step": step, "usd": estimate.total, "at": at}
    details = {"provider": provider, "model": model, "task_id": task_id, "units": units}
    entry.update({key: value for key, value in details.items() if value})
    job.append_cost(entry)
    log = Path(runs_dir) / "spend-log.csv"
    with _LOG_LOCK:
        new = not log.exists() or log.stat().st_size == 0
        if not new:
            _upgrade_old_header(log)
        with log.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if new:
                writer.writerow(SPEND_LOG_COLUMNS)
            writer.writerow(
                [
                    at,
                    job.id,
                    step,
                    f"{estimate.total:.4f}",
                    round(estimate.total * 1_000_000),
                    provider,
                    model,
                    task_id,
                    units,
                ]
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
