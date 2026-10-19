"""Barvis: module action items -> Odysseus to-dos, calendar and phone notifications.

A built-in scheduled task ("Barvis Action Items", every 15 minutes) syncs the action
items of a module MCP server (barvis-sound, server name ``convo``) into Odysseus:

* every new open item becomes a checklist note (label ``barvis-sound``) whose text
  links back to the transcript, with a due-date reminder when the item has a date;
* an item with a due date also gets an all-day calendar event (no alarm: the note's
  reminder is the one notification);
* ticking the note's item calls the module's ``update_action_item(done)``; an item
  closed in the module is ticked in the note;
* new items and, once a day, yesterday's digest are pushed to the phone through the
  ntfy integration (the existing reminder dispatcher).

Barvis only uses the module's MCP tools (``list_action_items``,
``update_action_item``, ``get_daily_digest``); it never opens the module's database.
The module stays the source of truth: notes are copies with a link back, and the sync
state (item id -> note id / event uid) lives in ``<DATA_DIR>/barvis_action_items_*.json``.
Ticking a to-do is the user's approval for the ``update_action_item`` call.

Settings (environment):

* ``BARVIS_ACTION_ITEMS_SERVER``: module MCP server name or id (default ``convo``).
* ``BARVIS_ACTION_ITEMS_CALENDAR``: ``false`` = no calendar events.
* ``BARVIS_ACTION_ITEMS_REMINDER_TIME``: local time of a dated reminder (``09:00``).
* ``BARVIS_NOTIFY_CHANNEL``: ``ntfy`` (default), ``browser`` or ``off``.
* ``BARVIS_DIGEST_HOUR``: local hour after which yesterday's digest is pushed
  (default 8; ``off`` = never).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ACTION = "barvis_sync_action_items"
LABEL = "barvis-sound"
TASK_NAME = "Barvis Action Items"
HOUSEKEEPING_DEFAULT = {
    ACTION: {"name": TASK_NAME, "schedule": "cron", "scheduled_time": None,
             "cron_expression": "*/15 * * * *", "ship_paused": True, "legacy_names": []},
}
INFO = ("Copy barvis-sound action items into to-dos (and calendar for dated ones), "
        "mark them done in the module when ticked, and push new items and the daily "
        "digest to the phone via ntfy.")
MAX_PUSHED_ITEMS = 5

# One line of barvis-sound's list_action_items result, e.g.
# - #12 [open] Send Alice the spreadsheet (owner Me) due 2026-09-19 (Friday) — from
#   "Q4 budget" 2026-09-15 10:00 http://host:8000/view/abc?sig=...#t=40
_LINE = re.compile(
    r"^- #(?P<id>\d+) \[(?P<status>open|done|dismissed)\] (?P<rest>.*) — from "
    r"\"(?P<title>.*)\" (?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (?P<link>\S+)$")
_DUE_DATE = re.compile(r" due (?P<date>\d{4}-\d{2}-\d{2})(?: \((?P<said>[^)]*)\))?$")
_DUE_TEXT = re.compile(r" due (?P<said>.+)$")
_OWNER = re.compile(r" \(owner (?P<owner>[^)]*)\)$")


@dataclass(frozen=True)
class ActionItem:
    id: int
    status: str
    text: str
    owner: str | None
    due_date: str | None  # YYYY-MM-DD
    due: str | None  # as said, e.g. "Friday"
    conversation: str
    when: str
    link: str


def parse_action_items(result: str) -> list[ActionItem]:
    """Parse the module's list_action_items text (lines that don't match are skipped)."""
    items = []
    for line in (result or "").splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        rest = m["rest"]
        due_date = due = owner = None
        if d := _DUE_DATE.search(rest):
            due_date, due, rest = d["date"], d["said"], rest[:d.start()]
        elif d := _DUE_TEXT.search(rest):
            due, rest = d["said"], rest[:d.start()]
        if o := _OWNER.search(rest):
            owner, rest = o["owner"], rest[:o.start()]
        items.append(ActionItem(int(m["id"]), m["status"], rest.strip(), owner, due_date, due,
                                m["title"], m["when"], m["link"]))
    return items


# ----------------------------------------------------------------------- settings
def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def server_key() -> str:
    return _env("BARVIS_ACTION_ITEMS_SERVER", "convo").casefold()


def calendar_enabled() -> bool:
    return _env("BARVIS_ACTION_ITEMS_CALENDAR", "true").lower() not in {"0", "false", "no", "off"}


def reminder_time() -> str:
    value = _env("BARVIS_ACTION_ITEMS_REMINDER_TIME", "09:00")
    return value if re.fullmatch(r"\d{2}:\d{2}", value) else "09:00"


def notify_channel() -> str:
    value = _env("BARVIS_NOTIFY_CHANNEL", "ntfy").lower()
    return value if value in {"ntfy", "browser", "off"} else "ntfy"


def digest_hour() -> int | None:
    value = _env("BARVIS_DIGEST_HOUR", "8").lower()
    if value in {"off", "none", ""}:
        return None
    try:
        return max(0, min(23, int(value)))
    except ValueError:
        return 8


# -------------------------------------------------------------------------- state
def _state_path(owner: str) -> Path:
    from src.constants import DATA_DIR

    slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
    return Path(DATA_DIR) / f"barvis_action_items_{slug}.json"


def load_state(owner: str) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(owner).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("items", {})
    return state


def save_state(owner: str, state: dict[str, Any]) -> None:
    path = _state_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# -------------------------------------------------------------------------- MCP
CallTool = Callable[[str, dict], Awaitable[str]]


def module_server_id(manager: Any, key: str | None = None) -> str | None:
    """Id of the connected module MCP server named ``key`` (name or id)."""
    key = (key or server_key()).casefold()
    connections = getattr(manager, "_connections", {}) or {}
    for server_id, conn in connections.items():
        if (conn or {}).get("status") != "connected":
            continue
        name = str((conn or {}).get("name") or "").strip().casefold()
        if str(server_id).casefold() == key or name == key:
            return server_id
    return None


def mcp_caller(manager: Any, server_id: str) -> CallTool:
    async def call(tool: str, arguments: dict) -> str:
        res = await manager.call_tool(f"mcp__{server_id}__{tool}", arguments)
        if res.get("exit_code") or res.get("error"):
            raise RuntimeError(f"{tool}: {res.get('error') or res.get('stderr') or 'failed'}")
        return res.get("stdout") or ""
    return call


# ------------------------------------------------------------------- notes, events
def _note_body(item: ActionItem) -> str:
    lines = [f"From \"{item.conversation}\" ({item.when})."]
    if item.owner:
        lines.append(f"Owner: {item.owner}.")
    if item.due or item.due_date:
        lines.append(f"Due: {item.due_date or ''} {f'({item.due})' if item.due else ''}".strip())
    lines.append(f"Transcript: {item.link}")
    lines.append(f"barvis-sound action item #{item.id}")
    return "\n".join(lines)


def create_note(db: Any, owner: str, item: ActionItem) -> str:
    from core.database import Note

    note = Note(
        id=str(uuid.uuid4()), owner=owner, title=item.text[:200], content=_note_body(item),
        items=json.dumps([{"text": item.text, "done": item.status != "open"}]),
        note_type="checklist", label=LABEL, source="agent",
        due_date=f"{item.due_date}T{reminder_time()}" if item.due_date else None,
    )
    db.add(note)
    return note.id


def note_done(db: Any, note_id: str) -> bool | None:
    """True/False for the note's first checklist item; None if the note is gone."""
    from core.database import Note

    note = db.query(Note).filter(Note.id == note_id).first()
    if note is None:
        return None
    try:
        items = json.loads(note.items or "[]")
    except ValueError:
        return False
    return bool(items and items[0].get("done"))


