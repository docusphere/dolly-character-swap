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
