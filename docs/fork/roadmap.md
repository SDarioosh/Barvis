# Fork roadmap and progress

fork-specific status. Upstream's own plans are in [../../ROADMAP.md](../../ROADMAP.md);
don't edit that file.

_Last updated: 2026-10-20_

## Status

| # | Item | Status | Where |
|---|---|---|---|
| 1 | Trusted read-only MCP servers, trusted reads not external effects, web-intent routing fix, tool trimming | merged (SDarioosh/Barvis#1) | [../../FORK.md](../../FORK.md) |
| 2 | Agent and module docs | merged (SDarioosh/Barvis#2) | `AGENTS.md`, `docs/fork/` |
| 3 | odysseus-sound eval ≥ 15/20 | 9/20 (right fact 18/20, link cited 9/20) | [modules/odysseus-sound.md](modules/odysseus-sound.md#eval-and-current-score) |
| 4 | Sandbox stack (whole system on CPU in the cloud sandbox) | working; baseline raw 1/3, Odysseus 1/3 | [sandbox.md](sandbox.md) |
| 5 | HTTP MCP servers keep tool annotations (no approvals for trusted reads over HTTP) | merged (SDarioosh/Barvis#5); sandbox 2/3 → 2/3, approvals 2 → 0 | [sandbox.md](sandbox.md#http-annotation-fix-finding-1-2026-10-18) |
| 6 | Production: the fork replaces the owner's upstream Odysseus on port 7000 with all Odysseus features; deploy/rollback script; `modules_setup.py` no longer trims tools by default | merged (SDarioosh/Barvis#8); production switched 2026-10-18 | [deploy.md](deploy.md) |
| 7 | `/sound` and `/context`: slash-invoked skills offer module tools for one message (MCP entries in `requires_toolsets`, skill tools pinned on the invoked turn, managed skills from `modules_setup.py`) | merged (SDarioosh/Barvis#9); sandbox, every tool on + Auto: plain 0/4, `/sound` 4/4, `/context` 1/4 ([sandbox.md](sandbox.md#slash-skills-2026-10-18)) | [deploy.md](deploy.md#features-and-modules) |
| 8 | Several tools in a row: a turn may read every personal store it offers (calendar, tasks, notes, memory, documents, skills), so a calendar lookup after a tasks lookup is no longer refused; writes unchanged | merged (SDarioosh/Barvis#16) | [../../FORK.md](../../FORK.md) |
| 9 | Action items and digest on the phone (roadmap 3–4): built-in task "Module Action Items" pushes new items, items due today and the daily digest via ntfy; items stay live in odysseus-sound (owner's decision, 2026-10-19) | merged (SDarioosh/Barvis#17), ships paused | [../../FORK.md](../../FORK.md), `src/module_action_items.py` |
| 10 | odysseus-sound page `/modules/sound`: live action items (done/dismiss), conversations list and "who is this?" cards for unnamed speakers (roadmap 3, 5) | merged (SDarioosh/Barvis#19); odysseus-sound's `list_unnamed_speakers` merged (SDarioosh/barvis-sound#24) | [../../FORK.md](../../FORK.md) |
| 11 | Stdio MCP servers added over the API keep working after that request (sandbox finding 2): each stdio connection has its own owner task | PR SDarioosh/Barvis#20 | [../../FORK.md](../../FORK.md), `src/mcp_stdio_owner.py` |
| 12 | One-command deploy: `fork_deploy.sh deploy` runs each module's deploy command first (odysseus-sound: update, install, background services that survive reboot), works from any directory | PR SDarioosh/Barvis#21; odysseus-sound side SDarioosh/barvis-sound#30 | [deploy.md](deploy.md#update-production), [modules.md](modules.md#deploying-a-module) |
| 13 | Conversations page v2: transcript reader, click-to-fix speakers (`reassign_line`, `name_speaker`, `rename_speaker_everywhere`), Ask in chat (`/?ask=` prefills a new chat with `/sound …`) | PR (this branch) | [../../FORK.md](../../FORK.md) |
| 14 | Voice notes into calendar and to-dos: module items get a kind (event / task / reminder) and a time; voice-note items are filed automatically into the Odysseus calendar and to-dos (one push, undo/move on `/modules/sound`), conversation items are suggestions; status syncs both ways (owner's decision 2026-10-20, replaces "no copies") | PR (this branch); module side PR in SDarioosh/barvis-sound | [modules/odysseus-sound.md](modules/odysseus-sound.md#action-items-in-odysseus) |
| 15 | Named to-do lists for voice-note items: "add milk to my shopping list" adds a line to that Odysseus to-do list (made if missing) instead of a to-do of its own; status syncs per line; Add to a list / Change list on `/modules/sound` | PR (this branch); module side PR in SDarioosh/barvis-sound | [modules/odysseus-sound.md](modules/odysseus-sound.md#action-items-in-odysseus) |

## Next

0. **Module tools without a slash command** (PR SDarioosh/Barvis#24). A plain question
   with a module cue (conversation words, a person the module knows) runs in agent mode
   and is offered the trusted modules' read tools, through the existing slash-skill
   hooks (no upstream lines). Sandbox setup C: 0/4 → 5/5 agent, 4/5 chat
   ([sandbox.md](sandbox.md#plain-module-questions-2026-10-20)). Owner's full run pending.
   "Regular tools" with every tool on still overflows 32k.
1. **Fixed (status item 11): the remaining MCP bug the sandbox found**
   ([sandbox.md](sandbox.md#findings-from-the-first-sandbox-run), finding 2): stdio servers
   added over the API failed on every call. Upstream issues for this and the HTTP
   annotation bug (item 5) are drafted in their PRs, for the owner to file.
2. **Citations to ≥ 15/20.** In the 2026-10-20 sandbox runs every passing answer cited
   its link; links are kept by the module (cite rule at the end of list results, "search
   before saying I don't know" in `INSTRUCTIONS`, SDarioosh/barvis-sound#40). A fork-side
   "Sources:" footer was tried and dropped (it failed a "nothing found" answer). Next:
   the owner's full 20-question run on the server.
3. **Action items, filed** (status items 9, 14). Decided 2026-10-19: no copies, a live
   view only. Changed 2026-10-20: voice-note events and to-dos are filed into the
   Odysseus calendar and to-dos (the module stays the source of truth, status syncs both
   ways); conversation items are suggestions on the odysseus-sound page. Next: repeating
   events, merging with the same event from an email.
4. **ntfy notifications** (status item 9). New action items and yesterday's digest go
   to the phone through the ntfy reminder channel.
5. **Conversations view** (status item 10). `/modules/sound` lists conversations, live
   action items (done/dismiss) and "who is this?" cards for unnamed speakers; item 13
   adds the transcript reader, speaker fixes and Ask in chat. Next: the daily digest on
   the page, and follow the user's theme (the page uses the default dark tokens only).
6. **Phone access.** Use Odysseus from the Android phone over Tailscale (Odysseus has a
   PWA). Lock services to Tailscale per the owner's security decision.
7. **Shared search.** One `context` search tool that queries every module with the
   common arguments in [modules.md](modules.md#shared-search-planned) and merges the
   dated, linked hits. Contract first (modules adopt the argument names), then a
   fork-side aggregator.
8. **Upstream:** offer the general fixes from PR #1 and the skill MCP toolsets from
   SDarioosh/Barvis#9 upstream
   ([upstream-sync.md](upstream-sync.md#sending-fixes-upstream)); weekly syncs.

## Future modules (placeholders)

No repos exist yet. Each would follow [modules.md](modules.md).

- `odysseus-<name>`: _to be decided by the owner._

## Open questions for the owner

- Which future modules are planned, and in what order?
