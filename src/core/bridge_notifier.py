import json
import asyncio
from typing import Any, Optional
import httpx

from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import security_manager

logger = setup_logger("BRIDGE-NOTIFIER")

class BridgeNotifier:
    """
    Handles all outgoing notifications to the STONIX UI Bridge.
    Ensures all payloads are signed with Vortex security headers.
    """

    def __init__(self):
        """Initialize BridgeNotifier with config settings."""
        self.bridge_url = config.bridge_url
        self.security_token = config.security_token
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy initialization of shared httpx client."""
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(timeout=1.5)
            return self._client

    async def notify_event(self, event_type: str, payload: Any):
        """Generic helper to notify STONIX UI with signed payloads."""
        try:
            payload_data = {"type": event_type, "payload": payload}
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": self.security_token,
                "X-Vortex-Signature": signature
            }
            url = f"{self.bridge_url}/notify"
            client = await self._get_client()
            await client.post(url, json=payload_data, headers=headers)
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as e:
            logger.debug("UI Notification failed (%s): %s", event_type, e)

    async def aclose(self):
        """Gracefully closes the shared HTTP client."""
        async with self._lock:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
                logger.debug("BridgeNotifier: Persistent client closed.")

    async def notify_reasoning(self, reasoning_result: dict):
        """Sends intelligence/reasoning metadata to the UI Bridge."""
        await self.notify_event("reasoning", {
            "intent": reasoning_result.get("intent_analysis", {}).get("primary_intent"),
            "confidence": reasoning_result.get(
                "intent_analysis", {}).get("confidence_scores", {}),
            "plan": reasoning_result.get("plan", []),
            "is_ambiguous": reasoning_result.get(
                "intent_analysis", {}).get("is_ambiguous", False)
        })

    async def notify_thinking(self, state: str):
        """Notifies UI of thinking state ('START' or 'END')."""
        await self.notify_event("thinking", state)

    async def notify_persona(self, persona: str):
        """Notifies UI about persona change."""
        await self.notify_event("persona_change", persona)

    async def notify_user_speaking(self, active: bool):
        """Syncs user speaking state to UI."""
        await self.notify_event("user_speaking_sync", active)

    async def notify_wake_word(self, active: bool):
        """Syncs wake word mode state to UI."""
        await self.notify_event("wake_word_sync", active)

# Global instances
bridge_notifier = BridgeNotifier()
