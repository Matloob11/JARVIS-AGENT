"""
# jarvis_vector_memory.py
Long-Term Semantic Memory for JARVIS using ChromaDB.

C-137: the start-up (the Chroma client, the sentence-transformers import and model load, the
collection) runs in a background thread, never on an event loop. A cold sentence-transformers
import scans the metadata of every installed package (transformers' import_utils) and froze the
voice loop for 45 s on 2 Oct. prewarm() starts it without waiting, and the coroutines await it from
any event loop: a recall waits at most RECALL_WAIT_S (that turn then goes on without long-term
memories). A write made before the memory is ready is queued for the start-up thread, which makes it
once the memory is ready, so it is kept even when its job's event loop stops meanwhile.

prewarm() opens Chroma, which only the job serving jarvis-hybrid may do (K-69). warm_model() loads
only the model and never opens Chroma: it is the part a worker's prewarm hook may run.

C-124: on the VPS brain (JARVIS_DESKTOP_MODE=remote) with JARVIS_ENCRYPTION_KEY set, every document is
stored Fernet-encrypted ('jarvis-fernet:v1:' prefix); recall decrypts in every mode. The PC brain keeps
writing plain text as before; the embedding function decrypts before it embeds, so the vectors still
come from the plain text (they carry its meaning; the text itself is not on disk). A document without the
prefix (every memory written before, or without a key) is plain text and is recalled as it is; one that
does not decrypt (another key) is returned as stored, with a WARNING, never dropped. On POSIX the
process that opens the PersistentClient holds an exclusive fcntl lock on chroma_db/.jarvis.lock; another
process runs without vector recall. Windows keeps the single worker of the agent_ctl runtime lock.
"""

import asyncio
import concurrent.futures
import functools
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import (
    SentenceTransformerEmbeddingFunction as _ChromaSentenceTransformer,
)
from cryptography.fernet import Fernet, InvalidToken
from services.utils.jarvis_config import DESKTOP_MODE_REMOTE, config, desktop_mode
from services.utils.jarvis_logger import setup_logger

try:
    import fcntl  # POSIX only: the single Chroma opener (C-124)
except ImportError:  # Windows: one worker at a time is the agent_ctl runtime lock (K-53)
    fcntl = None  # type: ignore[assignment]

# Late-bound Phoenix import to avoid startup lag
_PX_CACHED = None


def get_px() -> Any:
    """Lazily load Arize Phoenix to improve startup performance."""
    global _PX_CACHED # pylint: disable=global-statement
    if _PX_CACHED is not None:
        return _PX_CACHED
    try:
        # pylint: disable=import-outside-toplevel
        import phoenix as px_mod
        _PX_CACHED = px_mod
        return _PX_CACHED
    except ImportError:
        _PX_CACHED = False  # Use False to avoid repeated import attempts
        return None
    except (AttributeError, RuntimeError) as e:
        logger.debug("Phoenix load skipped: %s", e)
        _PX_CACHED = False
        return None





# --- Aggressive Log Suppression ---
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Suppress noisy library logs
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("chromadb").setLevel(logging.ERROR)

# Setup logging
logger = setup_logger("JARVIS-VECTOR-MEMORY")


# --- Configuration ---
DB_PATH = config.chroma_db_dir
COLLECTION_NAME = "jarvis_memory"
MODEL_NAME = "all-MiniLM-L6-v2"
# A recall during the warm-up waits this long, then that turn goes on without long-term memories.
RECALL_WAIT_S = 3.0
ENCRYPTION_KEY_ENV = "JARVIS_ENCRYPTION_KEY"
ENC_PREFIX = "jarvis-fernet:v1:"
LOCK_FILE = ".jarvis.lock"
# After another process was found holding chroma_db, the next try waits this long.
LOCK_RETRY_S = 60.0
_warned: set[str] = set()


def _warn_once(kind: str, message: str) -> None:
    if kind not in _warned:
        _warned.add(kind)
        logger.warning(message)


@functools.lru_cache(maxsize=4)
def _fernet_for(key: str) -> Fernet | None:
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        _warn_once("bad_key", f"{ENCRYPTION_KEY_ENV} is not a valid Fernet key: vector memory documents "
                              "are stored without encryption")
        return None


def _cipher() -> Fernet | None:
    """The Fernet of JARVIS_ENCRYPTION_KEY, read on every call; None without a (valid) key."""
    key = (os.getenv(ENCRYPTION_KEY_ENV) or "").strip()
    return _fernet_for(key) if key else None


def encrypts_writes() -> bool:
    """True when new documents are stored encrypted: the VPS brain (remote mode) with a valid key."""
    return desktop_mode() == DESKTOP_MODE_REMOTE and _cipher() is not None


