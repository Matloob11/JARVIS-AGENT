"""Plan task C-23: scripts/smoke/room_check.py, the read-only helper of the deploy runbook.

No network: the Socket.IO client and the /status poll are fakes; the clock is fake.
"""
# pylint: disable=protected-access
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("c23_room_check", ROOT / "scripts" / "smoke" / "room_check.py")
room_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(room_check)

STATUS = {
    "status": "ok", "brain": "pc",
    "agent": {"online": True, "in_room": True, "voice_ready": True, "paused": False, "restarting": False,
              "session": "running", "agent_name": "", "host": "owner-laptop", "version": "abc"},
    "livekit": {"ok": True, "mode": "always", "paused": False, "room_sid": "RM_1", "participants": 1,
                "decision": "present", "last_job_state": "running", "dispatch_id": "AD_1",
                "agents": [{"lk.agent.name": "", "jarvis.session": "running", "state": "active", "job_id": "AJ_1",
                            "bridge_link": True}]},
}


def test_the_summary_shows_flags_names_and_states_only():
    out = "\n".join(room_check.summarize(STATUS))
    assert "agent: online=yes in_room=yes voice_ready=yes paused=no restarting=no session=running target=''" in out
    assert "livekit: ok=yes mode=always paused=no room_sid=RM_1 people=1 decision=present" in out
    assert "  agent '' session running state active job AJ_1 bridge_link yes" in out
    assert "owner-laptop" not in out
    assert "host" not in room_check.public_blocks(STATUS)["agent"]


def test_the_cookie_and_the_origin():
    assert room_check.cookie_header("abc.def") == "jarvis_session=abc.def"
    assert room_check.cookie_header(" jarvis_session=abc.def \n") == "jarvis_session=abc.def"
    assert room_check.canonical_origin("https://jarvis.example.test/vortex") == "https://jarvis.example.test"


@pytest.mark.parametrize("wait, status, met", [
    ("paused", {"paused": True}, True), ("paused", {"paused": False}, False),
    ("absent", {"in_room": False}, True), ("absent", {"in_room": True}, False),
    ("voice_ready", {"voice_ready": True}, True), ("present", {"in_room": True}, True),
])
def test_the_wait_conditions(wait, status, met):
    assert room_check.condition_met(wait, status) is met


class FakeClient:
    """python-socketio's AsyncClient as follow() uses it; call() plays the bridge's answer to the command."""

    def __init__(self, after_command: list[dict[str, Any]], ack: dict[str, Any] | None = None) -> None:
        self.handlers: dict[str, Any] = {}
        self.after_command = after_command
        self.ack = ack or {"status": "sent"}
        self.connected: dict[str, Any] = {}
        self.sent: list[tuple[str, Any]] = []
        self.disconnected = False

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def connect(self, url: str, **kwargs: Any) -> None:
        self.connected = {"url": url, **kwargs}
        await self.handlers["init_state"]({"agent_status": {"online": True, "in_room": True, "voice_ready": True,
                                                            "paused": False, "restarting": False}})

    async def call(self, event: str, data: Any, timeout: float) -> Any:
        self.sent.append((event, data))
        for status in self.after_command:
            await self.handlers["agent_status"](status)
        return self.ack

    async def disconnect(self) -> None:
        self.disconnected = True


async def test_follow_waits_until_the_condition_and_never_prints_the_cookie(capsys):
    paused = {"online": True, "in_room": False, "voice_ready": False, "paused": True, "restarting": False}
    client = FakeClient([paused])
    code = await room_check.follow("pause_agent", wait="paused", watch=None, timeout=30,
                                   base="https://jarvis.example.test/vortex", cookie="jarvis_session=SECRETJWT",
                                   client_factory=lambda: client, poll=lambda _base: None)
    assert code == 0 and client.disconnected
    assert client.sent == [("ui_command", {"type": "pause_agent", "payload": {}})]
    assert client.connected["url"] == "https://jarvis.example.test"
    assert client.connected["socketio_path"] == "vortex/socket.io"
    assert client.connected["headers"] == {"Cookie": "jarvis_session=SECRETJWT", "Origin": "https://jarvis.example.test"}
    out = capsys.readouterr().out
    assert "SECRETJWT" not in out and "paused after" in out


async def test_a_refused_command_exits_1_at_once(capsys):
    """K-03 v9: in observe mode (or on a bridge without the room owner) pause_agent is refused; the probe says so
    and does not wait for a pause that cannot come."""
    polls: list[str] = []
    client = FakeClient([], ack={"status": "rejected", "reason": "pause_unavailable"})
    code = await room_check.follow("pause_agent", wait="paused", watch=None, timeout=30,
                                   base="https://jarvis.example.test/vortex", cookie="jarvis_session=SECRETJWT",
                                   client_factory=lambda: client, poll=polls.append)
    assert code == 1 and client.disconnected and not polls
    out = capsys.readouterr().out
    assert "the bridge refused pause_agent (pause_unavailable); nothing changed" in out and "SECRETJWT" not in out


async def test_watch_reports_whether_voice_ready_held():
    class Clock:
        now = 0.0

        def __call__(self) -> float:
            Clock.now += 1.0
            return Clock.now

    held = FakeClient([])
    assert await room_check.follow("stop", wait=None, watch=5, timeout=30, base="https://j.example.test/vortex",
                                   cookie="c", client_factory=lambda: held, poll=lambda _base: None,
                                   clock=Clock()) == 0
    dropped = FakeClient([{"online": True, "in_room": True, "voice_ready": False, "paused": False,
                           "restarting": True}])
    assert await room_check.follow("stop", wait=None, watch=5, timeout=30, base="https://j.example.test/vortex",
                                   cookie="c", client_factory=lambda: dropped, poll=lambda _base: None,
                                   clock=Clock()) == 1


def test_command_needs_the_cookie_and_a_condition(monkeypatch, capsys):
    monkeypatch.setattr(room_check, "_load_env", lambda: None)
    monkeypatch.setenv("UI_BRIDGE_URL", "https://j.example.test/vortex")
    monkeypatch.delenv(room_check.COOKIE_ENV, raising=False)
    assert room_check.main(["command", "stop", "--watch", "5"]) == 2
    monkeypatch.setenv(room_check.COOKIE_ENV, "jarvis_session=x")
    assert room_check.main(["command", "stop"]) == 2
    assert "C23_COOKIE" in capsys.readouterr().err
