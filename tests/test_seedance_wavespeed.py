import dataclasses
import json

import pytest

from character_swap.errors import CharSwapError
from character_swap.media.upload import UploadTooLarge
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.generators import seedance_wavespeed
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator
from fakes import FakeWaveSpeed

PROMPT = "Replace the person in @Video 1 with the character in @Image 1."


def request(tmp_path, *, seconds=4.0, **extra):
    video = tmp_path / "prepared.mp4"
    video.write_bytes(b"v")
    image = tmp_path / "character-primary.jpg"
    image.write_bytes(b"i")
    return GenerationRequest(
        video=video, primary=CharacterRef(image), seconds=seconds, seed=1, prompt=PROMPT, **extra
    )


def test_estimates_bill_input_and_output_seconds(tmp_path):
    gen = SeedanceWaveSpeedGenerator(None)
    assert gen.estimate(request(tmp_path), "draft").total == pytest.approx(0.88)
    assert gen.estimate(request(tmp_path), "final").total == pytest.approx(1.76)
    req = request(tmp_path, final_resolution="1080p")
    assert gen.estimate(req, "final").total == pytest.approx(4.40)
    assert "input video" in gen.estimate(req, "final").describe()


@pytest.mark.parametrize(("seconds", "billed"), [(2.0, 4), (4.2, 5), (12.0, 12), (31.0, 30)])
def test_billed_seconds_round_up_and_stay_between_4_and_30(tmp_path, seconds, billed):
    estimate = SeedanceWaveSpeedGenerator(None).estimate(
        request(tmp_path, seconds=seconds), "draft"
    )
    assert estimate.total == pytest.approx(billed * 2 * 0.11)


def test_the_draft_is_480p_and_the_default_final_is_720p(tmp_path):
    gen = SeedanceWaveSpeedGenerator
    assert gen.resolution(request(tmp_path), "draft") == "480p"
    assert gen.resolution(request(tmp_path), "final") == "720p"
    assert gen.resolution(request(tmp_path, final_resolution="1080p"), "final") == "1080p"


@pytest.mark.parametrize("bad", ["480p", "4k"])
def test_other_final_resolutions_are_refused(tmp_path, bad):
    with pytest.raises(ProviderError, match="720p or 1080p, not .*Nothing was charged"):
        SeedanceWaveSpeedGenerator(None).estimate(request(tmp_path, final_resolution=bad), "final")


def test_build_input_is_the_video_edit_body(tmp_path):
    body = SeedanceWaveSpeedGenerator.build_input(
        request(tmp_path), video_url="https://v", image_urls=["https://i"], resolution="480p"
    )
    assert body == {
        "prompt": PROMPT,
        "video": "https://v",
        "reference_images": ["https://i"],
        "resolution": "480p",
        "generate_audio": False,
    }


def test_capabilities():
    gen = SeedanceWaveSpeedGenerator
    assert (gen.name, gen.provider) == ("seedance", "wavespeed")
    assert gen.model == "bytedance/seedance-2.5/video-edit"
    assert (gen.min_seconds, gen.max_seconds) == (4, 30)
    assert gen.supports_real_faces is True and gen.supports_secondary is False
    assert gen.finals == ("720p", "1080p") and gen.promotes is False


def generate(gen, req, stage, tmp_path, **kw):
    return gen.generate(
        req,
        stage,
        record=tmp_path / f"{stage}-prediction.json",
        dest=tmp_path / f"{stage}-raw.mp4",
        **kw,
    )


def test_a_draft_uploads_the_clip_and_photo_and_sends_one_body(tmp_path):
    fake = FakeWaveSpeed()
    created = []
    gen = SeedanceWaveSpeedGenerator(fake)
    out = generate(gen, request(tmp_path), "draft", tmp_path, on_created=created.append)
    assert out.read_bytes() == b"mp4" and [c["id"] for c in created] == ["pred-1"]
    assert [p.name for p in fake.uploads] == ["prepared.mp4", "character-primary.jpg"]
    assert fake.bodies == [
        {
            "prompt": PROMPT,
            "video": "https://cdn.wavespeed.ai/uploads/prepared.mp4",
            "reference_images": ["https://cdn.wavespeed.ai/uploads/character-primary.jpg"],
            "resolution": "480p",
            "generate_audio": False,
        }
    ]


@pytest.mark.parametrize(("chosen", "sent"), [("", "720p"), ("1080p", "1080p")])
def test_a_final_is_a_fresh_run_never_an_upgrade(tmp_path, chosen, sent):
    fake = FakeWaveSpeed()
    req = request(tmp_path, final_resolution=chosen, draft_task_id="pred-1")
    generate(SeedanceWaveSpeedGenerator(fake), req, "final", tmp_path)
    (body,) = fake.bodies
    assert body["resolution"] == sent and body["prompt"] == PROMPT
    assert "draft_task_id" not in body and len(fake.uploads) == 2


