"""scripts/ops/migrate_agent_data.py (plan C-129): --merge-local, --pre/--final, --reverse and --verify.

Temporary folders, a throwaway Fernet key and fake data only: the real conversations/, chroma_db/ and data/ are never
read, and nothing reaches ssh, docker or the VPS. Every remote call goes to FakeVps, the one runner the tool takes: a
temp folder stands for /app, docker ps answers from a list, the shipped container programs run in a 'python -I' child
(isolated from this repo, as in the agent image) with the container's env, and the tar extraction is checked and done
with tarfile, never over an existing file. The tool prints counts, sha256 and names only, never a text. The chromadb
children send no usage telemetry (turned off in their settings; posthog.capture is stubbed in them and checked).
"""
from __future__ import annotations

import ast
import builtins
import dis
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import types
from collections import Counter
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("migrate_agent_data", ROOT / "scripts" / "ops" / "migrate_agent_data.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

STAMP = "20261021-120000"
USER = "matloob"
MEMORY = f"conversations/{USER}_memory.json"
# Fake texts: none of them may ever reach the tool's output
TEXTS = ("SALAM-A", "JAWAB-A", "PURANA-B", "NAYA-B", "LEGACY-C", "MEMORY-ONE", "MEMORY-TWO", "MEMORY-THREE",
         "TASK-SECRET", "VOICE-BYTES")
DOCS = ["MEMORY-ONE about tea", "MEMORY-TWO about Lahore", "MEMORY-THREE about cricket"]
# Recorded in every container child: each deletion the shipped programs make (the audit events of os.remove/unlink,
# os.rmdir and shutil.rmtree), reported as one JSON row on stderr at exit.
AUDIT_PREFIX = (
    "import sys, json, atexit\n"
    "_deleted = []\n"
    "sys.addaudithook(lambda event, args: _deleted.append(str(args[0])) "
    "if event in ('os.remove', 'os.rmdir', 'shutil.rmtree') else None)\n"
    "atexit.register(lambda: sys.stderr.write(json.dumps({'deleted': _deleted}) + '\\n'))\n")


# ------------------------------------------------------------------------------------------------- fixtures


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def _conv(ts: str, *messages: dict) -> dict:
    return {"timestamp": ts, "messages": list(messages), "user_id": "x"}


def _msg(text: str, role: str = "user", **extra) -> dict:
    return {"role": role, "content": text, **extra}


def _encrypt(key: str, data) -> bytes:
    return Fernet(key.encode()).encrypt(json.dumps(data).encode())


def _crypto(key: str):
    fernet = Fernet(key.encode())
    return (lambda text: fernet.encrypt(text.encode() if isinstance(text, str) else text),
            lambda token: fernet.decrypt(token).decode())


def make_tasks_db(path: Path, titles) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")  # X-66
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT)")
    con.executemany("INSERT INTO tasks (title) VALUES (?)", [(title,) for title in titles])
    con.commit()
    con.close()


def make_chroma(path: Path, docs, *, extra_index_rows=()) -> None:
    """A chroma.sqlite3 laid out as chromadb 1.5.0 lays it out (the tables the tool reads and seals)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT NOT NULL, topic TEXT, UNIQUE (name));
        CREATE TABLE segments (id TEXT PRIMARY KEY, type TEXT NOT NULL, scope TEXT NOT NULL, topic TEXT,
                               collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL, embedding_id TEXT NOT NULL,
                                 seq_id BLOB NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                 UNIQUE (segment_id, embedding_id));
        CREATE TABLE embedding_metadata (id INTEGER REFERENCES embeddings(id), key TEXT NOT NULL, string_value TEXT,
                                         int_value INTEGER, float_value REAL, bool_value INTEGER,
                                         PRIMARY KEY (id, key));
        CREATE INDEX embedding_metadata_string_value ON embedding_metadata (key, string_value)
            WHERE string_value IS NOT NULL;
        CREATE TABLE embeddings_queue (seq_id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL
                                       DEFAULT CURRENT_TIMESTAMP, operation INTEGER NOT NULL, topic TEXT NOT NULL,
                                       id TEXT NOT NULL, vector BLOB, encoding TEXT, metadata TEXT);
        CREATE VIRTUAL TABLE embedding_fulltext_search USING fts5(string_value, tokenize='trigram');
        CREATE TABLE max_seq_id (segment_id TEXT PRIMARY KEY, seq_id INTEGER);
    """)
    con.execute("INSERT INTO collections VALUES ('col-1', 'jarvis_memory', NULL)")
    con.execute("INSERT INTO segments VALUES ('seg-vec', 'urn:chroma:segment/vector/hnsw-local-persisted', 'VECTOR', "
                "NULL, 'col-1')")
    con.execute("INSERT INTO segments VALUES ('seg-meta', 'urn:chroma:segment/metadata/sqlite', 'METADATA', NULL, "
                "'col-1')")
    for number, doc in enumerate(docs, 1):
        con.execute("INSERT INTO embeddings (id, segment_id, embedding_id, seq_id) VALUES (?, 'seg-meta', ?, ?)",
                    (number, f"e{number}", number))
        con.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
                    (number, doc))
        con.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'source', 'voice')", (number,))
        con.execute("INSERT INTO embedding_fulltext_search (rowid, string_value) VALUES (?, ?)", (number, doc))
        con.execute("INSERT INTO embeddings_queue (seq_id, operation, topic, id, metadata) VALUES (?, 0, 't', ?, ?)",
                    (number, f"e{number}", json.dumps({"chroma:document": doc, "source": "voice"})))
    for rowid, text in extra_index_rows:
        con.execute("INSERT INTO embedding_fulltext_search (rowid, string_value) VALUES (?, ?)", (rowid, text))
    con.execute("INSERT INTO max_seq_id VALUES ('seg-meta', ?)", (len(docs),))
    con.commit()
    con.close()


