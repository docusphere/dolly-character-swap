import json
import socket
import threading
import time

import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from character_swap import cli
from character_swap.config import Settings
from character_swap.costs import Estimate, Line
from character_swap.faces import FaceLibrary
from character_swap.jobs import Job
from character_swap.pipeline import PREP_STAGES_BY_MODE, Services
from character_swap.review import decide as approve_review
from character_swap.review import fingerprint as review_fingerprint
from character_swap.stages import StageError
from fakes import FakePiAPI, FakeWaveSpeed
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


def test_prepare_declined_creates_nothing(tmp_path, settings, video):
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--mode", "depth"],
        settings,
        confirm=lambda _q: False,
    )
    assert code == 1 and "Depth video" in out.text and "Cancelled" in out.err.text
    assert not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


def test_prepare_runs_pipeline_and_prints_job(tmp_path, settings, video, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: calls.append(job.id))
    code, out = run(
        [
            "prepare",
            str(video),
            "--character",
            str(_photo(tmp_path / "c.jpg")),
            "--mode",
            "depth",
            "--yes",
        ],
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


def test_prepare_stage_error_is_friendly(tmp_path, settings, video, monkeypatch):
    def boom(job, services, status):
        raise StageError("Normalizing failed: too short")

    monkeypatch.setattr(cli, "prepare_job", boom)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
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
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--yes"], empty
    )
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
    assert "PIAPI_API_KEY: missing" in out.text
    assert "hosting: PiAPI temporary upload" in out.text
    assert code == 0


def test_doctor_never_prints_secret_values(tmp_path, monkeypatch):
    secret = Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "sekrit-value-123"})
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    code, out = run(["doctor"], secret)
    assert "sekrit-value-123" not in out.text
    assert code == 1 and "MISSING" in out.text


def _job(settings, video, mode="depth"):
    return Job.create(settings.runs_dir, video, generator="seedance", mode=mode)


def test_keyboard_interrupt_is_reported_with_job_id(tmp_path, settings, video, monkeypatch):
    def stop(job, services, status):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "prepare_job", stop)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    job_id = next(p.name for p in settings.runs_dir.iterdir() if p.is_dir())
    assert code == 130 and "Interrupted" in out.err.text
    assert f"charswap resume {job_id}" in out.err.text and "not charged again" in out.err.text


def test_unexpected_error_is_summarised(tmp_path, settings, video, monkeypatch):
    def bad(job, services, status):
        raise ValueError("kaboom")

    monkeypatch.setattr(cli, "prepare_job", bad)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 1 and "Unexpected error: kaboom. Run with --debug for details." in out.err.text


def test_debug_reraises(tmp_path, settings, video, monkeypatch):
    def bad(job, services, status):
        raise ValueError("kaboom")

    monkeypatch.setattr(cli, "prepare_job", bad)
    with pytest.raises(ValueError):
        run(
            [
                "--debug",
                "prepare",
                str(video),
                "--character",
                str(_photo(tmp_path / "c.jpg")),
                "--yes",
            ],
            settings,
            services_factory=lambda s: object(),
        )


def test_stage_error_prints_resume_hint(tmp_path, settings, video, monkeypatch):
    def fail(job, services, status):
        raise StageError("Depth failed: x")

    monkeypatch.setattr(cli, "prepare_job", fail)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
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


def test_provider_errors_are_friendly(tmp_path, settings, video, monkeypatch):
    from character_swap.providers.replicate import ReplicateError

    def throttled(job, services, status):
        raise ReplicateError("Rate limited, nothing was charged; wait a minute and retry.")

    monkeypatch.setattr(cli, "prepare_job", throttled)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 2 and "Error: Rate limited, nothing was charged" in out.err.text


def test_prompt_errors_are_friendly(settings, video):
    from character_swap.prompts import MAX_PROMPT

    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    for stage in _MODES["depth"].prep_stages:
        job.mark_stage(stage)
    job.path("prompt.txt").write_text("Put @Image 1 into @Video 1. " + "x" * MAX_PROMPT)
    code, out = run(["prompt", job.id], settings)
    assert code == 2 and "limit" in out.err.text


def test_rate_limit_wait_message():
    from character_swap.cli import rate_limit_message

    assert rate_limit_message(7.4, 1, 3) == (
        "Replicate is busy (rate limit); waiting 7 s before retrying (1/3). Nothing charged yet."
    )


def test_prepare_passes_the_language(tmp_path, settings, video, monkeypatch):
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: None)
    code, _ = run(
        [
            "prepare",
            str(video),
            "--character",
            str(_photo(tmp_path / "c.jpg")),
            "--mode",
            "depth",
            "--language",
            "en",
            "--yes",
        ],
        settings,
        services_factory=lambda s: object(),
    )
    job_id = next(p.name for p in settings.runs_dir.iterdir() if p.is_dir())
    assert code == 0 and Job.load(settings.runs_dir, job_id).state["language"] == "en"


