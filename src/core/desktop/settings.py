"""Desktop Node settings source, data root and environment hygiene (plan task C-120; K-72, K-15, K-76).

The node runs from a read-only snapshot of the deployed commit (C-123), so nothing it writes or reads as
settings may resolve inside that snapshot:
- The supervisor exports JARVIS_DATA_ROOT (the repo on the PC) and JARVIS_RUN_DIR (<data root>/run) to
  the node child, from %LOCALAPPDATA%/JARVIS/node/node_config.json ('agent_ctl node update', C-123),
  never from the snapshot path.
- dotenv becomes a no-op before any services module is imported, and an allow-listed parser reads only
  non-secret keys from the PC root .env (<data root>/.env), at start and whenever the file changes
  (SettingsSource.poll, every POLL_S). The kill switch, the clipboard flags, the presence keys and
  WHATSAPP_TEST_NUMBERS apply at once; JARVIS_LINK_URL and the K-15 path and app keys at the next node
  start (the node asks its supervisor for a restart when one changes). Turning the kill switch off applies
  at the first poll; any other change once a second poll reads the same file (never half-written).
  Relative paths resolve against the data root.
- JARVIS_OUTPUTS_DIR, LOG_FILE and WHATSAPP_CONTACTS_FILE are always absolute paths under the data root,
  because their jarvis_config defaults derive from the project root, which is the snapshot.
- Secret names are scrubbed from the environment, and EnvGuard checks again before every spawn, so a
  child process never inherits a secret. PINCHTAB_TOKEN and CONTROLLER_TOKEN come only from the
  DPAPI-wrapped secrets.dpapi and are set on config in memory (node.py), never in os.environ.

Standard library only, and nothing runs at import: the node imports this module first.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NODE_HOME_ENV = "JARVIS_NODE_HOME"  # tests and dev only; default %LOCALAPPDATA%/JARVIS/node
DATA_ROOT_ENV = "JARVIS_DATA_ROOT"
RUN_DIR_ENV = "JARVIS_RUN_DIR"
NODE_CONFIG_FILE = "node_config.json"
KEYS_FILE = "keys.dpapi"
SECRETS_FILE = "secrets.dpapi"
ROOT_ENV_FILE = ".env"
POLL_S = 2.0

DEFAULT_LINK_URL = "https://jarvis.srv1802669.hstgr.cloud"
LINK_SOCKETIO_PATH = "vortex/socket.io"
DEFAULT_OWNER_ROOM = "jarvis-hybrid"
# The node never posts to a bridge /notify: tools that try (tool_sync, logs) fail fast on a closed port.
DEAD_BRIDGE_URL = "http://127.0.0.1:9"
NODE_LOG_FILE = Path("logs") / "desktop_node.log"  # the node's own rotating log (one handler: node.py)
# LOG_FILE of the services modules the node loads: their setup_logger() file handlers (ERROR) write here, so no
# two rotating handlers ever share the node's own log file.
TOOLS_LOG_FILE = Path("logs") / "desktop_node_tools.log"

# Live keys: applied at once (os.environ and, for these three, the K-15 config attribute).
LIVE_BOOL_KEYS: Mapping[str, tuple[str, bool]] = {
    "JARVIS_DESKTOP_CONTROL": ("desktop_control_enabled", True),  # the K-15/K-76 kill switch
    "JARVIS_CLIPBOARD_HELPER": ("clipboard_helper_enabled", True),
    "JARVIS_CLIPBOARD_SPEAK": ("clipboard_speak", False),
}
PRESENCE_KEYS: Mapping[str, int] = {  # K-72 node policy keys and their defaults (C-121 reads them)
    "JARVIS_PRESENCE_WINDOW_S": 600,
    "JARVIS_CAPTURE_PRESENCE_S": 120,
    "JARVIS_ORIGIN_PRESENCE_GATE": 0,
}
LIVE_KEYS = (*LIVE_BOOL_KEYS, *PRESENCE_KEYS, "WHATSAPP_TEST_NUMBERS")
# Restart keys: the node reads them at start; a change makes it ask its supervisor for a restart.
LINK_URL_KEY = "JARVIS_LINK_URL"
PATH_KEYS = ("JARVIS_OUTPUTS_DIR", "JARVIS_SHARED_DIR", "JARVIS_FILE_SERVER_ROOT", "JARVIS_CHROME_PATH",
             "JARVIS_EDGE_PATH", "PINCHTAB_BIN", "FFMPEG_DIR", "WHATSAPP_CONTACTS_FILE", "JARVIS_CV_PATH")
PATH_LIST_KEYS = ("JARVIS_MEDIA_DIRS", "JARVIS_OPEN_ROOTS")
APP_KEYS = ("JARVIS_FILE_SERVER_PORT", "JARVIS_FILE_SERVER_TTL_MIN", "JARVIS_PLAYWRIGHT_CHANNEL",
            "PINCHTAB_URL", "PINCHTAB_AUTOSTART", "PINCHTAB_ALLOWED_DOMAINS", "WHATSAPP_APP_ID")
RESTART_KEYS = (LINK_URL_KEY, *PATH_KEYS, *PATH_LIST_KEYS, *APP_KEYS)
ALLOWED_KEYS = frozenset((*LIVE_KEYS, *RESTART_KEYS))

# A name that looks like it holds a secret (the pre-commit hook's rule plus COOKIE), and secrets whose name
# does not say so. The node needs none of them in its environment.
SECRET_NAME_RE = re.compile(r"KEY|TOKEN|SECRET|PASS|HASH|CREDENTIAL|PRIVATE|COOKIE", re.IGNORECASE)
EXTRA_SECRET_NAMES = frozenset({"EMAIL_USER", "SIM_LOOKUP_API_URL"})  # private values (pre-commit hook)
NODE_SECRET_NAMES = ("PINCHTAB_TOKEN", "CONTROLLER_TOKEN")
SECRETS_KIND = "desktop_node_secrets"

_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_NODE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ROOM_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_HEX32_RE = re.compile(r"[0-9a-fA-F]{64}")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

if any(SECRET_NAME_RE.search(name) for name in ALLOWED_KEYS):  # a secret must never become allow-listed
    raise RuntimeError("a secret-looking name is on the Desktop Node settings allow-list")


class NodeConfigError(ValueError):
    """The node's configuration (node_config.json, the data root, JARVIS_LINK_URL) is unusable."""


