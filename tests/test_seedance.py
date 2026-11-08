import dataclasses
import json

import pytest

from character_swap.media.upload import UploadTooLarge
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators import seedance_piapi
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.seedance_piapi import TASK_TYPE, SeedanceGenerator
from fakes import FakePiAPI

PROMPT = "Replace the person in @video1 with the character in @image1."


def request(tmp_path, *, seconds=12.0, face="a1", secondary=None, **extra):
    video = tmp_path / "prepared.mp4"
    video.write_bytes(b"v")
    image = tmp_path / "character-primary.jpg"
    image.write_bytes(b"i")
    primary = CharacterRef(image, face_asset_id=face, name="frank" if face else "")
    return GenerationRequest(
        video=video,
        primary=primary,
        secondary=secondary,
        seconds=seconds,
        seed=1,
        prompt=PROMPT,
        **extra,
    )


def generate(gen, req, stage, tmp_path):
    return gen.generate(
        req, stage, record=tmp_path / f"{stage}-prediction.json", dest=tmp_path / f"{stage}-raw.mp4"
    )


def test_estimates_include_the_input_video(tmp_path):
    gen = SeedanceGenerator(FakePiAPI())
    assert gen.estimate(request(tmp_path), "draft").total == pytest.approx(2.97)
    assert gen.estimate(request(tmp_path), "final").total == pytest.approx(15.84)
    req = request(tmp_path, final_resolution="720p")
    assert gen.estimate(req, "final").total == pytest.approx(6.93)
    assert "input video" in gen.estimate(req, "final").describe()


def test_unknown_final_resolution_is_refused(tmp_path):
    with pytest.raises(ProviderError, match="1080p or 720p.*Nothing was charged"):
        SeedanceGenerator(FakePiAPI()).estimate(request(tmp_path, final_resolution="4k"), "final")


