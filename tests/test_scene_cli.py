"""charswap commands on a scene swap (--from/--to). No paid call: fakes and monkeypatches."""

import pytest
from PIL import Image

from character_swap import cli, pipeline
from character_swap.config import Settings
from character_swap.faces import PHOTO_CONSENT, FaceLibrary
from character_swap.jobs import Job
from character_swap.media.video import count_frames
from character_swap.modes import MODES
from character_swap.pipeline import Services
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from character_swap.review import decide, fingerprint
from character_swap.stages.describe import DESCRIBE_MODEL
from fakes import FakeWaveSpeed, RefusingReplicate
from helpers import SCENE_RATE, solid, write_shots, write_video
from scene_jobs import TWO, fake_pitch, make_scene_job, prep_services

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 160, 30)


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(str(text))

    @property
    def text(self):
        return "\n".join(self.lines)


def run(argv, settings, **kw):
    out = Recorder()
    out.err = Recorder()
    code = cli.main(argv, settings=settings, out=out, err=out.err, **kw)
    return code, out


@pytest.fixture
def settings(tmp_path):
    return Settings.load(
        root=tmp_path, environ={"REPLICATE_API_TOKEN": "t", "WAVESPEED_API_KEY": "w"}
    )


@pytest.fixture
def scene_clip(tmp_path):
    """Three shots: police (24 frames), the face (95 frames: 1.00 s to 4.96 s), a masked man."""
    return write_shots(
        tmp_path / "scene.mp4",
        [(RED, 24), (BLUE, 95), (GREEN, 24)],
        audio_seconds=float(143 / SCENE_RATE),
    )


def _photo(path):
    Image.new("RGB", (828, 1086), (200, 150, 120)).save(path, "JPEG")
    return path


def _prepare_ids(monkeypatch):
    ids = []
    monkeypatch.setattr(cli, "prepare_job", lambda job, services, status: ids.append(job.id))
    return ids


def scene_args(clip, tmp_path, *extra, start="1.0", end="5.0"):
    return [
        "prepare",
        str(clip),
        "--from",
        start,
        "--to",
        end,
        "--subject",
        "man with white face paint",
        "--character",
        str(_photo(tmp_path / "me.jpg")),
        *extra,
    ]


def _no_job(settings):
    return not settings.runs_dir.exists() or not any(settings.runs_dir.iterdir())


# ---- prepare -------------------------------------------------------------------------
def test_the_summary_comes_before_the_rights_question(settings, scene_clip, tmp_path):
    asked = []
    code, out = run(
        scene_args(scene_clip, tmp_path),
        settings,
        confirm=lambda q: asked.append(q) and False,
    )
    assert code == 1 and asked == [cli.RIGHTS_QUESTION] and _no_job(settings)
    text = out.text
    assert "Scene: 6.0 s, 64x48. Shot cuts found at 1.00 s and 4.96 s." in text
    assert "Swapping 1.00 s to 4.96 s (4.0 s; moved to the shot cuts you were near)." in text
    assert "made 4 s long by holding its last frame" in text
    assert "No black bars found, so it sees 64x48." in text
    assert "never sent anywhere" in text
    assert text.index("Scene: 6.0 s") < text.index(PHOTO_CONSENT)
    assert "the result with this face also goes to Replicate" in PHOTO_CONSENT  # the lay-back


def test_a_scene_job_is_made_from_the_cut_part(settings, scene_clip, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--yes"), settings, services_factory=lambda s: object()
    )
    assert code == 0, out.err.text
    job = Job.load(settings.runs_dir, ids[0])
    state = job.state
    assert job.path("full-scene.mp4").read_bytes() == scene_clip.read_bytes()
    assert count_frames(job.path("upload.mp4")) == 96  # 95 scene frames -> 4 s at 24 fps
    segment = state["segment"]
    assert (segment["start_frame"], segment["end_frame"], segment["padded_seconds"]) == (24, 119, 4)
    assert len(segment["frame_map"]) == 96 and segment["frame_map"][0] == 24
    assert state["subject"] == "man with white face paint" and state["mode"] == "depth_mesh"
    assert "Scene description" in out.text and "setting" not in state


