# Module: odysseus-sound

- Repo: [SDarioosh/barvis-sound](https://github.com/SDarioosh/barvis-sound) (renamed from
  `barvis-ai`; older docs call it "convo-ai"). Default branch `main`.
- Agent guide: [AGENTS.md](https://github.com/SDarioosh/barvis-sound/blob/main/AGENTS.md).
  Server docs: [server/README.md](https://github.com/SDarioosh/barvis-sound/blob/main/server/README.md).
- Contract it follows: [../modules.md](../modules.md).

Code identifiers keep the old name: the Python package `convo_server`, the CLI
`convo-server`, `CONVO_*` env vars, the MCP server name `convo-ai`, and the Odysseus server
name `convo`. Don't rename them.

## What it provides

Wearables record conversations (Amazfit T-Rex 3 Pro watch app now; a XIAO ESP32-S3
pendant planned; an Omi pendant later). The odysseus-sound server on the owner's machine
detects speech (Silero VAD), transcribes (whisper.cpp), identifies speakers, splits
recordings into conversations, and summarizes them with the local LLM (title, summary,
key points, action items, people, topics, facts, daily digest). Everything is stored in
SQLite (`server/data/convo.db`) and exposed through an MCP server.

## Tools

Source: `server/convo_server/mcp_server.py` (`build_mcp`).

| Tool | Arguments | Annotations |
|---|---|---|
| `search_transcripts` | `speaker`, `query`, `since`, `until`, `limit` | read-only |
| `search_conversations` | `query`, `person`, `since`, `until`, `kind` (note/conversation/empty), `limit` | read-only |
| `get_conversation` | `conversation_id` | read-only |
| `get_transcript` | `conversation_id`, `start_seconds`, `end_seconds`, `max_lines` | read-only |
| `list_action_items` | `status` (open/done/dismissed/all), `person`, `since`, `until` | read-only |
| `list_facts` | `subject`, `query`, `limit`, `confirmed` (only facts the module has confirmed), `format` (`json` for the facts sync) | read-only |
| `get_daily_digest` | `date` | read-only |
| `list_people` | — | read-only |
| `list_unnamed_speakers` | `limit` | read-only |
| `update_action_item` | `item_id`, `status` (open/done/dismissed) | **write** |
| `name_speaker` | `conversation_id`, `speaker_label`, `name`, `remember_voice` | **write** |
| `reassign_line` | `line_id`, `name` | **write** |
| `rename_speaker_everywhere` | `old_name`, `new_name` | **write, destructive** |

- Read-only tools: `readOnlyHint: true`, `openWorldHint: false`. With `convo` in
  `ODYSSEUS_TRUSTED_READONLY_MCP`, Odysseus runs them without approval.
- Write tools: `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`,
  `openWorldHint: false`. `rename_speaker_everywhere` has `destructiveHint: true`
  (merging two names can't be undone). They keep Odysseus' normal approval.
- `name_speaker` renames the speaker's lines, redoes the summary and (with
  `remember_voice`) adds a voice sample; since MCP version 0.2.0 it also renames
  unnamed speakers in other conversations whose voice clearly matches, and says how many.
- `get_transcript` and `search_transcripts` lines start with `#<line id>`, the
  `line_id` for `reassign_line` (fix one line given to the wrong person).
- `get_transcript` prints `"<title>" <YYYY-MM-DD HH:MM:SS> <link>`, then one
  `#<line id> [<seconds>s] <speaker>: <text>` per line, 100 lines a page by default.
  Modules before 2026-10-20 printed `[<seconds>s <HH:MM:SS>]`, minute-precision start
  times and 200 lines; `src/sound_module_view.py` reads both. The result and the
  `INSTRUCTIONS` tell the model to answer "show me the transcript" with the link and a
  short summary instead of retyping the lines. Retyping was where a `/sound` transcript
  request spent its time (sandbox: [Transcript requests](../sandbox.md#transcript-requests-2026-10-20)).
- Voice notes (the owner recording a memo for themselves) are stored as their own kind.
  Results mark them `"<title>" (voice note)`, and `search_conversations` with
  `kind="note"` returns only voice notes (`"conversation"`: only the others; empty: both).
  The server `INSTRUCTIONS` tell the model to use it for "my voice notes / notes / memos";
  without it, "my latest voice notes" got the latest conversations.
- `list_unnamed_speakers` lists voices nobody has named yet ("Speaker N") with a few
  quotes, links and the closest known voice; for "who is this?" questions and cards.
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

On the owner's server `odysseus-deploy deploy` runs this as a background service
(odysseus-sound's `server/scripts/deploy.sh`, settings in `server/.env`;
[../deploy.md](../deploy.md#one-command-setup-once)).

Then add an HTTP server named `convo` with URL
`http://host.docker.internal:8001/mcp?access_token=<CONVO_MCP_TOKEN>` (Odysseus in Docker)
or `http://127.0.0.1:8001/mcp?access_token=<CONVO_MCP_TOKEN>` (Odysseus native, e.g. the
sandbox).

**stdio** (`convo-server mcp`, args `["mcp"]`, with the `CONVO_*` environment) works for a
native Odysseus since stdio connections got their own owner task (before, every call after
the adding request failed; [../sandbox.md](../sandbox.md#findings-from-the-first-sandbox-run),
finding 2). HTTP stays the documented setup: Odysseus in Docker can't spawn `convo-server`.

Then, per [../modules.md](../modules.md#registering-a-module-in-odysseus):

1. `ODYSSEUS_TRUSTED_READONLY_MCP=convo`, restart Odysseus (production: already in
   `docker-compose.override.yml`, see [../deploy.md](../deploy.md#features-and-modules)).
2. Paste the server's `INSTRUCTIONS` text (top of `mcp_server.py`) into a Custom preset
   system prompt.
3. `python scripts/modules_setup.py --url http://127.0.0.1:7010 --password-file <file>`
   (sandbox, or the server's test instance; on production `fork_deploy.sh` runs it).
   It creates the `/sound` and `/context` skills from that preset text.

## Slash skill: `/sound`

- Skill **`sound`**, server **`convo`**: `/sound <question>` gives that one message
  odysseus-sound's 13 tools (plus `ask_user`/`update_plan`) and nothing else. `/context
  <question>` does the same for every trusted module (today only `convo`).
- The skill's procedure is `INSTRUCTIONS` from `server/convo_server/mcp_server.py`, the
  same text as the Custom preset. odysseus-sound owns it: change it there, update the
  preset, rerun `modules_setup.py`.
- Needed because with every Odysseus tool on and "Auto", a plain recording question
  is routed to built-ins (calendar, files) and never sees a convo tool. Sandbox
  numbers: [../sandbox.md](../sandbox.md#slash-skills-2026-10-18).
- Examples: `/sound what did Alice say about the budget in September?`,
  `/context what did Maria ask me to do on Friday?` The model gets today's date on
  every turn, so relative dates work.
- Write tools (`update_action_item`, `name_speaker`) are offered on `/sound` turns and
  still ask for approval.
- Follow-ups in the same chat don't need `/sound` again: once a convo tool has run in a
  chat, every later message there is offered all 13 tools (write tools still ask). So
  "Speaker 1 and 3 were Shahyah, speaker 2 was Jenn" after `/sound what did we talk about
  last night?` calls `name_speaker`.
- Approving a write tool once covers more calls of that same tool in the same reply:
  one click renames all three speakers. A different write tool in that reply is still
  refused; ask for it in a new message.

## Action items in Odysseus

odysseus-sound stays the source of truth for action items. Since 2026-10-20 (owner's
decision, replacing "no copies" of 2026-10-19) Odysseus also **files** them where they
belong, so voice notes land in the hub's own calendar and to-dos:

| Item kind (module) | Example | Filed as |
|---|---|---|
| `event` | "meeting with Jason on Wednesday at 12 pm" | calendar event in the default calendar: timed (60 min or the stated length, reminder 30 min before), or all-day when no time was said |
| `task` | "get the proposal done on Wednesday" | to-do (checklist note, label `odysseus-sound`), due that day at `MODULE_DIGEST_HOUR` |
| `reminder` | "remind me at 5 to call Mom" | to-do due at that time (Odysseus fires its own reminder) |
| any task or reminder with a `list_name` | "add milk to my shopping list" | one line on that to-do list (see below) |
| `note` | "create a note battery dimensions are 13mm x 14mm x 3mm" | an Odysseus note (type note, label `odysseus-sound`) titled with the module's `note_title` ("Battery dimensions"), the text and the source link as content |
| `note` with a `list_name` | "add to my hardware notes: the screen is 1.4 inch" | one line `- The screen is 1.4 inch` on those notes (see below) |

- **Named to-do lists.** When the note names a list, the module returns `list_name`
  (`shopping`, without "my"/"the"/"list") and the item text without the phrase
  (`Milk`). Odysseus adds it as a line to the open checklist or to-do note with that
  title, in any case and with or without "list" ("Shopping", "shopping list"); if there
  is none, it creates a to-do list titled "Shopping" (label `odysseus-sound`). A due date
  goes into the line's text ("Eggs (due Wed 22 Oct)"); lines have no reminder of their
  own. An open line with the same text isn't added twice. The line's status follows
  both ways: ticked ↔ done, line removed → dismissed, list archived → done. Undo and
  moving take off the line only; the list stays. On `/modules/sound`, **Add to a list**
  / **Change list** picks a list (your existing lists are suggested) or clears it.
  A list whose lines are all ticked is archived 10 minutes later unless it is pinned
  (`src/checklist_auto_archive.py`), so the next "add … to my shopping list" starts a
  new one; pin a list to keep it.

- **Notes.** Voice notes only, and only on an explicit command ("create a note",
  "make a note", "note that", "add to my … notes"); a plain statement makes no note.
  A note with a `list_name` (`hardware`) is added as a line to the open note titled
  with that name, with or without "notes" ("Hardware notes", "hardware"); if there is
  none, Odysseus creates "Hardware notes". The same line isn't added twice. Once filed
  the module item is marked done (a note is no to-do; the module's text listing, digest
  and HTTP list leave notes out anyway), and status isn't synced after that: edit,
  archive or delete the note in Odysseus as you like. Undo on `/modules/sound` (shown
  while the item is still open) removes the note, or only its line. A note whose
  filing failed waits there with **Add to notes**.
- **Voice notes are filed automatically.** One push per pass ("Filed from your voice
  note": what went where, "check the time" when the module flagged it). Filed items get
  no other "new item" or "due today" push; Odysseus' own event and to-do reminders take
  over.
- **Conversations only suggest.** Their items wait on `/modules/sound` with **Add to
  calendar** / **Add to to-dos**; nothing is filed and nothing is pushed until tapped.
- **Fix or undo** on `/modules/sound`: Move to calendar / Move to to-dos, Change time,
  Undo (removes the Odysseus entry and dismisses the item). An item is filed once: if
  you delete the event or note in Odysseus it is not filed again.
- **Status both ways**: ticking the to-do (or archiving it) marks the module item done,
  and the other way round; deleting the event dismisses the item; a past event is done.
- The first run only remembers existing items (no backlog is filed).

The built-in task **Module Action Items** (`src/module_action_items.py`, every 5 min)
does all of this. It reads `list_action_items` with `format="json"` (fields in the module's
`server/README.md`) and writes through `update_action_item` (`status`, `kind`,
`due_date`, `due_time`, `list_name`). With an older module that returns only text it falls back to
pushes without filing. The module's `due_date`/`due_time` are local to its
`CONVO_TIMEZONE`; set the same timezone in Odysseus (the task logs a warning when they
differ and converts).

Settings: `MODULE_FILE_ITEMS` (`auto` default, `off` = pushes only, as before),
`MODULE_ACTION_ITEMS_SERVER` (default `convo`), `MODULE_NOTIFY_CHANNEL` (`ntfy`,
`browser`, `off`), `MODULE_DIGEST_HOUR` (`8`, `off`). ntfy needs an enabled ntfy
integration (Settings → Integrations); the topic is the reminder topic
(`reminder_ntfy_topic`).

The text lines of `list_action_items` (what models read) are still parsed as a fallback:
`- #<id> [<status>] [<kind>: ]<text>[ (owner <name>)][ due <YYYY-MM-DD>[ <HH:MM>][ (<as said>)]| due <as said>][ [time unclear]] — from "<title>"[ (voice note)] <YYYY-MM-DD HH:MM> <link>`
(`<kind>` is `event` or `reminder`; tasks have none). Change it only together with the
parsers and their tests.

## Facts in Odysseus memory

Confirmed facts also live in Odysseus memory, so chat context and the Memory page know
them without a module call. Since Phase 1 they pass through the hub's **belief ledger**
([../beliefs.md](../beliefs.md)): the module's facts are evidence, and the ledger decides
what is confirmed. The built-in task **Module Facts** (`src/module_facts.py`, hourly,
posts nothing to chat) calls `list_facts` with `{"format": "json", "limit": 1000}` (every
current fact; 1000 is the JSON mode's cap, so no fact is cut off and mistaken for
retired), which returns `{"facts": [{"id", "subject", "about" (owner/person/other),
"fact", "times_said", "days", "first_said", "last_said", "link", "confirmed"}]}`.

- **Evidence per fact**: source `odysseus-sound`, source id = the fact id, the fact text,
  the signed transcript link, `first_said`/`last_said` as seen dates, `times_said`/`days`
  as totals, and the row's `confirmed` (the module's own threshold) as the source's
  verdict. A module that doesn't send `confirmed` per row yet gets one more call,
  `{"confirmed": true, "format": "json", "limit": 1000}`, whose ids are the confirmed
  set.
- **Confirmed beliefs become memories**: confirmed by the module's flag, by a second
  source saying the same thing (`BELIEF_CONFIRM_SOURCES`), by enough sightings on enough
  days, or by the owner. One memory per belief, for the task's owner: text = the fact
  text (or the owner's correction) followed by ` (source: <link>)`; fields `source:
  "odysseus-sound"`, `belief_id`, `module_fact_id`, `link`, `subject`, `about`,
  `times_said`, `days`, `first_said`, `last_said`, `first_seen`, `last_seen` and
  `synced_at`. The memory text ends with the transcript link so chat answers can cite it
  (chat context injects only a memory's text). Copies written before the ledger are
  adopted (same memory id and use count), not duplicated.
- **Category** (from the bare fact text, not the link): about the owner → `preference`
  when it has a preference word
  (like(s), prefer(s), love(s), hate(s), enjoy(s), favorite/favourite, want(s),
  dislike), else `fact`; about a person → `contact`; anything else → `fact`.
- **Full reconcile every run**: a changed fact (text or metadata) updates its belief and
  memory in place (same memory id and use count); a fact the module no longer returns is
  marked gone, and its belief goes stale and leaves memory unless the owner confirmed
  it; beliefs not heard of for `BELIEF_STALE_DAYS` (180) go stale. Running twice
  changes nothing. Memory Tidy may merge or delete copies like any memory; the next run
  puts them back. The owner's own memories and other owners' copies are never touched.
- **Forgetting**: when the owner forgets a belief in Odysseus, the hub calls the module's
  `retire_fact {"fact_id", "reason"}` so the fact doesn't come back; a module without
  that tool is tolerated (the hub keeps the belief rejected).
- The result line has counts only (`facts: 12 seen, 1 new, 1 confirmed, 0 unconfirmed,
  0 gone, 0 stale; memory: 11 kept, 1 added, 0 updated, 0 removed`); a run with no change is not logged
  in Activity. An older module that answers `list_facts` with text: nothing is synced
  (logged once).
- Settings: `MODULE_FACTS_SYNC` (`on` default, `off` stops the sync; memories already
  mirrored stay), the action-item task's `MODULE_ACTION_ITEMS_SERVER` (default `convo`),
  and the ledger's `BELIEF_*` settings ([../beliefs.md](../beliefs.md#settings)).

## Conversations window

**Conversations** (sidebar, icon rail, `/open conversations`, an optional shortcut; admin
only) opens a tool window in the app; `/modules/sound` shows the same view as its own page
(`static/js/forkModuleWindows.js`, `static/js/soundModule.js`). It shows open action items live
(Done / Dismiss call `update_action_item`; Odysseus keeps no copy), the module's
conversations (filter by words, person, dates and type: all, conversations, voice notes;
voice notes are labelled), and a "who is this?" card for every unnamed speaker. **Open** reads one
conversation in place (`get_conversation`, `get_transcript` 200 lines a page); tapping a
speaker fixes one line (`reassign_line`), a whole unnamed `Speaker N` (`name_speaker`) or
a name everywhere (`rename_speaker_everywhere`, confirmed first). **Ask in chat** puts
`/sound About the conversation "<title>" (id <id>, <start>): ` into a new chat for the
owner to finish and send (the page links to `/?ask=…`). It calls
`list_action_items`, `update_action_item`, `search_conversations`, `get_conversation`,
`get_transcript`, `reassign_line`, `rename_speaker_everywhere`,
`list_unnamed_speakers` and `name_speaker` through the MCP
manager and parses their result lines (`src/sound_module_view.py`, test
`tests/test_sound_module_view.py`); change those line shapes together with the parser.
`SOUND_MODULE_SERVER` names the server (default `convo`). A signed `/view` link to a
moment (`#t=<s>`), clicked in chat or in the view (quotes, line times, "where it was
said"), opens the reader at that line; the **Transcript** buttons and a modified click
open the module's page in a new tab (Odysseus' CSP doesn't allow framing another origin).
This relies on the link format in [../modules.md](../modules.md): a `/view/<id>` path
with a `sig` parameter.
Audio isn't kept after transcription, so cards quote lines instead of playing them.

## Today insight

The hub's daily Today insight reads `get_daily_digest` (today) and `list_action_items`
(`status="open"`, `format="json"`) when the module is connected, and works without them
otherwise. Details: [../today-insight.md](../today-insight.md).

## Eval and current score

- Script: `server/eval/bakeoff.py` in odysseus-sound. Seed fictional data first with
  `server/scripts/seed_demo.py` using the same `CONVO_DATA_DIR`.
- 21 questions; a question passes when the frontend called a convo tool, cited a link to
  the right conversation, and the answer has the expected fact.
- Odysseus run: `python eval/bakeoff.py --frontend odysseus` from `server/`, with
  `ODYSSEUS_URL` (default `http://127.0.0.1:7010`), `ODYSSEUS_USER`, and
  `ODYSSEUS_PASSWORD`, `ODYSSEUS_PASSWORD_FILE` or `ODYSSEUS_API_TOKEN`; optional
  `ODYSSEUS_PRESET` (default `custom`) and `ODYSSEUS_APPROVAL`.
- Question 21 ("What was my latest voice note about?") needs the two demo voice notes:
  rerun `seed_demo.py` on an older demo store (it adds only what is missing). Scores
  below are out of 20 (questions 1–20, unchanged); compare new runs on `--only 1 … 20`
  plus 21 separately.
- Voice notes, sandbox 2026-10-20 (Qwen3-4B, `--trim`), question 21: before the `kind`
  filter 0/1 plain and 0/1 `/sound` (the model said it has no access to voice notes);
  after it `/sound` 1/1 (answered with the latest note, not the newer conversation) and
  plain 0/1 (the model wrote `search_conversations {"kind": "note"}` as text instead of
  a tool call). `/sound` on 1, 4, 16 with it: 2/3; 16 also fails with the old
  instructions (2/2), so it is model noise, not this change.
- Results: `server/eval/results/odysseus.jsonl` (gitignored).
- Sandbox (Qwen3-4B on CPU, questions 1, 4, 16; not comparable with the table below):
  raw 1/3, Odysseus over HTTP 1/3. With every Odysseus tool on and "Auto" (questions 1,
  4, 16, 21): plain 0/4, `/sound` 4/4, `/context` 1/4, `--trim` 3/4
  ([../sandbox.md](../sandbox.md#slash-skills-2026-10-18)). Slash-prefixed runs:
  `scripts/module_slash_eval.py` in Odysseus. Steps: [../sandbox.md](../sandbox.md#run-the-eval).

| Frontend (same model, same MCP server) | Score |
|---|---|
| upstream Odysseus | 3/20 |
| our fork, PR SDarioosh/Barvis#1 | 9/20 (right fact 18/20, link cited 9/20) |
| tuned Hermes Agent | 15/20 |

**Target: ≥ 15/20.** Remaining failures: answers with the right fact but no transcript
link, and 2 replies that were just "Done." because the model returned empty text after
its tool calls. Full analysis: odysseus-sound
[docs/frontend-decision.md](https://github.com/SDarioosh/barvis-sound/blob/main/docs/frontend-decision.md).

## Known gaps

- Plain questions (no `/sound` or `/context`) don't reach the convo tools when every
  Odysseus tool is on ([../roadmap.md](../roadmap.md)).
- A malformed date argument (e.g. `'2026-09-30”,'` from the 4B sandbox model) makes
  `search_conversations` raise instead of returning a readable error, so Odysseus only
  sees `exit_code=1` and the model reports a vague failure. An odysseus-sound fix, listed
  under "Next" in SDarioosh/Barvis#9.
