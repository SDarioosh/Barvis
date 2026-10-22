"""Fork: "Record a call" uploads forwarded to odysseus-sound's ingest API (src/call_recorder.py)."""
import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware import require_admin
from routes.sound_module_routes import setup_sound_module_routes
from src import call_recorder as rec

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
CFG = rec.Config(url="http://module:8000", token="t" * 32, device="computer")


def run(coro):
    return asyncio.run(coro)


class Module:
    """A fake odysseus-sound API that records what it was sent."""

    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {"ok": True, "stop": False, "reason": None}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self):
        return httpx.MockTransport(self)


def chunk(**kw):
    args = {"data": b"\x1aE\xdf\xa3webm", "session_id": "call-20261022T145930-ab12cd",
            "sequence": 0, "timestamp": "2026-10-22T14:59:30.000Z", "codec": "webm"}
    args.update(kw)
    return args


def test_config_needs_url_and_token(monkeypatch):
    for env in (rec.URL_ENV, rec.TOKEN_ENV, rec.DEVICE_ENV):
        monkeypatch.delenv(env, raising=False)
    assert rec.config() is None
    monkeypatch.setenv(rec.URL_ENV, "http://127.0.0.1:8000/")
    assert rec.config() is None
    monkeypatch.setenv(rec.TOKEN_ENV, "secret-token-1234")
    assert rec.config() == rec.Config("http://127.0.0.1:8000", "secret-token-1234", "computer")
    monkeypatch.setenv(rec.DEVICE_ENV, "bad device!")
    assert rec.config().device == "computer"
    monkeypatch.setenv(rec.DEVICE_ENV, "mac")
    assert rec.config().device == "mac"
    monkeypatch.setenv(rec.URL_ENV, "module:8000")  # no scheme
    assert rec.config() is None
    with pytest.raises(rec.RecorderError) as e:
        rec.require_config()
    assert e.value.status == 503


def test_check_chunk_accepts_a_piece_and_normalizes_the_time():
    sid, ts, codec = rec.check_chunk(**chunk(codec="WEBM"), now=NOW)
    assert (sid, ts, codec) == ("call-20261022T145930-ab12cd", "2026-10-22T14:59:30+00:00", "webm")
    _, ts, _ = rec.check_chunk(**chunk(timestamp="2026-10-22T16:59:30+02:00"), now=NOW)
    assert ts == "2026-10-22T14:59:30+00:00"


@pytest.mark.parametrize("bad, status", [
    ({"session_id": "has space"}, 400),
    ({"session_id": "../etc"}, 400),
    ({"session_id": ""}, 400),
    ({"sequence": -1}, 400),
    ({"sequence": rec.MAX_SEQUENCE + 1}, 400),
    ({"codec": "mp3"}, 400),
    ({"data": b""}, 400),
    ({"data": b"x" * (rec.MAX_CHUNK_BYTES + 1)}, 413),
    ({"timestamp": "yesterday"}, 400),
    ({"timestamp": "2026-10-22T14:59:30"}, 400),  # no time zone
    ({"timestamp": "2026-10-22T15:30:00Z"}, 400),  # in the future
    ({"timestamp": "2026-10-01T15:00:00Z"}, 400),  # weeks old
])
def test_check_chunk_rejects(bad, status):
    with pytest.raises(rec.RecorderError) as e:
        rec.check_chunk(**chunk(**bad), now=NOW)
    assert e.value.status == status


def test_forward_chunk_posts_to_the_ingest_api(monkeypatch):
    monkeypatch.setattr(rec, "datetime", _FixedDatetime)
    module = Module(body={"ok": True, "stop": True, "reason": "silence", "session_id": "x"})
    out = run(rec.forward_chunk(CFG, **chunk(sequence=7), transport=module.transport))
    assert out == {"ok": True, "stop": True, "reason": "silence"}
    (req,) = module.requests
    assert str(req.url) == "http://module:8000/v1/ingest/chunk"
    assert req.headers["authorization"] == "Bearer " + "t" * 32
    body = req.read()
    for field, value in [("device", "computer"), ("session_id", "call-20261022T145930-ab12cd"),
                         ("sequence", "7"), ("timestamp", "2026-10-22T14:59:30+00:00"),
                         ("codec", "webm"), ("kind", "conversation")]:
        assert f'name="{field}"\r\n\r\n{value}\r\n'.encode() in body
    assert b'name="audio"; filename="000007.webm"' in body and b"\x1aE\xdf\xa3webm" in body