@pytest.mark.parametrize(
    ("prompt", "words"),
    [
        ("  ", "charswap prompt"),
        ("Swap @video1 for @image1.", "write @Video 1 instead"),
        ("Swap @Video 1, nothing else.", "must mention @Image 1"),
    ],
)
def test_prompt_problems_stop_before_any_upload(tmp_path, prompt, words):
    fake = FakeWaveSpeed()
    req = dataclasses.replace(request(tmp_path), prompt=prompt)
    with pytest.raises(CharSwapError, match=words):
        generate(SeedanceWaveSpeedGenerator(fake), req, "draft", tmp_path)
    assert fake.uploads == [] and fake.bodies == []


def test_a_second_person_stops_before_any_upload(tmp_path):
    fake = FakeWaveSpeed()
    second = tmp_path / "character-secondary.jpg"
    second.write_bytes(b"2")
    req = request(tmp_path, secondary=CharacterRef(second))
    with pytest.raises(ProviderError, match="--provider piapi.*Nothing was charged"):
        generate(SeedanceWaveSpeedGenerator(fake), req, "draft", tmp_path)
    assert fake.uploads == [] and fake.bodies == []


def test_resume_reuses_the_saved_prediction_without_uploading(tmp_path):
    fake = FakeWaveSpeed()
    (tmp_path / "draft-prediction.json").write_text(json.dumps({"id": "p1", "status": "succeeded"}))
    generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert fake.bodies == [] and fake.uploads == []


def test_a_big_prepared_video_is_uploaded_as_a_smaller_copy(tmp_path, monkeypatch):
    seen = []

    def fake_fit(src, dst, max_bytes):
        seen.append((src.name, dst.name, max_bytes))
        dst.write_bytes(b"small")
        return dst

    monkeypatch.setattr(seedance_wavespeed, "fit_for_upload", fake_fit)
    fake = FakeWaveSpeed()
    generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert seen == [("prepared.mp4", "upload-copy.mp4", 209_715_200)]
    assert [p.name for p in fake.uploads] == ["upload-copy.mp4", "character-primary.jpg"]
    assert not (tmp_path / "upload-copy.mp4").exists()  # private media is removed after upload


def test_a_video_too_big_even_compressed_stops_before_paying(tmp_path, monkeypatch):
    def too_big(src, dst, max_bytes):
        raise UploadTooLarge("prepared.mp4 is still over 210 MB after compressing.")

    monkeypatch.setattr(seedance_wavespeed, "fit_for_upload", too_big)
    fake = FakeWaveSpeed()
    with pytest.raises(ProviderError, match="after compressing.*Nothing was charged"):
        generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert fake.bodies == [] and fake.uploads == []
    assert not (tmp_path / "draft-prediction.json").exists()


def test_an_expired_download_names_the_record_and_the_rebuy_cost(tmp_path):
    class Expiring(FakeWaveSpeed):
        def download(self, url, dest):
            raise OutputExpired("gone")

    record = tmp_path / "draft-prediction.json"
    with pytest.raises(ProviderError, match=r"expired .*Delete .*draft-prediction\.json.*\$0\.88"):
        SeedanceWaveSpeedGenerator(Expiring()).generate(
            request(tmp_path), "draft", record=record, dest=tmp_path / "raw.mp4"
        )
    assert record.exists()


def test_a_missing_character_photo_stops_before_any_upload(tmp_path):
    fake = FakeWaveSpeed()
    req = request(tmp_path)
    req.primary.image_path.unlink()
    with pytest.raises(ProviderError, match="character photo .*missing. Nothing was charged"):
        generate(SeedanceWaveSpeedGenerator(fake), req, "draft", tmp_path)
    assert fake.uploads == [] and fake.bodies == []


def test_the_compressed_copy_is_removed_even_when_its_upload_fails(tmp_path, monkeypatch):
    class FailingUpload(FakeWaveSpeed):
        def upload(self, path):
            super().upload(path)
            raise ProviderError("upload broke")

    def fake_fit(src, dst, max_bytes):
        dst.write_bytes(b"small")
        return dst

    monkeypatch.setattr(seedance_wavespeed, "fit_for_upload", fake_fit)
    fake = FailingUpload()
    with pytest.raises(ProviderError, match="upload broke"):
        generate(SeedanceWaveSpeedGenerator(fake), request(tmp_path), "draft", tmp_path)
    assert [p.name for p in fake.uploads] == ["upload-copy.mp4"]
    assert not (tmp_path / "upload-copy.mp4").exists() and fake.bodies == []


def test_the_model_is_defined_once_by_the_client():
    from character_swap.providers import wavespeed

    assert seedance_wavespeed.MODEL is wavespeed.MODEL
    assert SeedanceWaveSpeedGenerator.model == wavespeed.MODEL
