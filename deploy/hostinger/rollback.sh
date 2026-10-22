#!/usr/bin/env bash
# deploy/hostinger/rollback.sh - put the JARVIS services back on the image tags and code recorded by a deploy (or by
# any backup_vps.sh backup), plan C-12 / OP-08, contract K-54 COORD-4. It touches only the compose project jarvis,
# /opt/jarvis-agent and /etc/nginx/conf.d/jarvis.conf; every other app on the server is left alone.
#
# Usage (as root on the VPS; use the copy in /root/jarvis-backups/tools, which a code rollback never replaces):
#   rollback.sh [--last]                        undo the last deploy: back to its pre-deploy backup (default)
#   rollback.sh <backup>                        back to that folder of /root/jarvis-backups
#   rollback.sh --tag <tag> --services LIST     images only, from <image>:<tag> (e.g. rollback-<backup>)
#   rollback.sh [<backup>] --data               print the PC-side restore of that backup's agent data (changes nothing)
#   options: --services LIST  --code | --no-code  --nginx | --no-nginx  --with-env  --shared-values-differ-ok
#            --layout-unknown-ok  --data  --dry-run  --foreground
#
#   --data           (C-128) the VPS brain's data is never restored on the VPS: its backup (agent-volumes.tar.enc) is
#                    sealed to the PC backup key, so this prints the command that decrypts it on the PC and streams it
#                    back ('scripts/ops/restore_agent_data.py <backup>', which refuses while jarvis-agent or
#                    jarvis-agent-svc runs and moves the live data aside first, never deleting it). On its own (no
#                    --services, --code, --nginx or --with-env) it only prints, with no lock and no backup; with them,
#                    the rollback runs and the command is printed at its end. Refused (exit 1) when the backup holds
#                    no agent data.
#   agent, agent-svc (C-128): both run jarvis-agent:hostinger. When their recorded images differ, each service's tag
#                    goes back right before its own 'up' (rounds, the agent's first), so each container gets its own
#                    image, and at the end the shared tag names the agent's image again (a plain 'up' of agent keeps it;
#                    one of agent-svc would move it, which a warning says); with --tag,
#                    <image>:<tag>-<service> (backup_vps.sh's jarvis-agent:rollback-<backup>-agent-svc) wins over
#                    <image>:<tag> for that service. A rollback that recreates either prints AGENT_IMAGE_SHA=<the
#                    revision the agent runs> and the 'agent_ctl node update --sha <it>' command for the PC, so the
#                    laptop's Desktop Node matches the restored image (K-71: else MANIFEST_MISMATCH).
#
#   A backup made by deploy.sh or rollback.sh (it has deploy.env): the services it changed, and its code, nginx and
#   env choices, are the defaults. Any other backup_vps.sh folder: the services whose running image differs from its
#   containers.txt, and no code, nginx or env restore unless asked. A backup folder and --tag are never combined.
#   --services LIST  the compose services to put back ('none': only code / nginx / env); always 'up -d --no-deps'
#   --code           also restore /opt/jarvis-agent from <backup>/opt-jarvis-agent.tgz. Env files are never
#                    overwritten by this: they stay as they are now (see --with-env)
#   --nginx          also restore /etc/nginx/conf.d/jarvis.conf from the backup (nginx -t, then reload)
#   --with-env       (or --env) restore the image tags, docker-compose.yml and the env files together from one backup:
#                    the env files that the services of this rollback read (the env layout readers below: .env from
#                    hostinger.env, bridge.env, .env.agent from env.agent) plus root.env, mode 600, and, whenever .env
#                    goes back, the compose file of its opt-jarvis-agent.tgz (with no service that reads an env file,
#                    such as frontend or 'none', the live compose file stays with the live env files). Every other env
#                    file stays as it is. The live JARVIS_BRAIN and COMPOSE_PROFILES lines are written back into the
#                    restored .env (the brain switch owns them, K-72), and the live Desktop Link key lines into a
#                    restored bridge.env (DESKTOP_AGENT_PUBLIC_KEY, DESKTOP_PC_PUBLIC_KEY) and .env.agent (those and
#                    DESKTOP_AGENT_SIGNING_KEY): their other halves are on the laptop, which no rollback touches.
#                    Every other value comes back as the backup holds it, the owner login of bridge.env included,
#                    except that the shared values (AGENT_TOKEN, BRIDGE_HMAC_SECRET, LIVEKIT_API_KEY,
#                    LIVEKIT_API_SECRET, and the PC fallback's pair LIVEKIT_PC_API_KEY, LIVEKIT_PC_API_SECRET) must
#                    equal the live ones: the PC and every service this rollback does not recreate keep the live
#                    values, so a rotation since the backup undone in one file is refused (compared by digest, names
#                    only), and so is a backup .env whose PC secret was revoked since ('livekit_pc_pair.py revoke'):
#                    it is the one the laptop holds. The plan lists each file it replaces or keeps. With
#                    --tag rollback-<backup> the backup is that folder.
#   --shared-values-differ-ok  with --with-env: go on although a shared value of the restored env files differs from
#                    the live one; only when you make them equal again right after the rollback (its printed undo
#                    carries the option)
#   --layout-unknown-ok  go on when no backup folder records the target's env layout (a hand-made tag): only after
#                    checking that its images run with the live env files
#   --tag TAG        take the images from <image>:TAG instead of the backup's records (JARVIS's own jarvis-* images)
#   --dry-run        print the plan; change nothing
#   --foreground     stay attached (default: detached from the ssh session, log streamed here, as deploy.sh)
#
# Env layout (C-127): backup_vps.sh records in MANIFEST.txt which env files the containers read exist (.env,
#   bridge.env, .env.agent) and the names each assigns, never a value. Before any change the target's layout (that
#   record; for an older backup the names of its env copies) is compared with the live files, for the files the named
#   services read (ui-bridge: .env and bridge.env; livekit: .env; agent, agent-svc: .env and .env.agent; and .env
#   whenever --code brings another compose file). JARVIS_BRAIN and COMPOSE_PROFILES do not count: they always stay.
#   Every env file reader here reads past a UTF-8 byte order mark in front of the first line, as compose and the
#   Python env tools do (env_remote.env_entries), and never copies it into another line.
#   A different layout (for example a target from before the V2b secrets split) is refused unless --with-env, because
#   an old image with today's env files (or the reverse) can crash both brains. An unknown layout (a --tag with no
#   backup folder rollback-<folder>, such as a hand-made pre-emergency tag) is refused the same way unless
#   --layout-unknown-ok.
# Image tags: JARVIS's own jarvis-* tags are pointed at the recorded images. A third-party tag (LiveKit) only goes
#   back to the image it named when it was recorded, never onto another version: when the target state ran another
#   LiveKit version than the compose file in effect names, the rollback refuses and asks for --code.
# Steps: checks (services exist in the compose file they meet, no third-party tag would move, the env layout, with
#   --with-env the shared values) -> lock
#   -> backup_vps.sh pre-rollback (so 'rollback.sh <that backup>' undoes this rollback) -> smoke (pre)
#   -> stop services the target state did not have -> code -> nginx -> env files and compose file -> image tags
#   -> up -d --no-deps --no-build <services> -> health gate -> smoke (post).
# Exit codes: 0 rolled back and healthy; 1 refused or failed before any change; 3 failed after the changes started
#   (act now; the undo command is printed); 64 usage error.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# After a code rollback /opt/jarvis-agent can hold the old deploy.sh, which must never be sourced (it would deploy).
if ! grep -qx 'readonly DEPLOY_LIB_VERSION=3' "$HERE/deploy.sh" 2>/dev/null; then
  printf '[rollback] ERROR: %s is not the deploy.sh v3 library (C-128: the agent gate and the image rounds); use /root/jarvis-backups/tools/rollback.sh\n' \
    "$HERE/deploy.sh" >&2
  exit 1
