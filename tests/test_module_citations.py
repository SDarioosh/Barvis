"""Transcript links on answers from trusted module tools (src/module_citations.py)."""
import json

import pytest

import src.agent_loop as al
from src import mcp_trust, module_citations as mc
from tests.test_agent_rounds_exhausted import _collect, _patch_common, _types
from tests.test_mcp_trust import SEARCH, FakeManager

L1 = "http://127.0.0.1:8000/view/" + "a" * 32 + "?sig=" + "1" * 32 + "#t=16"
L2 = "http://127.0.0.1:8000/view/" + "b" * 32 + "?sig=" + "2" * 32
L3 = "http://127.0.0.1:8000/view/" + "c" * 32 + "?sig=" + "3" * 32

TRANSCRIPT = (
    "2 transcript lines (times are Europe/Berlin). When answering, put the link next "
    "to each fact you use:\n"
    f"- #4 2026-09-15 10:00 Alice: \"They're cutting our budget by fifteen percent.\" "
    f"(conversation \"Q4 budget\", id {'a' * 32}) {L1}\n"
    f"- #9 2026-10-03 18:00 Tom: \"The rent goes up to 1520 in January.\" "
    f"(conversation \"Landlord call\", id {'b' * 32}) {L2}"
)
CONVERSATIONS = (
    "1 conversations (times are Europe/Berlin). When answering, put the link next to "
    "each fact you use:\n"
    f"- 2026-11-02 19:00–20:10 \"Dinner with Jonas\" id {'c' * 32}; people: Jonas\n"
    "  Jonas recommended The Overstory and said he is allergic to peanuts.\n"
    f"  {L3}"
)
GET = f"\"Q4 budget\" — 2026-09-15 10:00–10:20\nLink: {L1}\nSummary: the budget is cut."


@pytest.fixture
def trusted(monkeypatch):
    m = FakeManager()
    monkeypatch.setattr(mcp_trust, "_manager", lambda: m)
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    return m


def _event(output, tool=SEARCH, **extra):
    return {"tool": tool, "output": output, "exit_code": 0, **extra}


def test_links_keep_their_entry_as_context():
    links = mc.links_from_output(TRANSCRIPT)
    assert [u for u, _ in links] == [L1, L2]
    assert "fifteen percent" in links[0][1] and "1520" in links[1][1]
    (url, context), = mc.links_from_output(CONVERSATIONS)
    assert url == L3 and "Dinner with Jonas" in context and "Overstory" in context
    (url, context), = mc.links_from_output(GET)
    assert url == L1 and "Q4 budget" in context


def test_trailing_punctuation_is_not_part_of_the_link():
    (url, _), = mc.links_from_output(f"- \"x y z\" {L2}.")
    assert url == L2


def test_rank_by_shared_words_and_dedupe():
    links = mc.links_from_output(TRANSCRIPT) + mc.links_from_output(TRANSCRIPT)
    ranked = mc.rank_sources("Tom said the rent goes up to 1520 in January.", links)
    assert [u for u, _ in ranked] == [L2]
    assert ranked[0][1] == "The rent goes up to 1520 in January"


def test_label_is_trimmed_and_bracket_safe():
    label = mc._label('- "' + "word [x] " * 20 + '"')
    assert len(label) <= mc.LABEL_CHARS and "[" not in label and label.endswith("…")


def test_footer_added_when_answer_has_no_link(trusted):
    answer = "Alice said finance is cutting the budget by fifteen percent."
    out = mc.with_module_sources(answer, [_event(TRANSCRIPT)])
    assert out.startswith(answer) and "\n\nSources:\n- [" in out
    assert f"]({L1})" in out and L2 not in out


def test_mcp_events_named_in_desc(trusted):
    event = {"tool": "mcp", "desc": f"{SEARCH} query=budget", "output": TRANSCRIPT,
             "exit_code": 0}
    out = mc.with_module_sources("The budget is cut by fifteen percent.", [event])
    assert L1 in out


@pytest.mark.parametrize("answer", [
    f"Alice said fifteen percent ([transcript]({L1})).",
    "See http://other.host/view/" + "d" * 32 + " for the budget cut of fifteen percent.",
    "I couldn't find anything Alice said about Mars.",
    "There is no mention of Mars in your conversations.",
    "",
])
def test_left_alone_when_cited_or_nothing_found(trusted, answer):
    assert mc.with_module_sources(answer, [_event(TRANSCRIPT)]) == answer


def test_left_alone_without_overlap(trusted):
    answer = "Paris is the capital of France."
    assert mc.with_module_sources(answer, [_event(TRANSCRIPT)]) == answer


def test_left_alone_for_untrusted_failed_or_linkless_results(trusted):
    answer = "Alice said finance is cutting the budget by fifteen percent."
    for events in (
        [_event(TRANSCRIPT, tool="mcp__srv2__search_web")],
        [_event(TRANSCRIPT, tool="bash")],
        [_event(TRANSCRIPT, exit_code=1)],
        [_event(TRANSCRIPT, error="boom")],
        [_event("No transcript lines found.")],
        [],
    ):
        assert mc.with_module_sources(answer, events) == answer


def test_off_without_trusted_servers(trusted, monkeypatch):
    monkeypatch.delenv(mcp_trust.TRUSTED_READONLY_MCP_ENV)
    answer = "Alice said finance is cutting the budget by fifteen percent."
    assert mc.with_module_sources(answer, [_event(TRANSCRIPT)]) == answer


def _run_loop(monkeypatch, synthesis_text, links):
    """One tool round, then an empty reply; the recovery round answers."""
    _patch_common(monkeypatch)
    import src.llm_core as llm_core

    synthesis_calls = []
    rounds = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        if len(rounds) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{"id": "c1", "name": "inspect_media",
                           "arguments": json.dumps({"path": "/workspace/a.mp4"})}],
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_synthesis(*args, **kwargs):
        synthesis_calls.append(kwargs)
        return synthesis_text

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_synthesis, raising=False)
    monkeypatch.setattr(mcp_trust, "ran_trusted_server_tool", lambda *a, **k: True)
    monkeypatch.setattr(mc, "module_links", lambda *a, **k: links)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "qwen3.5-35b",
        [{"role": "user", "content": "what did Alice say about the budget?"}],
        max_rounds=5,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
    )))
    finals = [e for e in events if isinstance(e, dict) and e.get("type") == "final_response"]
    return finals, synthesis_calls


def test_recovery_round_asks_for_links(monkeypatch):
    _finals, calls = _run_loop(monkeypatch, "Fifteen percent.", [])
    assert len(calls) == 1
    assert mc.RECOVERY_CITE_RULE in str(calls[0]["messages"][-1]["content"])


def test_loop_final_answer_gets_sources(monkeypatch):
    links = mc.links_from_output(TRANSCRIPT)
    finals, _calls = _run_loop(
        monkeypatch, "Alice said finance is cutting the budget by fifteen percent.", links)
    assert finals, "no final_response event"
    last = finals[-1]["content"]
    assert "Sources:" in last and L1 in last
