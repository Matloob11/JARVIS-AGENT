"""Pin the deployed commit into a read-only Desktop Node snapshot (plan task C-123 step 1; K-72).

The Desktop Node must run the *deployed* code, never the working tree that Cursor edits at the same time.
'agent_ctl node update' therefore exports the deployed commit into %LOCALAPPDATA%\\JARVIS\\node\\<sha> and
hands it to the logon task. This module does that export and the files beside it:

- build_snapshot(): 'git archive <sha>' of the committed tree into the snapshot directory (no .git, no
  uncommitted change), through a temporary tar that stdlib tarfile extracts into a staging folder, renamed
  to <sha> only when complete (an interrupted build never leaves a half tree under the snapshot's name);
- lock_read_only(): icacls so the user can read and execute the snapshot but never write it, so the node's
  own pinned code, keys and policy cannot be rewritten by a path tool (K-76 step 4);
- write_node_config(): %LOCALAPPDATA%\\JARVIS\\node\\node_config.json {data_root: the repo, run_dir:
  <repo>\\run, the pinned K-70 public keys, node_id, owner_room}; it is validated with the same loader the
  supervisor uses, so a bad write never reaches the node;
- write_secrets(): PINCHTAB_TOKEN and CONTROLLER_TOKEN from the root .env wrapped with DPAPI into
  secrets.dpapi (values never printed or logged), readable by the current user only;
- switch_current()/read_current(): run/node_current.json names the live snapshot, its sha and run mode.

Every external effect (git, icacls, DPAPI, the file ACL) is an injected callable, so the tests mock them and
no real snapshot, ACL or scheduled task is ever created. Nothing runs at import time.

The git archive and tar extraction are cross-platform; icacls and DPAPI are Windows-only and reached only
through their injected callables, so this module imports on Linux (the VPS) and Windows alike.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Stdlib-only modules; settings has no import-time side effects and desktop_protocol only needs cryptography.
from src.core.desktop import settings as node_settings  # noqa: E402  pylint: disable=wrong-import-position

NODE_CURRENT_FILE = "node_current.json"
# Only the committed tree is pinned (K-72): .git and the venv never go into a snapshot.
_SHORT_SHA = 12

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]
Protect = Callable[[bytes], bytes]


class SnapshotError(RuntimeError):
    """A snapshot step failed. The message never contains a key or token value."""


def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(cmd, check=False, **kwargs)


# --------------------------------------------------------------------------- the deployed commit


def resolve_sha(sha: str | None, *, root: Path = ROOT, runner: Runner = _run) -> str:
    """The full 40-char commit to pin: `sha` resolved (a branch, tag or short sha), else HEAD. The deploy
    pins HEAD; --sha pins an exact commit. SnapshotError when git cannot resolve it."""
    ref = (sha or "HEAD").strip()
    try:
        result = runner(["git", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                        capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotError(f"git rev-parse failed ({type(exc).__name__})") from None
    out = (result.stdout or "").strip()
    if result.returncode != 0 or len(out) != 40:
        raise SnapshotError(f"'{ref}' is not a commit this repository knows")
    return out


def snapshot_dir(sha: str, *, home: Path | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """%LOCALAPPDATA%\\JARVIS\\node\\<sha> (the full sha, so two deploys never collide)."""
    base = home if home is not None else node_settings.node_home(environ)
    return base / sha


# --------------------------------------------------------------------------- build the snapshot


_STAGING_INFIX = ".partial-"


def _staging_dirs(dest: Path) -> list[Path]:
    """Left-over staging folders of earlier, interrupted builds of `dest` (never locked read-only)."""
    try:
        return [p for p in dest.parent.glob(f".{dest.name}{_STAGING_INFIX}*") if p.is_dir()]
    except OSError:
        return []


def build_snapshot(sha: str, dest: Path, *, root: Path = ROOT, runner: Runner = _run,
                   extract: bool = True) -> Path:
    """'git archive <sha>' of the committed tree into `dest`. Returns dest.

    The archive is written to a temporary tar next to `dest` and extracted with stdlib tarfile (no shell pipe,
    the same on Windows and POSIX) into a staging folder '.<sha>.partial-<pid>' beside it, which is renamed to
    `dest` only once the extraction is complete. So a non-empty `dest` is always a complete tree: a build that
    is interrupted leaves only a staging folder, which the next build removes. SnapshotError when `dest` is
    already non-empty, or when git or the extraction fails (`dest` then does not exist or stays empty).
    """
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise SnapshotError(f"{dest} already exists and is not empty; remove it or pin a different sha")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in _staging_dirs(dest):
        shutil.rmtree(stale, ignore_errors=True)
    staging = dest.with_name(f".{dest.name}{_STAGING_INFIX}{os.getpid()}")
    staging.mkdir()
    fd, tar_name = tempfile.mkstemp(prefix=".node-archive-", suffix=".tar", dir=str(dest.parent))
    os.close(fd)
    tar_path = Path(tar_name)
    try:
        try:
            result = runner(["git", "-C", str(root), "archive", "--format=tar", "-o", str(tar_path), sha],
                            capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SnapshotError(f"git archive failed ({type(exc).__name__})") from None
        if result.returncode != 0:
            raise SnapshotError("git archive failed for the deployed commit")
        if extract:
            _extract_tar(tar_path, staging)
        if dest.exists():
            dest.rmdir()  # empty (checked above): the rename needs the name free on Windows
        try:
            os.replace(staging, dest)
        except OSError as exc:
            raise SnapshotError(f"the snapshot could not be moved into place ({type(exc).__name__})") from None
    finally:
        tar_path.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return dest


def _extract_tar(tar_path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    try:
        with tarfile.open(tar_path, "r:") as tar:
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                # git archive never emits '..' or absolute members, but a tar is untrusted input in general.
                if target != dest_resolved and dest_resolved not in target.parents:
                    raise SnapshotError("the archive contains a path outside the snapshot")
                if member.issym() or member.islnk():
                    raise SnapshotError("the archive contains a link, which a snapshot never holds")
            _safe_extractall(tar, dest)
    except tarfile.TarError as exc:
        raise SnapshotError(f"the snapshot archive could not be read ({type(exc).__name__})") from None


def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    # 'data' filter (Python 3.12+) blocks absolute paths, '..', links and device files during extraction.
    try:
        tar.extractall(dest, filter="data")  # type: ignore[call-arg]
    except TypeError:
        tar.extractall(dest)  # older Pythons: members were already validated above


# --------------------------------------------------------------------------- the read-only ACL


def lock_read_only(path: Path, *, runner: Runner = _run, user: str | None = None) -> list[str]:
    """Make `path` and everything under it readable and executable by the current user but NOT writable
    (K-76 step 4: the node's pinned code and keys stay out of reach of every path tool).

    Windows: icacls resets inheritance and grants the user RX only (no write, modify or full control).
    POSIX (dev and tests only): chmod a=rX recursively. Returns the icacls/chmod command for the audit and
    the tests; SnapshotError when the command fails. The command is run through the injected `runner`.
    """
    path = Path(path)
    if sys.platform != "win32":
        cmd = ["chmod", "-R", "a=rX", str(path)]
    else:
        account = user or os.environ.get("USERNAME") or ""
        if not account:
            raise SnapshotError("cannot determine the current user for the snapshot ACL")
        # /inheritance:r drops inherited (possibly writable) ACEs; /grant:r replaces, RX = read + execute only;
        # (OI)(CI) so files and sub-directories inherit the same read-only grant.
        cmd = ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)RX", "/T", "/C", "/Q"]
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotError(f"could not lock the snapshot read-only ({type(exc).__name__})") from None
    if result.returncode != 0:
        raise SnapshotError("could not lock the snapshot read-only (icacls/chmod refused)")
    return cmd


# --------------------------------------------------------------------------- node_config.json


def write_node_config(*, data_root: Path, run_dir: Path | None = None, agent_public_key: str | None = None,
                      pc_public_key: str | None = None, node_id: str | None = None,
                      owner_room: str | None = None, home: Path | None = None,
                      environ: Mapping[str, str] | None = None) -> Path:
    """Write %LOCALAPPDATA%\\JARVIS\\node\\node_config.json and validate it with the supervisor's loader.

    data_root is this repo (absolute); run_dir defaults to <data_root>\\run. The config is written then read
    back through node_settings.load_node_config, so a value the node would reject (a snapshot path as the
    data root, a malformed key) raises before the snapshot is switched live.
    """
    base = home if home is not None else node_settings.node_home(environ)
    base.mkdir(parents=True, exist_ok=True)
    data_root = Path(data_root).resolve()
    doc: dict[str, Any] = {"data_root": str(data_root),
                           "run_dir": str(Path(run_dir).resolve() if run_dir else data_root / "run")}
    for key, value in (("agent_public_key", agent_public_key), ("pc_public_key", pc_public_key),
                       ("node_id", node_id), ("owner_room", owner_room)):
        if value:
            doc[key] = value
    path = base / node_settings.NODE_CONFIG_FILE
    _write_json(path, doc)
    try:
        node_settings.load_node_config(path, environ)
    except node_settings.NodeConfigError as exc:
        raise SnapshotError(f"node_config.json is not usable: {exc}") from None
    return path


# --------------------------------------------------------------------------- secrets.dpapi


def write_secrets(values: Mapping[str, str], *, protect: Protect, home: Path | None = None,
                  environ: Mapping[str, str] | None = None,
                  restrict: Callable[[Path], None] | None = None) -> Path | None:
    """Wrap PINCHTAB_TOKEN and CONTROLLER_TOKEN from the root .env into secrets.dpapi (values NEVER printed
    or logged). `protect` is the DPAPI wrap (dp.dpapi_protect); `restrict` locks the file to the user.

    Returns the file path, or None when the root .env holds neither token (nothing to wrap). The document is
    the same shape node.py reads (node_settings.node_secrets_document / parse_node_secrets).
    """
    wanted = {name: values[name] for name in node_settings.NODE_SECRET_NAMES
              if (values.get(name) or "").strip()}
    base = home if home is not None else node_settings.node_home(environ)
    path = base / node_settings.SECRETS_FILE
    if not wanted:
        path.unlink(missing_ok=True)  # an update that clears the tokens removes a stale file
        return None
    document = node_settings.node_secrets_document(wanted)
    wrapped = protect(document)  # fails before anything on disk changes
    base.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wrapped)
    if restrict is not None:
        restrict(path)
    return path


def root_env_secrets(data_root: Path) -> dict[str, str]:
    """PINCHTAB_TOKEN and CONTROLLER_TOKEN as the root .env sets them; '' for a missing one. The returned
    mapping is used only to wrap the values and is never logged."""
    env_path = Path(data_root) / node_settings.ROOT_ENV_FILE
    found: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    # The allow-listed parser only keeps non-secret names, so read the two secret names directly here, with
    # the same assignment rules, and keep the values only in memory.
    for name in node_settings.NODE_SECRET_NAMES:
        value = _env_value(text, name)
        if value:
            found[name] = value
    return found


def _env_value(text: str, name: str) -> str:
    value = ""
    for line in text.lstrip("﻿").splitlines():
        match = node_settings._ASSIGNMENT_RE.match(line)  # pylint: disable=protected-access
        if match is not None and match.group(1) == name:
            value = node_settings._unquote(match.group(2))  # pylint: disable=protected-access
    return value


# --------------------------------------------------------------------------- run/node_current.json


def switch_current(run_dir: Path, *, sha: str, snapshot: Path, run_mode: str = "active") -> Path:
    """Point run/node_current.json at the snapshot the supervisor must launch next."""
    run_dir = Path(run_dir)
    doc = {"sha": sha, "short_sha": sha[:_SHORT_SHA], "snapshot": str(snapshot), "run_mode": run_mode,
           "updated_at": time.time(), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    path = run_dir / NODE_CURRENT_FILE
    _write_json(path, doc)
    return path


def read_current(run_dir: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads((Path(run_dir) / NODE_CURRENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _write_json(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(doc), indent=2, sort_keys=True, ensure_ascii=False)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        try:
            path.write_text(text, encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- the whole update


@dataclass(frozen=True)
class UpdatePlan:
    """What 'agent_ctl node update' pinned: the sha, its snapshot directory and the run mode."""

    sha: str
    snapshot: Path
    run_mode: str
    node_config: Path
    secrets_file: Path | None


def update_snapshot(*, sha: str | None, data_root: Path, run_dir: Path, run_mode: str = "active",
                    agent_public_key: str | None = None, pc_public_key: str | None = None,
                    node_id: str | None = None, owner_room: str | None = None,
                    protect: Protect, restrict: Callable[[Path], None] | None = None,
                    home: Path | None = None, root: Path = ROOT, runner: Runner = _run,
                    environ: Mapping[str, str] | None = None) -> UpdatePlan:
    """The full step 1: resolve the sha, archive it read-only, write node_config.json and secrets.dpapi, and
    switch run/node_current.json. The supervisor restart is the caller's (agent_ctl) job, so this function
    has no process side effects beyond the files it writes; every command goes through `runner`/`protect`.
    """
    base = home if home is not None else node_settings.node_home(environ)
    resolved = resolve_sha(sha, root=root, runner=runner)
    dest = snapshot_dir(resolved, home=base)
    # build_snapshot moves a tree into `dest` only once it is completely extracted, so a non-empty `dest` from an
    # earlier run is a complete snapshot of this sha and is reused.
    if not (dest.exists() and any(dest.iterdir())):
        build_snapshot(resolved, dest, root=root, runner=runner)
    # The read-only ACL is re-applied on EVERY run, not only on a fresh build. If a previous run archived the
    # tree but then raised before (or during) lock_read_only (e.g. icacls failed), a same-sha retry reuses the
    # non-empty `dest`; locking only inside the build branch would leave that snapshot writable while the node
    # is pointed at it, silently defeating the K-76 step-4 guarantee. 'icacls /inheritance:r /grant:r RX' and
    # 'chmod -R a=rX' are idempotent, so re-applying a correct lock is a safe no-op.
    lock_read_only(dest, runner=runner)
    config = write_node_config(data_root=data_root, run_dir=run_dir, agent_public_key=agent_public_key,
                               pc_public_key=pc_public_key, node_id=node_id, owner_room=owner_room, home=base,
                               environ=environ)
    secrets_file = write_secrets(root_env_secrets(data_root), protect=protect, home=base, environ=environ,
                                 restrict=restrict)
    switch_current(run_dir, sha=resolved, snapshot=dest, run_mode=run_mode)
    return UpdatePlan(resolved, dest, run_mode, config, secrets_file)
