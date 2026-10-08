"""
# ui_bridge.py
WebSocket server to bridge JARVIS core events to the React-based STONIX UI.

What must survive a restart (chat messages, logs, memories, tool logs, persona, mute, wake word,
location, voice match, SIM results) lives in the SQLite store (src/core/bridge_store.py,
C-STATE-1); BridgeStateManager keeps only volatile flags.

Agent link v2 (C-14, C-SOCK-1 / C-NOTIFY-1): every Socket.IO client gets a role at connect. An agent
(auth.agent_token = AGENT_TOKEN) joins room 'agent'; the legacy shared token joins BOTH rooms while
LEGACY_TOKEN_AUTH=1 (today's broadcast, so the current UI and PC agent keep working); the owner session
cookie jarvis_session (C-19) gives role 'ui' (room 'ui'). ui_command and vision_frame go only to room 'agent',
every notify and periodic event only to room 'ui'. POST /notify is authenticated before it changes anything.

Owner session auth (C-19, C-AUTH-1 / K-36, src/core/bridge_auth.py): POST /auth/login, POST /auth/logout and
GET /auth/me. /livekit/token, /messages, /feedback, /telemetry/production and the media index/file take the
session or, while LEGACY_TOKEN_AUTH=1, the legacy X-Vortex-Token header; /status also takes the session and the
agent's signed headers. A revoked or expired session ends its UI sockets with 'session_expired' {reason}.

VPS brain (C-116, K-38/K-39/K-48 v4): the same app also listens on the internal port 5002 (jarvis-network only,
never published; started when JARVIS_ROLE=bridge). There the token-less agent role {role:'agent', room, job_id},
unsigned agent notifies and /internal/* are accepted, and the legacy token never is; elsewhere /internal/* answers
404. Room 'agent' holds only agent sockets that serve JARVIS_ROOM_NAME (plus legacy sockets), so canary and probe
peers never get ui_command. The Desktop Link relay, namespace '/desktop', lives in src/core/bridge_desktop.py;
desktop_status reaches the UI as an event, in init_state and in GET /status. The public GET /health answers only
{status, version, uptime_s}.

Room owner (C-23, K-44 v9, src/core/bridge_rooms.py): in the VPS bridge (JARVIS_ROLE=bridge) the RoomManager is the
only room owner and dispatcher; no route here deletes a room. GET /livekit/token awaits a shielded ensure('token')
for at most 3 s; POST /livekit/ensure (C-NOTIFY-1 agent headers), POST /internal/livekit/ensure (:5002 only) and the
PC-signed POST /desktop/ensure are explicit ensures (each with its own rate bucket); LiveKit's webhooks reach POST
/internal/livekit/webhook on :5002 only. ui_command pause_agent and start own the paused flag where the room owner
holds pauses (always, on_demand): pause_agent then sends the bridge's own pause_agent and is never forwarded, and is
refused elsewhere (observe mode, the PC's local bridge); stop is only forwarded. The agent's notify job_end tells the
room owner a job is over. agent_status is always the whole K-02 v9 object, built in one place (build_agent_status)
for the event, init_state and GET /status.

Log pipeline (C-64, K-39/K-40 v10): an authenticated /notify passes one of three global rate buckets (critical 50/s,
logs 10/s, others 20/s; over it 429 {error: 'rate_limited', class} with Retry-After), so a log flood never throttles a
transcript. vortex_log_batch items are normalized (bridge_store.normalize_log_batch), stored in one transaction and
emitted as new_log each, with new_logs {items: [], dropped} for the UI's dropped counter; audit_event becomes
audit_update (not stored).
"""
# pylint: disable=too-many-lines  # the bridge app: every HTTP endpoint and Socket.IO handler of one process

import asyncio
import copy
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import sys
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, TypeVar, cast
from urllib.parse import unquote

import socketio
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_config import ROLE_BRIDGE, active_brain, config, runtime_role
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_telemetry import telemetry
from src.core import bridge_auth, bridge_desktop, bridge_media, bridge_rooms, bridge_security, confirm_policy
from src.core.bridge_security import (
    NOTIFY_MAX_BYTES,
    NonceCache,
    NotifyAuthError,
    NotifyRateLimiter,
    TokenBucket,
    same_secret,
)
from src.core.bridge_store import (
    EVENT_LOG,
    EVENT_MEMORY,
    EVENT_TOOL,
    KV_DESKTOP_LINK,
    MESSAGE_PAGE_MAX,
    SEEN_NOTIFY,
    BridgeStore,
    bridge_db_path,
    epoch_seconds,
    event_retention_days,
    log_max_rows,
    normalize_log_batch,
    normalize_message,
    parse_message_cursor,
    ui_message,
)

# Validate on import/startup: only what the bridge itself uses (LiveKit keys, plus the legacy token
# while LEGACY_TOKEN_AUTH is on); names are logged, values never.
config.validate(ROLE_BRIDGE)

logger = setup_logger("UI-BRIDGE")

# Legacy shared token ('' when unset: then nothing matches it).
VORTEX_SECURITY_TOKEN: str = config.security_token or ""
# Browser origins come only from ALLOWED_ORIGINS (K-49); '*' would switch the origin check off.
ALLOWED_ORIGINS: list[str] = [o for o in config.allowed_origins if o != "*"]
if len(ALLOWED_ORIGINS) != len(config.allowed_origins):
    logger.warning("ALLOWED_ORIGINS: '*' ignored; list the allowed origins instead")
logger.info("Allowed origins: %s", ", ".join(ALLOWED_ORIGINS) or "same origin only")
_MISSING_LINK_KEYS = [name for name, value in (("AGENT_TOKEN", config.agent_token),
                                                ("BRIDGE_HMAC_SECRET", config.bridge_hmac_secret))
                      if not value]
if _MISSING_LINK_KEYS:
    logger.warning("Agent link v2 is off until %s %s set; agents use the legacy token (legacy auth %s)",
                   " and ".join(_MISSING_LINK_KEYS), "is" if len(_MISSING_LINK_KEYS) == 1 else "are",
                   "on" if config.legacy_token_auth else "OFF")

sio: socketio.AsyncServer = socketio.AsyncServer(
    async_mode='asgi',
    # None = same origin only; an empty list would turn python-engineio's origin check off.
    cors_allowed_origins=ALLOWED_ORIGINS or None,
    logger=False,
    engineio_logger=False)

# Socket.IO roles and rooms (C-SOCK-1).
ROOM_UI, ROOM_AGENT = "ui", "agent"
ROLE_UI, ROLE_AGENT, ROLE_LEGACY = "ui", "agent", "legacy"
ROLE_ROOMS: dict[str, tuple[str, ...]] = {
    ROLE_UI: (ROOM_UI,),
    ROLE_AGENT: (ROOM_AGENT,),
    ROLE_LEGACY: (ROOM_UI, ROOM_AGENT),
}
# ui_command types the bridge forwards (C-EVT-1 / K-40 plus the dashboard set of DASH-UI-COMMANDS / K-03);
# anything else is answered {status: 'rejected'}.
UI_COMMAND_TYPES = frozenset({
    "chat", "mute", "unmute", "wake_word_toggle", "stop", "start", "persona_change", "clear_memory",
    "open_sim_panel", "sim_lookup", "swarm", "confirm_response", "settings_update", "desktop_stop",
    "notification_ack", "shutdown_agent", "pause_agent", "task_cancel", "tasks_refresh",
    "voice_take_over", "voice_enroll", "voice_enroll_start", "feedback", "job_cancel", "open_job_artifact",
    "request_state",
})
UI_COMMANDS_PER_S = 10.0
VISION_FRAMES_PER_S = 1.0
# agent_hello wakes the room owner's loop: one a second per socket (burst 5), so a looping sender cannot drive
# LiveKit calls (C-23).
AGENT_HELLOS_PER_S = 1.0
AGENT_HELLO_BURST = 5.0
VISION_FRAME_MAX_CHARS = 600 * 1024
_SHORT_TEXT = 64
_ROOM_NAME = re.compile(r"[A-Za-z0-9._:-]{1,64}")  # a LiveKit room as K-70 writes it
PUBLIC_PORT = 5001  # nginx /vortex/ -> 127.0.0.1:5101 -> this port; the internal one is bridge_desktop.INTERNAL_PORT
# C-19 step 7: uvicorn takes the client address and scheme from X-Forwarded-For / X-Forwarded-Proto, but only from a
# peer on loopback or a private network (nginx through Docker's port proxy, the jarvis network). The right-most
# untrusted X-Forwarded-For entry is nginx's own $remote_addr, so a client-sent X-Forwarded-For never becomes the
# address the login limiter counts ('*' would take the left-most, client-controlled entry).
TRUSTED_PROXIES = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
LIVEKIT_TOKEN_TTL_S = 3600         # C-LK-1: a session's LiveKit token
LIVEKIT_LEGACY_TOKEN_TTL_S = 6 * 3600  # the legacy token keeps livekit-api's default TTL until the cut-over
SESSION_WATCH_S = 60.0             # how often UI sockets are checked against the sessions table
GIT_SHA = (os.getenv("GIT_SHA") or "").strip() or "unknown"  # exported by deploy.sh (K-47)
_STARTED_MONO = time.monotonic()


@asynccontextmanager
async def lifespan(_app_instance: FastAPI) -> AsyncGenerator[None, None]:
    """Lifecycle manager for the FastAPI application."""
    # Startup tasks
    store_task = asyncio.create_task(open_store())
    vitals_task = asyncio.create_task(vitals_heartbeat())
    telemetry_task = asyncio.create_task(telemetry_heartbeat())
    speaking_task = asyncio.create_task(user_speaking_monitor())
    retention_task = asyncio.create_task(store_retention_loop())
    session_task = asyncio.create_task(session_watch_loop())
    # In the background, like the store: a long integrity check of bridge.db must never delay /health.
    desktop_task = asyncio.create_task(desktop.start())
    # C-23: the room owner's loop (it waits for the store itself; off unless JARVIS_ROLE=bridge with LiveKit keys)
    rooms_task = asyncio.create_task(rooms.run())
    yield
    # Shutdown tasks
    vitals_task.cancel()
    telemetry_task.cancel()
    speaking_task.cancel()
    retention_task.cancel()
    session_task.cancel()
    rooms_task.cancel()
    await asyncio.gather(rooms_task, return_exceptions=True)
    try:
        await rooms.close()  # the LiveKit API session
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Room owner did not close cleanly", exc_info=True)
    # start() is short (one kv read, the seen store's open): let it finish, so no store call outlives the close
    await asyncio.wait({desktop_task}, timeout=10.0)
    desktop_task.cancel()
    await asyncio.gather(desktop_task, return_exceptions=True)
    try:
        await desktop.stop()  # its last audit rows go out before the store closes
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Desktop link did not stop cleanly", exc_info=True)
    store_task.cancel()
    try:
        await store.close()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Bridge store did not close cleanly", exc_info=True)

app: FastAPI = FastAPI(lifespan=lifespan)


class InternalOnly:  # pylint: disable=too-few-public-methods  # pure ASGI middleware
    """/internal/* exists only on the internal listener (K-39/K-48 v4); anywhere else it is a plain 404, so nothing
    tells a public client the path exists. Pure ASGI, so streamed responses (C-117 blobs) pass untouched."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if (scope.get("type") == "http" and (path == "/internal" or path.startswith("/internal/"))
                and not bridge_desktop.is_internal_scope(scope)):
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        await self.inner(scope, receive, send)


app.add_middleware(InternalOnly)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Vortex-Token", "X-Vortex-Signature"],
)

# POST /notify's rate buckets (K-39 v10, C-64): critical 50/s, logs 10/s, others 20/s, for the whole bridge.
app.state.notify_limiter = NotifyRateLimiter()

app_asgi = socketio.ASGIApp(sio, other_asgi_app=app)

# State Management

_CHAT_IDS_KEPT = 500


class BridgeStateManager:
    """Thread-safe manager of the bridge's volatile state; persisted state lives in `store`."""
    def __init__(self) -> None:
        self.lock: asyncio.Lock = asyncio.Lock()
        self.state: dict[str, Any] = {
            "speaking": False,
            "user_speaking": False,
            "thinking": False,
            "last_transcription": "",
            "vitals": {"cpu": 0, "ram": 0, "disk": 0},
            "telemetry": {"success_rate": 1.0, "avg_latency": 0.5, "status": "HEALTHY"},
            "runner_status": "online",
            "last_runner_contact": time.time(),
            "last_user_speak_pulse": 0.0,
            # Volatile on purpose: nothing ever resets it (the UI closes the panel locally), so
            # persisting it would reopen the SIM panel on every page load forever.
            "sim_panel_open": False,
        }
        # Ids of typed chat commands: their transcriptions default to source 'text' (C-MSG-1).
        self._chat_ids: OrderedDict[str, None] = OrderedDict()
        # time.monotonic() when this process saw the running SIM lookup start (sim_data_loading).
        self.sim_loading_since: float | None = None

    async def get_state(self) -> dict[str, Any]:
        """Provides a safe deepcopy of the state, excluding coroutines or other un-pickleable objects."""
        async with self.lock:
            # We filter out coroutines and other non-serializable objects before deep copying
            # to prevent 'cannot pickle coroutine' errors during State Sync.
            safe_state = {}
            for k, v in self.state.items():
                if asyncio.iscoroutine(v) or hasattr(v, '__await__'):
                    logger.warning("🔍 Found coroutine in state key '%s'. Filtering out.", k)
                    continue
                safe_state[k] = v

            try:
                return copy.deepcopy(safe_state)
            except (TypeError, Exception) as e:
                logger.error("🚨 Deepcopy failure in bridge state: %s", e)
                # Fallback to shallow copy of top-level keys if deepcopy fails
                return safe_state.copy()

    async def update_state(self, key: str, value: Any) -> None:
        """Atomic update with coroutine guard."""
        if asyncio.iscoroutine(value) or hasattr(value, '__await__'):
            logger.error("🛑 CRITICAL: Attempted to store un-awaited coroutine in key '%s'. Discarding.", key)
            return

        async with self.lock:
            self.state[key] = value

    async def update_nested(self, parent_key: str, nested_key: str, value: Any) -> None:
        async with self.lock:
            if parent_key in self.state and isinstance(self.state[parent_key], dict):
                self.state[parent_key][nested_key] = value

    async def append_log(self, key: str, item: Any, limit: int = 30) -> None:
        async with self.lock:
            if key not in self.state:
                self.state[key] = []
            self.state[key].append(item)
            if len(self.state[key]) > limit:
                self.state[key].pop(0)

    async def get_field(self, key: str, default: Any = None) -> Any:
        async with self.lock:
            return self.state.get(key, default)

    def remember_chat_id(self, msg_id: Any) -> None:
        if isinstance(msg_id, str) and msg_id:
            self._chat_ids[msg_id] = None
            self._chat_ids.move_to_end(msg_id)
            while len(self._chat_ids) > _CHAT_IDS_KEPT:
                self._chat_ids.popitem(last=False)

    def is_chat_id(self, msg_id: Any) -> bool:
        return isinstance(msg_id, str) and msg_id in self._chat_ids

