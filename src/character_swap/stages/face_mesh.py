"""Track the face with MediaPipe and draw a mesh as motion guidance for the generator."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import av
import numpy as np
from PIL import Image, ImageDraw

from character_swap.media.video import FPS, VideoWriter, count_frames, iter_frames
from character_swap.stages import StageError

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
Point = tuple[float, float]  # normalized (x, y) in the full frame


@dataclass(frozen=True)
class MeshStyle:
    """Each entry: (edges as index pairs, RGB color, line width)."""

    lines: Sequence[tuple[Sequence[tuple[int, int]], tuple[int, int, int], int]]


class FaceDetector(Protocol):
    @property
    def style(self) -> MeshStyle: ...

    def detect(self, frame: np.ndarray, timestamp_ms: int) -> list[list[Point]]: ...

    def close(self) -> None: ...


def _download(url: str, target: str) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out)
        expected = response.headers.get("Content-Length")
        written = out.tell()
    if expected is not None and written != int(expected):
        raise OSError(f"download truncated: got {written} of {expected} bytes")


def ensure_model(models_dir: Path, download: Callable = _download) -> Path:
    model = models_dir / "face_landmarker.task"
    if not model.exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        partial = model.with_suffix(".download")
        try:
            download(MODEL_URL, str(partial))
        except (OSError, urllib.error.URLError) as exc:  # URLError and timeouts are OSErrors
            partial.unlink(missing_ok=True)
            raise StageError(
                f"Could not download the face model from {MODEL_URL}: {exc}. "
                "Check your connection and retry."
            ) from exc
        partial.replace(model)
    return model


def crop_box(width: int, height: int) -> tuple[int, int, int]:
    """Centre 50% of the width, top 65% of the height: (x0, crop_w, crop_h)."""
    return int(width * 0.25), int(width * 0.5), int(height * 0.65)


def to_full_frame(
    points: Sequence[Point], box: tuple[int, int, int], width: int, height: int
) -> list[Point]:
    """Map crop-normalized points back to full-frame normalized coordinates."""
    x0, crop_w, crop_h = box
    return [((x0 + x * crop_w) / width, (y * crop_h) / height) for x, y in points]


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
    report_dir.mkdir(parents=True, exist_ok=True)
    out_video.parent.mkdir(parents=True, exist_ok=True)
    try:
        total = count_frames(source)
        first = next(iter_frames(source), None)
    except (av.error.FFmpegError, OSError) as exc:
        raise StageError(
            f"{source.name} has no video frames (could not decode it: {exc})."
        ) from exc
    if first is None:
        raise StageError(f"{source.name} has no video frames.")
    height, width = first.shape[:2]

    backgrounds = iter_frames(background) if background else None
    if background:
        if count_frames(background) != total:
            raise StageError(
                "Depth composite and original differ in frame count; rebuild depth first."
            )
        bg_height, bg_width = next(iter_frames(background)).shape[:2]
        if (bg_height, bg_width) != (height, width):
            raise StageError(
                f"Depth composite is {bg_width}x{bg_height} but the original is {width}x{height}; "
                "rebuild depth first."
            )
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
            # 0.15 confidences kept from the original worker on purpose:
            # favors tracking continuity over precision.
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
        box = crop_box(width, height)
        x0, crop_w, crop_h = box
        crop = np.ascontiguousarray(frame[0:crop_h, x0 : x0 + crop_w])
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=crop)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        return [
            to_full_frame([(p.x, p.y) for p in face], box, width, height)
            for face in result.face_landmarks
        ]

    def close(self) -> None:
        self._landmarker.close()
