#!/usr/bin/env bash
# deploy/hostinger/deploy_from_pc.sh - ship the committed HEAD to the VPS and deploy the named JARVIS services
# (plan C-12 / OP-08, contract K-54 COORD-4). Claude only, from Git Bash in the repo on the PC.
#
# Usage:
#   bash deploy/hostinger/deploy_from_pc.sh --services LIST [--nginx] [--allow-dirty] [--no-auto-rollback]
#   bash deploy/hostinger/deploy_from_pc.sh --dry-run [--services LIST] [--nginx]   offline: no ssh at all
#   bash deploy/hostinger/deploy_from_pc.sh --check --services LIST [--nginx]       ship, then a dry run on the VPS
#   bash deploy/hostinger/deploy_from_pc.sh --attach RUN_ID                          follow a run after a dropped link
#
#   --services LIST     compose services to rebuild and recreate (required), e.g. 'ui-bridge' or 'ui-bridge,livekit';
#                       'none' ships the code only. Only the named services are touched ('up -d --no-deps'); the
#                       frontend is never rebuilt unless it is named (RUNBOOK, "The frontend rule").
#   --nginx             also install deploy/hostinger/jarvis.conf on the VPS (nginx -t, then reload)
#   --allow-dirty       deploy although the shipped paths have uncommitted changes (they are listed as NOT deployed)
#   --no-auto-rollback  passed to deploy.sh: after a failed gate print the rollback command instead of running it
#
# Only a commit is shipped, never the working tree: 'git archive' of deploy src services stonix_ui scripts
# .dockerignore and the root requirements*.txt, with core.autocrlf=false and core.eol=lf so shell scripts stay LF.
# HEAD is resolved once at the start; the archive, the file list and the service list all come from that commit,
# so a commit that lands meanwhile (Cursor works in the same tree) is not shipped under the recorded SHA.
# The VPS keeps it in /root/jarvis-backups/runs/<run>/; deploy.sh runs from there (the new version, never the old
# copy in /opt/jarvis-agent), takes the backup and only then installs the code and writes DEPLOYED_SHA.
# Env: JARVIS_VPS_HOST (default root@187.127.123.235), JARVIS_SSH_KEY (default ~/.ssh/id_ed25519_hostinger),
#   JARVIS_DOMAIN (default jarvis.srv1802669.hstgr.cloud).
# Exit codes: those of deploy.sh (0 deployed and healthy, 1 refused or nothing live changed, 2 rolled back
#   automatically, 3 act now), plus 5 = connection lost while the run goes on (the attach command is printed),
#   6 = deployed but the PC-side checks failed, 64 = usage error. --dry-run: 0 = a real run would go ahead,
#   1 = it would refuse.
set -euo pipefail

readonly VPS_HOST="${JARVIS_VPS_HOST:-root@187.127.123.235}"
readonly SSH_KEY="${JARVIS_SSH_KEY:-$HOME/.ssh/id_ed25519_hostinger}"
readonly DOMAIN="${JARVIS_DOMAIN:-jarvis.srv1802669.hstgr.cloud}"
readonly REMOTE_RUNS=/root/jarvis-backups/runs
readonly REMOTE_TOOLS=/root/jarvis-backups/tools
readonly -a SHIP_PATHS=(deploy src services stonix_ui scripts .dockerignore)   # + requirements*.txt from HEAD
readonly SERVICE_RE='^[a-z0-9][a-z0-9_.-]{0,62}$'
readonly RUN_ID_RE='^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,60}$'
readonly -a SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=20
  -o ServerAliveCountMax=6 "$VPS_HOST")

ROOT=""
SERVICES=()
SERVICES_GIVEN=0
NGINX=0
ALLOW_DIRTY=0
AUTO_ROLLBACK=1
DRY_RUN=0
CHECK=0
ATTACH=""
SHA=""
SHA_FULL=""
SHIP=()
REFUSE=()
ARCHIVE=""
LOCAL_LOG=""

