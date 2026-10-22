#!/usr/bin/env bash
# deploy/hostinger/deploy_from_pc.sh - ship the committed HEAD to the VPS and deploy the named JARVIS services
# (plan C-12 / OP-08, contract K-54 COORD-4). Claude only, from Git Bash in the repo on the PC.
#
# Usage:
#   bash deploy/hostinger/deploy_from_pc.sh --services LIST [--nginx] [--allow-dirty] [--no-auto-rollback]
#   bash deploy/hostinger/deploy_from_pc.sh --dry-run [--services LIST] [--nginx]   offline: no ssh at all
#   bash deploy/hostinger/deploy_from_pc.sh --check --services LIST [--nginx]       ship, then a dry run on the VPS
#   bash deploy/hostinger/deploy_from_pc.sh --attach RUN_ID [--no-node-update]       follow a run after a dropped link
#
#   --services LIST     compose services to rebuild and recreate (required), e.g. 'ui-bridge' or 'ui-bridge,livekit';
#                       'none' ships the code only. Only the named services are touched ('up -d --no-deps'); the
#                       frontend is never rebuilt unless it is named (RUNBOOK, "The frontend rule").
#   --nginx             also install deploy/hostinger/jarvis.conf on the VPS (nginx -t, then reload)
#   --allow-dirty       deploy although the shipped paths have uncommitted changes (they are listed as NOT deployed)
#   --no-auto-rollback  passed to deploy.sh: after a failed gate print the rollback command instead of running it
#   --now               passed to deploy.sh: recreate 'agent' in vps mode without waiting for 10 idle minutes, after a
#                       60 s notice (K-72)
#   --no-node-update    after an agent deploy, print the 'agent_ctl node update' command instead of running it
#
# The VPS brain (C-128): a deploy that names 'agent' or 'agent-svc'
#   - runs the K-71 manifest check first, strict: JARVIS_MANIFEST_STRICT=1 scripts/desktop/export_manifest.py --check
#     (on this PC; C-118) in a scratch copy of the shipped commit ('git archive', read-only on the repo: no worktree,
#     no hook), so the commit's registry is compared with the commit's manifest whatever the working tree holds (an
#     uncommitted regenerated manifest, Cursor's uncommitted tool edits). It refuses when that manifest is stale, cannot
#     be checked, or the exporter says it read uncommitted files (not verified). JARVIS_MANIFEST_STRICT=1 in the
#     environment runs it before every deploy;
#   - once deploy.sh exited 0, runs '<python> scripts/agent_ctl.py node update --sha <AGENT_IMAGE_SHA> --by claude' (with
#     --canary while the pinned node runs in canary mode), so the laptop's Desktop Node runs the commit the agent image
#     was built from (deploy.sh reports it; normally the deployed commit). That commit (the one the node is pinned to)
#     gets the same strict manifest check first on a scratch copy of it, unless this run already checked it (the shipped
#     commit, before the deploy): so an image built from another commit, and every '--attach RUN_ID', which follows a
#     run this process did not check (also one deploy.sh started on the VPS, <UTC>-manual-<hex>, whose commit comes
#     from the log's 'sha' line). A stale manifest there, or a commit this repo does not have, leaves the node as it is
#     and ends in exit 6 (--no-node-update too). It runs whatever the PC checks give (the container runs the new image
#     either way); after a dropped link '--attach RUN_ID' runs it once the run ends healthy, and the exit-5 message
#     prints it. deploy.sh's agent gate is described there.
#   Env: JARVIS_PY (default .venv_312/Scripts/python.exe in the repo), JARVIS_AGENT_CTL (a test hook: the agent_ctl
#   command line to call instead).
#
# Only a commit is shipped, never the working tree: 'git archive' of deploy (the deploy/hostinger scripts and their
# requirements and constraints files among them) src services stonix_ui scripts (scripts/ops, scripts/desktop and
# scripts/models: the backup, restore and agent tools) .dockerignore and the root requirements*.txt, with
# core.autocrlf=false and core.eol=lf so shell scripts stay LF.
# HEAD is resolved once at the start; the archive, the file list and the service list all come from that commit,
# so a commit that lands meanwhile (Cursor works in the same tree) is not shipped under the recorded SHA.
# The VPS keeps it in /root/jarvis-backups/runs/<run>/; deploy.sh runs from there (the new version, never the old
# copy in /opt/jarvis-agent), takes the backup and only then installs the code and writes DEPLOYED_SHA.
# Env: JARVIS_VPS_HOST (default root@187.127.123.235), JARVIS_SSH_KEY (default ~/.ssh/id_ed25519_hostinger),
#   JARVIS_DOMAIN (default jarvis.srv1802669.hstgr.cloud).
# Exit codes: those of deploy.sh (0 deployed and healthy, 1 refused or nothing live changed, 2 rolled back
#   automatically, 3 act now), plus 5 = connection lost while the run goes on (the attach command is printed),
#   6 = deployed but a PC-side step failed (the checks, or after an agent deploy the node update or the manifest check
#   of AGENT_IMAGE_SHA before it; both steps always run),
#   64 = usage error. --attach ends the same way (the PC checks and the node update after a healthy end).
#   --dry-run: 0 = a real run would go ahead, 1 = it would refuse. --check: deploy.sh's dry run on the VPS, 0 = a real
#   run would go ahead now, 1 = it would refuse now (deploy.sh's VERDICT line says why, e.g. the idle gate).
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
NOW=0
NODE_UPDATE=1
DRY_RUN=0
CHECK=0
ATTACH=""
SHA=""
SHA_FULL=""
SHIP=()
REFUSE=()
NODE_ARGV=()
NODE_FAILED=""
MANIFEST_PROBLEM=""
MANIFEST_FIX=""
MANIFEST_CHECKED=()   # the full commit ids whose desktop manifest this run checked and found current (K-71)
ARCHIVE=""
MANIFEST_SCRATCH=""
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
      --now) NOW=1; shift ;;
      --no-node-update) NODE_UPDATE=0; shift ;;
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