bridge_manager: BridgeStateManager = BridgeStateManager()

# SQLite store (C-STATE-1): BRIDGE_DB_PATH, /app/data/bridge.db on the jarvis_bridge_data volume.
store: BridgeStore = BridgeStore(bridge_db_path())

# Desktop Link relay (C-116, K-70/K-74): '/desktop', desktop_status and the persisted link controls. The lambdas look
# store and emit_ui up on every call, so a replaced store is used.
desktop: bridge_desktop.DesktopLink = bridge_desktop.DesktopLink(
    sio=sio, store=lambda: store, emit_ui=lambda event, data: emit_ui(event, data))

# The room owner (C-23, K-44 v9): on only in the VPS bridge (JARVIS_ROLE=bridge, LiveKit keys set); elsewhere every
# ensure answers 'disabled' and nothing touches LiveKit. The lambdas look the helpers up on every call (the tests
# replace the store and the socket table).
# pylint: disable=unnecessary-lambda
rooms: bridge_rooms.RoomManager = bridge_rooms.RoomManager(
    bridge_rooms.RoomSettings.from_env(), store=lambda: store,
    emit_ui=lambda event, data: emit_ui(event, data),
    send_agent_command=lambda data, sid=None: _send_agent_command(data, sid),
    agent_job_sockets=lambda: _agent_job_sockets(),
    ui_presence=lambda: _ui_presence(),
    on_status=lambda: _publish_agent_status(),
    desktop=lambda: desktop)
# pylint: enable=unnecessary-lambda

# The single media path (C-117, K-73 / DASH-C21): the index and files from the read-only jarvis_shared and
# jarvis_outputs mounts, the in-RAM blob inbox, and the newest backup snapshot from jarvis_agent_snapshots. The
# roots are read on every request, so a reconfigured mount is picked up; JARVIS_SNAPSHOTS_DIR is set in compose.
_SNAPSHOTS_DIR = (os.getenv("JARVIS_SNAPSHOTS_DIR") or "/app/snapshots").strip() or "/app/snapshots"
media_relay: bridge_media.MediaRelay = bridge_media.MediaRelay(
    shared_dir=lambda: config.shared_dir, outputs_dir=lambda: config.outputs_dir)
blob_store: bridge_media.BlobStore = bridge_media.BlobStore()
backup_relay: bridge_media.BackupRelay = bridge_media.BackupRelay(snapshots_dir=lambda: _SNAPSHOTS_DIR)
_BLOB_ID = re.compile(r"[0-9a-f]{32}")

# Persisted UI state (store kv table) and the value each key has until the agent reports one.
PERSISTED_DEFAULTS: dict[str, Any] = {
    "active_persona": "jarvis",
    "muted": False,
    "wake_word_active": True,
    "location": {"city": "Detecting...", "lat": 0, "lng": 0},
    "voice_match": 0.0,
    "sim_records": [],
    "sim_loading": False,
    "sim_query_masked": "",
    "sim_status": "idle",
    "sim_message": "",
    # C-79: the provider state of the last sim_panel_open / sim_data_result (K-34 fields; never a number),
    # so a freshly loaded standalone dialog can read it from init_state instead of defaulting blind.
    "sim_provider": "",
    "sim_provider_configured": False,
    "sim_requires_purpose": False,
}
# A SIM lookup answers within the provider's 10 s timeout. A stored loading state that this process
# did not see start, or that is older than this, lost its sim_data_result.
SIM_LOADING_MAX_S = 30.0

# ---------------------------------------------------------------- SIM lookup ui_command (C-79, K-03 / K-34)

SIM_QUERY_MAX_CHARS = 20     # K-34: the legacy string and the dict query
SIM_PURPOSE_MAX_CHARS = 200  # K-34: {query (<= 20 chars), purpose (<= 200 chars)}
SIM_PURPOSE_MIN_CHARS = 5    # jarvis_sim_lookup asks for >= 5 chars with a configured source
SIM_BAD_QUERY_MESSAGE = ("Query 11-digit mobile number (03XXXXXXXXX) ya 13-digit CNIC jaisi honi "
                         "chahiye. Number check kar ke dobara try karein.")
SIM_PURPOSE_LONG_MESSAGE = "Purpose 200 characters ya is se chhota rakhein."
SIM_PURPOSE_REQUIRED_MESSAGE = ("Authorized SIM lookup ke liye purpose kam az kam "
                                "5 characters ka hona chahiye.")
SIM_LOGIN_REQUIRED_MESSAGE = ("Login required: SIM lookup sirf owner ke signed-in dashboard se "
                              "hota hai. Login kar ke dobara try karein.")
# Provider names that mean "no authorized source" (the dialog's own rule); used only when a
# sim_data_result names its provider without a configured flag (the real emitter sends provider
# only), so the stored pair can never go stale after a provider flip (C-79 fix F3).
SIM_UNCONFIGURED_PROVIDERS = frozenset({"", "none", "not_configured", "disabled"})


def _sim_digits(query: str) -> str:
    return "".join(ch for ch in query if ch.isdigit())


def mask_sim_query(query: str) -> str:
    """K-34 masking, the same rule as services/info/jarvis_sim_lookup: the raw number or CNIC never
    reaches a log, an audit row or an emitted refusal."""
    digits = _sim_digits(str(query or ""))
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:3]}{'*' * max(len(digits) - 6, 3)}{digits[-3:]}"


def _sim_query_ok(query: str) -> bool:
    """C-79 / K-34: after normalization an 11-digit 03x mobile number or a 13-digit CNIC."""
    digits = _sim_digits(query)
    return (len(digits) == 11 and digits.startswith("03")) or len(digits) == 13


def parse_sim_lookup(payload: Any, requires_purpose: bool) -> tuple[Any, str, str, str]:
    """(clean payload, query_masked, reason, message) of a ui_command sim_lookup (K-03/K-34): the
    legacy string (<= 20 chars) or {query <= 20 chars, purpose <= 200 chars}, the query an 11-digit
    03x number or a 13-digit CNIC after normalization, and the purpose >= 5 chars when the dialog
    was told the source requires one (sim_panel_open requires_purpose). The clean payload keeps the
    sender's shape; reason '' means valid. The query leaves this function only masked or inside the
    clean payload (which goes to room 'agent' alone)."""
    if isinstance(payload, str):
        query, purpose, legacy = payload.strip(), "", True
    elif isinstance(payload, Mapping):
        raw_query, raw_purpose = payload.get("query"), payload.get("purpose", "")
        if not isinstance(raw_query, str) or not isinstance(raw_purpose, (str, type(None))):
            return None, "", "bad_payload", SIM_BAD_QUERY_MESSAGE
        query, purpose, legacy = raw_query.strip(), (raw_purpose or "").strip(), False
    else:
        return None, "", "bad_payload", SIM_BAD_QUERY_MESSAGE
    masked = mask_sim_query(query)
    if not query or len(query) > SIM_QUERY_MAX_CHARS or not _sim_query_ok(query):
        return None, masked, "bad_payload", SIM_BAD_QUERY_MESSAGE
    if len(purpose) > SIM_PURPOSE_MAX_CHARS:
        return None, masked, "bad_payload", SIM_PURPOSE_LONG_MESSAGE
    if requires_purpose and len(purpose) < SIM_PURPOSE_MIN_CHARS:
        return None, masked, "purpose_required", SIM_PURPOSE_REQUIRED_MESSAGE
    return (query if legacy else {"query": query, "purpose": purpose}), masked, "", ""


def sim_provider_updates(payload: Any) -> dict[str, Any]:
    """The persisted provider state of a sim_panel_open / sim_data_result payload (C-79): provider
    name, configured and requires_purpose, so the standalone dialog gets them from init_state after
    a reload. Only these non-sensitive fields are stored; K-34 masking is untouched."""
    if not isinstance(payload, Mapping):
        return {}
    updates: dict[str, Any] = {}
    provider = payload.get("provider")
    if isinstance(provider, str):
        updates["sim_provider"] = _short(provider)
        # F3: the tool's own sim_data_result carries only 'provider', so configured is derived
        # from the name; an explicit configured bool below wins.
        updates["sim_provider_configured"] = \
            provider.strip().lower() not in SIM_UNCONFIGURED_PROVIDERS
    for name, key in (("configured", "sim_provider_configured"),
                      ("requires_purpose", "sim_requires_purpose")):
        value = payload.get(name)
        if isinstance(value, bool):
            updates[key] = value
    return updates

T = TypeVar("T")
_STORE_ERROR_LOG_INTERVAL_S = 60.0
_store_errors: dict[str, float] = {"last_logged": float("-inf")}


def _log_store_error(action: str) -> None:
    """Logs a failed store call with its traceback, at most once a minute."""
    now = time.monotonic()
    if now - _store_errors["last_logged"] >= _STORE_ERROR_LOG_INTERVAL_S:
        _store_errors["last_logged"] = now
        logger.exception("Bridge store: %s failed; live UI events still go out", action)
    else:
        logger.debug("Bridge store: %s failed again", action)


async def stored(action: str, call: Awaitable[T], fallback: T) -> T:
    """Awaits one store call. A failing database never stops live UI events: the error is
    logged and the fallback returned."""
    try:
        return await call
    except Exception:  # pylint: disable=broad-exception-caught
        _log_store_error(action)
        return fallback


_TRUSTED_PROXY_NETS = tuple(ipaddress.ip_network(net) for net in TRUSTED_PROXIES.split(","))


def _trusted_proxy_peer(peer: str) -> bool:
    """True for a peer in TRUSTED_PROXIES (loopback or a private network: nginx through Docker), the same set
    uvicorn takes X-Forwarded-For from. An IPv4-mapped IPv6 peer counts as its IPv4 address."""
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr in net for net in _TRUSTED_PROXY_NETS)


def _peer_ip(peer: str, real_ip: str | None) -> str:
    """The client address for logs, audit rows and the login limiter: nginx's X-Real-IP when the peer is a
    trusted proxy (TRUSTED_PROXIES), else the peer itself."""
    behind_proxy = bool(peer) and _trusted_proxy_peer(peer)
    real = (real_ip or "").strip()
    return real[:_SHORT_TEXT] if real and behind_proxy else (peer or "unknown")


def _request_ip(request: Request) -> str:
    return _peer_ip(request.client.host if request.client else "", request.headers.get("x-real-ip"))


def _legacy_header_ok(request: Request) -> bool:
    """X-Vortex-Token equals the legacy token, and legacy auth is still on (C-AUTH-1 transition). The internal
    listener never takes the legacy token (K-38 v4)."""
    return (config.legacy_token_auth and not bridge_desktop.is_internal_scope(request.scope)
            and same_secret(request.headers.get("X-Vortex-Token"), VORTEX_SECURITY_TOKEN))


async def emit_ui(event: str, data: Any = None) -> None:
    """Every notify and periodic event goes only to room 'ui' (UI sockets and legacy sockets)."""
    await sio.emit(event, data, to=ROOM_UI)


# Replay protection for signed notifies: in memory for speed, in the store across bridge restarts.
_notify_nonces = NonceCache()


async def _mark_nonce_seen(nonce: str) -> bool:
    """False when the store saw this nonce before (a replay across a restart); a failing store counts
    as unseen, since the in-memory cache has already checked it."""
    return await stored("notify nonce", store.mark_seen(SEEN_NOTIFY, nonce), True)


