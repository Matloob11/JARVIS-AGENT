"""Desktop Node runtime (plan task C-120 steps 2, 3, 5, 6, 7 and 8; K-70 v5, K-72, K-74 v5, K-76).

The node child that the supervisor starts ('python -P -m src.core.desktop node [--canary]', cwd = the data
root, PYTHONPATH = the snapshot). main():
1. makes dotenv a no-op before any services module loads, reads the allow-listed settings from the PC root
   .env (settings.py), scrubs secret names from the environment and checks before every spawn (EnvGuard);
2. refuses an elevated token; loads keys.dpapi with desktop_protocol.load_pc_key_file() and the pinned agent
   key from node_config.json; PINCHTAB_TOKEN and CONTROLLER_TOKEN from secrets.dpapi go onto config in memory
   only;
3. builds NodeRuntime, starts the power listener and installs the optional modules of the later tasks
   (OPTIONAL_PLUGINS: their install(node) hook; one that fails keeps nothing installed), then runs it until a
   stop.

NodeRuntime (the LinkHandler of link.DesktopLink):
- an exec is answered in this order: a call_id the WAL holds as started gets its real outcome (re-ack or
  result_resend), never a second run; body checks (BAD_ARGS); the seq order of its reply (up to 500 ms); the
  clock: ClockEstimator.check_actuation() (CLOCK_SKEW), start_deadline() and check_start_window()
  (START_WINDOW_MISSED); the tool and its live param_schema_sha256 (MANIFEST_MISMATCH); the policy (K-76
  codes); a degraded executor (DESKTOP_DEGRADED). Every refusal is the ack {accepted:false, code}, recorded in
  the WAL first. An accepted call is written to the WAL before its ack {accepted:true, risk, cls}, runs on the
  executor, sends progress at least every PROGRESS_S and always ends with a result, which stays in the outbox
  until result_ack.
- a link refusal (an exec that failed a receiver check) is signed only when DesktopLinkError.refusal_code
  allowed it AND the WAL does not hold the call as started (else its real outcome goes out).
- cancel: the result follows within CANCEL_ANSWER_S ('cancelled' when the tool has not ended by then), on the
  call's own task, so the link keeps reading; a cancel that comes while the call is still judged refuses it
  (DESKTOP_REJECTED), or, once the WAL holds it as accepted, makes it end not_started.
- dead-man switch: a link down for more than DEAD_MAN_S cancels the running input-class calls (each still
  ends with a result in the outbox); a messaging call (a WhatsApp send step) completes.
- controls (tighten only): desktop_stop -> read, desktop_off -> off (both cancel every in-flight call with
  STOPPED_BY_USER), stop_all from an agent cancels that agent's calls, active_job and link_disabled.
- the status file (run/desktop_node.json) every STATUS_EVERY_S and on change, the audit log, the settings poll
  (every POLL_S; a restart-only key exits EXIT_RESTART), the C-31 watchdog on the link loop (a 60 s stall
  exits EXIT_STALLED for an immediate restart, reset after a resume).

Until C-121 installs src/core/desktop/policy.py, BaselinePolicy and MemoryState apply: the node reports mode
'read' and refuses every actuation (DESKTOP_READ_ONLY), so nothing can act on the desktop without the real
policy; reads, pc.ping and de-escalation run.
"""
# pylint: disable=too-many-lines  # one runtime: exec, cancel, controls, outbox, status and entry
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import importlib
import inspect
import logging
import math
import numbers
import os
import re
import socket
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Protocol

from services.ai_core import tool_placement
from services.utils import desktop_protocol as dp
from services.utils.desktop_protocol import DesktopLinkError, VerifiedEnvelope
from src.core.desktop import (
    EXIT_CONFIG,
    EXIT_ELEVATED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_RESTART,
    EXIT_STALLED,
    RUN_MODES,
    STALL_RESTART_S,
)
from src.core.desktop import health
from src.core.desktop import settings as node_settings
from src.core.desktop import wal as wal_mod
from src.core.desktop.executor import (
    ACTUATION_CLASSES,
    CANCEL_ANSWER_S,
    FAILED,
    LOCK_WAIT_MAX_S,
    NOT_STARTED,
    RESULT_STATUSES,
    TIMEOUT_MAX_S,
    CallHandle,
    CallOutcome,
    CompositeProvider,
    ExecCall,
    Executor,
    OperationRegistry,
    SeqGate,
    ToolLoop,
    ToolProvider,
    ToolSpec,
    k12_error,
    result_body,
)
from src.core.desktop.link import ISOLATED_ROOM_RE, AgentPeer, CallRef, DesktopLink, LinkTransport

logger = logging.getLogger("JARVIS-DESKTOP.node")

NODE_VERSION = "1.0"
DEAD_MAN_S = 5.0           # K-70: a link down this long cancels the running input-class calls
PROGRESS_S = 9.0           # K-70: progress at least every 10 s while a call runs
PROGRESS_SCAN_S = 1.0
PURGE_EVERY_S = 600.0
PRELOAD_TIMEOUT_S = 90.0
EARLY_CANCEL_TTL_S = 120.0
# The C-31 watchdog only has to see a 60 s stall here: a 1 s heartbeat keeps the idle node near 0 % CPU.
WATCHDOG_INTERVAL_S = 1.0
WATCHDOG_THRESHOLD_MS = 5000.0
RESULT_BODY_MAX = 180_000  # plaintext bytes; sealed and base64'd it stays under the 256 KiB result cap
OPTIONAL_PLUGINS = ("policy", "capabilities", "sensors", "mirror", "backup_pull")
MODES = ("off", "read", "full")
_MODE_RANK = {"off": 0, "read": 1, "full": 2}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_NODE_ID_CLEAN = re.compile(r"[^A-Za-z0-9._-]")
_ALERT_ON_REFUSAL = {"APPROVAL_INVALID": "APPROVAL_INVALID", "CLOCK_SKEW": "CLOCK_SKEW",
                     "MANIFEST_MISMATCH": "MANIFEST_MISMATCH", "DESKTOP_RATE_LIMITED": "DESKTOP_RATE_LIMITED"}
_RESULT_UNKNOWN = "Sir, is laptop kaam ka poora nateeja ab mehfooz nahi; ho sakta hai yeh ho chuka ho."
_RESULT_UNENCODABLE = "Sir, laptop ke is kaam ka nateeja bheja nahi ja saka."
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_SAFE_DEPTH = 24  # below the library's MAX_JSON_DEPTH (32), with room for the body around a tool result


# --- desktop state and policy (C-121 replaces both through install(node)) ---------------------------------------

@dataclass(frozen=True)
class DesktopState:
    """K-74 desktop_state: the node's mode, the Windows lock and the panic latch (never presence)."""

    mode: str = "read"
    locked: bool = False
    panic: bool = False

    def notice(self) -> dict[str, Any]:
        return {"mode": self.mode, "locked": self.locked, "panic": self.panic}


class StateSource(Protocol):
    def current(self) -> DesktopState:
        """The state now."""

    def tighten(self, mode: str, *, source: str) -> bool:
        """A remote stop: lowers the mode (full > read > off), never raises it. True when it changed."""

    def add_listener(self, callback: Callable[[DesktopState], None]) -> None:
        """callback(new_state) after every change."""


class MemoryState:
    """The C-120 state source: in memory, mode 'read' until C-121's persisted, presence-aware one replaces it
    (K-76: the real node starts in full unless run/desktop_state.json says otherwise)."""

    def __init__(self, mode: str = "read") -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._state = DesktopState(mode=mode)
        self._listeners: list[Callable[[DesktopState], None]] = []

    def current(self) -> DesktopState:
        return self._state

    def set(self, state: DesktopState) -> None:
        if state != self._state:
            self._state = state
            for callback in list(self._listeners):
                callback(state)

    def tighten(self, mode: str, *, source: str) -> bool:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if _MODE_RANK[mode] >= _MODE_RANK[self._state.mode]:
            return False
        logger.warning("Desktop mode %s -> %s (from %s)", self._state.mode, mode, source)
        self.set(DesktopState(mode=mode, locked=self._state.locked, panic=self._state.panic))
        return True

    def add_listener(self, callback: Callable[[DesktopState], None]) -> None:
        self._listeners.append(callback)


