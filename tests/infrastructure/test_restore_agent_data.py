"""C-128: the VPS brain's data in the backup and the PC side that opens it and streams it back. Offline: temp folders,
temp keys, a fake ssh.

backup_vps.sh's agent step runs services/utils/agent_volumes.py inside the agent image ('python -m
services.utils.agent_volumes /app <key>' from /app, under 'env -i'); it seals with services/utils/backup_crypto.py, the
format and the key pair of agent-svc's daily snapshot (K-77). The PC has one key store, scripts/ops/backup_key.py's
(DPAPI-wrapped, or an offline copy it exported); scripts/ops/backup_key.py decrypt and scripts/ops/restore_agent_data.py
both open the backup with it. Here the sealer runs as the image runs it, on a scratch '/app'; the restore tool runs
in-process with an injected Popen, so nothing reaches the VPS, Docker, DPAPI or a real key. Every test also checks that
no plain memory byte shows up in a sealed file or in anything printed. The second C-128 review added: a stream cut short
is INCOMPLETE on the VPS (the move script counts what arrived) and ACT NOW on the PC (a local read error, Ctrl-C, an
error of the PC), the time caps of the ssh steps, and the free-disk guard of steps 1 and 3.
"""
# pylint: disable=redefined-outer-name,too-many-positional-arguments,protected-access  # parametrized pytest tests
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import threading
from pathlib import Path
from types import ModuleType

import pytest

from services.utils import agent_volumes as av
from services.utils import backup_crypto as bc

ROOT = Path(__file__).resolve().parents[2]
BACKUP_VPS = ROOT / "deploy" / "hostinger" / "backup_vps.sh"
TOOL = ROOT / "scripts" / "ops" / "restore_agent_data.py"
KEY_TOOL = ROOT / "scripts" / "ops" / "backup_key.py"
MEMORY_SENTINEL = "c128-plain-memory-sentinel"
TASK_SENTINEL = "c128-plain-task-sentinel"
CHROMA_SENTINEL = "c128-plain-chroma-sentinel"
SENTINELS = (MEMORY_SENTINEL, TASK_SENTINEL, CHROMA_SENTINEL)
TS = "2026-10-21_0900Z-pre-deploy-abc1234"
STAMP = "20261021T090500Z"
PRE_RESTORE = "/root/jarvis-backups/2026-10-21_0905Z-pre-data-restore"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


rad = _load("jarvis_restore_agent_data", TOOL)
bkey = _load("jarvis_backup_key_c128", KEY_TOOL)


def _find_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    candidates = [parent / "usr" / "bin" / "bash.exe" for parent in Path(git).resolve().parents] if git else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe")
    return next((str(c) for c in candidates if c.is_file() and "system32" not in str(c).lower()), None)


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="bash (Git Bash on Windows) is not available")


def _posix(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    assert BASH is not None
    proc = subprocess.run([str(Path(BASH).parent / "cygpath.exe"), "-u", str(path)], capture_output=True, text=True,
                          check=True)
    return proc.stdout.strip()


def _wrap(data: bytes) -> bytes:   # a stand-in for DPAPI (reversible, so the key never shows as it is)
    return b"WRAPPED:" + base64.b64encode(data[::-1])


def _unwrap(blob: bytes) -> bytes:
    if not blob.startswith(b"WRAPPED:"):
        raise OSError("not wrapped for this user")
    return base64.b64decode(blob[len(b"WRAPPED:"):])[::-1]


@pytest.fixture
def key() -> bc.X25519PrivateKey:
    return bc.generate_private_key()


def offline_copy(tmp_path: Path, key: bc.X25519PrivateKey, name: str = "jarvis-backup-key.json") -> Path:
    """An offline copy of the backup key, as 'backup_key.py export' writes it (the key document, unwrapped)."""
    path = tmp_path / "usb" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bkey.key_document(key))
    return path


def make_app(base: Path, *, big: int = 0) -> list[sqlite3.Connection]:
    """A scratch /app of the three agent volumes; the open connections keep the WAL rows uncheckpointed (a live
    agent)."""
    conv, chroma, data = base / "conversations", base / "chroma_db", base / "data"
    for folder in (conv, chroma, data / "identity", chroma / "seg-1"):
        folder.mkdir(parents=True, exist_ok=True)
    (conv / "matloob_memory.json").write_text(json.dumps({"facts": [MEMORY_SENTINEL]}), encoding="utf-8")
    (conv / ".hidden_state.json").write_text("{}", encoding="utf-8")
    (data / "identity" / "identity.json").write_text('{"name": "owner"}', encoding="utf-8")
    (chroma / "seg-1" / "data_level0.bin").write_bytes(os.urandom(big) if big else b"\x00" * 64)
    aside = conv / ".pre-restore-20261001T000000Z"
    aside.mkdir()
    (aside / "old.json").write_text("moved aside earlier", encoding="utf-8")
    connections = []
    for path, text in ((conv / "tasks.db", TASK_SENTINEL), (chroma / "chroma.sqlite3", CHROMA_SENTINEL)):
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES (?)", (text,))
        con.commit()
        connections.append(con)
    return connections


def run_sealer(base: Path, public_hex: str) -> subprocess.CompletedProcess:
    """backup_vps.sh's agent step as the agent image runs it: 'python -m services.utils.agent_volumes /app <key>' from
    the code folder (/app in the image), with a bare environment ('env -i': no PYTHONPATH, no secret)."""
    keep = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(LANG="C.UTF-8")
    return subprocess.run([sys.executable, "-m", "services.utils.agent_volumes", str(base), public_hex],
                          capture_output=True, timeout=120, check=False, cwd=ROOT, env=env)


def sealed_backup(tmp_path: Path, key: bc.X25519PrivateKey, **kwargs: int) -> bytes:
    """A sealed agent backup of a fresh scratch /app, made in-process by the same code the image runs."""
    base = tmp_path / "vps-app"
    for con in make_app(base):
        con.close()
    out = io.BytesIO()
    av.seal_volumes(str(base), key.public_key(), out, **kwargs)
    return out.getvalue()


def plain_members(sealed: bytes, key: bc.X25519PrivateKey) -> dict[str, bytes]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(bc.open_bytes(sealed, key))) as tar:
        for info in tar:
            handle = tar.extractfile(info) if info.isfile() else None
            out[info.name + ("/" if info.isdir() else "")] = handle.read() if handle else b""
    return out


def _rows(blob: bytes, tmp_path: Path, name: str) -> list[tuple]:
    path = tmp_path / name
    path.write_bytes(blob)
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return con.execute("SELECT v FROM t").fetchall()
    finally:
        con.close()


def _records(sealed: bytes) -> tuple[bytes, list[bytes]]:
    """backup_crypto's layout: the header, then one length-prefixed record per chunk."""
    header, rest, parts = sealed[:bc.HEADER_BYTES], sealed[bc.HEADER_BYTES:], []
    while rest:
        (length,) = struct.unpack(">I", rest[:4])
        parts.append(rest[:4 + length])
        rest = rest[4 + length:]
    return header, parts


# --------------------------------------------------------------------------- one format, one key


