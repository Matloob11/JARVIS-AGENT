"""services/utils/backup_crypto.py, agent-svc's SnapshotWriter and scripts/ops/backup_key.py (plan task C-125 step 6;
contract K-77 'Snapshot'). Temp key pairs and temp folders only: no real backup, key or env file is read."""
# pylint: disable=protected-access
from __future__ import annotations

import base64
import importlib.util
import io
import sqlite3
import struct
import tarfile
import threading
from pathlib import Path

import pytest

from services.utils import backup_crypto as bc
from src.core.agent_services import SnapshotAborted, SnapshotWriter

ROOT = Path(__file__).resolve().parents[2]
SECRET_TEXT = b"PLAINTEXT-MARKER-REMINDER-0042"


def _load_tool():
    spec = importlib.util.spec_from_file_location("backup_key_tool", ROOT / "scripts" / "ops" / "backup_key.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def _wrap(data: bytes) -> bytes:  # a stand-in for DPAPI (reversible, so the plaintext never shows as is)
    return b"WRAPPED:" + base64.b64encode(data[::-1])


def _unwrap(blob: bytes) -> bytes:
    if not blob.startswith(b"WRAPPED:"):
        raise OSError("not wrapped for this user")
    return base64.b64decode(blob[len(b"WRAPPED:"):])[::-1]


# ---------------------------------------------------------------- sealing


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 64, 1000])
def test_round_trip_with_small_chunks(size):
    key = bc.generate_private_key()
    data = bytes(range(256)) * 4
    data = data[:size]
    sealed = bc.seal_bytes(data, key.public_key(), chunk_size=16)
    assert bc.open_bytes(sealed, key) == data
    assert bc.looks_sealed(sealed)


def test_the_sealed_file_holds_no_plaintext_and_every_file_differs():
    key = bc.generate_private_key()
    data = SECRET_TEXT * 100
    first, second = (bc.seal_bytes(data, key.public_key()) for _ in range(2))
    assert SECRET_TEXT not in first and SECRET_TEXT[:12] not in first
    assert first != second  # a fresh ephemeral key and salt per file


def test_a_wrong_key_does_not_open():
    sealed = bc.seal_bytes(SECRET_TEXT, bc.generate_private_key().public_key())
    with pytest.raises(bc.SnapshotCryptoError, match="does not verify"):
        bc.open_bytes(sealed, bc.generate_private_key())


def _records(sealed: bytes) -> tuple[bytes, list[bytes]]:
    header, rest, records = sealed[:bc.HEADER_BYTES], sealed[bc.HEADER_BYTES:], []
    while rest:
        (length,) = struct.unpack(">I", rest[:4])
        records.append(rest[:4 + length])
        rest = rest[4 + length:]
    return header, records


def test_truncation_reordering_appending_and_tampering_are_refused():
    key = bc.generate_private_key()
    sealed = bc.seal_bytes(SECRET_TEXT * 10, key.public_key(), chunk_size=32)
    header, records = _records(sealed)
    assert len(records) > 3
    cases = {
        "dropped final": header + b"".join(records[:-1]),
        "dropped middle": header + b"".join(records[:1] + records[2:]),
        "reordered": header + b"".join([records[1], records[0], *records[2:]]),
        "appended": sealed + records[0],
        "flipped bit": sealed[:-5] + bytes([sealed[-5] ^ 1]) + sealed[-4:],
        "header changed": header[:-1] + bytes([header[-1] ^ 1]) + b"".join(records),
        "not a snapshot": b"SQLite format 3\x00" + sealed[16:],
        "too short": sealed[:10],
    }
    for name, blob in cases.items():
        with pytest.raises(bc.SnapshotCryptoError):
            bc.open_bytes(blob, key)
        assert name


def test_key_forms():
    key = bc.generate_private_key()
    public_hex = bc.public_key_hex(key)
    raw = bytes.fromhex(public_hex)
    for text in (public_hex, public_hex.upper(), base64.b64encode(raw).decode(),
                 base64.urlsafe_b64encode(raw).decode().rstrip("=")):
        assert bc.public_key_hex(bc.parse_public_key(text)) == public_hex
    assert bc.public_key_hex(bc.parse_private_key(bc.private_key_hex(key))) == public_hex
    for bad in ("", "zz" * 32, "ab" * 31, "not a key"):
        with pytest.raises(bc.SnapshotCryptoError) as caught:
            bc.parse_public_key(bad)
        assert bad == "" or bad not in str(caught.value)
    assert len(bc.fingerprint(key)) == 8 and bc.fingerprint(key) == bc.fingerprint(key.public_key())


