"""The PC fallback's secrets, DPAPI-wrapped while the VPS brain runs (plan task C-127 step 5; contracts K-47 v4 and
K-72, user decision D13). Only names are ever printed.

Usage (from the repo root, with the project venv, as the Windows user who runs JARVIS):
    .venv_312/Scripts/python.exe scripts/ops/fallback_env.py --status               names only: where each one is
    .venv_312/Scripts/python.exe scripts/ops/fallback_env.py --wrap [--dry-run]     vps mode: into the DPAPI store
    .venv_312/Scripts/python.exe scripts/ops/fallback_env.py --restore [--dry-run]  back into the env files
    .venv_312/Scripts/python.exe scripts/ops/fallback_env.py --set NAME --value-from-stdin   one value, where it is now
    .venv_312/Scripts/python.exe scripts/ops/fallback_env.py --discard --dry-run    a store that never unwraps here:
    .venv_312/Scripts/python.exe scripts/ops/fallback_env.py --discard --confirm    moved aside, never deleted

FALLBACK_NAMES are the secrets the PC brain needs and the Desktop Node never reads: LIVEKIT_API_SECRET (plus the
pending LIVEKIT_PC_API_SECRET of C-127 step 6), the legacy token (VORTEX_SECURITY_TOKEN, VITE_VORTEX_SECURITY_TOKEN),
the AI, image, search and weather keys, SIM_LOOKUP_API_KEY and EMAIL_APP_PASSWORD. Not on the list: CONTROLLER_TOKEN
and PINCHTAB_TOKEN ('agent_ctl node update' wraps them into the node's own secrets.dpapi), AGENT_TOKEN,
BRIDGE_HMAC_SECRET and JARVIS_ENCRYPTION_KEY (push_agent_env.py and migrate_agent_data.py read them), and the
non-secret names (LIVEKIT_API_KEY, EMAIL_USER, SEARCH_ENGINE_ID, SIM_LOOKUP_API_URL).

--wrap     Refused unless this laptop's JARVIS_BRAIN is vps and no PC worker holds run/agent.lock. Refused too (the dry
           run as well) while a PC copy of a VPS env file in the repo (PC_COPY_NAMES: deploy/hostinger/.env,
           bridge.env and .env.agent, the git-ignored files the pre-commit hook scans; for example a stale copy of
           the pre-split VPS .env) holds a fallback secret or cannot be read: the refusal names each such copy and its
           fallback names; move them out of the repo after a backup outside it (RUNBOOK 'Before V4'). Reads the root
           .env and deploy/windows-local/.env, wraps the values it finds with Windows DPAPI for the current user
           (services/utils/desktop_protocol.dpapi_protect, the helper of the node keys, CRYPTPROTECT_UI_FORBIDDEN)
           into run/fallback.env.dpapi (a temp file restricted to the current user, then an atomic rename; unwrapped
           once to check it), and only then removes those lines from the env files (atomic; every other line byte
           for byte). An existing store is merged; a value still in an env file wins. 'agent_ctl brain vps' runs it
           once the Desktop Node has started.
--restore  Unwraps the store and puts each value back into the file it came from (filled in or appended; a file that
           has its own value meanwhile keeps it, and that is reported), reads the files back, and only then deletes
           the store. 'agent_ctl brain pc' runs it itself just before the PC worker starts (the only automatic
           loader; a PC worker start is refused while the store exists); by hand it is the emergency way back.
--status   Names only: the fallback names each env file holds, those the store holds (from its plaintext header;
           nothing is unwrapped), and those each PC copy of a VPS env file holds (PC_COPY_NAMES, as --wrap checks).
--set      One value, piped in or typed into a console that hides it (not Git Bash's mintty): into the store while the
           name is wrapped there, else into the root .env (key rotations in vps mode, C-18).
--discard  The way out when this Windows user can never unwrap the store (the repo copied to another PC or Windows
           user, a rebuilt Windows profile): while it exists every PC worker start is refused and 'agent_ctl brain pc'
           cannot restore it, so the PC fallback stays down. With --confirm it moves run/fallback.env.dpapi aside to
           run/fallback.env.dpapi.unreadable-<UTC> (a rename: never deleted, never over an earlier file) and names
           what it held (names only, from its plaintext header); --dry-run only reports. Refused while this Windows
           user can unwrap the store (--restore puts its values back). Then the values are entered again from their
           VPS copies (.env.agent: the AI, image, search, weather, SIM and email keys; the VPS .env: the LiveKit pairs
           and the legacy token) with --set (no store now, so into the root .env) or given new values on both sides
           with rotate_env_secret.py (the LiveKit pair: rotate_livekit_keys.py), and in vps mode wrapped again with
           --wrap (deploy/hostinger/RUNBOOK.md, 'The PC fallback secrets'). The refusals that meet such a store
           (load_store: StoreUnusable) end with that way out (UNUSABLE_HINT).

The store is JSON: {kind, v, created, names: {root: [...], overlay: [...]}, blob}; blob is base64 of the DPAPI-wrapped
canonical JSON of the values. The names are plaintext so --status and push_agent_env.py need no unwrap.
Exit codes: 0 ok, 1 refused or failed (the env files and the store are as they were; in the rare case that the store
cannot be put back after a failed env write, the output says so), 2 usage error.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rotate_env_secret = _load_sibling("rotate_env_secret")
gen_secrets = rotate_env_secret.gen_secrets
env_set = gen_secrets.env_set
EnvError = env_set.EnvError

STORE_NAME = "fallback.env.dpapi"
STORE_KIND = "jarvis_pc_fallback_env"  # the wrapped document's kind: never taken for a node key file, or the reverse
DPAPI_DESCRIPTION = "JARVIS PC fallback env"
ENV_PATHS = (ROOT / ".env", ROOT / "deploy" / "windows-local" / ".env")  # agent_ctl.ENV_FILES, the same order
LABELS = ("root", "overlay")
BRAIN_ENV = "JARVIS_BRAIN"
FALLBACK_NAMES = (
    # LiveKit and the legacy token
    "LIVEKIT_API_SECRET", "LIVEKIT_PC_API_SECRET", "VORTEX_SECURITY_TOKEN", "VITE_VORTEX_SECURITY_TOKEN",
    # AI and image providers
    "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN", "POLLINATIONS_TOKEN",
    # search, weather and SIM lookup
    "GOOGLE_SEARCH_API_KEY", "TAVILY_API_KEY", "WEATHER_API_KEY", "OPENWEATHER_API_KEY", "SIM_LOOKUP_API_KEY",
    # email
    "EMAIL_APP_PASSWORD",
)
# Copies of VPS env files a PC may hold inside the repo (git-ignored, plain text, read by no JARVIS process): the
# pre-commit hook's ENV_FILES beyond ENV_PATHS (scripts/coord/precommit.py; a test keeps the two lists equal), relative
# to the folder of the root .env. --wrap refuses while one holds a fallback secret; --status names them.
PC_COPY_NAMES = ("deploy/hostinger/.env", "deploy/hostinger/bridge.env", "deploy/hostinger/.env.agent")
# The way out that every refusal over a store this Windows user cannot use names (load_store's StoreUnusable).
UNUSABLE_HINT = ("if this Windows user can never unwrap it (the repo copied to another PC or Windows user, a rebuilt "
                 "Windows profile), move it aside with 'scripts/ops/fallback_env.py --discard --confirm' (check with "
                 "--discard --dry-run first) and enter the values again from their VPS copies with 'fallback_env.py "
                 "--set NAME --value-from-stdin' or rotate_env_secret.py (deploy/hostinger/RUNBOOK.md, 'The PC "
                 "fallback secrets')")
AFTER_DISCARD = ("next: enter the values again from their VPS copies (.env.agent holds the AI, image, search, weather, "
                 "SIM and email keys; the VPS .env the LiveKit pairs and the legacy token), one name at a time and "
                 "piped in, never on a command line: 'fallback_env.py --set NAME --value-from-stdin' (no store now, so "
                 "they go into the root .env), or give a name a new value on both sides with rotate_env_secret.py "
                 "(the LiveKit pair: rotate_livekit_keys.py); in vps mode wrap them again right after with "
                 "'fallback_env.py --wrap' (deploy/hostinger/RUNBOOK.md, 'The PC fallback secrets')")

Protect = Callable[[bytes], bytes]


class Report:
    """What a command did: its exit code, its output lines (names only) and whether a file changed. A plain class, not
    a dataclass: agent_ctl and the sibling tools load this file by its path, without a sys.modules entry."""

    def __init__(self, code: int = 0, lines: list[str] | None = None, changed: bool = False) -> None:
        self.code = code
        self.lines = list(lines or [])
        self.changed = changed

    def __repr__(self) -> str:
        return f"Report(code={self.code}, lines={self.lines!r}, changed={self.changed})"


class StoreUnusable(EnvError):
    """load_store: the store is there, but this Windows user cannot use it (it cannot be unwrapped here, or it is
    damaged or no store at all). Its message ends with the way out (UNUSABLE_HINT); `reason` is the message without
    it. Never a value. An EnvError, so every caller that catches EnvError catches it."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{reason}; {UNUSABLE_HINT}")
        self.reason = reason