class SecretEnvError(RuntimeError):
    """A secret name is in the environment that a spawn would pass on. The message names no value."""


# --- node home, node_config.json and the data root ----------------------------------------------------------

def node_home(environ: Mapping[str, str] | None = None) -> Path:
    """%LOCALAPPDATA%/JARVIS/node (keys.dpapi, secrets.dpapi, node_config.json and the snapshots)."""
    env = os.environ if environ is None else environ
    override = (env.get(NODE_HOME_ENV) or "").strip()
    if override:
        return Path(override)
    base = (env.get("LOCALAPPDATA") or "").strip() or str(Path.home() / "AppData" / "Local")
    return Path(base) / "JARVIS" / "node"


def code_root() -> Path:
    """The tree this code runs from: the pinned snapshot in production, the repo in a dev run."""
    return Path(__file__).resolve().parents[3]


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class NodeConfig:
    """%LOCALAPPDATA%/JARVIS/node/node_config.json, written by 'agent_ctl node update' (C-123).

    {data_root, run_dir?, agent_public_key?, pc_public_key?, node_id?, owner_room?}: data_root is this
    repo (absolute), run_dir defaults to <data_root>/run; the public keys are the pinned K-70 halves (32
    bytes as hex); node_id names the node on the link ('pc:<node_id>', default from the host name);
    owner_room is the room whose job peer gets window_context and clipboard events.
    """

    data_root: Path
    run_dir: Path
    agent_public_key: str | None = None
    pc_public_key: str | None = None
    node_id: str | None = None
    owner_room: str = DEFAULT_OWNER_ROOM
    source: Path | None = None


def _optional_text(doc: Mapping[str, Any], name: str, pattern: re.Pattern[str]) -> str | None:
    value = doc.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise NodeConfigError(f"{NODE_CONFIG_FILE}: {name} has the wrong format")
    return value.strip()


