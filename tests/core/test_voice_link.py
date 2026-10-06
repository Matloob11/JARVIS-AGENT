"""
tests/core/test_voice_link.py — C-35 VoiceLink wiring (K-56 / K-04).

Fakes only, no livekit: attach runs a relink pass (first start AND after a session rebuild, where
the fresh RoomIO starts unlinked), room handlers register once per job, connect/replace/take_over
relinks, a second tab never steals, and jarvis.linked_identity is written after EVERY relink.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from services.utils.ui_command_registry import UI_COMMANDS, CommandContext, dispatch, reset_dedupe
from src.core.voice_link import LINKED_IDENTITY_ATTR, VoiceLink


class FakeParticipant:
    def __init__(self, identity: str, kind: int = 0) -> None:
        self.identity = identity
        self.kind = kind


class FakeRoomIO:
    """Mirrors livekit-agents 1.4.6 RoomIO state semantics: `linked_participant` is backed by
    `_participant_available_fut`, and RoomIO's OWN participant_disconnected handler resets that
    future when the linked participant leaves (room_io.py:377-380) — so `linked_participant`
    answers None by the time VoiceLink's handler runs — while `_participant_identity` is kept."""

    def __init__(self) -> None:
        self.linked_participant: FakeParticipant | None = None
        self._participant_identity: str | None = None
        self.set_calls: list[str] = []

    def set_participant(self, identity: str) -> None:
        self.set_calls.append(identity)
        self._participant_identity = identity
        self.linked_participant = FakeParticipant(identity)

    def _on_participant_disconnected(self, participant: Any) -> None:
        linked = self.linked_participant
        if linked is None or getattr(participant, "identity", None) != linked.identity:
            return
        self.linked_participant = None  # the future reset; _participant_identity survives


class FakeLocalParticipant:
    def __init__(self, *, asynchronous: bool = False, fail: bool = False) -> None:
        self.attr_writes: list[dict[str, str]] = []
        self._async = asynchronous
        self._fail = fail

    def set_attributes(self, attrs: dict[str, str]) -> Any:
        if self._fail and not self._async:
            raise RuntimeError("rtc down")
        if not self._async:
            self.attr_writes.append(dict(attrs))
            return None

        async def _write() -> None:
            if self._fail:
                raise RuntimeError("rtc down")
            self.attr_writes.append(dict(attrs))

        return _write()


class FakeRoom:
    def __init__(self, *participants: FakeParticipant, local: FakeLocalParticipant | None = None) -> None:
        self.remote_participants = {p.identity: p for p in participants}
        self.local_participant = local or FakeLocalParticipant()
        self.handlers: list[tuple[str, Any]] = []
        self.off_calls: list[tuple[str, Any]] = []

    def on(self, event: str, handler: Any) -> None:
        self.handlers.append((event, handler))

    def off(self, event: str, handler: Any) -> None:
        self.off_calls.append((event, handler))

    def emit(self, event: str, participant: FakeParticipant) -> None:
        for name, handler in list(self.handlers):
            if name == event:
                handler(participant)

    def join(self, participant: FakeParticipant) -> None:
        self.remote_participants[participant.identity] = participant
        self.emit("participant_connected", participant)

    def leave(self, identity: str) -> None:
        participant = self.remote_participants.pop(identity)
        self.emit("participant_disconnected", participant)


class FakeSession:
    def __init__(self, *, public_room_io: bool = False) -> None:
        if public_room_io:
            self.room_io = FakeRoomIO()
        else:
            self._room_io = FakeRoomIO()

    @property
    def rio(self) -> FakeRoomIO:
        return getattr(self, "room_io", None) or self._room_io  # type: ignore[return-value]


class FakeCtx:
    def __init__(self, room: FakeRoom) -> None:
        self.room = room


def _link(room: FakeRoom, *, strict: bool = False) -> VoiceLink:
    return VoiceLink(FakeCtx(room), strict_owner=strict)


def _start_like_roomio(room: FakeRoom, session: FakeSession) -> None:
    """What session.start does on livekit-agents 1.4.6: RoomIO registers its own room handlers
    (room_io.py:156) BEFORE VoiceLink.attach ever runs, so on a leave RoomIO's handler clears
    linked_participant first."""
    room.on("participant_disconnected", session.rio._on_participant_disconnected)


# ---- attach -------------------------------------------------------------------------------------


def test_attach_registers_room_handlers_once_and_relinks_present_owner(caplog):
    room = FakeRoom(FakeParticipant("owner-aaaa1111"))
    link = _link(room)
    session = FakeSession()
    with caplog.at_level(logging.INFO, logger="JARVIS-VOICELINK"):
        assert link.attach(session) == "owner-aaaa1111"
    assert session.rio.set_calls == ["owner-aaaa1111"]
    assert [e for e, _ in room.handlers] == ["participant_connected", "participant_disconnected"]
    assert any("ROOMIO_RELINK" in r.getMessage() and "reason=connect" in r.getMessage()
               for r in caplog.records)

    link.attach(session)  # a second attach (same job) must not double the room handlers
    assert len(room.handlers) == 2


