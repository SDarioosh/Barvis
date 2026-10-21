# Personal context system: plan and phases

How Odysseus becomes a personal context system: it takes in as much of the owner's own
data as the owner feeds it, learns about them over time, and uses that knowledge to help
(first: a daily "Today" insight). This document is the roll-out plan. Status of what is
built lives in [roadmap.md](roadmap.md); the module contract in [modules.md](modules.md).

_Written 2026-10-21. Decisions are marked **decided**; everything else is a proposal for
the owner._

## Shape of the system

Three layers, kept apart on purpose:

| Layer | What it is | Where |
|---|---|---|
| **Evidence** | Things a source observed, each with a link to where it came from: a fact said in a conversation, a line in an email, a note, a calendar entry | the modules (odysseus-sound today) and Odysseus' own stores |
| **Beliefs** | What the system holds to be true about the owner, with provenance (source, id, link, first and last seen, how confirmed) and a retirement path | Odysseus memory |
| **Consumers** | Things that use the beliefs: chat context, the Today insight, later notifications and other modules | Odysseus |

Rules that hold in every phase:

- Everything stays on the owner's network: no cloud speech-to-text, LLMs or embeddings
  in any data path, no telemetry ([AGENTS.md](../../AGENTS.md)).
- Modules are reached only over their MCP server or documented HTTP API
  ([modules.md](modules.md)); Odysseus never touches a module's storage.
- Every belief carries provenance. A belief without a source link is not stored.
- Confirmation before use: a single mention is evidence, not a belief. What counts as
  confirmed is a setting, and the owner's own confirmation outranks any count.
- Everything is correctable and forgettable, and forgetting propagates back to the source.
- Every phase ships with a way to measure it (tests on fictional data, an eval, or a
  Server verification the owner runs).

## Phase 0: Today insight and confirmed facts (decided, in progress)

**Goal.** Every day a short AI insight (headline, what to do first with a reason each,
what is coming up, other things worth a look), built from the calendar, to-dos and
memory, shown in the web UI, in chat and on the T5 e-paper. Confirmed conversation facts
flow into memory so the insight gets richer over time.

**Order of work.** Module side first, then the hub, then the relays and the T5, then the
eval; one feature branch and one PR per repo, linked to each other.

| Stage | Repo | Ships |
|---|---|---|
| 0.1 Confirmed facts | odysseus-sound | A fact is confirmed when it is current, said in ≥ `CONVO_FACT_CONFIRM_SOURCES` distinct conversations spanning ≥ `CONVO_FACT_CONFIRM_DAYS` days (defaults 3 and 2). `list_facts` gains `confirmed` and `format="json"`; each JSON fact has `id`, `subject`, `about` (`owner`, `person`, `other`), `fact`, `times_said`, `days`, `first_said`, `last_said`, `link`. Text output unchanged. Contract version bumped; tool tables updated here and in the module's `AGENTS.md`. |
| 0.2 Facts into memory | hub | Hourly job: pulls every confirmed fact and reconciles against its copies by module fact id (a copy carries `source: "odysseus-sound"`, the fact id, the link, `times_said`, `days`). Facts no longer returned are retired. Category from `about`, mapped onto the hub's existing memory categories. Idempotent, so it survives the upstream memory consolidation job. |
| 0.3 Insight job | hub | Every 15 min: gathers calendar (today to +7 days), to-do notes of types `checklist`, `todo`, `goal`, memory, and the module's digest and open action items over MCP. Code buckets items (overdue, due today, due soon, undated), drops shopping-type lists without a due date (titles configurable; default Shopping, Groceries, Grocery), caps candidates, and hashes the inputs. The LLM runs only when the hash changed or the day rolled over, and only picks and writes the "why". Refs not in the input are dropped. Stored per owner and day as `{"date", "generated", "headline", "focus", "coming_up", "also"}`. Built: [today-insight.md](today-insight.md). |
| 0.4 Surfaces | hub | Today card in the web UI (tickable to-dos, events linking to the calendar, 👍/👎 stored); chat answers "what should I do first today?" from the stored insight through the memory-context hook, no new tool; `GET /api/codex/insight/today` with scope `insight:read` (404 when there is none). Built: [today-insight.md](today-insight.md). |
| 0.5 Relays | odysseus-sound | PC and Android relays fetch the insight with the Odysseus token, put it in the T5 feed as `insight`, drop refs not in the feed's `todos` or `calendar`; 404, 403 or no hub leaves `insight` out. The `type` filter stays: the hub already reports `todo` and `goal` notes as `checklist` (SDarioosh/Barvis#36); a test documents it. |
| 0.6 T5 firmware | odysseus-sound | `feed.c` parses `insight` (unknown keys ignored); `draw_today` shows INSIGHT, DO FIRST, COMING UP, ALSO WORTH A LOOK with the reasons, tickable to-dos, upcoming timed events in order, "Digest ready on Brief ›". Without an insight, `today_plan.c` orders the same sections by rule (overdue → due today by time → next days → pinned → undated; shopping lists only when dated). Host tests render the screens from fictional fixtures. |
| 0.7 Measure | both | Bakeoff rerun with `--frontend odysseus` (contract change). Server verification: confirmed-fact counts at the default thresholds (counts only), insight time on `qwen3.5-35b`, the insight on the owner's data on the test instance, phone and T5 steps. |

