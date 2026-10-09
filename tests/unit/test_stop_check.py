"""Plan task C-51 step 7: scripts/smoke/stop_check.py, the scripted 'stop' check of the live run.

No network: the Socket.IO client is a fake that plays the bridge and the agent (what the UI socket receives after each
ui_command), and the clock and the sleep are fakes.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("c51_stop_check", ROOT / "scripts" / "smoke" / "stop_check.py")
stop_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop_check)

BASE = "https://jarvis.example.test/vortex"
COOKIE = "jarvis_session=SECRETJWT"
PROMPT = "c51 long question"
FOLLOWUP = "c51 next question"
LISTENING = {"speaking": False, "thinking": False}
SPEAKING = {"speaking": True, "thinking": False}


class Clock:
    """The check's clock: every read moves it on by `tick` (so every wait ends); the fakes move it further."""

    def __init__(self, tick: float = 0.001) -> None:
        self.now = 100.0
        self.tick = tick

    def __call__(self) -> float:
        self.now += self.tick
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


Reaction = list[tuple[float, str, Any]]  # (seconds later, event, data) the UI socket receives


def reply(turn_id: str, text: str = "c51 reply text", source: str = "text") -> dict[str, Any]:
    return {"id": f"reply-{turn_id}", "role": "assistant", "text": text, "source": source, "turn_id": turn_id}


def answered_stop(asked: str) -> Reaction:
    """A stop that cuts the answer: back at listening, then the cut answer's reply (as the agent sends them)."""
    return [(0.25, "status_change", LISTENING), (0.05, "new_message", reply(asked))]


class FakeBridge:
    """python-socketio's AsyncClient as check_stop uses it. call() records the ui_command, lets the bridge and the
    agent react (react(type, payload, n) -> Reaction, n counting that type from 1) and returns the ack."""

    def __init__(self, clock: Clock, react: Callable[[str, dict[str, Any], int], Reaction],
                 acks: dict[str, Any] | None = None, online: bool = True) -> None:
        self.clock, self.react = clock, react
        self.acks = acks or {}
        self.online = online
        self.handlers: dict[str, Any] = {}
        self.connected: dict[str, Any] = {}
        self.sent: list[dict[str, Any]] = []
        self.disconnected = False

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def connect(self, url: str, **kwargs: Any) -> None:
        self.connected = {"url": url, **kwargs}
        await self.handlers["init_state"]({"agent_status": {"online": self.online, "in_room": True,
                                                            "voice_ready": True, "paused": False,
                                                            "restarting": False}})

    async def call(self, event: str, data: dict[str, Any], timeout: float) -> Any:
        assert event == "ui_command" and timeout > 0
        self.sent.append(data)
        ack = self.acks.get(data["type"], {"status": "sent"})
        if ack.get("status") != "sent":
            return ack  # a refused command never reaches the agent
        n = sum(1 for sent in self.sent if sent["type"] == data["type"])
        for later, name, payload in self.react(data["type"], data["payload"], n):
            self.clock.now += later
            await self.handlers[name](payload)
        return ack

    async def disconnect(self) -> None:
        self.disconnected = True


def agent(*, first: Callable[[str], Reaction] | None = None, stop: Callable[[str], Reaction] | None = None,
          second: Callable[[str], Reaction] | None = None) -> Callable[[str, dict[str, Any], int], Reaction]:
    """The agent's side: by default it speaks the long answer, the stop cuts it and the next question is answered;
    a scenario replaces one step."""
    asked: list[str] = []

    def react(command: str, payload: dict[str, Any], n: int) -> Reaction:
        if command == "chat" and n == 1:
            asked.append(payload["id"])
            return (first or (lambda _id: [(0.8, "status_change", SPEAKING)]))(payload["id"])
        if command == "stop":
            return (stop or answered_stop)(asked[0])
        return (second or (lambda turn: [(1.2, "new_message", reply(turn, "Four."))]))(payload["id"])

    return react


async def run(bridge: FakeBridge, clock: Clock) -> int:
    ids = iter(("q-1", "q-2"))
    return await stop_check.check_stop(base=BASE, cookie=COOKIE, prompt=PROMPT, followup=FOLLOWUP,
                                       client_factory=lambda: bridge, clock=clock, sleep=clock.sleep,
                                       new_id=lambda: next(ids))


