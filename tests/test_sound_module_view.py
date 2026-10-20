"""Fork: odysseus-sound page data from the module's MCP tools (src/sound_module_view.py)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import sound_module_view as view

CONVS = """2 conversations (times are Europe/Berlin). When answering, put the link next to each fact you use:
- 2026-10-14 17:00–17:12 "Alice hands over vendor contracts" id c1; people: Alice, Me
  Alice moves to Berlin; the user takes over her vendor contracts.
  http://h:8000/view/c1?sig=a
- 2026-10-17 23:00–23:02 "(not summarized: looked like tv_or_media)" id c2; people: —
  
  http://h:8000/view/c2?sig=b"""

UNNAMED = """2 unnamed speakers (times are UTC). When answering, put the link next to each fact you use:
- Speaker 1 in "Standup" 2026-10-07 09:30 (conversation id c3), 4 lines; sounds like Ben (0.47)
  #31 "I finished the API migration yesterday." http://h:8000/view/c3?sig=c#t=0
  #33 "I'm a bit worried about database load." http://h:8000/view/c3?sig=c#t=16
- Speaker 2 in "Standup" 2026-10-07 09:30 (conversation id c3), 1 lines
  #32 "Nice work." http://h:8000/view/c3?sig=c#t=8"""


def test_parse_conversations():
    a, b = view.parse_conversations(CONVS)
    assert (a.id, a.start, a.end, a.title, a.people) == (
        "c1", "2026-10-14 17:00", "17:12", "Alice hands over vendor contracts", ["Alice", "Me"])
    assert a.summary.startswith("Alice moves") and a.link == "http://h:8000/view/c1?sig=a"
    assert not a.voice_note
    assert (b.people, b.summary, b.link) == ([], "", "http://h:8000/view/c2?sig=b")
    assert view.parse_conversations("No conversations found.") == []


def test_parse_unnamed():
    s1, s2 = view.parse_unnamed(UNNAMED)
    assert (s1.label, s1.conversation_id, s1.lines, s1.guess, s1.guess_score) == (
        "Speaker 1", "c3", 4, "Ben", 0.47)
    assert [q.line_id for q in s1.quotes] == [31, 33]
    assert s1.quotes[0].link.endswith("#t=0")
    assert (s2.guess, [q.text for q in s2.quotes]) == (None, ["Nice work."])
    assert view.parse_unnamed("No unnamed speakers found.") == []


ITEMS = """2 open action items (times are UTC). When answering, put the link next to each fact you use:
- #12 [open] Send Alice the revised spreadsheet (owner Me) due 2026-09-19 (Friday) — from "Q4 budget" 2026-09-15 10:00 http://h:8000/view/a?sig=x#t=40
- #13 [open] Pick up Aunt Rosa — from "Dinner plans" 2026-10-16 20:00 http://h:8000/view/b?sig=y#t=16"""


def test_parse_action_items():
    a, b = view.parse_action_items(ITEMS)
    assert (a.id, a.text, a.owner, a.due) == (
        12, "Send Alice the revised spreadsheet", "Me", "2026-09-19 (Friday)")
    assert (b.text, b.owner, b.due, b.link) == (
        "Pick up Aunt Rosa", None, None, "http://h:8000/view/b?sig=y#t=16")


class FakeManager:
    _connections = {"srv1": {"name": "convo", "status": "connected"}}

    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        tool = name.split("__")[-1]
        if tool == "update_action_item":
            return {"stdout": f"Action item #{args['item_id']} is now {args['status']}.",
                    "exit_code": 0}
        out = {"search_conversations": CONVS, "list_unnamed_speakers": UNNAMED,
               "list_action_items": ITEMS,
               "name_speaker": "Renamed 4 lines from Speaker 1 to Ben; the summary will be redone."}
        return {"stdout": out[tool], "exit_code": 0}


@pytest.fixture
def client(monkeypatch):
    from core.middleware import require_admin
    from routes.sound_module_routes import setup_sound_module_routes

    manager = FakeManager()
    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: manager)
    app = FastAPI()
    app.include_router(setup_sound_module_routes())
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as c:
        c.manager = manager
        yield c


