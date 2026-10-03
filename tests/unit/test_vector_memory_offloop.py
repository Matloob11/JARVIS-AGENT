"""Plan task C-137: the vector memory starts off the event loop; ensure_room.py prints no credentials.

On 2 Oct the first use of the vector memory imported sentence-transformers on the voice loop:
transformers' import_utils scanned the metadata of every installed package and the loop stood still
for 45 s. Here a fresh module as slow to import (a sleep, then pure-Python work that holds the GIL)
stands in for the model, and Chroma is an in-memory fake, so no test loads a model, opens the
project's chroma_db or reaches the network. The C-31 loop watchdog, with the agent's own thresholds,
measures the test's event loop meanwhile. warm_model() loads the model without opening Chroma (K-69),
and a write made during the warm-up is made by the start-up thread, whatever happens to its job's
loop. ensure_room.py runs against a fake LiveKit API that answers with real protobufs, under
throwaway credentials.
"""
# pylint: disable=protected-access
import asyncio
import gc
import importlib
import importlib.util
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import dotenv
import pytest
from livekit import api as livekit_api
from livekit.protocol import agent_dispatch, models
from livekit.protocol import room as livekit_room

from services.ai_core import jarvis_vector_memory as jvm
from services.utils import jarvis_loopwatch
from services.utils.jarvis_loopwatch import STALL_TAG, LoopWatchdog
from services.utils.jarvis_security import redact_secrets
from src.core import agent_runner

ROOT = Path(__file__).resolve().parents[2]
SLOW_SLEEP_S = 0.6  # the import's file and metadata reads: the GIL is free meanwhile
SLOW_BUSY_S = 0.2  # then pure-Python work that holds the GIL between switches
SETTLE_S = 0.3  # time for a late heartbeat to report afterwards
FAKE_KEY = "c137-fake-livekit-key-0123456789"
FAKE_SECRET = "c137-fake-livekit-secret-0123456789abcdef"
FAKE_TURN = "c137-fake-turn-password-0123456789"
FAKE_METADATA = "c137-private-room-metadata"
# Generated protobuf classes, whose members pylint cannot see.
Room = models.Room  # pylint: disable=no-member
ListRoomsResponse = livekit_room.ListRoomsResponse  # pylint: disable=no-member
AgentDispatch = agent_dispatch.AgentDispatch  # pylint: disable=no-member
ListParticipantsResponse = livekit_room.ListParticipantsResponse  # pylint: disable=no-member
ParticipantInfo = models.ParticipantInfo  # pylint: disable=no-member
C137_WORKER_ID = "AW_c137current"

SLOW_MODULE = '''"""C-137 test double for the sentence-transformers import: as slow, without a model."""
import threading
import time

IMPORTED_IN = threading.current_thread().name
time.sleep({sleep_s})
_until = time.perf_counter() + {busy_s}
while time.perf_counter() < _until:
    pass


class Embedding:
    """Stands in for SentenceTransformerEmbeddingFunction."""

    def __init__(self, model_name):
        self.model_name = model_name
'''


