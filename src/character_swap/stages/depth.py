"""Turn the normalized video into a colored depth video (identity removed, motion kept)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from character_swap.stages import StageError, bad_output, download_or_fail

DEPTH_MODEL = "lucataco/depth-anything-video"
HF_SPACE = "depth-anything/Video-Depth-Anything"


def pick_depth_url(output) -> str:
    url = None
    if isinstance(output, str):
        url = output
    elif isinstance(output, list) and output:
        url = output[0]
    elif isinstance(output, dict):
        url = next(
            (v for k, v in output.items() if "depth" in k and isinstance(v, str) and v), None
        )
    if not isinstance(url, str) or not url:
        raise StageError(f"Depth model returned an unexpected output: {output!r}")
    return url


def depth_replicate(src: Path, dst: Path, replicate, record: Path) -> Path:
    if record.exists():  # resume: run() reads the saved prediction and ignores the input
        output = replicate.run(version=None, model=DEPTH_MODEL, input={}, record=record)
    else:
        version, props = replicate.latest_version(DEPTH_MODEL)
        key = next((k for k, v in props.items() if "video" in k and v.get("format") == "uri"), None)
        if key is None:
            raise StageError(f"{DEPTH_MODEL} no longer has a video input; check its schema.")
        url = replicate.upload(src)
        output = replicate.run(version=version, input={key: url}, record=record)
    try:
        depth_url = pick_depth_url(output)
    except StageError as exc:
        raise bad_output(record, "Depth model", output) from exc
    return download_or_fail(replicate, depth_url, dst, record=record, what="depth", cost="~$0.04")


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
    produced = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else None
    path = produced
    while isinstance(path, dict):
        path = path.get("video") or path.get("path")
    if not isinstance(path, str) or not path:
        raise StageError(f"The Hugging Face depth Space returned an unexpected result: {result!r}")
    shutil.copyfile(path, dst)
    return dst
