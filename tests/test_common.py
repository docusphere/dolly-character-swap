import json
import warnings

import httpx
import pytest
import respx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired, ProviderError


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


def _download(tmp_path, url, **kw):
    options = {
        "sleep": lambda _s: None,
        "provider": "WaveSpeed",
        "keeps": "does not say how long it keeps results",
    }
    options.update(kw)
    with httpx.Client() as http:
        return common.download_now(url, tmp_path / "raw.mp4", http=http, **options)


@respx.mock
def test_download_now_writes_the_file_without_any_key(tmp_path):
    route = respx.get("https://cdn.example/out.mp4").respond(200, content=b"mp4")
    dest = _download(tmp_path, "https://cdn.example/out.mp4")
    assert dest.read_bytes() == b"mp4"
    sent = route.calls[0].request.headers
    assert "authorization" not in sent and "x-api-key" not in sent
    assert [p.name for p in tmp_path.iterdir()] == ["raw.mp4"]  # no .part left behind


@pytest.mark.parametrize("code", [403, 404, 410])
@respx.mock
def test_download_now_names_the_provider_when_the_link_expired(tmp_path, code):
    respx.get("https://cdn.example/old.mp4").respond(code)
    with pytest.raises(OutputExpired, match=r"expired \(PiAPI keeps results 24 hours\)\."):
        _download(
            tmp_path,
            "https://cdn.example/old.mp4",
            provider="PiAPI",
            keeps="keeps results 24 hours",
        )


@respx.mock
def test_download_now_retries_5xx_then_raises_the_given_error(tmp_path):
    class Mine(ProviderError):
        pass

    route = respx.get("https://cdn.example/out.mp4").respond(503)
    with pytest.raises(Mine, match=r"Download failed \(503\)"):
        _download(tmp_path, "https://cdn.example/out.mp4", error=Mine)
    assert route.call_count == 3 and not (tmp_path / "raw.mp4").exists()


@respx.mock
def test_download_now_reports_a_lost_connection(tmp_path):
    respx.get("https://cdn.example/out.mp4").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ProviderError, match="Download failed"):
        _download(tmp_path, "https://cdn.example/out.mp4")


@respx.mock
def test_download_now_reports_a_redirect_loop_as_the_given_error(tmp_path):
    class Mine(ProviderError):
        pass

    respx.get("https://cdn.example/loop.mp4").respond(
        302, headers={"Location": "https://cdn.example/loop.mp4"}
    )
    with pytest.raises(Mine, match="Download failed"):
        _download(tmp_path, "https://cdn.example/loop.mp4", error=Mine)
    assert not (tmp_path / "raw.mp4").exists()
