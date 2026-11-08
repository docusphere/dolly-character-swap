import base64
import json

import pytest
import respx

from character_swap.providers.errors import OutputExpired
from character_swap.providers.piapi import (
    API,
    MAX_UPLOAD_BYTES,
    UPLOAD_URL,
    PiAPIClient,
    PiAPIError,
    task_status,
)


def client(**kw):
    return PiAPIClient("key", poll_seconds=0, sleep=lambda _s: None, **kw)


@pytest.mark.parametrize(
    ("raw", "ours"),
    [
        ("Completed", "succeeded"),
        ("success", "succeeded"),
        ("FAILED", "failed"),
        ("pending", "starting"),
        ("Staged", "starting"),
        (None, "starting"),
        ("processing", "processing"),
        ("retry", "processing"),
        ("something-new", "processing"),
    ],
)
def test_task_status_maps_every_casing(raw, ours):
    assert task_status(raw) == ours


@respx.mock
def test_upload_temp_sends_base64_with_the_key_and_returns_the_link(tmp_path):
    clip = tmp_path / "prepared.mp4"
    clip.write_bytes(b"video-bytes")
    route = respx.post(UPLOAD_URL).respond(
        200, json={"code": 200, "data": {"url": "https://img.theapi.app/temp/a.mp4"}}
    )
    assert client().upload_temp(clip) == "https://img.theapi.app/temp/a.mp4"
    sent = json.loads(route.calls[0].request.content)
    assert route.calls[0].request.headers["x-api-key"] == "key"
    assert sent == {
        "file_name": "prepared.mp4",
        "file_data": base64.b64encode(b"video-bytes").decode(),
    }


def test_upload_temp_refuses_mov_and_big_files_before_sending(tmp_path):
    mov = tmp_path / "clip.mov"
    mov.write_bytes(b"x")
    with pytest.raises(PiAPIError, match=r"\.mov"):
        client().upload_temp(mov)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(PiAPIError, match="under 7 MB"):
        client().upload_temp(big)


@respx.mock
def test_upload_temp_403_explains_the_plan(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(403, json={"code": 403, "message": "plan"})
    with pytest.raises(PiAPIError, match="Creator plan"):
        client().upload_temp(photo)


@respx.mock
def test_upload_temp_envelope_error_is_reported(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(200, json={"code": 400, "message": "bad file"})
    with pytest.raises(PiAPIError, match="bad file"):
        client().upload_temp(photo)


@respx.mock
def test_create_and_get_asset():
    respx.post(f"{API}/asset/upload").respond(
        202, json={"code": 200, "data": {"asset_id": "as_1", "status": "Processing"}}
    )
    respx.get(f"{API}/asset/as_1").respond(
        200, json={"code": 200, "data": {"asset_id": "as_1", "status": "Active"}}
    )
    assert client().create_asset("https://x/me.jpg", "frank")["asset_id"] == "as_1"
    assert client().get_asset("as_1")["status"] == "Active"


@respx.mock
def test_get_asset_returns_none_when_purged():
    respx.get(f"{API}/asset/as_1").respond(404, json={"code": 404, "message": "not found"})
    assert client().get_asset("as_1") is None


def test_get_asset_refuses_odd_ids():
    with pytest.raises(PiAPIError, match="not a PiAPI face id"):
        client().get_asset("../task")


@respx.mock
def test_free_calls_retry_5xx_then_give_a_plain_error(tmp_path):
    route = respx.get(f"{API}/asset/as_1").respond(503)
    with pytest.raises(PiAPIError, match="503"):
        client().get_asset("as_1")
    assert route.call_count == 3


@respx.mock
def test_401_names_the_key():
    respx.get(f"{API}/asset/as_1").respond(401)
    with pytest.raises(PiAPIError, match="PIAPI_API_KEY"):
        client().get_asset("as_1")


@respx.mock
def test_download_has_no_key_and_expiry_is_typed(tmp_path):
    route = respx.get("https://cdn.piapi.ai/out.mp4").respond(200, content=b"mp4")
    dest = client().download("https://cdn.piapi.ai/out.mp4", tmp_path / "raw.mp4")
    assert dest.read_bytes() == b"mp4" and "x-api-key" not in route.calls[0].request.headers
    respx.get("https://cdn.piapi.ai/old.mp4").respond(404)
    with pytest.raises(OutputExpired, match="24 hours"):
        client().download("https://cdn.piapi.ai/old.mp4", tmp_path / "old.mp4")
