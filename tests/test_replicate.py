import json

import httpx
import pytest
import respx

from character_swap.providers.replicate import (
    API,
    ReplicateClient,
    ReplicateError,
    ReplicateOutputExpired,
)


def client(**kw):
    return ReplicateClient("tok", poll_seconds=0, sleep=lambda _s: None, **kw)


@respx.mock
def test_run_creates_once_persists_and_polls(tmp_path):
    record = tmp_path / "pred.json"
    create = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(
            201, json={"id": "p1", "status": "starting", "urls": {"get": f"{API}/predictions/p1"}}
        )
    )
    respx.get(f"{API}/predictions/p1").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"id": "p1", "status": "processing", "urls": {"get": f"{API}/predictions/p1"}},
            ),
            httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": "https://o"}),
        ]
    )
    assert client().run(version="v1", input={"a": 1}, record=record) == "https://o"
    assert create.call_count == 1
    assert json.loads(create.calls[0].request.content) == {"version": "v1", "input": {"a": 1}}
    assert json.loads(record.read_text())["status"] == "succeeded"


@respx.mock
def test_official_model_uses_model_endpoint(tmp_path):
    route = respx.post(f"{API}/models/openai/whisper/predictions").mock(
        return_value=httpx.Response(
            201, json={"id": "p", "status": "succeeded", "output": {"t": 1}}
        )
    )
    assert client().run(model="openai/whisper", input={}, record=tmp_path / "r.json") == {"t": 1}
    assert route.called


@respx.mock
def test_existing_record_resumes_without_paying_again(tmp_path):
    record = tmp_path / "pred.json"
    record.write_text(
        json.dumps({"id": "p1", "status": "processing", "urls": {"get": f"{API}/predictions/p1"}})
    )
    create = respx.post(f"{API}/predictions")
    respx.get(f"{API}/predictions/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 7})
    )
    assert client().run(version="v1", input={}, record=record) == 7
    assert not create.called


def test_existing_succeeded_record_returns_output(tmp_path):
    record = tmp_path / "pred.json"
    record.write_text(json.dumps({"id": "p1", "status": "succeeded", "output": [1]}))
    assert client().run(version="v1", input={}, record=record) == [1]


def test_existing_failed_record_explains_how_to_retry(tmp_path):
    record = tmp_path / "pred.json"
    record.write_text(json.dumps({"id": "p1", "status": "failed", "error": "boom"}))
    with pytest.raises(ReplicateError, match="Delete .*pred.json to try again"):
        client().run(version="v1", input={}, record=record)


@respx.mock
def test_rejected_create_is_not_retried(tmp_path):
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(400, text="nope"))
    with pytest.raises(ReplicateError, match="rejected the request \\(400\\)"):
        client().run(version="v1", input={}, record=tmp_path / "r.json")
    assert route.call_count == 1
    assert not (tmp_path / "r.json").exists()


@respx.mock
def test_failed_prediction_raises_with_error(tmp_path):
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p", "status": "failed", "error": "bad input"})
    )
    with pytest.raises(ReplicateError, match="bad input"):
        client().run(version="v1", input={}, record=tmp_path / "r.json")


@respx.mock
def test_timeout_keeps_record_for_resume(tmp_path):
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(
            201, json={"id": "p", "status": "starting", "urls": {"get": f"{API}/predictions/p"}}
        )
    )
    respx.get(f"{API}/predictions/p").mock(
        return_value=httpx.Response(
            200, json={"id": "p", "status": "processing", "urls": {"get": f"{API}/predictions/p"}}
        )
    )
    with pytest.raises(ReplicateError, match="Resume the job"):
        client(max_polls=2).run(version="v1", input={}, record=tmp_path / "r.json")
    assert (tmp_path / "r.json").exists()


@respx.mock
def test_upload_retries_transient_errors(tmp_path):
    file = tmp_path / "a.wav"
    file.write_bytes(b"x")
    route = respx.post(f"{API}/files").mock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(201, json={"urls": {"get": "https://f"}}),
        ]
    )
    assert client().upload(file) == "https://f"
    assert route.call_count == 2


