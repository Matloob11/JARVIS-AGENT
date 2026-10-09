"""C-12 / OP-08: the deploy tooling (deploy_from_pc.sh, deploy.sh v2, rollback.sh, smoke_other_apps.sh), offline.
C-126 adds agent_ctl's compose calls and its ui-bridge recreate script (the brain switch, K-72 v9) to the same rules.

Every script runs under bash inside a scratch sandbox in tmp_path. JARVIS_DEPLOY_SANDBOX moves the VPS paths
(/opt/jarvis-agent, /root/jarvis-backups, /etc/nginx, /etc/letsencrypt) into it, and docker, nginx, systemctl,
curl, ss, ssh, flock, setsid, certbot, openssl and df are fakes that log their calls. Nothing reaches the VPS or the
real Docker: PATH holds only the fakes and bash's own tool folder, deploy.sh refuses a sandbox run unless docker,
nginx, systemctl, curl, certbot and flock resolve inside the sandbox, and deploy_from_pc.sh gets a *.invalid host.
The other apps in the fake server (sites, containers) must never show up by name in any output.
"""
# pylint: disable=redefined-outer-name,too-many-lines  # the fakes are embedded so the module stands alone
from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOSTINGER = ROOT / "deploy" / "hostinger"
AGENT_CTL = ROOT / "scripts" / "agent_ctl.py"
DOMAIN = "jarvis.srv1802669.hstgr.cloud"
OLD_SHA = "1111111"
NEW_SHA = "abcdef1"
RUN_ID = "20261002T090000Z-abcdef1-0a0b"
OTHER_NAMES = ("site-one.example.test", "site-two.example.test", "plain.example.test", "internal.example.test",
               "siteone", "sitetwo")
B_OLD = "sha256:" + "b1" * 32
F_OLD = "sha256:" + "f1" * 32
L_OLD = "sha256:" + "a1" * 32
B_EMERGENCY = "sha256:" + "b0" * 32


def _find_bash() -> str | None:
    """Git's usr/bin/bash.exe on Windows (bin/bash.exe would put Git's own ssh and curl first on PATH)."""
    if os.name != "nt":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        candidates += [parent / "usr" / "bin" / "bash.exe" for parent in Path(git).resolve().parents]
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe")
    for cand in candidates:
        if cand.is_file() and "system32" not in str(cand).lower():
            return str(cand)
    return None


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash (Git Bash on Windows) is not available")
# Full deploy / rollback runs spawn a few hundred processes each (10-20 s apiece under Git Bash), so they run on
# demand only: JARVIS_DEPLOY_E2E=1 (deploy/hostinger/RUNBOOK.md asks for it after any change to the deploy scripts).
e2e = pytest.mark.skipif(os.environ.get("JARVIS_DEPLOY_E2E") != "1",
                         reason="end-to-end deploy runs: set JARVIS_DEPLOY_E2E=1")


