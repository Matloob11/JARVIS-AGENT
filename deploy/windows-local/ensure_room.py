"""
Create/dispatch a LiveKit room so the local Windows agent worker actually gets a job.
Without this, the worker stays idle and UI chat never reaches the agent.

C-137: the console (agent_ctl start/restart shows it) gets only the room's name, sid, empty and
departure timeouts and whether an agent dispatch exists; never the Room protobuf, which carries
turn_password, nor the API key or secret.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit import api

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
# The root .env is the single settings source (K-47): this file only fills keys it lacks.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

ROOM_NAME = os.getenv("JARVIS_ROOM_NAME", "jarvis-hybrid")


def _http_url(ws_url: str) -> str:
    u = (ws_url or "").strip()
    if u.startswith("ws://"):
        return "http://" + u[len("ws://") :]
    if u.startswith("wss://"):
        return "https://" + u[len("wss://") :]
    return u


def _masked(text: object, *secrets: str) -> str:
    """text with each of the given credential values replaced by ***."""
    out = str(text)
    for value in secrets:
        if value:
            out = out.replace(value, "***")
    return out


def describe_room(room: Any, dispatches: int | None) -> str:
    """The console summary of a LiveKit Room: chosen fields only, never turn_password or metadata."""
    dispatch = "unknown" if dispatches is None else f"yes ({dispatches})" if dispatches else "no"
    return (f"name={room.name} sid={room.sid} empty_timeout={room.empty_timeout}s "
            f"departure_timeout={room.departure_timeout}s agent_dispatch={dispatch}")


async def _dispatch_count(lk: api.LiveKitAPI, *secrets: str) -> int | None:
    """The agent dispatches of the room, or None when the server cannot list them."""
    try:
        return len(await lk.agent_dispatch.list_dispatch(room_name=ROOM_NAME))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[ROOM] list_dispatch note: {_masked(exc, *secrets)}")
        return None


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
            print(f"[ROOM] delete_room note: {_masked(exc, api_key, api_secret)}")

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
            print(f"[ROOM] create_room note: {_masked(exc, api_key, api_secret)}")

        rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[ROOM_NAME]))
        room = next((r for r in rooms.rooms if r.name == ROOM_NAME), None)
        if room is None:
            print(f"[ROOM] Listed: room '{ROOM_NAME}' not found")
            print("[WARN] The room is missing: the agent worker gets no job until it exists.")
            return
        dispatches = await _dispatch_count(lk, api_key, api_secret)
        print(f"[ROOM] Listed: {describe_room(room, dispatches)}")
        print("[OK] Room ready — agent worker should receive the job now.")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(ensure_room())
