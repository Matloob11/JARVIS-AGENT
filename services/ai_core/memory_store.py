"""
Jarvis Memory Store Module
Persistent storage for conversation history and user context.

C-52: one memory file per user, <conversations>/<user>_memory.json (K-69: JARVIS_USER_ID in every mode and brain),
encrypted with jarvis_crypto exactly as before: a JSON list of conversations {messages: [{id, role, content, ...}],
timestamp, user_id}.
- Every message has an id. MemoryExtractor saves the router's (C-42) or livekit's; a message saved without one gets
  message_id(), derived from its conversation's time stamp, its place in it, its role and its text, so the same legacy
  message always gets the same id. A message whose id is saved already is never written again, and a conversation is
  appended with its new messages only: two conversations with the same time stamp are no longer taken for duplicates
  (a reply saved in the same Windows clock tick as its question was lost), and a later conversation no longer
  replaces the last one.
- The newest CACHE_SIZE messages stay in a deque (read from the file once, then every save adds its messages); the old
  OrderedDict keyed by its own length overwrote entries once it was full.
- save_to_disk() is a real flush: it runs the flush hooks (MemoryExtractor.flush) and returns how many messages they
  wrote.
- The readers and writers of one file in this process share one lock (clear()'s move too), and a writer whose file
  changed after it read it (another job's ConversationMemory wrote or cleared it meanwhile) puts its new messages into
  the file as it is now instead of writing its stale copy over it.
- clear() (C-51: the move to backups/memory, its backup names and the copy across file systems) is unchanged but for
  that lock; a job's clear_memory runs it through MemoryExtractor.clear_memory.
- Every clear that worked advances the file's clear mark (clear_mark(): a generation and the clear's time, kept for the
  whole process, so every job's ConversationMemory of that file sees it), under the file's lock together with the
  move. MemoryExtractor notes the generation when it collects a message and never writes one collected (or said)
  before a newer clear; save_messages(generation=...) writes nothing when the file was cleared after that generation
  (a save that started before a clear by another job and lands after it, the merge path included).
- A save that fails is logged at ERROR once per failure streak (with its cause, never a content), later failures of
  the streak at DEBUG; the extractor logs the streak's end.
"""

import asyncio
import errno
import hashlib
import json
import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

try:
    from cryptography.fernet import InvalidToken
except ImportError:
    class _FallbackInvalidToken(Exception):
        """Fallback exception if cryptography is not installed."""
    InvalidToken = _FallbackInvalidToken  # type: ignore

from services.ai_core.jarvis_vector_memory import jarvis_vector_db
from services.utils.jarvis_config import config
from services.utils.jarvis_crypto import jarvis_crypto
from services.utils.jarvis_logger import setup_logger
from services.utils.probe_context import bridge_suppressed  # K-57 (C-39)

logger = setup_logger("JARVIS-MEMORY-STORE")

_background_tasks: set[asyncio.Task[Any]] = set()

# C-51 (K-60 clear_memory): a cleared memory file is moved here (<backups>/memory/<UTC ts>_<user>_memory.json)
MEMORY_BACKUP_SUBDIR = "memory"

# C-52: the newest saved messages kept in memory (get_recent_context answers from them without a decrypt)
CACHE_SIZE = 50
DERIVED_ID_PREFIX = "m-"  # message_id() of a message saved without an id

# C-52: one lock per memory file, shared by every ConversationMemory of this process (each job's assistant has its
# own instance, and on Windows every job is a thread of the same worker process). Each read and write of the file
# takes it; reentrant, because a write that finds the file changed reads it again under the same lock.
_file_locks: dict[str, threading.RLock] = {}
_file_locks_guard = threading.Lock()
# C-52 review: the clear mark of each memory file, (generation, time.time() of the newest clear), for the whole process
# (keyed like _file_locks, guarded by _file_locks_guard). A clear by one job's ConversationMemory reaches every other
# job's extractor of the same file through it.
_clear_marks: dict[str, tuple[int, float]] = {}
NEVER_CLEARED = (0, 0.0)


def memory_backup_dir() -> str:
    """<project_root>/backups/memory (config.backups_dir), where clear() keeps the previous memory file."""
    return os.path.join(config.backups_dir, MEMORY_BACKUP_SUBDIR)


