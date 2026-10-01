"""
Create/dispatch a LiveKit room so the local Windows agent worker actually gets a job.
Without this, the worker stays idle and UI chat never reaches the agent.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

ROOM_NAME = os.getenv("JARVIS_ROOM_NAME", "jarvis-hybrid")


def _http_url(ws_url: str) -> str:
    u = (ws_url or "").strip()
    if u.startswith("ws://"):
        return "http://" + u[len("ws://") :]
    if u.startswith("wss://"):
        return "https://" + u[len("wss://") :]
    return u


async def ensure_room() -> None:
    livekit_url = os.getenv("LIVEKIT_URL", "")
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not livekit_url or not api_key or not api_secret:
        print("[ERROR] LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
        sys.exit(1)

    http_url = _http_url(livekit_url)
    print(f"[ROOM] LiveKit API: {http_url}")
    print(f"[ROOM] Ensuring room: {ROOM_NAME}")

    lk = api.LiveKitAPI(http_url, api_key, api_secret)
    try:
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=ROOM_NAME))
            print(f"[ROOM] Deleted existing room '{ROOM_NAME}'")
            await asyncio.sleep(1.5)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[ROOM] delete_room note: {exc}")

        # Single dispatch path only: CreateRoom with agents= (do NOT also create_dispatch)
        try:
            # departure_timeout default is ~20s — if the browser briefly drops,
            # LiveKit closes the room and kills the agent job (chat+voice die).
            await lk.room.create_room(
                api.CreateRoomRequest(
                    name=ROOM_NAME,
                    empty_timeout=3600,
                    departure_timeout=3600,
                    max_participants=10,
                    agents=[api.RoomAgentDispatch(agent_name="")],
                )
            )
            print(f"[ROOM] Created room '{ROOM_NAME}' with single agent dispatch")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[ROOM] create_room note: {exc}")

        rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[ROOM_NAME]))
        print(f"[ROOM] Listed: {rooms}")
        print("[OK] Room ready — agent worker should receive the job now.")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(ensure_room())
