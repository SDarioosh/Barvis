"""Barvis: barvis-sound action items -> to-dos, calendar, ntfy (src/barvis_action_items.py)."""
import datetime
import json
import re

import pytest

from src import barvis_action_items as bai
from tests.helpers.database import disposable_database

LIST = """3 all action items (times are Europe/Berlin). When answering, put the link next to each fact you use:
- #12 [open] Send Alice the revised spreadsheet (owner Me) due 2026-09-19 (Friday) — from "Q4 budget" 2026-09-15 12:00 http://h:8000/view/a?sig=x#t=40
- #13 [open] Pick up Aunt Rosa from the station due that afternoon — from "Dinner plans" 2026-10-16 22:00 http://h:8000/view/b?sig=y#t=16
- #14 [done] Review Ben's pull request — from "Standup" 2026-10-07 11:30 http://h:8000/view/c?sig=z#t=24"""

DIGEST = """2026-10-18: Dentist and dinner plans
You booked a crown and planned Mom's dinner.
* Crown on October 21
TODO: Pick up Aunt Rosa
Conversation: http://h:8000/view/b?sig=y
When answering, put the link next to each fact you use."""


def test_parse_action_items():
    items = bai.parse_action_items(LIST)
    assert [(i.id, i.status) for i in items] == [(12, "open"), (13, "open"), (14, "done")]
    a, b, c = items
    assert (a.text, a.owner, a.due_date, a.due) == (
        "Send Alice the revised spreadsheet", "Me", "2026-09-19", "Friday")
    assert (b.text, b.owner, b.due_date, b.due) == (
        "Pick up Aunt Rosa from the station", None, None, "that afternoon")
    assert a.link.endswith("#t=40") and a.conversation == "Q4 budget"
    assert (c.text, c.due) == ("Review Ben's pull request", None)
    assert bai.parse_action_items("No open action items found.") == []