@respx.mock
def test_client_errors_are_not_retried(tmp_path):
    file = tmp_path / "a.wav"
    file.write_bytes(b"x")
    route = respx.post(f"{API}/files").mock(return_value=httpx.Response(401))
    with pytest.raises(ReplicateError, match="REPLICATE_API_TOKEN was rejected"):
        client().upload(file)
    assert route.call_count == 1


@respx.mock
def test_latest_version_and_download(tmp_path):
    respx.get(f"{API}/models/a/b").mock(
        return_value=httpx.Response(
            200,
            json={
                "latest_version": {
                    "id": "v9",
                    "openapi_schema": {
                        "components": {
                            "schemas": {"Input": {"properties": {"video": {"format": "uri"}}}}
                        }
                    },
                }
            },
        )
    )
    respx.get("https://out/file.mp4").mock(return_value=httpx.Response(200, content=b"data"))
    version, props = client().latest_version("a/b")
    assert version == "v9" and "video" in props
    assert client().download("https://out/file.mp4", tmp_path / "f.mp4").read_bytes() == b"data"


# ---- double-charge safety ----------------------------------------------------
UNCONFIRMED = "may have been charged"


@respx.mock
def test_post_timeout_keeps_marker_and_never_reposts(tmp_path):
    record = tmp_path / "sub" / "r.json"
    route = respx.post(f"{API}/predictions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert record.exists() and route.call_count == 1
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 1


@respx.mock
def test_post_5xx_keeps_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(503, text="x"))
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert record.exists()


@respx.mock
def test_post_non_json_success_keeps_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert record.exists()


@respx.mock
def test_post_422_deletes_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(422, text="bad field"))
    with pytest.raises(ReplicateError, match="nothing was charged"):
        client().run(version="v1", input={}, record=record)
    assert not record.exists()


@respx.mock
def test_post_401_and_402_messages(tmp_path):
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(401))
    with pytest.raises(ReplicateError, match="REPLICATE_API_TOKEN was rejected"):
        client().run(version="v1", input={}, record=tmp_path / "a.json")
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(402))
    with pytest.raises(ReplicateError, match="billing"):
        client().run(version="v1", input={}, record=tmp_path / "b.json")
    assert not (tmp_path / "a.json").exists()


@respx.mock
def test_creating_marker_blocks_post(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "created_at": "t"}))
    route = respx.post(f"{API}/predictions")
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert not route.called


@pytest.mark.parametrize("content", ["", "{", "{}"])
def test_corrupt_record_is_reported(tmp_path, content):
    record = tmp_path / "r.json"
    record.write_text(content)
    with pytest.raises(ReplicateError, match="is damaged"):
        client().run(version="v1", input={}, record=record)


@respx.mock
def test_poll_url_is_built_from_id_not_response(tmp_path):
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(
            201, json={"id": "p1", "status": "starting", "urls": {"get": "https://evil.example/x"}}
        )
    )
    respx.get(f"{API}/predictions/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 1})
    )
    assert client().run(version="v1", input={}, record=tmp_path / "r.json") == 1


@respx.mock
def test_poll_429_is_retried(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing"}))
    route = respx.get(f"{API}/predictions/p1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 2}),
        ]
    )
    assert client().run(version="v1", input={}, record=record) == 2
    assert route.call_count == 2


@respx.mock
def test_poll_transport_errors_say_resume(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing"}))
    route = respx.get(f"{API}/predictions/p1").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ReplicateError, match="Resume the job . no new charge"):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 3


@respx.mock
def test_failed_without_error_says_no_reason(tmp_path):
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p", "status": "failed", "error": None})
    )
    with pytest.raises(ReplicateError, match="no reason given"):
        client().run(version="v1", input={}, record=tmp_path / "r.json")