def make_pc(root: Path, key: str) -> Path:
    """A PC data root: the owner memory, tasks.db, state files, Chroma, the voice-ID store and settings, plus what must
    never travel (lock files, an earlier .pre- copy, logs)."""
    conversations = root / "conversations"
    conversations.mkdir(parents=True)
    (conversations / f"{USER}_memory.json").write_bytes(_encrypt(key, [
        _conv("2026-10-08T12:00:00", _msg("SALAM-A", id="a1"), _msg("JAWAB-A", "assistant", id="a2")),
        _conv("2026-10-09T09:00:00", _msg("NAYA-B", id="b1"), _msg("PURANA-B", "assistant", id="b2"))]))
    make_tasks_db(conversations / "tasks.db", ["TASK-SECRET one", "two", "three"])
    (conversations / "identity.json").write_text(json.dumps({"facts": ["likes tea"]}), "utf-8")
    (conversations / "reminders.json").write_text(json.dumps([{"id": "r1", "text": "call"}]), "utf-8")
    for name in ("identity.json.lock", "reminders.json.lock"):  # jarvis_filelock sidecars: never copied
        (conversations / name).write_bytes(b"")
    (conversations / f"{USER}_memory.json.pre-20261001-000000").write_bytes(b"an earlier merge")
    make_chroma(root / "chroma_db" / "chroma.sqlite3", DOCS)
    segment = root / "chroma_db" / "0a1b2c3d-segment"
    segment.mkdir()
    (segment / "header.bin").write_bytes(b"HNSW-HEADER")
    (segment / "data_level0.bin").write_bytes(b"\x00\x01VECTORS")
    (root / "chroma_db" / ".jarvis.lock").write_bytes(b"")
    identity = root / "data" / "identity"
    (identity / "samples").mkdir(parents=True)
    (identity / "master_embedding.npy").write_bytes(b"NPY-VOICE-BYTES")
    (identity / "samples" / "s1.wav").write_bytes(b"RIFF-VOICE-BYTES")
    (root / "data" / "settings").mkdir(parents=True)
    (root / "data" / "settings" / "agent_settings.json").write_text('{"persona": "jarvis"}', "utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "jarvis.log").write_text("LOG-STAYS-ON-THE-PC", "utf-8")
    return root


def make_vps_with_old_data(root: Path) -> Path:
    """The VPS volumes (/app) with older JARVIS data, an unrelated file and C-124's lock file."""
    conversations = root / "conversations"
    conversations.mkdir(parents=True)
    (conversations / f"{USER}_memory.json").write_bytes(b"OLD-VPS-MEMORY")
    (conversations / "tasks.db").write_bytes(b"OLD-TASKS-DB")
    (conversations / "tasks.db-wal").write_bytes(b"OLD-TASKS-WAL")
    (conversations / "delivery_ledger.json").write_bytes(b"VPS-ONLY-LEDGER")
    chroma = root / "chroma_db"
    (chroma / "old-segment").mkdir(parents=True)
    (chroma / "old-segment" / "header.bin").write_bytes(b"OLD-HNSW")
    (chroma / "chroma.sqlite3").write_bytes(b"OLD-CHROMA")
    (chroma / ".jarvis.lock").write_bytes(b"")
    (root / "data" / "identity").mkdir(parents=True)
    (root / "data" / "identity" / "old.npy").write_bytes(b"OLD-VOICE")
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    """Every data file under root (lock sidecars and SQLite side files left out: any reader of a WAL database makes
    the latter, and read_locked makes the former, as every jarvis_filelock user does)."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.endswith((".lock", "-wal", "-shm"))}


def assert_nothing_lost(before: dict[str, bytes], root: Path) -> None:
    left = Counter(path.read_bytes() for path in root.rglob("*") if path.is_file())
    for rel, data in before.items():
        assert left[data] > 0, f"the content of {rel} is gone"
        left[data] -= 1


class FakeVps:
    """The runner given to the tool in place of ssh (see the module docstring)."""

    def __init__(self, root: Path, key: str, tmp: Path, *, user: str = USER, running=()) -> None:
        self.root, self.running = root, list(running)
        self.calls: list[str] = []
        self.events: list[str] | None = None
        self.deleted: list[str] = []
        self.extracted: list[list[str]] = []
        tmp.mkdir(parents=True, exist_ok=True)
        self.env = {name: value for name, value in os.environ.items() if not name.startswith("PYTHON")}
        self.env.update({"JARVIS_ENCRYPTION_KEY": key, "JARVIS_USER_ID": user, "TMP": str(tmp), "TEMP": str(tmp),
                         "TMPDIR": str(tmp)})

    def _event(self, name: str) -> None:
        if self.events is not None:
            self.events.append(name)

    def __call__(self, command: str, *, stdin=None, stdout_to=None, timeout=None) -> mig.RemoteResult:
        self.calls.append(command)
        if command == mig.PS_COMMAND:
            self._event("ps")
            return mig.RemoteResult(0, "".join(f"{name}|{service}\n" for name, service in self.running).encode(), "")
        if command == mig.EXTRACT_COMMAND:
            self._event("extract")
            return self._extract(stdin.read_bytes() if isinstance(stdin, Path) else stdin)
        if command == mig.PROGRAM_COMMAND:
            payload = json.loads(stdin)
            self._event(payload["args"].get("rows_to") and "pack" or next(
                name for name in mig.CONTAINER_ENTRIES if f"_exit_code = {name}(ARGS)" in payload["program"]))
            assert payload["args"]["root"] == mig.CONTAINER_ROOT
            payload["args"]["root"] = str(self.root)
            proc = subprocess.run([sys.executable, "-I", "-c", AUDIT_PREFIX + mig.LOADER],
                                  input=json.dumps(payload).encode(), capture_output=True, env=self.env, timeout=180,
                                  check=False)
            err = proc.stderr.decode("utf-8", "replace")
            for row in mig.env_remote.json_rows(err):
                self.deleted.extend(row.get("deleted") or [])
            out = proc.stdout
            if stdout_to is not None:
                Path(stdout_to).write_bytes(out)
                out = b""
            return mig.RemoteResult(proc.returncode, out, err)
        raise AssertionError(f"unexpected remote command: {command}")

    def _extract(self, data: bytes) -> mig.RemoteResult:
        """GNU tar's job, checked: plain relative members only, and never over an existing file."""
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                if member.name in ("", ".") or member.name.startswith(("./", "/")) or ".." in member.name.split("/"):
                    return mig.RemoteResult(2, b"", f"tar: refused member {member.name}")
                if member.isfile() and (self.root / member.name).exists():
                    return mig.RemoteResult(2, b"", f"tar: would replace {member.name}")
            tar.extractall(self.root, members=members, filter="data")
        self.extracted.append(sorted(member.name for member in members))
        return mig.RemoteResult(0, b"", "")


def _ctx(pc_root: Path, vps: FakeVps, key: str, lines: list[str], *, worker: bool = False, dry_run: bool = False,
         work: Path | None = None, backup=None) -> mig.Context:
    def fake_backup() -> bool:
        if getattr(vps, "events", None) is not None:
            vps.events.append("backup")
        return True

    return mig.Context(root=pc_root, user=USER, key=key, host="test-vps", runner=vps, backup=backup or fake_backup,
                       worker_running=lambda: worker, out=lines.append, dry_run=dry_run, stamp=STAMP,
                       work_parent=work)


def _no_text(lines: list[str]) -> None:
    joined = "\n".join(lines)
    assert not any(text in joined for text in TEXTS), "a memory, task or voice content reached the output"


def _documents(db: Path, key: str) -> list[str]:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT string_value FROM embedding_metadata WHERE key = 'chroma:document' "
                           "ORDER BY id").fetchall()
    finally:
        con.close()
    fernet = Fernet(key.encode())
    return [fernet.decrypt(text[len(mig.ENC_PREFIX):].encode()).decode() if text.startswith(mig.ENC_PREFIX) else text
            for (text,) in rows]


