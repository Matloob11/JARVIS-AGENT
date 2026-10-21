"""scripts/memory/migrate_user_memory.py and scripts/memory/dump_memory.py (C-52 steps 6 and 7).

Temporary folders, fake messages and a throwaway Fernet key (or the test key the conftest sets for jarvis_crypto);
the real conversations/ folder is never read. Both tools print counts, ids and file names only: no message text,
except dump_memory with --show-text by hand in a console (refused under pytest, whose output is captured).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "memory" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load("migrate_user_memory")
dump = _load("dump_memory")


@pytest.fixture
def fernet():
    return Fernet(Fernet.generate_key())


def _crypto(f):
    return (lambda text: f.encrypt(text.encode() if isinstance(text, str) else text),
            lambda token: f.decrypt(token).decode())


def _write(f, path: Path, conversations) -> None:
    path.write_bytes(f.encrypt(json.dumps(conversations).encode()))


def _conv(ts: str, *messages: dict) -> dict:
    return {"timestamp": ts, "messages": list(messages), "user_id": "x"}


def _msg(text: str, role: str = "user", **extra) -> dict:
    return {"role": role, "content": text, **extra}


def _files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def _inputs(tmp_path: Path, f) -> Path:
    """A conversations/ folder with the owner's file and two per-job files (one plain legacy JSON)."""
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    _write(f, conversations / "matloob_memory.json",
           [_conv("2026-10-08T12:00:00", _msg("SALAM-A", id="a1"), _msg("JAWAB-A", "assistant", id="a2"))])
    _write(f, conversations / "owner-1a2b3c4d_memory.json",
           [_conv("2026-10-02T10:00:00", _msg("PURANA-B")),
            _conv("2026-10-08T12:00:00", _msg("SALAM-A", id="a1")),  # the same id: kept once
            _conv("2026-10-09T09:00:00", _msg("NAYA-B", id="b1"))])
    (conversations / "User_memory.json").write_text(json.dumps(
        [_conv("2026-10-02T10:00:00", _msg("PURANA-B")),  # the same text and time: kept once
         _conv("2026-10-01T07:00:00", _msg("LEGACY-C"))]), "utf-8")
    (conversations / "tasks.db").write_bytes(b"not memory")
    return conversations


def test_a_dry_run_prints_counts_only_and_writes_nothing(tmp_path, fernet):
    conversations = _inputs(tmp_path, fernet)
    before = {name: (conversations / name).read_bytes() for name in _files(conversations)}
    lines: list[str] = []
    encrypt, decrypt = _crypto(fernet)
    code = mig.migrate(conversations, "matloob", encrypt, decrypt, dry_run=True, out=lines.append)
    assert code == mig.EXIT_OK
    assert {name: (conversations / name).read_bytes() for name in _files(conversations)} == before
    report = "\n".join(lines)
    assert "3 file(s): 6 conversations / 7 messages in, 2 duplicates dropped, 4 conversations / 5 messages" in report
    assert "(dry run)" in report and "nothing written" in report
    assert not any(word in report for word in ("SALAM", "JAWAB", "PURANA", "NAYA", "LEGACY"))  # never a text


def test_the_real_run_merges_dedupes_sorts_and_keeps_the_originals(tmp_path, fernet):
    conversations = _inputs(tmp_path, fernet)
    old_target = (conversations / "matloob_memory.json").read_bytes()
    lines: list[str] = []
    encrypt, decrypt = _crypto(fernet)
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, now=0, out=lines.append) == mig.EXIT_OK
    merged = json.loads(fernet.decrypt((conversations / "matloob_memory.json").read_bytes()))
    texts = [m["content"] for c in merged for m in c["messages"]]
    assert texts == ["LEGACY-C", "PURANA-B", "SALAM-A", "JAWAB-A", "NAYA-B"]  # by save time, each once
    names = _files(conversations)
    assert "owner-1a2b3c4d_memory.json" in names and "User_memory.json" in names and "tasks.db" in names
    [kept] = [name for name in names if name.startswith("matloob_memory.json.pre-")]
    assert (conversations / kept).read_bytes() == old_target  # the old target is kept too
    assert not any(name.endswith(".tmp") for name in names)
    assert not any(word in "\n".join(lines) for word in ("SALAM", "JAWAB", "PURANA", "NAYA", "LEGACY"))
    # a second run finds every message there already: the per-job files add nothing new
    lines.clear()
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, now=1, out=lines.append) == mig.EXIT_OK
    again = json.loads(fernet.decrypt((conversations / "matloob_memory.json").read_bytes()))
    assert [m["content"] for c in again for m in c["messages"]] == texts