@respx.mock
def test_download_expired_and_no_part_file(tmp_path):
    respx.get("https://out/gone").mock(return_value=httpx.Response(404))
    with pytest.raises(ReplicateOutputExpired, match="expired"):
        client().download("https://out/gone", tmp_path / "f.mp4")
    respx.get("https://out/ok").mock(return_value=httpx.Response(200, content=b"d"))
    client().download("https://out/ok", tmp_path / "g.mp4")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["g.mp4"]


@respx.mock
def test_auth_header_sent_to_api_but_not_downloads(tmp_path):
    file = tmp_path / "a.wav"
    file.write_bytes(b"x")
    up = respx.post(f"{API}/files").mock(
        return_value=httpx.Response(201, json={"urls": {"get": "u"}})
    )
    mv = respx.get(f"{API}/models/a/b").mock(
        return_value=httpx.Response(
            200,
            json={
                "latest_version": {
                    "id": "v",
                    "openapi_schema": {"components": {"schemas": {"Input": {"properties": {}}}}},
                }
            },
        )
    )
    dl = respx.get("https://out/f").mock(return_value=httpx.Response(200, content=b"d"))
    client().upload(file)
    client().latest_version("a/b")
    client().download("https://out/f", tmp_path / "f")
    assert up.calls[0].request.headers["authorization"] == "Bearer tok"
    assert mv.calls[0].request.headers["authorization"] == "Bearer tok"
    assert "authorization" not in dl.calls[0].request.headers


# ---- recoverable ids and edge cases -----------------------------------------
LOST = "Resume the job . no new charge"


@respx.mock
def test_record_save_failure_after_post_keeps_id_and_marker(tmp_path, monkeypatch):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "paid42", "status": "starting"})
    )

    def boom(_record, _data):
        raise OSError("disk full")

    monkeypatch.setattr(ReplicateClient, "_save", staticmethod(boom))
    with pytest.raises(ReplicateError, match="paid42.*replicate.com/predictions"):
        client().run(version="v1", input={}, record=record)
    assert record.exists()
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 1


@pytest.mark.parametrize("body", [{"error": "proxy"}, "<html>"])
@respx.mock
def test_bad_poll_payload_keeps_good_record(tmp_path, body):
    record = tmp_path / "r.json"
    good = {"id": "p1", "status": "processing"}
    record.write_text(json.dumps(good))
    resp = (
        httpx.Response(200, json=body) if isinstance(body, dict) else httpx.Response(200, text=body)
    )
    respx.get(f"{API}/predictions/p1").mock(return_value=resp)
    with pytest.raises(ReplicateError, match=LOST):
        client().run(version="v1", input={}, record=record)
    assert json.loads(record.read_text()) == good


@respx.mock
def test_poll_status_errors(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing"}))
    route = respx.get(f"{API}/predictions/p1")
    route.mock(return_value=httpx.Response(404))
    with pytest.raises(ReplicateError, match="no longer recognizes prediction p1 .status 404"):
        client().run(version="v1", input={}, record=record)
    route.mock(return_value=httpx.Response(401))
    with pytest.raises(ReplicateError, match="REPLICATE_API_TOKEN was rejected"):
        client().run(version="v1", input={}, record=record)
    route.mock(return_value=httpx.Response(400))
    with pytest.raises(ReplicateError, match=LOST):
        client().run(version="v1", input={}, record=record)


@respx.mock
def test_marker_write_failure_removes_marker(tmp_path, monkeypatch):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("character_swap.providers.replicate.json.dump", boom)
    with pytest.raises(OSError, match="disk full"):
        client().run(version="v1", input={}, record=record)
    assert not record.exists() and not route.called


@respx.mock
def test_marker_race_blocks_post(tmp_path, monkeypatch):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating"}))
    route = respx.post(f"{API}/predictions")
    monkeypatch.setattr("pathlib.Path.exists", lambda _self: False)
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert not route.called


@respx.mock
def test_408_keeps_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(408))
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert record.exists()