class _Records(logging.Handler):
    """Every record a logger receives while attached, from any thread."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.logger = logger
        logger.addHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self, tag: str = "") -> list[str]:
        return [record.getMessage() for record in self.records
                if record.getMessage().startswith(tag)]

    def close(self) -> None:
        self.logger.removeHandler(self)
        super().close()


@pytest.fixture(name="watch_log")
def _watch_log(request):
    """A private logger for one watchdog, and its records."""
    logger = logging.getLogger(f"tests.c137.{request.node.name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    collector = _Records(logger)
    yield collector
    collector.close()


@pytest.fixture(name="memory_log")
def _memory_log():
    collector = _Records(jvm.logger)
    yield collector
    collector.close()


class _FakeCollection:
    """The Chroma collection calls VectorMemory makes, in memory: recall ranks by shared words."""

    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any], str]] = []

    def add(self, documents: list[str], metadatas: list[dict[str, Any]], ids: list[str]) -> None:
        self.items.extend(zip(documents, metadatas, ids))

    def query(self, query_texts: list[str], n_results: int, include: list[str]) -> dict[str, Any]:
        assert include == ["documents", "metadatas", "distances"]
        words = set(query_texts[0].lower().split())
        ranked = sorted(self.items, key=lambda item: -len(words & set(item[0].lower().split())))
        ranked = ranked[:n_results]
        return {"documents": [[document for document, _, _ in ranked]],
                "metadatas": [[metadata for _, metadata, _ in ranked]],
                "distances": [[0.15 * rank for rank in range(len(ranked))]]}

    def count(self) -> int:
        return len(self.items)


class _FakeClient:
    def __init__(self, path: str) -> None:
        self.path = path
        self.collection = _FakeCollection()
        self.created_with: dict[str, Any] = {}

    def get_or_create_collection(self, name: str, embedding_function: Any) -> _FakeCollection:
        self.created_with = {"name": name, "embedding_function": embedding_function}
        return self.collection


@pytest.fixture(name="clients")
def _fake_chroma(monkeypatch, tmp_path) -> list[_FakeClient]:
    """Chroma replaced by _FakeClient and the database path by a temporary one; the clients made."""
    clients: list[_FakeClient] = []

    def persistent_client(path: str) -> _FakeClient:
        clients.append(_FakeClient(path))
        return clients[-1]

    monkeypatch.setattr(jvm, "chromadb", SimpleNamespace(PersistentClient=persistent_client))
    monkeypatch.setattr(jvm, "DB_PATH", str(tmp_path / "chroma_db"))
    return clients


def _write_slow_module(folder: Path) -> str:
    """A fresh module, never imported before, as slow to import as sentence-transformers."""
    name = f"c137_slow_embedding_{uuid.uuid4().hex[:8]}"
    source = SLOW_MODULE.format(sleep_s=SLOW_SLEEP_S, busy_s=SLOW_BUSY_S)
    (folder / f"{name}.py").write_text(source, encoding="utf-8")
    importlib.invalidate_caches()
    return name


@pytest.fixture(name="slow_embedding")
def _slow_embedding(monkeypatch, tmp_path):
    """
    SentenceTransformerEmbeddingFunction replaced by a factory that first imports a slow fresh module,
    as the real constructor imports sentence_transformers. Yields that module's name and the
    embeddings made.
    """
    folder = tmp_path / "modules"
    folder.mkdir()
    name = _write_slow_module(folder)
    monkeypatch.syspath_prepend(str(folder))
    made: list[Any] = []

    def factory(model_name: str) -> Any:
        made.append(importlib.import_module(name).Embedding(model_name))
        return made[-1]

    monkeypatch.setattr(jvm, "SentenceTransformerEmbeddingFunction", factory)
    yield SimpleNamespace(name=name, made=made)
    sys.modules.pop(name, None)


@pytest.fixture(name="gate")
def _gated_embedding(monkeypatch):
    """The start-up waits in the embedding until the yielded event is set (always set at the end)."""
    gate = threading.Event()

    def factory(model_name: str) -> Any:
        if not gate.wait(10.0):
            raise RuntimeError("c137 gate never opened")
        return SimpleNamespace(model_name=model_name)

    monkeypatch.setattr(jvm, "SentenceTransformerEmbeddingFunction", factory)
    yield gate
    gate.set()


# --------------------------------------------------------------------------- off the event loop


async def test_a_slow_start_up_import_never_stalls_the_event_loop(watch_log, memory_log, clients,
                                                                    slow_embedding):
    vm = jvm.VectorMemory()
    dog = LoopWatchdog(log=watch_log.logger, name="c137-offloop")  # the agent's thresholds
    dog.start()
    await asyncio.sleep(0.25)
    ticks = dog.ticks
    began = time.perf_counter()
    assert await vm.add_memory("Sir Matloob ka favourite color black hai.", {"user": "c137"}) is True
    took = time.perf_counter() - began
    beats = dog.ticks - ticks
    await asyncio.sleep(SETTLE_S)
    await dog.stop()

    # The slow import ran during that await, in the start-up thread, while the heartbeat went on.
    assert took >= SLOW_SLEEP_S
    assert sys.modules[slow_embedding.name].IMPORTED_IN == "jarvis-vector-init"
    assert beats >= 3
    assert watch_log.messages(STALL_TAG) == [] and dog.stall_count == 0
    assert len(clients) == 1 and clients[0].collection.count() == 1
    assert any(message.startswith("VECTOR_PREWARM done in ") for message in memory_log.messages())


async def test_the_same_import_on_the_event_loop_is_a_stall(watch_log, monkeypatch, slow_embedding):
    """The control: this measurement sees the 2 Oct stall when the import runs on the loop, as before."""
    # Never taken for a pause of the whole process on a loaded machine (see test_loopwatch.py).
    monkeypatch.setattr(jarvis_loopwatch, "_cpu_clock", time.monotonic)
    dog = LoopWatchdog(log=watch_log.logger, name="c137-onloop")
    dog.start()
    await asyncio.sleep(0.25)
    jvm.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")  # on the loop thread
    await asyncio.sleep(SETTLE_S)
    await dog.stop()

    assert sys.modules[slow_embedding.name].IMPORTED_IN == threading.current_thread().name
    assert dog.stall_count >= 1 and dog.max_stall_ms >= SLOW_SLEEP_S * 1000
    assert watch_log.messages(STALL_TAG)


def test_prewarm_needs_no_event_loop_and_starts_once(clients, slow_embedding):
    vm = jvm.VectorMemory()
    start = vm.prewarm()
    assert vm.prewarm() is start and vm.warming_up  # a second call joins the running start-up
    assert start.result(timeout=10) is True
    assert vm.prewarm() is start and not vm.warming_up
    assert len(clients) == 1 and len(slow_embedding.made) == 1
    assert sys.modules[slow_embedding.name].IMPORTED_IN == "jarvis-vector-init"


def test_warm_model_loads_only_the_model_and_never_opens_chroma(clients, slow_embedding, memory_log):
    """K-69: a worker's prewarm hook may load MiniLM in every process; only the job opens Chroma."""
    vm = jvm.VectorMemory()
    model = vm.warm_model()
    assert vm.warm_model() is model  # a second call joins the running load
    assert model.result(timeout=10) is True and vm.warm_model() is model
    assert sys.modules[slow_embedding.name].IMPORTED_IN == "jarvis-vector-model"
    assert clients == [] and vm.client is None and vm.collection is None
    assert vm._init_future is None and not vm.warming_up
    assert any(message.startswith("VECTOR_MODEL ready in ") for message in memory_log.messages())
    # The job's start-up then opens Chroma with that model and loads nothing again.
    assert vm.prewarm().result(timeout=10) is True
    assert len(slow_embedding.made) == 1 and len(clients) == 1
    assert clients[0].created_with["embedding_function"] is slow_embedding.made[0]


