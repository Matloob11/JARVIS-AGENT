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
"""

from __future__ import annotations

import asyncio
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
PREVIEW_CHARS = 60

# Control characters (C0, DEL, C1) except tab and newline; CR is folded into newline first.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")

NotifyFn = Callable[..., Awaitable[Any]]
SpawnFn = Callable[[Coroutine[Any, Any, Any]], "asyncio.Task[Any]"]


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


def valid_msg_id(msg_id: Any) -> str | None:
    """A client id the router keeps: a str of 1-128 printable characters, else None."""
    if isinstance(msg_id, str) and 0 < len(msg_id) <= ID_MAX_CHARS and msg_id.isprintable():
        return msg_id
    return None


@dataclass
class _Pending:
    msg_id: str
    origin: str
    since: float


class ConversationRouter:
    """One per job: routes typed text into the AgentSession and emits every transcript message once."""

    def __init__(self, assistant: Any = None, *, notify: NotifyFn | None = None,
                 spawn: SpawnFn | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.assistant = assistant
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

    # ------------------------------------------------------------------ session

    def attach(self, session: Any) -> None:
        """The session typed text goes into (after session.start)."""
        self.session = session

    def detach(self) -> None:
        """No session any more: typed text gets the offline answer until the next attach()."""
        self.session = None

    @property
    def running(self) -> bool:
        return self.session is not None

    # ------------------------------------------------------------------ typed input

    async def handle_text(self, text: Any, msg_id: Any = None, origin: str = ORIGIN_UI) -> str:
        """
        Routes one typed message (K-59). Returns its status: 'accepted', 'offline', 'blocked', 'duplicate'
        or 'empty'. Never raises for a bad message; one 'CHAT_ROUTER <status>' INFO line per message.
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
            await self._emit("user", clean, msg_id=user_id, source="text")
            await self._emit("assistant", BLOCKED_TEXT, msg_id=str(uuid.uuid4()), source="system",
                             turn_id=user_id)
            try:
                jarvis_audit.log_threat(threat, source=f"TypedChat:{origin}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("CHAT_ROUTER audit failed: %s", type(e).__name__)
            self._log("blocked", user_id, origin, clean)
            return "blocked"

        await self._emit("user", clean, msg_id=user_id, source="text")
        self._log_turn(user_id, "user", clean, "text")

        session = self.session
        if session is None:
            await self._offline(clean, user_id)
            self._log("offline", user_id, origin, clean)
            return "offline"

        key = _match_key(clean)
        self._add_pending(key, user_id, origin)
        await self._interrupt(session)
        try:
            session.generate_reply(user_input=TYPED_PREFIX + clean)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # RuntimeError: the session is not running or is closing; anything else: never leave it unanswered
            level = logger.info if isinstance(e, RuntimeError) else logger.error
            level("CHAT_ROUTER generate_reply unavailable (%s): offline answer", type(e).__name__)
            self._drop_pending(key, user_id)
            await self._offline(clean, user_id)
            self._log("offline", user_id, origin, clean)
            return "offline"

        self._log("accepted", user_id, origin, clean)
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
                return None
            self._turn_id, self._turn_source = item_id, "voice"
            if getattr(self.assistant, "muted", False) is True:
                return None  # muted: no voice bubble, as before
            self._log_turn(item_id, "user", text, "voice")
            logger.info("CHAT_ROUTER voice id=%s chars=%d: %s", item_id, len(text), preview(text))
            logger.debug("CHAT_ROUTER voice id=%s full text: %s", item_id, text)
            return self._spawn(self._emit("user", text, msg_id=item_id, source="voice"))

        turn_id, source = self._turn_id, self._turn_source
        self._log_turn(item_id, "assistant", text, source, turn_id)
        logger.info("CHAT_ROUTER reply id=%s turn=%s source=%s chars=%d: %s", item_id, turn_id or "-", source,
                    len(text), preview(text))
        logger.debug("CHAT_ROUTER reply id=%s full text: %s", item_id, text)
        return self._spawn(self._emit("assistant", text, msg_id=item_id, source=source, turn_id=turn_id))

    async def drain(self) -> None:
        """Waits for the emits on_conversation_item_added started (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

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
        entry: dict[str, Any] = {"id": msg_id, "role": role, "text": text, "source": source, "ts": time.time()}
        if turn_id:
            entry["turn_id"] = turn_id
        self.turn_log.append(entry)

    def _log(self, status: str, msg_id: str, origin: str, text: str) -> None:
        logger.info("CHAT_ROUTER %s id=%s origin=%s chars=%d%s", status, msg_id, origin, len(text),
                    f": {preview(text)}" if text else "")
        if text:
            logger.debug("CHAT_ROUTER %s id=%s full text: %s", status, msg_id, text)

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
        await self._emit("assistant", reply, msg_id=reply_id, source="system", turn_id=user_id)


def router_of(owner: Any) -> ConversationRouter | None:
    """The ConversationRouter attached to an assistant (BrainAssistant.attach_router), else None."""
    router = getattr(owner, "conversation_router", None)
    return router if isinstance(router, ConversationRouter) else None
