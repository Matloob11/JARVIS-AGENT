"""
# services/utils/jarvis_bridge.py
Shared UI notification and telemetry functions for JARVIS.
"""

import asyncio
import time
import uuid
import json
from typing import Optional, Any
import httpx
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_security import security_manager

logger = setup_logger("JARVIS-BRIDGE")

_HTTP_LOCK: Optional[asyncio.Lock] = None
HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_TELEMETRY_SEMAPHORE: Optional[asyncio.Semaphore] = None
_LAST_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _check_loop():
    """Checks if the event loop has changed and resets globals if so."""
    global _LAST_LOOP, _HTTP_LOCK, HTTP_CLIENT, _TELEMETRY_SEMAPHORE # pylint: disable=global-statement
    try:
        current_loop = asyncio.get_running_loop()
        if _LAST_LOOP is not current_loop:
            logger.debug("🔄 Event loop changed, resetting bridge globals.")
            _HTTP_LOCK = None
            if HTTP_CLIENT and not HTTP_CLIENT.is_closed:
                # We can't easily close the old client from a different loop synchronously
                # but we can discard it.
                HTTP_CLIENT = None
            _TELEMETRY_SEMAPHORE = None
            _LAST_LOOP = current_loop
    except RuntimeError:
        # No running loop
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


async def get_http_client() -> httpx.AsyncClient:
    """Returns a shared httpx client with thread-safe initialization."""
    global HTTP_CLIENT  # pylint: disable=global-statement
    async with _get_lock():
        if HTTP_CLIENT is None or HTTP_CLIENT.is_closed:
            HTTP_CLIENT = httpx.AsyncClient(timeout=10.0)
        return HTTP_CLIENT


async def notify_event(event_type: str, payload: Any):
    """Sends a generic event notification to the STONIX UI Bridge."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            # Double-check if client is somehow still a coroutine (asyncio quirk)
            if asyncio.iscoroutine(client):
                client = await client

            url = f"{config.bridge_url}/notify"
            payload_data = {
                "type": event_type,
                "payload": payload
            }
            # Calculate real signature for the payload
            # Use same sort_keys=True as the verifier in ui_bridge.py
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }
            await client.post(url, json=payload_data, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Bridge Notification failed for %s: %s", event_type, e)


async def notify_ui(status: str):
    """Sends a notification to the STONIX UI Bridge with heartbeat."""
    health_monitor.record_heartbeat("agent_runner")
    await notify_event("status", status)


async def notify_vitals(vitals: dict):
    """Pushes real-time system vitals to the UI."""
    payload = {
        "cpu": vitals.get("cpu", {}).get("usage", 0),
        "ram": vitals.get("memory", {}).get("percent", 0),
        "disk": vitals.get("disk", {}).get("percent", 0)
    }
    await notify_event("vitals", payload)


async def notify_location(location_data: dict):
    """Pushes real-time location to the UI."""
    await notify_event("location_sync", location_data)


# UI Bridge logging is handled via UIBridgeHandler in jarvis_logger.py


async def notify_log(message: str, category: str = "SYSTEM"):
    """Sends a system activity log to the UI."""
    await notify_event("vortex_log", {
        "text": message,
        "category": category,
        "timestamp": time.time()
    })


async def notify_voice_match(confidence: float):
    """Pushes voice biometric match confidence level."""
    await notify_event("voice_id_sync", {
        "confidence": confidence,
        "timestamp": time.time()
    })


async def notify_thinking(status: str):
    """Sends a thinking status notification to the UI."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            if asyncio.iscoroutine(client):
                client = await client
            url = f"{config.bridge_url}/notify"
            payload_data = {
                "type": "thinking",
                "payload": status
            }
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }
            await client.post(url, json=payload_data, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Thinking Notification failed: %s", e)


async def notify_transcription(role: str, text: str,
                               msg_id: Optional[str] = None,
                               is_final: bool = True):
    """Sends a transcription update to the STONIX UI Bridge."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            if asyncio.iscoroutine(client):
                client = await client
            url = f"{config.bridge_url}/notify"
            effective_id = msg_id or str(uuid.uuid4())
            payload_data = {
                "type": "transcription",
                "payload": {
                    "id": effective_id,
                    "role": role,
                    "text": text,
                    "is_final": is_final,
                    "timestamp": time.time()
                }
            }
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }
            await client.post(url, json=payload_data, headers=headers, timeout=5.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Debug instead of error to avoid console spam when UI is offline
            logger.debug("Transcription Notification failed: %s", e)


async def notify_memory(content: str):
    """Sends a new extracted memory to the STONIX UI Bridge."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            if asyncio.iscoroutine(client):
                client = await client
            url = f"{config.bridge_url}/notify"
            payload_data = {
                "type": "memory_update",
                "payload": {
                    "id": str(uuid.uuid4()),
                    "content": content,
                    "timestamp": time.time()
                }
            }
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }
            await client.post(url, json=payload_data, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Memory Notification failed: %s", e)


async def notify_tool_action(tool_name: str, action_details: str):
    """Sends a tool execution sync event to the UI."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            if asyncio.iscoroutine(client):
                client = await client
            url = f"{config.bridge_url}/notify"
            payload_data = {
                "type": "tool_sync",
                "payload": {
                    "tool": tool_name,
                    "details": action_details,
                    "timestamp": time.time()
                }
            }
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }
            await client.post(url, json=payload_data, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Tool Notification failed: %s", e)


async def notify_sim_data(records: list, phone_number: str = ""):
    """Pushes SIM lookup results to the UI SimInfoBox in real-time."""
    await notify_event("sim_data_result", {
        "records": records,
        "phone": phone_number,
        "timestamp": time.time()
    })


async def notify_sim_loading():
    """Notifies the UI that a SIM data lookup is in progress."""
    await notify_event("sim_data_loading", {})


async def notify_voice_match(confidence: float):
    """Sends the real-time voice ID match score (0.0–1.0) to the UI."""
    await notify_event("voice_id_sync", {
        "confidence": round(float(confidence), 4),
        "timestamp": time.time()
    })


async def notify_user_speaking(is_speaking: bool):
    """Notifies the UI that the user has started/stopped speaking."""
    await notify_event("user_speaking_sync", is_speaking)

