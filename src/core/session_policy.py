"""
# src/core/session_policy.py
C-114: 24/7 voice session safety. One job may serve an open dashboard for hours; these pieces keep its Gemini Live
session inside the model's limits and end a job nobody uses.

- compression_config(): Gemini's own server-side sliding window (ContextWindowCompressionConfig). Compression
  starts at JARVIS_CTX_TRIGGER_TOKENS (K-69, default 24000) and keeps 16000 tokens, always below the trigger.
  agent_runner passes it to the job's RealtimeModel in every mode.
- ChatTruncator: every 20 conversation items (agent_runner's conversation_item_added handler counts them) the chat
  context is cut for real, at a quiet point (never while the plugin's session lock is busy) and never two at once, in
  its two copies: the live realtime session's own (the copy the google plugin replays on every reconnect, a go_away
  or a C-49/C-50 fresh restart), handed back to it truncated to its last 40 items, so its diff holds removals only
  and nothing reaches Gemini again; and the agent's own (what a C-33 rebuild sends to its new session), truncated to
  its last 40 and set directly, the way livekit sets it, without a push. In both, the turns re-seeded from memory
  (below) are one block: a cut that takes their '[MEMORY]' note takes every one of them, so a replayed turn never
  stays without the note that says it is old memory (a later fresh reconnect would hand it to Gemini as the plain
  start of the conversation). Before, both grew without bound
  (BrainAssistant.prune_chat_context edited a copy and only ran from on_user_turn_completed, which Gemini's
  server-side turn detection never calls). The plugin logs a WARNING for every such cut;
  install_truncate_warning_filter() drops exactly that record.
- reseed_chat_ctx(): at job start, before session.start, the turns the assistant's ConversationMemory saved in the
  30 minutes before its newest saved turn (at most 20), and only when that newest turn is at most 60 minutes old, go
  into its chat context as user/model turns (Gemini drops system items) after one '[MEMORY]' note that says they
  come from memory and how old they are, so a new job (a restart, an idle recycle) still knows the conversation it
  follows. C-52 keeps C-114's bounds (since C-52 every message reaches the memory file within about 10 s and at the
  job's end, so the newest saved turn is the conversation's real end) and records the persona of every saved reply:
  a reply of the other persona than the one the job starts as (its saved persona, C-54) stays out together with the
  user turns it answered, so ANNA's replies never come back as the model's own turns in a job that starts as JARVIS
  (or the other way round); a reply saved before C-52 has no persona, counts as unknown and is replayed as before.
  User turns at the end with no reply after them stay out: the model must never take an old request for one still
  waiting. Every re-seeded message, the note too, has an id starting 'reseed-'; without_reseeded() keeps them away
  from the memory extractor (which also skips such ids itself), so a replayed turn is never saved twice.
- SessionPolicy: built with the job's parts (every session reports its conversation activity to it), its loop on
  the job's own protector (never in a probe or canary job, K-57). Every 60 s it compares the date part of the clock
  and the city (local mode: the readings of the existing location loop; remote mode: the Desktop Node's location
  that agent-svc caches, read here without a network call) with the assistant's and calls refresh_instructions()
  once on a real change (C-49/C-50 apply it at a safe point with one fresh reconnect). A new day refreshes at once.
  A new city counts only once 5 readings in a row (about 5 minutes) agree on it, and a city change refreshes at most
  once an hour (a job's first one at once; a new day takes an agreed city along at no extra cost), so an
  IP-geolocated city that flaps (a mobile hotspot, a VPN) never costs a reconnect every few minutes; a skipped flap is
  logged at DEBUG, never the city. It also ends the job through request_end('idle') when nobody has been in the room
  (no participant the voice link linked, and no other participant the bridge counts as a person for its on_demand
  demand: kind STANDARD, identity not 'agent-*' or 'probe-*') and nothing was said for JARVIS_IDLE_CLOSE_MIN minutes,
  and, for a job older than 24 h, at its first 10 minutes without conversation, people in the room or not. Neither
  rule ends a job while the agent speaks or thinks, a tool call runs or a background job of the job waits, nor a
  console-mode job. The idle close defaults to 30 minutes on the VPS brain and to off on the PC brain, whose bridge
  ('always' dispatch) would refill the empty room at once; 0 turns both rules off. The bridge then dispatches a fresh
  job when the room needs one (C-23).

Duck-typed: importable and unit-testable without the livekit runtime (google.genai loads only in
compression_config(), livekit only when a running activity's chat context is set; bridge_rooms, whose person rule
the idle close shares, imports only the standard library and the config); nothing runs at import but its logger
setup.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Coroutine, Iterable
from datetime import date, datetime
from typing import Any

from services.utils import jarvis_jobs, jarvis_location
from services.utils.jarvis_logger import setup_logger
from src.core import bridge_rooms  # its is_person: the idle close counts the people its on_demand demand counts

logger = setup_logger("JARVIS-SESSION-POLICY")

# ------------------------------------------------------------------ context window compression (K-69)
CTX_TRIGGER_ENV = "JARVIS_CTX_TRIGGER_TOKENS"
CTX_TRIGGER_DEFAULT = 24000
CTX_TARGET_TOKENS = 16000
# A trigger outside these bounds is clamped: below, the window would be cut on nearly every turn; above, it would
# not fit the native-audio model's 128k context.
CTX_TRIGGER_MIN = 4000
CTX_TRIGGER_MAX = 120000


def ctx_trigger_tokens() -> int:
    """JARVIS_CTX_TRIGGER_TOKENS within [CTX_TRIGGER_MIN, CTX_TRIGGER_MAX]; empty or malformed is the default. A
    warning names the variable, never its value (K-69)."""
    raw = (os.getenv(CTX_TRIGGER_ENV) or "").strip()
    if not raw:
        return CTX_TRIGGER_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not a whole number; using %d", CTX_TRIGGER_ENV, CTX_TRIGGER_DEFAULT)
        return CTX_TRIGGER_DEFAULT
    clamped = min(max(value, CTX_TRIGGER_MIN), CTX_TRIGGER_MAX)
    if clamped != value:
        logger.warning("%s is outside %d..%d; using %d", CTX_TRIGGER_ENV, CTX_TRIGGER_MIN, CTX_TRIGGER_MAX,
                       clamped)
    return clamped


def compression_config() -> Any:
    """google.genai ContextWindowCompressionConfig for the job's RealtimeModel: compression at the trigger, a sliding
    window down to CTX_TARGET_TOKENS (two thirds of a lower trigger, so the target always stays below it)."""
    from google.genai import types  # pylint: disable=import-outside-toplevel  # loaded with the google plugin anyway

    trigger = ctx_trigger_tokens()
    target = min(CTX_TARGET_TOKENS, trigger * 2 // 3)
    logger.info("SESSION_POLICY context compression on (sliding window below the trigger)")
    return types.ContextWindowCompressionConfig(trigger_tokens=trigger,
                                                sliding_window=types.SlidingWindow(target_tokens=target))


# ------------------------------------------------------------------ quiet points
QUIET_AGENT_STATES = ("listening", "idle")


def session_busy(session: Any) -> bool:
    """True while the agent speaks or thinks (a reply or a tool call runs) or the user speaks: the quiet-point rule of
    agent_core's C-49 session update. No session (between C-33 rebuilds) is not busy."""
    if session is None:
        return False
    state = getattr(session, "agent_state", None)
    if isinstance(state, str) and state not in QUIET_AGENT_STATES:
        return True
    if getattr(session, "user_state", None) == "speaking":
        return True
    speech = getattr(session, "current_speech", None)
    done = getattr(speech, "done", None)
    return callable(done) and not done()


