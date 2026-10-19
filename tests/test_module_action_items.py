"""Fork: odysseus-sound action items -> ntfy (src/module_action_items.py)."""
import datetime

import pytest

from src import module_action_items as bai

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
            assert args == {"status": "open"}  # read live, never written by the sync
            return "\n".join(ln for ln in self.text.splitlines() if "[done]" not in ln)
        if tool == "get_daily_digest":
            return self.digest
        raise AssertionError(f"the sync must not call {tool}")


@pytest.fixture
def pushes(tmp_path, monkeypatch):
    from routes import note_routes

    sent = []

    async def fake_dispatch(title, body, key, owner="", settings_override=None, **kw):
        sent.append({"title": title, "body": body, "key": key, **(settings_override or {})})
        return {"ntfy_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", fake_dispatch)
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    for var in ("MODULE_NOTIFY_CHANNEL", "MODULE_DIGEST_HOUR",
                "BARVIS_NOTIFY_CHANNEL", "BARVIS_DIGEST_HOUR"):
        monkeypatch.delenv(var, raising=False)
    return sent


EARLY = datetime.datetime(2026, 10, 19, 7, 0)  # before the digest hour


@pytest.mark.asyncio
async def test_first_run_remembers_the_backlog_then_pushes_only_new_items(pushes):
    module = FakeModule()
    assert await bai.sync("alice", module, now=EARLY) == {"new": 2, "pushed": 0}
    assert pushes == []
    module.text += ('\n- #15 [open] Book the restaurant due 2026-10-21 (Wednesday) — from '
                    '"Dinner" 2026-10-19 06:30 http://h:8000/view/d?sig=w#t=5')
    assert await bai.sync("alice", module, now=EARLY) == {"new": 1, "pushed": 1}
    (p,) = pushes
    assert p["title"] == "New action item" and "Book the restaurant (due 2026-10-21)" in p["body"]
    assert p["reminder_channel"] == "ntfy" and p["reminder_llm_synthesis"] is False
    assert await bai.sync("alice", module, now=EARLY) == {"new": 0, "pushed": 0}
    assert [c[0] for c in module.calls] == ["list_action_items"] * 3


@pytest.mark.asyncio
async def test_items_due_today_and_digest_are_pushed_once_a_day(pushes):
    module = FakeModule()
    await bai.sync("alice", module, now=EARLY)
    due_day = datetime.datetime(2026, 9, 19, 9, 0)  # #12 is due 2026-09-19
    await bai.sync("alice", module, now=due_day)
    titles = [p["title"] for p in pushes]
    assert titles == ["Action item due today", "Yesterday: Dentist and dinner plans"]
    assert "Send Alice the revised spreadsheet" in pushes[0]["body"]
    assert ("get_daily_digest", {"date": "2026-09-18"}) in module.calls
    assert "TODO: Pick up Aunt Rosa" in pushes[1]["body"]
    assert "When answering" not in pushes[1]["body"]
    await bai.sync("alice", module, now=due_day.replace(hour=20))
    assert len(pushes) == 2


@pytest.mark.asyncio
async def test_missing_digest_is_retried_and_notifications_can_be_off(pushes, monkeypatch):
    module = FakeModule(text="", digest="No digest for 2026-10-18. Try search_conversations.")
    morning = datetime.datetime(2026, 10, 19, 9, 0)
    await bai.sync("alice", module, now=morning)
    await bai.sync("alice", module, now=morning)
    assert len([c for c in module.calls if c[0] == "get_daily_digest"]) == 2 and pushes == []
    monkeypatch.setenv("MODULE_NOTIFY_CHANNEL", "off")
    module.digest = DIGEST
    await bai.sync("alice", module, now=morning)
    assert pushes == []


def test_settings_fall_back_to_former_barvis_names(monkeypatch):
    for var in ("MODULE_DIGEST_HOUR", "BARVIS_DIGEST_HOUR"):
        monkeypatch.delenv(var, raising=False)
    assert bai.digest_hour() == 8
    monkeypatch.setenv("BARVIS_DIGEST_HOUR", "6")
    assert bai.digest_hour() == 6
    monkeypatch.setenv("MODULE_DIGEST_HOUR", "7")
    assert bai.digest_hour() == 7


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
