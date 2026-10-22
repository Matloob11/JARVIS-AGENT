#!/usr/bin/env bash
# deploy/hostinger/backup_vps.sh v2 - scripted backup of the JARVIS stack on the Hostinger VPS (plan C-11 / OP-07,
# v2 C-128: the VPS brain's data sealed to the PC backup key, env copies in their own folder).
#
# Usage (as root on the VPS):
#   bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh [--with-media] [label]
#   bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh --prune-older-than-days N            # dry run: lists only
#   bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh --prune-older-than-days N --confirm  # deletes (user's OK first)
#
#   --with-media          also put Jarvis_Shared/Downloads into volumes.tgz (left out by default: downloads are media,
#                         kept on the server and only reported, never backed up unless asked, K-75)
#
# Writes /root/jarvis-backups/<YYYY-MM-DD_HHMMZ>[-label]/ (folders 700, files 600):
#   opt-jarvis-agent.tgz  /opt/jarvis-agent without stonix_ui/node_modules (members jarvis-agent/..., restore with -C /opt)
#                         and without deploy/hostinger/.env.agent (and its .tmp / temp copies): the agent's secrets,
#                         its link private key among them (K-70), are only in env/env.agent, which the PC mirror leaves out
#   jarvis.conf           /etc/nginx/conf.d/jarvis.conf
#   jarvis.htpasswd       /etc/nginx/jarvis.htpasswd                     (when present)
#   env/                  the env copies, in a folder of their own (C-128; rollback.sh also reads the v1 layout, where
#                         they sat next to the other files):
#     env/hostinger.env   /opt/jarvis-agent/deploy/hostinger/.env
#     env/bridge.env      /opt/jarvis-agent/deploy/hostinger/bridge.env  (when present)
#     env/env.agent       /opt/jarvis-agent/deploy/hostinger/.env.agent  (when present; C-127; stays on the VPS: the
#                         RUNBOOK's PC mirror leaves it out)
#     env/root.env        /opt/jarvis-agent/.env                         (when present)
#   DEPLOYED_SHA          /opt/jarvis-agent/DEPLOYED_SHA                 (when present)
#   volumes.tgz           /app/{logs,Jarvis_Outputs,Jarvis_Shared,data} of the bridge's named volumes, without
#                         Jarvis_Shared/Downloads unless --with-media
#   bridge.db             online SQLite copy of /app/data/bridge.db      (when present)
#   agent-volumes.tar.enc the VPS brain's data (C-128): a tar of /app/{conversations,chroma_db,data} of the
#                         jarvis_agent_* volumes, every SQLite file in it an online copy, sealed to
#                         BACKUP_RECIPIENT_PUBLIC_KEY (the PC backup key, .env.agent) with services/utils/backup_crypto.py,
#                         the format and the key pair of agent-svc's daily snapshot (K-77). Only the PC can open it
#                         (scripts/ops/restore_agent_data.py, scripts/ops/backup_key.py decrypt); it is never written in
#                         plain text, so without that key (or without an agent image to read the volumes with) the agent
#                         data is skipped with a warning. Made by the image's own code ('python -m
#                         services.utils.agent_volumes') inside the running jarvis-agent (else jarvis-agent-svc, else,
#                         or when the exec fails, a one-off 'compose run' of agent) as uid 10001 with 'env -i': no secret
#                         in that process. A failure is a warning too (MANIFEST agent_data: FAILED): the rest of the
#                         backup stands, so a bridge deploy goes on, while an agent deploy refuses such a backup.
#   images.txt            rollback tags, image ids, LiveKit image id
#   docker-ps.txt         docker ps -a of compose project jarvis
#   containers.txt        image, state and mounts per jarvis container (no env, no secrets)
#   MANIFEST.txt          what was captured, warnings and errors; the brain (JARVIS_BRAIN, vps or pc); the env layout:
#                         where the env copies are (env_dir:) and one 'env_layout:' line per env file the containers
#                         read (.env, bridge.env, .env.agent) with present/absent and the names it assigns a value,
#                         never a value (rollback.sh compares it with the live files, C-127); agent_data: and media:
#   SHA256SUMS            checksums of the files above, env/ included (check with: sha256sum -c SHA256SUMS)
# and tags the images that the jarvis-bridge / jarvis-frontend / jarvis-agent containers run (normally
# jarvis-{bridge,frontend,agent}:hostinger) as jarvis-{bridge,frontend,agent}:rollback-<backup folder name>; a
# jarvis-agent-svc that runs another image than jarvis-agent gets jarvis-agent:rollback-<backup>-agent-svc.
#
# Scope (COORD-4 / K-54): only the jarvis Docker project, /opt/jarvis-agent, the two nginx jarvis
# files and /root/jarvis-backups; read-only everywhere else. It never deletes a backup, except
# with --prune-older-than-days N --confirm, which needs the user's OK first. Prune only looks at
# folders this script made (YYYY-MM-DD_HHMMZ...) and always keeps the newest 3.
# Restore steps: deploy/hostinger/RUNBOOK.md, section "Backup & Restore" (agent data: "Data restore").
# The last line of a successful run is BACKUP_DIR=<folder> (deploy scripts parse it).
# Exit code: 0 ok, 1 error (the partial backup is kept and its folder printed), 2 usage error.
set -euo pipefail
umask 077