def _rows(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        con.close()


# ------------------------------------------------------------------------------------------------- --merge-local


def test_merge_local_merges_by_message_id_renames_every_original_and_prints_counts_only(tmp_path, key):
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    (conversations / "matloob_memory.json").write_bytes(_encrypt(key, [
        _conv("2026-10-08T12:00:00", _msg("SALAM-A", id="a1"), _msg("JAWAB-A", "assistant", id="a2"))]))
    (conversations / "owner-1a2b_memory.json").write_bytes(_encrypt(key, [
        _conv("2026-10-02T10:00:00", _msg("PURANA-B")),
        _conv("2026-10-08T12:00:00", _msg("SALAM-A", id="a1")),  # the same id: kept once
        _conv("2026-10-09T09:00:00", _msg("NAYA-B", id="b1"))]))
    (conversations / "web-legacy_memory.json").write_text(json.dumps(  # a legacy plain JSON list is read too
        [_conv("2026-10-01T07:00:00", _msg("LEGACY-C"))]), "utf-8")
    (conversations / "tasks.db").write_bytes(b"not memory")
    old_target = (conversations / "matloob_memory.json").read_bytes()
    lines: list[str] = []
    encrypt, decrypt = _crypto(key)
    assert mig.merge_local(conversations, USER, encrypt, decrypt, dry_run=True, out=lines.append) == mig.EXIT_OK
    assert sorted(p.name for p in conversations.iterdir()) == ["matloob_memory.json", "owner-1a2b_memory.json",
                                                               "tasks.db", "web-legacy_memory.json"]  # dry run

    assert mig.merge_local(conversations, USER, encrypt, decrypt, now=0, out=lines.append) == mig.EXIT_OK

    merged = json.loads(Fernet(key.encode()).decrypt((conversations / "matloob_memory.json").read_bytes()))
    assert [m["content"] for c in merged for m in c["messages"]] == ["LEGACY-C", "PURANA-B", "SALAM-A", "JAWAB-A",
                                                                    "NAYA-B"]  # by save time, each message once
    names = sorted(p.name for p in conversations.iterdir())
    assert [n for n in names if n.endswith("_memory.json")] == ["matloob_memory.json"]  # the PC brain's one file
    kept = [n for n in names if ".pre-" in n]
    assert len(kept) == 3 and "tasks.db" in names  # nothing deleted: every original is kept as <name>.pre-<ts>
    assert (conversations / next(n for n in kept if n.startswith("matloob_"))).read_bytes() == old_target
    report = "\n".join(lines)
    assert "3 file(s): 5 conversations / 6 messages in, 1 duplicates dropped, 4 conversations / 5 messages" in report
    assert "renamed to <name>.pre-" in report
    _no_text(lines)


def test_merge_local_refuses_an_ambiguous_id_or_an_unreadable_file_and_changes_nothing(tmp_path, key):
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    (conversations / "a_memory.json").write_bytes(_encrypt(key, [_conv("2026-10-01T00:00:00", _msg("one", id="m"))]))
    (conversations / "b_memory.json").write_bytes(_encrypt(key, [_conv("2026-10-02T00:00:00", _msg("two", id="m"))]))
    before = snapshot(tmp_path)
    lines: list[str] = []
    encrypt, decrypt = _crypto(key)
    assert mig.merge_local(conversations, USER, encrypt, decrypt, out=lines.append) == mig.EXIT_REFUSED
    assert "two different texts" in lines[-1] and snapshot(tmp_path) == before
    other = Fernet.generate_key().decode()  # a file this key cannot decrypt (and not plain JSON)
    (conversations / "b_memory.json").write_bytes(_encrypt(other, [_conv("2026-10-02T00:00:00", _msg("two"))]))
    before = snapshot(tmp_path)
    assert mig.merge_local(conversations, USER, encrypt, decrypt, out=lines.append) == mig.EXIT_REFUSED
    assert "b_memory.json" in lines[-1] and snapshot(tmp_path) == before
    _no_text(lines)


def test_merge_local_names_an_original_it_could_not_rename_and_knows_no_other_fate(tmp_path, key, monkeypatch):
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    for name, text in (("a_memory.json", "PURANA-B"), ("b_memory.json", "NAYA-B")):
        (conversations / name).write_bytes(_encrypt(key, [_conv("2026-10-01T00:00:00", _msg(text, id=name))]))
    encrypt, decrypt = _crypto(key)
    with pytest.raises(ValueError):  # keep, archive or rename: there is no deleting mode
        mig.migrate_user_memory.migrate(conversations, USER, encrypt, decrypt, originals="delete")
    original_rename = Path.rename

    def held_open(self, target):
        if self.name == "b_memory.json":
            raise PermissionError(13, "held open")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", held_open)
    lines: list[str] = []
    assert mig.merge_local(conversations, USER, encrypt, decrypt, now=0, out=lines.append) == 1
    merged = json.loads(Fernet(key.encode()).decrypt((conversations / "matloob_memory.json").read_bytes()))
    assert sorted(m["id"] for c in merged for m in c["messages"]) == ["a_memory.json", "b_memory.json"]
    assert "b_memory.json could not be renamed (PermissionError); 1 of 2" in lines[-1]
    assert (conversations / "b_memory.json").exists()  # kept as it was; a rerun merges it again without a duplicate
    _no_text(lines)


def test_merge_local_keeps_a_message_said_twice_in_one_conversation_without_ids(tmp_path, key):
    # an imported or hand-built list: no message ids, and the user says the same thing twice in one conversation
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    said = _conv("2026-10-01T07:00:00", _msg("SALAM-A"), _msg("JAWAB-A", "assistant"), _msg("SALAM-A"),
                 _msg("NAYA-B", "assistant"))
    (conversations / f"{USER}_memory.json").write_bytes(_encrypt(key, [said]))
    before = snapshot(tmp_path)
    lines: list[str] = []
    encrypt, decrypt = _crypto(key)
    assert mig.merge_local(conversations, USER, encrypt, decrypt, now=0, out=lines.append) == mig.EXIT_OK
    assert "4 messages in, 0 duplicates dropped" in "\n".join(lines) and "nothing to change" in lines[-1]
    assert snapshot(tmp_path) == before  # a repeat is not a duplicate: nothing is rewritten
    (conversations / "owner-1a2b_memory.json").write_bytes(_encrypt(key, [said]))  # the same conversation again
    lines.clear()
    assert mig.merge_local(conversations, USER, encrypt, decrypt, now=0, out=lines.append) == mig.EXIT_OK
    merged = json.loads(Fernet(key.encode()).decrypt((conversations / f"{USER}_memory.json").read_bytes()))
    assert [m["content"] for c in merged for m in c["messages"]] == ["SALAM-A", "JAWAB-A", "SALAM-A", "NAYA-B"]
    assert "8 messages in, 4 duplicates dropped, 1 conversations / 4 messages" in "\n".join(lines)
    assert [p.name for p in conversations.iterdir() if p.name.endswith(mig.SUFFIX)] == [f"{USER}_memory.json"]
    _no_text(lines)


def test_main_merge_local_needs_a_valid_key_and_a_stopped_worker(tmp_path, monkeypatch, capsys, key):
    (tmp_path / "conversations").mkdir()
    args = ["--merge-local", "--root", str(tmp_path), "--user", USER]
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    assert mig.main(args) == mig.EXIT_USAGE  # never jarvis_crypto's invented session key
    monkeypatch.setenv("JARVIS_ENCRYPTION_KEY", "not-a-fernet-key")
    assert mig.main(args) == mig.EXIT_USAGE
    monkeypatch.setenv("JARVIS_ENCRYPTION_KEY", key)
    monkeypatch.setattr(mig, "_worker_running", lambda: True)
    assert mig.main(args) == mig.EXIT_RUNNING
    assert mig.main(args + ["--dry-run"]) == mig.EXIT_OK  # a dry run reads only
    assert mig.main(["--merge-local", "--root", str(tmp_path), "--user", "../x"]) == mig.EXIT_USAGE
    out = capsys.readouterr().out
    assert "JARVIS_ENCRYPTION_KEY is not set" in out and "worker is running" in out and "nothing to merge" in out
    with pytest.raises(SystemExit):
        mig.main(["--merge-local", "--verify"])
    with pytest.raises(SystemExit):
        mig.main([])


# ------------------------------------------------------------------------------------------------- --pre / --final


def test_final_copies_seals_verifies_and_sets_every_old_vps_target_aside(tmp_path, key):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")
    vps.events = []
    pc_before = snapshot(pc)
    lines: list[str] = []
    (tmp_path / "work").mkdir()

    code = mig.push(_ctx(pc, vps, key, lines, work=tmp_path / "work"), final=True)

    assert code == mig.EXIT_OK, "\n".join(lines)
    assert vps.events == ["ps", "backup", "ps", "container_prepare", "extract", "container_inventory"]
    assert snapshot(pc) == pc_before  # the PC (the source) is only read
    # the copy: byte for byte, except Chroma, whose documents are sealed (C-124) and decrypt to the PC's texts
    for rel in (MEMORY, "conversations/identity.json", "conversations/reminders.json",
                "chroma_db/0a1b2c3d-segment/header.bin", "chroma_db/0a1b2c3d-segment/data_level0.bin",
                "data/identity/master_embedding.npy", "data/identity/samples/s1.wav",
                "data/settings/agent_settings.json"):
        assert (vps_root / rel).read_bytes() == (pc / rel).read_bytes(), rel
    assert _rows(vps_root / "conversations" / "tasks.db") == 3
    assert _documents(vps_root / "chroma_db" / "chroma.sqlite3", key) == DOCS
    assert _documents(pc / "chroma_db" / "chroma.sqlite3", key) == DOCS  # the PC's own Chroma stays plain
    assert not any(doc.encode() in (vps_root / "chroma_db" / "chroma.sqlite3").read_bytes() for doc in DOCS)
    assert not (vps_root / "logs").exists() and not (vps_root / "conversations" / "identity.json.lock").exists()
    assert not any(".pre-20261001" in name for names in vps.extracted for name in names)
    # nothing deleted: every old target is kept beside it as <name>.pre-<stamp>
    kept = {
        f"{MEMORY}.pre-{STAMP}": b"OLD-VPS-MEMORY",
        f"conversations/tasks.db.pre-{STAMP}": b"OLD-TASKS-DB",
        f"conversations/tasks.db.pre-{STAMP}-wal": b"OLD-TASKS-WAL",  # the side file follows its database
        f"chroma_db/chroma.sqlite3.pre-{STAMP}": b"OLD-CHROMA",
        f"chroma_db/old-segment.pre-{STAMP}/header.bin": b"OLD-HNSW",
        f"data/identity.pre-{STAMP}/old.npy": b"OLD-VOICE",
    }
    for rel, data in kept.items():
        assert (vps_root / rel).read_bytes() == data, rel
    assert (vps_root / "conversations" / "delivery_ledger.json").read_bytes() == b"VPS-ONLY-LEDGER"
    assert (vps_root / "chroma_db" / ".jarvis.lock").exists()  # C-124's lock file stays where it is
    assert not vps.deleted
    report = "\n".join(lines)
    assert "RESULT --final: copied and verified" in report and "VPS extract: tar exit 0" in report
    assert "VERIFY memory: 2 conversations / 4 messages on both sides" in report
    assert "VERIFY chroma: 3 of 3 documents decrypt in the container" in report
    assert "VERIFY conversations/tasks.db: tasks 3 on both sides" in report
    assert "sha256" in report and "VPS kept aside: conversations/tasks.db-wal" in report
    _no_text(lines)
    assert list((tmp_path / "work").iterdir()) == []  # only the tool's own staging folder is removed


@pytest.mark.parametrize("running", [("jarvis-agent", "agent"), ("jarvis-agent-svc", "agent-svc"),
                                     ("jarvis-agent-run-4f2a9c", "agent")])
@pytest.mark.parametrize("mode", ["final", "pre", "reverse", "verify"])
def test_every_mode_refuses_while_an_agent_container_runs(tmp_path, key, mode, running):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    vps = FakeVps(vps_root, key, tmp_path / "ctmp", running=[("jarvis-bridge", "ui-bridge"), running])
    before = snapshot(pc), snapshot(vps_root)
    lines: list[str] = []

    def no_backup() -> bool:
        raise AssertionError("backup_local ran although a container runs")

    ctx = _ctx(pc, vps, key, lines, backup=no_backup)
    code = {"final": lambda: mig.push(ctx, final=True), "pre": lambda: mig.push(ctx, final=False),
            "reverse": lambda: mig.pull(ctx), "verify": lambda: mig.verify(ctx)}[mode]()

    assert code == mig.EXIT_RUNNING
    assert vps.calls == [mig.PS_COMMAND]  # nothing after the check
    assert (snapshot(pc), snapshot(vps_root)) == before
    assert running[0] in lines[-1] and "jarvis-bridge" not in lines[-1]


def test_a_failed_container_check_or_a_running_pc_worker_or_unmerged_files_stop_before_any_change(tmp_path, key):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")
    lines: list[str] = []
    before = snapshot(vps_root)
    # --final and --reverse need the PC brain stopped
    assert mig.push(_ctx(pc, vps, key, lines, worker=True), final=True) == mig.EXIT_RUNNING
    assert mig.pull(_ctx(pc, vps, key, lines, worker=True)) == mig.EXIT_RUNNING
    assert vps.calls == [] and "agent_ctl.py stop" in lines[-1]
    # per-job memory files left: run --merge-local first
    (pc / "conversations" / "owner-9f_memory.json").write_bytes(_encrypt(key, []))
    assert mig.push(_ctx(pc, vps, key, lines), final=False) == mig.EXIT_REFUSED
    assert vps.calls == [] and "--merge-local first" in lines[-1]
    (pc / "conversations" / "owner-9f_memory.json").rename(pc / "conversations" / "owner-9f_memory.json.pre-x")
    # docker ps itself fails: nothing is guessed

    def broken(command, **_kwargs):
        return mig.RemoteResult(255, b"", "ssh: connect to host test-vps port 22: Connection refused")

    assert mig.push(_ctx(pc, broken, key, lines), final=False) == mig.EXIT_FAILED
    assert "Connection refused" in lines[-1] and snapshot(vps_root) == before


def test_prepare_refuses_another_key_or_user_before_renaming_anything(tmp_path, key):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    before = snapshot(vps_root)
    lines: list[str] = []
    other_key = FakeVps(vps_root, Fernet.generate_key().decode(), tmp_path / "ctmp")
    assert mig.push(_ctx(pc, other_key, key, lines), final=True) == mig.EXIT_REFUSED
    assert "cannot decrypt a probe" in "\n".join(lines) and mig.EXTRACT_COMMAND not in other_key.calls
    other_user = FakeVps(vps_root, key, tmp_path / "ctmp", user="someone-else")
    assert mig.push(_ctx(pc, other_user, key, lines), final=True) == mig.EXIT_REFUSED
    assert "JARVIS_USER_ID" in lines[-1] and mig.EXTRACT_COMMAND not in other_user.calls
    assert snapshot(vps_root) == before  # nothing renamed, nothing written
    _no_text(lines)


def test_a_failed_extraction_reports_it_and_keeps_the_earlier_files(tmp_path, key):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")

    def failing(command, **kwargs):
        if command == mig.EXTRACT_COMMAND:
            return mig.RemoteResult(2, b"", "tar: Cannot open: Permission denied\ntar: Exiting with failure status")
        return vps(command, **kwargs)

    lines: list[str] = []
    assert mig.push(_ctx(pc, failing, key, lines), final=True) == mig.EXIT_FAILED
    assert "FAILED tar exit 2: tar: Exiting with failure status" in lines
    assert (vps_root / f"{MEMORY}.pre-{STAMP}").read_bytes() == b"OLD-VPS-MEMORY"
    assert any(line.startswith("NOTHING DELETED") for line in lines)


# ------------------------------------------------------------------------------------------------- tar flags


def test_the_extraction_command_and_the_archive_members(tmp_path, key):
    assert mig.EXTRACT_COMMAND == ("cd /opt/jarvis-agent/deploy/hostinger && docker compose -p jarvis run --rm "
                                   "--no-deps -T --user 10001 --entrypoint tar agent -xzf - --no-same-owner "
                                   "--no-overwrite-dir -C /app")
    assert mig.PROGRAM_COMMAND.startswith("cd /opt/jarvis-agent/deploy/hostinger && docker compose -p jarvis run "
                                          "--rm --no-deps -T --user 10001 --entrypoint python agent -c ")
    pc = make_pc(tmp_path / "pc", key)
    rels = mig.data_set(str(pc), USER)
    assert rels == sorted([MEMORY, "conversations/identity.json", "conversations/reminders.json",
                           "conversations/tasks.db", "chroma_db/chroma.sqlite3",
                           "chroma_db/0a1b2c3d-segment/header.bin", "chroma_db/0a1b2c3d-segment/data_level0.bin",
                           "data/identity/master_embedding.npy", "data/identity/samples/s1.wav",
                           "data/settings/agent_settings.json"])
    buffer = io.BytesIO()
    mig.write_tar(buffer, str(pc), rels)
    with tarfile.open(fileobj=io.BytesIO(buffer.getvalue()), mode="r:gz") as tar:
        members = tar.getmembers()
    names = [member.name for member in members]
    assert not any(name in ("", ".") or name.startswith(("./", "/")) for name in names)  # never a './' entry
    assert not any(name in mig.ARCHIVE_TOPS for name in names)  # the volumes' mount points are never touched
    folders = [member.name for member in members if member.isdir()]
    assert folders == ["chroma_db/0a1b2c3d-segment", "data/identity", "data/settings", "data/identity/samples"]
    assert names.index("data/identity/samples") < names.index("data/identity/samples/s1.wav")
    assert {member.mode for member in members if member.isdir()} == {0o700}
    assert {member.mode for member in members if member.isfile()} == {0o600}
    assert {(member.uid, member.gid, member.uname, member.gname) for member in members} == {(0, 0, "", "")}
    with pytest.raises(ValueError):
        mig.write_tar(io.BytesIO(), str(pc), ["../escape"])


def _gnu_tar() -> str | None:
    for candidate in (shutil.which("tar"), r"C:\Program Files\Git\usr\bin\tar.exe", "/usr/bin/tar", "/bin/tar"):
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            version = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=20,
                                     check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "GNU tar" in version:
            return candidate
    return None


GNU_TAR = _gnu_tar()


@pytest.mark.skipif(GNU_TAR is None, reason="GNU tar (as in the agent image) not found")
def test_gnu_tar_extracts_the_archive_with_the_exact_flags_into_existing_volumes(tmp_path, key):
    pc = make_pc(tmp_path / "pc", key)
    rels = mig.data_set(str(pc), USER)
    buffer = io.BytesIO()
    mig.write_tar(buffer, str(pc), rels)
    app = tmp_path / "app"
    for top in mig.ARCHIVE_TOPS:  # the mount points exist, as the volumes do
        (app / top).mkdir(parents=True)
    args = list(mig.EXTRACT_TAR_ARGS)
    assert args[-2:] == ["-C", "/app"]
    args[-1] = "app"  # relative to cwd: no drive-letter translation for an MSYS tar
    env = {**os.environ, "PATH": str(Path(GNU_TAR).parent) + os.pathsep + os.environ.get("PATH", "")}
    proc = subprocess.run([GNU_TAR, *args], input=buffer.getvalue(), cwd=tmp_path, env=env, capture_output=True,
                          timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    for rel in rels:
        assert (app / rel).read_bytes() == (pc / rel).read_bytes(), rel


# ------------------------------------------------------------------------------------------------- SQLite online copy


def test_tasks_db_is_copied_with_the_online_backup_api(tmp_path):
    source = tmp_path / "pc" / "conversations" / "tasks.db"
    make_tasks_db(source, ["one"])
    writer = sqlite3.connect(source)  # the PC brain keeps it open: its rows sit in the WAL, not yet in the file
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.executemany("INSERT INTO tasks (title) VALUES (?)", [("two",), ("three",), ("four",)])
        writer.commit()
        raw = tmp_path / "raw.db"
        shutil.copyfile(source, raw)
        assert _rows(raw) == 1  # a plain file copy would lose them
        mig.stage_copy(str(tmp_path / "pc"), ["conversations/tasks.db"], str(tmp_path / "staging"))
    finally:
        writer.close()
    copy = tmp_path / "staging" / "conversations" / "tasks.db"
    assert _rows(copy) == 4
    con = sqlite3.connect(copy)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # one self-contained file
    finally:
        con.close()
    assert sorted(p.name for p in copy.parent.iterdir()) == ["tasks.db"]
    with pytest.raises(FileExistsError):  # never over an existing file
        mig.sqlite_copy(str(source), str(copy))


def test_state_files_are_read_under_their_jarvis_filelock(tmp_path):
    from services.utils.jarvis_filelock import locked

    path = tmp_path / "reminders.json"
    path.write_text("[]", "utf-8")
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with locked(path):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(10)
        with pytest.raises(TimeoutError):
            mig.read_locked(str(path), timeout=0.2)  # the same lock as the writers (X-66, K-77)
    finally:
        release.set()
        thread.join(10)
    assert mig.read_locked(str(path), timeout=2) == b"[]"


# ------------------------------------------------------------------------------------------------- Chroma (C-124)


def test_seal_chroma_seals_every_copy_of_a_text_once_and_leaves_none_on_disk(tmp_path, key):
    db = tmp_path / "chroma.sqlite3"
    # an index laid out by an older chromadb: a document under another rowid, and a metadata string
    make_chroma(db, DOCS, extra_index_rows=[(200, DOCS[0]), (300, "voice")])
    counts = mig.seal_chroma(str(db), key)
    assert counts == {"documents": 3, "sealed": 3, "already": 0, "index": 4, "queue": 3}
    assert _documents(db, key) == DOCS
    con = sqlite3.connect(db)
    try:
        index = dict(con.execute("SELECT rowid, string_value FROM embedding_fulltext_search").fetchall())
        queue = [json.loads(meta) for (meta,) in con.execute("SELECT metadata FROM embeddings_queue")]
    finally:
        con.close()
    assert index[300] == "voice" and all(index[rowid].startswith(mig.ENC_PREFIX) for rowid in (1, 2, 3, 200))
    assert all(item["chroma:document"].startswith(mig.ENC_PREFIX) and item["source"] == "voice" for item in queue)
    data = db.read_bytes()
    assert not any(doc.encode() in data for doc in DOCS)  # vacuumed with secure_delete: no freed page keeps a text
    report = mig.chroma_report(str(db), key)
    assert report["documents"] == 3 and report["sealed"] == 3 and report["decrypt_ok"] == 3 and report["plain"] == 0
    assert mig.chroma_report(str(db), Fernet.generate_key().decode())["decrypt_failed"] == 3
    assert mig.seal_chroma(str(db), key)["sealed"] == 0 and db.read_bytes() == data  # never sealed twice


def test_seal_chroma_refuses_an_unknown_layout_and_changes_nothing(tmp_path, key):
    db = tmp_path / "chroma.sqlite3"
    make_chroma(db, DOCS)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE embeddings_queue")
    con.commit()
    con.close()
    before = db.read_bytes()
    with pytest.raises(ValueError, match="unknown Chroma layout"):
        mig.seal_chroma(str(db), key)
    assert db.read_bytes() == before
    assert "unknown Chroma layout" in mig.chroma_report(str(db), key)["error"]


# Both chromadb children turn chromadb's telemetry off in their settings, as tests/unit/test_vector_encryption.py
# does: chromadb 1.5.0 sends posthog usage events unless anonymized_telemetry is False (it looks for pytest only in its
# own process, and these children are fresh 'python -I' processes).
CHROMA_MAKE = r"""
import sys, chromadb
from chromadb.config import Settings
client = chromadb.PersistentClient(path=sys.argv[1], settings=Settings(anonymized_telemetry=False))
collection = client.get_or_create_collection(name="jarvis_memory", embedding_function=None)
collection.add(ids=["a", "b", "c"], documents=["DOC-ALPHA one", "DOC-BETA two", "DOC-GAMMA three"],
               embeddings=[[0.1, 0.2, 0.3], [0.2, 0.1, 0.0], [0.9, 0.8, 0.7]],
               metadatas=[{"id": "m1"}, {"id": "m2"}, {"id": "m3"}])
print(collection.count())
"""
CHROMA_READ = r"""
import json, sys, chromadb
from chromadb.config import Settings
client = chromadb.PersistentClient(path=sys.argv[1], settings=Settings(anonymized_telemetry=False))
collection = client.get_collection(name="jarvis_memory")
got = collection.get(include=["documents", "metadatas"])
top = collection.query(query_embeddings=[[0.9, 0.8, 0.7]], n_results=1, include=["documents"])
print(json.dumps({"count": collection.count(), "ids": got["ids"], "documents": got["documents"],
                  "metadatas": got["metadatas"], "top": top["ids"][0]}))
"""
# Put before each chromadb child: posthog.capture (chromadb's one telemetry call) is replaced before chromadb loads,
# so nothing can reach the network, and every event chromadb would have sent (posthog not disabled) is reported as
# one JSON row on stderr at exit.
TELEMETRY_PREFIX = (
    "import sys, json, atexit\n"
    "_sent = []\n"
    "try:\n"
    "    import posthog\n"
    "except ImportError:\n"
    "    posthog = None\n"
    "else:\n"
    "    posthog.capture = lambda *args, **kwargs: None if posthog.disabled else _sent.append(\n"
    "        str(args[1] if len(args) > 1 else kwargs.get('event')))\n"
    "atexit.register(lambda: sys.stderr.write(json.dumps({'telemetry': _sent}) + '\\n'))\n")


def _chroma_child(script: str, path: Path, cwd: Path) -> tuple[subprocess.CompletedProcess, list[str]]:
    """(the result, the telemetry events chromadb would have sent) of a chromadb script run in a 'python -I' child
    with TELEMETRY_PREFIX, in cwd (chromadb's settings read a .env there: never the repo's) and without
    ANONYMIZED_TELEMETRY in its environment (the script itself must turn the telemetry off)."""
    env = {name: value for name, value in os.environ.items() if name.upper() != "ANONYMIZED_TELEMETRY"}
    done = subprocess.run([sys.executable, "-I", "-c", TELEMETRY_PREFIX + script, str(path)], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=300, check=False)
    rows = [json.loads(line) for line in done.stderr.splitlines() if line.startswith('{"telemetry": ')]
    assert rows, done.stderr[-500:]  # the prefix reports at every exit
    return done, rows[-1]["telemetry"]


@pytest.mark.skipif(importlib.util.find_spec("chromadb") is None, reason="chromadb is not installed")
def test_chromadb_reads_a_sealed_copy_with_the_same_records_and_vectors(tmp_path, key):
    source = tmp_path / "pc"
    made, sent = _chroma_child(CHROMA_MAKE, source / "chroma_db", tmp_path)
    assert made.returncode == 0 and made.stdout.strip() == "3", made.stderr[-500:]
    assert sent == []  # chromadb's telemetry is off: no usage event would have left this child
    rels = mig.data_set(str(source), USER)
    assert "chroma_db/chroma.sqlite3" in rels and len(rels) > 1  # the segment files travel with it
    mig.stage_copy(str(source), rels, str(tmp_path / "copy"))
    db = tmp_path / "copy" / "chroma_db" / "chroma.sqlite3"
    assert mig.seal_chroma(str(db), key)["sealed"] == 3
    read, sent = _chroma_child(CHROMA_READ, db.parent, tmp_path)
    assert read.returncode == 0, read.stderr[-500:]
    assert sent == []
    result = json.loads(read.stdout.strip().splitlines()[-1])
    assert result["count"] == 3 and result["ids"] == ["a", "b", "c"] and result["top"] == ["c"]
    assert [item["id"] for item in result["metadatas"]] == ["m1", "m2", "m3"]
    fernet = Fernet(key.encode())
    assert [fernet.decrypt(doc[len(mig.ENC_PREFIX):].encode()).decode() for doc in result["documents"]] == [
        "DOC-ALPHA one", "DOC-BETA two", "DOC-GAMMA three"]


def test_the_seal_prefix_is_the_vector_memorys():
    tree = ast.parse((ROOT / "services" / "ai_core" / "jarvis_vector_memory.py").read_text("utf-8"))
    values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "ENC_PREFIX" for target in node.targets)]
    assert values == [mig.ENC_PREFIX]


