"""Checks a live scene-swap draft. Never part of the default run, and never buys anything.

Run:  CHARSWAP_LIVE_SCENE_JOB=<id> uv run pytest tests/test_live_scene.py -m live --no-cov -q -s
<id> is a scene job (charswap prepare ... --from ... --to ...) whose draft was made with
`charswap generate <id> --draft` (about $2.86 + $0.002, with its price question).
"""

import os

import av
import pytest

from character_swap.config import Settings
from character_swap.jobs import Job
from character_swap.media.video import count_frames

pytestmark = pytest.mark.live

JOB = os.environ.get("CHARSWAP_LIVE_SCENE_JOB", "")


def _audio(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


@pytest.mark.skipif(not JOB, reason="set CHARSWAP_LIVE_SCENE_JOB to an approved scene job")
def test_a_scene_draft_is_the_whole_scene_with_its_own_sound():
    settings = Settings.load()
    job = Job.load(settings.runs_dir, JOB)
    assert "segment" in job.state and job.state.get("provider") == "wavespeed"
    assert job.stage_done("draft"), f"make the draft first: charswap generate {JOB} --draft"
    draft, scene = job.path("draft.mp4"), job.path("full-scene.mp4")
    with av.open(str(draft)) as made, av.open(str(scene)) as source:
        ours, theirs = made.streams.video[0], source.streams.video[0]
        assert (ours.width, ours.height) == (theirs.width, theirs.height)
        assert ours.average_rate == theirs.average_rate
    assert count_frames(draft) == count_frames(scene)
    assert _audio(draft) == _audio(scene)  # the soundtrack, byte for byte
    steps = [cost["step"] for cost in job.state["costs"]]
    assert steps.count("draft") == 1 and steps.count("draft-paste") == 1
    with av.open(str(job.path("draft-raw.mp4"))) as raw:
        stream = raw.streams.video[0]
        print(
            f"\nAI clip: {stream.width}x{stream.height}, {stream.average_rate} fps, "
            f"{count_frames(job.path('draft-raw.mp4'))} frames"
        )
