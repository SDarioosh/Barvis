"""Web lookups when the web toggle is on (src/web_turn_scope.py)."""
import json

import pytest

from src.agent_runtime.authority import ExactOperation, create_request_authority
from src.agent_runtime.remote_resources import bind_backend_for_operation
from src.web_turn_scope import (
    with_unclassified_web_search,
)

SEARCH = ExactOperation.normalize("web_search", json.dumps({"query": "mistral 7b vs qwen3.5-9b"}))
FETCH = ExactOperation.normalize("web_fetch", json.dumps({"url": "https://example.com"}))


def tools(message):
    return {g.tool for g in create_request_authority(message, owner="alice", session_id="s").grants}


def authority(message):
    return create_request_authority(message, owner="alice", session_id="s")


# 1. Model comparisons and explicit web lookups route to web search.

@pytest.mark.parametrize("message", [
    "search mistral 7b vs qwen3.5-9b",
    "search the web for mistral 7b vs qwen3.5-9b",
    "search qwen 9b versus llama 8b benchmarks",
])
def test_model_comparison_gets_web_search(message):
    assert "web_search" in tools(message)


@pytest.mark.parametrize("message", [
    "find a small qwen model I can run locally",
    "search hugging face for a 7b mistral model",
])
def test_model_discovery_still_uses_hugging_face(message):
    granted = tools(message)
    assert "search_hf_models" in granted
    assert "web_search" not in granted


# 2. Web toggle on + no tool in the words → web_search only.

def test_unclassified_turn_gets_web_search_when_enabled():
    base = authority("mistral 7b vs qwen3.5-9b")
    assert not base.permits(SEARCH)
    auth = with_unclassified_web_search(base, web_search_enabled=True)
    assert auth.permits(SEARCH)
    assert not auth.permits(FETCH)
    assert bind_backend_for_operation(auth, SEARCH).resource.tool_id == "web_search"


def test_web_toggle_off_grants_nothing():
    base = authority("mistral 7b vs qwen3.5-9b")
    assert with_unclassified_web_search(base, web_search_enabled=False) is base


def test_turn_whose_words_chose_tools_is_unchanged():
    base = authority("what are my tasks for today?")
    assert with_unclassified_web_search(base, web_search_enabled=True) is base


def test_disables_still_win():
    auth = with_unclassified_web_search(authority("mistral 7b vs qwen3.5-9b"), web_search_enabled=True)
    assert not auth.restrict(disabled_tools={"web_search"}).permits(SEARCH)
    assert with_unclassified_web_search(None, web_search_enabled=True) is None
