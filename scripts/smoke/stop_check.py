"""
scripts/smoke/stop_check.py: the scripted 'stop' check of plan task C-51 (K-60 CT-VC-CONTROL, step 7).

    $PY scripts/smoke/stop_check.py [--prompt TEXT] [--followup TEXT] [--speak S] [--within S] [--timeout S]

One UI Socket.IO session (role ui), opened the way scripts/smoke/room_check.py opens one: the owner-session cookie from
C23_COOKIE (what 'python -m src.core.bridge_auth mint-session --ttl 600 --label c51-stop' prints inside the bridge
container), <UI_BRIDGE_URL origin>/vortex/socket.io and the canonical Origin. After init_state (an agent socket must be
online):

1. ui_command chat {id, text: --prompt}, a question that needs a long answer. JARVIS must start speaking (status_change
   speaking true) within --timeout. A typed reply is spoken only while someone listens (C-41): keep the dashboard's
   voice connected while this runs.
2. After --speak seconds of that speech, ui_command stop. The dashboard's agent state must be back at listening
   (status_change speaking false, thinking false) within --within seconds (1) of the stop. The answer's reply
   (new_message role assistant with that question's turn_id) must come within STOP_REPLY_S, because the agent sends it
   only once the speech has ended. A reply that comes later means the answer went on behind a cleared speaking flag.
   No speech may start again in the STOP_SETTLE_S after the stop (the queued speech is cut too).
3. ui_command chat {id, text: --followup}: its reply (source 'text', not the system's offline answer) within
   --timeout. No agent_status after the stop may show the session restarting or gone (K-60: stop never ends the job).

Exit 0 PASS, 1 FAIL, 2 usage. It adds two typed questions and their replies to the chat history and changes nothing
else. It prints the stop's timings and the replies' lengths, never a cookie, key, token, identity or message text.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.smoke import room_check  # noqa: E402  pylint: disable=wrong-import-position

DEFAULT_PROMPT = ("Jarvis, please tell me a long story, at least 300 words, about an old lighthouse keeper and his "
                  "cat. Tell the whole story.")
DEFAULT_FOLLOWUP = "Jarvis, what is 2 plus 2?"
CHAT_MAX_CHARS = 4000  # K-03: chat text is 1-4000 characters
SPEAK_S = 2.0  # the answer is spoken this long before the stop
WITHIN_S = 1.0  # acceptance: back at listening this soon after the stop
STOP_REPLY_S = 5.0  # the cut answer's reply comes this soon after the stop (the whole answer takes a minute or more)
STOP_SETTLE_S = 2.0  # no speech starts again this long after the stop
TIMEOUT_S = 45.0  # the speech's start and the follow-up's reply
CONNECT_S = 15.0
ACK_S = 15.0
POLL_S = 0.05
EXIT_OK, EXIT_FAILED, EXIT_USAGE = room_check.EXIT_OK, room_check.EXIT_FAILED, room_check.EXIT_USAGE


def _say(message: str) -> None:
    print(f"[stop_check] {message}", flush=True)


class CheckFailed(Exception):
    """A failed step: the check ends as FAIL with this message."""


class Watch:
    """What the bridge sends this UI socket, each event stamped with the check's clock when it arrives."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.agent: dict[str, Any] = {}
        self.states: list[tuple[float, bool, bool]] = []  # status_change (t, speaking, thinking)
        self.replies: dict[str, tuple[float, Mapping[str, Any]]] = {}  # turn_id -> (t, its first assistant message)
        self.gone: list[tuple[float, str]] = []  # agent_status that showed the session restarting or gone
        self.ready = asyncio.Event()
        self.changed = asyncio.Event()

    async def on_init_state(self, data: Any) -> None:
        if isinstance(data, dict) and isinstance(data.get("agent_status"), dict):
            self.agent.update(data["agent_status"])
        self.ready.set()
        self.changed.set()

    async def on_agent_status(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self.agent.clear()
        self.agent.update(data)  # K-02 v9: always the whole object
        # None is 'unknown' (the PC's local bridge never reads LiveKit), never a lost session
        if data.get("online") is False or data.get("voice_ready") is False or data.get("restarting") is True:
            self.gone.append((self.clock(), " ".join(f"{name}={data.get(name)}"
                                                     for name in ("online", "voice_ready", "restarting"))))
        self.changed.set()

    async def on_status_change(self, data: Any) -> None:
        if isinstance(data, dict):
            self.states.append((self.clock(), data.get("speaking") is True, data.get("thinking") is True))
            self.changed.set()

    async def on_message(self, data: Any) -> None:
        if not isinstance(data, dict) or data.get("role") != "assistant" or not str(data.get("text") or "").strip():
            return
        turn_id = data.get("turn_id")
        if isinstance(turn_id, str) and turn_id not in self.replies:
            self.replies[turn_id] = (self.clock(), data)
            self.changed.set()

    def speaking(self) -> bool:
        """The newest status_change says JARVIS speaks."""
        return bool(self.states) and self.states[-1][1]

    def first_state(self, since: float, *, speaking: bool) -> float | None:
        """When the first status_change after `since` showed JARVIS speaking (True) or listening (False: neither
        speaking nor thinking)."""
        for stamp, is_speaking, is_thinking in self.states:
            if stamp >= since and (is_speaking if speaking else not (is_speaking or is_thinking)):
                return stamp
        return None

    def no_session_loss(self, since: float) -> None:
        """K-60: stop never ends the job. CheckFailed when an agent_status after `since` showed the session restarting
        or gone."""
        what = next((what for stamp, what in self.gone if stamp >= since), None)
        if what is not None:
            raise CheckFailed(f"the session restarted or went away after the stop (agent_status {what})")

    async def wait_for(self, predicate: Callable[[], Any], deadline: float) -> Any:
        """predicate()'s first value that is not None, before the check's clock passes deadline; else None."""
        while True:
            value = predicate()
            if value is not None:
                return value
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), min(POLL_S, remaining))
            except asyncio.TimeoutError:
                pass