# ------------------------------------------------------------------ real truncation
TRUNCATE_EVERY_ITEMS = 20
TRUNCATE_MAX_ITEMS = 40
QUIET_POLL_S = 1.0

# livekit-plugins-google 1.4.6 (realtime_api.RealtimeSession.update_chat_ctx) logs this WARNING whenever an update
# removes items, i.e. on every truncation.
GOOGLE_PLUGIN_LOGGER = "livekit.plugins.google"
TRUNCATE_WARNING = "Gemini Live does not support removing messages"


class _TruncateWarningFilter(logging.Filter):
    """
    Drops exactly one record of the google plugin: its WARNING 'Gemini Live does not support removing messages'.
    Removing is what the truncation wants: it only shortens the plugin's local copy (what a reconnect replays), while
    the server's own context is bounded by compression_config(). Unfiltered, every truncation would put a WARNING in
    the error log, where the bug hunter turns it into a 'System error detected' alert on the dashboard. Every other
    record passes unchanged (gemini_resume_guard's stale-handle filter on the same logger still sees them); never
    raises.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return not (record.levelno == logging.WARNING and record.getMessage() == TRUNCATE_WARNING)
        except Exception:  # pylint: disable=broad-exception-caught
            return True


_filter_lock = threading.Lock()


def install_truncate_warning_filter() -> None:
    """Puts the truncation-warning filter on the google plugin's logger, once per process (jobs share it)."""
    with _filter_lock:
        plugin_logger = logging.getLogger(GOOGLE_PLUGIN_LOGGER)
        if not any(isinstance(f, _TruncateWarningFilter) for f in plugin_logger.filters):
            plugin_logger.addFilter(_TruncateWarningFilter())


def realtime_session(agent: Any) -> Any:
    """The agent's live realtime session (livekit-agents 1.4.6: agent._activity.realtime_llm_session), else None."""
    activity = getattr(agent, "_activity", None)
    return None if activity is None else getattr(activity, "realtime_llm_session", None)


def realtime_lock_busy(rt_session: Any) -> bool:
    """
    True while the pinned google plugin's session lock (_session_lock, an asyncio.Lock) is held or awaited: a reconnect
    closing the old Gemini session or sending the replayed history holds it across awaits, and every update_chat_ctx
    takes it. An update that has to wait for it was built from a copy read before it ran, so a cut queued behind a
    livekit update of that moment (a typed message, a tool output), or ahead of one, could be undone and its removed
    items sent to Gemini as new turns. When this is False, the lock is taken without yielding (asyncio.Lock.acquire's
    own condition: not locked and no waiter that is not cancelled) and update_chat_ctx runs to its end in one go, so a
    cut started then is never interleaved. False for a session without such a lock.
    """
    lock = getattr(rt_session, "_session_lock", None)
    if not isinstance(lock, asyncio.Lock):
        return False
    try:
        return lock.locked() or any(not waiter.cancelled() for waiter in (getattr(lock, "_waiters", None) or ()))
    except Exception:  # pylint: disable=broad-exception-caught
        return True  # unsure: the next poll looks again


def set_agent_chat_ctx(agent: Any, chat_ctx: Any) -> Any:
    """
    Sets the agent's own chat context the way livekit-agents 1.4.6 does, minus any realtime push: with no activity as
    Agent.update_chat_ctx (a copy keeping only the function items of the agent's tools); with one as
    AgentActivity.update_chat_ctx (a copy keeping only those of the activity's tools, then no instructions item while a
    realtime session runs, the current instructions otherwise). Returns the context it set. Synchronous on purpose:
    nothing can be appended to the agent's copy between the caller's read and this write.
    """
    activity = getattr(agent, "_activity", None)
    if activity is None:
        tools = getattr(agent, "_tools", None)
        ctx = chat_ctx.copy(tools=tools) if isinstance(tools, list) else chat_ctx
    else:
        # pylint: disable-next=import-outside-toplevel  # only with a running livekit activity
        from livekit.agents.voice.generation import remove_instructions, update_instructions

        tools = getattr(activity, "tools", None)
        ctx = chat_ctx.copy(tools=tools) if isinstance(tools, list) else chat_ctx.copy()
        if getattr(activity, "realtime_llm_session", None) is not None:
            remove_instructions(ctx)
        else:
            instructions = getattr(agent, "instructions", None)
            if instructions is not None:
                update_instructions(ctx, instructions=instructions, add_if_missing=True)
    agent._chat_ctx = ctx  # pylint: disable=protected-access  # livekit's own field, as its update_chat_ctx sets it
    return ctx