@functools.lru_cache(maxsize=None)
def _posix(path: Path) -> str:
    """A path as bash sees it, in the same form bash gives PATH entries (Git Bash maps %TEMP% to /tmp)."""
    if os.name != "nt":
        return str(path)
    assert BASH is not None
    cygpath = Path(BASH).parent / "cygpath.exe"
    proc = subprocess.run([str(cygpath), "-u", str(Path(path).resolve())], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _write(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if mode is not None:
        os.chmod(path, mode)
    return path


# --------------------------------------------------------------------------- fakes

FAKE_DOCKER_PY = r'''
"""Fake docker CLI for tests/unit/test_deploy_tooling.py; state in $FAKE_STATE/docker_state.json."""
import json
import os
import re
import sys

STATE_DIR = os.environ["FAKE_STATE"]
STATE_FILE = os.path.join(STATE_DIR, "docker_state.json")
VALUE_FLAGS = {"--format", "--policy", "--pull", "--filter", "-f", "-p", "--project-directory", "--env-file",
               "--profile", "-t", "--timeout"}
sys.stdout.reconfigure(newline="\n")   # bash reads it: no CRLF on Windows


def load():
    with open(STATE_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)


def new_id(state, width):
    state["counter"] += 1
    return ("%0" + str(width) + "x") % (0xC0FFEE000 + state["counter"])


def compose_services(text):
    services, cur, inside, block = {}, None, False, ""
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            inside = False
        if not inside:
            continue
        m = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*$", line)
        if m:
            cur, block = m.group(1), ""
            services[cur] = {"image": "", "build": False, "container": "jarvis-" + cur, "profiles": False, "env": [],
                             "deps": []}
            continue
        if cur is None:
            continue
        m = re.match(r"^    ([a-z_]+):", line)
        if m:
            block = m.group(1)
        m = re.match(r"^    image:\s*(\S+)", line)
        if m:
            services[cur]["image"] = m.group(1)
        m = re.match(r"^    container_name:\s*(\S+)", line)
        if m:
            services[cur]["container"] = m.group(1)
        if block == "build":
            services[cur]["build"] = True
        if block == "profiles":
            services[cur]["profiles"] = True
        m = re.match(r"^      (LIVEKIT_KEYS):", line)
        if m:
            services[cur]["env"].append(m.group(1) + "=x")
        m = re.match(r"^      (?:- )?([A-Za-z0-9][A-Za-z0-9_.-]*):?\s*$", line)
        if m and block == "depends_on":
            services[cur]["deps"].append(m.group(1))
    return services


def with_deps(svcs, names):
    """The named services plus everything they depend on, as compose loads them for 'config <svc>'."""
    out, todo = [], list(names)
    while todo:
        s = todo.pop(0)
        if s in svcs and s not in out:
            out.append(s)
            todo.extend(svcs[s]["deps"])
    return out


def image_of(s, svc):
    return svc["image"] or "jarvis-" + s


def config_json(svcs, names):
    """Like 'docker compose config --format json': json.MarshalIndent, two spaces, sorted keys."""
    doc = {"name": "jarvis", "networks": {"jarvis-net": {"driver": "bridge", "name": "jarvis-network"}},
           "services": {}}
    for s in with_deps(svcs, names):
        svc = svcs[s]
        out = {"container_name": svc["container"], "depends_on": {d: {"condition": "service_started"}
                                                                  for d in svc["deps"]}}
        if svc["image"]:
            out["image"] = svc["image"]
        if svc["build"]:
            out["build"] = {"context": "/opt/jarvis-agent", "dockerfile": "Dockerfile"}
        if svc["env"]:
            out["environment"] = {"LIVEKIT_KEYS": "placeholder: placeholder"}
        doc["services"][s] = out
    return json.dumps(doc, indent=2, sort_keys=True)


def split(args):
    flags, values, pos, i = set(), {}, [], 0
    while i < len(args):
        a = args[i]
        if a in VALUE_FLAGS and i + 1 < len(args):
            values.setdefault(a, []).append(args[i + 1])
            i += 2
            continue
        if a.startswith("-"):
            flags.add(a)
        else:
            pos.append(a)
        i += 1
    return flags, values, pos


def status_text(c):
    if c["status"] == "running":
        return {"healthy": "Up 2 hours (healthy)", "unhealthy": "Up 2 hours (unhealthy)",
                "starting": "Up 5 seconds (health: starting)"}.get(c.get("health"), "Up 2 hours")
    if c["status"] == "restarting":
        return "Restarting (1) 5 seconds ago"
    return "Exited (0) 1 minute ago"


def render(fmt, name, c):
    out = fmt.replace("{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", c.get("health", "none"))
    out = out.replace("{{range .Config.Env}}{{println .}}{{end}}", "".join(e + "\n" for e in c.get("env", [])))
    out = re.sub(r"\{\{range \.Mounts\}\}.*?\{\{end\}\}", "", out)
    pairs = {"{{.Name}}": "/" + name, "{{.Names}}": name, "{{.Config.Image}}": c["ref"], "{{.Image}}": c["image"],
             "{{.ID}}": c["id"], "{{.Id}}": c["id"], "{{.State.Status}}": c["status"],
             "{{.State.StartedAt}}": c["started"], "{{.RestartCount}}": str(c["restarts"]),
             '{{.Label "com.docker.compose.project"}}': c.get("project", ""), "{{.Status}}": status_text(c),
             "{{.Ports}}": c.get("ports", ""), "{{.Service}}": c.get("service", "")}
    for key, val in pairs.items():
        out = out.replace(key, val)
    return out


def find(state, ref):
    ref = ref.lstrip("/")
    for name, c in state["containers"].items():
        if ref in (name, c["id"]):
            return name, c
    return None, None


def update_phase(state):
    bad = set(state.get("bad_images", []))
    running_bad = any(c["image"] in bad for c in state["containers"].values()
                      if c.get("project") == "jarvis" and c["status"] == "running")
    marker = os.path.join(STATE_DIR, "phase_bad")
    if running_bad and "regress" in state.get("bad_effects", []):
        open(marker, "w", encoding="utf-8").close()
    elif os.path.exists(marker):
        os.remove(marker)


def compose(state, args):
    i, gvals = 0, {}
    while i < len(args) and args[i].startswith("-"):
        if args[i] in VALUE_FLAGS:
            gvals.setdefault(args[i], []).append(args[i + 1])
            i += 2
        else:
            i += 1
    sub, rest = args[i], args[i + 1:]
    cfile = gvals.get("-f", [""])[-1]
    envf = gvals.get("--env-file", [None])[-1]
    profiles = gvals.get("--profile", [])
    if gvals.get("-p", [""])[-1] != "jarvis":
        sys.stderr.write("fake compose: project must be jarvis\n")
        return 3
    if envf and not os.path.isfile(envf):
        sys.stderr.write("env file %s not found\n" % envf)
        return 1
    if cfile == "-":
        svcs = compose_services(sys.stdin.read())
    elif os.path.isfile(cfile):
        with open(cfile, encoding="utf-8") as fh:
            svcs = compose_services(fh.read())
    else:
        sys.stderr.write("no compose file %s\n" % cfile)
        return 1
    flags, values, pos = split(rest)
    for s in pos:
        if s not in svcs:
            sys.stderr.write("no such service: %s\n" % s)
            return 1
    if sub == "config":
        active = [s for s in svcs if not svcs[s]["profiles"] or "*" in profiles or s in pos]
        if "--services" in flags:
            print("\n".join(active))
        elif "--images" in flags:
            # real compose also prints the depends_on images, in Go map order; here they always come first
            sel = with_deps(svcs, pos or active)
            print("\n".join(image_of(s, svcs[s]) for s in [s for s in sel if s not in pos] + pos))
        elif values.get("--format") == ["json"]:
            print(config_json(svcs, pos or active))
        return 0
    if sub == "ps":
        lines = []
        for name, c in state["containers"].items():
            if c.get("project") != "jarvis" or (pos and c["service"] not in pos):
                continue
            if "-a" not in flags and c["status"] != "running":
                continue
            lines.append(c["id"] if "-q" in flags else render(values.get("--format", ["{{.Names}}"])[-1], name, c))
        if lines:
            print("\n".join(lines))
        return 0
    if not pos:
        sys.stderr.write("fake compose: %s needs named services\n" % sub)
        return 3
    if sub == "pull":
        for s in pos:
            if svcs[s]["build"] and "--ignore-buildable" in flags:
                continue
            ref = svcs[s]["image"]
            if ref not in state["tags"]:
                img = "sha256:" + new_id(state, 64)
                state["tags"][ref] = img
                state["images"].append(img)
                if ref in state.get("bad_pull_refs", []):
                    state.setdefault("bad_images", []).append(img)
        save(state)
        return 0
    if sub == "build":
        for s in pos:
            if not svcs[s]["build"]:
                continue
            if s in state.get("fail_build", []):
                sys.stderr.write("build failed: %s\n" % s)
                save(state)
                return 1
            img = "sha256:" + new_id(state, 64)
            state["images"].append(img)
            state["tags"][image_of(s, svcs[s])] = img
            if s in state.get("bad_build_services", []):
                state.setdefault("bad_images", []).append(img)
        save(state)
        return 0
    if sub == "up":
        for s in pos:
            ref = image_of(s, svcs[s])
            img = state["tags"].get(ref)
            if not img:
                sys.stderr.write("no such image %s\n" % ref)
                return 1
            cname, env = svcs[s]["container"], svcs[s]["env"]
            old = state["containers"].get(cname)
            if old and old["image"] == img and old["status"] == "running" and old.get("env") == env \
                    and old["ref"] == ref:
                continue
            health = "none" if s in state.get("no_healthcheck", []) else "healthy"
            if img in state.get("bad_images", []) and "unhealthy" in state.get("bad_effects", []):
                health = "unhealthy"
            state["containers"][cname] = {
                "id": new_id(state, 12), "service": s, "project": "jarvis", "ref": ref, "image": img,
                "status": "running", "health": health, "started": "2026-10-02T09:%02d:00Z" % (state["counter"] % 60),
                "restarts": 0, "env": env, "ports": old["ports"] if old else ""}
        update_phase(state)
        save(state)
        return 0
    if sub == "stop":
        for c in state["containers"].values():
            if c.get("project") == "jarvis" and c["service"] in pos:
                c["status"] = "exited"
        update_phase(state)
        save(state)
        return 0
    sys.stderr.write("fake compose: unsupported %s\n" % sub)
    return 3


def main():
    argv = sys.argv[1:]
    with open(os.path.join(STATE_DIR, "docker_calls.log"), "a", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + "\n")
    state = load()
    if not argv:
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "compose":
        return compose(state, rest)
    if cmd == "inspect":
        _, values, pos = split(rest)
        fmt, rc = values.get("-f", ["{{.Name}}"])[-1], 0
        for ref in pos:
            name, c = find(state, ref)
            if c is None:
                sys.stderr.write("Error: No such object: %s\n" % ref)
                rc = 1
                continue
            sys.stdout.write(render(fmt, name, c) + "\n")
        return rc
    if cmd == "image" and rest[:1] == ["inspect"]:
        _, values, pos = split(rest[1:])
        rc = 0
        for ref in pos:
            img = state["tags"].get(ref) or (ref if ref in state["images"] else None)
            if img is None:
                sys.stderr.write("Error: No such image: %s\n" % ref)
                rc = 1
                continue
            print(img if values.get("-f") else '[{"Id": "%s"}]' % img)
        return rc
    if cmd == "tag":
        img = state["tags"].get(rest[0]) or (rest[0] if rest[0] in state["images"] else None)
        if img is None:
            sys.stderr.write("Error: No such image: %s\n" % rest[0])
            return 1
        state["tags"][rest[1]] = img
        save(state)
        return 0
    if cmd == "ps":
        flags, values, _ = split(rest)
        want = None
        for flt in values.get("--filter", []):
            if flt.startswith("label=com.docker.compose.project="):
                want = flt.split("=", 2)[2]
        lines = []
        for name, c in state["containers"].items():
            if (want is not None and c.get("project") != want) or ("-a" not in flags and c["status"] != "running"):
                continue
            lines.append(c["id"] if "-q" in flags else render(values.get("--format", ["{{.Names}}"])[-1], name, c))
        if lines:
            print("\n".join(lines))
        return 0
    if cmd == "info":
        return 0
    sys.stderr.write("fake docker: unsupported %s\n" % cmd)
    return 3


sys.exit(main())
'''

FAKE_SH = {
    "nginx": r'''#!/usr/bin/env bash
printf 'nginx %s\n' "$*" >> "$FAKE_STATE_SH/nginx_calls.log"
case "${1:-}" in
  -t)
    # nginx -t checks every site: a warning about another app names its certificate
    echo 'nginx: [warn] "ssl_stapling" ignored, no OCSP responder URL in the certificate "/etc/letsencrypt/live/site-one.example.test/fullchain.pem"' >&2
    if grep -q BROKEN "$JARVIS_DEPLOY_SANDBOX/etc/nginx/conf.d/jarvis.conf" 2>/dev/null; then
      echo 'nginx: [emerg] unexpected end of file, expecting "}" in /etc/nginx/conf.d/jarvis.conf:3' >&2
      echo "nginx: configuration file /etc/nginx/nginx.conf test failed" >&2; exit 1
    fi
    echo "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok" >&2
    echo "nginx: configuration file /etc/nginx/nginx.conf test is successful" >&2 ;;
  -T)
    [[ -f $FAKE_STATE_SH/nginx_T_fail ]] && exit 1
    cat "$FAKE_STATE_SH/nginx_T.txt" ;;
esac
exit 0
''',
    "systemctl": '#!/usr/bin/env bash\nprintf \'systemctl %s\\n\' "$*" >> "$FAKE_STATE_SH/systemctl_calls.log"\n',
    "certbot": '#!/usr/bin/env bash\nprintf \'certbot %s\\n\' "$*" >> "$FAKE_STATE_SH/certbot_calls.log"\n',
    "ss": '#!/usr/bin/env bash\ncat "$FAKE_STATE_SH/ss.txt"\n',
    "flock": '#!/usr/bin/env bash\n[[ -f $FAKE_STATE_SH/flock_busy ]] && exit 1\nexit 0\n',
    "setsid": '#!/usr/bin/env bash\nexec "$@"\n',
    "openssl": '#!/usr/bin/env bash\necho "notAfter=$(date -u -d "+60 days" "+%b %d %H:%M:%S %Y GMT")"\n',
    "df": r'''#!/usr/bin/env bash
avail=104857600
[[ -f $FAKE_STATE_SH/df_low ]] && avail=1048576
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfake 209715200 1 %s 50%% /\n' "$avail"
''',
    "curl": r'''#!/usr/bin/env bash
url="" w=0
while (( $# )); do
  case "$1" in
    -w) w=1; shift 2; continue ;;
    -o|-m|--connect-timeout|--resolve) shift 2; continue ;;
    http://*|https://*) url="$1" ;;
  esac
  shift
done
printf 'curl %s\n' "$url" >> "$FAKE_STATE_SH/curl_calls.log"
codes="$FAKE_STATE_SH/curl_codes.txt"
[[ -f $FAKE_STATE_SH/phase_bad ]] && codes="$FAKE_STATE_SH/curl_codes_bad.txt"
code=000
while read -r pat c; do
  if [[ -n $pat && $url == *"$pat"* ]]; then code=$c; break; fi
done < "$codes"
if (( w )); then printf '%s' "$code"; else printf '{"status":"ok"}'; fi
[[ $code != 000 ]]
''',
    "ssh": r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_STATE_SH/ssh_calls.log"
[[ -f $FAKE_STATE_SH/ssh_down ]] && exit 255
cmd="${!#}"
if [[ $cmd == *UPLOAD_OK* ]]; then
  cat > "$FAKE_STATE_SH/uploaded.tgz"
  run="$(printf '%s' "$cmd" | sed -n 's/^.*R=\([^;]*\);.*$/\1/p')"
  want="$(printf '%s' "$cmd" | sed -n 's/^.*echo "\([0-9a-f]\{64\}\)  .*$/\1/p')"
  got="$(sha256sum "$FAKE_STATE_SH/uploaded.tgz" | cut -d' ' -f1)"
  [[ $want == "$got" ]] || { echo "checksum mismatch" >&2; exit 1; }
  echo "UPLOAD_OK $run"
  exit 0
fi
if [[ $cmd == *deploy.sh* ]]; then
  echo "[deploy] == preflight"
  echo "[deploy] BACKUP=2026-10-02_0901Z-pre-deploy-fake"
  echo "[deploy] RESULT=ok"
  exit "$(cat "$FAKE_STATE_SH/ssh_deploy_rc" 2>/dev/null || echo 0)"
fi
echo "unexpected ssh command" >&2
exit 97
''',
}

FAKE_BACKUP_VPS = r'''#!/usr/bin/env bash
# test fake of backup_vps.sh with the same contract: a folder with opt-jarvis-agent.tgz, jarvis.conf, env copies,
# DEPLOYED_SHA and containers.txt, and BACKUP_DIR=<folder> as the last line
set -euo pipefail
S="$JARVIS_DEPLOY_SANDBOX"
base="$S/root/jarvis-backups"
n=$(( $(cat "$base/.fake_backup_n" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$base/.fake_backup_n"
name="$(printf '2026-10-02_%04dZ' "$n")${1:+-$1}"
b="$base/$name"
mkdir -p "$b"
tar -czf "$b/opt-jarvis-agent.tgz" -C "$S/opt" jarvis-agent
cp "$S/etc/nginx/conf.d/jarvis.conf" "$b/jarvis.conf"
cp "$S/opt/jarvis-agent/deploy/hostinger/.env" "$b/hostinger.env"
if [[ -f $S/opt/jarvis-agent/DEPLOYED_SHA ]]; then cp "$S/opt/jarvis-agent/DEPLOYED_SHA" "$b/DEPLOYED_SHA"; fi
: > "$b/containers.txt"
for id in $(docker ps -a --filter label=com.docker.compose.project=jarvis -q); do
  docker inspect -f '{{.Name}} image={{.Config.Image}} image_id={{.Image}} state={{.State.Status}}' "$id" >> "$b/containers.txt"
done
echo "[fake backup_vps] $name"
echo "BACKUP_DIR=$b"
'''

OLD_DEPLOY_V1 = "#!/usr/bin/env bash\n# old v1 deploy.sh: compose up --build for everything\necho V1_DEPLOY_RAN\n"

COMPOSE_OLD = """name: jarvis
services:
  livekit:
    image: livekit/livekit-server:latest
    container_name: jarvis-livekit
  ui-bridge:
    build:
      context: ../..
    image: jarvis-bridge:hostinger
    container_name: jarvis-bridge
    depends_on:
      - livekit
  frontend:
    build:
      context: ../..
    image: jarvis-frontend:hostinger
    container_name: jarvis-frontend
    depends_on:
      ui-bridge:
        condition: service_healthy
"""
COMPOSE_NEW = COMPOSE_OLD.replace("livekit-server:latest", "livekit-server:v1.13.7").replace(
    "    container_name: jarvis-livekit\n",
    "    container_name: jarvis-livekit\n    environment:\n      LIVEKIT_KEYS: \"${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}\"\n")
COMPOSE_WITH_AGENT = COMPOSE_NEW + """  agent:
    build:
      context: ../..
    image: jarvis-agent:hostinger
    container_name: jarvis-agent
    profiles: ["vps-brain"]
    depends_on:
      - livekit
"""

NGINX_T = """# configuration file /etc/nginx/nginx.conf:
user www-data;
events { worker_connections 768; }
http {
    include /etc/nginx/conf.d/*.conf;
    upstream siteone_backend { server 127.0.0.1:3000; keepalive 4; }
}

# configuration file /etc/nginx/conf.d/siteone.conf:
server {
    listen 80;
    server_name site-one.example.test;
    return 301 https://$host$request_uri;   # { braces in a comment }
}
server {
    listen 443 ssl http2;
    listen [::]:443 ssl;
    server_name site-one.example.test;
    location = /health { return 200 '{"ok": true}'; }
    location / { proxy_pass http://siteone_backend; }
}

# configuration file /etc/nginx/conf.d/sitetwo.conf:
server {
    listen 443 ssl;
    server_name www.site-two.example.test "site-two.example.test";
    location / { proxy_pass http://127.0.0.1:3001; }
}
server {
    listen 127.0.0.1:8080;
    server_name internal.example.test;
}
server { listen 80; server_name plain.example.test; root /var/www/plain; }

# configuration file /etc/nginx/conf.d/default.conf:
server {
    listen 80 default_server;
    server_name _ localhost 203.0.113.7 ~^regex\\.example\\.test$ *.wild.example.test;
}

# configuration file /etc/nginx/conf.d/jarvis.conf:
server {
    listen 443 ssl http2;
    server_name jarvis.srv1802669.hstgr.cloud;
    location / { proxy_pass http://127.0.0.1:5100; }
}
"""

SS_OUT = """LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*
LISTEN 0 511 0.0.0.0:80 0.0.0.0:*
LISTEN 0 511 0.0.0.0:443 0.0.0.0:*
LISTEN 0 511 [::]:443 [::]:*
LISTEN 0 200 127.0.0.1:5432 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:3000 0.0.0.0:*
LISTEN 0 4096 0.0.0.0:5101 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:5100 0.0.0.0:*
LISTEN 0 4096 0.0.0.0:7880 0.0.0.0:*
LISTEN 0 4096 0.0.0.0:7881 0.0.0.0:*
"""

CURL_CODES = """site-one.example.test 200
site-two.example.test 301
plain.example.test 200
127.0.0.1:5101/health 200
127.0.0.1:5100/ 200
127.0.0.1:7880/ 200
/vortex/health 200
/rtc/validate 401
jarvis.srv1802669.hstgr.cloud/ 401
jarvis.test.invalid/ 401
"""


def _container(ident: tuple[str, str, str], ref: str, image: str, health: str, ports: str) -> dict:
    cid, service, project = ident
    return {"id": cid, "service": service, "project": project, "ref": ref, "image": image, "status": "running",
            "health": health, "started": "2026-09-30T10:00:00Z", "restarts": 0, "env": [], "ports": ports}


def _docker_state() -> dict:
    other = "sha256:" + "e1" * 32
    return {
        "counter": 0,
        "tags": {"jarvis-bridge:hostinger": B_OLD, "jarvis-frontend:hostinger": F_OLD,
                 "livekit/livekit-server:latest": L_OLD,   # v1.13.7 is not pulled yet (as on the VPS)
                 "jarvis-bridge:pre-emergency-2026-09-30_2340": B_EMERGENCY},
        "images": [B_OLD, F_OLD, L_OLD, B_EMERGENCY, other],
        "containers": {
            "jarvis-bridge": _container(("0000000b0001", "ui-bridge", "jarvis"), "jarvis-bridge:hostinger", B_OLD,
                                        "healthy", "0.0.0.0:5101->5001/tcp"),
            "jarvis-frontend": _container(("0000000f0001", "frontend", "jarvis"), "jarvis-frontend:hostinger", F_OLD,
                                          "healthy", "127.0.0.1:5100->3000/tcp"),
            "jarvis-livekit": _container(("0000000a0001", "livekit", "jarvis"), "livekit/livekit-server:latest",
                                         L_OLD, "none",
                                         "0.0.0.0:7880-7881->7880-7881/tcp, 0.0.0.0:50000-50050->50000-50050/udp"),
            "siteone-web": _container(("00000e1e0001", "web", "siteone"), "siteone/web:1", other, "healthy",
                                      "127.0.0.1:3000->3000/tcp"),
            "sitetwo-db": _container(("00000e2e0001", "db", "sitetwo"), "postgres:16", other, "none", ""),
        },
        "no_healthcheck": ["livekit"],
    }


class Sandbox:  # pylint: disable=too-many-instance-attributes
    """A fake VPS (plus a fake PC side) under tmp_path."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "sb"
        self.fakebin = self.root / "fakebin"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.tmp = self.root / "tmp"
        self.app = self.root / "opt" / "jarvis-agent"
        self.hostinger = self.app / "deploy" / "hostinger"
        self.base = self.root / "root" / "jarvis-backups"
        self.nginx_conf = self.root / "etc" / "nginx" / "conf.d" / "jarvis.conf"
        self.run_dir = self.base / "runs" / RUN_ID
        for d in (self.fakebin, self.state, self.home, self.tmp, self.base, self.root / "var" / "www" / "html"):
            d.mkdir(parents=True, exist_ok=True)
        self.posix_root = _posix(self.root)
        self._fakes()
        self._installed_code()
        _write(self.nginx_conf, "# jarvis.conf v1 (live)\nserver { listen 443 ssl; }\n")
        le_live = self.root / "etc" / "letsencrypt" / "live" / DOMAIN
        _write(le_live / "fullchain.pem", "dummy certificate\n")
        self.save_state(_docker_state())
        _write(self.state / "nginx_T.txt", NGINX_T)
        _write(self.state / "ss.txt", SS_OUT)
        _write(self.state / "curl_codes.txt", CURL_CODES)
        _write(self.state / "curl_codes_bad.txt", CURL_CODES.replace("site-one.example.test 200",
                                                                      "site-one.example.test 502"))
        self.stage()

    # ------------------------------------------------------------ setup
    def sh(self, path: Path) -> str:
        """A sandbox path as bash sees it (one cygpath call per sandbox, not per path)."""
        return self.posix_root + "/" + Path(path).relative_to(self.root).as_posix()

    def _fakes(self) -> None:
        _write(self.fakebin / "fake_docker.py", FAKE_DOCKER_PY)
        python, fake = _posix(Path(sys.executable)), self.sh(self.fakebin / "fake_docker.py")
        _write(self.fakebin / "docker", f'#!/usr/bin/env bash\nexec "{python}" -I -S "{fake}" "$@"\n', 0o755)
        for name, body in FAKE_SH.items():
            _write(self.fakebin / name, body, 0o755)

    def _installed_code(self) -> None:
        _write(self.hostinger / "docker-compose.yml", COMPOSE_OLD)
        _write(self.hostinger / ".env", "LIVEKIT_API_KEY=placeholder\nLIVEKIT_API_SECRET=placeholder\nSENTINEL=before\n")
        _write(self.hostinger / "livekit.yaml", "port: 7880\nkeys:\n  placeholder: placeholder\n")
        _write(self.hostinger / "jarvis.conf", "# jarvis.conf v1 (code dir)\n")
        _write(self.hostinger / "deploy.sh", OLD_DEPLOY_V1, 0o755)
        _write(self.app / "src" / "core" / "ui_bridge.py", "VERSION = 'old'\n")
        _write(self.app / "stonix_ui" / "package.json", '{"name": "old-ui"}\n')
        _write(self.app / "DEPLOYED_SHA", OLD_SHA + "\n")

    def new_tree(self, compose: str = COMPOSE_NEW, jarvis_conf: str = "# jarvis.conf v2\nserver { listen 443 ssl; }\n"
                 ) -> dict[str, bytes]:
        tree = {
            "deploy/hostinger/docker-compose.yml": compose,
            "deploy/hostinger/livekit.yaml": "port: 7880\n# API keys come from LIVEKIT_KEYS\n",
            "deploy/hostinger/jarvis.conf": jarvis_conf,
            "deploy/hostinger/backup_vps.sh": FAKE_BACKUP_VPS,
            "src/core/ui_bridge.py": "VERSION = 'new'\n",
            "src/core/new_module.py": "NEW = True\n",
            "stonix_ui/package.json": '{"name": "new-ui"}\n',
            ".dockerignore": ".env\n",
        }
        for script in ("deploy.sh", "rollback.sh", "smoke_other_apps.sh"):
            tree["deploy/hostinger/" + script] = (HOSTINGER / script).read_text(encoding="utf-8")
        return {k: v.encode("utf-8") for k, v in tree.items()}

    def stage(self, **kwargs) -> None:
        """The run folder deploy_from_pc.sh leaves on the VPS: shipped.tar.gz plus the extracted tree."""
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        tree = self.new_tree(**kwargs)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in sorted(tree.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                tar.addfile(info, io.BytesIO(data))
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "shipped.tar.gz").write_bytes(buf.getvalue())
        for name, data in tree.items():
            dest = self.run_dir / "tree" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

    # ------------------------------------------------------------ state
    def load_state(self) -> dict:
        return json.loads((self.state / "docker_state.json").read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        (self.state / "docker_state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")

    def tweak(self, **values) -> None:
        state = self.load_state()
        state.update(values)
        self.save_state(state)

    def calls(self, tool: str = "docker") -> list[str]:
        path = self.state / f"{tool}_calls.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def mutating_docker_calls(self) -> list[str]:
        mutating = {"build", "up", "stop", "pull", "down", "rm", "restart", "kill", "tag", "rmi", "run", "exec"}
        found = []
        for call in self.calls():
            words = call.split()
            if words[:1] == ["compose"]:
                i = 1
                while i < len(words) and words[i].startswith("-"):
                    i += 2 if words[i] in ("-p", "-f", "--project-directory", "--env-file", "--profile") else 1
                sub = words[i] if i < len(words) else ""
            else:
                sub = words[0] if words else ""
            if sub in mutating:
                found.append(call)
        return found

    def backups(self) -> list[Path]:
        return sorted(p for p in self.base.iterdir() if p.is_dir() and p.name.startswith("2026-"))

    # ------------------------------------------------------------ running
    def env(self, extra_path: tuple[str, ...] = (), **extra: str) -> dict[str, str]:
        keep = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "COMSPEC", "PATHEXT")
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        assert BASH is not None
        env["PATH"] = os.pathsep.join([str(self.fakebin), str(Path(BASH).parent), *extra_path])
        env.update(HOME=str(self.home), LC_ALL="C", JARVIS_DEPLOY_SANDBOX=self.posix_root,
                   FAKE_STATE=str(self.state), FAKE_STATE_SH=self.sh(self.state), TMPDIR=self.sh(self.tmp))
        env.update(extra)
        return env

    def run(self, script: Path, *args: str, env: dict[str, str] | None = None, timeout: int = 180
            ) -> subprocess.CompletedProcess:
        assert BASH is not None
        script_sh = self.sh(script) if Path(script).is_relative_to(self.root) else _posix(script)
        return subprocess.run([BASH, script_sh, *args], env=env or self.env(), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)

    def deploy(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(self.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh", "--run-dir",
                        self.sh(self.run_dir), "--sha", NEW_SHA, *args)

    def fake_deployed_state(self) -> str:
        """What a deploy of ui-bridge at NEW_SHA leaves behind (backup, records, LAST_DEPLOY, tools), without one."""
        staged = self.run_dir / "tree" / "deploy" / "hostinger"
        proc = self.run(staged / "backup_vps.sh", "pre-deploy-" + NEW_SHA)
        assert proc.returncode == 0, _out(proc)
        name = proc.stdout.strip().splitlines()[-1].rsplit("/", 1)[1]
        backup = self.base / name
        _write(backup / "deploy.env", f"KIND=deploy\nSHA={NEW_SHA}\nPREV_SHA={OLD_SHA}\nSERVICES=ui-bridge\nNGINX=0\n"
               "CODE=1\nRESULT=ok\n")
        _write(backup / "deploy-images.txt", "# service\tcontainer\tref\timage_id\n"
               f"ui-bridge\tjarvis-bridge\tjarvis-bridge:hostinger\t{B_OLD}\n")
        _write(self.base / "LAST_DEPLOY", name + "\n")
        for tool in ("deploy.sh", "rollback.sh", "smoke_other_apps.sh", "backup_vps.sh"):
            _write(self.base / "tools" / tool, (staged / tool).read_text(encoding="utf-8"), 0o700)
        return name


def _out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def _assert_no_other_names(text: str) -> None:
    for name in OTHER_NAMES:
        assert name not in text, "an other app's name leaked into the output: " + name


def _digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _assert_tags(sb: Sandbox, before: dict[str, str], changed: dict[str, str]) -> None:
    """Every image tag points where it did before, except `changed` and the :rollback-<backup> tags of a run."""
    after = sb.load_state()["tags"]
    for ref in sorted(set(before) | set(after)):
        if ":rollback-" not in ref:
            want = changed.get(ref, before.get(ref))
            assert after.get(ref) == want, f"{ref} -> {after.get(ref)} (want {want})"


@pytest.fixture
def sb(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


# --------------------------------------------------------------------------- static checks

def test_scripts_parse_and_use_lf_line_endings() -> None:
    for name in ("deploy.sh", "rollback.sh", "smoke_other_apps.sh", "deploy_from_pc.sh", "backup_vps.sh"):
        path = HOSTINGER / name
        assert b"\r" not in path.read_bytes(), name + " has CR line endings"
        proc = subprocess.run([BASH, "-n", _posix(path)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, name + ": " + proc.stderr


@functools.lru_cache(maxsize=None)
def _agent_ctl() -> types.ModuleType:
    """scripts/agent_ctl.py, loaded once (its top level only defines things; tests/test_agent_ctl.py checks that)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("agent_ctl_deploy_tooling", AGENT_CTL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_commands_always_name_services() -> None:
    """Every 'compose up' names its services with --no-deps; no prune, no 'down', no nginx/docker restart. agent_ctl's
    brain switch too (K-75): each _vps_compose call stops, or starts with VPS_COMPOSE_UP, and its ui-bridge recreate
    (K-72 v9) is that same 'up' for the bridge alone. Without --no-deps, the depends_on of agent and agent-svc would
    let compose recreate the bridge (with GIT_SHA 'unknown') whenever its config differs."""
    ups = 0
    for name in ("deploy.sh", "rollback.sh"):
        for line in (HOSTINGER / name).read_text(encoding="utf-8").splitlines():
            code = line.split(" #", 1)[0].strip()
            if code.startswith("compose up"):
                ups += 1
                assert code.startswith("compose up -d --no-deps --no-build --pull never \"${"), name + ": " + code
            assert not re.search(r"\b(system|image|volume|builder|container|network) prune\b", code), name + ": " + code
            assert "compose down" not in code and " rmi " not in code, name + ": " + code
            assert "restart nginx" not in code and "restart docker" not in code, name + ": " + code
    assert ups == 3   # deploy, automatic rollback, rollback.sh

    ctl = _agent_ctl()
    assert ctl.VPS_COMPOSE_UP == ("up", "-d", "--no-deps", "--no-build", "--pull", "never")
    verbs = []
    for node in ast.walk(ast.parse(AGENT_CTL.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_vps_compose":
            first = node.args[0]
            if isinstance(first, ast.Starred):
                assert ast.unparse(first.value) == "VPS_COMPOSE_UP", ast.unparse(node)
                verbs.append("up")
            else:
                assert isinstance(first, ast.Constant) and first.value == "stop", ast.unparse(node)
                verbs.append("stop")
    assert verbs.count("up") == 2 and verbs.count("stop") == 3   # up: START_NEW and the restore (to vps)
    assert ctl._vps_compose(*ctl.VPS_COMPOSE_UP, "agent", "agent-svc") == (  # pylint: disable=protected-access
        "cd /opt/jarvis-agent/deploy/hostinger && docker compose -p jarvis --profile vps-brain up -d --no-deps "
        "--no-build --pull never agent agent-svc")
    recreate = ctl.vps_bridge_recreate_script()
    compose_lines = [line for line in recreate.splitlines() if "docker compose" in line]
    assert len(compose_lines) == 1 and compose_lines[0].endswith(
        "docker compose -p jarvis up -d --no-deps --no-build --pull never --force-recreate ui-bridge || exit 1")
    assert not re.search(r"\bprune\b|compose down|\brmi\b|restart (nginx|docker)", recreate)


# --------------------------------------------------------------------------- agent_ctl's ui-bridge recreate

FAKE_BRIDGE_DOCKER = r'''#!/usr/bin/env bash
# docker for agent_ctl's ui-bridge recreate: every call is logged next to this file; an env 'inspect' prints the
# running container's environment (GIT_SHA=$FAKE_RUNNING_SHA when set), a health 'inspect' answers 'starting' until
# its FAKE_HEALTHY_AT-th call, then 'healthy'
here=${0%/*}
case "$1" in
  compose)
    printf 'compose GIT_SHA=[%s] PWD=%s ARGS=%s\n' "${GIT_SHA-unset}" "$PWD" "$*" >> "$here/calls.log"
    exit "${FAKE_COMPOSE_EXIT:-0}" ;;
  inspect)
    printf 'inspect ARGS=%s\n' "$*" >> "$here/calls.log"
    case "$*" in
      *Config.Env*)
        echo "PATH=/usr/local/bin:/usr/bin"
        if [ -n "${FAKE_RUNNING_SHA:-}" ]; then echo "GIT_SHA=$FAKE_RUNNING_SHA"; fi
        exit 0 ;;
    esac
    n=0
    if [ -f "$here/inspects" ]; then read -r n < "$here/inspects"; fi
    n=$((n + 1)); echo "$n" > "$here/inspects"
    if [ "$n" -ge "${FAKE_HEALTHY_AT:-1}" ]; then echo healthy; else echo starting; fi ;;
  *) printf 'unexpected: docker %s\n' "$*" >&2; exit 99 ;;
esac
'''
BRIDGE_INSPECT = "inspect ARGS=inspect -f {{if .State.Health}}{{.State.Health.Status}}{{end}} jarvis-bridge"
BRIDGE_ENV_INSPECT = "inspect ARGS=inspect -f {{range .Config.Env}}{{println .}}{{end}} jarvis-bridge"
RUNNING_SHA = "1234abc"


@pytest.mark.parametrize(("healthy_at", "compose_exit", "deployed", "running", "code", "inspects"), [
    (3, 0, True, "", 0, 3),             # healthy on the third look
    (99, 0, True, "", 1, 4),            # never healthy: exit 1 after VPS_BRIDGE_TRIES looks
    (1, 1, True, "", 1, 0),             # compose failed: no wait
    (1, 0, False, "", 0, 1),            # no DEPLOYED_SHA: an empty GIT_SHA, which compose turns into 'unknown'
    (1, 0, True, RUNNING_SHA, 0, 1),    # the running bridge's own GIT_SHA wins over a newer DEPLOYED_SHA
    (1, 0, True, "unknown", 0, 1),      # a running 'unknown' says nothing: DEPLOYED_SHA
])
def test_agent_ctl_recreates_only_the_bridge_with_its_own_sha_then_waits_until_it_is_healthy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, healthy_at: int, compose_exit: int, deployed: bool,
        running: str, code: int, inspects: int) -> None:
    """The bridge reads JARVIS_BRAIN only when it starts (K-72 v9), so the brain switch recreates it: alone, from its
    image, with the GIT_SHA that image runs (the running container's, else DEPLOYED_SHA; never the calling shell's:
    a '--services frontend' or 'none' deploy writes DEPLOYED_SHA without a new bridge image), and only a healthy
    bridge counts."""
    ctl = _agent_ctl()
    monkeypatch.setattr(ctl, "VPS_BRIDGE_TRIES", 4)
    monkeypatch.setattr(ctl, "VPS_BRIDGE_POLL_S", 0)
    app = tmp_path / "opt" / "jarvis-agent"
    compose_dir = app / "deploy" / "hostinger"
    compose_dir.mkdir(parents=True)
    if deployed:
        _write(app / "DEPLOYED_SHA", NEW_SHA + "\nnot-the-first-line\n")   # only its first line counts
    script = ctl.vps_bridge_recreate_script()
    assert script.count(ctl.VPS_APP_DIR) == 2   # the compose folder and DEPLOYED_SHA, both moved into tmp_path
    boxed = _write(tmp_path / "recreate.sh", script.replace(ctl.VPS_APP_DIR, _posix(app)))
    fakebin = tmp_path / "fakebin"
    _write(fakebin / "docker", FAKE_BRIDGE_DOCKER, 0o755)
    keep = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "COMSPEC", "PATHEXT")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    assert BASH is not None
    env.update(PATH=os.pathsep.join([str(fakebin), str(Path(BASH).parent)]), LC_ALL="C", GIT_SHA="from-the-shell",
               FAKE_HEALTHY_AT=str(healthy_at), FAKE_COMPOSE_EXIT=str(compose_exit), FAKE_RUNNING_SHA=running)
    proc = subprocess.run([BASH, _posix(boxed)], env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, check=False)
    assert proc.returncode == code, _out(proc)
    sha = running if running not in ("", "unknown") else NEW_SHA if deployed else ""
    assert (fakebin / "calls.log").read_text(encoding="utf-8").splitlines() == [
        BRIDGE_ENV_INSPECT,
        f"compose GIT_SHA=[{sha}] PWD={_posix(compose_dir)} ARGS=compose -p jarvis up -d --no-deps --no-build "
        "--pull never --force-recreate ui-bridge"] + [BRIDGE_INSPECT] * inspects
    assert ("jarvis-bridge is not healthy" in proc.stderr) == (code == 1 and compose_exit == 0)


# --------------------------------------------------------------------------- rollback.sh --env after C-126

@pytest.mark.parametrize(("before", "line", "name", "after"), [
    ("A=1\n", "JARVIS_BRAIN=pc", "JARVIS_BRAIN", "A=1\nJARVIS_BRAIN=pc\n"),            # a backup from before C-126
    ("A=1\nJARVIS_BRAIN=\n", "JARVIS_BRAIN=pc", "JARVIS_BRAIN", "A=1\nJARVIS_BRAIN=pc\n"),  # an empty one is no brain
    ("JARVIS_BRAIN=vps\n", "JARVIS_BRAIN=pc", "JARVIS_BRAIN", "JARVIS_BRAIN=vps\n"),     # the restored file's own wins
    ("A=1\n", "", "JARVIS_BRAIN", "A=1\n"),                                             # nothing live: nothing added
    ("A=1\n", "COMPOSE_PROFILES=vps-brain", "COMPOSE_PROFILES", "A=1\nCOMPOSE_PROFILES=vps-brain\n"),
    ("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=vps-brain", "COMPOSE_PROFILES", "COMPOSE_PROFILES=\n"),  # a setting
])
def test_rollback_env_restore_keeps_the_live_brain(tmp_path: Path, before: str, line: str, name: str,
                                                   after: str) -> None:
    """C-126: the compose file needs a non-empty JARVIS_BRAIN in .env. rollback.sh --env to a backup from before C-126
    keeps the live JARVIS_BRAIN (and COMPOSE_PROFILES) line, so the rollback's own compose commands still run."""
    assert BASH is not None
    source = (HOSTINGER / "rollback.sh").read_text(encoding="utf-8")
    func = re.search(r"^keep_env_line\(\) \{.*?^\}\n", source, re.M | re.S)
    assert func is not None and 'keep_env_line "${dst[0]}" "$brain" JARVIS_BRAIN' in source
    env_file = tmp_path / ".env"
    env_file.write_bytes(before.encode("utf-8"))
    script = func.group(0) + 'log() { printf "%s\\n" "$*"; }\nkeep_env_line "$1" "$2" "$3"\n'
    proc = subprocess.run([BASH, "-c", script, "keep", _posix(env_file), line, name], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, check=False)
    assert proc.returncode == 0, _out(proc)
    assert env_file.read_bytes().decode("utf-8") == after


def test_gate_only_fails_when_the_compose_file_does_not_interpolate() -> None:
    """C-126: without a non-empty JARVIS_BRAIN, 'compose ps' sees no container and the gate checked nothing."""
    source = (HOSTINGER / "deploy.sh").read_text(encoding="utf-8")
    body = source[source.index("gate_only_main() {"):]
    body = body[:body.index("\n}\n")]
    assert body.index("compose config -q") < body.index("run_gates") and 'fail "docker compose config fails' in body


# --------------------------------------------------------------------------- smoke_other_apps.sh

def test_smoke_snapshot_shows_only_hashes(sb: Sandbox) -> None:
    proc = sb.run(HOSTINGER / "smoke_other_apps.sh")
    assert proc.returncode == 0, _out(proc)
    again = sb.run(HOSTINGER / "smoke_other_apps.sh")
    assert again.returncode == 0 and again.stdout == proc.stdout                    # one salt, stable output
    salt = (sb.base / ".smoke-salt").read_text(encoding="utf-8")
    assert len(salt) == 32 and int(salt, 16) >= 0
    assert not list(sb.base.glob(".smoke-tmp.*"))                                  # hashing scratch removed
    lines = proc.stdout.splitlines()
    assert lines == sorted(lines)
    assert "nginx-test ok" in lines
    sites = [ln for ln in lines if ln.startswith("site ")]
    assert sorted(ln.split()[2] for ln in sites) == ["200", "200", "301", "301"]   # www + apex of site two
    ctrs = [ln for ln in lines if ln.startswith("ctr ")]
    assert sorted(ln.split()[2] for ln in ctrs) == ["healthy", "up"]                # JARVIS containers excluded
    listens = [ln for ln in lines if ln.startswith("listen ")]
    assert len(listens) == 6                                                        # 5100/5101/7880/7881 excluded
    assert "4 sites" in proc.stderr and "7 names skipped" in proc.stderr
    _assert_no_other_names(_out(proc))
    assert DOMAIN not in proc.stdout
    plain_hash = hashlib.sha256(b"site-one.example.test").hexdigest()[:10]
    assert plain_hash not in proc.stdout                                            # salted, not guessable
    curl_urls = " ".join(sb.calls("curl"))
    assert "site-one.example.test" in curl_urls and DOMAIN not in curl_urls and "internal" not in curl_urls


def test_smoke_compare_classifies_changes(tmp_path: Path) -> None:
    pre = "ctr aaa healthy s1\nctr bbb up s2\nlisten l1\nlisten l2\nnginx-test ok\nsite s1 200\nsite s2 301\n"
    cases = {
        "same": (pre, 0, []),
        "site_5xx": (pre.replace("site s1 200", "site s1 502"), 2, ["REGRESSION site s1: 200 -> 502"]),
        "site_moved": (pre.replace("site s2 301", "site s2 200"), 1, ["WARN site s2: 301 -> 200"]),
        "ctr_unhealthy": (pre.replace("ctr aaa healthy s1", "ctr aaa unhealthy s1"), 2,
                          ["REGRESSION container aaa: healthy -> unhealthy"]),
        "ctr_restarted": (pre.replace("ctr bbb up s2", "ctr bbb up s9"), 1, ["WARN container bbb: restarted"]),
        "ctr_gone": (pre.replace("ctr bbb up s2\n", ""), 1, ["WARN container bbb: no longer running"]),
        "listener_gone": (pre.replace("listen l2\n", ""), 1, ["WARN listener l2: gone"]),
        "nginx_broken": (pre.replace("nginx-test ok", "nginx-test fail"), 2, ["REGRESSION nginx -T: ok -> fail"]),
        "new_site": (pre + "site s3 200\n", 1, ["INFO site s3: new (200)"]),
    }
    pre_file = _write(tmp_path / "pre.txt", pre)
    for label, (post, want_rc, want_lines) in cases.items():
        post_file = _write(tmp_path / f"post_{label}.txt", post)
        proc = subprocess.run([BASH, _posix(HOSTINGER / "smoke_other_apps.sh"), "--compare", _posix(pre_file),
                               _posix(post_file)], capture_output=True, text=True, check=False)
        assert proc.returncode == want_rc, (label, proc.stdout, proc.stderr)
        for want in want_lines:
            assert any(ln.startswith(want) for ln in proc.stdout.splitlines()), (label, proc.stdout)
        assert proc.stdout.splitlines()[-1].startswith("SUMMARY "), label


# --------------------------------------------------------------------------- deploy.sh

def test_deploy_requires_named_known_services(sb: Sandbox) -> None:
    script = sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh"
    assert sb.run(script, "--foreground").returncode == 64
    assert sb.run(script, "--services", "ui-bridge;reboot", "--foreground").returncode == 64
    assert sb.run(script, "--services", "none,frontend", "--foreground").returncode == 64
    proc = sb.deploy("--services", "nosuch", "--foreground")
    assert proc.returncode == 1, _out(proc)
    assert "no such service" in _out(proc)
    assert not sb.backups() and not sb.mutating_docker_calls()


def test_deploy_refuses_outside_the_sandbox_fakes(sb: Sandbox) -> None:
    (sb.fakebin / "certbot").unlink()   # a real certbot elsewhere must never be used in a test sandbox
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    assert proc.returncode == 1 and "must be a fake" in _out(proc)
    assert not sb.calls()


def test_deploy_refuses_livekit_when_its_keys_would_change(sb: Sandbox) -> None:
    _write(sb.hostinger / "livekit.yaml", "port: 7880\nkeys:\n  placeholder: other-secret-value\n")
    proc = sb.deploy("--services", "livekit", "--foreground")
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "differs from LIVEKIT_API_KEY/LIVEKIT_API_SECRET" in out and "values not shown" in out
    assert "other-secret-value" not in out and "placeholder" not in out
    assert not sb.backups() and not sb.mutating_docker_calls()


def test_deploy_dry_run_changes_nothing(sb: Sandbox) -> None:
    before_app, before_conf = _digest(sb.app), sb.nginx_conf.read_text(encoding="utf-8")
    proc = sb.deploy("--services", "ui-bridge", "--nginx", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "up -d --no-deps --no-build --pull never ui-bridge" in out
    assert "held back: deploy/hostinger/livekit.yaml" in out
    assert "VERDICT: a real run would proceed" in out
    assert _digest(sb.app) == before_app and sb.nginx_conf.read_text(encoding="utf-8") == before_conf
    assert not sb.backups() and not sb.mutating_docker_calls() and not sb.calls("systemctl")
    assert not (sb.run_dir / "tree").exists() and (sb.run_dir / "shipped.tar.gz").exists()


def test_service_image_is_the_services_own_image(sb: Sandbox) -> None:
    """'config --images <svc>' also prints the depends_on images (random order in Compose): restores must not use it."""
    compose = _write(sb.tmp / "compose.yml", COMPOSE_WITH_AGENT + "  worker:\n    build:\n      context: ../..\n")
    lib, cfile = sb.sh(sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh"), sb.sh(compose)
    script = (f'source "{lib}"; for s in livekit ui-bridge frontend agent worker nosuch; do '
              f'printf "%s=%s\\n" "$s" "$(service_image "{cfile}" "$s")"; done; '
              f'printf "stdin=%s\\n" "$(service_image - frontend < "{cfile}")"; '
              f'compose_file "{cfile}" config --images frontend | sed -n 1p')
    proc = subprocess.run([BASH, "-c", script], env=sb.env(), capture_output=True, text=True, check=False)
    assert proc.returncode == 0, _out(proc)
    assert proc.stdout.splitlines() == [
        "livekit=livekit/livekit-server:v1.13.7", "ui-bridge=jarvis-bridge:hostinger",
        "frontend=jarvis-frontend:hostinger", "agent=jarvis-agent:hostinger", "worker=jarvis-worker", "nosuch=",
        "stdin=jarvis-frontend:hostinger",
        "jarvis-bridge:hostinger"]   # the fake lists the dependencies first, as the real Compose often does


def test_deploy_dry_run_warns_when_a_rollback_could_not_restore_livekit(sb: Sandbox) -> None:
    _write(sb.hostinger / "docker-compose.yml", COMPOSE_NEW)   # shipped earlier; livekit still runs :latest
    proc = sb.deploy("--services", "livekit", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert ("livekit runs livekit/livekit-server:latest, but the installed docker-compose.yml names "
            "livekit/livekit-server:v1.13.7: if this deploy fails, the automatic rollback cannot bring") in out
    assert not sb.backups() and not sb.mutating_docker_calls()


@e2e
def test_deploy_bridge_only(sb: Sandbox) -> None:
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0, out
    _assert_no_other_names(out)
    calls = sb.calls()
    build = [i for i, c in enumerate(calls) if " build " in c + " "]
    up = [i for i, c in enumerate(calls) if " up -d" in c]
    assert len(build) == 1 and calls[build[0]].endswith("build ui-bridge")
    assert len(up) == 1 and calls[up[0]].endswith("up -d --no-deps --no-build --pull never ui-bridge")
    assert build[0] < up[0]
    assert not any("frontend" in c or "livekit" in c for c in sb.mutating_docker_calls())
    # the backup was taken before the install: it holds the old code
    (backup,) = sb.backups()
    with tarfile.open(backup / "opt-jarvis-agent.tgz") as tar:
        member = tar.extractfile("jarvis-agent/src/core/ui_bridge.py")
        assert member is not None and b"old" in member.read()
    assert (backup / "DEPLOYED_SHA").read_text(encoding="utf-8").strip() == OLD_SHA
    # the new code is installed, livekit.yaml is held back, env files are untouched
    assert (sb.app / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8") == "VERSION = 'new'\n"
    assert (sb.app / "src" / "core" / "new_module.py").exists()
    assert (sb.app / "DEPLOYED_SHA").read_text(encoding="utf-8").strip() == NEW_SHA
    assert "keys:" in (sb.hostinger / "livekit.yaml").read_text(encoding="utf-8")
    assert "SENTINEL=before" in (sb.hostinger / ".env").read_text(encoding="utf-8")
    record = (backup / "deploy.env").read_text(encoding="utf-8")
    for want in ("KIND=deploy", "SERVICES=ui-bridge", "RESULT=ok", "SHA=" + NEW_SHA, "PREV_SHA=" + OLD_SHA,
                 "HELD=deploy/hostinger/livekit.yaml", "CODE=1", "NGINX=0"):
        assert want in record.splitlines(), want
    assert (sb.base / "LAST_DEPLOY").read_text(encoding="utf-8").strip() == backup.name
    assert "result=ok" in (sb.base / "deploy.log").read_text(encoding="utf-8")
    assert (backup / "smoke-pre.txt").read_text(encoding="utf-8") == (backup / "smoke-post.txt").read_text(
        encoding="utf-8")
    assert sb.load_state()["tags"]["jarvis-bridge:rollback-" + backup.name] == B_OLD
    assert (sb.base / "tools" / "rollback.sh").exists() and (sb.base / "tools" / "deploy.sh").exists()
    assert not (sb.run_dir / "tree").exists() and (sb.run_dir / "rc").read_text(encoding="utf-8").strip() == "0"
    assert not sb.calls("systemctl")                                                 # no --nginx: no reload
    assert "rollback: bash " in out and "RESULT=ok" in out


@e2e
def test_deploy_livekit_gets_new_yaml_and_keys_env(sb: Sandbox) -> None:
    before = sb.load_state()["tags"]
    proc = sb.deploy("--services", "livekit", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "keys:" not in (sb.hostinger / "livekit.yaml").read_text(encoding="utf-8")
    assert any(c.endswith("pull --policy missing --ignore-buildable livekit") for c in sb.calls())
    state = sb.load_state()
    livekit, pulled = state["containers"]["jarvis-livekit"], state["tags"]["livekit/livekit-server:v1.13.7"]
    assert livekit["ref"] == "livekit/livekit-server:v1.13.7" and livekit["env"] == ["LIVEKIT_KEYS=x"]
    assert livekit["image"] == pulled != L_OLD
    _assert_tags(sb, before, {"livekit/livekit-server:v1.13.7": pulled})   # :latest still names the old image
    assert "would fail on its next restart" not in out
    assert "match the live livekit.yaml keys (values not shown)" in out and "placeholder" not in out


@e2e
def test_gate_only_warns_about_a_livekit_restart_hazard(sb: Sandbox) -> None:
    _write(sb.hostinger / "livekit.yaml", "port: 7880\n")   # keys gone from the file, container not recreated
    proc = sb.run(sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh", "--gate-only")
    assert proc.returncode == 0, _out(proc)
    assert "GATE=ok" in proc.stdout and "would fail on its next restart" in proc.stdout
    assert not sb.mutating_docker_calls() and not sb.backups()


@e2e
def test_deploy_unhealthy_service_rolls_back(sb: Sandbox) -> None:
    sb.tweak(bad_build_services=["ui-bridge"], bad_effects=["unhealthy"])
    before = sb.load_state()["tags"]
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    out = _out(proc)
    assert proc.returncode == 2, out
    assert "AUTOMATIC ROLLBACK" in out and "RESULT=rolled-back" in out
    _assert_tags(sb, before, {})   # jarvis-bridge:hostinger is back on B_OLD; LiveKit's tags were never touched
    state = sb.load_state()
    assert state["tags"]["jarvis-bridge:hostinger"] == B_OLD
    assert state["containers"]["jarvis-bridge"]["image"] == B_OLD
    assert state["containers"]["jarvis-bridge"]["health"] == "healthy"
    assert (sb.app / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8") == "VERSION = 'old'\n"
    assert (sb.app / "DEPLOYED_SHA").read_text(encoding="utf-8").strip() == OLD_SHA
    assert "SENTINEL=before" in (sb.hostinger / ".env").read_text(encoding="utf-8")
    (backup,) = sb.backups()
    assert "RESULT=rolled-back" in (backup / "deploy.env").read_text(encoding="utf-8")
    assert (sb.base / "LAST_DEPLOY").read_text(encoding="utf-8").strip() == backup.name
    assert (sb.run_dir / "rc").read_text(encoding="utf-8").strip() == "2"


@e2e
def test_deploy_other_apps_regression_rolls_back(sb: Sandbox) -> None:
    sb.tweak(bad_build_services=["ui-bridge"], bad_effects=["regress"])
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    out = _out(proc)
    assert proc.returncode == 2, out
    assert "REGRESSION site" in out and "confirmed by the re-check" in out
    _assert_no_other_names(out)
    assert sb.load_state()["containers"]["jarvis-bridge"]["image"] == B_OLD
    assert not (sb.state / "phase_bad").exists()


@e2e
def test_deploy_build_failure_changes_nothing_live(sb: Sandbox) -> None:
    sb.tweak(fail_build=["ui-bridge"])
    before = sb.load_state()["tags"]
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    out = _out(proc)
    assert proc.returncode == 1, out
    assert not any(" up -d" in c for c in sb.calls())
    _assert_tags(sb, before, {})
    assert (sb.app / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8") == "VERSION = 'old'\n"
    (backup,) = sb.backups()
    assert "RESULT=failed-before-live" in (backup / "deploy.env").read_text(encoding="utf-8")
    assert not (sb.base / "LAST_DEPLOY").exists()


@e2e
def test_deploy_rejected_nginx_conf_is_put_back(sb: Sandbox) -> None:
    sb.stage(jarvis_conf="# jarvis.conf v2\nBROKEN {\n")
    proc = sb.deploy("--services", "ui-bridge", "--nginx", "--foreground")
    out = _out(proc)
    assert proc.returncode == 1, out
    assert sb.nginx_conf.read_text(encoding="utf-8").startswith("# jarvis.conf v1 (live)")
    assert not sb.calls("systemctl") and not any(" up -d" in c for c in sb.calls())
    assert 'expecting "}" in /etc/nginx/conf.d/jarvis.conf:3' in out   # JARVIS's own nginx -t lines are shown
    _assert_no_other_names(out)


@e2e
def test_deploy_nginx_change_reloads_once(sb: Sandbox) -> None:
    proc = sb.deploy("--services", "none", "--nginx", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert sb.nginx_conf.read_text(encoding="utf-8").startswith("# jarvis.conf v2")
    assert sb.calls("systemctl") == ["systemctl reload nginx"]
    assert "nginx -t" in sb.calls("nginx")
    assert not sb.mutating_docker_calls()   # --services none: no pull, build, tag, up or stop
    # nginx -t warned about another app: never shown, kept root-only in the backup
    _assert_no_other_names(out)
    assert "nginx -t ok" in out and "1 more line(s) not shown" in out
    (backup,) = sb.backups()
    assert "site-one.example.test" in (backup / "nginx-test.txt").read_text(encoding="utf-8")


def test_deploy_lock_busy_refuses(sb: Sandbox) -> None:
    (sb.state / "flock_busy").touch()
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    assert proc.returncode == 1 and "another deploy or rollback is running" in _out(proc)
    assert not sb.backups()


def test_deploy_low_disk_refuses_a_build(sb: Sandbox) -> None:
    (sb.state / "df_low").touch()
    proc = sb.deploy("--services", "ui-bridge", "--foreground")
    assert proc.returncode == 1 and "no image build below" in _out(proc)
    assert not sb.backups()


@e2e
def test_deploy_detached_run_streams_its_log(sb: Sandbox) -> None:
    proc = sb.deploy("--services", "ui-bridge")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "running detached as pid" in out and "RESULT=ok" in out
    assert "RESULT=ok" in (sb.run_dir / "run.log").read_text(encoding="utf-8")
    assert (sb.run_dir / "rc").read_text(encoding="utf-8").strip() == "0"


@e2e
def test_deploy_new_service_is_stopped_by_the_rollback(sb: Sandbox) -> None:
    sb.stage(compose=COMPOSE_WITH_AGENT)
    sb.tweak(bad_build_services=["agent"], bad_effects=["unhealthy"])
    proc = sb.deploy("--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 2, out
    calls = sb.calls()
    assert any(c.endswith("stop agent") for c in calls)
    assert sb.load_state()["containers"]["jarvis-agent"]["status"] == "exited"
    assert sum(1 for c in calls if " up -d" in c) == 1                             # no second 'up' for agent


@e2e
def test_deploy_rollback_never_moves_a_third_party_tag(sb: Sandbox) -> None:
    """The installed compose already pins v1.13.7 (code shipped earlier) while livekit still runs :latest."""
    _write(sb.hostinger / "docker-compose.yml", COMPOSE_NEW)
    sb.tweak(bad_pull_refs=["livekit/livekit-server:v1.13.7"], bad_effects=["unhealthy"])
    before = sb.load_state()["tags"]
    proc = sb.deploy("--services", "livekit", "--foreground")
    out = _out(proc)
    assert proc.returncode == 3, out   # act now: going back needs the compose file that names :latest
    assert "the automatic rollback cannot bring livekit/livekit-server:latest back" in out   # warned before
    assert "a third-party tag is never moved onto another image" in out and "RESULT=rollback-failed" in out
    pulled = sb.load_state()["tags"]["livekit/livekit-server:v1.13.7"]
    assert pulled != L_OLD
    _assert_tags(sb, before, {"livekit/livekit-server:v1.13.7": pulled})


@e2e
def test_foreground_run_survives_a_lost_terminal(sb: Sandbox) -> None:
    """ssh without a pty: the reader goes away and there is no SIGHUP; the deploy must still finish."""
    script = sb.sh(sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh")
    pipeline = (f'bash "{script}" --run-dir "{sb.sh(sb.run_dir)}" --sha {NEW_SHA} --services ui-bridge --foreground'
                " | sed -n '/== install code/q'; echo \"PIPESTATUS=${PIPESTATUS[*]}\"")
    proc = subprocess.run([BASH, "-c", pipeline], env=sb.env(), capture_output=True, text=True, timeout=240,
                          check=False)
    assert "PIPESTATUS=0 0" in proc.stdout, _out(proc)
    assert (sb.run_dir / "rc").read_text(encoding="utf-8").strip() == "0"
    (backup,) = sb.backups()
    assert "RESULT=ok" in (backup / "deploy.env").read_text(encoding="utf-8")
    assert (sb.app / "DEPLOYED_SHA").read_text(encoding="utf-8").strip() == NEW_SHA
    assert "RESULT=ok" in (sb.run_dir / "run.log").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- rollback.sh

@e2e
def test_rollback_last_undoes_the_deploy(sb: Sandbox) -> None:
    assert sb.deploy("--services", "ui-bridge", "--foreground").returncode == 0
    env_file = sb.hostinger / ".env"
    env_file.write_text(env_file.read_text(encoding="utf-8").replace("SENTINEL=before", "SENTINEL=rotated"),
                        encoding="utf-8")                                           # a secret rotated after it
    proc = sb.run(sb.base / "tools" / "rollback.sh", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0, out
    _assert_no_other_names(out)
    state = sb.load_state()
    assert state["tags"]["jarvis-bridge:hostinger"] == B_OLD
    assert state["containers"]["jarvis-bridge"]["image"] == B_OLD
    assert (sb.app / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8") == "VERSION = 'old'\n"
    assert (sb.app / "DEPLOYED_SHA").read_text(encoding="utf-8").strip() == OLD_SHA
    assert "SENTINEL=rotated" in env_file.read_text(encoding="utf-8")              # --code keeps env files
    ups = [c for c in sb.calls() if " up -d" in c]
    assert ups[-1].endswith("up -d --no-deps --no-build --pull never ui-bridge")
    deploy_backup, rollback_backup = sb.backups()
    assert "KIND=rollback" in (rollback_backup / "deploy.env").read_text(encoding="utf-8")
    assert "ROLLED_BACK=" in (deploy_backup / "deploy.env").read_text(encoding="utf-8")
    assert "undo this rollback with: bash" in out
    # the undo goes forward again to the deployed state
    undo = sb.run(sb.base / "tools" / "rollback.sh", rollback_backup.name, "--foreground")
    assert undo.returncode == 0, _out(undo)
    assert (sb.app / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8") == "VERSION = 'new'\n"
    assert sb.load_state()["containers"]["jarvis-bridge"]["image"] != B_OLD


@e2e
def test_rollback_tag_mode_touches_only_images(sb: Sandbox) -> None:
    before, tags = _digest(sb.app), sb.load_state()["tags"]
    proc = sb.run(sb.run_dir / "tree" / "deploy" / "hostinger" / "rollback.sh", "--tag",
                  "pre-emergency-2026-09-30_2340", "--services", "ui-bridge", "--foreground")
    assert proc.returncode == 0, _out(proc)
    assert sb.load_state()["containers"]["jarvis-bridge"]["image"] == B_EMERGENCY
    _assert_tags(sb, tags, {"jarvis-bridge:hostinger": B_EMERGENCY})   # LiveKit's :latest still names L_OLD
    assert _digest(sb.app) == before
    assert not any("frontend" in c or "livekit" in c for c in sb.mutating_docker_calls())
    (backup,) = sb.backups()                                                          # the pre-rollback backup
    assert "KIND=rollback" in (backup / "deploy.env").read_text(encoding="utf-8")


@e2e
def test_rollback_to_a_plain_backup_after_a_livekit_upgrade(sb: Sandbox) -> None:
    """Plan step 9 with a backup_vps.sh folder from before the first sync deploy (no deploy.env)."""
    plain = sb.run(sb.run_dir / "tree" / "deploy" / "hostinger" / "backup_vps.sh", "verify")
    assert plain.returncode == 0, _out(plain)
    plain_name = plain.stdout.strip().splitlines()[-1].rsplit("/", 1)[1]
    assert sb.deploy("--services", "ui-bridge livekit", "--foreground").returncode == 0
    deployed = sb.load_state()
    l_new = deployed["tags"]["livekit/livekit-server:v1.13.7"]
    tools, n_backups = sb.base / "tools" / "rollback.sh", len(sb.backups())
    # without --code the compose file still names v1.13.7: going back to :latest would mean moving that tag
    proc = sb.run(tools, plain_name, "--foreground")
    assert proc.returncode == 1, _out(proc)
    assert "a third-party tag is never moved onto another image: add --code" in _out(proc)
    assert sb.load_state() == deployed and len(sb.backups()) == n_backups
    # with --code the old compose file names :latest again, so no third-party tag has to move
    proc = sb.run(tools, plain_name, "--code", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0, out
    _assert_no_other_names(out)
    state = sb.load_state()
    assert state["containers"]["jarvis-livekit"]["ref"] == "livekit/livekit-server:latest"
    assert state["containers"]["jarvis-livekit"]["image"] == L_OLD
    assert state["containers"]["jarvis-bridge"]["image"] == B_OLD
    _assert_tags(sb, deployed["tags"], {"jarvis-bridge:hostinger": B_OLD})   # v1.13.7 still names l_new
    # its undo goes forward again: livekit back on v1.13.7 (the image pulled for it) with LIVEKIT_KEYS
    proc = sb.run(tools, sb.backups()[-1].name, "--foreground")
    assert proc.returncode == 0, _out(proc)
    livekit = sb.load_state()["containers"]["jarvis-livekit"]
    assert livekit["ref"] == "livekit/livekit-server:v1.13.7" and livekit["image"] == l_new
    assert livekit["env"] == ["LIVEKIT_KEYS=x"]


@e2e
def test_rollback_undo_brings_a_new_service_back(sb: Sandbox) -> None:
    """Rolling back a deploy that created a service stops it; the undo it prints must start it again."""
    sb.stage(compose=COMPOSE_WITH_AGENT)
    assert sb.deploy("--services", "agent", "--foreground").returncode == 0
    tools = sb.base / "tools" / "rollback.sh"
    proc = sb.run(tools, "--foreground")
    assert proc.returncode == 0, _out(proc)
    assert sb.load_state()["containers"]["jarvis-agent"]["status"] == "exited"
    assert "  agent:" not in (sb.hostinger / "docker-compose.yml").read_text(encoding="utf-8")
    undo = sb.backups()[-1].name
    assert f"undo this rollback with: bash {sb.sh(sb.base / 'tools')}/rollback.sh {undo}" in _out(proc)
    proc = sb.run(tools, undo, "--foreground")
    assert proc.returncode == 0, _out(proc)
    state = sb.load_state()
    assert state["containers"]["jarvis-agent"]["status"] == "running"
    assert state["containers"]["jarvis-agent"]["image"] == state["tags"]["jarvis-agent:hostinger"]
    assert "  agent:" in (sb.hostinger / "docker-compose.yml").read_text(encoding="utf-8")


def test_rollback_dry_run_changes_nothing(sb: Sandbox) -> None:
    name = sb.fake_deployed_state()
    before, state, n_backups = _digest(sb.app), sb.load_state(), len(sb.backups())
    proc = sb.run(sb.base / "tools" / "rollback.sh", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert name in out and "image        ui-bridge: jarvis-bridge:hostinger -> b1b1b1b1b1b1" in out
    assert "code         restore" in out and "nginx        not touched" in out and "env          not touched" in out
    assert "--no-deps --no-build --pull never" in out and "VERDICT: a real run would proceed" in out
    assert _digest(sb.app) == before and sb.load_state() == state and len(sb.backups()) == n_backups
    assert not sb.mutating_docker_calls()


def test_rollback_usage_and_refusals(sb: Sandbox) -> None:
    tools = sb.run_dir / "tree" / "deploy" / "hostinger" / "rollback.sh"
    assert sb.run(tools, "--tag", "pre-emergency-2026-09-30_2340", "--foreground").returncode == 64   # no --services
    assert sb.run(tools, "../etc", "--foreground").returncode == 64                                  # not a backup name
    assert sb.run(tools, "--services", "ui-bridge;x", "--foreground").returncode == 64
    proc = sb.run(tools, "--foreground")                                                            # no deploy yet
    assert proc.returncode == 1 and "no deploy recorded" in _out(proc)
    proc = sb.run(tools, "--tag", "no-such-tag", "--services", "ui-bridge", "--foreground")
    assert proc.returncode == 1 and "there is no image jarvis-bridge:no-such-tag" in _out(proc)
    proc = sb.run(tools, "--tag", "pre-emergency-2026-09-30_2340", "--services", "livekit", "--foreground")
    assert proc.returncode == 1 and "--tag takes only images JARVIS builds" in _out(proc)   # never a 3rd-party tag
    assert not sb.backups() and not sb.mutating_docker_calls()


def test_rollback_refuses_an_old_deploy_library(sb: Sandbox) -> None:
    shutil.copy(HOSTINGER / "rollback.sh", sb.hostinger / "rollback.sh")   # next to the old v1 deploy.sh
    proc = sb.run(sb.hostinger / "rollback.sh", "--foreground")
    assert proc.returncode == 1 and "not the deploy.sh v2 library" in _out(proc)
    assert "V1_DEPLOY_RAN" not in _out(proc) and not sb.calls()


# --------------------------------------------------------------------------- deploy_from_pc.sh

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo,
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def pc_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small committed repo shaped like JARVIS (shared by the PC tests; a test that dirties it restores it)."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    return _make_pc_repo(tmp_path_factory.mktemp("pc") / "repo")


def _make_pc_repo(repo: Path) -> Path:
    hostinger = repo / "deploy" / "hostinger"
    for name in ("deploy_from_pc.sh", "deploy.sh", "rollback.sh", "smoke_other_apps.sh", "backup_vps.sh"):
        _write(hostinger / name, (HOSTINGER / name).read_text(encoding="utf-8"))
    _write(hostinger / "docker-compose.yml", COMPOSE_NEW)
    _write(repo / "src" / "core" / "ui_bridge.py", "VERSION = 'new'\n")
    _write(repo / "services" / "utils" / "x.py", "X = 1\n")
    _write(repo / "stonix_ui" / "package.json", "{}\n")
    _write(repo / "scripts" / "ops" / "tool.sh", "#!/usr/bin/env bash\necho ok\n")
    _write(repo / ".dockerignore", ".env\n")
    _write(repo / "requirements.txt", "httpx\n")
    _write(repo / "README.md", "not shipped\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "true")       # like Git for Windows: archive must still be LF
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "test commit")
    return repo


def _pc_env(sb: Sandbox, **extra: str) -> dict[str, str]:
    git = shutil.which("git")
    assert git is not None
    env = sb.env(extra_path=(str(Path(git).parent),), JARVIS_VPS_HOST="root@vps.invalid",
                 JARVIS_SSH_KEY=sb.sh(sb.home / "key"), JARVIS_DOMAIN="jarvis.test.invalid")
    env.update(extra)
    return env


def test_pc_dry_run_is_offline(sb: Sandbox, pc_repo: Path) -> None:
    sha = _git(pc_repo, "rev-parse", "--short", "HEAD")
    proc = sb.run(pc_repo / "deploy" / "hostinger" / "deploy_from_pc.sh", "--dry-run", "--services", "ui-bridge",
                  env=_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert sha in out and "src/core/ui_bridge.py" in out and "README.md" not in out
    assert "--services 'ui-bridge'" in out and "VERDICT: a real run would go ahead" in out
    assert not sb.calls("ssh") and not sb.calls("curl")


def test_pc_refuses_dirty_tree_bad_names_and_unknown_services(sb: Sandbox, pc_repo: Path) -> None:
    script = pc_repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    assert sb.run(script, "--services", "ui-bridge;rm", env=_pc_env(sb)).returncode == 64
    assert sb.run(script, env=_pc_env(sb)).returncode == 64                       # --services is required
    tracked = pc_repo / "src" / "core" / "ui_bridge.py"
    original = tracked.read_text(encoding="utf-8")
    try:
        _write(tracked, "VERSION = 'uncommitted'\n")
        proc = sb.run(script, "--services", "ui-bridge", env=_pc_env(sb))
        assert proc.returncode == 1 and "uncommitted changes" in _out(proc)
        assert "src/core/ui_bridge.py" in _out(proc)
        proc = sb.run(script, "--services", "nosuch", "--allow-dirty", env=_pc_env(sb))
        assert proc.returncode == 1 and "not a service" in _out(proc)
    finally:
        _write(tracked, original)
    assert not sb.calls("ssh")


def test_pc_ships_an_lf_archive_of_head(sb: Sandbox, pc_repo: Path) -> None:
    _write(sb.home / "key", "not a real key\n")
    sha = _git(pc_repo, "rev-parse", "--short", "HEAD")
    proc = sb.run(pc_repo / "deploy" / "hostinger" / "deploy_from_pc.sh", "--services", "ui-bridge,frontend",
                  "--nginx", env=_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0, out
    upload, deploy = sb.calls("ssh")
    assert "root@vps.invalid" in upload and "UPLOAD_OK" in upload
    assert "deploy.sh --run-dir /root/jarvis-backups/runs/" in deploy
    assert f"--sha {sha} " in deploy and "--services 'ui-bridge frontend' --nginx" in deploy
    with tarfile.open(sb.state / "uploaded.tgz") as tar:
        names = tar.getnames()
        assert "src/core/ui_bridge.py" in names and "requirements.txt" in names and ".dockerignore" in names
        assert "README.md" not in names and not any(n.endswith(".env") for n in names)
        for member in tar.getmembers():
            if member.isfile():
                data = tar.extractfile(member)
                assert data is not None and b"\r" not in data.read(), member.name
    assert "rollback: " in out and "rollback.sh 2026-10-02_0901Z-pre-deploy-fake" in out
    assert any("jarvis.test.invalid/vortex/health" in c for c in sb.calls("curl"))
    assert f"DONE: {sha} is deployed" in out


def test_pc_lost_connection_exits_5_with_the_attach_command(sb: Sandbox, pc_repo: Path) -> None:
    _write(sb.home / "key", "not a real key\n")
    (sb.state / "ssh_deploy_rc").write_text("255", encoding="utf-8")   # ssh's own code for a dropped link
    proc = sb.run(pc_repo / "deploy" / "hostinger" / "deploy_from_pc.sh", "--services", "ui-bridge", env=_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 5, out
    assert "the deploy goes on on the VPS" in out and "--attach 2" in out
    assert not sb.calls("curl")                                     # no PC checks while the outcome is unknown


def test_pc_ships_the_commit_it_resolved_while_head_moves(sb: Sandbox, tmp_path: Path) -> None:
    """Cursor commits in the same working tree: a commit right after HEAD was read must not be shipped."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = _make_pc_repo(tmp_path / "race")
    first, short = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "--short", "HEAD")
    real, mark, repo_sh = _posix(Path(str(shutil.which("git")))), sb.sh(sb.state / "raced"), _posix(repo)
    shim = _write(sb.root / "gitshim" / "git", f'''#!/usr/bin/env bash
# the first 'git ... rev-parse ... HEAD' answers, then another commit lands before the next git call
if [[ ! -e "{mark}" && " $* " == *" rev-parse "* && "${{!#}}" == HEAD ]]; then
  out="$("{real}" "$@")"; rc=$?
  : > "{mark}"
  printf "VERSION = 'committed meanwhile'\\n" > "{repo_sh}/src/core/ui_bridge.py"
  "{real}" -C "{repo_sh}" -c user.name=t -c user.email=t@example.invalid commit -qam meanwhile >/dev/null 2>&1
  printf '%s\\n' "$out"
  exit "$rc"
fi
exec "{real}" "$@"
''', 0o755)
    _write(sb.home / "key", "not a real key\n")
    env = _pc_env(sb)
    env["PATH"] = os.pathsep.join([str(shim.parent), env["PATH"]])
    proc = sb.run(repo / "deploy" / "hostinger" / "deploy_from_pc.sh", "--services", "ui-bridge", env=env)
    assert proc.returncode == 0, _out(proc)
    assert _git(repo, "rev-parse", "HEAD") != first                                 # HEAD did move meanwhile
    _, deploy = sb.calls("ssh")
    assert f"--sha {short} --sha-full {first} " in deploy
    with tarfile.open(sb.state / "uploaded.tgz") as tar:
        member = tar.extractfile("src/core/ui_bridge.py")
        assert member is not None and member.read() == b"VERSION = 'new'\n"         # the tree of that commit


@e2e
def test_pc_passes_the_deploy_exit_code_through(sb: Sandbox, pc_repo: Path) -> None:
    _write(sb.home / "key", "not a real key\n")
    (sb.state / "ssh_deploy_rc").write_text("2", encoding="utf-8")     # rolled back on the VPS
    proc = sb.run(pc_repo / "deploy" / "hostinger" / "deploy_from_pc.sh", "--services", "ui-bridge", env=_pc_env(sb))
    assert proc.returncode == 2, _out(proc)
    assert not sb.calls("curl")
