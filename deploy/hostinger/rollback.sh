#!/usr/bin/env bash
# deploy/hostinger/rollback.sh - put the JARVIS services back on the image tags and code recorded by a deploy (or by
# any backup_vps.sh backup), plan C-12 / OP-08, contract K-54 COORD-4. It touches only the compose project jarvis,
# /opt/jarvis-agent and /etc/nginx/conf.d/jarvis.conf; every other app on the server is left alone.
#
# Usage (as root on the VPS; use the copy in /root/jarvis-backups/tools, which a code rollback never replaces):
#   rollback.sh [--last]                        undo the last deploy: back to its pre-deploy backup (default)
#   rollback.sh <backup>                        back to that folder of /root/jarvis-backups
#   rollback.sh --tag <tag> --services LIST     images only, from <image>:<tag> (e.g. pre-emergency-2026-09-30_2340)
#   options: --services LIST  --code | --no-code  --nginx | --no-nginx  --env  --dry-run  --foreground
#
#   A backup made by deploy.sh or rollback.sh (it has deploy.env): the services it changed, and its code and nginx
#   choices, are the defaults. Any other backup_vps.sh folder: the services whose running image differs from its
#   containers.txt, and no code, nginx or env restore unless asked.
#   --services LIST  the compose services to put back ('none': only code / nginx / env); always 'up -d --no-deps'
#   --code           also restore /opt/jarvis-agent from <backup>/opt-jarvis-agent.tgz. Env files are never
#                    overwritten by this: they stay as they are now (see --env)
#   --nginx          also restore /etc/nginx/conf.d/jarvis.conf from the backup (nginx -t, then reload)
#   --env            also restore the env files kept in the backup (hostinger.env, bridge.env, root.env), mode 600
#   --tag TAG        take the images from <image>:TAG instead of the backup's records (JARVIS's own jarvis-* images)
#   --dry-run        print the plan; change nothing
#   --foreground     stay attached (default: detached from the ssh session, log streamed here, as deploy.sh)
#
# Image tags: JARVIS's own jarvis-* tags are pointed at the recorded images. A third-party tag (LiveKit) only goes
#   back to the image it named when it was recorded, never onto another version: when the target state ran another
#   LiveKit version than the compose file in effect names, the rollback refuses and asks for --code.
# Steps: checks (services exist in the compose file they meet, no third-party tag would move) -> lock ->
#   backup_vps.sh pre-rollback (so 'rollback.sh <that backup>' undoes this rollback) -> smoke (pre)
#   -> stop services the target state did not have -> code -> nginx -> env -> image tags
#   -> up -d --no-deps --no-build <services> -> health gate -> smoke (post).
# Exit codes: 0 rolled back and healthy; 1 refused or failed before any change; 3 failed after the changes started
#   (act now; the undo command is printed); 64 usage error.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# After a code rollback /opt/jarvis-agent can hold the old deploy.sh, which must never be sourced (it would deploy).
if ! grep -qx 'readonly DEPLOY_LIB_VERSION=2' "$HERE/deploy.sh" 2>/dev/null; then
  printf '[rollback] ERROR: %s is not the deploy.sh v2 library; use /root/jarvis-backups/tools/rollback.sh\n' \
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
      --env) ENV_OPT=1; shift ;;
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
  if [[ -n $RUN ]]; then validate_run_dir "$RUN"; fi
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