def test_the_image_step_seals_with_backup_crypto_and_both_pc_tools_open_it(tmp_path: Path, key, capsys) -> None:
    """The acceptance in small: the step backup_vps.sh runs in the agent image seals a tar of the agent data (every
    SQLite file an online copy that includes the rows still in the WAL of a live connection) in backup_crypto's format,
    with no plain memory byte, no -wal/-shm and no moved-aside folder in it. backup_key.py decrypt opens it like a
    snapshot (KIND=agent-volumes) and restore_agent_data.py --check-file checks it, both with the one backup key."""
    base = tmp_path / "app"
    connections = make_app(base, big=3 * bc.DEFAULT_CHUNK_SIZE // 2)   # a file over one chunk
    try:
        proc = run_sealer(base, bc.public_key_hex(key))
    finally:
        for con in connections:
            con.close()
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err
    summary = re.search(r"^AGENT_SEAL (.*?)\r?$", err, re.M)
    assert summary is not None, err
    counts = dict(item.split("=", 1) for item in summary.group(1).split())
    assert counts["sqlite"] == "2" and counts["sqlite_raw"] == "0" and counts["sqlite_bad"] == "0"
    assert counts["aside"] == "1" and counts["files"] == "6" and counts["vanished"] == "0"
    assert counts["recipient"] == bc.fingerprint(key)   # what 'backup_key.py public' prints as FINGERPRINT
    sealed = proc.stdout
    assert bc.looks_sealed(sealed) and sealed[:8] == bc.MAGIC == b"JRVSNAP1" and int(counts["sealed"]) == len(sealed)
    assert not any(s.encode() in sealed for s in SENTINELS) and not any(s in err for s in SENTINELS)
    members = plain_members(sealed, key)
    names = list(members)
    assert names[-1] == av.META == "AGENT-BACKUP.json"
    assert {"conversations/", "chroma_db/", "data/", "data/identity/", "conversations/matloob_memory.json",
            "conversations/.hidden_state.json", "conversations/tasks.db", "chroma_db/chroma.sqlite3",
            "chroma_db/seg-1/data_level0.bin", "data/identity/identity.json"} <= set(names)
    assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal")) or ".pre-restore-" in n], names
    assert MEMORY_SENTINEL.encode() in members["conversations/matloob_memory.json"]
    assert _rows(members["conversations/tasks.db"], tmp_path, "tasks.db") == [(TASK_SENTINEL,)]
    assert _rows(members["chroma_db/chroma.sqlite3"], tmp_path, "chroma.sqlite3") == [(CHROMA_SENTINEL,)]
    assert len(members["chroma_db/seg-1/data_level0.bin"]) == 3 * bc.DEFAULT_CHUNK_SIZE // 2
    meta = json.loads(members["AGENT-BACKUP.json"])
    assert meta["kind"] == av.META_KIND and meta["sqlite"] == 2
    assert {row["path"] for row in meta["sqlite_files"]} == {"conversations/tasks.db", "chroma_db/chroma.sqlite3"}

    sealed_file = tmp_path / "mirror" / TS / av.ARCHIVE
    sealed_file.parent.mkdir(parents=True)
    sealed_file.write_bytes(sealed)
    # backup_key.py decrypt, from its DPAPI-wrapped store (a stand-in wrap here), like a snapshot
    store = tmp_path / "JARVIS" / "backup" / "backup_key.dpapi"
    store.parent.mkdir(parents=True)
    store.write_bytes(_wrap(bkey.key_document(key)))
    assert bkey.main(["decrypt", str(sealed_file), "--key-file", str(store)], wrap=_wrap, unwrap=_unwrap) == 0
    out = capsys.readouterr().out
    assert "KIND=agent-volumes" in out and f"TAR_MEMBERS={len(members)}" in out, out
    tar_path = sealed_file.with_name("agent-volumes.tar")
    assert tar_path.read_bytes() == bc.open_bytes(sealed, key)
    tar_path.unlink()
    # restore_agent_data.py --check-file, from an offline copy of the same key
    assert rad.main(["--check-file", str(sealed_file), "--key-file", str(offline_copy(tmp_path, key))]) == 0
    out = capsys.readouterr().out
    assert "CHECK=ok" in out and "MEMBERS files=6" in out and "conversations=3 chroma_db=2 data=1" in out, out
    assert f"KEY={bc.fingerprint(key)}" in out and "SQLITE online=2 raw=0 bad=0" in out
    assert not any(s in out for s in SENTINELS) and bc.private_key_hex(key) not in out


def test_the_image_step_keeps_the_three_roots_and_refuses_a_bad_key(tmp_path: Path, key) -> None:
    (tmp_path / "app" / "conversations").mkdir(parents=True)   # chroma_db and data missing
    proc = run_sealer(tmp_path / "app", bc.public_key_hex(key))
    assert proc.returncode == 0, proc.stderr
    members = plain_members(proc.stdout, key)
    assert {"conversations/", "chroma_db/", "data/"} <= set(members)
    assert json.loads(members["AGENT-BACKUP.json"])["missing"] == ["chroma_db", "data"]
    for bad in ("", "zz" * 32, "ab" * 31):
        proc = run_sealer(tmp_path / "app", bad)
        assert proc.returncode == 2 and proc.stdout == b"" and b"AGENT_SEAL_ERROR" in proc.stderr


def test_there_is_one_sealed_format_and_one_key_store(tmp_path: Path, key, capsys,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """No second crypto and no second key: backup_vps.sh runs the image's module (no inline sealer), the restore tool
    imports no crypto primitive and makes, wraps or writes no key; it reads backup_key.py's store, the one
    'backup_key.py keygen' writes, so a backup sealed to the key keygen printed opens without any --key-file."""
    script = BACKUP_VPS.read_text(encoding="utf-8")
    assert "readonly AGENT_STEP_MODULE=services.utils.agent_volumes" in script
    assert f"readonly SEALED_MAGIC={bc.MAGIC.decode()}" in script
    assert 'env -i "${AGENT_STEP_ENV[@]}" python -m "$AGENT_STEP_MODULE" /app "$key"' in script
    assert re.search(r"docker exec -w /app .*python -m \"\$AGENT_STEP_MODULE\"", script)
    assert re.search(r"compose run --rm --no-deps -T -w /app --entrypoint env \"\$AGENT_SERVICE\" -i", script)
    for gone in ("AGENT_PY", "python -c \"$AGENT_PY\"", "JRVSEAL1", "X25519PrivateKey", "ChaCha20Poly1305"):
        assert gone not in script, gone
    source = TOOL.read_text(encoding="utf-8")
    assert "from cryptography" not in source and "import cryptography" not in source
    for gone in ("--gen-key", "--export-key", "--public-key", "backup_key.json", "dpapi_protect", "JRVSEAL1"):
        assert gone not in source, gone
    for name in ("gen_key", "write_key_file", "key_document", "export_key", "load_private_key", "seal_stream",
                 "SealError", "MAGIC", "KEY_FILE_NAME"):
        assert not hasattr(rad, name), name
    assert rad.open_sealed is bc.iter_open and rad.ARCHIVE == av.ARCHIVE
    for folder in (ROOT / "scripts" / "ops", ROOT / "services" / "utils", ROOT / "deploy" / "hostinger"):
        for path in folder.iterdir():
            if path.is_file() and path.suffix in (".py", ".sh", ".md"):
                assert "backup_key.json" not in path.read_text(encoding="utf-8", errors="replace"), path

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    store = bkey.default_key_file()
    assert store == tmp_path / "LocalAppData" / "JARVIS" / "backup" / "backup_key.dpapi"
    assert rad.backup_key.default_key_file() == store            # the restore tool's default is that same file
    assert bkey.main(["keygen"], wrap=_wrap, unwrap=_unwrap) == 0
    public = re.search(r"^BACKUP_RECIPIENT_PUBLIC_KEY=([0-9a-f]{64})$", capsys.readouterr().out, re.M)
    assert public is not None and store.is_file() and not store.read_bytes().lstrip().startswith(b"{")   # wrapped
    made = bkey.load_key(store, _unwrap)
    sealed_file = tmp_path / av.ARCHIVE
    out = io.BytesIO()
    av.seal_volumes(str(tmp_path / "empty-app"), bc.parse_public_key(public.group(1)), out)
    sealed_file.write_bytes(out.getvalue())
    monkeypatch.setattr(rad.backup_key, "dpapi_unwrap", _unwrap)   # this Windows user's DPAPI, in the test
    assert rad.main(["--check-file", str(sealed_file)]) == 0
    printed = capsys.readouterr().out
    assert "CHECK=ok" in printed and f"KEY={bc.fingerprint(made)} ({store})" in printed
    assert bc.private_key_hex(made) not in printed
    # the restore tool never writes a key file: the store is the only file under LOCALAPPDATA
    assert [p.name for p in (tmp_path / "LocalAppData").rglob("*") if p.is_file()] == ["backup_key.dpapi"]


def test_a_backup_never_opens_with_another_key_and_says_which_key_it_needs(tmp_path: Path, key, capsys) -> None:
    sealed = sealed_backup(tmp_path, key)
    mirror = tmp_path / "mirror" / TS
    mirror.mkdir(parents=True)
    (mirror / av.ARCHIVE).write_bytes(sealed)
    # the backup's MANIFEST names the key it was sealed to (AGENT_SEAL ... recipient=<fingerprint>)
    (mirror / "MANIFEST.txt").write_text(f"agent_data:    sealed via docker exec jarvis-agent: files=6 sealed="
                                         f"{len(sealed)} recipient={bc.fingerprint(key)}\n", encoding="utf-8")
    other = bc.generate_private_key()
    with pytest.raises(bc.SnapshotCryptoError, match="does not verify"):
        rad.check_file(mirror / av.ARCHIVE, other)
    assert rad.main(["--check-file", str(mirror / av.ARCHIVE), "--key-file",
                     str(offline_copy(tmp_path, other, "other.json"))]) == 1
    err = capsys.readouterr().err
    assert f"does not open with this key ({bc.fingerprint(other)})" in err
    assert f"the backup says it was sealed to the key {bc.fingerprint(key)}, this key is {bc.fingerprint(other)}" in err
    assert "--key-file <that file>" in err
    store = tmp_path / "store.dpapi"
    store.write_bytes(_wrap(bkey.key_document(other)))
    assert bkey.main(["decrypt", str(mirror / av.ARCHIVE), "--key-file", str(store)], wrap=_wrap, unwrap=_unwrap) == 1
    assert "FAILED=" in capsys.readouterr().out
    assert sorted(p.name for p in mirror.iterdir()) == ["MANIFEST.txt", av.ARCHIVE]   # no tar, no .part left


def _tar(members: list[tuple[str, bytes | None, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, kind, data in members:
            info = tarfile.TarInfo(name)
            if kind is not None:
                info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
            info.size = len(data) if kind in (None, tarfile.REGTYPE) else 0
            tar.addfile(info, io.BytesIO(data) if info.size else None)
    return buf.getvalue()


META = ("AGENT-BACKUP.json", None, json.dumps({"kind": "jarvis_agent_volumes", "v": 1}).encode())


@pytest.mark.parametrize(("members", "why"), [
    ([("../etc/cron.d/x", None, b"x"), META], "outside"),
    (([("/etc/passwd", None, b"x"), META]), "outside"),
    ([("conversations/../../x", None, b"x"), META], "outside"),
    ([("logs/agent.log", None, b"x"), META], "outside"),
    ([("conversations/link", tarfile.SYMTYPE, b""), META], "neither a folder nor a regular file"),
    ([("conversations/hard", tarfile.LNKTYPE, b""), META], "neither a folder nor a regular file"),
    ([("conversations/a.json", None, b"{}")], "record is missing"),
    ([META, ("data/late.json", None, b"{}")], "follows the AGENT-BACKUP.json record"),
    ([("conversations/a.json", None, b"{}"), ("AGENT-BACKUP.json", None, b"not json")], "not JSON"),
    ([("conversations/a.json", None, b"{}"), ("AGENT-BACKUP.json", None, b'{"kind": "jarvis_snapshot"}')],
     "not a JARVIS agent backup record"),
])
def test_the_check_refuses_anything_but_files_and_folders_of_the_three_roots(key, members: list, why: str) -> None:
    sealed = bc.seal_bytes(_tar(members), key.public_key())
    with pytest.raises(av.AgentVolumesError, match=why):
        av.inventory(bc.iter_open(io.BytesIO(sealed), key))


def test_the_check_authenticates_the_stream_up_to_its_final_chunk(tmp_path: Path, key) -> None:
    """The tar ends before the sealed stream does (its zero padding): the check still reads the stream to its final
    chunk, so a backup cut after its last member, or one with data after its final chunk, never passes."""
    sealed = sealed_backup(tmp_path, key, chunk_size=512)
    header, parts = _records(sealed)
    assert len(parts) > 3
    assert av.inventory(bc.iter_open(io.BytesIO(sealed), key)).files == 6
    for label, data in (("cut before the final chunk", header + b"".join(parts[:-1])),
                        ("data after the final chunk", sealed + parts[-1]),
                        ("a chunk dropped", header + b"".join(parts[:2] + parts[3:]))):
        with pytest.raises(bc.SnapshotCryptoError):
            av.inventory(bc.iter_open(io.BytesIO(data), key))
        assert label


# --------------------------------------------------------------------------- the VPS side of a restore


def _live(tmp_path: Path) -> Path:
    live = tmp_path / "live"
    (live / "conversations" / ".pre-restore-20260101T000000Z").mkdir(parents=True)
    (live / "conversations" / ".pre-restore-20260101T000000Z" / "older.json").write_text("older", encoding="utf-8")
    (live / "conversations" / "a_memory.json").write_text("live memory", encoding="utf-8")
    (live / "conversations" / "b_tasks.json").write_text("live tasks", encoding="utf-8")
    (live / "conversations" / ".dotfile").write_text("live dot", encoding="utf-8")
    (live / "data" / "identity").mkdir(parents=True)
    (live / "data" / "identity" / "identity.json").write_text("live identity", encoding="utf-8")
    return live


FAKE_MV = r'''#!/usr/bin/env bash
# mv for the move script: counts its calls and fails on call number MV_FAIL_AT (a disk error half-way)
n=$(( $(cat "$MV_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$MV_COUNT"
if [[ $n == "${MV_FAIL_AT:-0}" ]]; then echo "mv: cannot move '${2:-?}': Input/output error" >&2; exit 1; fi
exec /usr/bin/mv "$@"
'''


def run_move_script(tmp_path: Path, live: Path, plain: bytes, *, fail_at: int = 0,
                    want: int | str | None = None) -> subprocess.CompletedProcess:
    """The script the one-off agent container runs (MOVE_AND_EXTRACT), here under bash on a scratch folder; with
    fail_at, its mv fails on that call. want is its third argument, the byte count of the whole tar (default: what is
    sent)."""
    assert BASH is not None
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    (fakebin / "mv").write_bytes(FAKE_MV.encode())
    os.chmod(fakebin / "mv", 0o755)
    path = ([str(fakebin)] if fail_at else []) + [str(Path(BASH).parent), os.environ.get("PATH", "")]
    env = dict(os.environ, LC_ALL="C", MV_COUNT=_posix(tmp_path / "mv_count"), MV_FAIL_AT=str(fail_at),
               PATH=os.pathsep.join(path))
    size = str(len(plain) if want is None else want)
    return subprocess.run([BASH, "-c", rad.MOVE_AND_EXTRACT, "sh", STAMP, _posix(live), size], input=plain,
                          capture_output=True, timeout=120, check=False, env=env)


def _member_offset(plain: bytes, name: str) -> int:
    """Where a member's header starts in a plain tar: a stream cut there ends at a member boundary."""
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
        return next(info.offset for info in tar if info.name == name)


@needs_bash
def test_move_and_extract_moves_the_live_data_aside_and_never_deletes(tmp_path: Path, key) -> None:
    """Each volume's live content (hidden files too) goes into .pre-restore-<stamp>/, an earlier aside folder stays where
    it is, then tar extracts only the three folders. ASIDE_MADE comes as soon as the aside folder exists, before the
    first move; MOVED_ASIDE once the volume is done."""
    plain = bc.open_bytes(sealed_backup(tmp_path, key), key)
    live = _live(tmp_path)
    proc = run_move_script(tmp_path, live, plain)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out + proc.stderr.decode("utf-8", "replace")
    lines = out.splitlines()
    for volume, moved in (("conversations", 3), ("chroma_db", 0), ("data", 1)):
        made = lines.index(f"ASIDE_MADE {volume} .pre-restore-{STAMP}")
        assert lines[made + 1] == f"MOVED_ASIDE {volume} {moved} .pre-restore-{STAMP}", lines
    assert lines[-1] == "EXTRACTED"
    aside = live / "conversations" / f".pre-restore-{STAMP}"
    assert (aside / "a_memory.json").read_text(encoding="utf-8") == "live memory"
    assert (aside / ".dotfile").read_text(encoding="utf-8") == "live dot"
    assert (live / "conversations" / ".pre-restore-20260101T000000Z" / "older.json").exists()
    assert (live / "data" / f".pre-restore-{STAMP}" / "identity" / "identity.json").read_text(
        encoding="utf-8") == "live identity"
    assert MEMORY_SENTINEL in (live / "conversations" / "matloob_memory.json").read_text(encoding="utf-8")
    assert (live / "chroma_db" / "chroma.sqlite3").exists() and not (live / "AGENT-BACKUP.json").exists()
    # the same stamp again stops at its first mkdir, before any marker and before anything moves
    before = sorted(str(p.relative_to(live)) for p in live.rglob("*"))
    again = run_move_script(tmp_path, live, plain)
    assert again.returncode != 0 and "ASIDE_MADE" not in again.stdout.decode("utf-8", "replace")
    assert sorted(str(p.relative_to(live)) for p in live.rglob("*")) == before


@needs_bash
def test_move_and_extract_says_incomplete_for_a_stream_cut_at_a_member_boundary(tmp_path: Path, key) -> None:
    """C-128 review (medium): GNU tar exits 0 when its input ends at a member boundary once the three requested roots
    were seen (here inside data/), so a stream cut short used to read EXTRACTED and RESTORE_RC=0 while data/ held part
    of the backup. The script counts what arrives and says EXTRACTED only for the whole checked tar (its byte count is
    the third argument); else INCOMPLETE and exit 4, whoever reads it (the PC may be gone). Without that count it
    refuses before anything moves."""
    plain = bc.open_bytes(sealed_backup(tmp_path, key), key)
    cut = _member_offset(plain, "data/identity/identity.json")
    live = _live(tmp_path)
    proc = run_move_script(tmp_path, live, plain[:cut], want=len(plain))
    out, err = proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 4, out + err
    assert out.splitlines()[-1] == f"INCOMPLETE {cut} of {len(plain)} bytes arrived" and "EXTRACTED" not in out, out
    assert (live / "data" / "identity").is_dir() and not (live / "data" / "identity" / "identity.json").exists()
    assert (live / "data" / f".pre-restore-{STAMP}" / "identity" / "identity.json").read_text(
        encoding="utf-8") == "live identity"                                   # the data of before stays aside
    second = _live(tmp_path / "second")
    before = sorted(str(p.relative_to(second)) for p in second.rglob("*"))
    refused = run_move_script(tmp_path, second, plain, want="")
    out = refused.stdout.decode("utf-8", "replace")
    assert refused.returncode == 3 and out.startswith("RESTORE_REFUSED ") and "ASIDE_MADE" not in out, out
    assert sorted(str(p.relative_to(second)) for p in second.rglob("*")) == before


@needs_bash
def test_a_move_that_fails_half_way_is_reported_as_moved(tmp_path: Path, key) -> None:
    """C-128 review (low a): mv fails on its second call. One file already sits in .pre-restore-<stamp>/ and the next
    is still in place; the script had printed ASIDE_MADE before its first mv (never MOVED_ASIDE for that volume), so
    the run is ACT NOW (exit 3) naming the folder, never 'nothing was moved'."""
    plain = bc.open_bytes(sealed_backup(tmp_path, key), key)
    live = _live(tmp_path)
    proc = run_move_script(tmp_path, live, plain, fail_at=2)
    out, err = proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    assert proc.returncode != 0, out + err
    assert out.splitlines() == [f"ASIDE_MADE conversations .pre-restore-{STAMP}"], out
    assert (tmp_path / "mv_count").read_text(encoding="utf-8").strip() == "2" and "Input/output error" in err
    aside = live / "conversations" / f".pre-restore-{STAMP}"
    assert sorted(p.name for p in aside.iterdir()) == ["a_memory.json"]
    assert (live / "conversations" / "b_tasks.json").read_text(encoding="utf-8") == "live tasks"   # still in place
    assert not (live / "data" / f".pre-restore-{STAMP}").exists()                                   # never reached
    lines: list[str] = []
    with pytest.raises(rad.RestoreError) as exc_info:
        rad._extract_outcome(proc.returncode, out + f"RESTORE_RC={proc.returncode}\n", err, broke=[], when=STAMP,
                             backup_dir=PRE_RESTORE, out=lines.append)
    message = str(exc_info.value)
    assert exc_info.value.code == rad.EXIT_ACT_NOW == 3, message
    assert "moving the live data aside failed half-way in conversations" in message
    assert f".pre-restore-{STAMP}/ in conversations holds what was there (only part of it when a move failed " \
           "half-way, the rest is still in place)" in message
    assert "restore 2026-10-21_0905Z-pre-data-restore" in message and "nothing was moved" not in message
    assert f"ASIDE_MADE conversations .pre-restore-{STAMP}" in lines


class FakeVps:
    """ssh to the VPS: the four remote steps of a restore, told apart by their script (the last ssh argument). The
    extract step answers with canned output, or (live=<folder>) runs the real move script there on what it received,
    with the byte count the extract script names. hang: steps whose first wait times out; disk: the free KiB step 1
    reports per disk; write_error: what ssh's stdin raises on a write in the extract step."""

    def __init__(self, sealed: bytes, *, containers: str = "exited", image: str = "yes", extract: str = "ok",
                 damage: bool = False, pre_sealed: bool = True, agent_data: str = "",
                 pre_agent_data: str = "sealed via docker exec jarvis-agent: files=6",
                 refuse_extract: str = "", live: Path | None = None, tmp: Path | None = None,
                 mv_fail_at: int = 0, hang: set[str] | None = None, disk: dict[str, int] | None = None,
                 write_error: type[BaseException] | None = None) -> None:
        self.sealed = sealed
        self.containers = containers
        self.image = image
        self.extract = extract                # ok | fails | move_fails | dropped | dropped_silent | not_started
        self.damage = damage
        self.pre_sealed = pre_sealed          # the pre-data-restore backup holds agent-volumes.tar.enc
        self.agent_data = agent_data or "sealed via docker exec jarvis-agent: files=6"
        self.pre_agent_data = pre_agent_data  # its MANIFEST agent_data line
        self.refuse_extract = refuse_extract  # the extract step's RESTORE_REFUSED reason (the lock, a container)
        self.live, self.tmp, self.mv_fail_at = live, tmp, mv_fail_at
        self.hang, self.disk, self.write_error = set(hang or ()), disk or {}, write_error
        self.steps: list[str] = []
        self.scripts: dict[str, str] = {}
        self.timeouts: dict[str, float | None] = {}
        self.killed: list[str] = []
        self.received = b""

    def __call__(self, argv: list[str], stdin=None, stdout=None, stderr=None):  # noqa: ANN001  (Popen's shape)
        script = argv[-1]
        assert argv[0] == "ssh" and "BatchMode=yes" in argv and script.startswith("bash -c ")
        if "BRAIN_NOW" in script:
            step = "info"
        elif script.startswith("bash -c 'cat "):
            step = "download"
        elif "pre-data-restore" in script:
            step = "backup"
        else:
            assert "ASIDE_MADE" in script and "MOVED_ASIDE" in script and "--no-same-owner --no-overwrite-dir" in script
            step = "extract"
        self.steps.append(step)
        self.scripts[step] = script
        return _Proc(self, step, stdout)


class _Sink(io.BytesIO):
    """ssh's stdin: what the restore streams stays readable after it closes the pipe."""

    def close(self) -> None:
        return None


class _FailingSink(_Sink):
    """ssh's stdin that refuses a write with an error that is not the VPS stopping to read (a bug of this PC)."""

    def __init__(self, error: type[BaseException]) -> None:
        super().__init__()
        self.error = error

    def write(self, data) -> int:  # noqa: ANN001
        raise self.error("write refused")


class _LateOutput:
    """ssh's stdout or stderr for an extract step that runs the real script once its stdin closed."""

    def __init__(self) -> None:
        self.data = b""
        self.ready = threading.Event()

    def read(self) -> bytes:
        self.ready.wait(120)
        return self.data


CANNED = {
    "ok": (0, f"ASIDE_MADE conversations .pre-restore-{STAMP}\nMOVED_ASIDE conversations 3 .pre-restore-{STAMP}\n"
              f"ASIDE_MADE chroma_db .pre-restore-{STAMP}\nMOVED_ASIDE chroma_db 1 .pre-restore-{STAMP}\n"
              f"ASIDE_MADE data .pre-restore-{STAMP}\nMOVED_ASIDE data 1 .pre-restore-{STAMP}\nEXTRACTED\nRESTORE_RC=0\n"),
    "fails": (2, f"ASIDE_MADE conversations .pre-restore-{STAMP}\nMOVED_ASIDE conversations 3 .pre-restore-{STAMP}\n"
                 f"ASIDE_MADE chroma_db .pre-restore-{STAMP}\nMOVED_ASIDE chroma_db 1 .pre-restore-{STAMP}\n"
                 f"ASIDE_MADE data .pre-restore-{STAMP}\nMOVED_ASIDE data 1 .pre-restore-{STAMP}\n"
                 "tar: unexpected EOF\nRESTORE_RC=2\n"),
    "move_fails": (1, f"ASIDE_MADE conversations .pre-restore-{STAMP}\nRESTORE_RC=1\n"),
    "dropped": (255, f"ASIDE_MADE conversations .pre-restore-{STAMP}\n"),
    "dropped_silent": (255, ""),
    "not_started": (1, "RESTORE_RC=1\n"),
}


class _Proc:
    def __init__(self, vps: FakeVps, step: str, stdout) -> None:  # noqa: ANN001
        self.vps, self.step, self.target = vps, step, stdout
        self.returncode = 0
        self.stdin = _FailingSink(vps.write_error) if step == "extract" and vps.write_error else _Sink()
        self.stdout: io.BytesIO | _LateOutput = io.BytesIO()
        self.stderr: io.BytesIO | _LateOutput = io.BytesIO()
        self.hung = False
        if step == "extract":
            self._extract_output()

    def kill(self) -> None:
        self.vps.killed.append(self.step)
        self.returncode = -9

    def _info(self) -> bytes:
        sealed = self.vps.sealed
        lines = [f"SEALED {len(sealed)} {hashlib.sha256(sealed).hexdigest()}",
                 f"AGENT_DATA {self.vps.agent_data}",
                 "BRAIN_AT_BACKUP pc",
                 f"CTR jarvis-agent {self.vps.containers}", "CTR jarvis-agent-svc exited",
                 "BRAIN_NOW pc", f"IMAGE {self.vps.image}"]
        lines += [f"DISK {label} {kib}" for label, kib in self.vps.disk.items()]
        return ("\n".join(lines) + "\n").encode()

    def communicate(self, timeout=None):  # noqa: ANN001
        if self.step in self.vps.hang and not self.hung:   # the first wait runs out; the one after the kill ends
            self.hung = True
            self.vps.timeouts[self.step] = timeout
            raise subprocess.TimeoutExpired(["ssh"], timeout)
        if self.hung:
            return b"", b""
        self.vps.timeouts[self.step] = timeout
        if self.step == "info":
            return self._info(), b""
        if self.step == "download":
            data = self.vps.sealed[:-1] + b"\x00" if self.vps.damage else self.vps.sealed
            self.target.write(data)
            return None, b""
        backup = (f"BACKUP_DIR={PRE_RESTORE}\nBACKUP_RC=0\n"
                  f"PRE_RESTORE_SEALED {'yes' if self.vps.pre_sealed else 'no'}\n"
                  f"PRE_RESTORE_AGENT_DATA {self.vps.pre_agent_data}\n")
        return backup.encode(), b""

    def _extract_output(self) -> None:
        if self.vps.refuse_extract:   # refused before anything moved (the remote script exits 3)
            self.returncode = 3
            self.stdout = io.BytesIO(f"RESTORE_REFUSED {self.vps.refuse_extract}\n".encode())
            return
        if self.vps.live is not None:
            self.stdout, self.stderr = _LateOutput(), _LateOutput()
            return
        self.returncode, text = CANNED[self.vps.extract]
        self.stdout = io.BytesIO(text.encode())
        if self.vps.extract.startswith("dropped"):
            self.stderr = io.BytesIO(b"Connection to vps.invalid closed by remote host.\n")

    def wait(self, timeout=None) -> int:  # noqa: ANN001
        self.vps.received = self.stdin.getvalue()
        if self.step == "extract" and self.vps.live is not None and isinstance(self.stdout, _LateOutput):
            assert self.vps.tmp is not None and isinstance(self.stderr, _LateOutput)
            # the byte count the extract script hands the move script ('sh -c <script> sh <stamp> /app <bytes>')
            size = re.search(r" sh [0-9]{8}T[0-9]{6}Z /app ([0-9]+)$", self.vps.scripts["extract"], re.M)
            proc = run_move_script(self.vps.tmp, self.vps.live, self.vps.received, fail_at=self.vps.mv_fail_at,
                                   want=size.group(1) if size else None)
            self.returncode = proc.returncode
            self.stdout.data = proc.stdout + f"RESTORE_RC={proc.returncode}\n".encode()
            self.stderr.data = proc.stderr
            self.stdout.ready.set()
            self.stderr.ready.set()
        return self.returncode


def _options(tmp_path: Path, key: bc.X25519PrivateKey, *, dry_run: bool) -> object:
    return rad.Options(ts=TS, dry_run=dry_run, key_file=offline_copy(tmp_path, key), host="root@vps.invalid",
                       ssh_key=tmp_path / "id_test")


def test_restore_dry_run_checks_everything_and_changes_nothing(tmp_path: Path, key) -> None:
    vps = FakeVps(sealed_backup(tmp_path, key))
    lines: list[str] = []
    code = rad.restore(_options(tmp_path, key, dry_run=True), popen=vps, out=lines.append, stamp=STAMP,
                       workdir=tmp_path)
    text = "\n".join(lines)
    assert code == 0, text
    assert vps.steps == ["info", "download"]                      # no backup, no extraction
    assert "MEMBERS files=6 dirs=" in text and "SQLITE online=2 raw=0 bad=0" in text and "CHECK=ok" in text
    assert "VERDICT: a real run would go ahead" in text and f".pre-restore-{STAMP}" in text
    assert f"KEY={bc.fingerprint(key)}" in text
    assert not any(s in text for s in SENTINELS)
    assert not list(tmp_path.glob("jarvis-agent-restore-*"))      # the downloaded sealed file is gone again


def test_restore_streams_exactly_the_checked_tar_after_a_backup(tmp_path: Path, key) -> None:
    sealed = sealed_backup(tmp_path, key)
    vps = FakeVps(sealed)
    lines: list[str] = []
    code = rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                       workdir=tmp_path)
    text = "\n".join(lines)
    assert code == 0, text
    assert vps.steps == ["info", "download", "backup", "extract"]
    assert vps.received == bc.open_bytes(sealed, key)   # the plain tar, only over ssh
    # the move script gets that tar's whole length: it says EXTRACTED only when all of it arrived
    assert re.search(rf" sh {STAMP} /app {len(vps.received)}$", vps.scripts["extract"], re.M)
    assert f"TAR bytes={len(vps.received)} (the VPS extracts it as whole only when all of them arrive)" in text
    assert f"PRE_RESTORE_BACKUP={PRE_RESTORE}" in text
    assert "PRE_RESTORE_AGENT_DATA=sealed via docker exec jarvis-agent: files=6" in text
    assert "EXTRACTED" in text and "RESTORED from " + TS in text and "never deleted" in text
    assert ("Undo: restore 2026-10-21_0905Z-pre-data-restore the same way (it holds the live agent data of before"
            in text)
    assert not any(s in text for s in SENTINELS)


@needs_bash
def test_restore_end_to_end_with_a_move_that_fails_half_way(tmp_path: Path, key) -> None:
    """The real move script on what the restore streamed, with mv failing on its second call: ACT NOW (exit 3) naming
    the folder that holds the part that moved, never 'the restore did not start' or 'nothing was moved'."""
    live = _live(tmp_path)
    vps = FakeVps(sealed_backup(tmp_path, key), live=live, tmp=tmp_path, mv_fail_at=2)
    lines: list[str] = []
    with pytest.raises(rad.RestoreError) as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                    workdir=tmp_path)
    message = str(exc_info.value)
    assert exc_info.value.code == 3 and vps.steps == ["info", "download", "backup", "extract"], message
    assert f".pre-restore-{STAMP}/ in conversations" in message and "half-way" in message
    assert "nothing was moved" not in message and "did not start" not in message
    assert f"ASIDE_MADE conversations .pre-restore-{STAMP}" in lines and "RESTORE_RC=1" in lines
    assert not any(line.startswith("RESTORED") for line in lines)
    assert (live / "conversations" / f".pre-restore-{STAMP}" / "a_memory.json").exists()
    assert (live / "conversations" / "b_tasks.json").exists()


def test_restore_without_any_agent_volume_before_needs_no_sealed_copy(tmp_path: Path, key) -> None:
    """agent_data: none (no jarvis_agent_* volume yet): there is no live data to lose, so the restore goes on, as
    deploy.sh's agent_backup_check accepts it, and the undo line says so."""
    vps = FakeVps(sealed_backup(tmp_path, key), pre_sealed=False,
                  pre_agent_data="none (no jarvis_agent_* volume: the VPS brain never ran here)")
    lines: list[str] = []
    code = rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                       workdir=tmp_path)
    text = "\n".join(lines)
    assert code == 0 and vps.steps == ["info", "download", "backup", "extract"], text
    assert "Undo: there was no agent volume before (agent_data: none" in text


@pytest.mark.parametrize(("vps_kwargs", "dry_run", "steps", "code", "why"), [
    ({"containers": "running"}, False, ["info", "download"], 1, "running: stop the VPS brain first"),
    ({"containers": "running"}, True, ["info", "download"], 1, "a real run would REFUSE"),
    ({"image": "no"}, False, ["info", "download"], 1, "no jarvis-agent:hostinger image"),
    ({"damage": True}, False, ["info", "download"], 1, "arrived damaged"),
    ({"extract": "fails"}, False, ["info", "download", "backup", "extract"], 3,
     f"the extraction failed (exit 2) after the live data was moved aside: .pre-restore-{STAMP}/ in conversations, "
     "chroma_db, data"),
    # C-128 review (low a): the aside folder was made and a move failed before the volume was done
    ({"extract": "move_fails"}, False, ["info", "download", "backup", "extract"], 3,
     f"moving the live data aside failed half-way in conversations (exit 1) after the live data was moved aside: "
     f".pre-restore-{STAMP}/ in conversations"),
    # the connection broke off after the first marker, and with no output at all: both ACT NOW, naming the folder
    ({"extract": "dropped"}, False, ["info", "download", "backup", "extract"], 3,
     f"the VPS did not report the result (ssh exit 255: the connection may have broken off) after the live data was "
     f"moved aside: .pre-restore-{STAMP}/ in conversations"),
    ({"extract": "dropped_silent"}, False, ["info", "download", "backup", "extract"], 3,
     f"so how far the restore came is not known: look for .pre-restore-{STAMP}/ in conversations, chroma_db and data"),
    # the one-off container never ran the move script (RESTORE_RC reported, no marker): nothing moved
    ({"extract": "not_started"}, False, ["info", "download", "backup", "extract"], 1,
     "the restore did not start on the VPS (exit 1, before any .pre-restore folder was made): nothing was moved"),
    # C-128 review: the pre-data-restore backup could not seal the live agent data, so the restore could never be
    # undone: refused before anything moves (no extract step at all)
    ({"pre_sealed": False, "pre_agent_data": "SKIPPED: .env.agent has no BACKUP_RECIPIENT_PUBLIC_KEY (64 hex "
      "digits), and agent data is never written in plain text"}, False, ["info", "download", "backup"], 1,
     "holds no agent data (agent_data: SKIPPED: .env.agent has no BACKUP_RECIPIENT_PUBLIC_KEY"),
    ({"pre_sealed": False, "pre_agent_data": "FAILED via docker exec jarvis-agent (exit 1): AGENT_SEAL_ERROR OSError"},
     False, ["info", "download", "backup"], 1, "so the restore could not be undone: nothing was moved"),
    ({"pre_sealed": False, "pre_agent_data": ""}, False, ["info", "download", "backup"], 1,
     "(agent_data: not recorded)"),
    # a deploy or rollback took the deploy lock between the backup and the extraction
    ({"refuse_extract": "a deploy or rollback holds /root/jarvis-backups/.deploy.lock"}, False,
     ["info", "download", "backup", "extract"], 1,
     "refused on the VPS (since the backup: a deploy or rollback holds /root/jarvis-backups/.deploy.lock): nothing "
     "was moved"),
], ids=["running", "running-dry-run", "no-image", "damaged", "extract-fails", "move-fails-half-way", "dropped",
        "dropped-silent", "not-started", "pre-backup-skipped", "pre-backup-failed", "pre-backup-unrecorded",
        "lock-taken-meanwhile"])
def test_restore_refusals_and_failures(tmp_path: Path, key, vps_kwargs: dict, dry_run: bool, steps: list[str],
                                       code: int, why: str) -> None:
    vps = FakeVps(sealed_backup(tmp_path, key), **vps_kwargs)
    lines: list[str] = []
    try:
        got = rad.restore(_options(tmp_path, key, dry_run=dry_run), popen=vps, out=lines.append, stamp=STAMP,
                          workdir=tmp_path)
        message = "\n".join(lines)
    except rad.RestoreError as exc:
        got, message = exc.code, str(exc)
    assert got == code and why in message, message
    assert vps.steps == steps


def test_a_stream_that_breaks_off_is_reported_even_when_tar_says_ok(tmp_path: Path, key,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt and braces: the file is checked whole before it streams, but if the decryption still broke off mid-stream,
    a tar cut at a member boundary could look complete; the run then says ACT NOW (exit 3), never RESTORED."""
    vps = FakeVps(sealed_backup(tmp_path, key))
    real, calls = rad.open_sealed, []

    def flaky(src, k):  # noqa: ANN001
        calls.append(1)
        if len(calls) == 1:          # the check: whole and fine
            yield from real(src, k)
            return
        yield next(real(src, k))     # the stream: one chunk, then the break
        raise bc.SnapshotCryptoError("a chunk does not verify (wrong key or damaged file)")

    monkeypatch.setattr(rad, "open_sealed", flaky)
    lines: list[str] = []
    with pytest.raises(rad.RestoreError, match="the decrypted stream broke off") as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, workdir=tmp_path)
    assert exc_info.value.code == 3 and vps.steps == ["info", "download", "backup", "extract"]
    assert not any(line.startswith("RESTORED") for line in lines)


@needs_bash
def test_a_local_read_error_mid_stream_is_act_now_even_when_tar_exits_0(tmp_path: Path, key,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """C-128 review (medium), the reproduction: an OSError of the chunk source (reading the local sealed temp file)
    after the sha256 re-check was taken for the VPS that stopped reading. ssh's stdin closed at a chunk boundary, GNU
    tar exited 0 at the member boundary inside data/, the script printed EXTRACTED and RESTORE_RC=0, and the run said
    RESTORED (exit 0). Now any error of the source is recorded (ACT NOW, exit 3, naming the folders), and the real move
    script, given the checked tar's byte count, says INCOMPLETE instead of EXTRACTED."""
    sealed = sealed_backup(tmp_path, key, chunk_size=512)   # one tar block per chunk: the cut lands on any boundary
    plain = bc.open_bytes(sealed, key)
    cut = _member_offset(plain, "data/identity/identity.json")
    live = _live(tmp_path)
    vps = FakeVps(sealed, live=live, tmp=tmp_path)
    real, calls = rad.open_sealed, []

    def failing_read(src, k):  # noqa: ANN001
        calls.append(1)
        sent = 0
        for chunk in real(src, k):
            if len(calls) > 1 and sent == cut:   # the stream; the check before it read the file whole
                raise OSError(5, "Input/output error")
            sent += len(chunk)
            yield chunk

    monkeypatch.setattr(rad, "open_sealed", failing_read)
    lines: list[str] = []
    with pytest.raises(rad.RestoreError) as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                    workdir=tmp_path)
    message = str(exc_info.value)
    assert exc_info.value.code == 3, message
    assert "the decrypted stream broke off (OSError reading the local sealed file: Input/output error)" in message
    assert f".pre-restore-{STAMP}/ in conversations, chroma_db, data holds what was there" in message
    assert vps.received == plain[:cut]
    assert f"INCOMPLETE {cut} of {len(plain)} bytes arrived" in lines and "EXTRACTED" not in lines, lines
    assert not any(line.startswith("RESTORED") for line in lines)
    assert not (live / "data" / "identity" / "identity.json").exists()   # the part tar got, as the review saw it


def test_ctrl_c_or_an_error_of_this_pc_while_the_data_streams_is_act_now(tmp_path: Path, key,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """C-128 review (medium): a Ctrl-C (KeyboardInterrupt) once step 4 began ended in a traceback, not ACT NOW, while
    the VPS kept .pre-restore-<stamp>/ and part of the data. Now ssh is stopped and the run is exit 3 naming the folders
    to look for; an unexpected error of this PC (ssh's stdin refusing a write with ValueError) is the same."""
    sealed = sealed_backup(tmp_path, key)
    vps = FakeVps(sealed)
    real, calls = rad.open_sealed, []

    def interrupted(src, k):  # noqa: ANN001
        calls.append(1)
        if len(calls) == 1:          # the check: whole
            yield from real(src, k)
            return
        yield next(real(src, k))     # the stream: one chunk, then Ctrl-C
        raise KeyboardInterrupt

    monkeypatch.setattr(rad, "open_sealed", interrupted)
    lines: list[str] = []
    try:
        with pytest.raises(rad.RestoreError) as exc_info:
            rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                        workdir=tmp_path)
    except KeyboardInterrupt:
        pytest.fail("a Ctrl-C during step 4 reached the caller as a traceback, not as ACT NOW")
    message = str(exc_info.value)
    assert exc_info.value.code == 3 and "KeyboardInterrupt" in message, message
    assert f"look for .pre-restore-{STAMP}/ in conversations, chroma_db and data before anything else" in message
    assert "INCOMPLETE" in message and vps.killed == ["extract"]
    assert not any(line.startswith("RESTORED") for line in lines)
    monkeypatch.setattr(rad, "open_sealed", real)
    vps = FakeVps(sealed, write_error=ValueError)
    with pytest.raises(rad.RestoreError) as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lambda _l: None, stamp=STAMP,
                    workdir=tmp_path)
    assert exc_info.value.code == 3 and "ValueError" in str(exc_info.value) and vps.killed == ["extract"]


def test_a_step_that_does_not_end_in_time_stops_ssh_and_moves_nothing(tmp_path: Path, key) -> None:
    """C-128 review (low): communicate's TimeoutExpired was never caught, so ssh stayed alive (the VPS backup kept the
    deploy lock; on Windows the temp folder could not be removed while ssh held the sealed file) and the run ended in a
    traceback; a 1.5 GB backup over 2 MB/s could never come down within the flat 600 s. The download's cap now grows
    with the sealed size, the backup's is --backup-timeout, and a step that runs out stops ssh (killed, then reaped)
    and is exit 1 with nothing moved."""
    sealed = sealed_backup(tmp_path, key)
    vps = FakeVps(sealed, hang={"download"})
    with pytest.raises(rad.RestoreError) as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=True), popen=vps, out=lambda _l: None, stamp=STAMP,
                    workdir=tmp_path)
    message = str(exc_info.value)
    assert exc_info.value.code == 1 and "the sealed file did not arrive within" in message, message
    assert "nothing was changed" in message and vps.killed == ["download"]
    assert vps.timeouts["download"] == rad.download_timeout(len(sealed)) == 600 + len(sealed) // (256 * 1024)
    assert rad.download_timeout(1_500_000_000) > 1_500_000_000 // (2 << 20)   # 1.5 GB at 2 MB/s fits
    assert not list(tmp_path.glob("jarvis-agent-restore-*"))                  # the temp folder is gone
    opts = rad.Options(ts=TS, dry_run=False, key_file=offline_copy(tmp_path, key), host="root@vps.invalid",
                       ssh_key=tmp_path / "id_test", backup_timeout=7)
    vps = FakeVps(sealed, hang={"backup"})
    with pytest.raises(rad.RestoreError) as exc_info:
        rad.restore(opts, popen=vps, out=lambda _l: None, stamp=STAMP, workdir=tmp_path)
    message = str(exc_info.value)
    assert exc_info.value.code == 1 and "did not report within 7 s" in message and "--backup-timeout" in message
    assert "nothing was moved" in message and vps.killed == ["backup"] and vps.timeouts["backup"] == 7
    assert vps.steps == ["info", "download", "backup"]
    assert rad.parse_args([TS, "--backup-timeout", "3600"]).backup_timeout == 3600
    assert rad.parse_args([TS]).backup_timeout == rad.BACKUP_TIMEOUT_S == 1800
    with pytest.raises(SystemExit):
        rad.parse_args([TS, "--backup-timeout", "0"])


