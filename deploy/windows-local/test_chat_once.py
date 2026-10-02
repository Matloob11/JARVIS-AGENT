"""One-shot Socket.IO chat probe against the UI bridge (legacy token auth, K-38).

UI_BRIDGE_URL and VORTEX_SECURITY_TOKEN come from the root .env; deploy/windows-local/.env only fills
keys it lacks (K-47). A URL ending in /vortex (the domain's nginx prefix, C-16) is reached at its
origin with socketio_path 'vortex/socket.io', as the agent does. PASS when an assistant reply comes.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import socketio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def socket_target(url: str) -> tuple[str, str]:
    """(connect URL, socketio_path) as services/ai_core/agent_loops.py bridge_socket_target computes them:
    a URL ending in /vortex gives 'vortex/socket.io', any other URL 'socket.io'."""
    parts = urlsplit(url.strip())
    path = parts.path.strip("/")
    base = urlunsplit((parts.scheme, parts.netloc, "", parts.query, ""))
    if path == "vortex" or path.endswith("/vortex"):
        return base, f"{path}/socket.io"
    return base, "socket.io"


async def main() -> int:
    load_dotenv(ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    url = os.getenv("UI_BRIDGE_URL")
    token = os.getenv("VORTEX_SECURITY_TOKEN")
    if not url or not token:
        print("FAIL missing UI_BRIDGE_URL / VORTEX_SECURITY_TOKEN")
        return 1

    sio = socketio.AsyncClient()
    got: list[dict] = []

    @sio.on("new_message")
    async def on_msg(data):  # type: ignore[no-untyped-def]
        print("MSG", data.get("role"), str(data.get("text", ""))[:120])
        got.append(data)

    @sio.on("update_message")
    async def on_upd(data):  # type: ignore[no-untyped-def]
        print("UPD", data.get("role"), str(data.get("text", ""))[:120])
        got.append(data)

    base_url, socketio_path = socket_target(url)
    await sio.connect(base_url, auth={"token": token}, socketio_path=socketio_path, wait_timeout=10)
    print(f"connected ({base_url}, path /{socketio_path})")
    await sio.emit("ui_command", {"type": "chat", "payload": "hi"})
    await asyncio.sleep(25)
    await sio.disconnect()
    ok = any(g.get("role") == "assistant" for g in got)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
