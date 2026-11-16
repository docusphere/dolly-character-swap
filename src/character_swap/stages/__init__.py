"""Pipeline steps. Each module does one step and receives its provider client."""

from character_swap.errors import CharSwapError
from character_swap.providers.errors import OutputExpired


class StageError(CharSwapError, RuntimeError):
    """A pipeline step could not complete; the message says what to do."""


class NewJobNeeded(StageError):
    """This job can't go on: the message says how to start a new one (so no resume hint)."""


def download_or_fail(replicate, url, dest, *, record, what: str, cost: str, again: str = ""):
    """Download a result; on failure say exactly which record to delete to regenerate it.

    `again`: what to run instead of `charswap resume`, e.g. "run the same command again"
    for a step inside generate.
    """
    try:
        return replicate.download(url, dest)
    except OutputExpired as exc:
        raise StageError(
            f"The {what} result expired ({exc}). Delete {record} and {again or 'resume'} "
            f"to regenerate it (costs {cost})."
        ) from exc
    except Exception as exc:
        retry = again.capitalize() if again else "Resume the job"
        raise StageError(
            f"Could not download the {what} result ({exc}). {retry} to retry; no new charge."
        ) from exc


def bad_output(record, what: str, output, again: str = "") -> StageError:
    return StageError(
        f"{what} returned an unexpected output: {output!r}. "
        f"Delete {record} and {again or 'resume'} to regenerate it."
    )
