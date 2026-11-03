import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from character_swap.config import Settings
from character_swap.costs import Estimate, Line, record_spend
from character_swap.web.app import create_app
from character_swap.web.view_model import SECTIONS, sections_for
from viewer_jobs import make_job


@pytest.fixture
def settings(tmp_path, runs_dir):
    return Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(runs_dir)})


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


# ---- home ----------------------------------------------------------------------
def test_home_empty_state_explains_prepare(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No jobs yet" in response.text and "charswap prepare &lt;video&gt;" in response.text
    assert "All jobs (spend log): <strong>$0.0000</strong>" in response.text


def test_home_lists_jobs_newest_first(client, runs_dir, media):
    old = make_job(runs_dir, media, "depth", done=4)
    new = make_job(runs_dir, media, "direct")
    old.update(created_at="2026-10-30T10:00:00+00:00")
    new.update(created_at="2026-11-01T09:30:00+00:00")
    record_spend(new, "prepare", Estimate((Line("x", 0.25),)), runs_dir)
    text = client.get("/").text
    assert text.index(new.id[:8]) < text.index(old.id[:8])
    assert "4/9 steps" in text and "1/1 steps" in text
    assert "2026-11-01 09:30" in text and "$0.2500" in text
    assert f'href="/jobs/{old.id}"' in text


def test_home_marks_unreadable_jobs(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{broken")
    response = client.get("/")
    assert response.status_code == 200
    assert "Unreadable: state.json could not be read" in response.text


# ---- job page ------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["depth", "depth_mesh", "face_mesh", "direct"])
def test_job_page_has_every_section_and_anchor(client, runs_dir, media, mode):
    job = make_job(runs_dir, media, mode)
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 200
    for section in [*sections_for(mode), None]:
        section_id = section.id if section else "costs"
        assert f'<section id="{section_id}"' in response.text
        assert f'href="#{section_id}"' in response.text
    for section in sections_for(mode):
        assert section.why in response.text.replace("&#39;", "'")
    assert 'data-active="false"' in response.text
    assert "Ready for review" in response.text


def test_job_page_links_media_and_replicate(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    text = client.get(f"/jobs/{job.id}").text
    assert (
        f'<video controls preload="metadata" playsinline src="/jobs/{job.id}/files/depth.mp4">'
        in text
    )
    assert f'<audio controls preload="metadata" src="/jobs/{job.id}/files/vocals.wav">' in text
    assert (
        'href="https://replicate.com/p/pred-depth" target="_blank" rel="noopener noreferrer"'
        in text
    )
    assert (
        "This job, steps completed so far" in text
        and "Exact charges: replicate.com billing" in text
    )
    assert f"charswap review {job.id} --approve" in text


def test_live_job_page_polls_and_shows_running_step(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, running=True)  # depth running
    text = client.get(f"/jobs/{job.id}").text
    assert 'data-active="true"' in text
    shown = json.loads(text.split('data-stages="')[1].split('"')[0].replace("&#34;", '"'))
    assert shown["stages"]["depth"] == "running" and shown["stages"]["normalize"] == "done"
    assert shown["generation"] == {"draft": "pending", "final": "pending"}
    assert 'data-poll="fast"' in text
    assert "Waiting for this step…" in text and "⟳" in text


def test_failed_step_shows_error_and_resume_hint(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="Transcribing speech failed: <boom>")
    text = client.get(f"/jobs/{job.id}").text
    assert "Transcribing speech failed: &lt;boom&gt;" in text  # escaped, never raw HTML
    assert f"Fix the issue, then run <code>charswap resume {job.id}</code>" in text
    assert 'data-active="false"' in text


def test_untrusted_text_is_escaped(client, runs_dir, media):
    job = make_job(runs_dir, media, "face_mesh")
    job.update(transcript="<script>alert(1)</script>")
    text = client.get(f"/jobs/{job.id}").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_job_page_with_damaged_state_shows_the_error(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("[]")
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 500
    assert "state.json is not a JSON object." in response.text
    assert 'href="/"' in response.text


@pytest.mark.parametrize("job_id", ["0" * 32, "not-a-job", "A" * 32])
def test_unknown_job_is_a_404_page(client, job_id):
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 404
    assert "There is no job with that id." in response.text and 'href="/"' in response.text


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
    assert set(data) == {"stages", "generation", "done_count", "total_count", "active", "poll"}
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
    job = make_job(runs_dir, media, "depth")
    monkeypatch.setattr(
        "character_swap.review._compute_fingerprint", lambda _job: pytest.fail("hashed")
    )
    assert client.get(f"/api/jobs/{job.id}").json()["active"] is False


def test_api_errors_are_json(client, runs_dir, media):
    assert client.get(f"/api/jobs/{'0' * 32}").json() == {"error": "There is no job with that id."}
    job = make_job(runs_dir, media, "depth", done=0)
    job.path("state.json").write_text("{")
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 500 and "could not be read" in response.json()["error"]


# ---- read-only, local-only -------------------------------------------------------
@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_no_route_changes_anything(client, runs_dir, media, method):
    job = make_job(runs_dir, media, "depth")
    for path in [
        "/",
        f"/jobs/{job.id}",
        f"/jobs/{job.id}/files/prepared.mp4",
        f"/api/jobs/{job.id}",
        "/static/viewer.css",
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 405, (method, path)


def test_static_assets_are_served(client):
    css = client.get("/static/viewer.css")
    assert css.status_code == 200 and "prefers-color-scheme: dark" in css.text
    js = client.get("/static/viewer.js")
    assert js.status_code == 200 and "IntersectionObserver" in js.text
    assert client.get("/static/nope.css").status_code == 404


def test_api_docs_are_disabled(client):
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_requests_for_other_hosts_are_refused(settings):
    remote = TestClient(create_app(settings), base_url="http://evil.example")
    assert remote.get("/").status_code == 400
    local = TestClient(create_app(settings), base_url="http://localhost:8770")
    assert local.get("/").status_code == 200


def test_every_section_title_is_unique():
    assert len({section.id for section in SECTIONS}) == len(SECTIONS)


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}


def test_security_headers_on_pages_media_and_api(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    page = client.get(f"/jobs/{job.id}")
    video = client.get(f"/jobs/{job.id}/files/prepared.mp4", headers={"Range": "bytes=0-9"})
    api = client.get(f"/api/jobs/{job.id}")
    missing = client.get("/jobs/nope")
    for response in (page, video, api, missing, client.get("/")):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value, (name, response.url)
    assert video.status_code == 206 and video.headers["accept-ranges"] == "bytes"
    assert page.headers["cache-control"] == "no-store"
    assert video.headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-store"


def test_pages_use_no_inline_scripts_or_styles(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    for text in (client.get("/").text, client.get(f"/jobs/{job.id}").text):
        assert " style=" not in text and "<style" not in text
        assert "<script>" not in text


def test_job_page_has_refresh_banner_and_empty_stall_notice(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=5, running=True)
    text = client.get(f"/jobs/{job.id}").text
    assert 'id="new-results"' in text and 'id="refresh-button"' in text
    assert "New results are ready" in text
    assert 'id="stalled"' in text and 'role="status"' in text


def test_failed_repair_job_offers_no_approve_command(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth")
    job.mark_stage("composite", "failed", error="repair failed")
    text = client.get(f"/jobs/{job.id}").text
    assert "--approve" not in text and "Failed" in text


def test_home_directory_is_hidden_in_errors(client, runs_dir, media):
    home = str(Path.home())
    job = make_job(runs_dir, media, "depth", done=3, failed=f"cannot read {home}/clips/a.mp4")
    text = client.get(f"/jobs/{job.id}").text
    assert home not in text and "cannot read ~/clips/a.mp4" in text
    job.path("state.json").write_text(f"[] {home}")
    broken = client.get(f"/jobs/{job.id}")
    assert home not in broken.text


@pytest.mark.parametrize(
    ("kwargs", "word"),
    [
        ({"done": 3, "failed": "boom"}, "status-failed"),
        ({"done": 3, "running": True}, "status-running"),
        ({}, "status-done"),
    ],
)
def test_header_badge_has_status_class(client, runs_dir, media, kwargs, word):
    job = make_job(runs_dir, media, "depth", **kwargs)
    assert f'class="badge {word}" id="job-status"' in client.get(f"/jobs/{job.id}").text


def test_interrupted_stage_says_stopped(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="interrupted")
    text = client.get(f"/jobs/{job.id}").text
    assert f"Stopped (Ctrl-C). Run <code>charswap resume {job.id}</code> to continue." in text
    assert "interrupted" not in text and "Fix the issue" not in text


def test_failed_job_page_slow_polls_for_a_resume(client, runs_dir, media):
    job = make_job(runs_dir, media, "depth", done=3, failed="boom")
    text = client.get(f"/jobs/{job.id}").text
    assert 'data-active="false"' in text and 'data-poll="slow"' in text


def test_viewer_script_polls_by_mode(client):
    script = client.get("/static/viewer.js").text
    assert "slow: 10000" in script and "fast: 3000" in script
    assert "data.generation" in script
    assert 'pollMode === "fast" && !generating' in script  # no stall notice while generating


def test_failed_draft_page_names_the_generate_command(client, runs_dir, media):
    job = make_job(runs_dir, media, "direct")
    job.mark_stage("draft", "failed", error="boom")
    text = client.get(f"/jobs/{job.id}").text
    assert f"Fix the issue, then run <code>charswap generate {job.id} --draft</code>" in text
    assert 'class="badge status-failed" id="job-status"' in text


def test_finished_wan_job_page(client, runs_dir, media):
    from viewer_jobs import finish_stage

    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    text = client.get(f"/jobs/{job.id}").text
    assert 'class="badge status-done" id="job-status">Finished' in text
    assert f"/jobs/{job.id}/files/final.mp4" in text and 'data-poll="off"' in text


def test_stale_final_page_badge(client, runs_dir, media):
    from viewer_jobs import finish_stage

    job = make_job(runs_dir, media, "direct")
    finish_stage(job, media, "draft")
    finish_stage(job, media, "final")
    job.mark_stage("final", fingerprint="old")
    text = client.get(f"/jobs/{job.id}").text
    assert 'class="badge status-failed" id="job-status">Out of date' in text
    assert f"charswap generate {job.id} --draft" in text
