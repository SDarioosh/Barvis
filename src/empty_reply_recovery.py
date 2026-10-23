"""Answer a chat turn whose reply was only thinking (fork).

A thinking model (qwen3.6-35b on llama-server) sometimes spends a whole
tool-free reply on reasoning and stops without writing an answer: all of its
tokens arrive as reasoning deltas and the visible text stays empty. The chat
then shows an empty bubble after a long wait, and "try again" usually works.

Upstream keeps the reasoning as the saved reply and shows nothing more. When
that happens, the agent loop now asks the same model once more, with thinking
off, to write the answer, and gives it the end of its own reasoning so the
work isn't lost. Turns that ran tools are left to the existing recoveries
(`_empty_response_fallback`, `ran_trusted_server_tool` in src/mcp_trust.py).
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# The end of the reasoning holds the conclusions; keep that part.
MAX_REASONING_CHARS = 12_000
MAX_ANSWER_TOKENS = 4096
TIMEOUT_SECONDS = 180

INSTRUCTION = (
    "You finished thinking about my last message but wrote no answer, so I saw "
    "nothing. Your private notes so far are below. Do not call tools and do not "
    "repeat the notes; write the answer to my last message now.\n\n"
    "<notes>\n{notes}\n</notes>"
)


def _visible(text: str) -> str:
    from src.agent_loop import (
        _strip_think_blocks,
        _visible_response_text,
        strip_tool_blocks,
    )
    return _visible_response_text(_strip_think_blocks(strip_tool_blocks(text or ""))).strip()


def needs_recovery(full_response: str, reasoning: str, tool_events: Iterable[Any]) -> bool:
    """True when a tool-free turn produced reasoning but no visible answer."""
    if any(True for _ in tool_events or ()):
        return False
    if _visible(full_response):
        return False
    return bool((reasoning or "").strip())


def recovery_messages(messages: list, reasoning: str) -> list:
    notes = (reasoning or "").strip()
    if len(notes) > MAX_REASONING_CHARS:
        notes = "…" + notes[-MAX_REASONING_CHARS:]
    return list(messages) + [{"role": "user", "content": INSTRUCTION.format(notes=notes)}]


async def recover_thinking_only_reply(
    full_response: str,
    reasoning: str,
    tool_events: Iterable[Any],
    messages: list,
    *,
    url: str,
    model: str,
    headers: dict | None,
    max_tokens: int | None,
) -> str:
    """Return an answer for a thinking-only reply, or "" when not needed or it failed."""
    if not needs_recovery(full_response, reasoning, tool_events):
        return ""
    logger.info("[agent] reply was only thinking; asking once more with thinking off")
    try:
        from src.llm_core import llm_call_async

        raw = await llm_call_async(
            url=url,
            model=model,
            messages=recovery_messages(messages, reasoning),
            headers=headers,
            temperature=0.3,
            max_tokens=max(512, min(int(max_tokens or MAX_ANSWER_TOKENS), MAX_ANSWER_TOKENS)),
            timeout=TIMEOUT_SECONDS,
            max_retries=1,
            thinking_mode="off",
        )
    except Exception as exc:
        logger.warning("[agent] thinking-only recovery failed: %s", exc)
        return ""
    return _visible(raw if isinstance(raw, str) else str(raw or ""))