@pytest.fixture(autouse=True)
def _no_real_waits(monkeypatch):
    monkeypatch.setattr(stop_check, "POLL_S", 0.0)  # the fake clock decides every wait


async def test_a_stop_that_cuts_the_answer_passes_and_prints_no_cookie_or_text(capsys):
    clock = Clock()
    bridge = FakeBridge(clock, agent())
    assert await run(bridge, clock) == 0
    assert bridge.sent == [{"type": "chat", "payload": {"id": "q-1", "text": PROMPT}},
                           {"type": "stop", "payload": {}},
                           {"type": "chat", "payload": {"id": "q-2", "text": FOLLOWUP}}]
    assert bridge.connected["url"] == "https://jarvis.example.test"
    assert bridge.connected["socketio_path"] == "vortex/socket.io"
    assert bridge.connected["headers"] == {"Cookie": COOKIE, "Origin": "https://jarvis.example.test"}
    assert bridge.disconnected
    out = capsys.readouterr().out
    assert "listening 0.25 s after the stop (bound 1.0 s)" in out and "PASS" in out
    assert "SECRETJWT" not in out and PROMPT not in out and "c51 reply text" not in out and "Four." not in out


@pytest.mark.parametrize(("scenario", "expected"), [
    # the speaking flag clears in time, but the answer's reply never comes: the speech went on
    ({"stop": lambda _asked: [(0.25, "status_change", LISTENING)]}, "the speech went on"),
    ({"stop": lambda asked: [(1.5, "status_change", LISTENING), (0.05, "new_message", reply(asked))]},
     "back at listening only 1.5"),
    ({"stop": lambda asked: [*answered_stop(asked), (0.5, "status_change", SPEAKING)]}, "JARVIS spoke again"),
    ({"stop": lambda asked: [*answered_stop(asked), (0.1, "agent_status", {
        "online": True, "in_room": True, "voice_ready": False, "paused": False, "restarting": True})]},
     "the session restarted or went away after the stop"),
    ({"second": lambda turn: [(0.5, "new_message", reply(turn, "[offline] c51", source="system"))]},
     "the system's answer"),
    ({"second": lambda _turn: []}, "the next question got no reply"),
    ({"first": lambda _id: []}, "JARVIS did not start speaking"),
    # a short answer that ended before the stop proves nothing
    ({"first": lambda asked: [(0.8, "status_change", SPEAKING), (0.5, "status_change", LISTENING),
                              (0.1, "new_message", reply(asked))]}, "the answer ended before the stop"),
], ids=["went_on", "late", "resumed", "restarted", "offline_answer", "no_answer", "never_spoke", "too_short"])
async def test_each_way_the_stop_can_fail_exits_1_and_says_why(capsys, scenario, expected):
    clock = Clock(tick=0.01)
    assert await run(FakeBridge(clock, agent(**scenario)), clock) == 1
    out = capsys.readouterr().out
    assert expected in out and "FAIL" in out and "PASS" not in out


async def test_a_refused_command_or_an_offline_agent_fails_at_once(capsys):
    clock = Clock()
    refused = FakeBridge(clock, agent(), acks={"stop": {"status": "rejected", "reason": "rate_limited"}})
    assert await run(refused, clock) == 1
    assert [sent["type"] for sent in refused.sent] == ["chat", "stop"]  # no follow-up after a refused stop
    assert "the bridge did not relay stop (ack rejected, rate_limited)" in capsys.readouterr().out

    offline = FakeBridge(clock, agent(), online=False)
    assert await run(offline, clock) == 1 and not offline.sent and offline.disconnected
    assert "no agent socket is online" in capsys.readouterr().out


def test_main_needs_the_cookie_and_texts_a_chat_can_carry(monkeypatch, capsys):
    monkeypatch.setattr(stop_check.room_check, "_load_env", lambda: None)
    monkeypatch.setenv("UI_BRIDGE_URL", BASE)
    monkeypatch.delenv(stop_check.room_check.COOKIE_ENV, raising=False)
    assert stop_check.main([]) == 2
    assert "C23_COOKIE" in capsys.readouterr().err
    monkeypatch.setenv(stop_check.room_check.COOKIE_ENV, COOKIE)
    assert stop_check.main(["--prompt", "   "]) == 2
    assert stop_check.main(["--followup", "x" * 4001]) == 2
    err = capsys.readouterr().err
    assert "1-4000 characters" in err and "SECRETJWT" not in err