def load_node_config(path: Path | None = None, environ: Mapping[str, str] | None = None) -> NodeConfig:
    """Reads and checks node_config.json; NodeConfigError names the problem (never a key value).

    The data root must be an existing absolute folder outside the node home, so a snapshot (which lives
    under the node home) can never be the place where run/, logs/ or Jarvis_Outputs resolve.
    """
    home = node_home(environ)
    path = path or home / NODE_CONFIG_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NodeConfigError(f"{path} is missing: run 'agent_ctl node update' (C-123)") from None
    except (OSError, ValueError) as exc:
        raise NodeConfigError(f"{path} cannot be read ({type(exc).__name__})") from None
    if not isinstance(doc, dict):
        raise NodeConfigError(f"{NODE_CONFIG_FILE} must hold a JSON object")
    data_root = _absolute_dir(doc.get("data_root"), "data_root")
    if _inside(data_root, home):
        raise NodeConfigError("data_root lies inside the node home (a snapshot); it must be the repo")
    run_dir = doc.get("run_dir")
    run_path = data_root / "run" if run_dir in (None, "") else _absolute(run_dir, "run_dir")
    if _inside(run_path, home):
        raise NodeConfigError("run_dir lies inside the node home (a snapshot)")
    owner_room = _optional_text(doc, "owner_room", _ROOM_RE) or DEFAULT_OWNER_ROOM
    return NodeConfig(data_root=data_root, run_dir=run_path,
                      agent_public_key=_optional_text(doc, "agent_public_key", _HEX32_RE),
                      pc_public_key=_optional_text(doc, "pc_public_key", _HEX32_RE),
                      node_id=_optional_text(doc, "node_id", _NODE_ID_RE), owner_room=owner_room, source=path)


