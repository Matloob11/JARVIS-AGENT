"""
# agent_memory.py
Memory extraction for the JARVIS agent.

C-52: MemoryExtractor saves every user and assistant message of a job once, in the one memory file of
JARVIS_USER_ID (K-69; ConversationMemory, encrypted):
- run(items): router.turn_log dicts (C-42: every K-58 transcript message once, with its id) or livekit ChatMessages
  (an assistant without a router). Skipped: other roles, empty texts, injected lines ('[JARVIS CONTEXT]', '[SYSTEM',
  '[LOG DATA', 'System error detected', '[DESKTOP DATA', '[MEMORY]') and the turns re-seeded from memory (ids
  'reseed-*', C-114); the '[typed] ' mark is stripped. Each message is saved once by its id (_seen_ids: the newest
  SEEN_IDS_MAX ids, seeded from the newest SEED_FROM_DISK messages on disk); all new messages of one pass go into one
  bundle (one encrypted write). A message whose save failed stays pending and goes with the next pass.
- a save that fails (a file locked by antivirus or sync software, a full disk, a permission) backs run() off: its next
  write waits SAVE_RETRY_FIRST_S, doubling with every failure in a row up to SAVE_RETRY_MAX_S (the messages of the
  passes in between are collected and stay pending); flush() always tries at once. The failure is logged once per
  streak (ERROR; later ones at DEBUG) and the streak's end in one INFO line.
- flush(): run() without items: what is still pending, the new items of the bound source (bind(); the memory loop
  binds the job's router.turn_log) and a save that is still running. agent_runner's memory loop runs every 10 s, the
  job's end flushes (the runtime teardown and LiveKit's shutdown callback), and ConversationMemory.save_to_disk runs
  flush (the memory storage loop). Nothing to save means no disk access at all.
- every saved message: notify 'memory_update' {id, content[:500], role, source 'extractor', user_id, ts} (K-07), in
  order, in the background (one task per save, kept in _sends until it is done); a send stops once the memory file
  was cleared after its save.
- every saved assistant message records the persona it came from: 'jarvis' | 'anna' from the router's turn_log
  entry, else the bound persona provider (the assistant's reply persona), else 'unknown'. Messages saved before C-52
  have none; readers take them as unknown.
- clear_memory() (K-60 clear_memory, which control_commands runs after the owner's Allow): forgets what was said before
  the clear, then ConversationMemory.clear moves the file (C-51). Under the extractor's lock: a save still running
  lands first (in the file that is moved) and the memory_update sends of the saves before it finish (so none reaches
  the bridge after the clear's memory_cleared), then every message the bound source holds now (the newest turns the
  10 s loop has not read yet) and every pending one count as saved, so no later pass writes them into the fresh file.
- a clear of the file by another job of this process (a second job of the same user during a restart) reaches this
  extractor through the file's clear mark (memory_store.clear_mark): each message notes the clear generation it was
  collected under, and one collected before a newer clear is dropped (counted as saved, never written); in the first
  pass after such a clear a new message said before it (its own time stamp) is dropped too; a save that started
  before the clear and lands after it writes nothing (save_messages(generation=...)).
- a probe or canary job (K-57) saves nothing and notifies nothing.
K-29: the log carries counts and ids, never a text.
"""

import asyncio
import hashlib
import inspect
import math
import re
import time
from collections import OrderedDict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from services.ai_core.memory_store import NEVER_CLEARED, ConversationMemory, clear_mark
from services.utils.jarvis_bridge import notify_memory
from services.utils.jarvis_config import DEFAULT_USER_ID, config
from services.utils.jarvis_logger import setup_logger
from services.utils.probe_context import bridge_suppressed  # K-57 (C-39)

logger = setup_logger("JARVIS-MEMORY-EXTRACTOR")

_background_tasks: set[asyncio.Task[Any]] = set()