def test_attach_after_rebuild_relinks_the_fresh_unlinked_roomio():
    room = FakeRoom(FakeParticipant("owner-aaaa1111"))
    link = _link(room)
    first = FakeSession()
    link.attach(first)
    assert first.rio.set_calls == ["owner-aaaa1111"]

    rebuilt = FakeSession()  # the C-33 rebuild: new session, RoomIO starts unlinked
    link.attach(rebuilt)
    assert rebuilt.rio.set_calls == ["owner-aaaa1111"]
    assert len(room.handlers) == 2  # room handlers still registered exactly once


def test_attach_with_empty_room_links_nobody():
    room = FakeRoom()
    link = _link(room)
    session = FakeSession()
    assert link.attach(session) is None
    assert session.rio.set_calls == []


def test_attach_uses_public_room_io_when_present():
    room = FakeRoom(FakeParticipant("owner-pub"))
    link = _link(room)
    session = FakeSession(public_room_io=True)
    link.attach(session)
    assert session.room_io.set_calls == ["owner-pub"]


class RaisingRoomIOSession:
    """livekit-agents 1.4.6 AgentSession whose _room_io is None (e.g. after it aclosed itself —
    the C-33 rebuild backoff window): the room_io property RAISES RuntimeError
    (agent_session.py:808-814) instead of returning None."""

    _room_io = None

    @property
    def room_io(self) -> Any:
        raise RuntimeError("Cannot access room_io: the AgentSession was not started with a room.")


def test_room_io_property_raising_runtime_error_never_escapes(caplog):
    """F2: getattr only swallows AttributeError — the raising property must not blow
    linked_identity()/_relink out of the rtc emitter; the designed 'relink skipped' path runs."""
    room = FakeRoom(FakeParticipant("owner-a"))
    link = _link(room)
    session = RaisingRoomIOSession()
    with caplog.at_level(logging.DEBUG, logger="JARVIS-VOICELINK"):
        assert link.attach(session) is None  # the relink pass logs-and-skips, no RuntimeError
        assert link.linked_identity() is None
        room.join(FakeParticipant("owner-b"))  # room events in that window must not raise either
        room.leave("owner-b")
    assert any("relink skipped" in r.getMessage() for r in caplog.records)


# ---- participant_connected ----------------------------------------------------------------------


def test_connect_links_eligible_participant_when_nobody_linked():
    room = FakeRoom()
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    room.join(FakeParticipant("owner-bbbb2222"))
    assert session.rio.set_calls == ["owner-bbbb2222"]


def test_second_tab_never_steals_an_active_link():
    room = FakeRoom(FakeParticipant("owner-first"))
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    room.join(FakeParticipant("owner-second-tab"))
    assert session.rio.set_calls == ["owner-first"]  # the active link did not move


@pytest.mark.parametrize("identity,kind", [("agent-jarvis", 4), ("probe-123", 0)])
def test_connect_ignores_agent_and_probe_identities(identity, kind):
    room = FakeRoom()
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    room.join(FakeParticipant(identity, kind))
    assert session.rio.set_calls == []


def test_strict_owner_refuses_web_identities():
    room = FakeRoom()
    link = _link(room, strict=True)
    session = FakeSession()
    link.attach(session)
    room.join(FakeParticipant("web-tab"))
    assert session.rio.set_calls == []
    room.join(FakeParticipant("owner-cccc3333"))
    assert session.rio.set_calls == ["owner-cccc3333"]


# ---- participant_disconnected -------------------------------------------------------------------


def test_disconnect_of_linked_relinks_to_newest_remaining(caplog):
    room = FakeRoom(FakeParticipant("owner-old"))
    link = _link(room)
    session = FakeSession()
    _start_like_roomio(room, session)
    link.attach(session)
    room.join(FakeParticipant("owner-new"))  # second tab: no steal yet
    assert session.rio.set_calls == ["owner-old"]
    with caplog.at_level(logging.INFO, logger="JARVIS-VOICELINK"):
        room.leave("owner-old")
    assert session.rio.set_calls == ["owner-old", "owner-new"]
    assert any("reason=replace" in r.getMessage() for r in caplog.records)


def test_disconnect_of_someone_else_is_a_noop():
    room = FakeRoom(FakeParticipant("owner-linked"))
    link = _link(room)
    session = FakeSession()
    _start_like_roomio(room, session)
    link.attach(session)
    assert session.rio.set_calls == ["owner-linked"]
    room.join(FakeParticipant("web-viewer"))  # no steal
    room.leave("web-viewer")  # someone else left: the link is untouched
    assert session.rio.set_calls == ["owner-linked"]


def test_disconnect_of_linked_with_nobody_left_keeps_quiet():
    room = FakeRoom(FakeParticipant("owner-only"))
    link = _link(room)
    session = FakeSession()
    _start_like_roomio(room, session)
    link.attach(session)
    room.leave("owner-only")
    assert session.rio.set_calls == ["owner-only"]  # no second set_participant call
    assert link.linked_identity() is None


