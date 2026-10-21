"""JARVIS data migration tool (plan task C-129; contracts K-69 v44, K-72, K-75; C-124's encrypted Chroma).

Usage (from the repo root on the PC, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/migrate_agent_data.py --merge-local [--dry-run] [--user ID] [--dir PATH]
    .venv_312/Scripts/python.exe scripts/ops/migrate_agent_data.py --pre [--verify] [--dry-run]       # stage V4
    .venv_312/Scripts/python.exe scripts/ops/migrate_agent_data.py --final [--verify] [--dry-run]     # cutover (V6)
    .venv_312/Scripts/python.exe scripts/ops/migrate_agent_data.py --reverse [--verify] [--dry-run]   # 'brain pc'
    .venv_312/Scripts/python.exe scripts/ops/migrate_agent_data.py --verify [--dry-run]               # counts only
    options: --user ID (default JARVIS_USER_ID, else matloob), --root PATH (the PC data root, default the repo),
             --host H (default JARVIS_VPS_SSH, else JARVIS_VPS_HOST, else root@187.127.123.235),
             --ssh-key K (default JARVIS_SSH_KEY, else ~/.ssh/id_ed25519_hostinger)

--merge-local (stage V3; PC only; the worker stopped unless --dry-run)
    scripts/memory/migrate_user_memory.py's merge (C-52), reused: every conversations/*_memory.json is decrypted with
    JARVIS_ENCRYPTION_KEY (a legacy plain JSON list is read the way memory_store reads it), merged, deduped (a message
    once: by its id, else by its role, text, its conversation's time and its copy number in its file, so a message said
    twice in one conversation stays twice), sorted by save time and written encrypted to
    conversations/<user>_memory.json (K-69 v44: the one owner memory file) through a temporary file. The old target is
    kept as <name>.pre-<ts> and every other original is renamed <name>.pre-<ts>; nothing is deleted. Refuses (exit 4,
    nothing written or renamed) a file that neither decrypts nor reads as a JSON list, and one message id with two
    different texts. Counts only.

The data set: the same both ways, relative to the PC repo root and to /app in the agent containers (K-75 volumes)
    conversations/<user>_memory.json    the owner memory (stays encrypted; checked by decrypting it)
    conversations/tasks.db              SQLite online backup API (WAL included), copied as one file
    conversations/identity.json, learning_state.json, reminders.json   read under their jarvis_filelock lock (K-77)
    chroma_db/**                        chroma.sqlite3 by online backup after its segment folders; then every
                                        document is sealed in the copy (C-124: 'jarvis-fernet:v1:' + a Fernet token)
    data/identity/**, data/settings/**  the voice-ID store and agent_settings.json
    Never: logs and telemetry (they stay on the PC), lock files, SQLite -wal/-shm/-journal files (the online copy
    holds them), *.tmp and partial files, earlier .pre- copies, memory_store's .corrupted_ copies, and other per-job
    memory files (refused: run --merge-local first).

--pre (stage V4) and --final (cutover: the PC brain stopped, then 'docker compose stop agent agent-svc'), PC -> VPS
    1. refuses while a container of the compose services agent or agent-svc runs (jarvis-agent, jarvis-agent-svc or a
       one-off run of them; docker ps over ssh); --final also refuses while the PC worker holds run/agent.lock;
    2. runs backup_local.py --now (with a --keep that rotates nothing away: nothing is deleted anywhere);
    3. stages the data set in a temporary folder on the PC, seals Chroma's documents there, and builds a gzip tar of
       plain relative members (never './', '/' or '..'; folder entries only below the volumes' mount points);
    4. checks the containers again, then a one-off container runs 'prepare' (python as uid 10001): the container's
       JARVIS_USER_ID must be this user, its JARVIS_ENCRYPTION_KEY must decrypt a random probe this PC encrypted (so the
       keys match; no key travels) and chroma_db/.jarvis.lock (C-124) must be free; then every target the archive
       brings is renamed <name>.pre-<ts> (chroma_db's entries and a data tree as a whole; a database's -wal/-shm/
       -journal files follow it as <db>.pre-<ts>-wal);
    5. extracts: cd /opt/jarvis-agent/deploy/hostinger && docker compose -p jarvis run --rm --no-deps -T --user 10001
       --entrypoint tar agent -xzf - --no-same-owner --no-overwrite-dir -C /app   (tar's exit code is checked);
    6. a one-off container runs 'inventory': the sha256 of every copied file and the counts (memory conversations and
       messages, tasks.db rows per table, Chroma documents, files per tree), decrypted in the container with its own
       key; compared with the staged copy.
--reverse ('brain pc'), VPS -> PC with the same rules: refuses while the VPS agents or the PC worker run; backup_local
    first; a one-off container runs 'pack' (online copies in its own /tmp, then the tar on stdout); the PC checks every
    member, extracts into a staging folder (tarfile's 'data' filter), seals any plain document, compares sha256 and
    counts with the VPS's, and only then renames its own targets <name>.pre-<ts> and moves the copy in. When that
    replacing fails midway, the PC is put back as it was (the copied files taken out, the old names given back).
--verify: the counts of the PC data set and the VPS's (memory, tasks.db rows, Chroma documents, files per tree, state
    files) and the container's decrypt checks; exit 1 on a difference. With --pre, --final or --reverse it runs after
    the copy.
--dry-run: prints the plan (the exact remote commands, the files and the local counts, read read-only); no ssh, no
    backup, nothing written.

Rules: nothing is deleted on either side: targets are renamed aside, and the only things this tool removes are its own
temporary staging folder on the PC, its one-off containers (--rm) and, when it puts a failed --reverse back, the empty
folders that run made. On the VPS only the compose project jarvis is
used, through one-off containers of its service 'agent' with no deps; no service is started, stopped or recreated.
Every remote call goes through one runner (ssh_runner; the tests give a fake). Output: counts, sha256 and file names,
never a memory text, a document, a key or an env value.
Exit codes: 0 ok, 1 a remote step failed or the two sides differ, 2 usage or configuration, 3 something runs (a VPS
agent container, the PC worker, a holder of chroma_db/.jarvis.lock), 4 refused (unreadable or ambiguous input,
unmerged per-job memory files, the container's user or key differs, an unknown Chroma layout, an unsafe archive).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import inspect
import json
import os
import pathlib
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNNING, EXIT_REFUSED = 0, 1, 2, 3, 4

# ----------------------------------------------------------------------------------------------- the data set
SUFFIX = "_memory.json"
DEFAULT_USER_ID = "matloob"  # services/utils/jarvis_config.DEFAULT_USER_ID (K-69 v44)
ENC_PREFIX = "jarvis-fernet:v1:"  # services/ai_core/jarvis_vector_memory.ENC_PREFIX (C-124; a test keeps them equal)
STATE_FILES = ("conversations/identity.json", "conversations/learning_state.json", "conversations/reminders.json")
SQLITE_FILES = ("conversations/tasks.db",)
CHROMA_DIR = "chroma_db"
CHROMA_DB_FILE = "chroma.sqlite3"
# The chroma.sqlite3 tables this tool reads and seals (chromadb 1.5.0, pinned on the PC and in the image)
CHROMA_TABLES = ("collections", "segments", "embeddings", "embedding_metadata", "embeddings_queue",
                 "embedding_fulltext_search")
DATA_TREES = ("data/identity", "data/settings")
ARCHIVE_TOPS = ("conversations", CHROMA_DIR, "data")
KEEP_NAMES = (".jarvis.lock",)  # C-124's single-opener lock in chroma_db: never copied, never renamed
SQLITE_MAIN_SUFFIXES = (".db", ".sqlite3", ".sqlite")
SQLITE_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
SKIP_SUFFIXES = (".lock", ".tmp", ".part", ".partial") + SQLITE_SIDE_SUFFIXES
PRE_MARK = ".pre-"
SKIP_MARKS = (PRE_MARK, ".corrupted_")
LOCK_TIMEOUT_S = 10.0  # services/utils/jarvis_filelock.DEFAULT_TIMEOUT_S
STAMP_FORMAT = "%Y%m%d-%H%M%S"  # the .pre-<ts> of scripts/memory/migrate_user_memory.py
BACKUP_KEEP_ALL = 1_000_000  # backup_local.py --keep: its rotation removes no backup during a migration

# ----------------------------------------------------------------------------------------------- the VPS side
CONTAINER_ROOT = "/app"
COMPOSE_PROJECT = "jarvis"
COMPOSE_SERVICE = "agent"  # one-off containers of it: the agent image, uid 10001, the K-75 volumes, .env.agent
CONTAINER_USER = "10001"
AGENT_CONTAINERS = ("jarvis-agent", "jarvis-agent-svc")
AGENT_SERVICES = ("agent", "agent-svc")
VPS_COMPOSE_DIR = "/opt/jarvis-agent/deploy/hostinger"  # scripts/agent_ctl.py VPS_COMPOSE_DIR
DEFAULT_HOST = "root@187.127.123.235"  # deploy_from_pc.sh's JARVIS_VPS_HOST default
SSH_TIMEOUT_S = 900
EXTRACT_TAR_ARGS = ("-xzf", "-", "--no-same-owner", "--no-overwrite-dir", "-C", CONTAINER_ROOT)
# The container programs arrive as JSON {program, args} on stdin; python runs this loader (nothing on a command line).
LOADER = ('import json,sys; d=json.load(sys.stdin); '
          'exec(compile(d["program"], "jarvis-migrate", "exec"), {"__name__": "jarvis_migrate", "ARGS": d["args"]})')
REASONS = {
    "user": "the container's JARVIS_USER_ID is not this user, so the VPS brain would not read the copied memory file",
    "key": ("the container cannot decrypt a probe made with this PC's JARVIS_ENCRYPTION_KEY (missing, invalid or a "
            "different key in .env.agent), so it could not read the copied memory"),
    "chroma_lock": ("a process holds chroma_db/.jarvis.lock (C-124): an agent still runs on the VPS; stop "
                    "jarvis-agent and jarvis-agent-svc first"),
}


def _compose_run(entrypoint: str, *args: str) -> str:
    """The remote shell command of one one-off container of the service agent (no deps, uid 10001, no TTY)."""
    return f"cd {VPS_COMPOSE_DIR} && " + shlex.join(
        ["docker", "compose", "-p", COMPOSE_PROJECT, "run", "--rm", "--no-deps", "-T", "--user", CONTAINER_USER,
         "--entrypoint", entrypoint, COMPOSE_SERVICE, *args])


PS_COMMAND = shlex.join(["docker", "ps", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
                         "--format", '{{.Names}}|{{.Label "com.docker.compose.service"}}'])
EXTRACT_COMMAND = _compose_run("tar", *EXTRACT_TAR_ARGS)
PROGRAM_COMMAND = _compose_run("python", "-c", LOADER)


def _load(path: Path, name: str) -> ModuleType:
    """A script of this repo as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate_user_memory = _load(ROOT / "scripts" / "memory" / "migrate_user_memory.py", "jarvis_migrate_user_memory")