# ------------------------------------------------------------------------------------------- --reverse, round trip


def test_nothing_is_deleted_anywhere_and_the_counts_are_equal_after_a_round_trip(tmp_path, key, monkeypatch):
    pc_a = make_pc(tmp_path / "pc_a", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    pc_b = tmp_path / "pc_b"  # the PC brain's side, with older data of its own
    (pc_b / "conversations").mkdir(parents=True)
    (pc_b / "conversations" / f"{USER}_memory.json").write_bytes(b"OLD-PC-MEMORY")
    (pc_b / "chroma_db" / "seg-old").mkdir(parents=True)
    (pc_b / "chroma_db" / "seg-old" / "header.bin").write_bytes(b"OLD-PC-HNSW")
    (pc_b / "chroma_db" / "chroma.sqlite3").write_bytes(b"OLD-PC-CHROMA")
    roots = (pc_a, vps_root, pc_b)
    before = {root: snapshot(root) for root in roots}
    deleted: list[str] = []
    for module, name in ((os, "remove"), (os, "unlink"), (os, "rmdir"), (shutil, "rmtree")):
        original = getattr(module, name)

        def recording(path, *args, _original=original, **kwargs):
            deleted.append(os.path.abspath(os.fspath(path)))
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(module, name, recording)
    (tmp_path / "work").mkdir()
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")
    lines: list[str] = []

    assert mig.push(_ctx(pc_a, vps, key, lines, work=tmp_path / "work"), final=True) == mig.EXIT_OK, "\n".join(lines)
    vps.events = []
    ctx_b = _ctx(pc_b, vps, key, lines, work=tmp_path / "work")
    ctx_b.stamp = "20261021-130000"
    assert mig.pull(ctx_b) == mig.EXIT_OK, "\n".join(lines)
    assert vps.events == ["ps", "backup", "pack"]  # one container run, its stdout the archive
    assert mig.verify(ctx_b) == mig.EXIT_OK, "\n".join(lines)
    assert mig.verify(_ctx(pc_a, vps, key, lines)) == mig.EXIT_OK  # the PC brain's copy, the VPS and the source

    # counts and contents equal on all three sides
    for rel in (MEMORY, "conversations/identity.json", "conversations/reminders.json",
                "data/identity/samples/s1.wav", "data/settings/agent_settings.json"):
        assert (pc_b / rel).read_bytes() == (pc_a / rel).read_bytes() == (vps_root / rel).read_bytes(), rel
    assert _rows(pc_b / "conversations" / "tasks.db") == _rows(pc_a / "conversations" / "tasks.db") == 3
    assert _documents(pc_b / "chroma_db" / "chroma.sqlite3", key) == DOCS  # sealed on the VPS leg, still readable
    counts = [mig.counts_of(mig.inventory(str(root), mig.data_set(str(root), USER), USER, key)) for root in roots]
    assert counts[0] == counts[1] == counts[2]
    assert counts[0]["memory"] == [2, 4] and counts[0]["chroma"][0] == 3
    # nothing deleted anywhere: no removal under any root (only the tool's own staging folder went), and every
    # earlier file is still there, at its place or as *.pre-<stamp>
    for root in roots:
        assert not [path for path in deleted + vps.deleted if path.startswith(os.path.abspath(root))]
        assert_nothing_lost(before[root], root)
    assert (pc_b / f"{MEMORY}.pre-20261021-130000").read_bytes() == b"OLD-PC-MEMORY"
    assert (pc_b / "chroma_db" / "seg-old.pre-20261021-130000" / "header.bin").read_bytes() == b"OLD-PC-HNSW"
    report = "\n".join(lines)
    assert "RESULT --reverse: copied and verified" in report
    assert "PC kept aside: chroma_db/chroma.sqlite3 -> chroma_db/chroma.sqlite3.pre-20261021-130000" in report
    assert "RESULT --verify: the counts are equal on both sides" in report
    _no_text(lines)


def test_verify_finds_a_difference_between_the_two_sides(tmp_path, key):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = make_vps_with_old_data(tmp_path / "vps")
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")
    lines: list[str] = []
    assert mig.push(_ctx(pc, vps, key, lines), final=True) == mig.EXIT_OK
    memory = vps_root / MEMORY  # the VPS brain saves one more conversation
    data = json.loads(Fernet(key.encode()).decrypt(memory.read_bytes()))
    memory.write_bytes(_encrypt(key, data + [_conv("2026-10-21T13:00:00", _msg("NAYA-B", id="c1"))]))
    lines.clear()
    assert mig.verify(_ctx(pc, vps, key, lines)) == mig.EXIT_FAILED
    assert "DIFFERS memory: PC 2 conversations / 4 messages, VPS 3 conversations / 5 messages" in lines
    assert lines[-1] == "RESULT --verify: the two sides differ"
    _no_text(lines)


def test_the_pc_brain_reads_the_merged_and_the_reversed_memory_file(tmp_path, key, monkeypatch):
    """Acceptance: after --merge-local, and after --reverse ('brain pc'), memory_store's own reader (the PC brain's)
    loads conversations/matloob_memory.json with this key (K-69 v44: the one owner memory file)."""
    from services.ai_core import memory_store
    from services.utils.jarvis_crypto import JarvisCrypto

    monkeypatch.setenv("JARVIS_ENCRYPTION_KEY", key)
    monkeypatch.setattr(JarvisCrypto, "_key", None)  # jarvis_crypto caches its key per process
    conversations = tmp_path / "merge" / "conversations"
    conversations.mkdir(parents=True)
    (conversations / "owner-a_memory.json").write_bytes(_encrypt(key, [
        _conv("2026-10-02T10:00:00", _msg("PURANA-B", id="p1"), _msg("JAWAB-A", "assistant", id="p2"))]))
    (conversations / "agent-b_memory.json").write_bytes(_encrypt(key, [
        _conv("2026-10-03T10:00:00", _msg("NAYA-B", id="n1"))]))
    encrypt, decrypt = _crypto(key)
    assert mig.merge_local(conversations, USER, encrypt, decrypt, out=lambda _line: None) == mig.EXIT_OK
    assert [len(c["messages"]) for c in memory_store.read_conversations(USER, str(conversations))] == [2, 1]

    pc = make_pc(tmp_path / "pc", key)
    vps_root = tmp_path / "vps"
    (vps_root / "conversations").mkdir(parents=True)  # fresh volumes (stage V4)
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")
    lines: list[str] = []
    assert mig.push(_ctx(pc, vps, key, lines), final=False) == mig.EXIT_OK, "\n".join(lines)
    target = tmp_path / "pc_brain"
    (target / "conversations").mkdir(parents=True)
    assert mig.pull(_ctx(target, vps, key, lines)) == mig.EXIT_OK, "\n".join(lines)
    loaded = memory_store.read_conversations(USER, str(target / "conversations"))
    assert [len(c["messages"]) for c in loaded] == [2, 2]
    _no_text(lines)


def test_a_reverse_that_fails_midway_puts_the_pc_back_as_it_was(tmp_path, key, monkeypatch):
    pc = make_pc(tmp_path / "pc", key)
    vps_root = tmp_path / "vps"
    (vps_root / "conversations").mkdir(parents=True)
    vps = FakeVps(vps_root, key, tmp_path / "ctmp")
    lines: list[str] = []
    assert mig.push(_ctx(pc, vps, key, lines), final=False) == mig.EXIT_OK
    target = make_vps_with_old_data(tmp_path / "pc_brain")  # older PC brain data of every kind
    before = snapshot(target)
    tree_before = sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))
    original_move, calls = shutil.move, []

    def flaky_move(src, dst, *args, **kwargs):
        calls.append(dst)
        if len(calls) == 9:  # data/identity/samples/s1.wav cannot be moved in (a file held open, say)
            raise PermissionError(13, "held open")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(mig.shutil, "move", flaky_move)
    lines.clear()
    assert mig.pull(_ctx(target, vps, key, lines)) == mig.EXIT_FAILED
    assert snapshot(target) == before  # the copied files taken out, every old name given back
    assert (target / "conversations" / "tasks.db-wal").read_bytes() == b"OLD-TASKS-WAL"  # with its database
    assert sorted(path.relative_to(target).as_posix() for path in target.rglob("*")) == tree_before  # no new folder
    assert any(line.startswith("RESTORED the PC as it was") for line in lines)
    _no_text(lines)


