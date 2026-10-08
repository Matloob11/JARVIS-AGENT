"""JARVIS data migration tool (plan task C-129). Step 1 is built: --merge-local.

Usage (from the repo root, with the project venv, worker stopped):
    .venv_312/Scripts/python.exe scripts/ops/agent_ctl.py stop ...   (see CLAUDE.md; the tool refuses otherwise)
    .venv_312/Scripts/python.exe scripts/ops/backup_local.py --now
    .venv_312/Scripts/python.exe scripts/ops/migrate_agent_data.py --merge-local [--dry-run] [--dir PATH]

--merge-local  Merges every <dir>/*_memory.json (default dir: conversations/) into <dir>/<JARVIS_USER_ID>_memory.json,
               so the PC brain keeps one memory (K-69 v4). Each file is read the way services/ai_core/memory_store.py
               reads it (Fernet with JARVIS_ENCRYPTION_KEY, or a legacy plain JSON list). Entries with the same content
               are kept once; the result is sorted by 'timestamp' (stable; entries without one first) and written
               encrypted with the same key, through a temporary file. An existing target file is one of the inputs.
               Every input file (the old target included) is then renamed <name>.pre-<ts>; nothing is deleted.
               Refuses (exit 4, nothing written or renamed) when a file cannot be read, holds no list, or two entries
               share an 'id' with different content. Refuses while the agent worker holds run/agent.lock (exit 3),
               or without JARVIS_USER_ID / JARVIS_ENCRYPTION_KEY (exit 2): a missing key would make jarvis_crypto
               invent a session key and the merged file would be unreadable.
--dry-run      Reads, merges and reports; writes and renames nothing.

Output: counts and file names only, never memory content. Exit codes: 0 ok (or nothing to merge), 2 usage or
configuration, 3 worker running, 4 refused (unreadable or ambiguous input).
Steps 2-3 of C-129 (--pre/--final/--verify for the VPS copies, --reverse) are not built yet.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_OK, EXIT_USAGE, EXIT_RUNNING, EXIT_REFUSED = 0, 2, 3, 4
SUFFIX = "_memory.json"


class MergeRefused(Exception):
    """An input that cannot be merged safely; nothing has been written."""


def _fingerprint(entry: Any) -> str:
    """The same content test memory_store uses (sorted keys, str for anything else)."""
    return json.dumps(entry, sort_keys=True, default=str, ensure_ascii=False)


def read_memory_file(path: Path, decrypt) -> list[Any]:
    """One memory file as a list: Fernet first, then a legacy plain JSON list (memory_store's order)."""
    data = path.read_bytes()
    if not data:
        return []
    try:
        loaded = json.loads(decrypt(data))
    except Exception:  # pylint: disable=broad-exception-caught  # not ours with this key: maybe legacy plain JSON
        try:
            loaded = json.loads(data.decode("utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise MergeRefused(f"{path.name}: cannot be decrypted with JARVIS_ENCRYPTION_KEY or read as JSON") from exc
    if not isinstance(loaded, list):
        raise MergeRefused(f"{path.name}: holds {type(loaded).__name__}, not a list")
    return loaded


def merge_entries(lists: list[tuple[str, list[Any]]]) -> tuple[list[Any], int]:
    """(merged entries sorted by timestamp, duplicates dropped). Refuses two entries with one 'id' and different
    content."""
    seen: set[str] = set()
    by_id: dict[str, str] = {}
    merged: list[Any] = []
    dropped = 0
    for name, entries in lists:
        for entry in entries:
            fp = _fingerprint(entry)
            entry_id = entry.get("id") if isinstance(entry, dict) else None
            if isinstance(entry_id, str) and entry_id:
                known = by_id.get(entry_id)
                if known is not None and known != fp:
                    raise MergeRefused(f"{name}: id {entry_id[:12]}... appears with different content in two files")
                by_id[entry_id] = fp
            if fp in seen:
                dropped += 1
                continue
            seen.add(fp)
            merged.append(entry)

    def when(entry: Any) -> str:
        stamp = entry.get("timestamp") if isinstance(entry, dict) else None
        return stamp if isinstance(stamp, str) else ""

    merged.sort(key=when)  # stable: same-time entries keep their file order
    return merged, dropped


def merge_local(directory: Path, user_id: str, encrypt, decrypt, *, dry_run: bool = False,
                now: float | None = None, out=print) -> int:
    target = directory / f"{user_id}{SUFFIX}"
    sources = sorted(p for p in directory.glob(f"*{SUFFIX}") if p.is_file())
    if not sources or sources == [target]:
        out(f"MERGE nothing to merge in {directory.name}/ (files: {len(sources)})")
        return EXIT_OK
    try:
        lists = [(p.name, read_memory_file(p, decrypt)) for p in sources]
        merged, dropped = merge_entries(lists)
    except MergeRefused as exc:
        out(f"MERGE refused: {exc}; nothing written")
        return EXIT_REFUSED
    total_in = sum(len(entries) for _name, entries in lists)
    for name, entries in lists:
        out(f"  input {name}: {len(entries)} entries")
    out(f"MERGE {len(sources)} files, {total_in} entries in, {dropped} duplicates dropped, "
        f"{len(merged)} entries -> {target.name}{' (dry run)' if dry_run else ''}")
    if dry_run:
        return EXIT_OK
    payload = encrypt(json.dumps(merged, indent=2, ensure_ascii=False, default=str))
    check = json.loads(decrypt(payload))  # never write what this key cannot read back
    if len(check) != len(merged):
        out("MERGE refused: the encrypted result does not read back; nothing written")
        return EXIT_REFUSED
    fd, tmp = tempfile.mkstemp(prefix=f".{user_id}_memory.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        for path in sources:  # the old target too: its content is in the merge
            path.rename(path.with_name(f"{path.name}.pre-{stamp}"))
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    out(f"MERGE done: {target.name} written; {len(sources)} originals kept as *.pre-{stamp}")
    return EXIT_OK


def _worker_running() -> bool:
    from src.core import agent_lock  # pylint: disable=import-outside-toplevel

    return agent_lock.is_locked()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS data migration (C-129); step 1: --merge-local")
    parser.add_argument("--merge-local", action="store_true", help="merge every *_memory.json into the user's file")
    parser.add_argument("--dry-run", action="store_true", help="report only; write and rename nothing")
    parser.add_argument("--dir", default=None, help="memory folder (default: the configured conversations folder)")
    args = parser.parse_args(argv)
    if not args.merge_local:
        print("migrate_agent_data: only --merge-local is built (C-129 step 1)")
        return EXIT_USAGE

    from services.utils.jarvis_config import config  # loads the PC .env (override=False)  # pylint: disable=import-outside-toplevel

    user_id = (os.getenv("JARVIS_USER_ID") or "").strip()
    if not user_id:
        print("MERGE refused: JARVIS_USER_ID is not set (scripts/ops/env_set.py JARVIS_USER_ID=...)")
        return EXIT_USAGE
    if not (os.getenv("JARVIS_ENCRYPTION_KEY") or "").strip():
        print("MERGE refused: JARVIS_ENCRYPTION_KEY is not set; the memory files could not be read or rewritten")
        return EXIT_USAGE
    if not args.dry_run and _worker_running():
        print("MERGE refused: the agent worker is running; stop it first (scripts/agent_ctl.py stop)")
        return EXIT_RUNNING
    directory = Path(args.dir) if args.dir else Path(config.conversations_dir)
    if not directory.is_dir():
        print(f"MERGE refused: {directory} is not a folder")
        return EXIT_USAGE
    from services.utils.jarvis_crypto import jarvis_crypto  # pylint: disable=import-outside-toplevel

    return merge_local(directory, user_id, jarvis_crypto.encrypt, jarvis_crypto.decrypt, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
