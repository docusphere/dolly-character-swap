import json

import pytest

from character_swap.costs import Estimate, Line, estimate_prepare, estimate_stages, record_spend
from character_swap.modes import GENERATION_STAGES
from character_swap.pipeline import PAID_STAGES
from character_swap.review import decide, fingerprint
from character_swap.web import view_model as vm
from viewer_jobs import finish_stage, make_job, write_record

SECTION_IDS = {
    "depth": ["upload", "vocals", "transcript", "depth", "mask", "prepared", "review"],
    "depth_mesh": [
        "upload",
        "vocals",
        "transcript",
        "depth",
        "mask",
        "face-mesh",
        "prepared",
        "review",
    ],
    "face_mesh": ["upload", "vocals", "transcript", "face-mesh", "prepared", "review"],
    "direct": ["upload", "source", "review", "draft", "final"],
}


def sections(view):
    return {section.id: section for section in view.sections}


# ---- tables ----------------------------------------------------------------------
@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_sections_per_mode(mode):
    assert [section.id for section in vm.sections_for(mode)] == SECTION_IDS[mode]


@pytest.mark.parametrize(("mode", "count"), [("depth", 9), ("depth_mesh", 10), ("face_mesh", 7)])
def test_stage_counts_match_spec(mode, count):
    assert len(vm.STAGES_BY_MODE[mode]) == count
    assert vm.STAGES_BY_MODE["direct"] == ("normalize",)


@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_sections_cover_every_stage_of_the_mode(mode):
    covered = {stage for section in vm.sections_for(mode) for stage in section.stages}
    assert covered - set(GENERATION_STAGES) == set(vm.STAGES_BY_MODE[mode])


@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_paid_stages_are_a_subset_of_the_mode(mode):
    assert set(PAID_STAGES[mode]) <= set(vm.STAGES_BY_MODE[mode])


def test_every_section_explains_why():
    for section in vm.SECTIONS:
        assert section.title and len(section.why) > 40, section.id
        assert "—" not in section.why  # UI guide: no em dashes in body copy


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


def test_progress_stops_being_active_on_failure(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=2, failed="boom")
    info = vm.progress(job.state)
    assert info["stages"]["pitch"] == "failed" and info["active"] is False


def test_unknown_stage_status_counts_as_pending(runs_dir, media):
    job = make_job(runs_dir, media, "direct", done=0)
    job.mark_stage("normalize", "weird")
    assert vm.progress(job.state)["stages"] == {"normalize": "pending"}