async def _send(client: Any, command: str, payload: dict[str, Any]) -> None:
    """One ui_command; CheckFailed unless the bridge relayed it (ack status 'sent')."""
    ack = await client.call("ui_command", {"type": command, "payload": payload}, timeout=ACK_S)
    status, reason = (ack.get("status"), ack.get("reason")) if isinstance(ack, dict) else (ack, None)
    if status != "sent":
        raise CheckFailed(f"the bridge did not relay {command} (ack {status}{', ' + str(reason) if reason else ''})")


async def _ask_long_question(client: Any, watch: Watch, prompt: str, *, timeout: float,
                             new_id: Callable[[], str]) -> str:
    """Step 1: the question with a long answer, until JARVIS speaks it. Returns the question's id."""
    try:
        await asyncio.wait_for(watch.ready.wait(), CONNECT_S)
    except asyncio.TimeoutError as exc:
        raise CheckFailed(f"no init_state within {CONNECT_S:.0f} s (is the cookie still valid?)") from exc
    if watch.agent.get("online") is False:
        raise CheckFailed("no agent socket is online (init_state agent_status online=false)")
    asked, asked_at = new_id(), watch.clock()
    await _send(client, "chat", {"id": asked, "text": prompt})
    spoke = await watch.wait_for(lambda: watch.first_state(asked_at, speaking=True), asked_at + timeout)
    if spoke is None:
        raise CheckFailed(f"JARVIS did not start speaking within {timeout:.0f} s (a typed reply is spoken only while "
                          "someone listens, C-41: is the dashboard's voice connected?)")
    _say(f"speaking {spoke - asked_at:.1f} s after the question")
    return asked


async def _stop_the_answer(client: Any, watch: Watch, asked: str, *, within: float,
                           sleep: Callable[[float], Awaitable[Any]]) -> tuple[float, float]:
    """Step 2: stop while JARVIS speaks that answer. Returns (when the stop was sent, when JARVIS listened again)."""
    if not watch.speaking() or asked in watch.replies:
        raise CheckFailed("the answer ended before the stop: give a longer --prompt or a shorter --speak")
    stop_at = watch.clock()
    await _send(client, "stop", {})
    quiet = await watch.wait_for(lambda: watch.first_state(stop_at, speaking=False),
                                 stop_at + max(within, STOP_REPLY_S))
    cut = await watch.wait_for(lambda: watch.replies.get(asked), stop_at + STOP_REPLY_S)
    if quiet is None:
        raise CheckFailed(f"still speaking {max(within, STOP_REPLY_S):.0f} s after the stop")
    _say(f"listening {quiet - stop_at:.2f} s after the stop (bound {within:.1f} s)")
    if quiet - stop_at > within:
        raise CheckFailed(f"back at listening only {quiet - stop_at:.2f} s after the stop (bound {within:.1f} s)")
    if cut is None:
        raise CheckFailed(f"the speaking flag was cleared, but the answer's reply did not come within "
                          f"{STOP_REPLY_S:.0f} s of the stop: the speech went on")
    _say(f"the cut answer's reply came {cut[0] - stop_at:.2f} s after the stop "
         f"({len(str(cut[1].get('text') or ''))} chars)")
    await sleep(STOP_SETTLE_S)
    resumed = watch.first_state(quiet, speaking=True)
    if resumed is not None:
        raise CheckFailed(f"JARVIS spoke again {resumed - stop_at:.1f} s after the stop (a queued speech was not cut)")
    watch.no_session_loss(stop_at)
    return stop_at, quiet