async def _agent_request_ok(request: Request) -> bool:
    """An agent request (C-AUTH-1): the internal listener (K-38 v4), or the C-NOTIFY-1 signed headers over the raw
    body (b'' for a GET) on the public one; the nonce is spent like a notify's."""
    if bridge_desktop.is_internal_scope(request.scope):
        return True
    if "x-agent-token" not in request.headers:
        return False
    try:
        raw = await bridge_security.read_body(request)
    except NotifyAuthError:
        return False
    if raw is None:
        return False
    return await bridge_auth.verify_agent_headers(request.headers, raw, agent_token=config.agent_token,
                                                  nonces=_notify_nonces, mark_seen=_mark_nonce_seen)


async def _end_session_sockets(session_id: str, reason: str) -> None:
    """Tells the UI sockets of a revoked or expired owner session ('session_expired' {reason}) and closes them."""
    for sid, client in list(_clients.items()):
        if client.role == ROLE_UI and client.session_id == session_id:
            try:
                await sio.emit("session_expired", {"reason": reason}, to=sid)
                await sio.disconnect(sid)
            except (KeyError, ValueError):  # the socket has already gone
                logger.debug("Socket %s left before its session ended", sid)


# The owner login and the session checks (C-19). The lambdas look the store and the helpers up on every call, so
# a replaced one (tests swap the store) is used.
# pylint: disable=unnecessary-lambda
owner_auth: bridge_auth.OwnerAuth = bridge_auth.OwnerAuth(
    lambda: store,
    allowed_origins=lambda: ALLOWED_ORIGINS,
    client_ip=lambda request: _request_ip(request),
    legacy_ok=lambda request: _legacy_header_ok(request),
    agent_ok=lambda request: _agent_request_ok(request),
    is_internal=bridge_desktop.is_internal_scope,
    on_revoke=lambda session_id, reason: _end_session_sockets(session_id, reason))
# pylint: enable=unnecessary-lambda
owner_auth.install(app)


def _report_notify_refusals(limiter: NotifyRateLimiter) -> None:
    """One WARNING per class with unreported 429s, at most once a minute per class (K-39). Called on every
    refused notify and from the vitals loop, so a flood's last window is reported even when no later notify
    arrives (C-64 review COR-2/LS-5)."""
    for refused, count in limiter.refusal_report():
        logger.warning("⚠️ /notify rate limit: %d '%s' notify(s) answered 429 since the last report", count, refused)


def _notify_rate_limited(request: Request, event_type: Any, payload: Any) -> JSONResponse | None:
    """K-39 v10: the notify's class bucket (critical, logs, others); over it 429 {error: 'rate_limited', class} with
    Retry-After. A vortex_log_batch is charged per item it carries (C-64 review SP-3), every other notify costs
    one token. Refusals are logged at most once a minute per class, with their count."""
    limiter: NotifyRateLimiter = request.app.state.notify_limiter
    name, retry_after = limiter.check(event_type, bridge_security.notify_cost(event_type, payload))
    _report_notify_refusals(limiter)
    if retry_after is None:
        return None
    return JSONResponse({"error": "rate_limited", "class": name}, status_code=429,
                        headers=bridge_security.retry_after_header(retry_after))


def _printable(value: Any, limit: int) -> str:
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


# The in-flight log-batch broadcast tasks (strong references so none is garbage-collected mid-emit).
_log_broadcasts: set[asyncio.Task[None]] = set()


async def _broadcast_log_batch(items: list[dict[str, Any]], dropped: int, source: str, now: float) -> None:
    """The socket side of one vortex_log_batch: new_log per item, then the dropped counter. Runs as its own
    task (C-64 review LS-3), so POST /notify never waits for up to 50 serialized emits per UI socket and a log
    flood adds no latency to the voice path's notifies."""
    try:
        for item in items:
            await emit_ui("new_log", item)
        if dropped > 0:
            await emit_ui("new_logs", {"items": [], "dropped": dropped, "source": source, "ts": round(now, 3)})
    except Exception:  # pylint: disable=broad-exception-caught  # a lost broadcast is never worth a traceback
        logger.debug("log batch broadcast failed", exc_info=True)


async def _handle_log_batch(payload: Any) -> dict[str, Any]:
    """vortex_log_batch (K-40 v10): the normalized items stored in one transaction (init_state's logs) and emitted as
    one new_log each; a dropped count above 0 also goes out as new_logs {items: [], dropped, source, ts}, which only
    feeds the UI's dropped counter. The emits run after the HTTP answer (one background task per batch, LS-3)."""
    if not isinstance(payload, Mapping):
        return {"status": "ignored", "reason": "bad_payload"}
    now = time.time()
    items, dropped, source = normalize_log_batch(payload, now)
    if items:
        await stored("log batch", store.append_events(EVENT_LOG, items), 0)
    if items or dropped > 0:
        task = asyncio.create_task(_broadcast_log_batch(items, dropped, source, now))
        _log_broadcasts.add(task)
        task.add_done_callback(_log_broadcasts.discard)
    return {"status": "ok"}


_AUDIT_SEVERITIES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


async def _handle_audit_event(payload: Any) -> None:
    """audit_event (K-40 v10): the agent's mirrored CRITICAL security events, emitted as audit_update {action (<= 64
    chars), detail (<= 300), severity, ts} to room 'ui'; never stored (security.log keeps them)."""
    if not isinstance(payload, Mapping):
        return
    action = _printable(payload.get("action") or "", 64)
    if not action:
        return
    severity = str(payload.get("severity") or "").strip().upper()
    ts = epoch_seconds(payload.get("ts")) or time.time()
    await emit_ui("audit_update", {
        "action": action, "detail": _printable(payload.get("detail") or "", 300),
        "severity": severity if severity in _AUDIT_SEVERITIES else "INFO", "ts": round(ts, 3)})


async def _handle_status_event(payload: Any) -> None:
    """Handle general status events."""
    if payload == "START":
        desktop.note_voice_activity()
    await bridge_manager.update_state("speaking", payload == "START")

    state: dict[str, Any] = await bridge_manager.get_state()
    if state["speaking"]:
        await bridge_manager.update_state("thinking", False)

    await emit_ui("status_change", {
        "speaking": state["speaking"],
        "thinking": state.get("thinking", False),
    })


async def _handle_thinking_event(payload: Any) -> None:
    """Handle thinking state events."""
    is_thinking: bool = payload == "START"
    await bridge_manager.update_state("thinking", is_thinking)
    speaking: bool = cast(bool, await bridge_manager.get_field("speaking", False))
    await emit_ui("status_change", {
        "speaking": speaking,
        "thinking": is_thinking,
    })


async def _handle_transcription_event(payload: Any) -> None:
    """Normalize (C-MSG-1), upsert into the store, emit new_message (first time the id is seen)
    or update_message with the normalized dict, plus the legacy live caption."""
    raw: dict[str, Any] = (
        payload if isinstance(payload, dict) else {"text": "" if payload is None else str(payload)}
    )
    default_source = "text" if bridge_manager.is_chat_id(raw.get("id")) else "voice"
    try:
        status, msg = await store.upsert_message(raw, default_source)
    except Exception:  # pylint: disable=broad-exception-caught
        _log_store_error("message upsert")
        status, msg = "new", ui_message(normalize_message(raw, default_source))

    logger.info("📝 Transcription: role=%s, id=%s (%s)", msg["role"], msg["id"], status)
    if msg.get("source") == "voice":
        desktop.note_voice_activity()

    await emit_ui("new_message" if status == "new" else "update_message", msg)
    await emit_ui("transcription_update", {"text": msg["text"]})


def _internal_notify_body(raw: bytes | None) -> dict[str, Any]:
    """The {type, payload} object of an unsigned notify on the internal listener, with the public path's limits."""
    if raw is None or len(raw) > NOTIFY_MAX_BYTES:
        raise NotifyAuthError(413, "too_large")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise NotifyAuthError(400, "bad_body") from exc
    if not isinstance(data, dict):
        raise NotifyAuthError(400, "bad_body")
    return data


# ------------------------------------------------------------------ C-58 (K-31): job progress routing
JOBS_MAX = 20
_JOB_STATUS = {"queued": "queued", "running": "running", "start": "running", "success": "done", "done": "done",
               "error": "error", "failed": "error", "timeout": "error", "cancelled": "cancelled",
               "canceled": "cancelled"}
_jobs: "OrderedDict[str, dict[str, Any]]" = OrderedDict()  # job_id -> its newest tool_progress event (newest last)


def job_progress_event(payload: Any) -> dict[str, Any] | None:
    """K-31: the tool_progress event {job_id, tool, title, status, percent, message, artifact?, ts} of a job tool_sync
    (id, action, details, progress, timestamp) or of a direct tool_progress; None without a usable job id. success
    maps to done; a percent is clamped to 0..100, anything else is null."""
    if not isinstance(payload, Mapping):
        return None
    job_id = payload.get("job_id", payload.get("id"))
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    status = _JOB_STATUS.get(str(payload.get("status") or "running").strip().lower(), "running")
    percent = payload.get("percent", payload.get("progress"))
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        percent = None
    else:
        percent = max(0.0, min(100.0, float(percent)))
    ts = payload.get("ts", payload.get("timestamp"))
    event: dict[str, Any] = {
        "job_id": _printable(job_id.strip(), 64),
        "tool": _printable(payload.get("tool") or "job", 64),
        "title": _printable(payload.get("title") or payload.get("action") or payload.get("details") or "", 200),
        "status": status,
        "percent": percent,
        "message": _printable(payload.get("message") or payload.get("details") or "", 500),
        "ts": float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else time.time(),
    }
    artifact = payload.get("artifact")
    if isinstance(artifact, Mapping):
        clean = {key: _printable(artifact[key], 500) for key in ("kind", "path", "name")
                 if isinstance(artifact.get(key), str)}
        if clean:
            event["artifact"] = clean
    return event


def remember_job(event: dict[str, Any]) -> dict[str, Any]:
    """Upserts state.jobs (K-31, newest JOBS_MAX): a field the event leaves empty keeps its earlier value (a progress
    row without a title, an end row without a percent), so the UI and init_state always get the whole job. Returns
    the merged event."""
    previous = _jobs.pop(event["job_id"], {})
    merged = {**previous, **{key: value for key, value in event.items() if value not in (None, "")}}
    for key in ("title", "message"):
        merged.setdefault(key, "")
    merged.setdefault("percent", None)
    _jobs[event["job_id"]] = merged
    while len(_jobs) > JOBS_MAX:
        _jobs.popitem(last=False)
    return dict(merged)


def jobs_newest_first() -> list[dict[str, Any]]:
    """init_state.jobs (K-31): the remembered jobs, newest first."""
    return [dict(job) for job in reversed(_jobs.values())]


async def _route_job_event(payload: Any) -> None:
    event = job_progress_event(payload)
    if event is not None:
        await emit_ui("tool_progress", remember_job(event))