def test_restore_refuses_when_a_vps_disk_is_short_before_anything_changes(tmp_path: Path, key) -> None:
    """C-128 review (low): a restore writes a whole pre-data-restore backup, then a second copy of the data while the
    old one stays aside, on a disk the other apps share. Step 1 reads the free space of Docker's root dir and of
    /root/jarvis-backups: below 15 GB (deploy.sh's floor for the VPS brain) or 3 x the sealed file, whichever is more,
    it refuses with nothing changed (the dry run says a real run would), and step 3 checks again under the lock."""
    sealed = sealed_backup(tmp_path, key)
    gib = 1 << 20   # KiB
    vps = FakeVps(sealed, disk={"docker": 10 * gib, "backups": 200 * gib})
    lines: list[str] = []
    with pytest.raises(rad.RestoreError) as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                    workdir=tmp_path)
    message = str(exc_info.value)
    assert exc_info.value.code == 1 and "only 10 GB free on the VPS (docker disk): a restore needs 15 GB" in message
    assert "nothing was changed" in message and vps.steps == ["info", "download"], message
    lines = []
    vps = FakeVps(sealed, disk={"docker": 10 * gib, "backups": 200 * gib})
    assert rad.restore(_options(tmp_path, key, dry_run=True), popen=vps, out=lines.append, stamp=STAMP,
                       workdir=tmp_path) == 1
    assert "VERDICT: a real run would REFUSE: only 10 GB free on the VPS (docker disk)" in "\n".join(lines)
    lines = []
    vps = FakeVps(sealed, disk={"docker": 40 * gib, "backups": 40 * gib})
    assert rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                       workdir=tmp_path) == 0
    assert "DISK ok: 40 GB free on the VPS (a restore needs 15 GB)" in lines
    assert f'-lt {15 * gib} ]' in vps.scripts["backup"]          # step 3 checks the same need under the lock
    assert rad.disk_need_kib(6 << 30) == 18 * gib and rad.disk_need_kib(1 << 30) == 15 * gib
    lines = []
    vps = FakeVps(sealed)                                        # no DISK lines: said, and step 3 still checks
    assert rad.restore(_options(tmp_path, key, dry_run=False), popen=vps, out=lines.append, stamp=STAMP,
                       workdir=tmp_path) == 0
    assert any(line.startswith("WARN: the free space on the VPS disks could not be read") for line in lines), lines


