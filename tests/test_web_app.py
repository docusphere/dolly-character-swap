import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from character_swap.config import Settings
from character_swap.costs import Estimate, Line, record_spend
from character_swap.review import decide, fingerprint
from character_swap.web import steps
from character_swap.web.app import create_app
from viewer_jobs import finish_stage, make_job

KEYS = {"REPLICATE_API_TOKEN": "r-secret", "WAVESPEED_API_KEY": "w-secret"}


@pytest.fixture
def settings(tmp_path, runs_dir):
    return Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir), **KEYS})


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def _app_job(runs_dir, media, **kwargs):
    return make_job(runs_dir, media, "depth_mesh", provider="wavespeed", **kwargs)


def _approved(runs_dir, media):
    job = _app_job(runs_dir, media)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    return job


# ---- home ----------------------------------------------------------------------
def test_home_empty_state_explains_new_swap(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No swaps yet" in response.text and 'href="/new"' in response.text
    assert "Spent on all swaps: <strong>$0.00</strong>" in response.text
    assert "Hide the person" in response.text and "Original sound back" in response.text


def test_home_lists_swaps_newest_first(client, runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)
    new = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T09:30:00+00:00")
    record_spend(new, "prepare", Estimate((Line("x", 0.25),)), runs_dir)
    text = client.get("/").text
    assert text.index(new.id[:8]) < text.index(old.id[:8])
    assert "Step 2 · Prepare" in text and "Step 3 · Check" in text
    assert "2026-11-01 09:30" in text and "Spent on all swaps: <strong>$0.25</strong>" in text
    assert f'href="/jobs/{old.id}"' in text
    assert text.count("Made with the command line") == 1  # the Wan job


def test_home_marks_unreadable_jobs(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{broken")
    response = client.get("/")
    assert response.status_code == 200
    assert "Unreadable: state.json could not be read" in response.text


def test_a_missing_key_is_named_and_new_swap_is_disabled(tmp_path, runs_dir):
    settings = Settings.load(
        root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir), "REPLICATE_API_TOKEN": "r"}
    )
    client = TestClient(create_app(settings), base_url="http://127.0.0.1")
    text = client.get("/").text
    assert "<code>WAVESPEED_API_KEY</code> is missing" in text and "<code>.env</code>" in text
    assert "wavespeed.ai" in text and 'href="/new"' not in text
    assert 'aria-disabled="true">New swap' in text
    assert "REPLICATE_API_TOKEN</code> is missing" not in text
    new = client.get("/new").text
    assert "WAVESPEED_API_KEY</code> is missing" in new and " disabled>Prepare" in new


def test_the_app_re_reads_the_keys_on_each_request(tmp_path, runs_dir):
    current = {"values": {"CHARSWAP_RUNS_DIR": str(runs_dir)}}

    def reload():
        return Settings.load(root=tmp_path, environ=current["values"])

    client = TestClient(create_app(reload(), reload_settings=reload), base_url="http://127.0.0.1")
    assert "is missing" in client.get("/").text
    current["values"] = {**current["values"], **KEYS}
    assert "is missing" not in client.get("/").text


# ---- step 1: new swap ------------------------------------------------------------
def test_new_swap_form_shows_prices_and_choices(client):
    text = client.get("/new").text
    assert "Step 1 of 5" in text and 'name="clip"' in text and 'name="photo"' in text
    assert 'name="describe" maxlength="300"' in text
    assert 'value="depth_mesh" checked' in text and "Better likeness" in text
    assert 'name="rights" value="yes" required' in text
    assert "Prepare · about $0.06" in text
    assert 'data-draft-per-second="0.2200"' in text  # WaveSpeed bills input + output seconds


# ---- the step screens -------------------------------------------------------------
def test_job_link_opens_the_current_step(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=3, running=True)
    response = client.get(f"/jobs/{job.id}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("/step/2")
    done = _approved(runs_dir, media)
    location = client.get(f"/jobs/{done.id}", follow_redirects=False).headers["location"]
    assert location == f"/jobs/{done.id}/step/4"


def test_a_later_step_than_the_job_has_reached_redirects(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    response = client.get(f"/jobs/{job.id}/step/5", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("/step/3")
    assert client.get(f"/jobs/{job.id}/step/1").status_code == 200  # earlier steps stay open


@pytest.mark.parametrize("number", ["0", "6", "x"])
def test_unknown_step_numbers(client, runs_dir, media, number):
    job = _app_job(runs_dir, media)
    assert client.get(f"/jobs/{job.id}/step/{number}").status_code in (404, 422)


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5])
def test_every_step_has_the_bar_its_line_and_no_inline_code(client, runs_dir, media, number):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    text = client.get(f"/jobs/{job.id}/step/{number}").text
    assert f"Step {number} of 5" in text and 'aria-label="Steps"' in text
    assert 'aria-current="step"' in text
    assert steps.step(number).line.replace("'", "&#39;") in text
    assert " style=" not in text and "<style" not in text and "<script>" not in text


def test_prepare_step_shows_tabs_and_the_wipe_player(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=6, running=True)  # masks running
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert f'href="/jobs/{job.id}/step/2?tab=depth"' in text
    assert 'class="tab tab-running" aria-disabled="true"' in text
    text = client.get(f"/jobs/{job.id}/step/2?tab=depth").text
    assert 'class="wipe" data-wipe' in text and "Depth Anything" in text
    assert f'src="/jobs/{job.id}/files/original.mp4"' in text
    assert f'src="/jobs/{job.id}/files/depth.mp4"' in text
    assert "Next: check it" not in text and "About 2 minutes" in text


def test_finished_prepare_offers_the_next_step(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/2?tab=voice").text
    assert f'href="/jobs/{job.id}/step/3"' in text and "Next: check it" in text
    assert f'<audio controls preload="metadata" src="/jobs/{job.id}/files/vocals.wav">' in text
    assert "hello there" in text and "Demucs" in text


def test_check_step_compares_with_what_the_ai_gets(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/3").text
    assert f'src="/jobs/{job.id}/files/prepared.mp4"' in text and "What the AI gets" in text


def test_draft_step_shows_the_prompt_read_only_for_now(client, runs_dir, media):
    job = _approved(runs_dir, media)
    text = client.get(f"/jobs/{job.id}/step/4").text
    assert "@Video 1" in text and "readonly" in text and "Seedance 2.5" in text
    assert not job.path("prompt.txt").exists()


def test_final_step_offers_the_download(client, runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    text = client.get(f"/jobs/{job.id}/step/5").text
    assert f'href="/jobs/{job.id}/files/final.mp4" download=' in text
    assert "Spent on this swap" in text


def test_a_read_only_job_says_where_to_continue(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert f"Continue there: charswap status {job.id}" in text


def test_failed_step_shows_the_error_escaped(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=3, failed="Transcribing speech failed: <boom>")
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert "Transcribing speech failed: &lt;boom&gt;" in text


def test_untrusted_text_is_escaped(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    job.update(transcript="<script>alert(1)</script>")
    text = client.get(f"/jobs/{job.id}/step/2?tab=voice").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_job_page_with_damaged_state_shows_the_error(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("[]")
    for path in (f"/jobs/{job.id}", f"/jobs/{job.id}/step/1"):
        response = client.get(path)
        assert response.status_code == 500
        assert "state.json is not a JSON object." in response.text and 'href="/"' in response.text


@pytest.mark.parametrize("job_id", ["0" * 32, "not-a-job", "A" * 32])
def test_unknown_job_is_a_404_page(client, job_id):
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 404
    assert "There is no swap with that id." in response.text and 'href="/"' in response.text


def test_home_directory_is_hidden_in_errors(client, runs_dir, media):
    home = str(Path.home())
    job = _app_job(runs_dir, media, done=3, failed=f"cannot read {home}/clips/a.mp4")
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert home not in text and "cannot read ~/clips/a.mp4" in text
    job.path("state.json").write_text(f"[] {home}")
    assert home not in client.get(f"/jobs/{job.id}/step/2").text


def test_step_pages_carry_the_progress_to_poll(client, runs_dir, media):
    job = _app_job(runs_dir, media, done=5, running=True)
    text = client.get(f"/jobs/{job.id}/step/2").text
    assert 'data-poll="fast"' in text and f'data-job-id="{job.id}"' in text
    shown = json.loads(text.split('data-progress="')[1].split('"')[0].replace("&#34;", '"'))
    assert shown["stages"]["depth"] == "running" and shown["generation"]["draft"] == "pending"
    assert 'id="new-results"' in text and 'id="refresh-button"' in text


# ---- files ---------------------------------------------------------------------
def test_file_route_serves_video_with_ranges(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    size = job.path("prepared.mp4").stat().st_size
    full = client.get(f"/jobs/{job.id}/files/prepared.mp4")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "video/mp4" and len(full.content) == size
    part = client.get(f"/jobs/{job.id}/files/prepared.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"] == f"bytes 0-99/{size}"


def test_file_route_serves_nested_audio_and_images(client, runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    assert client.get(f"/jobs/{job.id}/files/character-primary.png").status_code == 200
    job.path("sam").mkdir()
    job.path("sam/frame.JPG").write_bytes(b"jpg")
    assert client.get(f"/jobs/{job.id}/files/sam/frame.JPG").status_code == 200


@pytest.mark.parametrize(
    "name",
    [
        "state.json",
        ".env",
        "demucs-prediction.json",
        "notes.txt",
        "missing.mp4",
        "sam",
        "%2E%2E/%2E%2E/outside.mp4",
        "..%2F..%2Foutside.mp4",
        "scene.jpg%00.png",
    ],
)
def test_file_route_refuses_everything_else(client, runs_dir, media, name):
    job = make_job(runs_dir, media, "depth", done=0)
    (runs_dir.parent / "outside.mp4").write_bytes(b"secret")
    job.path(".env").write_text("REPLICATE_API_TOKEN=secret")
    response = client.get(f"/jobs/{job.id}/files/{name}")
    assert response.status_code == 404
    assert "secret" not in response.text


def test_file_route_refuses_absolute_paths_and_symlinks(client, runs_dir, media, tmp_path):
    job = make_job(runs_dir, media, "depth", done=0)
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    absolute = quote(str(outside), safe="")
    assert client.get(f"/jobs/{job.id}/files/{absolute}").status_code == 404
    job.path("link.mp4").symlink_to(outside)
    assert client.get(f"/jobs/{job.id}/files/link.mp4").status_code == 404


def test_file_route_for_unknown_job_is_404(client):
    assert client.get(f"/jobs/{'0' * 32}/files/prepared.mp4").status_code == 404


# ---- polling API -----------------------------------------------------------------
def test_api_progress_shape(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh", done=3, running=True)
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert {"stages", "generation", "done_count", "total_count", "active", "poll"} <= set(data)
    assert list(data["stages"]) == [
        "normalize",
        "separate",
        "pitch",
        "transcribe",
        "describe",
        "face_mesh",
        "embed",
    ]
    assert data["stages"]["transcribe"] == "running"
    assert (data["done_count"], data["total_count"], data["active"]) == (3, 7, True)


def test_api_never_hashes_files(client, runs_dir, media, monkeypatch):
    job = _approved(runs_dir, media)
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    assert client.get(f"/api/jobs/{job.id}").json()["active"] is False
    assert client.get(f"/jobs/{job.id}", follow_redirects=False).status_code == 303


def test_api_errors_are_json(client, runs_dir, media):
    assert client.get(f"/api/jobs/{'0' * 32}").json() == {"error": "There is no swap with that id."}
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{")
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 500 and "could not be read" in response.json()["error"]


# ---- local-only, read-only GETs ----------------------------------------------------
def test_get_routes_never_write(client, runs_dir, media):
    job = _approved(runs_dir, media)
    finish_stage(job, media, "draft")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*"))
    for path in ["/", "/new", f"/jobs/{job.id}", f"/api/jobs/{job.id}"]:
        assert client.get(path).status_code == 200
    for number in range(1, 6):
        assert client.get(f"/jobs/{job.id}/step/{number}").status_code == 200
    assert sorted((p.name, p.stat().st_mtime_ns) for p in job.root.rglob("*")) == before


def test_static_assets_are_served(client):
    css = client.get("/static/viewer.css")
    assert css.status_code == 200 and "prefers-color-scheme: dark" in css.text
    assert "prefers-reduced-motion" in css.text and ".wipe-after" in css.text
    assert client.get("/static/nope.css").status_code == 404


def test_api_docs_are_disabled(client):
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_requests_for_other_hosts_are_refused(settings):
    remote = TestClient(create_app(settings), base_url="http://evil.example")
    assert remote.get("/").status_code == 400
    local = TestClient(create_app(settings), base_url="http://localhost:8770")
    assert local.get("/").status_code == 200


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}


def test_security_headers_on_pages_media_and_api(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    page = client.get(f"/jobs/{job.id}/step/2")
    video = client.get(f"/jobs/{job.id}/files/prepared.mp4", headers={"Range": "bytes=0-9"})
    api = client.get(f"/api/jobs/{job.id}")
    missing = client.get("/jobs/nope")
    for response in (page, video, api, missing, client.get("/"), client.get("/new")):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value, (name, response.url)
    assert video.status_code == 206 and video.headers["accept-ranges"] == "bytes"
    assert page.headers["cache-control"] == "no-store"
    assert video.headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-store"


# ---- the wipe player and live progress (static/app.js) ---------------------------------
def test_every_page_loads_the_one_app_script(client, runs_dir, media):
    job = _app_job(runs_dir, media)
    for path in ["/", "/new", f"/jobs/{job.id}/step/2"]:
        assert '<script src="/static/app.js" defer></script>' in client.get(path).text


def test_app_script_drives_the_wipe_player(client):
    script = client.get("/static/app.js").text
    assert 'setProperty("--split"' in script  # CSSOM, allowed by the CSP (no inline styles)
    assert "DRIFT = 0.1" in script and "before.currentTime = after.currentTime" in script
    assert "pointerdown" in script and 'divider.addEventListener("input"' in script
    assert "after.muted = !after.muted" in script  # the sound plays the "after" side
    assert '"wipe:ended"' in script  # step 3's approve gate listens for it


def test_app_script_polls_and_never_interrupts_playback(client):
    script = client.get("/static/app.js").text
    assert "fast: 2000" in script and "slow: 10000" in script
    assert "Object.keys(shown)" in script  # compares only what the page was rendered with
    assert "if (!playing())" in script and 'getElementById("new-results").hidden = false' in script