log() { printf '[deploy_from_pc] %s\n' "$*"; }
die() { printf '[deploy_from_pc] ERROR: %s\n' "$*" >&2; exit 1; }
usage_error() { printf '[deploy_from_pc] %s (see --help)\n' "$*" >&2; exit 64; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
safe() { printf '%s' "${1//[^A-Za-z0-9_.,:=\/ -]/?}"; }
in_list() { local x="$1" y; shift; for y in "$@"; do [[ $x == "$y" ]] && return 0; done; return 1; }

parse_services() {
  local -a toks=()
  local tok
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
  SERVICES_GIVEN=1
}

parse_args() {
  while (( $# )); do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --services) (( $# >= 2 )) || usage_error "--services needs a value"; parse_services "$2"; shift 2 ;;
      --services=*) parse_services "${1#*=}"; shift ;;
      --nginx) NGINX=1; shift ;;
      --allow-dirty) ALLOW_DIRTY=1; shift ;;
      --no-auto-rollback) AUTO_ROLLBACK=0; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --check) CHECK=1; shift ;;
      --attach) (( $# >= 2 )) || usage_error "--attach needs a run id"; ATTACH="$2"; shift 2 ;;
      *) usage_error "unknown argument: $(safe "$1")" ;;
    esac
  done
  if [[ -n $ATTACH ]]; then
    [[ $ATTACH =~ $RUN_ID_RE ]] || usage_error "not a run id: $(safe "$ATTACH")"
    return 0
  fi
  (( DRY_RUN + CHECK <= 1 )) || usage_error "--dry-run and --check do not go together"
  if (( ! SERVICES_GIVEN && ! DRY_RUN )); then
    usage_error "--services is required: name the compose services to deploy (e.g. 'ui-bridge'), or 'none'"
  fi
}

