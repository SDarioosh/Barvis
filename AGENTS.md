# Barvis: agent guide

Instructions for any coding agent (Claude Code, Google Antigravity, others) working in
this repository. Read this file completely before changing anything.

## What Barvis is

- Barvis is a fork of [Odysseus](https://github.com/odysseus-dev/odysseus), a self-hosted
  AI workspace (chat, agents, MCP, documents, email, notes, tasks, calendar). License
  AGPL-3.0.
- Barvis is the **hub** of a personal, fully local AI system: the one chat and agent
  interface the owner uses from an Android phone and a Mac over Tailscale.
- **Modules** are separate programs in separate repos (`barvis-<name>`). Each owns its
  data and exposes it through its own MCP server. Barvis is only a client of them.
- First module: [barvis-sound](https://github.com/SDarioosh/barvis-sound) (recorded
  conversations: transcripts, summaries, action items, facts). Details:
  [docs/barvis/modules/barvis-sound.md](docs/barvis/modules/barvis-sound.md).
- What the fork changes today: [BARVIS.md](BARVIS.md). System picture, ports and
  services: [docs/barvis/architecture.md](docs/barvis/architecture.md). Status and
  next work: [docs/barvis/roadmap.md](docs/barvis/roadmap.md).

## Privacy rules (non-negotiable)

- Audio, transcripts, summaries, facts, embeddings and chat content stay on the owner's
  network.
- No cloud speech-to-text, cloud LLMs or cloud embedding APIs in any data path. No
  telemetry. Don't add a provider, default or fallback that sends user data off the
  network.
- Cloud is fine only for development tooling (coding agents, package and model-weight
  downloads).
- Never paste real transcripts, chat content or tokens into issues, PRs, commit messages
  or test fixtures. Use fictional data (barvis-sound's `server/scripts/seed_demo.py`).

## Where Odysseus keeps things

Odysseus is large (~478k lines of Python) and fast-moving. Start from these files and
`grep` from there; don't read whole directories.

| Area | Where |
|---|---|
| Agent loop, turn routing, web-intent handling | `src/agent_loop.py` |
| Agent runtime: request authority, effects, evidence ledger | `src/agent_runtime/` (`authority.py`, `effect_adapters.py`, `effects.py`) |
| Tool gating, approvals, policy, admin-only tools | `src/tool_capabilities.py`, `src/tool_approvals.py`, `src/tool_policy.py`, `src/tool_security.py` |
| Tool dispatch and built-in tools | `src/tool_execution.py`, `src/agent_tools/` |
| MCP client, built-in MCP servers | `src/mcp_manager.py`, `src/mcp_oauth.py`, `src/builtin_mcp.py`, `mcp_servers/` |
| Presets (system prompts) | `src/preset_manager.py`, `routes/preset_routes.py` |
| HTTP routes | `routes/` (e.g. `routes/mcp_routes.py`, `routes/task_routes.py`, `routes/calendar_routes.py`) |
| Auth, middleware, paths and constants | `core/`, `src/constants.py` |
| Frontend | `static/` (`index.html`, `js/`, `css/`); module sources in `frontend/src/`, mapped by `frontend/modules.json` |
| Tests | `tests/` (see `tests/README.md`, `tests/KNOWN_FAILURES.md`) |
| Upstream's own rules | `CONTRIBUTING.md`, `SECURITY.md`, `THREAT_MODEL.md` |

Barvis code so far: `src/mcp_trust.py`, `scripts/barvis_setup.py`,
`tests/test_mcp_trust.py`, and the hooks listed in [BARVIS.md](BARVIS.md).

## Fork rules (merge-friendly)

Upstream changes every day, so every line we touch in an upstream file is a future
merge conflict.

- Put Barvis code in **new files** (`src/<feature>.py`, `scripts/barvis_*.py`,
  `tests/test_<feature>.py`, `docs/barvis/`).
- Change upstream files only with **small hooks**: a lazy import plus a call into the new
  file, a few lines at most. Put a comment on the hook that names the Barvis file it
  calls (for example `# Barvis: ... (src/mcp_trust.py)`), so `git grep` finds every hook.
- No renames, reformatting, import sorting, or drive-by refactors of upstream code. Don't
  run formatters over upstream files.
- Branding goes through theme and config, never by renaming identifiers or strings in
  upstream code.
- Prefer configuration over code (see `scripts/barvis_setup.py`).
- Follow upstream's code conventions in `CONTRIBUTING.md` (paths from `src/constants.py`,
  `internal_api_base()` instead of hardcoded URLs, Conventional Commits, its visual style).
- A general fix (not Barvis-specific) is written so it could go upstream, and is offered
  upstream. How: [docs/barvis/upstream-sync.md](docs/barvis/upstream-sync.md).
- Record every new hook in the "Changes from upstream" table in [BARVIS.md](BARVIS.md).

## Boundaries: hub and modules

- Barvis reaches a module **only** through that module's MCP server (or its documented
  HTTP API).
