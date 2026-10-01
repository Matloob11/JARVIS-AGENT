"""Undo an ASCII rewrite of a text file: put back the non-ASCII characters that became '?'.

An editor or script that saves UTF-8 text as ASCII/ANSI replaces every middle dot, arrow, Urdu letter
or emoji with '?'. This restores them from an earlier version (default: the committed one) while keeping
every later edit: only a '?' that lines up with a non-ASCII character of the earlier text is changed.
The result is written only when it differs from the current file in exactly those '?' characters.

Usage (repo root, Git Bash or PowerShell):
    python scripts/coord/restore_unicode.py plan.md               # dry run: what would be restored
    python scripts/coord/restore_unicode.py plan.md --write       # restore in place
    python scripts/coord/restore_unicode.py plan.md --base e75b22a --write
Exit code: 0 restored or nothing to do, 1 safety check failed (file left unchanged), 2 usage error.
"""
from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys


def asciify(text: str) -> str:
    """Every non-ASCII character as '?', the way an ASCII save writes it."""
    return "".join(ch if ord(ch) < 128 else "?" for ch in text)


LOST_MARKS = ("?", "�")  # an ASCII save writes '?', a failed decode writes U+FFFD


def lost_positions(old_line: str, new_line: str) -> list[tuple[int, int]]:
    """(old index, new index) pairs where a non-ASCII character of old_line is a '?' (or U+FFFD) in
    new_line.

    Other edits on the line are allowed: only the parts that still match are compared.
    """
    if old_line == new_line or old_line.isascii() or not any(m in new_line for m in LOST_MARKS):
        return []
    pairs = []
    matcher = difflib.SequenceMatcher(None, asciify(old_line), asciify(new_line), autojunk=False)
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag == "equal":
            pairs += [(i1 + k, j1 + k) for k in range(i2 - i1)
                      if ord(old_line[i1 + k]) >= 128 and new_line[j1 + k] in LOST_MARKS]
    return pairs


def paired_lines(old: str, new: str):
    """Yield (old_line, new_index, new_line) for lines of new that correspond to a line of old.

    Lines are matched on their ASCII form, so a line that only lost its non-ASCII characters still
    pairs with its original. Inside an edited block, a new line pairs with its closest old line.
    """
    a, b = old.split("\n"), new.split("\n")
    aa, bb = [asciify(x) for x in a], [asciify(x) for x in b]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, aa, bb, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                yield a[i1 + k], j1 + k, b[j1 + k]
        elif tag == "replace":
            block = aa[i1:i2]
            for j in range(j1, j2):
                best = difflib.get_close_matches(bb[j], block, n=1, cutoff=0.6)
                if best:
                    yield a[i1 + block.index(best[0])], j, b[j]


def restore(old: str, new: str) -> tuple[str, int]:
    """(new with the lost characters of old put back, how many were put back)."""
    lines = new.split("\n")
    count = 0
    for old_line, j, new_line in paired_lines(old, new):
        pos = lost_positions(old_line, new_line)
        if pos:
            chars = list(new_line)
            for i, jj in pos:
                chars[jj] = old_line[i]
            lines[j] = "".join(chars)
            count += len(pos)
    return "\n".join(lines), count


def loss_lines(old: str, new: str) -> list[int]:
    """1-based line numbers of new where committed non-ASCII characters became '?'."""
    return sorted(j + 1 for old_line, j, new_line in paired_lines(old, new) if lost_positions(old_line, new_line))


def _git_text(root: str, spec: str) -> str | None:
    proc = subprocess.run(["git", "cat-file", "blob", spec], capture_output=True, cwd=root, check=False)
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore non-ASCII characters an ASCII save turned into '?'.")
    parser.add_argument("path", help="file to repair, relative to the repo root")
    parser.add_argument("--base", default="HEAD", help="git revision with the good text (default HEAD)")
    parser.add_argument("--write", action="store_true", help="write the result (default: dry run)")
    args = parser.parse_args(argv)

    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                          check=True).stdout.strip()
    rel = args.path.replace("\\", "/")
    path = os.path.join(root, rel)
    old = _git_text(root, f"{args.base}:{rel}")
    if old is None or not os.path.isfile(path):
        print(f"{rel}: not found in {args.base} or on disk")
        return 2
    with open(path, "rb") as fh:
        raw = fh.read()
    crlf = b"\r\n" in raw
    cur = raw.decode("utf-8").replace("\r\n", "\n")
    fixed, count = restore(old.replace("\r\n", "\n"), cur)
    only_lost_marks = len(fixed) == len(cur) and all(
        c in LOST_MARKS and ord(f) >= 128 for f, c in zip(fixed, cur) if f != c)
    if not only_lost_marks:
        print(f"{rel}: safety check failed, file not changed")
        return 1
    print(f"{rel}: {count} character(s) to restore from {args.base}")
    if count and args.write:
        data = fixed.replace("\n", "\r\n") if crlf else fixed
        with open(path, "wb") as fh:
            fh.write(data.encode("utf-8"))
        print(f"{rel}: restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
