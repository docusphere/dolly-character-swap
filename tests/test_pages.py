import pytest

from character_swap.config import Settings
from character_swap.review import decide, fingerprint
from character_swap.web import pages
from viewer_jobs import finish_stage, make_job


def _approved(runs_dir, media, mode="depth_mesh"):
    job = make_job(runs_dir, media, mode, provider="wavespeed")
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    return job


def _tabs(page):
    return {tab.id: tab for tab in page.tabs}


# ---- the step bar --------------------------------------------------------------
def test_step_bar_links_only_the_steps_a_job_has_reached():
    bar = pages.step_bar("j" * 32, 2, 3)
    assert [(item.number, item.state) for item in bar] == [
        (1, "done"),
        (2, "current"),
        (3, "open"),
        (4, "locked"),
        (5, "locked"),
    ]
    assert [item.url for item in bar][:3] == [f"/jobs/{'j' * 32}/step/{n}" for n in (1, 2, 3)]
    assert bar[3].url is None and bar[4].url is None


def test_a_new_swap_bar_has_no_links():
    assert all(item.url is None for item in pages.step_bar(None, 1, 1))


# ---- home ----------------------------------------------------------------------
def test_home_names_missing_keys_but_never_their_values(tmp_path, runs_dir):
    empty = Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir)})
    page = pages.home_page(runs_dir, empty)
    assert [key.name for key in page.missing] == ["REPLICATE_API_TOKEN", "WAVESPEED_API_KEY"]
    assert "wavespeed.ai" in page.missing[1].where
    full = Settings.load(
        root=tmp_path, environ={"REPLICATE_API_TOKEN": "secret-r", "WAVESPEED_API_KEY": "secret-w"}
    )
    assert pages.missing_keys(full) == ()


# ---- step 1 and 2 ------------------------------------------------------------------
def test_upload_step_shows_the_clip_the_photo_and_the_look(runs_dir, media):
    job = make_job(runs_dir, media, "depth", provider="wavespeed")
    job.update(character_description="Short dark hair.")
    page = pages.job_page(job, runs_dir, 1)
    assert page.upload == f"/jobs/{job.id}/files/upload.mp4"
    assert page.photo == f"/jobs/{job.id}/files/character-primary.png"
    assert page.look == "Better likeness" and page.description == "Short dark hair."
    assert page.read_only is False and page.step.title == "Upload"


