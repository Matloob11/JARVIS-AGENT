#!/usr/bin/env bash
# deploy/hostinger/smoke_other_apps.sh - passive before/after check that the OTHER apps on the shared VPS are not
# affected by a JARVIS change (plan C-12 / OP-08, contract K-54 COORD-4). deploy.sh and rollback.sh run it.
#
# Usage (as root on the VPS):
#   bash smoke_other_apps.sh > smoke-pre.txt              snapshot: sorted lines on stdout, a summary on stderr
#   bash smoke_other_apps.sh --compare PRE.txt POST.txt   what changed between two snapshots
#
# The public repo holds no inventory of the other apps. Their sites, containers and listeners are discovered at
# runtime, and every name is shown only as a salted hash (salt: /root/jarvis-backups/.smoke-salt, created on first
# use, mode 600). Snapshots and logs therefore carry counts, states and status codes, never names.
#
# Snapshot lines:
#   nginx-test ok|fail               'nginx -T' (config test + dump; the dump is parsed in memory, never printed)
#   site <hash> <code>               one 'GET /' per nginx server_name served on 443 (https) or 80 (http), sent to
#                                    the local nginx (curl --resolve, 8 s); 000 = no answer. Skipped: JARVIS's own
#                                    names, '_', localhost, IP, wildcard and regex names, and other ports.
#   ctr <hash> <state> <start-hash>  each running container outside the compose project jarvis;
#                                    state = healthy|up|starting|unhealthy|restarting|paused|other
#   listen <hash>                    each TCP listener (ss -tln) except JARVIS's own published ports
# Passive: apart from that one 'GET /' per site through nginx (what any visitor does) it only reads (nginx -T,
# docker ps/inspect, ss). It never connects to another app's own ports and changes nothing except creating the salt.
#
# --compare prints REGRESSION, WARN and INFO lines and a SUMMARY line. Exit codes: 0 identical, 1 changed without
# a regression, 2 regression (a site turned 5xx/000, a container turned unhealthy/restarting/paused, or nginx -T
# started failing). Errors exit 4, usage errors 64.
set -euo pipefail
umask 077

# JARVIS_DEPLOY_SANDBOX: test hook only (tests/unit/test_deploy_tooling.py); it moves the salt file into a scratch
# folder. Never set it on the VPS.
readonly BASE="${JARVIS_DEPLOY_SANDBOX:-}/root/jarvis-backups"
readonly SALT_FILE="$BASE/.smoke-salt"
readonly JARVIS_DOMAIN=jarvis.srv1802669.hstgr.cloud
readonly JARVIS_PROJECT=jarvis
readonly JARVIS_PORTS="5100 5101 7880 7881"   # JARVIS's own published ports; never 'listen' lines
readonly CURL_MAX_S=8
readonly PROBES_IN_PARALLEL=6

SALT=""

