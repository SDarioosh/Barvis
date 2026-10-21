"""Fork: odysseus-sound's confirmed facts -> Odysseus memory (src/module_facts.py).

Fictional data only.
"""
import datetime
import json

import pytest

from src import module_facts as mf
from src.memory import MemoryManager

T0 = datetime.datetime(2026, 10, 21, 9, 0, tzinfo=datetime.timezone.utc)
T1 = T0 + datetime.timedelta(hours=1)


def fact(fid, text, about="owner", subject="Me", **kw):
    return {"id": fid, "subject": subject, "about": about, "fact": text,
            "times_said": kw.get("times_said", 2), "days": kw.get("days", 2),
            "first_said": kw.get("first_said", "2026-10-01"),
            "last_said": kw.get("last_said", "2026-10-15"),
            "link": f"http://h:8000/view/c{fid}?sig=abc#t={fid * 10}"}


class FakeModule:
    """The module's list_facts, as the MCP call returns it."""

    def __init__(self, facts=None, text=None):
        self.facts = list(facts or [])
        self.text = text
        self.calls = []

    async def __call__(self, tool, args):
        self.calls.append((tool, args))
        assert tool == "list_facts", f"the sync must not call {tool}"
        assert args == {"confirmed": True, "format": "json"}
        if self.text is not None:
            return self.text
        return json.dumps({"facts": self.facts}, separators=(",", ":"))


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mf, "_LOCKS", {})
    for var in ("MODULE_FACTS_SYNC", "BARVIS_FACTS_SYNC", "MODULE_ACTION_ITEMS_SERVER",
                "BARVIS_ACTION_ITEMS_SERVER"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def memories(path, owner="alice"):
    return [m for m in MemoryManager(str(path)).load_all() if (m.get("owner") or "") == owner]


def copies(path, owner="alice"):
    return {m["module_fact_id"]: m for m in memories(path, owner) if "module_fact_id" in m}


BASE = [
    fact(1, "Prefers oat milk in coffee"),
    fact(2, "Lives in Lisbon with her dog Pixel", about="person", subject="Jenn"),
    fact(3, "The office moves to Building C in November", about="other", subject="Office"),
]


@pytest.mark.asyncio
async def test_first_run_adds_one_memory_per_fact(data):
    stats = await mf.sync("alice", FakeModule(BASE), now=T0)
    assert stats == {"kept": 0, "added": 3, "updated": 0, "retired": 0}
    got = copies(data)
    assert set(got) == {1, 2, 3}
    one = got[1]
    assert one["text"] == ("Prefers oat milk in coffee "
                           "(source: http://h:8000/view/c1?sig=abc#t=10)")
    assert one["source"] == "odysseus-sound" and one["owner"] == "alice"
    assert one["link"] == "http://h:8000/view/c1?sig=abc#t=10"
    assert (one["subject"], one["about"], one["times_said"], one["days"]) == ("Me", "owner", 2, 2)
    assert (one["first_said"], one["last_said"]) == ("2026-10-01", "2026-10-15")
    assert one["synced_at"] == "2026-10-21T09:00:00+00:00"
    assert one["uses"] == 0 and one["id"]
    assert [got[i]["category"] for i in (1, 2, 3)] == ["preference", "contact", "fact"]
    state = json.loads((data / "module_facts_alice.json").read_text())
    assert state["added"] == 3 and state["last_run"] == "2026-10-21T09:00:00+00:00"


@pytest.mark.asyncio
async def test_rerun_is_idempotent(data):
    module = FakeModule(BASE)
    await mf.sync("alice", module, now=T0)
    before = (data / "memory.json").read_text()
    stats = await mf.sync("alice", module, now=T1)
    assert stats == {"kept": 3, "added": 0, "updated": 0, "retired": 0}
    assert (data / "memory.json").read_text() == before  # not even synced_at moves


@pytest.mark.asyncio
async def test_changed_fact_is_updated_in_place(data):
    module = FakeModule(BASE)
    await mf.sync("alice", module, now=T0)
    old = copies(data)[1]
    # the memory was used in chat meanwhile
    MemoryManager(str(data)).increment_uses([old["id"]])
    module.facts[0] = fact(1, "Prefers soy milk in coffee", times_said=3, days=3,
                           last_said="2026-10-20")
    stats = await mf.sync("alice", module, now=T1)
    assert stats == {"kept": 2, "added": 0, "updated": 1, "retired": 0}
    new = copies(data)[1]
    assert new["id"] == old["id"] and new["uses"] == 1
    assert new["text"] == "Prefers soy milk in coffee (source: http://h:8000/view/c1?sig=abc#t=10)"
    assert (new["times_said"], new["last_said"]) == (3, "2026-10-20")
    assert new["synced_at"] == "2026-10-21T10:00:00+00:00"
    # metadata alone also counts as a change
    module.facts[1] = {**module.facts[1], "link": "http://h:8000/view/c9?sig=def#t=5"}
    assert (await mf.sync("alice", module, now=T1))["updated"] == 1


@pytest.mark.asyncio
async def test_memory_text_carries_the_link_and_old_bare_copies_are_upgraded(data):
    module = FakeModule(BASE)
    await mf.sync("alice", module, now=T0)
    for fid, row in zip((1, 2, 3), BASE):
        got = copies(data)[fid]
        assert got["text"] == f"{row['fact']} (source: {row['link']})"
        assert got["link"] == row["link"]  # the separate field stays
    # a copy written before the link was in the text
    mm = MemoryManager(str(data))
    entries = mm.load_all_for_update()
    old = next(m for m in entries if m.get("module_fact_id") == 2)
    old["text"] = "Lives in Lisbon with her dog Pixel"
    mm.save(entries)
    stats = await mf.sync("alice", module, now=T1)
    assert stats == {"kept": 2, "added": 0, "updated": 1, "retired": 0}
    got = copies(data)[2]
    assert got["id"] == old["id"]
    assert got["text"] == ("Lives in Lisbon with her dog Pixel "
                           "(source: http://h:8000/view/c2?sig=abc#t=20)")
    # no link from the module: the bare fact
    assert mf.memory_text({"fact": "Likes jazz", "link": ""}) == "Likes jazz"


def test_category_ignores_words_in_the_link():
    row = {**fact(5, "Works night shifts"), "link": "http://h:8000/view/likes-love?sig=want#t=1"}
    assert mf.copy_fields(row)["category"] == "fact"
    assert "likes-love" in mf.copy_fields(row)["text"]


@pytest.mark.asyncio
async def test_fact_no_longer_returned_is_retired(data):
    module = FakeModule(BASE)
    await mf.sync("alice", module, now=T0)
    module.facts = module.facts[1:]
    stats = await mf.sync("alice", module, now=T1)
    assert stats == {"kept": 2, "added": 0, "updated": 0, "retired": 1}
    assert set(copies(data)) == {2, 3}


@pytest.mark.asyncio
async def test_own_memories_are_untouched_and_deleted_copy_comes_back(data):
    mm = MemoryManager(str(data))
    mine = mm.add_entry("I like hiking", source="user", category="preference", owner="alice")
    mm.save([mine])
    module = FakeModule(BASE)
    await mf.sync("alice", module, now=T0)
    # Memory Tidy (or the owner) deletes a copy and rewrites another
    entries = mm.load_all_for_update()
    entries = [m for m in entries if m.get("module_fact_id") != 2]
    for m in entries:
        if m.get("module_fact_id") == 3:
            m["text"], m["category"] = "Office moving", "project"
    mm.save(entries)
    stats = await mf.sync("alice", module, now=T1)
    assert stats == {"kept": 1, "added": 1, "updated": 1, "retired": 0}
    got = copies(data)
    assert got[3]["text"] == ("The office moves to Building C in November "
                              "(source: http://h:8000/view/c3?sig=abc#t=30)")
    assert got[3]["category"] == "fact"
    assert any(m["id"] == mine["id"] and m["text"] == "I like hiking"
               for m in memories(data))


@pytest.mark.asyncio
async def test_other_owners_copies_are_untouched(data):
    await mf.sync("bob", FakeModule([fact(1, "Prefers tea"), fact(7, "Enjoys chess")]), now=T0)
    bob_before = copies(data, "bob")
    await mf.sync("alice", FakeModule(BASE), now=T0)
    await mf.sync("alice", FakeModule([]), now=T1)  # alice's facts all retracted
    assert copies(data) == {}
    assert copies(data, "bob") == bob_before
    assert (data / "module_facts_bob.json").exists()


@pytest.mark.asyncio
async def test_auth_off_owner_none_and_empty_are_the_same(data):
    await mf.sync(None, FakeModule(BASE[:1]), now=T0)
    (entry,) = memories(data, "")
    assert "owner" not in entry
    assert (await mf.sync("", FakeModule(BASE[:1]), now=T1))["kept"] == 1


@pytest.mark.asyncio
async def test_text_answer_does_nothing_and_logs_once(data, caplog):
    module = FakeModule(text="3 facts. When answering, put the link next to each fact.")
    with caplog.at_level("INFO", logger="src.module_facts"):
        assert await mf.sync("alice", module, now=T0) is None
        assert await mf.sync("alice", module, now=T1) is None
    assert sum("answered list_facts with text" in r.getMessage() for r in caplog.records) == 1
    assert memories(data) == []
    assert mf.parse_facts_json("[1, 2]") is None and mf.parse_facts_json("12") is None


def test_parse_skips_bad_rows_and_repeated_ids():
    rows = mf.parse_facts_json(json.dumps({"facts": [
        fact(1, "Likes jazz"), {"id": "x", "fact": "no id"}, {"id": 2, "fact": "  "},
        fact(1, "Duplicate"), "junk", {**fact(4, "Hates queues"), "id": "4"}]}))
    assert [(r["id"], r["fact"]) for r in rows] == [(1, "Likes jazz"), (4, "Hates queues")]


@pytest.mark.parametrize("about,text,want", [
    ("owner", "Likes spicy food", "preference"),
    ("owner", "Favourite band is The Lumens", "preference"),
    ("owner", "Doesn't want meetings before ten", "preference"),
    ("owner", "Works night shifts on Tuesdays", "fact"),
    ("owner", "Likely to move next spring", "fact"),  # whole words only
    ("person", "Loves sailing", "contact"),
    ("other", "The gym prefers cash", "fact"),
    ("", "Something", "fact"),
])
def test_category_mapping(about, text, want):
    assert mf.category({"about": about, "fact": text}) == want


def test_summary_has_counts_only():
    assert mf.summary({"kept": 12, "added": 2, "updated": 1, "retired": 0}) == \
        "facts: 12 kept, 2 added, 1 updated, 0 retired"


# ------------------------------------------------------------------ the task entry point
class FakeManager:
    def __init__(self, module):
        self.module = module
        self._connections = {"srv1": {"status": "connected", "name": "convo"}}

    async def call_tool(self, name, arguments):
        assert name.startswith("mcp__srv1__")
        return {"stdout": await self.module(name.removeprefix("mcp__srv1__"), arguments)}


@pytest.fixture
def manager(monkeypatch):
    from src import tool_utils

    holder = FakeManager(FakeModule(BASE))
    monkeypatch.setattr(tool_utils, "get_mcp_manager", lambda: holder)
    return holder


@pytest.mark.asyncio
async def test_action_reports_counts_then_noops(data, manager):
    from src.builtin_actions import TaskNoop

    result, ok = await mf.action_sync_facts("alice")
    assert (result, ok) == ("facts: 0 kept, 3 added, 0 updated, 0 retired", True)
    assert "oat milk" not in result
    with pytest.raises(TaskNoop, match="3 kept"):
        await mf.action_sync_facts("alice")
    manager.module.text = "No facts."
    with pytest.raises(TaskNoop, match="text"):
        await mf.action_sync_facts("alice")


@pytest.mark.asyncio
async def test_action_off_does_nothing(data, manager, monkeypatch):
    from src.builtin_actions import TaskNoop

    monkeypatch.setenv("MODULE_FACTS_SYNC", "off")
    with pytest.raises(TaskNoop, match="off"):
        await mf.action_sync_facts("alice")
    assert manager.module.calls == [] and memories(data) == []
    assert not (data / "module_facts_alice.json").exists()


@pytest.mark.asyncio
async def test_action_without_module_server_noops(data, manager):
    from src.builtin_actions import TaskNoop

    manager._connections = {}
    with pytest.raises(TaskNoop, match="not connected"):
        await mf.action_sync_facts("alice")


@pytest.mark.asyncio
async def test_action_unreadable_memory_store_fails_without_saving(data, manager):
    (data / "memory.json").write_text("{not json", encoding="utf-8")
    result, ok = await mf.action_sync_facts("alice")
    assert ok is False and result.startswith("Facts sync failed")
    assert (data / "memory.json").read_text(encoding="utf-8") == "{not json"


def test_registered_as_hourly_silent_builtin_task():
    from src.builtin_actions import BUILTIN_ACTION_INFO, BUILTIN_ACTIONS
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS, TaskScheduler

    assert BUILTIN_ACTIONS[mf.ACTION] is mf.action_sync_facts
    assert mf.ACTION in BUILTIN_ACTION_INFO
    defaults = HOUSEKEEPING_DEFAULTS[mf.ACTION]
    assert defaults["name"] == "Module Facts"
    assert (defaults["schedule"], defaults["cron_expression"]) == ("cron", "0 * * * *")
    assert mf.ACTION in TaskScheduler._SILENT_ACTIONS
