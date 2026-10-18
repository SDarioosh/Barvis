# Upstream sync

Odysseus moves fast: ~340 commits in 30 days, ~478k lines of Python, no releases. Barvis
merges upstream `dev` into `barvis` **weekly** so each merge stays small.

## Branches

- `dev`: mirrors `upstream/dev`. Only fast-forwarded from upstream; never committed to.
- `barvis`: upstream plus the Barvis patches. Default branch; all PRs go here.
- Upstream remote: `https://github.com/odysseus-dev/odysseus.git`.

Agents never push to `dev` or `barvis` directly; the sync is a PR like any other.

## Weekly merge

```bash
git remote add upstream https://github.com/odysseus-dev/odysseus.git   # once
git fetch upstream origin
git checkout -b chore/upstream-sync-<YYYY-MM-DD> origin/barvis
git merge --no-ff upstream/dev
# resolve conflicts (below), run the checks (below), then:
git push -u origin chore/upstream-sync-<YYYY-MM-DD>
```

Open a PR into `barvis` titled `chore: merge upstream dev (<YYYY-MM-DD>)`. The description
lists the upstream range (`git log --oneline origin/barvis..upstream/dev | wc -l` and the
first/last commit), each conflict and how it was resolved, the test results, and the eval
score. The owner fast-forwards `dev` to `upstream/dev`.

Merge, don't rebase: `barvis` is shared history.

## Conflict hot spots

- `src/agent_loop.py`: hook in `_web_search_unavailable_for_turn`.
- `src/agent_runtime/`: hooks in `authority.py` (`create_request_authority`) and
  `effect_adapters.py` (`classify`). Upstream is refactoring the agent runtime, so these
  move most.
- `src/tool_capabilities.py`: hook in `ToolRunSecurityContext`.
- `src/mcp_manager.py`: one line in `_connect_http` that keeps tool `annotations`.

Find every hook with `git grep -n "mcp_trust"` (and `git grep -n "Barvis:"` for newer
hooks). The full list is the "Changes from upstream" table in [../../BARVIS.md](../../BARVIS.md).

When resolving:

- Take upstream's version of the file, then re-apply the Barvis hook at the matching spot.
  Don't keep old upstream code just because the hook sat next to it.
- If the function a hook lived in is gone or renamed, find where upstream moved that
  logic and put the hook there. If there is no clear place, stop and escalate
  (`ESCALATE: <reason>`); don't redesign the hook inside a merge PR.
- Never fix unrelated upstream code in the merge PR.

## After a merge

1. Barvis tests and the upstream tests around the hooks:

   ```bash
   python -m pytest -q tests/test_mcp_trust.py tests/test_request_authority.py tests/test_tool_policy.py
   python -m py_compile app.py routes/*.py src/*.py
   ```

2. Broader tests for what upstream changed in the hooked areas. Compare failures against
   a clean checkout of the previous `barvis` and the new `upstream/dev`; only new
   failures caused by Barvis matter. 23 tests already fail upstream
   (`tests/test_null_owner_gates.py`, `tests/test_runtime_evidence_contract.py`).
3. Bring up the sandbox stack ([sandbox.md](sandbox.md)) on the merged code and re-run
   `scripts/barvis_setup.py` against it. Check that module servers still connect.
4. Run each module's eval in the sandbox (barvis-sound:
   `server/eval/bakeoff.py --frontend odysseus --only 1 4 16`) and compare with the last
   sandbox run. Put both numbers in the PR.
5. Add a Server verification section: restart the server's test instance, re-run
   `barvis_setup.py` there, and run the full eval. The owner runs it; the server score
   must not drop.

Production (port 7000) is updated only by the owner, after the sync PR is merged:
`scripts/barvis_deploy.sh deploy` ([deploy.md](deploy.md)).

## Sending fixes upstream

Offer a change upstream when it is a general fix or feature, not Barvis-specific. Current
candidates (marked "Upstreamable: yes" in [../../BARVIS.md](../../BARVIS.md)): trusted
read-only MCP servers, trusted reads not counted as external effects, the
web-intent routing fix, and HTTP MCP servers keeping tool annotations (the smallest one;
a good first issue to file).

- Upstream asks LLM agents to **open an issue first** instead of a PR
  (`CONTRIBUTING.md`). So: an agent drafts the issue text (problem, repro, proposed fix,
  link to the Barvis commit) and gives it to the owner; the owner files it and decides
  on a PR.
- Upstream PRs target `dev`, follow upstream's PR template and Conventional Commits, and
  contain only the general change: no Barvis names, docs or config.
- Once upstream merges an equivalent change, the next sync drops the Barvis version:
  remove the hook and the Barvis file in the merge PR, and update [../../BARVIS.md](../../BARVIS.md).
