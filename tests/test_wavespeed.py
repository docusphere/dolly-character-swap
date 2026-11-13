import json

import httpx
import pytest
import respx

from character_swap.providers import common
from character_swap.providers.errors import OutputExpired
from character_swap.providers.wavespeed import (
    API,
    MAX_UPLOAD_BYTES,
    SUBMIT_URL,
    UPLOAD_URL,
    WaveSpeedClient,
    WaveSpeedError,
    WaveSpeedRejected,
    prediction_status,
)

KEY = "ws-secret-key-123"
BODY = {
    "prompt": "Replace the person in @Video 1 with the character in @Image 1.",
    "video": "https://cdn.wavespeed.ai/media/clip.mp4",
    "reference_images": ["https://cdn.wavespeed.ai/media/me.jpg"],
    "resolution": "480p",
    "generate_audio": False,
}
VIDEO = "https://cdn.wavespeed.ai/outputs/out.mp4"
RESULT = f"{API}/predictions/p1/result"
PUT_URL = "https://storage.example/upload/a"


def client(**kw):
    return WaveSpeedClient(KEY, poll_seconds=0, sleep=lambda _s: None, **kw)


def answer(status, pid="p1", **extra):
    return {"code": 200, "message": "success", "data": {"id": pid, "status": status, **extra}}


@pytest.mark.parametrize(
    ("raw", "ours"),
    [
        ("created", "starting"),
        ("queued", "starting"),
        ("pending", "starting"),
        (None, "starting"),
        ("processing", "processing"),
        ("something-new", "processing"),
        ("completed", "succeeded"),
        ("COMPLETED", "succeeded"),
        ("failed", "failed"),
        ("cancelled", "failed"),
        ("timeout", "failed"),
        ("deleted", "failed"),
    ],
)
def test_prediction_status_maps_to_the_pipelines_words(raw, ours):
    assert prediction_status(raw) == ours


# ---- upload (free) -------------------------------------------------------------------
def _ticket(**upload):
    target = {"method": "PUT", "url": PUT_URL, "headers": {"Content-Type": "video/mp4"}}
    target.update(upload)
    return {
        "code": 200,
        "data": {"download_url": "https://cdn.wavespeed.ai/media/a.mp4", "upload": target},
    }


@respx.mock
def test_upload_asks_for_a_ticket_then_puts_the_bytes_without_the_key(tmp_path):
    clip = tmp_path / "prepared.mp4"
    clip.write_bytes(b"video-bytes")
    ticket = respx.post(UPLOAD_URL).respond(200, json=_ticket())
    put = respx.put(PUT_URL).respond(200)
    assert client().upload(clip) == "https://cdn.wavespeed.ai/media/a.mp4"
    asked = ticket.calls[0].request
    assert asked.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(asked.content) == {"filename": "prepared.mp4", "size": 11}
    stored = put.calls[0].request
    assert stored.content == b"video-bytes" and stored.headers["content-type"] == "video/mp4"
    assert "authorization" not in stored.headers


@respx.mock
def test_upload_refuses_a_file_over_200_mb_before_sending(tmp_path):
    big = tmp_path / "big.mp4"
    with big.open("wb") as file:
        file.truncate(MAX_UPLOAD_BYTES + 1)  # sparse: no real 200 MB written
    with pytest.raises(WaveSpeedError, match="under 200 MB. Nothing was charged"):
        client().upload(big)
    assert respx.calls.call_count == 0


@pytest.mark.parametrize(
    "ticket",
    [
        {"code": 200, "data": {}},
        _ticket(url="http://insecure.example/a"),
        _ticket(method="POST"),
        {"code": 200, "data": {"upload": _ticket()["data"]["upload"]}},
    ],
)
@respx.mock
def test_an_upload_answer_without_usable_links_is_an_error(tmp_path, ticket):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(200, json=ticket)
    with pytest.raises(WaveSpeedError, match="no usable link"):
        client().upload(photo)