def test_a_setting_you_write_is_saved_and_not_priced(settings, scene_clip, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--setting", "A dim  room.", "--yes"),
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and "Scene description" not in out.text
    state = Job.load(settings.runs_dir, ids[0]).state
    assert state["setting"] == "A dim room."
    assert state["costs"][0]["usd"] == pytest.approx(0.0598)  # no $0.001 description


def test_a_setting_also_works_without_a_scene(settings, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    clip = write_shots(tmp_path / "c.mp4", [(BLUE, 96)], fps=24, audio_seconds=4)
    code, _ = run(
        ["prepare", str(clip), "--character", str(_photo(tmp_path / "me.jpg"))]
        + ["--setting", "A dim room.", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    state = Job.load(settings.runs_dir, ids[0]).state
    assert code == 0 and state["setting"] == "A dim room." and "segment" not in state


def test_a_cut_inside_the_part_is_a_warning_not_a_stop(settings, scene_clip, tmp_path, monkeypatch):
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--yes", start="0", end="6"),
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and ids
    assert "There is a shot cut at 1.0 s inside the part; the AI will see two shots." in out.text
    assert "no shot cut near either time" in out.text


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda a: a[:4] + a[6:], "Use --from and --to together."),
        (lambda a: [*a, "--provider", "piapi"], "needs WaveSpeed and depth_mesh or depth mode"),
        (lambda a: [*a, "--mode", "face_mesh"], "needs WaveSpeed and depth_mesh or depth mode"),
        (lambda a: [*a, "--generator", "wan"], "needs WaveSpeed and depth_mesh or depth mode"),
        (lambda a: a[:3] + ["5"] + a[4:5] + ["2"] + a[6:], "pick --from and --to inside it"),
        (lambda a: a[:5] + ["9"] + a[6:], "The scene is 6.0 s; pick --from and --to inside it"),
        (lambda a: a[:3] + ["2"] + a[4:5] + ["2.5"] + a[6:], "The part to swap is 0.5 s"),
        (lambda a: [*a, "--setting", "x" * 301], "Describe the setting in 1 to 300 characters"),
    ],
)
def test_every_refusal_comes_before_any_question(settings, scene_clip, tmp_path, change, message):
    asked = []
    code, out = run(change(scene_args(scene_clip, tmp_path)), settings, confirm=asked.append)
    assert code == 2 and message in out.err.text and "Nothing was charged." in out.err.text
    assert asked == [] and _no_job(settings)


def test_a_scene_whose_sound_does_not_fit_is_refused_first(settings, tmp_path):
    clip = write_shots(tmp_path / "s.mp4", [(RED, 24), (BLUE, 95), (GREEN, 24)], audio_seconds=3)
    asked = []
    code, out = run(scene_args(clip, tmp_path), settings, confirm=asked.append)
    assert code == 2 and "Re-export the scene" in out.err.text and asked == []


def test_a_missing_scene_is_refused_first(settings, tmp_path):
    code, out = run(scene_args(tmp_path / "nope.mp4", tmp_path), settings)
    assert code == 2 and "Video not found" in out.err.text


def test_a_setting_is_refused_for_wan(settings, tmp_path):
    clip = write_shots(tmp_path / "c.mp4", [(BLUE, 48)], fps=24)
    face = _photo(tmp_path / "me.jpg")
    code, out = run(
        ["prepare", str(clip), "--generator", "wan", "--character", str(face), "--setting", "A"],
        settings,
    )
    assert code == 2 and "--setting only matters for Seedance modes" in out.err.text


# ---- resume --setting ----------------------------------------------------------------
def test_resume_with_a_setting_skips_the_paid_description(settings, tmp_path, monkeypatch):
    job = make_scene_job(settings.runs_dir, tmp_path)
    ids = _prepare_ids(monkeypatch)
    code, out = run(
        ["resume", job.id, "--setting", "A dim room.", "--yes"],
        settings,
        services_factory=lambda s: object(),
    )
    assert code == 0 and ids == [job.id] and job.state["setting"] == "A dim room."
    assert "Remaining paid steps: separate, transcribe, depth, masks " in out.text
    assert job.state["costs"][-1]["usd"] == pytest.approx(0.0598)  # no $0.001 description


def test_resume_refuses_a_setting_once_the_description_is_done(settings, tmp_path, monkeypatch):
    job = make_scene_job(settings.runs_dir, tmp_path)
    job.mark_stage("describe")
    monkeypatch.setattr(cli, "prepare_job", lambda *a, **k: pytest.fail("must not run"))
    code, out = run(["resume", job.id, "--setting", "A dim room."], settings)
    assert code == 2 and "The setting is already done for this job" in out.err.text
    assert "setting" not in job.state


def test_a_refused_description_continues_for_free(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)
    job = make_scene_job(settings.runs_dir, tmp_path)
    services = prep_services(tmp_path, cls=RefusingReplicate, refuse=DESCRIBE_MODEL)
    code, out = run(["resume", job.id, "--yes"], settings, services_factory=lambda s: services)
    assert code == 2
    assert f'charswap resume {job.id} --setting "a dim room with ..."' in out.err.text
    code, out = run(
        ["resume", job.id, "--setting", "A dim room.", "--yes"],
        settings,
        services_factory=lambda s: services,
    )
    assert code == 0, out.err.text
    keys = [run["key"] for run in services.replicate.runs]
    assert keys.count(DESCRIBE_MODEL) == 1 and job.stage_done("embed")  # no new description


def test_two_people_say_start_a_new_job_not_resume(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)
    job = make_scene_job(settings.runs_dir, tmp_path)
    services = prep_services(tmp_path, boxes=TWO)
    code, out = run(["resume", job.id, "--yes"], settings, services_factory=lambda s: services)
    assert code == 2 and "Start a new job" in out.err.text
    assert "charswap resume" not in out.err.text and "Fix the issue" not in out.err.text


def test_a_stop_at_the_one_person_check_still_logs_the_prep_spend(
    settings, scene_clip, tmp_path, monkeypatch
):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)
    services = prep_services(tmp_path, boxes=TWO)
    code, out = run(
        scene_args(scene_clip, tmp_path, "--yes"), settings, services_factory=lambda s: services
    )
    assert code == 2 and "more than one person" in out.err.text
    (folder,) = [p for p in settings.runs_dir.iterdir() if p.is_dir()]
    job = Job.load(settings.runs_dir, folder.name)
    assert [c["step"] for c in job.state["costs"]] == ["prepare"]
    assert job.state["costs"][0]["usd"] == pytest.approx(0.0608)
    code, out = run(["resume", job.id, "--yes"], settings, services_factory=lambda s: services)
    assert code == 2 and [c["step"] for c in job.state["costs"]] == ["prepare"]