def test_archive_moves_the_per_job_files_to_the_legacy_folder_never_over_one(tmp_path, fernet):
    conversations = _inputs(tmp_path, fernet)
    legacy = tmp_path / "backups" / "memory" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "User_memory.json").write_bytes(b"an earlier archive")
    encrypt, decrypt = _crypto(fernet)
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, archive=True, legacy_dir=legacy, now=0,
                       out=lambda _line: None) == mig.EXIT_OK
    assert [n for n in _files(conversations) if n.endswith("_memory.json")] == ["matloob_memory.json"]
    assert _files(legacy) == ["User_memory.json", "User_memory.json.1", "owner-1a2b3c4d_memory.json"]
    assert (legacy / "User_memory.json").read_bytes() == b"an earlier archive"


def test_an_ambiguous_or_unreadable_input_is_refused_and_nothing_is_written(tmp_path, fernet):
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    _write(fernet, conversations / "a_memory.json", [_conv("2026-10-01T00:00:00", _msg("one", id="same"))])
    _write(fernet, conversations / "b_memory.json", [_conv("2026-10-02T00:00:00", _msg("two", id="same"))])
    lines: list[str] = []
    encrypt, decrypt = _crypto(fernet)
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, out=lines.append) == mig.EXIT_REFUSED
    assert "two different texts" in lines[-1] and "one" not in lines[-1]
    assert _files(conversations) == ["a_memory.json", "b_memory.json"]
    (conversations / "b_memory.json").write_bytes(b"garbage")
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, out=lines.append) == mig.EXIT_REFUSED
    assert not (conversations / "matloob_memory.json").exists()


def test_a_message_said_twice_without_an_id_is_kept_and_a_rerun_changes_nothing(tmp_path, fernet):
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    early = _conv("2026-10-01T07:00:00", _msg("SALAM-A"), _msg("JAWAB-A", "assistant"))
    later = _conv("2026-10-01T07:00:00", _msg("SALAM-A"), _msg("JAWAB-A", "assistant"), _msg("SALAM-A"),
                  _msg("NAYA-B", "assistant"))  # the same conversation saved later, SALAM-A said again (no ids)
    _write(fernet, conversations / "a-job_memory.json", [early])
    _write(fernet, conversations / "b-job_memory.json", [later])
    encrypt, decrypt = _crypto(fernet)
    lines: list[str] = []
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, archive=True, legacy_dir=tmp_path / "legacy",
                       now=0, out=lines.append) == mig.EXIT_OK
    merged = json.loads(fernet.decrypt((conversations / "matloob_memory.json").read_bytes()))
    # b-job brings only its new messages: the second SALAM-A (a repeat, not a copy of the first) and NAYA-B
    assert [[m["content"] for m in c["messages"]] for c in merged] == [["SALAM-A", "JAWAB-A"], ["SALAM-A", "NAYA-B"]]
    assert "6 messages in, 2 duplicates dropped, 2 conversations / 4 messages" in "\n".join(lines)
    # a rerun over the merged file alone: the conversation split into two entries of one save time keeps both copies
    before = (conversations / "matloob_memory.json").read_bytes()
    lines.clear()
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, now=1, out=lines.append) == mig.EXIT_OK
    assert "0 duplicates dropped" in "\n".join(lines) and "nothing to change" in lines[-1]
    assert (conversations / "matloob_memory.json").read_bytes() == before
    # and with b-job's file back (as after a rename that failed): every message of it is there already
    _write(fernet, conversations / "b-job_memory.json", [later])
    lines.clear()
    assert mig.migrate(conversations, "matloob", encrypt, decrypt, now=2, out=lines.append) == mig.EXIT_OK
    assert json.loads(fernet.decrypt((conversations / "matloob_memory.json").read_bytes())) == merged
    assert "4 duplicates dropped" in "\n".join(lines)
    assert not any(word in "\n".join(lines) for word in ("SALAM", "JAWAB", "NAYA"))


def test_merge_takes_any_json_role_or_time_stamp_of_a_message_without_an_id():
    said = {"role": ["user"], "content": "x"}  # a hand-built file: neither value can go into a set as it is
    odd = [{"timestamp": {"at": 1}, "messages": [said, dict(said)]}]
    merged, stats = mig.merge([("a_memory.json", odd), ("b_memory.json", odd)])
    assert merged == odd and stats["duplicates"] == 2  # the repeat stays, b's copy goes


