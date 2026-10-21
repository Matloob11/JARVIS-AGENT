"""Merges every per-job memory file into the one memory of the owner (plan task C-52, step 6).

Usage (from the repo root, with the project venv; the worker stopped unless --dry-run):
    .venv_312/Scripts/python.exe scripts/memory/migrate_user_memory.py --dry-run
    .venv_312/Scripts/python.exe scripts/agent_ctl.py stop --by claude --reason C-52
    .venv_312/Scripts/python.exe scripts/memory/migrate_user_memory.py [--archive] [--user ID] [--dir PATH]

Every <dir>/*_memory.json (default dir: the configured conversations/ folder) is read the way
services/ai_core/memory_store.py reads it (Fernet with JARVIS_ENCRYPTION_KEY, else a legacy plain JSON list) and merged
into <dir>/<user>_memory.json (user: --user, else JARVIS_USER_ID, else matloob; K-69). A message is kept once: by its
id when it has one, else by its role, its text, its conversation's save time (content + timestamp) and its copy
number in its file (a conversation that says the same thing twice keeps both, the same conversation in two files is
kept once, and a rerun over the merged file changes nothing); a conversation left without messages goes, a record
without messages is kept once, and the conversations are sorted by their save time (stable; one without a readable
time first). The result is encrypted with the same key, read back
and written through a temporary file; the old target file is kept beside it as <name>.pre-<ts>. The other files stay
where they are (the originals are kept) unless --archive moves them to backups/memory/legacy/ (never over an existing
file). --dry-run reads, merges and reports, and writes, renames and moves nothing. migrate(originals='rename') is
C-129's --merge-local (scripts/ops/migrate_agent_data.py): every other original is renamed <name>.pre-<ts> in place.

Refuses (exit 4, nothing written) when a file cannot be read or holds no list, or one message id has two different
texts; refuses while the agent worker holds run/agent.lock (exit 3; not for --dry-run) and without
JARVIS_ENCRYPTION_KEY (exit 2: jarvis_crypto would invent a session key and nothing could be read). Output: counts
and file names only, never a message text. Exit codes: 0 ok (or nothing to change), 1 the merged file was written but
an original could not be renamed (originals='rename'; it is named, a rerun merges it again without a duplicate),
2 usage or configuration, 3 worker running, 4 refused.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNNING, EXIT_REFUSED = 0, 1, 2, 3, 4
SUFFIX = "_memory.json"
LEGACY_SUBDIR = ("memory", "legacy")  # under the configured backups/ folder
# What happens to the merged per-job files: kept where they are (default), moved to the legacy folder (--archive) or
# renamed <name>.pre-<ts> in place (C-129 --merge-local).
ORIGINALS_MODES = ("keep", "archive", "rename")


class MigrateRefused(Exception):
    """An input that cannot be merged safely; nothing has been written."""


def _fingerprint(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def read_memory_file(path: Path, decrypt: Callable[[bytes], str]) -> list[Any]:
    """One memory file as a list: Fernet first, then a legacy plain JSON list (memory_store's order). Never renames
    or changes the file (memory_store moves an unreadable file aside; this tool only refuses)."""
    data = path.read_bytes()
    if not data:
        return []
    try:
        loaded = json.loads(decrypt(data))
    except Exception:  # pylint: disable=broad-exception-caught  # not ours with this key: maybe legacy plain JSON
        try:
            loaded = json.loads(data.decode("utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise MigrateRefused(f"{path.name}: cannot be decrypted with JARVIS_ENCRYPTION_KEY or read as JSON") from exc
    if not isinstance(loaded, list):
        raise MigrateRefused(f"{path.name}: holds {type(loaded).__name__}, not a list")
    return loaded


def saved_at(conversation: Any) -> float:
    """A conversation's save time in epoch seconds (an ISO 'timestamp' without a zone is local time, as
    MemoryExtractor writes it); -inf when it has none that can be read."""
    stamp = conversation.get("timestamp") if isinstance(conversation, dict) else None
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return float("-inf")


def count_messages(conversations: list[Any]) -> int:
    return sum(len(conversation["messages"]) for conversation in conversations
               if isinstance(conversation, dict) and isinstance(conversation.get("messages"), list))


def merge(lists: list[tuple[str, list[Any]]]) -> tuple[list[Any], dict[str, int]]:
    """(merged conversations sorted by save time, counts). The first list's entries win a duplicate. Refuses one
    message id with two different texts. A message without an id is keyed by its role, its text, its conversation's
    save time and its copy number: how many messages of its file with that role, text and save time came before it.
    So a conversation that says the same thing twice keeps both (memory_store.message_id keeps them apart by their
    place), while the same conversation in another file is a copy. The count runs over the whole file, not one entry:
    a merged file can hold one conversation as two entries of one save time (a later file brought only its new
    messages), and a rerun over it keeps every repeat."""
    seen: set[tuple[Any, ...]] = set()
    text_of: dict[str, str] = {}
    merged: list[Any] = []
    stats = {"conversations_in": 0, "messages_in": 0, "duplicates": 0, "messages_out": 0}
    for name, entries in lists:
        copies: Counter[tuple[str, str, str]] = Counter()  # this file's messages without an id so far, by key basis
        for entry in entries:
            stats["conversations_in"] += 1
            messages = entry.get("messages") if isinstance(entry, dict) else None
            if not isinstance(messages, list):  # an old record without messages: kept once
                key: tuple[Any, ...] = ("record", _fingerprint(entry))
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                merged.append(entry)
                continue
            kept: list[Any] = []
            for message in messages:
                stats["messages_in"] += 1
                if not isinstance(message, dict):
                    kept.append(message)
                    continue
                msg_id = message.get("id")
                text = _fingerprint(message.get("content"))
                if isinstance(msg_id, str) and msg_id:
                    known = text_of.get(msg_id)
                    if known is not None and known != text:
                        raise MigrateRefused(f"{name}: message id {msg_id[:12]}... has two different texts")
                    text_of[msg_id] = text
                    key = ("id", msg_id)
                else:  # role and time stamp as JSON text, like the text: any value of a hand-built file is a key
                    basis = (_fingerprint(message.get("role")), text, _fingerprint(entry.get("timestamp")))
                    key = ("text", *basis, copies[basis])
                    copies[basis] += 1
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                kept.append(message)
            if kept:
                merged.append({**entry, "messages": kept})
    merged.sort(key=saved_at)  # stable: same-time entries keep their order
    stats["conversations_out"] = len(merged)
    stats["messages_out"] = count_messages(merged)
    return merged, stats


def _free_name(path: Path) -> Path:
    """path, or path.<n> for the first n that is free (nothing is ever overwritten)."""
    candidate, n = path, 0
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}.{n}")
    return candidate


def migrate(directory: Path, user_id: str, encrypt: Callable[[str], bytes], decrypt: Callable[[bytes], str], *,
            dry_run: bool = False, archive: bool = False, legacy_dir: Path | None = None, now: float | None = None,
            out: Callable[[str], Any] = print, originals: str | None = None) -> int:
    """Merges every <directory>/*_memory.json into <directory>/<user_id>_memory.json (see the module docstring).
    originals: 'keep' (the default), 'archive' (the default when archive is True: moved to legacy_dir) or 'rename'
    (each merged per-job file renamed <name>.pre-<ts> where it is, C-129)."""
    mode = originals or ("archive" if archive else "keep")
    if mode not in ORIGINALS_MODES:
        raise ValueError(f"originals must be one of {', '.join(ORIGINALS_MODES)}")
    target = directory / f"{user_id}{SUFFIX}"
    sources = sorted(p for p in directory.glob(f"*{SUFFIX}") if p.is_file())
    if not sources:
        out(f"MIGRATE nothing to merge in {directory.name}/ (no *{SUFFIX} file)")
        return EXIT_OK
    ordered = ([target] if target in sources else []) + [p for p in sources if p != target]  # the target's first
    others = [p for p in ordered if p != target]
    try:
        lists = [(p.name, read_memory_file(p, decrypt)) for p in ordered]
        merged, stats = merge(lists)
    except MigrateRefused as exc:
        out(f"MIGRATE refused: {exc}; nothing written")
        return EXIT_REFUSED
    for name, entries in lists:
        out(f"  input {name}: {len(entries)} conversations, {count_messages(entries)} messages")
    out(f"MIGRATE {len(ordered)} file(s): {stats['conversations_in']} conversations / {stats['messages_in']} messages "
        f"in, {stats['duplicates']} duplicates dropped, {stats['conversations_out']} conversations / "
        f"{stats['messages_out']} messages -> {target.name}{' (dry run)' if dry_run else ''}")
    if not others and not stats["duplicates"]:
        out(f"MIGRATE nothing to change: only {target.name}, no duplicates")
        return EXIT_OK
    if dry_run:
        fate = {"archive": f"moves {len(others)} original(s) to the legacy folder",
                "rename": f"renames {len(others)} original(s) to <name>.pre-<ts> where they are",
                "keep": f"keeps {len(others)} original(s) where they are"}[mode]
        out(f"MIGRATE dry run: nothing written; a real run keeps the old {target.name} as a .pre- copy and {fate}")
        return EXIT_OK
    payload = encrypt(json.dumps(merged, indent=2, ensure_ascii=False, default=str))
    check = json.loads(decrypt(payload))  # never write what this key cannot read back
    if not isinstance(check, list) or len(check) != len(merged):
        out("MIGRATE refused: the encrypted result does not read back; nothing written")
        return EXIT_REFUSED
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    fd, tmp = tempfile.mkstemp(prefix=f".{user_id}_memory.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        if target.exists():
            kept = _free_name(target.with_name(f"{target.name}.pre-{stamp}"))
            shutil.copy2(target, kept)
            out(f"  the old {target.name} is kept as {kept.name}")
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if mode == "keep":
        out(f"MIGRATE done: {target.name} written ({stats['messages_out']} messages); {len(others)} original(s) "
            "kept where they are")
        return EXIT_OK
    if mode == "rename":
        for done, path in enumerate(others):
            try:
                path.rename(_free_name(path.with_name(f"{path.name}.pre-{stamp}")))
            except OSError as exc:  # the merged file holds its messages; a rerun merges it again without a duplicate
                out(f"MIGRATE {target.name} written ({stats['messages_out']} messages), but {path.name} could not be "
                    f"renamed ({type(exc).__name__}); {done} of {len(others)} original(s) renamed to *.pre-{stamp}: "
                    "rename the rest by hand")
                return EXIT_FAILED
        out(f"MIGRATE done: {target.name} written ({stats['messages_out']} messages); {len(others)} original(s) "
            f"renamed to <name>.pre-{stamp}")
        return EXIT_OK
    legacy = legacy_dir if legacy_dir is not None else directory / "legacy"  # main() passes backups/memory/legacy
    legacy.mkdir(parents=True, exist_ok=True)
    for path in others:
        shutil.move(str(path), str(_free_name(legacy / path.name)))
    out(f"MIGRATE done: {target.name} written ({stats['messages_out']} messages); {len(others)} original(s) moved to "
        f"{legacy}")
    return EXIT_OK


def _worker_running() -> bool:
    from src.core import agent_lock  # pylint: disable=import-outside-toplevel

    return agent_lock.is_locked()


def _valid_user(user_id: str) -> bool:
    return bool(user_id) and user_id not in (".", "..") and not any(ch in user_id for ch in "/\\:*?\"<>|")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge every *_memory.json into the owner's one memory file (C-52)")
    parser.add_argument("--dry-run", action="store_true", help="report only; write, rename and move nothing")
    parser.add_argument("--archive", action="store_true",
                        help="move the merged per-job files to backups/memory/legacy/ (default: keep them)")
    parser.add_argument("--user", default=None, help="the memory identity (default: JARVIS_USER_ID, else matloob)")
    parser.add_argument("--dir", default=None, help="memory folder (default: the configured conversations folder)")
    parser.add_argument("--legacy-dir", default=None,
                        help="where --archive moves files (default: backups/memory/legacy)")
    args = parser.parse_args(argv)

    # loads the PC .env (override=False)
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

    user_id = (args.user or config.user_id or "").strip()
    if not _valid_user(user_id):
        print("MIGRATE refused: no valid user id (--user or JARVIS_USER_ID)")
        return EXIT_USAGE
    if not (os.getenv("JARVIS_ENCRYPTION_KEY") or "").strip():
        print("MIGRATE refused: JARVIS_ENCRYPTION_KEY is not set; the memory files could not be read or rewritten")
        return EXIT_USAGE
    if not args.dry_run and _worker_running():
        print("MIGRATE refused: the agent worker is running; stop it first (scripts/agent_ctl.py stop)")
        return EXIT_RUNNING
    directory = Path(args.dir) if args.dir else Path(config.conversations_dir)
    if not directory.is_dir():
        print(f"MIGRATE refused: {directory} is not a folder")
        return EXIT_USAGE
    legacy = Path(args.legacy_dir) if args.legacy_dir else Path(config.backups_dir, *LEGACY_SUBDIR)
    from services.utils.jarvis_crypto import jarvis_crypto  # pylint: disable=import-outside-toplevel

    return migrate(directory, user_id, jarvis_crypto.encrypt, jarvis_crypto.decrypt, dry_run=args.dry_run,
                   archive=args.archive, legacy_dir=legacy)


if __name__ == "__main__":
    sys.exit(main())
