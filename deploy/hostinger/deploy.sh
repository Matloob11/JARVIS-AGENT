#!/usr/bin/env bash
# deploy/hostinger/deploy.sh v2 - deploy JARVIS services on the Hostinger VPS without touching the other apps
# (plan C-12 / OP-08, contract K-54 COORD-4). deploy_from_pc.sh starts it with the committed HEAD; rollback.sh
# sources this file for the shared functions.
#
# Usage (as root on the VPS):
#   deploy.sh --services LIST [--nginx] [--run-dir DIR --sha SHA] [--dry-run] [--foreground] [--no-auto-rollback]
#   deploy.sh --gate-only [--services LIST]     health gate only (read-only)
#
#   --services LIST     compose services to deploy (required), separated by spaces or commas, or 'none'. Only these
#                       are pulled or built and recreated, always with 'docker compose -p jarvis up -d --no-deps';
#                       no other container is touched. The frontend is deployed only when it is named (RUNBOOK,
#                       "The frontend rule"). Any service in docker-compose.yml can be named, profiles included.
#   --nginx             also install deploy/hostinger/jarvis.conf as /etc/nginx/conf.d/jarvis.conf: nginx -t, then
#                       'systemctl reload nginx'; when the test fails the old file is put back and nothing reloads
#   --bootstrap-cert    with --nginx, only if the JARVIS certificate is missing: one-time certbot --webroot bootstrap
#   --run-dir DIR       run folder under /root/jarvis-backups/runs. With DIR/shipped.tar.gz (staged by
#                       deploy_from_pc.sh) that code is installed into /opt/jarvis-agent after the backup; without
#                       it the code already in /opt/jarvis-agent is deployed.
#   --sha SHA           the commit being deployed (required for a staged run; written to /opt/jarvis-agent/DEPLOYED_SHA)
#   --dry-run           validate and print the plan; change nothing
#   --foreground        stay attached: Ctrl-C (or the hang-up of a terminal session) stops the run, which then rolls
#                       back what it changed. By default the run continues detached from this ssh session and its log
#                       is streamed here, so Ctrl-C or a dropped connection stops only the view, never the deploy.
#                       Either way the run writes to runs/<run>/run.log, so a closed output never stops it half-way.
#   --no-auto-rollback  after a failed gate print the rollback command instead of running it
#
# Steps: lock -> tools copy -> backup_vps.sh -> record + rollback tags -> smoke (pre) -> install code -> perms
#   -> compose config -> pull/build -> nginx -> up -d --no-deps <services> -> health gate -> smoke (post)
#   -> after any failure: automatic rollback to the pre-deploy image tags, code and nginx conf. Only JARVIS's own
#   jarvis-* tags are re-pointed; a third-party tag (LiveKit) is never moved onto another version.
# Files: /root/jarvis-backups/<backup>/ gets deploy.env, deploy-images.txt, smoke-pre.txt and smoke-post.txt, and
#   nginx-test.txt (the full 'nginx -t' output, which can name other apps, so it is never printed);
#   also deploy.log, LAST_DEPLOY, tools/ (copies of these scripts that survive a code rollback) and
#   runs/<run>/ (run.log, rc, shipped.tar.gz).
# Exit codes: 0 deployed and healthy; 1 refused or failed before any live change (the running services were not
#   changed); 2 a gate failed and the automatic rollback restored the pre-deploy state; 3 the rollback failed or
#   was disabled (act now: the command is printed); 64 usage error.
set -euo pipefail
umask 077

# shellcheck disable=SC2034  # rollback.sh greps for this line before it sources the file
readonly DEPLOY_LIB_VERSION=2
# JARVIS_DEPLOY_SANDBOX: test hook only (tests/unit/test_deploy_tooling.py). It moves every absolute path below into
# a scratch folder and skips the root/Linux checks; docker, nginx, systemctl, curl, certbot and flock must then be
# fakes inside that folder. Never set it on the VPS.
readonly SANDBOX="${JARVIS_DEPLOY_SANDBOX:-}"
readonly APP_DIR="$SANDBOX/opt/jarvis-agent"
readonly H="$APP_DIR/deploy/hostinger"
readonly COMPOSE_FILE="$H/docker-compose.yml"
readonly ENV_FILE="$H/.env"
readonly PROJECT=jarvis
readonly BASE="$SANDBOX/root/jarvis-backups"
readonly LOCK_FILE="$BASE/.deploy.lock"
readonly DEPLOY_LOG="$BASE/deploy.log"
readonly LAST_DEPLOY_FILE="$BASE/LAST_DEPLOY"
readonly TOOLS_DIR="$BASE/tools"
readonly RUNS_DIR="$BASE/runs"
readonly NGINX_CONF="$SANDBOX/etc/nginx/conf.d/jarvis.conf"
readonly LE_DIR="$SANDBOX/etc/letsencrypt"
readonly WEBROOT="$SANDBOX/var/www/html"
readonly DOMAIN=jarvis.srv1802669.hstgr.cloud
readonly BRIDGE_HEALTH_URL=http://127.0.0.1:5101/health
readonly FRONTEND_URL=http://127.0.0.1:5100/
readonly LIVEKIT_URL=http://127.0.0.1:7880/
readonly -a VIA_NGINX=(-k --resolve "$DOMAIN:443:127.0.0.1")
readonly -a TOOL_FILES=(deploy.sh rollback.sh smoke_other_apps.sh backup_vps.sh)
readonly DISK_FAIL_GB=5        # no build below this: a full disk hurts every app on the server
readonly DISK_WARN_GB=10
readonly CERT_WARN_DAYS=14
readonly NAME_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}Z?(-[A-Za-z0-9._-]+)?$'   # backup folders (+ the 2340 one)
readonly RUN_ID_RE='^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,60}$'
readonly SERVICE_RE='^[a-z0-9][a-z0-9_.-]{0,62}$'
readonly SHA_RE='^[0-9a-f]{7,40}$'
readonly INSPECT_FMT='{{.Name}}|{{.Config.Image}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}'
# Files a running container reads from the code dir (bind mounts). They are installed only when their service is
# deployed, so a container that is not recreated never meets a new file on its next restart (the sanitized
# livekit.yaml has no keys: line; only a recreated livekit gets LIVEKIT_KEYS from the compose file).
declare -A HOLD_FILES=([livekit]="deploy/hostinger/livekit.yaml")
if [[ -n $SANDBOX ]]; then
  POLL_S=0.2; STABLE_S=0; GATE_CAP_S=3; RECHECK_S=0
else
  POLL_S=3; STABLE_S=10; GATE_CAP_S=0; RECHECK_S=20   # a smoke regression must survive a 20 s re-check
fi

# Health gate per service. A service without an entry gets the generic gate: its container must be running and,
# when it has a Docker healthcheck, healthy within 120 s. A new compose service (for example agent / agent-svc
# from the VPS-brain plan) needs no change here unless it wants a longer wait or HTTP probes.
gate_timeout() {
  case "$1" in
    ui-bridge) echo 120 ;;
    frontend) echo 90 ;;
    livekit) echo 60 ;;
    agent|agent-svc) echo 240 ;;
    *) echo 120 ;;
  esac
}

