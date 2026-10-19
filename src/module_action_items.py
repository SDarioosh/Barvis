"""Fork: phone notifications for odysseus-sound action items and the daily digest.

Action items stay in the module (odysseus-sound is the only source; the owner chose a live
view over copies). A built-in scheduled task ("Module Action Items", every 15 minutes)
reads them through the module's MCP tools and pushes, through Odysseus' reminder
dispatcher on the ntfy channel:

* new open action items (the first run only remembers the backlog, it doesn't push it);
* once a day after ``MODULE_DIGEST_HOUR``: items due today, and yesterday's digest.

Odysseus never opens the module's database and keeps no copy of the items: the only state
is which item ids and days were already pushed (``<DATA_DIR>/barvis_action_items_*.json``;
the file name is kept from the fork's former name so existing state carries over).
The live list (tick done / dismiss) is on the odysseus-sound page, ``/modules/sound``.

Settings (environment; the former ``BARVIS_*`` names are still read as a fallback):

* ``MODULE_ACTION_ITEMS_SERVER``: module MCP server name or id (default ``convo``).
* ``MODULE_NOTIFY_CHANNEL``: ``ntfy`` (default), ``browser`` or ``off``.
* ``MODULE_DIGEST_HOUR``: local hour after which the daily pushes go out (default 8;
  ``off`` = no daily pushes).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# The action id is stored in each user's scheduled task, so it keeps the fork's former name.
ACTION = "barvis_sync_action_items"
LABEL = "odysseus-sound"
TASK_NAME = "Module Action Items"
HOUSEKEEPING_DEFAULT = {
    ACTION: {"name": TASK_NAME, "schedule": "cron", "scheduled_time": None,
             "cron_expression": "*/15 * * * *", "ship_paused": True,
             "legacy_names": ["Barvis Action Items"]},
}
INFO = ("Push new odysseus-sound action items, items due today and the daily digest to "
        "the phone via ntfy. Reads them live through the module's MCP tools; keeps no copy.")
MAX_PUSHED_ITEMS = 5

# One line of odysseus-sound's list_action_items result, e.g.
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
    """``MODULE_<name>``, else the former ``BARVIS_<name>``, else ``default``."""
    return (os.environ.get(f"MODULE_{name}") or os.environ.get(f"BARVIS_{name}")
            or default).strip()


def server_key() -> str:
    return _env("ACTION_ITEMS_SERVER", "convo").casefold()


def notify_channel() -> str:
    value = _env("NOTIFY_CHANNEL", "ntfy").lower()
    return value if value in {"ntfy", "browser", "off"} else "ntfy"


def digest_hour() -> int | None:
    value = _env("DIGEST_HOUR", "8").lower()
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


def _items_message(items: list[ActionItem], what: str = "new action item"
                   ) -> tuple[str, str]:
    title = what[:1].upper() + what[1:] if len(items) == 1 else f"{len(items)} {what}s"
    lines = []
    for it in items[:MAX_PUSHED_ITEMS]:
        due = f" (due {it.due_date or it.due})" if (it.due_date or it.due) else ""
        lines.append(f"- {it.text}{due}")
    if len(items) > MAX_PUSHED_ITEMS:
        lines.append(f"... and {len(items) - MAX_PUSHED_ITEMS} more on the Conversations page")
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
async def sync(owner: str, call: CallTool, *, now: datetime | None = None) -> dict[str, int]:
    """One pass. Returns counts: new (items first seen), pushed (notifications sent)."""
    now = now or datetime.now()
    state = load_state(owner)
    items = parse_action_items(await call("list_action_items", {"status": "open"}))
    stats = {"new": 0, "pushed": 0}
    first_run = "seen" not in state
    seen = set(state.get("seen", []))
    new = [i for i in items if i.id not in seen]
    stats["new"] = len(new)
    # remember open ids only; a closed item never comes back as new
    state["seen"] = sorted(seen | {i.id for i in items})
    save_state(owner, state)
    if new and not first_run:
        title, body = _items_message(new)
        key = "module-items-" + "-".join(str(i.id) for i in new[:20])
        stats["pushed"] += int(await push(owner, title, body, key))

    hour = digest_hour()
    today = now.date().isoformat()
    if hour is None or now.hour < hour:
        return stats
    if state.get("due_pushed") != today:
        due = [i for i in items if i.due_date == today]
        if due:
            title, body = _items_message(due, "action item due today")
            stats["pushed"] += int(await push(owner, title, body, f"module-due-{today}"))
        state["due_pushed"] = today
        save_state(owner, state)
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    if state.get("digest_pushed") != yesterday:
        msg = _digest_message(await call("get_daily_digest", {"date": yesterday}))
        if msg:  # not ready yet: try again next pass
            stats["pushed"] += int(await push(owner, msg[0], msg[1],
                                              f"module-digest-{yesterday}"))
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
    return (f"Action items: {stats['new']} new, {stats['pushed']} notifications sent."), True


def register_builtin_action(actions: dict, info: dict) -> None:
    actions[ACTION] = action_sync_action_items
    info[ACTION] = INFO


def register_housekeeping(defaults: dict) -> None:
    defaults.update(HOUSEKEEPING_DEFAULT)