# --------------------------------------------------------------------------- DPAPI and paths


def dpapi_protect(data: bytes) -> bytes:
    """Windows DPAPI for the current user: the node keys' helper (services/utils/desktop_protocol), reused as it is.
    Raises OSError (also off Windows)."""
    from services.utils import desktop_protocol as dp  # pylint: disable=import-outside-toplevel

    return dp.dpapi_protect(data, DPAPI_DESCRIPTION)


def dpapi_unprotect(blob: bytes) -> bytes:
    """Unwraps dpapi_protect() output; only the same Windows user can. Raises OSError."""
    from services.utils import desktop_protocol as dp  # pylint: disable=import-outside-toplevel

    return dp.dpapi_unprotect(blob)


def restrict_to_user(path: Path) -> None:
    """Only the current user may open the file (a protected one-entry DACL on Windows, else mode 600)."""
    from src.core.desktop import health  # pylint: disable=import-outside-toplevel

    health.restrict_to_current_user(path)


def _io(protect: Protect | None = None, unprotect: Protect | None = None,
        restrict: Callable[[Path], None] | None = None) -> tuple[Protect, Protect, Callable[[Path], None]]:
    """The DPAPI wrap, unwrap and ACL calls of one command: the ones given, else this module's, looked up when the
    command runs (the module's attributes are its globals, so a test can swap them on the loaded module)."""
    own = globals()
    return (protect or own["dpapi_protect"], unprotect or own["dpapi_unprotect"],
            restrict or own["restrict_to_user"])


