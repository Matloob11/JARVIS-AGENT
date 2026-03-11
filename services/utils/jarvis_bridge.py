"""
# services/utils/jarvis_bridge.py
Shared UI notification and telemetry functions for JARVIS.
"""

import asyncio
import time
import uuid
from typing import Optional
import httpx
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_health import health_monitor

logger = setup_logger("JARVIS-BRIDGE")

# Global resources for controlled telemetry
HTTP_CLIENT: Optional[httpx.AsyncClient] = None
TELEMETRY_SEMAPHORE = asyncio.Semaphore(5)


def get_http_client() -> httpx.AsyncClient:
    """Returns a shared httpx client."""
    global HTTP_CLIENT  # pylint: disable=global-statement
    if HTTP_CLIENT is None or HTTP_CLIENT.is_closed:
        HTTP_CLIENT = httpx.AsyncClient(timeout=10.0)
    return HTTP_CLIENT


async def notify_ui(status: str):
    """Sends a notification to the STONIX UI Bridge with heartbeat."""
    health_monitor.record_heartbeat("agent_runner")
    async with TELEMETRY_SEMAPHORE:
        try:
            client = get_http_client()
            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": "INTERNAL"
            }
            url = f"{config.bridge_url}/notify"
            await client.post(url, json={
                "type": "status",
                "payload": status
            }, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Bridge Notification failed: %s", e)


async def notify_thinking(status: str):
    """Sends a thinking status notification to the UI."""
    async with TELEMETRY_SEMAPHORE:
        try:
            client = get_http_client()
            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": "INTERNAL"
            }
            url = f"{config.bridge_url}/notify"
            await client.post(url, json={
                "type": "thinking",
                "payload": status
            }, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Thinking Notification failed: %s", e)


async def notify_transcription(role: str, text: str,
                               msg_id: Optional[str] = None,
                               is_final: bool = True):
    """Sends a transcription update to the STONIX UI Bridge."""
    async with TELEMETRY_SEMAPHORE:
        try:
            client = get_http_client()
            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": "INTERNAL"
            }
            url = f"{config.bridge_url}/notify"

            effective_id = msg_id or str(uuid.uuid4())
            payload = {
                "id": effective_id,
                "role": role,
                "text": text,
                "is_final": is_final,
                "timestamp": time.time()
            }

            await client.post(url, json={
                "type": "transcription",
                "payload": payload
            }, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Transcription Notification failed: %s", e)


async def notify_memory(content: str):
    """Sends a new extracted memory to the STONIX UI Bridge."""
    async with TELEMETRY_SEMAPHORE:
        try:
            client = get_http_client()
            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": "INTERNAL"
            }
            url = f"{config.bridge_url}/notify"
            await client.post(url, json={
                "type": "memory_update",
                "payload": {
                    "id": str(uuid.uuid4()),
                    "content": content,
                    "timestamp": time.time()
                }
            }, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Memory Notification failed: %s", e)


async def notify_tool_action(tool_name: str, action_details: str):
    """Sends a tool execution sync event to the UI."""
    async with TELEMETRY_SEMAPHORE:
        try:
            client = get_http_client()
            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": "INTERNAL"
            }
            url = f"{config.bridge_url}/notify"
            await client.post(url, json={
                "type": "tool_sync",
                "payload": {
                    "tool": tool_name,
                    "details": action_details,
                    "timestamp": time.time()
                }
            }, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Tool Notification failed: %s", e)