def test_a_start_up_during_the_model_load_waits_for_it_and_loads_the_model_once(clients,
                                                                                monkeypatch):
    entered, gate = threading.Event(), threading.Event()
    loads: list[str] = []

    def factory(model_name: str) -> Any:
        loads.append(threading.current_thread().name)
        entered.set()
        if not gate.wait(10.0):
            raise RuntimeError("c137 gate never opened")
        return SimpleNamespace(model_name=model_name)

    monkeypatch.setattr(jvm, "SentenceTransformerEmbeddingFunction", factory)
    vm = jvm.VectorMemory()
    try:
        model = vm.warm_model()
        assert entered.wait(10.0)
        start = vm.prewarm()  # its thread opens Chroma, then waits for the load under way
        assert not start.done() and vm.collection is None
    finally:
        gate.set()
    assert model.result(timeout=10) is True and start.result(timeout=10) is True
    assert loads == ["jarvis-vector-model"] and len(clients) == 1
    assert clients[0].created_with["embedding_function"] is vm.embedding_func


def test_a_failed_model_load_is_logged_and_the_start_up_loads_it_again(clients, memory_log,
                                                                      monkeypatch):
    attempts: list[str] = []

    def factory(model_name: str) -> Any:
        attempts.append(threading.current_thread().name)
        if len(attempts) == 1:
            raise OSError("c137 model files missing")
        return SimpleNamespace(model_name=model_name)

    monkeypatch.setattr(jvm, "SentenceTransformerEmbeddingFunction", factory)
    vm = jvm.VectorMemory()
    assert vm.warm_model().result(timeout=10) is False
    assert "Vector Memory model not loaded: c137 model files missing" in memory_log.messages()
    assert vm.embedding_func is None and clients == []
    assert vm.prewarm().result(timeout=10) is True
    assert attempts == ["jarvis-vector-model", "jarvis-vector-init"] and len(clients) == 1
    # A later warm_model() reports the model loaded, without loading it again.
    assert vm.warm_model().result(timeout=10) is True and len(attempts) == 2


