"""Lists the newest messages of one user's memory, decrypted, on this machine only (plan task C-52, step 7).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/memory/dump_memory.py --user matloob --last 40 [--dir PATH] [--show-text]

Reads <dir>/<user>_memory.json (default dir: the configured conversations/ folder; user: --user, else JARVIS_USER_ID,
else matloob) the way services/ai_core/memory_store.py reads it and lists its newest messages, oldest first: number,
local time, role, persona (a reply; 'unknown' for one saved before C-52), source, id and the length of the text. It
ends with a check line: how many messages, whether they are in time order, how many ids repeat and how many texts
repeat back to back. It only reads: nothing is written, moved or sent anywhere.

The message TEXT is never printed by default. --show-text prints it, for the owner reading a console by hand, and is
refused (exit 2, before anything is read) unless stdin and stdout are both a console. That console test alone proves
nothing about who reads the output: an agent's terminal tool that runs commands in a PTY or ConPTY (Cursor's agent
terminal, VS Code tasks) is a console too, and on Windows NUL counts as one. So the explicit flag is the guard, and
without it chat text never lands in an agent's captured output, a transcript or a log. Agents never pass --show-text.

Exit codes: 0 ok (a missing file too: nothing saved yet), 2 usage or configuration (--show-text without a console, no
JARVIS_ENCRYPTION_KEY, no such folder), 4 the file cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_OK, EXIT_USAGE, EXIT_UNREADABLE = 0, 2, 4
SUFFIX = "_memory.json"
DEFAULT_LAST = 40


class DumpRefused(Exception):
    """The memory file cannot be read."""


def read_memory_file(path: Path, decrypt: Callable[[bytes], str]) -> list[Any]:
    """One memory file as a list: Fernet first, then a legacy plain JSON list (memory_store's order); never changes
    the file."""
    data = path.read_bytes()
    if not data:
        return []
    try:
        loaded = json.loads(decrypt(data))
    except Exception:  # pylint: disable=broad-exception-caught  # not ours with this key: maybe legacy plain JSON
        try:
            loaded = json.loads(data.decode("utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise DumpRefused(f"{path.name}: cannot be decrypted with JARVIS_ENCRYPTION_KEY or read as JSON") from exc
    if not isinstance(loaded, list):
        raise DumpRefused(f"{path.name}: holds {type(loaded).__name__}, not a list")
    return loaded


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _saved_at(conversation: dict[str, Any]) -> float | None:
    stamp = conversation.get("timestamp")
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part if isinstance(part, str) else part.get("text") if isinstance(part, dict) else None
                 for part in content]
        return " ".join(part for part in parts if isinstance(part, str))
    return "" if content is None else str(content)


def flatten(conversations: list[Any]) -> list[dict[str, Any]]:
    """Every message in save order: {when (epoch seconds: its ts, else its conversation's save time), role, persona,
    source, id, text}."""
    rows: list[dict[str, Any]] = []
    for conversation in conversations:
        if not isinstance(conversation, dict) or not isinstance(conversation.get("messages"), list):
            continue
        saved = _saved_at(conversation)
        for message in conversation["messages"]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "?")
            persona = message.get("persona") if role == "assistant" else None
            rows.append({
                "when": _epoch(message.get("ts")) or saved,
                "role": role,
                "persona": persona if isinstance(persona, str) and persona else ("unknown" if role == "assistant"
                                                                                 else "-"),
                "source": str(message.get("source") or "-"),
                "id": str(message.get("id") or "-"),
                "text": _text(message.get("content")),
            })
    return rows


def report(rows: list[dict[str, Any]], last: int, *, show_text: bool, out: Callable[[str], Any] = print) -> dict:
    """Prints the newest `last` rows, the text only with show_text (main: --show-text by hand in a console), then the
    check line; returns the check."""
    shown = rows[-last:] if last > 0 else []
    for number, row in enumerate(shown, 1):
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["when"])) if row["when"] is not None else "?"
        head = f"{number:>3}  {when}  {row['role']:<9} {row['persona']:<7} {row['source']:<6} {row['id']}"
        out(f"{head}  {row['text']}" if show_text else f"{head}  ({len(row['text'])} chars)")
    ids = [row["id"] for row in shown if row["id"] != "-"]
    times = [row["when"] for row in shown if row["when"] is not None]
    check = {
        "shown": len(shown),
        "total": len(rows),
        "in_order": all(a <= b for a, b in zip(times, times[1:])),
        "duplicate_ids": len(ids) - len(set(ids)),
        "repeated_texts": sum(1 for a, b in zip(shown, shown[1:])
                              if (a["role"], a["text"]) == (b["role"], b["text"])),
    }
    out(f"CHECK {check['shown']} of {check['total']} messages shown; time order: "
        f"{'ok' if check['in_order'] else 'NOT in order'}; duplicate ids: {check['duplicate_ids']}; texts repeated "
        f"back to back: {check['repeated_texts']}"
        + ("" if show_text else "; text hidden (only --show-text, by hand in a console, shows it)"))
    return check


def run_by_hand() -> bool:
    """True when stdin and stdout are both a console. Not proof that a person reads the screen (an agent's PTY or
    ConPTY is a console too, and on Windows NUL counts as one): it only gates --show-text, the explicit opt-in, so a
    pipe, a file or captured output never gets the text even with the flag."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _print(line: str) -> None:
    try:
        print(line)
    except UnicodeEncodeError:  # a console without the characters: shown escaped rather than not at all
        print(line.encode("ascii", "backslashreplace").decode("ascii"))


def _valid_user(user_id: str) -> bool:
    return bool(user_id) and user_id not in (".", "..") and not any(ch in user_id for ch in "/\\:*?\"<>|")


def _usage_refusal(args: argparse.Namespace) -> str | None:
    """Why the arguments are refused before anything is read, else None."""
    if args.last < 1:
        return "--last must be 1 or more"
    if args.show_text and not run_by_hand():
        return ("--show-text prints chat text and needs a console on stdin and stdout (never a pipe, a file or "
                "captured output); run without it for the ids, times and the check")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List the newest messages of one memory file (C-52; local only)")
    parser.add_argument("--user", default=None, help="the memory identity (default: JARVIS_USER_ID, else matloob)")
    parser.add_argument("--last", type=int, default=DEFAULT_LAST, help=f"how many messages (default {DEFAULT_LAST})")
    parser.add_argument("--dir", default=None, help="memory folder (default: the configured conversations folder)")
    parser.add_argument("--show-text", action="store_true",
                        help="also print each message's text: only for the owner by hand in a console (refused unless "
                             "stdin and stdout are both a console); agents never pass it")
    args = parser.parse_args(argv)
    refusal = _usage_refusal(args)
    if refusal:
        print(f"DUMP refused: {refusal}")
        return EXIT_USAGE

    # loads the PC .env (override=False)
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

    user_id = (args.user or config.user_id or "").strip()
    if not _valid_user(user_id):
        print("DUMP refused: no valid user id (--user or JARVIS_USER_ID)")
        return EXIT_USAGE
    if not (os.getenv("JARVIS_ENCRYPTION_KEY") or "").strip():
        print("DUMP refused: JARVIS_ENCRYPTION_KEY is not set; the memory file could not be read")
        return EXIT_USAGE
    directory = Path(args.dir) if args.dir else Path(config.conversations_dir)
    if not directory.is_dir():
        print(f"DUMP refused: {directory} is not a folder")
        return EXIT_USAGE
    path = directory / f"{user_id}{SUFFIX}"
    if not path.is_file():
        print(f"DUMP {path.name}: no such file in {directory.name}/ (nothing saved for {user_id} yet)")
        return EXIT_OK
    from services.utils.jarvis_crypto import jarvis_crypto  # pylint: disable=import-outside-toplevel

    try:
        conversations = read_memory_file(path, jarvis_crypto.decrypt)
    except DumpRefused as exc:
        print(f"DUMP refused: {exc}")
        return EXIT_UNREADABLE
    rows = flatten(conversations)
    print(f"DUMP {path.name}: {len(conversations)} conversations, {len(rows)} messages")
    report(rows, args.last, show_text=args.show_text, out=_print)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
