import base64
import json

import httpx
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


BODY = {"model": "seedance", "task_type": "seedance-2.5-less-restriction", "input": {"prompt": "p"}}
VIDEO = "https://cdn.piapi.ai/out.mp4"


def task_json(status, task_id="t1", **extra):
    return {"code": 200, "data": {"task_id": task_id, "status": status, **extra}, "message": "ok"}


@respx.mock
def test_run_creates_once_saves_before_waiting_and_returns_the_video(tmp_path):
    record = tmp_path / "draft-prediction.json"
    create = respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    seen_while_waiting = []

    def poll(request):
        seen_while_waiting.append(json.loads(record.read_text())["id"])
        return httpx.Response(200, json=task_json("Completed", output={"video": VIDEO}))

    respx.get(f"{API}/task/t1").mock(side_effect=poll)
    created = []
    assert client().run(body=BODY, record=record, on_created=created.append) == VIDEO
    assert create.call_count == 1 and json.loads(create.calls[0].request.content) == BODY
    assert seen_while_waiting == ["t1"]  # the id was on disk before the first poll
    assert created == [
        {"id": "t1", "status": "starting", "task": {"task_id": "t1", "status": "pending"}}
    ]
    saved = json.loads(record.read_text())
    assert saved["status"] == "succeeded" and saved["task"]["output"]["video"] == VIDEO


@respx.mock
def test_saved_task_is_resumed_never_posted_again(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "t1", "status": "processing", "task": {}}))
    create = respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(200, json=task_json("success", output={"video_url": VIDEO}))
    assert client().run(body={}, record=record) == VIDEO
    assert create.call_count == 0


@respx.mock
def test_finished_record_returns_without_any_call(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(
        json.dumps({"id": "t1", "status": "succeeded", "task": {"output": {"video": VIDEO}}})
    )
    assert client().run(body={}, record=record) == VIDEO
    assert respx.calls.call_count == 0


@respx.mock
def test_unconfirmed_marker_refuses_without_posting(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "owner": "x"}))
    create = respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    with pytest.raises(PiAPIError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert create.call_count == 0


@pytest.mark.parametrize(
    ("code", "words"),
    [(400, "nothing was charged"), (402, "credit"), (422, "nothing was charged"), (429, "busy")],
)
@respx.mock
def test_4xx_releases_the_marker_and_says_nothing_was_charged(tmp_path, code, words):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").respond(code, json={"code": code, "message": "no"})
    with pytest.raises(PiAPIError, match=words):
        client().run(body=BODY, record=record)
    assert not record.exists()


@respx.mock
def test_429_is_not_retried(tmp_path):
    create = respx.post(f"{API}/task").respond(429)
    with pytest.raises(PiAPIError):
        client().run(body=BODY, record=tmp_path / "r.json")
    assert create.call_count == 1


@pytest.mark.parametrize("answer", [httpx.Response(500), httpx.Response(200, text="not json")])
@respx.mock
def test_unclear_answers_keep_the_marker(tmp_path, answer):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").mock(return_value=answer)
    with pytest.raises(PiAPIError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "creating"


@respx.mock
def test_timeout_keeps_the_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PiAPIError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert record.exists()


@respx.mock
def test_envelope_error_without_a_task_releases_the_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").respond(
        200, json={"code": 400, "message": "bad placeholder", "data": {}}
    )
    with pytest.raises(PiAPIError, match="bad placeholder"):
        client().run(body=BODY, record=record)
    assert not record.exists()


def test_empty_body_is_refused_before_anything_is_sent(tmp_path):
    with pytest.raises(PiAPIError, match="no input"):
        client().run(body={}, record=tmp_path / "r.json")
    assert not (tmp_path / "r.json").exists()


@respx.mock
def test_failed_task_says_refunded_and_how_to_retry(tmp_path):
    record = tmp_path / "r.json"
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(
        200, json=task_json("failed", error={"code": 1, "message": "content violation"})
    )
    with pytest.raises(PiAPIError, match=r"content violation.*refunds.*delete .*r\.json"):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "failed"  # kept: never re-bought silently
    with pytest.raises(PiAPIError, match="failed"):
        client().run(body=BODY, record=record)  # a second run does not post again


@respx.mock
def test_finished_without_a_video_link_is_an_error(tmp_path):
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(200, json=task_json("completed", output={}))
    with pytest.raises(PiAPIError, match="without a video link"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_still_running_after_max_polls_is_resumable(tmp_path):
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").respond(200, json=task_json("processing"))
    with pytest.raises(PiAPIError, match="still running.*no new charge"):
        client(max_polls=2).run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_lost_connection_while_waiting_is_resumable(tmp_path):
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    respx.get(f"{API}/task/t1").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(PiAPIError, match="no new charge"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_record_save_failure_still_reports_the_paid_task(tmp_path, monkeypatch):
    from character_swap.providers import common

    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    monkeypatch.setattr(common, "save", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    created = []
    with pytest.raises(PiAPIError, match="t1"):
        client().run(body=BODY, record=tmp_path / "r.json", on_created=created.append)
    assert [c["id"] for c in created] == ["t1"]


@respx.mock
def test_polling_backs_off_to_the_ceiling(tmp_path):
    waits = []
    respx.post(f"{API}/task").respond(200, json=task_json("pending"))
    answers = [task_json("processing")] * 4 + [task_json("completed", output={"video": VIDEO})]
    respx.get(f"{API}/task/t1").mock(side_effect=[httpx.Response(200, json=a) for a in answers])
    PiAPIClient("key", poll_seconds=10, max_poll_seconds=20, sleep=waits.append).run(
        body=BODY, record=tmp_path / "r.json"
    )
    assert waits == [10, 15, 20, 20, 20]
