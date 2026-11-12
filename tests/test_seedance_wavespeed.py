import pytest

from character_swap.providers.errors import ProviderError
from character_swap.providers.generators.base import CharacterRef, GenerationRequest
from character_swap.providers.generators.seedance_wavespeed import SeedanceWaveSpeedGenerator

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
