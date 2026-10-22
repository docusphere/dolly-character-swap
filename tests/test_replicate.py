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
    route = respx.post(f"{API}/predictions").mock(return_value=httpx.Response(500, text="nope"))
    with pytest.raises(ReplicateError, match="rejected the request \\(500\\)"):
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
    with pytest.raises(httpx.HTTPStatusError):
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
