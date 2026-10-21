# Belief ledger: evidence in, confirmed beliefs out

Stages 1.1 (evidence intake) and 1.5 (expiry and decay) of
[personal-context.md](personal-context.md#phase-1-one-intake-a-review-page-and-forgetting).
Code: `src/evidence_intake.py` (the ledger) and `src/module_facts.py` (its first source);
tests `tests/test_evidence_intake.py` and `tests/test_module_facts.py`.

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
| `events` | `id`, `belief_id`, `at`, `kind` (`added`, `evidence`, `confirmed`, `stale`, `rejected`, `corrected`, `restored`, `retired_upstream`), `detail`. Only changes are logged (not every hourly re-submit), and only the newest 50 per belief are kept. |

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
rule or its source that no longer meets it (one of its two sources went away) goes back
to `tentative` and leaves memory.

## Submitting evidence (for a new source)

```python
from src import evidence_intake as ledger

counts = ledger.submit(owner, [
    ledger.Evidence(
        source="notes",              # the source's name, stable
        source_id="note-42:3",       # the source's own stable id for this observation
        subject="Jenn", about="person",   # owner | person | other
        text="Jenn lives in Lisbon",
        link="/notes#note-42",       # required: evidence without a link is skipped
        seen_first="2026-10-01", seen_last="2026-10-14",  # ISO dates or datetimes
        times=2, days=2,             # the source's totals for this observation
        source_confirmed=False,      # True only if the source has its own confirmation
        category=None,               # fact | preference | contact; None: from about + text
        payload={"note_id": 42},     # extra fields, kept as JSON
        valid_until=None,            # optional ISO date: the belief ends that day
    ),
], now=None)
# {"beliefs_added", "evidence_added", "evidence_updated", "confirmed", "unchanged", "skipped"}
ledger.reconcile_source(owner, "notes", present_ids={"note-42:3", ...})
```

- Each item is filed under the belief its (`owner`, `source`, `source_id`) already maps
  to; else under a belief of the same owner whose text (or the owner's correction) has the
  same **normalized** text (lowercase, whitespace collapsed, trailing punctuation
  dropped); else a new `tentative` belief.
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
the most recent evidence) and `evidence` (each: `id`, `source`, `source_id`, `subject`,
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

## Memory mirroring

`sync_memory(owner)` keeps exactly one Odysseus memory per confirmed belief:

- Text: `(owner_text or text) + " (source: <link>)"`, the link of the most recent
  evidence (chat context injects only a memory's text, so the link must be in it for
  answers to cite it). Category from the belief.
- Fields: `source` (the most recent evidence's source: `odysseus-sound`, `notes` …),
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
`sync_memory`. Result line, counts only: `facts: 12 seen, 1 new, 1 confirmed, 0 gone,
0 stale; memory: 11 kept, 1 added, 0 updated, 0 removed`; a pass with no change is not
logged in Activity.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `BELIEF_CONFIRM_SOURCES` | 2 | rule (b): distinct sources that confirm a belief |
| `BELIEF_CONFIRM_TIMES` | 3 | rule (c): sightings across sources |
| `BELIEF_CONFIRM_DAYS` | 2 | rule (c): distinct days across sources |
| `BELIEF_STALE_DAYS` | 180 | decay: days without a sighting before a belief confirmed by its source or the rule goes stale |
| `MODULE_FACTS_SYNC` | on | `off` stops the facts job (memories already mirrored stay) |

Values below 1 count as 1; a value that isn't a number falls back to the default.