def _remove_quietly(path: str) -> None:
    """Removes a leftover of a failed clear (its empty backup reservation or partial copy); never raises."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Leftover %s of a failed memory clear not removed: %s", os.path.basename(path),
                       type(e).__name__)


def _file_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _file_lock(path: str) -> threading.RLock:
    """The process-wide lock of one memory file."""
    key = _file_key(path)
    with _file_locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = _file_locks[key] = threading.RLock()
        return lock


def clear_mark(path: str) -> tuple[int, float]:
    """C-52: the clear mark of the memory file at path: (how many clears of it worked in this process, the time.time()
    of the newest one); NEVER_CLEARED (0, 0.0) when none did."""
    with _file_locks_guard:
        return _clear_marks.get(_file_key(path), NEVER_CLEARED)


def clear_generation(path: str) -> int:
    """C-52: how many clears of the memory file at path worked in this process (0: none)."""
    return clear_mark(path)[0]


def _advance_clear_mark(path: str) -> None:
    """A clear of the file worked (called under the file's lock, right after the move)."""
    key = _file_key(path)
    with _file_locks_guard:
        generation = _clear_marks.get(key, NEVER_CLEARED)[0]
        _clear_marks[key] = (generation + 1, time.time())


def _file_state(path: str) -> tuple[int, int, int] | None:
    """(modification time in ns, size, file id) of the file, None when it does not exist or cannot be read. Every
    write replaces the file (os.replace), so a write by anybody else changes it."""
    try:
        stat = os.stat(path)
    except (OSError, ValueError):
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def _fingerprint(record: Any) -> str:
    """A stable duplicate key for a whole record (sorted keys, str for anything else)."""
    return json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)


def message_id(message: dict[str, Any], timestamp: Any = None, index: int = 0) -> str:
    """
    The id of a saved message (C-52): its own 'id', else one derived from its conversation's time stamp, its place in
    that conversation, its role and its text. The same legacy message always gets the same id, and two different
    messages of one time stamp never share one.
    """
    own = message.get("id")
    if isinstance(own, str) and own:
        return own
    basis = json.dumps([timestamp, index, message.get("role"), message.get("content")], default=str,
                       ensure_ascii=False)
    return DERIVED_ID_PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def flatten_messages(conversations: Any) -> list[dict[str, Any]]:
    """Every message of the conversations in save order, each with its id (message_id() for one saved without; such
    a message is a copy, the loaded data stays as it was)."""
    messages: list[dict[str, Any]] = []
    for conversation in conversations if isinstance(conversations, list) else []:
        if not isinstance(conversation, dict) or not isinstance(conversation.get("messages"), list):
            continue
        stamp = conversation.get("timestamp")
        for index, message in enumerate(conversation["messages"]):
            if not isinstance(message, dict):
                continue
            own = message.get("id")
            messages.append(message if isinstance(own, str) and own
                            else {"id": message_id(message, stamp, index), **message})
    return messages


def saved_ids(conversations: Any) -> set[str]:
    """The ids of every saved message (derived ones included)."""
    return {message["id"] for message in flatten_messages(conversations)}


def _json_default(obj: Any) -> Any:
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


