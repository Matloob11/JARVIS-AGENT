"""Restore the VPS brain's data from a backup_vps.sh backup, decrypting on this PC (plan task C-128 step 4; contracts
K-75, K-47 BACKUP_RECIPIENT_PUBLIC_KEY, K-77). Prints counts, sizes and key fingerprints, never a file's content.

Usage (from the repo root on the PC, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/restore_agent_data.py <TS> --dry-run   fetch, decrypt and check here
    .venv_312/Scripts/python.exe scripts/ops/restore_agent_data.py <TS>             then stream the data back
    .venv_312/Scripts/python.exe scripts/ops/restore_agent_data.py --check-file F   decrypt and check a local copy
    options: --key-file PATH (default: backup_key.py's store, %LOCALAPPDATA%\\JARVIS\\backup\\backup_key.dpapi),
             --host H (default JARVIS_VPS_HOST, else root@187.127.123.235), --ssh-key K (default JARVIS_SSH_KEY, else
             ~/.ssh/id_ed25519_hostinger), --backup-timeout S (step 3's time cap, default 1800 s)

The key is the VPS brain's one backup key pair (K-77), which also seals agent-svc's daily snapshots. Only
scripts/ops/backup_key.py makes and keeps it ('keygen' prints BACKUP_RECIPIENT_PUBLIC_KEY, 'public' prints it again,
'export' writes the offline copy); this tool reads it through backup_key.py: the DPAPI-wrapped store, or with
--key-file such an offline copy. The sealed format is backup_crypto's (services/utils/backup_crypto.py), the layout
inside is services/utils/agent_volumes.py's; backup_key.py decrypt opens the same file into a tar.

<TS>          a backup_vps.sh folder of /root/jarvis-backups that holds agent-volumes.tar.enc (rollback.sh <TS> --data
              prints this command). Steps, each one ssh run:
              1. read-only: the sealed file's size and sha256, the backup's agent_data line, the state of jarvis-agent
                 and jarvis-agent-svc, the free space of Docker's root dir and of /root/jarvis-backups (DISK lines);
              2. the sealed file comes to this PC (it stays encrypted on disk: a temp file, removed at the end), is
                 decrypted here and checked completely: every chunk authenticated to the final one, the tar read to its
                 end, every member a folder or a regular file under conversations/, chroma_db/ or data/, the
                 AGENT-BACKUP.json record last. A run, the dry run too, then refuses while an agent container runs,
                 without a jarvis-agent:hostinger image, or when a VPS disk has less free space than a restore needs:
                 15 GB (deploy.sh's floor for the VPS brain, K-75) or 3 x the sealed file, whichever is more (the
                 pre-data-restore backup seals the live data, and the extraction writes a second copy while the data of
                 before stays aside; nothing is pruned on this shared server). Free space that cannot be read is said
                 (WARN) and step 3 checks it again. --dry-run stops here and changes nothing anywhere;
              3. refused while jarvis-agent or jarvis-agent-svc runs (stop them first: in vps mode 'agent_ctl brain pc',
                 which keeps the owner served by the PC brain), while a deploy or rollback holds
                 /root/jarvis-backups/.deploy.lock, or when a disk is short of that need (checked again here); then
                 'backup_vps.sh pre-data-restore', so the restore itself can be undone. That backup must seal the live
                 agent data (agent-volumes.tar.enc) or find no agent volume (MANIFEST agent_data: none); otherwise
                 (SKIPPED: no BACKUP_RECIPIENT_PUBLIC_KEY in .env.agent, FAILED) the restore is refused with nothing
                 moved, the rule deploy.sh applies to an agent deploy;
              4. the decrypted tar streams over ssh stdin into a one-off agent container (docker compose -p jarvis run
                 --rm --no-deps -T, uid 10001, 'env -i': no secret in it), which gets the checked tar's byte count. In
                 each of the three volumes the live data is first moved aside into .pre-restore-<UTC>/ (never deleted;
                 later backups and agent-svc's daily snapshots leave those folders out): the script prints ASIDE_MADE
                 <volume> as soon as that folder exists, before the first move, and MOVED_ASIDE <volume> <count> once
                 the volume is done; then dd counts what arrives while tar -xf - --no-same-owner --no-overwrite-dir
                 extracts it. EXTRACTED comes only when tar exits 0 and exactly that byte count arrived; a stream cut
                 short (this PC stopped sending, was interrupted or killed, or the link broke) ends in INCOMPLETE <got>
                 of <want> bytes and exit 4, never EXTRACTED, even with nobody left to read it (GNU tar itself exits 0
                 when its input ends at a member boundary). Plain data exists only in this process's memory, inside ssh
                 and inside that container. Any failure after an ASIDE_MADE line is exit 3 naming the folder (a move
                 that failed half-way leaves part of the live data there); so is a failure the VPS did not report (the
                 connection broke off: look for the folder), an error reading or decrypting the local sealed file while
                 it streams (recorded, never taken for the VPS that stopped reading), and a Ctrl-C or any other error of
                 this PC once this step began (ssh is stopped first).
              Steps 3 and 4 each hold /root/jarvis-backups/.deploy.lock (deploy.sh's and rollback.sh's lock) from before
              their check of the agent containers to their end, so no deploy or rollback recreates an agent container
              while the volumes change; a busy lock refuses. Step 4 checks the containers again under the lock.
              agent_ctl's brain switch does not take that lock: never switch the brain while a restore runs.
              Time caps: step 1 600 s; the download 600 s plus 1 s per 256 KiB of the sealed file (a slow but live link
              finishes; a dead one ends within ssh's ServerAlive window of 120 s); step 3 --backup-timeout (1800 s).
              A step that runs out stops its ssh (killed and reaped, so no child keeps a file of this run open) and the
              run ends with exit 1, nothing moved; a backup cut off that way may still run on the VPS and hold the
              deploy lock until it ends.
--check-file  decrypt and check a local agent-volumes.tar.enc (a PC mirror of a backup) the same way; nothing is
              written.

Exit codes: 0 ok (restored, or a dry run / check that passed), 1 refused or failed before anything on the VPS changed,
2 usage or configuration (the key included), 3 failed after the live data was moved aside, or an outcome the VPS did
not report, or step 4 interrupted on this PC (act now: the output names the folder).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.utils import agent_volumes as av  # pylint: disable=wrong-import-position  # noqa: E402
from services.utils import backup_crypto as bc  # pylint: disable=wrong-import-position  # noqa: E402


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_key = _load_sibling("backup_key")   # the one key store (K-77): load_key, default_key_file, dpapi_unwrap

ARCHIVE = av.ARCHIVE
DEFAULT_HOST = "root@187.127.123.235"
BACKUP_BASE = "/root/jarvis-backups"
TOOLS_DIR = f"{BACKUP_BASE}/tools"
DEPLOY_LOCK = f"{BACKUP_BASE}/.deploy.lock"   # deploy.sh's LOCK_FILE: deploy.sh and rollback.sh hold it while they run
VPS_COMPOSE_DIR = "/opt/jarvis-agent/deploy/hostinger"
AGENT_CONTAINERS = ("jarvis-agent", "jarvis-agent-svc")
# The backup folders backup_vps.sh makes (deploy.sh's NAME_RE)
TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}Z?(-[A-Za-z0-9._-]+)?$")
RECIPIENT_RE = re.compile(r"(?:^| )recipient=([0-9a-f]{8})(?: |$)")
STAMP = "%Y%m%dT%H%M%SZ"
READ_SIZE = 1 << 20
EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_ACT_NOW = 0, 1, 2, 3
# Time caps of the ssh steps (s). The download's grows with the sealed size: a slow but live link finishes, a dead one
# ends within ssh's ServerAlive window (ServerAliveInterval 20 x ServerAliveCountMax 6 = 120 s) anyway.
INFO_TIMEOUT_S = 600
DOWNLOAD_BASE_S = 600
DOWNLOAD_FLOOR_RATE = 256 * 1024   # bytes per second
BACKUP_TIMEOUT_S = 1800            # step 3, the pre-data-restore backup (--backup-timeout)
# Free space a restore needs on the VPS disks (Docker's root dir, /root/jarvis-backups): deploy.sh's AGENT_DISK_FAIL_GB
# (K-75: a full disk hurts every app on this shared server), or DISK_MARGIN x the sealed file (the pre-data-restore
# backup seals the live data, the extraction writes a second copy while the data of before stays aside), whichever is
# more
AGENT_DISK_FAIL_GB = 15
DISK_MARGIN = 3
GIB_KIB = 1 << 20
# The decrypting side: backup_crypto's chunk-by-chunk opener (one name, so the tests can make a stream break off)
open_sealed = bc.iter_open

# Runs inside the one-off agent container as uid 10001 (POSIX sh: Debian's dash). $1 the stamp, $2 the base (/app; the
# tests pass a scratch folder), $3 the byte count of the whole checked tar (without it nothing moves: RESTORE_REFUSED).
# Each volume's live content is moved aside first, never deleted: ASIDE_MADE right after the aside folder exists and
# before the first move (so a move that fails half-way is still reported as moved), MOVED_ASIDE once the volume is
# done. A second run with the same stamp stops at the mkdir before anything moves. Then dd counts the stream while tar
# extracts only the three folders (AGENT-BACKUP.json stays out). tar's exit code is the result, and EXTRACTED comes only
# when exactly $3 bytes arrived: GNU tar exits 0 when its input ends at a member boundary, so a stream cut short (the PC
# stopped sending, was killed, or the link broke) is INCOMPLETE (exit 4) instead, whoever is left to read it.
MOVE_AND_EXTRACT = r'''set -eu
ts="$1"
base="${2:-/app}"
want="${3:-}"
case "$want" in ''|*[!0-9]*) echo "RESTORE_REFUSED the byte count of the stream is missing"; exit 3 ;; esac
log="$(mktemp)"
trap 'rm -f -- "$log"' EXIT
for d in conversations chroma_db data; do
  dir="$base/$d"
  [ -d "$dir" ] || mkdir -p "$dir"
  aside="$dir/.pre-restore-$ts"
  mkdir "$aside"
  echo "ASIDE_MADE $d .pre-restore-$ts"
  n=0
  for f in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
    [ -e "$f" ] || [ -L "$f" ] || continue
    case "${f##*/}" in .pre-restore-*) continue ;; esac
    mv -- "$f" "$aside/"
    n=$((n + 1))
  done
  echo "MOVED_ASIDE $d $n .pre-restore-$ts"
done
rc=0
dd bs=1048576 2> "$log" | tar -xf - --no-same-owner --no-overwrite-dir -C "$base" conversations chroma_db data || rc=$?
[ "$rc" -eq 0 ] || exit "$rc"
got="$(sed -n 's/^\([0-9][0-9]*\) bytes\{0,1\} .*$/\1/p' "$log" | tail -n 1)"
if [ "$got" != "$want" ]; then
  echo "INCOMPLETE ${got:-an unknown number} of $want bytes arrived"
  exit 4
fi
echo "EXTRACTED"
'''


class RestoreError(Exception):
    """A refusal or failure with its exit code; the message names the problem, never a value."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.code = code


