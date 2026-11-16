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
from character_swap.stages.masks import SAM_VERSION, NoMasks, segment
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


def make_zip(*names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"png")
    return buffer.getvalue()


def sam_fake(tmp_path, data, **kwargs):
    video = tmp_path / "depth.mp4"
    video.write_bytes(b"v")
    fake = FakeReplicate(
        outputs={SAM_VERSION: "https://o/m.zip"}, files={"https://o/m.zip": data}, **kwargs
    )
    return video, fake


def test_segment_ignores_path_traversal(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("../evil/mask_00000.png"))
    raw = segment(video, tmp_path / "sam", "person", fake)
    assert [p.name for p in raw.iterdir()] == ["mask_00000.png"]
    assert not (tmp_path / "evil").exists() and not (tmp_path / "sam" / "evil").exists()


def test_segment_empty_zip_is_error(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("a.txt", "b.txt"))
    with pytest.raises(StageError, match="a.txt"):
        segment(video, tmp_path / "sam", "person", fake)


def test_segment_rejects_non_string_output(tmp_path):
    video, fake = sam_fake(tmp_path, b"")
    fake.outputs[SAM_VERSION] = ["x"]
    with pytest.raises(StageError, match="sam-prediction.json"):
        segment(video, tmp_path / "sam", "person", fake)


def test_segment_expired_download_names_record(tmp_path):
    video, fake = sam_fake(tmp_path, b"", fail_download={"https://o/m.zip"})
    with pytest.raises(StageError, match=r"Delete .*sam-prediction\.json"):
        segment(video, tmp_path / "sam", "person", fake)


def test_depth_replicate_resumes_from_record_without_schema_lookup(tmp_path):
    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    record = tmp_path / "rec.json"
    record.write_text('{"id": "p1", "status": "succeeded"}')
    fake = FakeReplicate(outputs={DEPTH_MODEL: "https://o/d.mp4"}, files={"https://o/d.mp4": b"d"})
    depth_replicate(src, tmp_path / "depth.mp4", fake, record)
    assert fake.latest_calls == 0 and fake.runs == []


def test_depth_replicate_expired_download_names_record(tmp_path):
    src = tmp_path / "original.mp4"
    src.write_bytes(b"v")
    fake = FakeReplicate(
        outputs={f"{DEPTH_MODEL}-latest": "https://o/d.mp4"}, fail_download={"https://o/d.mp4"}
    )
    with pytest.raises(StageError, match=r"Delete .*rec\.json"):
        depth_replicate(src, tmp_path / "d.mp4", fake, tmp_path / "rec.json")


@pytest.mark.parametrize("bad", [{"depth_video": 5}, {"depth": ["x"]}, [7]])
def test_pick_depth_url_requires_string(bad):
    with pytest.raises(StageError):
        pick_depth_url(bad)


@pytest.mark.parametrize(
    "result",
    [[None], [None, None], [None, {"video": None}], [None, {"video": {"path": None}}], None],
)
def test_depth_huggingface_rejects_bad_results(tmp_path, result):
    class Client:
        def predict(self, *args, **kwargs):
            return result

    src = tmp_path / "o.mp4"
    src.write_bytes(b"v")
    with pytest.raises(StageError):
        depth_huggingface(src, tmp_path / "d.mp4", client_factory=Client)


def test_depth_huggingface_nested_video_path(tmp_path):
    produced = tmp_path / "hf.mp4"
    produced.write_bytes(b"hf")

    class Client:
        def predict(self, *args, **kwargs):
            return [None, {"video": {"path": str(produced)}}]

    src = tmp_path / "o.mp4"
    src.write_bytes(b"v")
    depth_huggingface(src, tmp_path / "d.mp4", client_factory=Client)
    assert (tmp_path / "d.mp4").read_bytes() == b"hf"


def test_segment_bad_zip_names_record(tmp_path):
    video, fake = sam_fake(tmp_path, b"not a zip")
    with pytest.raises(StageError, match=r"sam-prediction\.json"):
        segment(video, tmp_path / "sam", "person", fake)


def test_segment_transient_download_says_retry_without_charge(tmp_path):
    video, fake = sam_fake(tmp_path, b"", flaky_download={"https://o/m.zip"})
    with pytest.raises(StageError, match="no new charge") as info:
        segment(video, tmp_path / "sam", "person", fake)
    assert "Delete" not in str(info.value)


def test_segment_resume_does_not_rerun(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("mask_00000.png"))
    segment(video, tmp_path / "sam", "person", fake)
    segment(video, tmp_path / "sam", "person", fake)
    assert len(fake.runs) == 1


def test_pick_depth_url_skips_non_string_values():
    assert pick_depth_url({"depth_meta": {"a": 1}, "depth_video": "u"}) == "u"


def test_segment_reports_a_new_paid_run(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("mask_00000.png"))
    created = []
    segment(video, tmp_path / "sam", "person", fake, on_created=created.append)
    segment(video, tmp_path / "sam", "person", fake, on_created=created.append)  # resumed
    assert created == [{"id": "pred-1", "status": "starting"}]


def test_a_zip_without_masks_is_no_masks(tmp_path):
    video, fake = sam_fake(tmp_path, make_zip("a.txt"))
    with pytest.raises(NoMasks, match="no mask_"):
        segment(video, tmp_path / "sam", "person", fake)
    assert issubclass(NoMasks, StageError)


def test_a_damaged_zip_is_not_no_masks(tmp_path):
    video, fake = sam_fake(tmp_path, b"not a zip")
    with pytest.raises(StageError, match="not a valid zip") as info:
        segment(video, tmp_path / "sam", "person", fake)
    assert not isinstance(info.value, NoMasks)
