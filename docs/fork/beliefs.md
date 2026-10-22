# Belief ledger: evidence in, confirmed beliefs out

Stages 1.1 (evidence intake), 1.3 (the [review page](#review-page-13)), 1.4 (forgetting
propagates) and 1.5 (expiry and decay) of
[personal-context.md](personal-context.md#phase-1-one-intake-a-review-page-and-forgetting).
Code: `src/evidence_intake.py` (the ledger), `src/module_facts.py` (its first source) and
`src/notes_evidence.py` (the owner's notes, [Sources](#sources)); tests
`tests/test_evidence_intake.py`, `tests/test_module_facts.py` and
`tests/test_notes_evidence.py`.

Phase 0 copied odysseus-sound's confirmed facts straight into Odysseus memory. Now a
**belief ledger** sits between the sources and memory:

```
source (odysseus-sound, notes, chat …) ── Evidence ──▶ ledger (beliefs.db) ── confirmed ──▶ Odysseus memory
                                                        ▲
                                    owner: confirm / correct / forget / restore
```

- Every source submits **evidence**: what it observed, its own id for it, and a link to
  where it came from.
- The ledger keeps **beliefs**, each with its evidence, a status and a short event
  history.
- Only **confirmed** beliefs are mirrored into Odysseus memory, one memory per belief, so
  chat context, the Memory page and the Today insight see them.
- The owner's decisions (from the review page, stage 1.3) always win over the counting
  rule.

## Storage

`<DATA_DIR>/beliefs.db`: stdlib `sqlite3` in WAL mode, one file for all owners, created
on first use. No upstream model or database changes. Odysseus never opens a module's
database; the ledger holds only what sources submitted.

| Table | Columns |
|---|---|
| `beliefs` | `id`, `owner` (`''` without auth), `subject`, `about` (`owner`, `person`, `other`), `text`, `norm` (matching key), `category` (`fact`, `preference`, `contact`), `status` (`tentative`, `confirmed`, `stale`, `rejected`), `confirmed_by` (`source`, `owner`, `rule` or null), `first_seen`, `last_seen`, `valid_until` (ISO dates), `memory_id` (the mirrored memory), `created_at`, `updated_at` (UTC ISO), `owner_text` (the owner's correction; when set, memory shows it), `owner_norm`, `note` (e.g. the reason given when forgetting) |
| `evidence` | `id`, `belief_id`, `owner`, `source`, `source_id`, `subject`, `about`, `category`, `text` (what the source said), `link`, `seen_first`, `seen_last` (ISO dates), `times`, `days`, `source_confirmed` (0/1), `payload` (JSON of extra fields; `gone: true` and `gone_at` when the source stopped reporting it, `valid_until` when given), `updated_at`. Unique on (`belief_id`, `source`, `source_id`) and on (`owner`, `source`, `source_id`): one source id maps to one belief per owner. |
| `events` | `id`, `belief_id`, `at`, `kind` (`added`, `evidence`, `confirmed`, `stale`, `rejected`, `corrected`, `restored`, `retired_upstream` (a source stopped reporting it), `unconfirmed` (it no longer meets the rule)), `detail`. A ledger created before `unconfirmed` existed has its `events` table rebuilt with the wider CHECK on first open (rows and ids kept). Only changes are logged (not every hourly re-submit), and only the newest 50 per belief are kept. |

## Statuses and the confirmation rule

| Status | Meaning | In memory |
|---|---|---|
| `tentative` | Evidence, not yet enough to act on | no |
| `confirmed` | By its source (`source`), by the rule (`rule`) or by the owner (`owner`) | yes |
| `stale` | No source reports it any more, not seen for too long, or past its `valid_until` | no |
| `rejected` | The owner forgot it | no |

`confirmation_for(belief, evidence_rows, settings)` counts only **live** evidence (not
gone). A belief is confirmed when:

1. **(a)** any evidence has `source_confirmed`: the source's own threshold (odysseus-sound:
   said in ≥ 3 conversations on ≥ 2 days), or
2. **(b)** at least `BELIEF_CONFIRM_SOURCES` (2) distinct sources report it (a fact said in
   a conversation and written in a note confirms twice), or
3. **(c)** at least `BELIEF_CONFIRM_TIMES` (3) sightings summed across its evidence, on at
   least `BELIEF_CONFIRM_DAYS` (2) distinct days. Sources report totals, not every date,
   so distinct days are the larger of the biggest per-evidence `days` and the number of
   distinct first/last-seen dates across the evidence; this never over-counts.

The owner's decisions win and the rule never undoes them: an owner-confirmed belief stays
confirmed whatever the evidence does, and a rejected belief stays rejected even when new
evidence (from any source, even source-confirmed) arrives for it.

The rule is re-checked whenever a belief's evidence changes. A belief confirmed by the
rule or its source that no longer meets it (one of its two sources went away, or a
threshold was raised) goes back to `tentative` and leaves memory: event `unconfirmed`
("no longer meets the rule"), counted as `unconfirmed` by `submit` and
`reconcile_source`.

## Submitting evidence (for a new source)

```python
from src import evidence_intake as ledger

counts = ledger.submit(owner, [
    ledger.Evidence(
        source="notes",              # the source's name, stable
        source_id="note-42:3",       # the source's own stable id for this observation
        subject="Jenn", about="person",   # owner | person | other
        text="Jenn lives in Lisbon",
        link="/#open=notes&note=note-42",  # required: evidence without a link is skipped
        seen_first="2026-10-01", seen_last="2026-10-14",  # ISO dates or datetimes
        times=2, days=2,             # the source's totals for this observation
        source_confirmed=False,      # True only if the source has its own confirmation
        category=None,               # fact | preference | contact; None: from about + text
        payload={"note_id": 42},     # extra fields, kept as JSON
        valid_until=None,            # optional ISO date: the belief ends that day
    ),
], now=None)
# {"beliefs_added", "evidence_added", "evidence_updated", "confirmed", "unconfirmed",
#  "unchanged", "skipped"}
ledger.reconcile_source(owner, "notes", present_ids={"note-42:3", ...})
```

- Each item is filed under the belief its (`owner`, `source`, `source_id`) already maps
  to; else under a belief of the same owner whose text (or the owner's correction) has the
  same **normalized** text (lowercase, whitespace collapsed, trailing punctuation
  dropped); else a new `tentative` belief.
- For facts about the owner (`about="owner"`) the key also folds the owner's leading
  mention: `the owner`, `owner`, `me`, `i`, `i'm`, `i am` or the evidence's own
  `subject` (e.g. the owner's name, as the module sends it) becomes `owner`, a following
  `am`/`have` becomes `is`/`has`, and `my`, `the owner's` and `<name>'s` become
  `owner's`. So "The owner is allergic to peanuts." (a note), "Me is allergic to
  peanuts." (a conversation, subject `Me`) and "I am allergic to peanuts" are one belief.
  Facts about people and things keep the plain key. An owner correction is keyed with the
  belief's `about` and `subject`. Ledgers written before this are re-keyed once when
  opened (`PRAGMA user_version` 1).
- Re-submitting the same item changes nothing (`unchanged`); new totals or dates update
  the evidence; the belief's `first_seen`/`last_seen` are the min/max over its evidence.
- When a source changes the text behind an id: if that evidence is the belief's only one
  and the owner hasn't decided on it, the belief follows (memory is updated in place).
  Otherwise the evidence moves to the belief that matches the new text, or a new one.
  A changed `subject` or `about` (a renamed speaker) follows the same way.
- A source that lists everything it currently holds calls `reconcile_source` after
  `submit` with the ids it still has: the other ids are marked gone (event
  `retired_upstream`). A belief left without live evidence goes `stale` (and leaves
  memory) unless the owner confirmed it; owner-confirmed beliefs keep their status and
  show the evidence as gone. A gone id that is submitted again comes back.
- `submit`, `reconcile_source` and `apply_decay` mirror memory at the end; a job that
  calls several of them passes `mirror=False` and calls `sync_memory(owner)` once.
- Each new source gets its own on/off setting, off by default until tested (plan 1.2).

## Expiry and decay (1.5)

`apply_decay(owner, now=None, stale_days=None)` runs at the end of every facts-job pass:

- A belief confirmed by its source or the rule whose `last_seen` is older than
  `BELIEF_STALE_DAYS` (180) days goes `stale` (event `stale`, "not seen since …") and
  leaves memory. Tentative beliefs aren't demoted by age.
- Any tentative or confirmed belief whose `valid_until` has passed goes `stale`, owner
  confirmed or not (it is an explicit end date, not decay).
- Owner-confirmed beliefs never decay by age.
- A stale belief comes back only on new evidence (a newer `seen_last`, a gone id
  reported again) that is itself within the window; the same old evidence re-submitted
  every hour doesn't flap it.

## Owner actions (for the review page, 1.3)

| Call | Effect |
|---|---|
| `confirm(owner, id)` | `confirmed` by `owner`; in memory; never decays |
| `correct(owner, id, text)` | `owner_text = text`, `confirmed` by `owner`; memory shows the corrected text (same memory id and `uses`); evidence that says the corrected text later lands on this belief |
| `reject(owner, id, reason="")` | `rejected`, out of memory, `note = reason`; returns the evidence list so the caller can propagate the forget |
| `restore(owner, id)` | `rejected`/`stale` → `tentative`, then the rule (may confirm it again); to keep an old belief for good, confirm it |
| `list_beliefs(owner, status=None, subject=None, limit=500)` | beliefs with their evidence (`status`: one or a list; `subject` case-insensitive), by subject then most recently seen |
| `get_belief(owner, id)` | one belief with evidence and its 20 newest events |
| `stats(owner)` | counts per status and `total` |

A belief dict has `id`, `owner`, `subject`, `about`, `text`, `owner_text`, `shown_text`,
`category`, `status`, `confirmed_by`, `first_seen`, `last_seen` ("not heard since"),
`valid_until`, `memory_id`, `created_at`, `updated_at`, `note`, `link` and `source` (of
the citing evidence, see [Memory mirroring](#memory-mirroring)) and `evidence` (each: `id`, `source`, `source_id`, `subject`,
`about`, `category`, `text`, `link`, `seen_first`, `seen_last`, `times`, `days`,
`source_confirmed`, `gone`, `payload`, `updated_at`). Another owner's id raises
`BeliefNotFound` (a `LookupError`). Owner actions update memory right away; if the memory
store is unreadable the decision is still saved and the next facts pass mirrors it.

### Forgetting propagates (1.4)

`src.module_facts.propagate_forget(owner, belief_or_evidence, call=None)` calls the
module tool `retire_fact {"fact_id": <id>, "reason": "owner forgot it in Odysseus"}` for
each odysseus-sound evidence of a forgotten belief, through the connected module MCP
server (or `call`). It returns `{"retired", "failed", "unsupported"}`. A module without
`retire_fact` is tolerated (logged once per owner); the fact then keeps coming back from
the module, attaches to the rejected belief and stays out of memory. The review page
calls `reject`, then `propagate_forget` with what `reject` returned.

## Review page (1.3)

`/beliefs` ("What I know about you", sidebar: **Beliefs**, under Today) is where the
owner sees and decides. Code: `routes/beliefs_routes.py`, `static/beliefs.html`,
`static/js/beliefs.js`; tests `tests/test_beliefs_routes.py`. Same auth as the Today
page (admin session; API tokens are refused), and every call is scoped to the logged-in
owner: another owner's belief id answers 404.

| Route | Does |
|---|---|
| `GET /api/beliefs?status=&subject=` | `{"beliefs": [...], "stats": {...}}`. `status`: `tentative`, `confirmed`, `stale`, `rejected` or `all`; without it, all but rejected. `subject`: case-insensitive exact match. 400 for an unknown status. |
| `GET /api/beliefs/{id}` | the belief with its evidence and 20 newest events |
| `POST /api/beliefs/{id}/confirm` | `confirm`: confirmed by the owner (also a stale belief: restored and kept for good) |
| `POST /api/beliefs/{id}/correct` `{"text"}` | `correct`; 400 on empty text |
| `POST /api/beliefs/{id}/restore` | `restore`: back to tentative, then the rule |
| `POST /api/beliefs/{id}/forget` `{"reason"?}` | `reject`, then `propagate_forget`; answers `{"belief", "propagated": {"retired", "failed", "unsupported"}}` |
| `POST /api/beliefs/run` | the facts job for this owner now (`module_facts.sync`, at most 60 s); `{"summary": <counts line>, "stats"}`. 503 when the module isn't connected, 409 when `MODULE_FACTS_SYNC=off`, 502 on a failed call, 504 on timeout |

The page has a count per status, tabs Tentative / Confirmed / Stale / Rejected (the tab
is kept for the browser session), a subject filter, and the beliefs grouped by subject.
Each card shows the text (the owner's correction marked "edited"), category, a status
chip with how it was confirmed (by its source, by the rule, by you), first and last seen
("not heard since" for stale ones), and one line per evidence: source name, times ×
days, the dates and a link that opens in a new tab (odysseus-sound's are signed
transcript links). Gone evidence is marked "no longer reported".

| Status | Buttons |
|---|---|
| tentative | Confirm, Edit, Forget |
| confirmed | Edit, Forget |
| stale | Confirm (restores and keeps it), Forget |
| rejected | Restore |

Edit is an inline text field (Save / Cancel; Enter / Escape) that calls `correct`.
**Sync now** calls `/api/beliefs/run` and reloads.

**What Forget does.** After a confirmation prompt, the belief is rejected here first
(out of memory at once, and it stays rejected whatever arrives later). Then each
odysseus-sound fact behind it is retired in the module with `retire_fact`. The toast
reports the `propagated` counts: "Forgotten; the module retired 1 fact", or "Forgotten
here; the module could not be reached or cannot retire facts yet" (`unsupported`: the
module isn't connected or lacks `retire_fact`), or "could not retire it" (`failed`: the
call failed, timed out after 30 s, or raised). Forgetting never fails because of the
module: the local rejection always stands. A belief with no odysseus-sound evidence
makes no module call.

**The "No fact" case.** After a re-summary the module may no longer know the fact id
the ledger holds and answers `No fact #N.` That is a normal answer, not an error, so
`propagate_forget` counts it as `retired`: the fact is gone from the module anyway.
`Fact #N was already retired.` counts the same way.

## Memory mirroring

`sync_memory(owner)` keeps exactly one Odysseus memory per confirmed belief:

- Text: `(owner_text or text) + " (source: <link>)"` (chat context injects only a
  memory's text, so the link must be in it for answers to cite it). Category from the
  belief.
- The link (and the memory's `source`) come from one **citing evidence**
  (`primary_evidence`, ranked by `link_rank`): live evidence before gone evidence, then
  1. an absolute `http(s)://` link from `odysseus-sound` (the signed transcript link),
  2. any other absolute link,
  3. a relative link (a note's `/#open=notes&note=…`),

  and the newest (`seen_last`, then id) within the same rank. So a note that repeats a
  conversation fact confirms it but doesn't take over its citation: answers keep citing
  the transcript.
- Fields: `source` (the citing evidence's source: `odysseus-sound`, `notes` …),
  `belief_id`, `owner`, `subject`, `about`, `link`, `first_seen`, `last_seen`,
  `synced_at`. A belief with odysseus-sound evidence also keeps the Phase 0 fields
  `module_fact_id`, `times_said`, `days`, `first_said`, `last_said`.
- Updated in place (memory id and `uses` kept); copies of beliefs that are no longer
  confirmed, duplicates and copies of unknown beliefs are deleted; nothing is written
  when nothing changed, so a second run leaves `memory.json` byte-identical.
- A Phase 0 copy (`source: odysseus-sound`, `module_fact_id`, no `belief_id`) is adopted
  by the belief that has that fact id as evidence: same memory id and `uses`, no
  duplicate. Until the ledger has heard from the module at all, such copies are left
  alone.
- The owner's own memories and other owners' entries are never touched. Upstream's
  Memory Tidy may still rewrite or delete copies; the next run restores them.
- Same discipline as upstream's memory writers: a strict read (`load_all_for_update`,
  never saving over a store it couldn't read) and the save right after, under the
  ledger's lock.

## The odysseus-sound facts job

The built-in task **Module Facts** (hourly, silent) now calls `list_facts {"format":
"json", "limit": 1000}` (every current fact, not only confirmed ones) and submits each as
evidence from `odysseus-sound` with the fact id as `source_id`, `first_said`/`last_said`
as seen dates, `times_said`/`days` as totals and the row's `confirmed` as
`source_confirmed`. A module that doesn't send `confirmed` per row yet gets a second call
`{"confirmed": true, "format": "json", "limit": 1000}`, whose ids are the confirmed set.
Then `reconcile_source(owner, "odysseus-sound", ids)`, `apply_decay` and one
`sync_memory`. Result line, counts only: `facts: 12 seen, 1 new, 1 confirmed,
0 unconfirmed, 0 gone, 0 stale; memory: 11 kept, 1 added, 0 updated, 0 removed`; a pass with no change is not
logged in Activity.

## Sources

| Source | `source` | Task | Setting | Default |
|---|---|---|---|---|
| odysseus-sound facts | `odysseus-sound` | Module Facts (hourly) | `MODULE_FACTS_SYNC` | on |
| The owner's notes | `notes` | Notes Evidence (daily, 03:00) | `EVIDENCE_NOTES` | off |

### Notes (1.2)

Code: `src/notes_evidence.py`; test `tests/test_notes_evidence.py`. The built-in task
**Notes Evidence** (daily, silent, queued with the other model-backed tasks) reads the
owner's notes and asks the local model, one call per note, for the durable facts each one
states.

- **Read:** the owner's notes of type `note` (plain notes; not checklists, to-dos or
  goals) that aren't archived.
- **Skipped:** notes labelled `odysseus-sound` (filed from voice notes, so their facts
  already come through the module), calendar reminders (source and label `calendar`),
  and notes whose title and text together are shorter than 40 characters.
- **Only changed notes go to the model.** The state file
  `<DATA_DIR>/notes_evidence_<owner>.json` keeps, per note, a hash of its title and text
  (and of the prompt version) and the evidence ids it produced. A note whose hash is
  unchanged isn't sent again; pinning or recolouring a note doesn't count as a change.
  When the model's answer isn't usable JSON (after one retry) the note keeps its
  previous facts and is tried again on the next run.
- **The model** (background workload, thinking off, waits for chat to go quiet) gets
  the note as data, never as instructions, and answers `{"facts": [{"subject", "about"
  (owner, person, other), "fact"}]}`: facts that stay true for months, each one sentence
  that names its subject (`The owner …` for the owner); none from instructions, to-do or
  shopping lists, code or text quoted from someone else; at most 8 per note. Rows
  without a subject or fact are dropped; a fact is cut at 300 characters.
- **Evidence:** `source="notes"`, `source_id="<note id>:<index>"`, link
  `/#open=notes&note=<note id>` (the hash `static/js/forkEntityHash.js` opens on load),
  `seen_first = seen_last` = the date of the note's last edit, `times = days = 1`,
  `source_confirmed = False`.
- **Never source-confirmed.** A note is one sighting written by the owner, with no
  repetition to count. A fact that is also said in a conversation confirms by rule (b)
  (two sources); anything else stays tentative until the owner confirms it on the review
  page.
- **Gone:** after each pass `reconcile_source(owner, "notes", ids)` with the ids of every
  note still read (re-extracted or unchanged), so facts a changed note no longer states,
  and the facts of notes deleted, archived, relabelled or shortened, are marked gone.
- Result line, counts only: `notes: 2 read, 3 skipped, 14 unchanged, 5 facts; ledger:
  3 new, 1 confirmed, 1 gone, 1 stale` (`, 1 failed` when an answer wasn't usable); a
  pass that read nothing and changed nothing is not logged in Activity.
- **Off by default** (`EVIDENCE_NOTES=off`; `on` enables it): notes hold drafts, plans
  and other people's words, so the source stays off until it has been checked on real
  notes and the owner reviews tentative beliefs on the review page (plan 1.3 before 1.2).
  Turning it off stops the task; evidence already submitted stays in the ledger.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `BELIEF_CONFIRM_SOURCES` | 2 | rule (b): distinct sources that confirm a belief |
| `BELIEF_CONFIRM_TIMES` | 3 | rule (c): sightings across sources |
| `BELIEF_CONFIRM_DAYS` | 2 | rule (c): distinct days across sources |
| `BELIEF_STALE_DAYS` | 180 | decay: days without a sighting before a belief confirmed by its source or the rule goes stale |
| `MODULE_FACTS_SYNC` | on | `off` stops the facts job (memories already mirrored stay) |
| `EVIDENCE_NOTES` | off | `on` lets the Notes Evidence task submit facts from the owner's plain notes |

Values below 1 count as 1; a value that isn't a number falls back to the default.

## Measure (1.6)

`scripts/belief_eval.py` is the memory eval: a fictional history seeded through the
ledger's public API (`submit`, `reconcile_source`, `apply_decay`, `confirm`, `reject`,
`correct`, `restore`, `list_beliefs`, `sync_memory`) with every `now` given, so a run is
deterministic. No model is involved; `tests/test_belief_eval.py` runs the same checks
through pytest.

```bash
venv/bin/python scripts/belief_eval.py                     # temp dir, removed after
venv/bin/python scripts/belief_eval.py --data-dir /tmp/belief-eval --keep
```

- The history: one owner (Sam Rivera), three people (Priya Nair, Tomas Berg, Lena Okafor)
  and Project Heron, from March to October 2026. `odysseus-sound` is simulated the way the
  facts job drives it (each pass: submit everything the module lists, `reconcile_source`,
  `apply_decay`, one `sync_memory`); `notes` adds single sightings.
- Cases: said once (tentative), source-confirmed (3 times on 2 days), two sources
  (conversation + note), gone upstream (stale on reconcile), forgotten (rejected; the
  same fact under the same and a new module id stays rejected), corrected (memory shows
  the correction in the same memory; the original and the corrected text both join the
  belief), `valid_until` passed, decay (confirmed at day 179, stale at day 195, no flap
  when the module repeats the old sighting), text variant (casing, spaces, punctuation
  join; then 3 sightings on 3 days confirm by the rule), owner-confirmed (stays confirmed
  when the module drops it and past 180 days), restored after a mistaken forget, a
  two-source belief losing one source (back to tentative), gone then seen again, two
  notes on one day, one note, a preference category, and what the ledger knows about
  Priya Nair.
- Memory: exactly the expected confirmed beliefs are mirrored, one memory each, as
  `<shown text> (source: <link>)`; no memory keeps a corrected-away text; the owner's own
  memory is untouched; a second `sync_memory` writes nothing (`memory.json`
  byte-identical).
- Output: one row per check (`case`, `step`, `expected`, `actual`, `ok`/`FAIL`, with
  `status/confirmed_by/mem|out` for beliefs), then `cases: 18/18   checks: score 74/74`
  and the details of any failure. Exit code 1 on any mismatch, 2 when the data dir already
  holds this eval's beliefs or evidence ids for the owner (run `--cleanup` first).
- `--data-dir` (default: a temp dir) and `--owner` (default `eval-owner`) choose where and
  for whom to seed; an explicit `--data-dir` is kept, `--keep` keeps the temp dir. To look
  at the seeded ledger on the review page, seed the owner you log in as (`--owner ''`
  without auth) into a scratch dir and start Odysseus on it with
  `ODYSSEUS_DATA_DIR=/tmp/belief-eval`.

`--chat` (optional, not scored, the only non-deterministic part) then asks a running
Odysseus "What do you know about Priya Nair?" with odysseus-sound's bakeoff client, and
prints whether the answer has each expected fact (peanut allergy, the corrected
birthday, the design review) and its link, and whether it still gives the old birthday.
Dates match in either order and any case ("21 May", "21st of May", "May 21st",
"2026-05-21"); a module link counts when its path (`/view/<id>`) is cited, whatever the
query string, and a note link when its `#…` id is:

```bash
ODYSSEUS_URL=http://127.0.0.1:7010 ODYSSEUS_PASSWORD_FILE=$SBX/admin.txt \
  venv/bin/python scripts/belief_eval.py --chat --owner admin \
  --data-dir <the test instance's data dir> --bakeoff $SOUND/server/eval/bakeoff.py
```

It seeds that instance's `beliefs.db` and `memory.json` (sandbox or the test instance on
`127.0.0.1:7010`, never production). Other module evidence of that owner is left alone,
but the instance's own Module Facts pass retires the eval's module facts (ids 9001+), so
ask right after seeding or set `MODULE_FACTS_SYNC=off` on that instance.

### Cleanup

Each run writes a manifest, `belief_eval.<owner>.json` in the data dir: the ids of the
beliefs it created and of the own memory it seeded ("Sam Rivera keeps the spare bike
key …"). A kept dir prints its cleanup command at the end (`--chat` always does):

```bash
venv/bin/python scripts/belief_eval.py --cleanup --data-dir <dir> --owner admin
# cleanup: 16 beliefs, 0 kept, 0 missing, 8 memories, 1 own_memory
```

It deletes exactly those beliefs (with their evidence and events), then runs
`sync_memory` so their memory copies go, then deletes that one own memory, then the
manifest. A recorded belief that another source now backs (evidence that isn't the eval's
module ids 9001–9099 or `belief-eval-note-*` notes) is kept and counted as `kept`. Other
beliefs, other owners and the owner's other memories are never touched. Without a
manifest it exits 2. After a cleanup the same owner can be seeded again.
