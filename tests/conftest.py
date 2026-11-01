import shutil

import pytest

from fakes import FakeReplicate
from viewer_jobs import Media


@pytest.fixture
def fake_replicate():
    return FakeReplicate()


@pytest.fixture
def runs_dir(tmp_path):
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture
def media(tmp_path):
    """Tiny real clip/wav/images for job-viewer tests (see tests/viewer_jobs.py)."""
    return Media(tmp_path / "media")


def pytest_collection_modifyitems(config, items):
    if shutil.which("rubberband"):
        return
    skip = pytest.mark.skip(reason="rubberband binary not installed")
    for item in items:
        if "rubberband" in item.keywords:
            item.add_marker(skip)
