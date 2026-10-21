"""
# conversation_router.py
C-42 (K-59 CT-VC-CHAT-INPUT, K-58 CT-VC-MESSAGE): typed chat goes into the live Gemini session exactly once.

Typed text arrives from the UI (Socket.IO ui_command 'chat', origin 'ui'), from LiveKit 'lk.chat' (origin
'lk.chat') or from BrainAssistant.handle_user_query (origin 'query'). ConversationRouter.handle_text():
  - id: the client's id, else a new uuid4; ids already seen (LRU 512) are dropped as duplicates;
  - text: NFKC, control characters removed (newline and tab kept), at most 4000 characters;
  - VortexGuard.detect_threat only (no sanitize_input): a threat gets the user message, a system message
    'Security guard ne request block ki' and an audit line, and never reaches the model;
  - accepted: the user message is emitted once (source 'text', the same id); while the session runs,
    session.interrupt() and then session.generate_reply(user_input='[typed] ' + text), so the same brain
    (tools, memory, persona) answers; with no session '[offline] ' + the template answer as source 'system'.

on_conversation_item_added() is the single source of transcript messages from the session: a user item that
is a pending typed message is not emitted again; any other user item is a voice message (id = item.id); an
assistant item is emitted with id = item.id, turn_id = the user message it answers and that message's source.

C-43: every accepted typed or voice message also gets the Reasoning panel's event (K-67, jarvis_reasoning in the
background, 2 s cap, never the reply), and '/swarm <task>' runs the registered 'swarm' command once instead of a
reply, with no message from the router (the swarm module is the single emitter).

Every message goes out through one ordered chain (each send waits for the one before it, at most
EMIT_ORDER_WAIT_S): the typed user echo is sent beside the reply instead of before it, so its bridge round trip
never delays generate_reply, and the reply still reaches the UI after the echo.

C-119: add_typed_hook(hook) lets a module see typed text before the model (the stop-desktop phrases, matched in the
agent like 'ruko', K-76). A hook that returns True has handled the message itself: it is shown once and never sent to
the model ('handled'). A hook may instead return a follow-up (a zero-argument callable, typically saying the hook's
answer through the announcer): the message is handled the same way, and the follow-up runs only once Sir's own
message went out, so the chat never shows Jarvis's answer above the message it answers. Such a message never arms
the C-50 rescue, and it ends the one an older typed message still had armed, as the dashboard Stop does (C-51):
today it is a stop, and what it stopped is never asked of the model again. A typed message sent while the hooks run
keeps its own rescue.

C-52: turn_log (the last TURN_LOG_MAX messages of both sides, each with its message id) is what the job's memory
extractor saves; every assistant entry records the persona that answered (reply_persona).

C-50 escalation (live 20 Oct: Gemini Live sometimes never starts a reply to a typed turn, 'RealtimeError:
generate_reply timed out waiting for generation_created event', and the rescue's own generate_reply times out the same
way; a session idle ~55 minutes answered no typed message until a fresh session): when the rescue's reply ends without
any reply (its speech handle finished with no chat item and not interrupted, or generate_reply raised anything but the
closing session's RuntimeError) and the message still waits, the router restarts the realtime session fresh once
(gemini_resume_guard.restart_fresh 'typed_rescue', as C-51's stop does) at a safe point (no reply, tool call or user
speech; session_policy.session_busy, the plugin's session lock free), waits for the new connection and sends the typed
message once more as a new user turn. At most one escalation per message, at most one router restart per
FRESH_RESTART_GAP_S for the whole job, checked when the escalation starts and again right before its restart (a router
restart that came while it waited for the safe point is never repeated: that restart's new connection is awaited
instead); a reply that arrives meanwhile, C-51's stop (cancel_rescues), a newer spoken turn, a newer typed message (as
soon as it waits to be sent, _queued, so the escalation never restarts or sends again under it) and a C-119 handled
message end it with nothing more sent. If even that fails, one short assistant note (source 'system', turn_id = the
typed id, K-58) says the reply could not be made, but only after NOTE_GRACE_S in which the message still waited and the
session never looked busy: livekit-agents 1.4.6 still plays a generation that arrives after generate_reply's 5 s
timeout, as a server-initiated speech whose reply carries turn_id = the typed id, so a reply or speech that comes
meanwhile ends the escalation silently. A typed message after IDLE_FRESH_AFTER_S without any session activity (no
spoken user item or reply from the server, no attach, no restart; the typed user items livekit adds locally do not
count) restarts fresh first, at a safe point and inside the same rate limit. Typed messages reach the session one at a
time, in the order they came (one send lock per router around the idle restart, the interrupt and generate_reply), so a
message typed during that restart's wait is sent after the earlier one instead of overtaking it, and the newest is the
one watched. A message is registered as waiting (_queued) before any of those waits: C-51's stop (cancel_rescues,
counted) and a C-119 handled message that come meanwhile end it before it is sent ('stopped': shown, never sent to the
model). A spoken user item does not (in the pinned google plugin it comes only once that spoken turn's generation
completes, so one that arrives during these short waits is the late transcript of an earlier turn), a reply to
something else does not either, and a session that went away meanwhile gives the offline answer. Logs carry ids,
counts and reasons only, never the text (K-29).
"""
# pylint: disable=too-many-lines  # one router: typed input, the transcript, the rescue and its escalation

from __future__ import annotations

import asyncio
import functools
import inspect
import re
import time
import unicodedata
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from services.utils import jarvis_bridge
from services.utils.jarvis_audit import jarvis_audit
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard
from services.utils.ui_command_registry import CommandContext
from services.utils.ui_command_registry import dispatch as dispatch_ui_command
from src.core import gemini_resume_guard, session_policy
from src.core.bridge_notifier import bridge_notifier

logger = setup_logger("CHAT-ROUTER")

TYPED_PREFIX = "[typed] "
OFFLINE_PREFIX = "[offline] "
BLOCKED_TEXT = "Security guard ne request block ki"
OFFLINE_FALLBACK_TEXT = "Sir, abhi live session connected nahi hai. Thori dair baad dobara try karein."

ORIGIN_UI = "ui"
ORIGIN_LK_CHAT = "lk.chat"
ORIGIN_QUERY = "query"