@respx.mock
def test_a_failing_put_is_retried_then_reported(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(200, json=_ticket())
    put = respx.put(PUT_URL).respond(503)
    with pytest.raises(WaveSpeedError, match="503 while uploading me.jpg.*Nothing was charged"):
        client().upload(photo)
    assert put.call_count == 3


@respx.mock
def test_a_rejected_key_names_the_setting_but_never_the_key(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).respond(401)
    with pytest.raises(WaveSpeedError, match="WAVESPEED_API_KEY") as caught:
        client().upload(photo)
    assert KEY not in str(caught.value)


# ---- paid run ----------------------------------------------------------------------------
@respx.mock
def test_run_creates_once_saves_before_waiting_and_returns_the_video(tmp_path):
    record = tmp_path / "draft-prediction.json"
    create = respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    seen_while_waiting = []

    def poll(request):
        seen_while_waiting.append(json.loads(record.read_text())["id"])
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json=answer("completed", outputs=[VIDEO]))

    respx.get(RESULT).mock(side_effect=poll)
    created = []
    assert client().run(body=BODY, record=record, on_created=created.append) == VIDEO
    assert create.call_count == 1 and json.loads(create.calls[0].request.content) == BODY
    assert create.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    assert seen_while_waiting == ["p1"]  # the id was on disk before the first poll
    assert created == [
        {"id": "p1", "status": "starting", "task": {"id": "p1", "status": "created"}}
    ]
    saved = json.loads(record.read_text())
    assert saved["id"] == "p1" and saved["status"] == "succeeded"
    assert saved["task"]["outputs"] == [VIDEO]


@respx.mock
def test_an_output_object_with_a_url_works_too(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("processing"))
    respx.get(RESULT).respond(200, json=answer("completed", outputs=[{"url": VIDEO}]))
    assert client().run(body=BODY, record=tmp_path / "r.json") == VIDEO


@respx.mock
def test_a_saved_prediction_is_resumed_never_posted_again(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "processing", "task": {}}))
    create = respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(200, json=answer("completed", outputs=[VIDEO]))
    assert client().run(body={}, record=record) == VIDEO
    assert create.call_count == 0


@respx.mock
def test_a_finished_record_returns_without_any_call(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"id": "p1", "status": "succeeded", "task": {"outputs": [VIDEO]}}))
    assert client().run(body={}, record=record) == VIDEO
    assert respx.calls.call_count == 0


@respx.mock
def test_an_unconfirmed_marker_refuses_without_posting(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "creating", "owner": "x"}))
    with pytest.raises(WaveSpeedError, match="may have been charged.*WaveSpeed dashboard"):
        client().run(body=BODY, record=record)
    assert respx.calls.call_count == 0


@respx.mock
def test_a_failed_record_raises_without_posting_or_polling(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(
        json.dumps({"id": "p1", "status": "failed", "task": {"error": "content flagged"}})
    )
    with pytest.raises(WaveSpeedError, match="content flagged.*may be charged.*dashboard"):
        client().run(body=BODY, record=record)
    assert respx.calls.call_count == 0


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
@respx.mock
def test_a_clear_refusal_releases_the_marker_and_says_nothing_was_charged(tmp_path, code):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(code, json={"code": code, "message": "no"})
    with pytest.raises(WaveSpeedRejected, match="(?i)nothing was charged") as caught:
        client().run(body=BODY, record=record)
    assert caught.value.status_code == code and not record.exists()
    assert KEY not in str(caught.value)


@respx.mock
def test_429_is_busy_not_retried_and_releases_the_marker(tmp_path):
    record = tmp_path / "r.json"
    create = respx.post(SUBMIT_URL).respond(429)
    with pytest.raises(WaveSpeedRejected, match="busy.*nothing was charged.*in a minute"):
        client().run(body=BODY, record=record)
    assert create.call_count == 1 and not record.exists()


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(402, json={"code": 402, "message": "insufficient balance"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"code": 200, "data": {"status": "created"}}),
    ],
)
@respx.mock
def test_unclear_answers_keep_the_marker_and_point_at_the_dashboard(tmp_path, reply):
    record = tmp_path / "r.json"
    create = respx.post(SUBMIT_URL).mock(return_value=reply)
    with pytest.raises(
        WaveSpeedError, match="may have been charged.*WaveSpeed dashboard"
    ) as caught:
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "creating" and create.call_count == 1
    assert f"WaveSpeed answered {reply.status_code}: " in str(caught.value)
    assert KEY not in str(caught.value) and "Bearer" not in str(caught.value)