def test_the_writer_streams_into_tar():
    key = bc.generate_private_key()
    out = io.BytesIO()
    writer = bc.SealingWriter(out, key.public_key(), chunk_size=100)
    with tarfile.open(fileobj=writer, mode="w|") as tar:
        info = tarfile.TarInfo("conversations/a.json")
        info.size = len(SECRET_TEXT)
        tar.addfile(info, io.BytesIO(SECRET_TEXT))
    writer.close()
    assert writer.bytes_out == len(out.getvalue()) and SECRET_TEXT not in out.getvalue()
    with tarfile.open(fileobj=io.BytesIO(bc.open_bytes(out.getvalue(), key)), mode="r:") as tar:
        assert tar.extractfile("conversations/a.json").read() == SECRET_TEXT
    with pytest.raises(ValueError):
        writer.write(b"x")


# ---------------------------------------------------------------- the snapshot


def _sample_tree(root: Path) -> sqlite3.Connection:
    conv = root / "conversations"
    conv.mkdir(parents=True)
    (conv / "reminders.json").write_bytes(b'[{"message": "' + SECRET_TEXT + b'"}]')
    (conv / "reminders.json.lock").write_bytes(b"")
    (conv / ".env.agent").write_text("GOOGLE_API_KEY=should-never-be-inside", encoding="utf-8")
    (conv / "client_secret.json").write_text("{}", encoding="utf-8")
    (conv / "link.key").write_text("k", encoding="utf-8")
    (conv / ".location-x.tmp").write_text("t", encoding="utf-8")
    db = sqlite3.connect(conv / "tasks.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, description TEXT)")
    db.execute("INSERT INTO tasks (description) VALUES (?)", (SECRET_TEXT.decode(),))
    db.commit()  # left open: the row lives in the -wal file, which the online copy must include
    (root / "data").mkdir()
    (root / "data" / "notes.txt").write_text("data file", encoding="utf-8")
    chroma = root / "chroma_db" / "abc"
    chroma.mkdir(parents=True)
    (chroma / "data_level0.bin").write_bytes(b"\x00\x01" * 1000)
    (root / "logs").mkdir()
    (root / "logs" / "jarvis.log").write_text("never in the snapshot", encoding="utf-8")
    return db


def test_a_snapshot_round_trip_with_a_temp_key_pair(tmp_path):
    key = bc.generate_private_key()
    db = _sample_tree(tmp_path)
    try:
        writer = SnapshotWriter(root=tmp_path, out_dir=tmp_path / "snapshots", recipient_text=bc.public_key_hex(key),
                                chunk_size=4096)
        info = writer.write()
    finally:
        db.close()
    assert info is not None and info["name"].startswith("jarvis_snapshot_") and info["name"].endswith(".tar.enc")
    sealed_path = tmp_path / "snapshots" / info["name"]
    sealed = sealed_path.read_bytes()
    assert SECRET_TEXT not in sealed and b"should-never-be-inside" not in sealed and bc.looks_sealed(sealed)
    assert [p.name for p in (tmp_path / "snapshots").iterdir()] == [info["name"]]  # no partial file left
    assert writer.newest_ts() == pytest.approx(sealed_path.stat().st_mtime)

    plain = bc.open_bytes(sealed, key)
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
        names = sorted(tar.getnames())
        assert names == ["chroma_db/abc/data_level0.bin", "conversations/reminders.json", "conversations/tasks.db",
                         "data/notes.txt"]
        assert info["files"] == 4
        copy = tmp_path / "restored.db"
        copy.write_bytes(tar.extractfile("conversations/tasks.db").read())
    restored = sqlite3.connect(copy)
    try:
        assert restored.execute("SELECT description FROM tasks").fetchall() == [(SECRET_TEXT.decode(),)]
    finally:
        restored.close()


def test_a_second_snapshot_of_the_same_day_never_overwrites_the_first(tmp_path):
    key = bc.generate_private_key()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.txt").write_text("x", encoding="utf-8")
    writer = SnapshotWriter(root=tmp_path, out_dir=tmp_path / "snaps", recipient_text=bc.public_key_hex(key),
                            clock=lambda: 1760907600.0)
    first, second = writer.write(), writer.write()
    assert first["name"] != second["name"] and second["name"].endswith("_2.tar.enc")
    assert len(list((tmp_path / "snaps").iterdir())) == 2


def test_no_recipient_key_means_no_snapshot(tmp_path):
    for text, problem in ((None, "is not set"), ("", "is not set"), ("nope", "not a usable")):
        writer = SnapshotWriter(root=tmp_path, out_dir=tmp_path / "snaps", recipient_text=text)
        assert not writer.enabled and problem in writer.problem and "BACKUP_RECIPIENT_PUBLIC_KEY" in writer.problem
        assert writer.write() is None
    assert not (tmp_path / "snaps").exists()


