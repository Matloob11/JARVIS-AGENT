"""Lane checker for the shared Claude + Cursor working tree (plan task C-05, contract K-51 COORD-1).

The lanes live in .coord/lanes.json, the machine-readable copy of plan.md section 2.1:
    {"claude": [globs], "cursor": [globs], "shared": [paths], "append_only": [paths]}
Patterns are repo-relative POSIX globs: '*' and '?' stay inside one path segment, '**' spans
directories and 'dir/**' covers everything below dir. When several patterns match a path, the most
specific one wins: an exact path beats any glob, then the glob with more literal characters
(so 'tests/test_whatsapp.py' (cursor) beats 'tests/*.py' (claude)).

Usage (repo root, PY=.venv_312/Scripts/python.exe):
    $PY scripts/coord/check_lane.py --agent claude|cursor|user <paths...>
        Lists the paths outside that agent's lane and exits 1 (exit 0 when all are inside).
        Shared files are inside every lane; 'user' has no lane limit.
    $PY scripts/coord/check_lane.py --whoowns <path> [<path>...]
        Prints the owner: claude, cursor, shared or unowned (exit 1 if a path is unowned).

The pre-commit guard (scripts/coord/precommit.py) runs the same checks on the staged paths.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import posixpath
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LANES_FILE = ".coord/lanes.json"
LANE_NAMES = ("claude", "cursor")
AGENTS = ("claude", "cursor", "user")
SHARED = "shared"
UNOWNED = "unowned"
_GROUPS = (SHARED, *LANE_NAMES)  # order also breaks exact ties (never expected; tests check it)
_KEYS = (*_GROUPS, "append_only")
_WILDCARD = re.compile(r"\*+|\?|\[[^\]]*\]")


@dataclass(frozen=True)
class Rule:
    owner: str  # claude | cursor | shared
    pattern: str
    regex: re.Pattern
    rank: tuple  # (exact path?, literal characters): higher is more specific


@dataclass(frozen=True)
class Lanes:
    rules: tuple
    append_only: tuple


def _clean_pattern(pattern: str) -> str:
    p = pattern.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _translate(pattern: str) -> str:
    out, i, n = [], 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")  # zero or more directories
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            j = pattern.find("]", j)
            if j < 0:
                out.append(re.escape("["))
                i += 1
                continue
            body = pattern[i + 1:j]
            if body[:1] in ("!", "^"):
                body = "^" + body[1:]
            out.append("(?!/)[" + body.replace("\\", "\\\\") + "]")
            i = j + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def glob_regex(pattern: str) -> re.Pattern:
    """Compile a repo-relative POSIX glob; use with fullmatch. 'dir/**' also matches 'dir' itself."""
    if pattern.endswith("/**"):
        return re.compile(_translate(pattern[:-3]) + "(?:/.*)?")
    return re.compile(_translate(pattern))


def _rank(pattern: str) -> tuple:
    if not _WILDCARD.search(pattern):
        return (1, len(pattern))
    return (0, len(_WILDCARD.sub("", pattern)))


def _string_list(data: dict, key: str) -> list:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"'{key}' must be a list of path patterns")
    return value


def parse_lanes(text: str) -> Lanes:
    """Parse the JSON text of a lanes file. Keys starting with '_' are comments."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the lanes file must hold a JSON object")
    unknown = sorted(k for k in data if not k.startswith("_") and k not in _KEYS)
    if unknown:
        raise ValueError("unknown key(s): " + ", ".join(unknown))
    rules = []
    for owner in _GROUPS:
        for raw in _string_list(data, owner):
            pattern = _clean_pattern(raw)
            rules.append(Rule(owner, pattern, glob_regex(pattern), _rank(pattern)))
    append_only = tuple(normalize(p) for p in _string_list(data, "append_only"))
    return Lanes(tuple(rules), append_only)


def load_lanes(path: str | None = None) -> Lanes:
    """Read the lanes file (default: .coord/lanes.json in this repo)."""
    with open(path or os.path.join(ROOT, LANES_FILE), encoding="utf-8") as fh:
        return parse_lanes(fh.read())