def run_dir() -> Path:
    """JARVIS_RUN_DIR, else <repo>/run (src/core/agent_lock.run_dir)."""
    return Path(os.environ.get("JARVIS_RUN_DIR") or ROOT / "run")


def store_path(folder: Path | None = None) -> Path:
    return Path(folder if folder is not None else run_dir()) / STORE_NAME


def _labelled(paths: Iterable[Path]) -> list[tuple[str, Path]]:
    return [(LABELS[i] if i < len(LABELS) else f"file{i + 1}", Path(p)) for i, p in enumerate(paths)]


def _shown(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def laptop_brain(paths: Iterable[Path] = ENV_PATHS) -> str:
    """'vps' only for an explicit vps (the process environment first, then the env files in order), else 'pc', as
    agent_ctl.current_brain reads it."""
    value = (os.environ.get(BRAIN_ENV) or "").strip()
    if not value:
        for path in paths:
            if Path(path).is_file():
                try:
                    value = env_set.EnvFile(Path(path)).value(BRAIN_ENV).strip()
                except EnvError:
                    value = ""
                if value:
                    break
    return "vps" if value.lower() == "vps" else "pc"


def worker_running() -> bool:
    from src.core import agent_lock  # pylint: disable=import-outside-toplevel

    return agent_lock.is_locked()


# --------------------------------------------------------------------------- the store


def read_header(path: Path) -> dict[str, Any] | None:
    """The store's plaintext part ({kind, v, created, names}), or None when there is no usable store."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("kind") != STORE_KIND or doc.get("v") != 1:
        return None
    names = doc.get("names")
    if not isinstance(names, dict) or not all(isinstance(v, list) for v in names.values()):
        return None
    return doc


def wrapped_names(path: Path) -> set[str]:
    """Every name the store holds (from its header; nothing is unwrapped). Empty without a store."""
    header = read_header(path)
    if not header:
        return set()
    return {str(name) for names in header["names"].values() for name in names}


def _document(files: Mapping[str, Mapping[str, str]]) -> bytes:
    return json.dumps({"kind": STORE_KIND, "v": 1, "files": files}, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def load_store(path: Path, unprotect: Protect) -> dict[str, dict[str, str]]:
    """{label: {name: value}} from the store. Raises EnvError (no value in the message) when it is unusable: when the
    file is there, a StoreUnusable whose message ends with the way out (UNUSABLE_HINT: --discard)."""
    header = read_header(path)
    if not header:
        if not Path(path).exists():
            raise EnvError(f"{_shown(path)} is missing")
        raise StoreUnusable(f"{_shown(path)} is not a fallback store this Windows user can read")
    try:
        plain = unprotect(base64.b64decode(str(header.get("blob") or ""), validate=True))
        doc = json.loads(plain.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreUnusable(f"{_shown(path)} could not be unwrapped by this Windows user "
                            f"({type(exc).__name__})") from None
    files = doc.get("files") if isinstance(doc, dict) and doc.get("kind") == STORE_KIND else None
    if not isinstance(files, dict) or not all(isinstance(v, dict) for v in files.values()):
        raise StoreUnusable(f"{_shown(path)} holds no fallback values")
    found = {label: sorted(values) for label, values in files.items()}
    if found != {label: sorted(names) for label, names in header["names"].items()}:
        raise StoreUnusable(f"{_shown(path)}: its header and its wrapped values name different keys")
    return {str(label): {str(k): str(v) for k, v in values.items()} for label, values in files.items()}


def _replace_private(path: Path, data: bytes, restrict: Callable[[Path], None]) -> None:
    """Writes data to path through a new temp file next to it, restricted to the current user before the content is
    written, and an atomic rename. Raises whatever failed (the old file is then still in place); no temp file is
    left."""
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        os.close(fd)
        restrict(temp)  # before the content is written
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_store(path: Path, files: Mapping[str, Mapping[str, str]], *, protect: Protect, unprotect: Protect,
                restrict: Callable[[Path], None]) -> None:
    """Writes the store through a new temp file restricted to the current user and an atomic rename, then unwraps
    it once to check it; with no value left it removes the store instead. Raises EnvError (the old store, if any, is
    then still in place, except after a failed read-back check: put_values then writes the old bytes back)."""
    files = {label: dict(values) for label, values in files.items() if values}
    path = Path(path)
    if not files:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise EnvError(f"could not remove the empty {_shown(path)} ({type(exc).__name__})") from None
        return
    try:
        blob = protect(_document(files))  # fails before anything on disk changes
    except OSError as exc:
        raise EnvError(f"DPAPI could not wrap the values ({type(exc).__name__}); nothing was changed") from None
    header = {"kind": STORE_KIND, "v": 1, "created": int(time.time()),
              "names": {label: sorted(values) for label, values in files.items()},
              "blob": base64.b64encode(blob).decode("ascii")}
    try:
        _replace_private(path, json.dumps(header, indent=1, sort_keys=True).encode("utf-8"), restrict)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # the ACL call raises pywintypes.error
        raise EnvError(f"could not write {_shown(path)} ({type(exc).__name__}); nothing was changed") from None
    if load_store(path, unprotect) != files:
        raise EnvError(f"{_shown(path)} did not read back as written")


def _restore_store(path: Path, old: bytes, restrict: Callable[[Path], None]) -> str:
    """Puts the store back as it was before this command, its earlier bytes `old` (temp file restricted to the
    current user, atomic rename). 'kept' when it holds them still, 'restored' when they went back, else the error
    type (never a value)."""
    try:
        if path.is_file() and path.read_bytes() == old:
            return "kept"
        _replace_private(path, old, restrict)
        return "restored"
    except Exception as exc:  # pylint: disable=broad-exception-caught  # the ACL call raises pywintypes.error
        return type(exc).__name__


# --------------------------------------------------------------------------- env files


def _env_files(paths: Iterable[Path]) -> list[tuple[str, Any]]:
    """[(label, env_set.EnvFile)] for the env files that exist (the root .env must)."""
    out = []
    for i, (label, path) in enumerate(_labelled(paths)):
        if path.is_file():
            out.append((label, env_set.EnvFile(path)))
        elif i == 0:
            raise EnvError(f"{_shown(path)} not found")
    return out


def _drop_names(env_file: Any, names: set[str]) -> None:
    """Removes every assignment of the names from an env_set.EnvFile. python-dotenv counts the blank lines before an
    assignment as part of it; they stay, so only the assignment's own line (or lines) goes."""
    for i, key in enumerate(env_file.keys):
        if key in names:
            chunk = env_file.chunks[i]
            head = chunk[:len(chunk) - len(chunk.lstrip())]
            cut = max(head.rfind("\n"), head.rfind("\r"))
            env_file.chunks[i] = head[:cut + 1] if cut >= 0 else ""
            env_file.keys[i] = None
    for name in names:
        env_file.values.pop(name, None)


def _found(env_files: list[tuple[str, Any]], names: Iterable[str]) -> dict[str, dict[str, str]]:
    wanted = tuple(names)
    return {label: {n: f.value(n) for n in wanted if f.value(n)} for label, f in env_files}


def pc_copies(paths: Iterable[Path]) -> list[Path]:
    """The PC_COPY_NAMES files in the folder of the root .env (paths[0]), except any of the given env files."""
    paths = [Path(p) for p in paths]
    if not paths:
        return []
    base = paths[0].resolve().parent
    own = {os.path.normcase(str(p.resolve())) for p in paths}
    copies = [base / name for name in PC_COPY_NAMES]
    return [p for p in copies if os.path.normcase(str(p.resolve())) not in own]


def copy_findings(copies: Iterable[Path]) -> list[tuple[str, list[str] | None]]:
    """(shown path, the FALLBACK_NAMES it assigns a value) for each copy that exists, names only (nothing is printed
    or returned of a value); None instead of the names for a copy that cannot be read (names unknown)."""
    out: list[tuple[str, list[str] | None]] = []
    for path in (Path(p) for p in copies):
        if not path.is_file():
            continue
        try:
            f = env_set.EnvFile(path)
        except (EnvError, OSError):
            out.append((_shown(path), None))
            continue
        out.append((_shown(path), [n for n in FALLBACK_NAMES if f.value(n)]))
    return out


def _copies_refusal(copies: Iterable[Path]) -> str:
    """Why --wrap must wait (names only), else '': a PC copy of a VPS env file still holds a fallback secret in plain
    text (or cannot be read, so it may), which the Desktop Node and its children would find on disk in vps mode."""
    held = [(shown, names) for shown, names in copy_findings(copies) if names is None or names]
    if not held:
        return ""
    parts = [f"{shown} ({', '.join(names) if names is not None else 'unreadable, names unknown'})"
             for shown, names in held]
    return (f"refused: {'; '.join(parts)}: other PC copies of VPS env files hold fallback secrets in plain text. Move "
            "them out of the repo after a backup outside it (deploy/hostinger/RUNBOOK.md, 'Before V4: PC copies of the "
            "VPS env files'), then wrap again")


# --------------------------------------------------------------------------- commands


def wrap(paths: Iterable[Path] = ENV_PATHS, folder: Path | None = None, *, protect: Protect | None = None,
         unprotect: Protect | None = None, restrict: Callable[[Path], None] | None = None,
         brain: str | None = None, worker: bool | None = None, dry_run: bool = False,
         copies: Iterable[Path] | None = None) -> Report:
    """vps mode: the fallback values into the store, then out of the env files (see the module docstring). `copies`:
    the PC copies of VPS env files to check first (default pc_copies(paths))."""
    protect, unprotect, restrict = _io(protect, unprotect, restrict)
    paths = [Path(p) for p in paths]
    store = store_path(folder)
    report = Report()
    brain = laptop_brain(paths) if brain is None else brain
    if brain != "vps":
        return Report(1, [f"refused: this laptop's {BRAIN_ENV} is {brain}, not vps; the PC brain reads these values "
                          "from its env files"])
    if worker_running() if worker is None else worker:
        return Report(1, ["refused: a PC worker holds run/agent.lock; stop it first (agent_ctl stop)"])
    refusal = _copies_refusal(pc_copies(paths) if copies is None else copies)
    if refusal:   # also on a dry run: a run would refuse the same way
        return Report(1, [refusal])
    try:
        env_files = _env_files(paths)
        found = _found(env_files, FALLBACK_NAMES)
        merged = load_store(store, unprotect) if store.exists() else {}
        for label, values in found.items():
            merged.setdefault(label, {}).update(values)  # a value still in an env file is the newer one
        count = sum(len(v) for v in found.values())
        if not count:
            held = wrapped_names(store)
            report.lines.append(f"nothing to wrap: the env files hold no fallback secret ({len(held)} names in "
                                f"{_shown(store)})" if held else "nothing to wrap: no fallback secret in the env "
                                "files and no store")
            return report
        for label, f in env_files:
            if found.get(label):
                report.lines.append(f"{f.label}: {len(found[label])} to wrap: {', '.join(sorted(found[label]))}")
        if dry_run:
            report.lines.append(f"dry run: nothing written ({_shown(store)} and the env files unchanged)")
            return report
        write_store(store, merged, protect=protect, unprotect=unprotect, restrict=restrict)
        report.changed = True
        for label, f in env_files:
            if found.get(label):
                _drop_names(f, set(found[label]))
        try:
            written = gen_secrets.write_private([f for _, f in env_files])
        except EnvError as exc:
            report.code = 1
            report.lines.append(f"{_shown(store)} was written, but the env files still hold the values: {exc} "
                                "(nothing is lost; run --wrap again)")
            return report
        report.lines.append(f"wrapped {count} values into {_shown(store)}; removed from {', '.join(written)}")
        return report
    except EnvError as exc:
        return Report(1, [f"refused: {exc}"])


def restore(paths: Iterable[Path] = ENV_PATHS, folder: Path | None = None, *, unprotect: Protect | None = None,
            dry_run: bool = False) -> Report:
    """The store's values back into the env files, then the store deleted (see the module docstring)."""
    unprotect = _io(unprotect=unprotect)[1]
    paths = [Path(p) for p in paths]
    store = store_path(folder)
    report = Report()
    if not store.exists():
        report.lines.append(f"nothing to restore: no {_shown(store)}")
        return report
    try:
        stored = load_store(store, unprotect)
        env_files = dict(_env_files(paths))
        root = env_files[LABELS[0]]
        kept: list[str] = []
        put: dict[str, set[str]] = {}
        # The root .env first: a value whose own file is gone goes to the root .env unless it has one already.
        for label in sorted(stored, key=lambda item: (LABELS.index(item) if item in LABELS else len(LABELS), item)):
            target = env_files.get(label, root)
            done = put.setdefault(target.label, set())
            for name, value in sorted(stored[label].items()):
                current = target.value(name)
                if current or name in done:
                    if current and current != value:
                        kept.append(f"{name} ({target.label})")
                    continue
                target.set(name, env_set.render_value(name, value))
                done.add(name)
        pending = [f.label for f in env_files.values() if f.changed()]
        report.lines.append(f"restore {sum(len(v) for v in stored.values())} values into "
                            f"{', '.join(pending) if pending else 'no file (all present)'}")
        if kept:
            report.lines.append(f"kept the file's own newer value: {', '.join(sorted(kept))}")
        if dry_run:
            report.lines.append(f"dry run: nothing written ({_shown(store)} kept)")
            return report
        gen_secrets.write_private(list(env_files.values()))
        report.changed = True
        check = _env_files(paths)
        have = {name for _, f in check for name in f.values if f.value(name)}
        lost = sorted(name for values in stored.values() for name in values if name not in have)
        if lost:
            report.code = 1
            report.lines.append(f"{', '.join(lost)} did not reach the env files; {_shown(store)} is kept")
            return report
        store.unlink()
        report.lines.append(f"restored; {_shown(store)} deleted")
        return report
    except EnvError as exc:
        return Report(1, [f"refused: {exc}"])
    except OSError as exc:
        return Report(1, [f"failed: {type(exc).__name__} (the store is kept)"])


def put_values(values: Mapping[str, str], paths: Iterable[Path] = ENV_PATHS, folder: Path | None = None, *,
               protect: Protect | None = None, unprotect: Protect | None = None,
               restrict: Callable[[Path], None] | None = None, drop: Iterable[str] = (),
               exclusive: bool = False, check: bool = False) -> Report:
    """Sets values where they belong now: a FALLBACK_NAMES value into the store while one exists (and out of the env
    files, so the store holds the only copy), every other value into the root .env (replaced in place or appended).
    `drop` names are removed from both. With exclusive (move_values), a value set in the root .env also leaves every
    other env file, so this PC holds each name in one place only. Used by --set, livekit_pc_pair.py and
    rotate_livekit_keys.py.

    Order: every env file edit is made in memory and staged (temp files next to the files, gen_secrets.stage_private)
    before the store is written; only then are the staged files renamed over the env files. A failure after the store
    was written puts the store's earlier bytes back, so the store and the env files never disagree; the report then
    says so (its lines are kept), never that nothing was changed.

    With check, it stops before the store write: the values, the env files and the store are read (the store unwrapped
    by this Windows user), the env file edits are made in memory and staged, the temp files are removed, and the report
    says what a run would set and remove (names only). Nothing is written, so a caller that changes something else
    first (rotate_livekit_keys.py: the VPS) learns beforehand whether this PC can take the values."""
    protect, unprotect, restrict = _io(protect, unprotect, restrict)
    paths = [Path(p) for p in paths]
    store = store_path(folder)
    report = Report()
    for name, value in values.items():
        if not value or any(ch in value for ch in "\r\n\x00"):
            return Report(1, [f"refused: {name}: an empty or multi-line value"])
    staged: Any = []
    try:
        env_files = _env_files(paths)
        root = env_files[0][1]
        stored = load_store(store, unprotect) if store.exists() else None
        to_store = {n: v for n, v in values.items() if stored is not None and n in FALLBACK_NAMES}
        to_root = {n: v for n, v in values.items() if n not in to_store}
        dropped = set(drop) | set(to_store)
        # 1. the env files, in memory: a refusal here (a value that no .env line holds unchanged, an assignment over
        # several lines) changes nothing
        removed: dict[str, list[str]] = {}
        for _, f in env_files:
            here = dropped | (set(to_root) if exclusive and f is not root else set())
            removed[f.label] = sorted({key for key in f.keys if key} & here)
            _drop_names(f, here)
        for name, value in to_root.items():
            rotate_env_secret.replace_in(root, name, env_set.render_value(name, value))
        # 2. staged: a read-only or unwritable env file stops the run here, while nothing has changed yet
        staged = gen_secrets.stage_private([f for _, f in env_files])
        if check:   # the finally below removes the temp files; the store and the env files stay as they are
            if to_store:
                report.lines.append(f"{_shown(store)}: would set {', '.join(sorted(to_store))}")
            for _, f in env_files:
                parts = ([f"would set {', '.join(sorted(to_root))}"] if f is root and to_root else []) \
                    + ([f"would remove {', '.join(removed[f.label])}"] if removed.get(f.label) else [])
                if parts:
                    report.lines.append(f"{f.label}: {'; '.join(parts)}")
            return report
        # 3. the store
        old: bytes | None = None
        if stored is not None and (to_store or set(drop) & wrapped_names(store)):
            for label in list(stored):
                for name in set(drop) | set(to_store):
                    stored[label].pop(name, None)
            stored.setdefault(LABELS[0], {}).update(to_store)
            try:
                old = store.read_bytes()   # put back byte for byte if a later step fails
            except OSError as exc:
                raise EnvError(f"could not read {_shown(store)} ({type(exc).__name__}); nothing was changed") from None
            try:
                write_store(store, stored, protect=protect, unprotect=unprotect, restrict=restrict)
            except EnvError as exc:
                return _store_put_back(report, store, old, restrict, f"refused: {exc}")
            report.changed = True
            report.lines.append(f"{_shown(store)}: set {', '.join(sorted(to_store)) or 'nothing'}"
                                + (f"; removed {', '.join(sorted(set(drop)))}" if drop else ""))
        # 4. the staged env files renamed over the env files (commit_private puts back any it already replaced)
        try:
            written = gen_secrets.commit_private(staged)
        except EnvError as exc:
            if old is None:
                raise
            reason = str(exc).replace("; no file was changed", "; the env files are as they were")
            return _store_put_back(report, store, old, restrict, f"failed: the env files were not written: {reason}")
        for label in written:
            parts = ([f"set {', '.join(sorted(to_root))}"] if label == root.label and to_root else []) \
                + ([f"removed {', '.join(removed[label])}"] if removed.get(label) else [])
            report.lines.append(f"{label}: {'; '.join(parts) or 'rewritten'}")
        report.changed = report.changed or bool(written)
        return report
    except EnvError as exc:
        return Report(1, [f"refused: {exc}"])
    finally:
        gen_secrets.discard_private(staged)


def _store_put_back(report: Report, store: Path, old: bytes, restrict: Callable[[Path], None], why: str) -> Report:
    """put_values after a failure that came once the store may have changed: the store's earlier bytes go back, and
    the report keeps its lines and says what happened (names only)."""
    report.code = 1
    report.lines.append(why)
    outcome = _restore_store(store, old, restrict)
    if outcome == "kept":
        report.changed = False
        report.lines.append(f"{_shown(store)} still holds its earlier values; fix the cause and run the command again")
    elif outcome == "restored":
        report.changed = False
        report.lines.append(f"{_shown(store)} was put back byte for byte, so it and the env files agree again; fix "
                            "the cause and run the command again")
    else:
        report.changed = True
        report.lines.append(f"{_shown(store)} could NOT be put back ({outcome}): it holds the new values while the "
                            "env files keep the old ones; fix the cause, then finish the change by hand "
                            "('fallback_env.py --status' lists where each name is, names only)")
    return report


def find_values(names: Iterable[str], paths: Iterable[Path] = ENV_PATHS, folder: Path | None = None, *,
                unprotect: Protect | None = None) -> dict[str, str]:
    """name -> value of each name this PC holds, wherever the value is now: the env files in order first, then the
    store (unwrapped in this process only, and only when a name is not in the env files). A name found nowhere is left
    out; no value is ever printed. Raises EnvError (no value in the message)."""
    unprotect = _io(unprotect=unprotect)[1]
    paths = [Path(p) for p in paths]
    store = store_path(folder)
    env_files = _env_files(paths)
    found: dict[str, str] = {}
    for name in dict.fromkeys(names):
        value = next((f.value(name) for _, f in env_files if f.value(name)), "")
        if value:
            found[name] = value
    missing = [name for name in dict.fromkeys(names) if name not in found]
    if missing and store.exists():
        stored = load_store(store, unprotect)
        for name in missing:
            value = next((v[name] for v in stored.values() if v.get(name)), "")
            if value:
                found[name] = value
    return found


def move_values(renames: Mapping[str, str], paths: Iterable[Path] = ENV_PATHS, folder: Path | None = None, *,
                protect: Protect | None = None, unprotect: Protect | None = None,
                restrict: Callable[[Path], None] | None = None) -> Report:
    """Gives each target name the value of its source name, wherever that value is now (the root .env, the overlay
    or the store), and removes the source names; values stay inside this process (livekit_pc_pair.py --activate).
    Each target lives in one place afterwards: the store (a wrapped name) or the root .env, and no other env file
    keeps an older copy of it (deploy/windows-local/.env may hold the main LiveKit pair)."""
    protect, unprotect, restrict = _io(protect, unprotect, restrict)
    paths = [Path(p) for p in paths]
    store = store_path(folder)
    try:
        found = find_values(list(renames), paths, folder, unprotect=unprotect)
    except EnvError as exc:
        return Report(1, [f"refused: {exc}"])
    for source in renames:
        if source not in found:
            return Report(1, [f"refused: {source} is neither in the env files nor in {_shown(store)}"])
    values = {target: found[source] for source, target in renames.items()}
    return put_values(values, paths, folder, protect=protect, unprotect=unprotect, restrict=restrict,
                      drop=list(renames), exclusive=True)


def status(paths: Iterable[Path] = ENV_PATHS, folder: Path | None = None, *,
           copies: Iterable[Path] | None = None) -> Report:
    """Names only; nothing is unwrapped. `copies`: the PC copies of VPS env files to list (default pc_copies(paths))."""
    report = Report()
    store = store_path(folder)
    paths = [Path(p) for p in paths]
    for label, path in _labelled(paths):
        if not path.is_file():
            report.lines.append(f"{_shown(path)}: not present")
            continue
        try:
            f = env_set.EnvFile(path)
        except EnvError as exc:
            report.lines.append(f"{_shown(path)}: unreadable ({exc})")
            continue
        names = [n for n in FALLBACK_NAMES if f.value(n)]
        report.lines.append(f"{f.label} ({label}): {len(names)} fallback names: {', '.join(names) or 'none'}")
    for shown, names in copy_findings(pc_copies(paths) if copies is None else copies):
        if names is None:
            report.lines.append(f"{shown} (PC copy of a VPS env file): unreadable, names unknown; move it out of the "
                                "repo before --wrap")
        elif names:
            report.lines.append(f"{shown} (PC copy of a VPS env file): {len(names)} fallback names in plain text: "
                                f"{', '.join(names)}; move it out of the repo before --wrap")
        else:
            report.lines.append(f"{shown} (PC copy of a VPS env file): no fallback name")
    header = read_header(store)
    if header:
        held = sorted(wrapped_names(store))
        created = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(header.get("created") or 0)))
        report.lines.append(f"{_shown(store)}: {len(held)} wrapped names (since {created}): {', '.join(held)}")
    elif store.exists():
        report.code = 1
        report.lines.append(f"{_shown(store)}: present but not a fallback store ('--discard --dry-run' checks it, "
                            "'--discard --confirm' moves it aside)")
    else:
        report.lines.append(f"{_shown(store)}: none")
    report.lines.append(f"this laptop's {BRAIN_ENV}: {laptop_brain(paths)}")
    return report


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _aside_path(store: Path) -> Path:
    """run/fallback.env.dpapi.unreadable-<UTC>, with -2, -3 ... while that name is taken: never an earlier file's."""
    base = f"{store.name}.unreadable-{_utc_stamp()}"
    aside, n = store.with_name(base), 2
    while aside.exists():
        aside, n = store.with_name(f"{base}-{n}"), n + 1
    return aside