Unwrap = Callable[[bytes], bytes]


# --------------------------------------------------------------------------- the key and the sealed file


def load_key(path: Path, unwrap: Unwrap | None = None) -> bc.X25519PrivateKey:
    """The one backup key (K-77) through backup_key.py: its DPAPI-wrapped store, or an offline copy it exported."""
    try:
        return backup_key.load_key(path, unwrap or backup_key.dpapi_unwrap)
    except backup_key.BackupKeyError as exc:
        raise RestoreError(str(exc), EXIT_USAGE) from None


def check_file(path: Path, key: bc.X25519PrivateKey) -> av.Inventory:
    """Opens and checks a local sealed file completely (every chunk to the final one, every member, the record)."""
    with open(path, "rb") as handle:
        inv = av.inventory(open_sealed(handle, key))
    inv.sealed_bytes = path.stat().st_size
    return inv


def recipient_hint(agent_data: str, key: bc.X25519PrivateKey) -> str:
    """When the backup's agent_data line names another key than this one (AGENT_SEAL recipient=<fingerprint>)."""
    match = RECIPIENT_RE.search(agent_data or "")
    if match and match.group(1) != bc.fingerprint(key):
        return (f"; the backup says it was sealed to the key {match.group(1)}, this key is {bc.fingerprint(key)} "
                "(an older key file kept by 'backup_key.py keygen --rotate' as .bak opens it: --key-file <that file>)")
    return ""