**Open questions for the owner** (from the design review): reconcile instead of
`changed_since`; whether the server's hub predates SDarioosh/Barvis#36; whether the
existing memory categories are enough.

**Done when:** the insight shows on all three surfaces from fictional data in the
sandbox, the owner's verification is pasted into both PRs, and the bakeoff score is not
below the previous run.

## Phase 1: One intake, a review page, and forgetting

**Goal.** Make the belief store trustworthy and correctable, and let every source feed it
the same way.

| Stage | Repo | Ships |
|---|---|---|
| 1.1 Evidence intake | hub | One internal path for submitting evidence: subject, `about`, text, source, source id, link, seen-at. The facts job of 0.2 becomes its first caller. Confirmation counting moves here, so the hub can count across sources (a fact said in a conversation and written in an email confirms twice). The module keeps its own confirmation for its own tools. Built: [beliefs.md](beliefs.md) (the belief ledger in `DATA_DIR/beliefs.db`; the facts job submits every module fact as evidence and only confirmed beliefs reach memory). |
| 1.2 More sources | hub | Chat extraction, email (the sender-signature job already learns per sender), notes and documents submit through 1.1 with provenance. Each source has a switch, off by default until tested. |
| 1.3 Review page | hub | "What do you know about me": tentative and confirmed beliefs grouped by subject, each with its sources and links. Buttons: confirm, correct, forget. A confirmation makes a once-said fact usable the same day. |
| 1.4 Forget propagates | both | A new module write tool retires a fact by id (`readOnlyHint: false`, `destructiveHint: true`), so a fact the owner forgot in the hub does not return on the next reconcile. Contract change, coordinated. |
| 1.5 Expiry and decay | hub | Every belief carries last-seen and an optional valid-until. Retrieval demotes stale beliefs; the review page shows "not heard since". The module's supersede chain supplies the dates. Built: [beliefs.md](beliefs.md#expiry-and-decay-15) (confirmed beliefs not seen for `BELIEF_STALE_DAYS` and beliefs past `valid_until` go stale and leave memory; owner-confirmed ones never decay by age). |
| 1.6 Measure | hub | A memory eval on fictional seeded data: expected confirmed set, expected retirements after a correction, expected answers to "what do you know about X". Runs in the sandbox like the bakeoff. |

**Depends on:** Phase 0 merged. **Risk:** more sources means more wrong beliefs; keep the
sources switched off until the review page exists (1.3 before 1.2 in roll-out).

## Phase 2: Connected and structured

**Goal.** Beliefs connect to the owner's people and projects, and keep working past a few
hundred entries.

