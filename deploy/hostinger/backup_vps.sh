#!/usr/bin/env bash
# deploy/hostinger/backup_vps.sh - scripted backup of the JARVIS stack on the Hostinger VPS (plan C-11 / OP-07).
#
# Usage (as root on the VPS):
#   bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh [label]
#   bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh --prune-older-than-days N            # dry run: lists only
#   bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh --prune-older-than-days N --confirm  # deletes (user's OK first)
#
# Writes /root/jarvis-backups/<YYYY-MM-DD_HHMMZ>[-label]/ (folder 700, files 600):
#   opt-jarvis-agent.tgz  /opt/jarvis-agent without stonix_ui/node_modules (members jarvis-agent/..., restore with -C /opt)
#   jarvis.conf           /etc/nginx/conf.d/jarvis.conf
#   jarvis.htpasswd       /etc/nginx/jarvis.htpasswd                     (when present)
#   hostinger.env         /opt/jarvis-agent/deploy/hostinger/.env
#   bridge.env            /opt/jarvis-agent/deploy/hostinger/bridge.env  (when present)
#   root.env              /opt/jarvis-agent/.env                         (when present)
#   DEPLOYED_SHA          /opt/jarvis-agent/DEPLOYED_SHA                 (when present)
#   volumes.tgz           /app/{logs,Jarvis_Outputs,Jarvis_Shared,data} of the bridge's named volumes
#   bridge.db             online SQLite copy of /app/data/bridge.db      (when present)
#   images.txt            rollback tags, image ids, LiveKit image id
#   docker-ps.txt         docker ps -a of compose project jarvis
#   containers.txt        image, state and mounts per jarvis container (no env, no secrets)
#   MANIFEST.txt          what was captured, warnings and errors
#   SHA256SUMS            checksums of the files above (check with: sha256sum -c SHA256SUMS)
# and tags the images that the jarvis-bridge / jarvis-frontend containers run (normally
# jarvis-{bridge,frontend}:hostinger) as jarvis-{bridge,frontend}:rollback-<backup folder name>.
#
# Scope (COORD-4 / K-54): only the jarvis Docker project, /opt/jarvis-agent, the two nginx jarvis
# files and /root/jarvis-backups; read-only everywhere else. It never deletes a backup, except
# with --prune-older-than-days N --confirm, which needs the user's OK first. Prune only looks at
# folders this script made (YYYY-MM-DD_HHMMZ...) and always keeps the newest 3.
# Restore steps: deploy/hostinger/RUNBOOK.md, section "Backup & Restore".
# The last line of a successful run is BACKUP_DIR=<folder> (deploy scripts parse it).
# Exit code: 0 ok, 1 error (the partial backup is kept and its folder printed), 2 usage error.
set -euo pipefail
umask 077

readonly APP_DIR=/opt/jarvis-agent
readonly HOSTINGER_DIR="$APP_DIR/deploy/hostinger"
readonly COMPOSE_FILE="$HOSTINGER_DIR/docker-compose.yml"
readonly NGINX_CONF=/etc/nginx/conf.d/jarvis.conf
readonly HTPASSWD=/etc/nginx/jarvis.htpasswd
readonly PROJECT=jarvis
readonly BRIDGE_CTR=jarvis-bridge
readonly BRIDGE_SERVICE=ui-bridge
readonly LIVEKIT_CTR=jarvis-livekit
readonly KEEP_NEWEST=3
readonly SCRIPT_VERSION=1
# JARVIS_BACKUP_BASE exists only so the prune logic can be tested on a scratch folder.
readonly BACKUP_BASE="${JARVIS_BACKUP_BASE:-/root/jarvis-backups}"
readonly NAME_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}Z(-[A-Za-z0-9._-]+)?$'
readonly LABEL_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$'

WARNINGS=()
ERRORS=()
LABEL=""
NAME=""
B=""
CTR_TMP=""
STARTED_UTC=""
DB_SNAPSHOT=0
DB_STATUS="not checked"
VOLUMES_MODE=""