SEEN_IDS_MAX = 5000      # message ids this extractor remembers as saved
SEED_FROM_DISK = 500     # ... of which the newest saved messages on disk, read once before its first save
SAVED_ROLES = ("user", "assistant")
SOURCES = ("voice", "text", "system")  # K-58 message sources
PERSONAS = ("jarvis", "anna")
UNKNOWN_PERSONA = "unknown"
TYPED_MARK = "[typed]"   # conversation_router.TYPED_PREFIX without its trailing space
RESEED_ID_PREFIX = "reseed-"  # src/core/session_policy.RESEED_ID_PREFIX: a turn replayed from memory (C-114)
# Lines that were never a real turn: injected context, proactive and bug-hunter lines, laptop data, a re-seed note
# (session_policy.RESEED_SKIP_PREFIXES, which never replays them, minus the router's '[offline] ' answer: a chat
# message the user saw, so it is saved, though never replayed as the model's turn). C-53: a line that starts with the
# RECENT MEMORY block's data tag ('[MEMORY DATA', src/core/memory_recall.py) is memory read back, never a new turn.
INJECTED_PREFIXES = ("[JARVIS CONTEXT]", "[SYSTEM", "[LOG DATA", "System error detected", "[DESKTOP DATA",
                     "[MEMORY]", "[MEMORY DATA")
