"""Admin-trusted read-only MCP servers (src/mcp_trust.py)."""
from types import SimpleNamespace

import pytest

from src import mcp_trust
from src.agent_runtime.authority import ExactOperation, create_request_authority
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_policy import ToolPolicy


class FakeManager:
    def __init__(self):
        self._connections = {
            "srv1": {"status": "connected", "name": "convo"},
            "srv2": {"status": "connected", "name": "other"},
        }
        ro = {"readOnlyHint": True}
        self._tools = {
            "srv1": [
                {"name": "search_transcripts", "annotations": ro},
                # pydantic-style annotations object, as the mcp client returns
                {"name": "get_transcript", "annotations": SimpleNamespace(readOnlyHint=True,
                                                                          destructiveHint=None)},
                {"name": "name_speaker", "annotations": {"readOnlyHint": False}},
                {"name": "list_guess"},  # no annotation: name heuristic must not apply
                {"name": "wipe", "annotations": {"readOnlyHint": True, "destructiveHint": True}},
            ],
            "srv2": [{"name": "search_web", "annotations": ro}],
        }

    def is_builtin(self, server_id):
        return False


@pytest.fixture
def manager(monkeypatch):
    m = FakeManager()
    monkeypatch.setattr(mcp_trust, "_manager", lambda: m)
    return m


SEARCH = "mcp__srv1__search_transcripts"


def test_off_by_default(manager, monkeypatch):
    monkeypatch.delenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, raising=False)
    assert mcp_trust.trusted_readonly_mcp_tools() == frozenset()
    assert not mcp_trust.is_trusted_readonly_mcp_tool(SEARCH)


@pytest.mark.parametrize("value", ["convo", "CONVO", "srv1", " other , convo "])
def test_only_declared_read_only_tools_on_named_servers(manager, monkeypatch, value):
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, value)
    tools = mcp_trust.trusted_readonly_mcp_tools()
    assert SEARCH in tools and "mcp__srv1__get_transcript" in tools
    for name in ("name_speaker", "list_guess", "wipe"):
        assert f"mcp__srv1__{name}" not in tools
    assert ("mcp__srv2__search_web" in tools) == ("other" in value)


def test_disconnected_server_is_not_trusted(manager, monkeypatch):
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    manager._connections["srv1"]["status"] = "error"
    assert mcp_trust.trusted_readonly_mcp_tools() == frozenset()


def test_request_authority_grants_trusted_tools_only(manager, monkeypatch):
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    auth = create_request_authority("What did Mike say last week?", owner="alice",
                                    session_id="s", policy=ToolPolicy())
    assert auth.permits(ExactOperation.normalize(SEARCH, '{"speaker":"Mike"}'))
    assert not auth.permits(ExactOperation.normalize(
        "mcp__srv1__name_speaker", '{"name":"Mike"}'))


def test_request_authority_still_honours_disables(manager, monkeypatch):
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    auth = create_request_authority("What did Mike say?", owner="alice", session_id="s",
                                    policy=ToolPolicy())
    op = ExactOperation.normalize(SEARCH, "{}")
    assert not auth.restrict(disabled_tools={SEARCH}).permits(op)
    assert not auth.restrict(ToolPolicy(disable_mcp=True)).permits(op)


def test_gate_admits_trusted_reads_after_external_context(manager, monkeypatch):
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    ctx = ToolRunSecurityContext()
    ctx.observe_tool_result(SEARCH, {"output": "untrusted transcript text"}, "{}")
    assert ctx.external_untrusted_context_seen
    assert ctx.decision_for(SEARCH, "{}").allowed
    # Write tools on the trusted server and tools on other servers stay gated.
    assert not ctx.decision_for("mcp__srv1__name_speaker", "{}").allowed
    assert not ctx.decision_for("mcp__srv2__search_web", "{}").allowed


def test_web_disabled_shortcut_yields_to_trusted_tools(manager, monkeypatch):
    from src.agent_loop import WEB_TOOL_NAMES, _web_search_unavailable_for_turn
    disabled = set(WEB_TOOL_NAMES) | {"private_browser"}
    monkeypatch.delenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, raising=False)
    baseline = _web_search_unavailable_for_turn({"web"}, disabled, "What's the latest on the move?",
                                                None, None)
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    assert _web_search_unavailable_for_turn({"web"}, disabled, "What's the latest on the move?",
                                            None, None) is False
    # Disabling the trusted tools restores the original behaviour.
    trusted = mcp_trust.trusted_readonly_mcp_tools()
    assert _web_search_unavailable_for_turn({"web"}, disabled | trusted,
                                            "What's the latest on the move?", None, None) == baseline
