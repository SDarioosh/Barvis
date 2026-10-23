"""Fork: thinking defaults to on in Agent mode and off in Chat mode.

Upstream stores one thinking switch per chat (``sessions.thinking_mode``,
default ``"off"``), so every turn ran without thinking unless the owner
turned it on. The owner wants Agent mode to think by default and Chat mode
to stay fast.

A chat whose Thinking toggle was never used follows the mode the message
was sent in: on for ``mode=agent``, off for ``mode=chat``. The mode is the
one the web UI sends (the Agent/Chat toggle, plus the UI's own switches to
agent for plans, coding requests and an open document); a chat-mode message
the server later moves to agent mode for tools stays off. Once the owner
flips the toggle in a chat (the UI sends ``thinking_explicit: true``), that
choice holds in both modes. Upstream's DB value can't tell "never touched"
from "turned off", so the chats with a choice are kept here, in
``<DATA_DIR>/fork/thinking_choices.json``.

``ODYSSEUS_AGENT_THINKING_DEFAULT=off`` restores upstream behaviour.

Hooks: ``routes/chat_routes.py`` (thinking mode of a streamed turn),
``routes/history/history_routes.py`` (generation settings and the context
the toggle reads), ``static/js/chat.js`` (the toggle).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENV = "ODYSSEUS_AGENT_THINKING_DEFAULT"
_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv(ENV, "on").strip().lower() not in {"off", "0", "false", "no"}


def _store_path() -> Path:
    from src import constants
    return Path(constants.DATA_DIR) / "fork" / "thinking_choices.json"


def _load() -> set[str]:
    try:
        data = json.loads(_store_path().read_text())
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as exc:
        logger.warning("[thinking-defaults] unreadable store: %s", exc)
        return set()
    return {str(s) for s in data.get("sessions", [])} if isinstance(data, dict) else set()


def has_choice(session_id: str) -> bool:
    """True when the owner set Thinking in this chat with the toggle."""
    with _lock:
        return bool(session_id) and str(session_id) in _load()


def record_choice(session_id: str) -> None:
    if not session_id:
        return
    with _lock:
        sessions = _load()
        if str(session_id) in sessions:
            return
        sessions.add(str(session_id))
        path = _store_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sessions": sorted(sessions)}))
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("[thinking-defaults] could not save choice: %s", exc)


def turn_default(session_id: str, chat_mode: str, session_mode: str) -> str | None:
    """Thinking mode for a turn whose request named none, or None to keep upstream's.

    Only a chat still on the stored default ("off" or empty) that was sent in
    Agent mode changes, to "on".
    """
    if not _enabled() or str(chat_mode or "").lower() != "agent":
        return None
    if str(session_mode or "off").lower() not in {"", "off"}:
        return None
    if has_choice(session_id):
        return None
    return "on"


def context_fields(session_id: str) -> dict[str, Any]:
    """Fields the Thinking toggle needs to show the default of the current mode."""
    return {
        "thinking_explicit": has_choice(session_id),
        "thinking_agent_default": _enabled(),
    }