def test_prepare_tabs_fill_in_as_stages_finish(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", done=6, running=True, provider="wavespeed")
    page = pages.job_page(job, runs_dir, 2)  # depth done, masks running
    tabs = _tabs(page)
    assert list(tabs) == ["depth", "cutout", "face", "prepared", "voice"]
    assert [tab.state for tab in page.tabs] == ["ready", "running", "waiting", "waiting", "ready"]
    assert page.tab.id == "depth"  # the ready tab made last in the pipeline when none is asked
    depth = tabs["depth"].wipe
    assert depth.before == f"/jobs/{job.id}/files/original.mp4"
    assert depth.after == f"/jobs/{job.id}/files/depth.mp4" and depth.sound is False
    assert page.state == "running" and page.prep_done is False
    assert pages.job_page(job, runs_dir, 2, tab="depth").tab.id == "depth"
    assert pages.job_page(job, runs_dir, 2, tab="cutout").tab.id == "depth"  # not ready yet


@pytest.mark.parametrize("mode", ["depth", "face_mesh", "depth_mesh"])
def test_a_prepared_job_opens_on_what_the_ai_gets(runs_dir, media, mode):
    job = make_job(runs_dir, media, mode, provider="wavespeed")
    assert pages.job_page(job, runs_dir, 2).tab.id == "prepared"  # embed is the last stage
    assert pages.job_page(job, runs_dir, 2, tab="voice").tab.id == "voice"


def test_finished_prepare_tabs_use_each_modes_files(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    tabs = _tabs(pages.job_page(job, runs_dir, 2))
    assert tabs["cutout"].wipe.after.endswith("/depth-composite-silent.mp4")
    assert tabs["face"].wipe.after.endswith("/composite-silent.mp4")
    assert tabs["prepared"].wipe.after.endswith("/prepared.mp4")
    assert tabs["prepared"].texts == (("Room", "A bright studio with plants."),)
    voice = tabs["voice"]
    assert voice.wipe is None and [label for label, _url in voice.audio] == [
        "Voice only",
        "Raised 3 semitones (what the AI hears)",
    ]
    assert voice.texts == (("Words", "hello there"),)
    depth = make_job(runs_dir, media, "depth", provider="wavespeed")
    tabs = _tabs(pages.job_page(depth, runs_dir, 2))
    assert "face" not in tabs and tabs["cutout"].wipe.after.endswith("/composite-silent.mp4")


def test_a_tab_whose_file_is_gone_says_missing(runs_dir, media):
    job = make_job(runs_dir, media, "depth", provider="wavespeed")
    job.path("depth.mp4").unlink()
    assert _tabs(pages.job_page(job, runs_dir, 2))["depth"].state == "missing"


def test_a_failed_stage_shows_its_error(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, failed="Generating depth video failed: x")
    page = pages.job_page(job, runs_dir, 2)
    assert page.error == "Generating depth video failed: x" and page.state == "failed"
    assert _tabs(page)["depth"].state == "failed"
    stopped = make_job(runs_dir, media, "depth", done=5, failed="interrupted")
    assert pages.job_page(stopped, runs_dir, 2).error == "Stopped before it finished."


def test_a_wan_job_has_no_prepare_tabs(runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    page = pages.job_page(job, runs_dir, 2)
    assert page.tabs == () and page.tab is None and page.read_only
    assert page.original == f"/jobs/{job.id}/files/original.mp4"


# ---- step 3: check -------------------------------------------------------------
def test_check_step_compares_the_original_with_what_the_ai_gets(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh", provider="wavespeed")
    page = pages.job_page(job, runs_dir, 3)
    assert page.wipe.after.endswith("/prepared.mp4") and page.wipe.after_label == "What the AI gets"
    assert page.fingerprint == fingerprint(job) and page.approved is False
    decide(job, approved=True, note="ok", fingerprint=page.fingerprint)
    assert pages.job_page(job, runs_dir, 3).approved is True
    job.path("prepared.mp4").write_bytes(b"changed")  # the approval no longer matches
    assert pages.job_page(job, runs_dir, 3).approved is False


def test_check_step_before_preparing_has_no_player(runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, provider="wavespeed")
    page = pages.job_page(job, runs_dir, 3)
    assert page.wipe is None and page.fingerprint == "" and page.state == "waiting"


# ---- steps 4 and 5 ---------------------------------------------------------------
def test_draft_step_shows_the_default_prompt_without_writing_it(runs_dir, media):
    job = _approved(runs_dir, media)
    page = pages.job_page(job, runs_dir, 4)
    assert "@Video 1" in page.prompt and "hello there" in page.prompt
    assert not job.path("prompt.txt").exists()  # rendering never writes
    job.path("prompt.txt").write_text("My own words for @Image 1 and @Video 1.")
    assert pages.job_page(job, runs_dir, 4).prompt == "My own words for @Image 1 and @Video 1."
    assert page.wipe is None and page.state == "waiting"


def test_a_running_draft_shows_when_it_started(runs_dir, media):
    job = _approved(runs_dir, media)
    job.mark_stage("draft", "running", started_at="2026-11-13T10:00:00+00:00")
    page = pages.job_page(job, runs_dir, 4)
    assert page.state == "running" and page.started_at == "2026-11-13T10:00:00+00:00"


def test_only_a_seedance_draft_and_final_name_seedance(runs_dir, media):
    seedance = _approved(runs_dir, media)
    assert [tag.name for tag in pages.job_page(seedance, runs_dir, 4).tags] == ["Seedance 2.5"]
    wan = make_job(runs_dir, media, "direct")
    decide(wan, approved=True, note="ok", fingerprint=fingerprint(wan))
    assert (
        pages.job_page(wan, runs_dir, 4).tags == () and pages.job_page(wan, runs_dir, 5).tags == ()
    )


def test_a_finished_draft_plays_with_sound(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    page = pages.job_page(job, runs_dir, 4)
    assert page.wipe.after.endswith("/draft.mp4") and page.wipe.sound is True
    assert page.state == "ready" and page.stale is False
    job.mark_stage("draft", fingerprint="older-inputs")
    assert pages.job_page(job, runs_dir, 4).stale is True


def test_a_failed_draft_shows_the_error(runs_dir, media):
    job = _approved(runs_dir, media)
    job.mark_stage("draft", "failed", error="Generating the 480p draft failed: boom")
    page = pages.job_page(job, runs_dir, 4)
    assert page.state == "failed" and page.error.endswith("boom") and page.wipe is None


def test_a_finished_final_can_be_downloaded(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    job.append_cost({"step": "draft", "usd": 0.88, "task_id": "pred-draft"})
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint=fingerprint(job), resolution="720p")
    page = pages.job_page(job, runs_dir, 5)
    assert page.download == f"/jobs/{job.id}/files/final.mp4" and page.resolution == "720p"
    assert page.wipe.after_label == "Final" and page.wipe.sound
    assert page.spent_usd == pytest.approx(0.0608 + 0.88)


def test_a_final_file_that_is_gone_says_missing(runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    job.path("final.mp4").unlink()
    page = pages.job_page(job, runs_dir, 5)
    assert page.state == "missing" and page.download is None


def test_unreadable_check_files_or_prompt_are_a_damaged_job(runs_dir, media, monkeypatch):
    job = _approved(runs_dir, media)
    job.path("prompt.txt").write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(pages.ViewError, match="prompt.txt could not be read"):
        pages.job_page(job, runs_dir, 4)

    def unreadable(_job):
        raise PermissionError("Permission denied: prepared.mp4")

    monkeypatch.setattr(pages, "fingerprint", unreadable)
    with pytest.raises(pages.ViewError, match="could not be read: Permission denied"):
        pages.job_page(job, runs_dir, 3)


def test_pages_never_write(runs_dir, media):
    job = make_job(runs_dir, media, "depth_mesh")  # no provider saved: read as WaveSpeed's
    before = sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*"))
    for number in (1, 2, 3, 4, 5):
        pages.job_page(job, runs_dir, number)
    assert sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*")) == before