agent_named() { in_list agent "${SERVICES[@]}" || in_list agent-svc "${SERVICES[@]}"; }

python_cmd() {  # the project venv's python on this PC (JARVIS_PY overrides); '' when none is found
  if [[ -n ${JARVIS_PY:-} ]]; then printf '%s\n' "$JARVIS_PY"; return 0; fi
  if [[ -x $ROOT/.venv_312/Scripts/python.exe ]]; then printf '%s\n' "$ROOT/.venv_312/Scripts/python.exe"; return 0; fi
  if [[ -x $ROOT/.venv_312/bin/python ]]; then printf '%s\n' "$ROOT/.venv_312/bin/python"; return 0; fi
  return 0
}

manifest_strict() {  # an agent deploy, or JARVIS_MANIFEST_STRICT=1 (also true/yes/on) in the environment
  agent_named && return 0
  case "${JARVIS_MANIFEST_STRICT:-}" in 1|true|TRUE|True|yes|on) return 0 ;; esac
  return 1
}

remove_manifest_scratch() {  # this run's own scratch copy of the commit (never anything else)
  if [[ -n $MANIFEST_SCRATCH && -d $MANIFEST_SCRATCH && $(basename "$MANIFEST_SCRATCH") == jarvis-manifest.* ]]; then
    rm -rf -- "$MANIFEST_SCRATCH" || true
  fi
  MANIFEST_SCRATCH=""
}

