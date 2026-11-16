import csv

import pytest

from character_swap.costs import (
    PRICES,
    Estimate,
    cost_line,
    estimate_prepare,
    record_spend,
    total_spend,
)
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


def test_concurrent_spend_has_one_header_and_all_rows(runs_dir, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    estimate = estimate_prepare("face_mesh", 5)
    with ThreadPoolExecutor(20) as pool:
        list(pool.map(lambda _: record_spend(job, "prepare", estimate, runs_dir), range(20)))
    lines = (runs_dir / "spend-log.csv").read_text().splitlines()
    assert sum(line.startswith("at,") for line in lines) == 1
    assert len(lines) == 21
    assert len(job.state["costs"]) == 20


def test_corrupt_row_is_skipped_with_warning(runs_dir):
    (runs_dir / "spend-log.csv").write_text(
        "at,job,step,usd_estimate,usd_micro\nt,j,s,0.0170,17000\nt,j,s,0.0\n"
    )
    with pytest.warns(UserWarning, match="spend-log"):
        assert total_spend(runs_dir) == pytest.approx(0.017)


def test_micro_totals_are_exact(runs_dir, tmp_path):
    from character_swap.costs import Line

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    for _ in range(3):
        record_spend(job, "x", Estimate(lines=(Line("a", 0.1),)), runs_dir)
    assert total_spend(runs_dir) == 0.3


def test_spend_entry_names_provider_model_and_task(runs_dir, tmp_path):
    from character_swap.costs import Line

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(
        job,
        "draft",
        Estimate(lines=(Line("Wan draft (480p)", 0.1),)),
        runs_dir,
        provider="replicate",
        model="wan-video/wan-2.2-animate-replace",
        task_id="p9",
        units="8.0s",
    )
    row = next(csv.DictReader((runs_dir / "spend-log.csv").open()))
    assert (row["provider"], row["model"], row["task_id"], row["units"]) == (
        "replicate",
        "wan-video/wan-2.2-animate-replace",
        "p9",
        "8.0s",
    )
    entry = job.state["costs"][-1]
    assert entry["step"] == "draft" and entry["task_id"] == "p9" and entry["usd"] == 0.1
    assert entry["units"] == "8.0s"


def test_old_five_column_spend_logs_keep_adding_up(runs_dir, tmp_path):
    from character_swap.costs import Line

    (runs_dir / "spend-log.csv").write_text(
        "at,job,step,usd_estimate,usd_micro\n2026-11-02T10:00:00+00:00,j,prepare,0.0600,60000\n"
    )
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(job, "draft", Estimate(lines=(Line("x", 0.1),)), runs_dir, task_id="p9")
    assert total_spend(runs_dir) == pytest.approx(0.16)


def test_old_five_column_header_is_upgraded_once(runs_dir, tmp_path):
    from character_swap.costs import SPEND_LOG_COLUMNS, Line

    (runs_dir / "spend-log.csv").write_text(
        "at,job,step,usd_estimate,usd_micro\n2026-11-02T10:00:00+00:00,j,prepare,0.0600,60000\n"
    )
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    job = Job.create(runs_dir, video, generator="seedance", mode="depth")
    record_spend(job, "draft", Estimate(lines=(Line("x", 0.1),)), runs_dir, task_id="p9")
    record_spend(job, "final", Estimate(lines=(Line("y", 0.2),)), runs_dir, task_id="p10")
    rows = list(csv.reader((runs_dir / "spend-log.csv").open()))
    assert tuple(rows[0]) == SPEND_LOG_COLUMNS
    assert all(len(row) == len(SPEND_LOG_COLUMNS) for row in rows)
    assert rows[1][:5] == ["2026-11-02T10:00:00+00:00", "j", "prepare", "0.0600", "60000"]
    assert rows[1][5:] == ["", "", "", ""]
    assert [row[7] for row in rows[2:]] == ["p9", "p10"]
    assert total_spend(runs_dir) == pytest.approx(0.36)


def test_wan_prices_are_per_output_second():
    assert PRICES["wan-480"].unit == PRICES["wan-720"].unit == "second"
    assert PRICES["wan-480"].usd < PRICES["wan-720"].usd


def test_seedance_prices_are_per_second_and_include_the_input_video():
    seconds = 12
    draft = Estimate(
        lines=(cost_line("seedance-480", seconds), cost_line("seedance-480-input", seconds))
    )
    final_1080 = Estimate(
        lines=(cost_line("seedance-1080", seconds), cost_line("seedance-1080-input", seconds))
    )
    final_720 = Estimate(
        lines=(cost_line("seedance-720", seconds), cost_line("seedance-720-input", seconds))
    )
    assert draft.total == pytest.approx(2.97)
    assert final_1080.total == pytest.approx(15.84)
    assert final_720.total == pytest.approx(6.93)
    for key in ("seedance-480", "seedance-720", "seedance-1080"):
        assert PRICES[key].unit == "second" and PRICES[key].checked_on == "2026-11-08"
        assert PRICES[f"{key}-input"].usd == pytest.approx(PRICES[key].usd / 2)


def test_wavespeed_prices_bill_input_and_output_seconds():
    rates = {"480": 0.11, "720": 0.22, "1080": 0.55}
    for resolution, usd in rates.items():
        for key in (f"wavespeed-{resolution}", f"wavespeed-{resolution}-input"):
            assert PRICES[key].unit == "second" and PRICES[key].checked_on == "2026-11-12"
            assert PRICES[key].usd == pytest.approx(usd)
            assert PRICES[key].source == "wavespeed.ai/models/bytedance/seedance-2.5/video-edit"
    seconds = 4
    draft = Estimate(
        lines=(cost_line("wavespeed-480", seconds), cost_line("wavespeed-480-input", seconds))
    )
    assert draft.total == pytest.approx(0.88)


def test_prepare_can_leave_out_the_description():
    full = estimate_prepare("depth_mesh", 30)
    mine = estimate_prepare("depth_mesh", 30, skip=("describe",))
    assert "Scene description" not in [line.item for line in mine.lines]
    assert mine.total == pytest.approx(full.total - 0.001)
    assert estimate_prepare("depth_mesh", 30, skip=()) == full
