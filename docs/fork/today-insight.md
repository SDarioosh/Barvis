# Today insight

A short daily insight written by Odysseus from the owner's own data: a headline, what to
do first (with a reason each), what is coming up, and other things worth a look. It is
stage 0.3 and 0.4 of [personal-context.md](personal-context.md#phase-0-today-insight-and-confirmed-facts-decided-in-progress).
Everything runs on the owner's network with the local model; no input or answer leaves it.

Code: `src/today_insight.py` (the job, storage, chat context),
`routes/today_insight_routes.py` (routes), `static/today.html` and `static/js/today.js`
(the page). Test: `tests/test_today_insight.py`.

## The job

Built-in task **Today Insight** (action `today_insight`), seeded for every owner like the
other housekeeping tasks, every 15 minutes (`*/15 * * * *`; change it in Tasks). It posts
nothing to the assistant chat and queues for the model like the other model-backed
built-ins. A run that finds nothing changed leaves no Activity entry.

### Inputs, gathered in code

| Input | Where from |
|---|---|
| Calendar event occurrences from today 00:00 local to +7 days, not cancelled, not ended | the owner's calendars: events starting in the window (`_checkin_calendar_events`, as the check-in uses) and every recurring series, expanded with the calendar route's own `_expand_rrule` (`routes/calendar_routes.py`, skipped dates honoured): occurrence uid, summary, start, end, all-day, location, importance, event type |
| Open items of to-do notes | notes of type `checklist`, `todo`, `goal`, not archived; each item with its index, the note's title, pinned flag, due date and label. Calendar-reminder notes are left out (the event is already a candidate) |
| Memory | `MemoryManager(DATA_DIR).load(owner)`: entries of category `preference`, `contact`, `fact`, and every entry with `source: "odysseus-sound"`; at most 40, most used first, then newest. A trailing ` (source: <link>)` (memory copies of module facts carry one) is cut off: it cost ~1,700 prompt tokens and says nothing to the model |
| odysseus-sound | when its MCP server (`MODULE_ACTION_ITEMS_SERVER`, default `convo`) is connected: `get_daily_digest {"date": today}` and `list_action_items {"status": "open", "format": "json"}`. Items already filed into to-dos or the calendar by the Module Action Items task are left out (they are candidates already). A failed call or a disconnected module leaves that input out |

The owner's day and time come from the task's crew timezone (`_resolve_task_timezone`),
else the owner's personal assistant's timezone, else the server's.

### Rules applied before the model sees anything

- Each to-do item and event gets a bucket: `overdue` (due date before today), `due_today`,
  `due_soon` (within 7 days), `undated`; a to-do due later than a week is `later`.
  A note's due date counts for each of its items.
- Done items and ended events are dropped; an all-day event counts for its whole day.
- Shopping-type lists are dropped unless the note has a due date: titles in
  `TODAY_INSIGHT_SKIP_LISTS` (default `Shopping,Groceries,Grocery`), any case, with or
  without a trailing "list" ("shopping list", "Grocery List").
- Candidates, in order: every overdue and due-today item (by date and time), then due soon
  by date, then items of pinned notes, then undated, then later; at most 30. Each keeps
  its bucket and has a ref: `todo:<note id>:<item index>` or `event:<uid>`. The event uid
  is the occurrence uid exactly as the calendar route returns it: the plain uid for a
  single event, `<series uid>::<YYYY-MM-DD>` (all-day) or `<series uid>::<YYYY-MM-DDTHH:MM>`
  for an occurrence of a recurring series, so refs match the T5 feed's `calendar` list.
  The "ended" and "+7 days" checks apply to each occurrence, not the series' first date.
- Input hash: SHA-256 over the candidate list, the memory texts (sorted, so a chat that
  only bumps use counts and reorders them changes nothing), the digest text and the open
  action-item lines. The model runs only when there is no insight for today yet or
  the hash differs from the stored one; otherwise the run is a no-op. With no candidates
  at all, a fixed "nothing is due" insight is stored without the model.

### The model's part

The local model (the hub's background-task chain, `llm_call_async_with_fallback`,
thinking off) only picks candidates and writes the reasons. In the prompt each candidate
has a short label instead of its ref: `T1`, `T2`, … for to-dos and `E1`, … for events, in
candidate order (a small model copied the long ids badly: it dropped the `todo:`/`event:`
prefix, the item index or the occurrence date). It is asked for JSON only, with labels as
`ref`:

```json
{"headline": "1-2 sentences",
 "focus": [{"ref": "...", "why": "..."}],
 "coming_up": [{"ref": "...", "why": "..."}],
 "also": [{"ref": "...", "why": "..."}]}
```

The prompt: overdue and due-today first, but items not due today still count; use the
memory facts to personalise the reasons; keep reasons concrete. The answer is validated in
code: each `ref` is mapped back to a candidate ref (a label; an exact ref; as a last
resort a ref that matches exactly one candidate once the prefix is gone, by its start, as
a bare note id or series uid, or by its end; dates and short fragments never match), and
anything else is dropped. A ref is used once across sections, at most 5 per section, `why`
is cut to 80 characters, the headline to 300. An answer that is not usable JSON, or that
names no candidate at all although there are candidates, is retried once with a
corrective message; if that fails too, the previous insight (if any) stays, nothing
all-empty is stored, and the run is logged as failed in Activity.

The scheduled run waits for chat to go quiet before calling the model (as the other
model-backed built-ins do); the page's Refresh doesn't.

## Stored JSON

One file per owner and day: `<DATA_DIR>/today_insight/<owner>/<YYYY-MM-DD>.json`
(`<owner>` with characters other than letters, digits and `-_.@` replaced by `_`).
Example from the test's fictional data:

```json
{
 "date": "2026-10-21",
 "generated": "2026-10-21T07:00:00Z",
 "headline": "The Q4 deck is overdue; send it before the dentist at 12.",
 "focus": [{"ref": "todo:n1:0", "why": "Overdue since yesterday; Tom waits on it", "title": "Send the Q4 deck"}],
 "coming_up": [{"ref": "event:e1", "why": "12:00 today", "title": "Dentist"}],
 "also": [{"ref": "todo:n4:0", "why": "Sunday long run, pack shoes", "title": "Long run Sunday"}],
 "input_hash": "0944546b50b23f83c93ddba8c7ad63e8491be207d65fb6b29698fc5ebe6e6226",
 "votes": [{"vote": "up", "at": "2026-10-21T07:00:00Z"}]
}
```

`title` is the to-do item's text or the event's summary; readers that don't need it ignore
it. `votes` are 👍/👎 from the page, kept when the insight is rewritten later that day
(Phase 3 learns from them). Old days stay on disk.

## Routes

| Route | Auth | Answer |
|---|---|---|
| `GET /api/codex/insight/today` | API token with scope **`insight:read`** (or a browser session) | today's JSON without `input_hash` and `votes`; 404 `{"error": "no insight yet"}`; 403 without the scope |
| `GET /api/insight/today` | admin session (as `/modules/sound`) | the same plus `votes`, `refreshing` (a Refresh is running) and `refresh_error` (why the last one wrote nothing); 404 `{"error", "refreshing", "refresh_error"}` when none |
| `POST /api/insight/today/vote` | admin session | body `{"vote": "up" \| "down"}`; answers `{"ok": true, "votes": [...]}` |
| `POST /api/insight/refresh` | admin session | starts writing it now in the background, even when the inputs didn't change, and answers 202 `{"status": "started" \| "running", "generated": <current insight's generated or null>}` at once (a model call outlasts the 45 s request timeout) |
| `GET /today` | session | the page |

`insight:read` is in `ALLOWED_SCOPES` and in Admin → Tokens. odysseus-sound's phone and PC
relays fetch it with the Odysseus token for the T5 feed (stage 0.5); give that token
`insight:read` next to `todos:read`.

## The Today window

**Today** (sidebar, icon rail, `/open today`, an optional shortcut) opens a tool window in
the app; `/today` shows the same view as its own page. It shows the headline, Do first,
Coming up and Also worth a look with each item's title and reason, in the style of the
built-in windows (`static/css/fork-modules.css`, shared with Conversations).

- To-do items have a tick box that uses the notes page's own route
  (`POST /api/notes/<id>/items/<index>/toggle`) and shows the tick at once. The page reads
  the note first and finds the line by text if lines have moved; a line that is gone
  shows "No longer on this list." The title opens the note (`/#open=notes&note=<id>`).
