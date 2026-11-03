import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from character_swap import cli
from character_swap.config import Settings
from character_swap.costs import Estimate, Line
from character_swap.jobs import Job
from character_swap.pipeline import PREP_STAGES_BY_MODE, Services
from character_swap.review import decide as approve_review
from character_swap.review import fingerprint as review_fingerprint
from character_swap.stages import StageError
from helpers import solid, write_video


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(str(text))

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def settings(tmp_path):
    return Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "t"})


@pytest.fixture
def video(tmp_path):
    return write_video(tmp_path / "in.mp4", [solid((9, 9, 9))] * 96, audio_seconds=4)


def run(argv, settings, **kw):
    out = Recorder()
    out.err = Recorder()
    code = cli.main(argv, settings=settings, out=out, err=out.err, **kw)
    return code, out


def test_prepare_declined_creates_nothing(settings, video):
    code, out = run(["prepare", str(video), "--mode", "depth"], settings, confirm=lambda _q: False)
    assert code == 1 and "Depth video" in out.text and "Cancelled" in out.err.text
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


def test_prepare_runs_pipeline_and_prints_job(settings, video, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: calls.append(job.id))
    code, out = run(
        ["prepare", str(video), "--mode", "depth", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and calls and f"JOB {calls[0]}" in out.text


def test_prepare_wan_needs_character(settings, video):
    code, out = run(["prepare", str(video), "--generator", "wan", "--yes"], settings)
    assert code == 2 and "character" in out.err.text.lower()


def test_prepare_wan_direct_needs_no_token(tmp_path, video, monkeypatch):
    empty = Settings.load(root=tmp_path, environ={})
    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: calls.append(job.id))
    code, _ = run(
        ["prepare", str(video), "--generator", "wan", "--character", str(character), "--yes"],
        empty,
        services_factory=lambda s: object(),
    )
    assert code == 0 and calls


def test_prepare_stage_error_is_friendly(settings, video, monkeypatch):
    def boom(job, services, status):
        raise StageError("Normalizing failed: too short")

    monkeypatch.setattr(cli, "prepare_job", boom)
    code, out = run(["prepare", str(video), "--yes"], settings, services_factory=lambda s: object())
    assert code == 2 and "too short" in out.err.text
    assert not any(settings.runs_dir.glob("spend-log.csv"))  # nothing logged on failure


def _finish_stages(job):
    for name in PREP_STAGES_BY_MODE[job.mode]:
        job.mark_stage(name)


def test_unfinished_preparation_cannot_be_approved(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
    assert json.loads(run(["status", job.id], settings)[1].text)["review"]["status"] == (
        "unavailable"
    )
    code, out = run(["review", job.id, "--approve", "--note", "x", "--fingerprint", "f"], settings)
    assert code == 2 and "Preparation isn't finished" in out.err.text


def test_status_review_prompt_flow(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
    _finish_stages(job)
    job.update(transcript="hi", scene="A room.")

    code, out = run(["status", job.id], settings)
    data = json.loads(out.text)
    assert code == 0 and data["review"]["status"] == "pending"

    fingerprint = data["review"]["fingerprint"]
    code, out = run(
        ["review", job.id, "--approve", "--note", "watched", "--fingerprint", fingerprint],
        settings,
    )
    assert code == 0 and "approved" in out.text

    code, out = run(["prompt", job.id], settings)
    assert code == 0 and "mesh" in out.text.lower() and '"hi"' in out.text


def test_review_reject(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
    _finish_stages(job)
    fingerprint = json.loads(run(["status", job.id], settings)[1].text)["review"]["fingerprint"]
    code, out = run(
        ["review", job.id, "--reject", "--note", "bad", "--fingerprint", fingerprint], settings
    )
    assert code == 0 and "rejected" in out.text


def test_review_stale_fingerprint_is_friendly(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
    _finish_stages(job)
    code, out = run(
        ["review", job.id, "--approve", "--note", "x", "--fingerprint", "stale"], settings
    )
    assert code == 2 and "changed" in out.err.text


def test_prompt_for_direct_job_is_friendly(settings, video, tmp_path):
    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    job = Job.create(settings.runs_dir, video, generator="wan", mode="direct", character=character)
    code, out = run(["prompt", job.id], settings)
    assert code == 2 and "seedance" in out.err.text.lower()


def test_errors_are_friendly(settings):
    code, out = run(["status", "0" * 32], settings)
    assert code == 2 and "No job" in out.err.text


def test_missing_key_is_reported(tmp_path, video):
    empty = Settings.load(root=tmp_path, environ={})
    code, out = run(["prepare", str(video), "--yes"], empty)
    assert code == 2 and "REPLICATE_API_TOKEN" in out.err.text


def test_resume_continues_existing_job(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    calls = []
    monkeypatch.setattr(cli, "prepare_job", lambda j, s, status: calls.append(j.id))
    code, out = run(["resume", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert code == 0 and calls == [job.id] and "Resuming" in out.text
    assert len([p for p in settings.runs_dir.iterdir() if p.is_dir()]) == 1  # no new job
    assert job.state["costs"][-1]["step"] == "prepare"  # spend logged once on completion


def test_resume_does_not_log_spend_twice(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    monkeypatch.setattr(cli, "prepare_job", lambda j, s, status: None)
    for _ in range(2):
        run(["resume", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert [c["step"] for c in job.state["costs"]] == ["prepare"]


def test_repair_calls_pipeline(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    called = []
    monkeypatch.setattr(cli, "repair_with_rgb", lambda j, s, status: called.append(j.id))
    code, _ = run(["repair", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert code == 0 and called == [job.id]
    assert job.state["costs"][-1]["step"] == "repair"


def test_repair_declined_does_nothing(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    monkeypatch.setattr(cli, "repair_with_rgb", lambda *a, **k: pytest.fail("must not run"))
    code, out = run(["repair", job.id], settings, confirm=lambda _q: False)
    assert code == 1 and "Cancelled" in out.err.text


def test_doctor_reports_checks(settings, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    code, out = run(["doctor"], settings)
    assert "rubberband" in out.text and "REPLICATE_API_TOKEN: set" in out.text
    assert "pyav (ffmpeg):" in out.text
    assert "PIAPI_API_KEY: missing" in out.text and "hosting: tmpfiles" in out.text
    assert code == 0


def test_doctor_never_prints_secret_values(tmp_path, monkeypatch):
    secret = Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "sekrit-value-123"})
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    code, out = run(["doctor"], secret)
    assert "sekrit-value-123" not in out.text
    assert code == 1 and "MISSING" in out.text


def _job(settings, video, mode="depth"):
    return Job.create(settings.runs_dir, video, generator="seedance", mode=mode)


def test_keyboard_interrupt_is_reported_with_job_id(settings, video, monkeypatch):
    def stop(job, services, status):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "prepare_job", stop)
    code, out = run(["prepare", str(video), "--yes"], settings, services_factory=lambda s: object())
    job_id = next(p.name for p in settings.runs_dir.iterdir() if p.is_dir())
    assert code == 130 and "Interrupted" in out.err.text
    assert f"charswap resume {job_id}" in out.err.text and "not charged again" in out.err.text


def test_unexpected_error_is_summarised(settings, video, monkeypatch):
    def bad(job, services, status):
        raise ValueError("kaboom")

    monkeypatch.setattr(cli, "prepare_job", bad)
    code, out = run(["prepare", str(video), "--yes"], settings, services_factory=lambda s: object())
    assert code == 1 and "Unexpected error: kaboom. Run with --debug for details." in out.err.text


def test_debug_reraises(settings, video, monkeypatch):
    def bad(job, services, status):
        raise ValueError("kaboom")

    monkeypatch.setattr(cli, "prepare_job", bad)
    with pytest.raises(ValueError):
        run(
            ["--debug", "prepare", str(video), "--yes"],
            settings,
            services_factory=lambda s: object(),
        )


def test_stage_error_prints_resume_hint(settings, video, monkeypatch):
    def fail(job, services, status):
        raise StageError("Depth failed: x")

    monkeypatch.setattr(cli, "prepare_job", fail)
    code, out = run(["prepare", str(video), "--yes"], settings, services_factory=lambda s: object())
    job_id = next(p.name for p in settings.runs_dir.iterdir() if p.is_dir())
    assert code == 2 and f"Fix the issue, then run: charswap resume {job_id}" in out.err.text


def test_repair_failure_hints_repair(settings, video, monkeypatch):
    job = _job(settings, video)

    def fail(j, s, status):
        raise StageError("Repair: masks failed")

    monkeypatch.setattr(cli, "repair_with_rgb", fail)
    code, out = run(["repair", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert code == 2 and f"Fix the issue, then run: charswap repair {job.id}" in out.err.text


def test_resume_asks_before_remaining_paid_steps(settings, video, monkeypatch):
    job = _job(settings, video)
    job.mark_stage("separate")
    monkeypatch.setattr(cli, "prepare_job", lambda *a, **k: pytest.fail("must not run"))
    code, out = run(["resume", job.id], settings, confirm=lambda _q: False)
    assert code == 1 and "Remaining paid steps: transcribe, describe, depth, masks" in out.text
    assert "up to $0.0438" in out.text and "Cancelled" in out.err.text


def test_resume_without_paid_work_does_not_ask(settings, video, monkeypatch):
    job = _job(settings, video, mode="face_mesh")
    for name in ("separate", "transcribe", "describe"):
        job.mark_stage(name)
    monkeypatch.setattr(cli, "prepare_job", lambda *a, **k: None)
    code, _ = run(
        ["resume", job.id],
        settings,
        confirm=lambda _q: pytest.fail("must not ask"),
        services_factory=lambda s: object(),
    )
    assert code == 0


def test_remaining_paid_stages_by_mode(settings, video):
    from character_swap.pipeline import remaining_paid_stages

    assert remaining_paid_stages(_job(settings, video, "face_mesh")) == [
        "separate",
        "transcribe",
        "describe",
    ]
    assert len(remaining_paid_stages(_job(settings, video, "depth_mesh"))) == 5


def test_remaining_paid_stages_direct_is_empty(settings, video, tmp_path):
    from character_swap.pipeline import remaining_paid_stages

    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    job = Job.create(settings.runs_dir, video, generator="wan", mode="direct", character=character)
    assert remaining_paid_stages(job) == []


def test_repair_rejects_unrepairable_mode_before_asking(settings, video):
    job = _job(settings, video, mode="face_mesh")
    code, out = run(["repair", job.id], settings, confirm=lambda _q: pytest.fail("must not ask"))
    assert code == 2 and "no SAM mask" in out.err.text


def test_ask_treats_eof_as_no(monkeypatch):
    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    err = Recorder()
    assert cli._ask("Continue?", err=err) is False
    assert "pass --yes" in err.text


def test_help_has_example_epilog(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    assert "charswap review <id> --approve" in text and "show stages and review" in text


@pytest.mark.parametrize("mode", ["depth", "face_mesh", "depth_mesh", "direct"])
def test_paid_stage_tables_agree(mode):
    from character_swap.costs import PREP_STEPS, STAGE_PRICE
    from character_swap.pipeline import PAID_STAGES

    assert [STAGE_PRICE[s] for s in PAID_STAGES[mode]] == PREP_STEPS[mode]


@pytest.mark.parametrize("mode", ["depth", "depth_mesh"])
def test_paid_stages_exact_for_depth_modes(mode):
    from character_swap.pipeline import PAID_STAGES

    assert PAID_STAGES[mode] == ["separate", "transcribe", "describe", "depth", "masks"]


def test_estimate_stages_matches_prepare_estimate():
    from character_swap.costs import estimate_prepare, estimate_stages
    from character_swap.pipeline import PAID_STAGES

    assert estimate_stages(PAID_STAGES["depth"], 30) == estimate_prepare("depth", 30)
    assert estimate_stages([], 30).total == 0


# ---- view ----------------------------------------------------------------------
def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeServer:
    def __init__(self, raise_on_start=None, wait_for=None):
        self.calls = []
        self.raise_on_start = raise_on_start
        self.wait_for = wait_for

    def __call__(self, app, *, host, port):
        self.calls.append({"app": app, "host": host, "port": port})
        if self.wait_for is not None:
            self.wait_for.wait(timeout=5)  # "running" until the browser was opened
        if self.raise_on_start:
            raise self.raise_on_start


def test_view_serves_the_app_on_localhost_only(settings):
    port = free_port()
    server = FakeServer()
    opened = []
    code, out = run(
        ["view", "--port", str(port), "--no-browser"],
        settings,
        serve=server,
        open_browser=opened.append,
    )
    assert code == 0 and opened == []
    [call] = server.calls
    assert call["host"] == "127.0.0.1" and call["port"] == port
    assert f"Job viewer: http://127.0.0.1:{port}" in out.text and "Viewer stopped." in out.text
    page = TestClient(call["app"], base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and "No jobs yet" in page.text


def test_view_has_no_host_option(settings):
    with pytest.raises(SystemExit):
        run(["view", "--host", "0.0.0.0", "--no-browser"], settings, serve=FakeServer())


def test_view_port_check_binds_localhost_only(settings, monkeypatch):
    binds = []

    class SpySocket:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def setsockopt(self, *args):
            pass

        def settimeout(self, seconds):
            pass

        def connect_ex(self, address):
            binds.append(address)
            return 1  # nothing listening

        def bind(self, address):
            binds.append(address)

    monkeypatch.setattr(cli.socket, "socket", SpySocket)
    assert cli._port_free(8123) is True
    assert binds == [("127.0.0.1", 8123)] * 2  # connect probe, then bind


def test_view_default_port(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    server = FakeServer()
    code, _ = run(["view", "--no-browser"], settings, serve=server)
    assert code == 0 and server.calls[0]["port"] == 8770


def test_view_opens_the_browser_once_the_server_runs(settings, monkeypatch):
    monkeypatch.setattr(cli, "BROWSER_DELAY", 0)
    port = free_port()
    opened = threading.Event()
    urls = []

    def open_browser(url):
        urls.append(url)
        opened.set()

    code, _ = run(
        ["view", "--port", str(port)],
        settings,
        serve=FakeServer(wait_for=opened),
        open_browser=open_browser,
    )
    assert code == 0 and urls == [f"http://127.0.0.1:{port}"]


def test_view_does_not_open_the_browser_if_the_server_fails(settings, monkeypatch):
    monkeypatch.setattr(cli, "BROWSER_DELAY", 0.2)
    urls = []
    with pytest.raises(OSError):
        run(
            ["--debug", "view", "--port", str(free_port())],
            settings,
            serve=FakeServer(raise_on_start=OSError("bind failed")),
            open_browser=urls.append,
        )
    time.sleep(0.4)
    assert urls == []


def test_view_ctrl_c_stops_cleanly(settings):
    # real uvicorn normally handles SIGINT itself and returns, so "Viewer stopped." prints via
    # the normal path; this test covers the fallback.
    server = FakeServer(raise_on_start=KeyboardInterrupt())
    code, out = run(["view", "--port", str(free_port()), "--no-browser"], settings, serve=server)
    assert code == 0 and "Viewer stopped." in out.text
    assert "Interrupted" not in out.err.text and "resume" not in out.err.text


def test_view_bind_failure_at_startup_is_explained(settings, monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda port: True)  # lost the race after the check
    server = FakeServer(raise_on_start=SystemExit(1))  # what real uvicorn raises on bind failure
    code, out = run(["view", "--port", "8771", "--no-browser"], settings, serve=server)
    assert code == 2 and "Viewer stopped." not in out.text
    assert "Port 8771 is busy; try `charswap view --port 8772`" in out.err.text


def test_port_free_sees_a_wildcard_listener():
    with socket.socket() as wide:
        wide.bind(("0.0.0.0", 0))
        wide.listen()
        assert cli._port_free(wide.getsockname()[1]) is False


def test_view_busy_port_is_explained(settings):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        server = FakeServer()
        code, out = run(["view", "--port", str(port), "--no-browser"], settings, serve=server)
    assert code == 2 and server.calls == []
    assert f"Port {port} is busy; try `charswap view --port {port + 1}`" in out.err.text


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_view_rejects_bad_ports(settings, port):
    with pytest.raises(SystemExit):
        run(["view", "--port", port], settings, serve=FakeServer())


def test_help_lists_view(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "open the read-only job viewer" in capsys.readouterr().out


def test_provider_errors_are_friendly(settings, video, monkeypatch):
    from character_swap.providers.replicate import ReplicateError

    def throttled(job, services, status):
        raise ReplicateError("Rate limited, nothing was charged; wait a minute and retry.")

    monkeypatch.setattr(cli, "prepare_job", throttled)
    code, out = run(["prepare", str(video), "--yes"], settings, services_factory=lambda s: object())
    assert code == 2 and "Error: Rate limited, nothing was charged" in out.err.text


def test_prompt_errors_are_friendly(settings, video, monkeypatch):
    from character_swap.prompts import PromptError

    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")

    def too_long(**kwargs):
        raise PromptError("Prompt is 5000 characters; the limit is 4000.")

    monkeypatch.setattr(cli, "build_prompt", too_long)
    code, out = run(["prompt", job.id], settings)
    assert code == 2 and "the limit is 4000" in out.err.text


def test_rate_limit_wait_message():
    from character_swap.cli import rate_limit_message

    assert rate_limit_message(7.4, 1, 3) == (
        "Replicate is busy (rate limit); waiting 7 s before retrying (1/3). Nothing charged yet."
    )


def test_prepare_passes_the_language(settings, video, monkeypatch):
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: None)
    code, _ = run(
        ["prepare", str(video), "--mode", "depth", "--language", "en", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    job_id = next(p.name for p in settings.runs_dir.iterdir() if p.is_dir())
    assert code == 0 and Job.load(settings.runs_dir, job_id).state["language"] == "en"


@pytest.mark.parametrize("language", ["eng", "zz", "English"])
def test_prepare_rejects_an_unknown_language_before_the_cost_question(
    settings, video, language, capsys
):
    asked = []
    with pytest.raises(SystemExit) as exit_info:
        run(
            ["prepare", str(video), "--mode", "depth", "--language", language],
            settings,
            confirm=lambda q: asked.append(q) or True,
        )
    assert exit_info.value.code == 2 and asked == []
    assert "--language" in capsys.readouterr().err
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


# ---- generate (Plan 2a) ----------------------------------------------------------
class FakeWan:
    name, provider, model = "wan", "replicate", "fake/wan"

    def estimate(self, request, stage):
        return Estimate(lines=(Line(f"Wan {stage}", 0.08),))


def wan_services(_settings):
    return Services(replicate=None, face_detector=lambda: None, generators={"wan": FakeWan()})


def _wan_job(settings, video, tmp_path):
    face = tmp_path / "face.png"
    face.write_bytes(b"x")
    return Job.create(settings.runs_dir, video, generator="wan", mode="direct", character=face)


def _approved_wan_job(settings, video, tmp_path):
    job = _wan_job(settings, video, tmp_path)
    job.path("original.mp4").write_bytes(b"normalized")
    job.mark_stage("normalize")
    job.update(video={"frames": 96, "fps": 24, "width": 64, "height": 64, "seconds": 4.0})
    approve_review(job, approved=True, note="watched all", fingerprint=review_fingerprint(job))
    return job


def _mark_draft_done(job):
    """A finished draft made from the currently approved inputs."""
    job.mark_stage("draft", fingerprint=review_fingerprint(job))


def _fake_generate(seen):
    def fake(job, services, stage, *, runs_dir, status):
        seen.append(stage)
        return job.path(f"{stage}.mp4")

    return fake


def test_generate_draft_shows_the_cost_and_can_be_declined(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda _q: False,
        services_factory=wan_services,
    )
    assert code == 1 and "Wan draft" in out.text and "$0.0800" in out.text
    assert "Cancelled. Nothing was charged." in out.err.text and seen == []


def test_generate_draft_runs_after_confirmation(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda _q: True,
        services_factory=wan_services,
    )
    assert code == 0 and seen == ["draft"]
    assert f"charswap generate {job.id} --final" in out.text


def test_generate_needs_approval_before_any_cost_question(settings, video, tmp_path):
    job = _wan_job(settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 2 and asked == [] and "Review required" in out.err.text


def test_generate_resume_does_not_ask_again(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p", "status": "processing"}))
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 0 and asked == [] and seen == ["draft"] and "no new charge" in out.text


def test_generate_on_an_unconfirmed_marker_gives_guidance_not_comfort(
    settings, video, tmp_path, monkeypatch
):
    job = _approved_wan_job(settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"status": "creating"}))
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert asked == [] and "no new charge" not in out.text
    assert (
        "A request for this step was sent but never confirmed. It may have been charged; "
        "follow the steps below before running again."
    ) in out.text


def test_generate_refuses_a_clip_whose_sound_cannot_be_restored(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    job.update(video={"frames": 48, "fps": 24, "width": 64, "height": 64, "seconds": 2.0})
    asked = []  # the upload has 4 s of sound, the clip 2 s of picture
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 2 and asked == [] and "soundtrack" in out.err.text
    assert "costs" not in job.state


def test_repair_ctrl_c_does_not_promise_a_free_rerun(settings, video, monkeypatch):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "repair_with_rgb", stop)
    code, out = run(["repair", job.id, "--yes"], settings, services_factory=lambda s: object())
    assert code == 130 and f"charswap resume {job.id}" in out.err.text
    assert "not charged again" not in out.err.text and "buys a new SAM 3 cut" in out.err.text


def test_generate_final_reminds_to_watch_the_draft(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    _mark_draft_done(job)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--final", "--yes"], settings, services_factory=wan_services
    )
    assert code == 0 and seen == ["final"]
    assert "Wan final" in out.text and str(job.path("draft.mp4")) in out.text


def test_generate_finished_stage_is_a_no_op(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    _mark_draft_done(job)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(["generate", job.id, "--draft"], settings, services_factory=wan_services)
    assert code == 0 and seen == [] and "already done" in out.text


def test_generate_failure_hints_the_generate_command(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)

    def boom(*args, **kwargs):
        raise StageError("Generating the 480p draft failed: boom")

    monkeypatch.setattr(cli, "generate_stage", boom)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=wan_services
    )
    assert code == 2 and f"charswap generate {job.id} --draft" in out.err.text


def test_generate_ctrl_c_hints_the_generate_command(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "generate_stage", stop)
    code, out = run(
        ["generate", job.id, "--final", "--yes"], settings, services_factory=wan_services
    )
    assert code == 2 and "draft first" in out.err.text  # no draft yet: refused before Ctrl-C
    _mark_draft_done(job)
    code, out = run(
        ["generate", job.id, "--final", "--yes"], settings, services_factory=wan_services
    )
    assert code == 130 and f"charswap generate {job.id} --final" in out.err.text


def test_generate_needs_the_replicate_token(tmp_path, video):
    empty = Settings.load(root=tmp_path, environ={})
    job = _approved_wan_job(empty, video, tmp_path)
    code, out = run(["generate", job.id, "--draft"], empty, services_factory=wan_services)
    assert code == 2 and "REPLICATE_API_TOKEN" in out.err.text


def test_seedance_jobs_cannot_generate_yet(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    code, out = run(["generate", job.id, "--draft"], settings)
    assert code == 2 and "next version" in out.err.text


def test_status_says_what_to_do_next(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    data = json.loads(run(["status", job.id], settings)[1].text)
    assert data["next"] == f"charswap generate {job.id} --draft"


def test_prepare_wan_points_at_the_normalized_clip(tmp_path, video, monkeypatch):
    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: None)
    code, out = run(
        ["prepare", str(video), "--generator", "wan", "--character", str(character), "--yes"],
        Settings.load(root=tmp_path, environ={}),
        services_factory=lambda s: object(),
    )
    assert code == 0 and "original.mp4" in out.text and "prepared.mp4" not in out.text


def test_help_lists_generate(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    assert "generate" in text and "charswap generate <id> --draft" in text


def test_real_services_reach_the_wan_generator(settings):
    from character_swap.providers.generators.wan_replicate import WanGenerator

    services = cli.build_services(settings)
    assert isinstance(services.generators["wan"], WanGenerator)
