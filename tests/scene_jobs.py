"""Scene swap jobs for tests: a synthetic three-shot scene and fake prep services. No network."""

import io
import zipfile

import numpy as np
from PIL import Image

from character_swap.jobs import Job
from character_swap.media import scene
from character_swap.pipeline import Services
from character_swap.stages.depth import DEPTH_MODEL
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from character_swap.stages.separate import DEMUCS_VERSION
from character_swap.stages.transcribe import WHISPER_VERSION
from fakes import FakeReplicate
from helpers import SCENE_RATE, solid, tone, write_shots, write_video, write_wav

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 160, 30)
FRAMES = 96  # the default part: 95 scene frames (3.96 s) -> 96 job frames, 4 s for the AI
ONE = [(16, 8, 48, 40)]  # mask boxes (x0, y0, x1, y1) on the 64 x 48 job frames
TWO = [(4, 8, 28, 40), (36, 8, 60, 40)]  # two people of the same size


def fake_pitch(vocals, dst, **kwargs):
    """Stands in for Rubber Band (monkeypatch character_swap.pipeline.pitch_vocals)."""
    dst.write_bytes(vocals.read_bytes())
    return dst


def masks_zip(boxes, count=FRAMES, size=(64, 48)):
    """SAM 3's zip: `count` masks of `size`, each with `boxes` white."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(count):
            mask = np.zeros((size[1], size[0]), dtype=np.uint8)
            for x0, y0, x1, y1 in boxes:
                mask[y0:y1, x0:x1] = 255
            png = io.BytesIO()
            Image.fromarray(mask).save(png, "PNG")
            archive.writestr(f"masks/mask_{index:05d}.png", png.getvalue())
    return buffer.getvalue()


def prep_services(tmp_path, boxes=ONE, cls=FakeReplicate, **kwargs):
    """Fake Replicate answers for every paid prep step of a 4 s (96-frame) job."""
    stems = {
        name: write_wav(tmp_path / f"{name}.wav", tone(4, freq))
        for name, freq in (("v", 300), ("d", 80), ("b", 60), ("o", 500))
    }
    depth = write_video(tmp_path / "depth.mp4", [solid((100, 100, 100))] * FRAMES)
    fake = cls(
        outputs={
            DEMUCS_VERSION: {"vocals": "u/v", "drums": "u/d", "bass": "u/b", "other": "u/o"},
            WHISPER_VERSION: {"transcription": "hello"},
            DESCRIBE_MODEL: ["A studio."],
            f"{DEPTH_MODEL}-latest": "u/depth",
            SAM_VERSION: "u/zip",
        },
        files={
            **{f"u/{n}": path for n, path in stems.items()},
            "u/depth": depth,
            "u/zip": masks_zip(boxes),
        },
        **kwargs,
    )
    return Services(replicate=fake, face_detector=lambda: None, rubberband="unused")


def make_scene_job(runs_dir, tmp_path, *, mode="depth", setting="", face_frames=95):
    """A scene of three shots (64 x 48, 23.976 fps); the middle one is the part to swap.

    With the default 95 face frames the part is 1.00 s to 4.96 s: 96 job frames, 4 s.
    """
    total = 24 + face_frames + 24
    clip = write_shots(
        tmp_path / "scene.mp4",
        [(RED, 24), (BLUE, face_frames), (GREEN, 24)],
        audio_seconds=float(total / SCENE_RATE),
    )
    plan = scene.plan_part(clip, 1.0, float((24 + face_frames) / SCENE_RATE))
    crop = plan.segment["crop"]
    part = tmp_path / "part.mp4"
    frame_map = scene.cut_segment(
        clip,
        plan.segment["start"],
        plan.segment["end"],
        (crop["x"], crop["y"], crop["w"], crop["h"]),
        part,
    )
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (64, 64), (200, 150, 120)).save(photo, "JPEG")
    return Job.create(
        runs_dir,
        part,
        generator="seedance",
        mode=mode,
        character=photo,
        provider="wavespeed",
        scene=clip,
        segment={**plan.segment, "frame_map": frame_map},
        setting=setting,
    )
