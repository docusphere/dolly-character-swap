"""charswap commands on a scene swap (--from/--to). No paid call: fakes and monkeypatches."""

import pytest
from PIL import Image

from character_swap import cli
from character_swap.config import Settings
from character_swap.faces import PHOTO_CONSENT
from character_swap.jobs import Job
from character_swap.media.video import count_frames
from helpers import SCENE_RATE, write_shots

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
