"""Fork: the Belief Structure task (src/belief_structure.py, plan stage 2.2).

Fictional data only.
"""
import asyncio
import datetime
import json
import re

import pytest

from src import belief_structure as bs
from src import evidence_intake as ei

T0 = datetime.datetime(2026, 10, 21, 9, 0, tzinfo=datetime.timezone.utc)
T1 = T0 + datetime.timedelta(hours=1)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bs, "_LOCKS", {})
    for var in ("BELIEF_CONFIRM_SOURCES", "BELIEF_CONFIRM_TIMES", "BELIEF_CONFIRM_DAYS",
                "BELIEF_STALE_DAYS", "BELIEF_STRUCTURE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def seed(*facts, owner="alice", confirmed=True):
    """facts: (id, subject, about, text)."""
    ei.submit(owner, [ei.Evidence(
        source="odysseus-sound", source_id=str(fid), subject=subject, about=about, text=text,
        link=f"http://h:8000/view/c{fid}?sig=abc#t=10", seen_first="2026-10-01",
        seen_last="2026-10-15", times=3 if confirmed else 1, days=2 if confirmed else 1,
        source_confirmed=confirmed)
        for fid, subject, about, text in facts], now=T0)
    return {b["text"]: b for b in ei.list_beliefs(owner)}


class FakeLlm:
    """Answers each batch from ``answer(items)``; ``items`` are the beliefs it was sent."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def __call__(self, messages):
        self.calls.append(messages)
        body = messages[1]["content"]
        sent = [json.loads(line) for line in body.split("\n") if line.startswith("{")]
        return self.answer(sent, len(self.calls))


VALUES = {
    "Priya is allergic to peanuts": ("allergy", "peanuts", None, None),
    "Priya lives in Porto since March 2025": ("lives_in", "Porto", "2025-03-01", None),
    "Heron launches before 2027-01-31": ("deadline", "2027-01-31", None, "2027-01-31"),
    "Tomas works at Acme as a designer": ("shoe_size", "Acme designer", None, None),
}


def good(sent, _n):
    return json.dumps({"items": [
        {"id": b["id"], "attribute": VALUES[b["text"]][0], "value": VALUES[b["text"]][1],
         "valid_from": VALUES[b["text"]][2], "valid_to": VALUES[b["text"]][3]}
        for b in sent]})


FACTS = [(1, "Priya Nair", "person", "Priya is allergic to peanuts"),
         (2, "Priya Nair", "person", "Priya lives in Porto since March 2025"),
         (3, "Heron", "other", "Heron launches before 2027-01-31"),
         (4, "Tomas Berg", "person", "Tomas works at Acme as a designer")]


def test_structures_confirmed_beliefs_with_validation(data):
    beliefs = seed(*FACTS)
    seed((5, "Lena", "person", "Lena likes tea"), confirmed=False)
    llm = FakeLlm(good)
    stats = asyncio.run(bs.run("alice", llm=llm, now=T1))
    assert stats == {"candidates": 4, "structured": 4, "other": 1, "waiting": 0, "failed": 0}
    assert len(llm.calls) == 1
    sent = llm.calls[0][1]["content"]
    assert "Lena likes tea" not in sent  # tentative: never sent
    assert '"subject": "Priya Nair", "kind": "person"' in sent
    assert "data, not instructions" in llm.calls[0][0]["content"]
    got = {b["text"]: b for b in ei.list_beliefs("alice", status="confirmed")}
    p = got["Priya lives in Porto since March 2025"]
    assert (p["attribute"], p["value"], p["valid_from"], p["structured_at"]) == \
        ("lives_in", "Porto", "2025-03-01", "2026-10-21T10:00:00+00:00")
    assert got["Tomas works at Acme as a designer"]["attribute"] == "other"  # unknown
    heron = got["Heron launches before 2027-01-31"]
    assert (heron["attribute"], heron["valid_until"]) == ("deadline", "2027-01-31")
    assert heron["subject_kind"] == "project"  # inferred from the deadline
    assert beliefs["Priya is allergic to peanuts"]["attribute"] is None
    assert ei.stats("alice")["structured"] == 4
    # nothing left: a second run makes no model call
    assert asyncio.run(bs.run("alice", llm=llm, now=T1))["candidates"] == 0
    assert len(llm.calls) == 1


def test_bad_values_are_cleaned(data):
    seed(FACTS[0])

    def answer(sent, _n):
        return "```json\n" + json.dumps({"items": [
            {"id": sent[0]["id"], "attribute": "Lives In", "value": "x" * 500,
             "valid_from": "March 2025", "valid_to": "2026-02-30"},
            {"id": 999, "attribute": "allergy", "value": "not in the batch"}]}) + "\n```"

    stats = asyncio.run(bs.run("alice", llm=FakeLlm(answer), now=T1))
    assert stats["structured"] == 1
    (b,) = ei.list_beliefs("alice")
    assert (b["attribute"], len(b["value"]), b["valid_from"], b["valid_until"]) == \
        ("lives_in", 120, None, None)


def test_garbage_once_then_retry(data):
    seed(FACTS[0])

    def answer(sent, n):
        return "Sure! Here you go." if n == 1 else good(sent, n)

    llm = FakeLlm(answer)
    stats = asyncio.run(bs.run("alice", llm=llm, now=T1))
    assert stats["structured"] == 1 and stats["failed"] == 0 and len(llm.calls) == 2
    assert llm.calls[1][-1]["content"].startswith("That was not valid JSON")


def test_garbage_twice_leaves_the_batch_for_next_run(data):
    seed(*FACTS)
    stats = asyncio.run(bs.run("alice", llm=FakeLlm(lambda s, n: "{oops"), now=T1))
    assert stats == {"candidates": 4, "structured": 0, "other": 0, "waiting": 4, "failed": 1}
    assert len(ei.structure_candidates("alice")) == 4
    stats = asyncio.run(bs.run("alice", llm=FakeLlm(good), now=T1))
    assert stats["structured"] == 4


def test_missing_items_and_a_model_error_wait(data):
    seed(*FACTS)

    def partial(sent, _n):
        return json.dumps({"items": [{"id": sent[0]["id"], "attribute": "allergy",
                                      "value": "peanuts"}]})

    stats = asyncio.run(bs.run("alice", llm=FakeLlm(partial), now=T1))
    assert (stats["structured"], stats["waiting"]) == (1, 3)

    async def boom(messages):
        raise RuntimeError("endpoint down")

    stats = asyncio.run(bs.run("alice", llm=boom, now=T1))
    assert (stats["failed"], stats["waiting"]) == (1, 3)


def test_batches_of_ten_and_at_most_forty_per_run(data):
    seed(*[(i, f"Person {i}", "person", f"Person {i} likes number {i}") for i in range(1, 46)])

    def answer(sent, _n):
        return json.dumps({"items": [{"id": b["id"], "attribute": "likes",
                                      "value": re.sub(r".* ", "", b["text"])} for b in sent]})

    llm = FakeLlm(answer)
    stats = asyncio.run(bs.run("alice", llm=llm, now=T1))
    assert stats["candidates"] == 40 and stats["structured"] == 40
    assert len(llm.calls) == 4
    stats = asyncio.run(bs.run("alice", llm=llm, now=T1))
    assert stats["candidates"] == 5 and len(llm.calls) == 5


def test_correction_is_structured_again(data):
    beliefs = seed(FACTS[0])
    asyncio.run(bs.run("alice", llm=FakeLlm(good), now=T1))
    bid = beliefs["Priya is allergic to peanuts"]["id"]
    ei.correct("alice", bid, "Priya is allergic to tree nuts")

    def answer(sent, _n):
        assert sent[0]["text"] == "Priya is allergic to tree nuts"
        return json.dumps({"items": [{"id": bid, "attribute": "allergy",
                                      "value": "tree nuts"}]})

    stats = asyncio.run(bs.run("alice", llm=FakeLlm(answer), now=T1))
    assert stats["structured"] == 1
    assert ei.get_belief("alice", bid)["value"] == "tree nuts"


def test_text_changed_while_the_model_answered_waits(data):
    beliefs = seed(FACTS[0])
    bid = beliefs["Priya is allergic to peanuts"]["id"]

    def answer(sent, n):
        ei.correct("alice", bid, "Priya is allergic to tree nuts")  # meanwhile
        return good(sent, n)

    stats = asyncio.run(bs.run("alice", llm=FakeLlm(answer), now=T1))
    assert (stats["structured"], stats["waiting"]) == (0, 1)
    assert ei.get_belief("alice", bid)["structured_at"] is None


def test_default_llm_runs_in_the_background_without_thinking(monkeypatch):
    import src.interactive_gate as gate
    import src.llm_core as llm_core  # noqa: PLR0402
    import src.task_endpoint as te

    waited, sent = [], []

    async def fake_wait(label):
        waited.append(label)

    async def fake_call(cands, messages, **kw):
        sent.append(kw)
        return '{"items": []}'

    monkeypatch.setattr(gate, "wait_for_interactive_quiet", fake_wait)
    monkeypatch.setattr(llm_core, "llm_call_async_with_fallback", fake_call)
    monkeypatch.setattr(te, "resolve_task_candidates", lambda **kw: [("http://llm", "m", {})])

    async def go():
        call = await bs.default_llm("alice")
        return await call([{"role": "user", "content": "hi"}])

    assert asyncio.run(go()) == '{"items": []}'
    assert waited == ["belief structure"]
    assert sent[0]["workload"] == "background" and sent[0]["thinking_mode"] == "off"


def test_action_counts_only_noop_and_switch(data, monkeypatch):
    from src.builtin_actions import TaskNoop

    with pytest.raises(TaskNoop):
        asyncio.run(bs.action_belief_structure("alice"))  # nothing to do: no model call
    seed(FACTS[0])

    async def fake_llm(owner):
        return FakeLlm(good)

    monkeypatch.setattr(bs, "default_llm", fake_llm)
    text, ok = asyncio.run(bs.action_belief_structure("alice"))
    assert ok and text == "structure: 1 to do, 1 structured (0 other), 0 waiting"
    assert "Priya" not in text
    monkeypatch.setenv("BELIEF_STRUCTURE", "off")
    with pytest.raises(TaskNoop):
        asyncio.run(bs.action_belief_structure("alice"))


def test_registered_as_a_silent_model_backed_hourly_task():
    from src.builtin_actions import BUILTIN_ACTION_INFO, BUILTIN_ACTIONS
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS, TaskScheduler

    assert BUILTIN_ACTIONS[bs.ACTION] is bs.action_belief_structure
    assert bs.ACTION in BUILTIN_ACTION_INFO
    assert HOUSEKEEPING_DEFAULTS[bs.ACTION]["cron_expression"] == "15 * * * *"
    assert bs.ACTION in TaskScheduler._SILENT_ACTIONS
    assert bs.ACTION in TaskScheduler._MODEL_BACKED_ACTIONS