def tick_note(db: Any, note_id: str, done: bool = True) -> None:
    from core.database import Note

    note = db.query(Note).filter(Note.id == note_id).first()
    if note is None:
        return
    try:
        items = json.loads(note.items or "[]")
    except ValueError:
        items = []
    for it in items[:1]:
        it["done"] = done
    note.items = json.dumps(items)


def create_event(db: Any, owner: str, item: ActionItem) -> tuple[str, bool] | None:
    """All-day event on the due date; returns (uid, needs CalDAV push)."""
    if not item.due_date:
        return None
    from core.database import CalendarEvent
    from routes.calendar_routes import _ensure_default_calendar

    day = datetime.strptime(item.due_date, "%Y-%m-%d")
    cal = _ensure_default_calendar(db, owner)
    uid = str(uuid.uuid4())
    db.add(CalendarEvent(
        uid=uid, calendar_id=cal.id, summary=f"Due: {item.text}"[:200],
        description=_note_body(item), dtstart=day, dtend=day + timedelta(days=1),
        all_day=True, event_type="admin",
        caldav_sync_pending="create" if getattr(cal, "source", None) == "caldav" else None))
    return uid, getattr(cal, "source", None) == "caldav"


# --------------------------------------------------------------------------- push
async def push(owner: str, title: str, body: str, key: str) -> bool:
    """Phone notification through Odysseus' reminder dispatcher (ntfy by default)."""
    channel = notify_channel()
    if channel == "off":
        return False
    from routes.note_routes import dispatch_reminder

    # Verbatim text: no LLM rewrite of transcript-derived content.
    result = await dispatch_reminder(
        title, body, key, owner=owner,
        settings_override={"reminder_channel": channel, "reminder_llm_synthesis": False})
    return bool(result.get("ntfy_sent") or result.get("browser_sent"))