def test_importing_the_module_starts_nothing_and_loads_no_model():
    probe = (
        "import sys, threading\n"
        "before = {thread.ident for thread in threading.enumerate()}\n"
        "import services.ai_core.jarvis_vector_memory as memory\n"
        "print('NEW_THREADS', [thread.name for thread in threading.enumerate()"
        " if thread.ident not in before])\n"
        "print('HEAVY', sorted(name for name in ('torch', 'transformers', 'sentence_transformers')"
        " if name in sys.modules))\n"
        "print('IDLE', memory.jarvis_vector_db._init_future is None"
        " and memory.jarvis_vector_db._model_future is None)\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            capture_output=True, encoding="utf-8", errors="replace", timeout=240,
                            check=False)
    lines = [line for line in result.stdout.splitlines()
             if line.startswith(("NEW_THREADS", "HEAVY", "IDLE"))]
    assert lines == ["NEW_THREADS []", "HEAVY []", "IDLE True"], redact_secrets(result.stderr[-1500:])


# --------------------------------------------------------------------------- memory still works


async def test_memory_still_writes_and_recalls_after_the_off_loop_start(clients, slow_embedding):
    vm = jvm.VectorMemory()
    # Writes made while the memory starts go to the start-up thread: none is lost, and it starts once.
    writes = await asyncio.gather(
        vm.add_memory("Sir Matloob ka favourite color black hai.", {"user": "c137"}),
        vm.add_memory("JARVIS runs on the Dell laptop.", {"user": "c137"}),
        vm.add_memory("The VPS hosts the bridge.", {"user": "c137"}),
    )
    assert writes == [True, True, True]
    assert len(clients) == 1 and len(slow_embedding.made) == 1
    client, embedding = clients[0], slow_embedding.made[0]
    assert client.path == jvm.DB_PATH and embedding.model_name == "all-MiniLM-L6-v2"
    assert client.created_with == {"name": jvm.COLLECTION_NAME, "embedding_function": embedding}
    assert vm.client is client and vm.collection is client.collection and vm.embedding_func is embedding

    hits = await vm.query_memory("favourite color", n_results=2)
    assert hits[0] == {"document": "Sir Matloob ka favourite color black hai.",
                       "metadata": {"user": "c137"}, "score": 1.0}
    assert len(hits) == 2 and await vm.get_count() == 3


async def test_a_recall_during_the_warm_up_answers_at_once_without_memories(clients, gate, memory_log,
                                                                            monkeypatch):
    monkeypatch.setattr(jvm, "RECALL_WAIT_S", 0.2)
    vm = jvm.VectorMemory()
    write = asyncio.create_task(vm.add_memory("Sir Matloob ka favourite color black hai."))
    began = time.perf_counter()
    assert await vm.query_memory("favourite color") == []
    waited = time.perf_counter() - began
    assert 0.15 <= waited < 3.0 and vm.warming_up and not write.done()
    assert "Vector Memory still warming up: this recall returns no memories" in memory_log.messages()

    gate.set()
    assert await asyncio.wait_for(write, 10) is True  # the write made during the warm-up is kept
    hits = await vm.query_memory("favourite color")
    assert [hit["document"] for hit in hits] == ["Sir Matloob ka favourite color black hai."]
    assert len(clients) == 1