# JARVIS_DEPLOY_SANDBOX: test hook only (tests/infrastructure/test_agent_deploy.py), as in deploy.sh. It moves every
# absolute path below into a scratch folder and skips the root check; docker and flock must then be fakes inside that
# folder. Never set it on the VPS.
readonly SANDBOX="${JARVIS_DEPLOY_SANDBOX:-}"
readonly APP_DIR="$SANDBOX/opt/jarvis-agent"
readonly HOSTINGER_DIR="$APP_DIR/deploy/hostinger"
readonly COMPOSE_FILE="$HOSTINGER_DIR/docker-compose.yml"
readonly NGINX_CONF="$SANDBOX/etc/nginx/conf.d/jarvis.conf"
readonly HTPASSWD="$SANDBOX/etc/nginx/jarvis.htpasswd"
readonly PROJECT=jarvis
readonly BRIDGE_CTR=jarvis-bridge
readonly BRIDGE_SERVICE=ui-bridge
readonly LIVEKIT_CTR=jarvis-livekit
readonly AGENT_CTR=jarvis-agent
readonly AGENT_SVC_CTR=jarvis-agent-svc
readonly AGENT_SERVICE=agent
readonly AGENT_IMAGE=jarvis-agent:hostinger
readonly -a AGENT_VOLUMES=(jarvis_agent_conversations jarvis_agent_chroma jarvis_agent_data)
readonly AGENT_ARCHIVE=agent-volumes.tar.enc
readonly ENV_DIR=env
readonly KEEP_NEWEST=3
readonly SCRIPT_VERSION=2
# JARVIS_BACKUP_BASE exists only so the prune logic can be tested on a scratch folder.
readonly BACKUP_BASE="${JARVIS_BACKUP_BASE:-$SANDBOX/root/jarvis-backups}"
readonly NAME_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}Z(-[A-Za-z0-9._-]+)?$'
readonly LABEL_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$'
readonly BOM=$'\xef\xbb\xbf'
# The environment of the agent data step inside the container: 'env -i' drops every .env.agent secret first
readonly -a AGENT_STEP_ENV=(PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp LANG=C.UTF-8)

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
WITH_MEDIA=0
AGENT_STATUS="not checked"
AGENT_TMP=()      # this run's own scratch of the agent step (its stderr, its unfinished sealed output)
ENV_COPIES=()

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
  if (( ${#AGENT_TMP[@]} )); then rm -f -- "${AGENT_TMP[@]}"; fi
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

ctr_running() {  # <container>
  [[ "$(docker container inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

bridge_running() { ctr_running "$BRIDGE_CTR"; }

compose() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$HOSTINGER_DIR/.env" "$@"
}

# ---------------------------------------------------------------- 1. code
backup_code() {
  local parent base rc=0 entries
  parent="$(dirname "$APP_DIR")"
  base="$(basename "$APP_DIR")"
  log "[1/7] code: $APP_DIR -> opt-jarvis-agent.tgz (without stonix_ui/node_modules and .env.agent)"
  # C-127: .env.agent only as env/env.agent (copy_private below). The code archive also goes to the PC mirror, which
  # must never hold the agent's link private key (K-70) or its AI keys; a code restore never extracts env files anyway.
  tar -czf "$B/opt-jarvis-agent.tgz" --exclude="$base/stonix_ui/node_modules" \
    --exclude="$base/deploy/hostinger/.env.agent" --exclude="$base/deploy/hostinger/.env.agent.tmp" \
    --exclude="$base/deploy/hostinger/..env.agent.*" -C "$parent" "$base" || rc=$?
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
copy_private() {  # <source> <name in the backup, may hold a sub-folder> <required: 0|1>
  local src="$1" dest="$2" required="$3"
  if [[ -f "$src" ]]; then
    if install -m 600 -- "$src" "$B/$dest"; then
      log "      $src -> $dest"
      if [[ $dest == "$ENV_DIR"/* ]]; then ENV_COPIES+=("${dest#"$ENV_DIR"/}"); fi
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
  log "[2/7] nginx + env files (mode 600; the env copies in $ENV_DIR/)"
  copy_private "$NGINX_CONF" jarvis.conf 1
  copy_private "$HTPASSWD" jarvis.htpasswd 0
  mkdir -m 700 -- "$B/$ENV_DIR" || { fail "cannot create $B/$ENV_DIR"; return 0; }
  copy_private "$HOSTINGER_DIR/.env" "$ENV_DIR/hostinger.env" 1
  copy_private "$HOSTINGER_DIR/bridge.env" "$ENV_DIR/bridge.env" 0
  copy_private "$HOSTINGER_DIR/.env.agent" "$ENV_DIR/env.agent" 0
  copy_private "$APP_DIR/.env" "$ENV_DIR/root.env" 0
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
  log "[3/7] bridge SQLite: online copy of /app/data/bridge.db"
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
# live SQLite files are left out because bridge.db (step 3) is the consistent copy. Jarvis_Shared/Downloads is left
# out unless WITH_MEDIA=1 (--with-media): downloads are media, reported only (K-75).
# shellcheck disable=SC2016  # expanded by sh inside the container, not here
VOL_SH='set -e
cd /app
set --
for d in logs Jarvis_Outputs Jarvis_Shared data; do
  if [ -e "$d" ]; then set -- "$@" "$d"; fi
done
if [ "$#" -eq 0 ]; then echo "no volume folders under /app" >&2; exit 3; fi
if [ "${WITH_MEDIA:-0}" != 1 ]; then set -- --exclude=Jarvis_Shared/Downloads "$@"; fi
if [ "${SKIP_LIVE_DB:-0}" = 1 ]; then
  exec tar -czf - --exclude=data/bridge.db --exclude=data/bridge.db-wal --exclude=data/bridge.db-shm --exclude=data/bridge.db-journal "$@"
fi
exec tar -czf - "$@"'

volumes_archive_ok() {
  [[ -s "$B/volumes.tgz" ]] && tar -tzf "$B/volumes.tgz" >/dev/null 2>&1
}

backup_volumes() {
  local rc
  log "[4/7] volumes: /app/{logs,Jarvis_Outputs,Jarvis_Shared,data} -> volumes.tgz ($( ((WITH_MEDIA)) && echo 'Jarvis_Shared/Downloads included: --with-media' || echo 'without Jarvis_Shared/Downloads'))"
  if bridge_running; then
    rc=0
    docker exec -e "SKIP_LIVE_DB=$DB_SNAPSHOT" -e "WITH_MEDIA=$WITH_MEDIA" "$BRIDGE_CTR" sh -c "$VOL_SH" > "$B/volumes.tgz" || rc=$?
    if (( rc <= 1 )) && volumes_archive_ok; then
      VOLUMES_MODE="docker exec $BRIDGE_CTR"
      (( rc == 0 )) || warn "volumes: some files changed while they were archived"
    else
      warn "docker exec tar in $BRIDGE_CTR failed (exit $rc); trying a one-off compose container"
    fi
  fi
  if [[ -z "$VOLUMES_MODE" ]]; then
    rc=0
    compose run --rm --no-deps -T -e "SKIP_LIVE_DB=$DB_SNAPSHOT" -e "WITH_MEDIA=$WITH_MEDIA" --entrypoint sh "$BRIDGE_SERVICE" \
      -c "$VOL_SH" < /dev/null > "$B/volumes.tgz" || rc=$?
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

# ---------------------------------------------------------------- 5. agent data (sealed to the PC backup key)
# The image's own code does it (services/utils/agent_volumes.py, run as 'python -m services.utils.agent_volumes /app
# <key>' from /app, uid 10001, under 'env -i'): it walks /app/{conversations,chroma_db,data} of the jarvis_agent_*
# volumes (every SQLite file an online copy from a read-only connection, so a running agent keeps writing, its
# -wal/-shm/-journal left out; links and special files counted, never followed; the '.pre-restore-*' folders that
# restore_agent_data.py moved aside left out; AGENT-BACKUP.json with the counts last) and seals the tar while it makes
# it with services/utils/backup_crypto.py, the format and the key pair of agent-svc's daily snapshot (K-77), so no plain
# byte reaches stdout or a file. scripts/ops/restore_agent_data.py and scripts/ops/backup_key.py decrypt open it on the
# PC. stderr ends with one 'AGENT_SEAL key=value ...' line of counts (recipient=<the key's fingerprint>, as
# 'backup_key.py public' prints it). An image built before that module answers 'No module named ...': the step then
# fails with that reason (a one-off container of jarvis-agent:hostinger, the newer build, is tried after a failed exec).
readonly AGENT_STEP_MODULE=services.utils.agent_volumes
readonly SEALED_MAGIC=JRVSNAP1   # services/utils/backup_crypto.py MAGIC: the first 8 bytes of every sealed file

env_value_of() {  # <file> <name>: the value compose reads for the name, as deploy.sh's env_brain and rollback.sh's
  # env_value_digest read it: the last assignment, a UTF-8 BOM in front of the first line read past, blanks trimmed,
  # outer quotes dropped, an unquoted value cut before ' #' (an inline comment). Only for non-secret names: the brain
  # and the PC backup PUBLIC key.
  [[ -f $1 ]] || return 0
  { awk -v n="$2" -v bom="$BOM" '
    NR == 1 && index($0, bom) == 1 { $0 = substr($0, length(bom) + 1) }
    { sub(/\r$/, "") }
    match($0, "^[ \t]*(export[ \t]+)?" n "[ \t]*=") { v = substr($0, RLENGTH + 1); found = 1 }
    END {
      if (!found) exit
      sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v)
      q = substr(v, 1, 1)
      if (q == "\"" || q == "\047") { v = substr(v, 2); e = index(v, q); if (e) v = substr(v, 1, e - 1) }
      else if (match(v, /[ \t]+#/)) v = substr(v, 1, RSTART - 1)
      print v
    }' "$1" 2>/dev/null || true; } | tail -n 1
}

recipient_key() {  # BACKUP_RECIPIENT_PUBLIC_KEY of .env.agent (K-47: the PC backup key, public), lower-case hex; '' if none
  local v
  v="$(env_value_of "$HOSTINGER_DIR/.env.agent" BACKUP_RECIPIENT_PUBLIC_KEY)"
  if [[ $v =~ ^[0-9a-fA-F]{64}$ ]]; then printf '%s\n' "${v,,}"; fi
}

brain_of() {  # JARVIS_BRAIN of the compose .env (vps or pc; the brain switch's source of truth, K-72), else unknown
  local v
  v="$(env_value_of "$HOSTINGER_DIR/.env" JARVIS_BRAIN)"
  case "$v" in vps|pc) printf '%s\n' "$v" ;; *) printf 'unknown\n' ;; esac
}

backup_agent_data() {
  local vol key runner rc=0 part="$B/.$AGENT_ARCHIVE.part" errf summary magic
  local -a present=()
  log "[5/7] agent data: online SQLite copies + tar of /app/{conversations,chroma_db,data} -> $AGENT_ARCHIVE (sealed to the PC backup key)"
  for vol in "${AGENT_VOLUMES[@]}"; do
    if docker volume inspect "$vol" >/dev/null 2>&1; then present+=("$vol"); fi
  done
  if (( ${#present[@]} == 0 )); then
    AGENT_STATUS="none (no jarvis_agent_* volume: the VPS brain never ran here)"
    log "      $AGENT_STATUS"
    return 0
  fi
  key="$(recipient_key)"
  if [[ -z $key ]]; then
    AGENT_STATUS="SKIPPED: .env.agent has no BACKUP_RECIPIENT_PUBLIC_KEY (64 hex digits), and agent data is never written in plain text"
    warn "agent data not backed up: $AGENT_STATUS (push it with scripts/ops/push_agent_env.py)"
    return 0
  fi
  if ctr_running "$AGENT_CTR"; then
    runner="docker exec $AGENT_CTR"
  elif ctr_running "$AGENT_SVC_CTR"; then
    runner="docker exec $AGENT_SVC_CTR"
  elif ! docker image inspect "$AGENT_IMAGE" >/dev/null 2>&1; then
    AGENT_STATUS="SKIPPED: the volumes exist but no $AGENT_IMAGE image is there to read them with"
    warn "agent data not backed up: $AGENT_STATUS"
    return 0
  elif [[ ! -f $HOSTINGER_DIR/.env.agent ]]; then
    AGENT_STATUS="SKIPPED: no .env.agent, which the one-off agent container needs"
    warn "agent data not backed up: $AGENT_STATUS"
    return 0
  else
    runner="docker compose -p $PROJECT run --rm --no-deps $AGENT_SERVICE"
  fi
  errf="$(mktemp "$BACKUP_BASE/.agent-seal.XXXXXX")"
  AGENT_TMP=("$errf" "$part")
  if [[ $runner == "docker exec "* ]]; then
    docker exec -w /app "${runner#docker exec }" env -i "${AGENT_STEP_ENV[@]}" python -m "$AGENT_STEP_MODULE" /app "$key" \
      > "$part" 2> "$errf" || rc=$?
    # a container that is running but cannot take the exec (a restart loop), or that runs an image older than the
    # module: a one-off container of jarvis-agent:hostinger (before an agent deploy, the newer build) reads the volumes
    if (( rc != 0 )) && docker image inspect "$AGENT_IMAGE" >/dev/null 2>&1 && [[ -f $HOSTINGER_DIR/.env.agent ]]; then
      warn "agent data: docker exec in ${runner#docker exec } failed (exit $rc); trying a one-off agent container"
      runner="docker compose -p $PROJECT run --rm --no-deps $AGENT_SERVICE"
      rc=0
    fi
  fi
  if [[ $runner != "docker exec "* ]]; then
    compose run --rm --no-deps -T -w /app --entrypoint env "$AGENT_SERVICE" -i "${AGENT_STEP_ENV[@]}" \
      python -m "$AGENT_STEP_MODULE" /app "$key" < /dev/null > "$part" 2> "$errf" || rc=$?
  fi
  summary="$(sed -n 's/^AGENT_SEAL //p' "$errf" | tail -n 1)"
  magic="$(head -c 8 "$part" 2>/dev/null || true)"
  if (( rc == 0 )) && [[ -n $summary && $magic == "$SEALED_MAGIC" ]]; then
    mv -f -- "$part" "$B/$AGENT_ARCHIVE"
    chmod 600 "$B/$AGENT_ARCHIVE"
    AGENT_STATUS="sealed via $runner: $summary"
    log "      $AGENT_STATUS"
    if [[ $summary =~ (^| )sqlite_raw=[1-9] ]]; then warn "agent data: an SQLite file could not be copied online; its raw files are in $AGENT_ARCHIVE"; fi
    if [[ $summary =~ (^| )sqlite_bad=[1-9] ]]; then warn "agent data: an SQLite copy failed its integrity check (AGENT-BACKUP.json inside names it)"; fi
    if [[ $summary =~ (^| )changed=[1-9] ]]; then warn "agent data: some files changed while they were read"; fi
  else
    rm -f -- "$part"   # only this run's own unfinished (sealed) output
    AGENT_STATUS="FAILED via $runner (exit $rc): $({ grep -E '^AGENT_SEAL_ERROR|Error|error|No module named' "$errf" || true; } | tail -n 1 | cut -c1-200)"
    # a warning, not an error: the rest of the backup stands (a bridge deploy goes on); an agent deploy refuses it
    warn "agent data not backed up: $AGENT_STATUS"
  fi
  rm -f -- "$errf"
  AGENT_TMP=()
}

# ---------------------------------------------------------------- 6. rollback image tags
tag_one() {  # <source image id> <tag ref>: tags it and records it in images.txt
  if docker tag "$1" "$2"; then
    printf 'rollback\t%s\t%s\n' "$2" "$1" >> "$B/images.txt"
    log "      $2 -> ${1:7:12}"
  else
    fail "docker tag $1 $2 failed"
  fi
}

tag_images() {
  local tag="rollback-$NAME" img ctr_id ref_id src livekit agent_src="" svc_src
  log "[6/7] images: running jarvis-bridge / jarvis-frontend / jarvis-agent images -> :$tag"
  printf '# backup %s, %s\n# kind\tref\timage id\n' "$NAME" "$(date -u +%FT%TZ)" > "$B/images.txt"
  for img in jarvis-bridge jarvis-frontend jarvis-agent; do
    # the container and the image repository share the name (compose container_name / image)
    ctr_id="$(docker container inspect -f '{{.Image}}' "$img" 2>/dev/null || true)"
    ref_id="$(docker image inspect -f '{{.Id}}' "$img:hostinger" 2>/dev/null || true)"
    src="${ctr_id:-$ref_id}"
    if [[ -z "$src" ]]; then
      if [[ $img == jarvis-agent ]]; then
        log "      jarvis-agent: no container and no $AGENT_IMAGE image yet (the VPS brain is not built): no rollback tag"
      else
        fail "neither container $img nor image $img:hostinger exists; no rollback tag for $img"
      fi
      continue
    fi
    if [[ -n "$ctr_id" && -n "$ref_id" && "$ctr_id" != "$ref_id" ]]; then
      if [[ $img == jarvis-agent ]]; then   # normal before an agent deploy: the dedicated builder made a new image
        log "      jarvis-agent runs ${ctr_id:7:12}; $AGENT_IMAGE is ${ref_id:7:12} (a newer build not deployed yet)"
      else
        warn "$img: the running image ${ctr_id:7:12} is not $img:hostinger (${ref_id:7:12}); tagging the running one"
      fi
    fi
    tag_one "$src" "$img:$tag"
    if [[ $img == jarvis-agent ]]; then agent_src="$src"; fi
  done
  # agent-svc runs the same repository (jarvis-agent:hostinger). When its container runs another image than the agent
  # (a deploy of 'agent' alone), that image gets its own tag, so it stays referenced and rollback.sh can bring it back.
  svc_src="$(docker container inspect -f '{{.Image}}' "$AGENT_SVC_CTR" 2>/dev/null || true)"
  if [[ -n $svc_src && $svc_src != "$agent_src" ]]; then
    tag_one "$svc_src" "jarvis-agent:$tag-agent-svc"
  fi
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

# ---------------------------------------------------------------- 7. docker state
record_state() {
  local ids id
  log "[7/7] docker state of compose project $PROJECT"
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
# C-127: the env layout, names only. The same name rule as env_names() in rollback.sh (a test keeps them equal): the
# names a file assigns a non-empty value ('NAME=x' or 'export NAME = x'), sorted, comma-separated, '-' for none. A
# UTF-8 BOM in front of the first line is read past (compose does too), so the name on that line is recorded.
env_names() {  # <file>
  local names bom=$'\xef\xbb\xbf'
  names="$({ sed -n -E -e "1s/^$bom//" -e 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*[^[:space:]#].*$/\2/p' "$1" \
    || true; } | LC_ALL=C sort -u | paste -sd, -)"
  printf '%s\n' "${names:--}"
}

env_layout_lines() {  # one 'env_layout: <file> <present|absent> <names>' line per env file the containers read
  local f
  for f in .env bridge.env .env.agent; do
    if [[ -f "$HOSTINGER_DIR/$f" ]]; then
      printf 'env_layout:    %s present %s\n' "$f" "$(env_names "$HOSTINGER_DIR/$f")"
    else
      printf 'env_layout:    %s absent -\n' "$f"
    fi
  done
}

write_manifest() {
  local w e
  {
    printf 'backup:        %s\n' "$NAME"
    printf 'folder:        %s\n' "$B"
    printf 'started_utc:   %s\n' "$STARTED_UTC"
    printf 'finished_utc:  %s\n' "$(date -u +%FT%TZ)"
    printf 'host:          %s\n' "$(hostname 2>/dev/null || uname -n)"
    printf 'script:        backup_vps.sh v%s\n' "$SCRIPT_VERSION"
    printf 'label:         %s\n' "${LABEL:--}"
    printf 'brain:         %s\n' "$(brain_of)"
    printf 'rollback_tag:  rollback-%s\n' "$NAME"
    printf 'volumes_via:   %s\n' "${VOLUMES_MODE:-FAILED}"
    printf 'bridge_db:     %s\n' "$DB_STATUS"
    printf 'agent_data:    %s\n' "$AGENT_STATUS"
    printf 'media:         %s\n' "$( ((WITH_MEDIA)) && echo 'included (--with-media)' || echo 'excluded (Jarvis_Shared/Downloads; --with-media takes it)')"
    printf 'restore:       deploy/hostinger/RUNBOOK.md, section "Backup & Restore"; agent data: scripts/ops/restore_agent_data.py %s on the PC\n' "$NAME"
    printf 'env_dir:       %s/ (%s)\n' "$ENV_DIR" "${ENV_COPIES[*]:-no env copy}"
    env_layout_lines
    printf '\nfiles (bytes, name):\n'
    (cd "$B" && find . -type f ! -name MANIFEST.txt ! -name SHA256SUMS ! -name '.*' -printf '  %s\t%P\n' \
      | LC_ALL=C sort -k2)
    printf '\nwarnings: %d\n' "${#WARNINGS[@]}"
    for w in "${WARNINGS[@]}"; do printf '  - %s\n' "$w"; done
    printf 'errors: %d\n' "${#ERRORS[@]}"
    for e in "${ERRORS[@]}"; do printf '  - %s\n' "$e"; done
  } > "$B/MANIFEST.txt"
}

write_checksums() {
  (cd "$B" && find . -type f ! -name SHA256SUMS ! -name '.*' -printf '%P\n' | LC_ALL=C sort \
    | xargs -r -d '\n' sha256sum --) > "$B/.SHA256SUMS.tmp"
  mv -- "$B/.SHA256SUMS.tmp" "$B/SHA256SUMS"
}

# ---------------------------------------------------------------- prune (only with the user's OK)
untag_rollback() {  # <backup folder name>
  local name="$1" ref
  command -v docker >/dev/null 2>&1 || return 0
  for ref in "jarvis-bridge:rollback-$name" "jarvis-frontend:rollback-$name" "jarvis-agent:rollback-$name" \
      "jarvis-agent:rollback-$name-agent-svc"; do
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
      log "would delete $BACKUP_BASE/$name and the tags jarvis-{bridge,frontend,agent}:rollback-$name"
    fi
  done
  if (( ! confirm )); then
    log "dry run: nothing deleted. Add --confirm only after the user's OK."
  fi
}

# ---------------------------------------------------------------- backup
require_host() {
  local p
  if [[ -n $SANDBOX ]]; then
    p="$(command -v docker 2>/dev/null || true)"
    [[ $p == "$SANDBOX"/* ]] || die "sandbox: 'docker' must be a fake inside the sandbox (found '${p:-nothing}')"
    return 0
  fi
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run this as root on the VPS"
  command -v docker >/dev/null 2>&1 || die "docker not found"
}

main_backup() {
  local stamp n=1
  require_host
  [[ -d "$APP_DIR" ]] || die "$APP_DIR not found"
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
  backup_agent_data
  tag_images
  record_state
  write_manifest
  find "$B" -type f -exec chmod 600 {} +
  write_checksums
  find "$B" -type d -exec chmod 700 {} +

  log "files:"
  (cd "$B" && find . -type f ! -name '.*' -printf '      %M %s\t%P\n' | LC_ALL=C sort -k3)
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
    --with-media) WITH_MEDIA=1; shift ;;
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
  (( WITH_MEDIA == 0 )) || usage_error "--with-media goes with a backup, not with --prune-older-than-days"
  prune_backups "$PRUNE_DAYS" "$CONFIRM"
  exit 0
fi
(( CONFIRM == 0 )) || usage_error "--confirm only goes with --prune-older-than-days"
if [[ -n "$LABEL" && ! "$LABEL" =~ $LABEL_RE ]]; then
  usage_error "the label must be 1-40 characters of A-Z a-z 0-9 . _ - and start with a letter or digit"
fi
main_backup