@pytest.mark.parametrize("language", ["eng", "zz", "English"])
def test_prepare_rejects_an_unknown_language_before_the_cost_question(
    tmp_path, settings, video, language, capsys
):
    asked = []
    with pytest.raises(SystemExit) as exit_info:
        run(
            [
                "prepare",
                str(video),
                "--character",
                str(_photo(tmp_path / "c.jpg")),
                "--mode",
                "depth",
                "--language",
                language,
            ],
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


def _interrupted_draft(job):
    """What a stopped draft run leaves in state: not done, its approved inputs recorded."""
    job.mark_stage("draft", "failed", fingerprint=review_fingerprint(job))


def _fake_generate(seen):
    def fake(job, services, stage, *, runs_dir, status, final_resolution=""):
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
    _interrupted_draft(job)
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
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 2 and asked == [] and seen == [] and "no new charge" not in out.text
    assert (
        "A request for this step was sent but never confirmed. It may have been charged; "
        "follow the steps below before running again."
    ) in out.text
    assert "replicate.com/predictions" in out.err.text and str(record) in out.err.text
    assert "draft" not in job.state["stages"]  # not marked failed


def test_generate_unconfirmed_marker_end_to_end_makes_no_request(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    with respx.mock(assert_all_called=False):  # no routes: any request would fail the test
        code, out = run(["generate", job.id, "--draft"], settings, confirm=asked.append)
    assert code == 2 and asked == []
    assert "replicate.com/predictions" in out.err.text and str(record) in out.err.text
    assert json.loads(record.read_text()) == {"status": "creating"}
    assert "draft" not in job.state["stages"]  # not marked failed


def test_generate_after_a_failed_prediction_does_not_promise_a_free_resume(
    settings, video, tmp_path, monkeypatch
):
    job = _approved_wan_job(settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "p", "status": "failed"}))
    _interrupted_draft(job)
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert asked == [] and seen == ["draft"] and "no new charge" not in out.text


def _stale_draft(job):
    for name, data in [
        ("draft-prediction.json", b'{"id": "old", "status": "succeeded"}'),
        ("draft-raw.mp4", b"old raw"),
        ("draft.mp4", b"old draft"),
    ]:
        job.path(name).write_bytes(data)
    job.mark_stage("draft", fingerprint="an older approval")


def test_stale_draft_can_be_remade_after_a_new_cost_question(
    settings, video, tmp_path, monkeypatch
):
    job = _approved_wan_job(settings, video, tmp_path)
    _stale_draft(job)
    status = json.loads(run(["status", job.id], settings)[1].text)
    assert status["next"] == f"charswap generate {job.id} --draft"
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: asked.append(q) or True,
        services_factory=wan_services,
    )
    assert code == 0 and asked and seen == ["draft"]
    assert "approved inputs changed" in out.text and "$0.0800" in out.text
    kept = sorted(p.name for p in job.root.glob("draft-old-*"))
    assert len(kept) == 3
    assert sorted(job.path(n).read_bytes() for n in kept) == [
        b"old draft",
        b"old raw",
        (b'{"id": "old", "status": "succeeded"}'),
    ]
    assert not job.path("draft-raw.mp4").exists() and "draft" not in job.state["stages"]


def test_declining_a_stale_draft_remake_changes_nothing(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    _stale_draft(job)
    before = json.dumps(job.state["stages"], sort_keys=True)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda _q: False,
        services_factory=wan_services,
    )
    assert code == 1 and seen == [] and "Nothing was charged" in out.err.text
    assert job.path("draft-raw.mp4").read_bytes() == b"old raw"
    assert job.path("draft.mp4").read_bytes() == b"old draft"
    assert not list(job.root.glob("draft-old-*"))
    assert json.dumps(job.state["stages"], sort_keys=True) == before


def test_generate_refusals_do_not_hint_the_wrong_command(settings, video, tmp_path):
    job = _approved_wan_job(settings, video, tmp_path)
    code, out = run(["generate", job.id, "--final"], settings, services_factory=wan_services)
    assert code == 2 and f"charswap generate {job.id} --draft" in out.err.text
    assert "--final" not in out.err.text and "Fix the issue" not in out.err.text
    seedance = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    code, out = run(["generate", seedance.id, "--draft"], settings)
    assert code == 2 and "Fix the issue" not in out.err.text


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


def _stale_final(job):
    """A finished final from older inputs; the draft's files were removed by hand."""
    job.mark_stage("draft", fingerprint=review_fingerprint(job))
    job.path("final.mp4").write_bytes(b"old final")
    job.mark_stage("final", fingerprint="an older approval")


def test_stale_final_is_not_presented_as_done(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    _stale_final(job)
    asked, seen = [], []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--final"],
        settings,
        confirm=asked.append,
        services_factory=wan_services,
    )
    assert code == 2 and asked == [] and seen == [] and "already done" not in out.text
    assert (
        "This final was made from inputs that are no longer approved; run "
        f"`charswap generate {job.id} --draft` to make a new draft first."
    ) in out.err.text
    status = json.loads(run(["status", job.id], settings)[1].text)
    assert status["next"] == f"charswap generate {job.id} --draft"


def test_stale_final_is_remade_from_a_new_draft(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    _stale_final(job)
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=wan_services
    )
    assert code == 0 and seen == ["draft"] and "already done" not in out.text
    assert [p.read_bytes() for p in job.root.glob("final-old-*")] == [b"old final"]
    assert "final" not in job.state["stages"]


def _wan_args(video, character, *extra):
    return ["prepare", str(video), "--generator", "wan", "--character", str(character), *extra]


def test_prepare_wan_direct_is_free_asks_nothing_and_logs_no_cost(tmp_path, video, monkeypatch):
    empty = Settings.load(root=tmp_path, environ={})
    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    ids = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: ids.append(job.id))

    def never(_q):
        raise AssertionError("must not ask")

    code, out = run(
        _wan_args(video, character), empty, confirm=never, services_factory=lambda s: object()
    )
    assert code == 0 and "free for this mode" in out.text
    assert Job.load(empty.runs_dir, ids[0]).state.get("costs", []) == []


def test_prepare_seedance_still_asks(tmp_path, settings, video):
    asked = []
    code, _ = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "c.jpg")), "--mode", "depth"],
        settings,
        confirm=lambda q: asked.append(q) or False,
    )
    assert code == 1 and asked


def test_prepare_language_rejected_for_direct_mode(tmp_path, video):
    empty = Settings.load(root=tmp_path, environ={})
    character = tmp_path / "face.png"
    character.write_bytes(b"x")
    asked = []
    code, out = run(
        _wan_args(video, character, "--language", "es"),
        empty,
        confirm=lambda q: asked.append(q) or True,
    )
    assert code == 2 and "--language only matters for Seedance modes" in out.err.text
    assert not asked and not empty.runs_dir.exists()