| Stage | Repo | Ships |
|---|---|---|
| 2.1 People linking | both | A module voice profile links to a hub contact (the module's `list_people` exposes a stable person id; the hub stores the contact id next to it). A fact about "Alice" lands on the contact with her phone, email and calendar history. Names are no longer matched by string alone. |
| 2.2 Structured entries | hub | Beliefs get optional structure next to the text: subject, attribute, value, valid-from, valid-to. Provenance from Phase 0 makes the migration lossless. Free text stays the fallback. |
| 2.3 Retrieval | hub | Chat, the insight and the review page use one ranker over the structured store, with a local embedding model (the hub's vector store, the module's optional embeddings; both must be local). The flat keyword-and-similarity lookup is kept as a fallback. |
| 2.4 Projects | hub | Project as a first-class subject: facts, action items, events and documents that mention it are grouped. Feeds the insight's "why" ("blocks the proposal due Friday"). |
| 2.5 Measure | hub | The memory eval grows to a few thousand fictional entries; answers and insight ordering must not degrade. |

**Depends on:** 1.1 and 1.3. **Risk:** structure invites over-modelling; only attributes
that a consumer actually uses are added.

## Phase 3: Learning from outcomes

**Goal.** The system notices what the owner does with its help and gets better at
choosing.

| Stage | Repo | Ships |
|---|---|---|
| 3.1 Outcome log | hub | For each insight: which "do first" items were done that day, which were ignored, which were re-ranked by the owner, next to the 👍/👎 votes of Phase 0. Counts and ids only. |
| 3.2 Ranking tuning | hub | Rule weights per list, note type and event type learned from the outcome log (simple, inspectable weights; no model training). The T5's offline order in `today_plan.c` follows the same published rules. |
| 3.3 Push | hub | One ntfy push at the digest hour with the headline and the first item, through the existing module notify channel; off with one setting. |
| 3.4 Richer insight inputs | hub | Routines (habitual times and days from the calendar and conversation history), what the owner said they wanted to do this week (voice notes), and open loops from conversations ("you told Alice you'd send the file"). |
| 3.5 Measure | hub | Vote and outcome trends per week on the review page; the memory eval gains insight-ordering cases. |

**Depends on:** Phase 0 votes and Phase 1 review page. **Risk:** optimising for "done"
rewards easy items; keep the weights visible and the owner in control.

## Phase 4: Keep it healthy (ongoing from Phase 1)

| Stage | Repo | Ships |
|---|---|---|
| 4.1 Provenance-aware consolidation | hub | The fork's own consolidation pass that merges only within a source and keeps ids and links; the upstream consolidation job is turned off for sourced entries. |
| 4.2 Retention and export | both | A retention rule for transcripts and audio (the module), memory export in the hub, and a forget that is complete across hub and module. |
| 4.3 Capture quality | odysseus-sound | Diarization, owner-voice enrollment and the recorder work in `PROGRESS.md`. Capture is the ceiling on everything above: a fact attributed to the wrong speaker is wrong evidence. |
| 4.4 Shared search | both | The `context` search aggregator from [modules.md](modules.md#shared-search-planned), so chat can ask every module at once and the intake of 1.1 has a uniform read side. |
| 4.5 Upstream sync | hub | Each phase keeps its code in new files with small hooks ([FORK.md](../../FORK.md)); general fixes are offered upstream per [upstream-sync.md](upstream-sync.md). |

## Order of roll-out and what gates each step

1. Phase 0, module PR then hub PR, owner's Server verification, bakeoff not below the
   previous run.
2. Phase 1 in the order 1.1, 1.3, 1.5, 1.4, then 1.2 one source at a time, 1.6 alongside.
3. Phase 2 once the review page has been used for a few weeks and the owner has corrected
   real beliefs (the corrections tell us which structure is worth having).
4. Phase 3 once there are a few weeks of votes and outcomes to learn from.
5. Phase 4 items run alongside from Phase 1 on; 4.1 before 1.2 adds a second source.

## Open questions for the owner

- Phase 0: the three questions above (reconcile, hub age on the server, categories).
- Phase 1: which source comes second after conversations (email or notes)?
- Phase 1: should the review page live on `/modules/sound` or be its own page under
  the hub (proposal: its own page, since beliefs come from every source)?
- Phase 2: is a person id in the module contract acceptable (it is a contract change)?
- Phase 3: is a daily push wanted at all, or does the T5 cover it?