target_compose() {  # the docker-compose.yml in effect after this rollback, on stdout: with --code, the backup's own
  if (( CODE_OPT )); then
    tar -xzOf "$TB/opt-jarvis-agent.tgz" --occurrence=1 "$(basename "$APP_DIR")/deploy/hostinger/docker-compose.yml"
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
      id="$(docker image inspect -f '{{.Id}}' "${ref%%:*}:$TAG" 2>/dev/null || true)"
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
  if (( CODE_OPT )); then where="the docker-compose.yml in $TARGET"; fi
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
    if (( CODE_OPT )); then
      die "$svc: $where names $cur, but the target state ran $ref; a third-party tag is never moved onto another image, so this rollback cannot bring $ref back (leave $svc out with --services)"
    fi
    die "$svc: the target state ran $ref, but docker-compose.yml names $cur; a third-party tag is never moved onto another image: add --code (it restores the compose file that names $ref) or leave $svc out with --services"
  done <<< "$IMG_LINES"
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
  if [[ -z $TB ]] && (( CODE_OPT || NGINX_OPT || ENV_OPT )); then
    usage_error "--code, --nginx and --env need a backup folder"
  fi
  if (( CODE_OPT )); then
    code_archive_ok "$TB" || die "$TB has no usable opt-jarvis-agent.tgz (needed for --code)"
    target_compose > /dev/null 2>&1 || die "$TB/opt-jarvis-agent.tgz has no deploy/hostinger/docker-compose.yml"
  fi
  if (( plain )); then auto_services; fi
  build_image_record
  check_target
  if (( NGINX_OPT )); then [[ -f $TB/jarvis.conf ]] || die "$TB has no jarvis.conf (needed for --nginx)"; fi
  if (( ENV_OPT )); then [[ -f $TB/hostinger.env ]] || die "$TB has no hostinger.env (needed for --env)"; fi
  if [[ -n $TB && -f $TB/DEPLOYED_SHA ]]; then TARGET_SHA="$(head -n 1 "$TB/DEPLOYED_SHA")"; fi
  if (( CODE_OPT == 0 )); then TARGET_SHA="$(head -n 1 "$APP_DIR/DEPLOYED_SHA" 2>/dev/null || echo unknown)"; fi
  if (( ${#SERVICES[@]} == 0 && ! CODE_OPT && ! NGINX_OPT && ! ENV_OPT )); then
    die "nothing to roll back: no service differs from ${TARGET:-the tag} and no --code / --nginx / --env"
  fi
}

print_rollback_plan() {
  local svc name ref id cur
  step "rollback plan"
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
  log "env          $([[ $ENV_OPT == 1 ]] && echo "restore the env files from $TB (mode 600)" || echo 'not touched')"
  log "first        backup_vps.sh pre-rollback, smoke check (pre); after: health gate, smoke check (post)"
}

restore_env() {  # <backup dir>
  local b="$1" i
  local -a src=(hostinger.env bridge.env root.env) dst=("$H/.env" "$H/bridge.env" "$APP_DIR/.env")
  for i in "${!src[@]}"; do
    [[ -f $b/${src[$i]} ]] || continue
    if install_root_file 600 "$b/${src[$i]}" "${dst[$i]}"; then
      log "  restored ${dst[$i]}"
    else
      fail "cannot restore ${dst[$i]}"
    fi
  done
}

run_rollback() {
  local svc
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
  if (( ENV_OPT )); then step "env files"; restore_env "$TB"; fi
  for svc in "${SERVICES[@]}"; do in_list "$svc" "${NEW_SVCS[@]}" || up+=("$svc"); done
  if (( ${#up[@]} )); then
    step "images and recreate: ${up[*]}"
    restore_images "$B/restore-images.txt" "${up[@]}"
    GIT_SHA_ENV="$TARGET_SHA"
    TOUCHED=("${up[@]}")
    compose up -d --no-deps --no-build --pull never "${up[@]}" || fail "docker compose up failed"
  fi
  PHASE=verify
  run_gates "${up[@]}"
  smoke_verify "$B"
  if [[ -n $TB && -f $TB/deploy.env ]]; then record_set "$TB/deploy.env" ROLLED_BACK "$(now_utc) by $BNAME"; fi
  if (( ${#FAILS[@]} )); then
    finish failed 3
    log "the rollback is not healthy; undo it with: bash $TOOLS_DIR/rollback.sh $BNAME"
  else
    finish ok 0
    log "undo this rollback with: bash $TOOLS_DIR/rollback.sh $BNAME"
  fi
}

rollback_main() {
  local -a orig=("$@")
  local rc=0
  parse_rollback_args "$@"
  require_host
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
