"""Turn the normalized video into a colored depth video (identity removed, motion kept)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from character_swap.stages import StageError

DEPTH_MODEL = "lucataco/depth-anything-video"
HF_SPACE = "depth-anything/Video-Depth-Anything"


def pick_depth_url(output) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output:
        return output[0]
    if isinstance(output, dict):
        for key, value in output.items():
            if "depth" in key and value:
                return value
    raise StageError(f"Depth model returned an unexpected output: {output!r}")


def depth_replicate(src: Path, dst: Path, replicate, record: Path) -> Path:
    version, props = replicate.latest_version(DEPTH_MODEL)
    key = next((k for k, v in props.items() if "video" in k and v.get("format") == "uri"), None)
    if key is None:
        raise StageError(f"{DEPTH_MODEL} no longer has a video input; check its schema.")
    url = replicate.upload(src)
    output = replicate.run(version=version, input={key: url}, record=record)
    return replicate.download(pick_depth_url(output), dst)


def _default_hf_client(token: str | None):  # pragma: no cover - network
    from gradio_client import Client

    return Client(HF_SPACE, token=token or None, verbose=False)


def depth_huggingface(
    src: Path, dst: Path, *, token: str | None = None, client_factory: Callable | None = None
) -> Path:
    from gradio_client import handle_file

    client = client_factory() if client_factory else _default_hf_client(token)
    result = client.predict(
        {"video": handle_file(str(src))},
        max_len=-1,
        target_fps=24,
        max_res=960,
        grayscale=False,
        api_name="/infer_video_depth",
    )
    produced = result[1]
    path = produced.get("video") or produced.get("path") if isinstance(produced, dict) else produced
    shutil.copyfile(path, dst)
    return dst
