import csv

import pytest

from character_swap.costs import PRICES, Estimate, estimate_prepare, record_spend, total_spend
from character_swap.jobs import Job


def test_depth_prep_includes_every_paid_step():
    estimate = estimate_prepare("depth", seconds=10)
    items = [line.item for line in estimate.lines]
    assert items == [
        "Demucs vocal separation",
        "Whisper transcript",
        "Scene description",
        "Depth video",
        "SAM 3 masks",
    ]
    assert estimate.total == pytest.approx(sum(line.usd for line in estimate.lines))


def test_face_mesh_skips_depth_and_masks():
    items = [line.item for line in estimate_prepare("face_mesh", seconds=10).lines]
    assert "Depth video" not in items and "SAM 3 masks" not in items


def test_repair_is_one_sam_run():
    from character_swap.costs import estimate_repair

    assert [line.item for line in estimate_repair().lines] == ["SAM 3 masks"]


def test_direct_prep_is_free():
    assert estimate_prepare("direct", seconds=10).total == 0


def test_describe_text_lists_lines_and_disclaimer():
    text = estimate_prepare("depth", seconds=5).describe()
    assert "Depth video" in text and "estimate" in text.lower()


def test_every_price_has_a_checked_date():
    assert all(price.checked_on for price in PRICES.values())


def test_record_spend_writes_job_and_global_log(runs_dir, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(job, "prepare", estimate_prepare("depth", 10), runs_dir)
    record_spend(job, "prepare", Estimate(lines=()), runs_dir)
    rows = list(csv.DictReader((runs_dir / "spend-log.csv").open()))
    assert len(rows) == 2 and rows[0]["job"] == job.id
    assert total_spend(runs_dir) == pytest.approx(estimate_prepare("depth", 10).total)
    assert len(job.state["costs"]) == 2


def test_total_spend_without_log_is_zero(runs_dir):
    assert total_spend(runs_dir) == 0
