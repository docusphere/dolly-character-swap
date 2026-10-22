import io
import zipfile

import pytest

from character_swap.stages import StageError
from character_swap.stages.depth import (
    DEPTH_MODEL,
    depth_huggingface,
    depth_replicate,
    pick_depth_url,
)
from character_swap.stages.masks import SAM_VERSION, segment
from fakes import FakeReplicate


def test_depth_replicate_resolves_schema_and_downloads(tmp_path):
    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    fake = FakeReplicate(
        outputs={f"{DEPTH_MODEL}-latest": "https://o/depth.mp4"},
        files={"https://o/depth.mp4": b"depth"},
        schema={"input_video": {"format": "uri"}, "model_size": {"type": "string"}},
    )
    depth_replicate(src, tmp_path / "depth.mp4", fake, tmp_path / "rec.json")
    assert (tmp_path / "depth.mp4").read_bytes() == b"depth"
    assert "input_video" in fake.runs[0]["input"]


def test_depth_replicate_without_video_input_fails(tmp_path):
    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    fake = FakeReplicate(schema={"image": {"format": "uri"}})
    with pytest.raises(StageError, match="video input"):
        depth_replicate(src, tmp_path / "d.mp4", fake, tmp_path / "r.json")


@pytest.mark.parametrize(
    ("output", "expected"),
    [("u", "u"), (["a", "b"], "a"), ({"rgb": "x", "depth_video": "d"}, "d")],
)
def test_pick_depth_url(output, expected):
    assert pick_depth_url(output) == expected


def test_pick_depth_url_rejects_unknown_shape():
    with pytest.raises(StageError):
        pick_depth_url({"rgb": "x"})


def test_depth_huggingface_uses_injected_client(tmp_path):
    result_file = tmp_path / "hf.mp4"
    result_file.write_bytes(b"hf")

    class FakeClient:
        def predict(self, *args, **kwargs):
            return [None, {"video": str(result_file)}]

    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    depth_huggingface(src, tmp_path / "depth.mp4", client_factory=lambda: FakeClient())
    assert (tmp_path / "depth.mp4").read_bytes() == b"hf"


def test_segment_extracts_mask_pngs(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("out/mask_00000.png", b"png0")
        archive.writestr("out/mask_00001.png", b"png1")
        archive.writestr("out/readme.txt", b"ignore")
    video = tmp_path / "depth.mp4"
    video.write_bytes(b"v")
    fake = FakeReplicate(
        outputs={SAM_VERSION: "https://o/m.zip"}, files={"https://o/m.zip": buffer.getvalue()}
    )
    raw = segment(video, tmp_path / "sam", "person", fake)
    assert sorted(p.name for p in raw.iterdir()) == ["mask_00000.png", "mask_00001.png"]
    assert fake.runs[0]["input"] == {
        "video": "https://fake.replicate/depth.mp4",
        "prompt": "person",
        "mask_only": True,
        "return_zip": True,
    }