log()  { printf '[backup_vps] %s\n' "$*"; }
warn() { WARNINGS+=("$*"); printf '[backup_vps] WARNING: %s\n' "$*" >&2; }
fail() { ERRORS+=("$*"); printf '[backup_vps] ERROR: %s\n' "$*" >&2; }
die()  { printf '[backup_vps] FATAL: %s\n' "$*" >&2; exit 1; }
usage_error() { printf '[backup_vps] %s (see --help)\n' "$*" >&2; exit 2; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

on_exit() {
  local rc=$?
  if [[ -n "$CTR_TMP" ]]; then
    docker exec "$BRIDGE_CTR" rm -f -- "$CTR_TMP" >/dev/null 2>&1 || true
  fi
  if (( rc != 0 )) && [[ -n "$B" && -d "$B" ]]; then
    printf '[backup_vps] backup INCOMPLETE (exit %d); what was captured is kept in %s\n' "$rc" "$B" >&2
  fi
}
trap on_exit EXIT

take_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$BACKUP_BASE/.backup_vps.lock"
    flock -n 9 || die "another backup_vps.sh run holds $BACKUP_BASE/.backup_vps.lock"
  else
    warn "flock not found; running without the single-run lock"
  fi
}

bridge_running() {
  [[ "$(docker container inspect -f '{{.State.Running}}' "$BRIDGE_CTR" 2>/dev/null || true)" == "true" ]]
}

compose() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$HOSTINGER_DIR/.env" "$@"
}

# ---------------------------------------------------------------- 1. code
backup_code() {
  local parent base rc=0 entries
  parent="$(dirname "$APP_DIR")"
  base="$(basename "$APP_DIR")"
  log "[1/6] code: $APP_DIR -> opt-jarvis-agent.tgz (without stonix_ui/node_modules)"
  tar -czf "$B/opt-jarvis-agent.tgz" --exclude="$base/stonix_ui/node_modules" -C "$parent" "$base" || rc=$?
  if (( rc == 1 )); then
    warn "tar: some files under $APP_DIR changed while they were archived"
  elif (( rc != 0 )); then
    fail "tar of $APP_DIR failed (exit $rc)"
    return 0
  fi
  if entries="$(tar -tzf "$B/opt-jarvis-agent.tgz" | wc -l)"; then
    log "      $entries entries"
  else
    fail "opt-jarvis-agent.tgz cannot be listed (damaged archive)"
  fi
}

# ---------------------------------------------------------------- 2. nginx + env files
copy_private() {  # <source> <name in the backup> <required: 0|1>
  local src="$1" dest="$2" required="$3"
  if [[ -f "$src" ]]; then
    if install -m 600 -- "$src" "$B/$dest"; then
      log "      $src -> $dest"
    else
      fail "copy of $src failed"
    fi
  elif (( required )); then
    fail "$src is missing"
  else
    log "      $src not present (skipped)"
  fi
}

backup_files() {
  log "[2/6] nginx + env files (mode 600)"
  copy_private "$NGINX_CONF" jarvis.conf 1
  copy_private "$HTPASSWD" jarvis.htpasswd 0
  copy_private "$HOSTINGER_DIR/.env" hostinger.env 1
  copy_private "$HOSTINGER_DIR/bridge.env" bridge.env 0
  copy_private "$APP_DIR/.env" root.env 0
  copy_private "$APP_DIR/DEPLOYED_SHA" DEPLOYED_SHA 0
}

# ---------------------------------------------------------------- 3. bridge SQLite (online copy)
# Runs inside the bridge container: SQLite online backup API, then an integrity check of the copy.
SQLITE_PY='import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path, timeout=30)
dst = sqlite3.connect(dst_path)
try:
    src.backup(dst)
    result = dst.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    dst.close()
    src.close()
print("integrity_check=" + str(result))
sys.exit(0 if result == "ok" else 3)'

backup_bridge_db() {
  log "[3/6] bridge SQLite: online copy of /app/data/bridge.db"
  if ! bridge_running; then
    DB_STATUS="bridge not running: raw data/ files are in volumes.tgz (no live writer)"
    log "      $DB_STATUS"
    return 0
  fi
  if ! docker exec "$BRIDGE_CTR" test -f /app/data/bridge.db; then
    DB_STATUS="no /app/data/bridge.db yet (skipped)"
    log "      $DB_STATUS"
    return 0
  fi
  CTR_TMP="/tmp/jarvis-backup-$NAME.db"
  if docker exec "$BRIDGE_CTR" python -c "$SQLITE_PY" /app/data/bridge.db "$CTR_TMP" \
      && docker cp "$BRIDGE_CTR:$CTR_TMP" "$B/bridge.db"; then
    chmod 600 "$B/bridge.db"
    DB_SNAPSHOT=1
    DB_STATUS="online copy in bridge.db ($(stat -c %s "$B/bridge.db") bytes, integrity ok)"
    log "      $DB_STATUS"
  else
    rm -f -- "$B/bridge.db"  # only this run's own unfinished copy
    DB_STATUS="online copy FAILED: raw data/bridge.db* files are in volumes.tgz instead"
    warn "$DB_STATUS"
  fi
  docker exec "$BRIDGE_CTR" rm -f -- "$CTR_TMP" >/dev/null 2>&1 || warn "could not remove $CTR_TMP inside $BRIDGE_CTR"
  CTR_TMP=""
}

