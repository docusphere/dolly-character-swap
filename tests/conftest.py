import shutil

import pytest

from fakes import FakeReplicate


@pytest.fixture
def fake_replicate():
    return FakeReplicate()


@pytest.fixture
def runs_dir(tmp_path):
    path = tmp_path / "runs"
    path.mkdir()
    return path


def pytest_collection_modifyitems(config, items):
    if shutil.which("rubberband"):
        return
    skip = pytest.mark.skip(reason="rubberband binary not installed")
    for item in items:
        if "rubberband" in item.keywords:
            item.add_marker(skip)
