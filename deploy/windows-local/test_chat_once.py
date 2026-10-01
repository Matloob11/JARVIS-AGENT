"""One-shot Socket.IO chat probe against UI bridge."""
from __future__ import annotations

import asyncio
import os
import sys

import socketio
from dotenv import load_dotenv

load_dotenv()


async def main() -> int:
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

    await sio.connect(url, auth={"token": token})
    print("connected")
    await sio.emit("ui_command", {"type": "chat", "payload": "hi"})
    await asyncio.sleep(25)
    await sio.disconnect()
    ok = any(g.get("role") == "assistant" for g in got)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