# ---------------------------------------------------------------- 4. volumes
# Runs inside a container that has the jarvis volumes mounted under /app. With SKIP_LIVE_DB=1 the
# live SQLite files are left out because bridge.db (step 3) is the consistent copy.
# shellcheck disable=SC2016  # expanded by sh inside the container, not here
VOL_SH='set -e
cd /app
set --
for d in logs Jarvis_Outputs Jarvis_Shared data; do
  if [ -e "$d" ]; then set -- "$@" "$d"; fi
done
if [ "$#" -eq 0 ]; then echo "no volume folders under /app" >&2; exit 3; fi
if [ "${SKIP_LIVE_DB:-0}" = 1 ]; then
  exec tar -czf - --exclude=data/bridge.db --exclude=data/bridge.db-wal --exclude=data/bridge.db-shm --exclude=data/bridge.db-journal "$@"
fi
exec tar -czf - "$@"'

volumes_archive_ok() {
  [[ -s "$B/volumes.tgz" ]] && tar -tzf "$B/volumes.tgz" >/dev/null 2>&1
}

backup_volumes() {
  local rc
  log "[4/6] volumes: /app/{logs,Jarvis_Outputs,Jarvis_Shared,data} -> volumes.tgz"
  if bridge_running; then
    rc=0
    docker exec -e "SKIP_LIVE_DB=$DB_SNAPSHOT" "$BRIDGE_CTR" sh -c "$VOL_SH" > "$B/volumes.tgz" || rc=$?
    if (( rc <= 1 )) && volumes_archive_ok; then
      VOLUMES_MODE="docker exec $BRIDGE_CTR"
      (( rc == 0 )) || warn "volumes: some files changed while they were archived"
    else
      warn "docker exec tar in $BRIDGE_CTR failed (exit $rc); trying a one-off compose container"
    fi
  fi
  if [[ -z "$VOLUMES_MODE" ]]; then
    rc=0
    compose run --rm --no-deps -T -e "SKIP_LIVE_DB=$DB_SNAPSHOT" --entrypoint sh "$BRIDGE_SERVICE" -c "$VOL_SH" \
      < /dev/null > "$B/volumes.tgz" || rc=$?
    if (( rc <= 1 )) && volumes_archive_ok; then
      VOLUMES_MODE="docker compose -p $PROJECT run --rm --no-deps $BRIDGE_SERVICE"
      (( rc == 0 )) || warn "volumes: some files changed while they were archived"
    else
      fail "volumes.tgz could not be created (exit $rc)"
      return 0
    fi
  fi
  log "      via $VOLUMES_MODE: $(tar -tzf "$B/volumes.tgz" | wc -l) entries"
}

# ---------------------------------------------------------------- 5. rollback image tags
tag_images() {
  local tag="rollback-$NAME" img ctr_id ref_id src livekit
  log "[5/6] images: running jarvis-bridge / jarvis-frontend images -> :$tag"
  printf '# backup %s, %s\n# kind\tref\timage id\n' "$NAME" "$(date -u +%FT%TZ)" > "$B/images.txt"
  for img in jarvis-bridge jarvis-frontend; do
    # the container and the image repository share the name (compose container_name / image)
    ctr_id="$(docker container inspect -f '{{.Image}}' "$img" 2>/dev/null || true)"
    ref_id="$(docker image inspect -f '{{.Id}}' "$img:hostinger" 2>/dev/null || true)"
    src="${ctr_id:-$ref_id}"
    if [[ -z "$src" ]]; then
      fail "neither container $img nor image $img:hostinger exists; no rollback tag for $img"
      continue
    fi
    if [[ -n "$ctr_id" && -n "$ref_id" && "$ctr_id" != "$ref_id" ]]; then
      warn "$img: the running image ${ctr_id:7:12} is not $img:hostinger (${ref_id:7:12}); tagging the running one"
    fi
    if docker tag "$src" "$img:$tag"; then
      printf 'rollback\t%s:%s\t%s\n' "$img" "$tag" "$src" >> "$B/images.txt"
      log "      $img:$tag -> ${src:7:12}"
    else
      fail "docker tag $src $img:$tag failed"
    fi
  done
  livekit="$(docker container inspect -f '{{.Config.Image}} {{.Image}}' "$LIVEKIT_CTR" 2>/dev/null || true)"
  if [[ -n "$livekit" ]]; then
    printf 'livekit\t%s\t%s\n' "${livekit%% *}" "${livekit#* }" >> "$B/images.txt"
    log "      livekit image: $livekit"
  else
    warn "container $LIVEKIT_CTR not found; LiveKit image id not recorded"
  fi
  {
    printf '\n# docker image ls (jarvis-* and livekit/livekit-server)\n'
    docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}' \
      | grep -E '^(jarvis-|livekit/livekit-server:)' || true
  } >> "$B/images.txt"
}

