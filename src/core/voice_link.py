"""
# src/core/voice_link.py
VoiceLink — RoomIO relink wiring and state (C-35, contracts K-56 CT-VC-ROOMIO-LINK + K-04).

Policy decisions live in src/core/roomio_link.py (pure); this module owns the wiring:

- `attach(session)`: stores the live session (called after session.start AND after every C-33
  session rebuild — the rebuilt RoomIO starts unlinked) and runs one immediate relink pass for
  participants already present (covers the reload where the new tab joined first).
- room 'participant_connected': an eligible participant while nobody is linked is linked
  (reason=connect). A second tab NEVER steals an active link.
- room 'participant_disconnected' of the LINKED identity: relink to pick_target() when one exists
  (reason=replace). VoiceLink keeps its OWN `_linked_identity` and decides from it: in
  livekit-agents 1.4.6 RoomIO registers its own participant_disconnected handler during
  session.start (room_io.py:156) — BEFORE attach() registers ours — and that handler resets the
  future backing `RoomIO.linked_participant` when the linked participant leaves
  (room_io.py:377-380), so by the time our handler runs RoomIO already answers None.
- ui_command 'voice_take_over' {identity}: registered HERE via the C-34 registry (K-60: other areas
  register commands from their own modules). Switches only when the identity is present and
  eligible; a take-over that races a disconnect logs and does nothing (failure mode 11 — the next
  participant_connected relinks).
- After EVERY relink the agent attribute 'jarvis.linked_identity' is set via
  room.local_participant.set_attributes, best effort and never blocking (K-04: UIs read it to
  decide whether lk.chat is usable; K-56: every switch sets it), and
  'ROOMIO_RELINK identity=%s reason=%s' is logged (identities are bridge-minted owner-<8hex>,
  not secrets).

RoomIO access prefers public `session.room_io` and falls back to the pinned-private
`session._room_io` (livekit-agents 1.4.6 — today's code uses `_room_io`; the wiring step adds the
used name to the private-member contract test). The public property RAISES RuntimeError whenever
`_room_io` is None (agent_session.py:808-814, e.g. after the session acloses itself — exactly the
C-33 rebuild backoff window), so the access is wrapped, never a bare getattr.

Everything is duck-typed: importable and unit-testable without the livekit runtime.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any

from services.utils.ui_command_registry import register_ui_command
from src.core.roomio_link import SPEAK_TYPED_ENV, audio_out_wanted, eligible, pick_target, speak_policy

logger = logging.getLogger("JARVIS-VOICELINK")

LINKED_IDENTITY_ATTR = "jarvis.linked_identity"
# C-41: room events after which the audio-output gate is decided again
MEDIA_EVENTS = ("track_published", "track_unpublished", "track_muted", "track_unmuted", "track_subscribed",
                "track_unsubscribed")

RELINK_CONNECT = "connect"
RELINK_REPLACE = "replace"
RELINK_TAKE_OVER = "take_over"


def _room_io_of(session: Any) -> Any:
    """Public `session.room_io` when the pinned livekit-agents exposes it, else `session._room_io`.

    In livekit-agents 1.4.6 `AgentSession.room_io` is a property that RAISES
    RuntimeError('Cannot access room_io: ...') whenever `_room_io` is None
    (agent_session.py:808-814) — which it is after the session acloses itself, exactly the C-33
    rebuild backoff window. getattr only swallows AttributeError, so the access is wrapped
    explicitly; any event in that window takes the 'relink skipped' path instead of raising out
    of the rtc emitter or a registry handler."""
    if session is None:
        return None
    try:
        room_io = session.room_io
    except (AttributeError, RuntimeError):
        room_io = None
    if room_io is None:
        room_io = getattr(session, "_room_io", None)
    return room_io


class VoiceLink:
    """Keeps the session's RoomIO pointed at the right (owner) participant for the life of a job."""

    def __init__(self, ctx: Any, *, strict_owner: bool = False, speak_typed: str | None = None,
                 allow_probe: bool = False) -> None:
        self.ctx = ctx
        self.strict_owner = strict_owner
        self.allow_probe = allow_probe  # C-40: a probe job serves its 'probe-*' participant
        # C-41: JARVIS_SPEAK_TYPED ('auto' | 'always' | 'never'), read once per job
        self.speak_policy = speak_policy(speak_typed if speak_typed is not None else os.getenv(SPEAK_TYPED_ENV))
        self.session: Any = None
        self._linked_identity: str | None = None  # VoiceLink is authoritative for 'who did I link'
        self._room_handlers: list[tuple[str, Any]] = []
        self._attr_tasks: set[asyncio.Task[Any]] = set()

    # ---- wiring -------------------------------------------------------------------------------

    @property
    def room(self) -> Any:
        return getattr(self.ctx, "room", None)

    def attach(self, session: Any) -> str | None:
        """Binds the live session (first start or a C-33 rebuild), registers the two room handlers
        exactly once per job, and runs one immediate relink pass. Returns the identity linked by
        that pass, if any."""
        self.session = session
        self._ensure_room_handlers()
        self._watch_agent_state(session)
        linked = self.relink_pass()
        self.update_audio_out("session")  # C-41: a rebuilt session starts with its audio output on
        return linked

    def prepare(self, session: Any) -> None:
        """C-41, before session.start: with nobody to listen, the session starts with its audio output off,
        so a typed reply never waits for a subscriber that is not there."""
        if not self.audio_out_wanted_now():
            self._set_audio_out(session, False, "start")

    def _ensure_room_handlers(self) -> None:
        if self._room_handlers:
            return  # once per job; room handlers survive session rebuilds (C-33 double-listener fix)
        room = self.room
        on = getattr(room, "on", None) if room is not None else None
        if not callable(on):
            return
        on("participant_connected", self._on_participant_connected)
        on("participant_disconnected", self._on_participant_disconnected)
        self._room_handlers = [
            ("participant_connected", self._on_participant_connected),
            ("participant_disconnected", self._on_participant_disconnected),
        ]
        for event in MEDIA_EVENTS:  # C-41: the audio gate follows microphones and subscriptions
            on(event, self._on_media_change)
            self._room_handlers.append((event, self._on_media_change))

    def close(self) -> None:
        """Unregisters the room handlers (teardown; idempotent, never raises)."""
        room = self.room
        off = getattr(room, "off", None) if room is not None else None
        for event, handler in self._room_handlers:
            if callable(off):
                try:
                    off(event, handler)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("room.off(%s) failed: %s", event, exc)
        self._room_handlers = []

    # ---- state --------------------------------------------------------------------------------

    def linked_identity(self) -> str | None:
        """The identity THIS VoiceLink last linked, verified against room membership.

        VoiceLink's own `_linked_identity` is authoritative, NOT RoomIO's future-backed
        `linked_participant`: in livekit-agents 1.4.6 RoomIO's own participant_disconnected
        handler runs before ours (registered at session.start, room_io.py:156) and clears that
        future when the linked participant leaves (room_io.py:377-380), so RoomIO state cannot
        drive the K-56 replace transition. A leaver is never reported (membership check)."""
        identity = self._linked_identity
        if identity is None:
            return None
        remote = getattr(self.room, "remote_participants", None) or {}
        return identity if identity in remote else None

    def _room_io_linked_identity(self) -> str | None:
        """What the CURRENT session's RoomIO reports (None right after a C-33 rebuild — the fresh
        RoomIO starts unlinked — and after the linked participant left)."""
        room_io = _room_io_of(self.session)
        linked = getattr(room_io, "linked_participant", None) if room_io is not None else None
        identity = getattr(linked, "identity", None)
        return identity if isinstance(identity, str) else None

    # ---- events -------------------------------------------------------------------------------

    def _on_participant_connected(self, participant: Any) -> None:
        if not eligible(participant, self.strict_owner, self.allow_probe):
            return
        if self.linked_identity() is None:  # an active link is never stolen (only voice_take_over switches)
            identity = getattr(participant, "identity", None)
            if isinstance(identity, str):
                self._relink(identity, RELINK_CONNECT)
        self.update_audio_out("participant_connected")

    def _on_participant_disconnected(self, participant: Any) -> None:
        identity = getattr(participant, "identity", None)
        if not isinstance(identity, str) or identity != self._linked_identity:
            return  # someone else left: the link is untouched
        # The LINKED participant left. RoomIO (1.4.6) has typically already cleared its
        # linked_participant in its own handler (registered first), so the decision comes from
        # OUR state — the K-56 replace transition must still fire.
        self._linked_identity = None
        target = pick_target(self.room, None, strict_owner=self.strict_owner, allow_probe=self.allow_probe)
        if target is not None and target != identity:
            self._relink(target, RELINK_REPLACE)
        self.update_audio_out("participant_disconnected")

    def relink_pass(self) -> str | None:
        """One policy pass for participants already present: right after session.start, and after
        every session rebuild (the fresh RoomIO starts unlinked and is re-pointed even when OUR
        linked identity is unchanged). Also covers the linked person having left before these
        handlers existed. Returns the identity actually relinked, if any."""
        linked = self.linked_identity()
        target = pick_target(self.room, linked, strict_owner=self.strict_owner, allow_probe=self.allow_probe)
        if target is None:
            return None
        if target == linked and self._room_io_linked_identity() == target:
            return None  # the live RoomIO already points there: nothing to do
        reason = RELINK_REPLACE if (linked is not None and target != linked) else RELINK_CONNECT
        return target if self._relink(target, reason) else None

    def take_over(self, identity: Any) -> bool:
        """ui_command voice_take_over {identity}: the only way to move an ACTIVE link. Re-checks
        presence and eligibility first (a take-over racing a disconnect logs and does nothing)."""
        if not isinstance(identity, str) or not identity:
            logger.info("voice_take_over ignored: no identity in the payload")
            return False
        remote = dict(getattr(self.room, "remote_participants", None) or {})
        participant = remote.get(identity)
        if participant is None or not eligible(participant, self.strict_owner, self.allow_probe):
            logger.info("voice_take_over ignored: identity not present or not eligible")
            return False
        return self._relink(identity, RELINK_TAKE_OVER)

    # ---- C-41: the audio-output gate ----------------------------------------------------------

    def _on_media_change(self, *_args: Any) -> None:
        self.update_audio_out("media")

    def audio_out_wanted_now(self) -> bool:
        return audio_out_wanted(self.room, self.linked_identity(), self.speak_policy,
                                strict_owner=self.strict_owner, allow_probe=self.allow_probe)

    def update_audio_out(self, reason: str) -> None:
        """Turns the live session's audio output on or off when the policy's answer differs from the
        session's current state (a rebuilt session starts on). Never raises."""
        if self.session is not None:
            self._set_audio_out(self.session, self.audio_out_wanted_now(), reason)

    @staticmethod
    def _set_audio_out(session: Any, wanted: bool, reason: str) -> None:
        output = getattr(session, "output", None)
        setter = getattr(output, "set_audio_enabled", None) if output is not None else None
        if not callable(setter) or getattr(output, "audio_enabled", None) == wanted:
            return
        try:
            setter(wanted)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("audio output switch failed: %s", exc)
            return
        logger.info("AUDIO_OUT enabled=%s reason=%s", wanted, reason)

    # ---- C-42: the agent state after a text-only reply ----------------------------------------
    # livekit-agents 1.4.6 returns the state from 'speaking' to 'listening' after a realtime reply only while the audio
    # output is on (agent_activity.py:2446-2450; its pipeline paths do it either way), so with the gate above off every
    # typed reply left the agent 'speaking' for good (lk.agent.state and the UI). Each 'speaking' now waits for the
    # speech that started it and then settles the state as the pipeline path does.

    def _watch_agent_state(self, session: Any) -> None:
        on = getattr(session, "on", None)
        if callable(on):
            on("agent_state_changed", lambda event, s=session: self._on_agent_state(s, event))

    def _on_agent_state(self, session: Any, event: Any) -> None:
        if getattr(event, "new_state", None) == "speaking":
            self._settle_when_done(session)

    def _settle_when_done(self, session: Any) -> None:
        speech = getattr(session, "current_speech", None)
        add_done = getattr(speech, "add_done_callback", None)
        if callable(add_done) and not speech.done():
            add_done(lambda _speech, s=session: self._settle_state(s))

    def _settle_state(self, session: Any) -> None:
        """A speech ended: a state still 'speaking' with nothing else playing becomes 'listening'. With the audio output
        on, livekit-agents has already done it after the playout, so this changes nothing then."""
        if session is not self.session or getattr(session, "agent_state", None) != "speaking":
            return
        speech = getattr(session, "current_speech", None)
        if speech is not None and not speech.done():
            self._settle_when_done(session)  # a follow-up reply (after a tool) is speaking now
            return
        update = getattr(session, "_update_agent_state", None)
        if not callable(update):
            return
        try:
            update("listening")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("agent state settle failed: %s", exc)

    # ---- the relink itself --------------------------------------------------------------------

    def _relink(self, identity: str, reason: str) -> bool:
        room_io = _room_io_of(self.session)
        set_participant = getattr(room_io, "set_participant", None) if room_io is not None else None
        if not callable(set_participant):
            logger.debug("relink skipped: session has no RoomIO set_participant (reason=%s)", reason)
            return False
        try:
            set_participant(identity)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("RoomIO set_participant failed (reason=%s): %s", reason, exc)
            return False
        self._linked_identity = identity  # authoritative state (RoomIO's is future-backed)
        logger.info("ROOMIO_RELINK identity=%s reason=%s", identity, reason)
        self._set_linked_attr(identity)
        return True

    def _set_linked_attr(self, identity: str) -> None:
        """K-56/K-04: agent attribute jarvis.linked_identity after every relink — best effort,
        never blocking, never raising (same pattern as _set_session_attr)."""
        local = getattr(self.room, "local_participant", None)
        set_attributes = getattr(local, "set_attributes", None) if local is not None else None
        if not callable(set_attributes):
            return
        try:
            result = set_attributes({LINKED_IDENTITY_ATTR: identity})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("%s attribute write failed: %s", LINKED_IDENTITY_ATTR, exc)
            return
        if not inspect.isawaitable(result):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            result.close()  # type: ignore[attr-defined]  # no loop (a sync test): drop it silently
            return
        task = loop.create_task(self._await_attr_write(result))
        self._attr_tasks.add(task)
        task.add_done_callback(self._attr_tasks.discard)

    @staticmethod
    async def _await_attr_write(awaitable: Any) -> None:
        try:
            await awaitable
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("%s attribute write failed: %s", LINKED_IDENTITY_ATTR, exc)


# ---- C-34 registry command (K-60: registered from this module, not from agent_loops) ------------

@register_ui_command("voice_take_over")
async def _voice_take_over_command(payload: Any, ctx: Any) -> None:
    """ui_command voice_take_over {identity} (K-56/K-60): the runtime's VoiceLink decides."""
    link = getattr(getattr(ctx, "runtime", None), "voice_link", None)
    if link is None:
        logger.warning("voice_take_over ignored: this job has no voice link")
        return
    identity = payload.get("identity") if isinstance(payload, dict) else None
    link.take_over(identity)
