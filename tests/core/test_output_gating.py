"""
tests/core/test_output_gating.py — plan C-41: typed replies never wait for a browser.

livekit-agents' RoomIO audio output waits for a subscriber before every frame, so a reply hangs while nobody in the
room listens. The job's VoiceLink keeps the session's audio output on only while it is wanted: policy
JARVIS_SPEAK_TYPED 'auto' (the linked person's microphone is live), 'always' (an eligible person is present) or
'never'; 'AUDIO_OUT enabled=<b> reason=<r>' is logged on every switch.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.core import roomio_link
from src.core.voice_link import VoiceLink

AUDIO, VIDEO = 1, 2
MICROPHONE, CAMERA = 2, 1


def _pub(kind: int = AUDIO, source: int = MICROPHONE, muted: bool = False) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, source=source, muted=muted)


def _person(identity: str, *pubs: SimpleNamespace, kind: int = 0) -> SimpleNamespace:
    return SimpleNamespace(identity=identity, kind=kind, track_publications={f"TR_{i}": p for i, p in enumerate(pubs)})


class _Room:
    def __init__(self, *people: SimpleNamespace) -> None:
        self.remote_participants = {p.identity: p for p in people}
        self.handlers: list[tuple[str, Any]] = []
        self.local_participant = SimpleNamespace(set_attributes=lambda _a: None)

    def on(self, event: str, handler: Any) -> None:
        self.handlers.append((event, handler))

    def off(self, event: str, handler: Any) -> None:
        self.handlers.remove((event, handler))

    def fire(self, event: str, *args: Any) -> None:
        for name, handler in list(self.handlers):
            if name == event:
                handler(*args)


class _Session:
    """An AgentSession stand-in: output.set_audio_enabled / audio_enabled (default on) and a RoomIO to relink."""

    def __init__(self) -> None:
        self.switches: list[bool] = []
        session = self

        class _Output:
            audio_enabled = True

            def set_audio_enabled(self, enabled: bool) -> None:
                self.audio_enabled = enabled
                session.switches.append(enabled)

        self.output = _Output()
        self._room_io = SimpleNamespace(set_participant=lambda _identity: None, linked_participant=None)


@pytest.mark.parametrize("room, policy, wanted", [
    (_Room(), "auto", False),                                                     # nobody in the room
    (_Room(_person("owner-a", _pub())), "auto", True),                            # voice on
    (_Room(_person("owner-a", _pub(muted=True))), "auto", False),                 # mic muted
    (_Room(_person("owner-a")), "auto", False),                                   # connected, no mic at all
    (_Room(_person("owner-a", _pub(kind=VIDEO, source=CAMERA))), "auto", False),  # a camera is no microphone
    (_Room(_person("owner-a", _pub(muted=True))), "always", True),
    (_Room(_person("owner-a", _pub())), "never", False),
    (_Room(_person("agent-x", _pub())), "always", False),                         # agents never count
])
def test_the_policy(room: _Room, policy: str, wanted: bool):
    assert roomio_link.audio_out_wanted(room, None, policy) is wanted


def test_an_unknown_policy_is_auto():
    assert roomio_link.speak_policy("sometimes") == "auto" and roomio_link.speak_policy(None) == "auto"
    assert roomio_link.speak_policy(" Always ") == "always"


def test_with_nobody_listening_the_session_starts_with_audio_off(caplog):
    session = _Session()
    link = VoiceLink(SimpleNamespace(room=_Room()), speak_typed="auto")
    with caplog.at_level(logging.INFO, logger="JARVIS-VOICELINK"):
        link.prepare(session)
        link.attach(session)
    assert session.switches == [False]  # once: attach finds it already off
    assert "AUDIO_OUT enabled=False reason=start" in caplog.text


def test_the_gate_follows_the_owners_microphone(caplog):
    mic = _pub(muted=True)
    owner = _person("owner-a", mic)
    room = _Room(owner)
    session = _Session()
    link = VoiceLink(SimpleNamespace(room=room), speak_typed="auto")
    with caplog.at_level(logging.INFO, logger="JARVIS-VOICELINK"):
        link.prepare(session)
        link.attach(session)
        mic.muted = False
        room.fire("track_unmuted", owner, mic)
        mic.muted = True
        room.fire("track_muted", owner, mic)
        room.remote_participants.clear()
        room.fire("participant_disconnected", owner)
    assert session.switches == [False, True, False]
    assert "AUDIO_OUT enabled=True reason=media" in caplog.text


def test_a_rebuilt_session_gets_the_gate_again():
    link = VoiceLink(SimpleNamespace(room=_Room()), speak_typed="auto")
    first, rebuilt = _Session(), _Session()
    link.attach(first)
    link.attach(rebuilt)  # a C-33 rebuild: the fresh session starts with audio on
    assert first.switches == [False] and rebuilt.switches == [False]


def test_a_joining_listener_turns_audio_on_right_away():
    room = _Room()
    session = _Session()
    link = VoiceLink(SimpleNamespace(room=room), speak_typed="auto")
    link.attach(session)
    owner = _person("owner-a", _pub())
    room.remote_participants[owner.identity] = owner
    room.fire("participant_connected", owner)
    assert session.switches == [False, True]
