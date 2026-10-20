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


# get_conversation / get_transcript as odysseus-sound's mcp_server.py prints them (fictional)
CONV = """"Alice hands over vendor contracts" — 2026-10-14 17:00–17:12 (Europe/Berlin), device omi
Speakers: Alice, Me. People: Alice, Me
Link: http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef
Summary: Alice moves to Berlin; the user takes over her vendor contracts.
* Alice leaves for the Berlin office in January
* The user owns the vendor contracts from November
Action #12 [open]: Send Alice the revised spreadsheet (owner Me) due 2026-10-23 (Friday) http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef#t=40
Action #13 [done]: Call the printer vendor http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef#t=56
Fact [Alice]: Alice moves to the Berlin office in January. http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef#t=48
When answering, put the link next to each fact you use."""

TRANSCRIPT = """"Alice hands over vendor contracts" 2026-10-14 17:00 http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef
#31 [0s 17:00:00] Alice: Morning. I have news: I'm moving to Berlin.
#32 [8s 17:00:08] Me: Wow, congratulations!
#33 [16s 17:00:16] Speaker 2: Who takes the vendor contracts?
… 5 more lines; call again with a later start_seconds.
When answering, put the link next to each fact you use."""


def test_parse_conversation():
    c = view.parse_conversation(CONV, "c1")
    assert (c.id, c.title, c.when) == (
        "c1", "Alice hands over vendor contracts",
        "2026-10-14 17:00–17:12 (Europe/Berlin), device omi")
    assert (c.speakers, c.people) == (["Alice", "Me"], ["Alice", "Me"])
    assert c.link == "http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef"
    assert c.summary == "Alice moves to Berlin; the user takes over her vendor contracts."
    assert len(c.key_points) == 2 and c.key_points[0].startswith("Alice leaves")
    a, b = c.action_items
    assert (a.id, a.status, a.text, a.owner, a.due, a.link[-5:]) == (
        12, "open", "Send Alice the revised spreadsheet", "Me", "2026-10-23 (Friday)", "#t=40")
    assert (b.id, b.status, b.text, b.owner, b.due) == (
        13, "done", "Call the printer vendor", None, None)
    assert [(f.subject, f.fact) for f in c.facts] == [
        ("Alice", "Alice moves to the Berlin office in January.")]


def test_parse_conversation_voice_note_and_empty():
    c = view.parse_conversation(
        '"Grocery list" (voice note) — 2026-10-16 08:00–08:01 (UTC), device omi, type note\n'
        "Speakers: —. People: —\nLink: http://h:8000/view/n1?sig=a\nSummary: (pending)\n"
        "some new line shape\nWhen answering, put the link next to each fact you use.", "n1")
    assert (c.title, c.when, c.speakers, c.people) == (
        "Grocery list", "2026-10-16 08:00–08:01 (UTC), device omi, type note", [], [])
    assert (c.summary, c.key_points, c.action_items, c.facts) == (
        "(pending)\nsome new line shape", [], [], [])
    assert view.parse_conversation("", "x").title == ""


def test_parse_transcript():
    t = view.parse_transcript(TRANSCRIPT)
    assert t.title == "Alice hands over vendor contracts"
    assert [(ln.id, ln.offset, ln.time, ln.speaker) for ln in t.lines] == [
        (31, 0, "17:00:00", "Alice"), (32, 8, "17:00:08", "Me"), (33, 16, "17:00:16", "Speaker 2")]
    assert t.lines[0].text == "Morning. I have news: I'm moving to Berlin."
    assert t.lines[2].link == "http://h:8000/view/c1?sig=0123456789abcdef0123456789abcdef#t=16"
    assert (t.more, t.next_start) == (True, 16)
    last = view.parse_transcript("\n".join(TRANSCRIPT.splitlines()[:3] + ["garbage", "x."]))
    assert (len(last.lines), last.more, last.next_start) == (2, False, None)


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
        if tool in {"get_conversation", "get_transcript"} and args["conversation_id"] != "c1":
            return {"stdout": f"No conversation {args['conversation_id']!r}.", "exit_code": 0}
        if tool == "reassign_line":
            msg = (f"No line #{args['line_id']}." if args["line_id"] != 32 else
                   f"Line #32 (\"Wow, congratulations!\") moved from Me to {args['name']}; "
                   "the conversation's summary will be redone.")
            return {"stdout": msg, "exit_code": 0}
        if tool == "rename_speaker_everywhere":
            msg = (f"Renamed {args['old_name']} to {args['new_name']} in 3 lines across 1 "
                   "conversations; their summaries will be redone."
                   if args["old_name"] == "Alise" else f"No lines by {args['old_name']!r}. "
                   "Known speakers: Alice, Me.")
            return {"stdout": msg, "exit_code": 0}
        out = {"search_conversations": CONVS, "list_unnamed_speakers": UNNAMED,
               "list_action_items": ITEMS, "get_conversation": CONV,
               "get_transcript": TRANSCRIPT,
               "name_speaker": "Renamed 4 lines from Speaker 1 to Ben; the summary will be redone."}
        return {"stdout": out[tool], "exit_code": 0}