def test_restore_refuses_a_file_sealed_to_another_key_and_names_the_key_it_needs(tmp_path: Path, key) -> None:
    other = bc.generate_private_key()
    vps = FakeVps(sealed_backup(tmp_path, other),
                  agent_data=f"sealed via docker exec jarvis-agent: files=6 sealed=1 recipient={bc.fingerprint(other)}")
    with pytest.raises(rad.RestoreError, match=f"does not open with this key \\({bc.fingerprint(key)}\\)") as exc_info:
        rad.restore(_options(tmp_path, key, dry_run=True), popen=vps, out=lambda _l: None, workdir=tmp_path)
    assert f"sealed to the key {bc.fingerprint(other)}, this key is {bc.fingerprint(key)}" in str(exc_info.value)
    assert exc_info.value.code == 1 and vps.steps == ["info", "download"]


def test_the_remote_scripts_refuse_while_an_agent_container_runs_and_quote_everything() -> None:
    for script in (rad.backup_script(15 << 20), rad.extract_script(STAMP, 20480)):
        for ctr in ("jarvis-agent", "jarvis-agent-svc"):
            assert f'docker container inspect -f "{{{{.State.Running}}}}" {ctr}' in script
        assert script.index("RESTORE_REFUSED") < script.index("docker compose" if "compose" in script else "bash")
        # deploy.sh's lock, taken before the container check and held to the end (deploy.sh and rollback.sh wait)
        lock = script.index("exec 9>/root/jarvis-backups/.deploy.lock")
        assert lock < script.index("flock -n 9") < script.index("docker container inspect")
        assert "RESTORE_REFUSED a deploy or rollback holds /root/jarvis-backups/.deploy.lock" in script
        assert "flock -u" not in script and "exec 9>&-" not in script
    extract = rad.extract_script(STAMP, 20480)
    assert "run --rm --no-deps -T --entrypoint env agent -i" in extract and "up " not in extract
    assert "'set -eu" in extract   # MOVE_AND_EXTRACT travels as one quoted argument
    assert re.search(r" sh 20261021T090500Z /app 20480$", extract, re.M)   # the stamp, the base, the tar's bytes
    backup = rad.backup_script(15 << 20)
    assert backup.index("docker container inspect") < backup.index("df -Pk") < backup.index("pre-data-restore")
    # the marker comes right after the aside folder exists and before the first mv
    move = rad.MOVE_AND_EXTRACT
    assert move.index('mkdir "$aside"') < move.index('echo "ASIDE_MADE') < move.index("mv --")
    assert move.index("mv --") < move.index('echo "MOVED_ASIDE')
    assert rad.ssh_argv("h", Path("k"), "echo 'x'")[-1] == "bash -c 'echo '\"'\"'x'\"'\"''"
    info = rad.info_script(TS)
    assert "agent-volumes.tar.enc" in info and "docker" in info and "rm " not in info and " mv " not in info