def _photo(path):
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(path, "JPEG")
    return path


def test_face_add_shows_consent_asks_and_saves(settings, tmp_path):
    asked = []
    code, out = run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda q: asked.append(q) or True,
        piapi_factory=lambda s: FakePiAPI(),
    )
    assert code == 0 and asked and "right to use" in out.text
    assert "charswap prepare clip.mp4 --face frank" in out.text
    assert FaceLibrary(settings.root).get("frank").asset_id == "asset-1"


def test_face_add_declined_uploads_nothing(settings, tmp_path):
    piapi = FakePiAPI()
    code, out = run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda _q: False,
        piapi_factory=lambda s: piapi,
    )
    assert code == 1 and piapi.uploads == [] and "Nothing was uploaded" in out.err.text


def test_face_list_and_check(settings, tmp_path):
    piapi = FakePiAPI()
    run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg")), "--yes"],
        settings,
        piapi_factory=lambda s: piapi,
    )
    code, out = run(["face", "list"], settings)
    assert code == 0 and "frank" in out.text and "Active" in out.text
    piapi.assets.clear()
    code, out = run(["face", "check", "frank"], settings, piapi_factory=lambda s: piapi)
    assert code == 2 and "charswap face renew frank" in out.text


def test_face_list_when_empty(settings):
    code, out = run(["face", "list"], settings)
    assert code == 0 and "No saved faces yet" in out.text


def test_face_commands_need_the_piapi_key(settings, tmp_path):
    code, out = run(["face", "add", "frank", str(_photo(tmp_path / "me.jpg")), "--yes"], settings)
    assert code == 2 and "PIAPI_API_KEY" in out.err.text


def test_face_add_ctrl_c_gives_a_face_hint(settings, tmp_path):
    class Interrupted(FakePiAPI):
        def get_asset(self, asset_id):
            raise KeyboardInterrupt

    code, out = run(
        ["face", "add", "frank", str(_photo(tmp_path / "me.jpg")), "--yes"],
        settings,
        piapi_factory=lambda s: Interrupted(),
    )
    assert code == 130 and "charswap face check frank" in out.err.text


def test_face_add_bad_name_fails_before_consent_and_client(settings, tmp_path):
    def boom(_s):
        raise AssertionError("client built")

    asked = []
    code, out = run(
        ["face", "add", "Bad Name", str(_photo(tmp_path / "me.jpg"))],
        settings,
        confirm=lambda q: asked.append(q) or True,
        piapi_factory=boom,
    )
    assert code == 2 and not asked and "lowercase" in out.err.text


def _saved_face(settings, tmp_path, name="frank"):
    return FaceLibrary(settings.root, sleep=lambda _s: None).add(
        name, _photo(tmp_path / f"{name}.jpg"), FakePiAPI()
    )


def _prepare_ids(monkeypatch):
    ids = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: ids.append(job.id))
    return ids