env_remote = _load(Path(__file__).with_name("env_remote.py"), "jarvis_ops_env_remote")


# ======================================================================================================================
# Shipped: these functions run on the PC and, as source, inside the one-off containers (container_program()). They
# use only the modules of CONTAINER_HEADER (cryptography imported inside), the constants of SHIPPED_CONSTANTS and each
# other, and never print a content: names, counts and sha256 only.
# ======================================================================================================================


def emit_row(row, args):
    """One JSON report row of a container program: on stdout, or on stderr for 'pack' (whose stdout is the tar)."""
    stream = sys.stderr if args.get("rows_to") == "stderr" else sys.stdout
    stream.write(json.dumps(row, sort_keys=True) + "\n")
    stream.flush()


def is_skipped(name):
    """True for what never travels: lock files, SQLite side files (the online copy holds them), temporary and partial
    files, earlier .pre- copies and memory_store's .corrupted_ copies."""
    return name in KEEP_NAMES or name.endswith(SKIP_SUFFIXES) or any(mark in name for mark in SKIP_MARKS)


def tree_files(root, top):
    """The data files under root/top as relative POSIX paths, sorted; links and skipped names are left out."""
    base = os.path.join(root, *top.split("/"))
    found = []
    if not os.path.isdir(base) or os.path.islink(base):
        return found
    for folder, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not is_skipped(d) and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(filenames):
            path = os.path.join(folder, name)
            if is_skipped(name) or os.path.islink(path) or not os.path.isfile(path):
                continue
            found.append(os.path.relpath(path, root).replace(os.sep, "/"))
    return found


def data_set(root, user):
    """The relative paths of the data set that exist under root (see the module docstring): the owner memory file,
    the state files, the SQLite files, chroma_db (only with its chroma.sqlite3) and the data trees."""
    rels = []
    for rel in ["conversations/" + user + SUFFIX] + list(STATE_FILES) + list(SQLITE_FILES):
        path = os.path.join(root, *rel.split("/"))
        if os.path.isfile(path) and not os.path.islink(path):
            rels.append(rel)
    chroma_db = os.path.join(root, CHROMA_DIR, CHROMA_DB_FILE)
    if os.path.isfile(chroma_db) and not os.path.islink(chroma_db):
        rels.extend(tree_files(root, CHROMA_DIR))
    for tree in DATA_TREES:
        rels.extend(tree_files(root, tree))
    return sorted(set(rels))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fernet_for(key):
    """The Fernet of key, None without a valid one."""
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except Exception:  # pylint: disable=broad-exception-caught  # not a Fernet key (or not ASCII)
        return None


def probe_ok(key, probe, digest):
    """True when key decrypts probe (a Fernet token the PC made) to bytes whose sha256 is digest: both sides hold the
    same JARVIS_ENCRYPTION_KEY, and no key or value ever travels."""
    fernet = fernet_for(key)
    if fernet is None or not isinstance(probe, str) or not isinstance(digest, str):
        return False
    try:
        plain = fernet.decrypt(probe.encode("ascii"))
    except Exception:  # pylint: disable=broad-exception-caught  # InvalidToken: another key
        return False
    return hashlib.sha256(plain).hexdigest() == digest


def memory_report(path, key):
    """The counts of a memory file: {present, decrypt: ok|plain|empty|failed, conversations, messages}. Read the way
    memory_store reads it (Fernet with key first, then a legacy plain JSON list); never a text."""
    if not os.path.isfile(path):
        return {"present": False}
    with open(path, "rb") as handle:
        data = handle.read()
    if not data:
        return {"present": True, "decrypt": "empty", "conversations": 0, "messages": 0}
    loaded, decrypt = None, "failed"
    fernet = fernet_for(key)
    if fernet is not None:
        try:
            loaded, decrypt = json.loads(fernet.decrypt(data).decode("utf-8")), "ok"
        except Exception:  # pylint: disable=broad-exception-caught  # another key, or not a token: maybe plain JSON
            loaded = None
    if loaded is None:
        try:
            loaded, decrypt = json.loads(data.decode("utf-8")), "plain"
        except Exception:  # pylint: disable=broad-exception-caught
            return {"present": True, "decrypt": "failed"}
    if not isinstance(loaded, list):
        return {"present": True, "decrypt": "failed"}
    messages = sum(len(item["messages"]) for item in loaded
                   if isinstance(item, dict) and isinstance(item.get("messages"), list))
    return {"present": True, "decrypt": decrypt, "conversations": len(loaded), "messages": messages}


def _ro_connect(path):
    """A read-only connection to a SQLite file: it never writes, checkpoints or creates the file."""
    return sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def sqlite_report(path):
    """{present, tables: {name: rows}} of a SQLite file (read-only), or {present, error: <type>}."""
    if not os.path.isfile(path):
        return {"present": False}
    try:
        con = _ro_connect(path)
        try:
            names = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            tables = {name: con.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                      for name in names}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"present": True, "error": type(exc).__name__}
    return {"present": True, "tables": tables}


def chroma_report(path, key):
    """The counts of a chroma.sqlite3 (read-only): {present, documents (records), collections {name: records},
    sealed, plain, decrypt_ok, decrypt_failed (sealed with another key), without_text}, or {present, error}."""
    if not os.path.isfile(path):
        return {"present": False}
    try:
        con = _ro_connect(path)
        try:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [name for name in CHROMA_TABLES if name not in tables]
            if missing:
                return {"present": True, "error": "unknown Chroma layout (no " + ", ".join(missing) + ")"}
            rows = con.execute(
                "SELECT c.name, m.string_value FROM embeddings e "
                "JOIN segments s ON s.id = e.segment_id JOIN collections c ON c.id = s.collection "
                "LEFT JOIN embedding_metadata m ON m.id = e.id AND m.key = 'chroma:document'").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"present": True, "error": type(exc).__name__}
    fernet = fernet_for(key)
    report = {"present": True, "documents": len(rows), "collections": {}, "sealed": 0, "plain": 0,
              "decrypt_ok": 0, "decrypt_failed": 0, "without_text": 0}
    for name, text in rows:
        report["collections"][name] = report["collections"].get(name, 0) + 1
        if not isinstance(text, str):
            report["without_text"] += 1
        elif text.startswith(ENC_PREFIX):
            report["sealed"] += 1
            try:
                fernet.decrypt(text[len(ENC_PREFIX):].encode("ascii"))
                report["decrypt_ok"] += 1
            except Exception:  # pylint: disable=broad-exception-caught  # another key (or no key)
                report["decrypt_failed"] += 1
        else:
            report["plain"] += 1
    return report


