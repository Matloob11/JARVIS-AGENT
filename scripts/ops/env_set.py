"""Set-if-missing keys in both PC env files without ever printing a value (plan task C-26, contract K-15).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/env_set.py KEY=VALUE [KEY=VALUE ...] [--secret KEY ...]
    .venv_312/Scripts/python.exe scripts/ops/env_set.py --check [KEY ...]

Files: deploy/windows-local/.env (fills keys missing from the root .env, C-09) and the root .env, edited
together. --env-file PATH (repeatable) replaces that pair, e.g. in tests. Every file must already exist.

KEY=VALUE     adds KEY to each file that lacks it; a key with an empty value counts as missing. When one
              file already has a value, that value is copied to the others instead, so the files agree.
--secret KEY  the same with one new secrets.token_urlsafe(32) value for all files. Never type a secret
              value on a command line.
--comment T   writes '# T' above the keys this run appends to a file.
--dry-run     reports what would change and writes nothing.
--check       lists the key names (no values) with OK / MISSING / EMPTY / DIFFERS per key; with KEY
              arguments only those keys. Exit code 1 when a key is missing (or, for named keys, empty).

Only key names and what happened (added, filled, copied, present) are printed; values never are.
Existing lines stay byte-identical, line endings included: new keys are appended with the file's own line
ending and an empty 'KEY=' line is filled in place. A value is written unquoted when python-dotenv reads it
back unchanged, else quoted; a value that cannot be read back unchanged is refused. Both files are written
via a temp file and os.replace; if one write fails, the files already written are put back.
Back up first: scripts/ops/backup_local.py --now copies both files.
Exit codes: 0 ok, 1 --check found a missing key, 2 usage or file error (nothing written).
"""
from __future__ import annotations

import argparse
import io
import os
import re
import secrets
import sys
from pathlib import Path

from dotenv.parser import parse_stream

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILES = (ROOT / "deploy" / "windows-local" / ".env", ROOT / ".env")
KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BOM = "\ufeff"
TEMP_SUFFIX = ".env_set.tmp"  # git-ignored ('*.tmp', '.env.*')


class EnvError(Exception):
    """Stops the run before anything is written. The message never contains a value."""


