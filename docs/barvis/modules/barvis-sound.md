# Module: barvis-sound

- Repo: [SDarioosh/barvis-sound](https://github.com/SDarioosh/barvis-sound) (renamed from
  `barvis-ai`; older docs call it "convo-ai"). Default branch `main`.
- Agent guide: [AGENTS.md](https://github.com/SDarioosh/barvis-sound/blob/main/AGENTS.md).
  Server docs: [server/README.md](https://github.com/SDarioosh/barvis-sound/blob/main/server/README.md).
- Contract it follows: [../modules.md](../modules.md).

Code identifiers keep the old name: the Python package `convo_server`, the CLI
`convo-server`, `CONVO_*` env vars, the MCP server name `convo-ai`, and the Barvis server
name `convo`. Don't rename them.

## What it provides

Wearables record conversations (Amazfit T-Rex 3 Pro watch app now; a XIAO ESP32-S3
pendant planned; an Omi pendant later). The barvis-sound server on the owner's machine
detects speech (Silero VAD), transcribes (whisper.cpp), identifies speakers, splits
recordings into conversations, and summarizes them with the local LLM (title, summary,
key points, action items, people, topics, facts, daily digest). Everything is stored in
SQLite (`server/data/convo.db`) and exposed through an MCP server.

## Tools

Source: `server/convo_server/mcp_server.py` (`build_mcp`).

| Tool | Arguments | Annotations |
|---|---|---|
| `search_transcripts` | `speaker`, `query`, `since`, `until`, `limit` | read-only |
| `search_conversations` | `query`, `person`, `since`, `until`, `limit` | read-only |
| `get_conversation` | `conversation_id` | read-only |
| `get_transcript` | `conversation_id`, `start_seconds`, `end_seconds`, `max_lines` | read-only |
| `list_action_items` | `status` (open/done/dismissed/all), `person`, `since`, `until` | read-only |
| `list_facts` | `subject`, `query`, `limit` | read-only |
| `get_daily_digest` | `date` | read-only |
| `list_people` | — | read-only |
| `update_action_item` | `item_id`, `status` (open/done/dismissed) | **write** |
| `name_speaker` | `conversation_id`, `speaker_label`, `name`, `remember_voice` | **write** |

- Read-only tools: `readOnlyHint: true`, `openWorldHint: false`. With `convo` in
  `ODYSSEUS_TRUSTED_READONLY_MCP`, Barvis runs them without approval.
- Write tools: `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`,
  `openWorldHint: false`. They keep Barvis' normal approval. `name_speaker` renames the
  speaker's lines, redoes the summary and (with `remember_voice`) stores the voice.
- Dates are `YYYY-MM-DD` in `CONVO_TIMEZONE`.
- Links in results have the form `<CONVO_PUBLIC_URL>/view/<conversation_id>?sig=…#t=<seconds>`:
  a signed HTML transcript page that opens in a phone browser without a token. Rotating
  `CONVO_INGEST_TOKEN` revokes all links.
- Every non-empty result asks the model to put the link next to each fact used.

## Connect it

Environment for the MCP server (read from the environment or `server/.env`):

| Variable | Meaning |
|---|---|
| `CONVO_INGEST_TOKEN` | required; also the key that signs transcript links |
| `CONVO_DATA_DIR` | data directory holding `convo.db` |
| `CONVO_TIMEZONE` | timezone for dates (default `UTC`) |
| `CONVO_PUBLIC_URL` | base URL of links; use the address the phone reaches (Tailscale IP) |
| `CONVO_MCP_TOKEN` | HTTP only; ≥ 16 chars, separate from the ingest token |
| `CONVO_MCP_HOST`, `CONVO_MCP_PORT` | HTTP only; default `127.0.0.1`, `8001` |

**Streamable HTTP** (use this; it is what the sandbox stack and the owner's Docker setup
use):

```bash
cd server
CONVO_MCP_TOKEN=... CONVO_MCP_HOST=172.17.0.1 .venv/bin/convo-server mcp --http   # docker bridge IP
```

Then add an HTTP server named `convo` with URL
`http://host.docker.internal:8001/mcp?access_token=<CONVO_MCP_TOKEN>` (Barvis in Docker)
or `http://127.0.0.1:8001/mcp?access_token=<CONVO_MCP_TOKEN>` (Barvis native, e.g. the
sandbox).

**stdio** (`convo-server mcp`, args `["mcp"]`, with the `CONVO_*` environment) connects,
but every call then fails in Barvis; see
[../sandbox.md](../sandbox.md#findings-from-the-first-sandbox-run).

Then, per [../modules.md](../modules.md#registering-a-module-in-barvis):

1. `ODYSSEUS_TRUSTED_READONLY_MCP=convo`, restart Barvis (production: already in
   `docker-compose.override.yml`, see [../deploy.md](../deploy.md#features-and-modules)).
2. Paste the server's `INSTRUCTIONS` text (top of `mcp_server.py`) into a Custom preset
   system prompt.
3. `python scripts/barvis_setup.py --url http://127.0.0.1:7010 --password-file <file>`
   (sandbox, or the server's test instance; on production `barvis_deploy.sh` runs it).
   It creates the `/sound` and `/context` skills from that preset text.

## Slash skill: `/sound`

- Skill **`sound`**, server **`convo`**: `/sound <question>` gives that one message
  barvis-sound's 10 tools (plus `ask_user`/`update_plan`) and nothing else. `/context
  <question>` does the same for every trusted module (today only `convo`).
- The skill's procedure is `INSTRUCTIONS` from `server/convo_server/mcp_server.py`, the
  same text as the Custom preset. barvis-sound owns it: change it there, update the
  preset, rerun `barvis_setup.py`.
- Needed because with every Odysseus tool on and "Auto", a plain recording question
  is routed to built-ins (calendar, files) and never sees a convo tool. Sandbox
  numbers: [../sandbox.md](../sandbox.md#slash-skills-2026-10-18).
- Examples: `/sound what did Alice say about the budget in September?`,
  `/context what did Maria ask me to do on Friday?` The model gets today's date on
  every turn, so relative dates work.
- Write tools (`update_action_item`, `name_speaker`) are offered on `/sound` turns and
  still ask for approval.

## Eval and current score

- Script: `server/eval/bakeoff.py` in barvis-sound. Seed fictional data first with
  `server/scripts/seed_demo.py` using the same `CONVO_DATA_DIR`.
- 20 questions; a question passes when the frontend called a convo tool, cited a link to
  the right conversation, and the answer has the expected fact.
- Barvis run: `python eval/bakeoff.py --frontend odysseus` from `server/`, with
  `ODYSSEUS_URL` (default `http://127.0.0.1:7010`), `ODYSSEUS_USER`, and
  `ODYSSEUS_PASSWORD`, `ODYSSEUS_PASSWORD_FILE` or `ODYSSEUS_API_TOKEN`; optional
  `ODYSSEUS_PRESET` (default `custom`) and `ODYSSEUS_APPROVAL`.
- Results: `server/eval/results/odysseus.jsonl` (gitignored).
- Sandbox (Qwen3-4B on CPU, questions 1, 4, 16; not comparable with the table below):
  raw 1/3, Barvis over HTTP 1/3. Steps: [../sandbox.md](../sandbox.md#run-the-eval).

| Frontend (same model, same MCP server) | Score |
|---|---|
| upstream Odysseus | 3/20 |
| Barvis, PR SDarioosh/Barvis#1 | 9/20 (right fact 18/20, link cited 9/20) |
| tuned Hermes Agent | 15/20 |

**Target: ≥ 15/20.** Remaining failures: answers with the right fact but no transcript
link, and 2 replies that were just "Done." because the model returned empty text after
its tool calls. Full analysis: barvis-sound
[docs/frontend-decision.md](https://github.com/SDarioosh/barvis-sound/blob/main/docs/frontend-decision.md).

## Known gaps

- No test yet asserts the annotation of every tool (required by
  [../modules.md](../modules.md#interface-mcp-server)).
- Plain questions (no `/sound` or `/context`) don't reach the convo tools when every
  Odysseus tool is on ([../roadmap.md](../roadmap.md)).
- A malformed date argument (e.g. `'2026-09-30”,'` from the 4B sandbox model) makes
  `search_conversations` raise instead of returning a readable error, so Barvis only
  sees `exit_code=1` and the model reports a vague failure. A barvis-sound fix, listed
  under "Next" in SDarioosh/Barvis#9.