manifest_check_commit() {  # <commit> <label>: the strict K-71 check (JARVIS_MANIFEST_STRICT=1
  # scripts/desktop/export_manifest.py --check) in a scratch copy of that commit, so the working tree (an uncommitted
  # regenerated manifest, Cursor's uncommitted tool edits; --allow-dirty ships past both) can neither pass nor fail it.
  # 'git archive' only reads the repo: no worktree entry, no hook, nothing in the tree. 0 when the commit's manifest
  # matches its registry (its full id then joins MANIFEST_CHECKED); 1 otherwise, with MANIFEST_PROBLEM (and
  # MANIFEST_FIX for a stale one) set
  local commit="$1" label="$2" py out rc=0 full
  MANIFEST_PROBLEM=""
  MANIFEST_FIX=""
  py="$(python_cmd)"
  if [[ -z $py ]]; then
    MANIFEST_PROBLEM="the desktop manifest check needs the project python (.venv_312 in the repo, or JARVIS_PY)"
    return 1
  fi
  if ! full="$(git -C "$ROOT" rev-parse --quiet --verify "$commit^{commit}" 2> /dev/null)" || [[ -z $full ]]; then
    MANIFEST_PROBLEM="the desktop manifest of commit $label could not be checked: this repo has no such commit (K-71)"
    return 1
  fi
  if ! MANIFEST_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-manifest.XXXXXX")" \
      || ! git -C "$ROOT" -c core.autocrlf=false -c core.eol=lf archive --format=tar "$commit" \
        | tar -xf - -C "$MANIFEST_SCRATCH"; then
    remove_manifest_scratch
    MANIFEST_PROBLEM="the desktop manifest of commit $label could not be checked: the commit did not copy into a scratch folder (K-71)"
    return 1
  fi
  log "manifest: JARVIS_MANIFEST_STRICT=1 scripts/desktop/export_manifest.py --check in a scratch copy of commit $label (git archive): its registry against its manifest, whatever the working tree holds"
  out="$(cd "$MANIFEST_SCRATCH" && JARVIS_MANIFEST_STRICT=1 "$py" scripts/desktop/export_manifest.py --check 2>&1)" || rc=$?
  remove_manifest_scratch
  printf '%s\n' "$out" | tail -n 12 | sed 's/^/    /'
  if (( rc != 0 )); then
    MANIFEST_PROBLEM="the desktop manifest of commit $label is stale or could not be checked (export_manifest.py --check exit $rc)"
    MANIFEST_FIX="run scripts/desktop/export_manifest.py and commit services/ai_core/desktop_manifest.json first (K-71)"
    return 1
  fi
  if grep -qF 'uncommitted changes' <<< "$out"; then
    # the scratch copy has nothing uncommitted, so this would be a check of other files than the commit's
    MANIFEST_PROBLEM="the desktop manifest of commit $label was not verified: the exporter says it read uncommitted files, not the commit (K-71)"
    return 1
  fi
  MANIFEST_CHECKED+=("$full")
  return 0
}

manifest_checked() {  # <commit>: this run checked that commit's desktop manifest and found it current
  local full
  full="$(git -C "$ROOT" rev-parse --quiet --verify "$1^{commit}" 2> /dev/null || true)"
  [[ -n $full ]] && in_list "$full" "${MANIFEST_CHECKED[@]}"
}

manifest_check() {  # K-71 / C-118: a stale committed desktop manifest never ships with the VPS brain. The check runs on
  # commit SHA_FULL, the one that ships and that the builder bakes into the agent image built from it
  if ! manifest_strict; then
    log "manifest: not checked (no agent service named; JARVIS_MANIFEST_STRICT=1 checks every deploy)"
    return 0
  fi
  if ! manifest_check_commit "$SHA_FULL" "$SHA"; then
    REFUSE+=("$MANIFEST_PROBLEM${MANIFEST_FIX:+: $MANIFEST_FIX}")
  fi
}

node_run_mode() {  # 'canary' while the pinned Desktop Node runs in canary mode (run/node_current.json), else 'active'
  local f="${JARVIS_RUN_DIR:-$ROOT/run}/node_current.json"
  if [[ -f $f ]] && grep -Eq '"run_mode"[[:space:]]*:[[:space:]]*"canary"' "$f"; then echo canary; else echo active; fi
}

node_update_argv() {  # <sha>: NODE_ARGV, the agent_ctl call that pins the laptop's Desktop Node to that commit (C-123)
  local py
  local -a ctl=()
  if [[ -n ${JARVIS_AGENT_CTL:-} ]]; then
    read -r -a ctl <<< "$JARVIS_AGENT_CTL"
  else
    py="$(python_cmd)"
    ctl=("${py:-.venv_312/Scripts/python.exe}" "$ROOT/scripts/agent_ctl.py")
  fi
  NODE_ARGV=("${ctl[@]}" node update --sha "$1" --by claude --reason "C-128-deploy-${SHA:-$1}")
  if [[ $(node_run_mode) == canary ]]; then NODE_ARGV+=(--canary); fi
}

find_root() {
  ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" || die "not inside the JARVIS git repo"
  [[ -f $ROOT/deploy/hostinger/deploy.sh ]] || die "$ROOT is not the JARVIS repo"
}

