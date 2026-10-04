"""
# src/core/bridge_rooms.py
The LiveKit room owner of the UI bridge (plan task C-23; contracts K-44 v9 C-ROOM-1, K-02/K-38/K-39/K-43 v9).

The bridge in the VPS container (JARVIS_ROLE=bridge) is the only process that creates the JARVIS room and dispatches
its agent. RoomManager keeps one agent of the dispatch target in room JARVIS_ROOM_NAME, never deletes the room (only
the audited admin 'reset-room' below does) and never deletes a dispatch: on LiveKit v1.13.7 DeleteDispatch terminates
every job of that dispatch, a live agent included. The PC's local bridge (vortex.py, another JARVIS_ROLE) has the
manager off and touches no room.

LiveKit server v1.13.7 (read in its source at that tag) and what this module does about it:
- A dispatch with no worker registered under its name creates no job, and nothing launches it later (no re-dispatch
  of a dead worker's job either): the bridge dispatches again, with backoff.
- list_dispatch keeps an ended job as JS_RUNNING (deregisterJob never updates the dispatch copy): a pending/running
  job counts as in flight only until its agent was seen in the room and has left, or its agent reported the job's
  end (notify job_end), and at most 90 s after it started (360 s, the agent's own connect budget, for a job of the PC
  worker that reported itself last, started after the last Wake, whose agent never appeared).
- A room created without an agents list gets an implicit dispatch to agent_name '': while '' is accepted (pc mode),
  creating the room counts as a dispatch in flight, and while paused only a person about to join gets a new room.
- A stopped worker's agent stays listed for about 15-45 s (a ghost): nothing is dispatched beside any listed agent of
  an accepted name, ghosts included, and no agent is removed for being a ghost.

ensure(reason) runs under one asyncio.Lock; the first rule that matches decides (K-44 v9):
 1. a missing room is created (empty_timeout and departure_timeout 3600, max 10 participants, never an agents list);
    while paused only for a token (a person about to join: LiveKit would create it on the join anyway);
 2. observe mode: report only, except the old token route's heal without its delete: the owner's own voice connect
    (token), Wake (start) or the admin CLI's unpause may make one create_dispatch under rules 3, 4, 7 and 8 (no
    confirmation); the ensure routes answer from memory, with no LiveKit call;
 3. paused: no dispatch;  4. an agent of an accepted name is present: no dispatch;
 5. on_demand without demand: none;  6. 'no agent' not yet seen twice 8 s apart (start and unpause skip it): none;
 7. a dispatch in flight (one of an accepted name under 30 s old, the bridge's own marker under 30 s, or a
    pending/running job as above): none;  8. backoff 15, 30, 60, 120, 240, then 300 s (explicit triggers skip it at
    most once per 15 s);  9. create_dispatch to the target, the kv marker written first.
Then the removal checks outside observe mode (one remove_participant at most): an agent still present 20 s after a
pause (at once one that joined after the pause took effect), an agent whose jarvis.session has said 'closed' for 60 s,
an agent of a name that is not accepted once an accepted one has been voice-ready for 10 s, and duplicates of accepted
names present together for 60 s (the one with a connected agent socket is kept). A loop (run) calls ensure every 5 s
while a person or a UI is there and no agent is voice-ready, else every 20 s, and reads the kv mode and paused flag
every 5 s; triggers (kick) wake it at once (a burst of non-explicit kicks runs at most one pass a second), and
concurrent token ensures share one pass.

Pause: only the owner (always or on_demand mode) holds a pause. Its pause_agent carries payload.by 'bridge', and the
agent ends its job only for such a command (K-60 v9), so an older bridge that forwards the UI's own pause_agent ends
nothing. In observe mode the admin CLI's pause is kept and reported (it stops every new dispatch, the PC's
ensure_room.py included) but nothing is sent to an agent until the mode leaves observe.

Logs (logger UI-BRIDGE) carry one 'ROOM ...' line per decision change, dispatch, removal, mode or pause change; never
a person's identity, a key, a token or a worker id. Job and dispatch ids are fine.

Admin CLI (inside the bridge container; the running bridge applies a change within 5 s):
    python -m src.core.bridge_rooms status | mode always|on_demand|observe|clear | pause | unpause
                                   | reset-room --confirm <room>   [--db PATH]
reset-room is the only delete_room call in the code: it needs --confirm with the room's name, writes the audit row
room_reset and cuts every session in the room. Run it only with the owner's OK.
"""
# pylint: disable=too-many-lines  # one owner: settings, observation, the rules, removals, webhooks, status and the CLI

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.utils.jarvis_config import BRAIN_PC, BRAIN_VPS, ROLE_BRIDGE, active_brain, runtime_role
from src.core.bridge_store import (
    KV_PAUSED,
    KV_PC_AGENT,
    KV_ROOM_DISPATCH,
    KV_ROOM_MODE,
    BridgeStore,
    bridge_db_path,
)

# The bridge's own logger name (see bridge_store): its records never travel back through /notify.
logger = logging.getLogger("UI-BRIDGE")

MODE_ALWAYS, MODE_ON_DEMAND, MODE_OBSERVE = "always", "on_demand", "observe"
MODES = (MODE_ALWAYS, MODE_ON_DEMAND, MODE_OBSERVE)
DEFAULT_ROOM = "jarvis-hybrid"
DEFAULT_VPS_AGENT = "jarvis"     # K-72: the VPS brain's lk.agent.name
DEFAULT_PC_AGENT = "jarvis-pc"   # K-72: the PC brain's lk.agent.name from C-23 phase 3

API_TIMEOUT_S = 8.0              # every LiveKit API call (aiohttp total timeout)
ROOM_EMPTY_TIMEOUT_S = 3600
ROOM_DEPARTURE_TIMEOUT_S = 3600
ROOM_MAX_PARTICIPANTS = 10
LOOP_FAST_S = 5.0                # a person or a UI is there and no agent is voice-ready
LOOP_SLOW_S = 20.0
KV_POLL_S = 5.0                  # the admin CLI's mode and pause reach the running bridge within this
KV_WAIT_S = 30.0                 # how long run() waits for the store at start
CONFIRM_S = 8.0                  # 'no agent' must be seen twice, at least this far apart
DISPATCH_FRESH_S = 30.0          # a dispatch (or the bridge's marker) younger than this is in flight
JOB_FRESH_S = 90.0               # a pending/running job younger than this is in flight (v1.13.7 never ends one)
JOB_CONNECT_BUDGET_S = 360.0     # the agent's own budget: 5 connects of up to 60 s, 5-20 s apart (agent_runner)
SEEN_KEEP_S = 900.0              # agents seen and job ends reported are remembered this long (> any in-flight window)
KICK_MIN_S = 1.0                 # non-explicit kicks run at most one pass this often
BACKOFF_S = (15.0, 30.0, 60.0, 120.0, 240.0, 300.0)
BYPASS_EVERY_S = 15.0            # explicit triggers skip the backoff at most this often
DEMAND_WINDOW_S = 600.0          # on_demand: a UI or a want_voice this recent is demand
REPORT_FRESH_S = 600.0           # a PC report this recent wins over an observed agent name
REPORT_REFRESH_S = 60.0          # an unchanged report is written again at most this often
DISPATCH_STATUS_EVERY_S = 60.0   # list_dispatch for the status while an agent is present
RESULT_REUSE_S = 2.0             # ensure(reuse=True), the token route, reuses a result this fresh
PAUSE_REMOVE_S = 20.0
CLOSED_REMOVE_S = 60.0
WRONG_NAME_READY_S = 10.0
DUPLICATE_REMOVE_S = 60.0
WEBHOOK_MAX_BYTES = 64 * 1024
ERROR_LOG_EVERY_S = 60.0
MARKER_MATCH_SLACK_S = 5.0       # an implicit/pending marker matches a dispatch created this close to it

ATTR_AGENT_NAME = "lk.agent.name"
ATTR_SESSION = "jarvis.session"
SESSION_RUNNING, SESSION_CLOSED = "running", "closed"
KIND_STANDARD, KIND_AGENT = 0, 4               # livekit ParticipantInfo.Kind
STATE_ACTIVE, STATE_DISCONNECTED = 2, 3        # livekit ParticipantInfo.State
JOB_PENDING, JOB_RUNNING = 0, 1                # livekit JobStatus
STATE_NAMES = {0: "joining", 1: "joined", 2: "active", 3: "disconnected"}
AGENT_PREFIX = "agent-"
PROBE_PREFIX = "probe-"
REASON_RE = re.compile(r"[a-z_]{1,32}")
WORKER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MARKER_PENDING, MARKER_IMPLICIT = "pending", "implicit"
REASON_TOKEN, REASON_START, REASON_UNPAUSE = "token", "start", "unpause"
# observe mode: only the owner's own triggers may heal (rule 2): a voice connect, Wake, the admin CLI's unpause
OBSERVE_HEAL_REASONS = (REASON_TOKEN, REASON_START, REASON_UNPAUSE)

# ensure() results (K-39 v9)
RESULT_PRESENT = "present"
RESULT_DISPATCHED = "dispatched"
RESULT_IN_FLIGHT = "in_flight"
RESULT_CONFIRMING = "confirming"
RESULT_PAUSED = "paused"
RESULT_BACKOFF = "backoff"
RESULT_IDLE = "idle"
RESULT_OBSERVE = "observe"
RESULT_DISABLED = "disabled"
RESULT_DISPATCH_ERROR = "dispatch_error"
RESTARTING_DECISIONS = (RESULT_IN_FLIGHT, RESULT_DISPATCHED)

# The bridge's own pause_agent (K-38/K-60 v9): payload.by 'bridge' says the bridge holds this pause, and only such a
# command ends the agent's job. The UI's own pause_agent is never forwarded.
PAUSE_BY_BRIDGE = "bridge"
PAUSE_COMMAND: Mapping[str, Any] = {"type": "pause_agent", "payload": {"reason": "paused", "by": PAUSE_BY_BRIDGE}}
PAUSE_UNAVAILABLE = "pause_unavailable"  # ui_command pause_agent's refusal where the bridge cannot hold a pause
ALERT_DUPLICATE = "DUPLICATE_AGENT"
DISABLED_ROLE, DISABLED_NO_KEYS = "role", "no_keys"