class ChatTruncator:
    """
    Cuts the job's chat context to its last max_items, once every `every` conversation items. note_item() counts
    (agent_runner's conversation_item_added handler, every session of the job); at the count a task waits for a quiet
    point (session_busy: no reply or tool call runs, so no turn is still being added to either copy; and
    realtime_lock_busy() is False) and runs truncate(). One task at a time, and truncate() itself holds a lock, so
    two cuts never overlap.

    Two copies are cut, each to its own last max_items: the live realtime session's (what Gemini was sent, what the
    plugin replays on a reconnect), remove-only, and the agent's own (what a C-33 rebuild hands its new session). In
    each, the re-seeded block (reseed_chat_ctx: the '[MEMORY]' note, then the replayed turns) stays whole or goes whole
    (keep_reseed_block_whole).
    """

    def __init__(self, agent: Any, *, every: int = TRUNCATE_EVERY_ITEMS, max_items: int = TRUNCATE_MAX_ITEMS,
                 busy: Callable[[], bool] | None = None,
                 spawn: Callable[[Coroutine[Any, Any, Any]], Any] | None = None,
                 quiet_poll_s: float = QUIET_POLL_S, sleep: Callable[[float], Any] | None = None) -> None:
        self.agent = agent
        self.every = max(1, int(every))
        self.max_items = max(2, int(max_items))
        # default: the agent's live session (BrainAssistant.session is the one attach_session got)
        self.busy = busy or (lambda: session_busy(getattr(self.agent, "session", None)))
        self.spawn = spawn  # the job's task tracker (agent_runner's track); else asyncio.ensure_future
        self.quiet_poll_s = quiet_poll_s
        self._sleep = sleep
        self._count = 0
        self._task: Any = None
        self._lock = asyncio.Lock()
        self.truncations = 0

    @property
    def running(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    def note_item(self) -> None:
        """One conversation item; every `every` items a cut is scheduled (later, while one still runs). Never raises."""
        self._count += 1
        if self._count < self.every or self.running:
            return
        self._count = 0
        coro = self._truncate_when_quiet()
        try:
            self._task = (self.spawn or asyncio.ensure_future)(coro)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # no running loop
            coro.close()
            logger.debug("SESSION_POLICY truncation not scheduled: %s", type(exc).__name__)

    def _is_busy(self) -> bool:
        try:
            return bool(self.busy())
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def _truncate_when_quiet(self) -> None:
        sleep = self._sleep or asyncio.sleep
        while self._is_busy() or realtime_lock_busy(realtime_session(self.agent)):
            await sleep(self.quiet_poll_s)
        await self.truncate()

    async def truncate(self) -> bool:
        """
        One cut now (livekit-agents and livekit-plugins-google 1.4.6), remove-only on the realtime side:
        1. the live realtime session's own context (activity.realtime_llm_session.chat_ctx) is copied, cut to its last
           max_items (ChatContext.truncate: a leading function call or output goes too; when that took the re-seeded
           '[MEMORY]' note, the rest of the re-seeded block goes too, keep_reseed_block_whole) and handed back to that
           session's update_chat_ctx. The plugin diffs it against its own copy, so the diff holds removals only: its
           local copy (what a reconnect replays) gets shorter and nothing is sent to Gemini, whose server-side context
           compression_config() bounds. Skipped while realtime_lock_busy() (the next cut takes it);
        2. then, with no await between the read and the write, the agent's own copy is cut the same way to its own
           last max_items (the re-seeded block whole or not at all) and set directly (set_agent_chat_ctx), never
           pushed.
        Never agent.update_chat_ctx(cut copy): the plugin would diff ITS copy against the agent's (an LCS of the ids)
        and send every item the agent holds that it lacks, or holds in another order, to Gemini again as a new client
        turn at the end of its live context: a clipboard tip (agent_runner appends it to the agent's copy only), or
        after a barge-in the old question (livekit appends the interrupted reply first and the user's transcript only
        at the plugin's turn_complete, so the agent holds [reply, question] where the plugin holds [question, reply]).
        True when a copy was cut; False when there was nothing to cut (or no copy could be); a failure is logged, never
        raised.
        """
        async with self._lock:
            realtime = await self._cut_realtime_copy()
            own = self._cut_agent_copy()
        if realtime is None and own is None:
            return False
        self.truncations += 1
        if own is None:
            logger.info("SESSION_POLICY chat context cut: realtime %d -> %d items (remove only)", *realtime)
        elif realtime is None:
            logger.info("SESSION_POLICY chat context cut %d -> %d items", *own)
        else:
            logger.info("SESSION_POLICY chat context cut %d -> %d items (realtime %d -> %d, remove only)",
                        *own, *realtime)
        return True

    async def _cut_realtime_copy(self) -> tuple[int, int] | None:
        """Step 1 of truncate(): (items before, after) of the realtime session's copy, None when nothing was cut."""
        rt_session = realtime_session(self.agent)
        if rt_session is None:
            return None
        try:
            ctx = rt_session.chat_ctx.copy()  # the plugin's property returns a copy already; this keeps it ours
            before = len(ctx.items)
            if before <= self.max_items:
                return None
            if realtime_lock_busy(rt_session):  # a direct call (prune_chat_context): the next cut takes it
                logger.debug("SESSION_POLICY realtime copy not cut: its session lock is busy (a reconnect)")
                return None
            ctx.truncate(max_items=self.max_items)
            dropped = keep_reseed_block_whole(ctx)  # more removals only: still nothing for Gemini
            after = len(ctx.items)
            await rt_session.update_chat_ctx(ctx)  # its lock is free: the plugin's update runs to its end in one go
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("SESSION_POLICY chat truncation failed (realtime copy: %s)", type(exc).__name__)
            return None
        _log_block_cut("realtime", dropped)
        return before, after

    def _cut_agent_copy(self) -> tuple[int, int] | None:
        """Step 2 of truncate(): (items before, after) of the agent's own copy, None when nothing was cut."""
        try:
            chat_ctx = getattr(self.agent, "chat_ctx", None)
            if not callable(getattr(chat_ctx, "copy", None)):
                return None
            ctx = chat_ctx.copy()
            before = len(ctx.items)
            if before <= self.max_items:
                return None
            ctx.truncate(max_items=self.max_items)
            dropped = keep_reseed_block_whole(ctx)
            after = len(set_agent_chat_ctx(self.agent, ctx).items)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("SESSION_POLICY chat truncation failed (agent copy: %s)", type(exc).__name__)
            return None
        _log_block_cut("agent", dropped)
        return before, after


def _log_block_cut(which: str, dropped: int) -> None:
    """A re-seeded block that a cut took (keep_reseed_block_whole), logged once its copy is in place: a count, never a
    text."""
    if dropped:
        logger.info("SESSION_POLICY re-seeded turns cut with their memory note (%s copy: %d items)", which, dropped)


# ------------------------------------------------------------------ re-seed at job start
RESEED_TURNS = 20
RESEED_ID_PREFIX = "reseed-"
RESEED_READ = 80           # memory messages read to find RESEED_TURNS turns after the filter
# C-114's bounds, which C-52 keeps: nothing is replayed when the newest saved turn is older than RESEED_MAX_AGE_S, and
# only what was saved in the RESEED_WINDOW_S before that newest turn comes back.
RESEED_MAX_AGE_S = 3600.0
RESEED_WINDOW_S = 1800.0
# Off since 20 Oct 2026: live, a job whose context got replayed turns (sent to Gemini as client content at connect)
# never answered a typed message (generate_reply timed out waiting for generation_created, the C-50 rescue too),
# while a probe job without them answered at once. The recall comes back through the instructions (C-53).
RESEED_ENABLED = False
RESEED_CLOCK_SLACK_S = 120.0   # a save time this far ahead of the clock still counts
RESEED_TIMEOUT_S = 5.0     # the memory read (a decrypt in a thread) never holds the session start longer
RESEED_TEXT_MAX = 1500     # characters per replayed turn
RESEED_STEP_S = 0.001      # created_at spacing: the replayed turns sort before anything already in the context
MEMORY_TAG = "[MEMORY]"    # the data tag of the note before the replayed turns
RESEED_NOTE = MEMORY_TAG + " Pichli baat-cheet ka hissa (taqreeban {minutes} minute purana), sirf context ke liye."
# Texts that were never a real turn: injected context, proactive and bug-hunter lines, laptop data, a re-seed note, and
# (C-52, whose extractor saves the router's turn log) the router's '[offline] ' template answer of a typed message
# that came while no session ran: saved as a chat message, never replayed as the model's own turn.
OFFLINE_MARK = "[offline]"  # conversation_router.OFFLINE_PREFIX without its trailing space
RESEED_SKIP_PREFIXES = ("[JARVIS CONTEXT]", "[SYSTEM", "[LOG DATA", "System error detected", "[DESKTOP DATA",
                        MEMORY_TAG, OFFLINE_MARK)
TYPED_MARK = "[typed]"     # conversation_router.TYPED_PREFIX without its trailing space
REPLAY_ROLES = ("user", "assistant")
PERSONAS = ("jarvis", "anna")  # C-52: the persona MemoryExtractor records on every saved reply ('unknown' otherwise)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part if isinstance(part, str) else part.get("text") if isinstance(part, dict) else None
                 for part in content]
        return " ".join(part for part in parts if isinstance(part, str))
    return ""


