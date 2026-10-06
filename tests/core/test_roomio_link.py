"""
tests/core/test_roomio_link.py — C-35 pure link policy (K-56 CT-VC-ROOMIO-LINK).

Eligibility matrix (kind x identity-prefix x strict/permissive), newest-wins, linked-still-present
wins (never steal), reload ordering — all with plain fakes, no livekit import.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

import pytest

from src.core.roomio_link import (
    BLOCKED_IDENTITY_PREFIXES,
    KIND_SIP,
    KIND_STANDARD,
    OWNER_IDENTITY_PREFIX,
    eligible,
    pick_target,
)

KIND_INGRESS = 1
KIND_AGENT = 4


class FakeKind(enum.Enum):  # the enum-name path (livekit exposes proto ints, tests may use enums)
    PARTICIPANT_KIND_STANDARD = 0
    PARTICIPANT_KIND_SIP = 3
    PARTICIPANT_KIND_AGENT = 4


@dataclass
class P:
    identity: Any
    kind: Any = KIND_STANDARD


class FakeRoom:
    def __init__(self, *participants: P) -> None:
        self.remote_participants = {p.identity: p for p in participants}


# ---- eligibility matrix -------------------------------------------------------------------------


@pytest.mark.parametrize("kind,kind_ok", [
    (KIND_STANDARD, True),
    (KIND_SIP, True),
    (KIND_INGRESS, False),
    (KIND_AGENT, False),
    (FakeKind.PARTICIPANT_KIND_STANDARD, True),
    (FakeKind.PARTICIPANT_KIND_SIP, True),
    (FakeKind.PARTICIPANT_KIND_AGENT, False),
    ("standard", True),
    ("sip", True),
    (None, False),
    (True, False),
])
@pytest.mark.parametrize("identity,strict,identity_ok", [
    ("owner-1a2b3c4d", False, True),
    ("owner-1a2b3c4d", True, True),
    ("web-abc", False, True),
    ("web-abc", True, False),       # strict: only owner-*
    ("unprefixed", False, True),
    ("unprefixed", True, False),
    ("agent-xyz", False, False),    # never an agent
    ("agent-xyz", True, False),
    ("probe-123", False, False),    # never a probe
    ("probe-123", True, False),
    ("", False, False),
    (None, False, False),
    (42, False, False),
])
def test_eligibility_matrix(kind, kind_ok, identity, strict, identity_ok):
    assert eligible(P(identity, kind), strict) is (kind_ok and identity_ok)


def test_constants_match_contract():
    assert OWNER_IDENTITY_PREFIX == "owner-"
    assert BLOCKED_IDENTITY_PREFIXES == ("agent-", "probe-")


# ---- pick_target --------------------------------------------------------------------------------


def test_linked_still_present_wins_never_steal():
    room = FakeRoom(P("owner-first"), P("owner-second"))
    assert pick_target(room, "owner-first") == "owner-first"


def test_newest_eligible_wins_when_linked_left():
    room = FakeRoom(P("owner-old"), P("web-tab"), P("owner-new"))
    assert pick_target(room, "owner-gone") == "owner-new"  # join order: newest last


def test_reload_new_identity_joined_first_still_relinks():
    # Page reload: the new tab joined BEFORE the old identity's disconnect was processed.
    room = FakeRoom(P("owner-new"))
    assert pick_target(room, "owner-old") == "owner-new"


def test_nobody_linked_picks_newest_eligible():
    room = FakeRoom(P("agent-jarvis", KIND_AGENT), P("owner-a"), P("probe-99"), P("owner-b"))
    assert pick_target(room, None) == "owner-b"


def test_no_eligible_participant_returns_none():
    assert pick_target(FakeRoom(), None) is None
    assert pick_target(FakeRoom(P("agent-x", KIND_AGENT), P("probe-1")), None) is None


def test_strict_owner_filters_non_owner_identities():
    room = FakeRoom(P("owner-a"), P("web-newest"))
    assert pick_target(room, None, strict_owner=True) == "owner-a"
    assert pick_target(room, None, strict_owner=False) == "web-newest"


def test_room_without_remote_participants_attr():
    class Bare:
        pass

    assert pick_target(Bare(), None) is None
    assert pick_target(Bare(), "owner-x") is None