async def test_a_failed_start_is_logged_and_tried_again_at_the_next_use(clients, memory_log,
                                                                       monkeypatch):
    attempts: list[str] = []

    def factory(model_name: str) -> Any:
        attempts.append(model_name)
        if len(attempts) == 1:
            raise OSError("c137 model files missing")
        return SimpleNamespace(model_name=model_name)

    monkeypatch.setattr(jvm, "SentenceTransformerEmbeddingFunction", factory)
    vm = jvm.VectorMemory()
    assert await vm.add_memory("first try") is False
    assert vm.client is None and vm.collection is None and not vm.warming_up
    assert "Failed to initialize Vector Memory: c137 model files missing" in memory_log.messages()
    # The next use, here a recall, starts it again; this time it succeeds (the store is still empty).
    assert await vm.query_memory("first try") == []
    assert len(attempts) == 2 and len(clients) == 2 and vm.collection is clients[1].collection
    assert await vm.add_memory("second try") is True
    assert len(attempts) == 2 and vm.collection.count() == 1


async def test_a_cancelled_waiter_never_cancels_the_shared_start_up(clients, gate):
    vm = jvm.VectorMemory()
    waiter = asyncio.create_task(vm._ensure_initialized())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    start = vm._init_future
    assert start is not None and not start.cancel() and vm.warming_up
    gate.set()
    assert await vm._wait_ready() is True and start.result() is True and len(clients) == 1


async def test_a_write_nobody_holds_survives_the_warm_up(clients, gate):
    """asyncio keeps only weak references to tasks: the start-up holds a waiting write strongly."""
    vm = jvm.VectorMemory()
    written = asyncio.Event()

    async def write() -> None:
        if await vm.add_memory("kept while it waits"):
            written.set()

    asyncio.get_running_loop().create_task(write())  # no reference kept, on purpose
    await asyncio.sleep(0.05)
    gc.collect()
    gate.set()
    await asyncio.wait_for(written.wait(), 10)
    assert clients[0].collection.count() == 1


async def test_a_write_is_made_even_when_its_caller_is_cancelled_during_the_warm_up(clients, gate):
    vm = jvm.VectorMemory()
    write = asyncio.create_task(vm.add_memory("kept though its caller gave up"))
    await asyncio.sleep(0.05)
    write.cancel()
    with pytest.raises(asyncio.CancelledError):
        await write
    gate.set()
    # The start-up resolves after the writes queued during it.
    assert await asyncio.to_thread(vm._init_future.result, 10) is True
    assert [document for document, _, _ in clients[0].collection.items] == [
        "kept though its caller gave up"]


def _abandon(loop: asyncio.AbstractEventLoop) -> None:
    """Closes a loop whose tasks stay pending for good, as at a real job end, without warnings."""
    for task in asyncio.all_tasks(loop):
        task._log_destroy_pending = False
    loop.close()