@app.post("/notify")
async def notify_bridge(request: Request) -> Any:
    """
    Main notification endpoint for JARVIS updates (C-NOTIFY-1).
    The raw body is authenticated first (agent HMAC v2, or the legacy headers while
    LEGACY_TOKEN_AUTH=1); only then does the runner contact count and the event reach room 'ui'.
    """
    internal = bridge_desktop.is_internal_scope(request.scope)
    try:
        raw = await bridge_security.read_body(request)
        if internal and "x-agent-token" not in request.headers:
            # K-39 v4: on the internal listener the jarvis network is the agent's credential; the legacy token
            # is never taken there.
            if "x-vortex-token" in request.headers:
                raise NotifyAuthError(401, "bad_token")
            data: dict[str, Any] = _internal_notify_body(raw)
        else:
            auth = await bridge_security.verify_notify_request(
                request, raw, agent_token=config.agent_token, legacy_token=VORTEX_SECURITY_TOKEN,
                legacy_enabled=config.legacy_token_auth and not internal, nonces=_notify_nonces,
                mark_seen=_mark_nonce_seen)
            data = auth.data
    except NotifyAuthError as exc:
        logger.warning("🚫 /notify refused (%d %s) from %s", exc.status, exc.error, _request_ip(request))
        return JSONResponse({"error": exc.error}, status_code=exc.status, headers=exc.headers)

    health_monitor.record_heartbeat("ui_bridge")
    await bridge_manager.update_state("last_runner_contact", time.time())

    raw_type: Any = data.get("type")
    event_type: str | None = raw_type if isinstance(raw_type, str) else None
    refused = _notify_rate_limited(request, event_type, data.get("payload"))
    if refused is not None:
        return refused
    if event_type is None:
        return {"status": "ignored", "reason": "missing_type"}
    payload: Any = data.get("payload")

    async def _handle_token(p: dict[str, Any]) -> None:
        await emit_ui("token_stream", p)

    async def _handle_vitals_event(p: Any) -> None:  # vitals_pc, and the legacy 'vitals' handled as it (K-40)
        await _accept_agent_pc_vitals(p)

    async def _handle_reasoning(p: dict[str, Any]) -> None:
        await emit_ui("reasoning_update", p)

    async def _handle_intelligence(p: dict[str, Any]) -> None:
        await emit_ui("intelligence_update", p)

    async def _handle_job_end(p: Any) -> None:
        # C-23 (K-40/K-60 v9): the agent's job ended (paused, failed, ended): the room owner stops counting it as
        # in flight. Never emitted to the UI.
        job_id = p.get("job_id") if isinstance(p, dict) else None
        if rooms.note_job_end(job_id):
            reason = p.get("reason") if isinstance(p.get("reason"), str) else ""
            logger.info("ROOM job %s ended (%s)", job_id, _short(reason) or "-")

    handlers: dict[str, Any] = {
        "status": _handle_status_event,
        "thinking": _handle_thinking_event,
        "transcription": _handle_transcription_event,
        "token": _handle_token,
        "vitals": _handle_vitals_event,
        "vitals_pc": _handle_vitals_event,
        "reasoning": _handle_reasoning,
        "intelligence_sync": _handle_intelligence,
        "intelligence_update": _handle_intelligence,
        "job_end": _handle_job_end,
        "confirm_resolved": _handle_confirm_resolved,
    }

    if event_type == "confirm_request":  # K-14: the agent's broker reads the answer (ui_clients)
        return await _handle_confirm_request(payload)
    handler: Any | None = handlers.get(event_type)
    if handler:
        await handler(payload)
    elif event_type == "location_sync":
        await stored("location", store.kv_set("location", payload), None)
        await emit_ui("location_update", payload)
    elif event_type == "mute_sync":
        await stored("mute", store.kv_set("muted", payload), None)
        await emit_ui("mute_update", payload)
    elif event_type == "wake_word_sync":
        await stored("wake word", store.kv_set("wake_word_active", payload), None)
        await emit_ui("wake_word_update", payload)
    elif event_type == "vortex_log":
        await stored("log", store.append_event(EVENT_LOG, payload), 0)
        await emit_ui("new_log", payload)
    elif event_type == "vortex_log_batch":
        return await _handle_log_batch(payload)
    elif event_type == "audit_event":
        await _handle_audit_event(payload)
    elif event_type == "user_speaking_sync":
        await bridge_manager.update_state("user_speaking", payload)
        if payload:
            desktop.note_voice_activity()
            await bridge_manager.update_state("last_user_speak_pulse", time.time())
        await emit_ui("user_status_change", {"speaking": payload})
    elif event_type == "voice_id_sync":
        confidence: float = payload.get("confidence", 0.0) if isinstance(payload, dict) else 0.0
        await stored("voice match", store.kv_set("voice_match", confidence), None)
        await emit_ui("voice_match_update", payload)
    elif event_type == "persona_change":
        await stored("persona", store.kv_set("active_persona", payload), None)
        await emit_ui("persona_update", {"persona": payload})
    elif event_type == "task_alert":
        # Proactive task notification for the UI
        logger.info("🔔 Notifying UI of due task: %s", payload.get('description'))
        await emit_ui("task_notification", payload)
    elif event_type == "task_update":
        # General task list sync
        await emit_ui("task_sync", payload)
    elif event_type == "memory_update":
        await stored("memory", store.append_event(EVENT_MEMORY, payload), 0)
        await emit_ui("memory_sync", payload)
    elif event_type == "tool_sync":
        await stored("tool log", store.append_event(EVENT_TOOL, payload), 0)
        await emit_ui("tool_update", payload)
        # C-58 (K-31): a job event (job: true, every phase) or any progress row becomes a Jobs-panel tool_progress
        # and is remembered for init_state.jobs
        if isinstance(payload, Mapping) and (payload.get("job") is True or payload.get("phase") == "progress"):
            await _route_job_event(payload)
    elif event_type == "tool_progress":  # K-31: a direct one of the same shape is accepted too
        await _route_job_event(payload)
    elif event_type == "turn_rejected":  # C-44 (K-62): a voice turn the agent did not answer, and why
        await emit_ui("turn_rejected", payload)
    elif event_type == "voice_id_status":  # C-44 (K-63): the last status also reaches a late UI through init_state
        await bridge_manager.update_state("voice_id_status", payload)
        await emit_ui("voice_id_status", payload)
    elif event_type == "voice_enroll_result":  # C-44 (K-63)
        await emit_ui("voice_enroll_result", payload)
    elif event_type == "memory_cleared":  # C-44 (K-58): the bridge forgets its stored short-term memories too
        await stored("memory clear", store.delete_events(EVENT_MEMORY), 0)
        await emit_ui("memory_cleared", payload)
    elif event_type == "sim_data_result":
        records: list[Any] = payload.get("records", []) if isinstance(payload, dict) else []
        query_masked: str = payload.get("phone", "") if isinstance(payload, dict) else ""
        status: str = payload.get("status", "success") if isinstance(payload, dict) else "success"
        message: str = payload.get("message", "") if isinstance(payload, dict) else ""
        bridge_manager.sim_loading_since = None
        await stored("sim result", store.kv_set_many({
            "sim_records": records,
            "sim_loading": False,
            "sim_query_masked": query_masked,
            "sim_status": status,
            "sim_message": message,
            # C-79: a result that names its provider also refreshes the stored provider state
            **sim_provider_updates(payload),
        }), None)
        await emit_ui("sim_data_result", payload)
    elif event_type == "sim_data_loading":
        query_masked = payload.get("query_masked", "") if isinstance(payload, dict) else ""
        bridge_manager.sim_loading_since = time.monotonic()
        await stored("sim loading", store.kv_set_many({
            "sim_loading": True,
            "sim_records": [],
            "sim_query_masked": query_masked,
            "sim_status": "loading",
            "sim_message": "Secure lookup workflow started.",
        }), None)
        await emit_ui("sim_data_loading", payload)
    elif event_type == "sim_panel_open":
        await bridge_manager.update_state("sim_panel_open", True)
        updates = sim_provider_updates(payload)
        if updates:  # C-79: init_state hands the provider state to a freshly loaded dialog
            await stored("sim provider", store.kv_set_many(updates), None)
        await emit_ui("sim_panel_open", payload)
    else:
        logger.warning("⚠️ UNKNOWN EVENT: %s", event_type)

    return {"status": "ok"}


# ---------------------------------------------------------------- ConfirmFlow routing (C-29, K-14 / K-45)

CONFIRM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONFIRM_RISKS = ("medium", "high")
CONFIRM_SUMMARY_MAX = 300
CONFIRM_PREVIEW_MAX = 500
CONFIRM_TTL_DEFAULT_S = 60.0
CONFIRM_TTL_MAX_S = 600.0
# confirm_resolved reasons (K-14) that mean the owner decided; the UI also reads 'by' (K-45).
_OWNER_REASONS = {"approved", "denied", "voice"}


def _text(value: Any, limit: int) -> str:
    """Printable characters of a client-supplied text, cut to limit."""
    return "".join(ch for ch in str(value or "") if ch.isprintable())[:limit]


def _confirm_request_from(payload: Any) -> dict[str, Any] | None:
    """A confirm_request as stored and shown (K-14): id, tool, summary, risk, voice_allowed, expires_at (also as
    expires_ts), ttl_s and an optional args_preview. None when the id, tool or risk is unusable. The expiry comes
    from expires_at/expires_ts, else ttl_s, else 60 s, and is never more than 10 min away."""
    if not isinstance(payload, Mapping):
        return None
    confirm_id, tool, risk = payload.get("id"), payload.get("tool"), payload.get("risk")
    if not (isinstance(confirm_id, str) and CONFIRM_ID.fullmatch(confirm_id)) \
            or not isinstance(tool, str) or not _short(tool) or risk not in CONFIRM_RISKS:
        return None
    now = time.time()
    expires = payload.get("expires_at", payload.get("expires_ts"))
    if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
        ttl = payload.get("ttl_s")
        valid_ttl = isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and math.isfinite(ttl) and ttl > 0
        expires = now + (float(ttl) if valid_ttl else CONFIRM_TTL_DEFAULT_S)
    expires = round(min(float(expires), now + CONFIRM_TTL_MAX_S), 3)
    request: dict[str, Any] = {
        "id": confirm_id, "tool": _short(tool), "summary": _text(payload.get("summary"), CONFIRM_SUMMARY_MAX),
        "risk": risk, "voice_allowed": payload.get("voice_allowed") is True, "expires_at": expires,
        "expires_ts": expires, "ttl_s": round(max(0.0, expires - now), 1)}
    preview = payload.get("args_preview")
    if isinstance(preview, (dict, list)):
        preview = json.dumps(preview, ensure_ascii=False, default=str)
    if isinstance(preview, str) and preview:
        request["args_preview"] = preview[:CONFIRM_PREVIEW_MAX]
    return request


async def _handle_confirm_request(payload: Any) -> dict[str, Any]:
    """confirm_request from the agent's broker (K-14): stored as pending and sent only to room 'ui'. The answer says
    how many owner UI sockets could approve it; with none the broker refuses an action only the screen may allow.
    C-29 part 2 (K-45): a risk below the tool's static K-45 floor is clamped UP to it before anything is stored or
    shown (never lowered), voice_allowed is recomputed at the clamped level (K-14: high is screen-only unless
    config.voice_confirm_high, the K-15 env override), the clamp is audited (confirm_risk_clamped) and the answer
    carries the clamped risk and voice gate, so the broker enforces what the dialog shows."""
    request = _confirm_request_from(payload)
    if request is None:
        logger.warning("confirm_request ignored: its id, tool or risk is unusable")
        return {"status": "ignored", "reason": "bad_payload"}
    sent_risk = request["risk"]
    request["risk"], clamped = confirm_policy.clamp_risk(request["tool"], sent_risk)
    if clamped:
        sent_voice = request["voice_allowed"]
        if request["risk"] == "high":
            # K-14: the clamped dialog must never advertise a voice path the high-risk rule forbids.
            request["voice_allowed"] = sent_voice and bool(getattr(config, "voice_confirm_high", False))
        await stored("audit", store.audit("bridge", "confirm_risk_clamped",
                                          {"id": request["id"], "tool": request["tool"],
                                           "sent": sent_risk, "risk": request["risk"],
                                           "sent_voice_allowed": sent_voice,
                                           "voice_allowed": request["voice_allowed"]}), 0)
        logger.warning("confirm_request %s for %s: risk %s is below the K-45 floor; clamped to %s"
                       " (voice_allowed %s)", request["id"], request["tool"], sent_risk,
                       request["risk"], request["voice_allowed"])
    await stored("confirm expiry", store.expire_confirmations(), 0)
    await stored("confirm request", store.add_confirmation(request), None)
    ui_clients = sum(1 for client in _clients.values() if client.role == ROLE_UI)
    await emit_ui("confirm_request", request)
    logger.info("confirm_request %s for %s (%s): %d owner UI socket(s)", request["id"], request["tool"],
                request["risk"], ui_clients)
    answer: dict[str, Any] = {"status": "ok", "ui_clients": ui_clients}
    if clamped:  # the broker sees the stricter gate the dialog shows (K-45; absent when nothing changed)
        answer["risk"] = request["risk"]
        answer["voice_allowed"] = request["voice_allowed"]
    return answer


async def _handle_confirm_resolved(payload: Any) -> None:
    """confirm_resolved from the agent (K-14): a still-pending row is closed and every UI closes its dialog. 'by' is
    added for the K-45 readers: owner, timeout, or the reason itself (no_ui, cancelled)."""
    confirm_id = payload.get("id") if isinstance(payload, Mapping) else None
    if not (isinstance(confirm_id, str) and CONFIRM_ID.fullmatch(confirm_id)):
        return
    allow = payload.get("allow", payload.get("allowed")) is True
    reason = _short(payload.get("reason")) or ("approved" if allow else "denied")
    await stored("confirm resolved", store.resolve_confirmation(confirm_id, allow, by=f"agent:{reason}"), None)
    event: dict[str, Any] = {"id": confirm_id, "allow": allow, "reason": reason,
                             "by": "owner" if reason in _OWNER_REASONS else
                             "timeout" if reason == "expired" else reason}
    if payload.get("result_status") is not None:
        event["result_status"] = _short(payload.get("result_status"))
    await emit_ui("confirm_resolved", event)


async def _confirm_response(client: "SocketClient", payload: Any) -> dict[str, str]:
    """ui_command confirm_response (K-14/K-45, C-29). Only the owner's authenticated UI socket decides, only a
    pending, unexpired request, and only {id, allow: bool}. The decision is audited, forwarded to room 'agent'
    alone, and closes the dialog on every UI at once; refusals are audited too."""
    confirm_id = payload.get("id") if isinstance(payload, Mapping) else None
    allow = payload.get("allow") if isinstance(payload, Mapping) else None
    if client.role != ROLE_UI:
        await stored("audit", store.audit(client.role, "confirm_response_refused",
                                          {"id": _short(confirm_id), "reason": "not_owner_ui"}, client.ip), 0)
        logger.warning("confirm_response refused from role=%s: only the owner's UI decides", client.role)
        return _rejected("not_allowed")
    if not (isinstance(confirm_id, str) and CONFIRM_ID.fullmatch(confirm_id)) or not isinstance(allow, bool):
        return _rejected("bad_payload")
    record = await stored("confirm decision", store.resolve_confirmation(confirm_id, allow, by="owner"), None)
    if record is None:
        await stored("audit", store.audit("owner", "confirm_response_refused",
                                          {"id": confirm_id, "reason": "not_pending"}, client.ip), 0)
        logger.info("confirm_response %s refused: not pending (decided, expired or unknown)", confirm_id)
        return _rejected("not_pending")
    await stored("audit", store.audit("owner", "confirm_decision", {"id": confirm_id, "tool": record["tool"],
                                                                     "risk": record["risk"], "allow": allow},
                                      client.ip), 0)
    logger.info("confirm_response %s for %s: %s by the owner", confirm_id, record["tool"],
                "allowed" if allow else "denied")
    await sio.emit("agent_command", {"type": "confirm_response", "payload": {"id": confirm_id, "allow": allow}},
                   to=ROOM_AGENT)
    await emit_ui("confirm_resolved", {"id": confirm_id, "allow": allow, "by": "owner",
                                       "reason": "approved" if allow else "denied"})
    return {"status": "sent"}