- Events link to the calendar on their day, opened by `static/js/forkEntityHash.js`:
  `/#event-<uid>` for a single event (highlighted), `/#event-<YYYY-MM-DD>` for an
  occurrence of a recurring series (the calendar would resolve its compound uid to the
  series' first day).
- 👍/👎 store a vote and show the latest one. **Refresh** starts a new insight and shows
  "Writing…" while it polls `GET /api/insight/today` every 4 s until `generated` changes
  (then shows it) or `refreshing` turns false (then shows `refresh_error`).

## In chat

"What should I do first today?" is answered from the stored insight, without a new tool:
while memory is on, `src/chat_processor.py` (`build_context_preface`) adds a short block
from `today_insight.context_block(owner, message=message)` next to the saved memories, as
untrusted context, **only when the message is about today's plan**
(`today_insight.wants_insight`): it names today, tonight, this morning/afternoon/evening,
first, priority/priorities, plan, focus, agenda or schedule, or says "what should I do",
"what's next", "what now", "what to do", "where do I start", or is a short "what do I do
(now)?". Slash commands and slash-invoked skills (`/sound`, `/context`) never get it.

Why: with the block on every turn, the sandbox model answered an action-item question
("what did the doctor say…") with "no information in the provided context" and called no
module tool (4 of 4 runs); without the block it called `list_action_items` and passed.

The block's first line says what it is for:

```
Today's plan, for questions about today, priorities or what to do first. For anything else use your tools as usual. (2026-10-21)
<headline>
Do first: 1. <title>: <why> 2. …
Coming up: 1. …
Also: 1. …
```

At most 800 characters. Nothing is added when there is no insight for today (or memory is
off, as in incognito).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `TODAY_INSIGHT_ENABLED` | `on` | `off` makes every run a no-op (the stored insight stays readable) |
| `TODAY_INSIGHT_SKIP_LISTS` | `Shopping,Groceries,Grocery` | list titles left out unless dated |
| `MODULE_ACTION_ITEMS_SERVER` | `convo` | the odysseus-sound MCP server |
| interval | every 15 min | the task's schedule in Tasks |

## Known limits

- Upstream cancels a running model-backed task on any non-passive request ("Stopped by
  user" in Activity), and `/api/codex/*` is not on its passive list. A relay fetching
  `GET /api/codex/insight/today` while the scheduled run is generating cancels that
  generation; the next 15-minute run does it again. Upstream is left unchanged for this.
  A Refresh started from the page is not a scheduled task and isn't cancelled that way.

## Measure

Tests on fictional data (`tests/test_today_insight.py`): buckets, shopping lists by note
type and due date, candidate order and cap, ref validation, the hash no-op, a fake model
(good JSON, garbage once, garbage twice, labels, bare ids, empty sections twice), the
memory-order-independent hash, the Refresh route's 202 and the no-wait flag, storage, votes, `context_block`, the token
route's scope, and the real database loaders. The insight on the owner's data and its
time on the server's model are part of the Server verification (stage 0.7).