MESSAGE_MAX_CHARS = 8000  # one saved message (a typed one is at most 4000, K-59)
ID_MAX_CHARS = 128
# After a failed save run() writes again only this long after it (the memory loop's 10 s, doubled), doubling with every
# failure in a row up to SAVE_RETRY_MAX_S; flush() (the job's end, the storage loop) always tries at once.
SAVE_RETRY_FIRST_S = 20.0
SAVE_RETRY_MAX_S = 300.0
UPDATES_WAIT_S = 3.0  # clear_memory waits this long at most for the memory_update sends of earlier saves
# Control characters (C0, DEL, C1) except tab and newline; CR is folded into newline first.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clock() -> float:
    """The backoff's clock (monotonic; tests replace this function, never time.monotonic itself)."""
    return time.monotonic()


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _parts_text(content: Any) -> str:
    """The text of a message content: a str, or the text parts of a list (str, {'text': ...} or objects with a str
    .text), joined with spaces; images, audio and anything else are left out."""
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return ""
    parts: list[str] = []
    for part in content:
        text = part if isinstance(part, str) else part.get("text") if isinstance(part, dict) else getattr(
            part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return " ".join(parts)


def _raw_text(item: Any) -> str:
    if isinstance(item, dict):
        text = item.get("text")
        return text if isinstance(text, str) else _parts_text(item.get("content"))
    text = getattr(item, "text_content", None)  # livekit ChatMessage: its str parts
    return text if isinstance(text, str) else _parts_text(getattr(item, "content", None))


def _clean(text: str) -> str:
    """Control characters out (newline and tab kept), the '[typed] ' mark stripped, at most MESSAGE_MAX_CHARS."""
    text = _CONTROL_CHARS.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if text.startswith(TYPED_MARK):
        text = text[len(TYPED_MARK):].strip()
    return text[:MESSAGE_MAX_CHARS].rstrip()


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def known_persona(value: Any) -> str | None:
    """'jarvis' or 'anna' (any case), else None."""
    if isinstance(value, str) and value.strip().lower() in PERSONAS:
        return value.strip().lower()
    return None


class MemoryExtractor:
    """
    Saves the job's messages in the user's memory file, each once (C-52; see the module docstring).
    """
    user_id: str
    memory: ConversationMemory
    conversation_count: int
    _audited: bool

    def __init__(self, user_id: str | None = None, *, persona: Any = None, source: Any = None):
        """
        user_id: the memory identity (JARVIS_USER_ID, K-69; the runtime passes it). persona: a callable giving the
        persona a reply comes from (or a fixed one). source: a callable giving the job's items for flush().
        """
        self.user_id: str = user_id or getattr(config, "user_id", None) or DEFAULT_USER_ID
        self.memory = ConversationMemory(self.user_id)
        self.conversation_count = 0  # messages this extractor saved
        self._audited = False
        self._seeded = False
        self._lock = asyncio.Lock()
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self._pending: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._collected: dict[str, int] = {}  # pending id -> the clear generation it was collected under
        self._inflight: asyncio.Future[int] | None = None
        self._sends: set[asyncio.Task[Any]] = set()  # this extractor's memory_update sends still running
        self._failures = 0       # failed saves in a row
        self._retry_at = 0.0     # _clock() before which run() writes nothing after a failed save (flush() does)
        self._source = source
        self._persona = persona
        # the clear generation of the file when this extractor last read its items (a newer one: another job cleared)
        self._read_generation = self._clear_mark()[0]
        add_hook = getattr(self.memory, "add_flush_hook", None)
        if callable(add_hook):
            add_hook(self.flush)  # ConversationMemory.save_to_disk() runs it

    def _clear_mark(self) -> tuple[int, float]:
        """The memory file's clear mark (memory_store.clear_mark: generation, time of the newest clear); never cleared
        for a store without a file path (a test's stand-in)."""
        path = getattr(self.memory, "memory_file", None)
        return clear_mark(path) if isinstance(path, str) else NEVER_CLEARED

    def bind(self, *, source: Any = None, persona: Any = None) -> None:
        """The job's item source for flush() (the memory loop binds the router's turn_log) and the persona provider
        (the assistant's reply persona); None leaves one as it is."""
        if source is not None:
            self._source = source
        if persona is not None:
            self._persona = persona

    async def _audit_memory(self) -> None:
        """Audits memory structure and logs stale context count."""
        try:
            integrity_result = self.memory.validate_integrity()
            is_valid: bool = (
                await integrity_result
                if inspect.isawaitable(integrity_result)
                else bool(integrity_result)
            )
            if not is_valid:
                logger.warning(
                    "⚠️ Memory Integrity Compromised for user: %s", self.user_id)

            stale_result = self.memory.archive_stale_context()
            stale_count: int = (
                await stale_result
                if inspect.isawaitable(stale_result)
                else int(stale_result or 0)
            )
            if stale_count > 0:
                logger.info(
                    "🧠 Memory Optimizer: %d stale contexts identified.", stale_count)
        except (OSError, ValueError, RuntimeError, AttributeError, TypeError) as e:
            logger.debug("Memory audit failed: %s", e)

    async def _seed_seen_ids(self) -> None:
        """The ids of the newest SEED_FROM_DISK saved messages count as seen (a store without load_memory gives
        none)."""
        load = getattr(self.memory, "load_memory", None)
        if not callable(load):
            return
        try:
            conversations = load()
            if inspect.isawaitable(conversations):
                conversations = await conversations
        except Exception as e:  # pylint: disable=broad-exception-caught  # the store logs its own trouble
            logger.warning("MEMORY saved ids not read (%s); the store still drops a saved id", type(e).__name__)
            return
        ids: list[str] = []
        for conversation in conversations if isinstance(conversations, list) else []:
            messages = conversation.get("messages") if isinstance(conversation, dict) else None
            if isinstance(messages, list):
                ids.extend(message["id"] for message in messages
                           if isinstance(message, dict) and isinstance(message.get("id"), str))
        for msg_id in ids[-SEED_FROM_DISK:]:
            self._remember(msg_id)
        logger.info("MEMORY extractor for %s ready: %d saved ids known", self.user_id, len(ids[-SEED_FROM_DISK:]))

    def _remember(self, msg_id: str) -> None:
        self._seen_ids[msg_id] = None
        self._seen_ids.move_to_end(msg_id)
        while len(self._seen_ids) > SEEN_IDS_MAX:
            self._seen_ids.popitem(last=False)

    def _current_persona(self) -> Any:
        provider = self._persona
        if callable(provider):
            try:
                return provider()
            except Exception:  # pylint: disable=broad-exception-caught
                return None
        return provider

    def message_of(self, item: Any) -> dict[str, Any] | None:
        """
        The message an item is saved as: {id, role, content, ts, source?, turn_id?, persona (assistant)}, or None when
        it is not saved (another role, an empty text, an injected line, a re-seeded turn). The id is the item's own;
        an item without one (an old caller) gets a stable one from its role, its time (when it has one) and its text.
        """
        role = _field(item, "role")
        if role not in SAVED_ROLES:
            return None
        own_id = _field(item, "id")
        own_id = own_id if isinstance(own_id, str) and 0 < len(own_id) <= ID_MAX_CHARS else None
        if own_id is not None and own_id.startswith(RESEED_ID_PREFIX):
            return None  # C-114: replayed from memory at job start, so it is there already
        raw = _raw_text(item)
        typed = raw.lstrip().startswith(TYPED_MARK)
        text = _clean(raw)
        if not text or text.startswith(INJECTED_PREFIXES):
            return None
        said_at = _epoch(_field(item, "ts")) or _epoch(_field(item, "created_at"))
        if own_id is None:
            basis = f"{role}\n{said_at!r}\n{text}" if said_at is not None else f"{role}\n{text}"
            own_id = "x-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]
        message: dict[str, Any] = {"id": own_id, "role": role, "content": text,
                                   "ts": round(said_at if said_at is not None else time.time(), 3)}
        source = _field(item, "source")
        if source in SOURCES:
            message["source"] = source
        elif typed and role == "user":
            message["source"] = "text"
        if role == "assistant":
            turn_id = _field(item, "turn_id")
            if isinstance(turn_id, str) and turn_id:
                message["turn_id"] = turn_id
            message["persona"] = (known_persona(_field(item, "persona")) or known_persona(self._current_persona())
                                  or UNKNOWN_PERSONA)
        return message

    def _candidates(self, items: Iterable[Any]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in items:
            try:
                message = self.message_of(item)
            except Exception as e:  # pylint: disable=broad-exception-caught  # one odd item never stops the rest
                logger.debug("MEMORY item skipped: %s", type(e).__name__)
                continue
            if message is not None:
                messages.append(message)
        return messages

    async def _read_source(self) -> list[Any]:
        source = self._source
        if source is None:
            return []
        try:
            items = source() if callable(source) else source
            if inspect.isawaitable(items):
                items = await items
            return list(items or [])
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("MEMORY source not read (%s)", type(e).__name__)
            return []

    async def run(self, items: Iterable[Any] | None = None) -> int:
        """
        Saves the new user/assistant messages of items (router.turn_log dicts or ChatMessages; None: the bound source)
        and whatever is still pending, as one bundle. Returns how many messages were written. Never raises for a bad
        item or a failed save (those messages stay pending); a cancel of the caller passes through, while a save that
        has started still finishes. After a failed save it only collects until its backoff is over (flush() does not
        wait).
        """
        return await self._run(items, retry_now=False)

    async def flush(self) -> int:
        """Writes what is not on disk yet: the pending messages, the bound source's new ones and a save still running
        (run() without items), at once, also while run() backs off after a failed save. Returns how many messages it
        wrote."""
        return await self._run(None, retry_now=True)

    async def _run(self, items: Iterable[Any] | None, *, retry_now: bool) -> int:
        if bridge_suppressed.get():
            return 0  # K-57 (C-39): a probe or canary job saves no memory
        async with self._lock:
            # read before the items: a clear after this point makes what they hold older than it
            generation, cleared_at = self._clear_mark()
            cleared_since_read = generation != self._read_generation  # by another job (clear_memory re-reads)
            if items is None:
                items = await self._read_source()
            candidates = self._candidates(list(items or []))
            self._read_generation = generation
            busy = self._inflight is not None and not self._inflight.done()
            if not candidates and not self._pending and not busy:
                return 0  # nothing to save: no disk access
            if not self._audited:
                await self._audit_memory()
                self._audited = True
            if not self._seeded:
                self._seeded = True
                await self._seed_seen_ids()
            said_before_clear = 0
            for message in candidates:
                msg_id = message["id"]
                if msg_id in self._seen_ids or msg_id in self._pending:
                    continue
                if cleared_since_read and message["ts"] < cleared_at:
                    self._remember(msg_id)  # said before another job's clear, read only now: never written
                    said_before_clear += 1
                    continue
                self._pending[msg_id] = message
                self._collected[msg_id] = generation
            if said_before_clear:
                logger.info("MEMORY %d message(s) said before the memory file was cleared are not written",
                            said_before_clear)
            if not retry_now and self._failures and _clock() < self._retry_at:
                return 0  # backing off after a failed save: collected, written by a later pass or a flush
            return await self._write_pending()

    async def _write_pending(self) -> int:
        inflight = self._inflight
        if inflight is not None and not inflight.done():
            await self._await_save(inflight)  # its messages leave _pending once it is done
        generation = self._clear_mark()[0]
        self._drop_collected_before(generation)
        if not self._pending:
            return 0
        task = asyncio.ensure_future(self._save_bundle(list(self._pending.values()), generation))
        self._inflight = task
        return await self._await_save(task)

    def _drop_collected_before(self, generation: int) -> None:
        """The pending messages collected before a newer clear of the file (another job's ConversationMemory cleared
        it, or its clear() ran on its own) are never written; they count as saved, so the source never brings them
        back."""
        stale = [msg_id for msg_id in self._pending if self._collected.get(msg_id, generation) != generation]
        for msg_id in stale:
            self._pending.pop(msg_id, None)
            self._collected.pop(msg_id, None)
            self._remember(msg_id)
        if stale:
            logger.info("MEMORY %d message(s) collected before the memory file was cleared are not written",
                        len(stale))

    @staticmethod
    async def _await_save(task: "asyncio.Future[int]") -> int:
        """Waits for a save without cancelling it: a cancel of the caller (the job's loops end) passes through and the
        save still finishes; a save cancelled on its own (its loop is closing) counts as failed."""
        current = asyncio.current_task()
        before = current.cancelling() if current is not None else 0  # a cancel the caller already took counts not
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled() and (current is None or current.cancelling() <= before):
                return 0
            raise

    async def _save_bundle(self, messages: list[dict[str, Any]], generation: int = 0) -> int:
        """One encrypted write of the messages as one conversation (collected under the clear `generation`); their
        bookkeeping happens here, so a caller's cancel never leaves them half done."""
        bundle = {"messages": messages, "timestamp": datetime.now().isoformat(), "user_id": self.user_id}
        try:
            if isinstance(self.memory, ConversationMemory):
                saved = await self.memory.save_messages(bundle, generation=generation)
            else:  # another store (a test's stand-in): its save_conversation, all or nothing
                saved = list(messages) if await self.memory.save_conversation(bundle) else None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._save_failed(type(e).__name__, len(messages))
            return 0
        if saved is None:
            self._save_failed(None, len(messages))
            return 0
        for message in messages:  # on disk now, there already, or said before a newer clear (never written)
            self._pending.pop(message["id"], None)
            self._collected.pop(message["id"], None)
            self._remember(message["id"])
        self._save_worked()
        if not saved:
            return 0
        users = sum(1 for message in saved if message.get("role") == "user")
        self.conversation_count += len(saved)
        logger.info("✅ MEMORY saved %d message(s) (user %d, assistant %d)", len(saved), users, len(saved) - users)
        logger.debug("MEMORY saved ids: %s", ", ".join(str(message.get("id")) for message in saved))
        self._notify_saved(saved, generation)
        return len(saved)

    def _save_failed(self, error: str | None, count: int) -> None:
        """A failed save: its messages stay pending and run() backs off (SAVE_RETRY_FIRST_S, doubled with every
        failure in a row, at most SAVE_RETRY_MAX_S). Logged at ERROR once per failure streak, then at DEBUG."""
        self._failures += 1
        delay = min(SAVE_RETRY_FIRST_S * 2 ** min(self._failures - 1, 16), SAVE_RETRY_MAX_S)
        self._retry_at = _clock() + delay
        cause = f" ({error})" if error else ""
        if self._failures == 1:
            logger.error("❌ MEMORY save failed%s; %d message(s) stay pending; the memory loop tries again after %d s, "
                         "backing off up to %d s (a flush at the job's end tries at once); later failures of this "
                         "streak are logged at DEBUG", cause, count, int(delay), int(SAVE_RETRY_MAX_S))
        else:
            logger.debug("MEMORY save failed%s again (%d in a row); %d message(s) pending; next try after %d s", cause,
                         self._failures, count, int(delay))

    def _save_worked(self) -> None:
        if self._failures:
            logger.info("✅ MEMORY saving works again after %d failed attempt(s)", self._failures)
        self._failures = 0
        self._retry_at = 0.0

    def _notify_saved(self, saved: list[dict[str, Any]], generation: int = 0) -> None:
        """memory_update (K-07) for each saved message, in order, in one background task (kept in _sends until it is
        done, so clear_memory can let it finish first)."""
        try:
            task = asyncio.get_running_loop().create_task(self._send_updates(list(saved), generation))
        except RuntimeError:  # no running loop: nothing to send with
            return
        _background_tasks.add(task)
        self._sends.add(task)
        task.add_done_callback(_background_tasks.discard)
        task.add_done_callback(self._sends.discard)

    async def _send_updates(self, saved: list[dict[str, Any]], generation: int = 0) -> None:
        for index, message in enumerate(saved):
            if self._clear_mark()[0] != generation:  # the file was cleared after this save (by any job)
                logger.debug("MEMORY %d memory_update(s) not sent: the memory file was cleared since",
                             len(saved) - index)
                return
            try:
                await notify_memory(message.get("content", ""), memory_id=message.get("id"),
                                    role=message.get("role"), source="extractor", user_id=self.user_id,
                                    ts=message.get("ts"))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.debug("memory_update not sent: %s", type(e).__name__)

    async def clear_memory(self, backup_dir: str | None = None) -> str | None:
        """
        K-60 clear_memory (C-51 move, C-52 forget): what this job said before the clear never reaches the fresh memory
        file. Under the extractor's lock, so no pass of the memory loop and no flush runs meanwhile:
        1. a save still running (its caller was cancelled, the save goes on) lands first, in the file that is moved;
        2. the memory_update sends of the saves before the clear finish (at most UPDATES_WAIT_S, the rest are
           cancelled), so none reaches the bridge after the memory_cleared that control_commands sends once this
           returns (the bridge empties its stored memories on memory_cleared and would keep a late update again);
        3. the messages the bound source holds now that are not saved yet (the router's turn_log: the newest exchange
           the 10 s loop has not read) and the pending ones (a failed save) are noted;
        4. ConversationMemory.clear moves the file to backups/memory (never a delete), empties its cache and advances
           the file's clear mark (so another job's extractor of the file drops what it collected before);
        5. the noted messages count as saved and nothing is pending, so the next pass writes only what is said after
           the clear (and sends no memory_update for the cleared ones).
        Returns the backup path, None when there was no file. A move that fails raises (OSError) and forgets nothing:
        the file, the cache and the unsaved messages stay as they were. A cancel during the move forgets them anyway
        (the move may still land). Logs counts only (K-29).
        """
        async with self._lock:
            inflight = self._inflight
            if inflight is not None and not inflight.done():
                await self._await_save(inflight)
            await self._wait_for_updates()
            unsaved = dict.fromkeys(message["id"] for message in self._candidates(await self._read_source())
                                    if message["id"] not in self._seen_ids)
            unsaved.update(dict.fromkeys(self._pending))
            try:
                backup = await (self.memory.clear(backup_dir) if backup_dir else self.memory.clear())
            except asyncio.CancelledError:
                self._forget(unsaved)
                raise
            self._forget(unsaved)
            self._read_generation = self._clear_mark()[0]  # the source was read just before the move: nothing new
            logger.info("MEMORY cleared for %s: %d message(s) of this job not saved yet are forgotten", self.user_id,
                        len(unsaved))
            return backup

    async def _wait_for_updates(self) -> None:
        """This extractor's memory_update sends still running finish, at most UPDATES_WAIT_S (the bridge's notify
        takes at most 2 s each); the ones still running then are cancelled."""
        sends = [task for task in self._sends if not task.done()]
        if not sends:
            return
        _done, late = await asyncio.wait(sends, timeout=UPDATES_WAIT_S)
        for task in late:
            task.cancel()
        if late:
            logger.debug("MEMORY %d memory_update send(s) still running at the clear were cancelled", len(late))

    def _forget(self, msg_ids: Iterable[str]) -> None:
        """The messages count as saved (they are never written) and nothing is pending any more."""
        for msg_id in msg_ids:
            self._remember(msg_id)
        self._pending.clear()
        self._collected.clear()

    def clear_context(self) -> None:
        """Resets the count of saved messages (the ids stay: a message is still never saved twice)."""
        self.conversation_count = 0
