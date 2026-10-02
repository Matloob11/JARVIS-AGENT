"""Live probe: a persona_change notify reaches a dashboard socket as persona_update (plan task C-14; marker 'live').

Skipped unless JARVIS_LIVE_BRIDGE_URL names the bridge (see test_bridge_connectivity.py) and VORTEX_SECURITY_TOKEN
is set. It connects one legacy dashboard socket, switches the persona to the other one and back to the persona
the bridge had before, so the dashboard ends as it started. No credential is printed.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import socketio

from services.utils.jarvis_config import config
from services.utils.jarvis_security import security_manager

BRIDGE_URL = (os.getenv("JARVIS_LIVE_BRIDGE_URL") or "").strip().rstrip("/")
pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not BRIDGE_URL, reason="set JARVIS_LIVE_BRIDGE_URL to probe a running bridge")]
TIMEOUT = 15.0


def _socket_target(url: str) -> tuple[str, str]:
    """(origin, socketio_path): the nginx prefix /vortex gives 'vortex/socket.io'."""
    parts = urlsplit(url)
    prefix = parts.path.strip("/")
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")), f"{prefix}/socket.io" if prefix else "socket.io"


async def _notify(event_type: str, payload: Any) -> httpx.Response:
    body = {"type": event_type, "payload": payload}
    headers = {"X-Vortex-Token": config.security_token,
               "X-Vortex-Signature": security_manager.generate_signature(json.dumps(body, sort_keys=True))}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.post(f"{BRIDGE_URL}/notify", json=body, headers=headers)


async def test_persona_change_reaches_the_dashboard_and_is_restored():
    if not config.security_token:
        pytest.skip("VORTEX_SECURITY_TOKEN is not set here")
    sio = socketio.AsyncClient(reconnection=False)
    init_state: asyncio.Queue[Any] = asyncio.Queue()
    updates: asyncio.Queue[Any] = asyncio.Queue()
    sio.on("init_state", init_state.put)
    sio.on("persona_update", updates.put)
    origin, path = _socket_target(BRIDGE_URL)
    await sio.connect(origin, socketio_path=path, auth={"token": config.security_token}, wait_timeout=10)
    try:
        original = (await asyncio.wait_for(init_state.get(), TIMEOUT)).get("persona") or "jarvis"
        other = "anna" if original == "jarvis" else "jarvis"
        for persona in (other, original):
            assert (await _notify("persona_change", persona)).status_code == 200
            assert await asyncio.wait_for(updates.get(), TIMEOUT) == {"persona": persona}
    finally:
        await sio.disconnect()