def replay_text(role: Any, content: Any) -> str | None:
    """The text a remembered message is replayed with, or None when it is not a user/assistant turn worth replaying
    (system or tool items, empty texts, injected context). The '[typed] ' mark of typed chat is stripped."""
    if role not in REPLAY_ROLES:
        return None
    text = _content_text(content).strip()
    if text.startswith(TYPED_MARK):
        text = text[len(TYPED_MARK):].strip()
    if not text or text.startswith(RESEED_SKIP_PREFIXES):
        return None
    if len(text) > RESEED_TEXT_MAX:
        text = text[:RESEED_TEXT_MAX].rstrip() + "..."
    return text


def _saved_at(conversation: dict[str, Any]) -> float | None:
    """When ConversationMemory saved a conversation: its 'timestamp' (ISO; without a zone it is local time, as
    MemoryExtractor writes it) as epoch seconds, or None when it cannot be read."""
    stamp = conversation.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _has_turn(messages: list[Any]) -> bool:
    return any(isinstance(message, dict) and replay_text(message.get("role"), message.get("content")) is not None
               for message in messages)


def recent_messages(conversations: Iterable[Any], *, read: int = RESEED_READ, max_age_s: float = RESEED_MAX_AGE_S,
                    window_s: float = RESEED_WINDOW_S, now: float | None = None) -> tuple[list[Any], float]:
    """
    What a new job may replay from the ConversationMemory conversations ({messages, timestamp} dicts, saved oldest
    first): the messages saved within window_s before the newest saved turn (a message replay_text keeps: an injected
    line saved later never dates the conversation), at most `read` of them, oldest first, and how many seconds ago
    that newest turn was saved. ([], 0.0) when it was saved more than max_age_s ago or there is none. A conversation
    without a readable save time, or one saved ahead of the clock, is skipped; the scan stops at the first one older
    than the window (everything before it is older still).
    """
    now = time.time() if now is None else now
    picked: list[Any] = []  # newest first
    newest: float | None = None
    for conversation in reversed(list(conversations or [])):
        if len(picked) >= read:
            break
        if not isinstance(conversation, dict):
            continue
        saved = _saved_at(conversation)
        messages = conversation.get("messages")
        if saved is None or now - saved < -RESEED_CLOCK_SLACK_S or not isinstance(messages, list):
            continue
        if newest is None:
            if not _has_turn(messages):
                continue
            if now - saved > max_age_s:
                return [], 0.0  # the conversation ended too long ago to replay as the latest exchange
            newest = saved
        elif newest - saved > window_s:
            break
        picked.extend(reversed(messages))
    if newest is None:
        return [], 0.0
    return list(reversed(picked[:max(0, read)])), max(0.0, now - newest)


