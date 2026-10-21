"""Pasted text with a rewrite request gets no tools (src/inline_rewrite.py)."""

from __future__ import annotations

import pytest

from src.action_intents import classify_tool_intent
from src.inline_rewrite import pasted_text_rewrite
from src.personal_store_escalation import personal_store_turn
from src.tool_policy import build_effective_tool_policy
from src.turn_contract import (
    inline_text_transformation,
    requested_capabilities,
    resolve_turn_contract,
    selected_tools_for_request,
)
from tests.test_module_turn_scope import SCHEMAS, FakeManager
from tests.test_personal_store_escalation import _chat_turn, _turn_mode

# Fictional draft. Its words ("meeting", "email", a name) used to pull in the
# calendar, email and contact tools.
DRAFT = """Hi Jason,

I hope you are doing well. I wanted to reach out about the project timeline we discussed
in the meeting last week. I been looking at the schedule and i think we might need more
time for the testing phase, because the vendor told me there shipment is late.

Could you let me know if their is any flexibility? I also wanted to ask if you got any
email from the vendor about this.

Any guidance you can offer here is appreciated,
Sam"""

REWRITES = [
    DRAFT + "\nMake this sound grammatically correct and with a good flow",
    DRAFT + "\n\nMake this sound grammatically correct and with a good flow",
    DRAFT + "\r\n\r\nCan you rewrite this to sound more professional?",
    DRAFT + "\n\nPlease proofread the above",
    DRAFT + "\n\nfix the grammar and spelling",
    DRAFT + "\n\nImprove the flow",
    DRAFT + "\n\nmake it shorter",
    DRAFT + "\n\nTranslate this into German",
    DRAFT + "\n\nPolish my email please",
    "Rewrite this email so it sounds friendlier:\n\n" + DRAFT,
    "Can you fix the grammar in the following?\n" + DRAFT,
]

# Requests that need tools, or that are not text to edit, keep upstream routing.
NOT_REWRITES = [
    "",
    "Make this sound grammatically correct",  # no text
    "fix the grammar",
    DRAFT + "\n\nRewrite this and send it to Jason",
    DRAFT + "\n\nMake this sound better, then reply to Jason's email",
    DRAFT + "\n\nfix the grammar and save it as a note",
    DRAFT + "\n\nput this in the calendar",
    DRAFT + "\n\nmake it a task",
    DRAFT + "\n\nWhat do you think?",
    ("Find the email from Alex Example about the budget for next quarter's offsite planning\n"
     "and make it sound more professional"),
    "What's on my calendar tomorrow?\nAlso, what are my tasks for today?",
]


@pytest.mark.parametrize("message", REWRITES)
def test_pasted_text_with_a_rewrite_request_is_text_to_edit(message):
    assert pasted_text_rewrite(message)
    assert inline_text_transformation(message)
    # Upstream callers: no exact tools selected, no promotion out of chat mode.
    assert selected_tools_for_request(message) == frozenset()
    assert not classify_tool_intent(message).needs_tools
    assert not personal_store_turn(message)


@pytest.mark.parametrize("message", NOT_REWRITES)
def test_other_messages_are_left_alone(message):
    assert not pasted_text_rewrite(message)


def test_upstream_colon_form_still_works():
    assert inline_text_transformation("Fix the grammar: " + DRAFT)
    assert not classify_tool_intent("Fix the grammar: " + DRAFT).needs_tools


def test_store_requests_still_escalate():
    assert classify_tool_intent("send an email to Alex Example saying I'm late").needs_tools
    assert personal_store_turn("what's on my calendar tomorrow?")


def test_agent_mode_contract_offers_no_tools_even_after_earlier_tool_use(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_TRUSTED_READONLY_MCP", "convo")
    from src.module_turn_scope import with_module_tools

    message = REWRITES[0]
    contract = resolve_turn_contract(
        capabilities=requested_capabilities(message), schemas=SCHEMAS,
        policy=build_effective_tool_policy(), message=message,
        selected_tools=selected_tools_for_request(message),
        warm_tools={"search_emails", "manage_calendar"},  # used earlier in the chat
    )
    assert "search_emails" in contract.offered
    scoped = with_module_tools(contract, message=message, schemas=SCHEMAS,
                               manager=FakeManager())
    assert scoped.offered == frozenset() and scoped.schema_json == ()


@pytest.mark.asyncio
async def test_chat_mode_rewrite_stays_in_chat_mode(monkeypatch):
    chunks, agent_kwargs, captured = await _chat_turn(monkeypatch, REWRITES[0])
    assert _turn_mode(chunks)["mode"] == "chat"
    assert not agent_kwargs and "chat" in captured