# ---------------------------------------------------------------- 6. docker state
record_state() {
  local ids id
  log "[6/6] docker state of compose project $PROJECT"
  if ! docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.ID}}\t{{.Status}}\t{{.Ports}}' > "$B/docker-ps.txt"; then
    fail "docker ps failed"
  fi
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" || true)"
  : > "$B/containers.txt"
  for id in $ids; do
    docker container inspect -f '{{.Name}} image={{.Config.Image}} image_id={{.Image}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} started={{.State.StartedAt}} restarts={{.RestartCount}}{{range .Mounts}} mount={{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}}:{{.Destination}}{{end}}' \
      "$id" >> "$B/containers.txt" || warn "docker inspect $id failed"
  done
  docker volume ls --filter "label=com.docker.compose.project=$PROJECT" --format 'volume {{.Name}}' \
    >> "$B/containers.txt" || warn "docker volume ls failed"
  log "      $(grep -c . "$B/docker-ps.txt" || true) lines in docker-ps.txt"
}

# ---------------------------------------------------------------- manifest + checksums
write_manifest() {
  local w e
  {
    printf 'backup:        %s\n' "$NAME"
    printf 'folder:        %s\n' "$B"
    printf 'started_utc:   %s\n' "$STARTED_UTC"
    printf 'finished_utc:  %s\n' "$(date -u +%FT%TZ)"
    printf 'host:          %s\n' "$(hostname)"
    printf 'script:        backup_vps.sh v%s\n' "$SCRIPT_VERSION"
    printf 'label:         %s\n' "${LABEL:--}"
    printf 'rollback_tag:  rollback-%s\n' "$NAME"
    printf 'volumes_via:   %s\n' "${VOLUMES_MODE:-FAILED}"
    printf 'bridge_db:     %s\n' "$DB_STATUS"
    printf 'restore:       deploy/hostinger/RUNBOOK.md, section "Backup & Restore"\n'
    printf '\nfiles (bytes, name):\n'
    (cd "$B" && find . -maxdepth 1 -type f ! -name MANIFEST.txt ! -name SHA256SUMS ! -name '.*' -printf '  %s\t%P\n' \
      | LC_ALL=C sort -k2)
    printf '\nwarnings: %d\n' "${#WARNINGS[@]}"
    for w in "${WARNINGS[@]}"; do printf '  - %s\n' "$w"; done
    printf 'errors: %d\n' "${#ERRORS[@]}"
    for e in "${ERRORS[@]}"; do printf '  - %s\n' "$e"; done
  } > "$B/MANIFEST.txt"
}

write_checksums() {
  (cd "$B" && find . -maxdepth 1 -type f ! -name SHA256SUMS ! -name '.*' -printf '%P\n' | LC_ALL=C sort \
    | xargs -r -d '\n' sha256sum --) > "$B/.SHA256SUMS.tmp"
  mv -- "$B/.SHA256SUMS.tmp" "$B/SHA256SUMS"
}

# ---------------------------------------------------------------- prune (only with the user's OK)
untag_rollback() {  # <backup folder name>
  local name="$1" img ref
  command -v docker >/dev/null 2>&1 || return 0
  for img in jarvis-bridge jarvis-frontend; do
    ref="$img:rollback-$name"
    if docker image inspect "$ref" >/dev/null 2>&1; then
      # never -f: a tag still used by a container is kept
      if docker image rm "$ref" >/dev/null; then
        log "removed tag $ref"
      else
        warn "could not remove tag $ref (still in use?); kept"
      fi
    fi
  done
}

