import pytest

from character_swap.config import MissingSettingError
from character_swap.errors import CharSwapError
from character_swap.jobs import JobError
from character_swap.prompts import PromptError, validate_prompt
from character_swap.providers.errors import OutputExpired, ProviderError
from character_swap.providers.replicate import ReplicateError, ReplicateOutputExpired
from character_swap.review import ReviewError
from character_swap.stages import StageError, download_or_fail


@pytest.mark.parametrize(
    "cls",
    [
        MissingSettingError,
        JobError,
        PromptError,
        ProviderError,
        ReviewError,
        StageError,
        ReplicateError,
    ],
)
def test_user_facing_errors_share_one_base(cls):
    assert issubclass(cls, CharSwapError)


def test_replicate_errors_are_the_neutral_ones():
    assert issubclass(ReplicateError, ProviderError)
    assert issubclass(ReplicateOutputExpired, OutputExpired)
    assert issubclass(ReplicateOutputExpired, ReplicateError)


def test_old_builtin_bases_still_hold():
    assert issubclass(JobError, ValueError) and issubclass(ReviewError, ValueError)
    assert issubclass(StageError, RuntimeError) and issubclass(MissingSettingError, RuntimeError)
    with pytest.raises(ValueError, match="limit is 4000"):
        validate_prompt("x" * 4001)


class SomeProviderExpired(OutputExpired):
    """Any provider's expiry, whatever the class is called."""


class Downloader:
    def __init__(self, error):
        self.error = error

    def download(self, url, dest):
        raise self.error


def test_expiry_is_detected_by_type_not_by_name(tmp_path):
    client = Downloader(SomeProviderExpired("gone"))
    with pytest.raises(StageError, match=r"result expired.*costs ~\$0\.04"):
        download_or_fail(
            client, "u", tmp_path / "f.mp4", record="r.json", what="depth", cost="~$0.04"
        )


def test_other_download_failures_say_no_new_charge(tmp_path):
    client = Downloader(RuntimeError("connection reset"))
    with pytest.raises(StageError, match="no new charge"):
        download_or_fail(client, "u", tmp_path / "f.mp4", record="r.json", what="x", cost="~$1")