class ConversationMemory:
    """Handles persistent conversation memory for users (C-52: message ids, a deque of the newest messages)."""

    def __init__(self, user_id: str, storage_path: str | None = None):
        self.user_id = user_id
        self.storage_path = storage_path or config.conversations_dir
        self.memory_file = os.path.join(self.storage_path, f"{user_id}_memory.json")
        self.lock = asyncio.Lock()

        # C-52: the newest CACHE_SIZE saved messages, oldest first: read from the file once (_hydrated), then every
        # save adds its messages
        self.cache: deque[dict[str, Any]] = deque(maxlen=CACHE_SIZE)
        self._mem_cache_size = CACHE_SIZE
        self._hydrated = False
        self._read_state: tuple[int, int, int] | None = None  # the file as this instance last read or wrote it
        self._flush_hooks: list[Callable[[], Awaitable[Any]]] = []
        self._save_failures = 0  # failed saves in a row (the first of a streak is logged at ERROR, the rest at DEBUG)

        # Note: Directory is now ensured by jarvis_config singleton
        logger.info(
            "ConversationMemory initialized for user: %s (Cache: Enabled)", user_id)
        logger.info("Memory file path: %s", os.path.abspath(self.memory_file))

    async def load_memory(self) -> list[dict[str, Any]]:
        """Load conversation history from encrypted disk storage."""
        async with self.lock:
            return await self._load_memory_unlocked()

    async def _load_memory_unlocked(self) -> list[dict[str, Any]]:
        """Internal load without lock acquisition to safely call from other locked methods."""
        if not await asyncio.to_thread(os.path.exists, self.memory_file):
            self._read_state = None
            return []

        try:
            memory = await asyncio.to_thread(self._read_file)
            if not isinstance(memory, list):
                logger.error(
                    "Loaded memory is not a list for user %s. Resetting.", self.user_id)
                return []
            return memory
        except OSError:
            # C-52 verify (20 Oct): a file that cannot be read right now (another program holds it, access denied) is
            # not corrupt: it is never moved aside, and the caller's save fails and is retried (one log line a streak)
            self._read_state = None
            raise
        except (json.JSONDecodeError, Exception) as e:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Omega Corruption in User Memory %s: %s", self.user_id, e)
            # Backup corrupted file
            if await asyncio.to_thread(os.path.exists, self.memory_file):
                timestamp = int(datetime.now().timestamp())
                await asyncio.to_thread(
                    os.rename, self.memory_file, f"{self.memory_file}.corrupted_{timestamp}",
                )
            self._read_state = None
            return []

    def _read_file(self) -> Any:
        """The memory file's content (run in a thread): Fernet first, then a legacy plain JSON file; InvalidToken when
        it is neither. C-52: under the file's lock, and the file's state is noted as it was BEFORE the read, so a
        write in between counts as a change and is never missed."""
        with _file_lock(self.memory_file):
            state = _file_state(self.memory_file)
            with open(self.memory_file, 'rb') as f:
                data = f.read()

            # Phase 6: Check if data is encrypted or legacy JSON
            try:
                # Try decrypting
                if isinstance(data, (bytes, str)) and data:
                    decrypted = jarvis_crypto.decrypt(data)
                    loaded = json.loads(decrypted)
                else:
                    loaded = []
            except (ValueError, RuntimeError, InvalidToken, TypeError):
                # Fallback for legacy migration or key mismatch
                logger.warning(
                    "Decryption failed or invalid token. Attempting legacy JSON load or resetting.")

                try:
                    # Re-open in text mode for legacy JSON
                    with open(self.memory_file, encoding='utf-8') as f:
                        loaded = json.load(f)
                except Exception:  # pylint: disable=broad-exception-caught
                    # If it's truly undecryptable/corrupt, the outer exception handler will back it up
                    raise InvalidToken("Undecryptable memory") from None
            self._read_state = state
            return loaded

    async def clear(self, backup_dir: str | None = None) -> str | None:
        """
        C-51 (K-60 clear_memory): forgets this user's conversation history WITHOUT deleting it. The memory file is
        moved to <backup_dir>/<UTC ts>_<user>_memory.json (default backups/memory, created when missing; '-1', '-2'
        ... after the time stamp when that name is taken), still encrypted, and the cache is emptied. The backup name
        is reserved atomically before the move (O_CREAT | O_EXCL), so two clears in the same second, even by two
        ConversationMemory instances, never share it, and the move only ever replaces its own reservation, never an
        earlier backup. Where the backup folder is on another file system (the VPS brain keeps conversations and
        backups on separate volumes, K-75) the file is copied there in full first and only then removed. Returns the
        backup path, None when there was no file. A move that fails raises OSError and leaves the file and the cache
        as they were. C-52: the move holds the file's process lock (_move_to_backup), so another ConversationMemory's
        write of the same file (another job's) lands either before it (its messages go into the backup) or after it
        (it finds the file changed and writes only its own new messages, never its stale copy of the cleared file). A
        job's clear_memory goes through MemoryExtractor.clear_memory, which also forgets what the job has not written.
        A clear that worked (also one that found no file) advances the file's clear mark (clear_mark), so every other
        job's extractor of the file drops what it collected, or was said, before it.
        """
        target_dir = backup_dir or memory_backup_dir()
        async with self.lock:
            backup = await asyncio.to_thread(self._move_to_backup, target_dir)
            self.cache.clear()
            logger.info("🧠 Memory CLEARED for user: %s (backup %s)", self.user_id,
                        os.path.basename(backup) if backup else "none: no memory file")
            return backup

    def _move_to_backup(self, target_dir: str) -> str | None:
        """Moves the memory file into target_dir under a name reserved for it alone (an existing backup is never
        overwritten: os.replace only ever lands on this call's own empty reservation); None when there is no file.
        C-52: under the file's process lock, like every read and write of it (run in a thread); the clear mark advances
        under the same lock once the move worked (a move that fails raises first and leaves the mark as it was)."""
        with _file_lock(self.memory_file):
            target = self._move_file(target_dir)
            _advance_clear_mark(self.memory_file)
            return target

    def _move_file(self, target_dir: str) -> str | None:
        if not os.path.exists(self.memory_file):
            return None
        os.makedirs(target_dir, exist_ok=True)
        target = self._reserve_backup_name(target_dir)
        try:
            os.replace(self.memory_file, target)
        except OSError as e:
            if e.errno != errno.EXDEV:
                _remove_quietly(target)  # the empty reservation; the memory file stays as it was
                raise
            # Another file system: a complete copy under its final name first, the original removed only after it.
            partial = f"{target}.partial"
            try:
                shutil.copy2(self.memory_file, partial)
                os.replace(partial, target)
            except OSError:
                _remove_quietly(partial)
                _remove_quietly(target)
                raise
            os.remove(self.memory_file)
        return target

    def _reserve_backup_name(self, target_dir: str) -> str:
        """Creates <UTC ts>_<user>_memory.json in target_dir, empty and exclusively (O_CREAT | O_EXCL: of two clears
        in the same second only one gets a name), with '-1', '-2' ... after the time stamp while a name is taken."""
        name = os.path.basename(self.memory_file)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        suffix = 0
        while True:
            target = os.path.join(target_dir, f"{stamp}-{suffix}_{name}" if suffix else f"{stamp}_{name}")
            try:
                os.close(os.open(target, flags, 0o600))
            except FileExistsError:
                suffix += 1
                continue
            return target

    @staticmethod
    def _as_dict(conversation: Any) -> dict[str, Any]:
        """A conversation as a new dict (a pydantic model or an object with to_dict() too); the caller's stays as it
        was."""
        if hasattr(conversation, 'model_dump'):
            return dict(conversation.model_dump())
        if hasattr(conversation, 'to_dict'):
            return dict(conversation.to_dict())
        return dict(conversation)

    def _conversation_exists(
            self, new_conversation: dict[str, Any], existing_conversations: list[dict[str, Any]]) -> bool:
        """True when nothing of new_conversation is new (C-52): every message id of it is saved already, or (a record
        without messages) the same record is. Two different conversations with the same time stamp are never
        duplicates any more."""
        return self._new_entry(self._as_dict(new_conversation), existing_conversations)[0] is None

    def _conversation_fingerprint(self, conversation: dict[str, Any]) -> str:
        """Create a stable duplicate key for both session bundles and flat records."""
        return _fingerprint(conversation)

    @staticmethod
    def _new_entry(conversation: dict[str, Any],
                   memory: list[Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """(the entry to append, its messages): only the messages whose id is not saved yet, each with its id first
        (message_id() gives one to a message without); (None, []) when nothing is new. A record without a messages
        list (an old flat format) is kept once: an identical record means nothing is new."""
        messages = conversation.get("messages")
        if not isinstance(messages, list):
            fingerprint = _fingerprint(conversation)
            if any(_fingerprint(existing) == fingerprint for existing in memory):
                return None, []
            return conversation, []
        known = saved_ids(memory)
        stamp = conversation.get("timestamp")
        fresh: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            msg_id = message_id(message, stamp, index)
            if msg_id in known:
                continue
            known.add(msg_id)
            fresh.append({"id": msg_id, **{key: value for key, value in message.items() if key != "id"}})
        if not fresh:
            return None, []
        return {**conversation, "messages": fresh}, fresh

    async def save_conversation(self, conversation: dict | object) -> bool:
        """Atomic save; True when the conversation is on disk, also when every message of it was there already (C-52:
        each message is saved once, by its id; see save_messages)."""
        return await self._save(conversation) is not None

    async def save_messages(self, conversation: dict | object, *,
                            generation: int | None = None) -> list[dict[str, Any]] | None:
        """C-52: saves a conversation {messages, timestamp?, ...} in one encrypted write and returns the messages it
        wrote, each with its id (a message whose id is saved already is left out), [] when nothing was new, None when
        the write failed (logged). MemoryExtractor saves its bundles here. generation: the clear generation of the file
        (clear_generation) the messages were collected under; when a clear of the file worked after it (any job's, in
        this process), nothing is written and [] comes back: they were said before that clear."""
        return await self._save(conversation, generation)

    async def _save(self, conversation: Any, generation: int | None = None) -> list[dict[str, Any]] | None:
        if bridge_suppressed.get():
            return None  # K-57: a probe or canary job never writes memory
        async with self.lock:
            try:
                memory = await self._load_memory_unlocked()
                conversation_dict = self._as_dict(conversation)
                if 'timestamp' not in conversation_dict:
                    conversation_dict['timestamp'] = datetime.now().isoformat()
                entry, fresh = self._new_entry(conversation_dict, memory)
                if entry is None:
                    self._save_worked()
                    return []  # every message of it (or the same record) is saved already
                memory.append(entry)
                written = await asyncio.to_thread(self._write_encrypted, memory, entry, generation)
                self._save_worked()
                if written is False:
                    logger.info("Memory save of %d message(s) collected before the newest clear of the file dropped",
                                len(fresh))
                    return []
                self.cache.extend(fresh)
                if fresh:
                    # Vector DB Sync (Background task to avoid blocking)
                    t = asyncio.create_task(self._sync_to_vector_db(entry))
                    _background_tasks.add(t)
                    t.add_done_callback(_background_tasks.discard)
                return fresh
            except (AttributeError, TypeError, ValueError, KeyError, OSError, InvalidToken) as e:
                self._save_failed(type(e).__name__)  # K-29: never the content
                return None

    def _save_failed(self, error: str) -> None:
        """A failed save: ERROR for the first of a streak (a file locked by antivirus or sync software, a full disk
        or a permission fails every 10 s until it is fixed, and WARNING+ lines reach the UI log panel), then DEBUG."""
        self._save_failures += 1
        if self._save_failures == 1:
            logger.error("Error saving memory: %s (until a save works again, later failures are logged at DEBUG)",
                         error)
        else:
            logger.debug("Error saving memory: %s (%d failures in a row)", error, self._save_failures)

    def _save_worked(self) -> None:
        if self._save_failures:
            logger.debug("Memory saving works again after %d failed save(s)", self._save_failures)
        self._save_failures = 0

    def _write_encrypted(self, memory: list[Any], entry: dict[str, Any], generation: int | None = None) -> bool:
        """Encrypts and writes the memory list (run in a thread); False when it wrote nothing because a clear of the
        file worked after `generation` (C-52: checked under the file's lock, which the clear's move holds too). C-52:
        under the file's lock; when the file changed after this instance read it (another ConversationMemory of the
        same user wrote or cleared it meanwhile), the entry goes into the file as it is now (its messages that are not
        there yet), so neither write is lost."""
        with _file_lock(self.memory_file):
            if generation is not None and clear_generation(self.memory_file) != generation:
                return False
            if _file_state(self.memory_file) != self._read_state:
                memory = self._merged_into_current(entry)
                logger.info("Memory file of %s changed after it was read: the new messages went into it as it is now",
                            self.user_id)
            self._write_file(memory)
            return True

    def _merged_into_current(self, entry: dict[str, Any]) -> list[Any]:
        """The file's current conversations plus what of entry is new (under the file's lock)."""
        current = self._read_file() if os.path.exists(self.memory_file) else []
        if not isinstance(current, list):
            raise ValueError("the memory file holds no list")
        merged = self._new_entry(entry, current)[0]
        if merged is not None:
            current.append(merged)
        return current

    def _write_file(self, memory: list[Any]) -> None:
        """Phase 6: Encrypt and Sign. Written to this writer's own temporary file, then os.replace (atomic)."""
        json_data = json.dumps(memory, indent=2, ensure_ascii=False, default=_json_default)
        encrypted_data = jarvis_crypto.encrypt(json_data)
        temp_file = f"{self.memory_file}.{os.getpid()}-{threading.get_ident()}.tmp"
        try:
            with open(temp_file, 'wb') as f:
                f.write(encrypted_data)
            os.replace(temp_file, self.memory_file)
        except BaseException:
            try:
                os.remove(temp_file)
            except OSError:
                pass
            raise
        self._read_state = _file_state(self.memory_file)

    async def _sync_to_vector_db(self, conversation_dict: dict[str, Any]) -> None:
        """Sync messages to Vector DB in background coroutine."""
        try:
            if conversation_dict.get('messages'):
                for msg in conversation_dict['messages']:
                    content = msg.get('content', '')
                    role = msg.get('role', 'user')
                    if content and len(content) > 5:  # Skip short filler
                        # Call async vector DB directly
                        await jarvis_vector_db.add_memory(
                            text=content,
                            metadata={
                                "user_id": self.user_id,
                                "role": role,
                                "timestamp": conversation_dict.get('timestamp'),
                            },
                        )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Vector DB sync failed: %s", e)

    def add_flush_hook(self, hook: Callable[[], Awaitable[Any]]) -> None:
        """C-52: save_to_disk() runs hook (MemoryExtractor.flush registers itself) and counts what it wrote."""
        if hook not in self._flush_hooks:
            self._flush_hooks.append(hook)

    async def save_to_disk(self) -> int:
        """C-52: the real flush. Every save is written at once, so what is left is what an extractor collected and has
        not written yet: each flush hook runs (MemoryExtractor.flush). Returns how many messages they wrote (0 when
        nothing was pending); never raises."""
        written = 0
        for hook in list(self._flush_hooks):
            try:
                result = await hook()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("Memory flush failed (%s)", type(e).__name__)
                continue
            if isinstance(result, int) and not isinstance(result, bool):
                written += result
        return written

    async def get_recent_context(self, max_messages: int = 30) -> list[dict]:
        """The newest max_messages saved messages, oldest first, each with its id. C-52: from the deque of the newest
        CACHE_SIZE (read from the file once, then kept up to date by every save); more than that reads the file."""
        if max_messages <= 0:
            return []
        async with self.lock:
            if self._hydrated and max_messages <= CACHE_SIZE:
                logger.debug("Memory cache: %d recent messages", min(max_messages, len(self.cache)))
                return list(self.cache)[-max_messages:]
            # Use unlocked version to avoid deadlock
            memory = await self._load_memory_unlocked()
            all_messages = flatten_messages(memory)
            self.cache.clear()
            self.cache.extend(all_messages[-CACHE_SIZE:])
            self._hydrated = True
            recent_messages = all_messages[-max_messages:]
            logger.info(
                "Retrieved %d recent messages for user %s", len(recent_messages), self.user_id)
            return recent_messages

    async def get_conversation_count(self) -> int:
        """Get total number of saved conversations"""
        memory = await self.load_memory()
        return len(memory)

    async def clear_duplicates(self) -> int:
        """Removes duplicate conversations atomically under the lock. C-52: a conversation is a duplicate when every
        message id of it is in a kept one already (a record without messages: when it is the same record). Returns how
        many were removed."""
        memory = await self.load_memory()
        unique_conversations: list[Any] = []
        known: set[str] = set()
        records: set[str] = set()
        removed_count = 0

        for conv in memory:
            messages = conv.get("messages") if isinstance(conv, dict) else None
            if isinstance(messages, list):
                ids = saved_ids([conv])
                if ids and ids <= known:
                    removed_count += 1
                    continue
                known |= ids
            else:
                fingerprint = _fingerprint(conv)
                if fingerprint in records:
                    removed_count += 1
                    continue
                records.add(fingerprint)
            unique_conversations.append(conv)

        if removed_count > 0:
            async with self.lock:
                await asyncio.to_thread(self._write_unique, unique_conversations)
                logger.info("Removed %d duplicate conversations", removed_count)

        return removed_count

    def _write_unique(self, conversations: list[Any]) -> None:
        with _file_lock(self.memory_file):
            self._write_file(conversations)

    async def validate_integrity(self) -> bool:
        """Deep structural validation of the memory file."""
        try:
            memory = await self.load_memory()
            if not isinstance(memory, list):
                return False
            for conv in memory:
                if not isinstance(conv, dict) or "messages" not in conv:
                    return False
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def archive_stale_context(self, threshold_hours: int = 24) -> int:
        """
        Identify and 'archive' stale contexts by returning the count of old sessions.
        This helps the agent decide whether to start a fresh cognitive window.
        """
        memory = await self.load_memory()
        if not memory:
            return 0

        stale_count = 0
        now = datetime.now()
        for conv in memory:
            try:
                conv_time = datetime.fromisoformat(conv.get("timestamp", ""))
                if (now - conv_time).total_seconds() > (threshold_hours * 3600):
                    stale_count += 1
            except (ValueError, TypeError, AttributeError):
                continue

        logger.info("Memory Audit: Found %d stale conversation blocks (>%dh)",
                    stale_count, threshold_hours)
        return stale_count

    async def get_semantic_context(self, query: str, n_results: int = 3) -> list[dict[str, Any]]:
        """Search Long-Term Memory for semantically relevant information with scores."""
        logger.info("Semantic search initiated (%d chars)", len(query or ""))  # K-29: never the query itself
        # Vector DB query is now async
        return await jarvis_vector_db.query_memory(query, n_results)