FAKE_REMOTE_DOCKER = r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/docker_calls.log"
if [[ $1 == container && $2 == inspect ]]; then
  for ctr in ${RUNNING:-}; do [[ ${!#} == "$ctr" ]] && { echo true; exit 0; }; done
  echo false
  exit 0
fi
if [[ $1 == compose ]]; then
  cat > /dev/null
  if { printf '' >&9; } 2>/dev/null; then echo "LOCK_FD_HELD"; fi   # the restore's bash still holds the lock
  echo "EXTRACTED"
fi
'''
FAKE_REMOTE_FLOCK = ('#!/usr/bin/env bash\n[[ -f $FAKE_DIR/flock_busy ]] && exit 1\n'
                     'printf "%s\\n" "$*" >> "$FAKE_DIR/flock_calls.log"\n')
FAKE_REMOTE_BACKUP = r'''#!/usr/bin/env bash
# the tools copy of backup_vps.sh: a new backup folder, with or without the sealed agent data, BACKUP_DIR= last
mkdir -p "$FAKE_DIR/backups"
d="$FAKE_DIR/backups/2026-10-21_090$(ls "$FAKE_DIR/backups" | wc -l | tr -d ' ')Z-$1"
mkdir "$d"
printf 'agent_data:    %s\n' "$FAKE_AGENT_DATA" > "$d/MANIFEST.txt"
if [[ -n ${FAKE_SEALED:-} ]]; then printf 'JRVSNAP1' > "$d/agent-volumes.tar.enc"; fi
echo "[backup_vps] WARNING: agent data not backed up: $FAKE_AGENT_DATA" >&2
echo "BACKUP_DIR=$d"
'''


@needs_bash
def test_the_remote_scripts_take_the_deploy_lock_and_report_the_pre_restore_backup(tmp_path: Path) -> None:
    """The two remote steps under bash with a fake docker, flock and tools copy of backup_vps.sh: a busy deploy lock
    refuses before anything else (no docker call); the lock's descriptor stays open in the one-off container's docker
    call; the backup step reports whether the pre-data-restore backup sealed the agent data, and its agent_data line."""
    fake = tmp_path / "fake"
    for name, body in (("docker", FAKE_REMOTE_DOCKER), ("flock", FAKE_REMOTE_FLOCK)):
        (fake / "bin").mkdir(parents=True, exist_ok=True)
        (fake / "bin" / name).write_bytes(body.encode())
        os.chmod(fake / "bin" / name, 0o755)
    (fake / "tools").mkdir()
    (fake / "tools" / "backup_vps.sh").write_bytes(FAKE_REMOTE_BACKUP.encode())
    (fake / "compose").mkdir()

    def remote(script: str, **extra: str) -> tuple[int, str]:
        script = (script.replace(rad.DEPLOY_LOCK, _posix(fake / ".deploy.lock"))
                  .replace(rad.TOOLS_DIR, _posix(fake / "tools"))
                  .replace(rad.VPS_COMPOSE_DIR, _posix(fake / "compose")))
        env = dict(os.environ, PATH=os.pathsep.join([str(fake / "bin"), str(Path(BASH).parent)]),
                   FAKE_DIR=_posix(fake), **extra)
        proc = subprocess.run([BASH, "-c", script], input=b"", capture_output=True, timeout=60, check=False, env=env)
        return proc.returncode, proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")

    calls = fake / "docker_calls.log"
    (fake / "flock_busy").touch()
    for script in (rad.backup_script(15 << 20), rad.extract_script(STAMP, 20480)):
        code, out = remote(script)
        assert code == 3 and "RESTORE_REFUSED a deploy or rollback holds " in out, out
        assert not calls.exists() and not (fake / "backups").exists()
    (fake / "flock_busy").unlink()
    code, out = remote(rad.extract_script(STAMP, 20480), RUNNING="jarvis-agent-svc")
    assert code == 3 and "RESTORE_REFUSED jarvis-agent-svc is running" in out, out
    assert "compose" not in calls.read_text(encoding="utf-8")
    code, out = remote(rad.extract_script(STAMP, 20480))
    assert code == 0 and "LOCK_FD_HELD" in out and "EXTRACTED" in out and "RESTORE_RC=0" in out, out
    assert (fake / "flock_calls.log").read_text(encoding="utf-8").splitlines() == ["-n 9", "-n 9"]
    code, out = remote(rad.backup_script(15 << 20), FAKE_SEALED="1",
                       FAKE_AGENT_DATA="sealed via docker exec jarvis-agent: files=6")
    assert code == 0 and "BACKUP_RC=0" in out and "\nPRE_RESTORE_SEALED yes\n" in out, out
    assert "PRE_RESTORE_AGENT_DATA sealed via docker exec jarvis-agent: files=6" in out
    assert rad.pre_restore_agent_data(out) == "sealed via docker exec jarvis-agent: files=6"
    code, out = remote(rad.backup_script(15 << 20),
                       FAKE_AGENT_DATA="SKIPPED: .env.agent has no BACKUP_RECIPIENT_PUBLIC_KEY")
    assert code == 0 and "\nPRE_RESTORE_SEALED no\n" in out and "BACKUP_DIR=" in out, out
    assert rad.pre_restore_agent_data(out) == "SKIPPED: .env.agent has no BACKUP_RECIPIENT_PUBLIC_KEY"


FAKE_DF = ('#!/usr/bin/env bash\n'
           'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n"\n'
           'printf "fake 1 1 %s 50%% /\\n" "$FAKE_DF_KIB"\n')
FAKE_INFO_DOCKER = r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/docker_calls.log"
if [[ $1 == info ]]; then printf '%s\n' "$FAKE_DIR"; exit 0; fi
if [[ $1 == container && $2 == inspect ]]; then echo false; exit 0; fi
if [[ $1 == image && $2 == inspect ]]; then exit 0; fi
exit 0
'''


@needs_bash
def test_the_vps_disk_is_read_in_step_1_and_checked_again_under_the_lock(tmp_path: Path) -> None:
    """C-128 review (low): step 1 prints the free KiB of Docker's root dir and of /root/jarvis-backups (DISK lines,
    read-only), and step 3 refuses under the deploy lock, before backup_vps.sh runs, when a disk has less than the need
    the PC computed (nothing changed, no backup made)."""
    fake = tmp_path / "fake"
    for name, body in (("docker", FAKE_INFO_DOCKER), ("flock", FAKE_REMOTE_FLOCK), ("df", FAKE_DF)):
        (fake / "bin").mkdir(parents=True, exist_ok=True)
        (fake / "bin" / name).write_bytes(body.encode())
        os.chmod(fake / "bin" / name, 0o755)
    (fake / "tools").mkdir()
    (fake / "tools" / "backup_vps.sh").write_bytes(FAKE_REMOTE_BACKUP.encode())

    def remote(script: str, kib: int) -> tuple[int, str]:
        script = script.replace(rad.DEPLOY_LOCK, _posix(fake / ".deploy.lock")).replace(rad.TOOLS_DIR,
                                                                                         _posix(fake / "tools"))
        env = dict(os.environ, PATH=os.pathsep.join([str(fake / "bin"), str(Path(BASH).parent)]),
                   FAKE_DIR=_posix(fake), FAKE_DF_KIB=str(kib), FAKE_AGENT_DATA="none (no jarvis_agent_* volume)")
        proc = subprocess.run([BASH, "-c", script], input=b"", capture_output=True, timeout=60, check=False, env=env)
        return proc.returncode, proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")

    gib = 1 << 20
    _code, out = remote(rad.info_script(TS), 12 * gib)
    assert rad.parse_info(out)["disk"] == {"docker": 12 * gib, "backups": 12 * gib}, out
    code, out = remote(rad.backup_script(15 * gib), 12 * gib)
    assert code == 3 and "RESTORE_REFUSED only 12 GB free on the disk of " in out, out
    assert "a restore needs 15 GB" in out and not (fake / "backups").exists()
    code, out = remote(rad.backup_script(15 * gib), 40 * gib)
    assert code == 0 and "BACKUP_RC=0" in out and "BACKUP_DIR=" in out, out


def test_the_command_line(tmp_path: Path, key, capsys: pytest.CaptureFixture[str]) -> None:
    key_file = offline_copy(tmp_path, key)
    sealed_file = tmp_path / av.ARCHIVE
    sealed_file.write_bytes(sealed_backup(tmp_path, key))
    assert rad.main(["--check-file", str(sealed_file), "--key-file", str(key_file)]) == 0
    out = capsys.readouterr().out
    assert "CHECK=ok" in out and "MEMBERS files=6" in out and not any(s in out for s in SENTINELS)
    assert rad.main(["--check-file", str(tmp_path / "missing.enc"), "--key-file", str(key_file)]) == 2
    assert "no such file" in capsys.readouterr().err
    assert rad.main(["--check-file", str(sealed_file), "--key-file", str(tmp_path / "no-key.json")]) == 2
    assert "run 'backup_key.py keygen' first" in capsys.readouterr().err
    # the key tool's commands are not the restore tool's: one key store, made only by scripts/ops/backup_key.py
    for bad in ([], ["--dry-run"], ["../x"], [TS, "--gen-key"], ["--gen-key"], ["--public-key"],
                ["--export-key", str(tmp_path / "x.json")], ["--rotate", TS], [TS, "--check-file", str(sealed_file)]):
        with pytest.raises(SystemExit) as exit_info:
            rad.main(bad)
        assert exit_info.value.code == 2, bad
    assert not (tmp_path / "x.json").exists()