def test_a_stop_aborts_the_snapshot_and_removes_the_partial_file(tmp_path):
    key = bc.generate_private_key()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.txt").write_text("x", encoding="utf-8")
    stop = threading.Event()
    stop.set()
    writer = SnapshotWriter(root=tmp_path, out_dir=tmp_path / "snaps", recipient_text=bc.public_key_hex(key))
    with pytest.raises(SnapshotAborted):
        writer.write(stop)
    assert list((tmp_path / "snaps").iterdir()) == []


# ---------------------------------------------------------------- the PC helper (scripts/ops/backup_key.py)


def test_keygen_keeps_the_key_and_prints_only_the_public_key(tmp_path, capsys):
    key_file = tmp_path / "backup_key.dpapi"
    public = tool.keygen(key_file, wrap=_wrap, unwrap=_unwrap)
    out = capsys.readouterr().out
    key = tool.load_key(key_file, _unwrap)
    assert f"BACKUP_RECIPIENT_PUBLIC_KEY={public}" in out and bc.public_key_hex(key) == public
    assert bc.private_key_hex(key) not in out and bc.private_key_hex(key).encode() not in key_file.read_bytes()
    assert tool.keygen(key_file, wrap=_wrap, unwrap=_unwrap) == public  # kept without --rotate
    assert "KEPT=" in capsys.readouterr().out
    rotated = tool.keygen(key_file, rotate=True, wrap=_wrap, unwrap=_unwrap)
    assert rotated != public and len(list(tmp_path.glob("backup_key.dpapi.*.bak"))) == 1


def test_the_snapshot_decrypts_on_the_pc(tmp_path, capsys):
    key_file = tmp_path / "keys" / "backup_key.dpapi"
    assert tool.main(["keygen", "--key-file", str(key_file)], wrap=_wrap, unwrap=_unwrap) == 0
    public = capsys.readouterr().out.split("BACKUP_RECIPIENT_PUBLIC_KEY=")[1].split()[0]
    db = _sample_tree(tmp_path / "vps")
    try:
        info = SnapshotWriter(root=tmp_path / "vps", out_dir=tmp_path / "pulled", recipient_text=public).write()
    finally:
        db.close()
    snapshot = tmp_path / "pulled" / info["name"]
    assert tool.main(["decrypt", str(snapshot), "--key-file", str(key_file)], wrap=_wrap, unwrap=_unwrap) == 0
    out = capsys.readouterr().out
    assert "TAR_MEMBERS=4" in out and "DECRYPTED=" in out and "SHA256=" in out
    private = bc.private_key_hex(tool.load_key(key_file, _unwrap))
    assert private not in out
    tar_path = tmp_path / "pulled" / info["name"].replace(".tar.enc", ".tar")
    with tarfile.open(tar_path) as tar:
        assert "conversations/tasks.db" in tar.getnames()
    # a second run never overwrites the tar
    assert tool.main(["decrypt", str(snapshot), "--key-file", str(key_file)], wrap=_wrap, unwrap=_unwrap) == 2


def test_a_snapshot_for_another_key_fails_and_leaves_nothing(tmp_path, capsys):
    key_file = tmp_path / "backup_key.dpapi"
    tool.keygen(key_file, wrap=_wrap, unwrap=_unwrap)
    other = bc.generate_private_key()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.txt").write_text("x", encoding="utf-8")
    info = SnapshotWriter(root=tmp_path, out_dir=tmp_path / "pulled", recipient_text=bc.public_key_hex(other)).write()
    snapshot = tmp_path / "pulled" / info["name"]
    capsys.readouterr()
    assert tool.main(["decrypt", str(snapshot), "--key-file", str(key_file)], wrap=_wrap, unwrap=_unwrap) == 1
    assert "FAILED=" in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "pulled").iterdir()) == [info["name"]]


def test_the_helper_refuses_a_key_this_user_cannot_unwrap(tmp_path, capsys):
    key_file = tmp_path / "backup_key.dpapi"
    key_file.write_bytes(b"something else")
    assert tool.main(["public", "--key-file", str(key_file)], wrap=_wrap, unwrap=_unwrap) == 2
    assert "cannot unwrap" in capsys.readouterr().err
    assert tool.main(["public", "--key-file", str(tmp_path / "missing")], wrap=_wrap, unwrap=_unwrap) == 2


def test_latest_picks_the_newest_sealed_file(tmp_path, monkeypatch):
    folder = tmp_path / "JARVIS" / "vps_backups"
    folder.mkdir(parents=True)
    (folder / "plain.bin").write_bytes(b"SQLite format 3\x00 not sealed")
    sealed = folder / "jarvis_snapshot_2026-10-20.tar.enc"
    sealed.write_bytes(bc.MAGIC + b"rest")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tool.newest_snapshot(tool.default_backup_dir()) == sealed
    assert tool.default_output(sealed).name == "jarvis_snapshot_2026-10-20.tar"
