"""
# services/utils/jarvis_announcer.py
AnnouncerAPI (contract K-18, plan task C-55): the one way JARVIS shows and speaks something that did not come out of
the conversation itself: the result of an action Sir approved (ConfirmationBroker.on_result), a finished job, a
clipboard tip. services/ai_core/announcer.py re-exports it (the tools-info import path).

    attach(session) / detach(session=None)      agent_runner, when a job's session starts and ends
    set_session(session | None), set_assistant(assistant | None)   aliases
    await announce(text, *, priority='normal', speak=True, kind='info', spoken_text=None)

announce() always posts an assistant Message {id, role:'assistant', text, source:'system', final:true, ts} to the UI
first. When speak is true it then (in the background, so the caller never waits) speaks through the attached session:
it skips a muted assistant, waits up to 10 s for the agent to stop speaking, and calls
session.generate_reply(instructions='Sir ko mukhtasir Roman Urdu mein batayein: ' + what). Speaking is rate-limited to
one announcement per 5 s ('high' bypasses it). Without a session the spoken part is queued (at most 20, oldest dropped)
and flushed on the next attach. Nothing here raises; text never reaches the log (lengths only).

spoken_text (K-18 v17) replaces what is SPOKEN: a caller whose text holds untrusted content (a copied error) speaks a
fixed line instead, so that content never reaches the model as instructions. On Gemini Live an instructions-only
generate_reply is sometimes not voiced; the UI message is the record (CT-VC-PROACTIVE, C-56, improves speaking).

cancel_pending() (C-51, K-60 'stop'): drops the spoken parts still queued and the announcements still waiting for their
turn, which would otherwise start speaking right after Sir stopped JARVIS; their UI messages stay. It counts only those
(one already speaking is the session's interrupt to cut), and it is safe from any job's loop or thread: every piece of
state it reads or clears is kept under the announcer's lock.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any

logger = logging.getLogger("JARVIS-ANNOUNCER")

SPEAK_PREFIX = "Sir ko mukhtasir Roman Urdu mein batayein: "
RATE_LIMIT_S = 5.0
WAIT_SPEAKING_S = 10.0
SPEAKING_POLL_S = 0.2
QUEUE_MAX = 20
PRIORITIES = ("low", "normal", "high")


class Announcer:
    """One per process; agent_runner attaches the live job's session."""

    def __init__(self) -> None:
        self.session: Any = None
        self.assistant: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_spoken = float("-inf")
        self._queue: deque[tuple[str, str]] = deque(maxlen=QUEUE_MAX)  # (spoken text, priority)
        self._lock = threading.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._epoch = 0  # C-51: bumped by cancel_pending(); a speak started before it stays silent
        # C-51: the announcements handed to _speak that have not started speaking yet (token -> the loop they run on);
        # read and changed only under the lock: what cancel_pending() drops and counts
        self._waiting: dict[object, asyncio.AbstractEventLoop] = {}

    # ---- session -------------------------------------------------------------------------------

    def attach(self, session: Any) -> None:
        """The live session (called on its event loop); queued announcements are spoken now."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with self._lock:
            self.session, self._loop = session, loop
            pending = list(self._queue)
            self._queue.clear()
        for spoken, priority in pending:
            self._spawn_speak(spoken, priority)

    def detach(self, session: Any = None) -> None:
        """Forgets the session; with an argument only when it is the attached one (another job's end keeps it)."""
        with self._lock:
            if session is None or session is self.session:
                self.session, self._loop = None, None

    def set_session(self, session: Any) -> None:
        if session is None:
            self.detach()
        else:
            self.attach(session)

    def set_assistant(self, assistant: Any) -> None:
        self.assistant = assistant

    def cancel_pending(self) -> int:
        """C-51 (K-60 'stop'): drops the spoken parts queued for the next session and the announcements still waiting
        for their turn (each one then skips its generate_reply); the UI messages stay. A speech that already plays is
        the session's to interrupt (AgentSession.interrupt) and is not counted. Safe from any job's loop or thread
        (one lock over all of it). Returns how many were dropped."""
        with self._lock:
            waiting = sum(1 for loop in self._waiting.values() if not loop.is_closed())
            dropped = len(self._queue) + waiting
            self._queue.clear()
            self._waiting.clear()
            self._epoch += 1
        if dropped:
            logger.info("ANNOUNCE %d pending announcement(s) dropped: stopped by the user", dropped)
        return dropped

    # ---- announce -----------------------------------------------------------------------------

    async def announce(self, text: str, *, priority: str = "normal", speak: bool = True, kind: str = "info",
                       spoken_text: str | None = None) -> None:
        """K-18: UI message first, then (if speak) the spoken part in the background. Never raises."""
        try:
            clean = " ".join(str(text or "").split())
            if priority not in PRIORITIES:
                priority = "normal"
            if clean:
                await self._post(clean)
            spoken = " ".join(str(spoken_text if spoken_text is not None else clean).split())
            if not speak or not spoken:
                return
            with self._lock:
                attached = self.session is not None
                if not attached:
                    self._queue.append((spoken, priority))
            if attached:
                self._spawn_speak(spoken, priority)
            logger.info("ANNOUNCE kind=%s priority=%s chars=%d spoken=%s", kind, priority, len(clean),
                        "now" if attached else "queued")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("announce failed: %s", type(e).__name__)

    async def _post(self, text: str) -> None:
        from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel
        await jarvis_bridge.notify_transcription("assistant", text, msg_id=str(uuid.uuid4()), source="system")

    def _spawn_speak(self, spoken: str, priority: str) -> None:
        """Speaks on the session's own loop; from another loop or thread it is handed over thread-safely."""
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or loop.is_closed():
            return
        token = object()
        with self._lock:  # C-51: its epoch and its waiting entry together, so a 'stop' sees both or neither
            for stale in [key for key, owner in self._waiting.items() if owner.is_closed()]:
                del self._waiting[stale]  # handed to a loop that closed before it ran: it never speaks
            epoch = self._epoch
            self._waiting[token] = loop
        speak = self._speak(spoken, priority, epoch, token)
        if running is loop:
            task = loop.create_task(speak)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        try:
            asyncio.run_coroutine_threadsafe(speak, loop)
        except RuntimeError:  # the loop closed meanwhile: it never speaks
            speak.close()
            self._not_waiting(token)

    def _not_waiting(self, token: object | None) -> None:
        with self._lock:
            self._waiting.pop(token, None)

    def _stopped_since(self, epoch: int | None) -> bool:
        """True when cancel_pending() ran after this announcement was handed to _speak."""
        if epoch is None or epoch == self._epoch:
            return False
        logger.info("ANNOUNCE not spoken: stopped by the user")
        return True

    def _begin_speaking(self, epoch: int | None, token: object | None) -> bool:
        """C-51: the last check before generate_reply, atomic with cancel_pending(): False when a 'stop' dropped this
        announcement; else it no longer waits (a 'stop' from now on interrupts it instead of dropping it)."""
        with self._lock:
            stopped = epoch is not None and epoch != self._epoch
            self._waiting.pop(token, None)
        if stopped:
            logger.info("ANNOUNCE not spoken: stopped by the user")
        return not stopped

    async def _speak(self, spoken: str, priority: str, epoch: int | None = None, token: object | None = None) -> None:
        try:
            session = self.session
            if session is None or self._stopped_since(epoch):
                return
            if getattr(self.assistant, "muted", False):
                logger.info("ANNOUNCE not spoken: the assistant is muted")
                return
            now = time.monotonic()
            with self._lock:
                if priority != "high" and now - self._last_spoken < RATE_LIMIT_S:
                    logger.info("ANNOUNCE not spoken: rate limit (1 per %.0f s)", RATE_LIMIT_S)
                    return
                self._last_spoken = now
            waited = 0.0
            while getattr(session, "agent_state", None) == "speaking" and waited < WAIT_SPEAKING_S:
                await asyncio.sleep(SPEAKING_POLL_S)
                waited += SPEAKING_POLL_S
            if session is not self.session:  # the job ended while we waited
                return
            if not self._begin_speaking(epoch, token):  # C-51: 'stop' came while it waited for its turn
                return
            reply = session.generate_reply(instructions=SPEAK_PREFIX + spoken)
            if inspect.isawaitable(reply):
                await reply
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("announce speak failed: %s", type(e).__name__)
        finally:
            self._not_waiting(token)  # skipped (muted, rate limit, no session), failed or cancelled: no longer waiting


announcer = Announcer()

# Module-level API (K-18)
attach = announcer.attach
detach = announcer.detach
set_session = announcer.set_session
set_assistant = announcer.set_assistant
announce = announcer.announce
cancel_pending = announcer.cancel_pending
