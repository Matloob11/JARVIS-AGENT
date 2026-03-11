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
    visuals_task = asyncio.create_task(visuals_heartbeat())
    telemetry_task = asyncio.create_task(telemetry_heartbeat())
    yield
    # Shutdown tasks
    vitals_task.cancel()
    visuals_task.cancel()
    telemetry_task.cancel()

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

bridge_state = {
    "speaking": False,
    "thinking": False,
    "last_transcription": "",
    "messages": [],
    "memories": [],
    "tool_logs": [],
    "active_persona": "jarvis",
    "vitals": {"cpu": 0, "ram": 0, "disk": 0},
    "telemetry": {"success_rate": 1.0, "avg_latency": 0.5, "status": "HEALTHY"},
    "runner_status": "online",
    "last_runner_contact": time.time()
}


async def verify_vortex_request(request: Request, data: dict):
    """Verifies the token and signature for incoming requests."""
    token = request.headers.get("X-Vortex-Token")
    signature = request.headers.get("X-Vortex-Signature")

    if token != VORTEX_SECURITY_TOKEN:
        logger.warning(
            "🚫 UNAUTHORIZED /NOTIFY: Invalid Token. Headers: %s",
            dict(request.headers)
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not signature:
        logger.warning(
            "🚫 UNAUTHORIZED /NOTIFY: Missing Signature. Headers: %s",
            dict(request.headers)
        )
        raise HTTPException(
            status_code=401, detail="Missing Security Signature")

    json_payload = json.dumps(data, sort_keys=True)
    if signature != "INTERNAL" and not security_manager.verify_signature(json_payload, signature):
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

    if len(app.rate_limit_window) > 100:
        logger.warning("⚠️ RATE LIMIT TRIGGERED on /notify")
        raise HTTPException(status_code=429, detail="Throttling")

    app.rate_limit_window.append(now)


async def _handle_status_event(payload):
    """Handle status change events."""
    bridge_state["speaking"] = payload == "START"
    if bridge_state["speaking"]:
        bridge_state["thinking"] = False
    await sio.emit("status_change", {
        "speaking": bridge_state["speaking"],
        "thinking": bridge_state["thinking"]
    })


async def _handle_thinking_event(payload):
    """Handle thinking state events."""
    bridge_state["thinking"] = payload == "START"
    await sio.emit("status_change", {
        "speaking": bridge_state["speaking"],
        "thinking": bridge_state["thinking"]
    })


async def _handle_transcription_event(payload):
    """Handle transcription events with persistence and sync."""
    msg_id = payload.get("id") or str(uuid.uuid4())
    payload["id"] = msg_id

    logger.info("📝 Transcription: role=%s, id=%s",
                payload.get('role'), msg_id)

    existing_msg = next(
        (m for m in bridge_state["messages"] if m["id"] == msg_id), None)
    if existing_msg:
        existing_msg["text"] = payload["text"]
        await sio.emit("update_message", payload)
    else:
        bridge_state["messages"].append(payload)
        if len(bridge_state["messages"]) > 50:
            bridge_state["messages"].pop(0)
        await sio.emit("new_message", payload)

    await sio.emit("transcription_update", {"text": payload["text"]})


@app.post("/notify")
async def notify_bridge(data: dict, request: Request):
    """
    Main notification endpoint for JARVIS updates.
    Routes events to connected UI clients after auth.
    """
    health_monitor.record_heartbeat("ui_bridge")
    bridge_state["last_runner_contact"] = time.time()

    await verify_vortex_request(request, data)
    update_rate_limit()

    event_type = data.get("type")
    payload = data.get("payload")

    handlers = {
        "status": _handle_status_event,
        "thinking": _handle_thinking_event,
        "transcription": _handle_transcription_event,
        "token": lambda p: sio.emit("token_stream", p),
        "vitals": lambda p: sio.emit("vitals_update", p),
        "reasoning": lambda p: sio.emit("reasoning_update", p),
        "intelligence_sync": lambda p: sio.emit("intelligence_sync", p),
        "intelligence_update": lambda p: sio.emit("intelligence_sync", p)
    }

    if event_type in handlers:
        await handlers[event_type](payload)
    elif event_type == "persona_change":
        bridge_state["active_persona"] = payload
        await sio.emit("persona_update", {"persona": payload})
    elif event_type == "memory_update":
        bridge_state["memories"].append(payload)
        if len(bridge_state["memories"]) > 10:
            bridge_state["memories"].pop(0)
        await sio.emit("memory_sync", payload)
    elif event_type == "tool_sync":
        bridge_state["tool_logs"].append(payload)
        if len(bridge_state["tool_logs"]) > 20:
            bridge_state["tool_logs"].pop(0)
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
            bridge_state["vitals"] = {
                "cpu": vitals.get("cpu", {}).get("usage", 0),
                "ram": vitals.get("memory", {}).get("percent", 0),
                "disk": vitals.get("disk", {}).get("percent", 0)
            }
            await sio.emit("vitals_update", bridge_state["vitals"])
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Vitals Heartbeat error: %s", e)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected Vitals error")
        await asyncio.sleep(2.0)


async def visuals_heartbeat():
    """High-frequency visual synchronization for UI animations."""
    while True:
        try:
            if bridge_state["speaking"]:
                cpu_usage = bridge_state["vitals"].get("cpu", 0)
                base = 20 + (cpu_usage * 0.5)
                freq_data = [base * (0.5 + 0.5 * (hash(i + time.time()) % 10 / 10))
                             for i in range(40)]
                await sio.emit("frequency_data", freq_data)
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Visuals Heartbeat error: %s", e)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected Visuals error")
        await asyncio.sleep(0.1)


async def telemetry_heartbeat():
    """Periodically push production metrics to the UI."""
    while True:
        try:
            metrics = telemetry.get_production_metrics()
            bridge_state["telemetry"] = metrics
            await sio.emit("telemetry_update", metrics)
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Telemetry Heartbeat error: %s", e)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected Telemetry Heartbeat error")
        await asyncio.sleep(10.0)


@sio.on("connect")
async def handle_connect(sid, *_args, **_kwargs):
    """Handle new UI connections."""
    logger.info("✅ UI Connected: %s", sid)
    await sio.emit('init_state', bridge_state, room=sid)


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