prune_backups() {  # <days> <confirm: 0|1>
  local days="$1" confirm="$2" name ts cutoff i
  local -a names=() doomed=()
  if [[ ! "$days" =~ ^[0-9]{1,5}$ ]] || (( 10#$days < 1 )); then
    usage_error "--prune-older-than-days needs a whole number >= 1"
  fi
  if [[ ! -d "$BACKUP_BASE" ]]; then
    log "nothing to prune: $BACKUP_BASE does not exist"
    return 0
  fi
  if (( confirm )); then take_lock; fi
  # only folders this script made (YYYY-MM-DD_HHMMZ[-label]); hand-made snapshots are never touched
  while IFS= read -r name; do
    if [[ "$name" =~ $NAME_RE ]]; then names+=("$name"); fi
  done < <(find "$BACKUP_BASE" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort)
  cutoff=$(( $(date -u +%s) - 10#$days * 86400 ))
  for (( i = 0; i < ${#names[@]} - KEEP_NEWEST; i++ )); do
    name="${names[$i]}"
    if ! ts="$(date -u -d "${name:0:10} ${name:11:2}:${name:13:2}" +%s 2>/dev/null)"; then
      warn "cannot read the date of $name; kept"
      continue
    fi
    if (( ts < cutoff )); then doomed+=("$name"); fi
  done
  log "prune: ${#names[@]} scripted backup(s); the newest $KEEP_NEWEST are always kept; ${#doomed[@]} older than $days day(s)"
  if (( ${#doomed[@]} == 0 )); then
    log "nothing to prune"
    return 0
  fi
  for name in "${doomed[@]}"; do
    if (( confirm )); then
      rm -rf -- "${BACKUP_BASE:?}/$name"
      log "deleted $BACKUP_BASE/$name"
      untag_rollback "$name"
    else
      log "would delete $BACKUP_BASE/$name and the tags jarvis-{bridge,frontend}:rollback-$name"
    fi
  done
  if (( ! confirm )); then
    log "dry run: nothing deleted. Add --confirm only after the user's OK."
  fi
}

# ---------------------------------------------------------------- backup
main_backup() {
  local stamp n=1
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run this as root on the VPS"
  [[ -d "$APP_DIR" ]] || die "$APP_DIR not found"
  command -v docker >/dev/null 2>&1 || die "docker not found"
  [[ -d "$BACKUP_BASE" ]] || mkdir -m 700 -- "$BACKUP_BASE"
  take_lock

  STARTED_UTC="$(date -u +%FT%TZ)"
  stamp="$(date -u +%Y-%m-%d_%H%MZ)"
  NAME="$stamp${LABEL:+-$LABEL}"
  until mkdir -m 700 -- "$BACKUP_BASE/$NAME" 2>/dev/null; do
    [[ -e "$BACKUP_BASE/$NAME" ]] || die "cannot create $BACKUP_BASE/$NAME"
    n=$(( n + 1 ))
    (( n <= 99 )) || die "too many backups named $stamp${LABEL:+-$LABEL} in one minute"
    NAME="$stamp${LABEL:+-$LABEL}-$n"
  done
  B="$BACKUP_BASE/$NAME"
  log "backup $NAME -> $B"

  backup_code
  backup_files
  backup_bridge_db
  backup_volumes
  tag_images
  record_state
  write_manifest
  find "$B" -type f -exec chmod 600 {} +
  write_checksums
  chmod 700 "$B"

  log "files:"
  (cd "$B" && find . -maxdepth 1 -type f ! -name '.*' -printf '      %M %s\t%P\n' | LC_ALL=C sort -k3)
  if (( ${#ERRORS[@]} )); then
    printf '[backup_vps] FINISHED WITH %d ERROR(S); the partial backup is kept in %s\n' "${#ERRORS[@]}" "$B" >&2
    exit 1
  fi
  log "done: $(du -sh "$B" | cut -f1) in $B, ${#WARNINGS[@]} warning(s); no older backup was touched"
  printf 'BACKUP_DIR=%s\n' "$B"
}

# ---------------------------------------------------------------- arguments
PRUNE_DAYS=""
CONFIRM=0
while (( $# )); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --prune-older-than-days)
      (( $# >= 2 )) || usage_error "--prune-older-than-days needs a number of days"
      PRUNE_DAYS="$2"
      shift 2
      ;;
    --prune-older-than-days=*) PRUNE_DAYS="${1#*=}"; shift ;;
    --confirm) CONFIRM=1; shift ;;
    -*) usage_error "unknown option: $1" ;;
    *)
      [[ -z "$LABEL" ]] || usage_error "only one label is allowed"
      LABEL="$1"
      shift
      ;;
  esac
done

if [[ -n "$PRUNE_DAYS" ]]; then
  [[ -z "$LABEL" ]] || usage_error "--prune-older-than-days takes no label"
  prune_backups "$PRUNE_DAYS" "$CONFIRM"
  exit 0
fi
(( CONFIRM == 0 )) || usage_error "--confirm only goes with --prune-older-than-days"
if [[ -n "$LABEL" && ! "$LABEL" =~ $LABEL_RE ]]; then
  usage_error "the label must be 1-40 characters of A-Z a-z 0-9 . _ - and start with a letter or digit"
fi
main_backup
