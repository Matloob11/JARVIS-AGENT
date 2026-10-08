"""scripts/ops/migrate_agent_data.py --merge-local (C-129 step 1), with a temporary Fernet key."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("migrate_agent_data", ROOT / "scripts" / "ops" / "migrate_agent_data.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


@pytest.fixture
def fernet():
    f = Fernet(Fernet.generate_key())
    return f


def _enc(f, entries) -> bytes:
    return f.encrypt(json.dumps(entries).encode())


def _crypto(f):
    return (lambda text: f.encrypt(text.encode() if isinstance(text, str) else text),
            lambda token: f.decrypt(token).decode())


def _conv(ts: str, text: str, **extra):
    return {"timestamp": ts, "messages": [{"role": "user", "content": text}], **extra}


def test_merge_combines_dedupes_sorts_and_keeps_every_original(tmp_path, fernet):
    a = [_conv("2026-10-02T10:00:00", "salam"), _conv("2026-10-04T09:00:00", "weather")]
    b = [_conv("2026-10-03T08:00:00", "reminder"), _conv("2026-10-02T10:00:00", "salam")]  # one duplicate
    (tmp_path / "owner-aaaa1111_memory.json").write_bytes(_enc(fernet, a))
    (tmp_path / "agent-AJ_x_memory.json").write_bytes(_enc(fernet, b))
    (tmp_path / "web-legacy_memory.json").write_text(json.dumps([_conv("2026-10-01T07:00:00", "old")]), "utf-8")
    (tmp_path / "tasks.db").write_bytes(b"not memory")
    lines: list[str] = []
    encrypt, decrypt = _crypto(fernet)

    code = mig.merge_local(tmp_path, "matloob", encrypt, decrypt, now=0, out=lines.append)

    assert code == mig.EXIT_OK
    merged = json.loads(fernet.decrypt((tmp_path / "matloob_memory.json").read_bytes()))
    assert [c["messages"][0]["content"] for c in merged] == ["old", "salam", "reminder", "weather"]
    kept = sorted(p.name for p in tmp_path.glob("*.pre-*"))
    assert len(kept) == 3 and all(".json.pre-" in name for name in kept)  # nothing deleted
    assert (tmp_path / "tasks.db").exists()
    report = "\n".join(lines)
    assert "5 entries in, 1 duplicates dropped, 4 entries" in report
    assert "salam" not in report and "weather" not in report  # counts only, never content


def test_an_existing_target_is_one_of_the_inputs(tmp_path, fernet):
    (tmp_path / "matloob_memory.json").write_bytes(_enc(fernet, [_conv("2026-10-08T12:00:00", "new job")]))
    (tmp_path / "owner-b_memory.json").write_bytes(_enc(fernet, [_conv("2026-10-05T12:00:00", "before")]))
    encrypt, decrypt = _crypto(fernet)
    assert mig.merge_local(tmp_path, "matloob", encrypt, decrypt, now=0, out=lambda _l: None) == 0
    merged = json.loads(fernet.decrypt((tmp_path / "matloob_memory.json").read_bytes()))
    assert [c["messages"][0]["content"] for c in merged] == ["before", "new job"]
    assert any(p.name.startswith("matloob_memory.json.pre-") for p in tmp_path.iterdir())


def test_nothing_to_merge_when_only_the_target_exists(tmp_path, fernet):
    (tmp_path / "matloob_memory.json").write_bytes(_enc(fernet, []))
    encrypt, decrypt = _crypto(fernet)
    lines: list[str] = []
    assert mig.merge_local(tmp_path, "matloob", encrypt, decrypt, out=lines.append) == 0
    assert "nothing to merge" in lines[0] and not list(tmp_path.glob("*.pre-*"))


def test_a_file_this_key_cannot_read_refuses_the_whole_merge(tmp_path, fernet):
    other = Fernet(Fernet.generate_key())
    (tmp_path / "owner-a_memory.json").write_bytes(_enc(fernet, [_conv("2026-10-01T00:00:00", "x")]))
    (tmp_path / "owner-b_memory.json").write_bytes(_enc(other, [_conv("2026-10-02T00:00:00", "y")]))
    encrypt, decrypt = _crypto(fernet)
    lines: list[str] = []
    assert mig.merge_local(tmp_path, "matloob", encrypt, decrypt, out=lines.append) == mig.EXIT_REFUSED
    assert not (tmp_path / "matloob_memory.json").exists() and not list(tmp_path.glob("*.pre-*"))
    assert "owner-b_memory.json" in lines[-1]


def test_the_same_id_with_different_content_is_ambiguous(tmp_path, fernet):
    (tmp_path / "owner-a_memory.json").write_bytes(_enc(fernet, [_conv("2026-10-01T00:00:00", "x", id="c1")]))
    (tmp_path / "owner-b_memory.json").write_bytes(_enc(fernet, [_conv("2026-10-01T00:00:00", "y", id="c1")]))
    encrypt, decrypt = _crypto(fernet)
    assert mig.merge_local(tmp_path, "matloob", encrypt, decrypt, out=lambda _l: None) == mig.EXIT_REFUSED
    assert not (tmp_path / "matloob_memory.json").exists()


def test_a_dry_run_writes_and_renames_nothing(tmp_path, fernet):
    (tmp_path / "owner-a_memory.json").write_bytes(_enc(fernet, [_conv("2026-10-01T00:00:00", "x")]))
    before = sorted(p.name for p in tmp_path.iterdir())
    encrypt, decrypt = _crypto(fernet)
    assert mig.merge_local(tmp_path, "matloob", encrypt, decrypt, dry_run=True, out=lambda _l: None) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_main_refuses_without_user_id_or_key_and_while_the_worker_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("JARVIS_USER_ID", raising=False)
    monkeypatch.setattr(mig.os, "getenv", lambda name, default=None: {"JARVIS_ENCRYPTION_KEY": "k"}.get(name, default))
    assert mig.main(["--merge-local", "--dir", str(tmp_path)]) == mig.EXIT_USAGE
    monkeypatch.setattr(mig.os, "getenv", lambda name, default=None: {"JARVIS_USER_ID": "matloob"}.get(name, default))
    assert mig.main(["--merge-local", "--dir", str(tmp_path)]) == mig.EXIT_USAGE  # no key: never a session key
    monkeypatch.setattr(mig.os, "getenv",
                        lambda name, default=None: {"JARVIS_USER_ID": "matloob", "JARVIS_ENCRYPTION_KEY": "k"}.get(
                            name, default))
    monkeypatch.setattr(mig, "_worker_running", lambda: True)
    assert mig.main(["--merge-local", "--dir", str(tmp_path)]) == mig.EXIT_RUNNING
    assert mig.main([]) == mig.EXIT_USAGE
    out = capsys.readouterr().out
    assert "worker is running" in out and "JARVIS_ENCRYPTION_KEY is not set" in out