def _label(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _empty_assignment(key: str) -> re.Pattern[str]:
    """A binding text that assigns nothing: optional blank lines, 'KEY=' or 'export KEY = ""', its line ending."""
    return re.compile(rf"(\s*(?:export[ \t]+)?{re.escape(key)}[ \t]*=)[ \t]*(?:''|\"\")?[ \t]*(\r\n|\n|\r)?")


def render_value(key: str, value: str) -> str:
    """The text written after 'KEY=': unquoted when python-dotenv reads it back unchanged, else quoted."""
    if any(ch in value for ch in "\r\n\x00"):
        raise EnvError(f"{key}: multi-line values are not supported")
    for token in (value, f"'{value}'", f'"{value}"'):
        bindings = [b for b in parse_stream(io.StringIO(f"{key}={token}\n")) if b.key is not None or b.error]
        if len(bindings) == 1 and not bindings[0].error and bindings[0].key == key and bindings[0].value == value:
            return token
    raise EnvError(f"{key}: the value cannot be written to a .env line unchanged")


class EnvFile:
    """One .env file: its original bytes, python-dotenv's reading of it and this run's edits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.label = _label(path)
        if not path.is_file():
            raise EnvError(f"{self.label}: file not found")
        self.raw = path.read_bytes()
        try:
            text = self.raw.decode("utf-8")
        except UnicodeDecodeError:
            raise EnvError(f"{self.label}: not UTF-8 text") from None
        self.bom = BOM if text.startswith(BOM) else ""
        text = text[len(self.bom):]
        bindings = list(parse_stream(io.StringIO(text)))
        self.chunks = [b.original.string for b in bindings]
        if "".join(self.chunks) != text:
            raise EnvError(f"{self.label}: could not be read exactly")
        self.keys = [b.key if not b.error else None for b in bindings]
        self.values: dict[str, str] = {}  # last assignment wins, as in python-dotenv
        for b in bindings:
            if b.key is not None and not b.error:
                self.values[b.key] = b.value or ""
        crlf = text.count("\r\n")
        self.eol = "\r\n" if crlf and crlf >= text.count("\n") - crlf else "\n"
        self.appended: list[str] = []
        self.comment: str | None = None

    def value(self, key: str) -> str:
        """The key's value; '' when it is absent or empty."""
        return self.values.get(key, "")

    def set(self, key: str, token: str) -> str:
        """Fills an empty 'KEY=' line in place or appends 'KEY=<token>'; returns 'filled' or 'added'."""
        last = max((i for i, k in enumerate(self.keys) if k == key), default=None)
        if last is not None:
            match = _empty_assignment(key).fullmatch(self.chunks[last])
            if match:
                self.chunks[last] = match.group(1) + token + (match.group(2) or "")
                return "filled"
        self.appended.append(f"{key}={token}")
        return "added"

    def render(self) -> bytes:
        text = "".join(self.chunks)
        if self.appended:
            if text and not text.endswith(("\n", "\r")):
                text += self.eol
            lines = ([f"# {self.comment}"] if self.comment else []) + self.appended
            text += "".join(line + self.eol for line in lines)
        return (self.bom + text).encode("utf-8")

    def changed(self) -> bool:
        return self.render() != self.raw


def set_keys(files: list[EnvFile], wanted: list[tuple[str, str | None]]) -> list[str]:
    """wanted: (KEY, value) pairs; value None asks for a new secret. Returns report lines without values."""
    report = []
    for key, value in wanted:
        missing = [f for f in files if not f.value(key)]
        if not missing:
            differ = len({f.value(key) for f in files}) > 1
            report.append(f"{key}: present" + (" (the files hold different values)" if differ else ""))
            continue
        source = next((f for f in files if f.value(key)), None)
        if source is not None:
            new_value, prefix = source.value(key), f"value from {source.label} "
        elif value is None:
            new_value, prefix = secrets.token_urlsafe(32), "new secret "
        else:
            new_value, prefix = value, ""
        token = render_value(key, new_value)
        actions = [(f.set(key, token), f.label) for f in missing]
        parts = []
        for action in ("added", "filled"):
            labels = [label for done, label in actions if done == action]
            if labels:
                parts.append(f"{action} in {', '.join(labels)}")
        report.append(f"{key}: {prefix}{'; '.join(parts)}")
    return report


def write_files(files: list[EnvFile]) -> list[str]:
    """Writes the changed files (temp file + os.replace each); puts written files back if one write fails."""
    changed = [f for f in files if f.changed()]
    temps: list[tuple[EnvFile, Path]] = []
    replaced: list[EnvFile] = []
    try:
        for f in changed:
            tmp = f.path.with_name(f.path.name + TEMP_SUFFIX)
            tmp.write_bytes(f.render())
            temps.append((f, tmp))
        for f, tmp in temps:
            os.replace(tmp, f.path)
            replaced.append(f)
    except OSError as exc:
        not_restored = []
        for f in replaced:
            try:
                f.path.write_bytes(f.raw)
            except OSError:
                not_restored.append(f.label)
        note = (f"; could not put back {', '.join(not_restored)} (restore it from the backup)"
                if not_restored else "; no file was changed")
        raise EnvError(f"writing failed ({exc.strerror or type(exc).__name__}){note}") from None
    finally:
        for _, tmp in temps:
            if tmp.exists():
                tmp.unlink()
    return [f.label for f in changed]


def check(files: list[EnvFile], names: list[str]) -> int:
    """Prints OK / MISSING / EMPTY / DIFFERS per key (never a value); 1 when a key is missing."""
    print("; ".join(f"{f.label}: {len(f.values)} keys" for f in files))
    explicit = bool(names)
    if not explicit:
        names = list(dict.fromkeys(key for f in files for key in f.values))
    counts = dict.fromkeys(("OK", "MISSING", "EMPTY", "DIFFERS"), 0)
    for name in names:
        absent = [f.label for f in files if name not in f.values]
        empty = [f.label for f in files if name in f.values and not f.values[name]]
        if absent:
            status, detail = "MISSING", f"not in {', '.join(absent)}"
        elif empty:
            status, detail = "EMPTY", f"empty in {', '.join(empty)}"
        elif len({f.values[name] for f in files}) > 1:
            status, detail = "DIFFERS", "the files hold different values"
        else:
            status, detail = "OK", ""
        counts[status] += 1
        print(f"{status:<8} {name}" + (f"  ({detail})" if detail else ""))
    print("summary: " + ", ".join(f"{n} {s}" for s, n in counts.items()))
    failed = counts["MISSING"] + (counts["EMPTY"] if explicit else 0)
    return 1 if failed else 0


class _Parser(argparse.ArgumentParser):
    """argparse quotes the typed text in some errors (an unknown argument, '--check=X', an ambiguous '--c=X'),
    and that text could be a value: cut the message off before it."""

    _QUOTING_ERRORS = ("unrecognized arguments", "ambiguous option", "ignored explicit argument")

    def error(self, message: str):  # type: ignore[override]
        for marker in self._QUOTING_ERRORS:
            head, found, _ = message.partition(marker)
            if found:
                message = f"{head}{marker} (not shown: it may contain a value)"
                break
        super().error(message)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(
        prog="env_set.py",
        description="Set-if-missing keys in deploy/windows-local/.env and .env without printing values.",
    )
    parser.add_argument("items", nargs="*", metavar="KEY=VALUE",
                        help="keys to set when missing (with --check: key names to check)")
    parser.add_argument("--secret", action="append", default=[], metavar="KEY",
                        help="set KEY when missing to a new secrets.token_urlsafe(32) value")
    parser.add_argument("--check", action="store_true",
                        help="list key names per file (no values); exit 1 when a key is missing")
    parser.add_argument("--comment", metavar="TEXT", help="write '# TEXT' above the keys appended to a file")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--env-file", action="append", type=Path, metavar="PATH",
                        help="file to edit instead of deploy/windows-local/.env and .env (repeatable)")
    args = parser.parse_intermixed_args(argv)  # KEY=VALUE items may sit between options
    if args.check:
        if args.secret or args.comment or args.dry_run:
            parser.error("--check cannot be combined with --secret, --comment or --dry-run")
    elif not (args.items or args.secret):
        parser.error("give KEY=VALUE items and/or --secret KEY (or --check)")
    if args.comment is not None and (not args.comment.strip() or any(ch in args.comment for ch in "\r\n")):
        parser.error("--comment must be one non-empty line")
    return args