fi
# shellcheck source=deploy.sh
source "$HERE/deploy.sh"
LOG_TAG=rollback
MODE=rollback

TARGET=""
TB=""
TAG=""
CODE_OPT=""
NGINX_OPT=""
ENV_OPT=""
SERVICES_GIVEN=0
IMG_LINES=""
NEW_SVCS=()
TARGET_SHA=unknown
TARGET_NOTE=""
ENV_TB=""         # the backup whose env files and env layout belong to the target (TB, or the folder of a rollback-* tag)
COMPOSE_TB=""     # the backup whose docker-compose.yml is in effect after the rollback ('' = the live one)
ENV_COMPOSE=0     # 1: --with-env puts that docker-compose.yml back (with .env; --code restores the whole tree anyway)
LAYOUT_NOTE=""
LAYOUT_UNKNOWN_OK=0
SHARED_OK=0       # --shared-values-differ-ok
SHARED_NOTE=""
DATA_OPT=0        # --data: print the PC-side restore of the target's agent data (C-128)
DATA_ONLY=0       # --data without --services/--code/--nginx/--with-env: print only, change nothing
DATA_TB=""        # the backup whose agent-volumes.tar.enc --data names
ENV_READERS=()    # the env files the services of this rollback read: compared, and with --with-env put back
ENV_PUT=()        # --with-env: '<file>:<copy in the backup, relative to it>' to install ('root' is /opt/jarvis-agent/.env)
ENV_ASIDE=()      # --with-env: live files moved aside (bridge.env that the target never had)
ENV_KEPT=()       # --with-env: live files left as they are, with the reason
# The env files the containers read (C-127), and their names in a backup folder
readonly -a LAYOUT_FILES=(.env bridge.env .env.agent)
readonly LAYOUT_KEPT_NAMES="JARVIS_BRAIN,COMPOSE_PROFILES"   # the brain switch's: a rollback keeps the live lines
# --with-env (C-127): the Desktop Link keys a restored file keeps from the live one (their other halves are on the
# laptop: its key, and its pin of the agent's key, C-123), and the values the env files share with the PC and with
# each other, which must equal the live ones (compared by digest): the notify credentials, the main LiveKit pair and
# the PC fallback's own pair (C-127 step 6: the laptop holds it, and after a revoke the backup's is the revoked one)
readonly LINK_BRIDGE_NAMES="DESKTOP_AGENT_PUBLIC_KEY DESKTOP_PC_PUBLIC_KEY"
readonly LINK_AGENT_NAMES="DESKTOP_AGENT_SIGNING_KEY DESKTOP_AGENT_PUBLIC_KEY DESKTOP_PC_PUBLIC_KEY"
readonly SHARED_NAMES="AGENT_TOKEN BRIDGE_HMAC_SECRET LIVEKIT_API_KEY LIVEKIT_API_SECRET LIVEKIT_PC_API_KEY LIVEKIT_PC_API_SECRET"
readonly EMPTY_SHA256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