MAX_CHARS = 4000
DEDUPE_MAX = 512
ID_MAX_CHARS = 128
PENDING_MATCH_S = 10.0   # a user item without the '[typed] ' prefix matches a typed message this long
PENDING_KEEP_S = 120.0   # a prefixed item still finds its typed id this long (the reply may wait for silence)
TURN_LOG_MAX = 200
INTERRUPT_WAIT_S = 2.0   # the interrupt waits for playout; never let a stuck output hold a typed reply
OFFLINE_TIMEOUT_S = 10.0
REASONING_TIMEOUT_S = 2.0  # K-67: the panel's reasoning never takes longer (and never produces the reply)
REASONING_HISTORY = 10  # earlier turns jarvis_reasoning sees
SWARM_COMMAND = "swarm"
EMIT_ORDER_WAIT_S = 5.0  # a send waits this long at most for the one before it (a stuck bridge never stalls all)
PERSONAS = ("jarvis", "anna")  # C-52: an assistant entry of the turn log records which one answered
# C-50: a typed message that got no reply at all is asked again once (_rescue)
RESCUE_AFTER_S = 12.0
RESCUE_POLL_S = 5.0      # while the agent thinks or speaks the rescue looks again this often
RESCUE_GIVE_UP_S = 45.0  # and gives up after this long: a long tool call is never cut into (the UI offers a retry)
# C-50 escalation: the rescue's (and the re-send's) reply is watched this long for a reply that never started (the
# plugin itself gives up after 5 s without generation_created; a handle still running then is a reply under way)
RESCUE_WATCH_S = 30.0
WATCH_POLL_S = 0.25
FRESH_RESTART_GAP_S = 60.0   # at most one fresh restart by the router per this long, for the whole job
SAFE_POINT_WAIT_S = 15.0     # the escalation waits this long at most for a safe point, then gives up ('busy')
RECONNECT_WAIT_S = 8.0       # and this long at most for the fresh connection (then it sends anyway)
NOTE_GRACE_S = 12.0          # a failed escalation's note first waits this long for a late reply (_note_owed)
IDLE_FRESH_AFTER_S = 1800.0  # a typed message after this long without session activity restarts fresh first
RESCUE_FAILED_TEXT = "Sir, maazrat, is message ka jawab nahi ban saka. Meherbani karke dobara bhej dein."
# K-59 v50: the note's own marker (a K-58 extra field the bridge keeps), so the dashboard styles only this note as a
# system line; the other source 'system' messages (offline answers, self-repair diffs, announcements) stay replies.
RESCUE_NOTE_KIND = "typed_no_reply"
PREVIEW_CHARS = 60

# Results of the escalation's waits (_wait_until): a reason that reaches the log, never a text
_OK = "ok"
_ENDED = "ended"        # the typed message no longer waits: a reply came, a stop, a newer turn, a handled message
_SESSION = "session"    # the session changed (a C-33 rebuild)
_FAILED = "failed"      # the reply's speech handle ended without any reply
_UNKNOWN = "unknown"    # a handle that cannot tell, an interrupted one, or a reply still under way
_TIMEOUT = "timeout"
_RUNNING = "running"
_BUSY = "busy"          # a reply, a tool call or user speech is under way (no safe point, no note)

# Control characters (C0, DEL, C1) except tab and newline; CR is folded into newline first.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")
_SWARM_PREFIX = re.compile(r"^/swarm(?:\s+|$)", re.IGNORECASE)

NotifyFn = Callable[..., Awaitable[Any]]
ReasonFn = Callable[[str, list[dict[str, Any]]], Awaitable[Any]]
DispatchFn = Callable[[str, Any], Awaitable[Any]]
SpawnFn = Callable[[Coroutine[Any, Any, Any]], "asyncio.Task[Any]"]
# C-119: hook(text) -> False, True (handled, not for the model) or a follow-up callable (handled; it runs once Sir's
# message went out), or an awaitable of one of these
TypedHook = Callable[[str], Any]


def normalize_text(text: Any) -> str:
    """K-59: NFKC, CR/CRLF folded into newline, every other control character except newline and tab removed,
    stripped and capped at MAX_CHARS. A non-string gives ''."""
    if not isinstance(text, str):
        return ""
    clean = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    clean = _CONTROL_CHARS.sub("", clean).strip()
    return clean[:MAX_CHARS].rstrip()