def _uptime_s() -> int:
    return int(time.monotonic() - _STARTED_MONO)


@app.get("/health")
async def get_health() -> dict[str, Any]:
    """Public liveness (K-43): exactly {status, version, uptime_s}; never presence, brain or vitals."""
    return {"status": "ok", "version": GIT_SHA, "uptime_s": _uptime_s()}


async def _pc_signed(request: Request) -> bool:
    """K-70 PC-signed HTTP (X-Desk-* headers) for a bodiless request, checked by the Desktop Link relay."""
    if "x-desk-sig" not in request.headers:
        return False
    declared = request.headers.get("content-length", "")
    return await desktop.verify_pc_http(
        request.headers.raw, method=request.method, path=request.scope.get("raw_path") or request.url.path.encode(),
        query=request.scope.get("query_string", b""), content_length=int(declared) if declared.isdigit() else None,
        read_body=request.body, ip=_request_ip(request))


async def _db_ok() -> bool:
    await store.kv_get(KV_DESKTOP_LINK)
    return True


@app.get("/status")
async def get_status(request: Request) -> Any:
    """
    C-STATUS-1 (K-43 v9) with the K-74 'desktop' block (C-116) and the room owner's 'livekit' block (C-23: its last
    observation, never an extra LiveKit call per request); C-63 adds the agent heartbeats. status: 'down' when the DB
    is unusable, 'degraded' when LiveKit is not ok or the agent is neither paused nor online and voice-ready, else
    'ok'; a bridge without the room owner (the PC's local bridge) keeps the v8 rule: 'ok' while an owner-room agent
    socket is connected, and its livekit block says enabled false with ok null. Auth: the internal listener (agent
    role), K-70 PC-signed HTTP (agent_ctl brain and node checks), the owner session (C-19), the agent's signed
    headers, or the legacy header while LEGACY_TOKEN_AUTH=1.
    """
    if not (bridge_desktop.is_internal_scope(request.scope) or _legacy_header_ok(request)
            or await owner_auth.session_from_request(request) is not None
            or await _agent_request_ok(request) or await _pc_signed(request)):
        return JSONResponse({"error": "auth_required"}, status_code=401)
    db_ok = await stored("status check", _db_ok(), False)
    try:
        db_size = sum(os.path.getsize(store.path + suffix) for suffix in ("", "-wal")
                      if os.path.exists(store.path + suffix))
    except OSError:
        db_size = 0
    livekit = rooms.snapshot()
    agent = build_agent_status()
    if rooms.enabled:
        healthy = bool(livekit["ok"]) and (agent["paused"] or (agent["online"] and bool(agent["voice_ready"])))
    else:  # no room owner here (the PC's local bridge): it never reads LiveKit, so the v8 rule stays
        healthy = agent["online"]
    return {
        "status": "down" if not db_ok else ("ok" if healthy else "degraded"),
        "ts": round(time.time(), 3),
        "bridge": {"ok": True, "version": GIT_SHA, "uptime_s": _uptime_s()},
        "db": {"ok": db_ok, "size_bytes": db_size},
        "agent": agent,
        "livekit": livekit,
        "ui_clients": sum(1 for client in _clients.values() if client.role in (ROLE_UI, ROLE_LEGACY)),
        "brain": active_brain(),
        "desktop": desktop.status(),
    }


async def _media_auth(request: Request) -> bool:
    """Auth for the media path (K-73): the owner session (the legacy token until C-22) for the UI, or K-70
    PC-signed HTTP for the laptop node. A GET is bodiless, so the PC signature covers method, path and query."""
    return (_legacy_header_ok(request) or await owner_auth.session_from_request(request) is not None
            or await _pc_signed(request))


@app.get("/media/index")
async def media_index(request: Request, area: str, cursor: float | None = None) -> Any:
    """GET /media/index?area=shared|outputs&cursor=<ts> (K-73/DASH-C21): the allowed, non-excluded regular files
    of the area, each with seq, relpath, size, mtime_ns and sha256 when known; the returned cursor is the listing
    time minus 5 s. The directory walk never follows a symlink."""
    if not await _media_auth(request):
        return JSONResponse({"error": "auth_required"}, status_code=401)
    try:
        return await asyncio.to_thread(media_relay.build_index, area, cursor)
    except bridge_media.MediaError as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)


@app.get("/media/file")
async def media_file(request: Request, area: str, path: str) -> Any:
    """GET /media/file?area=&path= with HTTP Range (K-73/DASH-C21). The file is opened component by component
    with O_NOFOLLOW (only a regular file is served), so a symlink swapped in after a listing is refused."""
    if not await _media_auth(request):
        return JSONResponse({"error": "auth_required"}, status_code=401)
    try:
        fd, st = await asyncio.to_thread(media_relay.open_file, area, path)
    except bridge_media.MediaError as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)
    size = st.st_size
    try:
        span = bridge_media.parse_range(request.headers.get("range"), size)
    except bridge_media.MediaError:
        os.close(fd)
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
    headers = {"Accept-Ranges": "bytes", "Content-Type": bridge_media.content_type(path)}
    if span is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(bridge_media.stream_fd(fd, 0, size), headers=headers)
    start, end = span
    length = end - start + 1
    headers["Content-Length"] = str(length)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(bridge_media.stream_fd(fd, start, length), status_code=206, headers=headers)


@app.post("/desktop/blob")
async def desktop_blob(request: Request) -> Any:
    """POST /desktop/blob (K-73): the laptop node uploads an agent-sealed blob. The PC-signed length (<= 30 MB)
    and sha256 are verified before the body is read; the body is streamed and hashed under that hard cap, at most
    two uploads at once, and kept in RAM only (<= 256 MB total, TTL 15 min, deleted after the first fetch). The
    agent fetches it on GET /internal/blob/<id> on the internal listener only."""
    if not (desktop.active and desktop.keys is not None and desktop.seen is not None):
        return JSONResponse({"error": "link_unconfigured"}, status_code=503)
    declared = request.headers.get("content-length", "")
    try:
        blob_id, size = await bridge_media.receive_blob(
            public_key=desktop.keys.pc, seen=desktop.seen, headers=request.headers.raw, method=request.method,
            path=request.scope.get("raw_path") or request.url.path.encode(),
            query=request.scope.get("query_string", b""),
            content_length=int(declared) if declared.isdigit() else None,
            body_iter=request.stream(), store=blob_store, now=time.time())
    except bridge_media.MediaError as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)
    logger.info("Desktop blob stored (%d bytes)", size)
    return {"blob_id": blob_id, "size": size}


@app.get("/internal/blob/{blob_id}")
async def internal_blob(request: Request, blob_id: str) -> Any:
    """GET /internal/blob/<id> (K-73): the agent fetches a blob once, on the internal :5002 listener only (nginx
    answers 404 for /vortex/internal/, and InternalOnly 404s it on the public listener). The blob is deleted on
    this fetch."""
    if not bridge_desktop.is_internal_scope(request.scope) or not _BLOB_ID.fullmatch(blob_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    data = blob_store.take(blob_id)
    if data is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # Streamed in chunks so the blob stays counted against the 256 MB cap until the transport took it all.
    return StreamingResponse(bridge_media.blob_chunks(data), headers={"Content-Length": str(len(data))},
                             media_type="application/octet-stream")


@app.get("/desktop/backup/latest")
async def desktop_backup_latest(request: Request) -> Any:
    """GET /desktop/backup/latest (K-73, PC-signed): streams the newest encrypted snapshot from the read-only
    jarvis_agent_snapshots mount. The agent service writes the snapshots; the bridge only picks the newest."""
    if not await _pc_signed(request):
        return JSONResponse({"error": "auth_required"}, status_code=401)
    try:
        fd, st, name = await asyncio.to_thread(backup_relay.open_latest)
    except bridge_media.MediaError as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)
    safe_name = re.sub(r'[^A-Za-z0-9._-]', "_", name) or "snapshot"
    headers = {"Content-Length": str(st.st_size), "Content-Type": "application/octet-stream",
               "Content-Disposition": f'attachment; filename="{safe_name}"'}
    return StreamingResponse(bridge_media.stream_fd(fd, 0, st.st_size), headers=headers)


@app.get("/messages")
async def get_messages(before: float | None = None, limit: int = MESSAGE_PAGE_MAX, cursor: str | None = None,
                       _owner: bridge_auth.Principal = Depends(owner_auth.require_session_or_legacy),
                       ) -> dict[str, Any]:
    """
    Chat history (C-MSG-1): up to `limit` (1..100) messages older than `before` (epoch s or ms),
    ascending, has_more, and next_cursor for the dashboard's pager. `cursor` takes a previous
    next_cursor, which names the page's oldest message exactly, so messages that share a ts are
    never skipped; `before` wins when both are given.
    Auth: the owner session, or the X-Vortex-Token header while LEGACY_TOKEN_AUTH=1 (C-AUTH-1).
    """
    before_seq: int | None = None
    if before is None and cursor:
        try:
            before, before_seq = parse_message_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Bad cursor") from exc
    if before is not None and not math.isfinite(before):
        raise HTTPException(status_code=400, detail="Bad cursor")
    try:
        return await store.message_page(limit, before, before_seq)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log_store_error("history read")
        raise HTTPException(status_code=503, detail="History unavailable") from exc


