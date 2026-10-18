"""HTTP MCP servers keep their tool annotations (src/mcp_manager.py, _connect_http).

The stdio and SSE paths store each tool's ``annotations``; the Streamable HTTP
path used to drop them, so readOnlyHint never reached plan mode or the trusted
read-only check in src/mcp_trust.py, and every read over HTTP needed approval.
"""
import asyncio
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from mcp.types import Tool, ToolAnnotations

from src import mcp_trust
from src.mcp_manager import McpManager, mcp_tool_is_readonly

TOOLS = [
    Tool(name="search_transcripts", inputSchema={"type": "object"},
         annotations=ToolAnnotations(readOnlyHint=True)),
    Tool(name="name_speaker", inputSchema={"type": "object"},
         annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)),
]


class FakeSession:
    def __init__(self, *_streams):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=TOOLS)


@asynccontextmanager
async def fake_http_client(url, auth=None, **kwargs):
    yield (object(), object(), lambda: None)


@asynccontextmanager
async def fake_sse_client(url, **kwargs):
    yield (object(), object())


def _connect(transport):
    mgr = McpManager()
    if transport == "http":
        patches = [
            patch("mcp.client.streamable_http.streamablehttp_client", fake_http_client),
            patch("src.mcp_oauth.build_provider", lambda *a, **k: None),
        ]
        connect = mgr._connect_http
    else:
        patches = [patch("mcp.client.sse.sse_client", fake_sse_client)]
        connect = mgr._connect_sse
    with ExitStack() as stack:
        for p in [patch("mcp.ClientSession", FakeSession), *patches]:
            stack.enter_context(p)
        assert asyncio.run(connect("srv1", "convo", "http://127.0.0.1:1/mcp")) is True
    return mgr


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_connect_keeps_tool_annotations(transport):
    mgr = _connect(transport)
    tools = {t["name"]: t for t in mgr._tools["srv1"]}
    assert tools["search_transcripts"]["annotations"].readOnlyHint is True
    assert tools["name_speaker"]["annotations"].readOnlyHint is False
    assert mcp_tool_is_readonly(tools["search_transcripts"])
    assert not mcp_tool_is_readonly(tools["name_speaker"])


def test_trusted_http_server_grants_read_only_tools(monkeypatch):
    monkeypatch.setenv(mcp_trust.TRUSTED_READONLY_MCP_ENV, "convo")
    mgr = _connect("http")
    assert mcp_trust.trusted_readonly_mcp_tools(mgr) == frozenset(
        {"mcp__srv1__search_transcripts"})
