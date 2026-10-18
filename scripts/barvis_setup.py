#!/usr/bin/env python3
"""Barvis: configure an Odysseus instance to use Barvis modules over MCP.

Works through the admin API, so it runs against any instance and leaves upstream
code alone. By default it keeps every Odysseus feature:

* Leaves the tool toggles alone (Settings > Tools stays as the admin set it).
* Sets the default chat model to "Auto" tool mode: each turn is offered the
  tools that per-turn retrieval picks, module MCP tools included.
* Reports each configured MCP server and whether it is connected, and warns when
  a module server named with ``--module`` is missing or disconnected.

``--trim`` restores the old memory-only setup: every built-in tool except a small
keep-list is turned off, the built-in email/browser/image MCP tools are hidden,
and the model is set to "Regular tools" (``full``), which offers every enabled
tool on every turn. Fewer tools make a small local model faster and keep it from
picking a built-in (tasks, calendar, chat search) over a module tool.
``--untrim`` turns every tool back on (and sets "Auto").

Don't combine "Regular tools" with every tool on: all ~230 tool schemas go into
each request (~35k tokens), more than a 32k context holds.

Trusting a module's read-only tools is not done here; that is the
``ODYSSEUS_TRUSTED_READONLY_MCP`` environment variable (see src/mcp_trust.py).

Usage:
    python scripts/barvis_setup.py --url http://127.0.0.1:7000 \
        --user admin --password-file ~/.config/barvis/admin.txt \
        [--trim [--keep a,b] | --untrim] [--model-mode auto|full|unchanged] [--dry-run]

Re-run after each upgrade. With --trim, new upstream tools are turned off unless
added to KEEP or --keep.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

# Built-ins kept by --trim. Everything else is turned off; add more with --keep.
# - manage_tasks / manage_calendar / manage_notes: off until module action items
#   sync into them. While they're empty, the model asks them "when is my next
#   appointment?" instead of the conversation store.
KEEP = {
    "ask_user",
    "update_plan",
}
# Built-in MCP servers whose tools --trim hides from the model.
BUILTIN_MCP_OFF = {"email", "builtin_browser", "image_gen"}
MODEL_MODES = {"full": "full", "auto": "", "unchanged": None}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:7000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password-file", required=True, type=Path)
    tools = ap.add_mutually_exclusive_group()
    tools.add_argument("--trim", action="store_true",
                       help="turn off every built-in tool except a small keep-list")
    tools.add_argument("--untrim", action="store_true",
                       help="turn every tool back on (undoes --trim)")
    ap.add_argument("--keep", default="", help="with --trim: extra comma-separated tools to keep")
    ap.add_argument("--model-mode", choices=sorted(MODEL_MODES), default=None,
                    help="tool mode for the default chat model "
                         "(default: full = Regular tools with --trim, else auto)")
    ap.add_argument("--no-model-modes", action="store_true",
                    help="same as --model-mode unchanged (kept for old scripts)")
    ap.add_argument("--module", action="append", default=None,
                    help="MCP server name a module registers (default: convo); repeatable")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.no_model_modes:
        args.model_mode = "unchanged"
    elif args.model_mode is None:
        args.model_mode = "full" if args.trim else "auto"
    if args.keep and not args.trim:
        ap.error("--keep only applies with --trim")
    if args.module is None:
        args.module = ["convo"]
    return args


def trimmed_tools(c: httpx.Client, keep: set[str]) -> tuple[list[str], list[str]]:
    """Return (all built-in tool ids, the list --trim turns off)."""
    tools = [t["id"] for t in c.get("/api/tools").json()["tools"]]
    disabled = sorted(t for t in tools if t not in keep)
    # The built-in MCP servers are offered under qualified mcp__<server>__* names,
    # which the bare names above don't cover.
    mcp = c.get("/api/mcp/tools").json()
    mcp = mcp.get("tools", mcp) if isinstance(mcp, dict) else mcp
    disabled += sorted(t["qualified_name"] for t in mcp
                       if t.get("server_id") in BUILTIN_MCP_OFF)
    return tools, disabled


def report_modules(c: httpx.Client, modules: list[str]) -> list[str]:
    """Print every MCP server's status; return warnings for missing modules."""
    servers = c.get("/api/mcp/servers").json()
    for s in servers:
        state = s.get("status", "?") if s.get("is_enabled", True) else "disabled"
        err = f" ({s['error']})" if s.get("error") else ""
        print(f"mcp server {s.get('name')!r}: {state}, "
              f"{s.get('enabled_tool_count', 0)} tools{err}")
    warnings = []
    by_name = {str(s.get("name", "")).casefold(): s for s in servers}
    for name in modules:
        s = by_name.get(name.casefold())
        if s is None:
            warnings.append(f"module MCP server {name!r} is not registered "
                            f"(Settings > MCP; see docs/barvis/modules.md)")
        elif s.get("status") != "connected" or not s.get("is_enabled", True):
            warnings.append(f"module MCP server {name!r} is not connected")
    return warnings


def run(c: httpx.Client, args: argparse.Namespace) -> int:
    pw = args.password_file.expanduser().read_text().strip()
    c.post("/api/auth/login", json={"username": args.user, "password": pw,
                                    "remember": False}).raise_for_status()
    plan: dict = {}

    if args.trim:
        keep = KEEP | {t.strip() for t in args.keep.split(",") if t.strip()}
        tools, disabled = trimmed_tools(c, keep)
        print(f"--trim: {len(tools)} built-in tools; keeping {sorted(set(tools) & keep)}; "
              f"turning off {len(disabled)}")
        plan["disabled_tools"] = disabled
    elif args.untrim:
        print("--untrim: turning every tool back on")
        plan["disabled_tools"] = []
    else:
        print("tools: left as they are (use --trim or --untrim to change them)")
    if "disabled_tools" in plan and not args.dry_run:
        c.post("/api/tools", json={"disabled": plan["disabled_tools"]}).raise_for_status()

    mode = MODEL_MODES[args.model_mode]
    if mode is not None:
        dc = c.get("/api/default-chat").json()
        endpoint, model = dc.get("endpoint_id"), dc.get("model")
        if endpoint and model:
            label = "full (Regular tools)" if mode else "Auto"
            print(f"model tool mode: {model} -> {label}")
            plan["model_tool_mode"] = {model: mode}
            if not args.dry_run:
                c.patch(f"/api/model-endpoints/{endpoint}/models",
                        json={"model_tool_modes": {model: mode}}).raise_for_status()
        else:
            print("no default chat model; skipping tool mode", file=sys.stderr)

    warnings = report_modules(c, args.module)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    if args.dry_run:
        print(json.dumps({"would_apply": plan}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with httpx.Client(base_url=args.url, timeout=60) as c:
        try:
            return run(c, args)
        except httpx.ConnectError as exc:
            print(f"cannot reach {args.url}: {exc}. Is Barvis up? "
                  f"Check: curl {args.url}/api/health", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
