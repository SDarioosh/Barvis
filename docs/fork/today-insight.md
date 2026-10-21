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
| Calendar events from today 00:00 local to +7 days, not cancelled, not ended | the owner's calendars (`_checkin_calendar_events`, as the check-in uses): uid, summary, start, end, all-day, location, importance, event type |
| Open items of to-do notes | notes of type `checklist`, `todo`, `goal`, not archived; each item with its index, the note's title, pinned flag, due date and label. Calendar-reminder notes are left out (the event is already a candidate) |
| Memory | `MemoryManager(DATA_DIR).load(owner)`: entries of category `preference`, `contact`, `fact`, and every entry with `source: "odysseus-sound"`; at most 40, most used first, then newest |
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
  its bucket and has a ref: `todo:<note id>:<item index>` or `event:<uid>`.
- Input hash: SHA-256 over the candidate list, the memory texts, the digest text and the
  open action-item lines. The model runs only when there is no insight for today yet or
  the hash differs from the stored one; otherwise the run is a no-op. With no candidates
  at all, a fixed "nothing is due" insight is stored without the model.

### The model's part

The local model (the hub's background-task chain, `llm_call_async_with_fallback`,
thinking off) only picks refs and writes the reasons. It is asked for JSON only:

```json
{"headline": "1-2 sentences",
 "focus": [{"ref": "...", "why": "..."}],
 "coming_up": [{"ref": "...", "why": "..."}],
 "also": [{"ref": "...", "why": "..."}]}
```

The prompt: overdue and due-today first, but items not due today still count; use the
memory facts to personalise the reasons; keep reasons concrete. The answer is validated in
code: refs not in the candidate list are dropped, a ref is used once across sections, at
most 5 per section, `why` is cut to 80 characters, the headline to 300. Unparseable JSON
is retried once; if that fails too, the previous insight (if any) stays and the run is
logged as failed in Activity.

Recurring calendar events are taken as stored (not expanded), like the check-in digest.

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
| `GET /api/insight/today` | admin session (as `/modules/sound`) | the same plus `votes`; 404 when none |
| `POST /api/insight/today/vote` | admin session | body `{"vote": "up" \| "down"}`; answers `{"ok": true, "votes": [...]}` |
| `POST /api/insight/refresh` | admin session | writes it now, even when the inputs didn't change (the page's Refresh); 502 when the model's answer wasn't usable |
| `GET /today` | session | the page |

`insight:read` is in `ALLOWED_SCOPES` and in Admin → Tokens. odysseus-sound's phone and PC
relays fetch it with the Odysseus token for the T5 feed (stage 0.5); give that token
`insight:read` next to `todos:read`.

## The Today page

`/today` (sidebar: **Today**) shows the headline, Do first, Coming up and Also worth a
look with each item's title and reason, in the odysseus-sound page's style.

- To-do items have a tick box that uses the notes page's own route
  (`POST /api/notes/<id>/items/<index>/toggle`) and shows the tick at once. The page reads
  the note first and finds the line by text if lines have moved; a line that is gone
  shows "No longer on this list." The title opens the note (`/#open=notes&note=<id>`).
- Events link to the calendar on their day (`/#event-<uid>`, opened by
  `static/js/forkEntityHash.js`).
- 👍/👎 store a vote and show the latest one. **Refresh** rewrites the insight now.

## In chat

"What should I do first today?" is answered from the stored insight, without a new tool:
while memory is on, `src/chat_processor.py` (`build_context_preface`) adds a short block
from `today_insight.context_block(owner)` next to the saved memories, as untrusted
context: the headline, then "Do first: 1. <title>: <why> …", "Coming up: …", "Also: …",
at most 800 characters. Nothing is added when there is no insight for today (or memory is
off, as in incognito).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `TODAY_INSIGHT_ENABLED` | `on` | `off` makes every run a no-op (the stored insight stays readable) |
| `TODAY_INSIGHT_SKIP_LISTS` | `Shopping,Groceries,Grocery` | list titles left out unless dated |
| `MODULE_ACTION_ITEMS_SERVER` | `convo` | the odysseus-sound MCP server |
| interval | every 15 min | the task's schedule in Tasks |

## Measure

Tests on fictional data (`tests/test_today_insight.py`): buckets, shopping lists by note
type and due date, candidate order and cap, ref validation, the hash no-op, a fake model
(good JSON, garbage once, garbage twice), storage, votes, `context_block`, the token
route's scope, and the real database loaders. The insight on the owner's data and its
time on the server's model are part of the Server verification (stage 0.7).
