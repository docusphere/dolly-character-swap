import json
import warnings

import httpx
import pytest

from character_swap.providers import common


class Boom(Exception):
    pass


def test_claim_is_exclusive_and_carries_an_owner(tmp_path):
    record = tmp_path / "draft-prediction.json"
    marker = common.claim(record)
    assert marker["status"] == "creating" and len(marker["owner"]) == 32
    assert json.loads(record.read_text()) == marker
    with pytest.raises(FileExistsError):
        common.claim(record)


def test_release_removes_only_our_own_marker(tmp_path):
    record = tmp_path / "r.json"
    marker = common.claim(record)
    common.release(record, marker, nothing_charged="busy", taken_over="other run", error=Boom)
    assert not record.exists()


def test_release_leaves_someone_elses_record_and_stops(tmp_path):
    record = tmp_path / "r.json"
    marker = common.claim(record)
    record.write_text(json.dumps({"id": "theirs", "status": "starting"}))
    with pytest.raises(Boom, match="other run"):
        common.release(record, marker, nothing_charged="busy", taken_over="other run", error=Boom)
    assert json.loads(record.read_text())["id"] == "theirs"


def test_release_of_a_vanished_record_is_quiet(tmp_path):
    common.release(
        tmp_path / "gone.json",
        {"status": "creating"},
        nothing_charged="x",
        taken_over="y",
        error=Boom,
    )


def test_save_replaces_atomically_and_leaves_no_temp_files(tmp_path):
    record = tmp_path / "r.json"
    common.save(record, {"id": "a", "status": "starting"})
    common.save(record, {"id": "a", "status": "succeeded"})
    assert json.loads(record.read_text())["status"] == "succeeded"
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_announce_never_raises():
    def broken(_data):
        raise RuntimeError("disk full")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        common.announce(broken, {"id": "t1"}, what="paid PiAPI task")
    assert "Could not log paid PiAPI task t1" in str(caught[0].message)
    common.announce(None, {"id": "t1"})  # no callback: nothing happens


def _status_error(code):
    request = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError(
        "e", request=request, response=httpx.Response(code, request=request)
    )


def test_retry_free_retries_5xx_then_succeeds():
    answers = [_status_error(503), "ok"]
    waits = []

    def call():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert common.retry_free(call, sleep=waits.append) == "ok" and waits == [1]


def test_retry_free_never_retries_a_4xx():
    calls = []

    def call():
        calls.append(1)
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        common.retry_free(call, sleep=lambda _s: None)
    assert calls == [1]
