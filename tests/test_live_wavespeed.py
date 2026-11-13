"""Live WaveSpeed draft (about $0.88). Never part of the default run; needs Frank's explicit OK.

Run:  CHARSWAP_LIVE_JOB=<id> uv run pytest tests/test_live_wavespeed.py -m live --no-cov -q
<id> is an approved 4 s WaveSpeed job (prepared with `charswap prepare ... --character ...`)
that has no draft yet. WAVESPEED_API_KEY must be in .env.
"""

import json
import os

import pytest

from character_swap import cli
from character_swap.config import Settings
from character_swap.jobs import Job
from character_swap.media.video import stream_seconds

pytestmark = pytest.mark.live

JOB = os.environ.get("CHARSWAP_LIVE_JOB", "")


@pytest.mark.skipif(not JOB, reason="set CHARSWAP_LIVE_JOB to an approved 4 s WaveSpeed job")
def test_a_4_second_480p_wavespeed_draft_end_to_end():
    settings = Settings.load()
    job = Job.load(settings.runs_dir, JOB)
    assert job.state.get("provider") == "wavespeed" and not job.stage_done("draft")
    assert job.state["video"]["seconds"] == 4.0
    assert cli.main(["generate", JOB, "--draft", "--yes"], settings=settings) == 0
    assert job.stage_done("draft")
    record = json.loads(job.path("draft-prediction.json").read_text())
    assert record["status"] == "succeeded" and record["id"]
    assert abs(stream_seconds(job.path("draft.mp4"), "video") - 4.0) < 0.1
    draft_costs = [c for c in job.state["costs"] if c["step"] == "draft"]
    assert draft_costs[-1]["usd"] == pytest.approx(0.88)
    assert draft_costs[-1]["provider"] == "wavespeed"
