"""Fork: the belief review page's routes (routes/beliefs_routes.py).

Fictional data only.
"""
import datetime
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.beliefs_routes as br
from core.middleware import require_admin
from src import evidence_intake as ledger
from src import module_facts as mf

T0 = datetime.datetime(2026, 10, 21, 9, 0, tzinfo=datetime.timezone.utc)


def ev(sid, text, subject="Me", about="owner", confirmed=False, source=mf.SOURCE, **kw):
    return ledger.Evidence(
        source=source, source_id=str(sid), subject=subject, about=about, text=text,
        link=f"http://h:8000/view/c{sid}?sig=abc#t={sid * 10}",
        seen_first=kw.get("seen_first", "2026-10-01"), seen_last=kw.get("seen_last", "2026-10-15"),
        times=kw.get("times", 1), days=kw.get("days", 1), source_confirmed=confirmed)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mf, "_LOCKS", {})
    for var in ("MODULE_FACTS_SYNC", "BARVIS_FACTS_SYNC", "BELIEF_CONFIRM_SOURCES",
                "BELIEF_CONFIRM_TIMES", "BELIEF_CONFIRM_DAYS", "BELIEF_STALE_DAYS"):
        monkeypatch.delenv(var, raising=False)
    ledger.submit("alice", [
        ev(1, "Prefers oat milk in coffee", confirmed=True),
        ev(2, "Plays the cello"),
        ev(3, "Lives in Lisbon with her dog Pixel", subject="Jenn", about="person"),
    ], now=T0)
    ledger.submit("bob", [ev(9, "Collects vintage maps")], now=T0)
    return tmp_path


def ids(owner="alice"):
    return {b["shown_text"]: b["id"] for b in ledger.list_beliefs(owner)}


def client_for(user="alice"):
    app = FastAPI()

    @app.middleware("http")
    async def session(request, call_next):
        request.state.current_user = user
        request.state.api_token = False
        return await call_next(request)

    app.dependency_overrides[require_admin] = lambda: None
    app.include_router(br.setup_beliefs_routes())
    return TestClient(app)


@pytest.fixture
def client(data):
    return client_for()


def use_module(monkeypatch, call):
    monkeypatch.setattr(br, "module_caller", lambda: call)


# ------------------------------------------------------------------ reads
def test_list_defaults_to_all_but_rejected_with_stats(client):
    ledger.reject("alice", ids()["Plays the cello"])
    body = client.get("/api/beliefs").json()
    assert {b["shown_text"] for b in body["beliefs"]} == {
        "Prefers oat milk in coffee", "Lives in Lisbon with her dog Pixel"}
    assert body["stats"] == {"tentative": 1, "confirmed": 1, "stale": 0, "rejected": 1,
                             "total": 3, "structured": 0}
    assert all(b["owner"] == "alice" for b in body["beliefs"])


def test_list_status_and_subject_filters(client):
    got = client.get("/api/beliefs?status=tentative").json()["beliefs"]
    assert {b["shown_text"] for b in got} == {"Plays the cello",
                                              "Lives in Lisbon with her dog Pixel"}
    got = client.get("/api/beliefs?status=confirmed").json()["beliefs"]
    assert [b["confirmed_by"] for b in got] == ["source"]
    got = client.get("/api/beliefs?status=tentative&subject=jenn").json()["beliefs"]
    assert [b["subject"] for b in got] == ["Jenn"]
    assert len(client.get("/api/beliefs?status=all").json()["beliefs"]) == 3
    assert client.get("/api/beliefs?status=nonsense").status_code == 400


def test_get_has_evidence_and_events(client):
    body = client.get(f"/api/beliefs/{ids()['Prefers oat milk in coffee']}").json()
    assert body["status"] == "confirmed"
    assert body["evidence"][0]["link"] == "http://h:8000/view/c1?sig=abc#t=10"
    assert body["evidence"][0]["source"] == "odysseus-sound"
    assert [e["kind"] for e in body["events"]][-1] == "added"
    assert client.get("/api/beliefs/99999").status_code == 404


def test_another_owners_belief_is_404(client):
    bobs = ids("bob")["Collects vintage maps"]
    assert client.get(f"/api/beliefs/{bobs}").status_code == 404
    for action in ("confirm", "restore", "forget"):
        assert client.post(f"/api/beliefs/{bobs}/{action}").status_code == 404
    assert client.post(f"/api/beliefs/{bobs}/correct", json={"text": "x"}).status_code == 404
    assert ledger.get_belief("bob", bobs)["status"] == "tentative"