def test_reverse_refuses_an_unsafe_archive_and_changes_nothing_on_the_pc(tmp_path, key):
    for name, kind in (("../escape", tarfile.REGTYPE), ("/abs/x", tarfile.REGTYPE), ("./conversations/x",
                                                                                     tarfile.REGTYPE),
                       ("logs/x.log", tarfile.REGTYPE), ("conversations/link", tarfile.SYMTYPE)):
        archive = tmp_path / f"bad-{abs(hash(name))}.tgz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        dest = tmp_path / f"dest-{abs(hash(name))}"
        with pytest.raises(mig.Refused) as caught:
            mig.safe_extract(archive, dest)
        assert caught.value.code == mig.EXIT_REFUSED and not dest.exists()


# ------------------------------------------------------------------------------------------------- dry run, runner


def test_a_dry_run_prints_the_plan_and_touches_nothing(tmp_path, key, monkeypatch, capsys):
    pc = make_pc(tmp_path / "pc", key)
    before = snapshot(pc)

    def no_runner(command, **_kwargs):
        raise AssertionError(f"a dry run reached the VPS: {command}")

    def no_backup() -> bool:
        raise AssertionError("a dry run ran backup_local")

    for mode in ("--pre", "--final", "--reverse", "--verify"):
        lines: list[str] = []
        ctx = _ctx(pc, no_runner, key, lines, dry_run=True, backup=no_backup)
        code = {"--pre": lambda: mig.push(ctx, final=False), "--final": lambda: mig.push(ctx, final=True),
                "--reverse": lambda: mig.pull(ctx), "--verify": lambda: mig.verify(ctx)}[mode]()
        assert code == mig.EXIT_OK and lines[0].startswith(f"DRY RUN {mode}")
        report = "\n".join(lines)
        assert mig.PS_COMMAND in report and mig.PROGRAM_COMMAND in report
        if mode in ("--pre", "--final"):
            assert mig.EXTRACT_COMMAND in report and "memory: 2 conversations / 4 messages" in report
        _no_text(lines)
    assert snapshot(pc) == before
    monkeypatch.setenv("JARVIS_ENCRYPTION_KEY", key)
    monkeypatch.setattr(mig, "_worker_running", lambda: True)  # a dry run names the refusal and returns its code
    assert mig.main(["--final", "--dry-run", "--root", str(pc), "--user", USER], runner=no_runner,
                    backup=no_backup) == mig.EXIT_RUNNING
    out = capsys.readouterr().out
    assert "a real run would refuse: the PC brain" in out and mig.EXTRACT_COMMAND in out
    assert snapshot(pc) == before