def wanted_keys(items: list[str], secret_keys: list[str], check_mode: bool) -> list[tuple[str, str | None]]:
    """Validated (KEY, value) pairs. Error messages name the key or the item number, never a value."""
    wanted: list[tuple[str, str | None]] = []
    for number, item in enumerate(items, 1):
        key, sep, value = item.partition("=")
        if not KEY_RE.fullmatch(key):
            raise EnvError(f"item {number}: not a valid KEY name (use KEY=VALUE)")
        if not check_mode:
            if not sep:  # the item may be a value typed after a key ('--secret KEY VALUE'): never echo it
                raise EnvError(f"item {number}: missing '=VALUE' (use --secret KEY for a generated secret)")
            if not value:
                raise EnvError(f"{key}: empty value; nothing to set")
            if "${" in value:
                raise EnvError(f"{key}: python-dotenv would expand '${{...}}' in the value; not supported")
        wanted.append((key, value if not check_mode else None))
    for key in secret_keys:
        if not KEY_RE.fullmatch(key):
            raise EnvError("--secret: not a valid KEY name")
        wanted.append((key, None))
    names = [key for key, _value in wanted]
    repeated = sorted({key for key in names if names.count(key) > 1})
    if repeated:
        raise EnvError(f"given more than once: {', '.join(repeated)}")
    return wanted


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = args.env_file or list(DEFAULT_FILES)
    try:
        if len({os.path.normcase(str(p.resolve())) for p in paths}) != len(paths):
            raise EnvError("the same file is given twice")
        wanted = wanted_keys(args.items, args.secret, args.check)
        files = [EnvFile(path) for path in paths]
        if args.check:
            return check(files, [key for key, _value in wanted])
        for f in files:
            f.comment = args.comment
        for line in set_keys(files, wanted):
            print(line)
        if args.dry_run:
            pending = [f.label for f in files if f.changed()]
            print(f"dry run: nothing written (would change {', '.join(pending) if pending else 'nothing'})")
            return 0
        written = write_files(files)
        print(f"written: {', '.join(written)}" if written else "no change")
        return 0
    except EnvError as exc:
        print(f"env_set: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
