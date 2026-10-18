# Barvis

Barvis is a fork of [Odysseus](https://github.com/odysseus-dev/odysseus) used as the
chat frontend for [convo-ai](https://github.com/SDarioosh/barvis-ai), a private
system that records conversations from wearables, transcribes and summarizes
them on a local server, and exposes them through an MCP server. Everything runs
locally; no audio, transcripts or summaries leave the owner's network.

Upstream's README still describes the app itself. This file only covers what the
fork changes and how it stays in sync.

## Changes from upstream

The patch set is kept small so upstream merges stay routine: new files where
possible, small hooks in core files, no renames or reformatting.

| Change | Files | Upstreamable |
|---|---|---|
| **Trusted read-only MCP servers.** Servers named in `ODYSSEUS_TRUSTED_READONLY_MCP` get their `readOnlyHint: true` tools granted on every turn, without per-call approval and without the "outside server request authority" block on follow-up calls. Write tools and every other server keep the normal approval flow. | `src/mcp_trust.py` (new), hooks in `src/agent_runtime/authority.py` and `src/tool_capabilities.py` | yes |
| **Web-intent routing.** Questions with words like "latest" or "recommend" no longer get an instant "Web access is disabled" reply when trusted read-only MCP tools are available. | hook in `src/agent_loop.py` (`_web_search_unavailable_for_turn`) | yes |
| **Tool set.** Turns off the built-ins Barvis doesn't use and sets the model to "Regular tools". Configuration through the admin API, not code. | `scripts/barvis_setup.py` (new) | no |

Tests: `tests/test_mcp_trust.py`.

## Setup

1. Run Odysseus as usual (see upstream README).
2. Add the convo MCP server in Settings > MCP (or `POST /api/mcp/servers`), named
   `convo`. Stdio: command `convo-server`, args `["mcp"]`, with the convo server's
   `CONVO_*` environment. HTTP: `http://<host>:8001/mcp?access_token=<CONVO_MCP_TOKEN>`.
3. Set `ODYSSEUS_TRUSTED_READONLY_MCP=convo` in the environment (`.env` or
   docker-compose) and restart.
4. Run `python scripts/barvis_setup.py --url http://127.0.0.1:7000 --password-file <file>`.
   Re-run it after each upstream merge: new upstream tools are off unless added
   to its keep-list.
5. Put the convo MCP server's instructions in a Custom preset system prompt;
   Odysseus doesn't forward MCP server instructions to the model.

## Branches and upstream sync

- `dev` mirrors upstream `dev` and is never committed to directly.
- `barvis` is upstream plus the patches above, and is the default branch.
  Feature branches open PRs into `barvis`.
- Weekly: `git fetch upstream && git checkout barvis && git merge upstream/dev`,
  run `tests/test_mcp_trust.py` plus the upstream tests touching the hooked files
  (`tests/test_request_authority.py`, `tests/test_tool_policy.py`), and the convo
  side-by-side check (`server/eval/bakeoff.py --frontend odysseus` in convo-ai).
- Expect occasional conflicts in `src/agent_loop.py` and the agent runtime, which
  upstream is refactoring.

## License

AGPL-3.0, same as upstream. If others use a deployed Barvis over a network they
must be offered its source; this public repository does that. The convo server
is a separate program reached over MCP and is not covered by this license.