# ---- generate and repair ---------------------------------------------------------------
def ready_scene_job(settings, tmp_path):
    """An approved depth_mesh scene job whose part is 13 s (307 scene frames, padded).

    The prepared files are stand-ins; the review hashes whatever is there.
    """
    job = make_scene_job(settings.runs_dir, tmp_path, mode="depth_mesh", face_frames=307)
    for name in MODES["depth_mesh"].review_files:
        if name.endswith(".json"):  # face-landmarks.json
            job.path(name).write_text('{"fps": 24, "frames": []}')
        else:
            write_video(job.path(name), [solid((5, 5, 5))] * 24)
    for stage in MODES["depth_mesh"].prep_stages:
        job.mark_stage(stage)
    job.update(video={"seconds": 13.0}, transcript="hello there", scene="A studio.")
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    return job


def ws_services(settings):
    """The real WaveSpeed generator on a fake WaveSpeed; ws_services.last is that fake."""
    output = write_video(settings.root / "wavespeed-out.mp4", [solid((20, 200, 20))] * 24)
    fake = FakeWaveSpeed(files={"https://cdn.wavespeed.ai/out.mp4": output})
    ws_services.last = fake
    return Services(
        replicate=None,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": SeedanceWaveSpeedGenerator(fake)},
        faces=FaceLibrary(settings.root),
    )


