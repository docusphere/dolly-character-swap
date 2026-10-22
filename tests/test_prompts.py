from character_swap.prompts import build_prompt


def test_depth_prompt_mentions_depth_and_references():
    text = build_prompt(mode="depth")
    assert "@video1" in text and "@image1" in text and "depth" in text.lower()
    assert "@image2" not in text


def test_face_mesh_prompt_removes_mesh_lines():
    assert "remove every mesh line" in build_prompt(mode="face_mesh").lower()


def test_depth_mesh_mentions_both():
    text = build_prompt(mode="depth_mesh").lower()
    assert "depth" in text and "mesh" in text


def test_scene_transcript_and_secondary_are_included():
    text = build_prompt(mode="depth", scene="A loft.", transcript="Hi there", has_secondary=True)
    assert "Setting: A loft." in text
    assert '"Hi there"' in text
    assert "@image2" in text


def test_blank_scene_and_transcript_are_skipped():
    text = build_prompt(mode="depth", scene="  ", transcript="")
    assert "Setting:" not in text and "spoken words" not in text


def test_untrusted_text_cannot_inject_reference_tokens():
    import re

    text = build_prompt(mode="depth", scene="see @Image3", transcript="say @image9 and @Video2 now")
    assert "@image9" not in text and "@image3" not in text.lower() and "@video2" not in text.lower()
    assert set(re.findall(r"@(?:image|video|audio)\d", text.lower())) == {"@video1", "@image1"}


def test_quotes_and_newlines_are_neutralized():
    text = build_prompt(mode="depth", transcript='He said "hi"\n\nthere')
    assert "\"He said 'hi' there\"" in text


def test_long_transcript_is_truncated_to_limit():
    from character_swap.prompts import MAX_PROMPT

    text = build_prompt(
        mode="depth", scene="x" * 1000, transcript="word " * 2000, has_secondary=True
    )
    assert len(text) <= MAX_PROMPT
    assert "…" in text


def test_validate_prompt_limit():
    import pytest

    from character_swap.prompts import validate_prompt

    assert validate_prompt("a" * 4000) == "a" * 4000
    with pytest.raises(ValueError, match="Prompt is 4001 characters; the limit is 4000."):
        validate_prompt("a" * 4001)
