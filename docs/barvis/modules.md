# Barvis module contract

Every module that plugs into Barvis follows this contract. It exists so that a new module
works in Barvis without code changes to Barvis, and so that Barvis' trust rules stay safe.

Registered modules:

| Module | What it provides | Doc |
|---|---|---|
| barvis-sound | recorded conversations: transcripts, summaries, action items, facts, digests | [modules/barvis-sound.md](modules/barvis-sound.md) |

## Repo and ownership

- One module = one repo, named `barvis-<name>` (for example `barvis-sound`).
- The module is a separate program. It owns its storage (database, files, models) and is
  the only thing that writes to it.
- Barvis never reads or writes the module's storage directly. It uses the module's MCP
  server, or a documented HTTP API for things MCP can't do (for example an HTML view a
  link opens).
- The module never imports Barvis or Odysseus code. It talks to Barvis over a protocol,
  so Barvis' AGPL-3.0 doesn't extend to it. The module picks its own license.
- The module repo has its own `AGENTS.md` with a "This is a Barvis module" section that
  links here.

## Privacy

- Local only. The module's data path uses no cloud STT, LLM or embedding API and sends no
  telemetry. Models run on the owner's server (see
  [architecture.md](architecture.md#services-and-ports)).
- Network listeners bind to loopback, the Docker bridge or the Tailscale interface, never
  to the public internet.
- Tool results contain private data. Only a local model may read them.

## Interface: MCP server

### Transports

- **stdio** for clients that spawn a process (e.g. Hermes Agent, Barvis with a stdio
  server).
- **Streamable HTTP** for clients that connect over the network (e.g. Barvis in Docker).
- One command starts each, documented in the module's README. barvis-sound:
  `convo-server mcp` (stdio) and `convo-server mcp --http`.

### Auth (HTTP)

- Every HTTP request needs a token. HTTP refuses to start without one.
- Accept both `Authorization: Bearer <token>` and `?access_token=<token>` in the URL.
  Odysseus' HTTP MCP client only does OAuth and can't send a static header, so Barvis
  uses the query form: `http://<host>:<port>/mcp?access_token=<token>`.
- Compare tokens in constant time. Use a token separate from any device/ingest token.
  Reference implementation: `BearerAuth` in barvis-sound's
  `server/convo_server/mcp_server.py`.

### Tool naming and descriptions

- Tool names are `snake_case` verbs: `search_*`, `get_*`, `list_*` for reads; a specific
  verb (`update_*`, `name_*`, `create_*`) for writes. Don't prefix the module name:
  Barvis already qualifies tools as `mcp__<server_id>__<tool>`.
- Don't reuse the names of Odysseus built-ins the model might confuse them with (tasks,
  calendar, notes, contacts, memory, chat search). If the concept overlaps, say in the
  description what makes this tool different ("the user's recorded conversations").
- The description says when to use the tool, in one or two sentences. Every argument
  has a description. Dates are `YYYY-MM-DD` in the module's configured timezone.
- Keep the tool count small (barvis-sound has 10). Every schema is sent with every
  request.

### Annotations (Barvis trust depends on them)

Barvis admits a trusted server's tool without approval **only** if the tool declares
`readOnlyHint: true` and not `destructiveHint: true` (`src/mcp_trust.py`). There is no
name heuristic. So:

- A tool that changes nothing: `readOnlyHint: true`, `openWorldHint: false`.
- A tool that changes anything (status, names, records, files, external systems):
  `readOnlyHint: false`. Set `destructiveHint: true` if it deletes or overwrites data,
  `false` if the change is additive or reversible. Set `idempotentHint` honestly.
- Never mark a write tool read-only to avoid approval prompts. That is a security bug.
- A test in the module asserts the annotation of every tool.

### Results

- Return compact text, not raw JSON dumps.
- Every fact in a result carries a **source link** the user can open on the phone (for
  barvis-sound: a signed transcript link that jumps to the moment).
- Barvis (Odysseus) doesn't pass the MCP server's `instructions` to the model. Put the
  citation rule in the tool results themselves: every non-empty result says to put the
  link next to each fact used. Empty results say so plainly ("No conversations found.").
- Long results are paged with explicit arguments (e.g. `start_seconds`) and say how to
  get more.

## Registering a module in Barvis

Do this in the sandbox stack first ([sandbox.md](sandbox.md)); the owner repeats it on
the server's test instance (`127.0.0.1:7010`). Never on the owner's real install
(port 7000) unless the owner asks.