# ------------------------------------------------------------------ owner actions
def test_confirm(client):
    bid = ids()["Plays the cello"]
    body = client.post(f"/api/beliefs/{bid}/confirm").json()
    assert (body["status"], body["confirmed_by"]) == ("confirmed", "owner")
    assert body["memory_id"]


def test_correct_and_empty_text(client):
    bid = ids()["Plays the cello"]
    assert client.post(f"/api/beliefs/{bid}/correct", json={"text": "  "}).status_code == 400
    assert client.post(f"/api/beliefs/{bid}/correct", json={}).status_code == 400
    body = client.post(f"/api/beliefs/{bid}/correct",
                       json={"text": "Plays the  viola"}).json()
    assert (body["owner_text"], body["shown_text"], body["text"]) == \
        ("Plays the viola", "Plays the viola", "Plays the cello")
    assert (body["status"], body["confirmed_by"]) == ("confirmed", "owner")


def test_restore(client, monkeypatch):
    use_module(monkeypatch, None)
    bid = ids()["Plays the cello"]
    client.post(f"/api/beliefs/{bid}/forget")
    body = client.post(f"/api/beliefs/{bid}/restore").json()
    assert body["status"] == "tentative"


# ------------------------------------------------------------------ forget
class Module:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    async def __call__(self, tool, args):
        self.calls.append((tool, args))
        if self.error:
            raise self.error
        return self.answer


@pytest.mark.parametrize("answer", ["Fact #1 retired.", "No fact #1.",
                                    "Fact #1 was already retired."])
def test_forget_retires_the_module_fact(client, monkeypatch, answer):
    """A fact id the module no longer knows (re-summarized) is gone anyway: retired."""
    module = Module(answer)
    use_module(monkeypatch, module)
    bid = ids()["Prefers oat milk in coffee"]
    r = client.post(f"/api/beliefs/{bid}/forget", json={"reason": "wrong"})
    assert r.status_code == 200
    body = r.json()
    assert body["propagated"] == {"retired": 1, "failed": 0, "unsupported": 0}
    assert (body["belief"]["status"], body["belief"]["note"]) == ("rejected", "wrong")
    assert module.calls == [("retire_fact", {"fact_id": 1,
                                             "reason": "owner forgot it in Odysseus"})]
    assert not body["belief"]["memory_id"]


@pytest.mark.parametrize("error", [RuntimeError("retire_fact: database is locked"),
                                   ConnectionError("connection refused")])
def test_forget_stands_when_the_module_fails(client, monkeypatch, error):
    use_module(monkeypatch, Module(error=error))
    bid = ids()["Prefers oat milk in coffee"]
    r = client.post(f"/api/beliefs/{bid}/forget")
    assert r.status_code == 200
    assert r.json()["propagated"] == {"retired": 0, "failed": 1, "unsupported": 0}
    assert ledger.get_belief("alice", bid)["status"] == "rejected"


def test_forget_without_the_module(client, monkeypatch):
    use_module(monkeypatch, None)
    bid = ids()["Prefers oat milk in coffee"]
    body = client.post(f"/api/beliefs/{bid}/forget").json()
    assert body["propagated"] == {"retired": 0, "failed": 0, "unsupported": 1}
    assert body["belief"]["status"] == "rejected"


def test_forget_with_a_missing_retire_tool(client, monkeypatch):
    use_module(monkeypatch, Module(error=RuntimeError("retire_fact: Unknown tool: retire_fact")))
    bid = ids()["Prefers oat milk in coffee"]
    body = client.post(f"/api/beliefs/{bid}/forget").json()
    assert body["propagated"] == {"retired": 0, "failed": 0, "unsupported": 1}


def test_forget_without_module_evidence_calls_nothing(client, monkeypatch):
    module = Module("Fact #1 retired.")
    use_module(monkeypatch, module)
    ledger.submit("alice", [ev(7, "Keeps a sourdough starter", source="notes")], now=T0)
    bid = ids()["Keeps a sourdough starter"]
    body = client.post(f"/api/beliefs/{bid}/forget").json()
    assert body["propagated"] == {"retired": 0, "failed": 0, "unsupported": 0}
    assert module.calls == []


