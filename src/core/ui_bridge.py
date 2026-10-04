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
from src.core import bridge_auth, bridge_desktop, bridge_media, bridge_rooms, bridge_security
from src.core.bridge_security import NOTIFY_MAX_BYTES, NonceCache, NotifyAuthError, TokenBucket, same_secret
from src.core.bridge_store import (
    EVENT_LOG,
    EVENT_MEMORY,
    EVENT_TOOL,
    KV_DESKTOP_LINK,
    MESSAGE_PAGE_MAX,
    SEEN_NOTIFY,
    BridgeStore,
    bridge_db_path,
    event_retention_days,
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
    "voice_take_over", "voice_enroll", "feedback", "job_cancel", "open_job_artifact", "request_state",
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

# Proper initialization for rate limit window
app.state.rate_limit_window = []

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
}
# A SIM lookup answers within the provider's 10 s timeout. A stored loading state that this process
# did not see start, or that is older than this, lost its sim_data_result.
SIM_LOADING_MAX_S = 30.0

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


def update_rate_limit(request: Request) -> None:
    """Simple rate limit check for notify endpoint using app state."""
    now: float = time.time()
    window: list[float] = cast(list[float], getattr(request.app.state, "rate_limit_window", []))

    # Filter for last 60 seconds
    window = [ts for ts in window if now - ts < 60]

    if len(window) > 500:
        logger.warning("⚠️ RATE LIMIT TRIGGERED on /notify")
        raise HTTPException(status_code=429, detail="Throttling",
                            headers=bridge_security.retry_after_header(60 - (now - window[0])))

    window.append(now)
    request.app.state.rate_limit_window = window


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
    update_rate_limit(request) # Corrected to pass request context

    event_type: str | None = data.get("type")
    if not isinstance(event_type, str):
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
    }

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
        await emit_ui("sim_panel_open", payload)
    else:
        logger.warning("⚠️ UNKNOWN EVENT: %s", event_type)

    return {"status": "ok"}


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
    """Hourly: deletes event/log rows older than BRIDGE_EVENT_RETENTION_DAYS (0, the default,
    keeps them forever) and expired seen ids. Chat messages are never pruned."""
    retention_days = event_retention_days()
    logger.info("Event/log retention: %s", f"{retention_days:g} days" if retention_days
                else "kept forever (BRIDGE_EVENT_RETENTION_DAYS=0)")
    while True:
        try:
            deleted = await store.prune_events(retention_days)
            if deleted:
                logger.info("Retention: deleted %d event rows older than %g days",
                            deleted, retention_days)
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