local_checks() {
  local p dirty branch ahead svc
  local -a known=() top=()
  find_root
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
  manifest_check
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
  if (( NOW )); then cmd+=" --now"; fi
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
  if agent_named; then
    node_update_argv "<AGENT_IMAGE_SHA>"
    printf '    4. after a healthy agent deploy (%s): %s\n' "$( ((NODE_UPDATE)) && echo 'run' || echo 'print only, --no-node-update')" \
      "${NODE_ARGV[*]}"
    printf '       (AGENT_IMAGE_SHA: the commit the agent image was built from, which deploy.sh reports; normally %s)\n' "$SHA"
    printf '       (another commit than %s: the strict manifest check runs on a scratch copy of it first; stale or missing: no node update, exit 6)\n' "$SHA"
    printf '       (whatever the PC checks give, as the container runs the new image either way; after a dropped link, --attach <run id> runs it)\n'
    printf '       agent gate on the VPS: healthy within 180 s, /worker agent_name, LiveKit worker registered, models, net_guard, agent_hello on :5002, smoke diff identical; restarts idle-gated%s\n' \
      "$( ((NOW)) && echo ' (--now: a 60 s notice instead)' || echo ' (10 min without activity in vps mode; --now skips it after a notice)')"
  fi
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
  if agent_named && (( rc != 0 )); then
    log "if the VPS brain does not answer: .venv_312/Scripts/python.exe scripts/agent_ctl.py brain pc --now (the PC brain serves the owner)"
  fi
}

node_update_after_deploy() {  # pins the laptop's Desktop Node to the commit the agent image runs (C-128 step 2)
  local rev rc=0
  NODE_FAILED=""
  rev="$(sed -n 's/^\[deploy\] AGENT_IMAGE_SHA=//p' "$LOCAL_LOG" | tail -n 1 | tr -d '\r')"
  if [[ ! $rev =~ ^[0-9a-f]{7,40}$ ]]; then
    if [[ ! $SHA =~ ^[0-9a-f]{7,40}$ ]]; then
      log "the deploy is live, but neither the agent image's revision (AGENT_IMAGE_SHA=${rev:-none}) nor the deployed commit is known: pin the node by hand, on this PC: .venv_312/Scripts/python.exe scripts/agent_ctl.py node update --sha <the agent image's commit> --by claude --reason C-128-deploy"
      return 1
    fi
    log "note: deploy.sh reported no agent image revision (AGENT_IMAGE_SHA=${rev:-none}); the node is pinned to $SHA"
    rev="$SHA"
  elif [[ $rev != "$SHA"* && $SHA != "$rev"* ]]; then
    log "note: the agent image was built from $rev, not $SHA: the node is pinned to $rev, so it matches the image"
  fi
  # K-71: a node pinned to a commit whose committed manifest is not its registry answers MANIFEST_MISMATCH for every
  # changed laptop tool, so the commit the node is pinned to gets the strict scratch-copy check first, unless this run
  # already checked it (the shipped commit, before the deploy): an image built from another commit, and every
  # '--attach', which follows a run this process did not check (deploy.sh may even have started it on the VPS,
  # <UTC>-manual-<hex>, its commit from the log). A stale one, or one this repo cannot check, is never pinned
  if ! manifest_checked "$rev" && ! manifest_check_commit "$rev" "$rev"; then
    node_update_argv "$rev"
    log "node: NOT updated (the laptop's Desktop Node keeps its current commit): $MANIFEST_PROBLEM. Build the agent image from a commit whose desktop manifest is current (RUNBOOK, 'Agent image') and deploy agent again; or, once 'JARVIS_MANIFEST_STRICT=1 scripts/desktop/export_manifest.py --check' passes on $rev, pin the node by hand: ${NODE_ARGV[*]}"
    NODE_FAILED="the desktop manifest check of commit $rev, which the agent image runs (see above): the node was not updated"
    return 1
  fi
  node_update_argv "$rev"
  if (( ! NODE_UPDATE )); then
    log "node: not updated (--no-node-update); run it on this PC: ${NODE_ARGV[*]}"
    return 0
  fi
  log "node: ${NODE_ARGV[*]}"
  (cd "$ROOT" && "${NODE_ARGV[@]}") || rc=$?
  if (( rc != 0 )); then
    log "the deploy is live, but 'agent_ctl node update' failed (exit $rc); run it again: ${NODE_ARGV[*]}"
    return 1
  fi
  log "node: pinned to $rev"
}