@pytest.fixture
def client(monkeypatch):
    # The routes module's own require_admin: another test may swap core.middleware in
    # sys.modules, and then a fresh import names a different function.
    from routes.sound_module_routes import require_admin, setup_sound_module_routes

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
    assert [(c.id, c.title, c.people, c.link) for c in convs] == [
        ("n1", "Grocery list", ["Me"], "http://h:8000/view/n1?sig=a")]
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


def test_conversation_and_transcript_routes(client):
    r = client.get("/api/modules/sound/conversations/c1")
    assert r.status_code == 200
    conv = r.json()["conversation"]
    assert conv["id"] == "c1" and conv["action_items"][0]["id"] == 12
    assert client.manager.calls[-1] == ("mcp__srv1__get_conversation", {"conversation_id": "c1"})

    r = client.get("/api/modules/sound/conversations/c1/transcript", params={"start_seconds": 8})
    body = r.json()
    assert [ln["id"] for ln in body["lines"]] == [31, 32, 33]
    assert (body["more"], body["next_start"]) == (True, 16)
    assert client.manager.calls[-1] == ("mcp__srv1__get_transcript", {
        "conversation_id": "c1", "start_seconds": 8, "max_lines": 200})

    assert client.get("/api/modules/sound/conversations/nope").status_code == 404
    assert client.get("/api/modules/sound/conversations/nope/transcript").status_code == 404
    n = len(client.manager.calls)
    assert client.get("/api/modules/sound/conversations/bad%20id").status_code == 400
    assert client.get("/api/modules/sound/conversations/" + "a" * 201).status_code == 400
    assert client.get("/api/modules/sound/conversations/a$b/transcript").status_code == 400
    assert len(client.manager.calls) == n  # nothing reached the module


def test_fix_speaker_routes(client):
    r = client.post("/api/modules/sound/lines/32", json={"name": " Alice "})
    assert r.json()["ok"] is True and r.json()["message"].startswith("Line #32")
    assert client.manager.calls[-1] == ("mcp__srv1__reassign_line",
                                        {"line_id": 32, "name": "Alice"})
    assert client.post("/api/modules/sound/lines/99", json={"name": "Alice"}).json() == {
        "ok": False, "message": "No line #99."}

    r = client.post("/api/modules/sound/rename-speaker",
                    json={"old_name": "Alise", "new_name": "Alice"})
    assert r.json()["ok"] is True
    assert client.manager.calls[-1] == ("mcp__srv1__rename_speaker_everywhere",
                                        {"old_name": "Alise", "new_name": "Alice"})
    r = client.post("/api/modules/sound/rename-speaker",
                    json={"old_name": "Zed", "new_name": "Alice"})
    assert r.json()["ok"] is False

    n = len(client.manager.calls)
    assert client.post("/api/modules/sound/lines/32", json={"name": "  "}).status_code == 400
    assert client.post("/api/modules/sound/lines/0", json={"name": "Alice"}).status_code == 400
    assert client.post("/api/modules/sound/rename-speaker",
                       json={"old_name": "Alise", "new_name": "x" * 101}).status_code == 400
    assert len(client.manager.calls) == n


def test_reassign_line_already_correct_is_ok():
    import asyncio

    async def call(tool, args):
        return f"Line #{args['line_id']} is already {args['name']}'s."

    assert asyncio.run(view.reassign_line(call, 5, "Ben")) == {
        "ok": True, "message": "Line #5 is already Ben's."}