class LiveKitUnavailable(Exception):
    """The LiveKit API could not be read (unreachable, timed out, refused): nothing was decided."""


# ---------------------------------------------------------------- settings


def _http_url(ws_url: str) -> str:
    url = (ws_url or "").strip()
    if url.startswith("ws://"):
        return "http://" + url[len("ws://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    return url


_warned_modes: set[str] = set()


def parse_mode(value: Any, *, source: str = "AGENT_DISPATCH_MODE") -> str | None:
    """A dispatch mode, or None for unset; an unknown value gives None and one WARNING (naming the source)."""
    text = value.strip().lower() if isinstance(value, str) else ""
    if not text:
        return None
    if text in MODES:
        return text
    if f"{source}:{text}" not in _warned_modes:
        _warned_modes.add(f"{source}:{text}")
        logger.warning("ROOM %s is not one of %s; it is ignored", source, "/".join(MODES))
    return None


@dataclass(frozen=True)
class RoomSettings:  # pylint: disable=too-many-instance-attributes
    """What the room owner needs, read once at process start (RoomSettings.from_env). role_ok: JARVIS_ROLE=bridge."""

    api_url: str = ""
    key: str = ""
    secret: str = ""
    room: str = DEFAULT_ROOM
    vps_name: str = DEFAULT_VPS_AGENT
    pc_name: str = DEFAULT_PC_AGENT
    brain: str = BRAIN_PC
    env_mode: str | None = None
    role_ok: bool = False

    @property
    def keys_ok(self) -> bool:
        return bool(self.api_url and self.key and self.secret)

    @property
    def enabled(self) -> bool:
        return self.role_ok and self.keys_ok

    @property
    def disabled_reason(self) -> str:
        if not self.role_ok:
            return DISABLED_ROLE
        return "" if self.keys_ok else DISABLED_NO_KEYS

    @property
    def accepted(self) -> frozenset[str]:
        """The lk.agent.names that count as JARVIS here: pc mode '' (automatic dispatch, the PC worker before C-23
        phase 3) and JARVIS_PC_AGENT_NAME; vps mode only JARVIS_AGENT_NAME."""
        return frozenset({self.vps_name}) if self.brain == BRAIN_VPS else frozenset({"", self.pc_name})

    @property
    def default_mode(self) -> str:
        return MODE_ON_DEMAND if self.brain == BRAIN_VPS else MODE_ALWAYS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RoomSettings:
        """The settings from the environment (K-47 v9). LIVEKIT_URL ws/wss becomes http/https."""
        env: Mapping[str, str] = os.environ if environ is None else environ
        if environ is None:
            role_ok, brain = runtime_role() == ROLE_BRIDGE, active_brain()
        else:
            role_ok = (env.get("JARVIS_ROLE") or "").strip().lower() == ROLE_BRIDGE
            brain = BRAIN_VPS if (env.get("JARVIS_BRAIN") or "").strip().lower() == BRAIN_VPS else BRAIN_PC
        return cls(
            api_url=_http_url(env.get("LIVEKIT_URL") or ""),
            key=(env.get("LIVEKIT_API_KEY") or "").strip(),
            secret=(env.get("LIVEKIT_API_SECRET") or "").strip(),
            room=(env.get("JARVIS_ROOM_NAME") or "").strip() or DEFAULT_ROOM,
            vps_name=(env.get("JARVIS_AGENT_NAME") or "").strip() or DEFAULT_VPS_AGENT,
            pc_name=(env.get("JARVIS_PC_AGENT_NAME") or "").strip() or DEFAULT_PC_AGENT,
            brain=brain,
            env_mode=parse_mode(env.get("AGENT_DISPATCH_MODE")),
            role_ok=role_ok,
        )


@dataclass(frozen=True)
class Report:
    """What the PC side says about its worker in an ensure body (K-39 v9): the lk.agent.name it registered and its
    LiveKit worker id (compared, never logged)."""

    agent_name: str | None = None
    worker_id: str | None = None


# ---------------------------------------------------------------- observation


def _epoch_s(value: Any) -> float:
    """A LiveKit timestamp as epoch seconds whatever its unit (v1.13.7 dispatch and job states use nanoseconds)."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0:
        return 0.0
    if number > 1e17:
        return number / 1e9
    if number > 1e14:
        return number / 1e6
    if number > 1e11:
        return number / 1e3
    return number


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _shown(name: str) -> str:
    """An agent name for a log line ('' is automatic dispatch)."""
    return name if name else "''"


@dataclass
class AgentInfo:  # pylint: disable=too-many-instance-attributes
    """One agent participant as listed (identity kept for remove_participant, never logged or reported)."""

    identity: str
    job_id: str
    name: str
    session: str | None
    state: int
    joined_at: float
    accepted: bool
    listed: bool          # state is not DISCONNECTED
    bridge_link: bool     # its job has a connected agent socket

    @property
    def present(self) -> bool:
        return self.listed and self.accepted

    @property
    def voice_ready(self) -> bool:
        return self.present and self.state == STATE_ACTIVE and self.session in (None, SESSION_RUNNING)

    def public(self) -> dict[str, Any]:
        """The status form (K-43 v9): never the identity."""
        return {ATTR_AGENT_NAME: self.name, ATTR_SESSION: self.session,
                "state": STATE_NAMES.get(self.state, str(self.state)), "joined_at": round(self.joined_at, 3) or None,
                "job_id": self.job_id or None, "bridge_link": self.bridge_link}


@dataclass
class JobInfo:
    id: str
    status: int
    started_s: float
    updated_s: float
    participant_identity: str
    worker_id: str


@dataclass
class DispatchInfo:
    id: str
    name: str
    created_s: float
    jobs: list[JobInfo] = field(default_factory=list)


@dataclass
class Snapshot:  # pylint: disable=too-many-instance-attributes
    """One observation of the room."""

    ok: bool = False
    checked_at: float | None = None
    latency_ms: int | None = None
    room_exists: bool = False
    room_sid: str = ""
    agents: list[AgentInfo] = field(default_factory=list)
    people: int = 0
    dispatches: list[DispatchInfo] | None = None
    dispatches_fresh: bool = False   # listed in this observation (not carried over from an earlier one)
    error: str = ""

    @property
    def present(self) -> list[AgentInfo]:
        return [agent for agent in self.agents if agent.present]

    @property
    def voice_ready(self) -> bool:
        return any(agent.voice_ready for agent in self.agents)


def _job_info(job: Any) -> JobInfo:
    state = job.state
    return JobInfo(id=str(job.id or ""), status=int(state.status), started_s=_epoch_s(state.started_at),
                   updated_s=_epoch_s(state.updated_at), participant_identity=str(state.participant_identity or ""),
                   worker_id=str(state.worker_id or ""))


def _dispatch_info(dispatch: Any) -> DispatchInfo:
    state = dispatch.state
    return DispatchInfo(id=str(dispatch.id or ""), name=str(dispatch.agent_name or ""),
                        created_s=_epoch_s(state.created_at), jobs=[_job_info(job) for job in state.jobs])


def is_agent_participant(participant: Any) -> bool:
    """kind AGENT (the enum is a plain int: the old str(kind).endswith('AGENT') test never matched) or an
    'agent-*' identity."""
    try:
        kind = int(getattr(participant, "kind", -1))
    except (TypeError, ValueError):
        kind = -1
    return kind == KIND_AGENT or str(getattr(participant, "identity", "") or "").startswith(AGENT_PREFIX)


def is_person(participant: Any) -> bool:
    """A person in the room: kind STANDARD, not an agent or a probe, not disconnected."""
    identity = str(getattr(participant, "identity", "") or "")
    try:
        kind, state = int(getattr(participant, "kind", -1)), int(getattr(participant, "state", -1))
    except (TypeError, ValueError):
        return False
    return (kind == KIND_STANDARD and state != STATE_DISCONNECTED and not identity.startswith(PROBE_PREFIX)
            and not identity.startswith(AGENT_PREFIX))


def _lk() -> Any:
    """livekit.api, imported when first needed (the bridge image has livekit-api; the CLI imports it lazily too)."""
    from livekit import api  # pylint: disable=import-outside-toplevel
    return api


def default_api_factory(settings: RoomSettings) -> Callable[[], Any]:
    """One LiveKitAPI per manager, created inside the event loop (aiohttp), 8 s per call."""

    def factory() -> Any:
        import aiohttp  # pylint: disable=import-outside-toplevel
        return _lk().LiveKitAPI(settings.api_url, settings.key, settings.secret,
                                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_S))

    return factory


@dataclass
class EnsureResult:  # pylint: disable=too-many-instance-attributes
    """The answer of ensure() and of the three ensure routes (K-39 v9)."""

    room: str
    room_sid: str
    brain: str
    mode: str
    paused: bool
    target_agent_name: str
    result: str
    agent_in_room: bool
    voice_ready: bool
    agent_current_worker: bool | None = None
    dispatch_id: str | None = None
    retry_in_s: float | None = None
    created_room: bool = False
    created_dispatch: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"room": self.room, "room_sid": self.room_sid, "brain": self.brain, "mode": self.mode,
                "paused": self.paused, "target_agent_name": self.target_agent_name, "result": self.result,
                "agent_in_room": self.agent_in_room, "voice_ready": self.voice_ready,
                "agent_current_worker": self.agent_current_worker, "dispatch_id": self.dispatch_id,
                "retry_in_s": None if self.retry_in_s is None else round(self.retry_in_s, 1),
                "created": {"room": self.created_room, "dispatch": self.created_dispatch}}


# ---------------------------------------------------------------- the manager


async def _maybe_await(value: Any) -> Any:
    if asyncio.isfuture(value) or asyncio.iscoroutine(value) or hasattr(value, "__await__"):
        return await value
    return value


Kick = tuple[str, bool, bool]  # (reason, explicit, skip_confirm)


def _merged_kick(pending: Kick | None, new: Kick | None) -> Kick | None:
    """Two kicks as one: the first reason (an explicit one wins over a plain one), explicit and skip_confirm when
    either asked for it."""
    if pending is None or new is None:
        return pending or new
    first_reason, first_explicit, first_skip = pending
    reason, explicit, skip_confirm = new
    keep = reason if explicit and not first_explicit else first_reason
    return keep, first_explicit or explicit, first_skip or skip_confirm


def _job_identities(job: JobInfo) -> set[str]:
    """The participant identities a job's agent may have: its own, and livekit-agents' 'agent-<job id>'."""
    return {identity for identity in (job.participant_identity, f"{AGENT_PREFIX}{job.id}" if job.id else "")
            if identity}


def _retrieve(task: asyncio.Future[Any]) -> None:
    """A shared pass's error is taken here, so asyncio never logs it as lost when every caller gave up."""
    if not task.cancelled():
        task.exception()


class RoomManager:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """The room owner of one bridge process (see the module docstring).

    The callables are looked up on every call (a store the bridge replaces is used): store() -> BridgeStore;
    emit_ui(event, data) to the UI sockets; send_agent_command(data, sid=None) to the owner room's agent sockets;
    agent_job_sockets() -> {job_id: since} of the connected owner-room agent sockets; ui_presence() -> (UI sockets now,
    epoch s of the last UI socket or None); on_status() after every status change (ui_bridge emits agent_status);
    desktop() -> the DesktopLink (vps mode: set_active_job). api_factory() returns a LiveKitAPI-like object (tests
    inject a fake), receiver_factory() a WebhookReceiver; mono and wall are the clocks.
    """

    def __init__(self, settings: RoomSettings, *, store: Callable[[], BridgeStore] | None = None,
                 emit_ui: Callable[[str, Any], Awaitable[Any]] | None = None,
                 send_agent_command: Callable[..., Awaitable[Any]] | None = None,
                 agent_job_sockets: Callable[[], Mapping[str, Any]] | None = None,
                 ui_presence: Callable[[], tuple[int, float | None]] | None = None,
                 on_status: Callable[[], Any] | None = None,
                 desktop: Callable[[], Any] | None = None,
                 api_factory: Callable[[], Any] | None = None,
                 receiver_factory: Callable[[], Any] | None = None,
                 mono: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._store = store
        self._emit_ui_fn = emit_ui
        self._send_agent_command_fn = send_agent_command
        self._job_sockets_fn = agent_job_sockets
        self._ui_presence_fn = ui_presence
        self._on_status_fn = on_status
        self._desktop_fn = desktop
        self._api_factory = api_factory or default_api_factory(settings)
        self._receiver_factory = receiver_factory
        self._mono = mono
        self._wall = wall
        self._api: Any = None
        self._receiver: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._wake: asyncio.Event | None = None
        self._pending_kick: tuple[str, bool, bool] | None = None
        # observation and decisions
        self._snap = Snapshot()
        self._decision = ""
        self._dispatch_listed_at = -math.inf
        self._no_agent_since: float | None = None
        self._failures = 0
        self._next_attempt = -math.inf
        self._last_bypass = -math.inf
        self._paused_seen: dict[str, float] = {}
        self._pause_baseline: set[str] | None = None  # agents listed at the first pass after the pause took effect
        self._closed_since: dict[str, float] = {}
        self._voice_ready_since: float | None = None
        self._dup_since: float | None = None
        self._want_voice_at: float | None = None
        self._last_result: EnsureResult | None = None
        self._last_result_at = -math.inf
        self._shared_pass: asyncio.Future[EnsureResult] | None = None  # the token ensures' one pass
        self._active_job: str | None = None
        self._status_key: Any = None
        # job ends (rule 7): agent identities seen listed, and job ids whose agent reported the end (mono s)
        self._seen_agents: dict[str, float] = {}
        self._ended_jobs: dict[str, float] = {}
        self._woken_at_w = -math.inf  # wall s of the last unpause (Wake)
        self._name_seen_w: dict[str, float] = {}  # lk.agent.name -> wall s an agent of it was last present
        # persisted state (kv)
        self._paused = False
        self._paused_dirty = False
        self._pause_gen = 0  # local pause changes: a kv read older than one never undoes it
        self._mode_override: str | None = None
        self._pc_agent: dict[str, Any] | None = None
        self._marker: dict[str, Any] | None = None
        self._kv_loaded = False
        self._kv_tried_at = -math.inf
        self._kv_polled_at = -math.inf
        self._kv_critical_logged = False
        # rate-limited logs and audit rows
        self._errors_logged_at: dict[str, float] = {}
        self._webhook_audit_at = -math.inf
        self._no_keys_logged = False
        self._dup_warned_at = -math.inf

    # ------------------------------------------------------------ properties

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def disabled_reason(self) -> str:
        return self.settings.disabled_reason

    @property
    def mode(self) -> str:
        return self._mode_override or self.settings.env_mode or self.settings.default_mode

    @property
    def mode_source(self) -> str:
        if self._mode_override:
            return "kv"
        return "env" if self.settings.env_mode else "default"

    @property
    def paused(self) -> bool:
        """The owner paused the agent (kv paused); always False while the manager is off."""
        return self.enabled and self._paused

    @property
    def accepted(self) -> frozenset[str]:
        return self.settings.accepted

    @property
    def target_agent_name(self) -> str:
        """The name the next dispatch goes to (K-44 v9 Target)."""
        return self._dispatch_target(self._snap, self._wall())

    @property
    def last_snapshot(self) -> Snapshot:
        return self._snap

    # ------------------------------------------------------------ loop-bound primitives

    def _primitives(self) -> tuple[asyncio.Lock, asyncio.Event]:
        """The lock and the wake event of the running loop (made again when the loop changed: one bridge process
        has one loop; the tests run one per test)."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._lock is None or self._wake is None:
            self._loop, self._lock, self._wake = loop, asyncio.Lock(), asyncio.Event()
        return self._lock, self._wake

    # ------------------------------------------------------------ triggers

    def kick(self, reason: str, *, explicit: bool = False, skip_confirm: bool = False) -> None:
        """Wakes the loop for an ensure (non-blocking, coalesced). explicit: the trigger may skip the backoff (at most
        once per 15 s); skip_confirm: no 8 s confirmation (start, unpause)."""
        if not self.enabled:
            return
        self._pending_kick = _merged_kick(self._pending_kick, (reason, explicit, skip_confirm))
        try:
            self._primitives()[1].set()
        except RuntimeError:  # no running loop: the next loop pass takes the pending kick
            pass

    def _take_kick(self) -> tuple[str, bool, bool] | None:
        kick, self._pending_kick = self._pending_kick, None
        return kick

    # ------------------------------------------------------------ ensure

    async def ensure(self, reason: str, *, explicit: bool = False, report: Report | None = None,
                     want_voice: bool = False, skip_confirm: bool = False, reuse: bool = False,
                     route: bool = False) -> EnsureResult:
        """One reconcile pass (the rules in the module docstring). Raises LiveKitUnavailable when the room cannot be
        read. reuse (the token route, a non-explicit caller): a result under 2 s old is returned, and concurrent
        callers share one pass. route (the three ensure routes): in observe mode the answer comes from memory, with
        no LiveKit call and no lock."""
        if not self.enabled:
            if self.disabled_reason == DISABLED_NO_KEYS and not self._no_keys_logged:
                self._no_keys_logged = True
                logger.warning("ROOM owner off: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
            return self._result(Snapshot(), RESULT_DISABLED)
        if want_voice:
            self._want_voice_at = self._mono()
        if route:
            await self._ensure_kv()
            if self.mode == MODE_OBSERVE:  # observe changes nothing for a route: the PC uses its LiveKit path
                await self._take_report(report)
                return self._result(self._snap, RESULT_OBSERVE,
                                    current_worker=self._current_worker(self._snap, self._worker_id(report)))
        if reuse and not explicit and report is None:
            if self._last_result is not None and self._mono() - self._last_result_at < RESULT_REUSE_S:
                return self._last_result
            shared = self._shared_pass
            if shared is None or shared.done() or shared.get_loop() is not asyncio.get_running_loop():
                # one pass for every token request that arrives meanwhile
                shared = asyncio.ensure_future(self._locked_pass(reason, explicit=False, report=None,
                                                                 skip_confirm=skip_confirm))
                shared.add_done_callback(_retrieve)
                self._shared_pass = shared
            return await asyncio.shield(shared)
        return await self._locked_pass(reason, explicit=explicit, report=report, skip_confirm=skip_confirm,
                                       route=route)

    async def _locked_pass(self, reason: str, *, explicit: bool, report: Report | None, skip_confirm: bool,
                           route: bool = False) -> EnsureResult:
        lock = self._primitives()[0]
        async with lock:
            result = await self._ensure_locked(reason, explicit=explicit, report=report, skip_confirm=skip_confirm,
                                               route=route)
        self._last_result, self._last_result_at = result, self._mono()
        return result

    def _worker_id(self, report: Report | None) -> str | None:
        """The reported LiveKit worker id (pc mode, well formed), else None."""
        if report is None or self.settings.brain != BRAIN_PC or not isinstance(report.worker_id, str):
            return None
        return report.worker_id if WORKER_ID_RE.fullmatch(report.worker_id) else None

    async def _ensure_locked(self, reason: str, *, explicit: bool, report: Report | None,
                             skip_confirm: bool, route: bool = False) -> EnsureResult:
        await self._ensure_kv()
        await self._take_report(report)
        mode = self.mode
        worker_id = self._worker_id(report)
        snap = await self._observe(need_dispatches=worker_id is not None)
        created_room = fresh_room = False
        if not snap.room_exists and self._may_create(mode, reason, snap):
            await self._create_room(reason)
            created_room = fresh_room = True
            snap = await self._observe(need_dispatches=True)
        heal = mode == MODE_OBSERVE and reason in OBSERVE_HEAL_REASONS and not route
        decision, retry_in, dispatch_id = await self._decide(
            snap, mode, reason, explicit=explicit, skip_confirm=skip_confirm or heal, fresh_room=fresh_room,
            heal=heal)
        if mode != MODE_OBSERVE:
            await self._removals(snap)
        await self._set_active_job(snap)
        self._note_decision(decision, reason, retry_in)
        await self._status_maybe_changed()
        return self._result(snap, decision, current_worker=self._current_worker(snap, worker_id),
                            dispatch_id=dispatch_id, retry_in=retry_in, created_room=created_room,
                            created_dispatch=decision == RESULT_DISPATCHED)

    def _may_create(self, mode: str, reason: str, snap: Snapshot) -> bool:
        """Rule 1: while paused only a token gets a new room (a person about to join; LiveKit would create the room
        on the join anyway, with a 20 s departure timeout), since in pc mode a new room is LiveKit's implicit ''
        dispatch; observe creates a missing room only for a token (the old token route minus the delete);
        on_demand only with demand; always: yes."""
        if self._paused and reason != REASON_TOKEN:
            return False
        if mode == MODE_OBSERVE:
            return reason == REASON_TOKEN
        if mode == MODE_ON_DEMAND:
            return self._demand(snap, reason)
        return True

    # pylint: disable-next=too-many-arguments,too-many-return-statements
    async def _decide(self, snap: Snapshot, mode: str, reason: str, *, explicit: bool, skip_confirm: bool,
                      fresh_room: bool, heal: bool = False) -> tuple[str, float | None, str | None]:
        """Rules 2-9: (decision, retry_in_s, dispatch_id). heal: an observe-mode token, start or unpause (rule 2)."""
        now_m, now_w = self._mono(), self._wall()
        present = snap.present
        self._track_presence(snap, now_m)
        if present:  # pc mode: the name the PC worker registered, seen in any mode (observe included)
            await self._note_observed_name(present, now_w)
        if not snap.room_exists:
            if mode == MODE_OBSERVE:
                return RESULT_OBSERVE, None, None
            return (RESULT_PAUSED if self._paused else RESULT_IDLE), None, None
        if mode == MODE_OBSERVE and not heal:
            return RESULT_OBSERVE, None, None
        if self._paused:
            return RESULT_PAUSED, None, None
        if present:
            return RESULT_PRESENT, None, None
        if mode == MODE_ON_DEMAND and not self._demand(snap, reason):
            return RESULT_IDLE, None, None
        target = self._dispatch_target(snap, now_w)
        confirmed = self._no_agent_since is not None and now_m - self._no_agent_since >= CONFIRM_S
        if not (fresh_room or skip_confirm or confirmed):
            return RESULT_CONFIRMING, None, None
        # A room this pass created has no dispatch but LiveKit's implicit '' one, which cannot reach a worker of
        # another name: only a '' target waits for it (its marker).
        if (not fresh_room or target == "") and self._in_flight(snap, now_w):
            return RESULT_IN_FLIGHT, None, None
        if now_m < self._next_attempt:
            if explicit and now_m - self._last_bypass >= BYPASS_EVERY_S:
                self._last_bypass = now_m
            else:
                return RESULT_BACKOFF, self._next_attempt - now_m, None
        return await self._dispatch(target, reason, now_m, now_w)

    async def _dispatch(self, target: str, reason: str, now_m: float,
                        now_w: float) -> tuple[str, float | None, str | None]:
        """Rule 9: the marker is written first (a call that times out may still have happened), then one
        create_dispatch to the target. Backoff counts every attempt."""
        marker = {"id": MARKER_PENDING, "name": target, "reason": reason, "ts": round(now_w, 3)}
        self._marker = marker
        await self._kv_set(KV_ROOM_DISPATCH, marker)
        self._failures += 1
        self._next_attempt = now_m + BACKOFF_S[min(self._failures - 1, len(BACKOFF_S) - 1)]
        lk = _lk()
        metadata = json.dumps({"by": "bridge", "reason": reason, "ts": round(now_w, 3)}, separators=(",", ":"))
        try:
            created = await self._client().agent_dispatch.create_dispatch(
                lk.CreateAgentDispatchRequest(agent_name=target, room=self.settings.room, metadata=metadata))
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the marker stays: it may have happened
            logger.warning("ROOM dispatch to %s failed (%s); the marker counts as in flight", _shown(target),
                           type(exc).__name__)
            return RESULT_DISPATCH_ERROR, None, None
        dispatch_id = str(getattr(created, "id", "") or "") or None
        if dispatch_id:
            self._marker = {**marker, "id": dispatch_id}
            await self._kv_set(KV_ROOM_DISPATCH, self._marker)
        await self._audit("bridge", "agent_dispatched", {"name": target, "reason": reason, "dispatch_id": dispatch_id})
        logger.info("ROOM dispatched %s (%s)%s", _shown(target), reason, f" {dispatch_id}" if dispatch_id else "")
        return RESULT_DISPATCHED, None, dispatch_id

    def _track_presence(self, snap: Snapshot, now_m: float) -> None:
        """The confirmation clock (rule 6), the voice-ready clock (removal c) and the backoff reset."""
        if snap.present:
            self._no_agent_since = None
        elif self._no_agent_since is None:
            self._no_agent_since = now_m
        if snap.voice_ready:
            if self._voice_ready_since is None:
                self._voice_ready_since = now_m
            self._failures, self._next_attempt = 0, -math.inf
        else:
            self._voice_ready_since = None

    def _demand(self, snap: Snapshot, reason: str) -> bool:
        """on_demand (K-44 v9): a person in the room, a UI socket now or within 600 s, a want_voice within 600 s; a
        token request is a person about to join."""
        if reason == "token" or snap.people > 0:
            return True
        count, last_seen = self._ui_presence()
        if count > 0 or (last_seen is not None and self._wall() - last_seen < DEMAND_WINDOW_S):
            return True
        return self._want_voice_at is not None and self._mono() - self._want_voice_at < DEMAND_WINDOW_S

    def _in_flight(self, snap: Snapshot, now_w: float) -> bool:
        """Rule 7. A dispatch list that could not be read counts as in flight: nothing is added blind."""
        if snap.dispatches is None:
            return True
        marker = self._marker
        if isinstance(marker, dict) and now_w - _epoch_s(marker.get("ts")) < DISPATCH_FRESH_S:
            return True
        listed = {agent.identity for agent in snap.agents if agent.listed}
        for dispatch in snap.dispatches:
            if dispatch.name not in self.accepted:
                continue
            if dispatch.created_s and now_w - dispatch.created_s < DISPATCH_FRESH_S:
                return True
            for job in dispatch.jobs:
                if self._job_live(job, dispatch, listed, now_w):
                    return True
        return False

    def _job_live(self, job: JobInfo, dispatch: DispatchInfo, listed: set[str], now_w: float) -> bool:
        """A pending/running job that may still bring its agent (rule 7). v1.13.7 never updates a job's state, so it
        has ended once its agent was seen in the room and is no longer listed, or once its agent reported the end
        (notify job_end); else its age decides."""
        if job.status not in (JOB_PENDING, JOB_RUNNING):
            return False
        if job.id and job.id in self._ended_jobs:
            return False
        identities = _job_identities(job)
        if identities & self._seen_agents.keys() and not identities & listed:
            return False
        ref = max(job.started_s, job.updated_s) or dispatch.created_s
        return bool(ref) and now_w - ref < self._job_window(job, ref)

    def _job_window(self, job: JobInfo, ref: float) -> float:
        """How long a job whose agent never appeared counts as in flight: JOB_FRESH_S, or the agent's own connect
        budget for a job of the PC worker that reported itself last (pc mode), started after the last Wake (a job of
        the pause was told to end)."""
        reported = self._pc_agent if isinstance(self._pc_agent, dict) else {}
        if (self.settings.brain == BRAIN_PC and job.worker_id and reported.get("source") == "report"
                and reported.get("worker_id") == job.worker_id and ref >= self._woken_at_w):
            return JOB_CONNECT_BUDGET_S
        return JOB_FRESH_S

    def note_job_end(self, job_id: Any) -> bool:
        """An agent reported its job's end (notify job_end, K-40/K-60 v9): that job no longer counts as in flight.
        False for a malformed id or while the manager is off."""
        if not self.enabled or not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            return False
        self._ended_jobs[job_id] = self._mono()
        self.kick("job_end")
        return True

    def _remember_agents(self, snap: Snapshot) -> None:
        """Rule 7's memory: every listed agent identity (its job's agent was seen), and old entries dropped; and when
        a present agent's name was last seen (a name a worker serves, for the target's name switch)."""
        now_m, now_w = self._mono(), self._wall()
        for agent in snap.agents:
            if agent.listed:
                self._seen_agents[agent.identity] = now_m
            if agent.present:
                self._name_seen_w[agent.name] = now_w
        cutoff = now_m - SEEN_KEEP_S
        if any(seen < cutoff for seen in self._seen_agents.values()):
            self._seen_agents = {identity: seen for identity, seen in self._seen_agents.items() if seen >= cutoff}
        if any(ended < cutoff for ended in self._ended_jobs.values()):
            self._ended_jobs = {job_id: ended for job_id, ended in self._ended_jobs.items() if ended >= cutoff}

    def _candidate(self) -> str:
        """vps: JARVIS_AGENT_NAME. pc: the reported or observed name (kv pc_agent), else '' (the name the PC worker
        registers until C-23 phase 3; JARVIS_PC_AGENT_NAME after it, which its ensure_room.py reports)."""
        if self.settings.brain == BRAIN_VPS:
            return self.settings.vps_name
        name = self._pc_agent.get("name") if isinstance(self._pc_agent, dict) else None
        return name if isinstance(name, str) and name in self.accepted else ""

    def _other_pc_name(self, name: str) -> str:
        return self.settings.pc_name if name == "" else ""

    def _marker_dispatch(self, snap: Snapshot) -> DispatchInfo | None:
        """The listed dispatch the bridge's marker stands for: by id, else (implicit or pending) the newest of its name
        created from just before the marker on."""
        marker = self._marker
        if not isinstance(marker, dict) or snap.dispatches is None:
            return None
        marker_id = str(marker.get("id") or "")
        if marker_id and marker_id not in (MARKER_PENDING, MARKER_IMPLICIT):
            return next((d for d in snap.dispatches if d.id == marker_id), None)
        since = _epoch_s(marker.get("ts")) - MARKER_MATCH_SLACK_S
        named = [d for d in snap.dispatches if d.name == marker.get("name") and d.created_s >= since]
        return max(named, key=lambda d: d.created_s) if named else None

    def _dispatch_target(self, snap: Snapshot, now_w: float) -> str:
        """The candidate (the reported or observed name, else ''), or in pc mode the other accepted name when the
        bridge's last dispatch to the candidate got no job within 30 s (no worker under that name): the phase-3 name
        switch can never leave JARVIS silent. A name is never switched away from while the PC worker's fresh report
        says it registered it, or once an agent of that name was present after that dispatch (a worker serves it:
        the missing job was LiveKit's, e.g. its worker cache right after a restart)."""
        candidate = self._candidate()
        if self.settings.brain == BRAIN_VPS:
            return candidate
        marker = self._marker
        if not isinstance(marker, dict) or marker.get("name") != candidate:
            return candidate
        reported = self._pc_agent if isinstance(self._pc_agent, dict) else {}
        if (reported.get("source") == "report" and reported.get("name") == candidate
                and now_w - _epoch_s(reported.get("ts")) < REPORT_FRESH_S):
            return candidate
        dispatch = self._marker_dispatch(snap)
        if (dispatch is not None and not dispatch.jobs and now_w - dispatch.created_s >= DISPATCH_FRESH_S
                and self._name_seen_w.get(candidate, -math.inf) < dispatch.created_s):
            return self._other_pc_name(candidate)
        return candidate

    def _current_worker(self, snap: Snapshot, worker_id: str | None) -> bool | None:
        """agent_current_worker (K-39 v9): an agent participant whose job belongs to the reported worker id; None
        without one (or without a readable dispatch list)."""
        if worker_id is None:
            return None
        if snap.dispatches is None:
            return None
        owners: dict[str, str] = {}
        for dispatch in snap.dispatches:
            for job in dispatch.jobs:
                for identity in _job_identities(job):
                    owners[identity] = job.worker_id
        return any(owners.get(agent.identity) == worker_id for agent in snap.present)

    async def _take_report(self, report: Report | None) -> None:
        """A PC report (pc mode only): its agent name becomes the target (kv pc_agent, source 'report')."""
        if report is None or self.settings.brain != BRAIN_PC or not isinstance(report.agent_name, str):
            return
        name = report.agent_name
        if name not in self.accepted:
            return
        worker_id = report.worker_id if isinstance(report.worker_id, str) and WORKER_ID_RE.fullmatch(
            report.worker_id) else ""
        now_w = self._wall()
        current = self._pc_agent if isinstance(self._pc_agent, dict) else {}
        if (current.get("source") == "report" and current.get("name") == name
                and current.get("worker_id", "") == worker_id
                and now_w - _epoch_s(current.get("ts")) < REPORT_REFRESH_S):
            return
        if current.get("name") != name:
            logger.info("ROOM the PC worker reports agent name %s", _shown(name))
        self._pc_agent = {"name": name, "worker_id": worker_id, "source": "report", "ts": round(now_w, 3)}
        await self._kv_set(KV_PC_AGENT, self._pc_agent)

    async def _note_observed_name(self, present: list[AgentInfo], now_w: float) -> None:
        """pc mode, any dispatch mode (observe included): the lk.agent.name seen on the present agent becomes kv
        pc_agent (source 'observed') unless a report from the last 10 min says otherwise."""
        if self.settings.brain != BRAIN_PC:
            return
        name = self._kept(present).name
        current = self._pc_agent if isinstance(self._pc_agent, dict) else {}
        if current.get("source") == "report" and now_w - _epoch_s(current.get("ts")) < REPORT_FRESH_S:
            return
        if current.get("source") == "observed" and current.get("name") == name:
            return
        self._pc_agent = {"name": name, "worker_id": "", "source": "observed", "ts": round(now_w, 3)}
        await self._kv_set(KV_PC_AGENT, self._pc_agent)

    @staticmethod
    def _kept(agents: list[AgentInfo]) -> AgentInfo:
        """The agent that stays: voice-ready first, then one with an agent socket, then the earliest joined."""
        return min(agents, key=lambda a: (not a.voice_ready, not a.bridge_link, a.joined_at or math.inf))

    # ------------------------------------------------------------ LiveKit calls

    def _client(self) -> Any:
        if self._api is None:
            self._api = self._api_factory()
        return self._api

    async def close(self) -> None:
        """Closes the LiveKit API session (lifespan shutdown)."""
        api, self._api = self._api, None
        if api is not None:
            try:
                await _maybe_await(api.aclose())
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("ROOM LiveKit API did not close cleanly", exc_info=True)

    def _job_sockets(self) -> Mapping[str, Any]:
        try:
            sockets = self._job_sockets_fn() if self._job_sockets_fn is not None else {}
        except Exception:  # pylint: disable=broad-exception-caught
            return {}
        return sockets if isinstance(sockets, Mapping) else {}

    def _ui_presence(self) -> tuple[int, float | None]:
        try:
            count, last_seen = self._ui_presence_fn() if self._ui_presence_fn is not None else (0, None)
        except Exception:  # pylint: disable=broad-exception-caught
            return 0, None
        return int(count or 0), (float(last_seen) if isinstance(last_seen, (int, float)) else None)

    def _agent_info(self, participant: Any, sockets: Mapping[str, Any]) -> AgentInfo:
        attributes = dict(getattr(participant, "attributes", {}) or {})
        name = str(attributes.get(ATTR_AGENT_NAME, "") or "")
        session = attributes.get(ATTR_SESSION) or None
        identity = str(getattr(participant, "identity", "") or "")
        job_id = identity[len(AGENT_PREFIX):] if identity.startswith(AGENT_PREFIX) else ""
        joined_ms = getattr(participant, "joined_at_ms", 0) or 0
        joined = joined_ms / 1000.0 if joined_ms else float(getattr(participant, "joined_at", 0) or 0)
        state = int(getattr(participant, "state", 0) or 0)
        return AgentInfo(identity=identity, job_id=job_id, name=name, session=session, state=state, joined_at=joined,
                         accepted=name in self.accepted, listed=state != STATE_DISCONNECTED,
                         bridge_link=bool(job_id) and job_id in sockets)

    async def _observe(self, *, need_dispatches: bool) -> Snapshot:
        """list_rooms + list_participants; list_dispatch when no agent of an accepted name is present, for the status
        every 60 s, or when asked (agent_current_worker). A failure leaves the last presence as it was."""
        lk = _lk()
        api = self._client()
        started = self._mono()
        try:
            listed = await api.room.list_rooms(lk.ListRoomsRequest(names=[self.settings.room]))
            room = next((r for r in listed.rooms if r.name == self.settings.room), None)
            participants: list[Any] = []
            if room is not None:
                answer = await api.room.list_participants(lk.ListParticipantsRequest(room=self.settings.room))
                participants = list(answer.participants)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # unreachable, timeout, refused
            self._snap.ok, self._snap.error = False, type(exc).__name__
            self._snap.checked_at = round(self._wall(), 3)
            await self._status_maybe_changed()
            raise LiveKitUnavailable(type(exc).__name__) from exc
        sockets = self._job_sockets()
        snap = Snapshot(ok=True, checked_at=round(self._wall(), 3),
                        latency_ms=int(round((self._mono() - started) * 1000)), room_exists=room is not None,
                        room_sid=str(getattr(room, "sid", "") or "") if room is not None else "")
        for participant in participants:
            if is_agent_participant(participant):
                snap.agents.append(self._agent_info(participant, sockets))
            elif is_person(participant):
                snap.people += 1
        self._remember_agents(snap)
        if room is None:
            snap.dispatches, snap.dispatches_fresh = [], True
        elif need_dispatches or not snap.present or \
                self._mono() - self._dispatch_listed_at >= DISPATCH_STATUS_EVERY_S:
            try:
                dispatches = await api.agent_dispatch.list_dispatch(room_name=self.settings.room)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # unreadable: nothing is added blind
                self._log_error("list_dispatch", exc)
                snap.dispatches = None
            else:
                snap.dispatches = [_dispatch_info(d) for d in dispatches if not _epoch_s(d.state.deleted_at)]
                snap.dispatches_fresh = True
                self._dispatch_listed_at = self._mono()
        elif self._snap.dispatches is not None and self._snap.room_sid == snap.room_sid:
            snap.dispatches = self._snap.dispatches  # the status keeps the last list (never used to dispatch)
        self._snap = snap
        return snap

    async def _create_room(self, reason: str) -> None:
        """Rule 1: CreateRoom with no agents list. While '' is accepted the marker comes first, because LiveKit's
        implicit '' dispatch of a new room is a dispatch."""
        lk = _lk()
        if "" in self.accepted:
            self._marker = {"id": MARKER_IMPLICIT, "name": "", "reason": reason, "ts": round(self._wall(), 3)}
            await self._kv_set(KV_ROOM_DISPATCH, self._marker)
        try:
            await self._client().room.create_room(lk.CreateRoomRequest(
                name=self.settings.room, empty_timeout=ROOM_EMPTY_TIMEOUT_S,
                departure_timeout=ROOM_DEPARTURE_TIMEOUT_S, max_participants=ROOM_MAX_PARTICIPANTS))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise LiveKitUnavailable(type(exc).__name__) from exc
        await self._audit("bridge", "room_created", {"reason": reason})
        logger.info("ROOM created %s (%s)", self.settings.room, reason)

    # ------------------------------------------------------------ removals

    async def _removals(self, snap: Snapshot) -> None:
        """At most one remove_participant per pass (K-44 v9): an ignored pause, a closed session, a wrong name, a
        60 s duplicate. Never a ghost for being a ghost."""
        now_m = self._mono()
        listed = [agent for agent in snap.agents if agent.listed]
        identities = {agent.identity for agent in listed}
        self._closed_since = {i: t for i, t in self._closed_since.items() if i in identities}
        self._paused_seen = {i: t for i, t in self._paused_seen.items() if i in identities} if self._paused else {}
        if self._paused and self._pause_baseline is None:
            self._pause_baseline = set(identities)  # these got pause_agent: PAUSE_REMOVE_S to end their own jobs
        for agent in listed:
            if agent.session == SESSION_CLOSED:
                self._closed_since.setdefault(agent.identity, now_m)
            else:
                self._closed_since.pop(agent.identity, None)
            if self._paused:
                self._paused_seen.setdefault(agent.identity, now_m)
        present = snap.present
        if len(present) >= 2:
            if self._dup_since is None:
                self._dup_since = now_m
        else:
            self._dup_since = None
        if self._paused:
            baseline = self._pause_baseline or set()
            for agent in listed:
                # an agent that joined after the pause took effect (a new room's implicit '' dispatch for a person
                # about to join) goes at once, before its session can start listening
                late = agent.identity not in baseline
                if late or now_m - self._paused_seen.get(agent.identity, now_m) >= PAUSE_REMOVE_S:
                    await self._remove(agent, "paused")
                    return
            return
        for agent in listed:
            since = self._closed_since.get(agent.identity)
            if since is not None and now_m - since >= CLOSED_REMOVE_S:
                await self._remove(agent, "session_closed")
                return
        if self._voice_ready_since is not None and now_m - self._voice_ready_since >= WRONG_NAME_READY_S:
            for agent in listed:
                if not agent.accepted:
                    await self._remove(agent, "wrong_name")
                    return
        if self._dup_since is not None and now_m - self._dup_since >= DUPLICATE_REMOVE_S:
            await self._remove_duplicate(present, now_m)

    async def _remove_duplicate(self, present: list[AgentInfo], now_m: float) -> None:
        linked = [agent for agent in present if agent.bridge_link]
        if not linked:
            if now_m - self._dup_warned_at >= ERROR_LOG_EVERY_S:
                self._dup_warned_at = now_m
                logger.warning("ROOM %d agents of accepted names for 60 s and none has an agent socket: none is "
                               "removed (no way to tell the live one)", len(present))
            return
        keep = min(linked, key=lambda a: a.joined_at or math.inf)
        others = [agent for agent in present if agent is not keep]
        victim = max(others, key=lambda a: a.joined_at or 0.0)
        if await self._remove(victim, "duplicate"):
            await self._emit_ui("system_alert", {
                "id": str(uuid.uuid4()), "severity": "warning", "code": ALERT_DUPLICATE,
                "title": "Two JARVIS agents were in the room for a minute; the extra one was removed",
                "count": 1, "ts": round(self._wall(), 3)})

    async def _remove(self, agent: AgentInfo, why: str) -> bool:
        lk = _lk()
        try:
            await self._client().room.remove_participant(
                lk.RoomParticipantIdentity(room=self.settings.room, identity=agent.identity))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._log_error("remove_participant", exc)
            return False
        self._closed_since.pop(agent.identity, None)
        self._paused_seen.pop(agent.identity, None)
        await self._audit("bridge", "agent_removed", {"why": why, "name": agent.name, "job_id": agent.job_id})
        logger.warning("ROOM removed agent %s job %s (%s)", _shown(agent.name), agent.job_id or "-", why)
        return True

    # ------------------------------------------------------------ status

    def _restarting(self) -> bool:
        return not self.paused and not self._snap.voice_ready and self._decision in RESTARTING_DECISIONS

    def presence(self) -> dict[str, Any]:
        """For agent_status (K-02 v9): in_room, voice_ready, session (jarvis.session of the kept agent), restarting.
        A bridge without the room owner (the PC's local bridge) never looks at the room: in_room and voice_ready
        are None (unknown) there, never a false 'absent'."""
        if not self.enabled:
            return {"in_room": None, "voice_ready": None, "session": None, "restarting": False}
        present = self._snap.present  # a failed read keeps the last presence (K-43 v9: livekit.ok says so)
        kept = self._kept(present) if present else None
        return {"in_room": bool(present), "voice_ready": bool(kept and kept.voice_ready),
                "session": kept.session if kept is not None else None, "restarting": self._restarting()}

    def _last_job_state(self) -> str:
        dispatches = self._snap.dispatches
        if not dispatches:
            return "none"
        jobs = [(job, dispatch) for dispatch in dispatches if dispatch.name in self.accepted for job in dispatch.jobs]
        if not jobs:
            return "none"
        job, dispatch = max(jobs, key=lambda pair: max(pair[0].started_s, pair[0].updated_s, pair[1].created_s))
        if _job_identities(job) & {agent.identity for agent in self._snap.present}:
            return "running"
        listed = {agent.identity for agent in self._snap.agents if agent.listed}
        if self._job_live(job, dispatch, listed, self._wall()):
            return "pending" if job.status == JOB_PENDING else "running"
        return "ended_or_stale"

    def snapshot(self) -> dict[str, Any]:
        """The /status livekit block (K-43 v9): the last observation, never an extra LiveKit call; no person's
        identity, key or worker id. Without the room owner the block says enabled false and every observed field
        is None (unknown): this bridge never reads LiveKit."""
        if not self.enabled:
            return {"ok": None, "enabled": False, "latency_ms": None, "checked_at": None, "room_exists": None,
                    "room_sid": None, "participants": None, "agent_in_room": None, "voice_ready": None,
                    "mode": RESULT_DISABLED, "mode_source": self.mode_source, "paused": False,
                    "target_agent_name": self._candidate(), "dispatch_id": None, "last_job_state": None,
                    "decision": None, "agents": []}
        snap = self._snap
        marker_id = self._marker.get("id") if isinstance(self._marker, dict) else None
        return {
            "ok": bool(self.enabled and snap.ok), "enabled": self.enabled, "latency_ms": snap.latency_ms,
            "checked_at": snap.checked_at, "room_exists": snap.room_exists, "room_sid": snap.room_sid or None,
            "participants": snap.people, "agent_in_room": bool(snap.present), "voice_ready": snap.voice_ready,
            "mode": self.mode if self.enabled else RESULT_DISABLED, "mode_source": self.mode_source,
            "paused": self.paused, "target_agent_name": self.target_agent_name,
            "dispatch_id": marker_id if marker_id not in (None, MARKER_PENDING, MARKER_IMPLICIT) else None,
            "last_job_state": self._last_job_state(), "decision": self._decision or None,
            "agents": [agent.public() for agent in snap.agents if agent.listed],
        }

    async def _status_maybe_changed(self) -> None:
        """on_status after a change of what agent_status or /status shows."""
        presence = self.presence()
        key = (self._snap.ok, presence["in_room"], presence["voice_ready"], presence["session"],
               presence["restarting"], self.paused, self.mode)
        if key == self._status_key:
            return
        self._status_key = key
        if self._on_status_fn is not None:
            try:
                await _maybe_await(self._on_status_fn())
            except Exception:  # pylint: disable=broad-exception-caught  # a status emit never stops an ensure
                logger.debug("ROOM status callback failed", exc_info=True)

    def _note_decision(self, decision: str, reason: str, retry_in: float | None) -> None:
        if decision != self._decision:
            extra = f", retry in {retry_in:.0f} s" if retry_in is not None else ""
            logger.info("ROOM %s (%s%s)", decision, reason, extra)
        self._decision = decision

    def _result(self, snap: Snapshot, decision: str, *, current_worker: bool | None = None,  # noqa: PLR0913
                dispatch_id: str | None = None, retry_in: float | None = None, created_room: bool = False,
                created_dispatch: bool = False) -> EnsureResult:
        return EnsureResult(
            room=self.settings.room, room_sid=snap.room_sid, brain=self.settings.brain,
            mode=self.mode if self.enabled else RESULT_DISABLED, paused=self.paused,
            target_agent_name=self._dispatch_target(snap, self._wall()) if self.enabled else self._candidate(),
            result=decision, agent_in_room=bool(snap.present), voice_ready=snap.voice_ready,
            agent_current_worker=current_worker, dispatch_id=dispatch_id, retry_in_s=retry_in,
            created_room=created_room, created_dispatch=created_dispatch)

    async def _set_active_job(self, snap: Snapshot) -> None:
        """vps mode: the kept agent's job id goes to the Desktop Node (K-76 active_job) on change; with no agent
        present it is forgotten (never loosened)."""
        if self.settings.brain != BRAIN_VPS or self._desktop_fn is None:
            return
        present = snap.present
        job_id = self._kept(present).job_id or None if present else None
        if job_id == self._active_job:
            return
        self._active_job = job_id
        try:
            await self._desktop_fn().set_active_job(job_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("ROOM active_job not sent", exc_info=True)

    # ------------------------------------------------------------ pause

    async def set_paused(self, flag: bool, *, by: str, notify_agents: bool = True) -> None:
        """The owner pauses or wakes JARVIS (K-44 v9 Pause): kv paused, audit agent_paused/agent_unpaused {by} and the
        status; pausing sends the bridge's own pause_agent (payload.by 'bridge') to the owner room's agent sockets,
        again on a repeated pause (an agent still there is told once more), never in observe mode; unpausing (and
        every start) runs an explicit ensure without the 8 s confirmation."""
        if not self.enabled:
            return
        if flag != self._paused:
            await self._apply_paused(flag, by=by, persist=True, notify_agents=notify_agents)
        elif flag and notify_agents and self.mode != MODE_OBSERVE:
            await self._send_agent_command(dict(PAUSE_COMMAND))
        if not flag:
            self.kick(REASON_START, explicit=True, skip_confirm=True)
        await self._status_maybe_changed()

    async def _apply_paused(self, flag: bool, *, by: str, persist: bool, notify_agents: bool) -> None:
        """persist: a local change (the UI, the bridge itself), written to kv; a kv read that started before it never
        undoes it (_pause_gen). An admin CLI change read from kv has persist False. In observe mode no agent is told
        (the pause takes effect when the mode leaves observe)."""
        self._paused = flag
        self._paused_seen = {}
        self._pause_baseline = None
        if not flag:
            self._woken_at_w = self._wall()
        if persist:
            self._pause_gen += 1
            self._paused_dirty = not await self._kv_set(KV_PAUSED, flag)
            await self._audit(by, "agent_paused" if flag else "agent_unpaused", {"by": by})
        logger.warning("ROOM %s by %s", "paused" if flag else "unpaused", by)
        if flag and notify_agents and self.mode != MODE_OBSERVE:
            await self._send_agent_command(dict(PAUSE_COMMAND))

    async def _send_agent_command(self, data: Mapping[str, Any], sid: str | None = None) -> None:
        if self._send_agent_command_fn is None:
            return
        try:
            await _maybe_await(self._send_agent_command_fn(dict(data), sid))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("ROOM agent_command not sent", exc_info=True)

    # ------------------------------------------------------------ webhooks

    def _webhook_receiver(self) -> Any:
        if self._receiver is None:
            if self._receiver_factory is not None:
                self._receiver = self._receiver_factory()
            else:
                lk = _lk()
                self._receiver = lk.WebhookReceiver(lk.TokenVerifier(self.settings.key, self.settings.secret))
        return self._receiver

    async def handle_webhook(self, raw: bytes | None, auth: str | None) -> int:
        """POST /internal/livekit/webhook (K-44 v9): 413 over 64 KiB, 401 unless the JWT (sha256 of exactly this body)
        verifies, else 200 at once. An event only wakes the loop: the participant list stays the truth, so a
        replayed event changes nothing."""
        if raw is None or len(raw) > WEBHOOK_MAX_BYTES:
            return 413
        if not self.enabled:
            return 401
        token = (auth or "").strip()
        if token.lower().startswith("bearer "):
            token = token[len("bearer "):].strip()
        try:
            event = self._webhook_receiver().receive(raw.decode("utf-8"), token)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # any failure: not verified
            await self._webhook_refused(type(exc).__name__)
            return 401
        room = getattr(getattr(event, "room", None), "name", "") or ""
        if room != self.settings.room:
            return 200
        kind = str(getattr(event, "event", "") or "")
        participant = getattr(event, "participant", None)
        person = participant is not None and bool(getattr(participant, "identity", "")) and is_person(participant)
        explicit = kind == "participant_joined" and person and self.mode == MODE_ON_DEMAND
        logger.info("ROOM webhook %s", kind or "?")
        self.kick(f"webhook_{kind}" if REASON_RE.fullmatch(f"webhook_{kind}") else "webhook", explicit=explicit)
        return 200

    async def _webhook_refused(self, why: str) -> None:
        now = self._mono()
        if now - self._webhook_audit_at < ERROR_LOG_EVERY_S:
            return
        self._webhook_audit_at = now
        logger.warning("ROOM webhook refused (%s)", why)
        await self._audit("livekit", "webhook_rejected", {"reason": why})

    # ------------------------------------------------------------ the loop

    def _interval(self) -> float:
        count = self._ui_presence()[0]
        someone = self._snap.people > 0 or count > 0
        return LOOP_FAST_S if someone and not self._snap.voice_ready else LOOP_SLOW_S

    async def _wait_wake(self, timeout: float) -> None:
        wake = self._primitives()[1]
        if timeout > 0:
            try:
                await asyncio.wait_for(wake.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        wake.clear()

    async def run(self) -> None:
        """The reconcile loop (a supervised task of the bridge's lifespan): a startup ensure, then one every 5 s
        (someone there, no voice-ready agent) or 20 s, at once on a kick; the kv mode and paused flag every 5 s.
        Every error is caught and logged at most once a minute: the loop never dies."""
        if not self.enabled:
            if self.disabled_reason == DISABLED_NO_KEYS:
                logger.warning("ROOM owner off: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
            else:
                logger.info("ROOM owner off: JARVIS_ROLE is not bridge (this bridge touches no room)")
            return
        self._primitives()
        await self._load_kv(wait_s=KV_WAIT_S)
        logger.info("ROOM owner on: room %s, brain %s, mode %s (%s), accepted %s, target %s", self.settings.room,
                    self.settings.brain, self.mode, self.mode_source,
                    ", ".join(_shown(n) for n in sorted(self.accepted)), _shown(self._candidate()))
        if self._mode_override is not None:  # survives a rollback and a redeploy: say so (RUNBOOK Room ops)
            logger.warning("ROOM the admin CLI's mode %s (kv) overrides AGENT_DISPATCH_MODE=%s; 'python -m "
                           "src.core.bridge_rooms mode clear' returns to it", self._mode_override,
                           self.settings.env_mode or f"unset (default {self.settings.default_mode})")
        first, last_ensure = True, -math.inf
        while True:
            try:
                if self._mono() - self._kv_polled_at >= KV_POLL_S:
                    await self._poll_kv()
                kick = self._take_kick()
                if kick is not None and not first and not kick[1]:
                    # a burst of plain kicks (agent connects and hellos, webhooks) runs at most one pass a second
                    wait = last_ensure + KICK_MIN_S - self._mono()
                    if wait > 0:
                        await asyncio.sleep(min(wait, KICK_MIN_S))
                        kick = _merged_kick(kick, self._take_kick())
                # the interval is judged on every pass (at least every 5 s), so a UI that arrives shortens it at once
                if first or kick is not None or self._mono() >= last_ensure + self._interval():
                    reason, explicit, skip = ("startup", False, False) if first and kick is None else (
                        kick or ("loop", False, False))
                    first = False
                    try:
                        await self.ensure(reason, explicit=explicit, skip_confirm=skip)
                    except LiveKitUnavailable as exc:
                        self._log_error("LiveKit", exc)
                    finally:  # an unexpected error too waits for the next pass, never a tight loop
                        last_ensure = self._mono()
                due = last_ensure + self._interval()
                await self._wait_wake(max(0.0, min(KV_POLL_S, due - self._mono())))
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the loop outlives every error (a
                # cancel is a BaseException: it ends the loop)
                self._log_error("loop", exc, with_traceback=True)
                await asyncio.sleep(1.0)

    def _log_error(self, what: str, exc: BaseException, *, with_traceback: bool = False) -> None:
        now = self._mono()
        if now - self._errors_logged_at.get(what, -math.inf) >= ERROR_LOG_EVERY_S:
            self._errors_logged_at[what] = now
            logger.warning("ROOM %s error: %s (repeats within %.0f s are not logged)", what, type(exc).__name__,
                           ERROR_LOG_EVERY_S, exc_info=with_traceback)

    # ------------------------------------------------------------ kv

    async def _load_kv(self, *, wait_s: float = 0.0) -> bool:
        """The persisted state, once the store answers (run() waits up to 30 s). A paused flag that cannot be read
        counts as not paused (one CRITICAL line); one lost with a corrupt bridge.db (renamed aside) counts as paused
        (fail closed, as the Desktop Link's switch)."""
        attempts = max(1, int(wait_s) + 1)  # one a second, counted (not timed), so a frozen clock cannot hang it
        for attempt in range(attempts):
            self._kv_tried_at = self._mono()
            generation = self._pause_gen
            values = await self._kv_get_many((KV_PAUSED, KV_ROOM_MODE, KV_PC_AGENT, KV_ROOM_DISPATCH))
            if values is not None:
                if self._apply_loaded(values, keep_paused=generation != self._pause_gen):
                    await self._paused_lost()
                return True
            if attempt < attempts - 1:
                await asyncio.sleep(1.0)
        if not self._kv_critical_logged:
            self._kv_critical_logged = True
            logger.critical("ROOM the paused flag cannot be read (bridge store unavailable); it counts as not paused")
        return False

    def _apply_loaded(self, values: Mapping[str, Any], *, keep_paused: bool = False) -> bool:
        """Applies a kv read. keep_paused: a local pause change landed during the read, which is older than it. True
        when the stored paused flag was lost with a corrupt bridge.db (the caller fails closed)."""
        lost = False
        if not (self._paused_dirty or keep_paused):
            if KV_PAUSED in values:
                self._paused = _as_bool(values[KV_PAUSED])
            elif self._corrupt_db():
                lost = True
            else:
                self._paused = False  # never stored
        self._mode_override = parse_mode(values.get(KV_ROOM_MODE), source=KV_ROOM_MODE)
        pc_agent = values.get(KV_PC_AGENT)
        if self._pc_agent is None and isinstance(pc_agent, dict):
            self._pc_agent = dict(pc_agent)
        marker = values.get(KV_ROOM_DISPATCH)
        if self._marker is None and isinstance(marker, dict):
            self._marker = dict(marker)
        self._kv_loaded = True
        self._kv_polled_at = self._mono()
        return lost

    def _corrupt_db(self) -> bool:
        """The store renamed a corrupt bridge.db aside since it opened (BridgeStore.corrupt_renamed)."""
        try:
            return bool(self._store is not None and getattr(self._store(), "corrupt_renamed", 0))
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def _paused_lost(self) -> None:
        """Fail closed: the paused flag was lost with a corrupt bridge.db, so nobody can tell whether the owner had
        paused JARVIS. It counts as paused (kv written again, audit agent_paused {by 'bridge', reason}) until Wake."""
        self._paused = True
        self._paused_seen, self._pause_baseline = {}, None
        self._pause_gen += 1
        self._paused_dirty = not await self._kv_set(KV_PAUSED, True)
        await self._audit("bridge", "agent_paused", {"by": "bridge", "reason": "lost_with_corrupt_db"})
        logger.critical("ROOM the paused flag was lost with a corrupt bridge.db and counts as paused: Wake "
                        "(ui_command start) or 'python -m src.core.bridge_rooms unpause' clears it")

    async def _ensure_kv(self) -> None:
        if not self._kv_loaded and self._mono() - self._kv_tried_at >= KV_POLL_S:
            await self._load_kv()

    async def _poll_kv(self) -> None:
        """Every 5 s: the admin CLI's mode and pause (written in another process) take effect here. A local pause or
        Wake that lands while the read is in flight wins over that older read; a stored flag that is gone (a corrupt
        bridge.db renamed aside) never unpauses: memory is written back."""
        self._kv_polled_at = self._mono()
        if not self._kv_loaded:
            await self._load_kv()
            return
        generation = self._pause_gen
        values = await self._kv_get_many((KV_PAUSED, KV_ROOM_MODE))
        if values is None:
            return
        if self._paused_dirty:
            self._paused_dirty = not await self._kv_set(KV_PAUSED, self._paused)
        elif generation != self._pause_gen:
            pass  # the UI paused or woke JARVIS during the read: this read is older than that change
        elif KV_PAUSED not in values:
            if self._paused:
                self._paused_dirty = not await self._kv_set(KV_PAUSED, True)
        else:
            stored = _as_bool(values[KV_PAUSED])
            if stored != self._paused:
                await self._apply_paused(stored, by="admin_cli", persist=False, notify_agents=True)
                if not stored:
                    self.kick(REASON_UNPAUSE, explicit=True, skip_confirm=True)
        override = parse_mode(values.get(KV_ROOM_MODE), source=KV_ROOM_MODE)
        if override != self._mode_override:
            before = self.mode
            self._mode_override = override
            logger.warning("ROOM mode %s (%s)", self.mode, self.mode_source)
            if self.mode != before:
                if before == MODE_OBSERVE and self._paused:
                    # a pause kept while observing takes effect now: the agents are told (the removals follow)
                    self._paused_seen, self._pause_baseline = {}, None
                    await self._send_agent_command(dict(PAUSE_COMMAND))
                self.kick("mode")
        await self._status_maybe_changed()

    async def _kv_get_many(self, keys: Sequence[str]) -> dict[str, Any] | None:
        if self._store is None:
            return {}
        try:
            return await self._store().kv_get_many(keys)
        except Exception:  # pylint: disable=broad-exception-caught  # a failing store never stops the owner
            logger.debug("ROOM kv read failed", exc_info=True)
            return None

    async def _kv_set(self, key: str, value: Any) -> bool:
        if self._store is None:
            return True
        try:
            await self._store().kv_set(key, value)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("ROOM kv %s not written", key)
            return False
        return True

    async def _audit(self, actor: str, action: str, detail: Mapping[str, Any]) -> None:
        if self._store is None:
            return
        try:
            await self._store().audit(actor, action, dict(detail))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("ROOM audit row %s not written", action)

    async def _emit_ui(self, event: str, data: Any) -> None:
        if self._emit_ui_fn is None:
            return
        try:
            await _maybe_await(self._emit_ui_fn(event, data))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("ROOM UI event %s not sent", event, exc_info=True)


# ---------------------------------------------------------------- request bodies (the ensure routes, K-39 v9)


class EnsureBodyError(ValueError):
    """A refused ensure body: error is 'bad_body' or 'bad_agent_name'."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def parse_ensure_body(raw: bytes, settings: RoomSettings, *, internal: bool) -> tuple[str, Report, bool]:
    """(reason, report, want_voice) from an ensure body: a JSON object {} or {reason?, agent_name?, worker_id?,
    want_voice? (internal route only)}. Raises EnsureBodyError."""
    if not raw.strip():
        data: Any = {}
    else:
        try:
            data = json.loads(raw)
        except ValueError as exc:  # also UnicodeDecodeError
            raise EnsureBodyError("bad_body") from exc
    if not isinstance(data, dict):
        raise EnsureBodyError("bad_body")
    reason = data.get("reason", "ensure")
    if not isinstance(reason, str) or not REASON_RE.fullmatch(reason):
        raise EnsureBodyError("bad_body")
    agent_name = data.get("agent_name")
    if agent_name is not None and (not isinstance(agent_name, str) or agent_name not in ("", settings.pc_name)):
        raise EnsureBodyError("bad_agent_name")
    worker_id = data.get("worker_id")
    if worker_id is not None and (not isinstance(worker_id, str) or not WORKER_ID_RE.fullmatch(worker_id)):
        raise EnsureBodyError("bad_body")
    want_voice = data.get("want_voice", False)
    if not isinstance(want_voice, bool):
        raise EnsureBodyError("bad_body")
    return reason, Report(agent_name=agent_name, worker_id=worker_id), want_voice and internal


# ---------------------------------------------------------------- admin CLI


def _age(ts: Any, now: float) -> str:
    seconds = _epoch_s(ts)
    return f"{max(0.0, now - seconds):.0f} s ago" if seconds else "unknown time"


def status_lines(values: Mapping[str, Any], settings: RoomSettings, *, now: float) -> list[str]:
    """The CLI's report of the persisted room state: names and states only, never an identity, key or worker id."""
    override = parse_mode(values.get(KV_ROOM_MODE), source=KV_ROOM_MODE)
    mode = override or settings.env_mode or settings.default_mode
    source = "kv" if override else ("env" if settings.env_mode else "default")
    pc_agent = values.get(KV_PC_AGENT)
    marker = values.get(KV_ROOM_DISPATCH)
    lines = [
        f"room: {settings.room} (brain {settings.brain}, accepted "
        f"{', '.join(_shown(n) for n in sorted(settings.accepted))})",
        f"mode: {mode} ({source})",
        f"paused: {'yes' if _as_bool(values.get(KV_PAUSED, False)) else 'no'}",
    ]
    if isinstance(pc_agent, dict):
        lines.append(f"pc_agent: {_shown(str(pc_agent.get('name', '')))} (source {pc_agent.get('source', '?')}, worker "
                     f"id known {'yes' if pc_agent.get('worker_id') else 'no'}, {_age(pc_agent.get('ts'), now)})")
    else:
        lines.append("pc_agent: none")
    if isinstance(marker, dict):
        lines.append(f"last dispatch: {_shown(str(marker.get('name', '')))} ({marker.get('reason', '?')}, "
                     f"{marker.get('id', '?')}, {_age(marker.get('ts'), now)})")
    else:
        lines.append("last dispatch: none")
    return lines


async def _cli_live_read(settings: RoomSettings) -> list[str]:
    """One read-only look at the room for 'status' (when the keys are in the environment)."""
    api = default_api_factory(settings)()
    lk = _lk()
    try:
        listed = await api.room.list_rooms(lk.ListRoomsRequest(names=[settings.room]))
        room = next((r for r in listed.rooms if r.name == settings.room), None)
        if room is None:
            return ["live: room does not exist"]
        answer = await api.room.list_participants(lk.ListParticipantsRequest(room=settings.room))
        agents = [p for p in answer.participants if is_agent_participant(p)]
        people = sum(1 for p in answer.participants if not is_agent_participant(p) and is_person(p))
        lines = [f"live: room exists, sid {room.sid}, people {people}, agents {len(agents)}"]
        for participant in agents:
            attributes = dict(participant.attributes)
            lines.append(f"  agent {_shown(attributes.get(ATTR_AGENT_NAME, ''))} session "
                         f"{attributes.get(ATTR_SESSION) or '-'} state "
                         f"{STATE_NAMES.get(int(participant.state), participant.state)}")
        return lines
    finally:
        await api.aclose()


async def reset_room(settings: RoomSettings, store: BridgeStore, *, confirm: str) -> None:
    """The ONLY delete_room call in the code (tests/infrastructure/test_no_room_delete.py): cuts every session in the
    room. --confirm must be the room's name; the audit row room_reset is written first."""
    if confirm != settings.room:
        raise ValueError(f"--confirm must be the room's name ({settings.room})")
    if not settings.keys_ok:
        raise RuntimeError("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
    await store.audit("admin_cli", "room_reset", {"room": settings.room})
    api = default_api_factory(settings)()
    lk = _lk()
    try:
        await api.room.delete_room(lk.DeleteRoomRequest(room=settings.room))
    finally:
        await api.aclose()


async def _cli(args: argparse.Namespace, settings: RoomSettings, path: str) -> list[str]:
    store = BridgeStore(path, repair=False)  # only the bridge itself may rename a damaged database
    try:
        out: list[str] = []
        if args.command == "mode":
            if args.mode == "clear":
                await store.kv_delete(KV_ROOM_MODE)
            else:
                await store.kv_set(KV_ROOM_MODE, args.mode)
            await store.audit("admin_cli", "room_mode", {"mode": args.mode, "by": "admin_cli"})
            out.append(f"room mode {args.mode} written; the running bridge applies it within {KV_POLL_S:.0f} s")
        elif args.command in ("pause", "unpause"):
            flag = args.command == "pause"
            await store.kv_set(KV_PAUSED, flag)
            await store.audit("admin_cli", "agent_paused" if flag else "agent_unpaused", {"by": "admin_cli"})
            out.append(f"{args.command} written; the running bridge applies it within {KV_POLL_S:.0f} s")
        elif args.command == "reset-room":
            await reset_room(settings, store, confirm=args.confirm)
            out.append(f"room {settings.room} deleted: every session in it was cut (audit row room_reset)")
        values = await store.kv_get_many((KV_PAUSED, KV_ROOM_MODE, KV_PC_AGENT, KV_ROOM_DISPATCH))
        out[:0] = status_lines(values, settings, now=time.time())
        if args.command == "status" and settings.keys_ok:
            try:
                out += await _cli_live_read(settings)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # report, never a traceback
                out.append(f"live: LiveKit not readable ({type(exc).__name__})")
        return out
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.core.bridge_rooms",
                                     description="Room owner admin tools of the UI bridge (plan task C-23, K-44 v9).")
    parser.add_argument("--db", default=None, help="bridge database (default: BRIDGE_DB_PATH or <project>/data/bridge.db)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="mode, paused, PC agent name, last dispatch; with keys one live read")
    mode = commands.add_parser("mode", help="override AGENT_DISPATCH_MODE (clear removes the override)")
    mode.add_argument("mode", choices=(*MODES, "clear"))
    commands.add_parser("pause", help="pause JARVIS: no dispatch; outside observe mode the agent ends its job")
    commands.add_parser("unpause", help="wake JARVIS: an explicit ensure follows")
    reset = commands.add_parser("reset-room", help="delete the room (cuts every session; the owner's OK first)")
    reset.add_argument("--confirm", required=True, help="the room's name")
    args = parser.parse_args(argv)
    path = args.db or bridge_db_path()
    if not os.path.isfile(path):
        print(f"no bridge database at {path}", file=sys.stderr)
        return 1
    settings = RoomSettings.from_env()
    try:
        lines = asyncio.run(_cli(args, settings, path))
    except (ValueError, RuntimeError) as exc:  # our own refusals: a fixed text, never a value
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # pylint: disable=broad-exception-caught  # report and exit, never a traceback
        print(f"cannot use {path}: {type(exc).__name__}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