def seal_chroma(path, key):
    """C-124 for a Chroma copy (never the live chroma_db): every document is stored as ENC_PREFIX + a Fernet token of
    its text, in the three places chromadb 1.5 keeps it in chroma.sqlite3: embedding_metadata ('chroma:document'),
    the rows of the full-text index embedding_fulltext_search that hold a document's text, and the 'chroma:document'
    of the embeddings_queue records' metadata. The vectors stay (C-124 makes them from the text too), a sealed
    document is never sealed twice, and when anything changed the index is optimized and the file vacuumed with
    secure_delete on, so no freed page keeps a text. A layout it does not know raises ValueError with nothing
    changed. Returns counts: {documents, sealed, already, index, queue}."""
    fernet = fernet_for(key)
    if fernet is None:
        raise ValueError("no valid JARVIS_ENCRYPTION_KEY")

    def seal(text):
        return ENC_PREFIX + fernet.encrypt(text.encode("utf-8")).decode("ascii")

    con = sqlite3.connect(path)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [name for name in CHROMA_TABLES if name not in tables]
        if missing:
            raise ValueError("unknown Chroma layout (no " + ", ".join(missing) + ")")
        con.execute("PRAGMA secure_delete = ON")
        counts = {"documents": 0, "sealed": 0, "already": 0, "index": 0, "queue": 0}
        with con:  # one transaction: an error leaves the copy as it was
            first_token = {}
            for row_id, text in con.execute(
                    "SELECT id, string_value FROM embedding_metadata "
                    "WHERE key = 'chroma:document' AND string_value IS NOT NULL").fetchall():
                counts["documents"] += 1
                if text.startswith(ENC_PREFIX):
                    counts["already"] += 1
                    continue
                token = seal(text)
                first_token.setdefault(text, token)
                con.execute("UPDATE embedding_metadata SET string_value = ? WHERE id = ? AND key = 'chroma:document'",
                            (token, row_id))
                counts["sealed"] += 1
            if first_token:
                for row_id, text in con.execute(
                        "SELECT rowid, string_value FROM embedding_fulltext_search").fetchall():
                    if text in first_token:
                        con.execute("UPDATE embedding_fulltext_search SET string_value = ? WHERE rowid = ?",
                                    (first_token[text], row_id))
                        counts["index"] += 1
            for seq_id, meta in con.execute(
                    "SELECT seq_id, metadata FROM embeddings_queue WHERE metadata IS NOT NULL").fetchall():
                data = json.loads(meta)
                text = data.get("chroma:document") if isinstance(data, dict) else None
                if isinstance(text, str) and not text.startswith(ENC_PREFIX):
                    data["chroma:document"] = seal(text)
                    con.execute("UPDATE embeddings_queue SET metadata = ? WHERE seq_id = ?",
                                (json.dumps(data, ensure_ascii=False, separators=(",", ":")), seq_id))
                    counts["queue"] += 1
            if counts["sealed"] or counts["queue"]:
                con.execute("INSERT INTO embedding_fulltext_search(embedding_fulltext_search) VALUES ('optimize')")
        if counts["sealed"] or counts["queue"]:
            con.execute("VACUUM")
        left = con.execute(
            "SELECT COUNT(*) FROM embedding_metadata WHERE key = 'chroma:document' AND string_value IS NOT NULL "
            "AND substr(string_value, 1, ?) != ?", (len(ENC_PREFIX), ENC_PREFIX)).fetchone()[0]
        if left:
            raise ValueError(str(left) + " document(s) left unsealed")
    finally:
        con.close()
    return counts


def sqlite_copy(src, dst):
    """A consistent copy of a SQLite database, also of one another process has open (its WAL included), with the
    online backup API; the copy is one file in rollback-journal mode. src is opened read-only and never written."""
    if os.path.lexists(dst):
        raise FileExistsError("the copy target exists: " + os.path.basename(dst))
    source = _ro_connect(src)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE").fetchall()
            target.commit()
        finally:
            target.close()
    finally:
        source.close()


