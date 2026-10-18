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
| **Tool set.** Turns off the built-ins Barvis doesn't use (tasks, calendar and notes too, until action items sync into them), hides the built-in email/browser/image MCP tools, and sets the model to "Regular tools". Configuration through the admin API, not code. | `scripts/barvis_setup.py` (new) | no |

Tests: `tests/test_mcp_trust.py`, `tests/test_mcp_http_annotations.py`.

## Setup

1. Run Odysseus as usual (see upstream README).
2. Register each module: add its MCP server, add its name to
   `ODYSSEUS_TRUSTED_READONLY_MCP`, run `scripts/barvis_setup.py`, and put its
   instructions in a Custom preset. Steps:
   [docs/barvis/modules.md](docs/barvis/modules.md#registering-a-module-in-barvis);
   barvis-sound specifics:
   [docs/barvis/modules/barvis-sound.md](docs/barvis/modules/barvis-sound.md#connect-it).
3. Re-run `scripts/barvis_setup.py` after each upstream merge: new upstream tools are
   on until it disables them.

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