def _absolute(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise NodeConfigError(f"{NODE_CONFIG_FILE}: {name} is missing")
    path = Path(value.strip())
    if not path.is_absolute():
        raise NodeConfigError(f"{NODE_CONFIG_FILE}: {name} must be an absolute path")
    return path


def _absolute_dir(value: Any, name: str) -> Path:
    path = _absolute(value, name)
    if not path.is_dir():
        raise NodeConfigError(f"{NODE_CONFIG_FILE}: {name} is not an existing folder")
    return path


@dataclass(frozen=True)
class DataRoot:
    """JARVIS_DATA_ROOT and JARVIS_RUN_DIR as the supervisor exported them to the node child."""

    data_root: Path
    run_dir: Path

    @property
    def env_file(self) -> Path:
        """The PC root .env: the only settings file the node reads."""
        return self.data_root / ROOT_ENV_FILE

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"


def data_root_from_env(environ: Mapping[str, str] | None = None) -> DataRoot:
    """The node child's data root; both variables are required and absolute (never the snapshot path)."""
    env = os.environ if environ is None else environ
    root, run = (env.get(DATA_ROOT_ENV) or "").strip(), (env.get(RUN_DIR_ENV) or "").strip()
    if not root or not run:
        raise NodeConfigError(f"{DATA_ROOT_ENV} and {RUN_DIR_ENV} are not set: start the node through its "
                              "supervisor ('agent_ctl node start', C-123)")
    data_root, run_dir = Path(root), Path(run)
    if not data_root.is_absolute() or not run_dir.is_absolute():
        raise NodeConfigError(f"{DATA_ROOT_ENV} and {RUN_DIR_ENV} must be absolute paths")
    if not data_root.is_dir():
        raise NodeConfigError(f"{DATA_ROOT_ENV} is not an existing folder")
    return DataRoot(data_root, run_dir)


# --- the allow-listed .env parser ----------------------------------------------------------------------------

def _unquote(raw: str) -> str:
    """A quoted value ends at its closing quote (a comment may follow; backslashes stay literal, as Windows
    paths need); an unquoted value ends at an inline ' #' comment."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    hash_at = value.find(" #")
    return (value[:hash_at] if hash_at >= 0 else value).strip()


def parse_env_text(text: str, allowed: Iterable[str] = ALLOWED_KEYS) -> dict[str, str]:
    """The allow-listed assignments of a .env text: KEY=VALUE lines, optional 'export ', '#' comments,
    outer quotes removed, the last assignment wins. Values are never interpolated, and a name that is not
    on the allow-list is skipped without its value being kept."""
    names = frozenset(allowed)
    found: dict[str, str] = {}
    for line in text.lstrip("﻿").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if match is None or match.group(1) not in names:
            continue
        found[match.group(1)] = _unquote(match.group(2))
    return found


def parse_bool(value: str | None, default: bool) -> bool:
    """jarvis_config's rule: 1/true/yes/on or 0/false/no/off in any case; anything else is the default."""
    word = (value or "").strip().lower()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    return default


def _parse_int(value: str | None, default: int) -> int:
    try:
        number = int((value or "").strip())
    except ValueError:
        return default
    return number if number >= 0 else default


def resolve_path(value: str, data_root: Path) -> str:
    """~ and %VARS% expanded; a relative path is taken from the data root (never the snapshot or the cwd)."""
    expanded = os.path.expandvars(os.path.expanduser(value.strip().strip('"').strip("'")))
    path = Path(expanded)
    return str(path if path.is_absolute() else data_root / path)


def link_target(url: str) -> tuple[str, str]:
    """(base URL, socketio_path) of JARVIS_LINK_URL. Only https is accepted (K-70: TLS verified, http
    refused); a path is ignored, because the public bridge always serves 'vortex/socket.io'."""
    text = (url or "").strip()
    if not text.lower().startswith("https://"):
        raise NodeConfigError("JARVIS_LINK_URL must be an https:// URL (K-70 refuses http)")
    rest = text[len("https://"):].split("/", 1)[0].split("?", 1)[0]
    if not rest or "@" in rest or any(ch.isspace() for ch in rest):
        raise NodeConfigError("JARVIS_LINK_URL has no usable host")
    return f"https://{rest}", LINK_SOCKETIO_PATH


@dataclass(frozen=True)
class NodeSettings:
    """The allow-listed settings of one read of the PC root .env, resolved against the data root.

    values: what the file says (allowed keys only). environ: what the node exports (resolved paths, the
    node defaults never included). The typed fields are the live values.
    """

    values: Mapping[str, str] = field(default_factory=dict)
    environ: Mapping[str, str] = field(default_factory=dict)
    desktop_control: bool = True
    clipboard_helper: bool = True
    clipboard_speak: bool = False
    presence_window_s: int = PRESENCE_KEYS["JARVIS_PRESENCE_WINDOW_S"]
    capture_presence_s: int = PRESENCE_KEYS["JARVIS_CAPTURE_PRESENCE_S"]
    origin_presence_gate: bool = False
    link_url: str = DEFAULT_LINK_URL
    restart_digest: str = ""

    @classmethod
    def from_values(cls, values: Mapping[str, str], data_root: Path) -> NodeSettings:
        values = {name: value for name, value in values.items() if name in ALLOWED_KEYS}
        environ: dict[str, str] = {}
        for name, value in values.items():
            if name in PATH_KEYS and value.strip():
                environ[name] = resolve_path(value, data_root)
            elif name in PATH_LIST_KEYS and value.strip():
                parts = [part for part in value.split(";") if part.strip()]
                environ[name] = ";".join(resolve_path(part, data_root) for part in parts)
            else:
                environ[name] = value
        restart = {name: environ.get(name, "") for name in RESTART_KEYS}
        digest = hashlib.sha256(json.dumps(restart, sort_keys=True).encode("utf-8")).hexdigest()
        return cls(values=dict(values), environ=environ,
                   desktop_control=parse_bool(values.get("JARVIS_DESKTOP_CONTROL"), True),
                   clipboard_helper=parse_bool(values.get("JARVIS_CLIPBOARD_HELPER"), True),
                   clipboard_speak=parse_bool(values.get("JARVIS_CLIPBOARD_SPEAK"), False),
                   presence_window_s=_parse_int(values.get("JARVIS_PRESENCE_WINDOW_S"),
                                                PRESENCE_KEYS["JARVIS_PRESENCE_WINDOW_S"]),
                   capture_presence_s=_parse_int(values.get("JARVIS_CAPTURE_PRESENCE_S"),
                                                 PRESENCE_KEYS["JARVIS_CAPTURE_PRESENCE_S"]),
                   origin_presence_gate=parse_bool(values.get("JARVIS_ORIGIN_PRESENCE_GATE"), False),
                   link_url=(values.get(LINK_URL_KEY) or "").strip() or DEFAULT_LINK_URL,
                   restart_digest=digest)

    def live_environ(self) -> dict[str, str]:
        """The live keys to export now; '' for one the file does not set (it is then removed)."""
        return {name: self.environ.get(name, "") for name in LIVE_KEYS}

    def config_values(self) -> dict[str, bool]:
        """The K-15 config attributes behind the live boolean keys."""
        return {"desktop_control_enabled": self.desktop_control, "clipboard_helper_enabled": self.clipboard_helper,
                "clipboard_speak": self.clipboard_speak}

    def needs_restart(self, previous: NodeSettings) -> bool:
        """True when JARVIS_LINK_URL or a path or app key differs from `previous`."""
        return self.restart_digest != previous.restart_digest


class SettingsSource:
    """The PC root .env behind the allow-listed parser: load() at start, poll() every POLL_S.

    poll() re-reads the file (it is small) and returns (previous, new) when the allow-listed content
    changed, else None. A missing file gives the defaults, as an empty file would. A file that cannot be
    read right now (a sharing violation while another program writes it) keeps the current settings, and
    at start the node refuses to run without it (NodeConfigError). A change applies once two polls in a
    row read the same content, so a file read half-written never loosens the kill switch or restarts the
    node; turning the kill switch off applies at the first poll.
    """

    LOAD_TRIES = 3
    LOAD_RETRY_S = 0.1

    def __init__(self, env_file: Path, data_root: Path) -> None:
        self.env_file, self.data_root = Path(env_file), Path(data_root)
        self._fingerprint: str | None = None
        self._candidate: str | None = None
        self.current: NodeSettings | None = None

    def _read(self) -> tuple[str, NodeSettings] | None:
        """(fingerprint, settings) of the file now; None when it exists but cannot be read."""
        try:
            text = self.env_file.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text = ""
        except OSError:
            return None
        values = parse_env_text(text)
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()
        return fingerprint, NodeSettings.from_values(values, self.data_root)

    def load(self) -> NodeSettings:
        for attempt in range(self.LOAD_TRIES):
            read = self._read()
            if read is not None:
                (self._fingerprint, self.current), self._candidate = read, None
                return self.current
            if attempt + 1 < self.LOAD_TRIES:
                time.sleep(self.LOAD_RETRY_S)
        raise NodeConfigError(f"{self.env_file} cannot be read; the node does not start without its kill switch")

    def poll(self) -> tuple[NodeSettings, NodeSettings] | None:
        if self.current is None:
            try:
                self.load()
            except NodeConfigError:
                pass
            return None
        read = self._read()
        if read is None:
            return None
        fingerprint, settings = read
        if fingerprint == self._fingerprint:
            self._candidate = None
            return None
        kill_switch_off = self.current.desktop_control and not settings.desktop_control
        if not kill_switch_off and fingerprint != self._candidate:
            self._candidate = fingerprint  # applied when the next poll reads the same content
            return None
        self._candidate = None
        previous, self._fingerprint, self.current = self.current, fingerprint, settings
        return previous, settings


# --- environment hygiene -------------------------------------------------------------------------------------

def is_secret_name(name: str) -> bool:
    return name not in ALLOWED_KEYS and (bool(SECRET_NAME_RE.search(name)) or name.upper() in EXTRA_SECRET_NAMES)


def secret_names(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return sorted(name for name in env if is_secret_name(name))


def scrub_secrets(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Removes every secret-looking name; returns the names removed (never their values)."""
    env = os.environ if environ is None else environ
    removed = secret_names(env)
    for name in removed:
        env.pop(name, None)
    return removed


def assert_clean(environ: Mapping[str, str] | None = None) -> None:
    """SecretEnvError when a secret name is present (checked before every spawn)."""
    found = secret_names(environ)
    if found:
        raise SecretEnvError(f"refusing to spawn: {len(found)} secret name(s) in the environment "
                             f"({', '.join(found[:5])})")


def node_defaults(data_root: Path, run_dir: Path, *, run_mode: str = "active") -> dict[str, str]:
    """What the node always exports: its data root and role, the dead bridge URL, and the three paths
    whose jarvis_config defaults would otherwise resolve inside the snapshot."""
    return {DATA_ROOT_ENV: str(data_root), RUN_DIR_ENV: str(run_dir), "JARVIS_ROLE": "desktop",
            "JARVIS_DESKTOP_MODE": "local", "UI_BRIDGE_URL": DEAD_BRIDGE_URL,
            "LOG_FILE": str(data_root / TOOLS_LOG_FILE), "JARVIS_OUTPUTS_DIR": str(data_root / "Jarvis_Outputs"),
            "WHATSAPP_CONTACTS_FILE": str(data_root / "data" / "whatsapp_contacts.json"),
            "JARVIS_NODE_RUN_MODE": run_mode}


def apply_environment(settings: NodeSettings, root: DataRoot, *, run_mode: str = "active",
                      environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Scrubs secrets, then exports the node defaults and the allow-listed settings (a setting wins over a
    default; LOG_FILE is no setting, so the services modules always log to the node's tools log). Returns the
    scrubbed names.

    Allow-listed names inherited from the parent are dropped first: the PC root .env is their only source.
    """
    env = os.environ if environ is None else environ
    removed = scrub_secrets(env)
    for name in ALLOWED_KEYS:
        env.pop(name, None)
    env.update(node_defaults(root.data_root, root.run_dir, run_mode=run_mode))
    env.update(settings.environ)
    assert_clean(env)
    return removed


def apply_live_settings(settings: NodeSettings, *, environ: MutableMapping[str, str] | None = None,
                        config: Any = None) -> None:
    """Applies the live keys at once: os.environ, and the K-15 attributes on jarvis_config's config."""
    env = os.environ if environ is None else environ
    for name, value in settings.live_environ().items():
        if value:
            env[name] = value
        else:
            env.pop(name, None)
    if config is not None:
        for attr, value in settings.config_values().items():
            setattr(config, attr, value)


def _noop_load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
    return False


def neutralize_dotenv() -> None:
    """Makes dotenv.load_dotenv (and dotenv_values) inert before any services module imports it; a missing
    dotenv package is replaced by a stub, so a later 'from dotenv import load_dotenv' gets the no-op too."""
    try:
        import dotenv  # pylint: disable=import-outside-toplevel
        import dotenv.main  # pylint: disable=import-outside-toplevel
    except ImportError:
        import types  # pylint: disable=import-outside-toplevel
        stub = types.ModuleType("dotenv")
        stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        stub.dotenv_values = lambda *_a, **_k: {}  # type: ignore[attr-defined]
        stub.find_dotenv = lambda *_a, **_k: ""  # type: ignore[attr-defined]
        sys.modules["dotenv"] = stub
        return
    for module in (dotenv, dotenv.main):
        module.load_dotenv = _noop_load_dotenv  # type: ignore[assignment]
        module.dotenv_values = lambda *_a, **_k: {}  # type: ignore[assignment]


class EnvGuard:
    """Checks the environment before every spawn: subprocess.Popen (subprocess.*, asyncio subprocesses,
    os.popen), os.system, os.startfile and os.spawnv/os.spawnve. A secret name in os.environ, or in the
    env= mapping of a Popen, raises SecretEnvError instead of reaching the child."""

    def __init__(self, check: Callable[[Mapping[str, str] | None], None] = assert_clean) -> None:
        self._check = check
        self._saved: list[tuple[Any, str, Any]] = []

    @property
    def installed(self) -> bool:
        return bool(self._saved)

    def install(self) -> None:
        if self._saved:
            return
        check = self._check
        original_init = subprocess.Popen.__init__

        def guarded_init(popen: Any, *args: Any, **kwargs: Any) -> None:
            check(None)
            env = kwargs.get("env")
            if env is not None:
                check(env)
            original_init(popen, *args, **kwargs)

        self._patch(subprocess.Popen, "__init__", guarded_init)
        for name in ("system", "startfile", "spawnv", "spawnve"):
            original = getattr(os, name, None)
            if original is not None:
                self._patch(os, name, self._wrap(original, check))

    @staticmethod
    def _wrap(original: Callable[..., Any], check: Callable[[Mapping[str, str] | None], None]) -> Callable[..., Any]:
        def guarded(*args: Any, **kwargs: Any) -> Any:
            check(None)
            return original(*args, **kwargs)
        guarded.__wrapped__ = original  # type: ignore[attr-defined]
        return guarded

    def _patch(self, owner: Any, name: str, replacement: Any) -> None:
        self._saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def uninstall(self) -> None:
        while self._saved:
            owner, name, original = self._saved.pop()
            setattr(owner, name, original)


# --- secrets.dpapi -------------------------------------------------------------------------------------------

def node_secrets_document(values: Mapping[str, str]) -> bytes:
    """The plaintext that secrets.dpapi wraps ('agent_ctl node update', C-123). Never log or print it."""
    doc: dict[str, Any] = {"kind": SECRETS_KIND, "v": 1}
    doc.update({name: str(values[name]) for name in NODE_SECRET_NAMES if values.get(name)})
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_node_secrets(data: bytes) -> dict[str, str]:
    """PINCHTAB_TOKEN and CONTROLLER_TOKEN from an unwrapped secrets.dpapi; ValueError (no value shown)."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("secrets.dpapi does not hold a JSON document") from None
    if not isinstance(doc, dict) or doc.get("kind") != SECRETS_KIND or doc.get("v") != 1:
        raise ValueError("secrets.dpapi is not a Desktop Node secrets document")
    secrets_found: dict[str, str] = {}
    for name in NODE_SECRET_NAMES:
        value = doc.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError(f"secrets.dpapi: {name} has the wrong format")
        secrets_found[name] = value
    return secrets_found
