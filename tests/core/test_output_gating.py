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


def test_a_probe_identity_counts_only_in_a_probe_job():
    """C-40: in a probe job's own room the 'probe-*' participant is the person the session serves; anywhere else a
    probe identity never links and never turns audio on (K-56)."""
    probe = _person("probe-1791460000", _pub())
    room = _Room(probe)
    assert roomio_link.eligible(probe) is False and roomio_link.eligible(probe, allow_probe=True) is True
    assert roomio_link.audio_out_wanted(room, None, "auto") is False
    assert roomio_link.audio_out_wanted(room, None, "auto", allow_probe=True) is True
    assert roomio_link.pick_target(room, None, allow_probe=True) == "probe-1791460000"
    agent = _person("agent-AJ_1", _pub())
    assert roomio_link.eligible(agent, allow_probe=True) is False  # an agent never counts


# ---------------------------------------------------------------- C-42: the agent state after a text-only reply


class _Speech:
    def __init__(self) -> None:
        self._done, self._callbacks = False, []

    def done(self) -> bool:
        return self._done

    def add_done_callback(self, callback) -> None:
        self._callbacks.append(callback)

    def finish(self) -> None:
        self._done = True
        for callback in self._callbacks:
            callback(self)


class _StateSession(_Session):
    """A _Session with livekit-agents' agent_state, current_speech, _update_agent_state and event handlers."""

    def __init__(self) -> None:
        super().__init__()
        self.agent_state, self.current_speech = "listening", None
        self.states: list[str] = []
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def _update_agent_state(self, state: str) -> None:
        self.agent_state = state
        self.states.append(state)

    def speak(self, speech: _Speech) -> None:
        """livekit: the first text (or audio frame) of a speech makes the state 'speaking'."""
        self.current_speech, self.agent_state = speech, "speaking"
        self.handlers["agent_state_changed"](SimpleNamespace(new_state="speaking"))


def _attached() -> tuple[VoiceLink, _StateSession]:
    session = _StateSession()
    link = VoiceLink(SimpleNamespace(room=_Room()), speak_typed="auto")
    link.attach(session)
    return link, session


def test_a_text_only_reply_ends_in_listening():
    _link, session = _attached()
    speech = _Speech()
    session.speak(speech)
    assert session.states == []
    speech.finish()
    assert session.states == ["listening"]


def test_a_spoken_reply_is_left_to_livekit():
    _link, session = _attached()
    speech = _Speech()
    session.speak(speech)
    session.agent_state = "listening"  # livekit-agents did it after the playout
    speech.finish()
    assert session.states == []


def test_a_reply_after_a_tool_settles_when_it_ends():
    _link, session = _attached()
    first, follow_up = _Speech(), _Speech()
    session.speak(first)
    session.current_speech = follow_up  # the tool reply is speaking when the first speech ends
    first.finish()
    assert session.states == []
    follow_up.finish()
    assert session.states == ["listening"]


def test_a_replaced_session_is_never_touched():
    link, old = _attached()
    speech = _Speech()
    old.speak(speech)
    link.attach(_StateSession())  # a C-33 rebuild
    speech.finish()
    assert old.states == []


def test_the_livekit_gap_the_state_fix_covers_still_exists():
    """livekit-agents 1.4.6 settles 'speaking' after a realtime reply only with audio output on; an upgrade that fixes
    it (or renames the state setter) fails here, so the C-42 workaround can go."""
    import inspect

    from livekit.agents.voice import agent_activity, agent_session

    source = inspect.getsource(agent_activity.AgentActivity._realtime_generation_task_impl)
    assert 'elif self._session.agent_state == "speaking"' not in source
    assert "audio_output.wait_for_playout()" in source
    assert callable(getattr(agent_session.AgentSession, "_update_agent_state", None))
    assert isinstance(getattr(agent_session.AgentSession, "current_speech", None), property)