def normalize(path: str, root: str = ROOT) -> str:
    r"""Repo-relative POSIX form: 'src\core\x.py', './src/core/x.py' and absolute paths in the repo -> 'src/core/x.py'.

    A path outside the repo comes back starting with '../' (or as an absolute path) and is in no lane.
    """
    p = path.strip().replace("\\", "/")
    m = re.match(r"^/([A-Za-z])/(.*)$", p)
    if os.name == "nt" and m:  # Git Bash style /c/Users/...
        p = f"{m.group(1)}:/{m.group(2)}"
    if re.match(r"^[A-Za-z]:/", p) or p.startswith("//") or (p.startswith("/") and os.name != "nt"):
        try:
            p = os.path.relpath(p, root).replace("\\", "/")
        except ValueError:  # another drive
            return p
    p = posixpath.normpath(p) if p else p
    return "" if p == "." else p


def best_rules(path: str, lanes: Lanes) -> list:
    """The matching rules with the highest specificity (normally exactly one)."""
    rel = normalize(path)
    hits = [r for r in lanes.rules if r.regex.fullmatch(rel)]
    if not hits:
        return []
    top = max(r.rank for r in hits)
    return [r for r in hits if r.rank == top]


def owner_of(path: str, lanes: Lanes) -> str:
    """claude | cursor | shared | unowned."""
    best = best_rules(path, lanes)
    return best[0].owner if best else UNOWNED


def outside_lane(agent: str, paths: Iterable[str], lanes: Lanes) -> list:
    """[(path, owner)] for every path the agent may not commit. Shared paths are fine for all; user = no limit."""
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {', '.join(AGENTS)}")
    if agent == "user":
        return []
    bad = []
    for path in paths:
        owner = owner_of(path, lanes)
        if owner not in (agent, SHARED):
            bad.append((normalize(path), owner))
    return bad


def _lines(text: str) -> list:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _first_mismatch(old: list, new: list) -> int:
    """0-based index of the first old line that is not at the same place in `new`."""
    for i, line in enumerate(old):
        if i >= len(new) or new[i] != line:
            return i
    return len(old)


def removed_lines(old: str, new: str) -> list:
    """1-based numbers of the lines of `old` that `new` does not keep unchanged at the same place.

    Append-only means `new` starts with every line of `old`, in order, and adds lines only after them.
    Inserting between old lines counts as changing them, because log entries repeat identical template
    lines and an insertion could otherwise hide an edit. Line endings and a missing final newline are ignored.
    """
    a, b = _lines(old), _lines(new)
    if b[:len(a)] == a:
        return []
    gone = []
    for tag, i1, i2, *_ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            gone.extend(range(i1 + 1, i2 + 1))
    return gone or [_first_mismatch(a, b) + 1]


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_lane.py",
        description="Check repo paths against the Claude/Cursor lanes in .coord/lanes.json (plan.md section 2.1).",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--agent", type=str.lower, choices=AGENTS,
                      help="list the paths outside this agent's lane and exit 1 if there are any (user = no limit)")
    mode.add_argument("--whoowns", action="store_true",
                      help="print the owner of each path: claude, cursor, shared or unowned")
    ap.add_argument("--lanes", metavar="FILE", help=f"lanes file (default: {LANES_FILE} in the repo)")
    ap.add_argument("paths", nargs="+", metavar="path")
    args = ap.parse_args(argv)
    try:
        lanes = load_lanes(args.lanes)
    except (OSError, ValueError) as exc:
        print(f"check_lane: cannot read the lanes file: {exc}", file=sys.stderr)
        return 2
    if args.whoowns:
        owners = [(normalize(p), owner_of(p, lanes)) for p in args.paths]
        if len(owners) == 1:
            print(owners[0][1])
        else:
            for path, owner in owners:
                print(f"{path}: {owner}")
        return 1 if any(owner == UNOWNED for _, owner in owners) else 0
    bad = outside_lane(args.agent, args.paths, lanes)
    for path, owner in bad:
        where = "in no lane" if owner == UNOWNED else f"owner: {owner}"
        print(f"outside the {args.agent} lane: {path} ({where})")
    if bad:
        return 1
    if args.agent == "user":
        print(f"ok: user has no lane limit ({len(args.paths)} path(s))")
    else:
        print(f"ok: {len(args.paths)} path(s) in the {args.agent} lane")
    return 0


if __name__ == "__main__":
    sys.exit(main())
