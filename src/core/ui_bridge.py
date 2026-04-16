"""
# ui_bridge.py
WebSocket server to bridge JARVIS core events to the React-based STONIX UI.
"""

import asyncio
import copy
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import socketio
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_config import config
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import security_manager
from services.utils.jarvis_telemetry import telemetry

# Validate on import/startup
config.validate()

logger = setup_logger("UI-BRIDGE")

VORTEX_SECURITY_TOKEN: str = str(config.security_token) # Ensure string type
ALLOWED_ORIGINS: list[str] = [o.strip() for o in config.allowed_origins if o.strip()]
if "https://jarvis-agent-three.vercel.app" not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append("https://jarvis-agent-three.vercel.app")
print(f"DEBUG: FINAL ALLOWED_ORIGINS: {ALLOWED_ORIGINS}")

sio: socketio.AsyncServer = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=ALLOWED_ORIGINS,
    logger=False,
    engineio_logger=False)


@asynccontextmanager
async def lifespan(_app_instance: FastAPI) -> AsyncGenerator[None, None]:
    """Lifecycle manager for the FastAPI application."""
    # Startup tasks
    vitals_task = asyncio.create_task(vitals_heartbeat())
    telemetry_task = asyncio.create_task(telemetry_heartbeat())
    speaking_task = asyncio.create_task(user_speaking_monitor())
    yield
    # Shutdown tasks
    vitals_task.cancel()
    telemetry_task.cancel()
    speaking_task.cancel()

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

class BridgeStateManager:
    """Thread-safe state manager for the UI Bridge."""
    def __init__(self) -> None:
        self.lock: asyncio.Lock = asyncio.Lock()
        self.state: dict[str, Any] = {
            "speaking": False,
            "user_speaking": False,
            "thinking": False,
            "muted": False,
            "wake_word_active": True,
            "last_transcription": "",
            "messages": [],
            "memories": [],
            "tool_logs": [],
            "active_persona": "jarvis",
            "vitals": {"cpu": 0, "ram": 0, "disk": 0},
            "telemetry": {"success_rate": 1.0, "avg_latency": 0.5, "status": "HEALTHY"},
            "location": {"city": "Detecting...", "lat": 0, "lng": 0},
            "runner_status": "online",
            "last_runner_contact": time.time(),
            "last_user_speak_pulse": 0.0,
            "vortex_logs": [],
            "voice_match": 0.0,
            "sim_records": [],
            "sim_loading": False,
        }

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

    async def find_message(self, msg_id: str) -> dict[str, Any] | None:
        async with self.lock:
            return next((m for m in self.state["messages"] if m["id"] == msg_id), None)

    async def update_message(self, msg_id: str, text: str) -> bool:
        async with self.lock:
            for m in self.state["messages"]:
                if m["id"] == msg_id:
                    m["text"] = text
                    return True
            return False

bridge_manager: BridgeStateManager = BridgeStateManager()