@pytest.mark.parametrize("closed_early", [True, False], ids=["loop-closed", "loop-only-stopped"])
def test_a_write_left_waiting_by_an_ended_job_is_still_made(clients, gate, closed_early):
    """
    LiveKit's THREAD executor (the Windows default) ends a job by stopping its loop with its tasks
    still pending (livekit-agents 1.4.6 never closes it; it may be closed later or never). The write
    queued during the warm-up is made all the same once the memory is ready, with no error logged and
    nothing of that job kept alive, and the next job, on a loop of its own, finds the memory ready.
    """
    callback_errors = _Records(logging.getLogger("concurrent.futures"))
    vm = jvm.VectorMemory()
    loop = asyncio.new_event_loop()

    async def job() -> None:
        loop.create_task(vm.add_memory("said as the job ended"))
        await asyncio.sleep(0.1)

    try:
        job_thread = threading.Thread(target=loop.run_until_complete, args=(job(),))
        job_thread.start()
        job_thread.join(5.0)
        assert not job_thread.is_alive() and vm.warming_up
        ended_loop = weakref.ref(loop)
        stopped = [] if closed_early else [loop]  # a loop only stopped is closed after the start-up
        if closed_early:
            _abandon(loop)
        del loop, job, job_thread
        gate.set()
        assert vm._init_future.result(timeout=10) is True
        while stopped:
            _abandon(stopped.pop())
        gc.collect()
        assert ended_loop() is None  # the done start-up keeps none of the ended job's objects
        assert asyncio.run(vm.add_memory("said in the next job")) is True
    finally:
        callback_errors.close()
    assert [record.getMessage() for record in callback_errors.records] == []
    assert [document for document, _, _ in clients[0].collection.items] == [
        "said as the job ended", "said in the next job"]


# --------------------------------------------------------------------------- agent_runner


async def test_the_entrypoint_starts_the_vector_memory_right_after_connecting(monkeypatch):
    order: list[str] = []

    async def connect() -> None:
        order.append("connect")

    async def next_step() -> dict[str, str]:
        order.append("datetime")
        raise asyncio.CancelledError  # the job ends here; nothing after it is needed

    monkeypatch.setattr(jvm.jarvis_vector_db, "prewarm", lambda: order.append("prewarm"))
    monkeypatch.setattr(agent_runner, "redact_root_handlers", lambda: None)
    monkeypatch.setattr(agent_runner, "get_formatted_datetime", next_step)
    monkeypatch.setattr(agent_runner, "_cleanup_session_resources", AsyncMock())
    ctx = MagicMock()
    ctx.connect = connect
    await agent_runner.entrypoint(ctx)
    assert order == ["connect", "prewarm", "datetime"]


def test_the_prewarm_hook_never_fails_the_job(monkeypatch):
    def no_thread() -> None:
        raise RuntimeError("c137 no thread")

    monkeypatch.setattr(jvm.jarvis_vector_db, "prewarm", no_thread)
    records = _Records(agent_runner.logger)
    try:
        agent_runner._prewarm_vector_memory()
    finally:
        records.close()
    assert records.messages() == ["VECTOR_PREWARM not started: c137 no thread"]


# --------------------------------------------------------------------------- ensure_room.py


