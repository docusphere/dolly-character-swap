"""Pipeline steps. Each module does one step and receives its provider client."""


class StageError(RuntimeError):
    """A pipeline step could not complete; the message says what to do."""


def download_or_fail(replicate, url, dest, *, record, what: str, cost: str):
    """Download a result; on failure say exactly which record to delete to regenerate it."""
    try:
        return replicate.download(url, dest)
    except Exception as exc:
        raise StageError(
            f"Could not download the {what} result ({exc}). Delete {record} and resume "
            f"to regenerate it (costs {cost})."
        ) from exc


def bad_output(record, what: str, output) -> StageError:
    return StageError(
        f"{what} returned an unexpected output: {output!r}. "
        f"Delete {record} and resume to regenerate it."
    )
