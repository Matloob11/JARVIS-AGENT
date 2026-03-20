"""
# ui_bridge.py
WebSocket server to bridge JARVIS core events to the React-based STONIX UI.
"""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

import socketio
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from services.utils.jarvis_config import config
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import security_manager
from services.utils.jarvis_telemetry import telemetry
from services.utils.jarvis_adaptive import adaptive_engine

# Validate on import/startup
config.validate()

logger = setup_logger("UI-BRIDGE")

VORTEX_SECURITY_TOKEN = config.security_token
ALLOWED_ORIGINS = config.allowed_origins

sio = socketio.AsyncServer(
    async_mode='asgi', cors_allowed_origins=ALLOWED_ORIGINS)


@asynccontextmanager
async def lifespan(_app_instance: FastAPI):
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

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app_asgi = socketio.ASGIApp(sio, other_asgi_app=app)

# State Management
 
class BridgeStateManager:
    """Thread-safe state manager for the UI Bridge."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.state = {
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
            "voice_match": 0.0
        }
 
    async def get_state(self):
        async with self.lock:
            return dict(self.state)
 
    async def update_state(self, key, value):
        async with self.lock:
            self.state[key] = value
 
    async def update_nested(self, parent_key, nested_key, value):
        async with self.lock:
            if parent_key in self.state and isinstance(self.state[parent_key], dict):
                self.state[parent_key][nested_key] = value
 
    async def append_log(self, key, item, limit=30):
        async with self.lock:
            if key not in self.state:
                self.state[key] = []
            self.state[key].append(item)
            if len(self.state[key]) > limit:
                self.state[key].pop(0)

    async def get_field(self, key, default=None):
        async with self.lock:
            return self.state.get(key, default)

    async def find_message(self, msg_id):
        async with self.lock:
            return next((m for m in self.state["messages"] if m["id"] == msg_id), None)

    async def update_message(self, msg_id, text):
        async with self.lock:
            for m in self.state["messages"]:
                if m["id"] == msg_id:
                    m["text"] = text
                    return True
            return False

bridge_manager = BridgeStateManager()


async def verify_vortex_request(request: Request, data: dict):
    """Verifies the token and signature for incoming requests."""
    token = request.headers.get("X-Vortex-Token")
    signature = request.headers.get("X-Vortex-Signature")

    if token != VORTEX_SECURITY_TOKEN:
        logger.warning(
            "UNAUTHORIZED /NOTIFY: Invalid Token from %s",
            request.client.host if request.client else "unknown"
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not signature:
        logger.warning(
            "UNAUTHORIZED /NOTIFY: Missing Signature from %s",
            request.client.host if request.client else "unknown"
        )
        raise HTTPException(
            status_code=401, detail="Missing Security Signature")

    json_payload = json.dumps(data, sort_keys=True)
    if not security_manager.verify_signature(json_payload, signature):
        logger.warning("🚫 FORGED /NOTIFY: Signature Mismatch")
        raise HTTPException(
            status_code=403, detail="Signature Mismatch")


def update_rate_limit():
    """Simple rate limit check for notify endpoint."""
    now = time.time()
    if not hasattr(app, "rate_limit_window"):
        app.rate_limit_window = []
    app.rate_limit_window = [
        ts for ts in app.rate_limit_window if now - ts < 60]

    if len(app.rate_limit_window) > 500:
        logger.warning("⚠️ RATE LIMIT TRIGGERED on /notify")
        raise HTTPException(status_code=429, detail="Throttling")

    app.rate_limit_window.append(now)


async def _handle_status_event(payload):
    """Handle general status events."""
    await bridge_manager.update_state("speaking", payload == "START")
    
    state = await bridge_manager.get_state()
    if state["speaking"]:
        await bridge_manager.update_state("thinking", False)
        
    await sio.emit("status_change", {
        "speaking": state["speaking"],
        "thinking": state["thinking"]
    })


async def _handle_thinking_event(payload):
    """Handle thinking state events."""
    is_thinking = payload == "START"
    await bridge_manager.update_state("thinking", is_thinking)
    speaking = await bridge_manager.get_field("speaking", False)
    await sio.emit("status_change", {
        "speaking": speaking,
        "thinking": is_thinking
    })


async def _handle_transcription_event(payload):
    """Handle transcription events with persistence and sync."""
    msg_id = payload.get("id") or str(uuid.uuid4())
    payload["id"] = msg_id
 
    logger.info("📝 Transcription: role=%s, id=%s",
                payload.get('role'), msg_id)
 
    updated = await bridge_manager.update_message(msg_id, payload["text"])
    if updated:
        await sio.emit("update_message", payload)
    else:
        await bridge_manager.append_log("messages", payload, limit=50)
        await sio.emit("new_message", payload)
 
    await sio.emit("transcription_update", {"text": payload["text"]})


@app.post("/notify")
async def notify_bridge(data: dict, request: Request):
    """
    Main notification endpoint for JARVIS updates.
    Routes events to connected UI clients after auth.
    """
    health_monitor.record_heartbeat("ui_bridge")
    # Using bridge_manager instead of the removed bridge_state
    await bridge_manager.update_state("last_runner_contact", time.time())

    await verify_vortex_request(request, data)
    update_rate_limit()

    event_type = data.get("type")
    payload = data.get("payload")

    async def _handle_token(p):
        await sio.emit("token_stream", p)

    async def _handle_vitals_event(p):
        await sio.emit("vitals_update", p)

    async def _handle_reasoning(p):
        await sio.emit("reasoning_update", p)

    async def _handle_intelligence(p):
        await sio.emit("intelligence_update", p)

    handlers = {
        "status": _handle_status_event,
        "thinking": _handle_thinking_event,
        "transcription": _handle_transcription_event,
        "token": _handle_token,
        "vitals": _handle_vitals_event,
        "reasoning": _handle_reasoning,
        "intelligence_sync": _handle_intelligence,
        "intelligence_update": _handle_intelligence
    }

    if event_type in handlers and handlers[event_type]:
        await handlers[event_type](payload)
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
        confidence = payload.get("confidence", 0.0)
        await bridge_manager.update_state("voice_match", confidence)
        await sio.emit("voice_match_update", payload)
    elif event_type == "persona_change":
        await bridge_manager.update_state("active_persona", payload)
        await sio.emit("persona_update", {"persona": payload})
    elif event_type == "memory_update":
        await bridge_manager.append_log("memories", payload, limit=10)
        await sio.emit("memory_sync", payload)
    elif event_type == "tool_sync":
        await bridge_manager.append_log("tool_logs", payload, limit=20)
        await sio.emit("tool_update", payload)
    else:
        logger.warning("⚠️ UNKNOWN EVENT: %s", event_type)

    return {"status": "ok"}


@app.get("/health")
async def get_health():
    """Returns detailed system health state."""
    return health_monitor.generate_health_report()


@app.get("/telemetry/production")
async def get_production_telemetry():
    """Returns real-world interaction metrics for the dashboard."""
    return telemetry.get_production_metrics()


@app.post("/feedback")
async def record_user_feedback(data: dict, request: Request):
    """Receives user feedback and updates adaptive engine."""
    token = request.headers.get("X-Vortex-Token")
    if token != VORTEX_SECURITY_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    intent = data.get("intent")
    score = data.get("score", 0)
    strategy = data.get("strategy")

    if intent and strategy:
        adaptive_engine.record_feedback(intent, strategy, score)
        logger.info("Feedback recorded: %s (Score: %s)", intent, score)

    return {"status": "recorded"}


async def vitals_heartbeat():
    """Update real system vitals via JarvisHealthMonitor."""
    while True:
        try:
            health_monitor.record_heartbeat("ui_bridge")
            report = health_monitor.generate_health_report()
            vitals = report["vitals"]
            
            vitals_pkg = {
                "cpu": vitals.get("cpu", {}).get("usage", 0),
                "ram": vitals.get("memory", {}).get("percent", 0),
                "disk": vitals.get("disk", {}).get("percent", 0)
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
        await asyncio.sleep(2.0)


async def telemetry_heartbeat():
    """Periodically push production metrics to the UI."""
    while True:
        try:
            metrics = telemetry.get_production_metrics()
            await bridge_manager.update_state("telemetry", metrics)
            await sio.emit("telemetry_update", metrics)
        except asyncio.CancelledError:
            logger.info("Telemetry heartbeat task cancelled.")
            break
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Telemetry Heartbeat error: %s", e)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected Telemetry Heartbeat error")
        await asyncio.sleep(10.0)


async def user_speaking_monitor():
    """Reset speaking state if no pulse received for >1.5 seconds."""
    while True:
        try:
            state = await bridge_manager.get_state()
            if state["user_speaking"]:
                if time.time() - state["last_user_speak_pulse"] > 1.5:
                    await bridge_manager.update_state("user_speaking", False)
                    await sio.emit("user_status_change", {"speaking": False})
        except asyncio.CancelledError:
            logger.info("User speaking monitor task cancelled.")
            break
        except (KeyError, ValueError, TypeError) as e:
            logger.error("User speaking monitor data error: %s", e)
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Unexpected User speaking monitor error")
        await asyncio.sleep(0.5)


@sio.on("connect")
async def handle_connect(sid, *_args, **_kwargs):
    """Handle new UI connections."""
    logger.info("✅ UI Connected: %s", sid)
    state = await bridge_manager.get_state()
    # Frontend expects 'persona' key, not 'active_persona'
    state["persona"] = state.get("active_persona", "jarvis")
    await sio.emit('init_state', state, room=sid)


@sio.on("disconnect")
async def handle_disconnect(sid):
    """Handle UI disconnections."""
    logger.info("UI Disconnected: %s", sid)


@sio.on('ui_command')
async def handle_ui_command(_sid, data):
    """Relay commands from UI to the Agent."""
    await sio.emit('agent_command', data)
    return {"status": "success"}


@sio.on('vision_frame')
async def handle_vision_frame(_sid, data):
    """Relay camera frame from UI to the Agent."""
    await sio.emit('agent_vision_frame', data)
    return {"status": "success"}


if __name__ == "__main__":
    uvicorn.run(app_asgi, host="127.0.0.1", port=5001)