@pytest.fixture
def laid_back(monkeypatch):
    """The lay-back without SAM or paste: it saves its SAM record and copies the raw clip."""
    calls = []

    def fake_lay_back(job, stage, replicate, dst, *, runs_dir, status):
        calls.append(stage)
        folder = job.path(f"{stage}-sam")
        folder.mkdir(exist_ok=True)
        (folder / "sam-prediction.json").write_text('{"id": "s1", "status": "succeeded"}')
        dst.write_bytes(job.path(f"{stage}-raw.mp4").read_bytes())
        return dst

    monkeypatch.setattr(pipeline, "lay_back", fake_lay_back)
    return calls


def test_the_draft_price_is_the_padded_part_plus_the_lay_back(settings, tmp_path, laid_back):
    job = ready_scene_job(settings, tmp_path)
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: asked.append(q) and False,
        services_factory=ws_services,
    )
    assert code == 1 and asked == ["Continue?"] and ws_services.last.bodies == []
    assert "Seedance draft (480p)        ~$1.4300" in out.text  # 13 s, never the 17 s scene
    assert "SAM 3 masks" in out.text and "Total                        ~$2.8620" in out.text
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=ws_services
    )
    assert code == 0 and laid_back == ["draft"] and len(ws_services.last.bodies) == 1
    assert "makes a new 720p take (~$5.72)" in out.text
    assert "`--resolution 1080p` for a new 1080p take (~$14.30)" in out.text


@pytest.mark.parametrize("saved", ["record", "raw"])
def test_a_free_resume_still_asks_before_buying_the_lay_back(settings, tmp_path, laid_back, saved):
    job = ready_scene_job(settings, tmp_path)
    if saved == "record":
        job.path("draft-prediction.json").write_text('{"id": "p", "status": "succeeded"}')
    else:
        write_video(job.path("draft-raw.mp4"), [solid((20, 200, 20))] * 24)
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job))  # e.g. Ctrl-C
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: asked.append(q) and False,
        services_factory=ws_services,
    )
    assert code == 1 and asked == ["Continue?"] and laid_back == []
    assert "The AI part resumes free; laying it back needs one SAM 3 run (~$0.002)." in out.text
    code, out = run(
        ["generate", job.id, "--draft", "--yes"], settings, services_factory=ws_services
    )
    assert code == 0 and laid_back == ["draft"] and ws_services.last.bodies == []


def test_with_both_saved_the_resume_asks_nothing(settings, tmp_path, laid_back):
    job = ready_scene_job(settings, tmp_path)
    write_video(job.path("draft-raw.mp4"), [solid((20, 200, 20))] * 24)
    job.path("draft-sam").mkdir()
    job.path("draft-sam/sam-prediction.json").write_text('{"id": "s", "status": "succeeded"}')
    job.mark_stage("draft", "failed", fingerprint=fingerprint(job))
    asked = []
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=asked.append,
        services_factory=ws_services,
    )
    assert code == 0 and asked == [] and "resuming it (no new charge)" in out.text


def test_a_remakes_price_includes_a_new_lay_back(settings, tmp_path, laid_back):
    job = ready_scene_job(settings, tmp_path)
    write_video(job.path("draft.mp4"), [solid((20, 200, 20))] * 24)
    job.path("draft-sam").mkdir()
    job.path("draft-sam/sam-prediction.json").write_text('{"id": "s", "status": "succeeded"}')
    job.mark_stage("draft", fingerprint="inputs that are no longer approved")
    code, out = run(
        ["generate", job.id, "--draft"],
        settings,
        confirm=lambda q: False,
        services_factory=ws_services,
    )
    assert code == 1 and "SAM 3 masks" in out.text and "~$2.8620" in out.text
    assert job.path("draft-sam/sam-prediction.json").exists()  # declined: nothing archived


def test_repair_is_refused_on_a_scene_job(settings, tmp_path, monkeypatch):
    job = make_scene_job(settings.runs_dir, tmp_path)
    monkeypatch.setattr(cli, "repair_with_rgb", lambda *a, **k: pytest.fail("must not run"))
    asked = []
    code, out = run(["repair", job.id], settings, confirm=asked.append)
    assert code == 2 and asked == []
    assert "already cuts out on the original colors" in out.err.text