die() { printf '[smoke] ERROR: %s\n' "$*" >&2; exit 4; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

load_salt() {
  local tmp
  if [[ ! -s $SALT_FILE ]]; then
    mkdir -p -- "$BASE"
    tmp="$SALT_FILE.$$"
    od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > "$tmp"
    # the first writer wins, so two runs that start together still share one salt
    ln -- "$tmp" "$SALT_FILE" 2>/dev/null || mv -n -- "$tmp" "$SALT_FILE" 2>/dev/null || true
    rm -f -- "$tmp"
  fi
  SALT="$(cat -- "$SALT_FILE")"
  [[ $SALT =~ ^[0-9a-f]{32}$ ]] || die "$SALT_FILE does not hold a 32-hex-digit salt"
}

# 'nginx -T' on stdin -> "scheme name addr" per server_name, then "#skipped <n>". It tracks quotes, comments and
# braces, keeps the listen/server_name directives of each server block and prefers https over http for a name.
# shellcheck disable=SC2016  # awk program, not shell
readonly NGINX_AWK='
function word(w) { stmt[++ns] = w }
function endstmt() { ns = 0; split("", stmt) }
function listen(   i, a, port, addr) {
  for (i = 3; i <= ns; i++) if (stmt[i] == "quic") return
  a = stmt[2]; addr = ""; port = ""
  if (a ~ /^unix:/) return
  if (a ~ /^[0-9]+$/) port = a
  else if (a ~ /^\[.*\]:[0-9]+$/) { port = a; sub(/^.*\]:/, "", port) }
  else if (a ~ /:[0-9]+$/) { port = a; sub(/^.*:/, "", port); addr = a; sub(/:[0-9]+$/, "", addr) }
  else if (a ~ /^\[.*\]$/) port = "80"
  else { addr = a; port = "80" }
  if (addr !~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) addr = ""
  seen = 1
  if (port == "443") { https = 1; if (haddr == "") haddr = addr }
  else if (port == "80") { http = 1; if (paddr == "") paddr = addr }
}
function directive(   i) {
  if (srv == 0 || depth != srv || ns < 2) return
  if (stmt[1] == "server_name") { for (i = 2; i <= ns; i++) names[++nn] = stmt[i] }
  else if (stmt[1] == "listen") listen()
}
function open_block() {
  stack[++depth] = (ns >= 1) ? stmt[1] : ""
  if (stack[depth] == "server" && srv == 0) {
    srv = depth; nn = 0; split("", names); https = 0; http = 0; seen = 0; haddr = ""; paddr = ""
  }
  endstmt()
}
function valid(n) {
  if (n == "" || n == "_" || n == "localhost") return 0
  if (n ~ /^~/ || index(n, "*") || index(n, "$") || index(n, ":")) return 0
  if (n ~ /^[0-9.]+$/) return 0
  if (n !~ /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/) return 0
  if (n == jdomain || n ~ /^jarvis\./) return 0
  return 1
}
function close_block(   i, n, scheme, addr) {
  if (srv > 0 && depth == srv) {
    if (!seen) http = 1
    if (https) { scheme = "https"; addr = haddr } else if (http) { scheme = "http"; addr = paddr } else scheme = ""
    for (i = 1; i <= nn; i++) {
      n = tolower(names[i]); gsub("^[\"" sq "]|[\"" sq "]$", "", n)
      if (scheme == "" || !valid(n)) { skipped++; continue }
      if (!(n in best) || (scheme == "https" && best[n] !~ /^https/)) best[n] = scheme " " (addr == "" ? "127.0.0.1" : addr)
    }
    srv = 0
  }
  if (depth > 0) depth--
  endstmt()
}
{
  line = $0; len = length(line); tok = ""
  for (i = 1; i <= len; i++) {
    c = substr(line, i, 1)
    if (q != "") {
      tok = tok c
      if (c == "\\") { i++; tok = tok substr(line, i, 1) } else if (c == q) q = ""
      continue
    }
    if (c == "\"" || c == sq) { q = c; tok = tok c; continue }
    if (c == "#" && tok == "") break
    if (c == ";" || c == "{" || c == "}") {
      if (tok != "") { word(tok); tok = "" }
      if (c == ";") { directive(); endstmt() } else if (c == "{") open_block(); else close_block()
      continue
    }
    if (c == " " || c == "\t" || c == "\r") { if (tok != "") { word(tok); tok = "" }; continue }
    tok = tok c
  }
  if (tok != "" && q == "") word(tok)
}
END {
  for (n in best) { split(best[n], p, " "); print p[1], n, p[2] }
  print "#skipped", skipped + 0
}'

# Prints "site <name> <code>" for its target. Runs in xargs children; anonymize() hashes the name before printing.
probe_site() {  # <scheme> <name> <addr>
  local port=443 code
  [[ $1 == http ]] && port=80
  code="$(curl -sk -o /dev/null -m "$CURL_MAX_S" --connect-timeout 4 -w '%{http_code}' \
    --resolve "$2:$port:$3" "$1://$2/" 2>/dev/null)" || true
  [[ $code =~ ^[0-9]{3}$ ]] || code=000
  printf 'site %s %s\n' "$2" "$code"
}

SKIPPED=0
NGINX_STATE=fail
TARGETS=""
PS_LIST=""

# The collectors below run in the main shell or print raw lines that hold real names; only anonymize() output
# is ever printed.
discover_sites() {  # sets NGINX_STATE, SKIPPED and TARGETS ("scheme name addr" lines)
  local dump="" parsed
  if dump="$(nginx -T 2>/dev/null)"; then NGINX_STATE=ok; fi
  parsed="$(printf '%s\n' "$dump" | awk -v jdomain="$JARVIS_DOMAIN" -v sq="'" "$NGINX_AWK")"
  SKIPPED="$(printf '%s\n' "$parsed" | sed -n 's/^#skipped //p')"
  TARGETS="$(printf '%s\n' "$parsed" | grep -v '^#' || true)"
}

read_docker() {  # one 'docker ps' serves the container states and JARVIS's published ports
  PS_LIST="$(docker ps --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Status}}|{{.Ports}}')" \
    || die "docker ps failed"
}

raw_sites() {
  [[ -n $TARGETS ]] || return 0
  export -f probe_site
  export CURL_MAX_S
  printf '%s\n' "$TARGETS" | xargs -r -n 3 -P "$PROBES_IN_PARALLEL" bash -c 'probe_site "$@"' _
}

