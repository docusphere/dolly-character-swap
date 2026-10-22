"""Track the face with MediaPipe and draw a mesh as motion guidance for the generator."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw

from character_swap.media.video import FPS, VideoWriter, count_frames, iter_frames
from character_swap.stages import StageError

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
Point = tuple[float, float]  # normalized (x, y) in the full frame


class FaceDetector(Protocol):
    def detect(self, frame: np.ndarray, timestamp_ms: int) -> list[list[Point]]: ...


@dataclass(frozen=True)
class MeshStyle:
    """Each entry: (edges as index pairs, RGB color, line width)."""

    lines: Sequence[tuple[Sequence[tuple[int, int]], tuple[int, int, int], int]]


def ensure_model(models_dir: Path, download: Callable = urllib.request.urlretrieve) -> Path:
    model = models_dir / "face_landmarker.task"
    if not model.exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        partial = model.with_suffix(".download")
        download(MODEL_URL, str(partial))
        partial.replace(model)
    return model


def _draw(image: Image.Image, faces: list[list[Point]], style: MeshStyle) -> None:
    pen = ImageDraw.Draw(image)
    for face in faces:
        points = [(round(x * image.width), round(y * image.height)) for x, y in face]
        for edges, color, width in style.lines:
            for start, end in edges:
                pen.line([points[start], points[end]], fill=color, width=width)


def track_and_draw(
    source: Path,
    out_video: Path,
    detector: FaceDetector,
    style: MeshStyle,
    report_dir: Path,
    *,
    background: Path | None = None,
) -> dict:
    """Write the mesh video plus face-landmarks.json and face-tracking.json in report_dir."""
    total = count_frames(source)
    backgrounds = iter_frames(background) if background else None
    if background and count_frames(background) != total:
        raise StageError("Depth composite and original differ in frame count; rebuild depth first.")

    first = next(iter_frames(source))
    height, width = first.shape[:2]
    saved, counts = [], []
    with VideoWriter(out_video, width, height) as writer:
        for index, frame in enumerate(iter_frames(source)):
            faces = detector.detect(frame, round(index * 1000 / FPS))
            counts.append(len(faces))
            saved.append(
                {
                    "frame": index,
                    "time": index / FPS,
                    "faces": [[{"x": x, "y": y} for x, y in face] for face in faces],
                }
            )
            canvas = Image.fromarray(next(backgrounds) if backgrounds else frame)
            _draw(canvas, faces, style)
            writer.write(np.asarray(canvas))

    missing = [i for i, n in enumerate(counts) if n == 0]
    report = {
        "frames": len(counts),
        "tracked": len(counts) - len(missing),
        "missing_frames": missing,
    }
    (report_dir / "face-landmarks.json").write_text(json.dumps({"fps": FPS, "frames": saved}))
    (report_dir / "face-tracking.json").write_text(json.dumps(report, indent=2))
    if missing:
        raise StageError(
            f"Face tracking lost the face on frames {missing}. Use a clip with one centered, "
            "visible face, or choose depth mode."
        )
    return report


class MediaPipeDetector:  # pragma: no cover - exercised by the live smoke test
    """Real detector. Crops the centre/top region like the original worker for stability."""

    def __init__(self, model_path: Path):
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.15,
            min_face_presence_confidence=0.15,
            min_tracking_confidence=0.15,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    @property
    def style(self) -> MeshStyle:
        conn = self._mp.tasks.vision.FaceLandmarksConnections

        def pairs(group):
            return [(c.start, c.end) for c in group]

        return MeshStyle(
            lines=[
                (pairs(conn.FACE_LANDMARKS_TESSELATION), (50, 170, 205), 1),
                (pairs(conn.FACE_LANDMARKS_LIPS), (255, 135, 100), 2),
                (pairs(conn.FACE_LANDMARKS_LEFT_EYE), (130, 255, 150), 2),
                (pairs(conn.FACE_LANDMARKS_RIGHT_EYE), (130, 255, 150), 2),
            ]
        )

    def detect(self, frame: np.ndarray, timestamp_ms: int) -> list[list[Point]]:
        height, width = frame.shape[:2]
        x0, crop_w, crop_h = int(width * 0.25), int(width * 0.5), int(height * 0.65)
        crop = np.ascontiguousarray(frame[0:crop_h, x0 : x0 + crop_w])
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=crop)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        return [
            [((x0 + p.x * crop_w) / width, (p.y * crop_h) / height) for p in face]
            for face in result.face_landmarks
        ]

    def close(self) -> None:
        self._landmarker.close()