def test_record_with_id_but_no_status_is_damaged(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1"}))
    with pytest.raises(ReplicateError, match="is damaged"):
        client().run(version="v1", input={}, record=record)


@respx.mock
def test_auth_header_on_paid_post_and_poll(tmp_path):
    post = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p1", "status": "starting"})
    )
    poll = respx.get(f"{API}/predictions/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 1})
    )
    client().run(version="v1", input={}, record=tmp_path / "r.json")
    assert post.calls[0].request.headers["authorization"] == "Bearer tok"
    assert poll.calls[0].request.headers["authorization"] == "Bearer tok"


# ---- rate limits (first live run) -----------------------------------------------
@respx.mock
def test_polling_backs_off_to_a_ceiling(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing"}))
    running = [httpx.Response(200, json={"id": "p1", "status": "processing"}) for _ in range(5)]
    done = httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 1})
    respx.get(f"{API}/predictions/p1").mock(side_effect=[*running, done])
    sleeps = []
    slow = ReplicateClient("tok", poll_seconds=5, max_poll_seconds=20, sleep=sleeps.append)
    assert slow.run(version="v1", input={}, record=record) == 1
    assert sleeps == [5, 7.5, 11.25, 16.875, 20, 20]


@respx.mock
def test_create_429_waits_and_retries(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429),
            httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 3}),
        ]
    )
    sleeps = []
    patient = ReplicateClient("tok", poll_seconds=0, sleep=sleeps.append)
    assert patient.run(version="v1", input={}, record=record) == 3
    assert route.call_count == 3 and sleeps == [7.0, 10.0]
    assert json.loads(record.read_text())["id"] == "p"


@respx.mock
def test_every_create_post_happens_while_this_run_holds_the_marker(tmp_path):
    record = tmp_path / "r.json"
    answers = iter(
        [
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 1}),
        ]
    )
    held = []

    def answer(_request):
        held.append(json.loads(record.read_text())["status"])
        return next(answers)

    respx.post(f"{API}/predictions").mock(side_effect=answer)
    client().run(version="v1", input={}, record=record)
    assert held == ["creating", "creating", "creating"]


@respx.mock
def test_marker_is_released_while_waiting_on_429(tmp_path):
    record = tmp_path / "r.json"
    seen = []
    respx.post(f"{API}/predictions").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 1}),
        ]
    )

    def sleep(_seconds):
        seen.append(record.exists())

    ReplicateClient("tok", poll_seconds=0, sleep=sleep).run(version="v1", input={}, record=record)
    assert seen == [False]


@respx.mock
@pytest.mark.parametrize(
    "other", [{"status": "creating", "created_at": "x"}, {"id": "p2", "status": "processing"}]
)
def test_run_started_elsewhere_during_the_wait_is_never_posted_twice(tmp_path, other):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(429))

    def another_run_claims_it(_seconds):
        record.write_text(json.dumps(other))

    with pytest.raises(ReplicateError, match="Another charswap run"):
        ReplicateClient("tok", poll_seconds=0, sleep=another_run_claims_it).run(
            version="v1", input={}, record=record
        )
    assert route.call_count == 1
    assert json.loads(record.read_text()) == other  # the other run's marker/record is untouched


@respx.mock
def test_create_429_gives_up_after_three_retries(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "999"})
    )
    sleeps = []
    with pytest.raises(ReplicateError, match="Rate limited, nothing was charged"):
        ReplicateClient("tok", poll_seconds=0, sleep=sleeps.append).run(
            version="v1", input={}, record=record
        )
    assert route.call_count == 4 and sleeps == [60.0, 60.0, 60.0]
    assert not record.exists()


@respx.mock
def test_ctrl_c_while_waiting_on_429_leaves_no_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=httpx.Response(429))

    def stop(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ReplicateClient("tok", sleep=stop).run(version="v1", input={}, record=record)
    assert not record.exists()


@respx.mock
def test_5xx_after_a_429_still_keeps_the_marker(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        side_effect=[httpx.Response(429), httpx.Response(502)]
    )
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 2
    assert json.loads(record.read_text())["status"] == "creating"


@respx.mock
def test_timeout_after_a_429_keeps_the_marker_and_never_reposts(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        side_effect=[httpx.Response(429), httpx.ReadTimeout("slow")]
    )
    with pytest.raises(ReplicateError, match=UNCONFIRMED):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 2
    assert json.loads(record.read_text())["status"] == "creating"


def test_unconfirmed_message_warns_about_other_runs(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "created_at": "x"}))
    with pytest.raises(ReplicateError, match="no other charswap command is running"):
        client().run(version="v1", input={}, record=record)