raw_containers() {  # "ctr <name> <state> <started>/<restarts>" for every running container outside JARVIS
  local name project status st started restarts
  local -A state=() done_=()
  local -a names=()
  while IFS='|' read -r name project status _; do
    [[ -n $name ]] || continue
    [[ $project == "$JARVIS_PROJECT" || $name == jarvis-* ]] && continue
    case "$status" in
      Up*'(healthy)'*) st=healthy ;;
      Up*'(unhealthy)'*) st=unhealthy ;;
      Up*'(health: starting)'*) st=starting ;;
      Up*'(Paused)'*) st=paused ;;
      Up*) st=up ;;
      Restarting*) st=restarting ;;
      *) st=other ;;
    esac
    state[$name]=$st
    names+=("$name")
  done <<< "$PS_LIST"
  (( ${#names[@]} )) || return 0
  while IFS='|' read -r name started restarts; do
    name="${name#/}"
    [[ -n ${state[$name]+x} && -z ${done_[$name]+x} ]] || continue
    done_[$name]=1
    printf 'ctr %s %s %s/%s\n' "$name" "${state[$name]}" "$started" "$restarts"
  done < <(docker inspect -f '{{.Name}}|{{.State.StartedAt}}|{{.RestartCount}}' "${names[@]}" 2>/dev/null || true)
  for name in "${names[@]}"; do   # gone between 'ps' and 'inspect'
    [[ -n ${done_[$name]+x} ]] || printf 'ctr %s %s -\n' "$name" "${state[$name]}"
  done
}

raw_listeners() {  # "listen <addr:port>" for every TCP listener except JARVIS's published ports
  local out spec lo hi p addr
  local -A skip=()
  for p in $JARVIS_PORTS; do skip[$p]=1; done
  # e.g. "127.0.0.1:5100->3000/tcp, 0.0.0.0:7880-7881->7880-7881/tcp" on JARVIS's own containers
  while read -r spec; do
    [[ -n $spec ]] || continue
    lo="${spec%%-*}"; hi="${spec##*-}"
    (( hi - lo <= 4096 )) || continue
    for (( p = lo; p <= hi; p++ )); do skip[$p]=1; done
  done < <(printf '%s\n' "$PS_LIST" | awk -F'|' -v p="$JARVIS_PROJECT" '$2 == p || $1 ~ /^jarvis-/ { print $4 }' \
    | grep -oE ':[0-9]+(-[0-9]+)?->' | tr -d ':>' | sed 's/-$//' || true)
  out="$(ss -Htln)" || die "ss failed"
  while read -r addr; do
    p="${addr##*:}"
    [[ $p =~ ^[0-9]+$ ]] || continue
    [[ -n ${skip[$p]+x} ]] && continue
    printf 'listen %s\n' "$addr"
  done < <(printf '%s\n' "$out" | awk 'NF >= 4 { print $4 }')
}

anonymize() {  # raw lines on stdin -> the same lines with every name (and container start) as a salted hash
  local d kind a b c i=0 j=0 n
  local -a kinds=() vals=() files=() hashes=()
  d="$(mktemp -d "$BASE/.smoke-tmp.XXXXXX")"   # root-only folder; removed right after the one sha256sum run
  while read -r kind a b c; do
    [[ -n $kind ]] || continue
    kinds+=("$kind")
    vals+=("$b")
    printf '%s:%s' "$SALT" "$a" > "$d/$i.a"
    files+=("$d/$i.a")
    if [[ $kind == ctr ]]; then
      printf '%s:%s' "$SALT" "$c" > "$d/$i.c"
      files+=("$d/$i.c")
    fi
    i=$(( i + 1 ))
  done
  if (( ${#files[@]} )); then mapfile -t hashes < <(sha256sum -- "${files[@]}" | cut -c1-10); fi
  rm -rf -- "$d"
  for (( n = 0; n < i; n++ )); do
    case "${kinds[n]}" in
      ctr) printf 'ctr %s %s %s\n' "${hashes[j]}" "${vals[n]}" "${hashes[j + 1]}"; j=$(( j + 2 )) ;;
      listen) printf 'listen %s\n' "${hashes[j]}"; j=$(( j + 1 )) ;;
      *) printf '%s %s %s\n' "${kinds[n]}" "${hashes[j]}" "${vals[n]}"; j=$(( j + 1 )) ;;
    esac
  done
}

summary() {  # <snapshot text> -> one line on stderr
  printf '%s\n' "$1" | awk -v skipped="${SKIPPED:-0}" '
    $1 == "nginx-test" { ng = $2 }
    $1 == "site" { s++; codes[$3]++ }
    $1 == "ctr" { c++; states[$3]++ }
    $1 == "listen" { l++ }
    END {
      line = "smoke: nginx-test " ng "; " s + 0 " sites"
      sep = " ("; for (k in codes) { line = line sep k ":" codes[k]; sep = " " } if (sep == " ") line = line ")"
      line = line ", " c + 0 " containers"
      sep = " ("; for (k in states) { line = line sep k ":" states[k]; sep = " " } if (sep == " ") line = line ")"
      print line ", " l + 0 " listeners, " skipped " names skipped" > "/dev/stderr"
    }'
}

snapshot() {
  local raw snap
  load_salt
  discover_sites
  read_docker
  raw="$(raw_sites; raw_containers; raw_listeners)"
  snap="$({ printf 'nginx-test %s\n' "$NGINX_STATE"; printf '%s\n' "$raw" | anonymize; } | LC_ALL=C sort -u)"
  printf '%s\n' "$snap"
  summary "$snap"
}

compare() {  # <pre> <post>
  local rc=0
  [[ -r $1 && -r $2 ]] || die "cannot read '$1' or '$2'"
  awk '
    function bad_code(x) { return x == "000" || x ~ /^5/ }
    function bad_state(x) { return x == "unhealthy" || x == "restarting" || x == "paused" || x == "other" }
    function key(   k) { k = $1; if ($1 != "nginx-test") k = k " " $2; return k }
    function val() { if ($1 == "nginx-test") return $2; if ($1 == "site") return $3; if ($1 == "ctr") return $3 " " $4; return "1" }
    function out(rank, kind, text) { print rank, kind, text; if (kind == "REGRESSION") r++; else if (kind == "WARN") w++; else i++ }
    NF == 0 { next }
    FNR == NR { pre[key()] = val(); next }
    { post[key()] = val() }
    END {
      for (k in pre) {
        split(k, kk, " "); t = kk[1]; id = kk[2]
        if (!(k in post)) {
          if (t == "site") out(2, "WARN", "site " id ": no longer configured (was " pre[k] ")")
          else if (t == "ctr") { split(pre[k], a, " "); out(2, "WARN", "container " id ": no longer running (was " a[1] ")") }
          else if (t == "listen") out(2, "WARN", "listener " id ": gone")
          else out(2, "WARN", t ": missing")
          continue
        }
        if (pre[k] == post[k]) continue
        if (t == "nginx-test") {
          if (pre[k] == "ok") out(1, "REGRESSION", "nginx -T: ok -> " post[k]); else out(3, "INFO", "nginx -T: " pre[k] " -> " post[k])
        } else if (t == "site") {
          if (!bad_code(pre[k]) && bad_code(post[k])) out(1, "REGRESSION", "site " id ": " pre[k] " -> " post[k])
          else out(2, "WARN", "site " id ": " pre[k] " -> " post[k])
        } else if (t == "ctr") {
          split(pre[k], a, " "); split(post[k], b, " ")
          if (!bad_state(a[1]) && bad_state(b[1])) out(1, "REGRESSION", "container " id ": " a[1] " -> " b[1])
          else if (a[1] != b[1]) out(2, "WARN", "container " id ": " a[1] " -> " b[1])
          else out(2, "WARN", "container " id ": restarted (still " b[1] ")")
        }
      }
      for (k in post) {
        if (k in pre) continue
        split(k, kk, " ")
        if (kk[1] == "site") out(3, "INFO", "site " kk[2] ": new (" post[k] ")")
        else if (kk[1] == "ctr") { split(post[k], b, " "); out(3, "INFO", "container " kk[2] ": new (" b[1] ")") }
        else if (kk[1] == "listen") out(3, "INFO", "listener " kk[2] ": new")
        else out(3, "INFO", kk[1] ": new")
      }
      print 9, "SUMMARY", "regressions=" r + 0 " warnings=" w + 0 " info=" i + 0
      exit (r ? 2 : (w + i ? 1 : 0))
    }' "$1" "$2" | LC_ALL=C sort -k1,1n -k2,2 -k3 | cut -d' ' -f2- || rc=$?
  return "$rc"
}

main() {
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --compare)
      (( $# == 3 )) || { printf '[smoke] --compare needs two files (see --help)\n' >&2; exit 64; }
      local rc=0
      compare "$2" "$3" || rc=$?
      exit "$rc"
      ;;
    "") snapshot ;;
    *) printf '[smoke] unknown argument (see --help)\n' >&2; exit 64 ;;
  esac
}

main "$@"
