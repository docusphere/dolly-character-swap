import json

import pytest

from character_swap.providers.errors import ProviderError
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.wan_replicate import WAN_MODEL, WanGenerator
from fakes import FakeReplicate

OUT = "https://o/wan.mp4"


def request(tmp_path, seconds=4.0):
    video = tmp_path / "original.mp4"
    video.write_bytes(b"v")
    face = tmp_path / "character-primary.png"
    face.write_bytes(b"i")
    return GenerationRequest(video=video, primary=CharacterRef(face), seconds=seconds, seed=1234)


def fake():
    return FakeReplicate(outputs={WAN_MODEL: OUT}, files={OUT: b"mp4"})


def test_estimate_is_per_output_second_by_resolution(tmp_path):
    wan = WanGenerator(fake())
    req = request(tmp_path, seconds=10.0)
    assert wan.estimate(req, "draft").total == pytest.approx(0.20)
    assert wan.estimate(req, "final").total == pytest.approx(0.50)
    assert "480p" in wan.estimate(req, "draft").describe()


@pytest.mark.parametrize(("stage", "resolution"), [("draft", "480"), ("final", "720")])
def test_generate_sends_one_request_with_the_job_seed(tmp_path, stage, resolution):
    replicate = fake()
    dest = tmp_path / f"{stage}-raw.mp4"
    out = WanGenerator(replicate).generate(
        request(tmp_path), stage, record=tmp_path / f"{stage}.json", dest=dest
    )
    assert out == dest and dest.read_bytes() == b"mp4"
    assert [path.name for path in replicate.uploads] == ["original.mp4", "character-primary.png"]
    assert replicate.runs == [
        {
            "key": WAN_MODEL,
            "input": {
                "video": "https://fake.replicate/original.mp4",
                "character_image": "https://fake.replicate/character-primary.png",
                "resolution": resolution,
                "seed": 1234,
                "merge_audio": False,
                "go_fast": False,
            },
        }
    ]


def test_resume_reuses_the_saved_prediction_without_uploading(tmp_path):
    replicate = fake()
    record = tmp_path / "draft.json"
    record.write_text(json.dumps({"id": "p1", "status": "succeeded"}))
    WanGenerator(replicate).generate(
        request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
    )
    assert replicate.uploads == [] and replicate.runs == []


def test_on_created_reports_the_new_prediction(tmp_path):
    seen = []
    WanGenerator(fake()).generate(
        request(tmp_path),
        "draft",
        record=tmp_path / "r.json",
        dest=tmp_path / "raw.mp4",
        on_created=seen.append,
    )
    assert seen == [{"id": "pred-1", "status": "starting"}]


@pytest.mark.parametrize("bad", [None, ["https://o/a.mp4"], "not-a-url", {"video": OUT}])
def test_unexpected_output_is_a_provider_error(tmp_path, bad):
    replicate = FakeReplicate(outputs={WAN_MODEL: bad})
    with pytest.raises(ProviderError, match="unexpected output"):
        WanGenerator(replicate).generate(
            request(tmp_path), "draft", record=tmp_path / "r.json", dest=tmp_path / "raw.mp4"
        )


def test_expired_download_names_the_record_and_the_rebuy_cost(tmp_path):
    replicate = FakeReplicate(outputs={WAN_MODEL: OUT}, fail_download={OUT})
    record = tmp_path / "draft-prediction.json"
    with pytest.raises(ProviderError, match=r"Delete .*draft-prediction\.json.*about \$0\.08"):
        WanGenerator(replicate).generate(
            request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
        )
    assert record.exists()  # never deleted for the user: deleting it means paying again


def test_limits_and_capabilities():
    assert (WanGenerator.min_seconds, WanGenerator.max_seconds) == (1, 30)
    assert WanGenerator.supports_secondary is False and WanGenerator.provider == "replicate"
    assert WanGenerator.model == WAN_MODEL == "wan-video/wan-2.2-animate-replace"


def test_wan_has_one_final_and_never_upgrades():
    assert WanGenerator.finals == ("720p",) and WanGenerator.promotes is False
