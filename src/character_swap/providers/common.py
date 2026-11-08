"""Shared by every provider client: retried free calls and the money-safe record file.

Every paid request is guarded the same way: an exclusive "creating" marker is written
BEFORE the paid POST, so a crash, a timeout or a second run can never pay twice.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FREE_ATTEMPTS = 3


def retry_free(
    call: Callable[[], Any], *, sleep: Callable[[float], None], attempts: int = FREE_ATTEMPTS
) -> Any:
    """Retry a FREE call on transport errors, 5xx and 429. Raises the last raw httpx error."""
    for attempt in range(attempts):
        delay: float = 2**attempt
        try:
            return call()
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if (code < 500 and code != 429) or attempt == attempts - 1:
                raise
            retry_after = error.response.headers.get("Retry-After", "")
            if code == 429 and retry_after.isdigit():
                delay = min(int(retry_after), 60)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        sleep(delay)
    raise AssertionError("unreachable")


def claim(record: Path) -> dict:
    """Write our "creating" marker exclusively and return it.

    FileExistsError if anyone else has the record. The random owner lets this run
    recognize its own marker later, so it never deletes another run's.
    """
    marker = {
        "status": "creating",
        "created_at": datetime.now(UTC).isoformat(),
        "owner": uuid.uuid4().hex,
    }
    with open(record, "x") as file:  # exclusive: a second runner cannot also pay
        try:
            json.dump(marker, file)
        except BaseException:
            file.close()
            Path(record).unlink(missing_ok=True)  # the POST was never sent
            raise
    return marker


def release(
    record: Path,
    marker: dict,
    *,
    nothing_charged: str,
    taken_over: str,
    error: type[Exception],
) -> None:
    """Delete the record only if it is still exactly our marker.

    Only called when the provider confirmed nothing was created (`nothing_charged` says
    why). If another run replaced the record, it is left alone and `error(taken_over)`
    is raised.
    """
    record = Path(record)
    try:
        current = json.loads(record.read_text())
    except FileNotFoundError:
        return  # already gone; nothing of ours to remove
    except (OSError, ValueError) as exc:
        raise error(taken_over) from exc
    if current != marker:
        raise error(taken_over)
    try:
        record.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise error(
            f"{nothing_charged} But the marker {record} could not be removed ({exc}), "
            "so it is still there. Make sure no other charswap command is running for "
            "this job, then delete it and run again."
        ) from exc


def save(record: Path, data: dict) -> None:
    """Replace `record` atomically (a crash never leaves half a file)."""
    record = Path(record)
    fd, tmp = tempfile.mkstemp(dir=record.parent, prefix=record.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
        os.replace(tmp, record)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def announce(
    callback: Callable[[dict], None] | None,
    data: dict,
    *,
    what: str = "paid prediction",
    stacklevel: int = 3,
) -> None:
    """Report a new paid request; a failing logger must never lose track of it."""
    if callback is None:
        return
    try:
        callback(data)
    except Exception as exc:
        warnings.warn(f"Could not log {what} {data.get('id')}: {exc}", stacklevel=stacklevel)
