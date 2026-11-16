import json

import pytest

from character_swap.costs import estimate_prepare, estimate_stages
from character_swap.pipeline import PAID_STAGES
from character_swap.review import decide, fingerprint
from character_swap.web import view_model as vm
from viewer_jobs import RECORDS, finish_stage, make_job, write_record


# ---- tables ----------------------------------------------------------------------
@pytest.mark.parametrize(("mode", "count"), [("depth", 9), ("depth_mesh", 10), ("face_mesh", 7)])
def test_stage_counts_match_spec(mode, count):
    assert len(vm.STAGES_BY_MODE[mode]) == count
    assert vm.STAGES_BY_MODE["direct"] == ("normalize",)


@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_paid_stages_are_a_subset_of_the_mode(mode):
    assert set(PAID_STAGES[mode]) <= set(vm.STAGES_BY_MODE[mode])


def test_the_cost_table_names_every_paid_record():
    assert vm.PAID_RECORD == RECORDS  # the files the stages really write (tests/viewer_jobs.py)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["done", "failed"], "failed"),
        (["running", "failed"], "failed"),
        (["done", "running"], "running"),
        (["done", "done"], "done"),
        (["done", "pending"], "pending"),
        ([], "pending"),
    ],
)
def test_combine(statuses, expected):
    assert vm.combine(statuses) == expected