# ------------------------------------------------------------------ run
class FactsModule:
    def __init__(self, facts):
        self.facts, self.calls = facts, []

    async def __call__(self, tool, args):
        self.calls.append((tool, args))
        if tool == "list_people":
            return "No people yet."  # an older module: text, tolerated
        assert tool == "list_facts"
        return json.dumps({"facts": self.facts})


def test_run_syncs_facts_now(client, monkeypatch):
    module = FactsModule([
        {"id": 1, "subject": "Me", "about": "owner", "fact": "Prefers oat milk in coffee",
         "times_said": 3, "days": 2, "first_said": "2026-10-01", "last_said": "2026-10-15",
         "link": "http://h:8000/view/c1?sig=abc#t=10", "confirmed": True},
        {"id": 5, "subject": "Me", "about": "owner", "fact": "Runs on Saturday mornings",
         "times_said": 1, "days": 1, "first_said": "2026-10-20", "last_said": "2026-10-20",
         "link": "http://h:8000/view/c5?sig=abc#t=50", "confirmed": False},
    ])
    use_module(monkeypatch, module)
    r = client.post("/api/beliefs/run")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"].startswith("facts: 2 seen, 1 new, ")
    assert body["stats"]["facts"] == 2
    assert module.calls == [("list_facts", {"format": "json", "limit": 1000}),
                            ("list_people", {"format": "json"})]
    assert "Runs on Saturday mornings" in ids()


def test_run_without_the_module_or_switched_off(client, monkeypatch):
    use_module(monkeypatch, None)
    assert client.post("/api/beliefs/run").status_code == 503
    monkeypatch.setenv("MODULE_FACTS_SYNC", "off")
    assert client.post("/api/beliefs/run").status_code == 409


def test_run_reports_a_failed_call(client, monkeypatch):
    use_module(monkeypatch, Module(error=RuntimeError("list_facts: boom")))
    r = client.post("/api/beliefs/run")
    assert r.status_code == 502 and "boom" in r.json()["error"]


# ------------------------------------------------------------------ subjects (2.1)
def subject_ids(owner="alice"):
    return {s["display"]: s["id"] for s in ledger.list_subjects(owner)}


@pytest.fixture
def contacts(data, monkeypatch):
    """A local contact book (no CardDAV): two of alice's, one of bob's."""
    import routes.contacts.contacts_routes as cr

    book = data / "contacts.json"
    book.write_text(json.dumps({"contacts": [
        {"uid": "uid-jenn", "name": "Jenn Okafor", "owner": "alice"},
        {"uid": "uid-arlo", "name": "Arlo Vance", "owner": "alice"},
        {"uid": "uid-wren", "name": "Wren Hale", "owner": "bob"},
    ]}))
    monkeypatch.setattr(cr, "LOCAL_CONTACTS_FILE", book)
    monkeypatch.setattr(cr, "_get_carddav_config", lambda: {"url": ""})
    monkeypatch.setattr(cr, "_contact_cache", {"contacts": [], "fetched_at": None,
                                               "failed_at": None, "by_owner": {}})
    return book


def test_subjects_list_with_counts_kind_filter_and_stats(client):
    body = client.get("/api/beliefs/subjects").json()
    got = {s["display"]: s for s in body["subjects"]}
    assert [s["kind"] for s in body["subjects"]][0] == "owner"  # the owner first
    assert {k: v["kind"] for k, v in got.items()} == {"Owner": "owner", "Jenn": "person"}
    assert got["Owner"]["counts"] == {"tentative": 1, "confirmed": 1, "stale": 0,
                                      "rejected": 0, "total": 2}
    assert body["stats"]["total"] == 3
    people = client.get("/api/beliefs/subjects?kind=person").json()["subjects"]
    assert [s["display"] for s in people] == ["Jenn"]
    assert client.get("/api/beliefs/subjects?kind=robot").status_code == 400
    assert all(s["owner"] == "alice" for s in body["subjects"])


