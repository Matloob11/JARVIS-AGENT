"""
# ui_bridge.py
WebSocket server to bridge JARVIS core events to the React-based STONIX UI.
"""
import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import logging

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("UI-BRIDGE")

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins="*")
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app_asgi = socketio.ASGIApp(sio, other_asgi_app=app)

# State Management
bridge_state = {
    "speaking": False,
    "last_transcription": "",
    "messages": [],
}


@sio.event
async def connect(sid, environ):
    logger.info(f"UI Connected: {sid}")
    # Sync initial state
    await sio.emit('init_state', bridge_state, room=sid)


@sio.event
async def disconnect(sid):
    logger.info(f"UI Disconnected: {sid}")


@app.post("/notify")
async def notify_bridge(data: dict):
    """
    Endpoint for agent_runner.py to push status/transcription updates.
    """
    event_type = data.get("type")
    payload = data.get("payload")

    if event_type == "status":
        bridge_state["speaking"] = (payload == "START")
        await sio.emit("status_change", {"speaking": bridge_state["speaking"]})

    elif event_type == "transcription":
        # payload expected: {role: 'user'|'agent', text: '...', timestamp: '...'}
        bridge_state["messages"].append(payload)
        # Keep only last 50 messages
        if len(bridge_state["messages"]) > 50:
            bridge_state["messages"].pop(0)
        await sio.emit("new_message", payload)

    elif event_type == "vitals":
        # payload expected: {cpu: 20, temp: 45, etc}
        await sio.emit("vitals_update", payload)

    return {"status": "ok"}


async def heartbeat():
    """Simulate frequency data for the Vortex animation when speaking."""
    while True:
        if bridge_state["speaking"]:
            # Generate random spectral data if no real data is available
            freq_data = [i * (0.5 + 0.5 * (hash(i) % 10 / 10))
                         for i in range(40)]
            await sio.emit("frequency_data", freq_data)
        await asyncio.sleep(0.1)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(heartbeat())

if __name__ == "__main__":
    uvicorn.run(app_asgi, host="127.0.0.1", port=5001)
