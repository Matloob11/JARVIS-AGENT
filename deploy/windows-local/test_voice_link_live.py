"""Live VoiceLink check (C-35, K-56): two owner sessions join the JARVIS room as two 'tabs', and the agent's
jarvis.linked_identity attribute must follow the policy:

1. the first tab is linked (connect);
2. a second tab never steals an active link;
3. the linked tab leaving relinks the other one (replace);
4. a returning tab (a reload of tab 1) does not steal the active link either;
5. the other tab leaving relinks the returning one.

Each session is minted inside the bridge container over ssh exactly like test_chat_once.py --cookie-via-ssh
(JARVIS_VPS_SSH or JARVIS_VPS_HOST, key JARVIS_SSH_KEY), its LiveKit token comes from GET /vortex/livekit/token,
and both sessions are logged out at the end. The tabs publish nothing. Exit 0 when every step passed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from livekit import rtc

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_chat_once as probe  # noqa: E402  # pylint: disable=wrong-import-position

LINKED_ATTR = "jarvis.linked_identity"


async def livekit_token(http_base: str, origin: str, session: str) -> tuple[str, str, str]:
    """(LiveKit url, token, identity) for an owner session (K-37)."""
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(f"{http_base}/livekit/token",
                               headers={"Cookie": f"{probe.COOKIE_NAME}={session}", "Origin": origin})
    res.raise_for_status()
    data = res.json()
    return str(data["url"]), str(data["token"]), str(data["identity"])


def agent_linked(room: rtc.Room) -> str | None:
    """The agent participant's jarvis.linked_identity as this tab sees it."""
    for participant in room.remote_participants.values():
        if participant.identity.startswith("agent-"):
            return participant.attributes.get(LINKED_ATTR)
    return None


async def wait_linked(room: rtc.Room, expected: str, timeout: float = 20.0) -> bool:
    for _ in range(int(timeout / 0.5)):
        if agent_linked(room) == expected:
            return True
        await asyncio.sleep(0.5)
    return agent_linked(room) == expected


async def join(url: str, token: str) -> rtc.Room:
    room = rtc.Room()
    await room.connect(url, token)
    return room


async def run(bridge_url: str, settle_s: float) -> int:
    load_dotenv(probe.ROOT / ".env")
    load_dotenv(HERE / ".env", override=False)
    parts = urlsplit(bridge_url.strip())
    origin = f"{parts.scheme}://{parts.netloc}"
    http_base = bridge_url.strip().rstrip("/")
    sessions = [probe.mint_session_via_ssh(300), probe.mint_session_via_ssh(300)]
    if None in sessions:
        return 1
    results: list[tuple[str, bool]] = []
    rooms: list[rtc.Room] = []

    def step(name: str, ok: bool, seen: str | None) -> None:
        results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'} {name} (linked: {seen})")

    try:
        url1, token1, id1 = await livekit_token(http_base, origin, sessions[0])
        url2, token2, id2 = await livekit_token(http_base, origin, sessions[1])
        print(f"tab 1 = {id1}, tab 2 = {id2}")
        tab1 = await join(url1, token1)
        rooms.append(tab1)
        step("1. the first tab is linked", await wait_linked(tab1, id1), agent_linked(tab1))
        tab2 = await join(url2, token2)
        rooms.append(tab2)
        await asyncio.sleep(settle_s)
        step("2. a second tab never steals the link", agent_linked(tab2) == id1, agent_linked(tab2))
        await tab1.disconnect()
        step("3. the linked tab leaving relinks the other", await wait_linked(tab2, id2), agent_linked(tab2))
        url1b, token1b, _ = await livekit_token(http_base, origin, sessions[0])
        tab1b = await join(url1b, token1b)
        rooms.append(tab1b)
        await asyncio.sleep(settle_s)
        step("4. a returning tab does not steal the active link", agent_linked(tab1b) == id2, agent_linked(tab1b))
        await tab2.disconnect()
        step("5. the other tab leaving relinks the returning one", await wait_linked(tab1b, id1),
             agent_linked(tab1b))
    finally:
        for room in rooms:
            try:
                await room.disconnect()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        for session in sessions:
            await probe._logout(origin, http_base, session)  # pylint: disable=protected-access
    passed = all(ok for _, ok in results) and len(results) == 5
    print("PASS" if passed else "FAIL")
    return 0 if passed else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live VoiceLink check with two owner tabs (C-35)")
    parser.add_argument("--url", default=probe.DEFAULT_URL, help=f"bridge URL (default {probe.DEFAULT_URL})")
    parser.add_argument("--settle", type=float, default=6.0, help="seconds a 'no change' step waits (default 6)")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.url, args.settle))


if __name__ == "__main__":
    sys.exit(main())