def _note_minutes(age_s: float) -> int:
    return max(1, round(age_s / 60.0))


def reseed_note(age_s: float) -> str:
    """The user item before the replayed turns: they come from memory, about this many minutes old (at least 1)."""
    return RESEED_NOTE.format(minutes=_note_minutes(age_s))


def known_persona(value: Any) -> str | None:
    """'jarvis' or 'anna' (any case), else None (unknown)."""
    if isinstance(value, str) and value.strip().lower() in PERSONAS:
        return value.strip().lower()
    return None


def other_persona(message: Any, persona: str | None) -> bool:
    """C-52: True for a saved reply of the other persona than `persona` (the one the job starts as). A reply saved
    without a persona (before C-52) is unknown and never counts as the other; without a job persona nothing does."""
    if persona is None or not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    saved = known_persona(message.get("persona"))
    return saved is not None and saved != persona


def reseed_turns(messages: Iterable[Any], limit: int = RESEED_TURNS,
                 persona: str | None = None) -> list[tuple[str, str]]:
    """(role, text) of the last `limit` replayable turns of ConversationMemory messages ({role, content} dicts), oldest
    first; a turn repeated back to back counts once, and user turns at the end with no reply after them are dropped
    (the model would take an old request for one still waiting and could act on it). C-52: with `persona` ('jarvis' or
    'anna': the persona the job starts as), a reply of the other persona stays out together with the user turns it
    answered (those since the reply before it), so the job never takes the other persona's words for its own; a reply
    without a persona (saved before C-52) is unknown and kept."""
    persona = known_persona(persona)
    turns: list[tuple[str, str]] = []
    asked: list[tuple[str, str]] = []  # user turns since the last reply
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        text = replay_text(role, message.get("content"))
        if text is None:
            continue
        last = asked[-1] if asked else (turns[-1] if turns else None)
        if last == (role, text):
            continue
        if role == "user":
            asked.append((role, text))
            continue
        if other_persona(message, persona):
            asked = []  # answered by the other persona: the whole exchange stays out
            continue
        turns.extend(asked)
        asked = []
        turns.append((role, text))
    return turns[-limit:] if limit > 0 else []


def is_reseeded(item: Any) -> bool:
    """True for a chat item that reseed_chat_ctx put in (its id starts with 'reseed-')."""
    item_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
    return isinstance(item_id, str) and item_id.startswith(RESEED_ID_PREFIX)


def without_reseeded(items: Iterable[Any]) -> list[Any]:
    """The items without the re-seeded ones: they came from memory, so the extractor never saves them again."""
    return [item for item in items if not is_reseeded(item)]


def is_reseed_note(item: Any) -> bool:
    """True for the '[MEMORY]' note reseed_chat_ctx puts before the replayed turns: the one re-seeded item whose text
    starts with the tag (replay_text never replays such a text)."""
    if not is_reseeded(item):
        return False
    content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
    return _content_text(content).lstrip().startswith(MEMORY_TAG)


def keep_reseed_block_whole(ctx: Any) -> int:
    """
    The re-seeded block is one unit in a cut chat context (ChatTruncator, both copies): when the cut took its
    '[MEMORY]' note (put in first, so the first cut that reaches the block takes it), every 'reseed-*' item still
    there goes too. A replayed turn left without the note would reach Gemini at the next fresh reconnect (a C-51 stop,
    a persona, voice or wake-word switch, the midnight refresh) or C-33 rebuild as the plain start of this
    conversation, nothing saying it is up to about 90 minutes old. Removals only, in place, the order kept (the
    realtime copy's diff stays remove-only); a block with its note, or no block, is left alone. Returns how many items
    it removed.
    """
    items = ctx.items
    reseeded = [item for item in items if is_reseeded(item)]
    if not reseeded or any(is_reseed_note(item) for item in reseeded):
        return 0
    items[:] = [item for item in items if not is_reseeded(item)]
    return len(reseeded)