def test_replace_relink_fires_with_real_roomio_handler_ordering(caplog):
    """F1: in 1.4.6 RoomIO's own participant_disconnected handler runs BEFORE VoiceLink's and has
    already cleared linked_participant when the linked participant leaves — the K-56 'relink to
    the newest remaining' transition must still fire (second tab takes over on close of the
    first)."""
    room = FakeRoom(FakeParticipant("owner-old"))
    link = _link(room)
    session = FakeSession()
    _start_like_roomio(room, session)  # RoomIO's handler first, exactly like session.start
    link.attach(session)
    room.join(FakeParticipant("owner-new"))  # second tab: no steal
    assert session.rio.set_calls == ["owner-old"]
    with caplog.at_level(logging.INFO, logger="JARVIS-VOICELINK"):
        room.leave("owner-old")
    # RoomIO cleared its state first; VoiceLink's own _linked_identity drove the replace anyway
    assert session.rio.set_calls == ["owner-old", "owner-new"]
    assert session.rio.linked_participant.identity == "owner-new"
    assert link.linked_identity() == "owner-new"
    assert any("reason=replace" in r.getMessage() for r in caplog.records)


# ---- take_over ----------------------------------------------------------------------------------


def test_take_over_switches_an_active_link(caplog):
    room = FakeRoom(FakeParticipant("owner-a"), FakeParticipant("owner-b"))
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    assert session.rio.set_calls == ["owner-b"]  # newest wins on the attach pass
    with caplog.at_level(logging.INFO, logger="JARVIS-VOICELINK"):
        assert link.take_over("owner-a") is True
    assert session.rio.set_calls == ["owner-b", "owner-a"]
    assert any("reason=take_over" in r.getMessage() for r in caplog.records)


def test_take_over_racing_a_disconnect_does_nothing():
    room = FakeRoom(FakeParticipant("owner-here"))
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    assert link.take_over("owner-already-left") is False  # failure mode 11: just logs
    assert link.take_over(None) is False
    assert link.take_over("") is False
    assert session.rio.set_calls == ["owner-here"]


def test_take_over_refuses_ineligible_identity():
    room = FakeRoom(FakeParticipant("owner-x"), FakeParticipant("agent-jarvis", 4))
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    assert link.take_over("agent-jarvis") is False
    assert session.rio.set_calls == ["owner-x"]


# ---- jarvis.linked_identity attribute -----------------------------------------------------------


def test_attribute_written_after_every_relink_sync():
    room = FakeRoom(FakeParticipant("owner-a"), FakeParticipant("owner-b"))
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    link.take_over("owner-a")
    assert room.local_participant.attr_writes == [
        {LINKED_IDENTITY_ATTR: "owner-b"},
        {LINKED_IDENTITY_ATTR: "owner-a"},
    ]


async def test_attribute_write_supports_async_set_attributes():
    local = FakeLocalParticipant(asynchronous=True)
    room = FakeRoom(FakeParticipant("owner-async"), local=local)
    link = _link(room)
    link.attach(FakeSession())
    for _ in range(3):
        await asyncio.sleep(0)  # let the fire-and-forget attribute task run
    assert local.attr_writes == [{LINKED_IDENTITY_ATTR: "owner-async"}]


async def test_attribute_write_failures_never_break_the_relink():
    sync_fail = FakeLocalParticipant(fail=True)
    room = FakeRoom(FakeParticipant("owner-x"), local=sync_fail)
    link = _link(room)
    session = FakeSession()
    link.attach(session)
    assert session.rio.set_calls == ["owner-x"]  # the relink itself happened

    async_fail = FakeLocalParticipant(asynchronous=True, fail=True)
    room2 = FakeRoom(FakeParticipant("owner-y"), local=async_fail)
    link2 = _link(room2)
    session2 = FakeSession()
    link2.attach(session2)
    for _ in range(3):
        await asyncio.sleep(0)
    assert session2.rio.set_calls == ["owner-y"]


# ---- close + registry command -------------------------------------------------------------------


def test_close_unregisters_room_handlers_and_is_idempotent():
    room = FakeRoom(FakeParticipant("owner-z"))
    link = _link(room)
    link.attach(FakeSession())
    registered = list(room.handlers)
    link.close()
    assert room.off_calls == registered
    link.close()  # idempotent
    assert room.off_calls == registered


def test_voice_take_over_registered_from_this_module():
    assert "voice_take_over" in UI_COMMANDS


async def test_voice_take_over_dispatch_reaches_the_runtimes_voice_link():
    reset_dedupe()
    room = FakeRoom(FakeParticipant("owner-a"), FakeParticipant("owner-b"))
    link = _link(room)
    session = FakeSession()
    link.attach(session)

    class _Runtime:
        voice_link = link

    ctx = CommandContext(runtime=_Runtime())
    assert await dispatch("voice_take_over", {"identity": "owner-a"}, ctx) is True
    assert session.rio.set_calls == ["owner-b", "owner-a"]


async def test_voice_take_over_without_a_runtime_voice_link_is_harmless():
    reset_dedupe()
    assert await dispatch("voice_take_over", {"identity": "owner-a"}, CommandContext()) is True
