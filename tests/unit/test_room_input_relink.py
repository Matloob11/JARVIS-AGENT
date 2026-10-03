"""The voice session follows the owner when he reconnects under a new identity (2026-10-03 incident).

The bridge mints a new 'owner-<8 hex>' identity for every LiveKit token. livekit-agents' RoomIO keeps waiting for
the identity it linked first and ignores a participant with another identity, so after a UI reconnect the agent
stopped hearing the owner. relink_room_input() links the newest person when the linked one has left.
"""
from types import SimpleNamespace

from livekit import rtc

from src.core import agent_runner

STANDARD = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
AGENT = rtc.ParticipantKind.PARTICIPANT_KIND_AGENT


def person(identity, kind=STANDARD):
    return SimpleNamespace(identity=identity, kind=kind)


class FakeRoomIO:
    def __init__(self, linked=None):
        self.linked_participant = linked
        self.calls = []

    def set_participant(self, identity):
        self.calls.append(identity)


class FakeRoom:
    def __init__(self, *participants):
        self.remote_participants = {p.identity: p for p in participants}
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def fire(self, event, participant):
        for handler in self.handlers.get(event, []):
            handler(participant)


def session_with(room_io):
    return SimpleNamespace(_room_io=room_io)


def test_the_owner_is_followed_to_his_new_identity_after_a_reconnect():
    room_io = FakeRoomIO(linked=None)  # the linked owner-a4700ca6 has left; RoomIO waits for him
    room = FakeRoom(person("agent-AJ_1", AGENT), person("owner-1002473c"))

    assert agent_runner.relink_room_input(session_with(room_io), room) == "owner-1002473c"
    assert room_io.calls == ["owner-1002473c"]


def test_nothing_changes_while_the_linked_person_is_still_in_the_room():
    linked = person("owner-a4700ca6")
    room_io = FakeRoomIO(linked=linked)
    room = FakeRoom(linked, person("owner-1002473c"))

    assert agent_runner.relink_room_input(session_with(room_io), room) is None
    assert room_io.calls == []


def test_an_agent_or_an_empty_room_is_never_linked():
    room_io = FakeRoomIO(linked=None)
    assert agent_runner.relink_room_input(session_with(room_io), FakeRoom(person("agent-AJ_1", AGENT))) is None
    assert agent_runner.relink_room_input(session_with(room_io), FakeRoom()) is None
    assert room_io.calls == []


def test_the_newest_person_is_linked_when_two_are_left():
    room_io = FakeRoomIO(linked=None)
    room = FakeRoom(person("owner-older"), person("owner-newest"))

    assert agent_runner.relink_room_input(session_with(room_io), room) == "owner-newest"


def test_a_session_without_room_io_is_left_alone():
    assert agent_runner.relink_room_input(SimpleNamespace(), FakeRoom(person("owner-x"))) is None


def test_the_room_events_relink_on_a_reconnect_but_not_for_an_agent():
    linked = person("owner-a4700ca6")
    room_io = FakeRoomIO(linked=linked)
    room = FakeRoom(linked)
    agent_runner.follow_owner_reconnects(session_with(room_io), room)
    assert room_io.calls == []  # the linked owner is still here

    del room.remote_participants[linked.identity]  # he drops; RoomIO resets its link
    room_io.linked_participant = None
    room.fire("participant_disconnected", linked)
    assert room_io.calls == []  # nobody else to link yet

    ghost = person("agent-AJ_2", AGENT)
    room.remote_participants[ghost.identity] = ghost
    room.fire("participant_connected", ghost)
    assert room_io.calls == []  # an agent never takes the owner's place

    back = person("owner-1002473c")
    room.remote_participants[back.identity] = back
    room.fire("participant_connected", back)
    assert room_io.calls == ["owner-1002473c"]


def test_the_voice_session_survives_the_owner_leaving():
    """A page refresh is a client-initiated leave; with the library default the whole session closed."""
    assert agent_runner.session_room_options().close_on_disconnect is False


def test_the_entrypoint_starts_the_session_with_these_room_options():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(agent_runner))
    starts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == "start"
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "session"]
    assert starts, "session.start(...) not found"
    for call in starts:
        options = [kw for kw in call.keywords if kw.arg == "room_options"]
        assert options and isinstance(options[0].value, ast.Call)
        assert getattr(options[0].value.func, "id", None) == "session_room_options"
