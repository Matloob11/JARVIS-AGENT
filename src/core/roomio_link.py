"""
# src/core/roomio_link.py
RoomIO link policy (C-35, contract K-56 CT-VC-ROOMIO-LINK) — PURE decisions, no LiveKit I/O.

Who may the voice session listen to, and who should be linked next. The wiring (VoiceLink in
src/core/voice_link.py) calls these with live LiveKit objects; unit tests call them with plain fakes.
This module imports nothing from livekit on purpose (importable without the livekit runtime), so the
participant kind is matched by enum name or by the pinned protocol values (livekit 1.1.2:
ParticipantInfo.Kind STANDARD=0, INGRESS=1, EGRESS=2, SIP=3, AGENT=4).

Policy (K-56, design 2026-10-06):
- eligible(p, strict_owner): kind STANDARD or SIP (SIP stays in the permissive set — today's
  `_may_link` accepts SIP and that is a live feature; open question Q1), identity a non-empty string
  not starting with 'agent-' or 'probe-'; with strict_owner=True (remote mode, or
  JARVIS_RPC_OWNER_ONLY on the PC, mirroring rpc_owner_only()) the identity must start 'owner-'.
- pick_target(room, linked): a linked participant still in the room wins (NEVER steal — only
  voice_take_over switches an active link); else the NEWEST eligible participant
  (room.remote_participants keeps join order: the newest one is last); else None.
"""
from __future__ import annotations

from typing import Any

OWNER_IDENTITY_PREFIX = "owner-"
BLOCKED_IDENTITY_PREFIXES = ("agent-", "probe-")

# livekit 1.1.2 ParticipantInfo.Kind values (pinned; the contract test of the wiring step guards drift).
KIND_STANDARD = 0
KIND_SIP = 3
_HUMAN_KIND_VALUES = frozenset({KIND_STANDARD, KIND_SIP})
_HUMAN_KIND_NAMES = frozenset({
    "standard", "participant_kind_standard",
    "sip", "participant_kind_sip",
})


def _kind_is_human(kind: Any) -> bool:
    """True for a STANDARD or SIP participant kind, given as an enum (matched by name), a string,
    or the raw protocol int. None or anything unknown is not linkable."""
    name = getattr(kind, "name", None)
    if isinstance(name, str):
        return name.lower() in _HUMAN_KIND_NAMES
    if isinstance(kind, str):
        return kind.lower() in _HUMAN_KIND_NAMES
    if isinstance(kind, bool) or kind is None:
        return False
    try:
        return int(kind) in _HUMAN_KIND_VALUES
    except (TypeError, ValueError):
        return False


def eligible(participant: Any, strict_owner: bool = False) -> bool:
    """May the voice session link this participant? (K-56.) A person (STANDARD or SIP), never an
    'agent-*' or 'probe-*' identity; with strict_owner only an 'owner-*' identity qualifies."""
    identity = getattr(participant, "identity", None)
    if not isinstance(identity, str) or not identity:
        return False
    if not _kind_is_human(getattr(participant, "kind", None)):
        return False
    if identity.startswith(BLOCKED_IDENTITY_PREFIXES):
        return False
    if strict_owner and not identity.startswith(OWNER_IDENTITY_PREFIX):
        return False
    return True


def pick_target(room: Any, linked: str | None, *, strict_owner: bool = False) -> str | None:
    """The identity the RoomIO should be linked to now. A linked participant still in the room wins
    (never steal); else the newest eligible participant; else None (leave the link alone)."""
    remote = dict(getattr(room, "remote_participants", None) or {})
    if isinstance(linked, str) and linked in remote:
        return linked
    candidates = [p for p in remote.values() if eligible(p, strict_owner)]
    if not candidates:
        return None
    identity = getattr(candidates[-1], "identity", None)  # join order: the newest one is last
    return identity if isinstance(identity, str) else None