- Barvis never opens, reads or writes a module's database or files directly.
- A module never imports Barvis code. The AGPL covers Barvis only; modules talk to it
  over a protocol, so the AGPL doesn't extend to them. Don't copy Odysseus code into a
  module.
- The module contract (tool naming, annotations, links, auth, eval):
  [docs/barvis/modules.md](docs/barvis/modules.md).

## Where does this change belong?

| The change is about | Put it in |
|---|---|
| Capturing, processing or storing a module's data (audio, transcripts, summaries) | the module repo |
| A module's tool names, arguments, results, links or annotations | the module repo, coordinated per [modules.md](docs/barvis/modules.md#versioning) |
| How the model is offered or gated on MCP tools, approvals, routing | Barvis (hook + new file) |
| Chat UI, views of module data, notifications, tasks/calendar in the hub | Barvis (calls the module's MCP tools or API) |
| Which tools are on, model tool mode | Barvis config: `scripts/barvis_setup.py` |
| A preset's instructions for a module | Barvis config (Custom preset), text owned by the module doc |
| A bug in Odysseus that isn't Barvis-specific | Barvis, written to be offered upstream |

If a feature needs both repos, do the module side first, then the Barvis side, and link
the two PRs to each other.

## Run the isolated test instance

There are two Odysseus instances on the owner's server:

- **Owner's real install:** Docker, `/home/steam/models/odysseus`, port 7000. **Never
  modify it**: don't edit its files, restart, rebuild or reconfigure its containers,
  call its admin API, or point `barvis_setup.py` at it. Read-only inspection only, and
  only when asked.
- **Isolated test instance:** this fork's code on `127.0.0.1:7010`. Start it with
  `~/.hermes/cache/scratch/barvis-run.sh`; the log is
  `~/.hermes/cache/scratch/bakeoff/barvis.log`. Use it for every manual and eval run.

Local clones on the server: `~/projects/barvis` (this repo) and
`~/projects/barvis-ai-wt/*` (barvis-sound worktrees; the folder may be renamed).

Configure the test instance (never the real one) after a fresh start or an upstream
merge:

```bash
python scripts/barvis_setup.py --url http://127.0.0.1:7010 --password-file <file> --dry-run
python scripts/barvis_setup.py --url http://127.0.0.1:7010 --password-file <file>
```

Module registration and `ODYSSEUS_TRUSTED_READONLY_MCP`:
[docs/barvis/modules.md](docs/barvis/modules.md#registering-a-module-in-barvis).

## Tests

Upstream's full suite is slow and not clean. Run the focused tests for what you touched:

```bash
python -m pytest -q tests/test_mcp_trust.py tests/test_request_authority.py tests/test_tool_policy.py
python -m py_compile app.py routes/*.py src/*.py
node --check static/js/<file-you-changed>.js
```

Setup for a full run is in `tests/KNOWN_FAILURES.md`. For changes that affect answers,
also run the module evals against the test instance
([modules.md](docs/barvis/modules.md#eval-harness)).

### Known upstream failures

- 23 tests already fail on clean upstream, in `tests/test_null_owner_gates.py` and
  `tests/test_runtime_evidence_contract.py`. They are not Barvis bugs. Don't fix them
  in Barvis PRs.
- To judge a failure, run the same test on a clean `barvis` checkout (a worktree):
  `git worktree add ../barvis-clean origin/barvis`. A failure that also happens there is
  not yours. Report new failures only.

## Git rules

- Work on a feature branch and open a PR. Small commits, Conventional Commits style
  (`feat(mcp): ...`, `docs(barvis): ...`).
- Branches here: `dev` mirrors upstream `dev` and is **never committed to**. `barvis` is
  the integration and default branch; PRs go into `barvis`. Stacked PRs say so in the
  description.
- Agents **never merge**, and never push to `main`, `barvis` or `dev` directly. No force
  pushes to branches you didn't create.
- Never commit secrets, `.env`, databases, `data/`, logs, audio, or tokens. Before
  pushing, run the checks in `SECURITY.md` ("Publishing A Fork").
- Upstream sync is its own PR: [docs/barvis/upstream-sync.md](docs/barvis/upstream-sync.md).

## When stuck

- After **three failed attempts at the same problem**, stop. Don't try a fourth variant.
  End your reply with `ESCALATE: <reason>` (one sentence: what fails and what you tried).
- If something in these docs is unclear or contradicts the code, say so in the PR
  description as an open question instead of guessing.

## Device code

Barvis has no device code; devices belong to modules. If a change can't be verified on
real hardware, list what still needs real-device testing in the PR description and the
module's `PROGRESS.md`.

## Tool-specific files

- `AGENTS.md` (this file) is the single source of instructions.
- `CLAUDE.md` imports it for Claude Code (`@AGENTS.md`) and adds Claude Code notes.
- Google Antigravity reads `AGENTS.md` at the workspace root directly;
  `.agents/rules/antigravity.md` adds Antigravity notes only.
- Put shared rules here, never in the tool-specific files.