# ------------------------------------------------------------------ local checks
head_services() {  # top-level service keys of the committed docker-compose.yml
  git -C "$ROOT" show "$SHA_FULL:deploy/hostinger/docker-compose.yml" | awk '
    /^services:[[:space:]]*$/ { s = 1; next }
    s && /^[^[:space:]#]/ { s = 0 }
    s && /^  [A-Za-z0-9][A-Za-z0-9_.-]*:[[:space:]]*$/ { sub(/^  /, ""); sub(/:.*/, ""); print }'
}

local_checks() {
  local p dirty branch ahead svc
  local -a known=() top=()
  ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" || die "not inside the JARVIS git repo"
  [[ -f $ROOT/deploy/hostinger/deploy.sh ]] || die "$ROOT is not the JARVIS repo"
  # HEAD is resolved once: Cursor commits in the same working tree, so every later read uses this commit id
  SHA_FULL="$(git -C "$ROOT" rev-parse HEAD)"
  SHA="$(git -C "$ROOT" rev-parse --short "$SHA_FULL")"
  branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  git -C "$ROOT" cat-file -e "$SHA_FULL:deploy/hostinger/deploy.sh" 2>/dev/null \
    || die "commit $SHA has no deploy/hostinger/deploy.sh"
  mapfile -t top < <(git -C "$ROOT" ls-tree --name-only "$SHA_FULL")
  SHIP=()
  for p in "${SHIP_PATHS[@]}"; do
    if in_list "$p" "${top[@]}"; then SHIP+=("$p"); else log "note: $p is not in commit $SHA (skipped)"; fi
  done
  for p in "${top[@]}"; do
    if [[ $p =~ ^requirements[^/]*\.txt$ ]]; then SHIP+=("$p"); fi
  done

  log "commit: $SHA ($branch) $(git -C "$ROOT" log -1 --format=%s "$SHA_FULL" | cut -c1-90)"
  ahead="$(git -C "$ROOT" rev-list --count "@{upstream}..$SHA_FULL" 2>/dev/null || true)"
  if [[ -n $ahead && $ahead != 0 ]]; then log "note: HEAD is $ahead commit(s) ahead of its upstream (not pushed yet)"; fi

  dirty="$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all -- "${SHIP[@]}")"
  if [[ -n $dirty ]]; then
    log "uncommitted changes in the shipped paths: these are NOT deployed (only commit $SHA is):"
    printf '%s\n' "$dirty" | sed 's/^/    /'
    if (( ! ALLOW_DIRTY )); then REFUSE+=("the shipped paths have uncommitted changes (commit them, or pass --allow-dirty)"); fi
  else
    log "working tree: the shipped paths are clean"
  fi

  mapfile -t known < <(head_services)
  for svc in "${SERVICES[@]}"; do
    in_list "$svc" "${known[@]}" || REFUSE+=("'$svc' is not a service in HEAD's docker-compose.yml (${known[*]})")
  done
  if [[ ! -f $SSH_KEY ]] && (( ! DRY_RUN )); then REFUSE+=("ssh key $SSH_KEY not found"); fi
}

new_run_id() { printf '%s-%s-%s' "$(date -u +%Y%m%dT%H%M%SZ)" "$SHA" "$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"; }

remote_upload_cmd() {  # <run dir> <sha256>: unpack the archive into <run dir> after checking its checksum
  # shellcheck disable=SC2016  # $R is expanded by the remote shell
  printf 'set -eu; umask 077; R=%s; mkdir -p %s; mkdir -- "$R"; cat > "$R/shipped.tar.gz"; echo "%s  $R/shipped.tar.gz" | sha256sum -c --quiet -; mkdir -- "$R/tree"; tar -xzf "$R/shipped.tar.gz" -C "$R/tree"; test -f "$R/tree/deploy/hostinger/deploy.sh"; echo "UPLOAD_OK $R"' \
    "$1" "$REMOTE_RUNS" "$2"
}

remote_deploy_cmd() {  # <run dir> <deploy.sh mode flags...>
  local run="$1" cmd
  shift
  cmd="bash $run/tree/deploy/hostinger/deploy.sh --run-dir $run --sha $SHA --sha-full $SHA_FULL"
  cmd+=" --services '${SERVICES[*]:-none}'"
  if (( NGINX )); then cmd+=" --nginx"; fi
  if (( ! AUTO_ROLLBACK )); then cmd+=" --no-auto-rollback"; fi
  if (( $# )); then cmd+=" $*"; fi
  printf '%s' "$cmd"
}

remote_attach_cmd() {  # <run dir>
  # shellcheck disable=SC2016  # expanded by the remote shell
  printf 'R=%s; test -d "$R" || { echo "no such run: $R"; exit 1; }; p=$(cat "$R/pid" 2>/dev/null || true); if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then tail -n +1 -f --pid="$p" "$R/run.log"; else cat "$R/run.log" 2>/dev/null; fi; rc=$(cat "$R/rc" 2>/dev/null || true); echo "RUN_RC=${rc:-unknown}"; case "$rc" in ""|*[!0-9]*) exit 3 ;; *) exit "$rc" ;; esac' "$1"
}

build_archive() {
  ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/jarvis-ship.XXXXXX")"
  git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf -c tar.umask=0022 archive --format=tar "$SHA_FULL" \
    -- "${SHIP[@]}" | gzip -n -9 > "$ARCHIVE"
}

print_dry_run() {
  local id files svc
  id="$(new_run_id)"
  files="$(git -C "$ROOT" ls-tree -r --name-only "$SHA_FULL" -- "${SHIP[@]}")"
  log "DRY RUN: no connection is made and nothing changes"
  log "would ship $(printf '%s\n' "$files" | grep -c .) file(s) of commit $SHA (git archive, LF line endings):"
  for svc in "${SHIP[@]}"; do
    printf '    %-22s %s file(s)\n' "$svc" "$(git -C "$ROOT" ls-tree -r --name-only "$SHA_FULL" -- "$svc" | grep -c .)"
  done
  printf '%s\n' "$files" | sed 's/^/      /'
  log "services: ${SERVICES[*]:-none (code only)}; nginx: $([[ $NGINX == 1 ]] && echo yes || echo no)"
  log "would run (run id like $id), each remote command as one ssh argument:"
  printf '    1. git archive %s (core.autocrlf=false, core.eol=lf) | gzip -n > archive\n' "$SHA"
  printf '       %s < archive, remote command:\n         %s\n' "${SSH[*]}" "$(remote_upload_cmd "$REMOTE_RUNS/$id" '<sha256>')"
  printf '    2. %s, remote command:\n         %s\n' "${SSH[*]}" "$(remote_deploy_cmd "$REMOTE_RUNS/$id")"
  printf '       (deploy.sh: lock, backup_vps.sh, smoke pre, install, %s, gate, smoke post, rollback on failure)\n' \
    "$( (( ${#SERVICES[@]} )) && echo "build + up -d --no-deps ${SERVICES[*]}" || echo 'no container touched')"
  printf '    3. PC checks: https://%s/vortex/health = 200, https://%s/ = 401 (basic auth) or 200\n' "$DOMAIN" "$DOMAIN"
}

# ------------------------------------------------------------------ VPS steps
upload() {  # <run dir>
  local sum out
  sum="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
  log "upload: $(wc -c < "$ARCHIVE" | tr -d ' ') bytes, sha256 ${sum:0:16}... -> $1"
  out="$("${SSH[@]}" "$(remote_upload_cmd "$1" "$sum")" < "$ARCHIVE")" || die "upload failed: nothing was changed on the VPS"
  [[ $out == *"UPLOAD_OK $1"* ]] || die "upload did not confirm: $out"
}

follow_remote() {  # <command>: streams it into LOCAL_LOG; returns its exit code (255 = connection lost)
  local rc
  set +e
  "${SSH[@]}" "$1" 2>&1 | tee -a "$LOCAL_LOG"
  rc=${PIPESTATUS[0]}
  set -e
  return "$rc"
}

pc_checks() {
  local h r
  h="$(curl -s -o /dev/null -m 15 -w '%{http_code}' "https://$DOMAIN/vortex/health" 2>/dev/null || true)"
  r="$(curl -s -o /dev/null -m 15 -w '%{http_code}' "https://$DOMAIN/" 2>/dev/null || true)"
  log "PC check: https://$DOMAIN/vortex/health -> ${h:-000} (want 200)"
  log "PC check: https://$DOMAIN/ -> ${r:-000} (401 while the temporary basic auth is on, 200 after the cut-over)"
  [[ $h == 200 && $r =~ ^(200|401)$ ]]
}

report_end() {  # <deploy exit code> <run id>
  local rc="$1" backup
  backup="$(sed -n 's/^\[deploy\] BACKUP=//p' "$LOCAL_LOG" | tail -n 1)"
  log "run $2: deploy.sh exit $rc; local copy of the log: $LOCAL_LOG"
  if [[ -n $backup ]]; then
    log "backup: /root/jarvis-backups/$backup"
    case $rc in
      0|3) log "rollback: ${SSH[*]} 'bash $REMOTE_TOOLS/rollback.sh $backup'" ;;
    esac
  fi
}

attach() {  # <run id>
  local rc=0
  LOCAL_LOG="${TMPDIR:-/tmp}/jarvis-deploy-$1.log"
  follow_remote "$(remote_attach_cmd "$REMOTE_RUNS/$1")" || rc=$?
  if (( rc == 255 )); then log "connection lost again; the run goes on: $0 --attach $1"; exit 5; fi
  report_end "$rc" "$1"
  exit "$rc"
}

main() {
  local id rc=0 run
  parse_args "$@"
  if [[ -n $ATTACH ]]; then attach "$ATTACH"; fi
  local_checks
  if (( DRY_RUN )); then
    print_dry_run
    if (( ${#REFUSE[@]} )); then
      printf '[deploy_from_pc] VERDICT: a real run would REFUSE: %s\n' "${REFUSE[@]}"
      exit 1
    fi
    if (( ! SERVICES_GIVEN )); then log "VERDICT: add --services LIST (or 'none') for a real run"; exit 0; fi
    log "VERDICT: a real run would go ahead"
    exit 0
  fi
  if (( ${#REFUSE[@]} )); then
    printf '[deploy_from_pc] REFUSED: %s\n' "${REFUSE[@]}" >&2
    exit 1
  fi
  trap '[[ -z $ARCHIVE ]] || rm -f -- "$ARCHIVE"' EXIT
  build_archive
  id="$(new_run_id)"
  run="$REMOTE_RUNS/$id"
  LOCAL_LOG="${TMPDIR:-/tmp}/jarvis-deploy-$id.log"
  : > "$LOCAL_LOG"
  upload "$run"
  if (( CHECK )); then
    log "check: dry run of the staged deploy.sh on the VPS (nothing changes)"
    follow_remote "$(remote_deploy_cmd "$run" --dry-run)" || rc=$?
    if (( rc == 255 )); then die "connection lost during the check"; fi
    log "check finished: exit $rc ($([[ $rc == 0 ]] && echo 'a real run would go ahead' || echo 'a real run would refuse'))"
    exit "$rc"
  fi
  log "deploy $SHA on the VPS: services ${SERVICES[*]:-none}, nginx $([[ $NGINX == 1 ]] && echo yes || echo no)"
  log "the log streams below; Ctrl-C or a dropped link stops only this view (re-attach: $0 --attach $id)"
  follow_remote "$(remote_deploy_cmd "$run")" || rc=$?
  if (( rc == 255 )); then
    log "connection lost; the deploy goes on on the VPS. Follow it: bash $0 --attach $id"
    exit 5
  fi
  report_end "$rc" "$id"
  if (( rc != 0 )); then exit "$rc"; fi
  if ! pc_checks; then log "the deploy finished but the PC-side checks failed"; exit 6; fi
  log "DONE: $SHA is deployed (${SERVICES[*]:-code only})"
}

main "$@"
