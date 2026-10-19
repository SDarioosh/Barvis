@AGENTS.md

## Claude Code notes

- `AGENTS.md` above is the source of truth. Add shared rules there, not here.
- Use the focused test commands from `AGENTS.md`; the full upstream suite is slow and
  has known failures.
- Before pushing, re-read your diff for changes to upstream files: each one must be a
  small hook listed in `FORK.md`.