async def verify_vortex_request(request: Request, data: dict[str, Any]) -> None:
    """Verifies the token and signature for incoming requests."""
    token: str | None = request.headers.get("X-Vortex-Token")
    signature: str | None = request.headers.get("X-Vortex-Signature")

    if token != VORTEX_SECURITY_TOKEN:
        logger.warning(
            "UNAUTHORIZED /NOTIFY: Invalid Token from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not signature:
        logger.warning(
            "UNAUTHORIZED /NOTIFY: Missing Signature from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=401, detail="Missing Security Signature")

    json_payload: str = json.dumps(data, sort_keys=True)
    if not security_manager.verify_signature(json_payload, signature):
        logger.warning("🚫 FORGED /NOTIFY: Signature Mismatch")
        raise HTTPException(
            status_code=403, detail="Signature Mismatch")


def update_rate_limit(request: Request) -> None:
    """Simple rate limit check for notify endpoint using app state."""
    now: float = time.time()
    window: list[float] = cast(list[float], getattr(request.app.state, "rate_limit_window", []))

    # Filter for last 60 seconds
    window = [ts for ts in window if now - ts < 60]

    if len(window) > 500:
        logger.warning("⚠️ RATE LIMIT TRIGGERED on /notify")
        raise HTTPException(status_code=429, detail="Throttling")

    window.append(now)
    request.app.state.rate_limit_window = window


async def _handle_status_event(payload: Any) -> None:
    """Handle general status events."""
    await bridge_manager.update_state("speaking", payload == "START")

    state: dict[str, Any] = await bridge_manager.get_state()
    if state["speaking"]:
        await bridge_manager.update_state("thinking", False)

    await sio.emit("status_change", {
        "speaking": state["speaking"],
        "thinking": state.get("thinking", False),
    })


async def _handle_thinking_event(payload: Any) -> None:
    """Handle thinking state events."""
    is_thinking: bool = payload == "START"
    await bridge_manager.update_state("thinking", is_thinking)
    speaking: bool = cast(bool, await bridge_manager.get_field("speaking", False))
    await sio.emit("status_change", {
        "speaking": speaking,
        "thinking": is_thinking,
    })


async def _handle_transcription_event(payload: dict[str, Any]) -> None:
    """Handle transcription events with persistence and sync."""
    msg_id: str = cast(str, payload.get("id") or str(uuid.uuid4()))
    payload["id"] = msg_id

    logger.info("📝 Transcription: role=%s, id=%s",
                payload.get('role'), msg_id)

    updated: bool = await bridge_manager.update_message(msg_id, cast(str, payload.get("text", "")))
    if updated:
        await sio.emit("update_message", payload)
    else:
        await bridge_manager.append_log("messages", payload, limit=50)
        await sio.emit("new_message", payload)

    await sio.emit("transcription_update", {"text": payload.get("text", "")})


@app.post("/notify")
async def notify_bridge(data: dict[str, Any], request: Request) -> dict[str, str]:
    """
    Main notification endpoint for JARVIS updates.
    Routes events to connected UI clients after auth.
    """
    health_monitor.record_heartbeat("ui_bridge")
    # Using bridge_manager instead of the removed bridge_state
    await bridge_manager.update_state("last_runner_contact", time.time())

    await verify_vortex_request(request, data)
    update_rate_limit(request) # Corrected to pass request context

    event_type: str | None = data.get("type")
    if not isinstance(event_type, str):
        return {"status": "ignored", "reason": "missing_type"}
    payload: Any = data.get("payload")

    async def _handle_token(p: dict[str, Any]) -> None:
        await sio.emit("token_stream", p)

    async def _handle_vitals_event(p: dict[str, Any]) -> None:
        await sio.emit("vitals_update", p)

    async def _handle_reasoning(p: dict[str, Any]) -> None:
        await sio.emit("reasoning_update", p)

    async def _handle_intelligence(p: dict[str, Any]) -> None:
        await sio.emit("intelligence_update", p)

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
        await bridge_manager.update_state("location", payload)
        await sio.emit("location_update", payload)
    elif event_type == "mute_sync":
        await bridge_manager.update_state("muted", payload)
        await sio.emit("mute_update", payload)
    elif event_type == "wake_word_sync":
        await bridge_manager.update_state("wake_word_active", payload)
        await sio.emit("wake_word_update", payload)
    elif event_type == "vortex_log":
        await bridge_manager.append_log("vortex_logs", payload, limit=30)
        await sio.emit("new_log", payload)
    elif event_type == "user_speaking_sync":
        await bridge_manager.update_state("user_speaking", payload)
        if payload:
            await bridge_manager.update_state("last_user_speak_pulse", time.time())
        await sio.emit("user_status_change", {"speaking": payload})
    elif event_type == "voice_id_sync":
        confidence: float = payload.get("confidence", 0.0) if isinstance(payload, dict) else 0.0
        await bridge_manager.update_state("voice_match", confidence)
        await sio.emit("voice_match_update", payload)
    elif event_type == "persona_change":
        await bridge_manager.update_state("active_persona", payload)
        await sio.emit("persona_update", {"persona": payload})
    elif event_type == "task_alert":
        # Proactive task notification for the UI
        logger.info("🔔 Notifying UI of due task: %s", payload.get('description'))
        await sio.emit("task_notification", payload)
    elif event_type == "task_update":
        # General task list sync
        await sio.emit("task_sync", payload)
    elif event_type == "memory_update":
        await bridge_manager.append_log("memories", payload, limit=10)
        await sio.emit("memory_sync", payload)
    elif event_type == "tool_sync":
        await bridge_manager.append_log("tool_logs", payload, limit=20)
        await sio.emit("tool_update", payload)
    elif event_type == "sim_data_result":
        records: list[Any] = payload.get("records", []) if isinstance(payload, dict) else []
        await bridge_manager.update_state("sim_records", records)
        await bridge_manager.update_state("sim_loading", False)
        await sio.emit("sim_data_result", payload)
    elif event_type == "sim_data_loading":
        await bridge_manager.update_state("sim_loading", True)
        await bridge_manager.update_state("sim_records", [])
        await sio.emit("sim_data_loading", payload)
    else:
        logger.warning("⚠️ UNKNOWN EVENT: %s", event_type)

    return {"status": "ok"}


@app.get("/health")
async def get_health() -> dict[str, Any]:
    """Returns detailed system health state."""
    return health_monitor.generate_health_report()


@app.get("/telemetry/production")
async def get_production_telemetry() -> dict[str, Any]:
    """Returns real-world interaction metrics for the dashboard."""
    return telemetry.get_production_metrics()


@app.post("/feedback")
async def record_user_feedback(data: dict[str, Any], request: Request) -> dict[str, str]:
    """Receives user feedback and updates adaptive engine."""
    token: str | None = request.headers.get("X-Vortex-Token")
    if token != VORTEX_SECURITY_TOKEN:
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
            await sio.emit("vitals_update", vitals_pkg)
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
            await sio.emit("telemetry_update", metrics)
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
                    await sio.emit("user_status_change", {"speaking": False})
        except asyncio.CancelledError:
            logger.info("User speaking monitor task cancelled.")
            break
        except (KeyError, ValueError, TypeError) as e:
            logger.error("User speaking monitor data error: %s", e)
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Unexpected User speaking monitor error")
        
        # 🔋 PERFORMANCE: Slightly longer check interval
        await asyncio.sleep(1.5)


@sio.on("connect") # type: ignore[untyped-decorator]
async def handle_connect(sid: str, environ: dict[str, Any], auth: dict[str, Any] | None = None) -> bool:
    """
    Handle new UI connections with Zero-Trust Authentication.
    Accepts token via 'auth' payload or query string for maximum compatibility.
    """
    token: str | None = None

    # 1. Check Auth Payload (Recommended)
    if auth and isinstance(auth, dict):
        token = auth.get("token")

    # 2. Fallback to Query String
    if not token:
        query_string = environ.get('QUERY_STRING', '')
        params = dict(qc.split('=') for qc in query_string.split('&') if '=' in qc)
        token = params.get('token')

    if token != VORTEX_SECURITY_TOKEN:
        logger.warning("🚨 UNAUTHORIZED UI CONNECT attempt from SID: %s", sid)
        # Returning False refuses the connection in python-socketio
        return False

    logger.info("✅ UI Authenticated & Connected: %s", sid)
    state: dict[str, Any] = await bridge_manager.get_state()
    # Frontend expects 'persona' key, not 'active_persona'
    state["persona"] = state.get("active_persona", "jarvis")
    await sio.emit('init_state', state, room=sid)
    return True


@sio.on("disconnect") # type: ignore[untyped-decorator]
async def handle_disconnect(sid: str) -> None:
    """Handle UI disconnections."""
    logger.info("UI Disconnected: %s", sid)


@sio.on('ui_command') # type: ignore[untyped-decorator]
async def handle_ui_command(_sid: str, data: Any) -> dict[str, str]:
    """Relay commands from UI to the Agent."""
    await sio.emit('agent_command', data)
    return {"status": "success"}


@sio.on('vision_frame') # type: ignore[untyped-decorator]
async def handle_vision_frame(_sid: str, data: Any) -> dict[str, str]:
    """Relay camera frame from UI to the Agent."""
    await sio.emit('agent_vision_frame', data)
    return {"status": "success"}


if __name__ == "__main__":
    uvicorn.run(app_asgi, host="0.0.0.0", port=5001)