gate_probes() {  # <service> <FAIL|WARN> <tries>
  case "$1" in
    ui-bridge)
      expect_http "$2" "bridge $BRIDGE_HEALTH_URL" '200' "$3" "$BRIDGE_HEALTH_URL"
      expect_http "$2" "nginx /vortex/health" '200' "$3" "${VIA_NGINX[@]}" "https://$DOMAIN/vortex/health"
      ;;
    frontend)
      expect_http "$2" "frontend $FRONTEND_URL" '200' "$3" "$FRONTEND_URL"
      expect_http "$2" "nginx /" '200|401' "$3" "${VIA_NGINX[@]}" "https://$DOMAIN/"
      ;;
    livekit)
      expect_http "$2" "livekit $LIVEKIT_URL" '200' "$3" "$LIVEKIT_URL"
      expect_http "$2" "nginx /rtc/validate" '401' "$3" "${VIA_NGINX[@]}" "https://$DOMAIN/rtc/validate"
      ;;
  esac
}

# ------------------------------------------------------------------ state
LOG_TAG=deploy
MODE=deploy
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES=()
NGINX=0
BOOTSTRAP_CERT=0
RUN=""
STAGED=0
TREE=""
SHA=""
SHA_FULL=""
DRY_RUN=0
FOREGROUND=0
AUTO_ROLLBACK=1
GATE_ONLY=0
GIT_SHA_ENV=unknown
B=""
BNAME=""
PREV_SHA=none
PHASE=init
CODE_CHANGED=0
NGINX_CHANGED=0
TOUCHED=()
HELD=()
RESULT=""
FINAL_RC=""
ROOT_CODE_PRE=""
WAITED_HEALTH=""
STARTED_EPOCH=0
WARNS=()
FAILS=()

log()   { printf '[%s] %s\n' "$LOG_TAG" "$*"; }
step()  { printf '\n[%s] == %s\n' "$LOG_TAG" "$*"; }
warn()  { WARNS+=("$*"); printf '[%s] WARN: %s\n' "$LOG_TAG" "$*"; }
fail()  { FAILS+=("$*"); printf '[%s] FAIL: %s\n' "$LOG_TAG" "$*"; }
report() { if [[ $1 == FAIL ]]; then fail "${@:2}"; else warn "${@:2}"; fi; }
die()   { printf '[%s] ERROR: %s\n' "$LOG_TAG" "$*" >&2; exit 1; }
usage_error() { printf '[%s] %s (see --help)\n' "$LOG_TAG" "$*" >&2; exit 64; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$1"; }
safe() { printf '%s' "${1//[^A-Za-z0-9_.,:=\/ -]/?}"; }   # echo user input without control characters
in_list() { local x="$1" y; shift; for y in "$@"; do [[ $x == "$y" ]] && return 0; done; return 1; }
now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

compose_file() {  # <compose file> <args...>: always project jarvis, paths and env files from the live dir
  local f="$1"
  shift
  GIT_SHA="$GIT_SHA_ENV" docker compose -p "$PROJECT" -f "$f" --project-directory "$H" --env-file "$ENV_FILE" "$@"
}
compose() { compose_file "$COMPOSE_FILE" "$@"; }

own_image() { [[ ${1%%[:@]*} == jarvis-* ]]; }   # an image JARVIS builds itself (jarvis-<name>:hostinger)

service_image() {  # <compose file, or - for stdin> <service>: the image ref that compose file gives this one service.
  # Not 'config --images <svc>': that also prints the images of its depends_on services, in random order.
  { compose_file "$1" config --no-env-resolution --format json "$2" 2>/dev/null || true; } \
    | awk -v svc="$2" -v dflt="$PROJECT-$2" '
      $0 == "  \"services\": {" { inside = 1; next }
      inside && /^  }/ { exit }
      inside && $0 == "    \"" svc "\": {" { found = 1; next }
      found && /^    }/ { exit }
      found && /^      "image": "/ { img = $0; sub(/^      "image": "/, "", img); sub(/",?$/, "", img); exit }
      END { if (img != "") print img; else if (found) print dflt }'
}

ctr_id() {  # <service> [all]: id of the service's container (running only, unless 'all'); never fails
  local -a args=(ps -q)
  [[ ${2:-} == all ]] && args+=(-a)
  { compose "${args[@]}" "$1" 2>/dev/null || true; } | sed -n 1p
}
ctr_info() { docker inspect -f "$INSPECT_FMT" "$1" 2>/dev/null; }   # name|ref|image id|status|health|restarts|started

record_write() {  # <file> <KEY=value>...: a new record
  local kv
  : > "$1"
  for kv in "${@:2}"; do printf '%s\n' "${kv//$'\n'/ }" >> "$1"; done
}

record_set() {  # <file> <key> <value>: replace or append KEY=value
  local f="$1" k="$2" v="${3//$'\n'/ }"
  {
    if [[ -f $f ]]; then grep -v -- "^$k=" "$f" || true; fi
    printf '%s=%s\n' "$k" "$v"
  } > "$f.tmp.$$"
  mv -f -- "$f.tmp.$$" "$f"
}
record_get() { if [[ -f $1 ]]; then sed -n "s/^$2=//p" "$1" | tail -n 1; fi; }