async def _ask_next_question(client: Any, watch: Watch, followup: str, *, stop_at: float, timeout: float,
                             new_id: Callable[[], str]) -> None:
    """Step 3: the next question gets JARVIS's own answer, from a session that never restarted."""
    second, second_at = new_id(), watch.clock()
    await _send(client, "chat", {"id": second, "text": followup})
    answered = await watch.wait_for(lambda: watch.replies.get(second), second_at + timeout)
    watch.no_session_loss(stop_at)
    if answered is None:
        raise CheckFailed(f"the next question got no reply within {timeout:.0f} s")
    if answered[1].get("source") == "system":
        raise CheckFailed("the next question got the system's answer (offline or blocked), not JARVIS's")
    _say(f"the next question was answered {answered[0] - second_at:.1f} s after it was sent "
         f"({len(str(answered[1].get('text') or ''))} chars)")


async def _run(client: Any, watch: Watch, *, prompt: str, followup: str, speak: float, within: float,
               timeout: float, sleep: Callable[[float], Awaitable[Any]], new_id: Callable[[], str]) -> int:
    """The three steps of the module docstring; the first one that fails ends the check as FAIL."""
    try:
        asked = await _ask_long_question(client, watch, prompt, timeout=timeout, new_id=new_id)
        await sleep(speak)
        stop_at, quiet = await _stop_the_answer(client, watch, asked, within=within, sleep=sleep)
        await _ask_next_question(client, watch, followup, stop_at=stop_at, timeout=timeout, new_id=new_id)
    except CheckFailed as exc:
        _say(f"FAIL: {exc}")
        return EXIT_FAILED
    _say(f"PASS: stop cut the answer in {quiet - stop_at:.2f} s and the next question was answered")
    return EXIT_OK


async def check_stop(*, base: str, cookie: str, prompt: str = DEFAULT_PROMPT, followup: str = DEFAULT_FOLLOWUP,
                     speak: float = SPEAK_S, within: float = WITHIN_S, timeout: float = TIMEOUT_S,
                     client_factory: Callable[[], Any] | None = None, clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                     new_id: Callable[[], str] = lambda: str(uuid.uuid4())) -> int:
    """The check (see the module docstring). client_factory makes the python-socketio AsyncClient; tests inject a fake,
    a clock and a sleep."""
    if client_factory is None:
        import socketio  # pylint: disable=import-outside-toplevel

        def client_factory() -> Any:
            return socketio.AsyncClient(reconnection=False)

    client = client_factory()
    watch = Watch(clock)
    for event, handler in (("init_state", watch.on_init_state), ("agent_status", watch.on_agent_status),
                           ("status_change", watch.on_status_change), ("new_message", watch.on_message),
                           ("update_message", watch.on_message)):
        client.on(event, handler)
    origin = room_check.canonical_origin(base)
    path = urlsplit(base).path.strip("/")
    await client.connect(origin, headers={"Cookie": cookie, "Origin": origin}, transports=["websocket"],
                         socketio_path=f"{path}/socket.io" if path else "socket.io", wait_timeout=CONNECT_S)
    try:
        return await _run(client, watch, prompt=prompt, followup=followup, speak=speak, within=within,
                          timeout=timeout, sleep=sleep, new_id=new_id)
    finally:
        await client.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stop_check.py",
                                     description="C-51 step 7: 'stop' during a long answer, then the next question.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="a question that needs a long spoken answer")
    parser.add_argument("--followup", default=DEFAULT_FOLLOWUP, help="the next question, asked after the stop")
    parser.add_argument("--speak", type=float, default=SPEAK_S, help="seconds of speech before the stop")
    parser.add_argument("--within", type=float, default=WITHIN_S, help="seconds the stop may take")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S, help="seconds for the speech and the reply")
    args = parser.parse_args(argv)
    room_check._load_env()  # pylint: disable=protected-access  # the same env files as room_check
    base = room_check.bridge_base()
    raw_cookie = os.getenv(room_check.COOKIE_ENV) or ""
    if not base or not raw_cookie.strip():
        print(f"[stop_check] needs UI_BRIDGE_URL and {room_check.COOKIE_ENV} (an owner-session cookie from "
              "mint-session)", file=sys.stderr)
        return EXIT_USAGE
    texts = (args.prompt.strip(), args.followup.strip())
    if not all(0 < len(text) <= CHAT_MAX_CHARS for text in texts):
        print(f"[stop_check] --prompt and --followup need 1-{CHAT_MAX_CHARS} characters", file=sys.stderr)
        return EXIT_USAGE
    try:
        return asyncio.run(check_stop(base=base, cookie=room_check.cookie_header(raw_cookie), prompt=texts[0],
                                      followup=texts[1], speak=args.speak, within=args.within,
                                      timeout=args.timeout))
    except Exception as exc:  # pylint: disable=broad-exception-caught  # never a traceback with headers in it
        print(f"[stop_check] failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