def test_subject_detail_has_beliefs_with_structure_and_summary(client):
    jenn = subject_ids()["Jenn"]
    bid = ids()["Lives in Lisbon with her dog Pixel"]
    ledger.confirm("alice", bid)
    assert ledger.apply_structure("alice", bid, "lives_in", "Lisbon", "2024-05-01")
    ledger.submit("alice", [ev(4, "Jenn dislikes cilantro", subject="Jenn", about="person")],
                  now=T0)
    ledger.reject("alice", ids()["Jenn dislikes cilantro"])
    body = client.get(f"/api/beliefs/subjects/{jenn}").json()
    assert (body["subject"]["display"], body["subject"]["kind"]) == ("Jenn", "person")
    assert [(b["attribute"], b["value"]) for b in body["beliefs"]] == [("lives_in", "Lisbon")]
    assert body["summary"] == "Jenn (person). lives in: Lisbon (since 2024-05-01)."
    owner = subject_ids()["Owner"]
    body = client.get(f"/api/beliefs/subjects/{owner}").json()
    assert {b["shown_text"] for b in body["beliefs"]} == {"Prefers oat milk in coffee",
                                                          "Plays the cello"}
    assert body["summary"].startswith("Owner (owner). Also: Prefers oat milk in coffee.")


def test_subject_404s(client):
    bobs = subject_ids("bob")["Owner"]
    assert client.get(f"/api/beliefs/subjects/{bobs}").status_code == 404
    assert client.get("/api/beliefs/subjects/99999").status_code == 404
    assert client.get("/api/beliefs/subjects/nope").status_code == 404
    for action, body in (("kind", {"kind": "thing"}), ("rename", {"display": "X"}),
                         ("link", {"contact_id": "uid-jenn"}), ("unlink", None)):
        assert client.post(f"/api/beliefs/subjects/{bobs}/{action}",
                           json=body).status_code == 404
    jenn = subject_ids()["Jenn"]
    r = client.post("/api/beliefs/subjects/merge", json={"keep_id": jenn, "drop_id": bobs})
    assert r.status_code == 404
    r = client.post("/api/beliefs/subjects/merge", json={"keep_id": bobs, "drop_id": jenn})
    assert r.status_code == 404
    assert subject_ids("bob") == {"Owner": bobs}


def test_set_kind(client):
    got = subject_ids()
    body = client.post(f"/api/beliefs/subjects/{got['Jenn']}/kind",
                       json={"kind": "project"}).json()
    assert (body["kind"], body["kind_by"]) == ("project", "owner")
    r = client.post(f"/api/beliefs/subjects/{got['Jenn']}/kind", json={"kind": "owner"})
    assert r.status_code == 400 and "person, project or thing" in r.json()["detail"]
    r = client.post(f"/api/beliefs/subjects/{got['Owner']}/kind", json={"kind": "person"})
    assert r.status_code == 400 and r.json()["detail"] == "the owner subject keeps its kind"


def test_rename_and_duplicate_name(client):
    ledger.submit("alice", [ev(5, "Arlo teaches pottery", subject="Arlo", about="person")],
                  now=T0)
    got = subject_ids()
    body = client.post(f"/api/beliefs/subjects/{got['Jenn']}/rename",
                       json={"display": "Jenn Okafor"}).json()
    assert (body["display"], body["aliases"]) == ("Jenn Okafor", ["Jenn"])
    r = client.post(f"/api/beliefs/subjects/{got['Arlo']}/rename",
                    json={"display": "jenn okafor"})
    assert r.status_code == 400 and "already called" in r.json()["detail"]
    r = client.post(f"/api/beliefs/subjects/{got['Arlo']}/rename", json={"display": " "})
    assert r.status_code == 400


def test_link_unlink_and_contact_beliefs(client, contacts):
    ledger.submit("alice", [ev(5, "Arlo teaches pottery", subject="Arlo", about="person")],
                  now=T0)
    got = subject_ids()
    body = client.post(f"/api/beliefs/subjects/{got['Jenn']}/link",
                       json={"contact_id": "uid-jenn"}).json()
    assert body["contact_id"] == "uid-jenn"
    beliefs = client.get("/api/beliefs/contact/uid-jenn").json()
    assert beliefs["contact_id"] == "uid-jenn"
    assert [b["shown_text"] for b in beliefs["beliefs"]] == ["Lives in Lisbon with her dog Pixel"]
    assert beliefs["beliefs"][0]["contact_id"] == "uid-jenn"
    assert client.get("/api/beliefs/contact/uid-jenn?status=confirmed").json()["beliefs"] == []
    assert client.get("/api/beliefs/contact/uid-arlo").json()["beliefs"] == []
    assert client.get("/api/beliefs/contact/uid-jenn?status=x").status_code == 400
    # a contact links to one subject: a second link is a 400 with the ledger's message
    r = client.post(f"/api/beliefs/subjects/{got['Arlo']}/link", json={"contact_id": "uid-jenn"})
    assert r.status_code == 400 and "is linked to subject" in r.json()["detail"]
    # another owner's contact, an unknown one, or none: 400, nothing linked
    for cid in ("uid-wren", "uid-nobody", " "):
        r = client.post(f"/api/beliefs/subjects/{got['Arlo']}/link", json={"contact_id": cid})
        assert r.status_code == 400
    assert ledger.get_subject("alice", got["Arlo"])["contact_id"] is None
    body = client.post(f"/api/beliefs/subjects/{got['Jenn']}/unlink").json()
    assert body["contact_id"] is None
    assert client.get("/api/beliefs/contact/uid-jenn").json()["beliefs"] == []


