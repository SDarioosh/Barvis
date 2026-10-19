#!/usr/bin/env bash
# Barvis: update the modules, then build and deploy Barvis with Docker Compose, with
# backup and rollback.
#
# Works from any directory: it uses the Barvis checkout it lives in, which holds
# docker-compose.yml, .env and docker-compose.override.yml (see docs/barvis/deploy.md).
#
#   scripts/barvis_deploy.sh deploy [REF]   # modules, then build REF (default: <remote>/barvis) and switch to it
#   scripts/barvis_deploy.sh modules        # only update and restart the modules
#   scripts/barvis_deploy.sh rollback       # switch Barvis back to the image before the last deploy
#   scripts/barvis_deploy.sh status         # what is running, modules included
#
# Settings: environment, or KEY=value lines in ~/.config/barvis/deploy.env (all optional):
#   BARVIS_MODULES      module deploy commands, space separated; each is run with
#                       "deploy" or "status" (docs/barvis/modules.md#deploying-a-module),
#                       e.g. ~/projects/barvis-sound/server/scripts/deploy.sh
#   DOCKER              docker command (default: "sudo docker")
#   BARVIS_REMOTE       git remote of the Barvis repo (default: the one pointing at */Barvis)
#   BARVIS_BACKUP_DIR   where data backups go (default: ~/barvis-backups)
#   BARVIS_KEEP_BACKUPS how many backups to keep (default: 5)
#   BARVIS_PASSWORD_FILE admin password file for barvis_setup.py (default: ~/.config/barvis/admin.txt)
#   BARVIS_PY           python with httpx for barvis_setup.py (default: venv/bin/python, else python3)
#   BARVIS_SETUP_ARGS   extra arguments for barvis_setup.py (e.g. "--trim")
set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."
CONFIG=${BARVIS_DEPLOY_CONFIG:-$HOME/.config/barvis/deploy.env}
if [ -f "$CONFIG" ]; then  # KEY=value lines; the environment wins
  while IFS= read -r line || [ -n "$line" ]; do
    [[ $line =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
    value=${value#[\"\']} value=${value%[\"\']} value=${value/#\~/$HOME}
    [ -n "${!key+x}" ] || export "$key=$value"
  done < "$CONFIG"
fi

DOCKER=${DOCKER:-sudo docker}
BACKUP_DIR=${BARVIS_BACKUP_DIR:-$HOME/barvis-backups}
KEEP_BACKUPS=${BARVIS_KEEP_BACKUPS:-5}
PASSWORD_FILE=${BARVIS_PASSWORD_FILE:-$HOME/.config/barvis/admin.txt}
SERVICE=odysseus

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*"; }
compose() { $DOCKER compose "$@"; }

env_value() {  # value of KEY in .env, or the default
  local v=""
  [ -f .env ] && v=$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'" || true)
  echo "${v:-$2}"
}

APP_PORT=$(env_value APP_PORT 7011)
DATA_DIR=$(env_value APP_DATA_DIR ./data)
URL="http://127.0.0.1:${APP_PORT}"

preflight() {
  [ -f docker-compose.yml ] || die "run this from the Barvis checkout (no docker-compose.yml here)"
  [ -f docker-compose.override.yml ] || die "docker-compose.override.yml is missing; copy docker/barvis.override.yml (docs/barvis/deploy.md)"
  grep -q 'image: *barvis:' docker-compose.override.yml \
    || die "docker-compose.override.yml does not set 'image: barvis:...'; compose would run upstream's image"
}

wait_healthy() {
  local i
  for i in $(seq 1 60); do
    if curl -sf "$URL/api/health" >/dev/null 2>&1; then
      log "healthy on $URL"
      return 0
    fi
    sleep 2
  done
  return 1
}

image_rev() {  # commit label of an image, or "none"
  local rev
  rev=$($DOCKER image inspect -f '{{index .Config.Labels "barvis.revision"}}' "$1" 2>/dev/null | head -n 1 || true)
  echo "${rev:-none}"
}

switch_to() {  # retag $1 as barvis:current and recreate the container
  $DOCKER tag "$1" barvis:current && compose up -d
}

backup_data() {
  mkdir -p "$BACKUP_DIR"
  local file
  file="$BACKUP_DIR/data-$(date +%Y%m%d-%H%M%S)-$(image_rev barvis:current).tgz"
  log "stopping $SERVICE and backing up $DATA_DIR to $file"
  compose stop "$SERVICE"
  sudo tar czf "$file" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
  ls -1t "$BACKUP_DIR"/data-*.tgz 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f
}

run_setup() {
  if [ ! -f "$PASSWORD_FILE" ]; then
    log "no $PASSWORD_FILE; run scripts/barvis_setup.py yourself"
    return 0
  fi
  local py=${BARVIS_PY:-}
  if [ -z "$py" ]; then
    if [ -x venv/bin/python ]; then py=venv/bin/python; else py=python3; fi
  fi
  log "running barvis_setup.py"
  # shellcheck disable=SC2086
  "$py" scripts/barvis_setup.py --url "$URL" --password-file "$PASSWORD_FILE" ${BARVIS_SETUP_ARGS:-} \
    || log "barvis_setup.py failed; Barvis is running. If httpx is missing, set BARVIS_PY to a python that has it (e.g. ~/projects/barvis/venv/bin/python) and rerun the script by hand"
}

remote() {
  if [ -n "${BARVIS_REMOTE:-}" ]; then echo "$BARVIS_REMOTE"; return; fi
  git remote -v | awk 'tolower($2) ~ /[\/:]sdarioosh\/barvis(\.git)?$/ {print $1; exit}'
}

module_cmds() {  # BARVIS_MODULES, one per line, ~ expanded
  local m
  for m in ${BARVIS_MODULES:-}; do echo "${m/#\~/$HOME}"; done
}

# Update and restart each module before Barvis, so Barvis comes up against the new
# module servers. A failed module is reported at the end; Barvis is still deployed.
MODULES_FAILED=()
deploy_modules() {
  local m
  [ -n "${BARVIS_MODULES:-}" ] || { log "no BARVIS_MODULES set; skipping modules"; return 0; }
  if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
    log "started with sudo; skipping modules (they run as your user; run this script without sudo, it calls sudo for docker itself)"
    MODULES_FAILED+=(all)
    return 0
  fi
  while IFS= read -r m; do
    if [ ! -x "$m" ]; then
      log "module deploy command not found or not executable: $m"
      MODULES_FAILED+=("$m")
      continue
    fi
    log "module: $m deploy"
    "$m" deploy < /dev/null || MODULES_FAILED+=("$m")
  done < <(module_cmds)
}

modules_result() {
  [ ${#MODULES_FAILED[@]} -eq 0 ] || die "module deploy failed: ${MODULES_FAILED[*]} (see the output above)"
}

cmd_deploy() {
  preflight
  git diff --quiet HEAD -- || die "tracked files have local changes; move them to .env or docker-compose.override.yml first (git status)"
  deploy_modules
  local r ref sha
  r=$(remote); [ -n "$r" ] || die "no git remote points at the Barvis repo; set BARVIS_REMOTE"
  ref=${1:-$r/barvis}
  log "fetching $r"
  git fetch -q "$r"
  if [ "$ref" = "$r/barvis" ] && git show-ref -q --verify refs/heads/barvis; then
    git checkout -q barvis
    git merge -q --ff-only "$ref" || die "local branch barvis has diverged from $ref"
  else
    git checkout -q --detach "$ref"
  fi
  sha=$(git rev-parse --short HEAD)
  log "building barvis:$sha"
  $DOCKER build -q --label "barvis.revision=$sha" -t "barvis:$sha" . >/dev/null

  if $DOCKER image inspect barvis:current >/dev/null 2>&1; then
    $DOCKER tag barvis:current barvis:previous
  fi
  backup_data
  log "starting barvis:$sha"
  if ! { switch_to "barvis:$sha" && wait_healthy; }; then
    compose logs --tail 40 "$SERVICE" >&2 || true
    if $DOCKER image inspect barvis:previous >/dev/null 2>&1; then
      log "barvis:$sha did not start or is not healthy after 2 min; rolling back to barvis:previous"
      switch_to barvis:previous && wait_healthy || true
      die "deploy of $sha failed and was rolled back. If the database was migrated, restore the newest backup in $BACKUP_DIR (docs/barvis/deploy.md)"
    fi
    die "deploy of $sha failed and there is no previous image to roll back to"
  fi
  run_setup
  log "deployed $sha"
  modules_result
}

cmd_rollback() {
  preflight
  $DOCKER image inspect barvis:previous >/dev/null 2>&1 || die "no barvis:previous image"
  log "switching back to barvis:previous"
  switch_to barvis:previous && wait_healthy || die "not healthy after rollback; see: $DOCKER compose logs $SERVICE"
  local latest
  latest=$(ls -1t "$BACKUP_DIR"/data-*.tgz 2>/dev/null | head -n 1 || true)
  log "rolled back. If the newer version changed the database, restore its pre-deploy backup:"
  echo "    $DOCKER compose stop $SERVICE && sudo rm -rf $DATA_DIR && sudo tar xzf ${latest:-<backup>} -C $(dirname "$DATA_DIR") && $DOCKER compose up -d"
}

cmd_status() {
  echo "running image: barvis:current (revision $(image_rev barvis:current))"
  echo "checkout:      $(git rev-parse --short HEAD) $(git log -1 --format=%s)"
  echo -n "health:        "; curl -s "$URL/api/health" || echo "not answering on $URL"
  echo
  local m
  while IFS= read -r m; do
    [ -x "$m" ] && "$m" status < /dev/null || echo "module $m: status failed"
  done < <(module_cmds)
}

case "${1:-}" in
  deploy) shift; cmd_deploy "$@" ;;
  modules) deploy_modules; modules_result; log "modules done" ;;
  rollback) cmd_rollback ;;
  status) cmd_status ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