async def reseed_chat_ctx(agent: Any, *, persona: str | None = None, limit: int = RESEED_TURNS,
                          read: int = RESEED_READ, max_age_s: float = RESEED_MAX_AGE_S,
                          window_s: float = RESEED_WINDOW_S, timeout_s: float = RESEED_TIMEOUT_S) -> int:
    """
    Before the job's first session.start (no activity runs, so update_chat_ctx only sets the agent's context, which the
    session then hands to Gemini as its first client content): the last `limit` user/assistant turns that the agent's
    ConversationMemory (memory_extractor.memory.load_memory: the conversations with the time each was saved) saved in
    the window_s before its newest saved turn, and only when that turn is at most max_age_s old (recent_messages), as
    user/assistant messages after one user note (reseed_note: '[MEMORY] ... taqreeban N minute purana ...', N the
    newest turn's age), all with ids 'reseed-<hex>', before anything already in the context. In the google turn format
    the note joins the first replayed user turn as one more part (or stands before a replayed model turn).
    C-52: persona is the persona the job starts as (agent_runner: the saved one, C-54; default: the agent's active
    persona); a saved reply of the other persona stays out with the user turns it answered (reseed_turns), and a
    reply saved before C-52 (no persona) is unknown and replayed. The bounds stay C-114's (RESEED_MAX_AGE_S,
    RESEED_WINDOW_S). The note tells the model they are old context, never the latest exchange.
    A context that has them already (a C-33 rebuild, a retried start) is left alone. Logs counts, never a text.
    Returns how many turns went in (the note not counted); never raises.
    """
    if not RESEED_ENABLED:
        logger.info("SESSION_POLICY re-seed off (RESEED_ENABLED)")
        return 0
    turns: list[tuple[str, str]] = []
    age_s = 0.0
    try:
        if getattr(agent, "_activity", None) is not None:
            logger.info("SESSION_POLICY re-seed skipped: a session already runs")
            return 0
        chat_ctx = getattr(agent, "chat_ctx", None)
        update = getattr(agent, "update_chat_ctx", None)
        memory = getattr(getattr(agent, "memory_extractor", None), "memory", None)
        load_memory = getattr(memory, "load_memory", None)
        if not callable(load_memory) or not callable(update) or not callable(getattr(chat_ctx, "copy", None)):
            return 0
        if any(is_reseeded(item) for item in list(getattr(chat_ctx, "items", None) or [])):
            return 0
        job_persona = known_persona(persona) or known_persona(
            getattr(getattr(agent, "persona_manager", None), "active_persona_name", None))
        conversations = await asyncio.wait_for(load_memory(), timeout_s)
        recent, age_s = recent_messages(conversations if isinstance(conversations, list) else [], read=read,
                                        max_age_s=max_age_s, window_s=window_s)
        turns = reseed_turns(recent, limit, job_persona)
        left_out = sum(1 for message in recent if other_persona(message, job_persona))
        if left_out:
            logger.info("SESSION_POLICY re-seed left out %d repl%s of the other persona (the job starts as %s)",
                        left_out, "y" if left_out == 1 else "ies", job_persona)
        if not turns:
            logger.info("SESSION_POLICY re-seed: no turns saved in the last %d min to replay", int(max_age_s // 60))
            return 0
        ctx = chat_ctx.copy()
        now = time.time()
        first = min((getattr(item, "created_at", now) for item in ctx.items), default=now)
        start = first - (len(turns) + 2) * RESEED_STEP_S
        for index, (role, text) in enumerate([("user", reseed_note(age_s)), *turns]):
            ctx.add_message(role=role, content=text, id=f"{RESEED_ID_PREFIX}{uuid.uuid4().hex[:12]}",
                            created_at=start + index * RESEED_STEP_S)
        await update(ctx)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught  # the job starts without the replay
        logger.warning("SESSION_POLICY re-seed skipped (%s)", type(exc).__name__)
        return 0
    users = sum(1 for role, _text in turns if role == "user")
    logger.info("SESSION_POLICY re-seeded %d turns (%d user, %d assistant), about %d min old", len(turns), users,
                len(turns) - users, _note_minutes(age_s))
    return len(turns)


# ------------------------------------------------------------------ the per-job policy loop
POLICY_TICK_S = 60.0
IDLE_CLOSE_ENV = "JARVIS_IDLE_CLOSE_MIN"
IDLE_CLOSE_DEFAULT_MIN = 30.0  # the VPS brain's; agent_runner gives the PC brain 0 (its bridge refills an empty room)
RECYCLE_AGE_S = 24 * 3600.0   # a job this old ends at its first RECYCLE_IDLE_S without conversation, people or not
RECYCLE_IDLE_S = 10 * 60.0
END_IDLE = "idle"             # the job end reason (K-60) of both idle rules
SESSION_IDLE = "idle"         # jarvis.session (K-68) just before an idle close
CITY_READINGS = 5             # a city counts once this many readings in a row (60 s apart) agree on it
CITY_REFRESH_EVERY_S = 3600.0  # at most one city change an hour: each refresh is a fresh Gemini reconnect (C-49)


def idle_close_setting() -> float | None:
    """JARVIS_IDLE_CLOSE_MIN in minutes: None when it is empty (the brain's default applies), 0.0 for 0 or less (both
    idle rules off). A malformed value counts as empty; a warning names the variable, never its value (K-69)."""
    raw = (os.getenv(IDLE_CLOSE_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        logger.warning("%s is not a number; using the default", IDLE_CLOSE_ENV)
        return None
    return value if value > 0 else 0.0


def background_work(running_tools: Callable[[], Any] | None = None) -> bool:
    """True while this event loop has a background job queued or running (services.utils.jarvis_jobs, K-20: it lives
    in the job's loop only, so ending the job would cancel it and its result would never arrive) or a tool call of
    this process runs (running_tools: the plugin manager's, K-11)."""
    if jarvis_jobs.count_active() > 0:
        return True
    return bool(running_tools()) if callable(running_tools) else False


def date_part(formatted: Any) -> str:
    """'Thursday, November 13, 2025 - 07:25 PM' -> 'Thursday, November 13, 2025' (the time changes every minute)."""
    return formatted.split(" - ", 1)[0].strip() if isinstance(formatted, str) else ""


def calendar_date(formatted: Any) -> date | None:
    """The calendar day of a formatted date (get_formatted_datetime's, or BrainAssistant's '%Y-%m-%d' default)."""
    head = date_part(formatted)
    for fmt in ("%A, %B %d, %Y", "%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def date_changed(old: Any, new: Any) -> bool:
    """True when the day of `new` differs from the day of `old` (the date parts as text when either cannot be read)."""
    old_day, new_day = calendar_date(old), calendar_date(new)
    if old_day is not None and new_day is not None:
        return old_day != new_day
    return date_part(old) != date_part(new)


def _same_city(first: Any, second: Any) -> bool:
    def norm(value: Any) -> str:
        return value.strip().casefold() if isinstance(value, str) else ""
    return norm(first) == norm(second)


def _is_fallback_location(loc_data: dict[str, Any]) -> bool:
    """A failed lookup answers with USER_CITY and fixed coordinates (jarvis_search, jarvis_location): not a reading."""
    if loc_data.get("source") == jarvis_location.SOURCE_USER_CITY:
        return True
    return (loc_data.get("lat"), loc_data.get("lng")) == (jarvis_location.DEFAULT_LAT, jarvis_location.DEFAULT_LNG)


class SessionPolicy:
    """
    The per-job context and idle policy (C-114); agent_runner builds it with the job's parts and runs run() on the
    job's protector, never in a probe or canary job. Every tick_s (60 s):
    - idle close: nobody in the room (_present: no participant voice_link.linked_identity() names, and no remote
      participant the bridge counts as a person, bridge_rooms.is_person) and no conversation activity (note_activity:
      a message, a user transcription, an agent state change) for idle_close_s -> jarvis.session 'idle' and
      request_end('idle') once; somebody in the room or new activity starts the count again;
    - 24 h recycle: a job older than RECYCLE_AGE_S ends the same way at its first RECYCLE_IDLE_S (10 min) without
      conversation activity, people in the room or not;
      neither while the agent speaks or thinks, nor while background_busy() reports a tool call or a background job.
      idle_close_min (else JARVIS_IDLE_CLOSE_MIN) 0 turns both rules off; when that is empty the idle close takes
      idle_close_default_min (agent_runner: 30 on the VPS brain, 0 on the PC brain) and the recycle stays on;
    - context: the date part of now_formatted() and, with watch_city, the city of the last CITY_READINGS location
      readings (note_location: the PC's location loop feeds them, no second ipinfo call; read_location: the policy
      reads one itself each tick, the VPS brain's cached device location) against the assistant's
      current_date/current_city; a real change sets them and calls refresh_instructions() once. A new day refreshes
      at once; a new city needs CITY_READINGS readings in a row that agree on it and waits until the job's last city
      change is CITY_REFRESH_EVERY_S old (the first one comes at once; a new day takes it along).
    """

    def __init__(self, assistant: Any, *, voice_link: Any = None, session: Callable[[], Any] | None = None,
                 request_end: Callable[[str], Any] | None = None,
                 set_session_attr: Callable[[str], Any] | None = None,
                 now_formatted: Callable[[], Any] | None = None, watch_city: bool = False,
                 read_location: Callable[[], Any] | None = None, background_busy: Callable[[], Any] | None = None,
                 idle_close_min: float | None = None, idle_close_default_min: float = IDLE_CLOSE_DEFAULT_MIN,
                 tick_s: float = POLICY_TICK_S, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] | None = None) -> None:
        # No I/O here: the entrypoint builds it with the job's parts, before the first session starts.
        self.assistant = assistant
        self.voice_link = voice_link
        self._session = session or (lambda: None)
        self._request_end = request_end
        self._set_session_attr = set_session_attr
        self._now_formatted = now_formatted
        self.watch_city = watch_city
        self._read_location = read_location
        self._background_busy = background_busy
        setting = idle_close_setting() if idle_close_min is None else max(0.0, float(idle_close_min))
        # an explicit 0 turns both rules off; an empty setting takes the brain's default and keeps the 24 h recycle
        self.recycle = setting is None or setting > 0
        minutes = max(0.0, float(idle_close_default_min)) if setting is None else setting
        self.idle_close_s = minutes * 60.0
        self.tick_s = tick_s
        self._clock = clock
        self._sleep = sleep
        self.started_at = clock()
        self._last_activity = self.started_at
        self._idle_since: float | None = None
        self._city_readings: deque[str] = deque(maxlen=CITY_READINGS)
        self._city_changed_at: float | None = None  # the clock's time of this job's last city change
        self.ended = False
        self.refreshes = 0

    def note_activity(self) -> None:
        """One sign of use (agent_runner's session handlers: a conversation item, a user transcription, an agent state
        change): both idle rules count their quiet time from now."""
        self._last_activity = self._clock()

    # ---- location readings ------------------------------------------------------------------------------

    def note_location(self, loc_data: Any) -> None:
        """One location reading (get_current_city_data: the local location loop's every 60 s, or read_location's at
        each tick). Never raises."""
        try:
            if not isinstance(loc_data, dict) or _is_fallback_location(loc_data):
                return
            city = loc_data.get("city")
            if isinstance(city, str) and city.strip():
                self._city_readings.append(city.strip())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("SESSION_POLICY location reading ignored: %s", type(exc).__name__)

    def _agreed_city(self) -> str | None:
        readings = list(self._city_readings)
        if not self.watch_city or len(readings) < CITY_READINGS:
            return None
        return readings[-1] if all(_same_city(city, readings[-1]) for city in readings) else None

    def _city_moved(self, *, date_refresh: bool) -> bool:
        """
        Sets the assistant's current_city to a new city once the last CITY_READINGS readings agree on it and this job's
        last city change is CITY_REFRESH_EVERY_S old (the first one comes at once; with date_refresh, the refresh a
        new day makes anyway, it comes along at no extra reconnect). True when it did. A newest reading that is not
        the current city but has not held CITY_READINGS readings (a flap, or a move still settling) and a city change
        held back by the hourly bound are logged at DEBUG, never with the city.
        """
        readings = list(self._city_readings)
        current = getattr(self.assistant, "current_city", None)
        if not self.watch_city or not readings or _same_city(readings[-1], current):
            return False
        city = self._agreed_city()
        if city is None:
            run = next((index for index, reading in enumerate(reversed(readings))
                        if not _same_city(reading, readings[-1])), len(readings))
            logger.debug("SESSION_POLICY city flap skipped: %d of %d readings in a row agree", run, CITY_READINGS)
            return False
        now = self._clock()
        if self._city_changed_at is not None and not date_refresh:
            since = now - self._city_changed_at
            if since < CITY_REFRESH_EVERY_S:
                logger.debug("SESSION_POLICY city change held: the last one was %d min ago (one an hour at most)",
                             int(since // 60))
                return False
        self.assistant.current_city = city
        self._city_changed_at = now
        return True

    # ---- the checks ---------------------------------------------------------------------------------

    async def check_context(self) -> list[str]:
        """The date and city check; returns what changed ('date', 'city'). One refresh_instructions() per change."""
        assistant = self.assistant
        refresh = getattr(assistant, "refresh_instructions", None)
        if not callable(refresh):
            return []
        if self._read_location is not None:  # remote mode: the cached device location (USER_CITY is ignored)
            try:
                self.note_location(await self._read_location())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the date check still runs
                logger.debug("SESSION_POLICY location read failed: %s", type(exc).__name__)
        changed: list[str] = []
        if self._now_formatted is not None:
            now = await self._now_formatted()
            formatted = now.get("formatted") if isinstance(now, dict) else None
            if isinstance(formatted, str) and formatted.strip() and date_changed(
                    getattr(assistant, "current_date", None), formatted):
                assistant.current_date = formatted
                changed.append("date")
        if self._city_moved(date_refresh=bool(changed)):
            changed.append("city")
        if changed:
            logger.info("SESSION_POLICY context changed %s", "+".join(changed))
            self.refreshes += 1
            await refresh()
        return changed

    def _present(self) -> bool:
        """
        Somebody is in the room: the participant the voice link linked, or any remote participant of its room the
        bridge counts as a person (bridge_rooms.is_person, the rule of its on_demand demand: kind STANDARD, identity
        not 'agent-*' or 'probe-*'). The link alone would not do: on the VPS brain (strict_owner) it links only
        'owner-*', so a non-owner person left in the room (an older client, a CLI or test participant) would end the
        job every 30 quiet minutes and the bridge, still seeing demand, would dispatch a fresh one (a new re-seed and
        Gemini session) at once. True without a voice link to judge by (never an idle close) and on an error (the next
        tick looks again).
        """
        linked_identity = getattr(self.voice_link, "linked_identity", None)
        if not callable(linked_identity):
            return True  # nothing to judge by: never an idle close
        try:
            if linked_identity() is not None:  # pylint: disable=not-callable  # checked just above
                return True
            remote = getattr(getattr(self.voice_link, "room", None), "remote_participants", None) or {}
            return any(bridge_rooms.is_person(participant) for participant in list(remote.values()))
        except Exception:  # pylint: disable=broad-exception-caught
            return True

    def _busy(self) -> bool:
        """The agent speaks or thinks (session_busy), or background_busy() reports a tool call or a background job; an
        error counts as busy (the next tick looks again)."""
        try:
            if session_busy(self._session()):
                return True
            return self._background_busy is not None and bool(self._background_busy())
        except Exception:  # pylint: disable=broad-exception-caught
            return True

    def check_idle(self, now: float | None = None) -> bool:
        """The idle rules at `now` (the clock's time by default); True when this call ended the job."""
        if self.ended or (self.idle_close_s <= 0 and not self.recycle):
            return False
        now = self._clock() if now is None else now
        present = self._present()
        if present:
            self._idle_since = None
            idle_s = 0.0
        else:
            if self._idle_since is None:
                self._idle_since = now
            idle_s = now - max(self._idle_since, self._last_activity)  # nobody in the room AND quiet
        quiet_s = now - self._last_activity
        idle_close = self.idle_close_s > 0 and not present and idle_s >= self.idle_close_s
        recycle = self.recycle and now - self.started_at >= RECYCLE_AGE_S and quiet_s >= RECYCLE_IDLE_S
        if not (idle_close or recycle):
            return False
        if self._busy():
            return False  # never while the agent speaks or thinks or work runs: the next tick looks again
        self.ended = True
        if idle_close:
            logger.info("SESSION_POLICY idle close after %d min", int(idle_s // 60))
        else:
            logger.info("SESSION_POLICY idle close after %d min (the job is older than 24 h)", int(quiet_s // 60))
        for hook, value in ((self._set_session_attr, SESSION_IDLE), (self._request_end, END_IDLE)):
            if hook is None:
                continue
            try:
                hook(value)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("SESSION_POLICY idle close step failed (%s)", type(exc).__name__)
        return True

    async def tick(self) -> None:
        """One pass: the idle rules first (a job about to end needs no new instructions), then the context check."""
        try:
            if self.check_idle():
                return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("SESSION_POLICY idle check failed (%s)", type(exc).__name__)
        try:
            await self.check_context()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("SESSION_POLICY context check failed (%s)", type(exc).__name__)

    async def run(self) -> None:
        """Every tick_s until the job ends (its protector cancels it) or this policy ended the job."""
        sleep = self._sleep or asyncio.sleep
        while not self.ended:
            await sleep(self.tick_s)
            await self.tick()
