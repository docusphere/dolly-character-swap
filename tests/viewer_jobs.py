"""Build job folders with real tiny artifacts for the viewer tests. No pipeline, no network."""

import json
import shutil
from pathlib import Path

from PIL import Image

from character_swap.jobs import Job
from character_swap.modes import GENERATION_STAGES
from character_swap.review import fingerprint
from character_swap.web.view_model import STAGES_BY_MODE
from helpers import solid, tone, write_video, write_wav

# Files each stage leaves in the job folder (what the viewer looks for).
ARTIFACTS = {
    "normalize": ["original.mp4"],
    "separate": ["vocals.wav"],
    "pitch": ["vocals-plus3.wav"],
    "describe": ["scene.jpg"],
    "depth": ["depth.mp4"],
    "composite": ["mask.mp4", "composite-silent.mp4"],
    "face_mesh": ["composite-silent.mp4"],
    "embed": ["prepared.mp4"],
    "draft": ["draft.mp4"],
    "final": ["final.mp4"],
}
RECORDS = {
    "separate": "demucs-prediction.json",
    "transcribe": "whisper-prediction.json",
    "describe": "describe-prediction.json",
    "depth": "depth-prediction.json",
    "masks": "sam/sam-prediction.json",
    "draft": "draft-prediction.json",
    "final": "final-prediction.json",
}


class Media:
    """One encoded clip, one wav and one image per test, copied wherever a stage needs them."""

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.video = write_video(folder / "clip.mp4", [solid((180, 40, 40))] * 24, audio_seconds=1)
        self.wav = write_wav(folder / "voice.wav", tone(1))
        self.image = folder / "face.png"
        Image.new("RGB", (8, 8), (200, 150, 120)).save(self.image)
        jpg = folder / "scene.jpg"
        Image.new("RGB", (8, 8), (90, 90, 90)).save(jpg)
        self.jpg = jpg

    def copy(self, name: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".wav"):
            shutil.copyfile(self.wav, dest)
        elif name.endswith(".jpg"):
            shutil.copyfile(self.jpg, dest)
        else:
            shutil.copyfile(self.video, dest)


def write_record(job: Job, name: str, data: dict) -> None:
    path = job.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def finish_stage(job: Job, media: Media, name: str) -> None:
    """Leave exactly what the real stage leaves, then mark it done."""
    for artifact in ARTIFACTS.get(name, []):
        media.copy(artifact, job.path(artifact))
    if name == "face_mesh":
        if job.mode == "depth_mesh":
            media.copy("x.mp4", job.path("depth-composite-silent.mp4"))
        job.path("face-landmarks.json").write_text(json.dumps({"fps": 24, "frames": []}))
        report = {"frames": 24, "tracked": 24, "missing_frames": []}
        job.path("face-tracking.json").write_text(json.dumps(report))
    if name == "transcribe":
        job.update(transcript="hello there")
    if name == "describe":
        job.update(scene="A bright studio with plants.")
    if name in RECORDS and not (name == "depth" and job.state["depth_provider"] == "huggingface"):
        write_record(job, RECORDS[name], {"id": f"pred-{name}", "status": "succeeded"})
    if name in GENERATION_STAGES:  # like pipeline.generate: remember which inputs it used
        job.mark_stage(name, fingerprint=fingerprint(job))
        return
    job.mark_stage(name)


def make_job(
    runs_dir: Path,
    media: Media,
    mode: str = "depth",
    *,
    done: int | None = None,
    running: bool = False,
    failed: str | None = None,
    depth_provider: str = "replicate",
) -> Job:
    """A job whose first `done` stages are finished (all when None).

    The next stage is marked running when `running`, or failed with message `failed`.
    """
    generator = "wan" if mode == "direct" else "seedance"
    character = media.image if mode == "direct" else None
    job = Job.create(
        runs_dir,
        media.video,
        generator=generator,
        mode=mode,
        depth_provider=depth_provider,
        character=character,
    )
    stages = STAGES_BY_MODE[mode]
    count = len(stages) if done is None else done
    for name in stages[:count]:
        finish_stage(job, media, name)
    if count < len(stages):
        if running:
            job.mark_stage(stages[count], "running", started_at="2026-11-01T10:00:00+00:00")
        elif failed is not None:
            job.mark_stage(stages[count], "failed", error=failed)
    return job