@dataclass(frozen=True)
class PendingCall:
    """A verified exec before it is accepted: what the policy decides on (K-76)."""

    call_id: str
    tool: str
    cls: str
    origin: str
    room: str
    peer: str
    args: Mapping[str, Any]
    args_sha256: str
    approval: Mapping[str, Any] | None
    risk: str
    spec: ToolSpec
    job_id: str = ""

    @property
    def needs_input_lock(self) -> bool:
        return self.spec.needs_input_lock

    @property
    def actuation(self) -> bool:
        return self.cls in ACTUATION_CLASSES


class Policy(Protocol):
    def check(self, call: PendingCall) -> str | None | Awaitable[str | None]:
        """A K-76 refusal code, or None to run the call."""


class BaselinePolicy:
    """K-76 check order with what C-120 knows; C-121's policy replaces it (presence, launch surfaces, paths,
    approval channels, caps, active_job, alerts). Fail closed: kill switch, panic, mode and Windows lock first,
    then capture (presence is C-121's: refused), class and approval (args_sha256 must match), then origin."""

    def __init__(self, runtime: NodeRuntime) -> None:
        self._rt = runtime

    def check(self, call: PendingCall) -> str | None:  # pylint: disable=too-many-return-statements
        cls = call.cls
        if cls == tool_placement.DE_ESCALATION:
            return None
        if call.actuation and not self._rt.settings.desktop_control:
            return "DESKTOP_CONTROL_DISABLED"
        state = self._rt.state.current()
        if state.panic:
            return "DESKTOP_PANIC"
        if state.mode == "off":
            return "DESKTOP_CONTROL_DISABLED"
        if (call.actuation or cls == tool_placement.CAPTURE) and state.mode != "full":
            return "DESKTOP_READ_ONLY"
        if state.locked and (call.needs_input_lock or cls == tool_placement.CAPTURE):
            return "DESKTOP_LOCKED"
        if cls == tool_placement.CAPTURE:
            return "PRESENCE_REQUIRED"
        if cls in tool_placement.CONFIRM_CLASSES or call.risk in ("medium", "high"):
            if not isinstance(call.approval, Mapping):
                return "APPROVAL_REQUIRED"
            if not dp.digest_equal(call.approval.get("args_sha256"), call.args_sha256):
                return "APPROVAL_INVALID"
        if call.actuation and (call.origin == "canary" or ISOLATED_ROOM_RE.match(call.room)):
            return "DESKTOP_READ_ONLY"
        return None


# --- keys ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class NodeKeys:
    """The node's link keys: its Ed25519 signing key (from keys.dpapi), the pinned agent key and the DPAPI-
    wrapped tool secrets (never in os.environ)."""

    signing_key: Any = field(repr=False)
    agent_public_key: Any = field(repr=False)
    secrets: Mapping[str, str] = field(default_factory=dict, repr=False)


class KeyLoader:
    """Reads the node's keys from the node home. load_pc_key and unprotect are the DPAPI calls (tests replace
    them; nothing here creates a key)."""

    def __init__(self, home: Path, *, load_pc_key: Callable[[Path], Any] = dp.load_pc_key_file,
                 unprotect: Callable[[bytes], bytes] = dp.dpapi_unprotect) -> None:
        self.home, self._load_pc_key, self._unprotect = Path(home), load_pc_key, unprotect

    def load(self, cfg: node_settings.NodeConfig) -> NodeKeys:
        key_file = self.home / node_settings.KEYS_FILE
        if not key_file.is_file():
            raise node_settings.NodeConfigError(f"{key_file} is missing: run scripts/ops/gen_desktop_keys.py "
                                                "--role pc")
        signing_key = self._load_pc_key(key_file)
        if cfg.pc_public_key and not dp.digest_equal(dp.public_key_hex(signing_key), cfg.pc_public_key):
            raise node_settings.NodeConfigError("keys.dpapi does not match pc_public_key in node_config.json")
        if not cfg.agent_public_key:
            raise node_settings.NodeConfigError("node_config.json has no agent_public_key (C-123 node update)")
        agent_key = dp.public_key_from_hex(cfg.agent_public_key)
        secrets_file = self.home / node_settings.SECRETS_FILE
        secrets_found: dict[str, str] = {}
        if secrets_file.is_file():
            secrets_found = node_settings.parse_node_secrets(self._unprotect(secrets_file.read_bytes()))
        return NodeKeys(signing_key, agent_key, secrets_found)


def default_node_id(cfg_node_id: str | None = None) -> str:
    """node_config.json's node_id, else this host's name reduced to the K-70 id characters."""
    if cfg_node_id:
        return cfg_node_id
    name = _NODE_ID_CLEAN.sub("-", socket.gethostname() or "laptop").strip("-._") or "laptop"
    return name[:64]


def manifest_digests(load: Callable[[], Mapping[str, Any]] = tool_placement.load_manifest) -> tuple[str, str]:
    """(manifest_sha256, param_schema_sha256) of the committed manifest of this snapshot, for the hello; the
    second digests every tool's and pc.* operation's param_schema_sha256. ('', '') when it cannot be read."""
    try:
        manifest = load()
    except Exception:  # pylint: disable=broad-exception-caught  # the hello still goes out
        return "", ""
    schemas = {tool["name"]: tool.get("param_schema_sha256", "") for tool in manifest.get("tools") or ()}
    schemas.update({op["name"]: op.get("param_schema_sha256", "") for op in manifest.get("pc_operations") or ()})
    return str(manifest.get("manifest_sha256", "")), hashlib.sha256(dp.canonical_json(schemas)).hexdigest()


def json_safe(value: Any, depth: int = 0) -> Any:  # pylint: disable=too-many-return-statements
    """A tool result reduced to what canonical JSON can carry: NaN and infinity become None, a lone surrogate
    U+FFFD, keys strings, tuples and sets lists, other number types int or float, bytes text, any other value
    its str(), and nesting deeper than _SAFE_DEPTH a marker."""
    if isinstance(value, str):
        return _LONE_SURROGATE.sub("�", value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int)):
        return value
    if depth >= _SAFE_DEPTH:
        return "[nested too deep]"
    if isinstance(value, Mapping):
        return {json_safe(key if isinstance(key, str) else _text(key)): json_safe(item, depth + 1)
                for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item, depth + 1) for item in value]
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return json_safe(float(value))
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return json_safe(_text(value))


def _text(value: Any) -> str:
    try:
        return str(value)
    except Exception:  # pylint: disable=broad-exception-caught  # a broken __str__ still gives a value
        return f"<{type(value).__name__}>"


# --- the runtime ---------------------------------------------------------------------------------------------

@dataclass
class NodeOptions:
    """Timing knobs (tests shorten them) and the run mode."""

    run_mode: str = "active"
    heartbeat_s: float = 10.0
    ping_s: float = 30.0
    dead_man_s: float = DEAD_MAN_S
    cancel_answer_s: float = CANCEL_ANSWER_S
    progress_s: float = PROGRESS_S
    progress_scan_s: float = PROGRESS_SCAN_S
    status_every_s: float = health.STATUS_EVERY_S
    settings_poll_s: float = node_settings.POLL_S
    seq_hold_s: float = 0.5
    degrade_after_s: float = 10.0
    alert_fold_s: float = 60.0
    reconnect_first_s: float = 1.0
    reconnect_max_s: float = 30.0
    jitter_s: float = 1.0
    short_suspend_s: float = 30.0


@dataclass
class _Running:
    handle: CallHandle
    ref: CallRef
    peer: str
    exec_ts: float
    risk: str
    cls: str
    answered: bool = False
    last_progress: float = 0.0


