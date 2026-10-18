#!/usr/bin/env python3
"""Barvis: configure an Odysseus instance as the convo-ai memory frontend.

Applies the configuration half of the fork plan through the admin API, so it
works on any instance and leaves upstream code alone:

* Turns off every built-in tool except a small keep-list. With ~100 tool
  schemas in each request (~106 KB), a local model is slow and often picks a
  built-in (contacts, memory, email) over the convo tools.
* Optionally sets every model on the default endpoint to "Regular tools"
  (``full``) mode; "Auto" routes non-Odysseus models through a compact path
  that never offers third-party MCP tools.

Trusting the convo MCP server's read-only tools is not done here; that is the
``ODYSSEUS_TRUSTED_READONLY_MCP`` environment variable (see src/mcp_trust.py).

Usage:
    python scripts/barvis_setup.py --url http://127.0.0.1:7000 \
        --user admin --password-file ~/.config/barvis/admin.txt [--dry-run]

Re-run after upgrading: new upstream tools are disabled unless added to KEEP.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

# Built-ins Barvis uses. Everything else is disabled; re-enable in Settings > Tools.
# search_chats / list_sessions are off on purpose: for "what's the latest on X"
# the model picks chat-history search over the conversation store.
KEEP = {
    "ask_user",
    "update_plan",
    "manage_tasks",     # action items -> tasks
    "manage_calendar",  # action items with dates -> calendar (CalDAV)
    "manage_notes",
}
# Built-in MCP servers whose tools are hidden from the model.
BUILTIN_MCP_OFF = {"email", "builtin_browser", "image_gen"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:7000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password-file", required=True, type=Path)
    ap.add_argument("--keep", default="", help="extra comma-separated tools to keep")
    ap.add_argument("--no-model-modes", action="store_true",
                    help="don't switch the default endpoint's models to Regular tools")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keep = KEEP | {t.strip() for t in args.keep.split(",") if t.strip()}
    c = httpx.Client(base_url=args.url, timeout=60)
    pw = args.password_file.expanduser().read_text().strip()
    c.post("/api/auth/login", json={"username": args.user, "password": pw,
                                    "remember": False}).raise_for_status()

    tools = [t["id"] for t in c.get("/api/tools").json()["tools"]]
    disabled = sorted(t for t in tools if t not in keep)
    # The built-in email server is offered under qualified mcp__email__* names,
    # which the bare names above don't cover.
    mcp = c.get("/api/mcp/tools").json()
    mcp = mcp.get("tools", mcp) if isinstance(mcp, dict) else mcp
    disabled += sorted(t["qualified_name"] for t in mcp
                       if t.get("server_id") in BUILTIN_MCP_OFF)
    print(f"{len(tools)} built-in tools; keeping {sorted(set(tools) & keep)}")
    print(f"disabling {len(disabled)}")
    if not args.dry_run:
        c.post("/api/tools", json={"disabled": disabled}).raise_for_status()

    if not args.no_model_modes:
        dc = c.get("/api/default-chat").json()
        endpoint, model = dc.get("endpoint_id"), dc.get("model")
        if endpoint and model:
            print(f"model tool mode: {model} -> full (Regular tools)")
            if not args.dry_run:
                c.patch(f"/api/model-endpoints/{endpoint}/models",
                        json={"model_tool_modes": {model: "full"}}).raise_for_status()
        else:
            print("no default chat model; skipping tool mode", file=sys.stderr)

    if args.dry_run:
        print(json.dumps({"would_disable": disabled}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