def test_routes_call_only_module_tools(client):
    r = client.get("/api/modules/sound/conversations", params={"since": "2026-10-01", "limit": 999})
    assert r.status_code == 200 and r.json()["conversations"][0]["id"] == "c1"
    name, args = client.manager.calls[-1]
    assert name == "mcp__srv1__search_conversations"
    assert args["since"] == "2026-10-01" and args["limit"] == 50
    assert "kind" not in args  # older module versions don't take it
    client.get("/api/modules/sound/conversations", params={"kind": "note"})
    assert client.manager.calls[-1][1]["kind"] == "note"
    client.get("/api/modules/sound/conversations", params={"kind": "bogus"})
    assert "kind" not in client.manager.calls[-1][1]
    assert len(client.get("/api/modules/sound/unnamed").json()["speakers"]) == 2

    r = client.post("/api/modules/sound/name-speaker",
                    json={"conversation_id": "c3", "speaker_label": "Speaker 1", "name": " Ben "})
    assert r.json() == {"ok": True, "message": "Renamed 4 lines from Speaker 1 to Ben; "
                                               "the summary will be redone."}
    assert client.manager.calls[-1] == ("mcp__srv1__name_speaker", {
        "conversation_id": "c3", "speaker_label": "Speaker 1", "name": "Ben",
        "remember_voice": True})
    assert client.post("/api/modules/sound/name-speaker",
                       json={"conversation_id": "c3", "speaker_label": "Speaker 1",
                             "name": ""}).status_code == 422


def test_action_items_are_live_from_the_module(client):
    r = client.get("/api/modules/sound/action-items")
    assert [i["id"] for i in r.json()["action_items"]] == [12, 13]
    assert client.manager.calls[-1] == ("mcp__srv1__list_action_items", {"status": "open"})
    r = client.post("/api/modules/sound/action-items/12", json={"status": "done"})
    assert r.json() == {"ok": True, "message": "Action item #12 is now done."}
    assert client.manager.calls[-1] == ("mcp__srv1__update_action_item",
                                        {"item_id": 12, "status": "done"})
    assert client.post("/api/modules/sound/action-items/12",
                       json={"status": "deleted"}).status_code == 422
    assert client.get("/api/modules/sound/action-items?status=bogus").status_code == 400


def test_module_not_connected(client):
    client.manager._connections = {}
    assert client.get("/api/modules/sound/unnamed").status_code == 503


def test_api_is_admin_only(monkeypatch):
    from routes.sound_module_routes import setup_sound_module_routes

    app = FastAPI()
    app.include_router(setup_sound_module_routes())
    monkeypatch.setattr("core.middleware.auth_disabled", lambda: False)
    with TestClient(app) as c:
        assert c.get("/api/modules/sound/unnamed").status_code == 403


def test_former_page_address_redirects(client):
    r = client.get("/barvis/sound", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/modules/sound"


def test_parse_voice_note_rows():
    # odysseus-sound marks voice notes after the title: "Title" (voice note)
    convs = view.parse_conversations(
        '- 2026-10-16 08:00–08:01 "Grocery list" (voice note) id n1; people: Me\n'
        "  Buy milk and eggs.\n  http://h:8000/view/n1?sig=a")
    assert [(c.id, c.title, c.people, c.link, c.voice_note) for c in convs] == [
        ("n1", "Grocery list", ["Me"], "http://h:8000/view/n1?sig=a", True)]
    items = view.parse_action_items(
        '- #20 [open] Buy milk due 2026-10-17 (tomorrow) — from "Grocery list" (voice note) '
        "2026-10-16 08:00 http://h:8000/view/n1?sig=a#t=3")
    assert [(i.id, i.text, i.due, i.conversation_title) for i in items] == [
        (20, "Buy milk", "2026-10-17 (tomorrow)", "Grocery list")]


def test_parse_action_item_text_with_due_word():
    a, b = view.parse_action_items(
        '- #21 [open] Confirm the due date with Ana (owner Me) due 2026-10-23 (Friday) — from '
        '"Invoices" 2026-10-16 08:00 http://h:8000/view/x?sig=a#t=3\n'
        '- #22 [open] Check the due date with Ana due next week — from '
        '"Invoices" 2026-10-16 08:00 http://h:8000/view/x?sig=a#t=9')
    assert (a.text, a.owner, a.due) == ("Confirm the due date with Ana", "Me",
                                        "2026-10-23 (Friday)")
    assert (b.text, b.owner, b.due) == ("Check the due date with Ana", None, "next week")