def seal_document(text: str) -> str:
    """The document as stored: encrypted with the prefix on the VPS brain with a key, else the text
    itself (the PC brain, as before)."""
    cipher = _cipher() if desktop_mode() == DESKTOP_MODE_REMOTE else None
    if cipher is None:
        return text
    return ENC_PREFIX + cipher.encrypt(text.encode("utf-8")).decode("ascii")


def open_document(stored: Any) -> Any:
    """The text of a stored document. Without the prefix it is plain text (memories from before C-124);
    a token that does not decrypt is returned as stored, so no memory is ever dropped."""
    if not isinstance(stored, str) or not stored.startswith(ENC_PREFIX):
        return stored
    cipher = _cipher()
    if cipher is None:
        _warn_once("no_key", f"Encrypted vector memories found but {ENCRYPTION_KEY_ENV} is not set")
        return stored
    try:
        return cipher.decrypt(stored[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        _warn_once("bad_token", "A vector memory does not decrypt with the current key; kept as stored")
        return stored


class SentenceTransformerEmbeddingFunction(_ChromaSentenceTransformer):
    """Chroma's sentence-transformers function that embeds the plain text of an encrypted document
    (C-124), so recall still matches meaning. Same name and config as Chroma's, so a collection made
    before keeps its embedding function."""

    def __call__(self, input: Any) -> Any:  # pylint: disable=redefined-builtin
        return super().__call__([open_document(text) for text in input])


def _running_future() -> concurrent.futures.Future[bool]:
    """A future set running at once, so a waiter's cancellation never cancels what it stands for."""
    future: concurrent.futures.Future[bool] = concurrent.futures.Future()
    future.set_running_or_notify_cancel()
    return future


def _done_future(result: bool) -> concurrent.futures.Future[bool]:
    """A future that already holds result."""
    future = _running_future()
    future.set_result(result)
    return future


def _wake(holder: list[asyncio.Future[None]], _: concurrent.futures.Future[bool]) -> None:
    """
    A background future's done callback, in its thread: resolves the waiter in holder on the
    waiter's own loop and lets go of it. A done concurrent future keeps its callbacks, so an emptied
    holder is what keeps a job's loop from staying alive after its job ended.
    """
    try:
        waiter = holder.pop()
    except IndexError:  # the waiter gave up first (timeout or cancel)
        return
    try:
        waiter.get_loop().call_soon_threadsafe(_resolve, waiter)
    except RuntimeError:  # its loop is closed (the job ended); a queued write is made regardless
        pass


def _resolve(waiter: asyncio.Future[None]) -> None:
    if not waiter.done():  # a waiter that timed out or was cancelled stays as it is
        waiter.set_result(None)


async def _await_future(future: concurrent.futures.Future[bool],
                        timeout: float | None = None) -> bool:
    """
    Awaits a future that a background thread resolves, without blocking the event loop and without
    ever cancelling it (other waiters may share it): its result, or False after timeout seconds.
    """
    if not future.done():
        waiter = asyncio.get_running_loop().create_future()
        # Held strongly while it waits, so an awaiting task is never garbage collected meanwhile.
        holder = [waiter]
        future.add_done_callback(functools.partial(_wake, holder))
        try:
            await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            return False
        finally:
            holder.clear()
    return bool(future.result())


class VectorMemory:
    """
    Handles vector-based semantic memory using ChromaDB with lazy initialization.
    """

    def __init__(self):
        self.client = None
        self.embedding_func = None
        self.collection = None
        # A thread lock and concurrent futures, not asyncio ones: LiveKit runs each job on its own
        # event loop (one thread per job on Windows) and all of them share this instance.
        self._init_lock = threading.Lock()
        self._init_future: concurrent.futures.Future[bool] | None = None
        self._model_future: concurrent.futures.Future[bool] | None = None
        # Held by the thread that loads the model, so the model loads once when both threads run.
        self._model_lock = threading.Lock()
        # Writes made before the memory is ready, for the start-up thread: (the write, its outcome).
        self._queued_writes: list[tuple[dict[str, Any], concurrent.futures.Future[bool]]] = []
        # C-124: the open chroma_db/.jarvis.lock (POSIX), held for the life of the process once taken;
        # until when a refused lock is not tried again; why this process never opens Chroma.
        self._lock_handle: Any = None
        self._lock_refused_until = 0.0
        self._disabled: str | None = None

    @property
    def warming_up(self) -> bool:
        """True while the background start-up runs."""
        future = self._init_future
        return future is not None and not future.done()

    @staticmethod
    def _start(name: str, target: Callable[[concurrent.futures.Future[bool]], None]
               ) -> concurrent.futures.Future[bool]:
        """target(future) in a new daemon thread called name; the future it resolves."""
        future = _running_future()
        try:
            threading.Thread(target=target, args=(future,), name=name, daemon=True).start()
        except RuntimeError as e:  # no new thread now (interpreter shutdown)
            logger.error("Vector Memory thread %s not started: %s", name, e)
            future.set_result(False)
        return future

    def prewarm(self) -> concurrent.futures.Future[bool]:
        """
        Begins the background start-up unless it is running or has succeeded, and returns its future:
        True once the memory is ready, False when the start-up failed (the next call tries again).
        Never blocks, so it is safe on an event loop. It opens Chroma, so only the job serving
        jarvis-hybrid calls it, never a worker's prewarm hook (K-69): that hook calls warm_model().
        """
        with self._init_lock:
            future = self._init_future
            if future is None or (future.done() and self.collection is None):
                if self._disabled is not None or time.monotonic() < self._lock_refused_until:
                    return _done_future(False)  # this process runs without vector recall (C-124)
                future = self._init_future = self._start("jarvis-vector-init", self._initialize)
            return future

    def disable(self, reason: str) -> None:
        """C-124: this process never opens Chroma (a job that does not serve jarvis-hybrid): recalls
        answer at once without memories and writes are skipped."""
        with self._init_lock:
            if self._disabled is None:
                self._disabled = reason
                logger.info("Vector memory off in this process: %s", reason)

    def _acquire_db_lock(self) -> bool:
        """
        C-124: the exclusive, non-blocking fcntl lock on chroma_db/.jarvis.lock, kept for the life of
        the process. True when this process holds it (always on Windows, where agent_ctl's runtime
        lock already allows a single worker); False when another process holds it.
        """
        if fcntl is None or self._lock_handle is not None:
            return True
        os.makedirs(DB_PATH, exist_ok=True)
        handle = open(os.path.join(DB_PATH, LOCK_FILE), "a+", encoding="utf-8")  # pylint: disable=consider-using-with
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._lock_handle = handle
        return True

    def warm_model(self) -> concurrent.futures.Future[bool]:
        """
        Loads only the embedding model (the sentence-transformers import and its model) in a
        background thread unless that is running or has succeeded, and returns its future: True once
        loaded. It never opens Chroma, so a worker's prewarm hook may call it in every process
        (K-69); the start-up then finds the model loaded. Never blocks.
        """
        with self._init_lock:
            future = self._model_future
            if future is None or (future.done() and not future.result()):
                future = self._model_future = self._start("jarvis-vector-model", self._warm_model)
            return future

    def _load_model(self) -> Any:
        """The embedding function, made once; a thread arriving during a load waits for it."""
        with self._model_lock:
            if self.embedding_func is None:
                self.embedding_func = SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)
            return self.embedding_func

    def _warm_model(self, future: concurrent.futures.Future[bool]) -> None:
        """warm_model() itself, in the jarvis-vector-model thread."""
        started = time.monotonic()
        loaded = False
        try:
            self._load_model()
            loaded = True
            logger.info("VECTOR_MODEL ready in %.1fs", time.monotonic() - started)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Vector Memory model not loaded: %s", e)
        finally:
            future.set_result(loaded)

    def _initialize(self, future: concurrent.futures.Future[bool]) -> None:
        """The start-up, in the jarvis-vector-init thread; then it makes the queued writes."""
        started = time.monotonic()
        client = collection = None
        try:
            logger.info("Initializing Vector Memory (background thread)...")
            if not self._acquire_db_lock():
                self._lock_refused_until = time.monotonic() + LOCK_RETRY_S
                logger.warning("VECTOR_LOCK held by another process: this process runs without vector "
                               "recall (next try in %.0f s)", LOCK_RETRY_S)
                return
            client = chromadb.PersistentClient(path=DB_PATH)
            embedding_func = self._load_model()  # at once when warm_model() has loaded it
            collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=embedding_func,
            )
            logger.info("VECTOR_PREWARM done in %.1fs", time.monotonic() - started)
        except Exception as e:
            # Client and collection stay None, so the next use tries again.
            logger.error("Failed to initialize Vector Memory: %s", e)
            collection = None
        finally:
            with self._init_lock:
                if collection is not None:
                    # The collection last: a reader that sees it also sees the client. From here on
                    # writes go straight to it, so the queue taken below is complete.
                    self.client, self.collection = client, collection
                else:
                    # With the queue, under the lock: a later write starts a new start-up.
                    future.set_result(False)
                queued, self._queued_writes = self._queued_writes, []
            try:
                if queued and collection is None:
                    logger.warning("Vector Memory not initialized. Skipping %d queued writes.",
                                   len(queued))
                for memory, outcome in queued:
                    outcome.set_result(collection is not None and self._write(collection, memory))
            finally:
                if collection is not None:
                    future.set_result(True)  # after the queued writes, so a recall then finds them

    async def _wait_ready(self, timeout: float | None = None) -> bool:
        """
        Awaits the start-up (starting it when needed) without blocking the event loop: True once the
        memory is ready, False when the start-up failed or still runs after timeout seconds.
        """
        if self.collection is not None:
            return True
        return await _await_future(self.prewarm(), timeout) and self.collection is not None

    async def _ensure_initialized(self) -> None:
        """Waits, without blocking the event loop, until the start-up has succeeded or failed."""
        await self._wait_ready()

    def _queue_write(self, memory: dict[str, Any]) -> concurrent.futures.Future[bool] | None:
        """
        Queues a write for the start-up thread, starting the start-up when needed: the write's
        outcome. None when the memory is ready (the caller then writes it itself).
        """
        self.prewarm()
        with self._init_lock:
            if self.collection is not None:
                return None
            outcome = _running_future()
            start = self._init_future
            if start is not None and not start.done():
                self._queued_writes.append((memory, outcome))
                return outcome
        # The start-up has just failed or could not start.
        logger.warning("Vector Memory not initialized. Skipping add_memory.")
        outcome.set_result(False)
        return outcome

    @staticmethod
    def _write(collection: Any, memory: dict[str, Any]) -> bool:
        """collection.add for one memory (blocking): True once it is stored."""
        try:
            collection.add(**memory)
            return True
        except Exception as e:
            logger.error("Error adding to Vector Memory: %s", e)
            return False

    async def add_memory(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        """Add a piece of text to the semantic memory asynchronously."""
        if not text or not text.strip():
            return False

        # C-124: stored encrypted on the VPS brain with JARVIS_ENCRYPTION_KEY; the embedding function
        # embeds the plain text (SentenceTransformerEmbeddingFunction above).
        memory = {"documents": [seal_document(text)], "metadatas": [metadata or {}],
                  "ids": [str(uuid.uuid4())]}
        queued = self._queue_write(memory)
        if queued is None:
            # collection.add is blocking, run in thread
            return await asyncio.to_thread(self._write, self.collection, memory)
        # The start-up thread makes the write once the memory is ready, so it is made even when this
        # job's loop stops or this caller is cancelled meanwhile; here we only await its outcome.
        return await _await_future(queued)

    async def query_memory(self, query_text: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        Search for relevant memories with semantic similarity scores.
        Returns: List[Dict] containing 'document' and normalized 'score' (0-1).
        """
        if not query_text:
            return []

        if not await self._wait_ready(RECALL_WAIT_S):
            if self.warming_up:
                logger.info("Vector Memory still warming up: this recall returns no memories")
            return []

        try:
            # collection.query is blocking, run in thread
            results = await asyncio.to_thread(
                self.collection.query,
                query_texts=[query_text],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )

            extracted = []
            if results and results.get("documents") and results["documents"][0]:
                for i in range(len(results["documents"][0])):
                    doc = open_document(results["documents"][0][i])
                    # Convert distance to a similarity score (0.0 to 1.0)
                    dist = results["distances"][0][i] if "distances" in results else 1.0
                    # Normalizing distance (L2 distance in ChromaDB)
                    score = round(max(0, 1.0 - (dist / 1.5)), 4)

                    extracted.append({
                        "document": doc,
                        "metadata": results["metadatas"][0][i] if "metadatas" in results else {},
                        "score": score,
                    })
            return extracted
        except Exception as e:
            logger.error("Error querying Vector Memory: %s", e)
            return []

    async def clear_memory(self) -> bool:
        """Wipes the entire memory collection. (USE WITH CAUTION)"""
        await self._ensure_initialized()
        try:
            if self.client:
                await asyncio.to_thread(self.client.delete_collection, name=COLLECTION_NAME)
                self.collection = await asyncio.to_thread(
                    self.client.create_collection,
                    name=COLLECTION_NAME,
                    embedding_function=self.embedding_func,
                )
                logger.info("🧹 Collection '%s' cleared.", COLLECTION_NAME)
                return True
            return False
        except Exception as e:
            logger.error("Error clearing Vector Memory: %s", e)
            return False

    async def get_count(self) -> int:
        """Returns number of items in the collection."""
        await self._ensure_initialized()
        try:
            if self.collection:
                return int(await asyncio.to_thread(self.collection.count))
            return 0
        except Exception as e:
            logger.error("Error counting DB: %s", e)
            return 0


# Global Instance
jarvis_vector_db = VectorMemory()

if __name__ == "__main__":
    # Small test runner
    async def _test() -> None:
        db_test = VectorMemory()
        await db_test.add_memory("Sir Matloob ka favourite color black hai.", {"user": "Matloob"})
        results = await db_test.query_memory("Matloob ko kaunsa color pasand hai?")
        print("Test Query:", results)

    asyncio.run(_test())
