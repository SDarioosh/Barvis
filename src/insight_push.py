"""Fork: one phone push a day for the Today insight (Phase 3.3).

After every Today Insight pass (``today_insight._after_run``), :func:`maybe_push` sends
today's insight to the phone once, through the module notify channel
(``module_action_items.push``: Odysseus' reminder dispatcher, ntfy or browser; nothing
else). It pushes when all hold:

* ``TODAY_INSIGHT_PUSH=on`` (default ``off``);
* ``MODULE_NOTIFY_CHANNEL`` isn't ``off``;
* the insight is today's (the owner's local date) and not the fixed "nothing is due" one;
* the local hour is at least ``MODULE_DIGEST_HOUR`` (``off``: never);
* today's outcome file (``src/insight_outcomes.py``) has no ``pushed`` stamp yet, and
  fewer than ``MAX_TRIES`` sends were tried today.

Title ``Today``; body the headline, then ``First: <title>`` of the first focus row after
the owner's own order (``apply_owner_order``); key ``today-insight:<date>``. The day is
marked pushed only when the send succeeded, so a failed send is tried again on the next
pass, at most ``MAX_TRIES`` times a day.

Docs: ``docs/fork/today-insight.md#push``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TITLE = "Today"
MAX_TRIES = 3
MAX_BODY = 600

Push = Callable[[str, str, str, str], Awaitable[bool]]


def enabled() -> bool:
    """``TODAY_INSIGHT_PUSH``: ``on`` or ``off`` (default)."""
    value = (os.environ.get("TODAY_INSIGHT_PUSH") or "off").strip().lower()
    return value in {"on", "yes", "true", "1"}


def is_empty(insight: dict) -> bool:
    """The fixed insight written without the model when nothing is due."""
    from src.today_insight import EMPTY_HEADLINE, SECTIONS

    return (str(insight.get("headline") or "").strip() == EMPTY_HEADLINE
            and not any(insight.get(s) for s in SECTIONS))


def message(insight: dict, outcome: dict | None = None) -> tuple[str, str]:
    """(title, body): the headline, then ``First: <title>`` when focus has a row."""
    ordered = insight
    try:
        from src import insight_outcomes

        ordered = insight_outcomes.apply_owner_order(insight, outcome) or insight
    except Exception as e:  # noqa: BLE001 -- the model's order then
        logger.debug("insight push: owner order unavailable: %s", e)
    body = " ".join(str(ordered.get("headline") or "").split())
    first = next((r for r in ordered.get("focus") or [] if isinstance(r, dict)
                  and str(r.get("title") or "").strip()), None)
    if first is not None:
        line = "First: " + " ".join(str(first["title"]).split())
        body = f"{body}\n{line}" if body else line
    return TITLE, body[:MAX_BODY]


# ------------------------------------------------------------ pushed / tries state
# The outcome log (stream A) owns the ``pushed`` stamp and ``push_tries``. While a
# function is missing there, a small file next to the insight keeps the same state.
def _fallback_path(owner: str | None) -> Path:
    from src.today_insight import insight_path

    return insight_path(owner, "2000-01-01").parent / "push.json"


def _fallback(owner: str | None, day: str) -> dict:
    try:
        data = json.loads(_fallback_path(owner).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("date") != day:
        data = {"date": day, "pushed": None, "push_tries": 0}
    return data


def _save_fallback(owner: str | None, data: dict) -> None:
    path = _fallback_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _outcomes_func(name: str) -> Callable | None:
    try:
        from src import insight_outcomes
    except Exception:  # noqa: BLE001
        return None
    return getattr(insight_outcomes, name, None)


def _pushed(owner: str | None, day: str) -> Any:
    func, mark = _outcomes_func("pushed"), _outcomes_func("mark_pushed")
    if func is not None and mark is not None:
        return func(owner, day)
    return _fallback(owner, day).get("pushed")


def _note_try(owner: str | None, day: str) -> int:
    """Count one try today; returns today's count including this one."""
    func = _outcomes_func("note_push_try")
    if func is not None:
        return int(func(owner, day) or 0)
    data = _fallback(owner, day)
    data["push_tries"] = int(data.get("push_tries") or 0) + 1
    _save_fallback(owner, data)
    return data["push_tries"]


def _mark_pushed(owner: str | None, day: str, now: datetime) -> None:
    func = _outcomes_func("mark_pushed")
    if func is not None and _outcomes_func("pushed") is not None:
        func(owner, day, now=now)
        return
    data = _fallback(owner, day)
    data["pushed"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _save_fallback(owner, data)


def _outcome(owner: str | None, day: str) -> dict | None:
    load = _outcomes_func("load")
    try:
        return load(owner, day) if load is not None else None
    except Exception as e:  # noqa: BLE001
        logger.debug("insight push: outcome log unavailable: %s", e)
        return None


# ----------------------------------------------------------------------------- push
async def maybe_push(owner: str | None, insight: dict, *, now: datetime | None = None,
                     push: Push | None = None) -> bool:
    """Push today's insight once a day when the conditions above hold. True when sent."""
    if not enabled() or not isinstance(insight, dict):
        return False
    from src import module_action_items as mai
    from src import today_insight

    if mai.notify_channel() == "off":
        return False
    hour = mai.digest_hour()
    if hour is None:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(today_insight.owner_timezone(owner))
    day = local.date().isoformat()
    if insight.get("date") != day or is_empty(insight) or local.hour < hour:
        return False
    if _pushed(owner, day):
        return False
    title, body = message(insight, _outcome(owner, day))
    if not body or _note_try(owner, day) > MAX_TRIES:
        return False  # the count goes on past MAX_TRIES; only today's file holds it
    send = push or mai.push
    try:
        ok = bool(await send(owner or "", title, body, f"today-insight:{day}"))
    except Exception as e:  # noqa: BLE001 -- tried again on the next pass
        logger.warning("insight push for %s failed: %s", owner, e)
        ok = False
    if ok:
        _mark_pushed(owner, day, now)
    return ok