def test_draft_sends_one_edit_task_with_the_saved_face(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    out = generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert out.read_bytes() == b"mp4" and piapi.asset_checks == ["a1"]
    assert [p.name for p in piapi.uploads] == ["prepared.mp4"]
    assert piapi.bodies == [
        {
            "model": "seedance",
            "task_type": TASK_TYPE,
            "input": {
                "prompt": PROMPT,
                "mode": "omni_reference",
                "omni_reference_task_type": "edit",
                "video_urls": ["https://img.theapi.app/temp/prepared.mp4"],
                "image_urls": ["asset://a1"],
                "aspect_ratio": "adaptive",
                "duration": -1,
                "resolution": "480p",
                "auto_upload_assets": False,
                "draft": True,
            },
        }
    ]


def test_a_character_image_without_a_face_is_uploaded(tmp_path):
    piapi = FakePiAPI()
    generate(SeedanceGenerator(piapi), request(tmp_path, face=None), "draft", tmp_path)
    assert piapi.bodies[0]["input"]["image_urls"] == [
        "https://img.theapi.app/temp/character-primary.jpg"
    ]
    assert piapi.asset_checks == []


def test_two_characters_keep_their_order(tmp_path):
    second = tmp_path / "character-secondary.jpg"
    second.write_bytes(b"2")
    piapi = FakePiAPI(assets={"a1": "Active", "a2": "Active"})
    req = request(tmp_path, secondary=CharacterRef(second, face_asset_id="a2", name="sam"))
    generate(SeedanceGenerator(piapi), req, "draft", tmp_path)
    assert piapi.bodies[0]["input"]["image_urls"] == ["asset://a1", "asset://a2"]


def test_1080p_final_promotes_the_draft_and_sends_nothing_else(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    generate(SeedanceGenerator(piapi), request(tmp_path, draft_task_id="task-9"), "final", tmp_path)
    assert piapi.bodies == [
        {
            "model": "seedance",
            "task_type": TASK_TYPE,
            "input": {"draft_task_id": "task-9", "resolution": "1080p"},
        }
    ]
    assert piapi.uploads == [] and piapi.asset_checks == []


def test_720p_final_is_a_fresh_run_without_the_draft_flag(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    req = request(tmp_path, final_resolution="720p", draft_task_id="task-9")
    generate(SeedanceGenerator(piapi), req, "final", tmp_path)
    task = piapi.bodies[0]["input"]
    assert task["resolution"] == "720p" and "draft" not in task and task["prompt"] == PROMPT


def test_promote_without_a_draft_id_sends_nothing(tmp_path):
    piapi = FakePiAPI()
    with pytest.raises(ProviderError, match="--resolution 720p"):
        generate(SeedanceGenerator(piapi), request(tmp_path), "final", tmp_path)
    assert piapi.bodies == []


@pytest.mark.parametrize("status", ["Processing", "Failed", None])
def test_a_face_that_is_not_active_stops_before_paying(tmp_path, status):
    piapi = FakePiAPI(assets={} if status is None else {"a1": status})
    with pytest.raises(ProviderError, match="charswap face check frank.*Nothing was charged"):
        generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert piapi.bodies == [] and piapi.uploads == []


def test_an_empty_prompt_stops_before_paying(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    req = dataclasses.replace(request(tmp_path), prompt="  ")
    with pytest.raises(ProviderError, match="charswap prompt"):
        generate(SeedanceGenerator(piapi), req, "draft", tmp_path)
    assert piapi.bodies == []


def test_resume_reuses_the_saved_task_without_uploading(tmp_path):
    piapi = FakePiAPI(assets={"a1": "Active"})
    (tmp_path / "draft-prediction.json").write_text(json.dumps({"id": "t1", "status": "succeeded"}))
    generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert piapi.bodies == [] and piapi.uploads == [] and piapi.asset_checks == []


def test_big_prepared_video_is_uploaded_as_a_smaller_copy(tmp_path, monkeypatch):
    seen = []

    def fake_fit(src, dst, max_bytes):
        seen.append((src.name, dst.name, max_bytes))
        dst.write_bytes(b"small")
        return dst

    monkeypatch.setattr(seedance_piapi, "fit_for_upload", fake_fit)
    piapi = FakePiAPI(assets={"a1": "Active"})
    generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert seen == [("prepared.mp4", "upload-copy.mp4", 7_000_000)]
    assert [p.name for p in piapi.uploads] == ["upload-copy.mp4"]
    assert not (tmp_path / "upload-copy.mp4").exists()  # private media is removed after upload


def test_expired_download_names_the_record_and_the_rebuy_cost(tmp_path):
    class Expiring(FakePiAPI):
        def download(self, url, dest):
            raise OutputExpired("gone")

    record = tmp_path / "draft-prediction.json"
    with pytest.raises(ProviderError, match=r"24 hours.*Delete .*draft-prediction\.json.*\$2\.97"):
        SeedanceGenerator(Expiring(assets={"a1": "Active"})).generate(
            request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
        )
    assert record.exists()


def test_capabilities():
    assert (SeedanceGenerator.min_seconds, SeedanceGenerator.max_seconds) == (4, 30)
    assert SeedanceGenerator.supports_secondary and SeedanceGenerator.provider == "piapi"
    assert SeedanceGenerator.model == TASK_TYPE == "seedance-2.5-less-restriction"


def test_estimates_round_partial_seconds_up(tmp_path):
    gen = SeedanceGenerator(FakePiAPI())
    assert gen.estimate(request(tmp_path, seconds=12.4), "draft").total == pytest.approx(
        gen.estimate(request(tmp_path, seconds=13.0), "draft").total
    )


def test_video_too_big_even_compressed_stops_before_paying(tmp_path, monkeypatch):
    def too_big(src, dst, max_bytes):
        raise UploadTooLarge(
            "prepared.mp4 is still over 7 MB after compressing. Use a shorter clip."
        )

    monkeypatch.setattr(seedance_piapi, "fit_for_upload", too_big)
    piapi = FakePiAPI(assets={"a1": "Active"})
    with pytest.raises(ProviderError, match="shorter clip.*Nothing was charged"):
        generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert piapi.bodies == [] and piapi.uploads == []
    assert not (tmp_path / "draft-prediction.json").exists()


@pytest.mark.parametrize("code", [400, 404, 409, 422])
def test_a_refused_promote_suggests_the_7_day_limit_and_720p(tmp_path, monkeypatch, code):
    from character_swap.providers.piapi import PiAPIRejected

    piapi = FakePiAPI()

    def refuse(**_kw):
        raise PiAPIRejected(f"PiAPI rejected the request ({code}); nothing was charged: x", code)

    monkeypatch.setattr(piapi, "run", refuse)
    with pytest.raises(PiAPIRejected, match="about 7 days; use --resolution 720p") as caught:
        generate(
            SeedanceGenerator(piapi), request(tmp_path, draft_task_id="t-1"), "final", tmp_path
        )
    assert "nothing was charged" in str(caught.value)


def test_a_refused_fresh_run_gets_no_promote_hint(tmp_path, monkeypatch):
    from character_swap.providers.piapi import PiAPIRejected

    piapi = FakePiAPI(assets={"a1": "Active"})

    def refuse(**_kw):
        raise PiAPIRejected("PiAPI rejected the request (400); nothing was charged: x", 400)

    monkeypatch.setattr(piapi, "run", refuse)
    with pytest.raises(PiAPIRejected) as caught:
        generate(SeedanceGenerator(piapi), request(tmp_path), "draft", tmp_path)
    assert "7 days" not in str(caught.value)


def test_a_busy_promote_gets_no_7_day_hint(tmp_path, monkeypatch):
    from character_swap.providers.piapi import PiAPIRejected

    piapi = FakePiAPI()

    def refuse(**_kw):
        raise PiAPIRejected("PiAPI is busy (429). Nothing was charged.", 429)

    monkeypatch.setattr(piapi, "run", refuse)
    with pytest.raises(PiAPIRejected) as caught:
        generate(
            SeedanceGenerator(piapi), request(tmp_path, draft_task_id="t-1"), "final", tmp_path
        )
    assert "7 days" not in str(caught.value)


def test_finals_are_defined_once():
    from character_swap import pipeline

    assert pipeline.FINAL_RESOLUTIONS["seedance"] is seedance_piapi.FINALS
    assert pipeline.PROMOTE is seedance_piapi.PROMOTE