def test_the_ssh_runner_never_prompts_and_streams_files(tmp_path, monkeypatch):
    seen: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        if "stdout" in kwargs and hasattr(kwargs["stdout"], "write"):
            kwargs["stdout"].write(b"TAR")
        return subprocess.CompletedProcess(argv, 0, stdout=None if hasattr(kwargs.get("stdout"), "write")
                                           else b"rows", stderr=b"")

    monkeypatch.setattr(mig.subprocess, "run", fake_run)
    run = mig.ssh_runner("root@vps", tmp_path / "id_key")
    assert run("docker ps", stdin=b"{}") == mig.RemoteResult(0, b"rows", "")
    argv, kwargs = seen[-1]
    assert argv[:3] == ["ssh", "-i", str(tmp_path / "id_key")] and "BatchMode=yes" in argv and "-T" in argv
    assert argv[-2:] == ["root@vps", "docker ps"] and kwargs["input"] == b"{}"
    archive = tmp_path / "a.tgz"
    archive.write_bytes(b"ARCHIVE")
    out = tmp_path / "out.tgz"
    assert run("pack", stdin=archive, stdout_to=out).code == 0
    assert out.read_bytes() == b"TAR" and os.fspath(seen[-1][1]["stdin"].name) == str(archive)
    assert mig.ssh_runner("root@vps", None)("x")[0] == 0 and "-i" not in seen[-1][0]

    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(mig.subprocess, "run", timeout)
    assert run("x") == mig.RemoteResult(255, b"", "ssh did not run: TimeoutExpired")


def test_the_container_programs_are_self_contained():
    """Each shipped function uses only the program's own imports, constants and functions (no PC module), so it runs
    in the agent image as it runs here."""
    namespace: dict = {"__name__": "jarvis_migrate_check"}
    exec(compile(mig.container_program("container_inventory", run=False), "check", "exec"), namespace)  # noqa: S102

    def global_names(code: types.CodeType) -> set[str]:
        names = {ins.argval for ins in dis.get_instructions(code) if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME")}
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                names |= global_names(const)
        return names

    missing = sorted({(function.__name__, name) for function in mig.SHIPPED_FUNCTIONS
                      for name in global_names(namespace[function.__name__].__code__)
                      if name not in namespace and not hasattr(builtins, name)})
    assert missing == []
    for entry in mig.CONTAINER_ENTRIES:
        compile(mig.container_program(entry), entry, "exec")
    with pytest.raises(ValueError):
        mig.container_program("rename_aside")