def test_main_refuses_without_the_key_or_while_the_worker_runs(tmp_path, monkeypatch, capsys):
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    assert mig.main(["--dry-run", "--dir", str(conversations), "--user", "matloob"]) == mig.EXIT_USAGE
    monkeypatch.setenv("JARVIS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(mig, "_worker_running", lambda: True)
    assert mig.main(["--dir", str(conversations), "--user", "matloob"]) == mig.EXIT_RUNNING
    assert mig.main(["--dir", str(conversations), "--user", "../x"]) == mig.EXIT_USAGE
    assert mig.main(["--dry-run", "--dir", str(conversations), "--user", "matloob"]) == mig.EXIT_OK  # no lock check
    assert "nothing to merge" in capsys.readouterr().out


# ---------------------------------------------------------------- dump_memory


def _rows():
    return dump.flatten([
        _conv("2026-10-01T07:00:00", _msg("PURANI-BAAT"), _msg("PURANA-JAWAB", "assistant")),
        _conv("2027-01-15T12:00:00", _msg("NAYA-SAWAL", id="u1", ts=1_800_000_000.0, source="voice"),
              _msg("NAYA-JAWAB", "assistant", id="r1", ts=1_800_000_001.0, persona="anna", source="voice")),
    ])


def test_dump_hides_the_text_unless_asked_to_show_it():
    lines: list[str] = []
    check = dump.report(_rows(), 40, show_text=False, out=lines.append)
    text = "\n".join(lines)
    assert not any(word in text for word in ("PURANI", "PURANA", "NAYA"))
    assert "text hidden" in lines[-1] and "unknown" in lines[1] and "anna" in lines[3] and "r1" in lines[3]
    assert check == {"shown": 4, "total": 4, "in_order": True, "duplicate_ids": 0, "repeated_texts": 0}
    lines.clear()
    dump.report(_rows(), 2, show_text=True, out=lines.append)
    assert "NAYA-SAWAL" in lines[0] and "NAYA-JAWAB" in lines[1] and len(lines) == 3  # the newest 2, then the check


def test_dump_prints_text_only_with_show_text_by_hand_in_a_console(tmp_path, capsys, monkeypatch):
    """C-52 review: an agent's terminal tool (a PTY or ConPTY) is a console on both streams, and on Windows NUL counts
    as one, so a console alone never shows the text: only the explicit --show-text does, and only in a console."""
    from services.utils.jarvis_crypto import jarvis_crypto

    conversations = tmp_path / "conversations"
    conversations.mkdir()
    (conversations / "c52-pty_memory.json").write_bytes(jarvis_crypto.encrypt(json.dumps(
        [_conv("2026-10-20T12:00:00", _msg("PTY-SAWAL", id="u1"), _msg("PTY-JAWAB", "assistant", id="r1"))])))
    args = ["--user", "c52-pty", "--last", "40", "--dir", str(conversations)]
    monkeypatch.setattr(dump, "run_by_hand", lambda: True)  # both streams a console: an agent's PTY looks the same
    assert dump.main(args) == dump.EXIT_OK
    out = capsys.readouterr().out
    assert "PTY-" not in out and "text hidden" in out and "u1" in out and "r1" in out  # metadata and the check only
    assert dump.main(args + ["--show-text"]) == dump.EXIT_OK  # the owner asks for it by hand
    out = capsys.readouterr().out
    assert "PTY-SAWAL" in out and "PTY-JAWAB" in out and "text hidden" not in out
    monkeypatch.setattr(dump, "run_by_hand", lambda: False)  # a pipe, a file or captured output: refused outright
    assert dump.main(args + ["--show-text"]) == dump.EXIT_USAGE
    out = capsys.readouterr().out
    assert "refused" in out and "--show-text" in out and "PTY-" not in out and "u1" not in out  # nothing read


def test_dump_check_flags_disorder_duplicates_and_repeats():
    rows = dump.flatten([_conv("2026-10-20T12:00:00", _msg("x", id="m1", ts=20.0), _msg("x", id="m1", ts=10.0))])
    check = dump.report(rows, 40, show_text=False, out=lambda _line: None)
    assert check["in_order"] is False and check["duplicate_ids"] == 1 and check["repeated_texts"] == 1


def test_dump_main_reads_the_encrypted_file_and_never_prints_text_when_captured(tmp_path, capsys):
    from services.utils.jarvis_crypto import jarvis_crypto

    conversations = tmp_path / "conversations"
    conversations.mkdir()
    (conversations / "c52-dump_memory.json").write_bytes(jarvis_crypto.encrypt(json.dumps(
        [_conv("2026-10-20T12:00:00", _msg("CHUPA-HUA", id="u1"), _msg("JAWAB-CHUPA", "assistant", id="r1"))])))
    assert dump.main(["--user", "c52-dump", "--last", "40", "--dir", str(conversations)]) == dump.EXIT_OK
    out = capsys.readouterr().out
    assert "1 conversations, 2 messages" in out and "text hidden" in out and "CHUPA" not in out
    assert dump.main(["--user", "nobody", "--dir", str(conversations)]) == dump.EXIT_OK  # nothing saved yet
    assert "no such file" in capsys.readouterr().out
    (conversations / "broken_memory.json").write_bytes(b"garbage")
    assert dump.main(["--user", "broken", "--dir", str(conversations)]) == dump.EXIT_UNREADABLE
    assert dump.main(["--last", "0", "--dir", str(conversations)]) == dump.EXIT_USAGE