# ---- progress ------------------------------------------------------------------
def test_progress_of_fresh_job_is_active(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    info = vm.progress(job.state)
    assert info == {
        "stages": dict.fromkeys(vm.STAGES_BY_MODE["depth"], "pending"),
        "generation": {"draft": "pending", "final": "pending"},
        "done_count": 0,
        "total_count": 9,
        "active": True,
        "poll": "fast",
    }


def test_progress_keeps_generation_apart_from_preparation(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    info = vm.progress(job.state)
    assert info["stages"] == {"normalize": "done"}
    assert info["generation"] == {"draft": "pending", "final": "pending"}
    assert info["active"] is False and info["poll"] == "slow"  # waiting for the user
    job.mark_stage("draft", "running")
    info = vm.progress(job.state)
    assert info["active"] is True and info["poll"] == "fast"
    assert (info["done_count"], info["total_count"]) == (1, 1)
    job.mark_stage("draft")
    job.mark_stage("final")
    assert vm.progress(job.state)["poll"] == "off"


def test_failed_jobs_keep_slow_polling(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=2, failed="boom")
    info = vm.progress(job.state)
    assert info["active"] is False and info["poll"] == "slow"
    wan = make_job(runs_dir, media, "direct")
    wan.mark_stage("draft", "failed", error="boom")
    assert vm.progress(wan.state)["poll"] == "slow"


def test_progress_running_and_finished(runs_dir, media):
    running = make_job(runs_dir, media, "face_mesh", done=3, running=True)
    info = vm.progress(running.state)
    assert info["stages"]["transcribe"] == "running" and info["done_count"] == 3
    assert info["active"] is True
    finished = make_job(runs_dir, media, "face_mesh")
    assert vm.progress(finished.state)["active"] is False
    assert vm.progress(finished.state)["done_count"] == 7


def test_unknown_stage_status_counts_as_pending(runs_dir, media):
    job = make_job(runs_dir, media, "direct", done=0)
    job.mark_stage("normalize", "weird")
    assert vm.progress(job.state)["stages"] == {"normalize": "pending"}


# ---- which step a job is on ----------------------------------------------------------
def test_current_step_follows_the_job(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=4, provider="wavespeed")
    assert vm.current_step(job, job.state) == 2  # preparing
    for name in vm.STAGES_BY_MODE["depth_mesh"][4:]:
        finish_stage(job, media, name)
    assert vm.current_step(job, job.state) == 3  # prepared, not approved
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    assert vm.current_step(job, job.state) == 4  # approved, no draft
    finish_stage(job, media, "draft")
    assert vm.current_step(job, job.state) == 5
    job.mark_stage("draft", fingerprint="older-inputs")
    assert vm.current_step(job, job.state) == 4  # the draft is out of date


def test_current_step_never_hashes(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "depth")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    assert vm.current_step(job, job.state) == 4
    assert vm.saved_review(job)["approved"] is True


@pytest.mark.parametrize("content", ["{", "[]"])
def test_a_damaged_review_reads_as_not_approved(runs_dir, media, content):
    job = make_job(runs_dir, media, "depth")
    job.path("review.json").write_text(content)
    assert vm.saved_review(job) == {} and vm.current_step(job, job.state) == 3


@pytest.mark.parametrize(
    ("mode", "provider", "second", "expected"),
    [
        ("depth_mesh", "wavespeed", False, True),
        ("depth", "wavespeed", False, True),
        ("face_mesh", "wavespeed", False, False),
        ("depth", "piapi", False, False),
        ("depth", "wavespeed", True, False),
        ("direct", "", False, False),
    ],
)
def test_only_one_person_wavespeed_depth_jobs_get_buttons(
    runs_dir, media, mode, provider, second, expected
):
    job = make_job(runs_dir, media, mode, done=0, provider=provider)
    if second:
        job.update(character_secondary="character-secondary.png")
    state = job.state
    key = vm.generator_key(job, state)
    assert vm.is_app_job(state, key) is expected


# ---- links and files -------------------------------------------------------------
@pytest.mark.parametrize(
    "content",
    [
        None,  # missing record
        "{not json",
        json.dumps({"status": "creating", "created_at": "2026-11-01T10:00:00"}),
        json.dumps(["pred-1"]),
        json.dumps({"id": "../../evil"}),
        json.dumps({"id": 42}),
    ],
)
def test_no_link_without_a_confirmed_prediction(runs_dir, media, content):
    job = make_job(runs_dir, media, "depth")
    record = job.path("demucs-prediction.json")
    if content is None:
        record.unlink()
    else:
        record.write_text(content)
    assert vm.prediction_url(job, "demucs-prediction.json") is None


def test_file_url_quotes_names(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.path("my face.png").write_bytes(b"x")
    assert vm.file_if_present(job, "my face.png") == f"/jobs/{job.id}/files/my%20face.png"
    assert vm.file_url("j", "sam/a b.json") == "/jobs/j/files/sam/a%20b.json"


@pytest.mark.parametrize("name", ["../outside.png", None, 42, "", "missing.png"])
def test_file_if_present_refuses_odd_or_missing_names(runs_dir, media, name):
    job = make_job(runs_dir, media, "direct")
    assert vm.file_if_present(job, name) is None


def test_depth_mesh_composite_fallback(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=8)  # composite done, face_mesh not
    assert vm.composite_name(job, job.state) == "composite-silent.mp4"
    job.mark_stage("face_mesh", "running")
    assert vm.composite_name(job, job.state) == "depth-composite-silent.mp4"
    assert vm.composite_name(job, {**job.state, "mode": "depth"}) == "composite-silent.mp4"


# ---- costs ---------------------------------------------------------------------
def test_huggingface_depth_is_free(runs_dir, media):
    job = make_job(runs_dir, media, "depth", depth_provider="huggingface")
    rows = vm.cost_rows(job, job.state)
    depth_row = next(row for row in rows if row.step == "Depth video")
    assert depth_row == vm.CostRow("Depth video", "Hugging Face · free", 0.0, None)
    expected = round(estimate_prepare("depth", 30).total - 0.04, 4)
    assert vm.completed_cost(rows) == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_finished_job_costs_match_the_prepare_estimate(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    rows = vm.cost_rows(job, job.state)
    assert len(rows) == len(PAID_STAGES[mode])
    assert all(row.provider == "Replicate" for row in rows)
    assert all(row.link for row in rows)
    assert vm.completed_cost(rows) == pytest.approx(round(estimate_prepare(mode, 30).total, 4))


def test_costs_count_only_completed_steps(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="whisper down")
    rows = vm.cost_rows(job, job.state)
    assert [row.step for row in rows] == ["Separate vocals"]
    assert rows[0].link == "https://replicate.com/p/pred-separate"
    assert vm.completed_cost(rows) == pytest.approx(0.017)


def test_a_damaged_costs_field_or_entry_counts_as_zero(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.update(costs="oops")
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.0]
    job.update(costs=["garbage", {"step": "draft", "usd": "not a number", "task_id": "pred-draft"}])
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.0]


@pytest.mark.parametrize("value", ["nan", "inf", float("nan"), float("-inf")])
def test_non_finite_logged_costs_count_as_zero(runs_dir, media, value):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": value, "task_id": "pred-draft"})
    job.append_cost({"step": "draft", "usd": 0.5, "task_id": "pred-draft"})
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.5]


def test_wan_draft_row_links_to_replicate(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-draft"})
    rows = [(row.step, row.provider, row.usd, row.link) for row in vm.cost_rows(job, job.state)]
    assert rows == [("Draft (480p)", "Replicate", 0.08, "https://replicate.com/p/pred-draft")]


def test_remade_draft_row_counts_only_the_current_prediction(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-old"})
    job.append_cost({"step": "draft", "usd": 0.08, "at": "y", "task_id": "pred-draft"})
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.08]


def test_unmatched_current_prediction_never_counts_old_charges(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")  # current prediction: pred-draft
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-old"})
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.0]
    job.append_cost({"step": "draft", "usd": 0.05, "at": "y"})  # older log line without an id
    assert [row.usd for row in vm.cost_rows(job, job.state)] == [0.05]


def test_wavespeed_rows_name_wavespeed_and_the_final_resolution(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft"})
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint=fingerprint(job), resolution="1080p")
    rows = {r.step: r for r in vm.cost_rows(job, job.state)}
    assert rows["Draft (480p)"].provider == "WaveSpeed" and rows["Draft (480p)"].usd == 0.88
    assert rows["Final (1080p)"].link is None and rows["Separate vocals"].provider == "Replicate"


@pytest.mark.parametrize(("mode", "step"), [("depth", "Final (720p)"), ("direct", "Final (720p)")])
def test_final_row_without_a_saved_resolution_uses_the_generator_default(
    runs_dir, media, mode, step
):
    job = make_job(runs_dir, media, mode, provider="wavespeed" if mode == "depth" else "")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    rows = vm.cost_rows(job, job.state)
    assert [r.step for r in rows if r.step.startswith("Final")] == [step]


# ---- out-of-date work ------------------------------------------------------------------
def test_stale_stages(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    assert vm.stale_stages(job, job.state) == frozenset()
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    assert vm.stale_stages(job, job.state) == frozenset()
    job.mark_stage("final", fingerprint="made-from-older-inputs")
    assert vm.stale_stages(job, job.state) == {"final"}
    job.path("original.mp4").unlink()  # nothing saved can be proven current (as the CLI says)
    assert vm.stale_stages(job, job.state) == {"draft", "final"}


def test_stale_check_read_error_says_nothing(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")

    def broken(target):
        raise OSError("disk hiccup")

    monkeypatch.setattr(vm, "fingerprint", broken)
    assert vm.stale_stages(job, job.state) == frozenset()


# ---- damaged state and the job list ----------------------------------------------
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "could not be read"),
        ("[]", "not a JSON object"),
        (json.dumps({"mode": "nope", "stages": {}}), "unknown mode"),
        (json.dumps({"mode": "depth", "stages": []}), "no stages table"),
        (json.dumps({"mode": "depth", "stages": {}, "provider": "sora"}), "unknown provider"),
    ],
)
def test_damaged_state_raises_view_error(runs_dir, media, content, message):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text(content)
    with pytest.raises(vm.ViewError, match=message):
        vm.read_state(job)
    [summary] = vm.list_jobs(runs_dir)
    assert summary.id == job.id and message in summary.error


@pytest.mark.parametrize(
    "content",
    [
        {"mode": [], "stages": {}},
        {"mode": {"a": 1}, "stages": {}},
        {"mode": None, "stages": {}},
        {"mode": "depth", "stages": "oops"},
        {"mode": "depth", "stages": None},
    ],
)
def test_corrupt_mode_or_stages_never_break_the_home_list(runs_dir, media, content):
    bad = make_job(runs_dir, media, "depth", done=0)
    good = make_job(runs_dir, media, "direct")
    bad.path("state.json").write_text(json.dumps(content))
    summaries = {s.id: s for s in vm.list_jobs(runs_dir)}
    assert summaries[bad.id].error and summaries[good.id].error is None


def test_list_jobs_newest_first_with_step_thumb_and_cost(runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)  # separate + transcribe are paid
    new = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    wan = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T10:00:00+00:00")
    wan.update(created_at="2026-10-01T10:00:00+00:00")
    (runs_dir / "spend-log.csv").write_text("at,job,step,usd_estimate,usd_micro\n")
    (runs_dir / "not-a-job").mkdir()
    (runs_dir / ("a" * 32)).mkdir()  # half-created: no state.json
    (runs_dir / "_incoming").mkdir()  # the app's uploads in progress
    summaries = vm.list_jobs(runs_dir)
    assert [s.id for s in summaries] == [new.id, old.id, wan.id]
    paid = estimate_stages(["separate", "transcribe"], 30).total
    assert summaries[1].cost_usd == pytest.approx(paid)
    assert summaries[1].short_id == old.id[:8] and summaries[1].error is None
    assert [s.step for s in summaries] == [3, 2, 3]
    assert [s.read_only for s in summaries] == [False, False, True]  # old: depth, no provider
    assert summaries[0].thumb == f"/jobs/{new.id}/files/scene.jpg"
    assert summaries[1].thumb is None  # describe not done yet, no photo
    assert summaries[2].thumb == f"/jobs/{wan.id}/files/character-primary.png"


def test_list_jobs_without_runs_folder(tmp_path):
    assert vm.list_jobs(tmp_path / "missing") == []


def test_folder_removed_mid_scan_is_skipped(runs_dir, media, monkeypatch):
    keep = make_job(runs_dir, media, "direct")
    gone = make_job(runs_dir, media, "direct")
    gone.update(created_at=None)  # forces the mtime fallback
    real = vm._mtime_iso

    def flaky(path):
        if path.name == gone.id:
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(vm, "_mtime_iso", flaky)
    assert [s.id for s in vm.list_jobs(runs_dir)] == [keep.id]


def test_the_home_list_never_writes_the_provider(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh")  # no provider saved: read as WaveSpeed's
    before = job.path("state.json").read_bytes()
    [summary] = vm.list_jobs(runs_dir)
    assert summary.read_only is False  # resolved as WaveSpeed's, never written
    assert job.path("state.json").read_bytes() == before


def test_a_record_written_by_hand_is_read_safely(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    write_record(job, "rgb-repair/sam-prediction.json", {"id": "pred-repair"})
    assert vm.prediction_url(job, vm.RGB_REPAIR_RECORD) == "https://replicate.com/p/pred-repair"


def test_a_setting_you_wrote_has_no_cost_row(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    job.mark_stage("describe", source="you")
    rows = vm.cost_rows(job, job.state)
    assert "Scene description" not in [row.step for row in rows]
    expected = round(estimate_prepare("depth", 30).total - 0.001, 4)
    assert vm.completed_cost(rows) == pytest.approx(expected)


def test_the_lay_back_is_its_own_cost_row(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft"})
    write_record(job, "draft-sam/sam-prediction.json", {"id": "sam-1", "status": "succeeded"})
    job.append_cost({"step": "draft-paste", "usd": 0.002, "task_id": "sam-old"})  # archived
    job.append_cost({"step": "draft-paste", "usd": 0.002, "task_id": "sam-1"})
    rows = vm.cost_rows(job, job.state)
    assert [row.step for row in rows][-2:] == ["Draft (480p)", "Lay back (SAM 3)"]
    assert rows[-1] == vm.CostRow(
        "Lay back (SAM 3)", "Replicate", 0.002, "https://replicate.com/p/sam-1"
    )


def test_no_lay_back_row_without_its_current_record(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    job.append_cost({"step": "draft-paste", "usd": 0.002, "task_id": "sam-old"})
    assert "Lay back (SAM 3)" not in [row.step for row in vm.cost_rows(job, job.state)]
