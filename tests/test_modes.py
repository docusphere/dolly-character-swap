from character_swap import costs, jobs, pipeline, review
from character_swap.jobs import Job
from character_swap.modes import GENERATION_STAGES, GENERATOR_LIMITS, MODES, MODES_BY_GENERATOR


def test_every_derived_table_reads_the_one_mode_table():
    for name, mode in MODES.items():
        assert pipeline.PREP_STAGES_BY_MODE[name] == mode.prep_stages
        assert pipeline.PAID_STAGES[name] == list(mode.paid_stages)
        assert costs.PREP_STEPS[name] == [costs.STAGE_PRICE[s] for s in mode.paid_stages]
    assert jobs.MODES_BY_GENERATOR is MODES_BY_GENERATOR
    assert pipeline.GENERATOR_LIMITS is GENERATOR_LIMITS


def test_modes_by_generator_keeps_the_cli_order():
    assert MODES_BY_GENERATOR == {
        "seedance": ("depth", "face_mesh", "depth_mesh"),
        "wan": ("direct",),
    }


def test_paid_stages_are_prep_stages_and_generation_is_separate():
    assert GENERATION_STAGES == ("draft", "final")
    for mode in MODES.values():
        assert mode.prep_stages[0] == "normalize"
        assert set(mode.paid_stages) <= set(mode.prep_stages)
        assert not set(GENERATION_STAGES) & set(mode.prep_stages)


def test_only_wan_generates_in_this_version():
    assert [name for name, mode in MODES.items() if mode.generates] == ["direct"]
    assert MODES["direct"].generator == "wan"


def test_review_files_come_from_the_table(runs_dir, tmp_path):
    video, face = tmp_path / "in.mp4", tmp_path / "me.png"
    video.write_bytes(b"v")
    face.write_bytes(b"i")
    mesh = Job.create(runs_dir, video, generator="seedance", mode="depth_mesh")
    assert review.review_files(mesh) == list(MODES["depth_mesh"].review_files)
    wan = Job.create(runs_dir, video, generator="wan", mode="direct", character=face)
    assert review.review_files(wan) == ["original.mp4", "character-primary.png"]