def _match_key(text: str) -> str:
    """What a typed message and the session's user item are compared by: NFKC, whitespace collapsed."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def strip_typed_prefix(text: str) -> tuple[bool, str]:
    """(True, body) for a session user item that the router sent as typed text, else (False, text)."""
    stripped = text.lstrip()
    if stripped.startswith(TYPED_PREFIX.rstrip()):
        return True, stripped[len(TYPED_PREFIX.rstrip()):].lstrip()
    return False, text


def preview(text: str) -> str:
    """At most PREVIEW_CHARS characters on one line, for an INFO log line (the full text goes to DEBUG)."""
    flat = _WHITESPACE.sub(" ", text).strip()
    return flat if len(flat) <= PREVIEW_CHARS else flat[:PREVIEW_CHARS - 3] + "..."


def swarm_task_of(text: str) -> str | None:
    """The task of a typed '/swarm <task>' ('' for a bare '/swarm'), else None."""
    match = _SWARM_PREFIX.match(text)
    return text[match.end():].strip() if match else None


async def _advanced_reasoning(text: str, history: list[dict[str, Any]]) -> Any:
    from services.ai_core.jarvis_reasoning import (  # pylint: disable=import-outside-toplevel
        process_with_advanced_reasoning,
    )
    return await process_with_advanced_reasoning(text, history)


def valid_msg_id(msg_id: Any) -> str | None:
    """A client id the router keeps: a str of 1-128 printable characters, else None."""
    if isinstance(msg_id, str) and 0 < len(msg_id) <= ID_MAX_CHARS and msg_id.isprintable():
        return msg_id
    return None


def reply_persona(assistant: Any) -> str | None:
    """C-52: the persona a reply of this assistant comes from: its reply_persona (the persona its live session answers
    as), else its active persona; None when neither is 'jarvis' or 'anna'."""
    for value in (getattr(assistant, "reply_persona", None),
                  getattr(getattr(assistant, "persona_manager", None), "active_persona_name", None)):
        if isinstance(value, str) and value in PERSONAS:
            return value
    return None


def _agent_busy(session: Any) -> bool:
    """True while the agent thinks or speaks: a reply or a tool call is under way (the speech counts too, because a
    realtime tool call can run while the state still says 'listening')."""
    if getattr(session, "agent_state", None) in ("thinking", "speaking"):
        return True
    done = getattr(getattr(session, "current_speech", None), "done", None)
    return callable(done) and not done()


def _handle_state(handle: Any) -> str:
    """
    C-50 escalation: what a generate_reply speech handle (livekit-agents 1.4.6 SpeechHandle, public members only) says.
    _RUNNING while not done; _FAILED when it finished without any chat item and was not interrupted (its reply task
    raised, e.g. RealtimeError 'generate_reply timed out waiting for generation_created event', or nothing was
    generated); _UNKNOWN for anything else (an interrupted handle, one with items, or one that cannot tell).
    """
    done = getattr(handle, "done", None)
    try:
        finished = done() if callable(done) else None
    except Exception:  # pylint: disable=broad-exception-caught
        return _UNKNOWN
    if finished is False:
        return _RUNNING
    if finished is not True or getattr(handle, "interrupted", None) is True:
        return _UNKNOWN
    items = getattr(handle, "chat_items", None)
    if not isinstance(items, list):
        return _UNKNOWN
    return _UNKNOWN if items else _FAILED


@dataclass
class _Pending:
    msg_id: str
    origin: str
    since: float


@dataclass
class _Typed:
    """A typed message the rescue may send again (C-50 escalation): the text the model got and its pending key."""
    text: str
    origin: str
    key: str


class ConversationRouter:
    """One per job: routes typed text into the AgentSession and emits every transcript message once."""

    def __init__(self, assistant: Any = None, *, notify: NotifyFn | None = None,  # pylint: disable=too-many-arguments
                 spawn: SpawnFn | None = None, clock: Callable[[], float] = time.monotonic,
                 reason: ReasonFn | None = None, notify_reasoning: NotifyFn | None = None,
                 dispatch_command: DispatchFn | None = None) -> None:
        self.assistant = assistant
        self._reason = reason  # C-43 (K-67): jarvis_reasoning unless a test injects one
        self._notify_reasoning = notify_reasoning
        self._dispatch_command = dispatch_command  # '/swarm': the UI command registry unless injected
        self.session: Any = None
        self.turn_log: deque[dict[str, Any]] = deque(maxlen=TURN_LOG_MAX)
        self._notify = notify
        self._spawn_fn = spawn
        self._clock = clock
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._pending: OrderedDict[str, list[_Pending]] = OrderedDict()
        self._turn_id: str | None = None
        self._turn_source = "voice"
        self._tasks: set[asyncio.Task[Any]] = set()
        self._emit_tail: asyncio.Task[Any] | None = None  # the newest send of the ordered chain
        self._awaiting: OrderedDict[str, _Typed | None] = OrderedDict()  # C-50: typed ids with no reply yet
        # C-50 escalation: typed ids not sent yet (waiting for the send lock, the idle restart or the interrupt); a
        # stop or a C-119 handled message removes them, and handle_text then never sends them
        self._queued: OrderedDict[str, None] = OrderedDict()
        self._send_lock = asyncio.Lock()  # typed messages reach the session one at a time, in the order they came
        self._rescues: set[asyncio.Task[Any]] = set()  # never awaited by drain(): each one sleeps first
        self._typed_hooks: list[TypedHook] = []  # C-119: checked before the model (add_typed_hook)
        # C-50 escalation: the router's last fresh restart (job-wide rate limit) and the session's last activity
        self._last_fresh_restart: float | None = None
        self._fresh_restart: tuple[Any, Any] | None = None  # that restart: (realtime session, its connection before)
        self._last_activity: float | None = None
        self.rescue_counts: dict[str, int] = dict.fromkeys(
            ("rescues", "escalations", "restarts", "resends", "notes", "idle_restarts", "rate_limited"), 0)

    def add_typed_hook(self, hook: TypedHook) -> None:
        """C-119: hook(text) sees every accepted typed message (normalised) before the model; True means it handled
        the message itself (shown once, never sent to the model), and so does a follow-up callable it returns, which
        runs once Sir's own message went out (the hook's answer then follows it in the chat). Registering the same
        hook twice keeps one."""
        if hook not in self._typed_hooks:
            self._typed_hooks.append(hook)

    async def _typed_hook_handled(self, text: str) -> Any:
        """False, or what the first hook that handled text returned: True or its follow-up callable."""
        for hook in list(self._typed_hooks):
            try:
                handled = hook(text)
                if inspect.isawaitable(handled):
                    handled = await handled
            except Exception as e:  # pylint: disable=broad-exception-caught  # a broken hook never loses the message
                logger.error("CHAT_ROUTER typed hook failed: %s", type(e).__name__)
                continue
            if handled is True or callable(handled):
                return handled
        return False

    async def _follow_up(self, follow_up: Callable[[], Any], echo: "asyncio.Task[Any] | None") -> None:
        """A hook's follow-up, once Sir's message (echo) went out; never fails the typed message."""
        await self._sent(echo)
        try:
            result = follow_up()
            if inspect.isawaitable(result):
                await result
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("CHAT_ROUTER typed hook follow-up failed: %s", type(e).__name__)

    # ------------------------------------------------------------------ session

    def attach(self, session: Any) -> None:
        """The session typed text goes into (after session.start)."""
        self.session = session
        self._last_activity = self._clock()  # a new session is a new connection
        self._fresh_restart = None  # an old session's restart (the job-wide rate limit itself stays)

    def detach(self) -> None:
        """No session any more: typed text gets the offline answer until the next attach()."""
        self.session = None
        self._fresh_restart = None
        self._awaiting.clear()
        for task in list(self._rescues):
            task.cancel()

    @property
    def running(self) -> bool:
        return self.session is not None

    # ------------------------------------------------------------------ typed input

    async def handle_text(self, text: Any, msg_id: Any = None, origin: str = ORIGIN_UI) -> str:
        """
        Routes one typed message (K-59). Returns its status: 'accepted', 'offline', 'blocked', 'duplicate',
        'empty', 'handled' (a C-119 typed hook took it) or 'stopped' (C-51's stop or a C-119 handled message ended
        it before it was sent, _send_typed). Never raises for a bad message; one 'CHAT_ROUTER <status>' INFO line
        per message.
        """
        user_id = valid_msg_id(msg_id) or str(uuid.uuid4())
        if user_id in self._seen:
            self._seen.move_to_end(user_id)
            self._log("duplicate", user_id, origin, "")
            return "duplicate"
        self._remember(user_id)

        clean = normalize_text(text)
        if not clean:
            self._log("empty", user_id, origin, "")
            return "empty"

        threat = vortex_guard.detect_threat(clean)
        if threat:
            self._emit_ordered("user", clean, msg_id=user_id, source="text")
            await self._sent(self._emit_ordered("assistant", BLOCKED_TEXT, msg_id=str(uuid.uuid4()),
                                                source="system", turn_id=user_id))
            try:
                jarvis_audit.log_threat(threat, source=f"TypedChat:{origin}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("CHAT_ROUTER audit failed: %s", type(e).__name__)
            self._log("blocked", user_id, origin, clean)
            return "blocked"

        # waiting for a reply or to be sent; a message typed while the hooks run arms its own rescue, which stays
        older = list(self._awaiting) + list(self._queued)
        handled = await self._typed_hook_handled(clean) if self._typed_hooks else False
        if handled:
            # never sent to the model, so never rescued; the rescue of an older typed message ends too (C-50 x C-51):
            # the handled message is a stop, and that rescue would ask the model about what it stopped
            ended = self._end_rescues(older)
            echo = self._emit_ordered("user", clean, msg_id=user_id, source="text")
            self._log_turn(user_id, "user", clean, "text")
            self._log("handled", user_id, origin, clean)
            if ended:
                logger.info("CHAT_ROUTER handled id=%s ended %d typed rescue(s)", user_id, ended)
            if callable(handled):  # the hook's answer comes after Sir's own message
                await self._follow_up(handled, echo)
            return "handled"

        swarm_task = swarm_task_of(clean)
        if swarm_task is not None:
            return await self._swarm(swarm_task, user_id, origin, clean)

        self._emit_ordered("user", clean, msg_id=user_id, source="text")  # beside the reply, never before it
        self._log_turn(user_id, "user", clean, "text")

        session = self.session
        if session is None:
            await self._offline(clean, user_id)
            self._log("offline", user_id, origin, clean)
            return "offline"

        self._queued[user_id] = None  # registered before any wait: what ends a typed message ends it here too
        try:
            async with self._send_lock:  # C-50 escalation: never overtakes an earlier typed message still waiting
                status = await self._send_typed(clean, user_id, origin)
        finally:
            self._queued.pop(user_id, None)
        if status == "offline":  # the template answer is given outside the lock: it never holds a later message
            await self._offline(clean, user_id)
        elif status == "accepted":
            self.emit_reasoning(clean, "text", user_id)
        self._log(status, user_id, origin, clean)
        return status

    async def _send_typed(self, clean: str, user_id: str, origin: str) -> str:
        """
        Under the send lock: the idle restart (C-50 escalation), session.interrupt() and generate_reply, then the
        rescue is armed. 'accepted'; 'offline' (no session any more, or generate_reply raised); or 'stopped': while it
        waited (behind an earlier typed message, for the idle restart's new connection or for the interrupt) C-51's
        stop (cancel_rescues) or a C-119 handled message ended it, so the model never gets it. A spoken user item
        meanwhile does not: it is usually the late transcript of an earlier spoken turn (on_conversation_item_added).
        """
        session = self.session
        if session is not None and user_id in self._queued:
            await self._fresh_after_idle(session, user_id)  # a long idle session is restarted fresh first
        if user_id not in self._queued:
            return "stopped"
        session = self.session  # a C-33 rebuild (or the job's end) may have replaced it meanwhile
        if session is None:
            return "offline"
        key = _match_key(clean)
        self._add_pending(key, user_id, origin)
        await self._interrupt(session)
        if user_id not in self._queued:
            self._drop_pending(key, user_id)
            return "stopped"
        self._queued.pop(user_id, None)  # from here on it is the session's turn (and the rescue's)
        try:
            session.generate_reply(user_input=TYPED_PREFIX + clean)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # RuntimeError: the session is not running or is closing; anything else: never leave it unanswered
            level = logger.info if isinstance(e, RuntimeError) else logger.error
            level("CHAT_ROUTER generate_reply unavailable (%s): offline answer", type(e).__name__)
            self._drop_pending(key, user_id)
            return "offline"
        self._watch_reply(user_id, session, _Typed(clean, origin, key))
        return "accepted"

    async def text_input_cb(self, _session: Any, event: Any) -> None:
        """RoomOptions text_input callback for 'lk.chat' (K-59): the id from the stream attributes
        'jarvis.client_msg_id' or 'msg_id', else the stream id."""
        info = getattr(event, "info", None)
        attributes = getattr(info, "attributes", None)
        attributes = attributes if isinstance(attributes, dict) else {}
        msg_id = (valid_msg_id(attributes.get("jarvis.client_msg_id")) or valid_msg_id(attributes.get("msg_id"))
                  or valid_msg_id(getattr(info, "stream_id", None)))
        await self.handle_text(getattr(event, "text", ""), msg_id, origin=ORIGIN_LK_CHAT)

    # ------------------------------------------------------------------ session output

    def on_conversation_item_added(self, event: Any) -> "asyncio.Task[Any] | None":
        """
        The session's conversation_item_added (K-58, fast mode): the single source of transcript messages.
        Returns the task that emits the message, or None when nothing is emitted.
        """
        item = getattr(event, "item", event)
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            return None
        text = getattr(item, "text_content", None)
        if not isinstance(text, str) or not text.strip():
            return None
        item_id = valid_msg_id(getattr(item, "id", None)) or str(uuid.uuid4())

        if role == "user":
            typed, body = strip_typed_prefix(text)
            pending = self._take_pending(_match_key(body), prefixed=typed)
            if pending is not None or typed:
                # The router already emitted this message with the client's id.
                self._turn_id = pending.msg_id if pending is not None else None
                self._turn_source = "text"
                logger.debug("CHAT_ROUTER typed item %s not emitted again (turn %s)", item_id, self._turn_id)
                return None  # added locally by livekit, not by the server: no session activity
            self._last_activity = self._clock()  # C-50 escalation: the server transcribed a spoken turn
            self._turn_id, self._turn_source = item_id, "voice"
            self._awaiting.clear()  # C-51: a newer spoken turn (a 'ruko' too) supersedes a typed message's rescue
            # A typed message not sent yet (_queued) stays: the pinned google plugin adds a spoken turn's user item only
            # when that turn's generation completes (and the server keeps generating after an interrupt, C-51), so one
            # that arrives during a typed message's short wait (the interrupt, the send lock, the idle reconnect) is
            # the late transcript of an earlier spoken turn, not a stop. The dashboard Stop and C-119 still end it.
            if getattr(self.assistant, "muted", False) is True:
                return None  # muted: no voice bubble, as before
            self._log_turn(item_id, "user", text, "voice")
            logger.info("CHAT_ROUTER voice id=%s chars=%d: %s", item_id, len(text), preview(text))
            logger.debug("CHAT_ROUTER voice id=%s full text: %s", item_id, text)
            self.emit_reasoning(text, "voice", item_id)
            return self._emit_ordered("user", text, msg_id=item_id, source="voice")

        self._awaiting.clear()  # C-50: the model answers again; no typed message needs a rescue
        self._last_activity = self._clock()  # C-50 escalation: the session answers
        turn_id, source = self._turn_id, self._turn_source
        self._log_turn(item_id, "assistant", text, source, turn_id)
        logger.info("CHAT_ROUTER reply id=%s turn=%s source=%s chars=%d: %s", item_id, turn_id or "-", source,
                    len(text), preview(text))
        logger.debug("CHAT_ROUTER reply id=%s full text: %s", item_id, text)
        return self._emit_ordered("assistant", text, msg_id=item_id, source=source, turn_id=turn_id)

    async def drain(self) -> None:
        """Waits for the emits on_conversation_item_added started (tests, shutdown). Only unfinished tasks are awaited:
        gather() of tasks that are all done completes without yielding to the loop, so a set holding only finished
        tasks whose done callbacks (_tasks.discard) were still queued made the old 'while self._tasks' spin forever."""
        while pending := [task for task in self._tasks if not task.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------ C-43: reasoning and '/swarm'

    def emit_reasoning(self, text: str, source: str, msg_id: str) -> "asyncio.Task[Any] | None":
        """K-67: the Reasoning panel's event for an accepted user message (source 'text' or 'voice'), computed in the
        background with a REASONING_TIMEOUT_S cap; it never produces the reply."""
        return self._spawn(self._reasoning(text, source, msg_id))

    async def _reasoning(self, text: str, source: str, msg_id: str) -> None:
        history = [{"role": entry["role"], "content": entry["text"]} for entry in self.turn_log
                   if entry.get("id") != msg_id][-REASONING_HISTORY:]
        reason = self._reason or _advanced_reasoning
        try:
            result = await asyncio.wait_for(reason(text, history), REASONING_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.debug("CHAT_ROUTER reasoning took over %.0f s for id=%s: skipped", REASONING_TIMEOUT_S, msg_id)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("CHAT_ROUTER reasoning failed for id=%s: %s", msg_id, type(e).__name__)
            return
        notify = self._notify_reasoning or bridge_notifier.notify_reasoning
        try:
            await notify(result, source=source, msg_id=msg_id)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("CHAT_ROUTER reasoning notify failed: %s", type(e).__name__)

    async def _swarm(self, task: str, user_id: str, origin: str, clean: str) -> str:
        """'/swarm <task>' (K-59): the registered 'swarm' command runs once with the task; the router emits nothing (the
        swarm module is the single emitter) and the session does not answer."""
        if not task:
            self._log("empty", user_id, origin, clean)
            return "empty"
        dispatch_command = self._dispatch_command or self._dispatch_registered
        try:
            await dispatch_command(SWARM_COMMAND, task)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("CHAT_ROUTER swarm dispatch failed: %s", type(e).__name__)
        self._log("swarm", user_id, origin, clean)
        return "swarm"

    async def _dispatch_registered(self, command: str, payload: Any) -> bool:
        return await dispatch_ui_command(command, payload, CommandContext(assistant=self.assistant))

    # ------------------------------------------------------------------ internals

    def _remember(self, msg_id: str) -> None:
        self._seen[msg_id] = None
        while len(self._seen) > DEDUPE_MAX:
            self._seen.popitem(last=False)

    def _prune_pending(self, now: float) -> None:
        for key in list(self._pending):
            fresh = [p for p in self._pending[key] if now - p.since <= PENDING_KEEP_S]
            if fresh:
                self._pending[key] = fresh
            else:
                del self._pending[key]

    def _add_pending(self, key: str, msg_id: str, origin: str) -> None:
        now = self._clock()
        self._prune_pending(now)
        self._pending.setdefault(key, []).append(_Pending(msg_id, origin, now))

    def _drop_pending(self, key: str, msg_id: str) -> None:
        entries = [p for p in self._pending.get(key, []) if p.msg_id != msg_id]
        if entries:
            self._pending[key] = entries
        else:
            self._pending.pop(key, None)

    def _take_pending(self, key: str, *, prefixed: bool) -> _Pending | None:
        """The oldest typed message with this text (same text twice: in the order they were sent)."""
        now = self._clock()
        self._prune_pending(now)
        entries = self._pending.get(key)
        if not entries:
            return None
        pending = entries[0]
        if not prefixed and now - pending.since > PENDING_MATCH_S:
            return None
        self._drop_pending(key, pending.msg_id)
        return pending

    def _log_turn(self, msg_id: str, role: str, text: str, source: str, turn_id: str | None = None) -> None:
        """One entry of the turn log {id, role, text, source, ts, turn_id?, persona? (assistant)}: the jarvis_reasoning
        history and C-52's memory (MemoryExtractor saves each entry once, by its id)."""
        entry: dict[str, Any] = {"id": msg_id, "role": role, "text": text, "source": source, "ts": time.time()}
        if turn_id:
            entry["turn_id"] = turn_id
        if role == "assistant":
            persona = reply_persona(self.assistant)  # C-52: recorded when the reply came, not when it is saved
            if persona is not None:
                entry["persona"] = persona
        self.turn_log.append(entry)

    def _log(self, status: str, msg_id: str, origin: str, text: str) -> None:
        # C-64 SP-1 (C-33 step A fix): the INFO line carries status, id, origin and length only; typed chat text never
        # reaches the file log or the UI log panel. The full text stays on the console at DEBUG (a private template).
        logger.info("CHAT_ROUTER %s id=%s origin=%s chars=%d", status, msg_id, origin, len(text))
        if text:
            logger.debug("CHAT_ROUTER %s id=%s full text: %s", status, msg_id, text)

    def _emit_ordered(self, role: str, text: str, **kwargs: Any) -> "asyncio.Task[Any] | None":
        """Sends one message after the previous one went out (or after EMIT_ORDER_WAIT_S); returns its task."""
        previous = self._emit_tail

        async def send() -> None:
            if previous is not None and not previous.done():
                await asyncio.wait({previous}, timeout=EMIT_ORDER_WAIT_S)
            await self._emit(role, text, **kwargs)

        task = self._spawn(send())
        if task is not None:
            self._emit_tail = task
        return task

    @staticmethod
    async def _sent(task: "asyncio.Task[Any] | None") -> None:
        """Waits until a chained send went out (the blocked and offline answers return after their message)."""
        if task is not None:
            await asyncio.shield(task)

    async def _emit(self, role: str, text: str, **kwargs: Any) -> None:
        notify = self._notify or jarvis_bridge.notify_transcription
        try:
            await notify(role, text, **kwargs)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("CHAT_ROUTER notify failed for %s: %s", role, type(e).__name__)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> "asyncio.Task[Any] | None":
        try:
            task = self._spawn_fn(coro) if self._spawn_fn is not None else asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no running loop: nothing can be sent
            coro.close()
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def cancel_rescues(self) -> int:
        """C-51: 'stop' (K-60) ends what the owner stopped. A stop before a typed reply's first audio adds no assistant
        item, so without this the rescue would ask the session again ~12 s later and answer the stopped question (or
        run its cancelled tool call). A typed message not sent yet (_queued: waiting for an earlier one, the idle
        restart or the interrupt) is never sent. Returns how many typed messages were still waiting."""
        waiting = len(self._awaiting) + len(self._queued)
        self._awaiting.clear()
        self._queued.clear()
        for task in list(self._rescues):
            task.cancel()
        return waiting

    def _end_rescues(self, msg_ids: list[str]) -> int:
        """C-119 x C-50: the rescues of these typed messages end (each one's task returns at its next look without
        asking the session), and those not sent yet (_queued) are never sent; a rescue armed for any other message
        stays. Returns how many were still armed or waiting."""
        ended = 0
        for msg_id in msg_ids:
            found = False
            for waiting in (self._awaiting, self._queued):
                if msg_id in waiting:
                    del waiting[msg_id]
                    found = True
            ended += found
        return ended

    def _watch_reply(self, msg_id: str, session: Any, typed: _Typed | None = None) -> None:
        """C-50: arms the one-time rescue of a typed message (_rescue). Only the newest typed message is watched: a
        rescue asks the session to answer its context, whose last user turn is that message. typed is what the
        escalation sends again."""
        self._awaiting.clear()
        self._awaiting[msg_id] = typed
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no running loop: nothing to watch with
            return
        task = loop.create_task(self._rescue(msg_id, session))
        self._rescues.add(task)
        task.add_done_callback(self._rescues.discard)

    async def _rescue(self, msg_id: str, session: Any) -> None:
        """
        C-50: a typed message that got no reply at all is asked again once with session.generate_reply(), its text
        already in the context. Seen live: a typed turn 0.3 s after a voice switch's fresh reconnect, which then
        failed with 1008, so the plugin dropped what was queued for the connection (the turn's generate trigger
        included) and nothing answered. Nothing when any reply came, the session changed, a newer typed message waits
        to be sent or RESCUE_GIVE_UP_S passed while the agent was busy; never while it thinks or speaks. When that ask
        ends without any reply too, the escalation (_escalate) takes over once; the message keeps waiting (in
        _awaiting) until the rescue is over.
        """
        waited, delay = 0.0, RESCUE_AFTER_S
        while True:
            await asyncio.sleep(delay)
            waited += delay
            if self._typed_gone(msg_id, session) is not None:  # a reply, a stop, a newer turn, a rebuild
                self._awaiting.pop(msg_id, None)  # (a newer typed message waiting to be sent is the one watched next)
                return
            if not _agent_busy(session):
                break
            if waited >= RESCUE_GIVE_UP_S:
                self._awaiting.pop(msg_id, None)
                logger.info("CHAT_ROUTER no reply for typed id=%s after %.0f s of work: not asked again", msg_id,
                            waited)
                return
            delay = RESCUE_POLL_S
        typed = self._awaiting.get(msg_id)
        self.rescue_counts["rescues"] += 1
        logger.warning("CHAT_ROUTER no reply for typed id=%s after %.0f s: asking the session again", msg_id, waited)
        try:
            handle = session.generate_reply()
        except RuntimeError as e:  # the session is not running or is closing: its rebuild or the job's end follows
            self._awaiting.pop(msg_id, None)
            logger.info("CHAT_ROUTER rescue unavailable (%s)", type(e).__name__)
            return
        except Exception as e:  # pylint: disable=broad-exception-caught  # RealtimeError and the like
            failure = type(e).__name__
        else:
            outcome = await self._reply_outcome(msg_id, session, handle)
            if outcome != _FAILED:  # a reply, a stop, a newer turn, a rebuild, or a handle that cannot tell
                self._awaiting.pop(msg_id, None)
                return
            failure = "no_generation"
        await self._escalate(msg_id, session, typed, failure)

    # ------------------------------------------------------------------ C-50 escalation

    async def _escalate(self, msg_id: str, session: Any, typed: _Typed | None, failure: str) -> None:
        """The rescue's ask failed too: one fresh restart, then the typed message once more (_restart_and_resend);
        when that fails as well, the note (_post_rescue_failed), once _note_owed saw no late reply. Runs inside the
        rescue's task, so C-51's stop (cancel_rescues) and detach() cancel it; a reply, a newer turn (a typed one as
        soon as it waits to be sent) or a C-119 handled message end it at its next look. Only reasons and counts are
        logged."""
        self.rescue_counts["escalations"] += 1
        logger.warning("CHAT_ROUTER rescue for typed id=%s failed (%s): one fresh restart, then the message once more",
                       msg_id, failure)
        problem = await self._restart_and_resend(msg_id, session, typed)
        owed = problem is not None and await self._note_owed(msg_id, session)
        self._awaiting.pop(msg_id, None)
        if problem is not None:
            if owed:
                await self._post_rescue_failed(msg_id, problem)
            else:
                logger.info("CHAT_ROUTER typed id=%s after the escalation (%s): a reply came or is under way, or the "
                            "message no longer waits: no note", msg_id, problem)
        logger.info("CHAT_ROUTER typed rescue counts: %s",
                    " ".join(f"{name}={count}" for name, count in self.rescue_counts.items()))

    async def _restart_and_resend(self, msg_id: str, session: Any, typed: _Typed | None) -> str | None:
        """None when nothing more is owed (a reply came or is under way, or the message stopped waiting), else the
        reason the note gives the log. Steps: a safe point, the fresh restart and its new connection, a safe point
        again (the replayed history holds the plugin's lock), then the message once more. The job's rate limit is
        checked here and again right before the restart (_restart_and_reconnect)."""
        if typed is None:
            return "no_text"
        if self._restart_too_soon():
            self.rescue_counts["rate_limited"] += 1
            return "rate_limited"
        steps = (functools.partial(self._wait_safe_point, msg_id, session),
                 functools.partial(self._restart_and_reconnect, msg_id, session),
                 functools.partial(self._wait_safe_point, msg_id, session),
                 functools.partial(self._resend, msg_id, session, typed))
        for step in steps:
            result = await step()
            if result is not None:  # the step ends the escalation
                return None if result == _ENDED else result
        return None

    async def _wait_safe_point(self, msg_id: str, session: Any) -> str | None:
        """None at a safe point; else _ENDED, _SESSION or 'busy' (none came within SAFE_POINT_WAIT_S)."""
        result = await self._wait_until(lambda: self._typed_gone(msg_id, session) or self._safe_now(session),
                                        SAFE_POINT_WAIT_S)
        if result == _OK:
            return None
        return _BUSY if result == _TIMEOUT else result

    async def _restart_and_reconnect(self, msg_id: str, session: Any) -> str | None:
        """None once the fresh connection is up (or after RECONNECT_WAIT_S: the plugin queues what is sent for it);
        else _ENDED, _SESSION, 'no_restart' (the session offers no restart) or 'rate_limited'. The job's rate limit is
        checked again first: a router restart that came while the escalation waited for its safe point is never
        followed by a second one (the plugin would drop what was queued for the first one's connection); that
        restart's new connection is awaited instead."""
        if self._restart_too_soon():
            restart = self._fresh_restart
            if restart is None:  # no longer kept (an old session's): still never a second restart
                self.rescue_counts["rate_limited"] += 1
                return "rate_limited"
            logger.info("CHAT_ROUTER a fresh restart came while typed id=%s waited: no second one, its connection is "
                        "used", msg_id)
        else:
            restart = self._restart_fresh(self._realtime_of(session), "typed_rescue")
            if restart is None:
                return "no_restart"
        result = await self._wait_until(
            lambda: self._typed_gone(msg_id, session) or self._reconnected(*restart), RECONNECT_WAIT_S)
        if result == _TIMEOUT:
            logger.info("CHAT_ROUTER fresh connection not up after %.0f s for typed id=%s: sending anyway",
                        RECONNECT_WAIT_S, msg_id)
        return None if result in (_OK, _TIMEOUT) else result

    async def _resend(self, msg_id: str, session: Any, typed: _Typed) -> str:
        """The typed message once more as a new user turn (its user item is not shown again and the reply carries
        turn_id = this message); _ENDED when nothing more is owed, else why it failed."""
        self._add_pending(typed.key, msg_id, typed.origin)
        try:
            handle = session.generate_reply(user_input=TYPED_PREFIX + typed.text)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._drop_pending(typed.key, msg_id)
            return f"resend_{type(e).__name__}"
        self.rescue_counts["resends"] += 1
        logger.info("CHAT_ROUTER typed id=%s sent again after a fresh restart", msg_id)
        outcome = await self._reply_outcome(msg_id, session, handle)
        if outcome == _FAILED:
            return "resend_no_generation"
        return _SESSION if outcome == _SESSION else _ENDED

    async def _note_owed(self, msg_id: str, session: Any) -> bool:
        """
        Whether the note goes out after a failed escalation. livekit-agents 1.4.6 gives up on generate_reply after a
        fixed 5 s ('generate_reply timed out waiting for generation_created event'), yet a generation that arrives
        later still plays, as a server-initiated speech (google plugin _start_new_generation, user_initiated False);
        its reply carries turn_id = this message (its typed user item is the router's last user turn). So the note
        waits NOTE_GRACE_S first: True when the message still waited all along and the session never looked busy
        (session_policy.session_busy: no reply, tool call or user speech under way), or when the session changed
        (nothing can answer it there any more); False once the message stopped waiting (a reply, a stop, a newer turn)
        or a speech was under way: the escalation then ends silently, as the rescue does when a reply is in progress
        (the UI still offers its retry), so a note never contradicts a reply that follows, and Sir is never asked to
        send again what a late reply (and its tool call) already does.
        """
        def check() -> str | None:
            gone = self._typed_gone(msg_id, session)
            if gone is not None:
                return gone
            return _BUSY if session_policy.session_busy(session) else None

        return await self._wait_until(check, NOTE_GRACE_S) in (_TIMEOUT, _SESSION)

    async def _post_rescue_failed(self, msg_id: str, reason: str) -> None:
        """One short assistant note (K-58: source 'system', turn_id = the typed message) instead of silence; never in
        the turn log, so memory keeps the unanswered message as it was."""
        self.rescue_counts["notes"] += 1
        logger.warning("CHAT_ROUTER no reply for typed id=%s even after the escalation (%s): note posted", msg_id,
                       reason)
        await self._sent(self._emit_ordered("assistant", RESCUE_FAILED_TEXT, msg_id=str(uuid.uuid4()),
                                            source="system", turn_id=msg_id, extra={"kind": RESCUE_NOTE_KIND}))

    async def _reply_outcome(self, msg_id: str, session: Any, handle: Any) -> str:
        """How the reply a generate_reply started went: _ENDED (the message no longer waits: a reply came, a stop, a
        newer turn), _SESSION (the session changed), _FAILED (its handle ended without any reply) or _UNKNOWN (a handle
        that cannot tell, an interrupted one, one with items, or one still running after RESCUE_WATCH_S)."""
        if _handle_state(handle) not in (_RUNNING, _FAILED):
            return _UNKNOWN  # e.g. a test double or a future livekit: nothing to watch, as before the escalation

        def check() -> str | None:
            gone = self._typed_gone(msg_id, session)
            if gone is not None:
                return gone
            state = _handle_state(handle)
            return None if state == _RUNNING else state

        result = await self._wait_until(check, RESCUE_WATCH_S)
        return _UNKNOWN if result == _TIMEOUT else result

    async def _fresh_after_idle(self, session: Any, msg_id: str) -> None:
        """A typed message after IDLE_FRESH_AFTER_S without any session activity: one fresh restart before it is sent
        (live: a session idle ~55 minutes answered no typed message, a fresh one at once), at a safe point and inside
        the job's rate limit; it waits for the new connection at most RECONNECT_WAIT_S, less when the message stops
        waiting (_queued) or the session changes. Runs under the send lock. Never fails the message."""
        last = self._last_activity
        if last is None:
            return
        idle = self._clock() - last
        if idle < IDLE_FRESH_AFTER_S or self._restart_too_soon():
            return
        if self._safe_now(session) is None:
            logger.info("CHAT_ROUTER typed message after %.0f min without session activity: the session is busy, "
                        "no fresh restart", idle / 60)
            return
        restart = self._restart_fresh(self._realtime_of(session), "typed_after_idle")
        if restart is None:
            return
        self.rescue_counts["idle_restarts"] += 1
        logger.info("CHAT_ROUTER typed message after %.0f min without session activity: fresh restart first",
                    idle / 60)
        result = await self._wait_until(
            lambda: self._typed_gone(msg_id, session, self._queued) or self._reconnected(*restart), RECONNECT_WAIT_S)
        if result == _TIMEOUT:
            logger.info("CHAT_ROUTER fresh connection not up after %.0f s: sending anyway", RECONNECT_WAIT_S)

    def _typed_gone(self, msg_id: str, session: Any, waiting: dict[str, Any] | None = None) -> str | None:
        """_ENDED once msg_id no longer waits (in _awaiting, or in waiting when given) or, for a sent message (no
        waiting given: its rescue and escalation), once a newer typed message waits to be sent (_queued): that one is
        watched next, and its interrupt would cut whatever was asked for this one meanwhile. _SESSION once the session
        changed, else None."""
        if msg_id not in (self._awaiting if waiting is None else waiting):
            return _ENDED
        if waiting is None and self._queued:
            return _ENDED
        return _SESSION if self.session is not session else None

    def _safe_now(self, session: Any) -> str | None:
        """_OK at a safe point for a fresh restart: the agent neither thinks nor speaks, no speech (a tool call
        included) is running, the user is not speaking (session_policy.session_busy, agent_core's C-49 rule) and the
        plugin's session lock is free (no reconnect or context update under way); else None."""
        if session_policy.session_busy(session):
            return None
        return None if session_policy.realtime_lock_busy(self._realtime_of(session)) else _OK

    @staticmethod
    def _reconnected(rt_session: Any, before: Any) -> str | None:
        """_OK once the realtime session holds a connection other than before, or when it cannot tell."""
        return None if gemini_resume_guard.reconnected(rt_session, before) is False else _OK

    def _realtime_of(self, session: Any) -> Any:
        """The live Gemini session behind the AgentSession (the assistant's activity, else the session's own)."""
        return session_policy.realtime_session(self.assistant) or session_policy.realtime_session(session)

    def _restart_too_soon(self) -> bool:
        last = self._last_fresh_restart
        return last is not None and self._clock() - last < FRESH_RESTART_GAP_S

    def _restart_fresh(self, rt_session: Any, reason: str) -> tuple[Any, Any] | None:
        """One fresh reconnect (C-49 restart_fresh; it logs SESSION_RESTART_FRESH reason=<reason>), counted for the
        job's rate limit. Returns (rt_session, the connection it held before), what _reconnected tells the new
        connection by, and keeps it for an escalation that finds this restart came while it waited; None when the
        session offers no restart."""
        before = gemini_resume_guard.connection_of(rt_session)
        if not gemini_resume_guard.restart_fresh(rt_session, reason):
            return None
        self._last_fresh_restart = self._last_activity = self._clock()
        self._fresh_restart = (rt_session, before)
        self.rescue_counts["restarts"] += 1
        return self._fresh_restart

    @staticmethod
    async def _wait_until(check: Callable[[], str | None], limit: float) -> str:
        """check() every WATCH_POLL_S until it returns a result (returned) or limit seconds passed on the loop's clock
        (_TIMEOUT; a sleep can last longer than asked, e.g. Windows' ~16 ms timer)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        while True:
            result = check()
            if result is not None:
                return result
            if loop.time() >= deadline:
                return _TIMEOUT
            await asyncio.sleep(WATCH_POLL_S)

    async def _interrupt(self, session: Any) -> None:
        """Stops the current and queued speech before the typed reply; never fails the typed message."""
        try:
            result = session.interrupt()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("CHAT_ROUTER interrupt skipped: %s", type(e).__name__)
            return
        if not asyncio.isfuture(result) and not asyncio.iscoroutine(result):
            return
        try:
            await asyncio.wait_for(asyncio.shield(asyncio.ensure_future(result)), INTERRUPT_WAIT_S)
        except asyncio.TimeoutError:
            logger.debug("CHAT_ROUTER interrupt still finishing after %.0f s; replying anyway", INTERRUPT_WAIT_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("CHAT_ROUTER interrupt failed: %s", type(e).__name__)

    async def _template_answer(self, text: str) -> str:
        """The old template path (jarvis_reasoning) for when no live session can answer."""
        assistant = self.assistant
        process = getattr(assistant, "process_with_reasoning", None)
        if asyncio.iscoroutinefunction(process):
            return str(await process(text) or "")
        from services.ai_core.jarvis_reasoning import (  # pylint: disable=import-outside-toplevel
            analyze_user_intent,
            generate_smart_response,
        )
        intent = await analyze_user_intent(text)
        return str(await generate_smart_response(text, intent, []) or "")

    async def _offline(self, text: str, user_id: str) -> None:
        try:
            answer = (await asyncio.wait_for(self._template_answer(text), OFFLINE_TIMEOUT_S)).strip()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("CHAT_ROUTER offline template failed: %s", type(e).__name__)
            answer = ""
        reply = OFFLINE_PREFIX + (answer or OFFLINE_FALLBACK_TEXT)
        reply_id = str(uuid.uuid4())
        self._log_turn(reply_id, "assistant", reply, "system", user_id)
        await self._sent(self._emit_ordered("assistant", reply, msg_id=reply_id, source="system", turn_id=user_id))


def router_of(owner: Any) -> ConversationRouter | None:
    """The ConversationRouter attached to an assistant (BrainAssistant.attach_router), else None."""
    router = getattr(owner, "conversation_router", None)
    return router if isinstance(router, ConversationRouter) else None
