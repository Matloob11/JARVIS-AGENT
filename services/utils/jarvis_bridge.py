import asyncio
import json
import time
import uuid
from typing import Any

import httpx
from typing_extensions import TypedDict

from services.utils.jarvis_config import config
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import security_manager

logger = setup_logger("JARVIS-BRIDGE")

_HTTP_LOCK: asyncio.Lock | None = None
HTTP_CLIENT: httpx.AsyncClient | None = None
_TELEMETRY_SEMAPHORE: asyncio.Semaphore | None = None
_LAST_LOOP: asyncio.AbstractEventLoop | None = None


class BridgePayload(TypedDict):
    """Strict payload structure for UI notifications."""
    type: str
    payload: Any


def _check_loop() -> None:
    """Checks if the event loop has changed and resets globals if so."""
    global _LAST_LOOP, _HTTP_LOCK, HTTP_CLIENT, _TELEMETRY_SEMAPHORE # pylint: disable=global-statement
    try:
        current_loop = asyncio.get_running_loop()
        if _LAST_LOOP is not current_loop:
            logger.debug("🔄 Event loop changed, resetting bridge globals.")
            _HTTP_LOCK = None
            if HTTP_CLIENT and not HTTP_CLIENT.is_closed:
                HTTP_CLIENT = None
            _TELEMETRY_SEMAPHORE = None
            _LAST_LOOP = current_loop
    except RuntimeError:
        pass


def _get_lock() -> asyncio.Lock:
    """Lazily creates lock inside running event loop."""
    global _HTTP_LOCK # pylint: disable=global-statement
    _check_loop()
    if _HTTP_LOCK is None:
        _HTTP_LOCK = asyncio.Lock()
    return _HTTP_LOCK


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily creates semaphore inside running event loop."""
    global _TELEMETRY_SEMAPHORE  # pylint: disable=global-statement
    _check_loop()
    if _TELEMETRY_SEMAPHORE is None:
        _TELEMETRY_SEMAPHORE = asyncio.Semaphore(20)
    return _TELEMETRY_SEMAPHORE


def _get_headers(signature: str) -> dict[str, str]:
    """Helper to generate signed headers with guaranteed string types."""
    token = str(config.security_token) if config.security_token else "local"
    return {
        "X-Vortex-Token": token,
        "X-Vortex-Signature": signature,
    }


async def get_http_client() -> httpx.AsyncClient:
    """Returns a shared httpx client with thread-safe initialization."""
    global HTTP_CLIENT  # pylint: disable=global-statement

    # Check loop to ensure we're not using a client from a closed loop
    try:
        current_loop = asyncio.get_running_loop()
        global _LAST_LOOP # pylint: disable=global-statement
        if _LAST_LOOP is not current_loop:
            HTTP_CLIENT = None
            _LAST_LOOP = current_loop
    except RuntimeError:
        pass

    if HTTP_CLIENT is None or HTTP_CLIENT.is_closed:
        # Create a new client if none exists or it is closed
        HTTP_CLIENT = httpx.AsyncClient(timeout=10.0)

    return HTTP_CLIENT


async def notify_event(event_type: str, payload: Any) -> None:
    """Sends a generic event notification to the STONIX UI Bridge."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            url = f"{config.bridge_url}/notify"
            payload_data: BridgePayload = {
                "type": event_type,
                "payload": payload,
            }

            # FAANG standard: Guaranteed key ordering for signature stability
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = _get_headers(signature)
            await client.post(url, json=payload_data, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Bridge Notification failed for %s: %s", event_type, e)


async def notify_ui(status: str, payload: dict[str, Any] | None = None) -> None:
    """Sends a notification to the STONIX UI Bridge with heartbeat and optional payload."""
    health_monitor.record_heartbeat("agent_runner")
    if payload:
        await notify_event(status, payload)
    else:
        await notify_event("status", status)


async def notify_vitals(vitals: dict[str, dict[str, int | float]]) -> None:
    """Pushes real-time system vitals to the UI."""
    payload = {
        "cpu": vitals.get("cpu", {}).get("usage", 0),
        "ram": vitals.get("memory", {}).get("percent", 0),
        "disk": vitals.get("disk", {}).get("percent", 0),
    }
    await notify_event("vitals", payload)


async def notify_location(location_data: dict[str, Any]) -> None:
    """Pushes real-time location to the UI."""
    await notify_event("location_sync", location_data)


async def notify_log(message: str, category: str = "SYSTEM") -> None:
    """Sends a system activity log to the UI."""
    await notify_event("vortex_log", {
        "text": message,
        "category": category,
        "timestamp": time.time(),
    })


async def notify_voice_match(confidence: float) -> None:
    """Pushes voice biometric match confidence level."""
    await notify_event("voice_id_sync", {
        "confidence": confidence,
        "timestamp": time.time(),
    })


async def notify_thinking(status: str) -> None:
    """Sends a thinking status notification to the UI."""
    await notify_event("thinking", status)


async def notify_transcription(role: str, text: str,
                               msg_id: str | None = None,
                               is_final: bool = True) -> None:
    """Sends a transcription update to the STONIX UI Bridge."""
    await notify_event("transcription", {
        "id": msg_id or str(uuid.uuid4()),
        "role": role,
        "text": text,
        "is_final": is_final,
        "timestamp": time.time(),
    })


async def notify_memory(content: str) -> None:
    """Sends a new extracted memory to the STONIX UI Bridge."""
    await notify_event("memory_update", {
        "id": str(uuid.uuid4()),
        "content": content,
        "timestamp": time.time(),
    })


async def notify_tool_action(tool_name: str, action_details: str) -> None:
    """Sends a tool execution sync event to the UI."""
    await notify_event("tool_sync", {
        "tool": tool_name,
        "details": action_details,
        "timestamp": time.time(),
    })


async def notify_sim_data(
    records: list[Any],
    phone_number: str = "",
    status: str = "success",
    message: str = "",
) -> None:
    """Pushes SIM lookup results to the UI SimInfoBox in real-time."""
    await notify_event("sim_data_result", {
        "records": records,
        "phone": phone_number,
        "status": status,
        "message": message,
        "timestamp": time.time(),
    })


async def notify_sim_loading(payload: dict[str, Any] | None = None) -> None:
    """Notifies the UI that a SIM data lookup is in progress."""
    await notify_event("sim_data_loading", payload or {})


async def notify_user_speaking(is_speaking: bool) -> None:
    """Notifies the UI that the user has started/stopped speaking."""
    await notify_event("user_speaking_sync", is_speaking)