parse_rollback_args() {
  while (( $# )); do
    case "$1" in
      -h|--help) usage "$HERE/rollback.sh"; exit 0 ;;
      --last) shift ;;
      --tag) (( $# >= 2 )) || usage_error "--tag needs a value"; TAG="$2"; shift 2 ;;
      --services) (( $# >= 2 )) || usage_error "--services needs a value"; parse_services "$2"; SERVICES_GIVEN=1; shift 2 ;;
      --services=*) parse_services "${1#*=}"; SERVICES_GIVEN=1; shift ;;
      --code) CODE_OPT=1; shift ;;
      --no-code) CODE_OPT=0; shift ;;
      --nginx) NGINX_OPT=1; shift ;;
      --no-nginx) NGINX_OPT=0; shift ;;
      --with-env|--env) ENV_OPT=1; shift ;;
      --shared-values-differ-ok) SHARED_OK=1; shift ;;
      --layout-unknown-ok) LAYOUT_UNKNOWN_OK=1; shift ;;
      --data) DATA_OPT=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --foreground) FOREGROUND=1; shift ;;
      --run-dir) (( $# >= 2 )) || usage_error "--run-dir needs a folder"; RUN="$2"; shift 2 ;;
      -*) usage_error "unknown option: $(safe "$1")" ;;
      *)
        [[ -z $TARGET ]] || usage_error "name only one backup folder"
        TARGET="$1"
        shift
        ;;
    esac
  done
  [[ -z $TARGET || ( $TARGET =~ $NAME_RE && $TARGET != */* ) ]] || usage_error "not a backup folder name: $(safe "$TARGET")"
  [[ -z $TAG || $TAG =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || usage_error "not a tag: $(safe "$TAG")"
  # C-127: the images of one state with the env layout and env files of another is the mix that crashes a bridge
  [[ -z $TARGET || -z $TAG ]] || usage_error "name a backup folder or --tag, not both (--tag rollback-<backup> takes the images, env layout and env files of that backup)"
  if [[ -n $RUN ]]; then validate_run_dir "$RUN"; fi
  # --data on its own only prints the PC-side restore command (C-128): no lock, no backup, nothing changed
  if (( DATA_OPT && ! SERVICES_GIVEN )) && [[ $CODE_OPT != 1 && $NGINX_OPT != 1 && $ENV_OPT != 1 ]]; then DATA_ONLY=1; fi
}

svc_container() {  # <service>: its container name (from the project, else the usual jarvis-* name)
  local id n=""
  id="$(ctr_id "$1" all)"
  if [[ -n $id ]]; then n="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null || true)"; fi
  n="${n#/}"
  if [[ -n $n ]]; then printf '%s\n' "$n"; return 0; fi
  case "$1" in
    ui-bridge) echo jarvis-bridge ;;
    frontend) echo jarvis-frontend ;;
    livekit) echo jarvis-livekit ;;
    *) echo "jarvis-$1" ;;
  esac
}

ctxt_field() {  # <containers.txt> <container> <field>: a field of backup_vps.sh's containers.txt line
  awk -v n="/$2" -v f="$3=" '$1 == n { for (i = 2; i <= NF; i++) if (index($i, f) == 1) { print substr($i, length(f) + 1); exit } }' \
    "$1" 2>/dev/null || true
}

current_ref() {  # <service> <container>: the image ref the container runs now, else what compose names
  local ref=""
  ref="$(docker inspect -f '{{.Config.Image}}' "$2" 2>/dev/null || true)"
  [[ -n $ref ]] || ref="$(service_image "$COMPOSE_FILE" "$1")"
  printf '%s\n' "$ref"
}

target_compose() {  # the docker-compose.yml in effect after this rollback, on stdout: the backup's own with --code or
  # --with-env (the compose file goes back together with the env files), else the live one
  if [[ -n $COMPOSE_TB ]]; then
    tar -xzOf "$COMPOSE_TB/opt-jarvis-agent.tgz" --occurrence=1 "$(basename "$APP_DIR")/deploy/hostinger/docker-compose.yml"
  else
    cat -- "$COMPOSE_FILE"
  fi
}

auto_services() {  # no record and no --services: the services whose running image is not the backup's
  local svc name rid cid
  local -a all=()
  [[ -f $TB/containers.txt ]] || die "$TB has neither deploy.env nor containers.txt: name the services with --services"
  mapfile -t all < <({ target_compose | compose_file - config --services 2>/dev/null || true; })
  SERVICES=()
  for svc in "${all[@]}"; do
    name="$(svc_container "$svc")"
    rid="$(ctxt_field "$TB/containers.txt" "$name" image_id)"
    [[ -n $rid ]] || continue
    cid="$(docker inspect -f '{{.Image}}' "$name" 2>/dev/null || true)"
    if [[ $rid != "$cid" ]]; then SERVICES+=("$svc"); fi
  done
}

build_image_record() {  # IMG_LINES: service, container, ref, image id to restore; NEW_SVCS: none recorded before
  local svc name ref id line
  IMG_LINES=""
  NEW_SVCS=()
  for svc in "${SERVICES[@]}"; do
    name="$(svc_container "$svc")"
    if [[ -n $TAG ]]; then
      ref="$(current_ref "$svc" "$name")"
      [[ -n $ref ]] || die "$svc: cannot tell its image name (is it a service in docker-compose.yml?)"
      # rollback and snapshot tags exist only for JARVIS's own images; a third-party tag is never re-pointed
      own_image "$ref" || die "$svc runs $ref: --tag takes only images JARVIS builds (jarvis-*); name a backup folder"
      # a service's own tag first (jarvis-agent:rollback-<backup>-agent-svc beside the agent's), then the shared one
      id="$(docker image inspect -f '{{.Id}}' "${ref%%:*}:$TAG-$svc" 2>/dev/null || true)"
      [[ -n $id ]] || id="$(docker image inspect -f '{{.Id}}' "${ref%%:*}:$TAG" 2>/dev/null || true)"
      [[ -n $id ]] || die "$svc: there is no image ${ref%%:*}:$TAG"
    elif [[ -f $TB/deploy-images.txt ]]; then
      line="$({ awk -F'\t' -v s="$svc" '$1 == s' "$TB/deploy-images.txt" || true; } | tail -n 1)"
      [[ -n $line ]] || die "$svc is not in $TB/deploy-images.txt"
      IFS=$'\t' read -r _ name ref id <<< "$line"
      if [[ $id == - ]]; then
        NEW_SVCS+=("$svc")
        IMG_LINES+="$svc"$'\t-\t-\t-\n'
        continue
      fi
    else
      ref="$(ctxt_field "$TB/containers.txt" "$name" image)"
      id="$(ctxt_field "$TB/containers.txt" "$name" image_id)"
      [[ -n $id && -n $ref ]] || die "$svc: container $name is not in $TB/containers.txt"
    fi
    docker image inspect "$id" >/dev/null 2>&1 || die "$svc: image ${id:7:12} no longer exists (docker image ls)"
    IMG_LINES+="$svc"$'\t'"$name"$'\t'"$ref"$'\t'"$id"$'\n'
  done
}

check_target() {  # before any change: each service exists in the compose file it meets, and no third-party tag moves
  local svc name ref id cur where="docker-compose.yml"
  local -a back=()
  if [[ -n $COMPOSE_TB ]]; then where="the docker-compose.yml in $(basename "$COMPOSE_TB")"; fi
  while IFS=$'\t' read -r svc name ref id; do
    if [[ -n $svc && $id != - ]]; then back+=("$svc"); fi
  done <<< "$IMG_LINES"
  # the services to stop are stopped before the code goes back; the others start from the target compose file
  if (( ${#NEW_SVCS[@]} )); then
    compose config -q "${NEW_SVCS[@]}" || die "docker compose config rejected the services ${NEW_SVCS[*]} (to stop)"
  fi
  if (( ${#back[@]} )); then
    target_compose | compose_file - config -q "${back[@]}" || die "$where rejects the services ${back[*]}"
  fi
  while IFS=$'\t' read -r svc name ref id; do
    [[ -n $svc && $id != - ]] || continue
    cur="$(target_compose | service_image - "$svc")" || cur=""
    [[ -n $cur ]] || die "$svc: cannot tell which image $where names for it"
    if [[ $cur == "$ref" ]] || own_image "$cur"; then continue; fi
    if [[ -n $COMPOSE_TB ]]; then
      die "$svc: $where names $cur, but the target state ran $ref; a third-party tag is never moved onto another image, so this rollback cannot bring $ref back (leave $svc out with --services)"
    fi
    die "$svc: the target state ran $ref, but docker-compose.yml names $cur; a third-party tag is never moved onto another image: add --code (it restores the compose file that names $ref) or leave $svc out with --services"
  done <<< "$IMG_LINES"
}

# ------------------------------------------------------------------ the env layout (C-127)
env_names() {  # <file>: the names it assigns a non-empty value, sorted, comma-separated, '-' for none; never a value.
  # The same rule as env_names() in backup_vps.sh, which records the layout (a test keeps the two equal). A UTF-8 BOM
  # in front of the first line is read past (compose does too), so the name on that line counts.
  local names bom=$'\xef\xbb\xbf'
  names="$({ sed -n -E -e "1s/^$bom//" -e 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*[^[:space:]#].*$/\2/p' "$1" \
    || true; } | LC_ALL=C sort -u | paste -sd, -)"
  printf '%s\n' "${names:--}"
}

layout_copy_name() {  # <env file>: its name in a backup folder
  case "$1" in
    .env) echo hostinger.env ;;
    bridge.env) echo bridge.env ;;
    .env.agent) echo env.agent ;;
  esac
}

env_copy() {  # <backup dir> <copy name>: the copy's path relative to the backup: env/<name> (backup_vps.sh v2, C-128),
  # else <name> (the v1 layout, where the env copies sat next to the other files)
  if [[ -f $1/env/$2 ]]; then printf 'env/%s\n' "$2"; else printf '%s\n' "$2"; fi
}

live_layout() {  # '<file> <present|absent> <names>' for each env file the containers read, from the live files
  local f
  for f in "${LAYOUT_FILES[@]}"; do
    if [[ -f $H/$f ]]; then printf '%s present %s\n' "$f" "$(env_names "$H/$f")"; else printf '%s absent -\n' "$f"; fi
  done
}

target_layout() {  # <backup dir>: its recorded layout (MANIFEST.txt), else the names of its env copies; empty: unknown
  local b="$1" f c
  if [[ -f $b/MANIFEST.txt ]] && grep -q '^env_layout:' "$b/MANIFEST.txt"; then
    awk '$1 == "env_layout:" { print $2, $3, ($4 == "" ? "-" : $4) }' "$b/MANIFEST.txt"
    return 0
  fi
  [[ -f $b/$(env_copy "$b" hostinger.env) ]] || return 0
  for f in "${LAYOUT_FILES[@]}"; do
    c="$(env_copy "$b" "$(layout_copy_name "$f")")"
    if [[ -f $b/$c ]]; then printf '%s present %s\n' "$f" "$(env_names "$b/$c")"; else printf '%s absent -\n' "$f"; fi
  done
}

layout_readers() {  # the env files that the services of this rollback read (and .env when --code brings a compose file)
  local svc f env=0 bridge=0 agent=0
  for svc in "${SERVICES[@]}"; do
    case "$svc" in
      ui-bridge) env=1; bridge=1 ;;
      livekit) env=1 ;;
      agent|agent-svc) env=1; agent=1 ;;
    esac
  done
  if (( CODE_OPT )); then env=1; fi
  for f in "${LAYOUT_FILES[@]}"; do
    case "$f" in
      .env) (( env )) && echo "$f" ;;
      bridge.env) (( bridge )) && echo "$f" ;;
      .env.agent) (( agent )) && echo "$f" ;;
    esac
  done
  return 0
}

layout_diff() {  # <reader files, space-separated>; stdin: 'T <layout line>' and 'L <layout line>': one line (names only)
  # per reader file whose state or names differ between the target (T) and the live files (L)
  awk -v readers="$1" -v kept="$LAYOUT_KEPT_NAMES" '
    function only(a, b,   pa, pb, ca, cb, k, inb, out) {
      ca = split(a, pa, ","); cb = split(b, pb, ",")
      for (k = 1; k <= cb; k++) inb[pb[k]] = 1
      out = ""
      for (k = 1; k <= ca; k++)
        if (pa[k] != "-" && pa[k] != "" && !(pa[k] in skip) && !(pa[k] in inb)) out = out (out == "" ? "" : ",") pa[k]
      return out
    }
    BEGIN { n = split(readers, R, " "); m = split(kept, K, ","); for (i = 1; i <= m; i++) skip[K[i]] = 1 }
    { st[$1, $2] = $3; nm[$1, $2] = $4 }
    END {
      for (i = 1; i <= n; i++) {
        f = R[i]
        ts = "absent"; if (("T", f) in st) ts = st["T", f]
        ls = "absent"; if (("L", f) in st) ls = st["L", f]
        ot = only(nm["T", f], nm["L", f]); ol = only(nm["L", f], nm["T", f])
        if (ts != ls || ot != "" || ol != "")
          printf "%s: target %s, live %s; only in the target: %s; only live: %s\n", f, ts, ls, (ot == "" ? "none" : ot), (ol == "" ? "none" : ol)
      }
    }'
}

resolve_env_folder() {  # ENV_TB: the target folder, or for --tag rollback-<backup> that backup when it exists
  local name
  ENV_TB="$TB"
  if [[ -z $ENV_TB && $TAG == rollback-* ]]; then
    name="${TAG#rollback-}"
    if [[ $name =~ $NAME_RE && $name != */* && -d $BASE/$name ]]; then ENV_TB="$BASE/$name"; fi
  fi
  return 0
}