def test_prepare_seedance_with_a_saved_face(settings, video, tmp_path, monkeypatch):
    _saved_face(settings, tmp_path)
    ids = _prepare_ids(monkeypatch)
    code, _ = run(
        ["prepare", str(video), "--mode", "depth", "--face", "frank", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["face_primary"] == "frank"
    assert state["character_primary"] == "character-primary.jpg"


def test_prepare_seedance_needs_a_character(settings, video):
    code, out = run(["prepare", str(video), "--mode", "depth", "--yes"], settings)
    assert code == 2 and "--face NAME" in out.err.text and "--character IMAGE" in out.err.text


def test_prepare_refuses_face_and_character_together(settings, video, tmp_path):
    _saved_face(settings, tmp_path)
    code, out = run(
        [
            "prepare",
            str(video),
            "--face",
            "frank",
            "--character",
            str(_photo(tmp_path / "c.jpg")),
            "--yes",
        ],
        settings,
    )
    assert code == 2 and "not both" in out.err.text


def test_prepare_refuses_a_face_that_is_not_active(settings, video, tmp_path):
    lib = FaceLibrary(settings.root, sleep=lambda _s: None, polls=0)
    lib.add("frank", _photo(tmp_path / "me.jpg"), FakePiAPI(assets={"asset-1": "Processing"}))
    code, out = run(["prepare", str(video), "--face", "frank", "--yes"], settings)
    assert code == 2 and "charswap face check frank" in out.err.text


def test_prepare_seedance_character_image_is_checked_and_warned(
    settings, video, tmp_path, monkeypatch
):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        ["prepare", str(video), "--character", str(_photo(tmp_path / "toon.png")), "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and "not a real person" in out.text
    assert Job.load(settings.runs_dir, ids[0]).state["character_primary"] == "character-primary.jpg"


def test_prepare_two_people(settings, video, tmp_path, monkeypatch):
    _saved_face(settings, tmp_path, "frank")
    _saved_face(settings, tmp_path, "sam")
    ids = _prepare_ids(monkeypatch)
    code, _ = run(
        [
            "prepare",
            str(video),
            "--face",
            "frank",
            "--face2",
            "sam",
            "--subject",
            "two men",
            "--yes",
        ],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["face_secondary"] == "sam" and state["subject"] == "two men"


def test_prepare_wan_refuses_a_second_character(settings, video, tmp_path):
    code, out = run(
        _wan_args(
            video, _photo(tmp_path / "a.jpg"), "--character2", str(_photo(tmp_path / "b.jpg"))
        ),
        settings,
    )
    assert code == 2 and "one character" in out.err.text


def _processing_face(settings, tmp_path):
    lib = FaceLibrary(settings.root, sleep=lambda _s: None, polls=0)
    return lib.add(
        "frank", _photo(tmp_path / "me.jpg"), FakePiAPI(assets={"asset-1": "Processing"})
    )


def test_wan_face_only_needs_to_exist_not_be_active(settings, video, tmp_path, monkeypatch):
    face = _processing_face(settings, tmp_path)
    ids = _prepare_ids(monkeypatch)
    code, _ = run(
        ["prepare", str(video), "--generator", "wan", "--face", "frank", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    job = Job.load(settings.runs_dir, ids[0])
    assert code == 0 and job.state["character_primary"] == "character-primary.jpg"
    library_image = FaceLibrary(settings.root).image_path(face)
    assert job.path("character-primary.jpg").read_bytes() == library_image.read_bytes()


def test_seedance_still_refuses_a_processing_face(settings, video, tmp_path):
    _processing_face(settings, tmp_path)
    code, out = run(["prepare", str(video), "--face", "frank", "--yes"], settings)
    assert code == 2 and "charswap face check frank" in out.err.text


def test_real_person_note_prints_once_for_two_characters(settings, video, tmp_path, monkeypatch):
    _prepare_ids(monkeypatch)
    code, out = run(
        [
            "prepare",
            str(video),
            "--character",
            str(_photo(tmp_path / "a.png")),
            "--character2",
            str(_photo(tmp_path / "b.png")),
            "--yes",
        ],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and out.text.count("not a real person") == 1


from character_swap.modes import MODES as _MODES  # noqa: E402


def _approved_seedance_job(settings, video, tmp_path):
    _saved_face(settings, tmp_path)
    library = FaceLibrary(settings.root)
    job = Job.create(
        settings.runs_dir,
        video,
        generator="seedance",
        mode="depth",
        character=library.image_path(library.get("frank")),
        faces={"primary": "frank"},
    )
    for name in _MODES["depth"].review_files:
        write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in _MODES["depth"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="hello there", scene="A studio.")
    approve_review(job, approved=True, note="ok", fingerprint=review_fingerprint(job))
    return job


def seedance_services(settings):
    """The real Seedance generator on a fake PiAPI that returns a real 4 s video."""
    from character_swap.providers.generators.seedance_piapi import SeedanceGenerator

    output = write_video(settings.root / "seedance-out.mp4", [solid((20, 200, 20))] * 96)
    piapi = FakePiAPI(files={"https://cdn.piapi.ai/out.mp4": output}, assets={"asset-1": "Active"})
    seedance_services.last = piapi
    return Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:piapi": SeedanceGenerator(piapi)},
        faces=FaceLibrary(settings.root),
    )


@pytest.fixture
def piapi_settings(tmp_path):
    return Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "t", "PIAPI_API_KEY": "k"})


def test_seedance_draft_shows_the_prompt_and_the_price_then_asks(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=lambda q: asked.append(q) or False,
        services_factory=seedance_services,
    )
    assert code == 1 and asked and seedance_services.last.bodies == []
    assert "hello there" in out.text and f"charswap prompt {job.id}" in out.text
    assert "Seedance draft (480p)" in out.text and "~$0.9900" in out.text  # 4 s: 0.66 + 0.33
    assert out.text.index("hello there") < out.text.index("~$0.9900")


def test_seedance_draft_sends_the_prompt_it_showed(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").write_text("Put @image1 into @video1, gently.")
    code, out = run(
        ["generate", job.id, "--draft", "--yes"],
        piapi_settings,
        services_factory=seedance_services,
    )
    assert code == 0 and "Put @image1 into @video1, gently." in out.text
    (body,) = seedance_services.last.bodies
    assert body["input"]["prompt"] == "Put @image1 into @video1, gently."


def test_seedance_remake_shows_the_new_prompt_not_the_stale_one(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    _stale_draft(job)
    job.mark_stage("draft", fingerprint="an older approval", prompt="OLD @image1 @video1")
    job.path("prompt.txt").write_text("NEW @image1 into @video1.")
    code, out = run(
        ["generate", job.id, "--draft", "--yes"],
        piapi_settings,
        services_factory=seedance_services,
    )
    assert code == 0 and "NEW @image1 into @video1." in out.text and "OLD @image1" not in out.text
    (body,) = seedance_services.last.bodies
    assert body["input"]["prompt"] == "NEW @image1 into @video1."


def test_seedance_generate_needs_the_piapi_key(settings, video, tmp_path):
    job = _approved_seedance_job(settings, video, tmp_path)
    code, out = run(["generate", job.id, "--draft"], settings, services_factory=seedance_services)
    assert code == 2 and "PIAPI_API_KEY" in out.err.text


def test_seedance_unconfirmed_marker_points_at_piapi(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and seedance_services.last.bodies == []
    assert "PiAPI" in out.err.text and "replicate.com" not in out.err.text
    assert str(record) in out.err.text


def test_resolution_only_with_final(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft", "--resolution", "720p"],
        piapi_settings,
        confirm=asked.append,
        services_factory=lambda s: pytest.fail("no services before the refusal"),
    )
    assert code == 2 and "only for --final" in out.err.text and asked == []


def test_after_the_draft_both_final_prices_are_shown(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    assert code == 0 and job.stage_done("draft")
    assert "1080p upgrade (~$5.28)" in out.text and "--resolution 720p" in out.text
    assert "(~$2.31)" in out.text


def test_after_the_draft_a_missing_face_only_drops_the_prices(
    piapi_settings, video, tmp_path, monkeypatch
):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    original = cli._after_draft_hint

    def hint(job, services):
        (services.faces.folder / "faces.json").write_text("[]")  # the face left the library
        return original(job, services)

    monkeypatch.setattr(cli, "_after_draft_hint", hint)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    assert code == 0 and f"Run `charswap generate {job.id} --final`." in out.text


def test_seedance_1080p_final_is_the_default_promote(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    code, out = run(
        ["generate", job.id, "--final"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and "Seedance final (1080p)" in out.text and "~$5.2800" in out.text
    assert "fresh run" not in out.text and seedance_services.last.bodies == []


def test_seedance_720p_final_warns_it_is_a_fresh_run(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "720p"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and "fresh run" in out.text and "Seedance final (720p)" in out.text
    assert seedance_services.last.bodies == []


def test_seedance_final_resume_never_asks_or_charges(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    job.path("final-prediction.json").write_text(json.dumps({"id": "t", "status": "succeeded"}))
    job.mark_stage("final", "failed", fingerprint=review_fingerprint(job), resolution="720p")
    asked = []
    code, out = run(
        ["generate", job.id, "--final"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text
    assert seedance_services.last.bodies == []


def test_prompt_writes_an_editable_file(settings, video, tmp_path):
    job = _approved_seedance_job(settings, video, tmp_path)
    code, out = run(["prompt", job.id], settings)
    assert code == 0 and job.path("prompt.txt").exists()
    assert "hello there" in out.text and "prompt.txt" in out.text
    job.path("prompt.txt").write_text("Put @image1 into @video1.")
    assert "Put @image1 into @video1." in run(["prompt", job.id], settings)[1].text


def test_prompt_waits_for_preparation(settings, video, tmp_path):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="depth")
    code, out = run(["prompt", job.id], settings)
    assert code == 2 and "charswap resume" in out.err.text and not job.path("prompt.txt").exists()
    assert "provider" not in job.state  # a refused prompt saves nothing


def test_doctor_reports_piapi_hosting_and_faces(settings):
    code, out = run(["doctor"], settings)
    assert "hosting: PiAPI temporary upload" in out.text and "saved faces: 0" in out.text


def test_doctor_survives_an_unreadable_face_library(settings):
    (settings.root / "faces").mkdir()
    (settings.root / "faces" / "faces.json").write_text("{not json")
    code, out = run(["doctor"], settings)
    assert "saved faces: unreadable" in out.text


def test_real_services_build_each_seedance_provider_whose_key_is_set(tmp_path, settings):
    from character_swap.providers.generators.seedance_piapi import SeedanceGenerator
    from character_swap.providers.generators.seedance_wavespeed import (
        SeedanceWaveSpeedGenerator,
    )

    both = Settings.load(
        root=tmp_path,
        environ={"REPLICATE_API_TOKEN": "t", "PIAPI_API_KEY": "k", "WAVESPEED_API_KEY": "w"},
    )
    services = cli.build_services(both)
    assert isinstance(services.generators["seedance:piapi"], SeedanceGenerator)
    assert isinstance(services.generators["seedance:wavespeed"], SeedanceWaveSpeedGenerator)
    assert services.faces is not None
    assert set(cli.build_services(settings).generators) == {"wan"}


def test_a_failed_720p_try_does_not_stick_to_a_plain_final(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    # A 720p attempt that failed before PiAPI saved anything: state only, no record.
    job.mark_stage("final", "failed", fingerprint=review_fingerprint(job), resolution="720p")
    assert not job.path("final-prediction.json").exists()
    code, out = run(
        ["generate", job.id, "--final"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and "Seedance final (1080p)" in out.text and "(720p)" not in out.text
    code, out = run(
        ["generate", job.id, "--final", "--yes"],
        piapi_settings,
        services_factory=seedance_services,
    )
    assert code == 0
    (body,) = seedance_services.last.bodies
    assert body["input"] == {"draft_task_id": "task-1", "resolution": "1080p"}


@pytest.mark.parametrize("record", [None, "{damaged", '{"status": "succeeded"}'])
def test_promote_without_the_draft_task_id_is_refused_before_asking(
    piapi_settings, video, tmp_path, record
):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    draft_record = job.path("draft-prediction.json")
    draft_record.unlink()
    job.path("draft-raw.mp4").write_bytes(b"kept")  # the draft video itself is still there
    if record is not None:
        draft_record.write_text(record)
    asked = []
    code, out = run(
        ["generate", job.id, "--final"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and seedance_services.last.bodies == []
    assert "task id is missing" in out.err.text and "Nothing was charged" in out.err.text


def test_prompt_for_a_stale_draft_shows_what_the_remake_sends(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    _stale_draft(job)
    job.mark_stage("draft", fingerprint="an older approval", prompt="OLD @image1 @video1")
    job.path("prompt.txt").write_text("NEW @image1 into @video1.")
    code, out = run(["prompt", job.id], piapi_settings)
    assert code == 0 and "NEW @image1 into @video1." in out.text and "OLD @image1" not in out.text
    assert "new paid draft" in out.text


def test_stale_remake_with_a_bad_prompt_is_refused_before_anything(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    _stale_draft(job)
    job.mark_stage("draft", fingerprint="an older approval", prompt="OLD @image1 @video1")
    job.path("prompt.txt").write_text("No placeholders here.")
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and "@video1" in out.err.text
    assert job.path("draft-prediction.json").exists()
    assert not list(job.root.glob("draft-old-*")) and seedance_services.last.bodies == []


def test_doctor_reports_a_malformed_face_list_as_unreadable(settings):
    (settings.root / "faces").mkdir()
    (settings.root / "faces" / "faces.json").write_text('["x"]')
    code, out = run(["doctor"], settings)
    assert "saved faces: unreadable" in out.text


def test_after_the_draft_a_malformed_face_list_only_drops_the_prices(
    piapi_settings, video, tmp_path, monkeypatch
):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    original = cli._after_draft_hint

    def hint(job, services):
        (services.faces.folder / "faces.json").write_text('["x"]')
        return original(job, services)

    monkeypatch.setattr(cli, "_after_draft_hint", hint)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    assert code == 0 and f"Run `charswap generate {job.id} --final`." in out.text


# ---- prompt iteration: an edited prompt.txt makes a new paid draft --------------------
def _drafted_seedance_job(settings, video, tmp_path, prompt="FIRST @image1 into @video1."):
    job = _approved_seedance_job(settings, video, tmp_path)
    job.path("prompt.txt").write_text(prompt)
    code, _ = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=seedance_services
    )
    assert code == 0 and job.stage_done("draft")
    return job


def test_an_edited_prompt_asks_then_makes_a_new_draft(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").write_text("SECOND @image1 into @video1.")
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=lambda q: asked.append(q) or True,
        services_factory=seedance_services,
    )
    assert code == 0 and asked and "prompt.txt changed since the draft" in out.text
    assert "SECOND @image1 into @video1." in out.text and "~$0.9900" in out.text
    (body,) = seedance_services.last.bodies
    assert body["input"]["prompt"] == "SECOND @image1 into @video1."
    assert len(list(job.root.glob("draft-old-*"))) == 3
    assert job.state["stages"]["draft"]["prompt"] == "SECOND @image1 into @video1."
    from character_swap.review import status as review_status

    assert review_status(job)["approved"] is True  # a prompt edit never clears the review


def test_the_final_promotes_the_latest_draft(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").write_text("SECOND @image1 into @video1.")
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    latest = json.loads(job.path("draft-prediction.json").read_text())["id"]
    code, _ = run(
        ["generate", job.id, "--final", "--yes"], piapi_settings, services_factory=seedance_services
    )
    assert code == 0
    (body,) = seedance_services.last.bodies
    assert body["input"] == {"draft_task_id": latest, "resolution": "1080p"}


def test_the_same_prompt_is_still_already_done(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").write_text("  FIRST @image1 into @video1.\n")  # only whitespace
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 0 and asked == [] and "already done" in out.text


def test_no_prompt_file_is_still_already_done(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").unlink()
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 0 and asked == [] and "already done" in out.text


def test_declining_a_prompt_remake_archives_nothing(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").write_text("SECOND @image1 into @video1.")
    before = json.dumps(job.state["stages"], sort_keys=True)
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and not list(job.root.glob("draft-old-*"))
    assert job.path("draft-prediction.json").exists() and seedance_services.last.bodies == []
    assert json.dumps(job.state["stages"], sort_keys=True) == before


def test_a_resumed_draft_ignores_prompt_edits(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    job.path("draft-prediction.json").write_text(json.dumps({"id": "t-1", "status": "succeeded"}))
    job.mark_stage(
        "draft", "failed", fingerprint=review_fingerprint(job), prompt="A @image1 @video1"
    )
    job.path("prompt.txt").write_text("B @image1 into @video1.")
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text
    assert seedance_services.last.bodies == [] and not list(job.root.glob("draft-old-*"))


def test_prompt_after_a_draft_says_generate_draft_makes_a_new_paid_one(
    piapi_settings, video, tmp_path
):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    code, out = run(["prompt", job.id], piapi_settings)
    assert code == 0 and "FIRST @image1 into @video1." in out.text
    assert f"charswap generate {job.id} --draft" in out.text and "new paid draft" in out.text


def test_prompt_after_an_edit_shows_the_edited_text(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").write_text("SECOND @image1 into @video1.")
    code, out = run(["prompt", job.id], piapi_settings)
    assert code == 0 and "SECOND @image1 into @video1." in out.text
    assert "FIRST" not in out.text and "new paid draft" in out.text


# ---- Wan never needs the face library --------------------------------------------------
def test_a_wan_job_with_a_removed_face_can_still_draft(settings, video, tmp_path, monkeypatch):
    _saved_face(settings, tmp_path)
    library = FaceLibrary(settings.root)
    job = Job.create(
        settings.runs_dir,
        video,
        generator="wan",
        mode="direct",
        character=library.image_path(library.get("frank")),
        faces={"primary": "frank"},
    )
    job.path("original.mp4").write_bytes(b"normalized")
    job.mark_stage("normalize")
    job.update(video={"frames": 96, "fps": 24, "width": 64, "height": 64, "seconds": 4.0})
    approve_review(job, approved=True, note="ok", fingerprint=review_fingerprint(job))
    library.index.write_text("[]")  # the face left the library
    seen = []
    monkeypatch.setattr(cli, "generate_stage", _fake_generate(seen))

    def services(s):
        return Services(
            replicate=None,
            face_detector=lambda: None,
            generators={"wan": FakeWan()},
            faces=FaceLibrary(s.root),
        )

    code, out = run(["generate", job.id, "--draft", "--yes"], settings, services_factory=services)
    assert code == 0 and seen == ["draft"], out.err.text


# ---- a failed final at another resolution ----------------------------------------------
def test_a_failed_final_record_names_the_failure_when_switching(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    record = job.path("final-prediction.json")
    record.write_text(json.dumps({"id": "t-9", "status": "failed"}))
    job.mark_stage("final", "failed", fingerprint=review_fingerprint(job), resolution="1080p")
    seedance_services.last = FakePiAPI()  # the draft's fake sent the draft body
    asked = []
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "720p"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and seedance_services.last.bodies == []
    assert "task failed" in out.err.text and str(record) in out.err.text


def test_a_running_final_at_another_resolution_is_still_refused(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("final-prediction.json").write_text(json.dumps({"id": "t-9", "status": "processing"}))
    job.mark_stage("final", "failed", fingerprint=review_fingerprint(job), resolution="1080p")
    asked = []
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "720p"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and "--resolution 1080p" in out.err.text


def test_prompt_recreates_a_deleted_file_with_what_the_draft_sent(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    job.path("prompt.txt").unlink()
    code, out = run(["prompt", job.id], piapi_settings)
    assert code == 0 and job.path("prompt.txt").read_text() == "FIRST @image1 into @video1."
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 0 and asked == [] and "already done" in out.text


# ---- a prompt remake never orphans a paid final -----------------------------------------
@pytest.mark.parametrize(
    "record",
    [{"id": "t-9", "status": "processing"}, {"status": "creating"}],
    ids=["running", "unconfirmed"],
)
def test_a_prompt_remake_waits_for_a_final_in_progress(piapi_settings, video, tmp_path, record):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    final_record = job.path("final-prediction.json")
    final_record.write_text(json.dumps(record))
    job.mark_stage("final", "failed", fingerprint=review_fingerprint(job), resolution="1080p")
    job.path("prompt.txt").write_text("SECOND @image1 into @video1.")
    seedance_services.last = FakePiAPI()
    asked = []
    code, out = run(
        ["generate", job.id, "--draft", "--yes"],
        piapi_settings,
        confirm=asked.append,
        services_factory=seedance_services,
    )
    assert code == 2 and asked == [] and seedance_services.last.bodies == []
    assert "A final is still in progress" in out.err.text
    assert f"charswap generate {job.id} --final" in out.err.text
    assert json.loads(final_record.read_text()) == record
    assert not list(job.root.glob("*-old-*"))


def test_a_prompt_remake_says_the_finished_final_is_moved(piapi_settings, video, tmp_path):
    job = _drafted_seedance_job(piapi_settings, video, tmp_path)
    code, _ = run(
        ["generate", job.id, "--final", "--yes"], piapi_settings, services_factory=seedance_services
    )
    assert code == 0 and job.stage_done("final")
    job.path("prompt.txt").write_text("SECOND @image1 into @video1.")
    shown = []
    out = Recorder()
    out.err = Recorder()

    def ask(question):
        shown.append(out.text)  # everything printed before the question
        return True

    code = cli.main(
        ["generate", job.id, "--draft"],
        settings=piapi_settings,
        out=out,
        err=out.err,
        confirm=ask,
        services_factory=seedance_services,
    )
    assert code == 0 and "final-old-*" in shown[0] and "kept, not deleted" in shown[0]
    assert len(list(job.root.glob("final-old-*"))) == 3 and not job.path("final.mp4").exists()
    (body,) = seedance_services.last.bodies
    assert body["input"]["prompt"] == "SECOND @image1 into @video1."


def test_an_inputs_remake_mentions_the_final_too(settings, video, tmp_path, monkeypatch):
    job = _approved_wan_job(settings, video, tmp_path)
    _stale_draft(job)
    job.path("final.mp4").write_bytes(b"old final")
    job.mark_stage("final", fingerprint="an older approval")
    monkeypatch.setattr(cli, "generate_stage", _fake_generate([]))
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda _q: False,
        services_factory=wan_services,
    )
    assert code == 1 and "final-old-*" in out.text


# ---- WaveSpeed (the default Seedance provider) ------------------------------------------
@pytest.fixture
def ws_settings(tmp_path):
    return Settings.load(
        root=tmp_path, environ={"REPLICATE_API_TOKEN": "t", "WAVESPEED_API_KEY": "k"}
    )


def _approved_wavespeed_job(settings, video, tmp_path, *, provider="wavespeed"):
    """An approved depth_mesh job with a plain photo. provider="" = an old job (none saved)."""
    job = Job.create(
        settings.runs_dir,
        video,
        generator="seedance",
        mode="depth_mesh",
        character=_photo(tmp_path / "me.jpg"),
        provider=provider,
    )
    for name in _MODES["depth_mesh"].review_files:
        if name.endswith(".json"):  # face-landmarks.json
            job.path(name).write_text('{"fps": 24, "frames": []}')
        else:
            write_video(job.path(name), [solid((5, 5, 5))] * 96)
    for stage in _MODES["depth_mesh"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 4.0}, transcript="hello there", scene="A studio.")
    approve_review(job, approved=True, note="ok", fingerprint=review_fingerprint(job))
    return job


def wavespeed_services(settings):
    """The real WaveSpeed generator on a fake WaveSpeed that returns a real 4 s video."""
    from character_swap.providers.generators.seedance_wavespeed import (
        SeedanceWaveSpeedGenerator,
    )

    output = write_video(settings.root / "wavespeed-out.mp4", [solid((20, 200, 20))] * 96)
    fake = FakeWaveSpeed(files={"https://cdn.wavespeed.ai/out.mp4": output})
    wavespeed_services.last = fake
    return Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": SeedanceWaveSpeedGenerator(fake)},
        faces=FaceLibrary(settings.root),
    )


def test_prompt_saves_the_provider_and_uses_wavespeeds_names(settings, video, tmp_path):
    job = _approved_wavespeed_job(settings, video, tmp_path, provider="")
    code, out = run(["prompt", job.id], settings)
    assert code == 0 and job.state["provider"] == "wavespeed"
    assert "@Video 1" in out.text and "@Video 1" in job.path("prompt.txt").read_text()


def test_wavespeed_generate_needs_the_wavespeed_key(settings, video, tmp_path):
    job = _approved_wavespeed_job(settings, video, tmp_path)
    code, out = run(["generate", job.id, "--draft"], settings, services_factory=wavespeed_services)
    assert code == 2 and "WAVESPEED_API_KEY" in out.err.text


def test_a_wavespeed_unconfirmed_marker_points_at_wavespeed(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    record = job.path("draft-prediction.json")
    record.write_text(json.dumps({"status": "creating"}))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 2 and asked == [] and wavespeed_services.last.bodies == []
    assert "WaveSpeed dashboard" in out.err.text and "PiAPI" not in out.err.text
    assert str(record) in out.err.text


def test_an_interrupted_wavespeed_draft_resumes_on_wavespeed(
    ws_settings, video, tmp_path, monkeypatch
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path, provider="")  # an old job

    def stopped(self, url, dest):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeWaveSpeed, "download", stopped)
    code, _ = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 130 and job.path("draft-prediction.json").exists()
    assert job.state["provider"] == "wavespeed"  # saved before the paid create
    monkeypatch.undo()
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text, out.err.text
    assert wavespeed_services.last.bodies == [] and job.stage_done("draft")


RENAMED = "Updated prompt.txt to WaveSpeed's input names."


def test_prompt_renames_old_piapi_names_for_wavespeed(settings, video, tmp_path):
    job = _approved_wavespeed_job(settings, video, tmp_path, provider="")
    job.path("prompt.txt").write_text("Put @image1 into @video1.")
    code, out = run(["prompt", job.id], settings)
    assert code == 0 and RENAMED in out.text and "Put @Image 1 into @Video 1." in out.text
    assert job.path("prompt.txt").read_text() == "Put @Image 1 into @Video 1."


def test_generate_renames_before_the_cost_question(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("prompt.txt").write_text("Put @image1 into @video1.")
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=lambda _q: False,
        services_factory=wavespeed_services,
    )
    assert code == 1 and out.text.index(RENAMED) < out.text.index("~$0.8800")
    assert "Put @Image 1 into @Video 1." in out.text


def test_a_rename_alone_never_makes_a_new_draft(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    job.path("prompt.txt").write_text("FIRST @Image 1 into @Video 1.")
    code, _ = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0 and job.stage_done("draft")
    job.path("prompt.txt").write_text("FIRST @image1 into @video1.")  # same text, old names
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and RENAMED in out.text and "already done" in out.text
    assert not list(job.root.glob("draft-old-*"))


def test_a_wavespeed_draft_shows_the_prompt_and_price_then_sends_one_body(
    ws_settings, video, tmp_path
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        ws_settings,
        confirm=lambda q: asked.append(q) or False,
        services_factory=wavespeed_services,
    )
    assert code == 1 and asked and wavespeed_services.last.bodies == []
    assert "@Video 1" in out.text and "hello there" in out.text and "~$0.8800" in out.text
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0 and job.stage_done("draft")
    (body,) = wavespeed_services.last.bodies
    assert body["resolution"] == "480p" and body["generate_audio"] is False
    assert body["prompt"] == job.state["stages"]["draft"]["prompt"]
    assert body["reference_images"] == ["https://cdn.wavespeed.ai/uploads/character-primary.jpg"]
    uploads = [p.name for p in wavespeed_services.last.uploads]
    assert uploads == ["prepared.mp4", "character-primary.jpg"]
    assert job.state["costs"][-1]["provider"] == "wavespeed"


def test_after_a_wavespeed_draft_both_new_take_prices_are_shown(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0
    assert "makes a new 720p take (~$1.76)" in out.text
    assert "`--resolution 1080p` for a new 1080p take (~$4.40)" in out.text
    assert "upgrade" not in out.text


def test_a_wavespeed_final_is_a_new_720p_take_by_default(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    code, out = run(
        ["generate", job.id, "--final"],
        ws_settings,
        confirm=lambda _q: False,
        services_factory=wavespeed_services,
    )
    assert code == 1 and "Seedance final (720p)" in out.text and "~$1.7600" in out.text
    assert "The final is a new take at higher resolution" in out.text
    assert "upgrades the exact draft" not in out.text and wavespeed_services.last.bodies == []


def test_a_wavespeed_1080p_final_sends_a_fresh_body_with_the_drafts_prompt(
    ws_settings, video, tmp_path
):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    sent = job.state["stages"]["draft"]["prompt"]
    job.path("prompt.txt").write_text("@Video 1 @Image 1 edited after the draft")
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "1080p", "--yes"],
        ws_settings,
        services_factory=wavespeed_services,
    )
    assert code == 0 and job.stage_done("final") and "~$4.4000" in out.text
    (body,) = wavespeed_services.last.bodies
    assert body["resolution"] == "1080p" and body["prompt"] == sent
    assert "draft_task_id" not in body


def test_a_piapi_1080p_final_never_says_new_take(piapi_settings, video, tmp_path):
    job = _approved_seedance_job(piapi_settings, video, tmp_path)
    run(
        ["generate", job.id, "--draft", "--yes"], piapi_settings, services_factory=seedance_services
    )
    code, out = run(
        ["generate", job.id, "--final"],
        piapi_settings,
        confirm=lambda _q: False,
        services_factory=seedance_services,
    )
    assert code == 1 and "new take" not in out.text and "fresh run" not in out.text


def test_a_plain_wavespeed_final_sends_a_720p_body(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    code, out = run(
        ["generate", job.id, "--final", "--yes"], ws_settings, services_factory=wavespeed_services
    )
    assert code == 0 and job.stage_done("final"), out.err.text
    (body,) = wavespeed_services.last.bodies
    assert body["resolution"] == "720p" and "draft_task_id" not in body
    assert body["prompt"] == job.state["stages"]["draft"]["prompt"]
    assert job.state["stages"]["final"]["resolution"] == "720p"


def test_a_started_wavespeed_1080p_final_keeps_its_resolution(ws_settings, video, tmp_path):
    job = _approved_wavespeed_job(ws_settings, video, tmp_path)
    run(["generate", job.id, "--draft", "--yes"], ws_settings, services_factory=wavespeed_services)
    job.path("final-prediction.json").write_text(json.dumps({"id": "p", "status": "succeeded"}))
    job.mark_stage("final", "failed", fingerprint=review_fingerprint(job), resolution="1080p")
    draft_fake = wavespeed_services.last
    asked = []
    code, out = run(
        ["generate", job.id, "--final", "--resolution", "720p"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 2 and asked == [] and "--resolution 1080p" in out.err.text
    assert wavespeed_services.last is draft_fake  # refused before any service was even built
    assert [b["resolution"] for b in draft_fake.bodies] == ["480p"]
    code, out = run(
        ["generate", job.id, "--final"],
        ws_settings,
        confirm=asked.append,
        services_factory=wavespeed_services,
    )
    assert code == 0 and asked == [] and "no new charge" in out.text, out.err.text
    assert "1080p final" in out.text and wavespeed_services.last.bodies == []
    assert job.stage_done("final") and job.state["stages"]["final"]["resolution"] == "1080p"
