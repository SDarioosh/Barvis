# Barvis roadmap and progress

Barvis-specific status. Upstream's own plans are in [../../ROADMAP.md](../../ROADMAP.md);
don't edit that file.

_Last updated: 2026-10-18_

## Status

| # | Item | Status | Where |
|---|---|---|---|
| 1 | Trusted read-only MCP servers, trusted reads not external effects, web-intent routing fix, tool trimming | merged (SDarioosh/Barvis#1) | [../../BARVIS.md](../../BARVIS.md) |
| 2 | Agent and module docs | merged (SDarioosh/Barvis#2) | `AGENTS.md`, `docs/barvis/` |
| 3 | barvis-sound eval ≥ 15/20 | 9/20 (right fact 18/20, link cited 9/20) | [modules/barvis-sound.md](modules/barvis-sound.md#eval-and-current-score) |
| 4 | Sandbox stack (whole system on CPU in the cloud sandbox) | working; baseline raw 1/3, Barvis 1/3 | [sandbox.md](sandbox.md) |
| 5 | HTTP MCP servers keep tool annotations (no approvals for trusted reads over HTTP) | merged (SDarioosh/Barvis#5); sandbox 2/3 → 2/3, approvals 2 → 0 | [sandbox.md](sandbox.md#http-annotation-fix-finding-1-2026-10-18) |
| 6 | Production: Barvis replaces the owner's Odysseus on port 7000 with all Odysseus features; deploy/rollback script; `barvis_setup.py` no longer trims tools by default | merged (SDarioosh/Barvis#8); production switched 2026-10-18 | [deploy.md](deploy.md) |
| 7 | `/sound` and `/context`: slash-invoked skills offer module tools for one message (MCP entries in `requires_toolsets`, skill tools pinned on the invoked turn, managed skills from `barvis_setup.py`) | PR SDarioosh/Barvis#9; sandbox results in [sandbox.md](sandbox.md#slash-skills-2026-10-18) | [deploy.md](deploy.md#features-and-modules) |

## Next

0. **Module tools without a slash command.** `/sound` and `/context` work (item 7),
   but a plain recording question in "Auto" with every tool on still goes to built-ins
   ([sandbox.md](sandbox.md#slash-skills-2026-10-18), setup C). Options: let the route
   classifier recognize module topics from the module's tool descriptions, or offer the
   module skills' tools whenever the skill index matches. Measure against C and D.
   Also "Regular tools" with every tool on still overflows 32k; slash skills don't help
   there (every enabled tool is sent).
1. **Fix the remaining MCP bug the sandbox found** ([sandbox.md](sandbox.md#findings-from-the-first-sandbox-run)):
   stdio servers added over the API fail on every call. (The HTTP annotation bug is
   item 5 above; its upstream issue is drafted in that PR, for the owner to file.)
2. **Citations to ≥ 15/20.** Most remaining failures have the right fact and no
   transcript link. Replies that were just "Done." (the model returned empty text after
   its tool calls, and the chat route saves "Done." for an empty answer) now get one
   tool-free answer round (BARVIS.md, "Empty replies after module tools"). Still open:
   why links get dropped; fix in Barvis, or in the module's result text if that's the
   cause. Rerun
   `server/eval/bakeoff.py --frontend odysseus` after each change.
3. **Action items → tasks and calendar.** barvis-sound action items flow into Odysseus
   tasks and CalDAV. Barvis reads them through the module's MCP tools
   (`list_action_items`, `update_action_item`); it never writes the module's database.
   Until then, with every Odysseus tool on, the empty tasks/calendar can attract
   questions meant for the conversation store (see the all-tools eval in
   [sandbox.md](sandbox.md#all-odysseus-tools-on-2026-10-18)).
4. **ntfy notifications.** Notify the phone (for example new action items or the daily
   digest) through the existing ntfy service. Local only.
5. **Conversations view.** A Barvis view that lists conversations and opens transcripts,
   built on the module's MCP tools or documented HTTP API. New files under `static/`
   and `routes/`, upstream visual style (`CONTRIBUTING.md`).
6. **Phone access.** Use Barvis from the Android phone over Tailscale (Odysseus has a
   PWA). Lock services to Tailscale per the owner's security decision.
7. **Shared search.** One `context` search tool that queries every module with the
   common arguments in [modules.md](modules.md#shared-search-planned) and merges the
   dated, linked hits. Contract first (modules adopt the argument names), then a
   Barvis-side aggregator.
8. **Upstream:** offer the general fixes from PR #1 and the skill MCP toolsets from
   SDarioosh/Barvis#9 upstream
   ([upstream-sync.md](upstream-sync.md#sending-fixes-upstream)); weekly syncs.

## Future modules (placeholders)

No repos exist yet. Each would follow [modules.md](modules.md).

- `barvis-<name>`: _to be decided by the owner._

## Open questions for the owner

- Which future modules are planned, and in what order?
- For the tasks/calendar sync: does Barvis copy action items into Odysseus tasks
  (two sources), or show them live from the module?
