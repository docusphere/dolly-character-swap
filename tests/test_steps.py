from pathlib import Path

import pytest

from character_swap.modes import MODES
from character_swap.web import steps

SEEDANCE_MODES = ("depth", "face_mesh", "depth_mesh")


def _texts():
    for item in steps.STEPS:
        yield item.title
        yield item.heading
        yield item.line
        for tag in item.tags:
            yield tag.tip
    for tab in steps.TABS:
        yield tab.title
        yield tab.line
        for tag in tab.tags:
            yield tag.tip
    yield from steps.HOW_IT_WORKS
    for _mode, label, hint in steps.LOOKS:
        yield label
        yield hint
    for name in dir(steps):
        value = getattr(steps, name)
        if name.isupper() and isinstance(value, str):
            yield value


def test_five_steps_in_order():
    assert [s.number for s in steps.STEPS] == [1, 2, 3, 4, 5]
    assert [s.title for s in steps.STEPS] == ["Upload", "Prepare", "Check", "Draft", "Final"]
    assert steps.step(4).id == "draft"
    with pytest.raises(ValueError, match="no step 6"):
        steps.step(6)


def test_every_text_is_short_plain_and_has_no_em_dash():
    for text in _texts():
        assert text and "—" not in text, text
    for item in steps.STEPS:
        assert len(item.line) <= 90, item.line  # one short line per step
    for tab in steps.TABS:
        assert len(tab.line) <= 90, tab.line


def test_every_tag_has_a_one_sentence_tip():
    tags = [tag for item in steps.STEPS for tag in item.tags]
    tags += [tag for tab in steps.TABS for tag in tab.tags]
    names = {tag.name for tag in tags}
    assert {"SAM 3", "Depth Anything", "MediaPipe Face Mesh", "Whisper", "Demucs"} <= names
    for tag in tags:
        assert tag.tip.endswith(".") and tag.tip.count(". ") == 0, tag.name


@pytest.mark.parametrize(
    ("mode", "ids"),
    [
        ("depth", ["depth", "cutout", "prepared", "voice"]),
        ("depth_mesh", ["depth", "cutout", "face", "prepared", "voice"]),
        ("face_mesh", ["face", "prepared", "voice"]),
        ("direct", []),
    ],
)
def test_tabs_per_mode(mode, ids):
    assert [tab.id for tab in steps.tabs_for(mode)] == ids


@pytest.mark.parametrize("mode", SEEDANCE_MODES)
def test_tabs_cover_the_modes_prep_stages(mode):
    covered = {stage for tab in steps.tabs_for(mode) for stage in tab.stages}
    # normalize makes the "before" side of every wipe; describe feeds the prompt only
    assert covered | {"normalize", "describe"} == set(MODES[mode].prep_stages)


def test_tab_files():
    by_id = {tab.id: tab for tab in steps.TABS}
    assert by_id["depth"].after == "depth.mp4"
    assert by_id["cutout"].after == "@composite"  # depth-composite-silent.mp4 in depth_mesh
    assert by_id["face"].after == "composite-silent.mp4"
    assert by_id["prepared"].after == "prepared.mp4"
    assert by_id["voice"].after == ""  # two audio players, no wipe


def test_looks_default_to_lip_sync():
    assert [mode for mode, _label, _hint in steps.LOOKS] == ["depth_mesh", "depth"]
    assert steps.LOOKS[0][1] == "Better lip sync" and steps.LOOKS[1][1] == "Better likeness"


def test_the_price_question_names_the_amount_and_who_charges_it():
    assert steps.CHARGE.format(usd="$0.88", provider="WaveSpeed") == (
        "Charge about $0.88 on WaveSpeed?"
    )


def test_the_templates_read_their_messages_from_here():
    folder = Path(steps.__file__).parent / "templates"
    text = "".join(path.read_text(encoding="utf-8") for path in folder.glob("*.html"))
    moved = (steps.STEP_FAILED, steps.APPROVED, steps.NO_PHOTO, steps.MADE_WITH_CLI)
    for value in (*moved, steps.UNREADABLE, steps.SPENT_SWAP, steps.SPENT_ALL, "Making the"):
        assert value not in text, value
    assert steps.READ_ONLY.startswith(steps.MADE_WITH_CLI + ". ")
    assert steps.MAKING_FINAL.format(resolution="720p") == "Making the 720p final"
