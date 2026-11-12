import re

import pytest

from character_swap.prompts import (
    MAX_PROMPT,
    PromptError,
    build_prompt,
    check_placeholders,
    validate_prompt,
)


def test_depth_prompt_mentions_depth_and_references():
    text = build_prompt(provider="piapi", mode="depth")
    assert "@video1" in text and "@image1" in text and "depth" in text.lower()
    assert "@image2" not in text


def test_piapi_wording_is_unchanged():
    assert build_prompt(provider="piapi", mode="depth").startswith(
        "Replace the person in @video1 with the character in @image1 in every frame, including "
        "face, hair, hands, clothing and body shape."
    )


def test_wavespeed_prompts_use_wavespeeds_input_names():
    for mode in ("depth", "face_mesh", "depth_mesh"):
        text = build_prompt(provider="wavespeed", mode=mode, transcript="hi")
        assert "@Video 1" in text and "@Image 1" in text
        assert "@video1" not in text and "@image1" not in text


def test_an_unknown_provider_is_refused():
    with pytest.raises(PromptError, match="Unknown Seedance provider 'sora'"):
        build_prompt(provider="sora", mode="depth")


def test_face_mesh_prompt_removes_mesh_lines():
    assert "remove every mesh line" in build_prompt(provider="piapi", mode="face_mesh").lower()


def test_depth_mesh_mentions_both():
    text = build_prompt(provider="wavespeed", mode="depth_mesh").lower()
    assert "depth" in text and "mesh" in text


def test_scene_transcript_and_secondary_are_included():
    text = build_prompt(
        provider="piapi", mode="depth", scene="A loft.", transcript="Hi there", has_secondary=True
    )
    assert "Setting: A loft." in text
    assert '"Hi there"' in text
    assert "@image2" in text


def test_blank_scene_and_transcript_are_skipped():
    text = build_prompt(provider="piapi", mode="depth", scene="  ", transcript="")
    assert "Setting:" not in text and "spoken words" not in text


@pytest.mark.parametrize(
    ("provider", "names"),
    [("piapi", {"@video1", "@image1"}), ("wavespeed", {"@video 1", "@image 1"})],
)
def test_untrusted_text_cannot_inject_reference_tokens(provider, names):
    text = build_prompt(
        provider=provider,
        mode="depth",
        scene="see @Image3 and @Image 3",
        transcript="say @image9 and @Video2 and @@image9 and @@@video2 and @Video 2 now",
    )
    assert "@image9" not in text and "@image3" not in text.lower() and "@video2" not in text.lower()
    assert set(re.findall(r"@(?:image|video|audio) ?\d", text.lower())) == names


def test_quotes_and_newlines_are_neutralized():
    text = build_prompt(provider="piapi", mode="depth", transcript='He said "hi"\n\nthere')
    assert "\"He said 'hi' there\"" in text


def test_long_transcript_is_truncated_to_limit():
    text = build_prompt(
        provider="piapi",
        mode="depth",
        scene="x" * 1000,
        transcript="word " * 2000,
        has_secondary=True,
    )
    assert len(text) <= MAX_PROMPT
    assert "…" in text


def test_validate_prompt_limit():
    assert validate_prompt("a" * 4000) == "a" * 4000
    with pytest.raises(ValueError, match="Prompt is 4001 characters; the limit is 4000."):
        validate_prompt("a" * 4001)


@pytest.mark.parametrize(
    ("provider", "text", "secondary"),
    [
        ("wavespeed", "Put @Image 1 into @Video 1.", False),
        ("piapi", "Put @image1 into @video1.", False),
        ("piapi", "Put @image1 and @image2 into @video1.", True),
    ],
)
def test_check_placeholders_accepts_the_providers_names(provider, text, secondary):
    check_placeholders(text, provider=provider, has_secondary=secondary)


@pytest.mark.parametrize(
    ("provider", "text", "secondary", "words"),
    [
        ("wavespeed", "Swap @video1 for @image1.", False, "must mention @Video 1"),
        ("wavespeed", "Swap @Video 1, nothing else.", False, "must mention @Image 1"),
        ("wavespeed", "@Video 1 @Image 1 @Image 2", False, "mentions @Image 2"),
        ("piapi", "Swap @video1 please.", False, "must mention @image1"),
        ("piapi", "@video1 @image1", True, "must mention @image2"),
        ("piapi", "@video1 @image1 and @image2", False, "mentions @image2"),
    ],
)
def test_check_placeholders_refuses_names_that_do_not_match(provider, text, secondary, words):
    with pytest.raises(PromptError, match=words):
        check_placeholders(text, provider=provider, has_secondary=secondary)


@pytest.mark.parametrize(
    ("provider", "text", "secondary", "words"),
    [
        ("wavespeed", "@Video 10 and @Image 1", False, "must mention @Video 1"),
        ("wavespeed", "@Video 1 and @Image 12", False, "must mention @Image 1"),
        ("wavespeed", "@Video 1 @Image 1 @Image 20", True, "must mention @Image 2"),
        ("piapi", "@video10 and @image1", False, "must mention @video1"),
        ("piapi", "@video1 and @image10", False, "must mention @image1"),
        ("piapi", "@video1 @image1 @image20", True, "must mention @image2"),
    ],
)
def test_check_placeholders_does_not_count_longer_numbers(provider, text, secondary, words):
    with pytest.raises(PromptError, match=words):
        check_placeholders(text, provider=provider, has_secondary=secondary)


@pytest.mark.parametrize(
    ("provider", "text"),
    [
        ("wavespeed", "@Video 1 @Image 1 and @Image 20."),
        ("piapi", "@video1 @image1 and @image20."),
    ],
)
def test_check_placeholders_allows_longer_numbers_when_there_is_no_second_character(provider, text):
    check_placeholders(text, provider=provider, has_secondary=False)


@pytest.mark.parametrize("provider", ["piapi", "wavespeed"])
def test_braces_in_scene_and_transcript_survive_unchanged(provider):
    text = build_prompt(
        provider=provider, mode="depth", scene="{video} {0} {", transcript="{image1} }{"
    )
    assert "Setting: {video} {0} {" in text
    assert '"{image1} }{"' in text
