import json

import httpx
import pytest
import respx

from character_swap.providers.replicate import API, ReplicateClient, ReplicateError


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
    with pytest.raises(ReplicateError, match="expired"):
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
