"""Scene jobs through the pipeline: prepare (Task 9) and generate (Task 10)."""

import json
import shutil

import av
import pytest
from PIL import Image

from character_swap import pipeline
from character_swap.costs import Estimate, Line
from character_swap.jobs import Job, JobError, clean_setting
from character_swap.media.scene import EDGES, SceneError
from character_swap.media.video import count_frames, iter_frames
from character_swap.pipeline import (
    Services,
    archive_stale_draft,
    build_request,
    generate,
    generation_estimate,
    is_scene_job,
    needs_lay_back_charge,
    prepare,
    remaining_paid_stages,
)
from character_swap.review import decide, fingerprint
from character_swap.stages import StageError
from character_swap.stages import paste as paste_stage
from character_swap.stages.describe import DESCRIBE_MODEL
from character_swap.stages.masks import SAM_VERSION
from fakes import FakeReplicate, RefusingReplicate
from helpers import solid, write_shots, write_video
from scene_jobs import (
    BLUE,
    FRAMES,
    GREEN,
    RED,
    TWO,
    drafted_scene_job,
    fake_pitch,
    make_scene_job,
    masks_zip,
    prep_services,
)

QUIET = {"status": lambda _m: None}


@pytest.fixture(autouse=True)
def no_rubberband(monkeypatch):
    monkeypatch.setattr("character_swap.pipeline.pitch_vocals", fake_pitch)


def sam_inputs(services):
    return [run["input"]["video"] for run in services.replicate.runs if run["key"] == SAM_VERSION]


# ---- the job folder ----------------------------------------------------------------
def test_a_scene_job_keeps_the_scene_the_part_and_the_segment(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, setting="  A dim\n room.  ")
    state = job.state
    assert job.path("full-scene.mp4").read_bytes() == (tmp_path / "scene.mp4").read_bytes()
    assert job.path("upload.mp4").read_bytes() == (tmp_path / "part.mp4").read_bytes()
    assert state["segment"]["start_frame"] == 24 and len(state["segment"]["frame_map"]) == 96
    assert state["setting"] == "A dim room." and "scene" not in state
    assert is_scene_job(job)