parse_services() {  # <raw list> -> SERVICES ('none' -> empty)
  local -a toks=()
  local tok
  SERVICES=()
  read -r -a toks <<< "${1//,/ }"
  for tok in "${toks[@]}"; do
    [[ $tok =~ $SERVICE_RE ]] || usage_error "not a service name: '$(safe "$tok")'"
    in_list "$tok" "${SERVICES[@]}" || SERVICES+=("$tok")
  done
  (( ${#SERVICES[@]} )) || usage_error "--services is empty (name services, or 'none')"
  if in_list none "${SERVICES[@]}"; then
    (( ${#SERVICES[@]} == 1 )) || usage_error "'none' cannot be combined with service names"
    SERVICES=()
  fi
}

require_host() {
  local c p
  if [[ -n $SANDBOX ]]; then
    for c in docker nginx systemctl curl certbot flock; do
      p="$(command -v "$c" 2>/dev/null || true)"
      [[ $p == "$SANDBOX"/* ]] || die "sandbox: '$c' must be a fake inside the sandbox (found '${p:-nothing}')"
    done
    return 0
  fi
  [[ "$(uname -s)" == Linux ]] || die "run this on the VPS (Linux), not on $(uname -s)"
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "run this as root on the VPS"
  for c in docker flock curl tar gzip sha256sum awk sed find install cmp; do
    command -v "$c" >/dev/null 2>&1 || die "'$c' not found"
  done
}

validate_run_dir() {  # <dir>
  [[ $1 == "$RUNS_DIR"/* && $(basename "$1") =~ $RUN_ID_RE && $(dirname "$1") == "$RUNS_DIR" ]] \
    || die "--run-dir must be $RUNS_DIR/<run id>"
  [[ -d $1 ]] || die "$1 does not exist"
}

new_run_dir() {  # <kind> -> prints the new folder
  local d
  d="$RUNS_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$1-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
  mkdir -p -- "$RUNS_DIR"
  mkdir -- "$d"
  printf '%s\n' "$d"
}

cleanup_tree() {  # the extracted copy of a staged run is scratch; shipped.tar.gz and the log stay
  if [[ -n $RUN && -d $RUN/tree && $(basename "$RUN") =~ $RUN_ID_RE && $(dirname "$RUN") == "$RUNS_DIR" ]]; then
    rm -rf -- "${RUN:?}/tree"
  fi
}

disk_free_gb() { { df -Pk -- "$1" 2>/dev/null || true; } | awk 'NR == 2 { printf "%d", $4 / 1048576 }'; }

code_archive_ok() {  # <backup dir>: opt-jarvis-agent.tgz exists and holds jarvis-agent/... (backup_vps.sh layout)
  local first
  [[ -f $1/opt-jarvis-agent.tgz ]] || return 1
  first="$({ tar -tzf "$1/opt-jarvis-agent.tgz" 2>/dev/null || true; } | sed -n 1p)"
  [[ $first == "$(basename "$APP_DIR")"/* || $first == "$(basename "$APP_DIR")" ]]
}

cert_days_left() {
  local pem="$LE_DIR/live/$DOMAIN/fullchain.pem" end end_s
  [[ -r $pem ]] || return 1
  end="$(openssl x509 -enddate -noout -in "$pem" 2>/dev/null)" || return 1
  end_s="$(date -u -d "${end#notAfter=}" +%s 2>/dev/null)" || return 1
  printf '%d\n' $(( (end_s - $(date -u +%s)) / 86400 ))
}

http_code() { curl -s -o /dev/null -m 8 --connect-timeout 4 -w '%{http_code}' "$@" 2>/dev/null || true; }

expect_http() {  # <FAIL|WARN> <label> <code regex> <tries> <curl args...>; records, never fails
  local level="$1" label="$2" want="$3" tries="$4" code="" i
  shift 4
  for (( i = 1; i <= tries; i++ )); do
    code="$(http_code "$@")"
    if [[ $code =~ ^($want)$ ]]; then
      log "  ok   $label -> $code"
      return 0
    fi
    if (( i < tries )); then sleep "$POLL_S"; fi
  done
  report "$level" "$label -> ${code:-000} (want $want)"
  return 0
}

wait_healthy() {  # <service> <timeout s>: healthy, or running steadily when there is no healthcheck
  local svc="$1" timeout="$2" t0=$SECONDS id info status health started seen="" since=0 last="no container"
  if (( GATE_CAP_S > 0 && timeout > GATE_CAP_S )); then timeout=$GATE_CAP_S; fi
  while :; do
    id="$(ctr_id "$svc")"
    if [[ -n $id ]] && info="$(ctr_info "$id")"; then
      IFS='|' read -r _ _ _ status health _ started <<< "$info"
      last="status=$status health=$health"
      if [[ $status == running ]]; then
        if [[ $health == healthy ]]; then WAITED_HEALTH=healthy; return 0; fi
        if [[ $health == none ]]; then
          if [[ $started != "$seen" ]]; then seen="$started"; since=$SECONDS; fi
          if (( SECONDS - since >= STABLE_S )); then WAITED_HEALTH="running (no healthcheck)"; return 0; fi
        fi
      fi
    fi
    if (( SECONDS - t0 >= timeout )); then
      log "  $svc: not healthy after ${timeout}s ($last)"
      return 1
    fi
    sleep "$POLL_S"
  done
}

gate_service() {  # <service> <FAIL|WARN> <wait 0|1>
  local svc="$1" level="$2" id info status="" health="" tries=1
  if (( $3 )); then
    if wait_healthy "$svc" "$(gate_timeout "$svc")"; then
      log "  ok   $svc: container $WAITED_HEALTH"
    else
      report "$level" "$svc: container not healthy"
    fi
    tries=5
  else
    id="$(ctr_id "$svc" all)"
    [[ -n $id ]] || return 0   # not part of this server (yet): nothing to check
    info="$(ctr_info "$id" || true)"
    IFS='|' read -r _ _ _ status health _ _ <<< "$info"
    if [[ $status != running || $health == unhealthy ]]; then
      report "$level" "$svc: container $status ($health)"
    else
      log "  ok   $svc: $status$([[ $health == none ]] && echo ' (no healthcheck)' || echo " ($health)")"
    fi
  fi
  gate_probes "$svc" "$level" "$tries"
}

livekit_keys_match() {  # the live livekit.yaml keys: entries hold LIVEKIT_API_KEY/SECRET of .env; nothing is printed
  local key secret pair
  local -a pairs=()
  key="$(sed -n 's/^LIVEKIT_API_KEY=//p' "$ENV_FILE" | tail -n 1 | tr -d "\r\"'")"
  secret="$(sed -n 's/^LIVEKIT_API_SECRET=//p' "$ENV_FILE" | tail -n 1 | tr -d "\r\"'")"
  [[ -n $key && -n $secret ]] || return 1
  mapfile -t pairs < <(awk '/^keys:/ { k = 1; next } k && /^[^[:space:]#]/ { exit }
    k && /^[[:space:]]+[^[:space:]#]/ { sub(/^[[:space:]]+/, ""); gsub(/["\047\r]/, ""); sub(/[[:space:]]+#.*$/, ""); print }' \
    "$H/livekit.yaml")
  for pair in "${pairs[@]}"; do
    [[ $pair == "$key: $secret" || $pair == "$key:$secret" ]] && return 0
  done
  return 1
}

livekit_keys_check() {  # a livekit.yaml without keys: needs LIVEKIT_KEYS in the running container (names only)
  local yaml="$H/livekit.yaml" id n
  [[ -f $yaml ]] || return 0
  if grep -q '^keys:' "$yaml"; then return 0; fi
  id="$(ctr_id livekit)"
  [[ -n $id ]] || return 0
  n="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$id" 2>/dev/null | grep -c '^LIVEKIT_KEYS=' || true)"
  if [[ ${n:-0} == 0 ]]; then
    warn "livekit: livekit.yaml has no keys: line and the running container has no LIVEKIT_KEYS, so it would fail on its next restart; deploy livekit in a maintenance window"
  fi
}

host_checks() {
  local days free root docker_root
  if days="$(cert_days_left)"; then
    if (( days < CERT_WARN_DAYS )); then warn "the certificate of $DOMAIN expires in $days days"; else log "  ok   certificate: $days days left"; fi
  else
    warn "cannot read the certificate of $DOMAIN"
  fi
  docker_root="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
  [[ -n $docker_root && -d $docker_root ]] || docker_root="$APP_DIR"
  free="$(disk_free_gb "$docker_root")"
  if [[ -n $free ]] && (( free < DISK_WARN_GB )); then
    warn "only ${free} GB free on the Docker disk (nothing is pruned on this shared server; ask the user)"
  elif [[ -n $free ]]; then
    log "  ok   disk: ${free} GB free"
  fi
  livekit_keys_check
  root="$(http_code "${VIA_NGINX[@]}" "https://$DOMAIN/")"
  if [[ -n $ROOT_CODE_PRE && $root != "$ROOT_CODE_PRE" ]]; then
    warn "https://$DOMAIN/ answered $ROOT_CODE_PRE before and $root now (401 = basic auth on, 200 = off)"
  fi
}

run_gates() {  # <services at FAIL level...>; the other JARVIS services that exist get quick WARN-level checks
  local svc
  step "health gate"
  for svc in "$@"; do gate_service "$svc" FAIL 1; done
  for svc in ui-bridge frontend livekit; do
    in_list "$svc" "$@" || gate_service "$svc" WARN 0
  done
  if (( NGINX_CHANGED )); then
    expect_http FAIL "nginx / (after the nginx change)" '200|401' 5 "${VIA_NGINX[@]}" "https://$DOMAIN/"
    expect_http FAIL "nginx /vortex/health (after the nginx change)" '200' 5 "${VIA_NGINX[@]}" "https://$DOMAIN/vortex/health"
    expect_http FAIL "nginx /rtc/validate (after the nginx change)" '401' 5 "${VIA_NGINX[@]}" "https://$DOMAIN/rtc/validate"
  fi
  host_checks
}

# ------------------------------------------------------------------ backup, records, smoke
run_backup() {  # <label> -> B, BNAME
  local tmp rc dir
  step "backup: backup_vps.sh $1"
  tmp="$(mktemp "$BASE/.backup-out.XXXXXX")"
  set +e
  bash "$SCRIPT_DIR/backup_vps.sh" "$1" 2>&1 | tee "$tmp"
  rc=${PIPESTATUS[0]}
  set -e
  dir="$(sed -n 's/^BACKUP_DIR=//p' "$tmp" | tail -n 1)"
  rm -f -- "$tmp"
  if (( rc != 0 )) || [[ -z $dir || ! -d $dir || $(dirname "$dir") != "$BASE" ]]; then return 1; fi
  B="$dir"
  BNAME="$(basename "$dir")"
  [[ $BNAME =~ $NAME_RE ]]
}

record_images() {  # <file> <services...>: container, image ref and image id of each service right now
  local f="$1" svc id info name ref img
  shift
  printf '# service\tcontainer\tref\timage_id (recorded %s)\n' "$(now_utc)" > "$f"
  for svc in "$@"; do
    id="$(ctr_id "$svc" all)"
    if [[ -n $id ]] && info="$(ctr_info "$id")"; then
      IFS='|' read -r name ref img _ <<< "$info"
      printf '%s\t%s\t%s\t%s\n' "$svc" "${name#/}" "$ref" "$img" >> "$f"
    else
      printf '%s\t-\t-\t-\n' "$svc" >> "$f"
    fi
  done
}

tag_rollback_images() {  # <record file>: our own images (jarvis-*) also get a :rollback-<backup> tag
  local svc name ref img repo
  while IFS=$'\t' read -r svc name ref img; do
    [[ $svc == \#* || $img == - || -z $img ]] && continue
    repo="${ref%%:*}"
    [[ $repo == jarvis-* ]] || continue
    if docker tag "$img" "$repo:rollback-$BNAME"; then
      log "  $svc: $repo:rollback-$BNAME -> ${img:7:12}"
    else
      warn "could not tag $repo:rollback-$BNAME"
    fi
  done < "$1"
}

record_is_new() {  # <record file> <service>: the service had no container before the change
  awk -F'\t' -v s="$2" '$1 == s && $4 == "-" { f = 1 } END { exit !f }' "$1" 2>/dev/null
}

smoke_snapshot() {  # <file>
  if bash "$SCRIPT_DIR/smoke_other_apps.sh" > "$1.tmp"; then
    mv -f -- "$1.tmp" "$1"
    return 0
  fi
  rm -f -- "$1.tmp"
  return 1
}

smoke_verify() {  # <backup dir>: compare smoke-pre.txt with now; a regression must survive one re-check
  local b="$1" rc=0
  step "other apps: smoke check (post)"
  if ! smoke_snapshot "$b/smoke-post.txt"; then fail "smoke_other_apps.sh failed (post)"; return 0; fi
  bash "$SCRIPT_DIR/smoke_other_apps.sh" --compare "$b/smoke-pre.txt" "$b/smoke-post.txt" || rc=$?
  case $rc in
    0) log "  ok   other apps: identical before and after"; return 0 ;;
    1) warn "other apps: something changed (no regression; see the lines above)"; return 0 ;;
    2) ;;
    *) fail "smoke compare failed (exit $rc)"; return 0 ;;
  esac
  log "  regression seen; re-checking in ${RECHECK_S}s (one flapping app should not roll JARVIS back)"
  sleep "$RECHECK_S"
  if ! smoke_snapshot "$b/smoke-post2.txt"; then fail "smoke_other_apps.sh failed (re-check)"; return 0; fi
  rc=0
  bash "$SCRIPT_DIR/smoke_other_apps.sh" --compare "$b/smoke-pre.txt" "$b/smoke-post2.txt" || rc=$?
  if (( rc == 2 )); then
    fail "other apps regressed after the change (confirmed by the re-check)"
  else
    log "  ok   the regression did not persist (re-check exit $rc)"
  fi
}

# ------------------------------------------------------------------ changes
install_root_file() {  # <mode> <src> <dest>
  if [[ -n $SANDBOX ]]; then
    install -m "$1" -- "$2" "$3"
  else
    install -m "$1" -o root -g root -- "$2" "$3"
  fi
}

publish_tools() {  # copies outside the code dir, so a code rollback never takes the tools with it
  local f
  mkdir -p -- "$TOOLS_DIR"
  for f in "${TOOL_FILES[@]}"; do
    if [[ ! -f $SCRIPT_DIR/$f ]]; then warn "tools: $f not found next to deploy.sh"; continue; fi
    [[ $SCRIPT_DIR/$f -ef $TOOLS_DIR/$f ]] && continue
    install -m 700 -- "$SCRIPT_DIR/$f" "$TOOLS_DIR/.$f.tmp" && mv -f -- "$TOOLS_DIR/.$f.tmp" "$TOOLS_DIR/$f"
  done
  printf '%s\n' "$SHA" > "$TOOLS_DIR/TOOLS_SHA"
}

install_code() {
  local -a excl=() files=()
  local svc f n
  HELD=()
  for svc in "${!HOLD_FILES[@]}"; do
    in_list "$svc" "${SERVICES[@]}" && continue
    read -r -a files <<< "${HOLD_FILES[$svc]}"
    for f in "${files[@]}"; do
      [[ -e $APP_DIR/$f ]] || continue
      excl+=("--exclude=$f")
      HELD+=("$f")
    done
  done
  step "install code $SHA into $APP_DIR"
  CODE_CHANGED=1
  if ! tar -xzf "$RUN/shipped.tar.gz" -C "$APP_DIR" --anchored "${excl[@]}"; then
    fail "extracting the shipped code failed"
    return 1
  fi
  if ! { printf '%s\n' "$SHA" > "$APP_DIR/DEPLOYED_SHA.tmp" && chmod 644 "$APP_DIR/DEPLOYED_SHA.tmp" \
      && mv -f -- "$APP_DIR/DEPLOYED_SHA.tmp" "$APP_DIR/DEPLOYED_SHA"; }; then
    fail "cannot write DEPLOYED_SHA"
    return 1
  fi
  record_set "$B/deploy.env" HELD "${HELD[*]}"
  if (( ${#HELD[@]} )); then log "  held back (their service is not deployed now): ${HELD[*]}"; fi
  # files the commit does not have stay in place; list them for the record
  {
    comm -13 <(tar -tzf "$RUN/shipped.tar.gz" | sed 's#/$##' | LC_ALL=C sort -u) \
      <(cd "$APP_DIR" && find deploy src services stonix_ui scripts \( -path stonix_ui/node_modules -o -name __pycache__ \) -prune \
          -o -type f ! -name '.env' ! -name '*.env' ! -name '.env.*' ! -name '*.pyc' -print 2>/dev/null | LC_ALL=C sort -u)
  } > "$B/extra-files.txt" 2>/dev/null || true
  n="$(grep -c . "$B/extra-files.txt" 2>/dev/null || true)"
  if [[ ${n:-0} != 0 ]]; then log "  $n file(s) on the VPS are not in this commit (kept; list: $B/extra-files.txt)"; fi
  log "  DEPLOYED_SHA=$SHA"
}

set_perms() {
  step "permissions: env files and livekit.yaml 600, $APP_DIR 750, no world-writable files"
  chmod 750 "$APP_DIR" || warn "chmod 750 $APP_DIR failed"
  find "$APP_DIR" -maxdepth 3 -type f \( -name .env -o -name '*.env' -o -name '.env.*' \) ! -name '*.example' \
    -exec chmod 600 {} + || warn "chmod of the env files failed"
  if [[ -f $H/livekit.yaml ]]; then chmod 600 "$H/livekit.yaml" || warn "chmod livekit.yaml failed"; fi
  find "$APP_DIR" -xdev -path "$APP_DIR/stonix_ui/node_modules" -prune -o -perm -0002 ! -type l \
    -exec chmod o-w {} + || warn "removing o+w failed"
}

nginx_test() {  # nginx -t checks every site on the server, so its messages can name other apps: the full output goes
  # to a root-only file in the backup and only the lines about jarvis.conf are shown
  local out rc=0 n keep="${B:-$BASE}/nginx-test.txt"
  out="$(nginx -t 2>&1)" || rc=$?
  { printf '== %s nginx -t: exit %s\n' "$(now_utc)" "$rc"; printf '%s\n' "$out"; } >> "$keep"
  printf '%s\n' "$out" | grep -F 'conf.d/jarvis.conf' | sed "s/^/[$LOG_TAG]   /" || true
  n="$(printf '%s\n' "$out" | grep -v -F -e 'conf.d/jarvis.conf' -e 'syntax is ok' -e 'test is successful' \
    -e 'test failed' | grep -c . || true)"
  if (( ${n:-0} > 0 )); then log "  nginx -t: $n more line(s) not shown, as they can name other apps (root-only: $keep)"; fi
  if (( rc == 0 )); then log "  nginx -t ok"; else log "  nginx -t failed (exit $rc)"; fi
  return "$rc"
}

restore_nginx_conf() {  # <backup dir> <reload 0|1>
  [[ -f $1/jarvis.conf ]] || { fail "no jarvis.conf in $1; put /etc/nginx/conf.d/jarvis.conf back by hand"; return 1; }
  install_root_file 644 "$1/jarvis.conf" "$NGINX_CONF" || return 1
  if (( $2 )); then
    nginx_test || { fail "nginx -t fails even with the restored jarvis.conf; not reloading"; return 1; }
    systemctl reload nginx || return 1
  fi
  log "  jarvis.conf restored from $(basename "$1")"
}

cert_bootstrap() {
  local f
  if (( ! BOOTSTRAP_CERT )); then
    fail "no certificate in $LE_DIR/live/$DOMAIN; rerun with --nginx --bootstrap-cert for the one-time certbot bootstrap"
    return 1
  fi
  for f in "$LE_DIR/options-ssl-nginx.conf" "$LE_DIR/ssl-dhparams.pem"; do
    [[ -f $f ]] || { fail "$f is missing (certbot's nginx helper files); this script never writes into $LE_DIR"; return 1; }
  done
  log "  no certificate yet: HTTP-only site for the ACME challenge, then certbot --webroot"
  NGINX_CHANGED=1
  mkdir -p -- "$WEBROOT"
  cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root $WEBROOT;
    }
    location / {
        return 200 'jarvis-acme-ready';
        add_header Content-Type text/plain;
    }
}
EOF
  chmod 644 "$NGINX_CONF"
  if ! { nginx_test && systemctl reload nginx; }; then fail "nginx rejected the bootstrap site"; return 1; fi
  certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email || { fail "certbot failed"; return 1; }
}

nginx_apply() {
  local new="$H/jarvis.conf" bootstrapped=0
  step "nginx: install jarvis.conf"
  [[ -f $new ]] || { fail "$new is missing"; return 1; }
  if [[ ! -f $LE_DIR/live/$DOMAIN/fullchain.pem ]]; then
    cert_bootstrap || return 1
    bootstrapped=1   # nginx already runs the bootstrap site, so a restore must reload
  fi
  if [[ -f $NGINX_CONF ]] && cmp -s -- "$new" "$NGINX_CONF"; then
    log "  jarvis.conf unchanged (no reload)"
    return 0
  fi
  NGINX_CHANGED=1
  install_root_file 644 "$new" "$NGINX_CONF" || { fail "cannot write $NGINX_CONF"; return 1; }
  if ! nginx_test; then
    fail "nginx -t rejected the new jarvis.conf; the previous file is back"
    if restore_nginx_conf "$B" "$bootstrapped"; then NGINX_CHANGED=$bootstrapped; fi
    return 1
  fi
  systemctl reload nginx || { fail "systemctl reload nginx failed"; return 1; }
  log "  nginx reloaded"
}

restore_code() {  # <backup dir>: /opt/jarvis-agent from opt-jarvis-agent.tgz; env files are never overwritten
  local b="$1"
  code_archive_ok "$b" || { fail "$b has no opt-jarvis-agent.tgz holding $(basename "$APP_DIR")/..."; return 1; }
  tar -xzf "$b/opt-jarvis-agent.tgz" -C "$(dirname "$APP_DIR")" \
    --exclude='.env' --exclude='*.env' --exclude='.env.*' || { fail "code restore from $b failed"; return 1; }
  if [[ ! -f $b/DEPLOYED_SHA ]]; then printf 'unknown\n' > "$APP_DIR/DEPLOYED_SHA"; fi
  log "  code restored from $(basename "$b") (env files left as they are)"
}

restore_images() {  # <record file> <services...>: point the image each service runs back at its recorded image id
  local rec="$1" svc line ref id cur
  shift
  for svc in "$@"; do
    line="$({ awk -F'\t' -v s="$svc" '$1 == s' "$rec" 2>/dev/null || true; } | tail -n 1)"
    if [[ -z $line ]]; then fail "$svc: no image record in $rec"; continue; fi
    IFS=$'\t' read -r _ _ ref id <<< "$line"
    [[ $id == - ]] && continue
    if ! docker image inspect "$id" >/dev/null 2>&1; then fail "$svc: image ${id:7:12} is gone"; continue; fi
    cur="$(service_image "$COMPOSE_FILE" "$svc")"
    if [[ -z $cur ]]; then fail "$svc: cannot tell which image docker-compose.yml names for it"; continue; fi
    # JARVIS's own tags are re-pointed freely. A third-party tag (livekit/livekit-server:v1.13.7) only goes back to
    # the image it named when it was recorded, never onto another version: that needs the compose file naming it.
    if [[ $cur != "$ref" ]] && ! own_image "$cur"; then
      fail "$svc: docker-compose.yml names $cur, but $ref ran before; a third-party tag is never moved onto another image, so restore the code that names $ref (rollback.sh <backup> --code)"
      continue
    fi
    if docker tag "$id" "$cur"; then
      log "  $svc: $cur -> ${id:7:12}"
    else
      fail "$svc: docker tag ${id:7:12} $cur failed"
    fi
    if [[ $cur != "$ref" ]] && own_image "$ref"; then docker tag "$id" "$ref" || true; fi
  done
}

pull_and_build() {
  step "images: pull missing, build: ${SERVICES[*]}"
  compose pull --policy missing --ignore-buildable "${SERVICES[@]}" || { fail "docker compose pull failed"; return 1; }
  compose build "${SERVICES[@]}" || { fail "docker compose build failed"; return 1; }
}

start_services() {
  step "recreate: docker compose -p $PROJECT up -d --no-deps ${SERVICES[*]}"
  TOUCHED=("${SERVICES[@]}")
  compose up -d --no-deps --no-build --pull never "${SERVICES[@]}" || { fail "docker compose up failed"; return 1; }
}

apply_changes() {
  if (( STAGED )); then install_code || return 1; fi
  set_perms
  compose config -q || { fail "docker compose config rejected the installed compose file"; return 1; }
  if (( ${#SERVICES[@]} )); then pull_and_build || return 1; fi
  if (( NGINX )); then nginx_apply || return 1; fi
  if (( ${#SERVICES[@]} )); then start_services || return 1; fi
  return 0
}

# ------------------------------------------------------------------ rollback after a failed deploy
auto_rollback() {  # back to $B: code, nginx, image tags, then the touched services; 0 when healthy again
  local svc
  local -a new=() again=()
  set +e   # recovery is best effort: every step runs and records its own failure
  for svc in "${TOUCHED[@]}"; do
    if record_is_new "$B/deploy-images.txt" "$svc"; then new+=("$svc"); else again+=("$svc"); fi
  done
  FAILS=()
  if (( ${#new[@]} )); then
    log "  stop the services this deploy created: ${new[*]}"
    compose stop "${new[@]}" || fail "could not stop ${new[*]}"
  fi
  if (( CODE_CHANGED )); then restore_code "$B" || true; fi
  if (( NGINX_CHANGED )); then restore_nginx_conf "$B" 1 || fail "nginx restore failed"; fi
  if (( ${#SERVICES[@]} )); then restore_images "$B/deploy-images.txt" "${SERVICES[@]}"; fi
  GIT_SHA_ENV="$PREV_SHA"
  if (( ${#again[@]} )); then
    compose up -d --no-deps --no-build --pull never "${again[@]}" || fail "docker compose up (rollback) failed"
  fi
  run_gates "${again[@]}"
  (( ${#FAILS[@]} == 0 ))
}

rollback_command() { printf 'bash %s/rollback.sh %s' "$TOOLS_DIR" "$BNAME"; }

rollback_after_failure() {  # sets RESULT and FINAL_RC
  local -a first=("${FAILS[@]}")
  set +e
  if (( ${#TOUCHED[@]} == 0 && ! NGINX_CHANGED )); then
    step "failed before any live change: putting the code and image tags back"
    if (( CODE_CHANGED )); then restore_code "$B" || true; fi
    if (( ${#SERVICES[@]} )); then restore_images "$B/deploy-images.txt" "${SERVICES[@]}"; fi
    FAILS=("${first[@]}")
    finish failed-before-live 1
    return 0
  fi
  if (( ! AUTO_ROLLBACK )); then
    log "automatic rollback is off (--no-auto-rollback); to go back run: $(rollback_command)"
    finish failed-no-rollback 3
    return 0
  fi
  step "AUTOMATIC ROLLBACK to $BNAME"
  if auto_rollback; then
    FAILS=("${first[@]}")
    finish rolled-back 2
  else
    local -a second=("${FAILS[@]}")
    FAILS=("${first[@]}" "${second[@]/#/rollback: }")
    finish rollback-failed 3
  fi
}

# ------------------------------------------------------------------ finish
finish() {  # <result> <exit code>: records the outcome; does not exit
  local dur w f
  RESULT="$1"
  FINAL_RC="$2"
  dur=$(( $(date +%s) - STARTED_EPOCH ))
  if [[ -n $B ]]; then
    record_set "$B/deploy.env" RESULT "$RESULT"
    record_set "$B/deploy.env" FINISHED "$(now_utc)"
    # 'rollback.sh --last' goes back to the backup of the last deploy that reached the change phase
    if [[ $MODE == deploy && ( $PHASE == change || $PHASE == verify ) && $RESULT != failed-before-live ]]; then
      printf '%s\n' "$BNAME" > "$LAST_DEPLOY_FILE"
    fi
  fi
  printf '%s %s sha=%s services=%s nginx=%s backup=%s result=%s duration=%ss\n' "$(now_utc)" "$MODE" "${SHA:-?}" \
    "$(IFS=,; printf '%s' "${SERVICES[*]:-none}")" "$NGINX" "${BNAME:--}" "$RESULT" "$dur" >> "$DEPLOY_LOG"
  step "summary"
  for w in "${WARNS[@]}"; do log "  warning: $w"; done
  for f in "${FAILS[@]}"; do log "  failure: $f"; done
  log "services: ${SERVICES[*]:-none}; sha: ${SHA:-?}; nginx: $NGINX; took ${dur}s"
  if [[ -n $BNAME ]]; then
    log "backup: $B"
    if [[ $MODE == deploy && $RESULT != rolled-back && $RESULT != failed-before-live ]]; then
      log "rollback: $(rollback_command)"
    fi
  fi
  log "RESULT=$RESULT"
}

on_exit() {
  local rc=$?
  set +e
  trap - EXIT INT TERM HUP
  if [[ -z $RESULT ]]; then
    case "$PHASE" in
      change|verify)
        fail "unexpected exit ($rc) during the $PHASE phase"
        if [[ $MODE == deploy ]]; then rollback_after_failure; else finish failed 3; fi
        ;;
      *)
        FINAL_RC=$rc
        if [[ -n $B ]]; then finish failed-before-change "$rc"; fi
        ;;
    esac
  fi
  [[ -n $FINAL_RC ]] || FINAL_RC=$rc
  if [[ -n $RUN && -d $RUN ]]; then printf '%s\n' "$FINAL_RC" > "$RUN/rc"; fi
  cleanup_tree
  exit "$FINAL_RC"
}

start_run() {  # inside the run (detached child or --foreground): log, pid file and traps
  if [[ -z ${JARVIS_DETACHED:-} ]]; then
    # --foreground: the run writes only to run.log and a background tail shows it here. A lost terminal then stops
    # only that viewer; a write into a closed ssh pipe would kill bash (SIGPIPE) before on_exit could roll back.
    [[ -n $RUN ]] || RUN="$(new_run_dir "$MODE-fg")"
    : >> "$RUN/run.log"
    tail -s 0.2 -c +"$(( $(wc -c < "$RUN/run.log") + 1 ))" -f --pid=$$ -- "$RUN/run.log" &
    exec >> "$RUN/run.log" 2>&1
  fi
  if [[ -n $RUN ]]; then printf '%s\n' "$$" > "$RUN/pid"; fi
  STARTED_EPOCH=$(date +%s)
  trap on_exit EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP   # --foreground in a terminal session that hung up: roll back (a detached run ignores HUP)
}

take_lock() {
  mkdir -p -- "$BASE"
  exec 9> "$LOCK_FILE"
  flock -n 9 || die "another deploy or rollback is running (lock $LOCK_FILE)"
}

detach_and_follow() {  # <run dir> <script> <args...>: rerun the script detached from this session, show its log
  local run="$1" script="$2" pid="" i rc=""
  shift 2
  command -v setsid >/dev/null 2>&1 || die "setsid not found; use --foreground"
  rm -f -- "$run/pid" "$run/rc"
  : > "$run/run.log"
  JARVIS_DETACHED=1 nohup setsid bash "$script" "$@" >> "$run/run.log" 2>&1 < /dev/null &
  for (( i = 0; i < 150; i++ )); do
    [[ -s $run/pid ]] && break
    sleep 0.1
  done
  pid="$(cat "$run/pid" 2>/dev/null || true)"
  if [[ ! $pid =~ ^[0-9]+$ ]]; then
    cat -- "$run/run.log" >&2 || true
    die "the detached run did not start"
  fi
  log "running detached as pid $pid; Ctrl-C or a dropped connection stops only this view"
  log "follow again: tail -n +1 -f --pid=$pid $run/run.log; cat $run/rc"
  tail -n +1 -f --pid="$pid" -- "$run/run.log" || true
  rc="$(cat "$run/rc" 2>/dev/null || true)"
  if [[ ! $rc =~ ^[0-9]+$ ]]; then
    log "no exit code in $run/rc (was the run killed?)"
    return 3
  fi
  return "$rc"
}

# ------------------------------------------------------------------ deploy
parse_deploy_args() {
  local services_given=0
  while (( $# )); do
    case "$1" in
      -h|--help) usage "${BASH_SOURCE[0]}"; exit 0 ;;
      --services) (( $# >= 2 )) || usage_error "--services needs a value"; parse_services "$2"; services_given=1; shift 2 ;;
      --services=*) parse_services "${1#*=}"; services_given=1; shift ;;
      --nginx) NGINX=1; shift ;;
      --bootstrap-cert) BOOTSTRAP_CERT=1; shift ;;
      --run-dir) (( $# >= 2 )) || usage_error "--run-dir needs a folder"; RUN="$2"; shift 2 ;;
      --sha) (( $# >= 2 )) || usage_error "--sha needs a value"; SHA="$2"; shift 2 ;;
      --sha-full) (( $# >= 2 )) || usage_error "--sha-full needs a value"; SHA_FULL="$2"; shift 2 ;;
      --dry-run) DRY_RUN=1; shift ;;
      --foreground) FOREGROUND=1; shift ;;
      --no-auto-rollback) AUTO_ROLLBACK=0; shift ;;
      --gate-only) GATE_ONLY=1; shift ;;
      *) usage_error "unknown argument: $(safe "$1")" ;;
    esac
  done
  if (( ! services_given && ! GATE_ONLY )); then
    usage_error "--services is required: name the compose services to deploy, or 'none'"
  fi
  [[ -z $SHA || $SHA =~ $SHA_RE ]] || usage_error "--sha must be a hex commit id"
  [[ -z $SHA_FULL || $SHA_FULL =~ $SHA_RE ]] || usage_error "--sha-full must be a hex commit id"
  if (( BOOTSTRAP_CERT && ! NGINX )); then usage_error "--bootstrap-cert goes with --nginx"; fi
  if [[ -n $RUN ]]; then validate_run_dir "$RUN"; fi
}

preflight() {
  local cf free svc id ref cur
  step "preflight"
  [[ -d $APP_DIR ]] || die "$APP_DIR not found"
  [[ -f $ENV_FILE ]] || die "$ENV_FILE not found"
  if [[ -n $RUN && -f $RUN/shipped.tar.gz ]]; then
    STAGED=1
    TREE="$RUN/tree"
    [[ -f $TREE/deploy/hostinger/docker-compose.yml ]] || die "$TREE is incomplete"
    [[ -n $SHA ]] || usage_error "--sha is required for a staged run"
  fi
  PREV_SHA="$(head -n 1 "$APP_DIR/DEPLOYED_SHA" 2>/dev/null || true)"
  [[ -n $PREV_SHA ]] || PREV_SHA=none
  if (( ! STAGED )); then
    # without staged code the installed code is deployed, so the commit is the one already recorded
    if [[ -n $SHA && $SHA != "$PREV_SHA" ]]; then
      die "--sha $SHA only applies to a staged run; $APP_DIR holds $PREV_SHA"
    fi
    SHA="$PREV_SHA"
  fi
  GIT_SHA_ENV="$SHA"
  cf="$COMPOSE_FILE"
  if (( STAGED )); then cf="$TREE/deploy/hostinger/docker-compose.yml"; fi
  # the new compose file must parse with the live env files and must define every named service
  compose_file "$cf" config -q "${SERVICES[@]}" \
    || die "docker compose config rejected $cf with the live env files (unknown service name?)"
  if (( ${#SERVICES[@]} )); then
    free="$(disk_free_gb "$APP_DIR")"
    if [[ -n $free ]] && (( free < DISK_FAIL_GB )); then
      die "only ${free} GB free: no image build below ${DISK_FAIL_GB} GB on this shared server"
    fi
  fi
  # A recreated livekit takes its credentials from LIVEKIT_KEYS (.env), no longer from a keys: line in the live
  # livekit.yaml; if the two differ, every client and the PC agent would lose LiveKit.
  if in_list livekit "${SERVICES[@]}" && grep -q '^keys:' "$H/livekit.yaml" 2>/dev/null; then
    livekit_keys_match || die "the keys: entry in the live livekit.yaml differs from LIVEKIT_API_KEY/LIVEKIT_API_SECRET in $ENV_FILE; deploying livekit would change its credentials (values not shown)"
    log "  livekit: LIVEKIT_API_KEY/SECRET in .env match the live livekit.yaml keys (values not shown)"
  fi
  # The automatic rollback restores the installed compose file. If that already names another third-party image
  # than the running container (code shipped earlier, container not recreated since), it cannot go back.
  for svc in "${SERVICES[@]}"; do
    id="$(ctr_id "$svc" all)"
    [[ -n $id ]] || continue
    ref="$(docker inspect -f '{{.Config.Image}}' "$id" 2>/dev/null || true)"
    cur="$(service_image "$COMPOSE_FILE" "$svc")"
    if [[ -n $ref && -n $cur && $ref != "$cur" ]] && ! own_image "$cur"; then
      warn "$svc runs $ref, but the installed docker-compose.yml names $cur: if this deploy fails, the automatic rollback cannot bring $ref back (a third-party tag is never moved; rollback.sh <older backup> --code can)"
    fi
  done
  if (( NGINX )); then
    [[ -f $NGINX_CONF ]] || (( BOOTSTRAP_CERT )) || die "$NGINX_CONF does not exist yet (first install: --bootstrap-cert)"
    if [[ ! -f $LE_DIR/live/$DOMAIN/fullchain.pem ]] && (( ! BOOTSTRAP_CERT )); then
      die "no certificate for $DOMAIN; the one-time certbot bootstrap needs --nginx --bootstrap-cert"
    fi
  fi
  log "  sha $SHA (now deployed: $PREV_SHA); staged code: $([[ $STAGED == 1 ]] && echo yes || echo no)"
  log "  services: ${SERVICES[*]:-none}; nginx: $([[ $NGINX == 1 ]] && echo yes || echo no)"
}

print_plan() {
  local svc id info name ref img status health cmd held=() files=() f all=()
  step "DRY RUN: nothing is changed"
  cmd="docker compose -p $PROJECT -f $COMPOSE_FILE --project-directory $H --env-file $ENV_FILE"
  for svc in "${!HOLD_FILES[@]}"; do
    in_list "$svc" "${SERVICES[@]}" && continue
    read -r -a files <<< "${HOLD_FILES[$svc]}"
    for f in "${files[@]}"; do [[ -e $APP_DIR/$f ]] && held+=("$f"); done
  done
  log "1  lock         flock -n $LOCK_FILE"
  log "2  tools        copy ${TOOL_FILES[*]} -> $TOOLS_DIR/"
  log "3  backup       bash $SCRIPT_DIR/backup_vps.sh pre-deploy-${SHA:0:12}"
  log "4  record       deploy.env, deploy-images.txt and :rollback-<backup> tags for the jarvis-* images"
  log "5  smoke (pre)  bash $SCRIPT_DIR/smoke_other_apps.sh > <backup>/smoke-pre.txt"
  if (( STAGED )); then
    log "6  install      tar -xzf $RUN/shipped.tar.gz -C $APP_DIR${held:+ (held back: ${held[*]})}; DEPLOYED_SHA=$SHA"
  else
    log "6  install      none: the code in $APP_DIR ($PREV_SHA) is deployed as it is"
  fi
  log "7  perms        env files + livekit.yaml 600, $APP_DIR 750, no o+w"
  log "8  check        $cmd config -q"
  if (( ${#SERVICES[@]} )); then
    log "9  images       $cmd pull --policy missing --ignore-buildable ${SERVICES[*]}"
    log "                $cmd build ${SERVICES[*]}"
  else
    log "9  images       none (no service named)"
  fi
  if (( NGINX )); then
    if [[ -f $NGINX_CONF ]] && cmp -s -- "${TREE:-$APP_DIR}/deploy/hostinger/jarvis.conf" "$NGINX_CONF"; then
      log "10 nginx        jarvis.conf unchanged: no reload"
    else
      log "10 nginx        install jarvis.conf -> $NGINX_CONF; nginx -t; systemctl reload nginx (restore on failure)"
    fi
  else
    log "10 nginx        not touched (no --nginx)"
  fi
  if (( ${#SERVICES[@]} )); then
    log "11 recreate     $cmd up -d --no-deps --no-build --pull never ${SERVICES[*]}"
  else
    log "11 recreate     none"
  fi
  log "12 gate         ${SERVICES[*]:-no service}: healthy + HTTP probes; other JARVIS services: quick checks (WARN)"
  log "13 smoke (post) compare with smoke-pre.txt; a regression is re-checked after ${RECHECK_S}s"
  log "14 on failure   $([[ $AUTO_ROLLBACK == 1 ]] && echo 'automatic rollback to the backup (code, nginx, image tags)' || echo 'print the rollback command (--no-auto-rollback)')"
  step "current state of the jarvis services"
  mapfile -t all < <({ compose_file "${TREE:-$APP_DIR}/deploy/hostinger/docker-compose.yml" --profile '*' \
    config --services 2>/dev/null || true; })
  for svc in "${all[@]}"; do
    id="$(ctr_id "$svc" all)"
    if [[ -n $id ]] && info="$(ctr_info "$id")"; then
      IFS='|' read -r name ref img status health _ _ <<< "$info"
      log "  $svc: ${name#/} $ref ${img:7:12} $status ($health)$(in_list "$svc" "${SERVICES[@]}" && echo '  <- deploy' || echo '  (not touched)')"
    else
      log "  $svc: no container$(in_list "$svc" "${SERVICES[@]}" && echo '  <- deploy (new)' || echo '')"
    fi
  done
  log "VERDICT: a real run would proceed"
}

gate_only_main() {
  local -a svcs=("${SERVICES[@]}")
  if (( ${#svcs[@]} == 0 )); then
    mapfile -t svcs < <({ compose ps -a --format '{{.Service}}' 2>/dev/null || true; } | LC_ALL=C sort -u)
  fi
  run_gates "${svcs[@]}"
  if (( ${#FAILS[@]} )); then log "GATE=fail (${#FAILS[@]} failure(s))"; FINAL_RC=1; else log "GATE=ok"; FINAL_RC=0; fi
}

run_deploy() {
  start_run
  preflight
  take_lock
  publish_tools
  PHASE=backup
  run_backup "pre-deploy-${SHA:0:12}" || die "backup_vps.sh failed: nothing was changed"
  log "BACKUP=$BNAME"
  record_write "$B/deploy.env" KIND=deploy "SHA=$SHA" "SHA_FULL=$SHA_FULL" "PREV_SHA=$PREV_SHA" \
    "SERVICES=${SERVICES[*]}" "NGINX=$NGINX" "CODE=$STAGED" "RUN=$RUN" "STARTED=$(now_utc)" RESULT=running
  record_images "$B/deploy-images.txt" "${SERVICES[@]}"
  tag_rollback_images "$B/deploy-images.txt"
  ROOT_CODE_PRE="$(http_code "${VIA_NGINX[@]}" "https://$DOMAIN/")"
  step "other apps: smoke check (pre)"
  smoke_snapshot "$B/smoke-pre.txt" || die "smoke_other_apps.sh failed: nothing was changed"
  PHASE=change
  if ! apply_changes; then
    rollback_after_failure
    return 0
  fi
  PHASE=verify
  run_gates "${SERVICES[@]}"
  smoke_verify "$B"
  if (( ${#FAILS[@]} )); then
    rollback_after_failure
    return 0
  fi
  finish ok 0
}

deploy_main() {
  local -a orig=("$@")
  local rc=0
  parse_deploy_args "$@"
  require_host
  if (( GATE_ONLY )); then
    gate_only_main
    return 0
  fi
  if (( DRY_RUN )); then
    trap cleanup_tree EXIT
    preflight
    print_plan
    FINAL_RC=0
    return 0
  fi
  if (( ! FOREGROUND )) && [[ -z ${JARVIS_DETACHED:-} ]]; then
    [[ -n $RUN ]] || RUN="$(new_run_dir manual)"
    detach_and_follow "$RUN" "$SCRIPT_DIR/deploy.sh" "${orig[@]}" --run-dir "$RUN" --foreground || rc=$?
    FINAL_RC=$rc
    return 0
  fi
  run_deploy
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  deploy_main "$@"
  exit "${FINAL_RC:-0}"
fi