def discard(folder: Path | None = None, *, unprotect: Protect | None = None, confirm: bool = False) -> Report:
    """A store this Windows user can never unwrap, moved aside so that 'agent_ctl brain pc' and the PC worker are no
    longer held up by it: renamed, never deleted (see the module docstring). Without confirm only a report of what it
    would do (the dry run). Refused while this Windows user can unwrap the store. Names only."""
    unprotect = _io(unprotect=unprotect)[1]
    store = store_path(folder)
    if not store.exists():
        return Report(0, [f"nothing to discard: no {_shown(store)}"])
    try:
        load_store(store, unprotect)
    except StoreUnusable as exc:
        reason = exc.reason
    except EnvError as exc:   # gone meanwhile
        return Report(1, [f"refused: {exc}"])
    else:
        return Report(1, [f"refused: this Windows user can unwrap {_shown(store)}, so its values are not lost: "
                          "'--restore' (or 'agent_ctl brain pc') puts them back into the env files; --discard is only "
                          "for a store that can never be unwrapped here"])
    names = sorted(wrapped_names(store))
    aside = _aside_path(store)
    lines = [f"{reason}: the PC fallback cannot start while it is there",
             f"it holds {len(names)} names: {', '.join(names)}" if names
             else "its names cannot be read (no usable plaintext header)"]
    if not confirm:
        lines.append(f"dry run: '--discard --confirm' moves it aside to {_shown(aside)} (never deleted); nothing "
                     "written")
        return Report(0, lines)
    try:
        os.rename(store, aside)   # never over an existing file (_aside_path), and never a delete
    except OSError as exc:
        lines.append(f"failed: {_shown(store)} could not be moved aside ({type(exc).__name__}); nothing was changed")
        return Report(1, lines)
    lines.append(f"moved {_shown(store)} aside to {_shown(aside)}: never deleted (remove it by hand once you are "
                 "sure; renamed back, it is the store again)")
    lines.append(AFTER_DISCARD)
    return Report(0, lines, changed=True)