@pytest.mark.parametrize("status, detail, expect", [
    (401, "invalid or missing bearer token", 502),  # our token is wrong: not the user's fault
    (422, "could not decode audio: x", 422),
    (400, "sequence must be >= 0", 400),
    (500, "", 502),
])
def test_forward_chunk_module_errors(monkeypatch, status, detail, expect):
    monkeypatch.setattr(rec, "datetime", _FixedDatetime)
    module = Module(status=status, body={"detail": detail})
    with pytest.raises(rec.RecorderError) as e:
        run(rec.forward_chunk(CFG, **chunk(), transport=module.transport))
    assert e.value.status == expect
    if expect in (400, 422):
        assert e.value.message == detail
    assert "t" * 32 not in e.value.message  # the token never reaches the page


def test_forward_chunk_unreachable(monkeypatch):
    monkeypatch.setattr(rec, "datetime", _FixedDatetime)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(rec.RecorderError) as e:
        run(rec.forward_chunk(CFG, **chunk(), transport=httpx.MockTransport(down)))
    assert (e.value.status, e.value.message) == (502, "odysseus-sound is not reachable")


def test_bad_piece_is_never_sent():
    module = Module()
    with pytest.raises(rec.RecorderError):
        run(rec.forward_chunk(CFG, **chunk(codec="exe"), transport=module.transport))
    assert module.requests == []


def test_end_session():
    module = Module(body={"ok": True, "stop": True})
    assert run(rec.end_session(CFG, "call-1", transport=module.transport)) == {
        "ok": True, "found": True}
    assert str(module.requests[0].url) == "http://module:8000/v1/sessions/call-1/end"
    missing = Module(status=404, body={"detail": "session not found"})
    assert run(rec.end_session(CFG, "call-2", transport=missing.transport)) == {
        "ok": True, "found": False}
    with pytest.raises(rec.RecorderError):
        run(rec.end_session(CFG, "a/b", transport=module.transport))


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


# ------------------------------------------------------------------ routes
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(rec.URL_ENV, "http://module:8000")
    monkeypatch.setenv(rec.TOKEN_ENV, "t" * 32)
    monkeypatch.delenv(rec.DEVICE_ENV, raising=False)
    monkeypatch.setattr(rec, "datetime", _FixedDatetime)
    module = Module()
    real_client = rec._client
    monkeypatch.setattr(rec, "_client", lambda transport: real_client(module.transport))
    app = FastAPI()
    app.include_router(setup_sound_module_routes())
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as c:
        c.module = module
        yield c


def post_chunk(client, headers=None, **form):
    data = {"session_id": "call-1", "sequence": "0", "timestamp": "2026-10-22T14:59:30Z",
            "codec": "webm", **form}
    return client.post("/api/modules/sound/record/chunk", data=data,
                       files={"audio": ("000000.webm", b"\x1aE\xdf\xa3", "audio/webm")},
                       headers={"X-Odysseus-Recorder": "1"} if headers is None else headers)


def test_routes_forward_and_end(client):
    assert client.get("/api/modules/sound/record/config").json() == {
        "configured": True, "device": "computer", "max_bytes": rec.MAX_CHUNK_BYTES}
    r = post_chunk(client)
    assert r.status_code == 200 and r.json() == {"ok": True, "stop": False, "reason": None}
    assert str(client.module.requests[-1].url) == "http://module:8000/v1/ingest/chunk"
    r = client.post("/api/modules/sound/record/call-1/end", headers={"X-Odysseus-Recorder": "1"})
    assert r.json() == {"ok": True, "found": True}


def test_routes_need_the_recorder_header(client):
    assert post_chunk(client, headers={}).status_code == 400
    assert client.post("/api/modules/sound/record/call-1/end").status_code == 400
    assert client.module.requests == []


def test_routes_pass_errors_through(client):
    r = post_chunk(client, codec="mp3")
    assert r.status_code == 400 and "codec" in r.json()["detail"]
    assert client.module.requests == []


def test_routes_not_configured(client, monkeypatch):
    monkeypatch.delenv(rec.TOKEN_ENV)
    assert client.get("/api/modules/sound/record/config").json()["configured"] is False
    r = post_chunk(client)
    assert r.status_code == 503 and rec.TOKEN_ENV in r.json()["detail"]


def test_record_page_is_served(client):
    r = client.get("/modules/sound/record")
    assert r.status_code == 200 and "Record a call" in r.text and "callRecorder.js" in r.text


def test_routes_are_admin_only(monkeypatch):
    app = FastAPI()
    app.include_router(setup_sound_module_routes())
    monkeypatch.setattr("core.middleware.auth_disabled", lambda: False)
    with TestClient(app) as c:
        assert c.get("/api/modules/sound/record/config").status_code == 403
        assert post_chunk(c).status_code == 403