class NodeRuntime:
    """The Desktop Node (see the module docstring)."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    def __init__(self, *, node_id: str, keys: NodeKeys, data_root: Path, run_dir: Path,
                 settings_source: node_settings.SettingsSource, transport_factory: Callable[[], LinkTransport],
                 tool_provider: ToolProvider, owner_room: str = node_settings.DEFAULT_OWNER_ROOM,
                 options: NodeOptions | None = None, config: Any = None, git_sha: str = "",
                 manifest_sha256: str = "", param_schema_sha256: str = "",
                 seen: dp.SeenStore | None = None, wal: wal_mod.WriteAheadLog | None = None,
                 tool_loop: ToolLoop | None = None,
                 watchdog_factory: Callable[[NodeRuntime], Any] | None = None,
                 stall_action: Callable[[int], None] | None = None,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic) -> None:
        self.options = options or NodeOptions()
        if self.options.run_mode not in RUN_MODES:
            raise ValueError(f"run mode must be one of {RUN_MODES}")
        self.node_id, self.data_root, self.run_dir = node_id, Path(data_root), Path(run_dir)
        self.run_mode, self.config = self.options.run_mode, config
        self.git_sha, self.manifest_sha256, self.param_schema_sha256 = git_sha, manifest_sha256, param_schema_sha256
        self.boot_id = uuid.uuid4().hex
        self.started_at = wall()
        self._wall, self._mono = wall, mono
        self.settings_source = settings_source
        self.settings = settings_source.current or settings_source.load()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.seen = seen or dp.SeenStore(self.run_dir / wal_mod.WAL_FILE, namespace=f"pc:{node_id}")
        self.wal = wal or wal_mod.WriteAheadLog(self.run_dir / wal_mod.WAL_FILE, wall=wall)
        self.audit = health.AuditLog(self.data_root / health.AUDIT_FILE, wall=wall)
        self.status_file = health.StatusFile(self.run_dir / health.STATUS_FILE, wall=wall)
        self.clock = dp.ClockEstimator(wall=wall, mono=mono)
        self.receiver = dp.Receiver(f"pc:{node_id}", {"agent": keys.agent_public_key}, self.seen, clock=self.clock,
                                    wall=wall)
        self.signer = dp.LinkSigner(f"pc:{node_id}", keys.signing_key)
        self.tool_loop = tool_loop or ToolLoop()
        self.executor = Executor(self.tool_loop, degrade_after_s=self.options.degrade_after_s, clock=mono,
                                 on_running=self._on_running)
        self.operations = OperationRegistry()
        self.operations.register("pc.ping", self._pc_ping)
        self.provider = CompositeProvider(self.operations, tool_provider)
        self.gate = SeqGate(hold_s=self.options.seq_hold_s, clock=mono)
        self.state: StateSource = MemoryState("read")
        self.state.add_listener(self._state_changed)
        self.policy: Policy = BaselinePolicy(self)
        self.link = DesktopLink(
            transport_factory=transport_factory, signer=self.signer, receiver=self.receiver, clock=self.clock,
            handler=self, owner_room=owner_room, heartbeat_s=self.options.heartbeat_s, ping_s=self.options.ping_s,
            alert_fold_s=self.options.alert_fold_s, reconnect_first_s=self.options.reconnect_first_s,
            reconnect_max_s=self.options.reconnect_max_s, jitter_s=self.options.jitter_s,
            short_suspend_s=self.options.short_suspend_s, wall=wall, mono=mono)
        self.plugins: dict[str, str] = {}
        self.active_job: Mapping[str, Any] | None = None
        self.bridge_hint: dict[str, Any] = {}
        self.exit_code = EXIT_OK
        self._watchdog_factory = watchdog_factory
        self._watchdog: Any = None
        self._stall_action = stall_action or os._exit  # pylint: disable=protected-access
        self._stop_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._running: dict[str, _Running] = {}
        self._pending: dict[str, str] = {}  # call_id being judged -> its peer
        self._early_cancel: dict[str, tuple[str, float]] = {}
        self.power_listener: Any = None  # link.PowerListener in the node process (C-121 adds session events)
        self._dead_man: asyncio.TimerHandle | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._call_tasks: set[asyncio.Task[Any]] = set()
        self._extra_tasks: list[tuple[Callable[[], Awaitable[Any]], bool, str]] = []
        self._hint_listeners: list[Callable[[dp.BridgeHint], Any]] = []
        self._control_listeners: list[Callable[[str, Mapping[str, Any], str], Any]] = []
        self._shutdown_hooks: list[Callable[[], Any]] = []
        self._outbox_size = 0
        self._status_dirty = threading.Event()

    # --- plugin API (C-121, C-122) --------------------------------------------------------------------------

    def set_policy(self, policy: Policy) -> None:
        self.policy = policy

    def set_state_source(self, state: StateSource) -> None:
        self.state = state
        state.add_listener(self._state_changed)
        self._state_changed(state.current())

    def register_operation(self, name: str, handler: Callable[[Mapping[str, Any]], Awaitable[Any]],
                           **options: Any) -> ToolSpec:
        """A pc.* operation (C-122 capabilities)."""
        return self.operations.register(name, handler, **options)

    def add_task(self, factory: Callable[[], Awaitable[Any]], *, active_only: bool = False, name: str = "") -> None:
        """A background coroutine on the link loop; active_only: not in --canary (C-122 sensors)."""
        self._extra_tasks.append((factory, active_only, name or getattr(factory, "__name__", "task")))

    def add_hint_listener(self, callback: Callable[[dp.BridgeHint], Any]) -> None:
        self._hint_listeners.append(callback)

    def add_control_listener(self, callback: Callable[[str, Mapping[str, Any], str], Any]) -> None:
        self._control_listeners.append(callback)

    def add_shutdown_hook(self, callback: Callable[[], Any]) -> None:
        self._shutdown_hooks.append(callback)

    def raise_alert(self, code: str, **fields: Any) -> None:
        """A K-74 alert notice (link.raise_alert); thread-safe."""
        self.link.raise_alert(code, **fields)

    def install_plugins(self, names: Sequence[str] = OPTIONAL_PLUGINS, *,
                        import_module: Callable[[str], Any] = importlib.import_module) -> dict[str, str]:
        """Imports src.core.desktop.<name> and calls its install(node), when the module exists. An install that
        raises keeps nothing it set (state source, policy, tasks, listeners, hooks, operations): a half-installed
        C-121 must never leave a full-mode state behind BaselinePolicy."""
        for name in names:
            module_name = f"src.core.desktop.{name}"
            try:
                module = import_module(module_name)
            except ModuleNotFoundError as exc:
                if exc.name == module_name:
                    self.plugins[name] = "absent"
                    continue
                self.plugins[name] = "error"
                logger.error("Desktop Node module %s failed to load (%s)", name, type(exc).__name__)
                continue
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the core still runs
                self.plugins[name] = "error"
                logger.error("Desktop Node module %s failed to load (%s)", name, type(exc).__name__)
                continue
            install = getattr(module, "install", None)
            if not callable(install):
                self.plugins[name] = "no install()"
                continue
            saved = self._plugin_snapshot()
            try:
                install(self)
                self.plugins[name] = "installed"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._restore_plugin_snapshot(saved)
                self.plugins[name] = "error"
                logger.error("Desktop Node module %s install failed (%s); none of it stays installed", name,
                             type(exc).__name__)
        logger.info("Desktop Node modules: %s", ", ".join(f"{k}={v}" for k, v in self.plugins.items()) or "none")
        return dict(self.plugins)

    def _plugin_snapshot(self) -> tuple[Any, ...]:
        return (self.policy, self.state, list(self._extra_tasks), list(self._hint_listeners),
                list(self._control_listeners), list(self._shutdown_hooks), self.operations.snapshot())

    def _restore_plugin_snapshot(self, saved: tuple[Any, ...]) -> None:
        (self.policy, self.state, self._extra_tasks, self._hint_listeners, self._control_listeners,
         self._shutdown_hooks, operations) = saved
        self.operations.restore(operations)

    # --- LinkHandler: hello and state ----------------------------------------------------------------------------

    async def hello_fields(self, report_limit: int) -> dict[str, Any]:
        report = await self.wal.areport(limit=report_limit) if report_limit > 0 else []
        state = self.state.current()
        return {"boot_id": self.boot_id, "version": NODE_VERSION, "git_sha": self.git_sha or "unknown",
                "manifest_sha256": self.manifest_sha256, "param_schema_sha256": self.param_schema_sha256,
                "mode": state.mode, "locked": state.locked, "wal_report": report}

    def desktop_state(self) -> dict[str, Any]:
        return self.state.current().notice()

    def _state_changed(self, _state: DesktopState) -> None:
        """State listener; any thread (C-121's tray, panic hotkey and session handlers have their own)."""
        self.link.state_changed()
        self._status_dirty.set()

    def link_changed(self, state: str) -> None:
        loop = self._loop
        if state in ("disconnected", "suspended"):
            if loop is not None and self._dead_man is None:
                self._dead_man = loop.call_later(self.options.dead_man_s, self._dead_man_fired)
        elif state in ("connected", "resumed") and self._dead_man is not None and self.link.connected:
            self._dead_man.cancel()
            self._dead_man = None
        if state == "resumed":
            self._restart_watchdog()
        self._status_dirty.set()

    def _dead_man_fired(self) -> None:
        self._dead_man = None
        if self.link.connected and not self.link.quiet:
            return
        runs = [run for run in list(self._running.values())
                if not run.answered and run.cls == tool_placement.INPUT]
        if runs:
            logger.warning("Dead-man switch: the link was down %.0f s; cancelled %d input call(s)",
                           self.options.dead_man_s, len(runs))
        for run in runs:  # each still ends with a result (kept in the outbox until the link returns)
            self._cancel_soon(run, "DESKTOP_OFFLINE")
            self.audit.record("dead_man_cancel", call_id=run.ref.call_id, tool=run.ref.tool, cls=run.cls)

    # --- LinkHandler: exec ----------------------------------------------------------------------------------------

    async def handle_exec(self, env: VerifiedEnvelope, body: dict[str, Any], peer: AgentPeer) -> None:
        """Registers the call before the link reads its next message (a cancel right behind it finds it), then
        judges and runs it on its own task, so a seq hold never stops the link from reading."""
        ref = CallRef.of(env.header)
        received_mono, received_wall = self._mono(), self._wall()
        if ref.call_id in self._pending:
            return  # the same call is being judged right now; its answer is on the way
        self._pending[ref.call_id] = peer.ident
        self._track(self._exec_task(env, body, peer, ref, received_mono, received_wall))

    def _track(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """A call's own task (an exec, or the answer to a cancel): the link never waits for a tool."""
        task = asyncio.ensure_future(coro)
        self._call_tasks.add(task)
        task.add_done_callback(self._call_tasks.discard)
        return task

    async def _exec_task(self, env: VerifiedEnvelope, body: dict[str, Any], peer: AgentPeer, ref: CallRef,
                         received_mono: float, received_wall: float) -> None:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        try:
            await self._judge_exec(env, body, peer, ref, env.header.get("seq"), received_mono, received_wall)
        except Exception:  # pylint: disable=broad-exception-caught  # logged; never ends the link
            logger.exception("Desktop exec %s failed in the node", ref.tool)
        finally:
            self.gate.done(env.header.get("seq"))  # never holds the reply's next call (idempotent)
            self._pending.pop(ref.call_id, None)
            self._early_cancel.pop(ref.call_id, None)

    async def _judge_exec(self, env: VerifiedEnvelope, body: dict[str, Any], peer: AgentPeer, ref: CallRef,
                          seq: Any, received_mono: float, received_wall: float) -> None:
        # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-return-statements
        entry = await self.wal.aget(ref.call_id)
        handle = self.executor.get(ref.call_id)
        if (entry is not None and entry.started) or (handle is not None and not handle.done):
            self.gate.done(seq)
            await self._answer_started(entry, ref, peer.ident)
            return
        refuse = self._refuser(ref, peer.ident, env, seq)
        if self._stopping:
            await refuse("DESKTOP_OFFLINE")
            return
        problem = self._body_problem(body)
        if problem:
            await refuse(problem)
            return
        # start_by on the monotonic clock, measured at receipt, with the offset at receipt (K-70)
        deadline = dp.start_deadline(float(env.header["start_by"]), offset=self.clock.offset_s,
                                     wall_now=received_wall, mono_now=received_mono)
        in_order = await self.gate.wait_turn(seq, deadline=deadline)
        try:
            self.clock.check_actuation()
        except DesktopLinkError:
            await refuse("CLOCK_SKEW")
            return
        try:
            dp.check_start_window(deadline, mono_now=self._mono())
        except DesktopLinkError:
            await refuse("START_WINDOW_MISSED", state=wal_mod.STATE_START_WINDOW_MISSED)
            return
        early = self._take_early_cancel(ref.call_id)
        if early is not None:
            await refuse(early)
            return
        spec = await self.provider.describe(ref.tool)
        if spec is None or not dp.digest_equal(spec.param_schema_sha256, body.get("param_schema_sha256")):
            await refuse("MANIFEST_MISMATCH")
            return
        args = body.get("args") or {}
        pending = PendingCall(call_id=ref.call_id, tool=ref.tool, cls=spec.cls, origin=ref.origin, room=ref.room,
                              peer=peer.ident, args=args, args_sha256=str(body.get("args_sha256", "")),
                              approval=body.get("approval") if isinstance(body.get("approval"), Mapping) else None,
                              risk=spec.call_risk(args), spec=spec, job_id=peer.job_id)
        code = await self._policy_code(pending)
        if code is None and spec.needs_input_lock and self.executor.degraded:
            code = "DESKTOP_DEGRADED"
        if code is None:
            code = self._take_early_cancel(ref.call_id)  # a cancel or stop that came during describe or policy
        if code is not None:
            await refuse(code)
            return
        await self._accept_and_run(env, body, pending, ref, peer, in_order)

    async def _policy_code(self, pending: PendingCall) -> str | None:
        try:
            code = self.policy.check(pending)
            if inspect.isawaitable(code):
                code = await code
        except Exception:  # pylint: disable=broad-exception-caught  # a broken policy fails closed
            logger.exception("Desktop policy check failed for %s", pending.tool)
            return "DESKTOP_REJECTED"
        return code if isinstance(code, str) and code else None

    def _body_problem(self, body: Mapping[str, Any]) -> str | None:
        """BAD_ARGS for an exec body the node cannot run (the library already matched args to args_sha256)."""
        args, timeout, lock_wait = body.get("args"), body.get("timeout_s"), body.get("lock_wait_s", 0)
        approval = body.get("approval")
        number = (int, float)
        valid = (isinstance(args, dict) and isinstance(body.get("args_sha256"), str)
                 and isinstance(body.get("param_schema_sha256"), str)
                 and (timeout is None or (isinstance(timeout, number) and not isinstance(timeout, bool)
                                          and 0 < timeout <= TIMEOUT_MAX_S))
                 and isinstance(lock_wait, number) and not isinstance(lock_wait, bool)
                 and 0 <= lock_wait <= LOCK_WAIT_MAX_S
                 and (approval is None or isinstance(approval, dict)))
        return None if valid else "BAD_ARGS"

    def _refuser(self, ref: CallRef, peer_ident: str, env: VerifiedEnvelope | None,
                 seq: Any) -> Callable[..., Awaitable[None]]:
        async def refuse(code: str, *, state: str = wal_mod.STATE_REFUSED) -> None:
            await self._refuse(ref, peer_ident, code, exec_id=env.header.get("id") if env else None, seq=seq,
                               state=state)
        return refuse

    async def _refuse(self, ref: CallRef, peer_ident: str, code: str, *, exec_id: str | None = None,
                      seq: Any = None, state: str = wal_mod.STATE_REFUSED) -> None:
        """The K-70 refusal: WAL first, then the ack {accepted:false, code}; nothing more for this exec."""
        self.gate.done(seq)
        self._policy_call_hook("release_call", ref.call_id)  # cap slots the policy reserved go back, no toast
        written = await self.wal.arefuse(ref.call_id, code=code, state=state, tool=ref.tool, room=ref.room,
                                         origin=ref.origin, peer=peer_ident, exec_id=exec_id, boot_id=self.boot_id)
        if not written:  # it started meanwhile: its real outcome instead
            await self._answer_started(await self.wal.aget(ref.call_id), ref, peer_ident)
            return
        self.audit.record("exec_refused", call_id=ref.call_id, tool=ref.tool, origin=ref.origin, room=ref.room,
                          peer=peer_ident, code=code)
        logger.info("Desktop exec %s refused: %s", ref.tool, code)
        alert = _ALERT_ON_REFUSAL.get(code)
        if alert is not None:
            self.raise_alert(alert, call_id=ref.call_id, tool=ref.tool)
        await self._send(ref, peer_ident, "ack", {"call_id": ref.call_id, "accepted": False, "code": code})

    async def _accept_and_run(self, env: VerifiedEnvelope, body: Mapping[str, Any], pending: PendingCall,
                              ref: CallRef, peer: AgentPeer, in_order: bool) -> None:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        spec = pending.spec
        timeout = float(body.get("timeout_s") or spec.timeout_s)
        call = ExecCall(call_id=ref.call_id, tool=ref.tool, args=pending.args, spec=spec, timeout_s=timeout,
                        lock_wait_s=float(body.get("lock_wait_s") or 0.0), cls=pending.cls, room=ref.room,
                        origin=ref.origin, peer=peer.ident, seq=env.header.get("seq"))
        if not await self.wal.aaccept(ref.call_id, tool=ref.tool, room=ref.room, origin=ref.origin, peer=peer.ident,
                                      exec_id=env.header.get("id"), boot_id=self.boot_id):
            self.gate.done(call.seq)
            self._policy_call_hook("release_call", ref.call_id)
            await self._answer_started(await self.wal.aget(ref.call_id), ref, peer.ident)
            return
        self.gate.done(call.seq)
        # A cancel or stop that came while the WAL recorded the acceptance: the call never starts. It is taken
        # before the start, because the tool loop may run a quick call to its end before a later cancel lands.
        early = self._take_early_cancel(ref.call_id)
        handle = self.executor.start(call, cancel_reason=None if early is None else
                                     ("CANCELLED" if early == "DESKTOP_REJECTED" else early))
        run = _Running(handle=handle, ref=ref, peer=peer.ident, exec_ts=float(env.header["ts"]), risk=pending.risk,
                       cls=pending.cls, last_progress=self._mono())
        self._running[ref.call_id] = run  # no await since the take: a later cancel finds the run here
        self.audit.record("exec_accepted", call_id=ref.call_id, tool=ref.tool, cls=pending.cls, risk=pending.risk,
                          origin=ref.origin, room=ref.room, peer=peer.ident, args_sha256=pending.args_sha256,
                          approval_channel=(pending.approval or {}).get("channel"), in_order=in_order)
        self._status_dirty.set()
        await self._send(ref, peer.ident, "ack", {"call_id": ref.call_id, "accepted": True, "risk": pending.risk,
                                                  "cls": pending.cls})
        outcome = await self.executor.wait(handle)
        if outcome is None or not outcome.started:
            self._policy_call_hook("release_call", ref.call_id)  # it never started: no cap slot, no toast
        if outcome is not None:
            await self._finish(run, outcome)  # a no-op when a cancel already answered for it
        self.executor.forget(ref.call_id)

    def _on_running(self, handle: CallHandle) -> None:
        """Tool loop, right before the tool runs: the WAL says 'running' first (write-ahead), then the policy
        learns that the call really starts (its cap slots stay taken and a capture shows its toast)."""
        self.wal.mark_running(handle.call_id)
        self._policy_call_hook("call_started", handle.call_id)

    def _policy_call_hook(self, name: str, call_id: str) -> None:
        """C-121's policy hears when an accepted call starts (call_started) or will never start (release_call);
        a policy without the hook (BaselinePolicy) is skipped. Never raises."""
        hook = getattr(self.policy, name, None)
        if hook is None:
            return
        try:
            hook(call_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Desktop policy %s failed", name)

    async def _finish(self, run: _Running, outcome: CallOutcome) -> None:
        """The one result of a call: WAL and outbox in one transaction, then the result to its peer. Nothing
        here stops a started call from ending with a result (K-70)."""
        if run.answered:
            return
        run.answered = True
        self._running.pop(run.ref.call_id, None)
        body = self._result_body(run.ref.tool, outcome)
        finished_at = round(self._wall(), 3)
        record = dp.ResultRecord(call_id=run.ref.call_id, tool=run.ref.tool, room=run.ref.room,
                                 origin=run.ref.origin, original_ts=run.exec_ts, finished_at=finished_at,
                                 result=body)
        state = wal_mod.STATE_NEVER_STARTED if outcome.status == NOT_STARTED else wal_mod.STATE_COMPLETED
        try:
            await self.wal.afinish(run.ref.call_id, state=state, status=outcome.status, effect=outcome.effect,
                                   code=outcome.code, record=record, peer=run.peer)
        except Exception:  # pylint: disable=broad-exception-caught  # the agent still gets the result now
            logger.exception("Desktop WAL could not record the end of %s", run.ref.tool)
        self.audit.record("exec_finished", call_id=run.ref.call_id, tool=run.ref.tool, cls=run.cls,
                          status=outcome.status, effect=outcome.effect, code=outcome.code,
                          duration_ms=outcome.duration_ms)
        self._status_dirty.set()
        if not await self._send(run.ref, run.peer, "result", {"call_id": run.ref.call_id, **body}):
            logger.info("Desktop result of %s kept in the outbox (no session with its peer now)", run.ref.tool)

    def _result_body(self, tool: str, outcome: CallOutcome) -> dict[str, Any]:
        """The K-70 result body of an outcome; a tool result that cannot be encoded at all becomes a short error
        result (status and effect stay)."""
        try:
            result = dict(outcome.result)
            # A pc.* operation (C-122) returns its uploaded blobs as a top-level 'blobs' list: K-70 carries them
            # in the result body, beside the K-12 result.
            blobs = result.pop("blobs") if isinstance(result.get("blobs"), list) else ()
            return self._fit(tool, result_body(dataclasses.replace(outcome, result=result), blobs=blobs))
        except Exception as exc:  # pylint: disable=broad-exception-caught  # logged by type only (no tool data)
            logger.error("Desktop result of %s could not be encoded (%s); an error result goes instead", tool,
                         type(exc).__name__)
            return result_body(dataclasses.replace(outcome, result=k12_error(tool, "RESULT_UNENCODABLE",
                                                                              _RESULT_UNENCODABLE)))

    def _fit(self, tool: str, body: dict[str, Any]) -> dict[str, Any]:
        """Keeps a result body encodable and under RESULT_BODY_MAX: a value canonical JSON refuses (NaN, a
        datetime, a lone surrogate) is reduced by json_safe, then data is dropped, then a short error result."""
        try:
            size = len(dp.canonical_json(body))
        except dp.CanonicalJsonError:
            body = json_safe(body)
            size = len(dp.canonical_json(body))
        if size <= RESULT_BODY_MAX:
            return body
        result = dict(body.get("result") or {})
        result.pop("data", None)
        result["message"] = str(result.get("message", ""))[:1000]
        result["truncated"] = True
        smaller = {**body, "result": result}
        if len(dp.canonical_json(smaller)) <= RESULT_BODY_MAX:
            return smaller
        return {**body, "result": k12_error(tool, "RESULT_TOO_LARGE", "Sir, is kaam ka nateeja bohat bara tha.")}

    async def _send(self, ref: CallRef, peer_ident: str, typ: str, body: Mapping[str, Any]) -> bool:
        try:
            return await self.link.send_to_peer(typ, body, peer_ident=peer_ident, ref=ref)
        except DesktopLinkError as exc:
            logger.error("Desktop %s for %s not built (%s)", typ, ref.tool, exc.reason)
            return False

    async def _answer_started(self, entry: wal_mod.WalEntry | None, ref: CallRef, peer_ident: str) -> None:
        """A call the agent was told started: re-ack while it runs, else its result again (result_resend)."""
        run = self._running.get(ref.call_id)
        if run is not None and not run.answered:
            await self._send(ref, peer_ident, "ack", {"call_id": ref.call_id, "accepted": True, "risk": run.risk,
                                                      "cls": run.cls})
            return
        peer = self.link.peer(peer_ident)
        if peer is None:
            return
        outbox = await self.wal.aoutbox_entry(ref.call_id)
        if outbox is not None:
            await self._resend(outbox.record, peer)
            return
        if entry is None:
            return
        # A K-70 result status; a call that never finished in the WAL (interrupted by a crash) has an unknown
        # effect, so it is reported as failed with effect 'possible'.
        status = entry.status if entry.status in RESULT_STATUSES else FAILED
        summary = {"status": status,
                   "result": k12_error(entry.tool or ref.tool, "DESKTOP_RESULT_UNKNOWN", _RESULT_UNKNOWN),
                   "duration_ms": 0, "effect": entry.effect or "possible", "blobs": []}
        record = dp.ResultRecord(call_id=ref.call_id, tool=entry.tool or ref.tool, room=entry.room or ref.room,
                                 origin=entry.origin or ref.origin,
                                 original_ts=float(entry.accepted_at or entry.updated_at),
                                 finished_at=float(entry.finished_at or entry.updated_at), result=summary)
        await self._resend(record, peer)

    async def _resend(self, record: dp.ResultRecord, peer: AgentPeer) -> bool:
        try:
            return await self.link.send_result_resend(record, peer)
        except DesktopLinkError as exc:
            logger.error("result_resend for %s not built (%s)", record.tool, exc.reason)
            return False

    # --- LinkHandler: the rest ------------------------------------------------------------------------------------

    async def handle_cancel(self, env: VerifiedEnvelope, body: dict[str, Any], peer: AgentPeer) -> None:
        call_id = str(env.call_id)
        run = self._running.get(call_id)
        if run is None:
            if call_id in self._pending:  # still being judged: refused, or cancelled before it starts
                self._early_cancel[call_id] = ("DESKTOP_REJECTED", self._mono())
                return
            entry = await self.wal.aget(call_id)
            if entry is not None and entry.started:  # finished already: the real result answers the cancel
                await self._answer_started(entry, CallRef.of(env.header), peer.ident)
            return
        self._cancel_soon(run, "CANCELLED")

    def _cancel_soon(self, run: _Running, reason: str) -> asyncio.Task[Any]:
        """Cancels a running call now; its answer (_cancel_run) follows on its own task, so the link keeps
        reading meanwhile (the next exec, pings, result_acks)."""
        self.executor.cancel(run.ref.call_id, reason)
        return self._track(self._cancel_run(run, reason))

    async def _cancel_run(self, run: _Running, reason: str) -> None:
        """Cancels a call and answers within cancel_answer_s: the real outcome, or 'cancelled'."""
        self.executor.cancel(run.ref.call_id, reason)
        outcome = await self.executor.wait(run.handle, timeout=self.options.cancel_answer_s)
        if outcome is None:
            code = reason if reason in ("STOPPED_BY_USER", "DESKTOP_OFFLINE") else "CANCELLED"
            outcome = CallOutcome(status="cancelled", result=k12_error(run.ref.tool, code, status="cancelled"),
                                  started=run.handle.running, effect="possible" if run.handle.running else "none",
                                  code=code, duration_ms=int((self._mono() - run.handle.created) * 1000))
        await self._finish(run, outcome)

    def _take_early_cancel(self, call_id: str) -> str | None:
        now = self._mono()
        for key in [key for key, (_c, at) in self._early_cancel.items() if now - at > EARLY_CANCEL_TTL_S]:
            del self._early_cancel[key]
        item = self._early_cancel.pop(call_id, None)
        return item[0] if item else None

    def _stop_calls(self, reason: str, *, peer: str | None = None) -> list[asyncio.Task[Any]]:
        """A stop, at once: every call (of one peer when given) still being judged is refused (STOPPED_BY_USER)
        and every running one is cancelled; their answers follow on their own tasks."""
        for call_id, owner in list(self._pending.items()):
            if peer is None or owner == peer:
                self._early_cancel[call_id] = ("STOPPED_BY_USER", self._mono())
        return [self._cancel_soon(run, reason) for run in list(self._running.values())
                if not run.answered and (peer is None or run.peer == peer)]

    async def cancel_all(self, reason: str, *, peer: str | None = None) -> int:
        """Stops every in-flight call (of one peer when given) and waits for their answers (_cancel_run)."""
        tasks = self._stop_calls(reason, peer=peer)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    async def handle_result_ack(self, call_id: str, peer_ident: str) -> None:
        if await self.wal.aack_result(call_id):
            self._status_dirty.set()

    async def handle_control(self, kind: str, body: Mapping[str, Any], source: str) -> None:
        """Tighten-only controls from the bridge (hint) or an agent (signed). A stop reaches every call at once;
        the link does not wait for their answers."""
        self.audit.record("control", kind=kind, source=source)
        if kind == "desktop_stop":
            self.state.tighten("read", source=source)
            self._stop_calls("STOPPED_BY_USER")
        elif kind == "desktop_off":
            self.state.tighten("off", source=source)
            self._stop_calls("STOPPED_BY_USER")
        elif kind == "stop_all":
            self._stop_calls("STOPPED_BY_USER", peer=source)
        elif kind == "link_disabled":
            self._stop_calls("STOPPED_BY_USER")
        elif kind == "active_job":
            self.active_job = dict(body)
        for listener in list(self._control_listeners):
            await _maybe_await(listener(kind, body, source))
        self._status_dirty.set()

    async def handle_sync_request(self, peer: AgentPeer) -> None:
        await self._resend_outbox(peer)

    async def handle_peer(self, peer: AgentPeer) -> None:
        await self._resend_outbox(peer)
        self._status_dirty.set()

    async def handle_hint(self, hint: dp.BridgeHint) -> None:
        self.bridge_hint = {key: hint.body[key] for key in ("brain", "active_job", "desktop_status")
                            if key in hint.body}
        for listener in list(self._hint_listeners):
            await _maybe_await(listener(hint))

    async def handle_link_refusal(self, header: Mapping[str, Any], error: DesktopLinkError) -> None:
        """An authentic exec that failed a receiver check: its real outcome when the WAL holds it as started,
        else the refusal DesktopLinkError.refusal_code allowed (K-70)."""
        ref, sender = CallRef.of(header), str(header.get("from", ""))
        if not ref.call_id or self.link.peer(sender) is None or error.refusal_code is None:
            return
        entry = await self.wal.aget(ref.call_id)
        run = self._running.get(ref.call_id)
        if (entry is not None and entry.started) or run is not None or ref.call_id in self._pending:
            if ref.call_id not in self._pending:
                await self._answer_started(entry, ref, sender)
            return
        await self._refuse(ref, sender, error.refusal_code, exec_id=str(header.get("id") or "") or None)

    def _route(self, record: dp.ResultRecord, original_peer: str) -> AgentPeer | None:
        """Where a kept result goes now: its own peer, else the newest job peer of its room, else svc for a
        scheduler or job call."""
        peers = self.link.peers
        if original_peer in peers:
            return peers[original_peer]
        same_room = [peer for peer in peers.values() if not peer.svc and record.room and peer.room == record.room]
        if same_room:
            return max(same_room, key=lambda peer: peer.since)
        if record.origin in ("scheduler", "job"):
            svc = [peer for peer in peers.values() if peer.svc]
            if svc:
                return max(svc, key=lambda peer: peer.since)
        return None

    async def _resend_outbox(self, peer: AgentPeer) -> int:
        sent = 0
        for entry in await self.wal.aoutbox():
            if self._route(entry.record, entry.peer) is peer and await self._resend(entry.record, peer):
                sent += 1
        if sent:
            logger.info("Desktop outbox: %d result(s) re-sent to %s", sent, peer.ident)
        return sent

    async def _pc_ping(self, _args: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "success", "tool": "pc.ping", "message": "pong",
                "data": {"node_id": self.node_id, "ts": round(self._wall(), 3), "boot_id": self.boot_id}}

    # --- background loops ----------------------------------------------------------------------------------------

    async def _progress_loop(self) -> None:
        while True:
            await asyncio.sleep(self.options.progress_scan_s)
            now = self._mono()
            for run in list(self._running.values()):
                if run.answered or now - run.last_progress < self.options.progress_s:
                    continue
                run.last_progress = now
                await self._send(run.ref, run.peer, "progress", {
                    "call_id": run.ref.call_id, "state": run.handle.phase,
                    "elapsed_ms": int((now - run.handle.created) * 1000)})

    async def _status_loop(self) -> None:
        """run/desktop_node.json every status_every_s, and within a second of a change (at most 1/s)."""
        last = self._mono()
        while True:
            await asyncio.sleep(min(1.0, self.options.status_every_s))
            now = self._mono()
            if (self._status_dirty.is_set() and now - last >= 1.0) or now - last >= self.options.status_every_s:
                self._status_dirty.clear()
                last = now
                await self.write_status()

    async def write_status(self) -> None:
        try:
            self._outbox_size = await self.wal.aoutbox_size()
        except Exception:  # pylint: disable=broad-exception-caught  # the status still goes out
            pass
        await asyncio.to_thread(self.status_file.write, self.status_doc())

    async def _settings_loop(self) -> None:
        while True:
            await asyncio.sleep(self.options.settings_poll_s)
            try:
                self.poll_settings()
            except Exception:  # pylint: disable=broad-exception-caught  # the kill switch poll never stops
                logger.exception("Settings poll failed")

    def poll_settings(self) -> bool:
        """Applies a changed PC root .env: live keys at once; a restart-only key stops the node with
        EXIT_RESTART (the supervisor starts it again). True when something changed."""
        change = self.settings_source.poll()
        if change is None:
            return False
        previous, current = change
        node_settings.apply_live_settings(current, config=self.config)
        self.settings = current
        changed = sorted(name for name in node_settings.ALLOWED_KEYS
                         if previous.values.get(name) != current.values.get(name))
        logger.info("PC root .env changed: %s", ", ".join(changed) or "formatting only")
        self.audit.record("settings_changed", count=len(changed))
        self._status_dirty.set()
        if current.needs_restart(previous):
            logger.warning("A restart-only setting changed; the node restarts")
            self.request_stop(EXIT_RESTART)
        return True

    async def _purge_loop(self) -> None:
        while True:
            await asyncio.sleep(PURGE_EVERY_S)
            try:
                await self.wal.apurge()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("WAL purge failed")

    # --- status ---------------------------------------------------------------------------------------------------

    def status_doc(self) -> dict[str, Any]:
        """run/desktop_node.json and the /health body: no arguments, results, paths, titles or keys."""
        state = self.state.current()
        link = self.link
        doc: dict[str, Any] = {
            "service": "jarvis-desktop-node", "pid": os.getpid(), "boot_id": self.boot_id, "node_id": self.node_id,
            "version": NODE_VERSION, "git_sha": self.git_sha or "unknown", "run_mode": self.run_mode,
            "started_at": round(self.started_at, 3), "link": link.status(), "connects": link.connects,
            "peers": len(link.peers), "svc_peer": link.event_peer("location") is not None,
            "owner_peer": link.event_peer("window_context") is not None, "mode": state.mode,
            "locked": state.locked, "panic": state.panic, "outbox": self._outbox_size,
            "offset_s": None if self.clock.offset_s is None else round(self.clock.offset_s, 3),
            "rtt_ms_p50": self.clock.rtt_ms_p50, "clock_skew": self.clock.skew_exceeded(),
            "last_exec": self.executor.last_finished or None, "desktop_control": self.settings.desktop_control,
            "plugins": dict(self.plugins), "policy": type(self.policy).__name__}
        doc.update(self.executor.stats())
        doc["status"] = "degraded" if self.executor.degraded else "ok"
        return doc

    async def _preload_tools(self) -> None:
        """Loads the laptop tools (their module imports take seconds) before the link connects, so the first
        exec is acked within the agent's 3 s; after PRELOAD_TIMEOUT_S the link starts anyway."""
        for provider in getattr(self.provider, "providers", ()):
            load = getattr(provider, "load", None)
            if not callable(load):
                continue
            started = self._mono()
            try:
                specs = await asyncio.wait_for(asyncio.to_thread(load), timeout=PRELOAD_TIMEOUT_S)
                logger.info("Desktop tools ready: %d in %.1f s", len(specs or ()), self._mono() - started)
            except TimeoutError:
                logger.error("Desktop tools still loading after %.0f s; the link starts anyway", PRELOAD_TIMEOUT_S)
            except Exception:  # pylint: disable=broad-exception-caught  # the node runs; those tools are refused
                logger.exception("Desktop tools failed to load")

    # --- watchdog -------------------------------------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        if self._watchdog_factory is None:
            return
        try:
            self._watchdog = self._watchdog_factory(self)
            self._watchdog.start()
        except Exception:  # pylint: disable=broad-exception-caught  # the node runs without it
            logger.exception("Loop watchdog not started")
            self._watchdog = None

    def _restart_watchdog(self) -> None:
        old = self._watchdog
        if old is None or self._loop is None:
            return

        async def restart() -> None:
            await old.stop()
            self._start_watchdog()

        self._tasks.append(self._loop.create_task(restart(), name="watchdog-reset"))

    def on_stall(self, stall: Any = None) -> None:
        """C-31 critical stall (watcher thread, the loop is still blocked): exit for an immediate restart."""
        logger.critical("Desktop Node event loop stalled for %s ms; exiting for a restart",
                        getattr(stall, "ms", "?"))
        self.audit.record("loop_stall", reason="watchdog")
        self._stall_action(EXIT_STALLED)

    # --- lifecycle ------------------------------------------------------------------------------------------------

    def request_stop(self, code: int = EXIT_OK) -> None:
        """Thread-safe: ends run() with this exit code (the first request wins)."""
        loop, event = self._loop, self._stop_event
        if loop is None or event is None:
            self.exit_code = code
            return

        def stop() -> None:
            if not event.is_set():
                self.exit_code = code
                event.set()

        loop.call_soon_threadsafe(stop)

    async def run(self) -> int:
        """Recovers the WAL, then runs the link and the loops until request_stop()."""
        self._loop = asyncio.get_running_loop()
        self.link.loop = self._loop  # state changes and alerts from other threads reach it from now on
        self._stop_event = asyncio.Event()
        self.tool_loop.start()
        recovered = await asyncio.to_thread(self.wal.recover)
        if any(recovered.values()):
            logger.warning("WAL recovery after an unclean stop: %s", recovered)
        self.audit.record("node_start", boot_id=self.boot_id, node_id=self.node_id, mode=self.state.current().mode,
                          state=self.run_mode)
        await self._preload_tools()  # before the hello: a first exec must not wait for the tool imports
        self._start_watchdog()
        loops: list[tuple[Callable[[], Awaitable[Any]], str]] = [
            (self.link.run, "link"), (self._progress_loop, "progress"), (self._status_loop, "status"),
            (self._settings_loop, "settings"), (self._purge_loop, "purge")]
        loops += [(factory, name) for factory, active_only, name in self._extra_tasks
                  if not (active_only and self.run_mode != "active")]
        self._tasks += [asyncio.create_task(_guarded(factory, name), name=f"node:{name}") for factory, name in loops]
        await self.write_status()
        try:
            await self._stop_event.wait()
        finally:
            await self._shutdown()
        return self.exit_code

    async def _shutdown(self) -> None:
        self._stopping = True
        logger.info("Desktop Node stopping (exit %d)", self.exit_code)
        try:
            await asyncio.wait_for(self.cancel_all("STOPPED_BY_USER"), timeout=self.options.cancel_answer_s + 2.0)
        except TimeoutError:
            logger.warning("Desktop Node stop: some calls did not answer in time")
        for task in list(self._call_tasks):  # their results are in the outbox; nothing waits for a tool now
            task.cancel()
        await asyncio.gather(*self._call_tasks, return_exceptions=True)
        for hook in list(self._shutdown_hooks):
            try:
                await _maybe_await(hook())
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Shutdown hook failed")
        if self._watchdog is not None:
            try:
                await self._watchdog.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        await self.link.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        doc = self.status_doc()
        doc["link"] = "stopped"
        await asyncio.to_thread(self.status_file.write, doc)
        self.audit.record("node_stop", boot_id=self.boot_id, code=str(self.exit_code))

    def close(self) -> None:
        """After run(): stops the tool loop and closes the stores."""
        self.tool_loop.stop()
        self.wal.close()
        self.seen.close()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _guarded(factory: Callable[[], Awaitable[Any]], name: str) -> None:
    try:
        await factory()
    except Exception:  # pylint: disable=broad-exception-caught  # logged; the node keeps running
        logger.exception("Desktop Node task %s ended with an error", name)


# --- process entry -------------------------------------------------------------------------------------------

def _setup_logging(log_file: Path) -> None:
    from services.utils.jarvis_logger import RedactingFormatter  # pylint: disable=import-outside-toplevel

    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("socketio", "engineio", "aiohttp", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _watch_parent(runtime: NodeRuntime) -> None:
    """The supervisor's pipe: a 'stop' line, or its end (the supervisor died), stops the node."""
    stream = sys.stdin

    def watch() -> None:
        try:
            while stream is not None:
                line = stream.readline()
                if not line or line.strip().lower() == "stop":
                    break
        except (OSError, ValueError):
            pass
        runtime.request_stop(EXIT_OK)

    threading.Thread(target=watch, name="desktop-parent", daemon=True).start()


def _git_sha() -> str:
    sha = (os.environ.get("JARVIS_NODE_SHA") or os.environ.get("GIT_SHA") or "").strip()
    if sha:
        return sha[:40]
    from src.core.agent_lock import _git_sha as repo_sha  # pylint: disable=import-outside-toplevel

    return repo_sha()


def build_runtime(*, run_mode: str, root: node_settings.DataRoot, source: node_settings.SettingsSource,
                  cfg: node_settings.NodeConfig, keys: NodeKeys, config: Any) -> NodeRuntime:
    """The production wiring: the real Socket.IO transport, the plugin registry and the C-31 watchdog."""
    from services.utils.jarvis_loopwatch import LoopWatchdog  # pylint: disable=import-outside-toplevel
    from src.core.desktop.executor import RegistryToolProvider  # pylint: disable=import-outside-toplevel
    from src.core.desktop.link import SocketIOTransport  # pylint: disable=import-outside-toplevel

    url, path = node_settings.link_target(source.current.link_url if source.current else
                                          node_settings.DEFAULT_LINK_URL)
    manifest_sha, schema_sha = manifest_digests()

    def watchdog(node: NodeRuntime) -> Any:
        return LoopWatchdog(interval_s=WATCHDOG_INTERVAL_S, threshold_ms=WATCHDOG_THRESHOLD_MS,
                            critical_ms=STALL_RESTART_S * 1000.0, on_critical=node.on_stall,
                            context=lambda: node.executor.stats()["busy_tools"], name="desktop-link")

    return NodeRuntime(
        node_id=default_node_id(cfg.node_id), keys=keys, data_root=root.data_root, run_dir=root.run_dir,
        settings_source=source, transport_factory=lambda: SocketIOTransport(url, path),
        tool_provider=RegistryToolProvider(), owner_room=cfg.owner_room, options=NodeOptions(run_mode=run_mode),
        config=config, git_sha=_git_sha(), manifest_sha256=manifest_sha, param_schema_sha256=schema_sha,
        watchdog_factory=watchdog)


def main(argv: Sequence[str] | None = None) -> int:
    """The node child (see the module docstring). Exit codes: src/core/desktop/__init__.py."""
    parser = argparse.ArgumentParser(prog="python -m src.core.desktop node",
                                     description="JARVIS Desktop Node (C-120); started by its supervisor")
    parser.add_argument("--canary", action="store_true", help="canary run mode: no sensors (C-122)")
    args = parser.parse_args(argv)
    run_mode = "canary" if args.canary else "active"
    node_settings.neutralize_dotenv()  # before any services module that calls load_dotenv is imported
    try:
        root = node_settings.data_root_from_env()
        source = node_settings.SettingsSource(root.env_file, root.data_root)
        current = source.load()  # an unreadable PC root .env: the node never runs without its kill switch
    except node_settings.NodeConfigError as exc:
        print(f"[JARVIS] Desktop Node: {exc}", file=sys.stderr, flush=True)
        return EXIT_CONFIG
    node_settings.apply_environment(current, root, run_mode=run_mode)
    node_settings.EnvGuard().install()
    _setup_logging(root.data_root / node_settings.NODE_LOG_FILE)
    from src.core.desktop.supervisor import is_elevated  # pylint: disable=import-outside-toplevel

    if is_elevated():
        logger.error("Desktop Node refuses an elevated (administrator) token")
        return EXIT_ELEVATED
    try:
        cfg = node_settings.load_node_config()
        keys = KeyLoader(node_settings.node_home()).load(cfg)
    except (node_settings.NodeConfigError, OSError, ValueError) as exc:
        logger.error("Desktop Node keys or configuration unusable: %s", type(exc).__name__)
        return EXIT_CONFIG
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

    node_settings.apply_live_settings(current, config=config)
    for name, attr in (("PINCHTAB_TOKEN", "pinchtab_token"), ("CONTROLLER_TOKEN", "controller_token")):
        if keys.secrets.get(name):
            setattr(config, attr, keys.secrets[name])  # memory only, never os.environ (K-72)
    try:
        runtime = build_runtime(run_mode=run_mode, root=root, source=source, cfg=cfg, keys=keys, config=config)
    except node_settings.NodeConfigError as exc:
        logger.error("Desktop Node not started: %s", exc)
        return EXIT_CONFIG
    from src.core.desktop.link import PowerListener  # pylint: disable=import-outside-toplevel

    power = PowerListener(lambda state: _power_event(runtime, state))
    try:
        install_with_power(runtime, power)
        if os.environ.get("JARVIS_NODE_SUPERVISED") == "1":
            _watch_parent(runtime)
        return asyncio.run(runtime.run())
    except Exception:  # pylint: disable=broad-exception-caught  # logged; the supervisor restarts the node
        logger.exception("Desktop Node crashed")
        return EXIT_FAILED
    finally:
        power.stop()
        runtime.close()


def install_with_power(runtime: NodeRuntime, power: Any, *,
                       import_module: Callable[[str], Any] = importlib.import_module) -> dict[str, str]:
    """main()'s order: the power listener runs first, so an optional module's install(node) finds
    node.power_listener and its hwnd (C-121 registers its session notifications there); then the modules."""
    runtime.power_listener = power
    power.start()
    return runtime.install_plugins(import_module=import_module)


def _power_event(runtime: NodeRuntime, state: str) -> None:
    """PowerListener thread: hands the event to the link loop; waits briefly for suspend, shutdown and logoff
    so the notice leaves before Windows sleeps or ends the session."""
    loop = runtime._loop  # pylint: disable=protected-access
    if loop is None or loop.is_closed():
        return
    future = asyncio.run_coroutine_threadsafe(runtime.link.on_power(state), loop)
    if state != "resume":
        try:
            future.result(timeout=1.8)
        except Exception:  # pylint: disable=broad-exception-caught  # never block the window procedure long
            pass


if __name__ == "__main__":
    sys.exit(main())
