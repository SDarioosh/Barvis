# Deploying Barvis on the server

Barvis runs on the owner's server in two places:

| | Production | Test instance |
|---|---|---|
| What | the owner's daily install: all Odysseus features plus modules | throwaway copy for checks and evals |
| How | Docker Compose, image `barvis:<commit>` built locally | from source (`uvicorn`), its own data dir |
| Where | `/home/steam/models/odysseus`, port 7000 | `~/projects/barvis`, `127.0.0.1:7010` (`~/.hermes/cache/scratch/barvis-run.sh`) |
| Changed by | the owner only, with `scripts/barvis_deploy.sh` | verification steps in PRs |

Production ran upstream Odysseus until 2026-10-18 and was converted in place, so it kept
its accounts, chats, settings, ChromaDB and ntfy data.

The flow for every change: **PR → sandbox checks → merge into `barvis` → test instance
(Server verification) → `barvis_deploy.sh deploy` on production.**

## Update production

```bash
cd /home/steam/models/odysseus
scripts/barvis_deploy.sh deploy          # latest barvis
scripts/barvis_deploy.sh status          # what is running
scripts/barvis_deploy.sh rollback        # back to the image before the last deploy
```

`deploy` does, in order:

1. Refuses to run if tracked files have local changes, or if
   `docker-compose.override.yml` doesn't select a `barvis:` image.
2. Fetches the Barvis remote and fast-forwards the local `barvis` branch (or checks out
   the ref you pass, e.g. a tag).
3. Builds `barvis:<short sha>`, tags the running image `barvis:previous`.
4. Stops the app container and backs up the data dir to
   `~/barvis-backups/data-<date>-<old sha>.tgz` (keeps the last 5).
5. Starts the new image, waits up to 2 minutes for `/api/health`. If it never comes up,
   it switches back to `barvis:previous` by itself and says so.
6. Runs `scripts/barvis_setup.py` if `~/.config/barvis/admin.txt` exists.

`barvis_setup.py` needs Python with `httpx`; on the owner's server put
`export BARVIS_PY=~/projects/barvis/venv/bin/python` in `~/.bashrc`.

Settings (all optional): `DOCKER` (default `sudo docker`), `BARVIS_REMOTE`,
`BARVIS_BACKUP_DIR`, `BARVIS_KEEP_BACKUPS`, `BARVIS_PASSWORD_FILE`, `BARVIS_PY`,
`BARVIS_SETUP_ARGS`. See the top of the script.

**Rolling back data.** `rollback` switches the image only. If the newer version migrated
the database, the old one may not read it; restore the backup taken before that deploy
(the command `rollback` prints):

```bash
sudo docker compose stop odysseus
sudo rm -rf data && sudo tar xzf ~/barvis-backups/data-<date>-<sha>.tgz -C .
sudo docker compose up -d
```

## Features and modules

Production keeps every Odysseus feature. `scripts/barvis_setup.py` (run by `deploy`):

- leaves the tool toggles in Settings > Tools alone;
- sets the default chat model to "Auto" tool mode;
- lists the MCP servers and warns if `convo` (or each `--module`) is missing or
  disconnected.

`--trim` turns off every built-in except a small keep-list and sets "Regular tools"
(the old memory-only setup); `--untrim` turns everything back on.
`--model-mode auto|full|unchanged` overrides the tool mode. Put the flags in
`BARVIS_SETUP_ARGS` to make `deploy` use them.

**Known gap (2026-10-18).** With every Odysseus tool on, module tools don't reach the
model yet. "Auto" picks tools per turn and doesn't pick module MCP tools (recording
questions go to the calendar or get no tool), and "Regular tools" sends all ~130 tool
schemas (~35k tokens) with every request. Sandbox numbers:
[sandbox.md](sandbox.md#all-odysseus-tools-on-2026-10-18). The fix is a Barvis routing
change that always offers trusted module tools in "Auto" ([roadmap.md](roadmap.md)).
Until it lands, `BARVIS_SETUP_ARGS=--trim` is the setup in which barvis-sound answers
work, at the cost of the other features.

A module plugs in as an MCP server ([modules.md](modules.md#registering-a-module-in-barvis)).
For barvis-sound on this server:

- Run its MCP server on the Docker bridge address, so the container can reach it:
  `CONVO_MCP_HOST=172.17.0.1 .venv/bin/convo-server mcp --http` (port 8001).
- In Settings > MCP add an HTTP server named `convo`:
  `http://host.docker.internal:8001/mcp?access_token=<CONVO_MCP_TOKEN>`.
- `ODYSSEUS_TRUSTED_READONLY_MCP=convo` is set in `docker-compose.override.yml`.
- Paste its `INSTRUCTIONS` text into a Custom preset
  ([modules/barvis-sound.md](modules/barvis-sound.md#connect-it)).

Check reachability from inside the container:

```bash
sudo docker compose exec odysseus python -c "import urllib.request as u
try: u.urlopen('http://host.docker.internal:8001/mcp', timeout=5); print('reachable')
except u.HTTPError as e: print('reachable, HTTP', e.code)
except Exception as e: print('NOT reachable:', e)"
```

## One-time setup (done 2026-10-18)

How an upstream Odysseus Compose install becomes Barvis production. Kept for a rebuild.

```bash
cd /home/steam/models/odysseus
git status                                    # local edits to tracked files? move them to .env first
sudo docker compose stop odysseus
sudo tar czf ~/odysseus-data-$(date +%F).tgz data/
git remote add barvis https://github.com/SDarioosh/Barvis.git
git fetch barvis && git checkout -b barvis barvis/barvis
cp docker/barvis.override.yml docker-compose.override.yml
printf 'APP_BIND=0.0.0.0\nAPP_PORT=7000\n' >> .env
mkdir -p ~/.config/barvis && chmod 700 ~/.config/barvis
read -rsp 'Barvis admin password: ' P; printf '%s' "$P" > ~/.config/barvis/admin.txt; unset P; echo
chmod 600 ~/.config/barvis/admin.txt
scripts/barvis_deploy.sh deploy
```

The admin password is the existing Odysseus one: Barvis keeps the accounts.

Pitfalls met during the switch:

- **Compose runs upstream's image unless told otherwise.** `docker-compose.yml` pulls
  `ghcr.io/odysseus-dev/odysseus` and only builds locally if the pull fails. The override
  selects `barvis:current` with `pull_policy: never`.
- **Variables reach the container only if listed.** `ODYSSEUS_TRUSTED_READONLY_MCP` is
  not in upstream's `environment:` list, so it lives in the override.
- **Stay in the same folder.** Compose names volumes (ChromaDB, ntfy, SearXNG) after the
  folder; a new checkout elsewhere starts with empty ones.
- **Ports go in `.env`.** The fallback port is 7011, and a `ports:` entry in the override
  is added to the base one instead of replacing it.
- **Docker needs `sudo` here,** including inside `$( … )`.
- **"Connection reset by peer"** right after a start means the app is still starting
  (5–15 s); `deploy` waits for `/api/health`.
- Harmless warnings: `ODYSSEUS_TTS_CACHE_MAX_BYTES variable is not set` (upstream's
  compose file has no default for it) and "current commit information was not captured"
  during `sudo docker build` (root can't read a repo owned by `steam`; the script labels
  the image with the commit instead).