check_env_layout() {  # LAYOUT_NOTE; refuses (exit 1, nothing changed) a different env layout without --with-env, and an
  # unknown one (nothing records it) without --layout-unknown-ok. ENV_READERS: the files compared (layout_readers)
  local target live line readers_line what
  local -a diffs=()
  if (( ${#ENV_READERS[@]} == 0 )); then
    LAYOUT_NOTE="not compared (no service of this rollback reads an env file)"
    return 0
  fi
  readers_line="${ENV_READERS[*]}"
  if [[ -n $ENV_TB ]]; then target="$(target_layout "$ENV_TB")"; else target=""; fi
  if [[ -z $target ]]; then
    if (( ENV_OPT )); then
      LAYOUT_NOTE="not recorded for $(basename "$ENV_TB"); its env files go back with --with-env"
      return 0
    fi
    if [[ -n $TAG ]]; then
      what="the tag :$TAG has no backup folder rollback-<folder> that records it"
    else
      what="$TARGET records none"
    fi
    if (( LAYOUT_UNKNOWN_OK )); then
      LAYOUT_NOTE="UNKNOWN ($what); $readers_line not compared, as --layout-unknown-ok asks"
      warn "env layout of the target unknown ($what): $readers_line not compared (--layout-unknown-ok)"
      return 0
    fi
    # an image from before the V2b secrets split (C-112: the legacy validate needs GOOGLE_API_KEY) crash-loops with
    # the split env files, and the reverse; with nothing to compare, that is refused like a differing layout
    die "the env layout of the target is unknown ($what), so $readers_line cannot be compared with the live env files: an image with the env files of another layout can crash both brains. Name a backup folder (or a rollback-<backup> tag whose folder still exists), or add --layout-unknown-ok once you know its images run with the live env files"
  fi
  live="$(live_layout)"
  mapfile -t diffs < <({ sed 's/^/T /' <<< "$target"; sed 's/^/L /' <<< "$live"; } | layout_diff "$readers_line")
  if (( ${#diffs[@]} == 0 )); then
    LAYOUT_NOTE="same as $(basename "$ENV_TB") ($readers_line; names only)"
    return 0
  fi
  for line in "${diffs[@]}"; do log "  env layout   $line"; done
  if (( ENV_OPT )); then
    LAYOUT_NOTE="differs from $(basename "$ENV_TB") ($readers_line): its env files and docker-compose.yml go back with the images (--with-env)"
    return 0
  fi
  die "the env layout of $(basename "$ENV_TB") differs from the live env files (names above, never values): an image with the env files of another layout can crash both brains. Add --with-env to restore its image tags, docker-compose.yml and env files together, or pick a target with the live layout"
}

env_value_digest() {  # <file> <name>: the sha256 of NAME's value as compose reads it (its last assignment; outer quotes
  # dropped, an unquoted value cut before ' #'; a UTF-8 BOM in front of the first line read past), '-' when the file
  # has no non-empty NAME. Only the digest leaves the pipe, never a value (C-127: how the values the env files share
  # are compared, names-only output).
  local d bom=$'\xef\xbb\xbf'
  if [[ ! -f $1 ]]; then printf -- '-\n'; return 0; fi
  d="$({ awk -v n="$2" -v bom="$bom" '
    NR == 1 && index($0, bom) == 1 { $0 = substr($0, length(bom) + 1) }
    { sub(/\r$/, "") }
    match($0, "^[ \t]*(export[ \t]+)?" n "[ \t]*=") { v = substr($0, RLENGTH + 1); found = 1 }
    END {
      if (!found) exit
      sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v)
      q = substr(v, 1, 1)
      if (q == "\"" || q == "\047") { v = substr(v, 2); e = index(v, q); if (e) v = substr(v, 1, e - 1) }
      else if (match(v, /[ \t]+#/)) v = substr(v, 1, RSTART - 1)
      printf "%s", v
    }' "$1" 2>/dev/null || true; } | sha256sum)"
  d="${d%% *}"
  if [[ $d == "$EMPTY_SHA256" ]]; then d=-; fi
  printf '%s\n' "$d"
}

check_shared_values() {  # --with-env (C-127): SHARED_NOTE. Each SHARED_NAMES value (the notify credentials, the main
  # LiveKit pair, the PC fallback's pair) that an env file put back holds must equal the value in the live env files:
  # the PC, the laptop and every service this rollback does not recreate keep the live one, so a value rotated since
  # the backup would come back in the restored file only (the agent's notifies dropped, LiveKit refusing the bridge or
  # the PC worker), and a PC secret revoked since the backup would work again at the next LiveKit recreate. Compared
  # by digest here on the VPS, names only; refused (exit 1, nothing changed) unless --shared-values-differ-ok.
  local item c n d g others compared=0
  local -a differ=()
  local -A live=()
  for g in "${LAYOUT_FILES[@]}"; do
    for n in $SHARED_NAMES; do live["$g/$n"]="$(env_value_digest "$H/$g" "$n")"; done
  done
  for item in "${ENV_PUT[@]}"; do
    [[ ${item%%:*} != root ]] || continue
    c="${item#*:}"
    for n in $SHARED_NAMES; do
      d="$(env_value_digest "$ENV_TB/$c" "$n")"
      [[ $d != - ]] || continue
      others=""
      for g in "${LAYOUT_FILES[@]}"; do
        if [[ ${live[$g/$n]} == - ]]; then continue; fi
        compared=1
        if [[ ${live[$g/$n]} != "$d" ]]; then others+="${others:+, }$g"; fi
      done
      if [[ -n $others ]]; then differ+=("$n: $c of the backup differs from the live $others"); fi
    done
  done
  if (( ${#differ[@]} == 0 )); then
    if (( compared )); then
      SHARED_NOTE="the restored env files hold the live ${SHARED_NAMES// /, } (compared by digest, names only)"
    else
      SHARED_NOTE="nothing to compare (no env file put back shares ${SHARED_NAMES// /, } with a live one)"
    fi
    return 0
  fi
  for item in "${differ[@]}"; do log "  env values   $item"; done
  if (( SHARED_OK )); then
    SHARED_NOTE="DIFFER from the live env files (names above), as --shared-values-differ-ok allows: make them equal again right after the rollback"
    warn "a shared value of the restored env files differs from the live one (names above): going on as --shared-values-differ-ok asks"
    return 0
  fi
  die "a value the restored env files share with the live ones differs (names above, never values): the PC, the laptop and the services this rollback does not recreate keep the live values, so the restored files would be out of step with them (the agent's notifies dropped, LiveKit refusing the bridge or the PC worker, or a revoked PC fallback pair accepted again). Make the values equal first, or add --shared-values-differ-ok when you make them equal again right after the rollback"
}

resolve_plan() {
  local kind rsvcs result plain=0
  if [[ -z $TARGET && -z $TAG ]]; then
    TARGET="$(head -n 1 "$LAST_DEPLOY_FILE" 2>/dev/null || true)"
    [[ -n $TARGET ]] || die "no deploy recorded in $LAST_DEPLOY_FILE yet; name a backup folder"
    [[ $TARGET =~ $NAME_RE ]] || die "$LAST_DEPLOY_FILE does not hold a backup folder name"
  fi
  if [[ -n $TARGET ]]; then
    TB="$BASE/$TARGET"
    [[ -d $TB ]] || die "no backup folder $TB"
  fi
  if (( DATA_OPT )); then
    resolve_env_folder   # a rollback-<backup> tag names its folder
    DATA_TB="${TB:-$ENV_TB}"
    [[ -n $DATA_TB ]] || usage_error "--data needs a backup folder: name it, or use a rollback-<backup> tag whose folder still exists"
    if [[ ! -f $DATA_TB/agent-volumes.tar.enc ]]; then
      die "$(basename "$DATA_TB") holds no agent data (no agent-volumes.tar.enc; its MANIFEST says agent_data: $({ sed -n 's/^agent_data: *//p' "$DATA_TB/MANIFEST.txt" 2>/dev/null || true; } | head -n 1)): nothing to restore"
    fi
    if (( DATA_ONLY )); then return 0; fi
  fi
  if [[ -n $TB && -f $TB/deploy.env ]]; then
    kind="$(record_get "$TB/deploy.env" KIND)"
    rsvcs="$(record_get "$TB/deploy.env" SERVICES)"
    result="$(record_get "$TB/deploy.env" RESULT)"
    TARGET_NOTE="the state before the ${kind:-deploy} to $(record_get "$TB/deploy.env" SHA) (result ${result:-?})"
    if (( ! SERVICES_GIVEN )); then
      SERVICES=()
      read -r -a SERVICES <<< "$rsvcs"
    fi
    [[ -n $CODE_OPT ]] || CODE_OPT="$(record_get "$TB/deploy.env" CODE)"
    [[ -n $NGINX_OPT ]] || NGINX_OPT="$(record_get "$TB/deploy.env" NGINX)"
    [[ -n $ENV_OPT ]] || ENV_OPT="$(record_get "$TB/deploy.env" ENV)"
    if [[ $result == rolled-back || -n "$(record_get "$TB/deploy.env" ROLLED_BACK)" ]]; then
      TARGET_NOTE+="; already rolled back once (running it again is harmless)"
    fi
  elif (( ! SERVICES_GIVEN )); then
    [[ -z $TAG ]] || usage_error "--tag needs --services"
    plain=1
  fi
  if [[ -n $TB && ! -f $TB/deploy.env ]]; then TARGET_NOTE="a plain backup_vps.sh backup"; fi
  [[ $CODE_OPT == 1 ]] || CODE_OPT=0
  [[ $NGINX_OPT == 1 ]] || NGINX_OPT=0
  [[ $ENV_OPT == 1 ]] || ENV_OPT=0
  resolve_env_folder
  if [[ -z $TB ]] && (( CODE_OPT || NGINX_OPT )); then
    usage_error "--code and --nginx need a backup folder"
  fi
  if [[ -z $ENV_TB ]] && (( ENV_OPT )); then
    usage_error "--with-env needs a backup folder: name it, or use a rollback-<backup> tag whose folder still exists"
  fi
  if (( SHARED_OK && ! ENV_OPT )); then usage_error "--shared-values-differ-ok goes with --with-env"; fi
  if (( CODE_OPT )); then
    code_archive_ok "$TB" || die "$TB has no usable opt-jarvis-agent.tgz (needed for --code)"
    COMPOSE_TB="$TB"
    target_compose > /dev/null 2>&1 || die "$COMPOSE_TB/opt-jarvis-agent.tgz has no deploy/hostinger/docker-compose.yml"
  fi
  if (( plain )); then auto_services; fi
  mapfile -t ENV_READERS < <(layout_readers)
  # --with-env brings docker-compose.yml back together with .env, which every service that reads an env file reads
  # (C-127). With no such service (frontend, 'none') no env file the compose file feeds goes back, so the live compose
  # file stays too: the backup's compose file with today's env files at the next recreate is the mix the env layout
  # check exists to stop
  if (( ENV_OPT && ! CODE_OPT )) && in_list .env "${ENV_READERS[@]}"; then
    code_archive_ok "$ENV_TB" || die "$ENV_TB has no usable opt-jarvis-agent.tgz (--with-env brings its docker-compose.yml back with .env)"
    COMPOSE_TB="$ENV_TB"
    ENV_COMPOSE=1
    target_compose > /dev/null 2>&1 || die "$COMPOSE_TB/opt-jarvis-agent.tgz has no deploy/hostinger/docker-compose.yml"
  fi
  build_image_record
  check_target
  check_env_layout
  if (( NGINX_OPT )); then [[ -f $TB/jarvis.conf ]] || die "$TB has no jarvis.conf (needed for --nginx)"; fi
  if (( ENV_OPT )); then
    [[ -f $ENV_TB/$(env_copy "$ENV_TB" hostinger.env) ]] || die "$ENV_TB has no hostinger.env (needed for --with-env)"
    plan_env_files "$ENV_TB"
    check_shared_values
  fi
  if [[ -n $TB && -f $TB/DEPLOYED_SHA ]]; then TARGET_SHA="$(head -n 1 "$TB/DEPLOYED_SHA")"; fi
  if (( CODE_OPT == 0 )); then TARGET_SHA="$(head -n 1 "$APP_DIR/DEPLOYED_SHA" 2>/dev/null || echo unknown)"; fi
  if (( ${#SERVICES[@]} == 0 && ! CODE_OPT && ! NGINX_OPT && ! ENV_OPT )); then
    die "nothing to roll back: no service differs from ${TARGET:-the tag} and no --code / --nginx / --with-env"
  fi
}

print_data_plan() {  # --data: the command that restores the agent data from the PC, which decrypts it
  local ts
  ts="$(basename "$DATA_TB")"
  log "data         the agent data of $ts goes back from the PC, which decrypts it (never on the VPS); on the PC, in the repo:"
  log "               .venv_312/Scripts/python.exe scripts/ops/restore_agent_data.py $ts --dry-run"
  log "               .venv_312/Scripts/python.exe scripts/ops/restore_agent_data.py $ts"
  log "             it refuses while jarvis-agent or jarvis-agent-svc runs (vps mode: 'agent_ctl brain pc' first) and moves the live data aside into .pre-restore-<UTC>/, never deleting it"
}

recreates_agent() {  # the plan brings agent or agent-svc back onto a recorded image (IMG_LINES)
  local svc name ref id
  while IFS=$'\t' read -r svc name ref id; do
    if [[ ( $svc == agent || $svc == agent-svc ) && -n $id && $id != - ]]; then return 0; fi
  done <<< "$IMG_LINES"
  return 1
}

print_rollback_plan() {
  local svc name ref id cur
  step "rollback plan"
  if (( DATA_ONLY )); then
    log "target       $DATA_TB (agent data only: --data prints, nothing changes on the VPS)"
    print_data_plan
    return 0
  fi
  if [[ -n $TB ]]; then log "target       $TB ($TARGET_NOTE)"; else log "target       images tagged :$TAG"; fi
  while IFS=$'\t' read -r svc name ref id; do
    [[ -n $svc ]] || continue
    if [[ $id == - ]]; then
      log "stop         $svc (the target state had no such service)"
      continue
    fi
    cur="$(docker inspect -f '{{.Image}}' "$name" 2>/dev/null || true)"
    log "image        $svc: $ref -> ${id:7:12} (running now: ${cur:7:12})"
  done <<< "$IMG_LINES"
  log "recreate     docker compose -p $PROJECT up -d --no-deps --no-build --pull never <services above> (GIT_SHA=$TARGET_SHA)"
  log "code         $([[ $CODE_OPT == 1 ]] && echo "restore $APP_DIR from $TB/opt-jarvis-agent.tgz (env files untouched)" || echo 'not touched')"
  log "nginx        $([[ $NGINX_OPT == 1 ]] && echo "restore jarvis.conf from $TB, nginx -t, reload" || echo 'not touched')"
  if (( ENV_OPT )); then print_env_plan; else log "env          not touched"; fi
  log "env layout   $LAYOUT_NOTE"
  if (( ENV_OPT )); then log "env values   $SHARED_NOTE"; fi
  log "first        backup_vps.sh pre-rollback, smoke check (pre); after: health gate, smoke check (post)"
  if recreates_agent; then
    log "node         after the recreate: AGENT_IMAGE_SHA=<the revision the agent runs then> and '$PC_NODE_UPDATE --sha <it>' for the PC: the laptop's Desktop Node must match the restored image (K-71)"
  fi
  if (( DATA_OPT )); then print_data_plan; fi
}

env_dest() {  # <env file of ENV_PUT ('root' for the root .env)>: the live path it is installed to
  if [[ $1 == root ]]; then printf '%s\n' "$APP_DIR/.env"; else printf '%s\n' "$H/$1"; fi
}

plan_env_files() {  # <backup dir>: ENV_PUT, ENV_ASIDE and ENV_KEPT for --with-env (C-127). Only the env files that the
  # services of this rollback read (ENV_READERS) go back: a file whose readers are not recreated would meet the old
  # values (or names) at their next restart, out of step with their image. root.env (no container reads it) goes back
  # as before.
  local b="$1" f c
  ENV_PUT=()
  ENV_ASIDE=()
  ENV_KEPT=()
  for f in "${LAYOUT_FILES[@]}"; do
    c="$(env_copy "$b" "$(layout_copy_name "$f")")"
    if ! in_list "$f" "${ENV_READERS[@]}"; then
      if [[ -f $H/$f ]]; then ENV_KEPT+=("$f (no service of this rollback reads it)"); fi
    elif [[ -f $b/$c ]]; then
      ENV_PUT+=("$f:$c")
    elif [[ $f == bridge.env && -f $H/$f ]]; then
      ENV_ASIDE+=("$f")   # bridge.env wins over .env: a live one the target never had would shadow the restored .env
    elif [[ -f $H/$f ]]; then
      ENV_KEPT+=("$f (the backup has no ${c#env/})")   # .env.agent: the agent services need one
    fi
  done
  c="$(env_copy "$b" root.env)"
  if [[ -f $b/$c ]]; then ENV_PUT+=("root:$c"); fi
  return 0
}

env_put_has() {  # <env file of ENV_PUT ('root' for the root .env)>: true when the plan puts it back
  local item
  for item in "${ENV_PUT[@]}"; do [[ ${item%%:*} == "$1" ]] && return 0; done
  return 1
}

print_env_plan() {  # every file --with-env replaces, moves aside or keeps
  local item f note="" link=""
  plan_env_files "$ENV_TB"
  if env_put_has .env; then
    note="; the live JARVIS_BRAIN and COMPOSE_PROFILES lines are written back into .env"
  fi
  if env_put_has bridge.env; then link="bridge.env"; fi
  if env_put_has .env.agent; then link+="${link:+ and }.env.agent"; fi
  if [[ -n $link ]]; then note+="; the live Desktop Link key lines are written back into $link"; fi
  log "env          from $ENV_TB (mode 600$note)"
  for item in "${ENV_PUT[@]}"; do log "  replace    $(env_dest "${item%%:*}") <- ${item#*:}"; done
  if (( ENV_COMPOSE )); then
    log "  replace    $COMPOSE_FILE <- the docker-compose.yml in opt-jarvis-agent.tgz"
  elif (( ! CODE_OPT )); then
    log "  keep       $COMPOSE_FILE (no service of this rollback reads an env file, so .env and the compose file stay)"
  fi
  for f in "${ENV_ASIDE[@]}"; do log "  aside      $H/$f (the target had none; it would win over the restored .env)"; done
  for f in "${ENV_KEPT[@]}"; do log "  keep       $H/$f"; done
}

live_env_line() {  # <env file> <name>: its last NAME=... line, the one compose takes ('' when it has none). A UTF-8 BOM
  # in front of the first line is read past, as compose does, and never part of the line returned (it is written into
  # the middle of another file, where compose would take it for a part of the name).
  local bom=$'\xef\xbb\xbf'
  { sed "1s/^$bom//" "$1" 2>/dev/null | grep -E "^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*=" || true; } | tail -n 1
}

keep_env_line() {  # <restored env file> <live line> <name>: the live NAME=... line replaces every NAME line of the
  # restored file. JARVIS_BRAIN and COMPOSE_PROFILES belong to the brain switch (agent_ctl brain, K-72), never to a
  # rollback, and the Desktop Link keys to the laptop's pairing: the backup's own lines never win (C-127). No live
  # line (COMPOSE_PROFILES, a link key): the backup's goes too. No live JARVIS_BRAIN value: the restored file keeps its
  # own (compose's ${JARVIS_BRAIN:?} rejects an empty one). A NAME line behind a UTF-8 BOM (the first line) counts and
  # goes too, the BOM with it. The line itself (a value) is never printed.
  local file="$1" line="$2" name="$3" value="${2#*=}" had=0 pat bom=$'\xef\xbb\xbf'
  pat="^[[:space:]]*(export[[:space:]]+)?$name[[:space:]]*="
  if [[ $name == JARVIS_BRAIN && -z ${value//[[:space:]]/} ]]; then return 0; fi
  # a here-string, not a pipe: 'grep -q' stops at the first match, and under pipefail the writer's SIGPIPE would fail it
  if grep -qE "$pat" <<< "$(sed "1s/^$bom//" "$file" 2>/dev/null)"; then
    sed -i -E -e "1{/^$bom${pat#^}/d;}" -e "/$pat/d" "$file" || return 1
    had=1
  fi
  if [[ -z $line ]]; then
    if (( had )); then log "  dropped $name from $file (the live $(basename -- "$file") has none)"; fi
    return 0
  fi
  if [[ -s $file && -n "$(tail -c 1 "$file")" ]]; then printf '\n' >> "$file" || return 1; fi
  printf '%s\n' "$line" >> "$file" && log "  kept the live $name in $file"
}

restore_env() {  # <backup dir>: the env files of plan_env_files back, together (C-127)
  local b="$1" item f dst brain profiles aside n names
  local -A link=()
  plan_env_files "$b"
  # C-126: the compose file needs a non-empty JARVIS_BRAIN in .env and a backup from before C-126 has none. The live
  # brain and its COMPOSE_PROFILES belong to the brain switch (agent_ctl brain), never to a rollback, so they stay.
  brain="$(live_env_line "$H/.env" JARVIS_BRAIN)"
  profiles="$(live_env_line "$H/.env" COMPOSE_PROFILES)"
  # C-127: the Desktop Link keys stay as they are live. Their other halves are on the laptop (its own key, its pin of
  # the agent's key) and in the env file a one-sided rollback keeps, so an older key would cut the laptop off.
  for n in $LINK_AGENT_NAMES; do
    link["bridge.env/$n"]="$(live_env_line "$H/bridge.env" "$n")"
    link[".env.agent/$n"]="$(live_env_line "$H/.env.agent" "$n")"
  done
  for f in "${ENV_ASIDE[@]}"; do   # moved aside, never deleted
    aside="$H/$f.pre-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
    if mv -- "$H/$f" "$aside"; then log "  moved $H/$f aside to $aside (the target had none)"; else fail "cannot move $H/$f aside"; fi
  done
  for item in "${ENV_PUT[@]}"; do
    f="${item%%:*}"
    dst="$(env_dest "$f")"
    if ! install_root_file 600 "$b/${item#*:}" "$dst"; then
      fail "cannot restore $dst"
      continue
    fi
    log "  restored $dst"
    if [[ $f == .env ]]; then
      keep_env_line "$dst" "$brain" JARVIS_BRAIN || fail "cannot keep JARVIS_BRAIN in $dst"
      keep_env_line "$dst" "$profiles" COMPOSE_PROFILES || fail "cannot keep COMPOSE_PROFILES in $dst"
    fi
    names=""
    if [[ $f == bridge.env ]]; then names="$LINK_BRIDGE_NAMES"; elif [[ $f == .env.agent ]]; then names="$LINK_AGENT_NAMES"; fi
    for n in $names; do
      keep_env_line "$dst" "${link[$f/$n]}" "$n" || fail "cannot keep $n in $dst"
    done
  done
  for f in "${ENV_KEPT[@]}"; do log "  kept the live $H/$f"; done
}

restore_compose_file() {  # <backup dir>: only deploy/hostinger/docker-compose.yml from its code archive (C-127)
  local member
  member="$(basename "$APP_DIR")/deploy/hostinger/docker-compose.yml"
  if tar -xzf "$1/opt-jarvis-agent.tgz" -C "$(dirname "$APP_DIR")" --occurrence=1 "$member"; then
    log "  restored $COMPOSE_FILE from $(basename "$1") (it goes back with the env files)"
  else
    fail "cannot restore $COMPOSE_FILE from $1"
  fi
}

run_rollback() {
  local svc undo
  local -a up=()
  start_run
  resolve_plan
  print_rollback_plan
  take_lock
  PHASE=backup
  SHA="$TARGET_SHA"
  NGINX="$NGINX_OPT"
  run_backup pre-rollback || die "backup_vps.sh failed: nothing was changed"
  log "BACKUP=$BNAME"
  PREV_SHA="$(head -n 1 "$APP_DIR/DEPLOYED_SHA" 2>/dev/null || echo none)"
  # this backup records the state before the rollback, so 'rollback.sh <it>' undoes the rollback
  record_write "$B/deploy.env" KIND=rollback "TARGET=${TARGET:-tag:$TAG}" "SHA=$TARGET_SHA" "PREV_SHA=$PREV_SHA" \
    "SERVICES=${SERVICES[*]}" "CODE=$CODE_OPT" "NGINX=$NGINX_OPT" "ENV=$ENV_OPT" "STARTED=$(now_utc)" RESULT=running
  record_images "$B/deploy-images.txt" "${SERVICES[@]}"
  tag_rollback_images "$B/deploy-images.txt"
  printf '%s' "$IMG_LINES" > "$B/restore-images.txt"
  ROOT_CODE_PRE="$(http_code "${VIA_NGINX[@]}" "https://$DOMAIN/")"
  step "other apps: smoke check (pre)"
  smoke_snapshot "$B/smoke-pre.txt" || die "smoke_other_apps.sh failed: nothing was changed"
  PHASE=change
  set +e   # from here every step runs and records its own failure
  if (( ${#NEW_SVCS[@]} )); then
    step "stop ${NEW_SVCS[*]} (not part of the target state)"
    compose stop "${NEW_SVCS[@]}" || fail "could not stop ${NEW_SVCS[*]}"
  fi
  if (( CODE_OPT )); then step "code"; CODE_CHANGED=1; restore_code "$TB"; fi
  if (( NGINX_OPT )); then step "nginx"; NGINX_CHANGED=1; restore_nginx_conf "$TB" 1 || fail "nginx restore failed"; fi
  if (( ENV_OPT )); then
    step "env files$( (( ! ENV_COMPOSE )) || echo ' and docker-compose.yml')"
    restore_env "$ENV_TB"
    if (( ENV_COMPOSE )); then restore_compose_file "$ENV_TB"; fi
  fi
  for svc in "${SERVICES[@]}"; do in_list "$svc" "${NEW_SVCS[@]}" || up+=("$svc"); done
  if (( ${#up[@]} )); then
    step "images and recreate: ${up[*]}"
    GIT_SHA_ENV="$TARGET_SHA"
    TOUCHED=("${up[@]}")
    # each service's tag goes back right before its own 'up' (rounds: agent and agent-svc share jarvis-agent:hostinger)
    recreate_in_rounds "$B/restore-images.txt" "docker compose up failed" "${up[@]}"
  fi
  PHASE=verify
  run_gates "${up[@]}"
  smoke_verify "$B"
  if [[ -n $TB && -f $TB/deploy.env ]]; then record_set "$TB/deploy.env" ROLLED_BACK "$(now_utc) by $BNAME"; fi
  # its undo puts back the env files this rollback replaced: with differing shared values it needs the option again
  undo="bash $TOOLS_DIR/rollback.sh $BNAME$( (( ! (ENV_OPT && SHARED_OK) )) || echo ' --shared-values-differ-ok')"
  if (( ${#FAILS[@]} )); then
    finish failed 3
    log "the rollback is not healthy; undo it with: $undo"
  else
    finish ok 0
    log "undo this rollback with: $undo"
  fi
  # C-128: the node update after an agent deploy pinned the laptop's Desktop Node to the image this rollback replaced
  if in_list agent "${up[@]}" || in_list agent-svc "${up[@]}"; then
    step "the laptop's Desktop Node (K-71)"
    node_pin_hint "C-128-rollback-$BNAME"
  fi
  if (( DATA_OPT )); then step "agent data (--data)"; print_data_plan; fi
}

rollback_main() {
  local -a orig=("$@")
  local rc=0
  parse_rollback_args "$@"
  require_host
  if (( DATA_ONLY )); then
    resolve_plan
    print_rollback_plan
    log "VERDICT: nothing changed (--data on its own only prints the PC-side restore command)"
    FINAL_RC=0
    return 0
  fi
  if (( DRY_RUN )); then
    resolve_plan
    print_rollback_plan
    log "VERDICT: a real run would proceed (dry run: nothing changed)"
    FINAL_RC=0
    return 0
  fi
  if (( ! FOREGROUND )) && [[ -z ${JARVIS_DETACHED:-} ]]; then
    resolve_plan   # fail fast here; the detached run resolves the same plan again
    [[ -n $RUN ]] || RUN="$(new_run_dir rollback)"
    detach_and_follow "$RUN" "$HERE/rollback.sh" "${orig[@]}" --run-dir "$RUN" --foreground || rc=$?
    FINAL_RC=$rc
    return 0
  fi
  run_rollback
}

rollback_main "$@"
exit "${FINAL_RC:-0}"