class FakeModule:
    def __init__(self, text=LIST, digest=DIGEST):
        self.text, self.digest, self.calls = text, digest, []

    async def __call__(self, tool, args):
        self.calls.append((tool, args))
        if tool == "list_action_items":
            return self.text
        if tool == "get_daily_digest":
            return self.digest
        self.set_status(args["item_id"], args["status"])
        return f"Action item #{args['item_id']} is now {args['status']}."

    def set_status(self, item_id, status):
        self.text = re.sub(rf"#{item_id} \[\w+\]", f"#{item_id} [{status}]", self.text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from core import database
    from routes import note_routes

    pushes = []

    async def fake_dispatch(title, body, key, owner="", settings_override=None, **kw):
        pushes.append({"title": title, "body": body, "key": key, **(settings_override or {})})
        return {"ntfy_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", fake_dispatch)
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    for var in ("BARVIS_ACTION_ITEMS_CALENDAR", "BARVIS_NOTIFY_CHANNEL", "BARVIS_DIGEST_HOUR"):
        monkeypatch.delenv(var, raising=False)
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        yield factory, pushes


def notes(factory):
    from core.database import Note

    with factory() as db:
        return {n.title: n for n in db.query(Note).all()}


@pytest.mark.asyncio
async def test_new_items_become_todos_events_and_one_push(env):
    factory, pushes = env
    module = FakeModule()
    now = datetime.datetime(2026, 10, 19, 7, 0)  # before the digest hour
    stats = await bai.sync("alice", module, now=now, session_factory=factory)
    assert stats == {"created": 2, "events": 1, "closed_in_module": 0, "ticked": 0, "pushed": 1}
    got = notes(factory)
    a = got["Send Alice the revised spreadsheet"]
    assert a.note_type == "checklist" and a.label == "barvis-sound" and a.owner == "alice"
    assert json.loads(a.items) == [{"text": "Send Alice the revised spreadsheet", "done": False}]
    assert a.due_date == "2026-09-19T09:00" and "#t=40" in a.content and "#12" in a.content
    assert got["Pick up Aunt Rosa from the station"].due_date is None
    assert "Review Ben's pull request" not in got  # already done in the module

    from core.database import CalendarEvent
    with factory() as db:
        (ev,) = db.query(CalendarEvent).all()
        assert ev.all_day and ev.summary == "Due: Send Alice the revised spreadsheet"
        assert ev.dtstart == datetime.datetime(2026, 9, 19)

    assert len(pushes) == 1 and pushes[0]["title"] == "2 new action items"
    assert pushes[0]["reminder_channel"] == "ntfy" and pushes[0]["reminder_llm_synthesis"] is False
    assert "(due 2026-09-19)" in pushes[0]["body"]
    # second pass: nothing new
    assert not any((await bai.sync("alice", module, now=now, session_factory=factory)).values())


@pytest.mark.asyncio
async def test_ticking_a_todo_marks_the_module_item_done_and_back(env):
    factory, _ = env
    module = FakeModule()
    now = datetime.datetime(2026, 10, 19, 7, 0)
    await bai.sync("alice", module, now=now, session_factory=factory)
    from core.database import Note
    with factory() as db:
        n = db.query(Note).filter(Note.title.like("Send Alice%")).one()
        n.items = json.dumps([{"text": n.title, "done": True}])
        db.commit()
    stats = await bai.sync("alice", module, now=now, session_factory=factory)
    assert stats["closed_in_module"] == 1
    assert ("update_action_item", {"item_id": 12, "status": "done"}) in module.calls

    # the module closes #13 itself (e.g. via chat): the to-do gets ticked
    module.set_status(13, "dismissed")
    stats = await bai.sync("alice", module, now=now, session_factory=factory)
    assert stats["ticked"] == 1
    rosa = notes(factory)["Pick up Aunt Rosa from the station"]
    assert json.loads(rosa.items)[0]["done"] is True
    assert module.calls.count(("update_action_item", {"item_id": 12, "status": "done"})) == 1

    # reopened in the module: the to-do is unticked, not closed again
    module.set_status(12, "open")
    stats = await bai.sync("alice", module, now=now, session_factory=factory)
    assert stats["ticked"] == 1 and stats["closed_in_module"] == 0
    spreadsheet = notes(factory)["Send Alice the revised spreadsheet"]
    assert json.loads(spreadsheet.items)[0]["done"] is False


@pytest.mark.asyncio
async def test_deleted_todo_is_not_recreated(env, monkeypatch):
    factory, _ = env
    monkeypatch.setenv("BARVIS_ACTION_ITEMS_CALENDAR", "false")
    module = FakeModule()
    now = datetime.datetime(2026, 10, 19, 7, 0)
    await bai.sync("alice", module, now=now, session_factory=factory)
    from core.database import CalendarEvent, Note
    with factory() as db:
        assert db.query(CalendarEvent).count() == 0
        db.query(Note).delete()
        db.commit()
    await bai.sync("alice", module, now=now, session_factory=factory)
    assert notes(factory) == {}


@pytest.mark.asyncio
async def test_daily_digest_is_pushed_once_after_the_hour(env):
    factory, pushes = env
    module = FakeModule(text="No open action items found.")
    morning = datetime.datetime(2026, 10, 19, 9, 0)
    stats = await bai.sync("alice", module, now=morning, session_factory=factory)
    assert stats["pushed"] == 1
    assert ("get_daily_digest", {"date": "2026-10-18"}) in module.calls
    (p,) = pushes
    assert p["title"] == "Yesterday: Dentist and dinner plans"
    assert "TODO: Pick up Aunt Rosa" in p["body"] and "When answering" not in p["body"]
    await bai.sync("alice", module, now=morning.replace(hour=20), session_factory=factory)
    assert len(pushes) == 1


@pytest.mark.asyncio
async def test_missing_digest_is_retried_and_notifications_can_be_off(env, monkeypatch):
    factory, pushes = env
    module = FakeModule(text="", digest="No digest for 2026-10-18. Try search_conversations.")
    morning = datetime.datetime(2026, 10, 19, 9, 0)
    await bai.sync("alice", module, now=morning, session_factory=factory)
    await bai.sync("alice", module, now=morning, session_factory=factory)
    assert [c for c in module.calls if c[0] == "get_daily_digest"] and pushes == []
    assert len([c for c in module.calls if c[0] == "get_daily_digest"]) == 2
    monkeypatch.setenv("BARVIS_NOTIFY_CHANNEL", "off")
    module.digest = DIGEST
    await bai.sync("alice", module, now=morning, session_factory=factory)
    assert pushes == []


def test_module_server_lookup():
    class M:
        _connections = {"abc123": {"name": "convo", "status": "connected"},
                        "zzz": {"name": "other", "status": "connected"},
                        "off1": {"name": "convo2", "status": "error"}}
    assert bai.module_server_id(M(), "convo") == "abc123"
    assert bai.module_server_id(M(), "ABC123") == "abc123"
    assert bai.module_server_id(M(), "convo2") is None


def test_registered_as_builtin_task():
    from src.builtin_actions import BUILTIN_ACTION_INFO, BUILTIN_ACTIONS
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS, TaskScheduler

    assert BUILTIN_ACTIONS[bai.ACTION] is bai.action_sync_action_items
    assert bai.ACTION in BUILTIN_ACTION_INFO
    assert HOUSEKEEPING_DEFAULTS[bai.ACTION]["ship_paused"] is True
    assert bai.ACTION in TaskScheduler._SILENT_ACTIONS
