@AGENTS.md

## Claude Code notes

- `AGENTS.md` above is the source of truth. Add shared rules there, not here.
- Use the focused test commands from `AGENTS.md`; the full upstream suite is slow and
  has known failures.
- Before pushing, re-read your diff for changes to upstream files: each one must be a
  small hook listed in `BARVIS.md`.
- `.claude/settings.json` blocks edits to the owner's Odysseus install, reading `.env` files and data directories, pushes to protected branches and `gh pr merge`, and asks before `docker`, `sudo`, `systemctl` and force pushes. Don't work around these rules (for example through a script); ask the owner instead.