@respx.mock
def test_an_unclear_answer_shows_one_short_line_of_what_wavespeed_said(tmp_path):
    respx.post(SUBMIT_URL).respond(402, text="insufficient\n   balance " + "x" * 500)
    with pytest.raises(WaveSpeedError) as caught:
        client().run(body=BODY, record=tmp_path / "r.json")
    message = str(caught.value)
    said = message.split("WaveSpeed answered 402: ", 1)[1].split(". The request was sent", 1)[0]
    assert said.startswith("insufficient balance x")  # whitespace and line breaks collapsed
    assert "\n" not in message and len(said) <= 200 and said.endswith("…")
    assert KEY not in message


@respx.mock
def test_a_timeout_keeps_the_marker(tmp_path):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(WaveSpeedError, match="may have been charged"):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "creating"


@respx.mock
def test_a_refusal_that_still_names_a_prediction_is_saved_not_released(tmp_path):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(
        400, json={"code": 400, "message": "odd", "data": {"id": "p9", "status": "created"}}
    )
    created = []
    with pytest.raises(WaveSpeedError, match=r"prediction p9.*may be charged") as caught:
        client().run(body=BODY, record=record, on_created=created.append)
    assert not isinstance(caught.value, WaveSpeedRejected) and str(record) in str(caught.value)
    saved = json.loads(record.read_text())
    assert saved["id"] == "p9" and saved["status"] == "starting"
    assert [c["id"] for c in created] == ["p9"]


def test_an_empty_body_is_refused_before_anything_is_sent(tmp_path):
    with pytest.raises(WaveSpeedError, match="no input"):
        client().run(body={}, record=tmp_path / "r.json")
    assert not (tmp_path / "r.json").exists()


@respx.mock
def test_a_failed_prediction_says_it_may_be_charged_and_keeps_its_record(tmp_path):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    poll = respx.get(RESULT).respond(200, json=answer("failed", error="content flagged"))
    with pytest.raises(
        WaveSpeedError, match=r"content flagged.*may be charged.*dashboard.*delete .*r\.json"
    ):
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["status"] == "failed"  # kept: never re-bought silently
    with pytest.raises(WaveSpeedError, match="failed"):
        client().run(body=BODY, record=record)  # no second POST, no polling
    assert poll.call_count == 1


@respx.mock
def test_finished_without_a_video_link_is_an_error(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(200, json=answer("completed", outputs=[]))
    with pytest.raises(WaveSpeedError, match="without a video link"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_a_busy_poll_is_retried_for_free(tmp_path):
    create = respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, json=answer("completed", outputs=[VIDEO])),
        ]
    )
    assert client().run(body=BODY, record=tmp_path / "r.json") == VIDEO
    assert create.call_count == 1