1. **Start the module's MCP server**, HTTP bound to loopback / the Docker bridge /
   Tailscale. Use HTTP: stdio servers added over the API currently fail on every call
   in Barvis ([sandbox.md](sandbox.md#findings-from-the-first-sandbox-run)).
2. **Add it to Barvis:** Settings > MCP, or `POST /api/mcp/servers`. Give it a short
   name (barvis-sound: `convo`). HTTP URL form: `http://<host>:<port>/mcp?access_token=<token>`.
3. **Trust its read-only tools:** add the server name to `ODYSSEUS_TRUSTED_READONLY_MCP`
   (comma separated, e.g. `ODYSSEUS_TRUSTED_READONLY_MCP=convo,<next>`) in `.env` or
   docker-compose, then restart. Write tools keep normal approval. Known bug: Barvis
   drops annotations for HTTP servers, so their read-only tools still ask for approval
   until that is fixed ([sandbox.md](sandbox.md#findings-from-the-first-sandbox-run)).
4. **Trim tools:** run `scripts/barvis_setup.py` (it disables built-ins outside its
   keep-list and sets "Regular tools" mode; in "Auto" mode non-Odysseus models are never
   offered MCP tools). Use `--dry-run` first.
5. **Add instructions to the preset:** paste the module's usage instructions (its MCP
   `instructions` text) into a Custom preset system prompt, because Odysseus doesn't
   forward them.
6. **Run the module's eval** (see "Eval harness"): a few questions in the sandbox, the
   full run by the owner on the server. Record the server score in the module doc.
7. **Document it:** add `docs/barvis/modules/<name>.md` (copy the structure of
   [barvis-sound.md](modules/barvis-sound.md)), a row in the table at the top of this
   file, and its services in [architecture.md](architecture.md).

## Eval harness

- Each module ships an eval script like barvis-sound's `server/eval/bakeoff.py`:
  - fictional seed data (never real recordings), created by a script in the module;
  - a fixed question list, each with the expected source(s) and answer keywords;
  - a pass rule: the frontend called a module tool, cited a link to an expected source,
    and the answer has an expected keyword;
  - a `--frontend odysseus` mode that talks to a Barvis instance over its API;
  - results written to a gitignored file.
- Each module states a **target score** in its module doc. barvis-sound: ≥ 15/20 (what
  tuned Hermes Agent reached).
- Rerun the eval after: any change to the module's tool contract, any Barvis change to
  MCP gating/routing/presets, and every upstream merge.
- Agents run it in the sandbox stack with a small model and a few questions
  ([sandbox.md](sandbox.md#run-the-eval)); those scores only compare with other sandbox
  runs. The target score is measured by the owner on the server, from the PR's Server
  verification section.

## Versioning

The **tool contract** is: tool names, argument names and types, which arguments are
required, annotations, the link format in results, and the auth scheme.

Breaking changes (need a coordinated change):

- renaming or removing a tool or argument, or making an optional argument required;
- changing an annotation (especially `readOnlyHint` or `destructiveHint`);
- changing the link format or what a link opens;
- changing the auth scheme or the MCP URL path.

Non-breaking: adding a tool, adding an optional argument, wording changes in descriptions
or results that keep links and the citation rule.

For a breaking change:

1. Open the module PR first. Its description lists the contract change.
2. Open a Barvis PR that updates the module doc (and preset text, setup or trust config if
   needed). Link the two PRs to each other.
3. Rerun the module eval with `--frontend odysseus` in the sandbox stack with both
   changes in place, and list the full server run under Server verification. Put the
   scores in both PRs.
4. The owner merges the module PR first, then the Barvis PR.

Bump the version string the module's MCP server reports (barvis-sound: `version="0.1.0"`
in `build_mcp`) on every contract change.

## Checklist: adding a new module

- [ ] Repo `barvis-<name>` with `AGENTS.md` ("This is a Barvis module" section linking
      here), `CLAUDE.md` (`@AGENTS.md`), `README.md`, `PROGRESS.md`.
- [ ] Owns its storage; nothing else writes to it.
- [ ] Local-only data path; no cloud APIs, no telemetry.
- [ ] MCP server over stdio and Streamable HTTP; HTTP requires a token via Bearer header
      or `?access_token=`.
- [ ] Tool names and descriptions as in "Interface: MCP server"; no clash with Odysseus built-ins.
- [ ] Correct `readOnlyHint` / `destructiveHint` on every tool, with a test.
- [ ] Every result fact has a source link; non-empty results carry the citation rule.
- [ ] Eval script with fictional seed data, a `--frontend odysseus` mode and a target
      score.
- [ ] Registered in the sandbox stack, then on the server's test instance: MCP server added, name in
      `ODYSSEUS_TRUSTED_READONLY_MCP`, `barvis_setup.py` run, preset instructions added.
- [ ] Eval run against Barvis; score recorded.
- [ ] `docs/barvis/modules/<name>.md`, the table above, and
      [architecture.md](architecture.md) updated.