@app.get("/livekit/token")
async def livekit_token(owner: bridge_auth.Principal = Depends(owner_auth.require_session_or_legacy)) -> Any:
    """
    Issue a LiveKit access token for the browser voice client (C-LK-1 / K-37).
    Auth: the owner session, or the X-Vortex-Token header while LEGACY_TOKEN_AUTH=1.
    The bridge mints the identity and ignores any identity the client sends: 'owner-<first 8 chars of the session
    id>' for a session (stable per session, TTL 1 h); the legacy token keeps a new owner-<8 hex> for every token
    (C-116), so two legacy tabs or devices never push each other out. (K-37 names 'owner-legacy' for legacy mode; a
    single fixed identity would make LiveKit drop the other tab, so today's per-token identity and 6 h TTL stay
    until the cut-over C-22, which ends legacy mode.) 503 {error: 'livekit_unavailable'} when no token can be
    minted.
    K-37 v9 (C-23): before minting, the route awaits the room owner's ensure('token') for at most 3 s (shielded, so a
    slow one finishes in the background; an error is only logged) and never deletes a room. The token is minted
    either way.
    """

    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    room_name = os.getenv("JARVIS_ROOM_NAME", "jarvis-hybrid")
    # Browser-facing URL (public). Internal docker URL is not usable from clients.
    public_url = os.getenv(
        "LIVEKIT_PUBLIC_URL",
        os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880"),
    )
    # If bridge has internal docker hostname, prefer explicit public URL
    if "jarvis-livekit" in public_url:
        public_url = os.getenv("LIVEKIT_PUBLIC_URL", "ws://187.127.123.235:7880")

    if not api_key or not api_secret:
        logger.error("LiveKit token refused: LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured")
        return JSONResponse({"error": "livekit_unavailable"}, status_code=503)

    if owner.is_session:
        identity, ttl_s = f"owner-{owner.session_id[:8]}", LIVEKIT_TOKEN_TTL_S
    else:
        identity, ttl_s = f"owner-{secrets.token_hex(4)}", LIVEKIT_LEGACY_TOKEN_TTL_S
    # K-37 v9: the room owner makes sure the room (and, outside observe mode, its agent) is there; never a delete.
    await _ensure_before_token()
    try:
        from livekit import api as lk_api  # pylint: disable=import-outside-toplevel

        access = (
            lk_api.AccessToken(api_key, api_secret)
            .with_identity(identity)
            .with_ttl(timedelta(seconds=ttl_s))
            .with_name("Owner")
            .with_attributes({"role": "owner"})
            .with_grants(
                lk_api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
        )
        issued_at = time.time()
        jwt_token = access.to_jwt()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("LiveKit token mint failed")
        return JSONResponse({"error": "livekit_unavailable"}, status_code=503)

    return {
        "token": jwt_token,
        "url": public_url,
        "room": room_name,
        "identity": identity,
        "expires_at": int(issued_at) + ttl_s,
    }


# ---------------------------------------------------------------- the room owner's routes (C-23, K-39 v9)

ROOM_TOKEN_WAIT_S = 3.0      # K-37 v9: the token route waits this long for its ensure
ENSURE_MAX_BYTES = 1024      # K-39 v9: an ensure body is a JSON object of at most 1 KiB
# Ensure calls: at most 2 a second (burst 10) per route, so one trust class never starves another (the internal
# route's only credential is the network; the public routes are taken only after their signature checked out);
# above it 429 with Retry-After.
ENSURE_INTERNAL, ENSURE_AGENT, ENSURE_PC = "internal", "agent", "pc"
_ensure_buckets: dict[str, TokenBucket] = {name: TokenBucket(2.0, 10.0)
                                           for name in (ENSURE_INTERNAL, ENSURE_AGENT, ENSURE_PC)}


def _retrieve_ensure_outcome(task: "asyncio.Future[Any]") -> None:
    """A token ensure that finished after its 3 s: its error is taken here, so asyncio never logs it as lost."""
    error = None if task.cancelled() else task.exception()
    if error is not None:
        logger.debug("Room ensure (token) failed: %s", type(error).__name__)


async def _ensure_before_token() -> None:
    """GET /livekit/token's ensure (K-37 v9): shielded and awaited for at most ROOM_TOKEN_WAIT_S; a timeout or an
    error never stops the token."""
    task = asyncio.ensure_future(rooms.ensure("token", reuse=True))  # a result under 2 s old is enough
    task.add_done_callback(_retrieve_ensure_outcome)
    try:
        await asyncio.wait_for(asyncio.shield(task), ROOM_TOKEN_WAIT_S)
    except Exception:  # pylint: disable=broad-exception-caught  # timeout or LiveKit unavailable
        logger.debug("Room ensure before the token: not finished or failed", exc_info=True)


def _ensure_refused(error: str, status: int, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers=dict(headers or {}))


async def _run_room_ensure(reason: str, *, bucket: str, report: bridge_rooms.Report | None = None,
                           want_voice: bool = False) -> Any:
    """An explicit ensure for one of the three routes: 429 above 2 a second on that route, 503 livekit_unavailable
    without LiveKit keys or when LiveKit cannot be read, else the K-39 v9 answer (observe mode: from memory, with no
    LiveKit call)."""
    limiter = _ensure_buckets[bucket]
    if not limiter.take():
        return _ensure_refused("rate_limited", 429, bridge_security.retry_after_header(limiter.retry_after()))
    if not rooms.enabled and rooms.disabled_reason == bridge_rooms.DISABLED_NO_KEYS:
        return _ensure_refused("livekit_unavailable", 503)
    try:
        result = await rooms.ensure(reason, explicit=True, report=report, want_voice=want_voice, route=True)
    except bridge_rooms.LiveKitUnavailable:
        return _ensure_refused("livekit_unavailable", 503)
    return result.as_dict()


async def _ensure_from_body(request: Request, *, internal: bool) -> Any:
    """POST /livekit/ensure (C-NOTIFY-1 agent headers over the exact raw body, read once) and POST
    /internal/livekit/ensure (:5002, no headers; want_voice)."""
    try:
        raw = await bridge_security.read_body(request, limit=ENSURE_MAX_BYTES)
    except NotifyAuthError as exc:
        return _ensure_refused(exc.error, exc.status)
    if raw is None:
        return _ensure_refused("too_large", 413)
    if not internal and not await bridge_auth.verify_agent_headers(
            request.headers, raw, agent_token=config.agent_token, nonces=_notify_nonces, mark_seen=_mark_nonce_seen):
        return _ensure_refused("auth_required", 401)
    try:
        reason, report, want_voice = bridge_rooms.parse_ensure_body(raw, rooms.settings, internal=internal)
    except bridge_rooms.EnsureBodyError as exc:
        return _ensure_refused(exc.error, 400)
    return await _run_room_ensure(reason, bucket=ENSURE_INTERNAL if internal else ENSURE_AGENT, report=report,
                                  want_voice=want_voice)


@app.post("/livekit/ensure")
async def livekit_ensure(request: Request) -> Any:
    """POST /vortex/livekit/ensure (K-39 v9): the PC's ensure_room.py and agent_ctl. The C-NOTIFY-1 agent headers over
    the exact body {} or {reason?, agent_name?, worker_id?}; an explicit ensure (K-44 v9)."""
    return await _ensure_from_body(request, internal=False)


@app.post("/internal/livekit/ensure")
async def internal_livekit_ensure(request: Request) -> Any:
    """POST /internal/livekit/ensure (K-39 v9): the VPS containers on :5002 only (InternalOnly answers 404
    elsewhere); no headers; want_voice asks for a voice session in on_demand mode."""
    if not bridge_desktop.is_internal_scope(request.scope):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await _ensure_from_body(request, internal=True)


@app.post("/desktop/ensure")
async def desktop_ensure(request: Request) -> Any:
    """POST /vortex/desktop/ensure (K-39 v9): the laptop's K-70 PC-signed HTTP with an empty body; an explicit
    ensure. 503 link_unconfigured while the Desktop Link relay is off."""
    if not (desktop.active and desktop.keys is not None and desktop.seen is not None):
        return _ensure_refused("link_unconfigured", 503)
    if not await _pc_signed(request):
        return _ensure_refused("auth_required", 401)
    return await _run_room_ensure("desktop_ensure", bucket=ENSURE_PC)


@app.post("/internal/livekit/webhook")
async def internal_livekit_webhook(request: Request) -> Any:
    """LiveKit's webhooks (K-44/K-48 v9): :5002 only (jarvis-network, never published), JWT-verified over the raw
    body by the room owner; an event only wakes its loop."""
    if not bridge_desktop.is_internal_scope(request.scope):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        raw = await bridge_security.read_body(request, limit=bridge_rooms.WEBHOOK_MAX_BYTES)
    except NotifyAuthError as exc:
        return _ensure_refused(exc.error, exc.status)
    status = await rooms.handle_webhook(raw, request.headers.get("authorization"))
    return JSONResponse({}, status_code=status)


@app.get("/telemetry/production")
async def get_production_telemetry(
        _owner: bridge_auth.Principal = Depends(owner_auth.require_session_or_legacy)) -> dict[str, Any]:
    """Returns real-world interaction metrics for the dashboard (the owner session or the legacy header). The
    metrics call is a coroutine: awaiting it fixes the old 500."""
    return await telemetry.get_production_metrics()


@app.post("/feedback")
async def record_user_feedback(data: dict[str, Any],
                               _owner: bridge_auth.Principal = Depends(owner_auth.require_session_or_legacy),
                               ) -> dict[str, str]:
    """Receives user feedback and updates adaptive engine (the owner session or the legacy header)."""

    intent: str | None = data.get("intent")
    score: int = data.get("score", 0)
    strategy: str | None = data.get("strategy")

    if intent and strategy:
        adaptive_engine.record_feedback(intent, strategy, score)
        logger.info("Feedback recorded: %s (Score: %s)", intent, score)

    return {"status": "recorded"}


# K-40 vitals: in pc mode the PC brain's agent sends this laptop's vitals_pc over /notify every 10 s (in vps mode
# the Desktop Node sends them, K-74); the bridge's own psutil figures are vitals_vps.
_agent_pc_vitals: dict[str, Any] | None = None
_agent_pc_vitals_mono = 0.0
_latest_vps_vitals: dict[str, Any] | None = None
_DISK_LABEL = re.compile(r"[A-Za-z0-9 :()._-]{1,16}")


def _pc_vitals_from_agent(payload: Any) -> dict[str, Any] | None:
    """A vitals_pc (or legacy 'vitals') notify payload as the UI gets it: cpu, ram and disk (0..100, one decimal),
    optional cores, ram_total_gb, disk_label and host, and ts = the bridge's receive time. None when cpu, ram or
    disk is missing or not a finite number."""
    if not isinstance(payload, Mapping):
        return None
    vitals: dict[str, Any] = {}
    for name in ("cpu", "ram", "disk"):
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        vitals[name] = round(min(100.0, max(0.0, float(value))), 1)
    cores = payload.get("cores")
    if isinstance(cores, int) and not isinstance(cores, bool) and 1 <= cores <= 4096:
        vitals["cores"] = cores
    total = payload.get("ram_total_gb")
    if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total) \
            and 0 <= total <= 65536:
        vitals["ram_total_gb"] = round(float(total), 1)
    label = payload.get("disk_label")
    if isinstance(label, str) and _DISK_LABEL.fullmatch(label):
        vitals["disk_label"] = label
    host = _short(payload.get("host"))
    if host:
        vitals["host"] = host
    vitals["ts"] = round(time.time(), 3)
    return vitals


async def _accept_agent_pc_vitals(payload: Any) -> None:
    """vitals_pc from the PC brain's agent (K-40): kept for init_state and the legacy vitals_update, and sent to the
    UI as vitals_pc plus a legacy vitals_update marked source 'pc'."""
    global _agent_pc_vitals, _agent_pc_vitals_mono  # pylint: disable=global-statement
    vitals = _pc_vitals_from_agent(payload)
    if vitals is None:
        logger.debug("vitals_pc notify ignored: cpu, ram or disk is missing or not a number")
        return
    _agent_pc_vitals, _agent_pc_vitals_mono = vitals, time.monotonic()
    await emit_ui("vitals_pc", vitals)
    await emit_ui("vitals_update", {**vitals, "source": "pc"})


def _newest(*readings: dict[str, Any] | None) -> dict[str, Any] | None:
    present = [reading for reading in readings if reading]
    return dict(max(present, key=lambda reading: reading.get("ts") or 0)) if present else None


def latest_pc_vitals() -> dict[str, Any] | None:
    """init_state.vitals_pc: the newest laptop reading, from the PC brain's agent or from the Desktop Node."""
    return _newest(_agent_pc_vitals, desktop.latest_pc_vitals())


def fresh_pc_vitals() -> dict[str, Any] | None:
    """The newest laptop reading younger than 30 s, which the legacy vitals_update prefers (K-74)."""
    agent = _agent_pc_vitals if time.monotonic() - _agent_pc_vitals_mono < bridge_desktop.VITALS_FRESH_S else None
    return _newest(agent, desktop.fresh_pc_vitals())


async def vitals_heartbeat() -> None:
    """Every 12 s: the bridge's own figures as vitals_vps (K-40), and the legacy vitals_update."""
    global _latest_vps_vitals  # pylint: disable=global-statement
    while True:
        try:
            # C-64 review COR-2: a notify flood that stops abruptly still gets its last refusal window logged.
            _report_notify_refusals(app.state.notify_limiter)
            health_monitor.record_heartbeat("ui_bridge")
            report: dict[str, Any] = health_monitor.generate_health_report()
            vitals: dict[str, Any] = report["vitals"]

            vitals_pkg: dict[str, Any] = {
                "cpu": vitals.get("cpu", {}).get("usage", 0),
                "ram": vitals.get("memory", {}).get("percent", 0),
                "disk": vitals.get("disk", {}).get("percent", 0),
            }
            await bridge_manager.update_state("vitals", vitals_pkg)
            _latest_vps_vitals = {**vitals_pkg, "host": "vps", "ts": round(time.time(), 3)}
            await emit_ui("vitals_vps", _latest_vps_vitals)
            # K-74: the legacy vitals_update prefers the laptop's own reading while it is under 30 s old.
            await emit_ui("vitals_update", fresh_pc_vitals() or {**_latest_vps_vitals, "source": "vps"})
        except asyncio.CancelledError:
            logger.info("Vitals heartbeat task cancelled.")
            break
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Vitals Heartbeat error: %s", e)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected Vitals error")
        
        # 🔋 PERFORMANCE: 12 seconds is plenty for UI bars, saves massive CPU/Network resources
        await asyncio.sleep(12.0)


async def telemetry_heartbeat() -> None:
    """Periodically push production metrics to the UI."""
    while True:
        try:
            metrics: dict[str, Any] = await telemetry.get_production_metrics()
            await bridge_manager.update_state("telemetry", metrics)
            await emit_ui("telemetry_update", metrics)
        except asyncio.CancelledError:
            logger.info("Telemetry heartbeat task cancelled.")
            break
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Telemetry Heartbeat error: %s", e)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected Telemetry Heartbeat error")
        
        # 🔋 PERFORMANCE: 60 seconds for analytics is efficient
        await asyncio.sleep(60.0)


async def user_speaking_monitor() -> None:
    """Reset speaking state if no pulse received for >1.5 seconds."""
    while True:
        try:
            state: dict[str, Any] = await bridge_manager.get_state()
            if state.get("user_speaking"):
                if time.time() - state.get("last_user_speak_pulse", 0) > 1.5:
                    await bridge_manager.update_state("user_speaking", False)
                    await emit_ui("user_status_change", {"speaking": False})
        except asyncio.CancelledError:
            logger.info("User speaking monitor task cancelled.")
            break
        except (KeyError, ValueError, TypeError) as e:
            logger.error("User speaking monitor data error: %s", e)
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Unexpected User speaking monitor error")
        
        # 🔋 PERFORMANCE: Slightly longer check interval
        await asyncio.sleep(1.5)


async def open_store() -> None:
    """Opens bridge.db in the background, so a long integrity check never delays /health."""
    try:
        await store.open()
        logger.info("Bridge store ready: %s", store.path)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.critical("Bridge store unavailable (%s); live events still reach the UI, but "
                        "nothing is persisted", store.path, exc_info=True)