def read_locked(path, timeout=LOCK_TIMEOUT_S):
    """The bytes of a state file, read while holding its cross-process lock: the scheme of
    services/utils/jarvis_filelock.locked (an OS lock on byte 0 of '<path>.lock'; msvcrt on Windows, fcntl elsewhere).
    TimeoutError when another holder keeps it longer than timeout seconds."""
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                if os.name == "nt":
                    import msvcrt  # pylint: disable=import-outside-toplevel,import-error
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl  # pylint: disable=import-outside-toplevel,import-error
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("file lock busy: " + os.path.basename(path) + ".lock") from None
                time.sleep(0.02)
        try:
            with open(path, "rb") as handle:
                return handle.read()
        finally:
            if os.name == "nt":
                import msvcrt  # pylint: disable=import-outside-toplevel,import-error
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # pylint: disable=import-outside-toplevel,import-error
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def stage_copy(src_root, rels, dst_root, lock_timeout=LOCK_TIMEOUT_S):
    """Copies rels from src_root into dst_root (a new folder; never over a file): the SQLite files with sqlite_copy
    (chroma.sqlite3 after Chroma's segment files, so a copy taken while Chroma writes finds every record in its
    queue), the state files under their lock, everything else byte for byte."""
    chroma_db = CHROMA_DIR + "/" + CHROMA_DB_FILE
    for rel in sorted(rels, key=lambda item: (item == chroma_db, item)):
        src = os.path.join(src_root, *rel.split("/"))
        dst = os.path.join(dst_root, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if rel in SQLITE_FILES or rel == chroma_db:
            sqlite_copy(src, dst)
        elif rel in STATE_FILES:
            data = read_locked(src, lock_timeout)
            with open(dst, "xb") as handle:
                handle.write(data)
        else:
            with open(src, "rb") as source, open(dst, "xb") as target:
                shutil.copyfileobj(source, target)


def write_tar(fileobj, root, rels, mtime=None):
    """A gzip tar (a stream, so stdout works) of rels under root. Every member is a plain relative name: never './',
    '/', '..' or a bare top-level folder (the volumes' mount points exist and stay as they are). Folders below the top
    level get an entry of their own (mode 700) before their files (mode 600); owner 0 and no owner names (extracted
    with --no-same-owner)."""
    stamp = int(time.time() if mtime is None else mtime)
    folders = set()
    for rel in rels:
        parts = rel.split("/")
        if len(parts) < 2 or rel.startswith(("/", "./")) or "\\" in rel or any(p in ("", ".", "..") for p in parts):
            raise ValueError("not a relative data path: " + rel)
        for depth in range(2, len(parts)):
            folders.add("/".join(parts[:depth]))
    with tarfile.open(fileobj=fileobj, mode="w|gz", format=tarfile.PAX_FORMAT) as tar:
        for folder in sorted(folders, key=lambda item: (item.count("/"), item)):
            info = tarfile.TarInfo(folder)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o700, stamp
            tar.addfile(info)
        for rel in sorted(rels):
            with open(os.path.join(root, *rel.split("/")), "rb") as handle:
                stat = os.fstat(handle.fileno())
                info = tarfile.TarInfo(rel)
                info.size, info.mode, info.mtime = stat.st_size, 0o600, int(stat.st_mtime)
                tar.addfile(info, handle)


def aside_target(path, stamp):
    """A free name to set path aside under: <path>.pre-<stamp>, else <path>.pre-<stamp>.<n>; free for its SQLite side
    files too, so nothing is ever renamed over an existing name."""
    base = path + PRE_MARK + stamp
    candidate, number = base, 0
    while os.path.lexists(candidate) or any(os.path.lexists(candidate + side) for side in SQLITE_SIDE_SUFFIXES):
        number += 1
        candidate = base + "." + str(number)
    return candidate


def rename_aside(root, plan, stamp, on_rename=None):
    """Sets every existing target of plan aside under root with os.rename, never over an existing name: a file or a
    whole data tree becomes <name>.pre-<stamp>; a SQLite database's -wal, -shm and -journal files follow it as
    <db>.pre-<stamp>-wal (SQLite then still finds them); for each folder of plan['children'] every entry is set aside
    except KEEP_NAMES and earlier .pre- entries. plan: {files, trees, children} of relative paths. Returns
    [[old, new]] (relative paths), calling on_rename with each pair at once."""
    done = []

    def move(old_rel, old_path, new_path):
        os.rename(old_path, new_path)
        pair = [old_rel, os.path.relpath(new_path, root).replace(os.sep, "/")]
        done.append(pair)
        if on_rename is not None:
            on_rename(pair)

    def aside(rel):
        path = os.path.join(root, *rel.split("/"))
        sides = list(SQLITE_SIDE_SUFFIXES) if rel.endswith(SQLITE_MAIN_SUFFIXES) else []
        if not os.path.lexists(path) and not any(os.path.lexists(path + side) for side in sides):
            return
        target = aside_target(path, stamp)
        if os.path.lexists(path):
            move(rel, path, target)
        for side in sides:
            if os.path.lexists(path + side):
                move(rel + side, path + side, target + side)

    for rel in list(plan.get("files") or []) + list(plan.get("trees") or []):
        aside(rel)
    for parent in plan.get("children") or []:
        folder = os.path.join(root, *parent.split("/"))
        if not os.path.isdir(folder):
            continue
        names = sorted(os.listdir(folder))
        for name in names:
            if name in KEEP_NAMES or PRE_MARK in name:
                continue
            side = next((suffix for suffix in SQLITE_SIDE_SUFFIXES if name.endswith(suffix)), None)
            if side and name[:-len(side)] in names and name[:-len(side)].endswith(SQLITE_MAIN_SUFFIXES):
                continue  # it follows its database
            aside(parent + "/" + name)
    return done


def rename_plan(rels):
    """What a copy of rels replaces: {children: [chroma_db] (all of it), trees: the data trees it brings (each as a
    whole), files: the other files}."""
    trees = [tree for tree in DATA_TREES if any(rel.startswith(tree + "/") for rel in rels)]
    children = [CHROMA_DIR] if any(rel.startswith(CHROMA_DIR + "/") for rel in rels) else []
    files = [rel for rel in rels if not any(rel.startswith(top + "/") for top in trees + children)]
    return {"files": files, "trees": trees, "children": children}


def inventory(root, rels, user, key):
    """sha256 and size of every file of rels under root, and the counts: the memory file (conversations and messages,
    and whether it decrypts with key), each SQLite file's rows per table, Chroma's documents (how many are sealed and
    decrypt with key), the files of each data tree and the state files present. Never a content."""
    def at(rel):
        return os.path.join(root, *rel.split("/"))

    files = {rel: [sha256_file(at(rel)), os.path.getsize(at(rel))] for rel in rels}
    memory_rel = "conversations/" + user + SUFFIX
    chroma_rel = CHROMA_DIR + "/" + CHROMA_DB_FILE
    return {
        "files": files,
        "memory": memory_report(at(memory_rel), key) if memory_rel in files else {"present": False},
        "sqlite": {rel: sqlite_report(at(rel)) for rel in SQLITE_FILES if rel in files},
        "chroma": chroma_report(at(chroma_rel), key) if chroma_rel in files else {"present": False},
        "trees": {tree: sum(1 for rel in files if rel.startswith(tree + "/")) for tree in DATA_TREES},
        "state": [rel for rel in STATE_FILES if rel in files],
    }


def chroma_lock(root):
    """C-124's single-opener lock, chroma_db/.jarvis.lock (POSIX fcntl), held until this program ends: its open file,
    or None when another process holds it (an agent runs). True where there is no fcntl (Windows: agent_lock rules)."""
    try:
        import fcntl  # pylint: disable=import-outside-toplevel,import-error
    except ImportError:
        return True
    folder = os.path.join(root, CHROMA_DIR)
    os.makedirs(folder, exist_ok=True)
    handle = open(os.path.join(folder, KEEP_NAMES[0]), "a+", encoding="utf-8")  # pylint: disable=consider-using-with
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def container_checks(args, key):
    """{user_matches, key_matches}: the container's JARVIS_USER_ID (empty means DEFAULT_USER_ID, K-69 v44) is
    args['user'], and its JARVIS_ENCRYPTION_KEY decrypts the PC's probe. Booleans only."""
    user = (os.environ.get("JARVIS_USER_ID") or "").strip() or DEFAULT_USER_ID
    return {"user_matches": user == args.get("user"),
            "key_matches": probe_ok(key, args.get("probe"), args.get("probe_sha256"))}


def container_prepare(args):
    """Before the extraction: refuses on another user or key (4) or a held Chroma lock (3), then renames every target
    of args['plan'] aside with args['stamp'], one 'renamed' row per rename."""
    key = (os.environ.get("JARVIS_ENCRYPTION_KEY") or "").strip()
    checks = container_checks(args, key)
    emit_row({"checks": checks}, args)
    for name, reason in (("user_matches", "user"), ("key_matches", "key")):
        if not checks[name]:
            emit_row({"result": "refused", "reason": reason, "code": 4}, args)
            return 4
    lock = chroma_lock(args["root"])
    if lock is None:
        emit_row({"result": "refused", "reason": "chroma_lock", "code": 3}, args)
        return 3
    rename_aside(args["root"], args["plan"], args["stamp"], lambda pair: emit_row({"renamed": pair}, args))
    emit_row({"result": "ok"}, args)
    return 0


def container_inventory(args):
    """The inventory of args['files'] (those that exist), or of the whole data set when it is None, with the checks;
    refuses (3) while a process holds the Chroma lock."""
    key = (os.environ.get("JARVIS_ENCRYPTION_KEY") or "").strip()
    emit_row({"checks": container_checks(args, key)}, args)
    lock = chroma_lock(args["root"])
    if lock is None:
        emit_row({"result": "refused", "reason": "chroma_lock", "code": 3}, args)
        return 3
    rels = args.get("files")
    if rels is None:
        rels = data_set(args["root"], args["user"])
    else:
        rels = [rel for rel in rels if os.path.isfile(os.path.join(args["root"], *rel.split("/")))]
    emit_row({"inventory": inventory(args["root"], rels, args["user"], key)}, args)
    emit_row({"result": "ok"}, args)
    return 0


def container_pack(args):
    """--reverse: the data set as a gzip tar on stdout, made from online copies in a fresh folder of the container's
    own /tmp (gone with the one-off container); the inventory of exactly those copies and the result go to stderr.
    Refuses on another user or key (4) or a held Chroma lock (3)."""
    key = (os.environ.get("JARVIS_ENCRYPTION_KEY") or "").strip()
    checks = container_checks(args, key)
    emit_row({"checks": checks}, args)
    for name, reason in (("user_matches", "user"), ("key_matches", "key")):
        if not checks[name]:
            emit_row({"result": "refused", "reason": reason, "code": 4}, args)
            return 4
    lock = chroma_lock(args["root"])
    if lock is None:
        emit_row({"result": "refused", "reason": "chroma_lock", "code": 3}, args)
        return 3
    rels = data_set(args["root"], args["user"])
    work = tempfile.mkdtemp(prefix="jarvis-migrate-")
    stage_copy(args["root"], rels, work)
    emit_row({"inventory": inventory(work, rels, args["user"], key)}, args)
    write_tar(sys.stdout.buffer, work, rels)
    sys.stdout.buffer.flush()
    emit_row({"result": "ok"}, args)
    return 0


SHIPPED_CONSTANTS = ("SUFFIX", "DEFAULT_USER_ID", "ENC_PREFIX", "STATE_FILES", "SQLITE_FILES", "CHROMA_DIR",
                     "CHROMA_DB_FILE", "CHROMA_TABLES", "DATA_TREES", "KEEP_NAMES", "SQLITE_MAIN_SUFFIXES",
                     "SQLITE_SIDE_SUFFIXES", "SKIP_SUFFIXES", "PRE_MARK", "SKIP_MARKS", "LOCK_TIMEOUT_S")
SHIPPED_FUNCTIONS = (emit_row, is_skipped, tree_files, data_set, sha256_file, fernet_for, probe_ok, memory_report,
                     _ro_connect, sqlite_report, chroma_report, seal_chroma, sqlite_copy, read_locked, stage_copy,
                     write_tar, aside_target, rename_aside, rename_plan, inventory, chroma_lock, container_checks,
                     container_prepare, container_inventory, container_pack)
CONTAINER_ENTRIES = ("container_prepare", "container_inventory", "container_pack")
CONTAINER_HEADER = ("from __future__ import annotations\n"
                    "import hashlib, json, os, pathlib, shutil, sqlite3, sys, tarfile, tempfile, time\n")


def container_program(entry: str, *, run: bool = True) -> str:
    """The source a one-off container runs: CONTAINER_HEADER, the shipped constants and functions, then (run) the
    call of entry with ARGS (which LOADER defines from stdin); its return value is the exit code. An unexpected
    exception reports its type (and errno) only and exits 1: a message could quote a path or a value."""
    if entry not in CONTAINER_ENTRIES:
        raise ValueError(f"{entry} is not a container entry")
    constants = "".join(f"{name} = {globals()[name]!r}\n" for name in SHIPPED_CONSTANTS)
    functions = "\n\n".join(textwrap.dedent(inspect.getsource(function)) for function in SHIPPED_FUNCTIONS)
    guard = ("\n\ntry:\n"
             f"    _exit_code = {entry}(ARGS)\n"
             "except Exception as _exc:  # the type only: a message could quote a path or a value\n"
             "    emit_row({'result': 'failed', 'error': 'unexpected ' + type(_exc).__name__,\n"
             "              'errno': getattr(_exc, 'errno', None)}, ARGS)\n"
             "    _exit_code = 1\n"
             "raise SystemExit(_exit_code)\n") if run else "\n"
    return CONTAINER_HEADER + constants + "\n\n" + functions + guard


# ======================================================================================================================
# PC side
# ======================================================================================================================


class RemoteResult(NamedTuple):
    code: int
    out: bytes
    err: str


# runner(command, *, stdin: bytes | Path | None = None, stdout_to: Path | None = None, timeout=...) -> RemoteResult
Runner = Callable[..., RemoteResult]


class Refused(Exception):
    """A step refused or failed; code is the exit code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def ssh_runner(host: str, key: Path | None) -> Runner:
    """The one runner of every remote call: ssh without prompts (BatchMode) and without a TTY; the command is one
    argument for the remote shell; stdin from bytes or a file, stdout captured or into a file."""
    def run(command: str, *, stdin: bytes | Path | None = None, stdout_to: Path | None = None,
            timeout: float = SSH_TIMEOUT_S) -> RemoteResult:
        argv = ["ssh", *(["-i", str(key)] if key else []), "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=20", host, command]
        with contextlib.ExitStack() as stack:
            kwargs: dict[str, Any] = {"stderr": subprocess.PIPE, "timeout": timeout, "check": False}
            if isinstance(stdin, bytes):
                kwargs["input"] = stdin
            elif stdin is not None:
                kwargs["stdin"] = stack.enter_context(open(stdin, "rb"))
            else:
                kwargs["stdin"] = subprocess.DEVNULL
            kwargs["stdout"] = stack.enter_context(open(stdout_to, "wb")) if stdout_to else subprocess.PIPE
            try:
                proc = subprocess.run(argv, **kwargs)  # pylint: disable=subprocess-run-check
            except (OSError, subprocess.TimeoutExpired) as exc:
                return RemoteResult(255, b"", f"ssh did not run: {type(exc).__name__}")
        return RemoteResult(proc.returncode, proc.stdout if isinstance(proc.stdout, bytes) else b"",
                            (proc.stderr or b"").decode("utf-8", "replace"))
    return run


def run_backup_local(out: Callable[[str], Any] = print) -> bool:
    """scripts/ops/backup_local.py --now (C-11) with a --keep that rotates nothing away; True when it worked. Only its
    BACKUP_ and ROTATION lines are shown."""
    command = [sys.executable, str(ROOT / "scripts" / "ops" / "backup_local.py"), "--now", "--keep",
               str(BACKUP_KEEP_ALL)]
    try:
        proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out(f"BACKUP backup_local.py did not run ({type(exc).__name__})")
        return False
    for line in (proc.stdout or "").splitlines():
        if line.startswith(("BACKUP_", "ROTATION")):
            out(line)
    if proc.returncode != 0:
        out(f"BACKUP backup_local.py --now failed (exit {proc.returncode})")
        return False
    return True


def _worker_running() -> bool:
    from src.core import agent_lock  # pylint: disable=import-outside-toplevel

    return agent_lock.is_locked()


def valid_user(user_id: str) -> bool:
    return bool(user_id) and user_id not in (".", "..") and not any(ch in user_id for ch in "/\\:*?\"<>|")


def make_probe(key: str) -> tuple[str, str]:
    """A random 16-byte probe encrypted with key, and the sha256 of its plain bytes (container_checks)."""
    plain = secrets.token_bytes(16)
    fernet = fernet_for(key)
    if fernet is None:
        raise Refused(EXIT_USAGE, "JARVIS_ENCRYPTION_KEY is not a valid Fernet key")
    return fernet.encrypt(plain).decode("ascii"), hashlib.sha256(plain).hexdigest()


class Context:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """One run: the PC data root, the owner, the key, the VPS target and the injected runner, backup and worker check.
    (A plain class: tests load this script by path, where a dataclass cannot resolve its module.)"""

    def __init__(self, *, root: Path, user: str, key: str, host: str, runner: Runner, backup: Callable[[], bool],
                 worker_running: Callable[[], bool], out: Callable[[str], Any] = print, dry_run: bool = False,
                 stamp: str | None = None, work_parent: Path | None = None) -> None:
        self.root, self.user, self.key, self.host = root, user, key, host
        self.runner, self.backup, self.worker_running = runner, backup, worker_running
        self.out, self.dry_run = out, dry_run
        self.stamp = stamp or time.strftime(STAMP_FORMAT)  # the .pre-<ts> of every target this run sets aside
        self.work_parent = work_parent  # where the temporary staging folder goes (default: the system temp folder)


@contextlib.contextmanager
def work_dir(ctx: Context) -> Iterator[Path]:
    """This run's own temporary folder (staging copy and archive), removed at the end: the one thing the tool deletes,
    because it holds copies of private data."""
    path = Path(tempfile.mkdtemp(prefix=f"jarvis-migrate-{ctx.stamp}-", dir=ctx.work_parent))
    try:
        yield path
    finally:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            ctx.out(f"WARNING the temporary folder {path} was not removed ({type(exc).__name__}): remove it by hand "
                    "(it holds copies of private data)")


def run_program(ctx: Context, entry: str, args: dict[str, Any], *,
                stdout_to: Path | None = None) -> tuple[RemoteResult, list[dict[str, Any]]]:
    """One container program through the runner; its JSON rows (stderr for 'pack', else stdout)."""
    payload = json.dumps({"program": container_program(entry), "args": args}).encode("utf-8")
    result = ctx.runner(PROGRAM_COMMAND, stdin=payload, stdout_to=stdout_to)
    text = result.err if args.get("rows_to") == "stderr" else result.out.decode("utf-8", "replace")
    return result, env_remote.json_rows(text)


def outcome(result: RemoteResult, rows: list[dict[str, Any]], step: str) -> tuple[int, str]:
    """(exit code, message) of a container program: its last result row, else ssh's or compose's last stderr line."""
    final = next((row for row in reversed(rows) if "result" in row), None)
    if final is None:
        return EXIT_FAILED, f"{step} failed (exit {result.code}): {env_remote.failure_line(result.err)}"
    if final.get("result") == "ok" and result.code == 0:
        return EXIT_OK, ""
    if final.get("result") == "refused":
        code = final.get("code") if final.get("code") in (EXIT_RUNNING, EXIT_REFUSED) else EXIT_REFUSED
        return code, REASONS.get(str(final.get("reason")), f"{step} refused")
    return EXIT_FAILED, f"{step} failed ({final.get('error') or 'exit ' + str(result.code)})"


def row_value(rows: list[dict[str, Any]], name: str) -> Any:
    return next((row[name] for row in rows if name in row), None)


def running_agents(stdout: bytes) -> list[str]:
    """The running containers of the services agent / agent-svc in docker ps's 'name|service' lines."""
    running = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        name, _, service = line.strip().partition("|")
        if name in AGENT_CONTAINERS or service in AGENT_SERVICES:
            running.append(name or service)
    return running


def check_vps_stopped(ctx: Context) -> int:
    result = ctx.runner(PS_COMMAND)
    if result.code != 0:
        ctx.out(f"FAILED could not list the VPS containers (exit {result.code}): "
                f"{env_remote.failure_line(result.err)}")
        return EXIT_FAILED
    running = running_agents(result.out)
    if running:
        ctx.out(f"REFUSED running on the VPS: {', '.join(running)}; stop them first ('docker compose -p "
                f"{COMPOSE_PROJECT} stop agent agent-svc' in {VPS_COMPOSE_DIR}); nothing was changed")
        return EXIT_RUNNING
    ctx.out("CHECK no agent or agent-svc container runs on the VPS")
    return EXIT_OK


# ----------------------------------------------------------------------------------------------- reports


def counts_of(inv: dict[str, Any]) -> dict[str, Any]:
    """The comparable counts of an inventory (JSON-shaped, so both sides compare the same way)."""
    memory, chroma = inv.get("memory") or {}, inv.get("chroma") or {}
    counts: dict[str, Any] = {
        "memory": [memory.get("conversations"), memory.get("messages")] if memory.get("present") else None,
        "chroma": [chroma.get("documents"), dict(sorted((chroma.get("collections") or {}).items()))]
        if chroma.get("present") else None,
        "state files": sorted(inv.get("state") or []),
    }
    for rel, report in sorted((inv.get("sqlite") or {}).items()):
        counts[rel] = report.get("tables", report.get("error")) if report.get("present") else None
    for tree, number in sorted((inv.get("trees") or {}).items()):
        counts[tree] = number
    return counts


def describe(name: str, value: Any) -> str:
    if value is None:
        return "absent"
    if name == "memory":
        return f"{value[0]} conversations / {value[1]} messages"
    if name == "chroma":
        return f"{value[0]} documents" + "".join(f", {key} {number}" for key, number in value[1].items())
    if name == "state files":
        return ", ".join(rel.split("/")[-1] for rel in value) or "none"
    if isinstance(value, dict):
        return ", ".join(f"{table} {rows}" for table, rows in value.items()) or "no tables"
    if isinstance(value, int):
        return f"{value} file(s)"
    return str(value)


def compare(left: dict[str, Any], right: dict[str, Any], left_label: str, right_label: str, *, hashes: bool,
            skip_hash: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """(lines, problems) comparing two inventories: every file's sha256 (hashes) and the counts."""
    lines: list[str] = []
    problems: list[str] = []
    if hashes:
        left_files, right_files = left.get("files") or {}, right.get("files") or {}
        same = 0
        for rel in sorted(set(left_files) | set(right_files)):
            if rel in skip_hash and rel in left_files and rel in right_files:
                lines.append(f"{rel}: documents sealed after the copy, so its sha256 differs by design")
            elif rel not in right_files:
                problems.append(f"{rel}: missing on {right_label}")
            elif rel not in left_files:
                problems.append(f"{rel}: only on {right_label}")
            elif left_files[rel][0] != right_files[rel][0]:
                problems.append(f"{rel}: sha256 differs ({left_label} {left_files[rel][0][:16]}, {right_label} "
                                f"{right_files[rel][0][:16]})")
            else:
                same += 1
        lines.append(f"files: {same} of {len(left_files)} with the same sha256 on {left_label} and {right_label}")
    left_counts, right_counts = counts_of(left), counts_of(right)
    for name in sorted(set(left_counts) | set(right_counts)):
        a, b = left_counts.get(name), right_counts.get(name)
        if a == b:
            lines.append(f"{name}: {describe(name, a)} on both sides")
        else:
            problems.append(f"{name}: {left_label} {describe(name, a)}, {right_label} {describe(name, b)}")
    return lines, problems


def decrypt_problems(inv: dict[str, Any], label: str, *, sealed_only: bool,
                     checks: dict[str, Any] | None = None) -> list[str]:
    """What keeps label's side from reading its data: the memory file does not decrypt, Chroma documents sealed with
    another key (sealed_only: also plain ones, the VPS rule of C-124), the container's user or key differs."""
    problems = []
    if checks is not None:
        if checks.get("key_matches") is False:
            problems.append(f"{label}: {REASONS['key']}")
        if checks.get("user_matches") is False:
            problems.append(f"{label}: {REASONS['user']}")
    memory = inv.get("memory") or {}
    if memory.get("present") and memory.get("decrypt") not in ("ok", "empty", "plain"):
        problems.append(f"{label}: the memory file does not decrypt with JARVIS_ENCRYPTION_KEY")
    chroma = inv.get("chroma") or {}
    if chroma.get("present"):
        if chroma.get("error"):
            problems.append(f"{label}: chroma_db: {chroma['error']}")
        if chroma.get("decrypt_failed"):
            problems.append(f"{label}: {chroma['decrypt_failed']} Chroma document(s) do not decrypt with "
                            "JARVIS_ENCRYPTION_KEY")
        if sealed_only and chroma.get("plain"):
            problems.append(f"{label}: {chroma['plain']} Chroma document(s) are plain text (C-124 seals every "
                            "document on the VPS)")
    return problems


def report_inventory(out: Callable[[str], Any], label: str, inv: dict[str, Any], *, files: bool = True) -> None:
    """The sha256 of every file (files) and the counts of an inventory; never a content."""
    if files:
        for rel, (digest, size) in sorted((inv.get("files") or {}).items()):
            out(f"{label} {rel}  sha256 {digest}  {size} bytes")
    memory = inv.get("memory") or {}
    if memory.get("present"):
        out(f"{label} memory: {memory.get('conversations')} conversations / {memory.get('messages')} messages "
            f"(decrypt {memory.get('decrypt')})")
    for rel, report in sorted((inv.get("sqlite") or {}).items()):
        out(f"{label} {rel}: {describe(rel, report.get('tables', report.get('error')))}")
    chroma = inv.get("chroma") or {}
    if chroma.get("present"):
        if chroma.get("error"):
            out(f"{label} chroma: {chroma['error']}")
        else:
            out(f"{label} chroma: {chroma['documents']} documents; sealed {chroma['sealed']} (decrypt ok "
                f"{chroma['decrypt_ok']}, failed {chroma['decrypt_failed']}), plain {chroma['plain']}")
    for tree, number in sorted((inv.get("trees") or {}).items()):
        if number:
            out(f"{label} {tree}: {number} file(s)")
    if inv.get("state"):
        out(f"{label} state files: {describe('state files', inv['state'])}")


def local_refusal(ctx: Context, *, require_stopped_worker: bool, source: bool) -> tuple[int, str] | None:
    """A reason the PC side refuses before anything runs: the PC worker runs (require_stopped_worker), and for the PC
    as the source (source): per-job memory files are left, the owner memory or Chroma cannot be read with the key."""
    if require_stopped_worker and ctx.worker_running():
        return EXIT_RUNNING, ("the PC brain (agent worker) holds run/agent.lock: stop it first "
                              "(scripts/agent_ctl.py stop); this copy runs with both brains stopped")
    if not source:
        return None
    conversations = ctx.root / "conversations"
    if conversations.is_dir():
        unmerged = [p.name for p in sorted(conversations.glob(f"*{SUFFIX}"))
                    if p.is_file() and p.name != f"{ctx.user}{SUFFIX}"]
        if unmerged:
            return EXIT_REFUSED, (f"{len(unmerged)} per-job memory file(s) are not merged into {ctx.user}{SUFFIX}: "
                                  "run --merge-local first")
    memory = memory_report(str(conversations / f"{ctx.user}{SUFFIX}"), ctx.key)
    if memory.get("present") and memory.get("decrypt") == "failed":
        return EXIT_REFUSED, f"conversations/{ctx.user}{SUFFIX} does not decrypt with JARVIS_ENCRYPTION_KEY"
    chroma = chroma_report(str(ctx.root / CHROMA_DIR / CHROMA_DB_FILE), ctx.key)
    if chroma.get("present") and chroma.get("error"):
        return EXIT_REFUSED, f"chroma_db/{CHROMA_DB_FILE}: {chroma['error']}"
    if chroma.get("decrypt_failed"):
        return EXIT_REFUSED, (f"{chroma['decrypt_failed']} Chroma document(s) are sealed with another key than "
                              "JARVIS_ENCRYPTION_KEY")
    return None


def undo_hint(ctx: Context, renamed: list[list[str]], where: str) -> None:
    if renamed:
        ctx.out(f"NOTHING DELETED: {len(renamed)} earlier {where} target(s) are kept as *{PRE_MARK}{ctx.stamp} "
                "(rename them back to undo; the RUNBOOK's data restore)")


# ----------------------------------------------------------------------------------------------- --pre / --final


def push(ctx: Context, *, final: bool) -> int:
    """--pre / --final: the PC data set to the VPS (module docstring, steps 1-6)."""
    mode = "--final" if final else "--pre"
    out = ctx.out
    refusal = local_refusal(ctx, require_stopped_worker=final, source=True)
    rels = data_set(str(ctx.root), ctx.user)
    plan = rename_plan(rels)
    if ctx.dry_run:
        out(f"DRY RUN {mode}: PC {ctx.root} -> VPS {ctx.host} (compose project {COMPOSE_PROJECT}); nothing is "
            "contacted, backed up or changed")
        if refusal:
            out(f"  a real run would refuse: {refusal[1]}")
        out(f"  1. ssh: {PS_COMMAND}   (refuses while jarvis-agent or jarvis-agent-svc runs)")
        out(f"  2. backup_local.py --now --keep {BACKUP_KEEP_ALL}")
        out(f"  3. stage {len(rels)} file(s) on the PC (SQLite online copies, state files under their lock), seal "
            "Chroma's documents in the copy, build the tar:")
        report_inventory(out, "     PC", inventory(str(ctx.root), rels, ctx.user, ctx.key))
        out(f"  4. ssh: {PS_COMMAND} again, then ssh: {PROGRAM_COMMAND}   ('prepare': user, key probe, "
            f"chroma_db/.jarvis.lock; renames to *{PRE_MARK}{ctx.stamp}: files {plan['files']}, trees "
            f"{plan['trees']}, every entry of {plan['children']})")
        out(f"  5. ssh: {EXTRACT_COMMAND}  < agent-data.tgz   (tar's exit code checked)")
        out(f"  6. ssh: {PROGRAM_COMMAND}   ('inventory': sha256 and counts compared, decrypted in the container)")
        return refusal[0] if refusal else EXIT_OK
    if refusal:
        out(f"REFUSED {refusal[1]}; nothing was changed")
        return refusal[0]
    if not rels:
        out(f"REFUSED no data to copy under {ctx.root} (wrong --root?); nothing was changed")
        return EXIT_REFUSED
    if not final:
        out("NOTE --pre is a preliminary copy: the PC brain may still write; --final copies again with it stopped")
    code = check_vps_stopped(ctx)
    if code:
        return code
    if not ctx.backup():
        out("REFUSED backup_local.py --now did not work; nothing was copied")
        return EXIT_FAILED
    with work_dir(ctx) as work:
        staging = work / "data"
        try:
            stage_copy(str(ctx.root), rels, str(staging))
            chroma_db = staging / CHROMA_DIR / CHROMA_DB_FILE
            seal = seal_chroma(str(chroma_db), ctx.key) if chroma_db.is_file() else None
        except (OSError, sqlite3.Error, TimeoutError, ValueError) as exc:
            out(f"REFUSED the staging copy failed ({type(exc).__name__}: {exc}); nothing was changed on the VPS")
            return EXIT_REFUSED
        staged = inventory(str(staging), rels, ctx.user, ctx.key)
        report_inventory(out, "STAGED", staged)
        if seal is not None:
            out(f"STAGED chroma: {seal['sealed']} document(s) sealed now (C-124), {seal['already']} were sealed; "
                f"index rows {seal['index']}, queue records {seal['queue']}")
        if (staged.get("memory") or {}).get("decrypt") == "plain":
            out(f"WARNING conversations/{ctx.user}{SUFFIX} is a legacy plain JSON file: the VPS brain reads it the way "
                "memory_store does and encrypts it at its next save")
        problems = decrypt_problems(staged, "the staged copy", sealed_only=True)
        if problems:
            out("REFUSED " + "; ".join(problems) + "; nothing was changed on the VPS")
            return EXIT_REFUSED
        archive = work / "agent-data.tgz"
        with open(archive, "wb") as handle:
            write_tar(handle, str(staging), rels)
        out(f"ARCHIVE {len(rels)} file(s), {archive.stat().st_size} bytes, sha256 {sha256_file(str(archive))}")
        code = check_vps_stopped(ctx)  # again, right before the first change
        if code:
            return code
        probe, digest = make_probe(ctx.key)
        common = {"root": CONTAINER_ROOT, "user": ctx.user, "probe": probe, "probe_sha256": digest}
        result, rows = run_program(ctx, "container_prepare", {**common, "plan": plan, "stamp": ctx.stamp})
        renamed = [row["renamed"] for row in rows if "renamed" in row]
        for old, new in renamed:
            out(f"VPS kept aside: {old} -> {new}")
        code, message = outcome(result, rows, "prepare")
        if code:
            out(f"{'REFUSED' if code in (EXIT_RUNNING, EXIT_REFUSED) else 'FAILED'} {message}")
            undo_hint(ctx, renamed, "VPS")
            return code
        result = ctx.runner(EXTRACT_COMMAND, stdin=archive)
        if result.code != 0:
            out(f"FAILED tar exit {result.code}: {env_remote.failure_line(result.err)}")
            undo_hint(ctx, renamed, "VPS")
            return EXIT_FAILED
        out("VPS extract: tar exit 0")
        result, rows = run_program(ctx, "container_inventory", {**common, "files": rels})
        code, message = outcome(result, rows, "inventory")
        remote = row_value(rows, "inventory")
        if code or not isinstance(remote, dict):
            out(f"FAILED {message or 'the inventory sent no report'}")
            undo_hint(ctx, renamed, "VPS")
            return code or EXIT_FAILED
        lines, problems = compare(staged, remote, "staged", "VPS", hashes=True)
        problems += decrypt_problems(remote, "VPS", sealed_only=True, checks=row_value(rows, "checks"))
        for line in lines:
            out(f"VERIFY {line}")
        chroma = remote.get("chroma") or {}
        if chroma.get("present") and not chroma.get("error"):
            out(f"VERIFY chroma: {chroma['decrypt_ok']} of {chroma['documents']} documents decrypt in the container")
        if (remote.get("memory") or {}).get("present"):
            out(f"VERIFY memory: decrypt in the container {remote['memory'].get('decrypt')}")
        for problem in problems:
            out(f"DIFFERS {problem}")
        undo_hint(ctx, renamed, "VPS")
        out(f"RESULT {mode}: {'copied and verified' if not problems else 'copied, but the check found differences'}")
        return EXIT_FAILED if problems else EXIT_OK


# ----------------------------------------------------------------------------------------------- --reverse


def safe_extract(archive: Path, dest: Path) -> list[str]:
    """Extracts a VPS archive into dest (a new folder) with tarfile's 'data' filter, after checking every member: a
    plain relative name under conversations/, chroma_db/ or data/ (never '/', './', '..', a backslash or a drive),
    only files and folders. Returns the file names; Refused (4) on anything else, with nothing extracted."""
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        files = []
        for member in members:
            name = member.name
            parts = name.split("/")
            if (not name or name.startswith(("/", "./")) or "\\" in name or ":" in name
                    or any(part in ("", ".", "..") for part in parts) or parts[0] not in ARCHIVE_TOPS
                    or not (member.isfile() or member.isdir())):
                raise Refused(EXIT_REFUSED, f"the VPS archive holds an unsafe member ({name[:80]!r})")
            if member.isfile():
                files.append(name)
        tar.extractall(dest, members=members, filter="data")
    return sorted(files)


def move_into_place(staging: Path, root: Path, rels: list[str], moved: list[str], made: list[Path]) -> None:
    """Moves each staged file to its place under root, noting it in moved and every folder it had to make in made;
    never over an existing file."""
    for rel in rels:
        target = root.joinpath(*rel.split("/"))
        if os.path.lexists(target):
            raise Refused(EXIT_FAILED, f"{rel} exists although it was set aside; nothing was overwritten")
        missing, folder = [], target.parent
        while not folder.exists():
            missing.append(folder)
            folder = folder.parent
        for folder in reversed(missing):
            folder.mkdir()
            made.append(folder)
        shutil.move(str(staging.joinpath(*rel.split("/"))), str(target))
        moved.append(rel)


def restore_pc(ctx: Context, staging: Path, moved: list[str], made: list[Path], renamed: list[list[str]]) -> bool:
    """Puts the PC back as it was after a failed --reverse: the files already moved in go back to the staging folder,
    the folders this run made (empty again) go, and every target set aside gets its name back (newest first). No
    earlier file is touched; False when a step fails (the state is then printed and stays for a person to finish)."""
    try:
        for rel in reversed(moved):
            source = staging.joinpath(*rel.split("/"))
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(ctx.root.joinpath(*rel.split("/"))), str(source))
        for folder in reversed(made):
            os.rmdir(folder)  # only an empty folder this run made; raises otherwise
        for old, new in reversed(renamed):
            old_path = ctx.root.joinpath(*old.split("/"))
            if os.path.lexists(old_path):
                raise FileExistsError(old)
            os.rename(ctx.root.joinpath(*new.split("/")), old_path)
    except OSError as exc:
        ctx.out(f"FAILED putting the PC back ({type(exc).__name__}: {exc}); the earlier files are kept as "
                f"*{PRE_MARK}{ctx.stamp}: rename them back by hand")
        return False
    ctx.out(f"RESTORED the PC as it was ({len(renamed)} name(s) given back, {len(moved)} copied file(s) taken out)")
    return True


def pull(ctx: Context) -> int:
    """--reverse ('brain pc'): the VPS data set to the PC (module docstring)."""
    out = ctx.out
    refusal = local_refusal(ctx, require_stopped_worker=True, source=False)
    if ctx.dry_run:
        rels = data_set(str(ctx.root), ctx.user)
        out(f"DRY RUN --reverse: VPS {ctx.host} -> PC {ctx.root}; nothing is contacted, backed up or changed")
        if refusal:
            out(f"  a real run would refuse: {refusal[1]}")
        out(f"  1. ssh: {PS_COMMAND}   (refuses while jarvis-agent or jarvis-agent-svc runs)")
        out(f"  2. backup_local.py --now --keep {BACKUP_KEEP_ALL}")
        out(f"  3. ssh: {PROGRAM_COMMAND}   ('pack': user, key probe, chroma_db/.jarvis.lock, online copies in the "
            "container's /tmp, the tar on stdout, sha256 and counts on stderr)")
        out("  4. check every member, extract into a PC staging folder, seal any plain Chroma document, compare sha256 "
            "and counts with the VPS's")
        out(f"  5. rename the PC targets the copy brings to *{PRE_MARK}{ctx.stamp} and move the copy in; the PC data "
            f"set now ({len(rels)} file(s)):")
        report_inventory(out, "     PC", inventory(str(ctx.root), rels, ctx.user, ctx.key), files=False)
        return refusal[0] if refusal else EXIT_OK
    if refusal:
        out(f"REFUSED {refusal[1]}; nothing was changed")
        return refusal[0]
    code = check_vps_stopped(ctx)
    if code:
        return code
    if not ctx.backup():
        out("REFUSED backup_local.py --now did not work; nothing was copied")
        return EXIT_FAILED
    with work_dir(ctx) as work:
        archive = work / "agent-data.tgz"
        probe, digest = make_probe(ctx.key)
        result, rows = run_program(ctx, "container_pack", {"root": CONTAINER_ROOT, "user": ctx.user, "probe": probe,
                                                           "probe_sha256": digest, "rows_to": "stderr"},
                                   stdout_to=archive)
        code, message = outcome(result, rows, "pack")
        remote = row_value(rows, "inventory")
        if code or not isinstance(remote, dict):
            out(f"{'REFUSED' if code in (EXIT_RUNNING, EXIT_REFUSED) else 'FAILED'} "
                f"{message or 'the pack sent no report'}; nothing was changed on the PC")
            return code or EXIT_FAILED
        report_inventory(out, "VPS", remote)
        staging = work / "data"
        try:
            extracted = safe_extract(archive, staging)
            if extracted != sorted(remote.get("files") or {}):
                raise Refused(EXIT_FAILED, "the archive does not hold exactly the files the VPS reported")
            chroma_db = staging / CHROMA_DIR / CHROMA_DB_FILE
            seal = seal_chroma(str(chroma_db), ctx.key) if chroma_db.is_file() else None
        except Refused as exc:
            out(f"{'REFUSED' if exc.code == EXIT_REFUSED else 'FAILED'} {exc}; nothing was changed on the PC")
            return exc.code
        except (OSError, tarfile.TarError, sqlite3.Error, ValueError) as exc:
            out(f"FAILED the VPS copy could not be unpacked ({type(exc).__name__}: {exc}); nothing was changed on "
                "the PC")
            return EXIT_FAILED
        staged = inventory(str(staging), extracted, ctx.user, ctx.key)
        changed = (CHROMA_DIR + "/" + CHROMA_DB_FILE,) if seal and (seal["sealed"] or seal["queue"]) else ()
        lines, problems = compare(remote, staged, "VPS", "PC copy", hashes=True, skip_hash=changed)
        problems += decrypt_problems(staged, "PC copy", sealed_only=False, checks=row_value(rows, "checks"))
        for line in lines:
            out(f"VERIFY {line}")
        if problems:
            for problem in problems:
                out(f"DIFFERS {problem}")
            out("FAILED the VPS copy does not match its report; nothing was changed on the PC")
            return EXIT_FAILED
        renamed: list[list[str]] = []
        moved: list[str] = []
        made: list[Path] = []

        def kept_aside(pair: list[str]) -> None:
            renamed.append(pair)
            out(f"PC kept aside: {pair[0]} -> {pair[1]}")

        try:
            rename_aside(str(ctx.root), rename_plan(extracted), ctx.stamp, kept_aside)
            move_into_place(staging, ctx.root, extracted, moved, made)
        except (OSError, Refused) as exc:
            out(f"FAILED the PC files could not be replaced ({type(exc).__name__}: {exc})")
            if not restore_pc(ctx, staging, moved, made, renamed):
                undo_hint(ctx, renamed, "PC")
            return EXIT_FAILED
        final = inventory(str(ctx.root), extracted, ctx.user, ctx.key)
        lines, problems = compare(staged, final, "PC copy", "PC", hashes=True)
        for problem in problems:
            out(f"DIFFERS {problem}")
        undo_hint(ctx, renamed, "PC")
        out(f"RESULT --reverse: {'copied and verified' if not problems else 'copied, but the check found differences'}"
            + (f"; {seal['sealed']} plain Chroma document(s) sealed on the way" if seal and seal["sealed"] else ""))
        return EXIT_FAILED if problems else EXIT_OK


# ----------------------------------------------------------------------------------------------- --verify


def verify(ctx: Context) -> int:
    """--verify: the counts of both sides (and the container's decrypt checks); exit 1 on a difference."""
    out = ctx.out
    rels = data_set(str(ctx.root), ctx.user)
    local = inventory(str(ctx.root), rels, ctx.user, ctx.key)
    if ctx.dry_run:
        out(f"DRY RUN --verify: PC {ctx.root} and VPS {ctx.host}; nothing is contacted or changed")
        out(f"  1. ssh: {PS_COMMAND}   (refuses while jarvis-agent or jarvis-agent-svc runs)")
        out(f"  2. ssh: {PROGRAM_COMMAND}   ('inventory' of the VPS data set: counts, decrypted in the container)")
        report_inventory(out, "  PC", local, files=False)
        return EXIT_OK
    code = check_vps_stopped(ctx)
    if code:
        return code
    probe, digest = make_probe(ctx.key)
    result, rows = run_program(ctx, "container_inventory", {"root": CONTAINER_ROOT, "user": ctx.user, "files": None,
                                                            "probe": probe, "probe_sha256": digest})
    code, message = outcome(result, rows, "inventory")
    remote = row_value(rows, "inventory")
    if code or not isinstance(remote, dict):
        out(f"{'REFUSED' if code in (EXIT_RUNNING, EXIT_REFUSED) else 'FAILED'} "
            f"{message or 'the inventory sent no report'}")
        return code or EXIT_FAILED
    lines, problems = compare(local, remote, "PC", "VPS", hashes=False)
    problems += decrypt_problems(remote, "VPS", sealed_only=True, checks=row_value(rows, "checks"))
    problems += decrypt_problems(local, "PC", sealed_only=False)
    for line in lines:
        out(f"VERIFY {line}")
    chroma = remote.get("chroma") or {}
    if chroma.get("present") and not chroma.get("error"):
        out(f"VERIFY chroma: {chroma['decrypt_ok']} of {chroma['documents']} documents decrypt in the container")
    for problem in problems:
        out(f"DIFFERS {problem}")
    out(f"RESULT --verify: {'the counts are equal on both sides' if not problems else 'the two sides differ'}")
    return EXIT_FAILED if problems else EXIT_OK


# ----------------------------------------------------------------------------------------------- --merge-local


def merge_local(directory: Path, user_id: str, encrypt: Callable[[str], bytes], decrypt: Callable[[bytes], str], *,
                dry_run: bool = False, now: float | None = None, out: Callable[[str], Any] = print) -> int:
    """--merge-local (step 1, stage V3): scripts/memory/migrate_user_memory.migrate, reused, with every merged per-job
    file renamed <name>.pre-<ts> where it is (the old target kept as a .pre- copy too)."""
    return migrate_user_memory.migrate(directory, user_id, encrypt, decrypt, dry_run=dry_run, originals="rename",
                                       now=now, out=out)


# ----------------------------------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JARVIS data migration between the PC and the VPS brain (C-129)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--merge-local", action="store_true", help="merge every *_memory.json into the owner's file")
    modes.add_argument("--pre", action="store_true", help="stage V4: copy the PC data to the VPS (preliminary)")
    modes.add_argument("--final", action="store_true", help="cutover: copy the PC data to the VPS, both brains stopped")
    modes.add_argument("--reverse", action="store_true", help="'brain pc': copy the VPS data to the PC")
    parser.add_argument("--verify", action="store_true",
                        help="compare the counts of both sides (alone or after a copy)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no ssh, no backup, nothing written")
    parser.add_argument("--user", default=None, help="the memory identity (default JARVIS_USER_ID, else matloob)")
    parser.add_argument("--dir", default=None, help="--merge-local: the memory folder (default conversations/)")
    parser.add_argument("--root", default=None, help="the PC data root (default: the repo)")
    parser.add_argument("--host", default=None, help="ssh target (default JARVIS_VPS_SSH, JARVIS_VPS_HOST, else "
                                                     f"{DEFAULT_HOST})")
    parser.add_argument("--ssh-key", default=None, help="ssh identity file (default JARVIS_SSH_KEY)")
    args = parser.parse_args(argv)
    if not (args.merge_local or args.pre or args.final or args.reverse or args.verify):
        parser.error("choose --merge-local, --pre, --final, --reverse or --verify")
    if args.merge_local and args.verify:
        parser.error("--verify compares the PC with the VPS; it does not go with --merge-local")
    return args


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         backup: Callable[[], bool] | None = None) -> int:
    args = parse_args(argv)
    # loads the PC .env (override=False)
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

    user = (args.user or config.user_id or "").strip()
    if not valid_user(user):
        print("REFUSED no valid user id (--user or JARVIS_USER_ID)")
        return EXIT_USAGE
    key = (os.getenv("JARVIS_ENCRYPTION_KEY") or "").strip()
    if not key:
        print("REFUSED JARVIS_ENCRYPTION_KEY is not set: the memory could not be read or written (jarvis_crypto would "
              "invent a session key)")
        return EXIT_USAGE
    fernet = fernet_for(key)
    if fernet is None:
        print("REFUSED JARVIS_ENCRYPTION_KEY is not a valid Fernet key")
        return EXIT_USAGE
    root = Path(args.root).resolve() if args.root else Path(config.project_root)
    if not root.is_dir():
        print(f"REFUSED {root} is not a folder")
        return EXIT_USAGE
    if args.merge_local:
        directory = Path(args.dir) if args.dir else (root / "conversations" if args.root
                                                     else Path(config.conversations_dir))
        if not args.dry_run and _worker_running():
            print("REFUSED the agent worker is running; stop it first (scripts/agent_ctl.py stop)")
            return EXIT_RUNNING
        if not directory.is_dir():
            print(f"REFUSED {directory} is not a folder")
            return EXIT_USAGE
        # Fernet with JARVIS_ENCRYPTION_KEY, exactly what jarvis_crypto does (without its class-level key cache)
        return merge_local(directory, user, lambda text: fernet.encrypt(text.encode("utf-8")),
                           lambda token: fernet.decrypt(token).decode("utf-8"), dry_run=args.dry_run)
    host = (args.host or os.getenv("JARVIS_VPS_SSH") or os.getenv("JARVIS_VPS_HOST") or DEFAULT_HOST).strip()
    ssh_key = Path(os.path.expanduser(args.ssh_key)) if args.ssh_key else env_remote.default_ssh_key()
    ctx = Context(root=root, user=user, key=key, host=host,
                  runner=runner or ssh_runner(host, ssh_key if ssh_key.is_file() else None),
                  backup=backup or run_backup_local, worker_running=_worker_running, dry_run=args.dry_run)
    try:
        code = EXIT_OK
        if args.pre or args.final:
            code = push(ctx, final=args.final)
        elif args.reverse:
            code = pull(ctx)
        if args.verify and code == EXIT_OK:
            code = verify(ctx)
        return code
    except Refused as exc:
        print(f"REFUSED {exc}")
        return exc.code
    except (OSError, sqlite3.Error, TimeoutError, ValueError) as exc:  # a PC file that cannot be read: its name only
        print(f"FAILED {type(exc).__name__}: {exc}")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