def _items_message(items: list[ActionItem]) -> tuple[str, str]:
    title = ("New action item" if len(items) == 1 else f"{len(items)} new action items")
    lines = []
    for it in items[:MAX_PUSHED_ITEMS]:
        due = f" (due {it.due_date or it.due})" if (it.due_date or it.due) else ""
        lines.append(f"- {it.text}{due}")
    if len(items) > MAX_PUSHED_ITEMS:
        lines.append(f"... and {len(items) - MAX_PUSHED_ITEMS} more in Notes")
    return title, "\n".join(lines)


def _digest_message(text: str) -> tuple[str, str] | None:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines or lines[0].startswith("No digest"):
        return None
    # drop the citation rule meant for the model
    lines = [ln for ln in lines if not ln.startswith("When answering")]
    head = lines[0]
    title = head.split(": ", 1)[1] if ": " in head else head
    return f"Yesterday: {title}"[:200], "\n".join(lines[1:])[:1500]


# --------------------------------------------------------------------------- sync
async def sync(owner: str, call: CallTool, *, now: datetime | None = None,
               session_factory: Callable[[], Any] | None = None) -> dict[str, int]:
    """One sync pass. Returns counts: created, events, closed_in_module, ticked (to-dos
    ticked or unticked to follow the module), pushed."""
    from core import database

    now = now or datetime.now()
    factory = session_factory or database.SessionLocal
    state = load_state(owner)
    known: dict[str, dict] = state["items"]
    items = parse_action_items(await call("list_action_items", {"status": "all"}))
    stats = {"created": 0, "events": 0, "closed_in_module": 0, "ticked": 0, "pushed": 0}
    new_open: list[ActionItem] = []
    caldav_push: list[str] = []
    db = factory()
    try:
        for item in items:
            key = str(item.id)
            entry = known.get(key)
            if entry is None:
                if item.status != "open":
                    # closed before Barvis saw it: remember, don't copy
                    known[key] = {"note_id": None, "status": item.status}
                    continue
                entry = {"note_id": create_note(db, owner, item), "status": "open"}
                stats["created"] += 1
                if calendar_enabled():
                    made = create_event(db, owner, item)
                    if made:
                        entry["event_uid"] = made[0]
                        stats["events"] += 1
                        if made[1]:
                            caldav_push.append(made[0])
                known[key] = entry
                new_open.append(item)
                continue
            note_id = entry.get("note_id")
            if item.status != "open":
                if note_id and entry.get("status") == "open":
                    tick_note(db, note_id)
                    stats["ticked"] += 1
                entry["status"] = item.status
                continue
            if entry.get("status") != "open":
                # reopened in the module (the module wins): untick the to-do
                if note_id:
                    tick_note(db, note_id, done=False)
                    stats["ticked"] += 1
                entry["status"] = "open"
            elif note_id and note_done(db, note_id):
                await call("update_action_item", {"item_id": item.id, "status": "done"})
                entry["status"] = "done"
                stats["closed_in_module"] += 1
            elif note_id and note_done(db, note_id) is None:
                entry["note_id"] = None  # the user deleted the to-do: don't recreate it
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    save_state(owner, state)

    for uid in caldav_push:
        try:
            from routes.calendar_routes import _push_caldav_event_after_commit

            await _push_caldav_event_after_commit(owner, uid, "create")
        except Exception:
            logger.warning("Barvis: CalDAV push of %s failed; calendar sync retries it", uid)

    if new_open:
        title, body = _items_message(new_open)
        key = "barvis-items-" + "-".join(str(i.id) for i in new_open[:20])
        stats["pushed"] += int(await push(owner, title, body, key))

    hour = digest_hour()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    if hour is not None and now.hour >= hour and state.get("digest_pushed") != yesterday:
        msg = _digest_message(await call("get_daily_digest", {"date": yesterday}))
        if msg:
            if await push(owner, msg[0], msg[1], f"barvis-digest-{yesterday}"):
                stats["pushed"] += 1
            state["digest_pushed"] = yesterday
            save_state(owner, state)
    return stats


async def action_sync_action_items(owner: str, **kwargs) -> tuple[str, bool]:
    """Built-in task entry point (see ``register_builtin_action``)."""
    from src.builtin_actions import TaskNoop
    from src.tool_utils import get_mcp_manager

    manager = get_mcp_manager()
    server_id = module_server_id(manager) if manager is not None else None
    if server_id is None:
        raise TaskNoop(f"module MCP server {server_key()!r} is not connected")
    try:
        stats = await sync(owner, mcp_caller(manager, server_id))
    except RuntimeError as e:
        return f"Action item sync failed: {e}", False
    if not any(stats.values()):
        raise TaskNoop("no changes")
    return (f"Action items: {stats['created']} new to-dos ({stats['events']} on the "
            f"calendar), {stats['closed_in_module']} marked done in barvis-sound, "
            f"{stats['ticked']} ticked from barvis-sound, {stats['pushed']} "
            "notifications."), True


def register_builtin_action(actions: dict, info: dict) -> None:
    actions[ACTION] = action_sync_action_items
    info[ACTION] = INFO


def register_housekeeping(defaults: dict) -> None:
    defaults.update(HOUSEKEEPING_DEFAULT)
