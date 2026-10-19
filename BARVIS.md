# Barvis

Barvis is a fork of [Odysseus](https://github.com/odysseus-dev/odysseus) and the hub of
a personal, fully local AI system: the one chat and agent interface. Separate
**modules** plug into it over MCP. The first is
[barvis-sound](https://github.com/SDarioosh/barvis-sound) (formerly convo-ai), which
records conversations from wearables, transcribes and summarizes them on a local
server, and exposes them through an MCP server. Everything runs locally; no audio,
transcripts or summaries leave the owner's network.

Upstream's README still describes the app itself. This file only covers what the
fork changes. More:

- [AGENTS.md](AGENTS.md): instructions for coding agents (fork rules, tests, git rules).
- [docs/barvis/architecture.md](docs/barvis/architecture.md): hub, modules, devices,
  models, ports.
- [docs/barvis/modules.md](docs/barvis/modules.md): the module contract and how to
  register a module.
- [docs/barvis/modules/barvis-sound.md](docs/barvis/modules/barvis-sound.md): the first
  module.
- [docs/barvis/upstream-sync.md](docs/barvis/upstream-sync.md): weekly upstream merge.
- [docs/barvis/sandbox.md](docs/barvis/sandbox.md): running the whole system in a cloud
  sandbox, and the Server verification steps for the owner.
- [docs/barvis/deploy.md](docs/barvis/deploy.md): production on the server: deploy,
  update, rollback.
- [docs/barvis/roadmap.md](docs/barvis/roadmap.md): status and next work.

## Changes from upstream

The patch set is kept small so upstream merges stay routine: new files where
possible, small hooks in core files, no renames or reformatting.

| Change | Files | Upstreamable |
|---|---|---|
| **Trusted read-only MCP servers.** Servers named in `ODYSSEUS_TRUSTED_READONLY_MCP` get their `readOnlyHint: true` tools granted on every turn, without per-call approval and without the "outside server request authority" block on follow-up calls. Write tools and every other server keep the normal approval flow. | `src/mcp_trust.py` (new), hooks in `src/agent_runtime/authority.py` and `src/tool_capabilities.py` | yes |
| **Trusted reads aren't external effects.** The evidence ledger counted every MCP call as an unverified external change, which appended "External operation … not independently verified" to answers and sometimes replaced the answer with it. Trusted read-only calls are now admitted reads. | hook in `src/agent_runtime/effect_adapters.py` (`classify`) | yes |
| **Web-intent routing.** Questions with words like "latest" or "recommend" no longer get an instant "Web access is disabled" reply when trusted read-only MCP tools are available. | hook in `src/agent_loop.py` (`_web_search_unavailable_for_turn`) | yes |
| **Env reference entry.** Upstream's `tests/test_env_reference.py` requires every `ODYSSEUS_*` variable the code reads to have a note, so `ODYSSEUS_TRUSTED_READONLY_MCP` has one and `website/configuration-reference.md` is regenerated with it. | hook in `scripts/generate_env_reference.py` (`VARIABLE_NOTES`), generated `website/configuration-reference.md` | with the trusted read-only change |
| **HTTP MCP servers keep tool annotations.** `_connect_http` dropped each tool's `annotations` (stdio and SSE kept them), so `readOnlyHint` was lost for Streamable HTTP servers: trusted read-only tools still needed approval, and plan mode fell back to the name heuristic. Now stored the same way as stdio/SSE. General bug, not Barvis-specific. | one line in `src/mcp_manager.py` (`_connect_http`); test `tests/test_mcp_http_annotations.py` (new) | yes (issue to file) |
| **MCP tools in skill `requires_toolsets`.** A skill can require `mcp:<server name or id>` (every enabled tool of a connected server) or a qualified `mcp__<server>__<tool>`. They expand to the real tool names wherever `requires_toolsets` is checked or applied: skill matching, the skill index's visibility gate, active skills, and skills the model loads with `manage_skills view`. Only connected servers and enabled tools count; write tools keep their approval. | `src/skill_mcp_toolsets.py` (new), 4 hooks in `src/agent_loop.py` | yes (issue draft in SDarioosh/Barvis#9) |
| **Slash-invoked skills bring their tools.** In "Auto", the route's turn contract chose tools from the message words and replaced what the agent loop added for a skill, so `/<skill>` got its `requires_toolsets` only when the SKILL.md text named the tool, and the SKILL.md text in the composed message could itself be misread (a "search skills" request, refused). The invoke route's message is now recognized server-side; the skill is looked up in the sender's own invokable skills and the turn is offered exactly its declared tools plus `ask_user`/`update_plan`. Current message only, no state. Browsers submit textarea line breaks as CRLF, so the message is matched after normalizing `\r\n`. | `src/skill_invocation.py` (new), hook in `routes/chat_routes.py` (after `resolve_turn_contract`) | yes |
| **Slash-invoked skills run in agent mode.** The web UI sends `mode=chat` unless the agent toggle is on, and chat mode never builds a turn contract, so a slash-invoked skill's pinned tools never applied: `/sound` reached the model with no tools at all (the lexical tool-intent classifier doesn't see a tool request in the composed skill message). A slash-invoked skill that declares `requires_toolsets` now switches that turn to agent mode, like the other chat→agent escalations. | `invoked_skill_needs_tools` in `src/skill_invocation.py`, hook in `routes/chat_routes.py` (chat→agent escalation) | yes |
| **Follow-ups keep a used module's tools.** On a message without a slash command, upstream offers back only the exact tools that already ran in the chat. After `/sound` searched a conversation, "Speaker 2 was Jenn" got the search tools but not `name_speaker`, so the model said it had no such tool. Now a tool from a server in `ODYSSEUS_TRUSTED_READONLY_MCP` that ran earlier in the chat brings back that server's other enabled tools. Offered only: write tools still ask for approval; policy and per-tool disables still apply. | `with_trusted_server_tools` in `src/mcp_trust.py`, hook in `routes/chat_routes.py` (after `_successful_session_tool_names`) | yes |
| **An approved module tool covers the rest of the task.** The chat approval card lifts the approval gate for the rest of the resumed task, but request authority admitted only the one exact approved MCP call, so after approving one `name_speaker` the next ones ("Speaker 3", "Speaker 2") were blocked as "outside server request authority". Now, when the approved tool is on a server in `ODYSSEUS_TRUSTED_READONLY_MCP`, the resumed task gets a grant for that same tool only, plus its MCP backend in the authority's sealed backend list (without it the calls failed with "External backend is outside sealed request scope"). Policy and per-tool disables still apply; other tools get nothing. | `with_approved_tool_grant` in `src/mcp_trust.py`, hook in `routes/chat_routes.py` (after the exact-approval authority) | yes, with discussion |
| **Module action items and digest on the phone.** A built-in task "Barvis Action Items" (every 15 min, ships paused) reads barvis-sound's open action items live through its MCP tools and pushes new ones, items due today, and yesterday's digest to the phone through the ntfy reminder channel, verbatim (no LLM rewrite). Keeps no copy of the items (the owner chose a live view): only which ids and days were already pushed. Never touches the module's database. | `src/barvis_action_items.py` (new), hooks in `src/builtin_actions.py` (action registry) and `src/task_scheduler.py` (housekeeping default, silent action) | no |
| **Managed skills.** Skills with `source: managed` (created by `barvis_setup.py`) are slash-invokable without the LLM skill audit that "Auto-approve skills" requires, and an audit verdict doesn't hide them. Only the owner's own skills are affected. | hook in `services/memory/skills.py` (`index_for`), check in `src/skill_invocation.py` | with discussion |
| **Setup.** Keeps every Odysseus feature: leaves the tool toggles alone, sets the default model to "Auto" tool mode, checks that module MCP servers are connected, and creates or updates the `/sound` skill (requires `mcp:convo`, procedure = the module's instructions from the Custom preset) and `/context` (requires every server in `ODYSSEUS_TRUSTED_READONLY_MCP`). `--trim` turns off the built-ins a memory-only setup doesn't use, hides the built-in email/browser/image MCP tools and sets "Regular tools"; `--untrim` turns them back on. Configuration through the admin API, not code. Untagged questions in "Auto" still don't reach module tools; use `/sound` or `/context` ([roadmap](docs/barvis/roadmap.md)). | `scripts/barvis_setup.py` (new) | no |
| **Deployment.** Production runs the locally built `barvis:<commit>` image with Docker Compose; one command builds, backs up data, switches, health-checks and rolls back. | `scripts/barvis_deploy.sh`, `docker/barvis.override.yml` (new); [docs/barvis/deploy.md](docs/barvis/deploy.md) | no |

Tests: `tests/test_mcp_trust.py`, `tests/test_mcp_http_annotations.py`,
`tests/test_barvis_setup.py`, `tests/test_skill_mcp_toolsets.py`,
`tests/test_barvis_action_items.py`.

## Setup

1. Run it with Docker Compose using the Barvis image and override:
   [docs/barvis/deploy.md](docs/barvis/deploy.md). Every Odysseus feature stays
   available; modules add to it.
2. Register each module: add its MCP server, add its name to
   `ODYSSEUS_TRUSTED_READONLY_MCP`, put its instructions in a Custom preset, and run
   `scripts/barvis_setup.py` (it also creates the module's slash skill, e.g. `/sound`,
   and `/context`). Steps:
   [docs/barvis/modules.md](docs/barvis/modules.md#registering-a-module-in-barvis);
   barvis-sound specifics:
   [docs/barvis/modules/barvis-sound.md](docs/barvis/modules/barvis-sound.md#connect-it).
3. Update with `scripts/barvis_deploy.sh deploy`; it re-runs `scripts/barvis_setup.py`.

## Branches and upstream sync

`dev` mirrors upstream `dev` and is never committed to; `barvis` is upstream plus the
patches above and is the default branch; PRs go into `barvis`. The weekly merge,
conflict hot spots and post-merge checks are in
[docs/barvis/upstream-sync.md](docs/barvis/upstream-sync.md).

## License

AGPL-3.0, same as upstream. If others use a deployed Barvis over a network they
must be offered its source; this public repository does that. Modules such as
barvis-sound are separate programs reached over MCP and are not covered by this
license.