# ---- job page: statuses ----------------------------------------------------------
@pytest.mark.parametrize("mode", list(SECTION_IDS))
def test_finished_job_page(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    view = vm.job_view(job, runs_dir)
    assert [s.id for s in view.sections] == SECTION_IDS[mode]
    assert [s.number for s in view.sections] == list(range(1, len(SECTION_IDS[mode]) + 1))
    for section in view.sections:
        if section.id in ("review", "draft", "final"):
            continue  # waiting for the user, not part of preparation
        assert section.status == "done", section.id
        assert all(item.state == "ready" for item in section.media), section.id
    assert view.nav[-1] == vm.NavItem("costs", "Costs", "costs", "$", "summary")
    assert [item.id for item in view.nav[:-1]] == SECTION_IDS[mode]
    assert view.status == "Ready for review" and view.progress["active"] is False


def test_running_section_and_navigator(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=7, running=True)  # composite running
    view = vm.job_view(job, runs_dir)
    mask = sections(view)["mask"]
    assert mask.status == "running"
    assert [item.state for item in mask.media] == ["waiting", "waiting"]
    nav = {item.id: item for item in view.nav}
    assert nav["mask"].icon == "⟳" and nav["mask"].status_text == "running"
    assert nav["depth"].icon == "✓" and nav["prepared"].icon == "◻"
    assert view.status == "Preparing"


def test_failed_section_shows_error(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, failed="Generating depth video failed: x")
    view = vm.job_view(job, runs_dir)
    depth = sections(view)["depth"]
    assert depth.status == "failed" and depth.error == "Generating depth video failed: x"
    assert {item.id: item.icon for item in view.nav}["depth"] == "✕"
    assert view.status == "Failed"
    assert sections(view)["vocals"].error is None


def test_failed_stage_without_message_gets_generic_error(runs_dir, media):
    job = make_job(runs_dir, media, "direct", done=0)
    job.mark_stage("normalize", "failed")
    assert sections(vm.job_view(job, runs_dir))["upload"].error == "This step failed."


def test_partially_done_section_shows_finished_files(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=2)  # separate done, pitch not yet
    vocals = sections(vm.job_view(job, runs_dir))["vocals"]
    assert vocals.status == "pending"
    assert [item.state for item in vocals.media] == ["ready", "ready", "waiting"]
    assert vocals.media[1].url == f"/jobs/{job.id}/files/vocals.wav"
    assert [item.kind for item in vocals.media] == ["audio", "audio", "audio"]


# ---- review section ------------------------------------------------------------
def test_review_pending_shows_approve_command(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    view = vm.job_view(job, runs_dir)
    review = sections(view)["review"]
    texts = {text.label: text for text in review.texts}
    full = fingerprint(job)
    assert review.status == "pending" and texts["Status"].value == "pending"
    assert texts["Fingerprint"].value == full[:12]
    command = texts["Approve after watching"]
    assert command.code and f"charswap review {job.id} --approve" in command.value
    assert command.value.endswith(f"--fingerprint {full}")


def test_review_approved_and_rejected(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    view = vm.job_view(job, runs_dir)
    review = sections(view)["review"]
    assert review.status == "done" and view.status == "Approved"
    labels = [text.label for text in review.texts]
    assert "Note" in labels and "Approve after watching" not in labels
    decide(job, approved=False, note="mask flickers", fingerprint=fingerprint(job))
    view = vm.job_view(job, runs_dir)
    assert sections(view)["review"].status == "failed" and view.status == "Rejected"


def test_review_unavailable_before_prepare(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    review = sections(vm.job_view(job, runs_dir))["review"]
    assert review.status == "pending"
    assert [(t.label, t.value) for t in review.texts][:2] == [
        ("Status", "unavailable"),
        ("Note", "Prepare the video first."),
    ]


def test_review_read_errors_show_unavailable(runs_dir, media, monkeypatch):
    job = make_job(runs_dir, media, "direct")

    def broken(_job):
        raise OSError("permission denied")

    monkeypatch.setattr(vm.review, "status", broken)
    assert vm.review_status(job)["status"] == "unavailable"


# ---- replicate links -----------------------------------------------------------
def test_paid_sections_link_to_replicate(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    by_id = sections(vm.job_view(job, runs_dir))
    assert by_id["vocals"].links == (
        vm.Link("View on Replicate ↗", "https://replicate.com/p/pred-separate"),
    )
    assert [link.url for link in by_id["transcript"].links] == [
        "https://replicate.com/p/pred-transcribe",
        "https://replicate.com/p/pred-describe",
    ]
    assert by_id["depth"].links[0].url == "https://replicate.com/p/pred-depth"
    assert by_id["mask"].links[0].url == "https://replicate.com/p/pred-masks"
    assert by_id["prepared"].links == () and by_id["upload"].links == ()


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
    assert sections(vm.job_view(job, runs_dir))["vocals"].links == ()


def test_rgb_repair_links_both_attempts(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    write_record(job, "rgb-repair/sam-prediction.json", {"id": "pred-repair"})
    job.update(mask_source="rgb")
    links = sections(vm.job_view(job, runs_dir))["mask"].links
    assert links == (
        vm.Link("First attempt ↗", "https://replicate.com/p/pred-masks"),
        vm.Link("Repair ↗", "https://replicate.com/p/pred-repair"),
    )


def test_huggingface_depth_has_no_link_and_is_free(runs_dir, media):
    job = make_job(runs_dir, media, "depth", depth_provider="huggingface")
    view = vm.job_view(job, runs_dir)
    assert sections(view)["depth"].links == ()
    depth_row = next(row for row in view.costs.rows if row.step == "Depth video")
    assert depth_row == vm.CostRow("Depth video", "Hugging Face · free", 0.0, None)
    expected = round(estimate_prepare("depth", 30).total - 0.04, 4)
    assert view.costs.completed_usd == pytest.approx(expected)


# ---- costs ---------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_finished_job_costs_match_the_prepare_estimate(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    costs = vm.job_view(job, runs_dir).costs
    assert len(costs.rows) == len(PAID_STAGES[mode])
    assert all(row.provider == "Replicate" for row in costs.rows)
    assert costs.completed_usd == pytest.approx(round(estimate_prepare(mode, 30).total, 4))


def test_costs_count_only_completed_steps(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="whisper down")
    view = vm.job_view(job, runs_dir)
    assert [row.step for row in view.costs.rows] == ["Separate vocals"]
    assert view.costs.rows[0].link == "https://replicate.com/p/pred-separate"
    assert view.costs.completed_usd == pytest.approx(0.017)
    assert view.cost_usd == view.costs.completed_usd


def test_logged_and_all_jobs_spend(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    other = make_job(runs_dir, media, "face_mesh")
    assert vm.job_view(job, runs_dir).costs.logged_usd == 0
    record_spend(job, "prepare", Estimate((Line("a", 0.0188),)), runs_dir)
    record_spend(other, "prepare", Estimate((Line("a", 0.02),)), runs_dir)
    job.append_cost({"step": "odd", "usd": "not a number"})
    job.append_cost("garbage")
    costs = vm.job_view(job, runs_dir).costs
    assert costs.logged_usd == pytest.approx(0.0188)
    assert costs.all_jobs_usd == pytest.approx(0.0388)


def test_logged_cost_ignores_a_damaged_costs_field(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.update(costs="oops")
    assert vm.logged_cost(job.state) == 0.0


# ---- files and texts -------------------------------------------------------------
def test_done_step_with_missing_file_says_missing(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    job.path("depth.mp4").unlink()
    depth = sections(vm.job_view(job, runs_dir))["depth"]
    assert depth.status == "done"
    assert depth.media == (vm.MediaItem("Depth video", "video", None, "missing"),)


def test_depth_mesh_composite_fallback(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=8)  # composite done, face_mesh not
    composite = sections(vm.job_view(job, runs_dir))["mask"].media[1]
    assert composite.url == f"/jobs/{job.id}/files/composite-silent.mp4"
    job.mark_stage("face_mesh", "running")
    assert vm.composite_name(job, job.state) == "depth-composite-silent.mp4"

    done = make_job(runs_dir, media, "depth_mesh")
    composite = sections(vm.job_view(done, runs_dir))["mask"].media[1]
    assert composite.url == f"/jobs/{done.id}/files/depth-composite-silent.mp4"
    done.path("depth-composite-silent.mp4").unlink()
    composite = sections(vm.job_view(done, runs_dir))["mask"].media[1]
    assert composite.state == "missing"  # never falls back to the meshed video


def test_transcript_scene_and_upload_facts(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    by_id = sections(vm.job_view(job, runs_dir))
    texts = {text.label: text.value for text in by_id["transcript"].texts}
    assert texts == {"Transcript": "hello there", "Scene": "A bright studio with plants."}
    assert by_id["transcript"].media[0].kind == "image"
    upload = {text.label: text.value for text in by_id["upload"].texts}
    assert upload["Upload"].startswith("1.0 s · ") and upload["Normalized"].endswith("KB")


def test_empty_transcript_is_explained(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    job.update(transcript="", scene=None)
    texts = sections(vm.job_view(job, runs_dir))["transcript"].texts
    assert [t.value for t in texts] == ["(no speech found)", "(no description)"]


def test_unreadable_video_is_reported_not_raised(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.path("original.mp4").write_bytes(b"not a video" * 20_000)
    upload = {t.label: t.value for t in sections(vm.job_view(job, runs_dir))["upload"].texts}
    assert upload["Normalized"] == "unreadable video · 0.2 MB"


def test_face_tracking_summary(runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    texts = sections(vm.job_view(job, runs_dir))["face-mesh"].texts
    assert texts == (vm.TextItem("Tracking", "Face found in 24 of 24 frames"),)
    report = {"frames": 24, "tracked": 12, "missing_frames": list(range(12, 24))}
    job.path("face-tracking.json").write_text(json.dumps(report))
    job.mark_stage("face_mesh", "failed", error="Face tracking lost the face")
    texts = sections(vm.job_view(job, runs_dir))["face-mesh"].texts
    assert texts[0].value.startswith("Face found in 12 of 24 frames · lost on frames 12, 13")
    assert texts[0].value.endswith(" …")
    job.path("face-tracking.json").write_text("{")
    texts = sections(vm.job_view(job, runs_dir))["face-mesh"].texts
    assert texts[0].value == "face-tracking.json missing or unreadable"


def test_direct_job_shows_character(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    source = sections(vm.job_view(job, runs_dir))["source"]
    assert [item.url for item in source.media] == [
        f"/jobs/{job.id}/files/original.mp4",
        f"/jobs/{job.id}/files/character-primary.png",
    ]
    job.update(character_primary="../outside.png")
    assert sections(vm.job_view(job, runs_dir))["source"].media[1].state == "missing"
    job.update(character_primary=None)
    assert sections(vm.job_view(job, runs_dir))["source"].media[1].state == "missing"


# ---- damaged state and the job list ----------------------------------------------
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "could not be read"),
        ("[]", "not a JSON object"),
        (json.dumps({"mode": "nope", "stages": {}}), "unknown mode"),
        (json.dumps({"mode": "depth", "stages": []}), "no stages table"),
    ],
)
def test_damaged_state_raises_view_error(runs_dir, media, content, message):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text(content)
    with pytest.raises(vm.ViewError, match=message):
        vm.job_view(job, runs_dir)
    [summary] = vm.list_jobs(runs_dir)
    assert summary.id == job.id and message in summary.error


def test_list_jobs_newest_first_with_progress(runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)  # separate + transcribe are paid
    new = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T10:00:00+00:00")
    (runs_dir / "spend-log.csv").write_text("at,job,step,usd_estimate,usd_micro\n")
    (runs_dir / "not-a-job").mkdir()
    (runs_dir / ("a" * 32)).mkdir()  # half-created: no state.json
    summaries = vm.list_jobs(runs_dir)
    assert [s.id for s in summaries] == [new.id, old.id]
    assert (summaries[1].done_count, summaries[1].total_count) == (4, 9)
    paid = estimate_stages(["separate", "transcribe"], 30).total
    assert summaries[1].cost_usd == pytest.approx(paid)
    assert summaries[1].short_id == old.id[:8] and summaries[1].error is None


def test_list_jobs_without_runs_folder(tmp_path):
    assert vm.list_jobs(tmp_path / "missing") == []


# ---- hardening ---------------------------------------------------------------------
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
    with pytest.raises(vm.ViewError):
        vm.job_view(bad, runs_dir)
    summaries = {s.id: s for s in vm.list_jobs(runs_dir)}
    assert summaries[bad.id].error and summaries[good.id].error is None


def test_cost_row_links_match_section_links(runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    view = vm.job_view(job, runs_dir)
    section_links = {link.url for section in view.sections for link in section.links}
    assert {row.link for row in view.costs.rows} <= section_links
    assert all(row.link for row in view.costs.rows)


def test_file_url_quotes_names(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.path("my face.png").write_bytes(b"x")
    job.update(character_primary="my face.png")
    item = sections(vm.job_view(job, runs_dir))["source"].media[1]
    assert item.url == f"/jobs/{job.id}/files/my%20face.png"
    assert vm.file_url("j", "sam/a b.json") == "/jobs/j/files/sam/a%20b.json"


@pytest.mark.parametrize("value", ["nan", "inf", float("nan"), float("-inf")])
def test_non_finite_logged_costs_count_as_zero(value):
    state = {"costs": [{"usd": value}, {"usd": 0.5}]}
    assert vm.logged_cost(state) == 0.5


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


def test_video_facts_treats_a_vanished_file_as_missing(tmp_path, monkeypatch):
    gone = tmp_path / "gone.mp4"
    assert vm.video_facts(gone) == "file missing"
    monkeypatch.setattr(vm, "stream_seconds", lambda *_a: 1.0)
    assert vm.video_facts(gone) == "file missing"


# ---- generation sections (Plan 2a) ---------------------------------------------------
def test_direct_job_shows_draft_and_final(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert draft.status == "pending" and draft.media[0].state == "waiting" and draft.links == ()
    assert view.status == "Ready for review"
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-draft"})
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert draft.status == "done" and draft.media[0].url == f"/jobs/{job.id}/files/draft.mp4"
    assert [link.url for link in draft.links] == ["https://replicate.com/p/pred-draft"]
    assert view.status == "Draft ready"
    rows = [(row.step, row.provider, row.usd, row.link) for row in view.costs.rows]
    assert rows == [("Draft (480p)", "Replicate", 0.08, "https://replicate.com/p/pred-draft")]
    assert view.costs.completed_usd == pytest.approx(0.08)


def test_generating_and_finished_statuses(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.mark_stage("draft", "running")
    assert vm.job_view(job, runs_dir).status == "Generating"
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    view = vm.job_view(job, runs_dir)
    assert view.status == "Finished" and sections(view)["final"].status == "done"
    assert not any(t.label == "Out of date" for t in sections(view)["final"].texts)


def test_failed_draft_says_which_command_to_run(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.mark_stage("draft", "failed", error="Generating the 480p draft failed: boom")
    view = vm.job_view(job, runs_dir)
    draft = sections(view)["draft"]
    assert view.status == "Failed"
    assert draft.error == "Generating the 480p draft failed: boom"
    assert draft.retry == f"charswap generate {job.id} --draft"
    assert sections(view)["upload"].retry == f"charswap resume {job.id}"


def test_seedance_jobs_have_no_generation_sections_yet():
    for mode in ("depth", "face_mesh", "depth_mesh"):
        assert not {"draft", "final"} & {s.id for s in vm.sections_for(mode)}


def test_stale_final_says_make_a_new_draft(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint="made-from-older-inputs")
    view = vm.job_view(job, runs_dir)
    assert view.status == "Out of date"  # never presented as finished
    texts = {t.label: t for t in sections(view)["final"].texts}
    assert "no longer approved" in texts["Out of date"].value
    assert texts["Make a new draft"].value == f"charswap generate {job.id} --draft"
    assert texts["Make a new draft"].code
    assert "Out of date" not in {t.label for t in sections(view)["draft"].texts}


def test_damaged_generation_entry_is_not_called_stale(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    state = job.state
    state["stages"]["draft"] = "garbage"
    job.path("state.json").write_text(json.dumps(state))
    view = vm.job_view(job, runs_dir)  # never raises
    assert sections(view)["draft"].texts == ()


def test_remade_draft_row_counts_only_the_current_prediction(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.08, "at": "x", "task_id": "pred-old"})
    job.append_cost({"step": "draft", "usd": 0.08, "at": "y", "task_id": "pred-draft"})
    costs = vm.job_view(job, runs_dir).costs
    assert [row.usd for row in costs.rows] == [0.08]
    assert costs.logged_usd == pytest.approx(0.16)  # every charge is still logged


def test_draft_cost_without_task_ids_falls_back_to_the_step(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.05, "at": "x"})
    assert [row.usd for row in vm.job_view(job, runs_dir).costs.rows] == [0.05]
