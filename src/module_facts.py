"""Fork: odysseus-sound's confirmed facts as Odysseus memories.

A built-in scheduled task ("Module Facts", hourly) reads the facts odysseus-sound has
confirmed (``list_facts {"confirmed": true, "format": "json"}``) through the module's
MCP server and keeps one Odysseus memory per fact, so chat context and the Memory page
know them without a module call. odysseus-sound stays the source of truth: each run is a
full reconcile of this owner's copies against the module's list.

* A fact id seen for the first time is added (``MemoryManager.add_entry``).
* A copy whose text or metadata changed is updated in place: the memory keeps its id
  and ``uses``.
* A copy whose id the module no longer returns (retracted, replaced, merged) is
  deleted.
* Running twice changes nothing.

A copy is an ordinary memory entry with ``source: "odysseus-sound"``, ``owner``,
``module_fact_id``, ``link`` (the signed transcript link), ``subject``, ``about``,
``times_said``, ``days``, ``first_said``, ``last_said`` and ``synced_at``; its text is
the module's fact text, never rewritten. Category: ``about`` owner -> ``preference``
when the text has a preference word ("likes", "prefers", "hates" ...), else ``fact``;
person -> ``contact``; other -> ``fact``.

Upstream's "Memory Tidy" task (``action_consolidate_memory``) may merge, rewrite or
delete copies like any other memory. That is harmless: the next run restores a
rewritten copy's text and re-adds a deleted one. Entries without
``module_fact_id`` (the owner's own memories) are never touched, and neither are
another owner's copies. The task doesn't fire ``memory_added``, so syncing doesn't
trigger Memory Tidy itself.

Odysseus never opens the module's database. Per-owner state (last run, counts) is in
``<DATA_DIR>/module_facts_<owner>.json``. An older module that answers ``list_facts``
with text does nothing (logged once).

Settings (environment; the former ``BARVIS_*`` names are still read as a fallback):

* ``MODULE_ACTION_ITEMS_SERVER``: module MCP server name or id (default ``convo``),
  shared with the action-item task.
* ``MODULE_FACTS_SYNC``: ``on`` (default) or ``off``. ``off`` stops the sync; copies
  already in memory stay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.module_action_items import (
    CallTool,
    _env,
    mcp_caller,
    module_server_id,
    server_key,
)

logger = logging.getLogger(__name__)

ACTION = "module_sync_facts"
TASK_NAME = "Module Facts"
SOURCE = "odysseus-sound"
HOUSEKEEPING_DEFAULT = {
    ACTION: {"name": TASK_NAME, "schedule": "cron", "scheduled_time": None,
             "cron_expression": "0 * * * *"},
}
INFO = ("Copy odysseus-sound's confirmed facts into Odysseus memory (one memory per fact, "
        "with its transcript link) and keep them in step: new facts added, changed ones "
        "updated, retracted ones removed. Reads them through the module's MCP tools.")

PREFERENCE_WORDS = frozenset({
    "like", "likes", "prefer", "prefers", "love", "loves", "hate", "hates", "enjoy",
    "enjoys", "favorite", "favourite", "want", "wants", "dislike",
})
# Copied from the module row; a difference in any of them is an update.
FIELDS = ("text", "category", "subject", "about", "times_said", "days", "first_said",
          "last_said", "link")
_LOCKS: dict[str, asyncio.Lock] = {}


# ----------------------------------------------------------------------- settings
def enabled() -> bool:
    """``MODULE_FACTS_SYNC``: on (default) or off."""
    return _env("FACTS_SYNC", "on").lower() not in {"off", "no", "false", "0"}


# -------------------------------------------------------------------------- state
def _state_path(owner: str | None) -> Path:
    from src.constants import DATA_DIR

    slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
    return Path(DATA_DIR) / f"module_facts_{slug}.json"


def load_state(owner: str | None) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(owner).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return state if isinstance(state, dict) else {}


def save_state(owner: str | None, state: dict[str, Any]) -> None:
    path = _state_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _lock(owner: str | None) -> asyncio.Lock:
    """One memory reconcile per owner at a time."""
    return _LOCKS.setdefault(owner or "", asyncio.Lock())


# ------------------------------------------------------------------------ parsing
def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_facts_json(result: str) -> list[dict] | None:
    """The module's ``format="json"`` facts, or None when it answered with text.
    Rows without an id or text are skipped; a repeated id keeps its first row."""
    try:
        data = json.loads(result or "")
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
        return None
    out, seen = [], set()
    for row in data["facts"]:
        if not isinstance(row, dict):
            continue
        try:
            fact_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(row.get("fact") or "").strip()
        if not text or fact_id in seen:
            continue
        seen.add(fact_id)
        out.append({**row, "id": fact_id, "fact": text})
    return out


def category(row: dict) -> str:
    """The hub memory category for a module fact (fact|preference|contact)."""
    about = str(row.get("about") or "").strip().lower()
    if about == "owner":
        words = set(re.findall(r"[a-z]+", str(row.get("fact") or "").lower()))
        return "preference" if words & PREFERENCE_WORDS else "fact"
    if about == "person":
        return "contact"
    return "fact"


def copy_fields(row: dict) -> dict[str, Any]:
    """The memory fields a copy of ``row`` carries (besides id, owner, source ...)."""
    return {
        "text": row["fact"],
        "category": category(row),
        "subject": str(row.get("subject") or ""),
        "about": str(row.get("about") or ""),
        "times_said": _int(row.get("times_said")),
        "days": _int(row.get("days")),
        "first_said": str(row.get("first_said") or ""),
        "last_said": str(row.get("last_said") or ""),
        "link": str(row.get("link") or ""),
    }


def _is_copy(entry: dict, owner: str | None) -> bool:
    return (entry.get("source") == SOURCE and "module_fact_id" in entry
            and (entry.get("owner") or "") == (owner or ""))


# -------------------------------------------------------------------------- sync
def reconcile(owner: str | None, rows: list[dict], *, now: datetime | None = None
              ) -> dict[str, int]:
    """Make this owner's copies match ``rows``; returns kept/added/updated/retired.

    Same discipline as upstream's memory writers: a strict read
    (``load_all_for_update``, raises ``MemoryStoreUnreadable`` instead of saving over
    a store it couldn't read) and the save right after, with no await in between.
    """
    from src.constants import DATA_DIR
    from src.memory import MemoryManager

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    manager = MemoryManager(DATA_DIR)
    entries = manager.load_all_for_update()
    by_id = {r["id"]: r for r in rows}
    stats = {"kept": 0, "added": 0, "updated": 0, "retired": 0}
    indexed: list[dict] = []  # copies whose text the vector index must (re)learn
    removed: list[str] = []
    seen: set[int] = set()
    out: list[dict] = []
    for entry in entries:
        if not _is_copy(entry, owner):
            out.append(entry)
            continue
        fact_id = _int(entry.get("module_fact_id"))
        row = by_id.get(fact_id)
        if row is None or fact_id in seen:  # retracted upstream, or a duplicate copy
            stats["retired"] += 1
            removed.append(str(entry.get("id") or ""))
            continue
        seen.add(fact_id)
        fields = copy_fields(row)
        if any(entry.get(k) != v for k, v in fields.items()):
            if entry.get("text") != fields["text"]:
                indexed.append(entry)
            entry.update(fields, synced_at=stamp, timestamp=int(now.timestamp()))
            stats["updated"] += 1
        else:
            stats["kept"] += 1
        out.append(entry)
    for row in rows:
        if row["id"] in seen:
            continue
        fields = copy_fields(row)
        entry = manager.add_entry(fields["text"], source=SOURCE, category=fields["category"],
                                  owner=owner or None)
        entry.update(fields, module_fact_id=row["id"], synced_at=stamp,
                     timestamp=int(now.timestamp()))
        out.append(entry)
        indexed.append(entry)
        stats["added"] += 1
    if stats["added"] or stats["updated"] or stats["retired"]:
        manager.save(out)
        _update_vector_index(indexed, removed)
    return stats


def _update_vector_index(indexed: list[dict], removed: list[str]) -> None:
    """Best effort, like upstream's manage_memory: the index Odysseus loaded, if any."""
    ai = sys.modules.get("src.ai_interaction")
    vector = getattr(ai, "_memory_vector", None) if ai is not None else None
    if vector is None or not getattr(vector, "healthy", False):
        return
    for entry in indexed:
        try:
            vector.add(entry["id"], entry["text"])
        except Exception:
            logger.debug("memory vector add failed", exc_info=True)
    for memory_id in removed:
        try:
            vector.remove(memory_id)
        except Exception:
            logger.debug("memory vector remove failed", exc_info=True)


async def sync(owner: str | None, call: CallTool, *, now: datetime | None = None
               ) -> dict[str, int] | None:
    """One pass. Counts, or None when the module answered with text (older module)."""
    rows = parse_facts_json(await call("list_facts", {"confirmed": True, "format": "json"}))
    async with _lock(owner):
        state = load_state(owner)
        if rows is None:
            if not state.get("text_only"):
                logger.info("odysseus-sound answered list_facts with text; facts not synced "
                            "(update the module)")
                state["text_only"] = True
                save_state(owner, state)
            return None
        stats = reconcile(owner, rows, now=now)
        state.pop("text_only", None)
        state.update(stats, last_run=(now or datetime.now(timezone.utc))
                     .astimezone(timezone.utc).isoformat(timespec="seconds"))
        save_state(owner, state)
        return stats


def summary(stats: dict[str, int]) -> str:
    """Counts only: never fact text."""
    return (f"facts: {stats['kept']} kept, {stats['added']} added, "
            f"{stats['updated']} updated, {stats['retired']} retired")


async def action_sync_facts(owner: str, **kwargs) -> tuple[str, bool]:
    """Built-in task entry point (see ``register_builtin_action``)."""
    from src.builtin_actions import TaskNoop
    from src.tool_utils import get_mcp_manager

    if not enabled():
        raise TaskNoop("MODULE_FACTS_SYNC is off")
    manager = get_mcp_manager()
    server_id = module_server_id(manager) if manager is not None else None
    if server_id is None:
        raise TaskNoop(f"module MCP server {server_key()!r} is not connected")
    try:
        stats = await sync(owner, mcp_caller(manager, server_id))
    except RuntimeError as e:  # a failed tool call, or MemoryStoreUnreadable
        return f"Facts sync failed: {e}", False
    if stats is None:
        raise TaskNoop("odysseus-sound answered list_facts with text; nothing synced")
    if not (stats["added"] or stats["updated"] or stats["retired"]):
        raise TaskNoop(summary(stats))
    return summary(stats), True


def register_builtin_action(actions: dict, info: dict) -> None:
    actions[ACTION] = action_sync_facts
    info[ACTION] = INFO


def register_housekeeping(defaults: dict) -> None:
    defaults.update(HOUSEKEEPING_DEFAULT)