def test_a_single_shot_job_is_not_a_scene_job(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth")
    assert not is_scene_job(job) and "segment" not in job.state and "setting" not in job.state
    assert not job.path("full-scene.mp4").exists()


def test_a_scene_needs_its_segment(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 24)
    with pytest.raises(JobError, match="both the scene and its segment"):
        Job.create(runs_dir, clip, generator="seedance", mode="depth", scene=clip)
    with pytest.raises(JobError, match="Video not found"):
        Job.create(
            runs_dir,
            clip,
            generator="seedance",
            mode="depth",
            scene=tmp_path / "no.mp4",
            segment={},
        )
    assert not any(runs_dir.iterdir())


@pytest.mark.parametrize("text", ["", "   ", "x" * 301])
def test_a_setting_must_be_1_to_300_characters(text):
    with pytest.raises(JobError, match="Describe the setting in 1 to 300 characters"):
        clean_setting(text)


# ---- prepare -------------------------------------------------------------------------
def test_a_scene_job_cuts_out_its_subject_on_the_colors(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    services = prep_services(tmp_path)
    prepare(job, services, **QUIET)
    assert sam_inputs(services) == ["https://fake.replicate/original.mp4"]
    assert job.state["mask_source"] == "rgb" and job.path("prepared.mp4").exists()


def test_a_single_shot_job_still_cuts_out_on_the_depth_video(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth")
    services = prep_services(tmp_path, boxes=TWO)  # two people: no check on a single shot
    prepare(job, services, **QUIET)
    assert sam_inputs(services) == ["https://fake.replicate/depth.mp4"]
    assert "mask_source" not in job.state and job.stage_done("embed")


def test_two_people_stop_a_scene_job_before_the_face_mesh(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, mode="depth_mesh")
    services = prep_services(tmp_path, boxes=TWO)
    with pytest.raises(StageError, match="more than one person in 96 of 96 frames") as info:
        prepare(job, services, **QUIET)
    message = str(info.value)
    assert str(job.path("mask.mp4")) in message and job.path("mask.mp4").exists()
    assert '--subject "man with white face paint"' in message and "about $0.06" in message
    assert job.state["stages"]["composite"]["status"] == "failed"
    assert "face_mesh" not in job.state["stages"]
    runs = len(services.replicate.runs)
    with pytest.raises(StageError, match="more than one person"):  # a resume checks again
        prepare(job, services, **QUIET)
    assert len(services.replicate.runs) == runs  # the saved masks: nothing bought again


def test_a_setting_you_wrote_replaces_the_paid_description(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path, setting="A dim room.")
    assert "describe" not in remaining_paid_stages(job)
    services = prep_services(tmp_path)
    prepare(job, services, **QUIET)
    assert DESCRIBE_MODEL not in [run["key"] for run in services.replicate.runs]
    assert job.state["scene"] == "A dim room." and job.path("scene.jpg").exists()
    assert job.state["stages"]["describe"]["source"] == "you"


def test_after_a_refused_description_a_setting_finishes_for_free(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    services = prep_services(tmp_path, cls=RefusingReplicate, refuse=DESCRIBE_MODEL)
    with pytest.raises(StageError, match=f"charswap resume {job.id} --setting"):
        prepare(job, services, **QUIET)
    paid = [run["key"] for run in services.replicate.runs]
    job.update(setting="A dim room.")  # what `charswap resume <id> --setting` saves
    prepare(job, services, **QUIET)
    assert [run["key"] for run in services.replicate.runs][: len(paid)] == paid
    assert DESCRIBE_MODEL not in [run["key"] for run in services.replicate.runs[len(paid) :]]
    assert job.state["stages"]["describe"]["source"] == "you" and job.stage_done("embed")


def test_a_scene_whose_sound_no_longer_fits_is_refused_before_paying(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    job.update(video={"seconds": 4.0})
    write_shots(job.path("full-scene.mp4"), [(BLUE, 143)], audio_seconds=3.0)
    with pytest.raises(SceneError, match=r"sound lasts 3\.0 s but its picture 6\.0 s"):
        pipeline._check_soundtrack_fits(job)


def test_the_segment_is_saved_as_json(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    saved = json.loads(job.path("state.json").read_text())["segment"]
    assert saved["fps"] == "24000/1001" and saved["crop"] == {"x": 0, "y": 0, "w": 64, "h": 48}


# ---- generate: the lay-back ----------------------------------------------------------
YELLOW, MAGENTA = (220, 220, 40), (220, 40, 220)


class FakeSeedance:
    """Stands in for the WaveSpeed generator: one paid create per record, a canned video."""

    name, provider, model = "seedance", "wavespeed", "fake/seedance"
    finals, promotes = ("720p", "1080p"), False

    def __init__(self, video):
        self.video, self.creates = video, 0

    def estimate(self, request, stage):
        return Estimate(lines=(Line(f"Fake {stage}", 0.88),))

    def generate(self, request, stage, *, record, dest, on_created=None):
        if not record.exists():
            self.creates += 1
            record.write_text(json.dumps({"id": f"p-{stage}", "status": "succeeded"}))
            if on_created is not None:
                on_created({"id": f"p-{stage}", "status": "starting"})
        dest.write_bytes(self.video.read_bytes())
        return dest


def ai_video(tmp_path, frames=FRAMES):
    """The AI's draft at twice the job's size: a yellow room, the new person where A is."""
    picture = solid(YELLOW, (128, 96))
    picture[16:80, 32:96] = MAGENTA  # ONE's box (16, 8, 48, 40), doubled
    return write_video(tmp_path / f"ai-{frames}.mp4", [picture] * frames)


def gen_services(
    tmp_path, *, frames=FRAMES, b_boxes=((32, 16, 96, 80),), b_count=FRAMES, **replicate
):
    fake = FakeReplicate(
        outputs={SAM_VERSION: "u/b"},
        files={"u/b": masks_zip(list(b_boxes), count=b_count, size=(128, 96))},
        **replicate,
    )
    generator = FakeSeedance(ai_video(tmp_path, frames))
    return Services(
        replicate=fake,
        face_detector=lambda: None,
        generators={"seedance:wavespeed": generator},
    )


def approved_scene_job(runs_dir, tmp_path):
    job = make_scene_job(runs_dir, tmp_path)
    prepare(job, prep_services(tmp_path), **QUIET)
    decide(job, approved=True, note="watched all", fingerprint=fingerprint(job))
    return job


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def _near(frame, x, y, color):
    patch = frame[y - 2 : y + 2, x - 2 : x + 2].astype(int)
    return abs(patch - color).mean() < 16


def test_a_scene_draft_is_the_whole_scene_with_the_new_person(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path)
    result = generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert result == job.path("draft.mp4") and job.stage_done("draft")
    frames = list(iter_frames(result))
    assert len(frames) == count_frames(job.path("full-scene.mp4")) == 143
    assert audio_packets(result) == audio_packets(job.path("full-scene.mp4"))
    assert _near(frames[10], 32, 24, RED) and _near(frames[130], 32, 24, GREEN)  # other shots
    assert _near(frames[70], 32, 24, MAGENTA) and _near(frames[70], 4, 44, BLUE)
    (run,) = services.replicate.runs
    assert run["input"]["prompt"] == "person"
    assert run["input"]["video"] == "https://fake.replicate/draft-raw.mp4"
    assert [(c["step"], c["usd"]) for c in job.state["costs"]] == [
        ("draft", 0.88),
        ("draft-paste", 0.002),
    ]
    assert job.state["costs"][1]["task_id"] == "pred-1"


def test_nobody_on_the_result_uses_the_original_outline(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path, b_count=0)  # a zip with no mask_*.png
    said = []
    generate(job, services, "draft", runs_dir=runs_dir, status=said.append)
    assert paste_stage.NOBODY in said and job.stage_done("draft")
    assert _near(list(iter_frames(job.path("draft.mp4")))[70], 32, 24, MAGENTA)


def test_blank_masks_on_the_result_also_mean_nobody(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    said = []
    services = gen_services(tmp_path, b_boxes=())  # SAM 3 returns only empty masks
    generate(job, services, "draft", runs_dir=runs_dir, status=said.append)
    assert paste_stage.NOBODY in said and job.stage_done("draft")


def test_a_crash_while_laying_back_buys_nothing_again(runs_dir, tmp_path, monkeypatch):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path)
    real, calls = paste_stage.paste_back, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(paste_stage, "paste_back", flaky)
    with pytest.raises(StageError, match="disk full"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert job.path("draft-raw.mp4").exists() and not job.path("draft.mp4").exists()
    assert not job.path("draft.part.mp4").exists()
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    generator = services.generators["seedance:wavespeed"]
    assert generator.creates == 1 and len(services.replicate.runs) == 1  # nothing bought twice
    assert [c["step"] for c in job.state["costs"]] == ["draft", "draft-paste"]
    assert job.stage_done("draft")


def test_result_masks_that_do_not_match_name_the_record(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    with pytest.raises(StageError, match=r"10 masks for the AI clip's 96 frames\. Delete .*"):
        generate(job, gen_services(tmp_path, b_count=10), "draft", runs_dir=runs_dir, **QUIET)
    error = job.state["stages"]["draft"]["error"]
    assert str(job.path("draft-sam") / "sam-prediction.json") in error


@pytest.mark.parametrize("b_count", [FRAMES - 2, FRAMES - 1, FRAMES + 1, FRAMES + 2])
def test_result_masks_off_by_a_frame_or_two_are_still_laid_back(runs_dir, tmp_path, b_count):
    job = approved_scene_job(runs_dir, tmp_path)
    generate(job, gen_services(tmp_path, b_count=b_count), "draft", runs_dir=runs_dir, **QUIET)
    assert job.stage_done("draft")
    assert _near(list(iter_frames(job.path("draft.mp4")))[70], 32, 24, MAGENTA)


@pytest.mark.parametrize("b_count", [FRAMES - 3, FRAMES + 3])
def test_result_masks_off_by_more_than_two_still_stop(runs_dir, tmp_path, b_count):
    job = approved_scene_job(runs_dir, tmp_path)
    with pytest.raises(StageError, match=rf"{b_count} masks for the AI clip's 96 frames"):
        generate(job, gen_services(tmp_path, b_count=b_count), "draft", runs_dir=runs_dir, **QUIET)


@pytest.mark.parametrize(
    ("trouble", "says"),
    [
        ("fail_download", r"expired.*Delete .*draft-sam.*sam-prediction\.json and run the same"),
        ("flaky_download", r"Could not download.*Run the same command again to retry; no new"),
    ],
)
def test_a_lay_back_download_problem_says_run_the_same_command(runs_dir, tmp_path, trouble, says):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path, **{trouble: {"u/b"}})
    with pytest.raises(StageError, match=says) as info:
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert "resume" not in str(info.value).lower()


def test_a_new_sam_run_never_mixes_in_old_masks(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    stale = job.path("draft-sam/raw-masks")
    stale.mkdir(parents=True)
    for index in range(96, 120):  # left over from an earlier result whose record was deleted
        (stale / f"mask_{index:05d}.png").write_bytes(b"old")
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    clean = list((job.path("draft-sam") / "clean-masks").glob("mask_*.png"))
    assert job.stage_done("draft") and len(clean) == FRAMES


def test_an_ai_clip_that_cannot_be_laid_back_buys_no_sam(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    services = gen_services(tmp_path, frames=48)  # 2.0 s for a 3.96 s part
    with pytest.raises(StageError, match="can't be laid back into the 4.0 s shot"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.replicate.runs == [] and job.path("draft-raw.mp4").exists()


def test_the_price_includes_the_lay_back_until_it_is_bought(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    generator = FakeSeedance(ai_video(tmp_path))
    request = build_request(job, stage="draft")
    items = [line.item for line in generation_estimate(job, generator, request, "draft").lines]
    assert items == ["Fake draft", "SAM 3 masks"] and needs_lay_back_charge(job, "draft")
    job.path("draft-sam").mkdir()
    job.path("draft-sam/sam-prediction.json").write_text('{"id": "s", "status": "succeeded"}')
    estimate = generation_estimate(job, generator, request, "draft")
    assert [line.item for line in estimate.lines] == ["Fake draft"]
    assert not needs_lay_back_charge(job, "draft") and needs_lay_back_charge(job, "final")
    remake = generation_estimate(job, generator, request, "draft", replacing_stale=True)
    assert remake.total == pytest.approx(0.882)  # the old masks are archived first


def test_a_new_draft_never_reuses_the_old_results_masks(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    moved = archive_stale_draft(job)
    assert not job.path("draft-sam").exists()
    (old,) = [path for path in moved if path.name.endswith("-sam")]
    assert old.name.startswith("draft-old-") and (old / "sam-prediction.json").exists()


def test_a_single_shot_job_never_lays_back_or_reads_a_scene(runs_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "lay_back", lambda *a, **k: pytest.fail("laid back"))
    monkeypatch.setattr(pipeline, "check_sound", lambda *a: pytest.fail("read a scene"))
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 96, audio_seconds=4)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (64, 64), (200, 150, 120)).save(photo, "JPEG")
    job = Job.create(
        runs_dir, clip, generator="seedance", mode="depth", character=photo, provider="wavespeed"
    )
    prepare(job, prep_services(tmp_path), **QUIET)
    decide(job, approved=True, note="ok", fingerprint=fingerprint(job))
    services = gen_services(tmp_path)
    generator = services.generators["seedance:wavespeed"]
    request = build_request(job, stage="draft")
    assert generation_estimate(job, generator, request, "draft") == generator.estimate(
        request, "draft"
    )
    generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert job.stage_done("draft") and services.replicate.runs == []
    assert [c["step"] for c in job.state["costs"]] == ["draft"]


# ---- the swapped area and the edge width ---------------------------------------------------
def test_a_scene_draft_also_writes_the_swapped_area(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    area = job.path("draft-area.mp4")
    assert count_frames(area) == count_frames(job.path("full-scene.mp4")) == 143
    assert not _near(list(iter_frames(area))[70], 32, 24, MAGENTA)  # the original, tinted
    assert sorted(p.name for p in job.root.glob("*.part*")) == []


def test_a_new_draft_also_moves_the_old_swapped_area_aside(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    moved = archive_stale_draft(job)
    assert not job.path("draft-area.mp4").exists()
    assert any(p.name.startswith("draft-old-") and p.name.endswith("-area.mp4") for p in moved)


def _edges_used(monkeypatch):
    real, used = paste_stage.paste_back, []

    def spy(*args, edge, **kwargs):
        used.append(edge)
        return real(*args, edge=edge, **kwargs)

    monkeypatch.setattr(paste_stage, "paste_back", spy)
    return used


def test_the_saved_edge_is_used_when_a_draft_is_laid_back(runs_dir, tmp_path, monkeypatch):
    job = approved_scene_job(runs_dir, tmp_path)
    job.update(edge="wide")
    used = _edges_used(monkeypatch)
    generate(job, gen_services(tmp_path), "draft", runs_dir=runs_dir, **QUIET)
    assert used == [EDGES["wide"]]


def test_without_a_saved_edge_the_lay_back_uses_the_normal_one(runs_dir, tmp_path, monkeypatch):
    job = drafted_scene_job(runs_dir, tmp_path)
    used = _edges_used(monkeypatch)
    pipeline.lay_back_again(job, "draft", "normal", **QUIET)
    assert used == [EDGES["normal"]] and job.state["edge"] == "normal"


def test_an_unknown_saved_edge_stops_before_buying_the_lay_back(runs_dir, tmp_path):
    job = approved_scene_job(runs_dir, tmp_path)
    job.update(edge="huge")
    services = gen_services(tmp_path)
    with pytest.raises(StageError, match=r"unknown edge 'huge'.*charswap edge .* normal"):
        generate(job, services, "draft", runs_dir=runs_dir, **QUIET)
    assert services.replicate.runs == [] and job.path("draft-raw.mp4").exists()


def test_laying_back_again_is_free_and_uses_the_new_edge(runs_dir, tmp_path, monkeypatch):
    job = drafted_scene_job(runs_dir, tmp_path)
    used = _edges_used(monkeypatch)
    said = []
    result = pipeline.lay_back_again(job, "draft", "wide", status=said.append)
    assert result == job.path("draft.mp4") and used == [EDGES["wide"]]
    assert said == [
        "Laying the new person back with a wide edge (free)...",
        f"Draft ready: {result}",
    ]
    assert job.state["edge"] == "wide" and job.state.get("costs", []) == []
    frames = list(iter_frames(result))
    assert len(frames) == count_frames(job.path("full-scene.mp4")) == 143
    assert audio_packets(result) == audio_packets(job.path("full-scene.mp4"))
    assert _near(frames[70], 32, 24, MAGENTA) and _near(frames[10], 32, 24, RED)
    assert count_frames(job.path("draft-area.mp4")) == 143
    assert sorted(p.name for p in job.root.glob("*.part*")) == []


def test_laying_the_final_back_again(runs_dir, tmp_path):
    job = drafted_scene_job(runs_dir, tmp_path, "final")
    said = []
    pipeline.lay_back_again(job, "final", "tight", status=said.append)
    assert said[-1] == f"Final ready: {job.path('final.mp4')}"
    assert job.path("final-area.mp4").exists() and not job.path("draft.mp4").exists()


def test_laying_back_again_when_sam_found_nobody_uses_the_original_outline(runs_dir, tmp_path):
    job = drafted_scene_job(runs_dir, tmp_path, b_box=None)
    said = []
    pipeline.lay_back_again(job, "draft", "wide", status=said.append)
    assert paste_stage.NOBODY in said
    assert _near(list(iter_frames(job.path("draft.mp4")))[70], 32, 24, MAGENTA)


def test_laying_back_again_reuses_the_cleaned_masks(runs_dir, tmp_path):
    job = drafted_scene_job(runs_dir, tmp_path)
    pipeline.lay_back_again(job, "draft", "normal", **QUIET)
    shutil.rmtree(job.path("draft-sam/raw-masks"))  # only the cleaned ones are left
    pipeline.lay_back_again(job, "draft", "wide", **QUIET)
    assert job.state["edge"] == "wide"


@pytest.mark.parametrize("missing", ["draft-raw.mp4", "draft-sam", "stage"])
def test_laying_back_again_needs_the_saved_draft(runs_dir, tmp_path, missing):
    job = drafted_scene_job(runs_dir, tmp_path)
    if missing == "stage":
        job.reset_stages("draft")
    elif missing == "draft-sam":
        shutil.rmtree(job.path(missing))
    else:
        job.path(missing).unlink()
    with pytest.raises(StageError) as caught:
        pipeline.lay_back_again(job, "draft", "wide", **QUIET)
    message = str(caught.value)
    assert f"charswap generate {job.id} --draft" in message
    assert message.endswith("Nothing was charged.")
    assert job.state["edge"] == "wide"  # kept for the draft you make next
    assert not job.path("draft.mp4").exists()


def test_laying_the_final_back_before_it_exists_names_the_final(runs_dir, tmp_path):
    job = drafted_scene_job(runs_dir, tmp_path)
    with pytest.raises(StageError, match=r"charswap generate \w+ --final"):
        pipeline.lay_back_again(job, "final", "wide", **QUIET)


def test_a_failed_re_lay_keeps_the_draft_you_had(runs_dir, tmp_path, monkeypatch):
    job = drafted_scene_job(runs_dir, tmp_path)
    pipeline.lay_back_again(job, "draft", "normal", **QUIET)
    before = job.path("draft.mp4").read_bytes(), job.path("draft-area.mp4").read_bytes()

    def broken(*args, area, **kwargs):
        area.write_bytes(b"half")
        raise RuntimeError("disk full")

    monkeypatch.setattr(paste_stage, "paste_back", broken)
    with pytest.raises(StageError, match="disk full"):
        pipeline.lay_back_again(job, "draft", "wide", **QUIET)
    assert (job.path("draft.mp4").read_bytes(), job.path("draft-area.mp4").read_bytes()) == before
    assert sorted(p.name for p in job.root.glob("*.part*")) == []


def test_a_single_shot_job_has_no_edge_to_change(runs_dir, tmp_path):
    clip = write_video(tmp_path / "c.mp4", [solid(RED)] * 24, audio_seconds=1)
    photo = tmp_path / "me.jpg"
    Image.new("RGB", (64, 64), (200, 150, 120)).save(photo, "JPEG")
    job = Job.create(runs_dir, clip, generator="seedance", mode="depth", character=photo)
    with pytest.raises(JobError, match=r"scene swap.*Nothing was charged\."):
        pipeline.lay_back_again(job, "draft", "wide", **QUIET)
    assert "edge" not in job.state


def test_an_unknown_edge_name_is_refused(runs_dir, tmp_path):
    job = drafted_scene_job(runs_dir, tmp_path)
    with pytest.raises(JobError, match="tight, normal or wide"):
        pipeline.lay_back_again(job, "draft", "huge", **QUIET)
    assert "edge" not in job.state