# --------------------------------------------------------------------------- VPS steps


Popen = Callable[..., Any]


def ssh_argv(host: str, ssh_key: Path, script: str) -> list[str]:
    """ssh, never interactive, with one remote command: the script as a single 'bash -c' argument."""
    return ["ssh", "-i", str(ssh_key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=20",
            "-o", "ServerAliveCountMax=6", host, shlex.join(["bash", "-c", script])]


DOCKER_ROOT_LINE = 'dr="$(docker info -f "{{.DockerRootDir}}" 2>/dev/null || true)"'
DF_AVAIL = "df -Pk -- \"$p\" 2>/dev/null | awk 'NR == 2 { print $4 }'"   # the free KiB of the disk that holds $p


def info_script(ts: str) -> str:
    """Step 1, read-only: KEY value lines about the VPS disks, the backup, the agent containers and the brain."""
    backup = f"{BACKUP_BASE}/{ts}"
    lines = [
        "set -u",
        DOCKER_ROOT_LINE,
        '[ -n "$dr" ] && [ -d "$dr" ] || dr=/var/lib/docker',
        f'p="$dr"; echo "DISK docker $({DF_AVAIL})"',
        f'p={BACKUP_BASE}; echo "DISK backups $({DF_AVAIL})"',
        f"B={shlex.quote(backup)}",
        'if [ ! -d "$B" ]; then echo "NO_BACKUP"; exit 0; fi',
        f'f="$B/{ARCHIVE}"',
        'if [ -f "$f" ]; then echo "SEALED $(stat -c %s "$f") $(sha256sum "$f" | cut -d" " -f1)";',
        'else echo "NO_SEALED"; fi',
        'sed -n "s/^agent_data: */AGENT_DATA /p" "$B/MANIFEST.txt" 2>/dev/null | head -n 1',
        'sed -n "s/^brain: */BRAIN_AT_BACKUP /p" "$B/MANIFEST.txt" 2>/dev/null | head -n 1',
    ]
    for ctr in AGENT_CONTAINERS:
        lines.append(f'echo "CTR {ctr} $(docker container inspect -f "{{{{.State.Status}}}}" {ctr} 2>/dev/null '
                     '|| echo absent)"')
    lines += [
        f'echo "BRAIN_NOW $(sed -n "s/^JARVIS_BRAIN=//p" {VPS_COMPOSE_DIR}/.env 2>/dev/null | tail -n 1 '
        "| tr -d '\"\\047 \\r')\"",
        'if docker image inspect jarvis-agent:hostinger >/dev/null 2>&1; then echo "IMAGE yes";',
        'else echo "IMAGE no"; fi',
    ]
    return "\n".join(lines) + "\n"


def refuse_running_lines() -> list[str]:
    return [f'if [ "$(docker container inspect -f "{{{{.State.Running}}}}" {ctr} 2>/dev/null || true)" = true ]; then '
            f'echo "RESTORE_REFUSED {ctr} is running"; exit 3; fi' for ctr in AGENT_CONTAINERS]


def lock_lines() -> list[str]:
    """deploy.sh's lock, taken before the container check and held by this ssh run's bash (fd 9) to its end, the
    backup or the one-off container included: no deploy or rollback recreates an agent container meanwhile. A lock
    another run holds refuses (nothing changed)."""
    return [f"exec 9>{DEPLOY_LOCK} || {{ echo \"RESTORE_REFUSED cannot open {DEPLOY_LOCK}\"; exit 3; }}",
            f'flock -n 9 || {{ echo "RESTORE_REFUSED a deploy or rollback holds {DEPLOY_LOCK}"; exit 3; }}']


def disk_lines(need_kib: int) -> list[str]:
    """Refuses (RESTORE_REFUSED, nothing changed) when Docker's root dir or /root/jarvis-backups lies on a disk with
    less than need_kib free; a disk whose free space cannot be read is passed over (step 1 said so)."""
    if need_kib <= 0:
        return []
    need_gb = -(-need_kib // GIB_KIB)
    return [DOCKER_ROOT_LINE,
            f'for p in "$dr" {BACKUP_BASE}; do',
            '  [ -n "$p" ] && [ -d "$p" ] || continue',
            f'  free="$({DF_AVAIL})"',
            '  case "$free" in ""|*[!0-9]*) continue ;; esac',
            f'  if [ "$free" -lt {need_kib} ]; then echo "RESTORE_REFUSED only $((free / {GIB_KIB})) GB free on the '
            f'disk of $p, a restore needs {need_gb} GB (nothing is pruned on this shared server)"; exit 3; fi',
            'done']


def backup_script(need_kib: int = 0) -> str:
    """Step 3: under the deploy lock, refused while an agent container runs or a disk is short of need_kib (KiB); then
    backup_vps.sh pre-data-restore (the tools copy when there). It reports whether that backup sealed the live agent
    data (PRE_RESTORE_SEALED yes|no) and its MANIFEST agent_data line (PRE_RESTORE_AGENT_DATA ...)."""
    return "\n".join(["set -u", *lock_lines(), *refuse_running_lines(), *disk_lines(need_kib),
                      f"T={TOOLS_DIR}/backup_vps.sh",
                      f'[ -f "$T" ] || T={VPS_COMPOSE_DIR}/backup_vps.sh',
                      'out="$(bash "$T" pre-data-restore < /dev/null 2>&1)"',
                      'rc=$?',
                      'printf "%s\\n" "$out" | tail -n 25',
                      'echo "BACKUP_RC=$rc"',
                      'd="$(printf "%s\\n" "$out" | sed -n "s/^BACKUP_DIR=//p" | tail -n 1)"',
                      f'if [ -n "$d" ] && [ -f "$d/{ARCHIVE}" ]; then echo "PRE_RESTORE_SEALED yes"; '
                      'else echo "PRE_RESTORE_SEALED no"; fi',
                      'if [ -n "$d" ]; then sed -n "s/^agent_data: */PRE_RESTORE_AGENT_DATA /p" "$d/MANIFEST.txt" '
                      '2>/dev/null | head -n 1; fi',
                      'exit "$rc"']) + "\n"


def extract_script(stamp: str, size: int) -> str:
    """Step 4: under the deploy lock, refused while an agent container runs; then the one-off agent container moves
    aside and extracts the tar that arrives on stdin, which must be size bytes long to count as whole.
    RESTORE_RC=<its exit code> is the last line whenever this script ran to its end, so a failure without it is one
    the VPS did not report."""
    inner = shlex.join(["docker", "compose", "-p", "jarvis", "run", "--rm", "--no-deps", "-T", "--entrypoint", "env",
                        "agent", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/tmp", "LANG=C.UTF-8",
                        "sh", "-c", MOVE_AND_EXTRACT, "sh", stamp, "/app", str(int(size))])
    return "\n".join(["set -u", *lock_lines(), *refuse_running_lines(),
                      f"cd {VPS_COMPOSE_DIR} || exit 1",
                      inner,
                      "rc=$?",
                      'echo "RESTORE_RC=$rc"',
                      'exit "$rc"']) + "\n"


def parse_info(text: str) -> dict[str, Any]:
    info: dict[str, Any] = {"containers": {}, "disk": {}}
    for line in (text or "").splitlines():
        words = line.split()
        if not words:
            continue
        head = words[0]
        if head == "DISK" and len(words) >= 3 and words[2].isdigit():
            info["disk"][words[1]] = int(words[2])
        elif head == "NO_BACKUP":
            info["no_backup"] = True
        elif head == "NO_SEALED":
            info["sealed"] = None
        elif head == "SEALED" and len(words) >= 3 and words[1].isdigit():
            info["sealed"] = {"size": int(words[1]), "sha256": words[2]}
        elif head == "AGENT_DATA":
            info["agent_data"] = line[len("AGENT_DATA "):].strip()
        elif head == "BRAIN_AT_BACKUP":
            info["brain_at_backup"] = line[len("BRAIN_AT_BACKUP "):].strip()
        elif head == "CTR" and len(words) >= 3:
            info["containers"][words[1]] = words[2]
        elif head == "BRAIN_NOW":
            info["brain_now"] = words[1] if len(words) > 1 else ""
        elif head == "IMAGE" and len(words) > 1:
            info["image"] = words[1] == "yes"
    return info


def _marked(text: str, *markers: str) -> list[str]:
    found: list[str] = []
    for line in (text or "").splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] in markers and words[1] not in found:
            found.append(words[1])
    return found


def aside_volumes(text: str) -> list[str]:
    """The volumes whose .pre-restore-<stamp>/ folder the move script made (ASIDE_MADE or MOVED_ASIDE), in order."""
    return _marked(text, "ASIDE_MADE", "MOVED_ASIDE")


def half_moved(text: str) -> list[str]:
    """The volumes whose aside folder was made (ASIDE_MADE) but whose move never finished (no MOVED_ASIDE)."""
    done = _marked(text, "MOVED_ASIDE")
    return [volume for volume in _marked(text, "ASIDE_MADE") if volume not in done]


def _kill(proc: Any) -> None:
    """Stops an ssh child that may still run (TerminateProcess on Windows, SIGKILL elsewhere); an ended one stays."""
    with contextlib.suppress(Exception):
        proc.kill()


def _close(stream: Any) -> None:
    with contextlib.suppress(OSError, ValueError, AttributeError):
        stream.close()


def _run(popen: Popen, argv: list[str], *, stdout: Any = subprocess.PIPE, timeout: float = INFO_TIMEOUT_S,
         on_timeout: str = "") -> tuple[int, str, str]:
    """One ssh run with its stdin closed. When it does not end within timeout seconds, ssh is killed and reaped (its
    pipes close and no handle of it stays open, so a temp folder can go on Windows too) and RestoreError(on_timeout,
    exit 1) follows; anything else that ends the wait (Ctrl-C) kills ssh too before it goes on up."""
    proc = popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=30)
        raise RestoreError(on_timeout or f"the VPS did not answer within {int(timeout)} s (ssh was stopped): nothing "
                           "was changed", EXIT_REFUSED) from None
    except BaseException:
        _kill(proc)
        raise
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
    return proc.returncode, text, err.decode("utf-8", "replace") if isinstance(err, bytes) else (err or "")