node_hint_after_drop() {  # <run id>: a dropped link leaves the node update of an agent deploy to --attach (or to you)
  node_update_argv "<AGENT_IMAGE_SHA>"
  log "an agent deploy: once the run ends healthy, '--attach $1' also pins the laptop's Desktop Node to the agent image, after the strict manifest check of its commit (with --no-node-update it prints the call instead); by hand, with the '[deploy] AGENT_IMAGE_SHA=' line of the run's log, once 'JARVIS_MANIFEST_STRICT=1 scripts/desktop/export_manifest.py --check' passes on that commit: ${NODE_ARGV[*]}"
}

after_deploy() {  # deploy.sh exited 0: the PC checks, and after an agent deploy the node update (C-128 step 2). Both run
  # whatever the other gives (a moment of 000 or 502 from this PC leaves jarvis-agent on the new image all the same);
  # exit 6 when either failed
  local -a failed=()
  if ! pc_checks; then
    failed+=("the PC checks (check https://$DOMAIN/vortex/health and https://$DOMAIN/ from this PC again)")
  fi
  if agent_named && ! node_update_after_deploy; then
    failed+=("${NODE_FAILED:-the node update (run the command printed above)}")
  fi
  if (( ${#failed[@]} )); then
    log "the deploy is live (deploy.sh exit 0), but a PC-side step failed:"
    printf '[deploy_from_pc]   - %s\n' "${failed[@]}"
    exit 6
  fi
  log "DONE: $SHA is deployed (${SERVICES[*]:-code only})"
}

attached_run() {  # <run id>: SERVICES and SHA of a run this PC follows again: deploy.sh's services line from its log,
  # and the commit from the run id (<UTC>-<sha>-<n>, new_run_id), else from the log's 'sha' line
  local line tok sha
  local -a toks=()
  line="$({ sed -n 's/^\[deploy\] \{1,3\}services: \([^;]*\);.*$/\1/p' "$LOCAL_LOG" 2>/dev/null || true; } | tail -n 1 | tr -d '\r')"
  read -r -a toks <<< "$line"
  SERVICES=()
  for tok in "${toks[@]}"; do
    if [[ $tok != none && $tok =~ $SERVICE_RE ]] && ! in_list "$tok" "${SERVICES[@]}"; then SERVICES+=("$tok"); fi
  done
  sha="${1#*Z-}"
  sha="${sha%-*}"
  if [[ ! $sha =~ ^[0-9a-f]{7,40}$ ]]; then
    sha="$({ sed -n 's/^\[deploy\]   sha \([0-9a-f]\{7,40\}\) .*$/\1/p' "$LOCAL_LOG" 2>/dev/null || true; } | tail -n 1)"
  fi
  SHA="$sha"
}

attach() {  # <run id>: follow a run again; it ends like the deploy itself (report, PC checks, node update)
  local rc=0
  find_root
  LOCAL_LOG="${TMPDIR:-/tmp}/jarvis-deploy-$1.log"
  follow_remote "$(remote_attach_cmd "$REMOTE_RUNS/$1")" || rc=$?
  attached_run "$1"
  if (( rc == 255 )); then
    log "connection lost again; the run goes on: $0 --attach $1"
    if agent_named; then node_hint_after_drop "$1"; fi
    exit 5
  fi
  report_end "$rc" "$1"
  if (( rc != 0 )); then exit "$rc"; fi
  after_deploy
  exit 0
}

cleanup() {  # EXIT: this run's archive and scratch copy
  if [[ -n $ARCHIVE ]]; then rm -f -- "$ARCHIVE" || true; fi
  remove_manifest_scratch
}

main() {
  local id rc=0 run
  parse_args "$@"
  trap cleanup EXIT
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
    if agent_named; then node_hint_after_drop "$id"; fi
    exit 5
  fi
  report_end "$rc" "$id"
  if (( rc != 0 )); then exit "$rc"; fi
  after_deploy
}

main "$@"
