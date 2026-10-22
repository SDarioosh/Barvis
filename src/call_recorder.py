"""Fork: record calls from the computer into odysseus-sound (the "Record a call" page).

The page (``static/call-recorder.html``) captures a meeting's audio in the browser (a tab,
a window or the whole screen, with "share audio", plus the microphone), mixes it, and
sends a self-contained 30-second WebM/Opus file at a time to Odysseus. This file checks
each piece and forwards it to odysseus-sound's documented ingest API
(``POST /v1/ingest/chunk``, ``POST /v1/sessions/{id}/end``), the same path the watch and
the phone relay use. The module then transcribes, diarizes and summarizes it like any
other conversation.

The module's ingest token stays on the server: the browser only talks to Odysseus.
Odysseus never touches the module's storage (docs/fork/modules.md).

Settings (environment):

* ``SOUND_MODULE_API_URL``: base URL of the module's API, e.g. ``http://127.0.0.1:8000``
  (Odysseus native) or ``http://host.docker.internal:8000`` (Odysseus in Docker; the module
  must listen where the container can reach it).
* ``SOUND_MODULE_INGEST_TOKEN``: the module's ``CONVO_INGEST_TOKEN``.
* ``SOUND_MODULE_CALL_DEVICE``: the device name recordings get (default ``computer``).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

URL_ENV = "SOUND_MODULE_API_URL"
TOKEN_ENV = "SOUND_MODULE_INGEST_TOKEN"
DEVICE_ENV = "SOUND_MODULE_CALL_DEVICE"
DEFAULT_DEVICE = "computer"

# Same rule as odysseus-sound's ids (convo_server/pipeline.py _SAFE).
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CODECS = {"webm", "ogg"}  # what MediaRecorder writes in Chromium (webm) and Firefox (ogg)
MAX_CHUNK_BYTES = 5 * 1024 * 1024  # 30 s of Opus is ~120-250 KB; leaves room for 60 s at 64 kbit/s
MAX_SEQUENCE = 100_000  # 4 h sessions (the module's default cap) are 480 pieces
TIMEOUT = httpx.Timeout(60.0, connect=5.0)  # the module decodes and runs VAD before it answers


class RecorderError(Exception):
    """A request the page can show: ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    device: str


def config() -> Config | None:
    """The module's API address and ingest token, or None when not configured."""
    url = (os.environ.get(URL_ENV) or "").strip().rstrip("/")
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    device = (os.environ.get(DEVICE_ENV) or "").strip() or DEFAULT_DEVICE
    if not url or not token or not re.match(r"^https?://", url):
        return None
    if not SAFE_ID.match(device):
        logger.warning("%s=%r is not a valid device name; using %r", DEVICE_ENV, device,
                       DEFAULT_DEVICE)
        device = DEFAULT_DEVICE
    return Config(url=url, token=token, device=device)


def require_config() -> Config:
    cfg = config()
    if cfg is None:
        raise RecorderError(503, f"Call recording isn't set up: set {URL_ENV} and {TOKEN_ENV} "
                                 "for Odysseus (docs/fork/deploy.md).")
    return cfg


def check_session_id(session_id: str) -> str:
    session_id = (session_id or "").strip()
    if not SAFE_ID.match(session_id):
        raise RecorderError(400, "invalid session id")
    return session_id


def check_chunk(*, data: bytes, session_id: str, sequence: int, timestamp: str, codec: str,
                now: datetime | None = None) -> tuple[str, str, str]:
    """Validate one piece; returns (session_id, timestamp in ISO-8601 UTC, codec)."""
    session_id = check_session_id(session_id)
    if not 0 <= sequence <= MAX_SEQUENCE:
        raise RecorderError(400, "invalid sequence")
    codec = (codec or "").strip().lower()
    if codec not in CODECS:
        raise RecorderError(400, f"codec must be one of {sorted(CODECS)}")
    if not data:
        raise RecorderError(400, "empty audio")
    if len(data) > MAX_CHUNK_BYTES:
        raise RecorderError(413, "audio piece too large")
    try:
        started = datetime.fromisoformat((timestamp or "").strip())
    except ValueError:
        raise RecorderError(400, "invalid timestamp") from None
    if started.tzinfo is None:
        raise RecorderError(400, "timestamp needs a time zone")
    now = now or datetime.now(UTC)
    # A wrong clock on the recording computer would misplace the conversation; a piece
    # can't start in the future, and a queued retry is at most a few hours old.
    if started - now > timedelta(minutes=5) or now - started > timedelta(days=2):
        raise RecorderError(400, "timestamp is too far from the server's clock")
    return session_id, started.astimezone(UTC).isoformat(), codec


def _client(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=transport)


def _module_error(res: httpx.Response) -> RecorderError:
    try:
        detail = str(res.json().get("detail") or "")
    except ValueError:
        detail = ""
    if res.status_code == 401:
        return RecorderError(502, f"odysseus-sound refused the ingest token ({TOKEN_ENV}).")
    if res.status_code in (400, 413, 422):
        return RecorderError(res.status_code, detail or f"odysseus-sound HTTP {res.status_code}")
    return RecorderError(502, f"odysseus-sound HTTP {res.status_code}")


async def forward_chunk(cfg: Config, *, data: bytes, session_id: str, sequence: int,
                        timestamp: str, codec: str,
                        transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Send one checked piece to the module; returns ``{"ok", "stop", "reason"}``."""
    session_id, timestamp, codec = check_chunk(data=data, session_id=session_id,
                                               sequence=sequence, timestamp=timestamp,
                                               codec=codec)
    form = {"device": cfg.device, "session_id": session_id, "sequence": str(sequence),
            "timestamp": timestamp, "codec": codec, "kind": "conversation"}
    files = {"audio": (f"{sequence:06d}.{codec}", data, f"audio/{codec}")}
    try:
        async with _client(transport) as client:
            res = await client.post(f"{cfg.url}/v1/ingest/chunk", data=form, files=files,
                                    headers={"Authorization": f"Bearer {cfg.token}"})
    except httpx.HTTPError as exc:
        logger.warning("odysseus-sound ingest unreachable at %s: %s", cfg.url, exc)
        raise RecorderError(502, "odysseus-sound is not reachable") from None
    if res.status_code != 200:
        raise _module_error(res)
    try:
        body = res.json()
    except ValueError:
        raise RecorderError(502, "odysseus-sound sent an unreadable answer") from None
    return {"ok": bool(body.get("ok", True)), "stop": bool(body.get("stop")),
            "reason": body.get("reason")}


async def end_session(cfg: Config, session_id: str,
                      transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Tell the module the call is over, so it processes it now instead of after the idle
    timeout. A session the module never saw (Stop before the first piece) is fine."""
    session_id = check_session_id(session_id)
    try:
        async with _client(transport) as client:
            res = await client.post(f"{cfg.url}/v1/sessions/{session_id}/end",
                                    headers={"Authorization": f"Bearer {cfg.token}"})
    except httpx.HTTPError as exc:
        logger.warning("odysseus-sound unreachable at %s: %s", cfg.url, exc)
        raise RecorderError(502, "odysseus-sound is not reachable") from None
    if res.status_code == 404:
        return {"ok": True, "found": False}
    if res.status_code != 200:
        raise _module_error(res)
    return {"ok": True, "found": True}