@pytest.mark.parametrize(("header", "wait"), [("", 10.0), ("abc", 10.0), ("0", 1.0), ("nan", 10.0)])
def test_retry_after_parsing(header, wait):
    from character_swap.providers.replicate import _retry_after

    assert _retry_after(httpx.Response(429, headers={"Retry-After": header})) == wait


# ---- 429 hardening: only our own marker is ever removed ------------------------
OTHER = {"status": "creating", "created_at": "x", "owner": "someone-else"}


@respx.mock
@pytest.mark.parametrize("code", [429, 422])
def test_record_replaced_during_the_post_is_never_deleted(tmp_path, code):
    record = tmp_path / "r.json"

    def other_run_takes_over(_request):
        record.write_text(json.dumps(OTHER))
        return httpx.Response(code)

    route = respx.post(f"{API}/predictions").mock(side_effect=other_run_takes_over)
    with pytest.raises(ReplicateError, match="Another charswap run"):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 1
    assert json.loads(record.read_text()) == OTHER


@respx.mock
def test_final_429_with_record_replaced_is_never_deleted(tmp_path):
    record = tmp_path / "r.json"
    answers = iter([httpx.Response(429)] * 3)

    def answer(_request):
        try:
            return next(answers)
        except StopIteration:
            record.write_text(json.dumps(OTHER))
            return httpx.Response(429)

    route = respx.post(f"{API}/predictions").mock(side_effect=answer)
    with pytest.raises(ReplicateError, match="Another charswap run"):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 4
    assert json.loads(record.read_text()) == OTHER


@respx.mock
def test_marker_records_an_owner(tmp_path):
    record = tmp_path / "r.json"
    seen = []

    def answer(_request):
        seen.append(json.loads(record.read_text()))
        return httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 1})

    respx.post(f"{API}/predictions").mock(side_effect=answer)
    client().run(version="v1", input={}, record=record)
    assert seen[0]["status"] == "creating" and seen[0]["owner"] and seen[0]["created_at"]


@respx.mock
def test_on_wait_is_told_about_each_429_wait(tmp_path):
    respx.post(f"{API}/predictions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429),
            httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 1}),
        ]
    )
    waits = []
    ReplicateClient(
        "tok", poll_seconds=0, sleep=lambda _s: None, on_wait=lambda *a: waits.append(a)
    ).run(version="v1", input={}, record=tmp_path / "r.json")
    assert waits == [(7.0, 1, 3), (10.0, 2, 3)]


@respx.mock
def test_marker_that_cannot_be_released_on_429_is_reported(tmp_path, monkeypatch):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(429))

    def stuck(_self, missing_ok=False):
        raise PermissionError("read-only")

    monkeypatch.setattr("pathlib.Path.unlink", stuck)
    with pytest.raises(ReplicateError, match="nothing was charged.*could not be removed"):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 1 and record.exists()


@respx.mock
def test_marker_that_cannot_be_reclaimed_after_429_is_reported(tmp_path):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(429))

    def lock_the_folder(_seconds):
        tmp_path.chmod(0o500)

    try:
        with pytest.raises(ReplicateError, match="nothing was charged"):
            ReplicateClient("tok", sleep=lock_the_folder).run(version="v1", input={}, record=record)
    finally:
        tmp_path.chmod(0o700)
    assert route.call_count == 1 and not record.exists()


@respx.mock
def test_record_vanishing_between_exists_and_read_falls_back_to_a_claimed_create(
    tmp_path, monkeypatch
):
    record = tmp_path / "r.json"
    route = respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p", "status": "succeeded", "output": 5})
    )
    real_exists = type(record).exists
    monkeypatch.setattr(
        "pathlib.Path.exists", lambda self: True if self == record else real_exists(self)
    )
    assert client().run(version="v1", input={}, record=record) == 5
    assert route.call_count == 1


