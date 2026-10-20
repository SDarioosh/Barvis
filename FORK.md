# Our Odysseus fork

This repo is our fork of [Odysseus](https://github.com/odysseus-dev/odysseus), the hub of
a personal, fully local AI system: the one chat and agent interface. It keeps the
Odysseus name; separate **modules** add capabilities and plug into it over MCP. The first is
[odysseus-sound](https://github.com/SDarioosh/barvis-sound) (formerly convo-ai and barvis-sound), which
records conversations from wearables, transcribes and summarizes them on a local
server, and exposes them through an MCP server. Everything runs locally; no audio,
transcripts or summaries leave the owner's network.

The fork was called Barvis until 2026-10-19. The repo URL (`SDarioosh/Barvis`), the
`barvis` branch, and a few stored identifiers (the action-item task's action id, its
state files) keep that name; old `BARVIS_*` settings are still read
([deploy.md](docs/fork/deploy.md#moving-from-the-barvis-name-once)).

Upstream's README still describes the app itself. This file only covers what the
fork changes. More:

- [AGENTS.md](AGENTS.md): instructions for coding agents (fork rules, tests, git rules).
- [docs/fork/architecture.md](docs/fork/architecture.md): hub, modules, devices,
  models, ports.
- [docs/fork/modules.md](docs/fork/modules.md): the module contract and how to
  register a module.
- [docs/fork/modules/odysseus-sound.md](docs/fork/modules/odysseus-sound.md): the first
  module.
- [docs/fork/upstream-sync.md](docs/fork/upstream-sync.md): weekly upstream merge.
- [docs/fork/sandbox.md](docs/fork/sandbox.md): running the whole system in a cloud
  sandbox, and the Server verification steps for the owner.
- [docs/fork/deploy.md](docs/fork/deploy.md): production on the server: deploy,
  update, rollback.
- [docs/fork/roadmap.md](docs/fork/roadmap.md): status and next work.

## Changes from upstream

The patch set is kept small so upstream merges stay routine: new files where
possible, small hooks in core files, no renames or reformatting.

| Change | Files | Upstreamable |
|---|---|---|
| **Trusted read-only MCP servers.** Servers named in `ODYSSEUS_TRUSTED_READONLY_MCP` get their `readOnlyHint: true` tools granted on every turn, without per-call approval and without the "outside server request authority" block on follow-up calls. Write tools and every other server keep the normal approval flow. | `src/mcp_trust.py` (new), hooks in `src/agent_runtime/authority.py` and `src/tool_capabilities.py` | yes |
| **Trusted reads aren't external effects.** The evidence ledger counted every MCP call as an unverified external change, which appended "External operation … not independently verified" to answers and sometimes replaced the answer with it. Trusted read-only calls are now admitted reads. | hook in `src/agent_runtime/effect_adapters.py` (`classify`) | yes |
| **Web-intent routing.** Questions with words like "latest" or "recommend" no longer get an instant "Web access is disabled" reply when trusted read-only MCP tools are available. | hook in `src/agent_loop.py` (`_web_search_unavailable_for_turn`) | yes |
| **Env reference entry.** Upstream's `tests/test_env_reference.py` requires every `ODYSSEUS_*` variable the code reads to have a note, so `ODYSSEUS_TRUSTED_READONLY_MCP` has one and `website/configuration-reference.md` is regenerated with it. | hook in `scripts/generate_env_reference.py` (`VARIABLE_NOTES`), generated `website/configuration-reference.md` | with the trusted read-only change |
| **HTTP MCP servers keep tool annotations.** `_connect_http` dropped each tool's `annotations` (stdio and SSE kept them), so `readOnlyHint` was lost for Streamable HTTP servers: trusted read-only tools still needed approval, and plan mode fell back to the name heuristic. Now stored the same way as stdio/SSE. General bug, not fork-specific. | one line in `src/mcp_manager.py` (`_connect_http`); test `tests/test_mcp_http_annotations.py` (new) | yes (issue to file) |
| **Stdio MCP servers keep working after the request that added them.** A stdio server added, enabled or reconnected over the API was connected inside the HTTP request; `BaseHTTPMiddleware` cancels its task group once the response is sent, which also cancelled the stdio transport, so every later call failed with an empty error (`MCP tool call failed: <name>:`, sandbox finding 2). Every stdio connection now opens in its own long-lived owner task (fresh context) and is held there until `disconnect_server`, the way built-in servers already were; disconnects close it from that task. General bug, not fork-specific. | `src/mcp_stdio_owner.py` (new), hooks in `src/mcp_manager.py` (`connect_server`, `hold_owned_connection`); test `tests/test_mcp_stdio_owner.py` (new) | yes (issue to file) |
| **MCP tools in skill `requires_toolsets`.** A skill can require `mcp:<server name or id>` (every enabled tool of a connected server) or a qualified `mcp__<server>__<tool>`. They expand to the real tool names wherever `requires_toolsets` is checked or applied: skill matching, the skill index's visibility gate, active skills, and skills the model loads with `manage_skills view`. Only connected servers and enabled tools count; write tools keep their approval. | `src/skill_mcp_toolsets.py` (new), 4 hooks in `src/agent_loop.py` | yes (issue draft in SDarioosh/Barvis#9) |
| **Slash-invoked skills bring their tools.** In "Auto", the route's turn contract chose tools from the message words and replaced what the agent loop added for a skill, so `/<skill>` got its `requires_toolsets` only when the SKILL.md text named the tool, and the SKILL.md text in the composed message could itself be misread (a "search skills" request, refused). The invoke route's message is now recognized server-side; the skill is looked up in the sender's own invokable skills and the turn is offered exactly its declared tools plus `ask_user`/`update_plan`. Current message only, no state. Browsers submit textarea line breaks as CRLF, so the message is matched after normalizing `\r\n`. | `src/skill_invocation.py` (new), hook in `routes/chat_routes.py` (after `resolve_turn_contract`) | yes |
| **Slash-invoked skills run in agent mode.** The web UI sends `mode=chat` unless the agent toggle is on, and chat mode never builds a turn contract, so a slash-invoked skill's pinned tools never applied: `/sound` reached the model with no tools at all (the lexical tool-intent classifier doesn't see a tool request in the composed skill message). A slash-invoked skill that declares `requires_toolsets` now switches that turn to agent mode, like the other chat→agent escalations. | `invoked_skill_needs_tools` in `src/skill_invocation.py`, hook in `routes/chat_routes.py` (chat→agent escalation) | yes |
| **Follow-ups keep a used module's tools.** On a message without a slash command, upstream offers back only the exact tools that already ran in the chat. After `/sound` searched a conversation, "Speaker 2 was Jenn" got the search tools but not `name_speaker`, so the model said it had no such tool. Now a tool from a server in `ODYSSEUS_TRUSTED_READONLY_MCP` that ran earlier in the chat brings back that server's other enabled tools. Offered only: write tools still ask for approval; policy and per-tool disables still apply. | `with_trusted_server_tools` in `src/mcp_trust.py`, hook in `routes/chat_routes.py` (after `_successful_session_tool_names`) | yes |
| **An approved module tool covers the rest of the task.** The chat approval card lifts the approval gate for the rest of the resumed task, but request authority admitted only the one exact approved MCP call, so after approving one `name_speaker` the next ones ("Speaker 3", "Speaker 2") were blocked as "outside server request authority". Now, when the approved tool is on a server in `ODYSSEUS_TRUSTED_READONLY_MCP`, the resumed task gets a grant for that same tool only, plus its MCP backend in the authority's sealed backend list (without it the calls failed with "External backend is outside sealed request scope"). Policy and per-tool disables still apply; other tools get nothing. | `with_approved_tool_grant` in `src/mcp_trust.py`, hook in `routes/chat_routes.py` (after the exact-approval authority) | yes, with discussion |
| **Module action items and digest on the phone.** A built-in task "Module Action Items" (every 15 min, ships paused) reads odysseus-sound's open action items live through its MCP tools and pushes new ones, items due today, and yesterday's digest to the phone through the ntfy reminder channel, verbatim (no LLM rewrite). Keeps no copy of the items (the owner chose a live view): only which ids and days were already pushed. Never touches the module's database. | `src/module_action_items.py` (new), hooks in `src/builtin_actions.py` (action registry) and `src/task_scheduler.py` (housekeeping default, silent action) | no |
| **Several tools in a row.** Upstream grants a turn only the tools its message words name ("what are my tasks for today?" → `manage_tasks`), but the model is often shown more: tools used earlier in the chat, and every enabled tool on the run that resumes after the approval card or with a "Regular tools" model. So after the tasks lookup, the model's calendar call was refused ("disabled by user or server request authority policy" / "outside server request authority"). Now every personal store the turn offers (calendar, tasks, notes, memory, documents, skills) gets its read-only actions (`list_events`, `list`, `search`, `view` …, from upstream's `_PRIVATE_ACTION_READS`). Writes still need the words or an approval; user, owner, global, incognito and plan-mode disables still win; admin and session tools are left out. | `src/offered_reads.py` (new), hook in `routes/chat_routes.py` (after the final turn policy) | yes, with discussion |
| **Web lookups with the web toggle on.** Two gaps made web questions fail. "search mistral 7b vs qwen3.5-9b" (even "search the web for …") was routed to the Hugging Face model search only; a clause that compares models (vs, versus, compare, benchmark) or names the web (web, online, internet, google) now goes to normal web routing. A message whose words name no tool ("mistral 7b vs qwen3.5-9b") got no grants, so the model's `web_search` call was blocked as "outside server request authority"; with the web toggle on, such a turn now gets a `web_search` grant (not fetch or browser). Toggle off, policy and per-tool disables still win. | `src/web_turn_scope.py` (new), hooks in `src/turn_contract.py` (model-discovery rule) and `routes/chat_routes.py` (after `request_authority_for_http`); test `tests/test_web_turn_scope.py` (new) | yes, with discussion |
| **Empty replies after module tools get an answer.** After `/sound` searched and opened a conversation, the model sometimes returned no text, and the chat showed only the placeholder "Done.". Upstream already runs one tool-free "your last response was empty, answer now from the evidence above" round, but only for unattended runs. A web-chat turn whose reply is empty after a successful tool on a server in `ODYSSEUS_TRUSTED_READONLY_MCP` now gets that same round. Not when the turn stopped on an approval or question card. | `ran_trusted_server_tool` in `src/mcp_trust.py`, hook in `src/agent_loop.py` (end-of-loop empty-final recovery); test `tests/test_mcp_empty_answer.py` (new) | yes |
| **odysseus-sound page: conversations and "who is this?".** `/modules/sound` (sidebar: Conversations) lists open action items live (Done / Dismiss call the module's `update_action_item`; no copies in Odysseus), lists recorded conversations (filter by words, person, dates) with summaries and a link to each signed transcript, and shows a card per unnamed speaker: a few of their lines (each opens the transcript at that moment), the closest known voice ("It's Ben"), or a typed name, which calls the module's `name_speaker`. **Open** shows a conversation in place (summary, key points, action items, facts, and the transcript 200 lines at a time; `#c=<id>` deep links); tapping a line's speaker fixes that line (`reassign_line`), every line of an unnamed `Speaker N` (`name_speaker`), or renames a person in all conversations (`rename_speaker_everywhere`, after a confirm: it merges and can't be undone). **Ask in chat** opens a new chat prefilled with `/sound About the conversation "…" (id …, …): `. Data only through the module's MCP tools (`search_conversations`, `get_conversation`, `get_transcript`, `list_unnamed_speakers`, `name_speaker`, `reassign_line`, `rename_speaker_everywhere`, `list_action_items`, `update_action_item`); admin only. | `routes/sound_module_routes.py`, `src/sound_module_view.py`, `static/sound-module.html`, `static/js/soundModule.js` (new); hooks in `app.py` (router) and `static/index.html` (sidebar link) | no |
| **Prefilled new chat from a link.** `/?ask=<text>` opens a new chat with the text in the composer (one line, at most 2000 characters), focused, and removes `ask` from the URL. It never sends: a link can't start a turn or a tool call. Used by the odysseus-sound page's Ask in chat. | `static/js/forkChatAsk.js` (new), hook in `static/index.html` (one script tag) | yes, with discussion |
| **Managed skills.** Skills with `source: managed` (created by `modules_setup.py`) are slash-invokable without the LLM skill audit that "Auto-approve skills" requires, and an audit verdict doesn't hide them. Only the owner's own skills are affected. | hook in `services/memory/skills.py` (`index_for`), check in `src/skill_invocation.py` | with discussion |
| **Setup.** Keeps every Odysseus feature: leaves the tool toggles alone, sets the default model to "Auto" tool mode, checks that module MCP servers are connected, and creates or updates the `/sound` skill (requires `mcp:convo`, procedure = the module's instructions from the Custom preset) and `/context` (requires every server in `ODYSSEUS_TRUSTED_READONLY_MCP`). `--trim` turns off the built-ins a memory-only setup doesn't use, hides the built-in email/browser/image MCP tools and sets "Regular tools"; `--untrim` turns them back on. Configuration through the admin API, not code. Untagged questions in "Auto" still don't reach module tools; use `/sound` or `/context` ([roadmap](docs/fork/roadmap.md)). | `scripts/modules_setup.py` (new) | no |
| **Slash eval.** Runs a module's bakeoff (odysseus-sound's `eval/bakeoff.py`, unmodified) with each question sent as `/<skill> <question>`, and reports approvals and `tools_sent` from the Odysseus log. Development tool. | `scripts/module_slash_eval.py` (new) | no |
| **Deployment.** Production runs the locally built `odysseus-fork:<commit>` image with Docker Compose; one command (from any directory) first runs each module's own deploy command (`FORK_MODULES`), then builds, backs up data, switches, health-checks and rolls back. | `scripts/fork_deploy.sh`, `docker/fork.override.yml` (new); [docs/fork/deploy.md](docs/fork/deploy.md) | no |

Tests: `tests/test_mcp_trust.py`, `tests/test_mcp_empty_answer.py`, `tests/test_mcp_http_annotations.py`,
`tests/test_mcp_stdio_owner.py`,
`tests/test_modules_setup.py`, `tests/test_skill_mcp_toolsets.py`, `tests/test_offered_reads.py`,
`tests/test_module_action_items.py`, `tests/test_sound_module_view.py`.

## Setup

1. Run it with Docker Compose using the fork's image and override:
   [docs/fork/deploy.md](docs/fork/deploy.md). Every Odysseus feature stays
   available; modules add to it.
2. Register each module: add its MCP server, add its name to
   `ODYSSEUS_TRUSTED_READONLY_MCP`, put its instructions in a Custom preset, and run
   `scripts/modules_setup.py` (it also creates the module's slash skill, e.g. `/sound`,
   and `/context`). Steps:
   [docs/fork/modules.md](docs/fork/modules.md#registering-a-module-in-odysseus);
   odysseus-sound specifics:
   [docs/fork/modules/odysseus-sound.md](docs/fork/modules/odysseus-sound.md#connect-it).
3. Update with `scripts/fork_deploy.sh deploy`; it updates and restarts the modules
   listed in `FORK_MODULES` first and re-runs `scripts/modules_setup.py`.

## Branches and upstream sync

`dev` mirrors upstream `dev` and is never committed to; `barvis` is upstream plus the
patches above and is the default branch; PRs go into `barvis`. The weekly merge,
conflict hot spots and post-merge checks are in
[docs/fork/upstream-sync.md](docs/fork/upstream-sync.md).

## License

AGPL-3.0, same as upstream. If others use a deployed instance of this fork over a network they
must be offered its source; this public repository does that. Modules such as
odysseus-sound are separate programs reached over MCP and are not covered by this
license.
