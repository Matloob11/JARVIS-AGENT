"""Append-only env defaults for the PC env files, without ever printing a value (plan task C-46, contract K-35).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/env_defaults.py --tools-info
    .venv_312/Scripts/python.exe scripts/ops/env_defaults.py --add KEY=VALUE [KEY=VALUE ...]
    .venv_312/Scripts/python.exe scripts/ops/env_defaults.py --update KEY=VALUE [KEY=VALUE ...]
    .venv_312/Scripts/python.exe scripts/ops/env_defaults.py --check KEY [KEY ...]

Files: --file PATH (repeatable); without it the root .env and deploy/windows-local/.env (start-agent.bat copies
the latter over .env until EXT C-ENV-1 changes that, so both get the same lines). Every file must already exist.

--add KEY=VALUE   appends 'KEY=VALUE' to each file that has no 'KEY=' line yet. A file that has one keeps it as it
                  is, even when it is empty: existing lines are never rewritten, so no key ever gets a second value
                  (e.g. JARVIS_CONFIRM_TIMEOUT_S, owned elsewhere). An empty VALUE writes 'KEY='. A secret-like
                  name (containing KEY, TOKEN, SECRET or PASSWORD) is only ever added empty: fill it by hand or
                  with 'scripts/ops/env_set.py --secret KEY', never on a command line.
--tools-info      adds the contract K-35 (tools_info_env) names with their defaults, as --add would.
--update KEY=VALUE  sets a non-secret switch in place (every 'KEY=' line of it, keeping its 'KEY=' part and line
                  ending; an inline comment on that line is dropped) or appends it when missing. Allowed only for
                  JARVIS_SELF_REPAIR_APPLY, TAVILY_SEARCH_DEPTH, SIM_SOURCE, SIM_LOOKUP_SHOW_ADDRESS,
                  SIM_LOOKUP_DAILY_LIMIT and JARVIS_*_MODEL; a name containing
                  KEY, TOKEN, SECRET or PASSWORD is always refused.
--check KEY ...   prints 'present' / 'missing' per key (never a value); exit 1 when a key is missing in a file.
--comment TEXT    writes '# TEXT' above the keys this run appends to a file.
--dry-run         reports what would change; writes nothing and makes no backup.
--backup-root DIR before writing, each file that changes is copied to DIR/env-<timestamp>/ (default
                  %USERPROFILE%/JARVIS-backups, outside the repo; a folder inside the repo is refused).

Output is key names, file labels and what happened ('added X', 'kept X', 'updated X'); values never appear.
Every other line stays byte-identical (encoding, BOM and line endings kept: new lines use the file's own line
ending). Files are written via a temp file and os.replace (scripts/ops/env_set.py); if one write fails, the files
already written are put back.
Exit codes: 0 ok, 1 --check found a missing key, 2 usage, refusal or file error (nothing written).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import sys
import time
from pathlib import Path
from types import ModuleType


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


env_set = _load_sibling("env_set")
EnvError = env_set.EnvError

ROOT = env_set.ROOT
DEFAULT_FILES = (ROOT / ".env", ROOT / "deploy" / "windows-local" / ".env")
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
UPDATE_ALLOWED = re.compile(r"JARVIS_SELF_REPAIR_APPLY|TAVILY_SEARCH_DEPTH|SIM_SOURCE|SIM_LOOKUP_SHOW_ADDRESS"
                            r"|SIM_LOOKUP_DAILY_LIMIT|JARVIS_[A-Z0-9_]+_MODEL")
TOOLS_INFO_COMMENT = "Tools-info keys (C-46, contract K-35): scripts/ops/env_defaults.py"

# Contract K-35 (tools_info_env), docs/CONTRACTS.md. Secrets stay empty; the user fills them.
TOOLS_INFO_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("JARVIS_TEXT_MODEL", "gemini-3.5-flash"),
    ("JARVIS_VISION_MODEL", "gemini-3.8-flash"),
    ("JARVIS_GROQ_TEXT_MODEL", "openai/gpt-oss-120b"),
    ("JARVIS_GROQ_VISION_MODEL", "qwen/qwen3.8-27b"),
    ("JARVIS_OPENROUTER_MODEL", "openrouter/free"),
    ("JARVIS_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"),
    ("JARVIS_IMAGE_PROVIDER", "auto"),
    ("POLLINATIONS_TOKEN", ""),
    ("TAVILY_SEARCH_DEPTH", "advanced"),
    ("SMTP_HOST", "smtp.gmail.com"),
    ("SMTP_PORT", "587"),
    ("JARVIS_DOC_DIRS", ""),
    ("JARVIS_RAG_WARM_DELAY_S", "120"),
    ("JARVIS_FRAME_MAX_AGE_S", "10"),
    ("JARVIS_SELF_REPAIR_APPLY", "0"),
    ("SIM_SOURCE", ""),
    ("SIM_LOOKUP_API_URL", ""),
    ("SIM_LOOKUP_API_KEY", ""),
    ("SIM_LOOKUP_DAILY_LIMIT", "20"),
    ("SIM_LOOKUP_SHOW_ADDRESS", "1"),  # K-34 v14/v15: address on by default, 0 = opt-out
)


def secret_like(key: str) -> bool:
    """True for a name that may hold a secret (KEY, TOKEN, SECRET or PASSWORD in it, any case)."""
    upper = key.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def default_backup_root() -> Path:
    """%USERPROFILE%/JARVIS-backups (the folder scripts/ops/backup_local.py also uses), outside the repo."""
    return Path(os.getenv("USERPROFILE") or os.path.expanduser("~")) / "JARVIS-backups"


def _token(key: str, value: str) -> str:
    """The text written after 'KEY='. Raises EnvError (naming the key only) for a value .env cannot hold."""
    if "${" in value:
        raise EnvError(f"{key}: python-dotenv would expand '${{...}}' in the value; not supported")
    return env_set.render_value(key, value) if value else ""


def parse_items(items: list[str], option: str) -> list[tuple[str, str]]:
    """Validated (KEY, VALUE) pairs. Error messages name the key or the item number, never a value."""
    pairs: list[tuple[str, str]] = []
    for number, item in enumerate(items, 1):
        key, sep, value = item.partition("=")
        if not env_set.KEY_RE.fullmatch(key):
            raise EnvError(f"{option} item {number}: not a valid KEY name (use KEY=VALUE)")
        if not sep:
            raise EnvError(f"{option} item {number}: missing '=' (use KEY=VALUE, or KEY= for an empty value)")
        pairs.append((key, value))
    return pairs


def _has_line(env_file, key: str) -> bool:
    """True when the file has a 'KEY=' assignment (commented-out lines do not count)."""
    return key in env_file.keys


def add_keys(files: list, wanted: list[tuple[str, str]]) -> list[str]:
    """Appends each key to the files without a 'KEY=' line. Returns report lines (key names and labels only)."""
    report = []
    for key, value in wanted:
        if value and secret_like(key):
            raise EnvError(f"{key}: a secret-like name is only added empty ('{key}='); fill it by hand or with "
                           f"scripts/ops/env_set.py --secret {key}")
        token = _token(key, value)
        added, kept = [], []
        for f in files:
            if _has_line(f, key):
                kept.append(f.label)
            else:
                f.appended.append(f"{key}={token}")
                added.append(f.label)
        if added:
            report.append(f"added {key} ({', '.join(added)})")
        if kept:
            report.append(f"kept {key} ({', '.join(kept)})")
    return report


def _replace_value(env_file, key: str, token: str) -> bool:
    """Puts token after every 'KEY=' of key in the file. Returns True when a line changed."""
    pattern = re.compile(rf"(\s*(?:export[ \t]+)?{re.escape(key)}[ \t]*=)[^\r\n]*(\r\n|\n|\r)?")
    changed = False
    for i, name in enumerate(env_file.keys):
        if name != key:
            continue
        match = pattern.fullmatch(env_file.chunks[i])
        if not match:
            raise EnvError(f"{env_file.label}: {key} spans several lines; edit that file by hand")
        new = match.group(1) + token + (match.group(2) or "")
        if new != env_file.chunks[i]:
            env_file.chunks[i] = new
            changed = True
    return changed


def update_keys(files: list, wanted: list[tuple[str, str]]) -> list[str]:
    """Sets allow-listed non-secret switches. Returns report lines (key names and labels only)."""
    for key, _value in wanted:  # refuse before touching anything
        if secret_like(key):
            raise EnvError(f"--update refused for {key}: the name looks like a secret "
                           f"(contains {', '.join(m for m in SECRET_MARKERS if m in key.upper())})")
        if not UPDATE_ALLOWED.fullmatch(key):
            raise EnvError(f"--update refused for {key}: only JARVIS_SELF_REPAIR_APPLY, TAVILY_SEARCH_DEPTH, "
                           "SIM_SOURCE, SIM_LOOKUP_SHOW_ADDRESS, SIM_LOOKUP_DAILY_LIMIT and JARVIS_*_MODEL "
                           "may be updated")
    report = []
    for key, value in wanted:
        token = _token(key, value)
        groups: dict[str, list[str]] = {"updated": [], "added": [], "kept": []}
        for f in files:
            if not _has_line(f, key):
                f.appended.append(f"{key}={token}")
                groups["added"].append(f.label)
            elif _replace_value(f, key, token):
                groups["updated"].append(f.label)
            else:
                groups["kept"].append(f.label)
        for action, labels in groups.items():
            if labels:
                report.append(f"{action} {key} ({', '.join(labels)})")
    return report


def check(files: list, names: list[str]) -> int:
    """Prints present / missing per key (never a value); 1 when a key is missing in any file."""
    print("; ".join(f"{f.label}: {len(f.values)} keys" for f in files))
    missing_count = 0
    for name in names:
        absent = [f.label for f in files if not _has_line(f, name)]
        empty = [f.label for f in files if _has_line(f, name) and not f.value(name)]
        if absent:
            missing_count += 1
            print(f"missing {name} (not in {', '.join(absent)})")
        else:
            print(f"present {name}" + (f" (empty in {', '.join(empty)})" if empty else ""))
    print(f"summary: {len(names) - missing_count} present, {missing_count} missing")
    return 1 if missing_count else 0


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def backup(files: list, backup_root: Path) -> Path:
    """Copies each file to backup_root/env-<timestamp>/ (a new folder). The folder name holds no value."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = backup_root / f"env-{stamp}"
    n = 1
    while folder.exists():
        n += 1
        folder = backup_root / f"env-{stamp}-{n}"
    try:
        folder.mkdir(parents=True)
        for index, f in enumerate(files, 1):
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f.label).strip("_") or "env"
            shutil.copy2(f.path, folder / f"{index:02d}_{safe}")
    except OSError as exc:
        raise EnvError(f"backup failed ({exc.strerror or type(exc).__name__}); no file was changed") from None
    return folder


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="env_defaults.py",
                     description="Append-only env defaults for the PC env files; never prints a value.")
    parser.add_argument("--file", action="append", type=Path, metavar="PATH",
                        help="env file to edit (repeatable; default .env and deploy/windows-local/.env)")
    parser.add_argument("--add", action="extend", nargs="+", default=[], metavar="KEY=VALUE",
                        help="append KEY=VALUE to each file without a 'KEY=' line")
    parser.add_argument("--tools-info", action="store_true",
                        help="add the contract K-35 (tools_info_env) names with their defaults")
    parser.add_argument("--update", action="extend", nargs="+", default=[], metavar="KEY=VALUE",
                        help="set an allow-listed non-secret switch in place (or append it)")
    parser.add_argument("--check", action="extend", nargs="+", default=[], metavar="KEY",
                        help="print present / missing per key; exit 1 when one is missing")
    parser.add_argument("--comment", metavar="TEXT", help="write '# TEXT' above the keys appended to a file")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--backup-root", type=Path, metavar="DIR",
                        help="backup folder root (default %%USERPROFILE%%/JARVIS-backups; never inside the repo)")
    args = parser.parse_args(argv)
    editing = bool(args.add or args.update or args.tools_info)
    if args.check and (editing or args.comment or args.dry_run):
        parser.error("--check cannot be combined with --add, --update, --tools-info, --comment or --dry-run")
    if not (args.check or editing):
        parser.error("give --add KEY=VALUE, --tools-info, --update KEY=VALUE or --check KEY")
    if args.comment is not None and (not args.comment.strip() or any(ch in args.comment for ch in "\r\n")):
        parser.error("--comment must be one non-empty line")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = args.file or list(DEFAULT_FILES)
    try:
        if len({os.path.normcase(str(p.resolve())) for p in paths}) != len(paths):
            raise EnvError("the same file is given twice")
        if args.check:
            for number, name in enumerate(args.check, 1):
                if not env_set.KEY_RE.fullmatch(name):
                    raise EnvError(f"--check item {number}: not a valid KEY name")
            return check([env_set.EnvFile(p) for p in paths], args.check)

        explicit = parse_items(args.add, "--add")
        updates = parse_items(args.update, "--update")
        given = {key for key, _value in explicit + updates}
        # A preset key also named in --add / --update follows that item instead of the preset default.
        adds = [(k, v) for k, v in TOOLS_INFO_DEFAULTS if k not in given] if args.tools_info else []
        adds += explicit
        names = [key for key, _value in adds + updates]
        repeated = sorted({key for key in names if names.count(key) > 1})
        if repeated:
            raise EnvError(f"given more than once: {', '.join(repeated)}")
        backup_root = args.backup_root or default_backup_root()
        if not args.dry_run and _inside_repo(backup_root):
            raise EnvError("--backup-root must be outside the repo")

        files = [env_set.EnvFile(p) for p in paths]
        comment = args.comment or (TOOLS_INFO_COMMENT if args.tools_info else None)
        for f in files:
            f.comment = comment
        report = update_keys(files, updates) + add_keys(files, adds)
        for line in report:
            print(line)
        changed = [f for f in files if f.changed()]
        if args.dry_run:
            print(f"dry run: nothing written (would change {', '.join(f.label for f in changed) or 'nothing'})")
            return 0
        if not changed:
            print("no change")
            return 0
        folder = backup(changed, backup_root)
        print(f"backup: {folder}")
        written = env_set.write_files(files)
        print(f"written: {', '.join(written)}")
        return 0
    except EnvError as exc:
        print(f"env_defaults: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