def test_contacts_listing_is_owner_scoped_and_marks_links(client, contacts):
    jenn = subject_ids()["Jenn"]
    ledger.link_contact("alice", jenn, "uid-jenn")
    body = client.get("/api/beliefs/contacts").json()
    assert body["contacts"] == [
        {"id": "uid-arlo", "name": "Arlo Vance", "subject_id": None},
        {"id": "uid-jenn", "name": "Jenn Okafor", "subject_id": jenn},
    ]
    body = client_for("bob").get("/api/beliefs/contacts").json()
    assert body["contacts"] == [{"id": "uid-wren", "name": "Wren Hale", "subject_id": None}]
    # bob's view of alice's contact: nothing
    assert client_for("bob").get("/api/beliefs/contact/uid-jenn").json()["beliefs"] == []


def test_merge_and_its_400s(client, contacts):
    ledger.submit("alice", [ev(5, "Jen O. runs a bakery", subject="Jen O.", about="person")],
                  now=T0)
    got = subject_ids()
    ledger.link_contact("alice", got["Jen O."], "uid-jenn")
    body = client.post("/api/beliefs/subjects/merge",
                       json={"keep_id": got["Jenn"], "drop_id": got["Jen O."]}).json()
    assert body["id"] == got["Jenn"] and body["aliases"] == ["Jen O."]
    assert body["contact_id"] == "uid-jenn" and body["counts"]["total"] == 2
    assert "Jen O." not in subject_ids()
    r = client.post("/api/beliefs/subjects/merge",
                    json={"keep_id": got["Jenn"], "drop_id": got["Jenn"]})
    assert r.status_code == 400 and "into itself" in r.json()["detail"]
    r = client.post("/api/beliefs/subjects/merge",
                    json={"keep_id": got["Jenn"], "drop_id": got["Owner"]})
    assert r.status_code == 400 and r.json()["detail"] == "the owner subject can only be kept"
    assert client.post("/api/beliefs/subjects/merge", json={}).status_code == 422


def test_attribute_filter(client):
    bid = ids()["Prefers oat milk in coffee"]
    ledger.apply_structure("alice", bid, "likes", "oat milk")
    got = client.get("/api/beliefs?attribute=likes").json()["beliefs"]
    assert [(b["id"], b["value"]) for b in got] == [(bid, "oat milk")]
    assert client.get("/api/beliefs?attribute=diet").json()["beliefs"] == []
    assert client.get("/api/beliefs?attribute=shoe_size").status_code == 400
    assert client.get("/api/beliefs?attribute=likes&status=tentative").json()["beliefs"] == []


def test_belief_routes_leave_other_literal_paths_alone(client):
    """``/api/beliefs/search`` belongs to another router: not swallowed as a belief id."""
    assert client.get("/api/beliefs/search?q=milk").status_code == 404


# ------------------------------------------------------------------ auth
def test_routes_refuse_api_tokens(data):
    app = FastAPI()

    @app.middleware("http")
    async def token(request, call_next):
        request.state.api_token = True
        request.state.current_user = "api"
        request.state.api_token_owner = "alice"
        return await call_next(request)

    app.include_router(br.setup_beliefs_routes())
    c = TestClient(app)
    assert c.get("/api/beliefs").status_code in (401, 403)
    assert c.post("/api/beliefs/1/confirm").status_code in (401, 403)
    assert c.post("/api/beliefs/run").status_code in (401, 403)


def test_page_is_served(client):
    r = client.get("/beliefs")
    assert r.status_code == 200
    assert "What I know about you" in r.text and "/static/js/beliefs.js" in r.text