@respx.mock
def test_marker_already_gone_on_rejection_still_reports_the_rejection(tmp_path):
    record = tmp_path / "r.json"

    def deleted_meanwhile(_request):
        record.unlink()
        return httpx.Response(422, text="bad field")

    respx.post(f"{API}/predictions").mock(side_effect=deleted_meanwhile)
    with pytest.raises(ReplicateError, match="rejected the input"):
        client().run(version="v1", input={}, record=record)
    assert not record.exists()


@respx.mock
def test_unreadable_record_after_429_is_left_alone(tmp_path):
    record = tmp_path / "r.json"

    def half_written(_request):
        record.write_text("{")
        return httpx.Response(429)

    route = respx.post(f"{API}/predictions").mock(side_effect=half_written)
    with pytest.raises(ReplicateError, match="Another charswap run"):
        client().run(version="v1", input={}, record=record)
    assert route.call_count == 1 and record.read_text() == "{"


# ---- spend hook ------------------------------------------------------------------
@respx.mock
def test_on_created_runs_once_for_a_new_prediction_only(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p9", "status": "succeeded", "output": 1})
    )
    seen = []
    client().run(version="v1", input={}, record=record, on_created=seen.append)
    client().run(version="v1", input={}, record=record, on_created=seen.append)  # resumed
    assert [prediction["id"] for prediction in seen] == ["p9"]


@respx.mock
def test_on_created_is_not_called_when_resuming_a_running_prediction(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing"}))
    create = respx.post(f"{API}/predictions")
    respx.get(f"{API}/predictions/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "status": "succeeded", "output": 2})
    )
    seen = []
    assert client().run(version="v1", input={}, record=record, on_created=seen.append) == 2
    assert seen == [] and create.call_count == 0


@respx.mock
def test_on_created_runs_after_the_record_is_saved(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p9", "status": "succeeded", "output": 1})
    )
    saved = []
    client().run(
        version="v1",
        input={},
        record=record,
        on_created=lambda _p: saved.append(json.loads(record.read_text())["id"]),
    )
    assert saved == ["p9"]


@respx.mock
def test_on_created_failure_never_loses_the_prediction(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "p9", "status": "succeeded", "output": 1})
    )

    def broken(_prediction):
        raise OSError("disk full")

    with pytest.warns(UserWarning, match="p9"):
        assert client().run(version="v1", input={}, record=record, on_created=broken) == 1
    assert json.loads(record.read_text())["id"] == "p9"


@respx.mock
def test_on_created_runs_once_after_a_throttled_create(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(201, json={"id": "p9", "status": "succeeded", "output": 1}),
        ]
    )
    seen = []
    client().run(version="v1", input={}, record=record, on_created=seen.append)
    assert [prediction["id"] for prediction in seen] == ["p9"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(422, json={"detail": "bad"}),
        httpx.Response(502),
        httpx.Response(201, text="<html>"),
        httpx.Response(201, json={"status": "starting"}),
    ],
)
@respx.mock
def test_on_created_never_runs_without_a_confirmed_prediction_id(tmp_path, response):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(return_value=response)
    seen = []
    with pytest.raises(ReplicateError):
        client().run(version="v1", input={}, record=record, on_created=seen.append)
    assert seen == []


@respx.mock
def test_on_created_still_logs_a_paid_prediction_whose_record_could_not_be_saved(
    tmp_path, monkeypatch
):
    record = tmp_path / "r.json"
    respx.post(f"{API}/predictions").mock(
        return_value=httpx.Response(201, json={"id": "paid42", "status": "starting"})
    )

    def boom(_record, _data):
        raise OSError("disk full")

    monkeypatch.setattr(ReplicateClient, "_save", staticmethod(boom))
    seen = []
    with pytest.raises(ReplicateError, match="paid42"):
        client().run(version="v1", input={}, record=record, on_created=seen.append)
    assert [prediction["id"] for prediction in seen] == ["paid42"]
