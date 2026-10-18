# Barvis roadmap and progress

Barvis-specific status. Upstream's own plans are in [../../ROADMAP.md](../../ROADMAP.md);
don't edit that file.

_Last updated: 2026-10-18_

## Status

| # | Item | Status | Where |
|---|---|---|---|
| 1 | Trusted read-only MCP servers, trusted reads not external effects, web-intent routing fix, tool trimming | PR open: SDarioosh/Barvis#1 | [../../BARVIS.md](../../BARVIS.md) |
| 2 | Agent and module docs | PR open, stacked on #1 | `AGENTS.md`, `docs/barvis/` |
| 3 | barvis-sound eval ≥ 15/20 | 9/20 (right fact 18/20, link cited 9/20) | [modules/barvis-sound.md](modules/barvis-sound.md#eval-and-current-score) |

## Next

1. **Citations to ≥ 15/20.** Most remaining failures have the right fact and no
   transcript link. Two replies were just "Done." because the model returned empty text
   after its tool calls. Find why Barvis turns that into "Done." and why links get
   dropped; fix in Barvis, or in the module's result text if that's the cause. Rerun
   `server/eval/bakeoff.py --frontend odysseus` after each change.
2. **Action items → tasks and calendar.** barvis-sound action items flow into Odysseus
   tasks and CalDAV. Barvis reads them through the module's MCP tools
   (`list_action_items`, `update_action_item`); it never writes the module's database.
   Re-enable `manage_tasks` / `manage_calendar` / `manage_notes` in
   `scripts/barvis_setup.py` once they hold this data.
3. **ntfy notifications.** Notify the phone (for example new action items or the daily
   digest) through the existing ntfy service. Local only.
4. **Conversations view.** A Barvis view that lists conversations and opens transcripts,
   built on the module's MCP tools or documented HTTP API. New files under `static/`
   and `routes/`, upstream visual style (`CONTRIBUTING.md`).
5. **Phone access.** Use Barvis from the Android phone over Tailscale (Odysseus has a
   PWA). Lock services to Tailscale per the owner's security decision.
6. **Upstream:** offer the general fixes from PR #1 upstream
   ([upstream-sync.md](upstream-sync.md#sending-fixes-upstream)); weekly syncs.

## Future modules (placeholders)

No repos exist yet. Each would follow [modules.md](modules.md).

- `barvis-<name>`: _to be decided by the owner._

## Open questions for the owner

- Which future modules are planned, and in what order?
- For the tasks/calendar sync: does Barvis copy action items into Odysseus tasks
  (two sources), or show them live from the module?