# --------------------------------------------------------------------------- agent_ctl brain


def restore_for_brain_pc(paths: Iterable[Path], folder: Path) -> Report:
    """'agent_ctl brain pc' (and the restore of a failed 'brain vps'): before the PC worker starts. Nothing to do
    without a store; never raises."""
    if not store_path(folder).exists():
        return Report()
    try:
        return restore(paths, folder)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # the switch reports it, never a traceback
        return Report(1, [f"failed: unexpected {type(exc).__name__}"])


def wrap_for_brain_vps(paths: Iterable[Path], folder: Path, *, worker: bool) -> Report:
    """'agent_ctl brain vps' (and the restore of a failed 'brain pc'): after the Desktop Node started. Never raises;
    a failure only means the values stay in the env files."""
    try:
        return wrap(paths, folder, brain="vps", worker=worker)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return Report(1, [f"failed: unexpected {type(exc).__name__}"])


# --------------------------------------------------------------------------- command line


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="fallback_env.py",
                     description="The PC fallback secrets: DPAPI-wrapped in vps mode, restored for 'brain pc'. "
                                 "Names only.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="names only: where each fallback secret is")
    group.add_argument("--wrap", action="store_true", help="vps mode: move them into run/fallback.env.dpapi")
    group.add_argument("--restore", action="store_true", help="put them back into the env files")
    group.add_argument("--set", metavar="NAME", help="one value (with --value-from-stdin), where the name is now")
    group.add_argument("--discard", action="store_true",
                       help="move a store this Windows user can never unwrap aside (never deleted): with --confirm, "
                            "or --dry-run to check")
    parser.add_argument("--value-from-stdin", action="store_true", help="with --set: read the value from stdin")
    parser.add_argument("--confirm", action="store_true", help="with --discard: really move the store aside")
    parser.add_argument("--dry-run", action="store_true", help="with --wrap/--restore/--discard: report only")
    parser.add_argument("--env-file", action="append", type=Path, metavar="PATH",
                        help="env files instead of .env and deploy/windows-local/.env (repeatable; the first is "
                             "the root .env)")
    parser.add_argument("--run-dir", type=Path, help="folder of the store (default JARVIS_RUN_DIR or run/)")
    args = parser.parse_args(argv)
    if args.set is not None:
        if not env_set.KEY_RE.fullmatch(args.set):
            parser.error("--set: not a valid NAME")
        if not args.value_from_stdin:
            parser.error("--set needs --value-from-stdin (never type a secret on a command line)")
    elif args.value_from_stdin:
        parser.error("--value-from-stdin goes with --set")
    if args.confirm and not args.discard:
        parser.error("--confirm goes with --discard")
    if args.discard and args.confirm == args.dry_run:
        parser.error("--discard needs --confirm (it moves the store aside) or --dry-run (a check that writes "
                     "nothing), one of the two")
    if args.dry_run and not (args.wrap or args.restore or args.discard):
        parser.error("--dry-run goes with --wrap, --restore or --discard")
    return args


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    args = parse_args(argv)
    paths = args.env_file or list(ENV_PATHS)
    folder = args.run_dir
    if args.status:
        report = status(paths, folder)
    elif args.wrap:
        report = wrap(paths, folder, dry_run=args.dry_run)
    elif args.restore:
        report = restore(paths, folder, dry_run=args.dry_run)
    elif args.discard:
        report = discard(folder, confirm=args.confirm)
    else:
        try:
            value = rotate_env_secret.read_value(args.set, stdin or sys.stdin)
        except EnvError as exc:
            print(f"fallback_env: {exc}", file=sys.stderr)
            return 2
        report = put_values({args.set: value}, paths, folder)
    for line in report.lines:
        print(line, file=sys.stderr if report.code else sys.stdout)
    return report.code


if __name__ == "__main__":
    sys.exit(main())