def _last_line(text: str) -> str:
    return ((text or "").strip().splitlines() or ["no answer"])[-1][:200]


def _source_error(exc: Exception) -> str:
    """Why the plaintext source of step 4 stopped: the decryption's own message (never content), or the type and the
    system's reason of an error reading the local sealed file."""
    if isinstance(exc, bc.SnapshotCryptoError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"{type(exc).__name__} reading the local sealed file: {exc.strerror or exc.errno or 'no reason given'}"
    return type(exc).__name__


def stream_restore(popen: Popen, argv: list[str], chunks: Iterable[bytes]) -> tuple[int, str, str, str]:
    """Step 4: the plaintext chunks into ssh's stdin while its output is read in two threads. Returns ssh's exit code,
    its stdout and stderr, and why the chunk source stopped before its end ('' when it ran to its end). Only an error
    writing into ssh's stdin means the VPS stopped reading (a refusal or a failed extraction: its output says why); an
    error of the source (reading or decrypting the local sealed file) is recorded, never passed over. Either way stdin
    is closed and the VPS's answer read. Anything else that ends this function (Ctrl-C, an error of this PC) kills ssh
    first and goes on up."""
    proc = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [threading.Thread(target=lambda s=s, b=b: b.append(s.read()), daemon=True)
               for s, b in ((proc.stdout, out), (proc.stderr, err))]
    for reader in readers:
        reader.start()
    broke = ""
    try:
        source = iter(chunks)
        while True:
            try:
                chunk = next(source)
            except StopIteration:
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught  # recorded: step 4 is then ACT NOW
                broke = _source_error(exc)
                break
            try:
                proc.stdin.write(chunk)
            except OSError:   # BrokenPipeError among them
                break         # the VPS stopped reading; its output says why
        _close(proc.stdin)
        code = proc.wait()
    except BaseException:
        _close(proc.stdin)
        _kill(proc)
        with contextlib.suppress(Exception):
            proc.wait(timeout=30)
        raise
    for reader in readers:
        reader.join(timeout=30)
    return code, b"".join(out).decode("utf-8", "replace"), b"".join(err).decode("utf-8", "replace"), broke


@dataclass
class Options:
    ts: str
    dry_run: bool
    key_file: Path
    host: str
    ssh_key: Path
    backup_timeout: int = BACKUP_TIMEOUT_S


def download_timeout(sealed_size: int) -> int:
    """The download's time cap (s): DOWNLOAD_BASE_S plus 1 s per DOWNLOAD_FLOOR_RATE bytes of the sealed file."""
    return DOWNLOAD_BASE_S + max(0, int(sealed_size)) // DOWNLOAD_FLOOR_RATE


def disk_need_kib(sealed_size: int) -> int:
    """The free space (KiB) a restore needs on each VPS disk it writes: AGENT_DISK_FAIL_GB, or DISK_MARGIN x the sealed
    file, whichever is more."""
    return max(AGENT_DISK_FAIL_GB * GIB_KIB, DISK_MARGIN * -(-max(0, int(sealed_size)) // 1024))


def disk_problem(disk: dict[str, int], need_kib: int) -> str | None:
    """None when step 1 could read no free space; '' when every disk read has need_kib free; else the refusal."""
    if not disk:
        return None
    label, free = min(disk.items(), key=lambda item: item[1])
    if free >= need_kib:
        return ""
    return (f"only {free // GIB_KIB} GB free on the VPS ({label} disk): a restore needs {-(-need_kib // GIB_KIB)} GB "
            f"there ({AGENT_DISK_FAIL_GB} GB, deploy.sh's floor for the VPS brain, or {DISK_MARGIN} x the sealed file "
            "for the pre-data-restore backup and the extracted copy, while the data of before stays aside; nothing is "
            "pruned on this shared server)")


def _unknown_outcome(why: str, when: str, backup_dir: str, err: str = "") -> str:
    undo = f"put it back from there, or restore {Path(backup_dir).name or 'the pre-data-restore backup'}"
    return (f"{why}, so how far the restore came is not known: look for .pre-restore-{when}/ in conversations, "
            f"chroma_db and data before anything else; if it is there, {undo}"
            + (f"; {_last_line(err)}" if err else ""))


def restore(opts: Options, *, popen: Popen = subprocess.Popen, out: Callable[[str], None] = print,
            unwrap: Unwrap | None = None, stamp: str | None = None, workdir: Path | None = None) -> int:
    """The whole <TS> run (steps 1-4); returns the exit code."""
    key = load_key(opts.key_file, unwrap)
    out(f"KEY={bc.fingerprint(key)} ({opts.key_file})")
    code, text, err = _run(popen, ssh_argv(opts.host, opts.ssh_key, info_script(opts.ts)), timeout=INFO_TIMEOUT_S,
                           on_timeout=f"the VPS did not answer step 1 within {INFO_TIMEOUT_S} s (ssh was stopped): "
                                      "nothing was changed")
    if code != 0:
        raise RestoreError(f"the VPS could not be read: {_last_line(err)}")
    info = parse_info(text)
    if info.get("no_backup"):
        raise RestoreError(f"no backup folder {BACKUP_BASE}/{opts.ts}")
    sealed = info.get("sealed")
    if not sealed:
        raise RestoreError(f"{opts.ts} holds no {ARCHIVE} (its MANIFEST says agent_data: "
                           f"{info.get('agent_data', 'nothing (a backup older than C-128)')})")
    out(f"BACKUP={BACKUP_BASE}/{opts.ts} agent_data: {info.get('agent_data', '?')}")
    out(f"BRAIN at the backup={info.get('brain_at_backup', '?')} now={info.get('brain_now') or '?'}")
    running = [ctr for ctr, state in info["containers"].items() if state == "running"]
    need_kib = disk_need_kib(sealed["size"])
    with tempfile.TemporaryDirectory(prefix="jarvis-agent-restore-", dir=workdir) as tmp:
        local = Path(tmp) / ARCHIVE
        cap = download_timeout(sealed["size"])
        with open(local, "wb") as handle:
            code, _, err = _run(popen, ssh_argv(opts.host, opts.ssh_key,
                                                f"cat {shlex.quote(f'{BACKUP_BASE}/{opts.ts}/{ARCHIVE}')}"),
                                stdout=handle, timeout=cap,
                                on_timeout=f"the sealed file did not arrive within {cap} s (ssh was stopped; the time "
                                           f"allowed grows with its size: {DOWNLOAD_BASE_S} s plus 1 s per "
                                           f"{DOWNLOAD_FLOOR_RATE // 1024} KiB): nothing was changed")
        if code != 0:
            raise RestoreError(f"the sealed file did not arrive: {_last_line(err)}")
        digest = _sha256(local)
        if local.stat().st_size != sealed["size"] or digest != sealed["sha256"]:
            raise RestoreError("the sealed file arrived damaged (its size or sha256 differs from the VPS copy)")
        out(f"SEALED_FILE bytes={sealed['size']} sha256={digest[:16]}...")
        try:
            inv = check_file(local, key)
        except (bc.SnapshotCryptoError, av.AgentVolumesError) as exc:
            raise RestoreError(f"the sealed file does not open with this key ({bc.fingerprint(key)}): {exc}"
                               + recipient_hint(info.get("agent_data", ""), key)) from None
        for line in inv.lines():
            out(line)
        out(f"TAR bytes={inv.plain_bytes} (the VPS extracts it as whole only when all of them arrive)")
        out("CHECK=ok (every chunk authenticated to the final one, every member a folder or file under "
            "conversations/, chroma_db/, data/)")
        refusals: list[str] = []
        if running:
            refusals.append(f"{', '.join(running)} running: stop the VPS brain first ('agent_ctl brain pc' in vps "
                            "mode keeps the owner served by the PC brain), then run this again")
        if not info.get("image"):
            refusals.append("no jarvis-agent:hostinger image on the VPS: the one-off agent container cannot extract")
        disk = disk_problem(info["disk"], need_kib)
        if disk is None:
            out(f"WARN: the free space on the VPS disks could not be read; a restore needs "
                f"{-(-need_kib // GIB_KIB)} GB on each (step 3 checks it again under the lock)")
        elif disk:
            refusals.append(disk)
        else:
            out(f"DISK ok: {min(info['disk'].values()) // GIB_KIB} GB free on the VPS (a restore needs "
                f"{-(-need_kib // GIB_KIB)} GB)")
        if refusals:
            if opts.dry_run:
                out(f"VERDICT: a real run would REFUSE: {refusals[0]}")
                return EXIT_REFUSED
            raise RestoreError(f"{disk}: nothing was changed" if refusals[0] == disk else refusals[0])
        when = stamp or time.strftime(STAMP, time.gmtime())
        if opts.dry_run:
            out("DRY RUN: nothing changed. A real run would:")
            out(f"  1. refuse while jarvis-agent or jarvis-agent-svc runs, a deploy or rollback holds {DEPLOY_LOCK} "
                "(it holds that lock itself while it backs up and while it extracts) or a VPS disk has less than "
                f"{-(-need_kib // GIB_KIB)} GB free, then run backup_vps.sh pre-data-restore, which must seal the live "
                "agent data (or find no agent volume), else refuse with nothing moved")
            out(f"  2. move the live data of each volume aside into .pre-restore-{when}/ (never deleted)")
            out(f"  3. stream the decrypted tar ({inv.plain_bytes} bytes) into a one-off agent container: tar -xf - "
                "--no-same-owner --no-overwrite-dir -C /app conversations chroma_db data; EXTRACTED only when all of "
                "it arrived (else INCOMPLETE)")
            out("VERDICT: a real run would go ahead")
            return EXIT_OK
        backup_dir, before = _pre_restore_backup(popen, opts, out, need_kib)
        if _sha256(local) != digest:
            raise RestoreError("the local sealed file changed since it was checked: nothing was changed")
        try:   # step 4: from here on whatever stops this PC is ACT NOW, never a bare traceback
            with open(local, "rb") as handle:
                code, text, err, broke = stream_restore(
                    popen, ssh_argv(opts.host, opts.ssh_key, extract_script(when, inv.plain_bytes)),
                    open_sealed(handle, key))
            _extract_outcome(code, text, err, broke=[broke] if broke else [], when=when, backup_dir=backup_dir,
                             out=out)
        except RestoreError:
            raise
        except BaseException as exc:  # pylint: disable=broad-exception-caught  # Ctrl-C, an error of this PC
            raise RestoreError(_unknown_outcome(
                f"step 4 stopped on this PC ({type(exc).__name__}) while the data streamed (ssh was stopped; the VPS "
                "marks a stream that arrived only in part INCOMPLETE, never EXTRACTED)", when, backup_dir),
                EXIT_ACT_NOW) from None
    out(f"RESTORED from {opts.ts}; the live data of before is in .pre-restore-{when}/ of each volume (never deleted)")
    if before.startswith("none"):
        undo = ("Undo: there was no agent volume before (agent_data: none in the pre-data-restore backup "
                f"{Path(backup_dir).name}), so the .pre-restore-{when}/ folders are empty.")
    else:
        undo = f"Undo: restore {Path(backup_dir).name} the same way (it holds the live agent data of before, sealed)."
    out("NEXT: start the VPS brain again the way it was stopped (vps mode: 'agent_ctl brain vps'; a brain switch's "
        "data sync (C-129) runs before its start, so check the data after it). " + undo)
    return EXIT_OK


def _pre_restore_backup(popen: Popen, opts: Options, out: Callable[[str], None], need_kib: int) -> tuple[str, str]:
    """Step 3; returns the pre-data-restore backup folder and its agent_data line. Refuses (nothing moved) when a VPS
    disk is short of need_kib, and unless that backup sealed the live agent data or found no agent volume."""
    cap = opts.backup_timeout
    code, text, err = _run(popen, ssh_argv(opts.host, opts.ssh_key, backup_script(need_kib)), timeout=cap,
                           on_timeout=f"backup_vps.sh pre-data-restore did not report within {cap} s (ssh was "
                                      "stopped): nothing was moved; the backup may still run on the VPS and holds "
                                      f"{DEPLOY_LOCK} until it ends: run this again later, or with a longer "
                                      "--backup-timeout")
    backup_dir = next((line.split("=", 1)[1] for line in text.splitlines() if line.startswith("BACKUP_DIR=")), "")
    if "RESTORE_REFUSED" in text:
        raise RestoreError("refused on the VPS:" + _refusal(text) + " (nothing was changed)")
    if code != 0 or not backup_dir:
        raise RestoreError(f"backup_vps.sh pre-data-restore failed (exit {code}): nothing was changed; "
                           f"{_last_line(text or err)}")
    out(f"PRE_RESTORE_BACKUP={backup_dir}")
    before = pre_restore_agent_data(text)
    out(f"PRE_RESTORE_AGENT_DATA={before or '?'}")
    if "PRE_RESTORE_SEALED yes" not in text.splitlines() and not before.startswith("none"):
        # the live data would be moved aside with no sealed copy anywhere: the undo could not work
        raise RestoreError(f"the pre-data-restore backup {Path(backup_dir).name} holds no agent data (agent_data: "
                           f"{before or 'not recorded'}), so the restore could not be undone: nothing was moved. "
                           "Fix that first (for SKIPPED: BACKUP_RECIPIENT_PUBLIC_KEY in .env.agent, "
                           "scripts/ops/push_agent_env.py), then run this again")
    return backup_dir, before


def _extract_outcome(code: int, text: str, err: str, *, broke: list[str], when: str, backup_dir: str,
                     out: Callable[[str], None]) -> None:
    """Step 4's result: returns only when the data was extracted whole (tar's exit 0 and the VPS's EXTRACTED, which it
    prints only once the whole tar arrived). Any failure after a .pre-restore folder was made (ASIDE_MADE, even when a
    move failed half-way), a stream that arrived only in part (INCOMPLETE), a local source that broke off (broke), or
    a run without the script's own RESTORE_RC line is ACT NOW."""
    lines = (text or "").splitlines()
    for line in lines:
        if line.startswith(("ASIDE_MADE", "MOVED_ASIDE", "INCOMPLETE", "EXTRACTED", "RESTORE_RC", "RESTORE_REFUSED")):
            out(line)
    if "RESTORE_REFUSED" in text:
        raise RestoreError("refused on the VPS (since the backup:" + _refusal(text) + "): nothing was moved")
    if code == 0 and "EXTRACTED" in (line.strip() for line in lines) and not broke:
        return
    reported = any(line.startswith("RESTORE_RC=") for line in lines)
    incomplete = next((line[len("INCOMPLETE "):].strip() for line in lines if line.startswith("INCOMPLETE ")), "")
    half = half_moved(text)
    asides = aside_volumes(text)
    if broke:
        why = f"the decrypted stream broke off ({broke[0]})"
    elif not reported:
        why = f"the VPS did not report the result (ssh exit {code}: the connection may have broken off)"
    elif incomplete:
        why = f"only part of the stream reached the VPS ({incomplete}), so tar extracted only part of it (exit {code})"
    elif half:
        why = f"moving the live data aside failed half-way in {', '.join(half)} (exit {code})"
    elif asides:
        why = f"the extraction failed (exit {code})"
    else:
        why = f"exit {code}"
    undo = f"put it back from there, or restore {Path(backup_dir).name or 'the pre-data-restore backup'}"
    if asides:
        raise RestoreError(f"{why} after the live data was moved aside: .pre-restore-{when}/ in {', '.join(asides)} "
                           f"holds what was there (only part of it when a move failed half-way, the rest is still in "
                           f"place): {undo}; {_last_line(err)}", EXIT_ACT_NOW)
    if not reported:
        raise RestoreError(_unknown_outcome(why, when, backup_dir, err or "no answer"), EXIT_ACT_NOW)
    raise RestoreError(f"the restore did not start on the VPS ({why}, before any .pre-restore folder was made): "
                       f"nothing was moved; {_last_line(err)}")


def pre_restore_agent_data(text: str) -> str:
    """The pre-data-restore backup's MANIFEST agent_data line (backup_script's PRE_RESTORE_AGENT_DATA), '' if none."""
    return next((line[len("PRE_RESTORE_AGENT_DATA "):].strip() for line in text.splitlines()
                 if line.startswith("PRE_RESTORE_AGENT_DATA ")), "")


def _refusal(text: str) -> str:
    """What follows RESTORE_REFUSED on its line (a container name or the lock), at most 200 characters."""
    return text.split("RESTORE_REFUSED", 1)[1].splitlines()[0][:200] if "RESTORE_REFUSED" in text else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(READ_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_agent_data(sealed_file: Path) -> str:
    """The agent_data line of the MANIFEST.txt beside a mirrored agent-volumes.tar.enc, '' when there is none."""
    try:
        text = sealed_file.with_name("MANIFEST.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return next((line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("agent_data:")), "")


# --------------------------------------------------------------------------- command line


def _default_ssh_key() -> Path:
    value = (os.getenv("JARVIS_SSH_KEY") or "").strip()
    return Path(os.path.expanduser(value)) if value else Path.home() / ".ssh" / "id_ed25519_hostinger"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="restore_agent_data.py", description=__doc__.strip().splitlines()[0],
                                     epilog="The key: scripts/ops/backup_key.py keygen | public | export.")
    parser.add_argument("ts", nargs="?", help="the backup folder name in /root/jarvis-backups")
    parser.add_argument("--dry-run", action="store_true", help="fetch, decrypt and check; change nothing")
    parser.add_argument("--check-file", type=Path, default=None, help="decrypt and check a local sealed file")
    parser.add_argument("--key-file", type=Path, default=None,
                        help="the backup key (default: backup_key.py's store; or an offline copy it exported)")
    parser.add_argument("--host", default=None, help="ssh target (default JARVIS_VPS_HOST, else root@187.127.123.235)")
    parser.add_argument("--ssh-key", type=Path, default=None, help="ssh key (default JARVIS_SSH_KEY)")
    parser.add_argument("--backup-timeout", type=int, default=BACKUP_TIMEOUT_S, metavar="S",
                        help=f"how long step 3's pre-data-restore backup may take, in seconds (default "
                             f"{BACKUP_TIMEOUT_S})")
    args = parser.parse_args(argv)
    if sum(bool(x) for x in (args.ts, args.check_file)) != 1:
        parser.error("give exactly one of <TS> or --check-file")
    if args.dry_run and not args.ts:
        parser.error("--dry-run goes with <TS>")
    if args.ts and not TS_RE.match(args.ts):
        parser.error(f"not a backup folder name: {args.ts!r}")
    if args.backup_timeout <= 0:
        parser.error("--backup-timeout takes a number of seconds above 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    key_file = args.key_file or backup_key.default_key_file()
    try:
        if args.check_file:
            key = load_key(key_file)
            print(f"KEY={bc.fingerprint(key)} ({key_file})")
            try:
                inv = check_file(args.check_file, key)
            except FileNotFoundError:
                raise RestoreError(f"no such file: {args.check_file}", EXIT_USAGE) from None
            except (bc.SnapshotCryptoError, av.AgentVolumesError) as exc:
                raise RestoreError(f"{args.check_file.name} does not open with this key ({bc.fingerprint(key)}): {exc}"
                                   + recipient_hint(_manifest_agent_data(args.check_file), key)) from None
            print(f"SEALED_FILE bytes={inv.sealed_bytes}")
            for line in inv.lines():
                print(line)
            print("CHECK=ok")
            return EXIT_OK
        opts = Options(ts=args.ts, dry_run=args.dry_run, key_file=key_file,
                       host=args.host or os.getenv("JARVIS_VPS_HOST") or DEFAULT_HOST,
                       ssh_key=args.ssh_key or _default_ssh_key(), backup_timeout=args.backup_timeout)
        return restore(opts)
    except RestoreError as exc:
        print(f"[restore_agent_data] {'ACT NOW: ' if exc.code == EXIT_ACT_NOW else ''}{exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
