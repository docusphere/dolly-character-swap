import json

import pytest

from character_swap import cli
from character_swap.config import Settings
from character_swap.jobs import Job
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


def test_status_review_prompt_flow(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
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
    fingerprint = json.loads(run(["status", job.id], settings)[1].text)["review"]["fingerprint"]
    code, out = run(
        ["review", job.id, "--reject", "--note", "bad", "--fingerprint", fingerprint], settings
    )
    assert code == 0 and "rejected" in out.text


def test_review_stale_fingerprint_is_friendly(settings, video):
    job = Job.create(settings.runs_dir, video, generator="seedance", mode="face_mesh")
    for name in ["original.mp4", "face-landmarks.json", "composite-silent.mp4", "prepared.mp4"]:
        job.path(name).write_bytes(name.encode())
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