@respx.mock
def test_still_running_after_max_polls_is_resumable(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(200, json=answer("processing"))
    with pytest.raises(WaveSpeedError, match="still running.*no new charge"):
        client(max_polls=2).run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_lost_connection_while_waiting_is_resumable(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WaveSpeedError, match="no new charge"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_a_poll_answer_for_another_prediction_leaves_the_record_alone(tmp_path):
    record = tmp_path / "r.json"
    first = {"id": "p1", "status": "processing", "task": {"id": "p1"}}
    record.write_text(json.dumps(first))
    respx.get(RESULT).respond(200, json=answer("completed", pid="p2", outputs=[VIDEO]))
    with pytest.raises(WaveSpeedError, match="no new charge"):
        client().run(body={}, record=record)
    assert json.loads(record.read_text()) == first


@respx.mock
def test_polling_backs_off_from_5_to_15_seconds(tmp_path):
    waits = []
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    answers = [answer("processing")] * 4 + [answer("completed", outputs=[VIDEO])]
    respx.get(RESULT).mock(side_effect=[httpx.Response(200, json=a) for a in answers])
    WaveSpeedClient(KEY, sleep=waits.append).run(body=BODY, record=tmp_path / "r.json")
    assert waits == [5, 7.5, 11.25, 15, 15]


@respx.mock
def test_record_save_failure_still_reports_the_paid_prediction(tmp_path, monkeypatch):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    monkeypatch.setattr(common, "save", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    created = []
    with pytest.raises(WaveSpeedError, match="p1"):
        client().run(body=BODY, record=tmp_path / "r.json", on_created=created.append)
    assert [c["id"] for c in created] == ["p1"]


@respx.mock
def test_a_failing_logger_warning_points_at_the_caller(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("completed", outputs=[VIDEO]))

    def broken(_prediction):
        raise OSError("disk full")

    with pytest.warns(UserWarning, match="Could not log") as caught:
        assert client().run(body=BODY, record=tmp_path / "r.json", on_created=broken) == VIDEO
    assert caught[0].filename == __file__


@respx.mock
def test_a_damaged_record_is_never_replaced(tmp_path):
    record = tmp_path / "r.json"
    record.write_text("{not json")
    with pytest.raises(WaveSpeedError, match="damaged.*dashboard"):
        client().run(body=BODY, record=record)
    assert record.read_text() == "{not json" and respx.calls.call_count == 0


@respx.mock
def test_a_record_without_a_prediction_id_is_damaged(tmp_path):
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"status": "processing"}))
    with pytest.raises(WaveSpeedError, match="damaged"):
        client().run(body=BODY, record=record)
    assert respx.calls.call_count == 0


@respx.mock
def test_losing_the_claim_race_never_posts(tmp_path, monkeypatch):
    def taken(_record):
        raise FileExistsError("another run claimed it first")

    monkeypatch.setattr(common, "claim", taken)
    with pytest.raises(WaveSpeedError, match="may have been charged.*WaveSpeed dashboard"):
        client().run(body=BODY, record=tmp_path / "r.json")
    assert respx.calls.call_count == 0


@pytest.mark.parametrize(
    ("code", "said"), [(401, "WAVESPEED_API_KEY"), (404, "no longer recognizes prediction p1")]
)
@respx.mock
def test_a_poll_refusal_says_why_and_keeps_the_record(tmp_path, code, said):
    record = tmp_path / "r.json"
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).respond(code)
    with pytest.raises(WaveSpeedError, match=said) as caught:
        client().run(body=BODY, record=record)
    assert json.loads(record.read_text())["id"] == "p1" and KEY not in str(caught.value)


@respx.mock
def test_an_undecodable_poll_answer_is_resumable(tmp_path):
    respx.post(SUBMIT_URL).respond(200, json=answer("created"))
    respx.get(RESULT).mock(side_effect=httpx.DecodingError("bad gzip"))
    with pytest.raises(WaveSpeedError, match="no new charge"):
        client().run(body=BODY, record=tmp_path / "r.json")


@respx.mock
def test_an_undecodable_upload_answer_is_a_wavespeed_error(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    respx.post(UPLOAD_URL).mock(side_effect=httpx.DecodingError("bad gzip"))
    with pytest.raises(WaveSpeedError, match="Could not reach WaveSpeed.*Nothing was charged"):
        client().upload(photo)


# ---- download ----------------------------------------------------------------------------
@respx.mock
def test_download_has_no_key_and_expiry_is_typed(tmp_path):
    route = respx.get(VIDEO).respond(200, content=b"mp4")
    dest = client().download(VIDEO, tmp_path / "raw.mp4")
    assert dest.read_bytes() == b"mp4" and "authorization" not in route.calls[0].request.headers
    respx.get("https://cdn.wavespeed.ai/outputs/old.mp4").respond(404)
    with pytest.raises(OutputExpired, match="WaveSpeed"):
        client().download("https://cdn.wavespeed.ai/outputs/old.mp4", tmp_path / "old.mp4")
