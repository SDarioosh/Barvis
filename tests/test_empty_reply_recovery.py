"""A tool-free reply that was only thinking gets one answer round (src/empty_reply_recovery.py).

The owner asked qwen3.6-35b a plain question; it thought for a long time and
the chat showed an empty bubble. "Try again" usually worked.
"""
import json

import src.agent_loop as al
from src import empty_reply_recovery as err
from tests.test_agent_rounds_exhausted import _collect, _patch_common, _types

QUESTION = "How do I allow installs from unknown apps on my phone?"


def test_needs_recovery_only_for_thinking_without_text_or_tools():
    assert err.needs_recovery("", "The user asks about settings...", [])
    assert err.needs_recovery("  \n\n", "thinking", [])
    assert err.needs_recovery("<think>still thinking</think>", "thinking", [])
    assert not err.needs_recovery("Open Settings.", "thinking", [])
    assert not err.needs_recovery("", "", [])
    assert not err.needs_recovery("", "thinking", [{"tool": "web_search"}])


def test_recovery_messages_keep_the_end_of_long_reasoning():
    reasoning = "start " + "x" * (err.MAX_REASONING_CHARS + 50) + " conclusion"
    msgs = err.recovery_messages([{"role": "user", "content": QUESTION}], reasoning)
    assert msgs[0]["content"] == QUESTION
    last = msgs[-1]
    assert last["role"] == "user"
    assert "conclusion" in last["content"] and "start" not in last["content"]
    assert "do not call tools" in last["content"].lower()


def _run(monkeypatch, stream_chunks, answer="Open Settings, then Apps, then Special access."):
    _patch_common(monkeypatch)
    import src.llm_core as llm_core

    calls = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    async def _fake_stream(_candidates, messages, **kwargs):
        for chunk in stream_chunks:
            yield "data: " + json.dumps(chunk) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_call(*args, **kwargs):
        calls.append(kwargs)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_call, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "qwen3.6-35b",
        [{"role": "user", "content": QUESTION}],
        max_rounds=5,
        owner="admin",
    )))
    return events, calls


THINKING_ONLY = [{"delta": "The user wants the unknown-apps setting. Settings > Apps...",
                  "thinking": True}]


def test_thinking_only_reply_is_answered(monkeypatch):
    events, calls = _run(monkeypatch, THINKING_ONLY)

    assert len(calls) == 1
    assert calls[0]["thinking_mode"] == "off"
    assert calls[0]["model"] == "qwen3.6-35b"
    sent = calls[0]["messages"]
    assert any(m.get("content") == QUESTION for m in sent)
    assert "Settings > Apps" in sent[-1]["content"]
    final = [e for e in events if e.get("type") == "final_response"]
    assert final and final[-1]["fallback"] == "thinking_only_recovery"
    assert final[-1]["content"].startswith("Open Settings")


def test_visible_answer_is_left_alone(monkeypatch):
    _events, calls = _run(monkeypatch, THINKING_ONLY + [{"delta": "Open Settings."}])
    assert calls == []


def test_failed_recovery_keeps_upstream_behaviour(monkeypatch):
    events, calls = _run(monkeypatch, THINKING_ONLY, answer=RuntimeError("down"))
    assert len(calls) == 1
    assert not [e for e in events if e.get("fallback") == "thinking_only_recovery"]


def _sent_thinking_modes(monkeypatch, **loop_kwargs):
    """The thinking_mode each model request is sent with, after the route's per-model kwargs."""
    _patch_common(monkeypatch)
    sent = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    async def _fake_stream(candidates, messages, **kwargs):
        mode = kwargs.get("thinking_mode")
        factory = kwargs.get("candidate_request_factory")
        if factory is not None:
            url, model, headers = candidates[0]
            request = factory(0, url, model, headers)
            if hasattr(request, "__await__"):
                request = await request
            mode = {**kwargs, **((request or {}).get("kwargs") or {})}.get("thinking_mode")
        sent.append(mode)
        yield "data: " + json.dumps({"delta": "Open Settings."}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    _collect(al.stream_agent_loop(
        "http://x/v1", "qwen3.6-35b",
        [{"role": "user", "content": QUESTION}],
        max_rounds=5,
        owner="admin",
        **loop_kwargs,
    ))
    return sent


def test_thinking_off_reaches_the_primary_model(monkeypatch):
    # Agent mode dropped "Thinking: Off": the primary model's request kwargs
    # carried thinking_mode=None, so llama-server never got enable_thinking=false
    # and Qwen3.6 thought anyway.
    assert _sent_thinking_modes(monkeypatch, thinking_mode="off") == ["off"]


def test_thinking_on_reaches_the_primary_model(monkeypatch):
    assert _sent_thinking_modes(monkeypatch, thinking_mode="on") == ["on"]
