"""Pipeline steps. Each module does one step and receives its provider client."""

from character_swap.errors import CharSwapError
from character_swap.providers.errors import OutputExpired


class StageError(CharSwapError, RuntimeError):
    """A pipeline step could not complete; the message says what to do."""


class NewJobNeeded(StageError):
    """This job can't go on: the message says how to start a new one (so no resume hint)."""


def download_or_fail(replicate, url, dest, *, record, what: str, cost: str):
    """Download a result; on failure say exactly which record to delete to regenerate it."""
    try:
        return replicate.download(url, dest)
    except OutputExpired as exc:
        raise StageError(
            f"The {what} result expired ({exc}). Delete {record} and resume "
            f"to regenerate it (costs {cost})."
        ) from exc
    except Exception as exc:
        raise StageError(
            f"Could not download the {what} result ({exc}). Resume the job to retry; no new charge."
        ) from exc


def bad_output(record, what: str, output) -> StageError:
    return StageError(
        f"{what} returned an unexpected output: {output!r}. "
        f"Delete {record} and resume to regenerate it."
    )