async def store_retention_loop() -> None:
    """Hourly: deletes event/log rows older than BRIDGE_EVENT_RETENTION_DAYS (0, the default, keeps them
    forever), caps the log feed at BRIDGE_LOG_MAX_ROWS newest rows (C-64 review LS-2: bridge.db stays bounded
    even with retention 0; 0 removes the cap) and drops expired seen ids. Chat messages are never pruned."""
    retention_days = event_retention_days()
    log_cap = log_max_rows()
    logger.info("Event/log retention: %s; log feed capped at %s rows",
                f"{retention_days:g} days" if retention_days else "kept forever (BRIDGE_EVENT_RETENTION_DAYS=0)",
                log_cap if log_cap else "no cap (BRIDGE_LOG_MAX_ROWS=0)")
    while True:
        try:
            deleted = await store.prune_events(retention_days)
            if deleted:
                logger.info("Retention: deleted %d event rows older than %g days",
                            deleted, retention_days)
            capped = await store.cap_events(EVENT_LOG, log_cap)
            if capped:
                logger.info("Retention: deleted %d log rows beyond the newest %d (BRIDGE_LOG_MAX_ROWS)",
                            capped, log_cap)
            await store.prune_seen()
        except asyncio.CancelledError:
            logger.info("Store retention task cancelled.")
            break
        except Exception:  # pylint: disable=broad-exception-caught
            _log_store_error("retention")

        await asyncio.sleep(3600.0)


async def _expire_ui_sessions() -> None:
    """Ends the UI sockets whose owner session was revoked (revoke-all, another logout) or has expired."""
    checked: dict[str, bool] = {}
    for client in list(_clients.values()):
        session_id = client.session_id
        if client.role != ROLE_UI or not session_id or session_id in checked:
            continue
        checked[session_id] = await owner_auth.session_active(session_id)
        if not checked[session_id]:
            await _end_session_sockets(session_id, "expired")


async def session_watch_loop() -> None:
    """Every SESSION_WATCH_S: UI sockets of a session that is no longer valid get 'session_expired' and close."""
    while True:
        await asyncio.sleep(SESSION_WATCH_S)
        try:
            await _expire_ui_sessions()
        except asyncio.CancelledError:
            logger.info("Session watch task cancelled.")
            break
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Session watch failed")


async def build_init_state() -> dict[str, Any]:
    """init_state (C-STATE-1): volatile flags from memory, the rest from the store, with every
    legacy key the current dashboard reads (persona, vortex_logs, ...)."""
    state: dict[str, Any] = await bridge_manager.get_state()
    snapshot: dict[str, Any] | None = await stored(
        "init_state read", store.init_snapshot(PERSISTED_DEFAULTS), None)
    persisted = copy.deepcopy(PERSISTED_DEFAULTS)
    if snapshot is None:
        snapshot = {"messages": [], "logs": [], "memories": [], "tool_logs": [], "settings": {},
                    "notifications": []}
    else:
        persisted.update(snapshot["kv"])
    state.update(persisted)
    since = bridge_manager.sim_loading_since
    if since is None or time.monotonic() - since > SIM_LOADING_MAX_S:
        # The stored lookup's sim_data_result was lost (agent or bridge restart, dropped notify):
        # report it idle, or the panel's lookup form would stay blocked on every page load.
        state["sim_loading"] = False
        if state.get("sim_status") == "loading":
            state["sim_status"], state["sim_message"] = "idle", ""
    # Frontend expects 'persona' key, not 'active_persona'
    state["persona"] = state.get("active_persona", "jarvis")
    state["messages"] = snapshot["messages"]
    state["vortex_logs"] = snapshot["logs"]
    state["logs"] = snapshot["logs"]
    state["memories"] = snapshot["memories"]
    state["tool_logs"] = snapshot["tool_logs"]
    state["notifications"] = snapshot["notifications"]
    if snapshot["settings"]:
        # Only a stored object: an empty one would hide persona/muted from the settings panel.
        state["settings"] = snapshot["settings"]
    state["agent_status"] = build_agent_status()  # K-02/K-42 v9: the whole object; a late UI never shows 'Unknown'
    # C-29: a reloaded UI shows a still-pending confirmation again (the requests as they were sent).
    state["confirmations"] = [record["payload"] for record in
                              await stored("confirmations", store.list_confirmations(), [])]
    state["jobs"] = jobs_newest_first()  # C-58 (K-31): the Jobs panel comes back after a reload
    state["desktop_status"] = desktop.status()  # K-74 (C-STATE-1 v4)
    state["vitals_pc"] = latest_pc_vitals()
    if _latest_vps_vitals is not None:
        state["vitals_vps"] = dict(_latest_vps_vitals)
    state["server_time"] = time.time()
    return state


@dataclass
class SocketClient:
    """One authenticated Socket.IO connection (C-SOCK-1)."""

    role: str
    ip: str
    since: float = field(default_factory=time.time)
    host: str = ""
    version: str = ""
    room: str = ""      # agents: the LiveKit room they serve (K-38 v4); ui_command goes only to JARVIS_ROOM_NAME's
    job_id: str = ""
    internal: bool = False  # came in on the internal listener (jarvis-network)
    session_id: str = ""    # role 'ui': the owner session of the jarvis_session cookie (C-19)
    commands: TokenBucket = field(default_factory=lambda: TokenBucket(UI_COMMANDS_PER_S))
    frames: TokenBucket = field(default_factory=lambda: TokenBucket(VISION_FRAMES_PER_S))
    hellos: TokenBucket = field(default_factory=lambda: TokenBucket(AGENT_HELLOS_PER_S, AGENT_HELLO_BURST))


_clients: dict[str, SocketClient] = {}
# Audit rows for refused sockets: a steady trickle, so a flood of bad connects cannot fill the disk.
_refusal_audit = TokenBucket(1.0, 30.0)


class AgentStatusTracker:  # pylint: disable=too-few-public-methods
    """agent_status (K-02 v9) as the bridge last built and emitted it. since: when the tuple (online, in_room,
    voice_ready, paused, restarting) last changed (bridge start until then)."""

    def __init__(self) -> None:
        self.key: tuple[Any, ...] | None = None
        self.since: float = time.time()
        self.emitted: tuple[Any, ...] | None = None


_status_tracker = AgentStatusTracker()
# When the last UI socket left (epoch s): the room owner's on_demand demand window (K-44 v9).
_ui_last_seen: float | None = None


def _short(value: Any) -> str:
    """A client-supplied label for logs and the session: printable characters only, cut short."""
    return "".join(ch for ch in str(value or "") if ch.isprintable())[:_SHORT_TEXT]


def _rejected(reason: str) -> dict[str, str]:
    return {"status": "rejected", "reason": reason}


def _legacy_tokens(environ: Mapping[str, Any], auth: Mapping[str, Any]) -> list[Any]:
    """auth.token, else the last ?token= query parameter, as sent and percent-decoded."""
    token = auth.get("token")
    if token:
        return [token]
    raw = ""
    for part in str(environ.get("QUERY_STRING") or "").split("&"):
        name, sep, value = part.partition("=")
        if sep and name == "token":
            raw = value
    return list(dict.fromkeys((raw, unquote(raw)))) if raw else []


def _socket_role(environ: Mapping[str, Any], auth: Any) -> tuple[str | None, str]:
    """(role, '') for valid credentials, else (None, reason). An agent_token decides on its own; the
    legacy token counts only while LEGACY_TOKEN_AUTH=1. The jarvis_session cookie (role 'ui', C-19) is checked
    before this, in handle_connect, because it needs the store."""
    auth = auth if isinstance(auth, dict) else {}
    if "agent_token" in auth:
        if same_secret(auth.get("agent_token"), config.agent_token):
            return ROLE_AGENT, ""
        return None, "bad_agent_token"
    tokens = _legacy_tokens(environ, auth)
    if not tokens:
        return None, "no_credentials"
    if not config.legacy_token_auth:
        return None, "legacy_off"
    if any(same_secret(token, VORTEX_SECURITY_TOKEN) for token in tokens):
        return ROLE_LEGACY, ""
    return None, "bad_token"


def _internal_role(auth: Any) -> tuple[str | None, str]:
    """The internal listener takes only the token-less agent role {role: 'agent', room, job_id} (K-38 v4): no
    agent_token, no legacy token and no UI there; that role exists nowhere else."""
    if isinstance(auth, dict) and auth.get("role") == ROLE_AGENT:
        return ROLE_AGENT, ""
    return None, "internal_agent_only"


def _socket_ip(environ: Mapping[str, Any]) -> str:
    scope = environ.get("asgi.scope")
    peer = scope.get("client") if isinstance(scope, dict) else None
    return _peer_ip(str(peer[0]) if peer else "", environ.get("HTTP_X_REAL_IP"))


def _owner_room() -> str:
    return os.getenv("JARVIS_ROOM_NAME") or "jarvis-hybrid"


def _is_owner_agent(client: SocketClient) -> bool:
    """An agent socket serving the owner's room: only these get ui_command and count for agent_status (K-38 v4);
    canary and probe peers declare another room."""
    return client.role == ROLE_AGENT and client.room == _owner_room()


def _agent_room(auth: Mapping[str, Any], internal: bool) -> str:
    """The room an agent socket declares. A public agent_token socket that declares none predates C-15 and serves
    JARVIS_ROOM_NAME; an internal one must declare it."""
    room = auth.get("room")
    if isinstance(room, str) and _ROOM_NAME.fullmatch(room):
        return room
    return "" if internal else _owner_room()


async def _join_rooms(sid: str, client: SocketClient) -> None:
    """Puts the socket in its role's rooms and records the role in its Socket.IO session. An agent socket that
    serves another room than JARVIS_ROOM_NAME joins none, so it never receives a ui_command or a camera frame."""
    socket_rooms = ROLE_ROOMS[client.role] if client.role != ROLE_AGENT or _is_owner_agent(client) else ()
    try:
        for room in socket_rooms:
            await sio.enter_room(sid, room)
        await sio.save_session(sid, {"role": client.role, "ip": client.ip, "since": client.since})
    except (KeyError, ValueError):  # no such live socket (it has already gone)
        logger.debug("Socket %s left before joining its rooms", sid)


def build_agent_status() -> dict[str, Any]:
    """agent_status as the UI should see it right now (K-02 v9): always the whole object, for the event, init_state
    (C-STATE-1) and GET /status. online: an agent socket of the owner's room is connected; in_room / voice_ready /
    session / restarting: the room owner's last observation (LiveKit presence); paused: the owner's flag;
    last_heartbeat: the agent's last authenticated contact; agent_name: the dispatch target."""
    agent = next((client for client in _clients.values() if _is_owner_agent(client)), None)
    presence = rooms.presence()
    paused = rooms.paused
    online = agent is not None
    key = (online, presence["in_room"], presence["voice_ready"], paused, presence["restarting"])
    tracker = _status_tracker
    if key != tracker.key:
        tracker.key, tracker.since = key, time.time()
    heartbeat = bridge_manager.state.get("last_runner_contact")
    return {
        "online": online, "in_room": presence["in_room"], "voice_ready": presence["voice_ready"], "paused": paused,
        "restarting": presence["restarting"], "since": tracker.since,
        "last_heartbeat": heartbeat if isinstance(heartbeat, (int, float)) else None,
        "host": agent.host if agent is not None else "", "version": agent.version if agent is not None else "",
        "brain": rooms.settings.brain, "agent_name": rooms.target_agent_name, "session": presence["session"],
    }


async def _publish_agent_status() -> None:
    """Emits agent_status to room 'ui' when its tuple (online, in_room, voice_ready, paused, restarting), the host or
    the version changed since the last emit (the room owner's on_status, agent connects, hellos and leaves)."""
    status = build_agent_status()
    key = (status["online"], status["in_room"], status["voice_ready"], status["paused"], status["restarting"],
           status["host"], status["version"])
    if key == _status_tracker.emitted:
        return
    _status_tracker.emitted = key
    await emit_ui("agent_status", status)


async def _send_agent_command(data: Any, sid: str | None = None) -> None:
    """The room owner's agent_command (pause_agent): to one socket, else to room 'agent' (owner-room agents)."""
    await sio.emit("agent_command", data, to=sid or ROOM_AGENT)


def _agent_job_sockets() -> dict[str, float]:
    """{job_id: since} of the connected agent sockets serving the owner's room (the room owner's bridge_link)."""
    return {client.job_id: client.since for client in _clients.values() if _is_owner_agent(client) and client.job_id}


def _ui_presence() -> tuple[int, float | None]:
    """(UI sockets now, epoch s of the last UI socket): the room owner's on_demand demand (K-44 v9)."""
    count = sum(1 for client in _clients.values() if client.role in (ROLE_UI, ROLE_LEGACY))
    return count, (time.time() if count else _ui_last_seen)


