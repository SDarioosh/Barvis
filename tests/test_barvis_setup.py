"""Barvis: scripts/barvis_setup.py against a fake admin API."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "barvis_setup", Path(__file__).resolve().parents[1] / "scripts" / "barvis_setup.py")
barvis_setup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(barvis_setup)


class FakeApi:
    def __init__(self, servers=None):
        self.calls: list[tuple[str, str, object]] = []
        self.servers = servers if servers is not None else [
            {"name": "convo", "status": "connected", "is_enabled": True,
             "enabled_tool_count": 10},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/tools" and request.method == "GET":
            return httpx.Response(200, json={"tools": [
                {"id": "ask_user"}, {"id": "web_search"}, {"id": "manage_tasks"}]})
        if path == "/api/mcp/tools":
            return httpx.Response(200, json=[
                {"server_id": "email", "qualified_name": "mcp__email__send_email"},
                {"server_id": "convo", "qualified_name": "mcp__convo__get_transcript"},
            ])
        if path == "/api/mcp/servers":
            return httpx.Response(200, json=self.servers)
        if path == "/api/default-chat":
            return httpx.Response(200, json={"endpoint_id": "ep1", "model": "qwen3.5-35b"})
        return httpx.Response(200, json={"ok": True})

    def writes(self):
        return [(m, p, b) for m, p, b in self.calls
                if m in {"POST", "PATCH"} and p != "/api/auth/login"]


def _run(tmp_path, argv, api=None):
    api = api or FakeApi()
    pw = tmp_path / "pw.txt"
    pw.write_text("not-a-real-password\n")
    args = barvis_setup.parse_args(["--password-file", str(pw), *argv])
    with httpx.Client(base_url="http://barvis.test", transport=httpx.MockTransport(api)) as c:
        assert barvis_setup.run(c, args) == 0
    return api


def test_default_keeps_every_tool_and_sets_auto_mode(tmp_path):
    api = _run(tmp_path, [])
    assert api.writes() == [
        ("PATCH", "/api/model-endpoints/ep1/models",
         {"model_tool_modes": {"qwen3.5-35b": ""}}),
    ]


def test_trim_defaults_to_regular_tools(tmp_path):
    api = _run(tmp_path, ["--trim"])
    assert api.writes()[-1] == (
        "PATCH", "/api/model-endpoints/ep1/models",
        {"model_tool_modes": {"qwen3.5-35b": "full"}})


def test_no_model_modes_leaves_mode_alone(tmp_path):
    api = _run(tmp_path, ["--no-model-modes"])
    assert api.writes() == []


def test_trim_turns_off_all_but_keep_list_and_builtin_mcp(tmp_path):
    api = _run(tmp_path, ["--trim", "--keep", "manage_tasks", "--model-mode", "unchanged"])
    assert api.writes() == [
        ("POST", "/api/tools",
         {"disabled": ["web_search", "mcp__email__send_email"]}),
    ]


def test_untrim_clears_disabled_list_and_sets_auto(tmp_path):
    api = _run(tmp_path, ["--untrim"])
    assert api.writes() == [
        ("POST", "/api/tools", {"disabled": []}),
        ("PATCH", "/api/model-endpoints/ep1/models",
         {"model_tool_modes": {"qwen3.5-35b": ""}}),
    ]


def test_dry_run_writes_nothing(tmp_path):
    api = _run(tmp_path, ["--trim", "--dry-run"])
    assert api.writes() == []


def test_missing_module_server_is_reported(tmp_path, capsys):
    _run(tmp_path, [], FakeApi(servers=[]))
    assert "module MCP server 'convo' is not registered" in capsys.readouterr().err


def test_keep_without_trim_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        barvis_setup.parse_args(["--password-file", "x", "--keep", "web_search"])
