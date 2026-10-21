# Belief ledger: evidence in, confirmed beliefs out

Stages 1.1 (evidence intake), 1.3 (the [review page](#review-page-13)), 1.4 (forgetting
propagates) and 1.5 (expiry and decay) of
[personal-context.md](personal-context.md#phase-1-one-intake-a-review-page-and-forgetting),
and, of Phase 2, the hub side of 2.1 ([subjects and contacts](#subjects-and-contacts-21)),
2.2 ([structure](#structure-22)) and 2.4 ([projects](#projects-24)).
Code: `src/evidence_intake.py` (the ledger), `src/module_facts.py` (its first source),
`src/notes_evidence.py` (the owner's notes, [Sources](#sources)) and
`src/belief_structure.py` (the Belief Structure task); tests
`tests/test_evidence_intake.py`, `tests/test_module_facts.py`,
`tests/test_notes_evidence.py`, `tests/test_subjects.py` and
`tests/test_belief_structure.py`.

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
| `beliefs` | `id`, `owner` (`''` without auth), `subject` (the name the source used), `about` (`owner`, `person`, `other`), `text`, `norm` (matching key), `category` (`fact`, `preference`, `contact`), `status` (`tentative`, `confirmed`, `stale`, `rejected`), `confirmed_by` (`source`, `owner`, `rule` or null), `first_seen`, `last_seen`, `valid_until` (ISO dates), `memory_id` (the mirrored memory), `created_at`, `updated_at` (UTC ISO), `owner_text` (the owner's correction; when set, memory shows it), `owner_norm`, `note` (e.g. the reason given when forgetting); Phase 2: `subject_id` (the [subject](#subjects-and-contacts-21)), `attribute`, `value`, `valid_from`, `structured_at` ([structure](#structure-22)) |
| `evidence` | `id`, `belief_id`, `owner`, `source`, `source_id`, `subject`, `about`, `category`, `text` (what the source said), `link`, `seen_first`, `seen_last` (ISO dates), `times`, `days`, `source_confirmed` (0/1), `payload` (JSON of extra fields; `gone: true` and `gone_at` when the source stopped reporting it, `valid_until` when given), `updated_at`. Unique on (`belief_id`, `source`, `source_id`) and on (`owner`, `source`, `source_id`): one source id maps to one belief per owner. |
| `events` | `id`, `belief_id`, `at`, `kind` (`added`, `evidence`, `confirmed`, `stale`, `rejected`, `corrected`, `restored`, `retired_upstream` (a source stopped reporting it), `unconfirmed` (it no longer meets the rule)), `detail`. A ledger created before `unconfirmed` existed has its `events` table rebuilt with the wider CHECK on first open (rows and ids kept). Only changes are logged (not every hourly re-submit), and only the newest 50 per belief are kept. |
| `subjects` | see [Subjects and contacts](#subjects-and-contacts-21) |
| `subject_sources` | see [Subjects and contacts](#subjects-and-contacts-21) |

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
        person_id=None,              # optional: the source's stable id of the person
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
| `correct(owner, id, text)` | `owner_text = text`, `confirmed` by `owner`; memory shows the corrected text (same memory id and `uses`); evidence that says the corrected text later lands on this belief; clears `structured_at`, so the Belief Structure task structures it again |
| `reject(owner, id, reason="")` | `rejected`, out of memory, `note = reason`; returns the evidence list so the caller can propagate the forget |
| `restore(owner, id)` | `rejected`/`stale` → `tentative`, then the rule (may confirm it again); to keep an old belief for good, confirm it |
| `list_beliefs(owner, status=None, subject=None, limit=500, *, subject_id=None, attribute=None)` | beliefs with their evidence (`status`: one or a list; `subject` case-insensitive on the source's name; `subject_id` one subject; `attribute` one of the [vocabulary](#structure-22)), by subject then most recently seen |
| `get_belief(owner, id)` | one belief with evidence and its 20 newest events |
| `stats(owner)` | counts per status, `total`, and `structured` (beliefs with `structured_at`) |

A belief dict has `id`, `owner`, `subject`, `about`, `text`, `owner_text`, `shown_text`,
`category`, `status`, `confirmed_by`, `first_seen`, `last_seen` ("not heard since"),
`valid_until`, `memory_id`, `created_at`, `updated_at`, `note`, `subject_id`,
`subject_display`, `subject_kind`, `contact_id` (of its subject), `attribute`, `value`,
`valid_from`, `structured_at`, `link` and `source` (of
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
| `GET /api/beliefs?status=&subject=&attribute=` | `{"beliefs": [...], "stats": {...}}`. `status`: `tentative`, `confirmed`, `stale`, `rejected` or `all`; without it, all but rejected. `subject`: case-insensitive exact match. `attribute`: one of the [attribute vocabulary](#structure-22) (structured beliefs only). 400 for an unknown status or attribute. |
| `GET /api/beliefs/{id}` | the belief with its evidence and 20 newest events |
| `POST /api/beliefs/{id}/confirm` | `confirm`: confirmed by the owner (also a stale belief: restored and kept for good) |
| `POST /api/beliefs/{id}/correct` `{"text"}` | `correct`; 400 on empty text |
| `POST /api/beliefs/{id}/restore` | `restore`: back to tentative, then the rule |
| `POST /api/beliefs/{id}/forget` `{"reason"?}` | `reject`, then `propagate_forget`; answers `{"belief", "propagated": {"retired", "failed", "unsupported"}}` |
| `GET /api/beliefs/subjects?kind=` | `{"subjects": [...], "stats"}` (`list_subjects`: counts per status, aliases, source person ids); `kind`: `person`, `project`, `thing` or `owner`; 400 for another kind |
| `GET /api/beliefs/subjects/{id}` | `{"subject", "beliefs", "summary", "stats"}`: the subject, its beliefs of every status but rejected (with `attribute`, `value`, `valid_from`) and `subject_summary` |
| `POST /api/beliefs/subjects/{id}/kind` `{"kind"}` | `set_subject_kind`: `person`, `project` or `thing` |
| `POST /api/beliefs/subjects/{id}/rename` `{"display"}` | `rename_subject` |
| `POST /api/beliefs/subjects/{id}/link` `{"contact_id"}` | `link_contact`; the id must be one of the owner's contacts (else 400) |
| `POST /api/beliefs/subjects/{id}/unlink` | `unlink_contact` |
| `POST /api/beliefs/subjects/merge` `{"keep_id", "drop_id"}` | `merge_subjects`; answers the kept subject |
| `GET /api/beliefs/contacts` | the owner's Odysseus contacts for the link picker, `{"contacts": [{"id", "name", "subject_id"}]}` by name (`subject_id`: the subject the contact is linked to, or null). Read with the contacts routes' own listing (`routes/contacts`: the local book or CardDAV, owner-scoped the same way) |
| `GET /api/beliefs/contact/{contact_id}?status=` | `{"contact_id", "beliefs"}` (`beliefs_for_contact`; `status` as for `/api/beliefs`). For a later contact-card integration; no upstream UI uses it yet |
| `POST /api/beliefs/run` | the facts job for this owner now (`module_facts.sync`, at most 60 s); `{"summary": <counts line>, "stats"}`. 503 when the module isn't connected, 409 when `MODULE_FACTS_SYNC=off`, 502 on a failed call, 504 on timeout |

Subject routes answer 400 with the ledger's message for invalid input (a name another
subject has, a contact already linked to another subject, the owner subject's kind,
dropping the owner subject, merging a subject into itself) and 404 for an unknown or
another owner's subject. Belief ids are `int` path parameters (`{belief_id:int}`), so a
literal path another router adds under `/api/beliefs/` (such as `/search`) still reaches
that router.

The page has a count per status, a search box, a **By status / By subject** toggle (kept
for the browser session), and in By status tabs Tentative / Confirmed / Stale / Rejected
(the tab is kept for the browser session), a subject filter, an attribute filter (the
vocabulary of [Structure](#structure-22)) and the beliefs grouped by subject (the
subject's name opens it).
Each card shows the text (the owner's correction marked "edited"), its subject's kind
icon, a status chip with how it was confirmed (by its source, by the rule, by you), the
attribute and value when structured ("Lives in: Lisbon (since …)"), category, first and
last seen
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

**By subject** (plan stage 2.1) lists the subjects (the owner first, then by name), with a
name filter and a kind filter. Each card shows the name, a kind chip (You, Person,
Project, Thing), the aliases, the counts per status, the linked contact's name and the
names the sources use, and these buttons:

| Button | Does |
|---|---|
| Open | the subject: its summary paragraph (`subject_summary`; "no summary" while nothing is confirmed) and its beliefs (all but rejected) grouped by attribute in vocabulary order, each group under a label chip ("Lives in", "Works at" …), unstructured ones under "Other"; every belief card has its usual buttons |
| Kind (dropdown) | Person, Project or Thing (`kind`); not on the owner subject |
| Rename | inline field; the old name stays an alias; a name another subject has is refused (merge instead) |
| Link contact / Change contact | a picker over `/api/beliefs/contacts` with a search box; a contact already linked to another subject is shown with that subject's name and can't be picked (merge the two instead) |
| Unlink | removes the contact link |
| Merge into… | pick the subject this one is the same as, confirm; its beliefs, names and person ids move there and it is removed (not on the owner subject; it can be the target) |

**What linking does.** The subject gets the contact's `uid` (`contact_id`); memory
mirrors right away, so every confirmed belief about the subject carries `contact_id` in
its memory entry (text unchanged), and `beliefs_for_contact` (`GET
/api/beliefs/contact/{id}`) answers for that contact. Contact cards in Odysseus don't show
beliefs yet (no upstream UI change).

**Search.** The box at the top calls `GET /api/beliefs/search?q=…&status=all` (the
ranked search of stage 2.3, `routes/belief_search_routes.py`) and shows the hits as
belief cards with their `why`. While that route isn't installed (404) the page falls back
to a plain text match over the beliefs (every word in the text, the subject or the
structured value) and says so.

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

## Subjects and contacts (2.1)

Hub side of plan stage 2.1. Every belief belongs to one **subject**: the owner, a person, a
project or a thing. Names are no longer matched by string alone: a module's stable person
id maps to one subject, whatever name the module uses for that person today.

| Table | Columns |
|---|---|
| `subjects` | `id`, `owner`, `key` (normalized name: lowercase, whitespace collapsed, quotes and trailing punctuation dropped), `display`, `kind` (`person`, `project`, `thing`, `owner`), `kind_by` (`auto` or `owner`: who set the kind), `contact_id` (an Odysseus contact's `uid`, or null), `aliases` (JSON list of other names), `note`, `created_at`, `updated_at`. Unique on (`owner`, `key`). |
| `subject_sources` | `subject_id`, `owner`, `source` (e.g. `odysseus-sound`), `source_person_id` (the source's stable person id), `name` (the name the source gave last), `updated_at`. Unique on (`source`, `source_person_id`, `owner`): one person id maps to one subject per owner. |

**Resolution** (`submit`, for each new belief and whenever the source changes an item's
subject or `about`):

1. `about="owner"` → the owner's one subject of kind `owner` (created on first use, named
   "Owner" unless the first name it hears is a real name; `Me`, `I`, `Owner`, `the owner`
   are not names). A real name the source uses for the owner becomes an alias.
2. `Evidence.person_id` already mapped in `subject_sources` → that subject (a new name for
   it becomes an alias).
3. A subject whose key or one of whose aliases matches the name (case-insensitive) → that
   subject.
4. Else a new subject: `person` for `about="person"`, `project` when the name matches
   [the project words](#projects-24), else `thing`. Evidence without a subject goes to
   the subject "General".

A person id seen for the first time is mapped to the subject found. Evidence with a person
id that joins an existing belief (same text) maps the id to that belief's subject. The
belief's `subject` column keeps the source's name; `subject_id` is the subject.

**Migration.** A ledger from before Phase 2 (`PRAGMA user_version` < 2) gets the new
columns on first open, and every belief is filed under a subject from its `subject` and
`about` (steps 1, 3, 4 above). Lossless: no text, status or evidence changes.

**People from odysseus-sound.** The facts job calls `list_people {"format": "json"}`
(`{"people": [{"id", "name", "aliases", "is_owner", …}]}` or a bare list) and
`sync_people(owner, "odysseus-sound", people)`:

- `is_owner` → the owner subject (an owner subject still called "Owner" takes the real
  name; "Owner" stays an alias); the mapping is moved there if it pointed elsewhere.
- Else the subject the id maps to, else one whose name or alias matches the person's
  name or aliases, else a new `person`. A matched inferred `thing` or `project` becomes a
  `person`.
- Names and aliases the subject doesn't have become aliases.
- A person the module no longer lists (merged or deleted there) keeps its mapping: nothing
  is deleted.

**Functions** (all scoped to the owner; another owner's id raises `SubjectNotFound`, a
`LookupError`; invalid input raises `ValueError`):

| Call | Effect |
|---|---|
| `list_subjects(owner, kind=None)` | subjects (the owner first, then by name), each with `counts` (per status and `total`), `aliases` and `sources` (`[{"source", "person_id", "name"}]`) |
| `get_subject(owner, id)` | one subject, same shape |
| `find_subject(owner, name)` | the subject whose name or alias matches, or None |
| `set_subject_kind(owner, id, kind)` | `person`, `project` or `thing`; `kind_by = owner`, so inference never changes it again; the owner subject keeps its kind |
| `link_contact(owner, id, contact_id)` / `unlink_contact(owner, id)` | link a subject to an Odysseus contact (`uid` from `routes/contacts`); one contact links to one subject (a second link raises: merge the subjects) |
| `merge_subjects(owner, keep_id, drop_id)` | beliefs and person ids move to `keep_id`; the dropped name and its aliases join the aliases; its contact link is kept when the kept subject has none, and its kind when the kept one is an inferred `thing`; the owner subject can be kept, never dropped |
| `rename_subject(owner, id, display)` | new name; the old one becomes an alias; a name another subject has raises (merge instead) |
| `beliefs_for_contact(owner, contact_id, status=None)` | the beliefs of the subject linked to that contact (empty when none is) |
| `sync_people(owner, source, people, now=None, mirror=True)` | see above; counts `people`, `subjects_added`, `mapped`, `aliases_added` |
| `subject_summary(owner, id)` | one paragraph from the subject's confirmed beliefs grouped by attribute, e.g. `Priya Nair (person). lives in: Porto (since 2025-03-01). allergy: peanuts. Also: Priya ran the design review.`; empty when nothing is confirmed. For the insight and chat (2.3). |

Subject changes that affect memory (kind, contact, merge) mirror memory right away.

## Structure (2.2)

Confirmed beliefs get optional structure next to their text: `attribute`, `value`,
`valid_from` (a date) and `structured_at` (when it was written). The text stays what
memory shows; structure is for consumers that look beliefs up
(`beliefs_by_attribute(owner, attribute, subject_id=None, *, status="confirmed")`,
`subject_summary`).

**Attribute vocabulary** (`evidence_intake.ATTRIBUTES`); anything else becomes `other`:

| Attribute | For |
|---|---|
| `lives_in` | a home town or address |
| `works_at` | an employer |
| `role` | a job title or role |
| `birthday` | a birthday |
| `allergy` | an allergy |
| `diet` | a diet (vegetarian, no alcohol …) |
| `likes` / `dislikes` | preferences |
| `relationship` | how a person relates to the owner or someone else |
| `owns` | a thing someone owns |
| `pet` | a pet |
| `plans` | a stated plan |
| `decision` | a decision taken |
| `contact_info` | a phone number, email or handle |
| `health` | a health fact |
| `project_status` | where a project stands |
| `deadline` | a due date |
| `other` | none of the above |

Only attributes a consumer uses are added (plan risk: over-modelling).

**The Belief Structure task** (`src/belief_structure.py`; built-in, hourly at :15 after
the facts job; posts nothing to chat; queued with the other model-backed tasks):

- Takes up to 40 **confirmed** beliefs per run with `structured_at` empty
  (`structure_candidates(owner, limit=40)`), oldest first. A correction (`correct`) or a
  source edit of the belief's text clears `structured_at`, so the belief is structured
  again; the old structure stays until then.
- Sends up to 10 per call to the local model (background workload, thinking off, waits
  for chat to go quiet): id, subject name, subject kind and text, as data, never as
  instructions. The answer: `{"items": [{"id", "attribute", "value", "valid_from",
  "valid_to"}]}`; one retry on unusable JSON, then the batch waits for the next run.
- Validation (`apply_structure(owner, id, attribute, value, valid_from=None,
  valid_to=None, *, expected_text=None)`): unknown attribute → `other`; value cut at 120
  characters; dates must be whole ISO dates; ids outside the batch are dropped. A
  `valid_to` becomes `valid_until` only when the belief has none yet (then expiry applies
  as usual). A belief whose text changed while the model answered, or that is no longer
  confirmed, is left for the next run.
- Result line, counts only: `structure: 12 to do, 12 structured (2 other), 0 waiting`
  (`, 1 failed` when a batch had no usable answer). Nothing to do: no model call, not
  logged in Activity. `BELIEF_STRUCTURE=off` stops it.

## Projects (2.4)

A subject of kind `project` groups everything known about a project. It becomes one:

- by the owner: `set_subject_kind(owner, id, "project")` (the review page's Kind dropdown);
- at creation, when it isn't a person (`about` isn't `person`, no source person id) and its
  name matches `\b(project|launch|release|migration|plan)\b` (case-insensitive):
  "Project Heron", "Spring launch";
- after structuring, when one of its beliefs gets `project_status` or `deadline` and the
  subject is still an inferred `thing` that no source maps to a person.

Inference never overrides the owner (`kind_by = owner`) and never turns a person into a
project. `subject_summary` gives the insight and chat one paragraph per project (used by
2.3, retrieval).

## Retrieval (2.3)

One ranker over the ledger, used by the Today insight, chat and the review page. Code:
`src/belief_search.py`, `routes/belief_search_routes.py`; test
`tests/test_belief_search.py`.

```python
from src import belief_search
hits = belief_search.search_beliefs(owner, "where does Tom live", k=10,
                                    status=("confirmed",), subject_id=None,
                                    attribute=None, kinds=None)
# each hit: the belief dict (as list_beliefs returns it) + "score" (0..1) + "why"
```

**Ranking.** The score adds four parts; each weight is larger than the sum of the ones
after it, so the order of the parts is also the order of importance:

| Part | Weight | When |
|---|---|---|
| subject | 0.55 | the query names the belief's subject: its name or an alias (case-insensitive, whole words, `'s` allowed), or a person's first name when no other subject has it; "I", "me", "my", "myself", "mine" mean the owner's subject when the query names no one else |
| attribute | 0.25 | the query uses a word of the belief's `attribute` (synonym map below); an unstructured belief whose text has such a word gets 60 % of it |
| semantic | 0.12 | cosine similarity of the query to `"<subject>: <shown text>"`, scaled from `MIN_SIMILARITY` (0.5, adds nothing below) to 1; only with a local embedding lane |
| keyword | 0.08 | token Jaccard of the query and the same text (lowercase words, stop words dropped): the fallback and tie-breaker |

Only beliefs that score above 0 are returned. Ties: `last_seen` (newest first), then id;
the same call always gives the same order. `why` names the parts that scored, in that
order ("subject match, attribute, keyword"), plus "; no embeddings" when no lane was used.
`status` is one status, several, `"all"` or None; `subject_id`, `attribute` and `kinds`
(subject kinds) narrow the pool. `search_details(...)` returns the same hits with
`semantic` (a lane was used), `embedded` and `pending` (see below).

**Synonym map** (`SYNONYMS`, query word → attribute):

| Words | Attribute |
|---|---|
| allergy, allergies, allergic | `allergy` |
| live, lives, living, home, address, based | `lives_in` |
| work, works, working, job, employer, employed, company | `works_at` |
| role, title, position | `role` |
| birthday, birthdays, born | `birthday` |
| diet, vegetarian, vegan, pescatarian, eat, eats, alcohol | `diet` |
| like, likes, love, loves, favourite, favorite, enjoy, enjoys | `likes` |
| dislike, dislikes, hate, hates | `dislikes` |
| wife, husband, partner, sister, brother, mother, father, son, daughter, friend, colleague, boss, manager, married | `relationship` |
| own, owns | `owns` |
| pet, pets, dog, dogs, cat, cats | `pet` |
| plan, plans, planning | `plans` |
| decided, decision, decisions | `decision` |
| phone, email, number, handle | `contact_info` |
| health, medication, condition | `health` |
| status, progress | `project_status` |
| deadline, due | `deadline` |

**Embeddings.** Only the hub's own lane, never a remote API: `belief_search.embedder()`
takes the memory index Odysseus loaded at start-up (`MemoryVectorStore`, whose lanes come
from `embedding_lanes.build_embedding_lanes`) and uses its first healthy lane that is
local (FastEmbed, or a configured endpoint on loopback, a private or Tailscale address,
a container name or a `.local` host). No index, an unhealthy one, or only a remote lane:
no semantic part. A lane that fails during a search is logged and the search answers
from the other parts.

**Vector cache.** `belief_vectors` in `beliefs.db`: `belief_id` (primary key, deleted with
the belief), `text_hash` (of the embedded text), `model` (lane name and fingerprint),
`vector` (float32 BLOB), `updated_at`. A search embeds, lazily and in batches of 50, the
beliefs of its pool whose vector is missing or whose text or model changed, at most
`EMBED_PER_CALL` (200) per call, lowest ids first; the others are counted as `pending`
and done on the next call, so a request never waits on more than 200. A second search
over unchanged beliefs embeds only the query. `reindex(owner)` drops the owner's vectors
(and those of deleted beliefs) and recomputes all of them (tentative, confirmed, stale)
without the cap.

**Prompt block.** `context_for(owner, query, k=8, max_chars=1200)`: the top confirmed
hits as

```
What I know (confirmed). Use your tools for anything else, and cite the link next to each fact you use:
- Priya Nair: Priya Nair lives in Porto (http://…/view/…?sig=…#t=20)
- Sam Rivera (you): I am allergic to penicillin (http://…)
```

Lines that don't fit `max_chars` are skipped; "" when nothing matches.

**Where it is used.**

- *Today insight*: `select_memory` gets the query `memory_query(candidates)` (the day's
  candidate titles, list names and locations, one per line) and puts the ranker's top
  40 confirmed beliefs first (texts without links), then the other memory entries most
  used first, up to 40. With an empty ledger or no hit, the old selection (most used
  first). The input hash still uses the sorted texts, so a reorder alone doesn't rerun
  the model.
- *Chat*: the existing memory-context hook (`build_context_preface` →
  `today_insight.context_block`, label "saved insight: today and beliefs") adds
  `belief_search.chat_block(owner, message)` as untrusted context when
  `wants_beliefs(message, owner)`: "what do you know about / tell me about / who is
  <known subject>", or an attribute question about a known subject or the owner ("where
  does Tom live", "am I allergic to anything", "when is Mom's birthday?", "does Jonas
  have any allergies", "what's the status of Project Heron"). Known subjects: the
  owner's subjects with a confirmed belief. Never on slash turns or slash-invoked skills,
  never with words about what was said or happened (said, told, asked, mentioned,
  promised, recommended, latest, a meeting, a conversation, a voice note, a month or
  weekday, last week …): those questions must reach the module's tools, so none of
  odysseus-sound's 21 bakeoff questions gets the block (tested). "What is <person>
  allergic to?" is such a bakeoff question and is left to the module; the yes/no form
  ("Is Priya allergic to nuts?") and "allergies" get the block. Without a ledger file the
  gate returns at once and creates nothing.
- *Review page*: `GET /api/beliefs/search?q=&k=&status=` (admin session like the other
  belief routes; API tokens refused; scoped to the logged-in owner) answers `{"query",
  "results", "semantic", "pending"}`. `k` 1–50 (default 10); `status` one status, a
  comma list or `all` (default `confirmed`; 400 for an unknown one). Mounted before the
  review page's router so `search` isn't read as a belief id.

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
  `synced_at`, `subject_id`, `subject_kind` (`owner`, `person`, `project`, `thing`), and
  `contact_id` while the subject is linked to a contact (removed when unlinked). The text
  doesn't change; a subject change updates the entry in place. A belief with odysseus-sound evidence also keeps the Phase 0 fields
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
Each pass also calls `list_people {"format": "json"}` once and maps the module's people to
subjects ([Subjects and contacts](#subjects-and-contacts-21)) before the facts are
submitted; a fact row's `person_id` travels as `Evidence.person_id`. A module that answers
`list_people` with text or lacks the tool is tolerated (logged once, state
`people_text_only`); its facts match subjects by name.
Then `reconcile_source(owner, "odysseus-sound", ids)`, `apply_decay` and one
`sync_memory`. Result line, counts only: `facts: 12 seen, 1 new, 1 confirmed,
0 unconfirmed, 0 gone, 0 stale; memory: 11 kept, 1 added, 0 updated, 0 removed; people:
4 seen, 0 new` (the people part only when the module answered JSON); a pass with no change
is not logged in Activity.

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
| `BELIEF_STRUCTURE` | on | `off` stops the Belief Structure task (structure already written stays) |

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

### At scale (2.5)

`scripts/belief_eval_scale.py` is the same kind of eval at a few thousand entries (plan
stage 2.5): a deterministic fictional corpus (seeded RNG) goes through the ledger's
public API with `now` fixed at 2026-10-21, and the script measures the ledger, the
Belief Structure task and retrieval. No model, no network;
`tests/test_belief_eval_scale.py` runs the small preset through pytest.

```bash
venv/bin/python scripts/belief_eval_scale.py                 # medium, temp dir, removed after
venv/bin/python scripts/belief_eval_scale.py --size small    # 30 subjects, 300 beliefs (seconds)
venv/bin/python scripts/belief_eval_scale.py --size large    # 1000 subjects, 10000 beliefs
venv/bin/python scripts/belief_eval_scale.py --data-dir /tmp/belief-scale --keep --out /tmp/scale.json
```

| Preset | Subjects (people / projects / things, plus the owner) | Beliefs | Queries |
|---|---|---|---|
| `small` | 30 (20 / 4 / 6) | 300 | up to 60 |
| `medium` (default) | 300 (200 / 40 / 60) | 3000 | 200 |
| `large` | 1000 (666 / 134 / 200) | 10000 | 200 |

**The corpus.** One owner (Robin Vale), people with nicknames as aliases (about a quarter
of them; the module sometimes uses the nickname), projects named so they are inferred as
projects ("Project Kestrel", "Wren migration"), and things ("Blue van"). Beliefs come from
23 text templates, one per kind of fact ("<name> lives in <city>", "<name> is allergic
to <x>", "<project> is due on <date>", "The <thing> is kept in <place>" …), each with
its attribute. They arrive as evidence from `odysseus-sound` (fact ids 100001+, with
the module's person ids and a `list_people` sync first) and `notes`
(`scale-note-<n>`), on dates between January and October 2026, and each belief has a
planned fate:

| Fate | Share | How | Ends |
|---|---|---|---|
| source | 42 % | the module confirms it (3–6 times on 2–4 days) | confirmed |
| two sources | 8 % | once in a conversation, once in a note | confirmed (rule) |
| rule | 5 % | two module ids, 3 sightings on 2 days | confirmed (rule) |
| corrected | 3 % | said once, the owner corrects it | confirmed (owner) |
| owner | 2 % | said once, the owner confirms it | confirmed (owner) |
| aged | 5 % | module-confirmed, last heard before April | stale (decay) |
| gone | 3 % | module-confirmed, then no longer listed | stale (reconcile) |
| rejected | 4 % | module-confirmed, the owner forgets it | rejected |
| once | 28 % | one sighting | tentative |

So about 60 % end confirmed. The same `--seed` (default 7) and size give the same corpus;
the run prints its digest (`corpus 95f5dd30b8c21f46` for medium).

**What it does, in order, and what it times.** `sync_people`; submit every module item
in calls of 1000 (`mirror=False`), then the notes; `reconcile_source`, `apply_decay`; a
first `sync_memory` (writes every mirrored belief); the owner's decisions one call each
(`correct`, `reject`, `confirm`: each mirrors memory, as on the review page); the hourly
facts pass with nothing new (the same module items again, `reconcile_source`,
`apply_decay`); `sync_memory` twice (the second must write nothing and leave
`memory.json` byte-identical); `list_beliefs` (all, confirmed, the review page's default
500, one subject), `list_subjects`, `stats`. Then the Belief Structure task
(`belief_structure.run`) over every confirmed belief with a fake model that maps each
template to its attribute and value, and the retrieval questions.

**Retrieval** (stage 2.3, `src/belief_search.py`). A fixed question set generated from
the corpus, each with the belief ids it expects (the confirmed beliefs it asks about):
by subject name ("What do you know about Kaia Lindqvist?", ~25 %), by alias (the
nickname, ~15 %), about the owner, one per attribute the owner has ("Am I allergic to
anything?", "Where do I live?", "What do you know about me?"), and by attribute ("Where
does … live?", "When is Project Kestrel due?", "Where is the Blue van kept?"). Each is
asked with `search_beliefs(owner, q, k=10, status=("confirmed",))`:

- **top-1**: share of questions whose first hit is an expected belief;
- **top-5**: mean over questions of expected beliefs among the first five, out of
  min(5, expected);
- **p50 / p95**: latency per question in ms;
- **leaks**: hits that aren't confirmed beliefs (must be 0).

It runs twice: the **default** lane, then the **keyword** fallback forced (the module's
`embedder()` replaced by one that answers None, "no lane"; the run prints what it
replaced). A script process has no embedding lane loaded, so in the sandbox both lanes
rank without embeddings ("default lane used embeddings: no"); the default lane
uses embeddings only where the hub's memory index is loaded. While `src/belief_search.py`
isn't importable the part is skipped with a message and the run still passes.

**Checks and thresholds.** Exit 0 when all hold, 1 otherwise, 2 when the data dir already
has beliefs for the owner (`--owner` or a fresh `--data-dir`):

- every belief ends in its planned status; one belief per planned item; the expected
  number of subjects, and every project inferred from its name;
- one memory per confirmed belief; a second `sync_memory` writes nothing and leaves
  `memory.json` byte-identical;
- the Belief Structure task structures every confirmed belief, each with the planned
  attribute and value;
- when retrieval runs: top-5 recall of the default lane ≥ `--min-top5` (0.9) and no
  leaks. The keyword lane's numbers are reported, not gated.

**Reading the numbers.** Medium and large on the sandbox CPU (a reference, not a target;
timings vary by machine, counts don't):

| Measure | Medium | Large |
|---|---|---|
| beliefs (confirmed / tentative / stale / rejected) | 3000 (1815 / 830 / 232 / 123) | 10000 (6038 / 2732 / 796 / 434) |
| evidence rows / subjects | 3432 / 301 | 11287 / 1001 |
| `beliefs.db` / `memory.json` | 2.9 MB / 1.3 MB | 9.7 MB / 4.2 MB |
| submit, new evidence, per 1000 (first → last chunk) | ~380 ms (190 → 570) | ~1200 ms (200 → 2150) |
| submit, unchanged (the hourly pass), per 1000 | ~55 ms, flat | ~53 ms, flat |
| `sync_memory` first / no-op | ~120 / ~110 ms | ~500 / ~230 ms |
| owner action (correct, reject, confirm), mean / p95 | ~105 / ~135 ms | ~360 / ~440 ms |
| `list_beliefs` all / review page (500) / one subject | ~85 / ~20 / ~1 ms | ~240 / ~35 / ~1 ms |
| `list_subjects` | ~200 ms | ~4 s |
| structure (fake model): as planned, per batch of 10, hourly runs | 1815 / 1815, ~21 ms, 46 | 6038 / 6038, ~21 ms, 151 |
| retrieval (the 2.3 ranker, no embedding lane): top-1, top-5, p50 / p95 | 0.92, 0.97, ~75 / ~110 ms | 0.91, 0.97, ~365 / ~460 ms |

- Per-chunk submit times should stay flat as the ledger grows (the unchanged pass does).
  When the chunks of new evidence grow chunk after chunk, a lookup per item scales with
  the ledger size. They do today (about +210 ms per 1000 beliefs already in the ledger),
  and `list_subjects` grows with subjects × beliefs: without `ANALYZE` statistics SQLite
  answers the text match in `submit` and the per-subject counts of `list_subjects` from
  an index on `owner` alone, i.e. by scanning every belief of the owner. A fix belongs in
  the ledger, not in the eval; rerun `--size large` to see it.
- An owner action costs about one no-op `sync_memory` plus a read; it grows with the
  number of mirrored beliefs, since each one reads and compares `memory.json`.
- "hourly runs to structure all" is how long a backlog of this size takes the real task
  (40 per run).
- Retrieval: the per-kind top-5 (`alias`, `attribute`, `name`, `owner`) shows which
  questions miss; the JSON lists up to 10 misses per lane.

**Results file.** The full numbers (corpus digest and fates, ledger counts, every timing
with the per-chunk times, memory, structure, retrieval per lane with misses, the checks)
go to `--out`, by default `data/eval/belief_eval_scale.<size>.json` (under `data/`,
gitignored).