@sio.on("connect") # type: ignore[untyped-decorator]
async def handle_connect(sid: str, environ: dict[str, Any], auth: dict[str, Any] | None = None) -> bool:
    """
    Socket.IO roles (C-SOCK-1): auth.agent_token = AGENT_TOKEN -> 'agent' (room 'agent'); a valid
    jarvis_session cookie in the handshake -> 'ui' (room 'ui', C-19); auth.token or ?token= =
    VORTEX_SECURITY_TOKEN -> 'legacy' in both rooms while LEGACY_TOKEN_AUTH=1.
    Anything else is refused (False) and audited as 'socket_auth_failed'. UI and legacy sockets get
    init_state; the first agent socket turns agent_status online.
    """
    internal = bridge_desktop.is_internal_environ(environ)
    session: bridge_auth.Principal | None = None
    if not internal and not (isinstance(auth, dict) and "agent_token" in auth):
        session = await owner_auth.session_from_environ(environ)
    if session is not None:
        role, reason = ROLE_UI, ""
    else:
        role, reason = _internal_role(auth) if internal else _socket_role(environ, auth)
        if role is None and reason == "no_credentials" and owner_auth.has_session_cookie(environ):
            reason = "bad_session"
    ip = _socket_ip(environ)
    if role is None:
        logger.warning("🚨 Socket refused (%s) from %s: %s", reason, ip, sid)
        if _refusal_audit.take():
            await stored("audit", store.audit("anonymous", "socket_auth_failed",
                                              {"reason": reason, "sid": sid}, ip), 0)
        return False

    client = SocketClient(role=role, ip=ip, internal=internal, session_id=session.session_id if session else "")
    if role == ROLE_AGENT and isinstance(auth, dict):
        client.host, client.version = _short(auth.get("agent_id")), _short(auth.get("version"))
        client.room, client.job_id = _agent_room(auth, internal), _short(auth.get("job_id"))
    _clients[sid] = client
    await _join_rooms(sid, client)
    logger.info("✅ Socket connected: role=%s sid=%s from %s%s%s", role, sid, ip,
                f" host={client.host}" if client.host else "",
                f" room={client.room or '-'}{' (internal)' if internal else ''}" if role == ROLE_AGENT else "")
    if role == ROLE_AGENT:
        if _is_owner_agent(client):
            if rooms.paused and rooms.mode != bridge_rooms.MODE_OBSERVE:
                # K-38 v9: a job that starts while the owner paused JARVIS ends at once
                await sio.emit("agent_command", dict(bridge_rooms.PAUSE_COMMAND), to=sid)
            rooms.kick("agent_connect")
            await _publish_agent_status()
        return True
    if role == ROLE_UI:  # a legacy socket may be an old agent, so only a real UI session counts as the owner
        await desktop.note_owner_activity()
    state: dict[str, Any] = await build_init_state()
    await sio.emit('init_state', state, room=sid)
    return True


@sio.on("disconnect") # type: ignore[untyped-decorator]
async def handle_disconnect(sid: str, *_reason: Any) -> None:
    """Forgets the socket; when an agent socket serving the owner's room leaves, agent_status is recomputed and goes
    to room 'ui' if it changed (the last one leaving turns it offline)."""
    global _ui_last_seen  # pylint: disable=global-statement
    client = _clients.pop(sid, None)
    logger.info("Socket disconnected: role=%s sid=%s", client.role if client else "unknown", sid)
    if client is not None and client.role in (ROLE_UI, ROLE_LEGACY):
        _ui_last_seen = time.time()
    if client is not None and _is_owner_agent(client):
        await _publish_agent_status()


@sio.on("agent_hello") # type: ignore[untyped-decorator]
async def handle_agent_hello(sid: str, data: Any) -> dict[str, str]:
    """The only event an agent socket may send: {version, host} (C-SOCK-1). Only an agent serving the owner's
    room updates agent_status."""
    client = _clients.get(sid)
    if client is None or client.role != ROLE_AGENT:
        return _rejected("not_allowed")
    if not client.hellos.take():
        return _rejected("rate_limited")
    if isinstance(data, dict):
        client.version = _short(data.get("version")) or client.version
        client.host = _short(data.get("host")) or client.host
    logger.info("Agent hello: host=%s version=%s sid=%s", client.host or "-", client.version or "-", sid)
    if _is_owner_agent(client):
        rooms.kick("agent_hello")
        await _publish_agent_status()
    return {"status": "ok"}


async def _ack_notification(payload: Any) -> None:
    """notification_ack {id} (K-74/K-77): the stored UI notification counts as delivered."""
    notification_id = payload.get("id") if isinstance(payload, dict) else None
    if isinstance(notification_id, str) and 0 < len(notification_id) <= 128:
        await stored("notification ack", store.ack_notification(notification_id, "ui"), False)


async def _sim_lookup_command(client: SocketClient, payload: Any) -> dict[str, str]:
    """ui_command sim_lookup (C-79, K-03/K-34): only the owner's authenticated UI socket searches, the
    payload is validated (legacy string or {query, purpose}; the query an 11-digit 03x number or a
    13-digit CNIC after normalization, the purpose required when the dialog was told so), the accepted
    command is audited masked and forwarded only to room 'agent'. Junk is answered with the
    sim_data_result shape the dialog reads (status validation_error), so it never spins into its 20 s
    timeout. The raw number and the purpose text are never logged or audited."""
    if client.role != ROLE_UI:
        await stored("audit", store.audit(client.role, "sim_lookup_refused",
                                          {"reason": "not_owner_ui"}, client.ip), 0)
        logger.warning("sim_lookup refused from role=%s: only the owner's UI searches", client.role)
        # F2: a legacy-token dashboard drops acks, so it also gets the shaped refusal the dialog
        # reads (else it spins into its 20 s timeout and blames the agent).
        await emit_ui("sim_data_result", {
            "records": [], "phone": "", "status": "validation_error",
            "message": SIM_LOGIN_REQUIRED_MESSAGE, "timestamp": time.time(),
            "ts": round(time.time(), 3)})
        return _rejected("not_allowed")
    requires_purpose = bool(await stored(
        "sim purpose flag", store.kv_get("sim_requires_purpose", False), False))
    clean, masked, reason, message = parse_sim_lookup(payload, requires_purpose)
    if reason:
        await stored("audit", store.audit("owner", "sim_lookup_refused",
                                          {"reason": reason, "query_masked": masked}, client.ip), 0)
        logger.info("sim_lookup rejected (%s) for %s", reason, masked or "-")
        # C79-R5/F5: 'timestamp' matches _emit_sim_data's shape; 'ts' stays for anything that read it.
        await emit_ui("sim_data_result", {"records": [], "phone": masked, "status": "validation_error",
                                          "message": message, "timestamp": time.time(),
                                          "ts": round(time.time(), 3)})
        return _rejected(reason)
    purpose_chars = len(clean["purpose"]) if isinstance(clean, dict) else 0
    await stored("audit", store.audit("owner", "sim_lookup",
                                      {"query_masked": masked, "purpose_chars": purpose_chars},
                                      client.ip), 0)
    logger.info("sim_lookup forwarded for %s (purpose %s)", masked, "set" if purpose_chars else "none")
    await sio.emit("agent_command", {"type": "sim_lookup", "payload": clean}, to=ROOM_AGENT)
    return {"status": "sent"}


@sio.on('ui_command') # type: ignore[untyped-decorator]
async def handle_ui_command(sid: str, data: Any) -> dict[str, str]:
    """
    Relays a dashboard command to room 'agent' (C-SOCK-1): only from UI or legacy sockets, at most 10 a
    second per socket, and only the allow-listed types. Ack {status: 'sent'} or {status: 'rejected', reason}.
    """
    client = _clients.get(sid)
    if client is None or client.role not in (ROLE_UI, ROLE_LEGACY):
        logger.warning("ui_command refused from role=%s sid=%s", client.role if client else "none", sid)
        return _rejected("not_allowed")
    if not client.commands.take():
        logger.debug("ui_command rate-limited for sid=%s", sid)
        return _rejected("rate_limited")
    cmd_type = data.get("type") if isinstance(data, dict) else None
    if not isinstance(cmd_type, str) or cmd_type not in UI_COMMAND_TYPES:
        logger.warning("ui_command rejected: unknown type '%s'", _short(cmd_type))
        return _rejected("unknown_type")
    if cmd_type == "chat" and isinstance(data.get("payload"), dict):
        bridge_manager.remember_chat_id(data["payload"].get("id"))
    await desktop.note_owner_activity()
    if cmd_type == "desktop_stop":
        # K-74 tighten-only Stop desktop: persisted, sent to the laptop and to the agent peers (stop_all).
        await desktop.ui_desktop_stop(by=client.role)
    elif cmd_type == "notification_ack":
        await _ack_notification(data.get("payload"))
    elif cmd_type == "confirm_response":  # C-29: checked, audited and forwarded there, never as received
        return await _confirm_response(client, data.get("payload"))
    elif cmd_type == "sim_lookup":  # C-79: validated, audited and forwarded there, never as received
        return await _sim_lookup_command(client, data.get("payload"))
    elif cmd_type == "pause_agent":
        # K-03/K-44 v9: only the room owner (always or on_demand mode) can hold a pause. It sets the flag and sends
        # its own pause_agent (payload.by 'bridge', the only one that ends the agent's job, K-60 v9); the UI's
        # command itself is never forwarded. Elsewhere (observe mode, the PC's local bridge) nothing would keep
        # the agent out or bring it back on Wake, so the command is refused and changes nothing.
        if not (rooms.enabled and rooms.mode != bridge_rooms.MODE_OBSERVE):
            logger.info("ui_command pause_agent refused: this bridge holds no pause (room owner %s)",
                        rooms.mode if rooms.enabled else "off")
            return _rejected(bridge_rooms.PAUSE_UNAVAILABLE)
        await rooms.set_paused(True, by=client.role)
        return {"status": "sent"}
    elif cmd_type == "start" and rooms.enabled:
        # K-03/K-44 v9: Wake clears the paused flag and runs an explicit ensure; in observe mode the flag is left
        # alone and Wake may only heal a room without an agent (the old token route's heal, never a delete). It is
        # still forwarded below. stop is only forwarded: it interrupts speech, never pauses.
        if rooms.mode != bridge_rooms.MODE_OBSERVE:
            await rooms.set_paused(False, by=client.role)
        else:
            rooms.kick(bridge_rooms.REASON_START, explicit=True, skip_confirm=True)
    await sio.emit('agent_command', data, to=ROOM_AGENT)
    return {"status": "sent"}


@sio.on('vision_frame') # type: ignore[untyped-decorator]
async def handle_vision_frame(sid: str, data: Any) -> dict[str, str]:
    """
    Relays a camera frame to room 'agent' (C-SOCK-1, K-05): only from UI or legacy sockets, at most one a
    second per socket and at most 600 KB of frame data.
    """
    client = _clients.get(sid)
    if client is None or client.role not in (ROLE_UI, ROLE_LEGACY):
        return _rejected("not_allowed")
    frame = data.get("frame") if isinstance(data, dict) else data
    if isinstance(frame, (str, bytes)) and len(frame) > VISION_FRAME_MAX_CHARS:
        return _rejected("too_large")
    if not client.frames.take():
        return _rejected("rate_limited")
    await sio.emit('agent_vision_frame', data, to=ROOM_AGENT)
    return {"status": "sent"}


# ---------------------------------------------------------------- Desktop Link ('/desktop', C-116)


@sio.on("connect", namespace=bridge_desktop.NAMESPACE) # type: ignore[untyped-decorator]
async def handle_desktop_connect(sid: str, environ: dict[str, Any], auth: Any = None) -> bool:
    """'/desktop' connect: the node's signed hello on the public listener, an agent peer's agent_hello on the
    internal one (K-70); see src/core/bridge_desktop.py."""
    return await desktop.on_connect(sid, environ, auth)


@sio.on("disconnect", namespace=bridge_desktop.NAMESPACE) # type: ignore[untyped-decorator]
async def handle_desktop_disconnect(sid: str, *reason: Any) -> None:
    await desktop.on_disconnect(sid, *reason)


@sio.on(bridge_desktop.EVENT_MSG, namespace=bridge_desktop.NAMESPACE) # type: ignore[untyped-decorator]
async def handle_desktop_msg(sid: str, data: Any = None) -> None:
    """One signed K-70 envelope (JSON text): verified, then relayed or, for a notice, consumed."""
    await desktop.on_msg(sid, data)


# As asyncio's create_server (uvicorn.run's path before C-116): SO_REUSEADDR only on POSIX. On Windows it would let a
# second local bridge bind a port that is in use instead of failing, and the UI and the agent could then reach
# different bridges.
_REUSE_ADDRESS = os.name == "posix" and sys.platform != "cygwin"


def _listen_socket(port: int) -> socket.socket:
    """A TCP socket bound to 0.0.0.0:port for uvicorn."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if _REUSE_ADDRESS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def main() -> None:
    """Serves the app on PUBLIC_PORT and, in the bridge container (JARVIS_ROLE=bridge), also on the internal port
    5002 (K-48 v4: compose never publishes it). One uvicorn server, so one lifespan and one Socket.IO server for
    both listeners. The local bridge on the PC (vortex.py) keeps the public port only."""
    try:
        sockets = [_listen_socket(PUBLIC_PORT)]
        if runtime_role() == ROLE_BRIDGE:
            sockets.append(_listen_socket(bridge_desktop.INTERNAL_PORT))
            # uvicorn's 'running on' line names only the public port
            logger.info("Internal listener on 0.0.0.0:%d (jarvis-network only, never published)",
                        bridge_desktop.INTERNAL_PORT)
    except OSError as exc:  # as uvicorn.run did: a port in use is one error line and exit code 1
        logger.error("Cannot listen: %s", exc)
        sys.exit(1)
    uvicorn.Server(uvicorn.Config(app_asgi, host="0.0.0.0", port=PUBLIC_PORT, proxy_headers=True,
                                  forwarded_allow_ips=TRUSTED_PROXIES)).run(sockets=sockets)


if __name__ == "__main__":
    main()