def _load_ensure_room(monkeypatch, tmp_path: Path) -> Any:
    """deploy/windows-local/ensure_room.py as a module: throwaway settings, no .env read, no sleeps, run/
    under tmp_path (never the live worker's) and a current worker that started a minute ago."""
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.c137.invalid")
    monkeypatch.setenv("LIVEKIT_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", FAKE_SECRET)
    monkeypatch.setenv("JARVIS_ROOM_NAME", "c137-room")
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    spec = importlib.util.spec_from_file_location(
        "c137_ensure_room", ROOT / "deploy" / "windows-local" / "ensure_room.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=AsyncMock(), run=asyncio.run))
    monkeypatch.setattr(module, "current_worker", lambda: module.Worker(time.time() - 60, C137_WORKER_ID))
    return module


def _fake_livekit(monkeypatch, *, rooms: list[Room], dispatches=(), participants=(),
                  dispatch_error: Exception | None = None,
                  create_dispatch_error: Exception | None = None) -> SimpleNamespace:
    """LiveKitAPI replaced: the calls ensure_room.py makes, answered with real protobufs. Returns the
    room and agent_dispatch services, whose AsyncMocks record the calls."""
    room_svc = SimpleNamespace(
        delete_room=AsyncMock(),
        create_room=AsyncMock(),
        list_rooms=AsyncMock(return_value=ListRoomsResponse(rooms=rooms)),
        list_participants=AsyncMock(return_value=ListParticipantsResponse(participants=list(participants))))
    dispatch_svc = SimpleNamespace(
        list_dispatch=AsyncMock(side_effect=dispatch_error, return_value=list(dispatches)),
        create_dispatch=AsyncMock(side_effect=create_dispatch_error))

    class FakeLiveKitAPI:
        def __init__(self, url: str, key: str, secret: str) -> None:
            assert (url, key, secret) == ("https://livekit.c137.invalid", FAKE_KEY, FAKE_SECRET)
            self.room = room_svc
            self.agent_dispatch = dispatch_svc
            self.aclose = AsyncMock()

    monkeypatch.setattr(livekit_api, "LiveKitAPI", FakeLiveKitAPI)
    return SimpleNamespace(room=room_svc, agent_dispatch=dispatch_svc)


def _room(name: str) -> Room:
    return Room(name=name, sid="RM_c137", empty_timeout=3600, departure_timeout=3600,
                max_participants=10, num_participants=1, turn_password=FAKE_TURN,
                metadata=FAKE_METADATA)


def _person() -> ParticipantInfo:
    joined = time.time() - 600
    return ParticipantInfo(identity="c137-owner-browser", kind=ParticipantInfo.Kind.STANDARD,
                           state=ParticipantInfo.State.ACTIVE, joined_at=int(joined),
                           joined_at_ms=int(joined * 1000))


def _assert_no_credentials(out: str) -> None:
    for secret in (FAKE_TURN, "turn_password", FAKE_KEY, FAKE_SECRET, FAKE_METADATA, C137_WORKER_ID,
                   "c137-owner-browser"):
        assert secret not in out


async def test_ensure_room_prints_the_room_without_credentials(monkeypatch, capsys, tmp_path):
    module = _load_ensure_room(monkeypatch, tmp_path)
    room = _room(module.ROOM_NAME)
    # What the old print showed: the whole protobuf, TURN password included.
    assert FAKE_TURN in str(ListRoomsResponse(rooms=[room]))
    # The owner's UI is in the room, the dispatch has no live job, and adding one fails with an error that
    # quotes the credentials: the room is listed by chosen fields only and the error is masked.
    fake = _fake_livekit(monkeypatch, rooms=[room], participants=[_person()],
                         dispatches=[AgentDispatch(id="AD_c137", room=module.ROOM_NAME)],
                         create_dispatch_error=RuntimeError(
                             f"unauthenticated: key {FAKE_KEY} secret {FAKE_SECRET}"))
    await module.ensure_room()

    out = capsys.readouterr().out
    _assert_no_credentials(out)
    assert ("[ROOM] Listed: name=c137-room sid=RM_c137 empty_timeout=3600s departure_timeout=3600s "
            "agent_dispatch=yes (1)") in out
    assert "[ROOM] create_dispatch note: unauthenticated: key *** secret ***" in out
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out
    fake.agent_dispatch.create_dispatch.assert_awaited_once()
    fake.room.delete_room.assert_not_awaited()  # a person is in the room: never deleted
    fake.room.create_room.assert_not_awaited()


async def test_ensure_room_without_a_dispatch_list_or_without_the_room(monkeypatch, capsys, tmp_path):
    module = _load_ensure_room(monkeypatch, tmp_path)
    fake = _fake_livekit(monkeypatch, rooms=[_room(module.ROOM_NAME)], participants=[_person()],
                         dispatch_error=RuntimeError(f"404 not found for key {FAKE_KEY}"))
    await module.ensure_room()
    out = capsys.readouterr().out
    _assert_no_credentials(out)
    assert "agent_dispatch=unknown" in out and "[ROOM] list_dispatch note: 404 not found for key ***" in out
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out
    fake.agent_dispatch.create_dispatch.assert_not_awaited()
    fake.room.delete_room.assert_not_awaited()

    fake = _fake_livekit(monkeypatch, rooms=[])
    await module.ensure_room()
    out = capsys.readouterr().out
    _assert_no_credentials(out)
    assert "[ROOM] Listed: room 'c137-room' not found" in out and "[OK]" not in out
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out
    fake.room.create_room.assert_awaited_once()
    fake.room.delete_room.assert_not_awaited()
