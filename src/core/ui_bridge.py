"""
# ui_bridge.py
WebSocket server to bridge JARVIS core events to the React-based STONIX UI.

What must survive a restart (chat messages, logs, memories, tool logs, persona, mute, wake word,
location, voice match, SIM results) lives in the SQLite store (src/core/bridge_store.py,
C-STATE-1); BridgeStateManager keeps only volatile flags.

Agent link v2 (C-14, C-SOCK-1 / C-NOTIFY-1): every Socket.IO client gets a role at connect. An agent
(auth.agent_token = AGENT_TOKEN) joins room 'agent'; the legacy shared token joins BOTH rooms while
LEGACY_TOKEN_AUTH=1 (today's broadcast, so the current UI and PC agent keep working); C-19 adds the
session cookie for role 'ui' (room 'ui'). ui_command and vision_frame go only to room 'agent', every
notify and periodic event only to room 'ui'. POST /notify is authenticated before it changes anything.
"""

import asyncio
import copy
import ipaddress
import math
import os
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast
from urllib.parse import unquote

import socketio
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_config import ROLE_BRIDGE, config
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_telemetry import telemetry
from src.core import bridge_security
from src.core.bridge_security import NonceCache, NotifyAuthError, TokenBucket, same_secret
from src.core.bridge_store import (
    EVENT_LOG,
    EVENT_MEMORY,
    EVENT_TOOL,
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
VISION_FRAME_MAX_CHARS = 600 * 1024
_SHORT_TEXT = 64


@asynccontextmanager
async def lifespan(_app_instance: FastAPI) -> AsyncGenerator[None, None]:
    """Lifecycle manager for the FastAPI application."""
    # Startup tasks
    store_task = asyncio.create_task(open_store())
    vitals_task = asyncio.create_task(vitals_heartbeat())
    telemetry_task = asyncio.create_task(telemetry_heartbeat())
    speaking_task = asyncio.create_task(user_speaking_monitor())
    retention_task = asyncio.create_task(store_retention_loop())
    yield
    # Shutdown tasks
    vitals_task.cancel()
    telemetry_task.cancel()
    speaking_task.cancel()
    retention_task.cancel()
    store_task.cancel()
    try:
        await store.close()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Bridge store did not close cleanly", exc_info=True)

app: FastAPI = FastAPI(lifespan=lifespan)

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


def _peer_ip(peer: str, real_ip: str | None) -> str:
    """The client address for logs and audit rows: nginx's X-Real-IP when the peer is the proxy (a
    loopback or private address), else the peer itself."""
    try:
        behind_proxy = bool(peer) and (ipaddress.ip_address(peer).is_loopback
                                       or ipaddress.ip_address(peer).is_private)
    except ValueError:
        behind_proxy = False
    real = (real_ip or "").strip()
    return real[:_SHORT_TEXT] if real and behind_proxy else (peer or "unknown")


def _request_ip(request: Request) -> str:
    return _peer_ip(request.client.host if request.client else "", request.headers.get("x-real-ip"))


def _legacy_header_ok(request: Request) -> bool:
    """X-Vortex-Token equals the legacy token, and legacy auth is still on (C-AUTH-1 transition)."""
    return config.legacy_token_auth and same_secret(request.headers.get("X-Vortex-Token"),
                                                    VORTEX_SECURITY_TOKEN)


async def emit_ui(event: str, data: Any = None) -> None:
    """Every notify and periodic event goes only to room 'ui' (UI sockets and legacy sockets)."""
    await sio.emit(event, data, to=ROOM_UI)


# Replay protection for signed notifies: in memory for speed, in the store across bridge restarts.
_notify_nonces = NonceCache()


async def _mark_nonce_seen(nonce: str) -> bool:
    """False when the store saw this nonce before (a replay across a restart); a failing store counts
    as unseen, since the in-memory cache has already checked it."""
    return await stored("notify nonce", store.mark_seen(SEEN_NOTIFY, nonce), True)


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

    await emit_ui("new_message" if status == "new" else "update_message", msg)
    await emit_ui("transcription_update", {"text": msg["text"]})


@app.post("/notify")
async def notify_bridge(request: Request) -> Any:
    """
    Main notification endpoint for JARVIS updates (C-NOTIFY-1).
    The raw body is authenticated first (agent HMAC v2, or the legacy headers while
    LEGACY_TOKEN_AUTH=1); only then does the runner contact count and the event reach room 'ui'.
    """
    try:
        raw = await bridge_security.read_body(request)
        auth = await bridge_security.verify_notify_request(
            request, raw, agent_token=config.agent_token, legacy_token=VORTEX_SECURITY_TOKEN,
            legacy_enabled=config.legacy_token_auth, nonces=_notify_nonces,
            mark_seen=_mark_nonce_seen)
    except NotifyAuthError as exc:
        logger.warning("🚫 /notify refused (%d %s) from %s", exc.status, exc.error, _request_ip(request))
        return JSONResponse({"error": exc.error}, status_code=exc.status, headers=exc.headers)

    health_monitor.record_heartbeat("ui_bridge")
    await bridge_manager.update_state("last_runner_contact", time.time())
    update_rate_limit(request) # Corrected to pass request context

    data: dict[str, Any] = auth.data
    event_type: str | None = data.get("type")
    if not isinstance(event_type, str):
        return {"status": "ignored", "reason": "missing_type"}
    payload: Any = data.get("payload")

    async def _handle_token(p: dict[str, Any]) -> None:
        await emit_ui("token_stream", p)

    async def _handle_vitals_event(p: dict[str, Any]) -> None:
        await emit_ui("vitals_update", p)

    async def _handle_reasoning(p: dict[str, Any]) -> None:
        await emit_ui("reasoning_update", p)

    async def _handle_intelligence(p: dict[str, Any]) -> None:
        await emit_ui("intelligence_update", p)

    handlers: dict[str, Any] = {
        "status": _handle_status_event,
        "thinking": _handle_thinking_event,
        "transcription": _handle_transcription_event,
        "token": _handle_token,
        "vitals": _handle_vitals_event,
        "reasoning": _handle_reasoning,
        "intelligence_sync": _handle_intelligence,
        "intelligence_update": _handle_intelligence,
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


@app.get("/health")
async def get_health() -> dict[str, Any]:
    """Returns detailed system health state."""
    return health_monitor.generate_health_report()


@app.get("/messages")
async def get_messages(request: Request, before: float | None = None,
                       limit: int = MESSAGE_PAGE_MAX, cursor: str | None = None) -> dict[str, Any]:
    """
    Chat history (C-MSG-1): up to `limit` (1..100) messages older than `before` (epoch s or ms),
    ascending, has_more, and next_cursor for the dashboard's pager. `cursor` takes a previous
    next_cursor, which names the page's oldest message exactly, so messages that share a ts are
    never skipped; `before` wins when both are given.
    Auth: X-Vortex-Token header (legacy token, until the session login of OP-17).
    """
    if not _legacy_header_ok(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
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
async def livekit_token(request: Request, identity: str = "web-user") -> dict[str, str]:
    """
    Issue a LiveKit access token for the browser voice client.
    Auth: X-Vortex-Token header (same as UI socket auth).
    """
    if not _legacy_header_ok(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

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
        raise HTTPException(status_code=503, detail="LiveKit keys not configured")

    safe_identity = "".join(c for c in identity if c.isalnum() or c in "-_")[:64] or "web-user"
    try:
        from livekit import api as lk_api  # pylint: disable=import-outside-toplevel

        # Ensure room exists with an agent job before the browser joins.
        # Without this, Voice can show CONNECTED while the agent is gone
        # (empty room after idle/departure timeout) — chat + speech both fail.
        internal_ws = os.getenv("LIVEKIT_URL", "")
        api_http = internal_ws.replace("ws://", "http://").replace("wss://", "https://")
        if api_http:
            try:
                lk = lk_api.LiveKitAPI(api_http, api_key, api_secret)
                try:
                    rooms = await lk.room.list_rooms(lk_api.ListRoomsRequest(names=[room_name]))
                    need_dispatch = True
                    if rooms.rooms:
                        parts = await lk.room.list_participants(
                            lk_api.ListParticipantsRequest(room=room_name)
                        )
                        has_agent = any(
                            (getattr(p, "kind", None) and str(p.kind).endswith("AGENT"))
                            or str(getattr(p, "identity", "")).startswith("agent-")
                            for p in parts.participants
                        )
                        need_dispatch = not has_agent
                    if need_dispatch:
                        if rooms.rooms:
                            try:
                                await lk.room.delete_room(lk_api.DeleteRoomRequest(room=room_name))
                            except Exception:  # pylint: disable=broad-exception-caught
                                pass
                        await lk.room.create_room(
                            lk_api.CreateRoomRequest(
                                name=room_name,
                                empty_timeout=3600,
                                departure_timeout=3600,
                                max_participants=10,
                                agents=[lk_api.RoomAgentDispatch(agent_name="")],
                            )
                        )
                        logger.info("LiveKit room '%s' ensured with agent dispatch", room_name)
                finally:
                    await lk.aclose()
            except Exception as ensure_exc:  # pylint: disable=broad-exception-caught
                logger.warning("LiveKit room ensure skipped: %s", ensure_exc)

        access = (
            lk_api.AccessToken(api_key, api_secret)
            .with_identity(safe_identity)
            .with_name(safe_identity)
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
        jwt_token = access.to_jwt()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("LiveKit token mint failed")
        raise HTTPException(status_code=500, detail=f"Token error: {exc}") from exc

    return {
        "token": jwt_token,
        "url": public_url,
        "room": room_name,
    }


@app.get("/telemetry/production")
async def get_production_telemetry() -> dict[str, Any]:
    """Returns real-world interaction metrics for the dashboard."""
    return telemetry.get_production_metrics()


@app.post("/feedback")
async def record_user_feedback(data: dict[str, Any], request: Request) -> dict[str, str]:
    """Receives user feedback and updates adaptive engine."""
    if not _legacy_header_ok(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    intent: str | None = data.get("intent")
    score: int = data.get("score", 0)
    strategy: str | None = data.get("strategy")

    if intent and strategy:
        adaptive_engine.record_feedback(intent, strategy, score)
        logger.info("Feedback recorded: %s (Score: %s)", intent, score)

    return {"status": "recorded"}


async def vitals_heartbeat() -> None:
    """Update real system vitals via JarvisHealthMonitor."""
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
            await emit_ui("vitals_update", vitals_pkg)
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
    commands: TokenBucket = field(default_factory=lambda: TokenBucket(UI_COMMANDS_PER_S))
    frames: TokenBucket = field(default_factory=lambda: TokenBucket(VISION_FRAMES_PER_S))


_clients: dict[str, SocketClient] = {}
# Audit rows for refused sockets: a steady trickle, so a flood of bad connects cannot fill the disk.
_refusal_audit = TokenBucket(1.0, 30.0)


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
    legacy token counts only while LEGACY_TOKEN_AUTH=1. C-19 adds the jarvis_session cookie (role 'ui')."""
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


def _socket_ip(environ: Mapping[str, Any]) -> str:
    scope = environ.get("asgi.scope")
    peer = scope.get("client") if isinstance(scope, dict) else None
    return _peer_ip(str(peer[0]) if peer else "", environ.get("HTTP_X_REAL_IP"))


async def _join_rooms(sid: str, client: SocketClient) -> None:
    """Puts the socket in its role's rooms and records the role in its Socket.IO session."""
    try:
        for room in ROLE_ROOMS[client.role]:
            await sio.enter_room(sid, room)
        await sio.save_session(sid, {"role": client.role, "ip": client.ip, "since": client.since})
    except (KeyError, ValueError):  # no such live socket (it has already gone)
        logger.debug("Socket %s left before joining its rooms", sid)


def _agent_status(client: SocketClient) -> dict[str, Any]:
    """agent_status for room 'ui' while an agent socket is connected (C-SOCK-1); C-23 adds voice_ready,
    paused and heartbeats."""
    return {"online": True, "since": client.since, "host": client.host, "version": client.version}


@sio.on("connect") # type: ignore[untyped-decorator]
async def handle_connect(sid: str, environ: dict[str, Any], auth: dict[str, Any] | None = None) -> bool:
    """
    Socket.IO roles (C-SOCK-1): auth.agent_token = AGENT_TOKEN -> 'agent' (room 'agent');
    auth.token or ?token= = VORTEX_SECURITY_TOKEN -> 'legacy' in both rooms while LEGACY_TOKEN_AUTH=1.
    Anything else is refused (False) and audited as 'socket_auth_failed'. UI and legacy sockets get
    init_state; the first agent socket turns agent_status online.
    """
    role, reason = _socket_role(environ, auth)
    ip = _socket_ip(environ)
    if role is None:
        logger.warning("🚨 Socket refused (%s) from %s: %s", reason, ip, sid)
        if _refusal_audit.take():
            await stored("audit", store.audit("anonymous", "socket_auth_failed",
                                              {"reason": reason, "sid": sid}, ip), 0)
        return False

    client = SocketClient(role=role, ip=ip)
    if role == ROLE_AGENT and isinstance(auth, dict):
        client.host, client.version = _short(auth.get("agent_id")), _short(auth.get("version"))
    _clients[sid] = client
    await _join_rooms(sid, client)
    logger.info("✅ Socket connected: role=%s sid=%s from %s%s", role, sid, ip,
                f" host={client.host}" if client.host else "")
    if role == ROLE_AGENT:
        if sum(1 for c in _clients.values() if c.role == ROLE_AGENT) == 1:
            await emit_ui("agent_status", _agent_status(client))
        return True
    state: dict[str, Any] = await build_init_state()
    await sio.emit('init_state', state, room=sid)
    return True


@sio.on("disconnect") # type: ignore[untyped-decorator]
async def handle_disconnect(sid: str, *_reason: Any) -> None:
    """Forgets the socket; when the last agent socket leaves, agent_status goes offline in room 'ui'."""
    client = _clients.pop(sid, None)
    logger.info("Socket disconnected: role=%s sid=%s", client.role if client else "unknown", sid)
    if client is not None and client.role == ROLE_AGENT and not any(
            c.role == ROLE_AGENT for c in _clients.values()):
        await emit_ui("agent_status", {"online": False, "since": time.time()})


@sio.on("agent_hello") # type: ignore[untyped-decorator]
async def handle_agent_hello(sid: str, data: Any) -> dict[str, str]:
    """The only event an agent socket may send: {version, host} (C-SOCK-1)."""
    client = _clients.get(sid)
    if client is None or client.role != ROLE_AGENT:
        return _rejected("not_allowed")
    if isinstance(data, dict):
        client.version = _short(data.get("version")) or client.version
        client.host = _short(data.get("host")) or client.host
    logger.info("Agent hello: host=%s version=%s sid=%s", client.host or "-", client.version or "-", sid)
    await emit_ui("agent_status", _agent_status(client))
    return {"status": "ok"}


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


if __name__ == "__main__":
    uvicorn.run(app_asgi, host="0.0.0.0", port=5001)
