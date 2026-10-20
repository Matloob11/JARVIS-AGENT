import pytest
import os
import shutil
from unittest.mock import MagicMock, patch, AsyncMock
from jarvis_vector_memory import VectorMemory
from memory_store import ConversationMemory

@pytest.fixture
def mock_chroma():
    with patch("chromadb.PersistentClient") as mock_client:
        with patch("jarvis_vector_memory.SentenceTransformerEmbeddingFunction") as mock_ef:
            mock_ef.return_value = MagicMock()
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_collection = MagicMock()
            mock_instance.get_or_create_collection.return_value = mock_collection
            yield mock_instance, mock_collection

@pytest.mark.asyncio
async def test_vector_memory_initialization(mock_chroma):
    vm = VectorMemory()
    await vm._ensure_initialized()
    assert vm.client is not None
    assert vm.collection is not None

@pytest.mark.asyncio
async def test_vector_memory_add_memory(mock_chroma):
    mock_client, mock_collection = mock_chroma
    vm = VectorMemory()
    await vm._ensure_initialized()

    await vm.add_memory("test memory", {"source": "test"})
    assert mock_collection.add.called

@pytest.mark.asyncio
async def test_vector_memory_search(mock_chroma):
    mock_client, mock_collection = mock_chroma
    mock_collection.query.return_value = {
        "documents": [["result 1"]],
        "distances": [[0.1]],
        "metadatas": [[{"source": "test"}]]
    }

    vm = VectorMemory()
    await vm._ensure_initialized()

    results = await vm.query_memory("query")
    assert len(results) == 1
    assert results[0]["document"] == "result 1"
    assert results[0]["score"] > 0

@pytest.mark.asyncio
async def test_conversation_memory_save_load():
    user_id = "test_user"
    mem = ConversationMemory(user_id=user_id, storage_path="test_conversations")
    conversation = {"id": "1", "messages": [{"role": "user", "content": "hello"}]}

    with patch.object(mem, "_load_memory_unlocked", new_callable=AsyncMock) as mock_load_unlocked:
        with patch.object(mem, "_sync_to_vector_db", new_callable=AsyncMock):
            with patch("asyncio.to_thread", new_callable=AsyncMock): # Mock disk write
                mock_load_unlocked.return_value = []
                success = await mem.save_conversation(conversation)
                assert success is True

                mock_load_unlocked.return_value = [conversation]
                history = await mem.load_memory()
                assert len(history) == 1
                assert history[0]["id"] == "1"


# ================================================================ C-52: memory core
# Every test keeps its memory file in a temporary folder (tmp_path) with fake messages; memory_update never reaches a
# bridge and the vector store is never touched.

import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
import types  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from livekit.agents import llm  # noqa: E402
from livekit.agents.voice import ConversationItemAddedEvent  # noqa: E402

from services.ai_core import agent_memory  # noqa: E402
from services.ai_core import memory_store as ms  # noqa: E402
from services.ai_core.agent_memory import MemoryExtractor  # noqa: E402
from services.utils import jarvis_bridge, probe_context  # noqa: E402
from services.utils.jarvis_crypto import jarvis_crypto  # noqa: E402

USER = "c52-test"


@pytest.fixture(autouse=True)
def _reseed_switched_on(monkeypatch):
    """The re-seed is off in production since 20 Oct (session_policy.RESEED_ENABLED, contracts v43); the C-52
    re-seed tests here keep its rules checked."""
    from src.core import session_policy as sp  # pylint: disable=import-outside-toplevel
    monkeypatch.setattr(sp, "RESEED_ENABLED", True)


@pytest.fixture
def notified(monkeypatch):
    """Every memory_update the extractor sends (agent_memory.notify_memory), as {content, memory_id, role, ...}."""
    calls: list[dict] = []

    async def notify(content, **kwargs):
        calls.append({"content": content, **kwargs})

    monkeypatch.setattr(agent_memory, "notify_memory", notify)
    return calls


@pytest.fixture
def store(tmp_path, monkeypatch):
    memory = ms.ConversationMemory(USER, storage_path=str(tmp_path))
    monkeypatch.setattr(memory, "_sync_to_vector_db", AsyncMock())  # never the vector store
    return memory


@pytest.fixture
def records():
    """Every log record (any logger), with its formatted message (and in .entries with its logger and level)."""
    import logging

    class _Records(logging.Handler):
        def __init__(self) -> None:
            super().__init__(logging.DEBUG)
            self.messages: list[str] = []
            self.entries: list[tuple[str, int, str]] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())
            self.entries.append((record.name, record.levelno, record.getMessage()))

    handler = _Records()
    loggers = [logging.getLogger(name) for name in ("JARVIS-SESSION-POLICY", "JARVIS-MEMORY-EXTRACTOR",
                                                    "JARVIS-MEMORY-STORE", "AGENT-RUNNER")]
    for logger in loggers:
        logger.addHandler(handler)
    yield handler
    for logger in loggers:
        logger.removeHandler(handler)


def _extractor(memory, **kwargs) -> MemoryExtractor:
    extractor = MemoryExtractor(USER, **kwargs)
    extractor.memory = memory
    memory.add_flush_hook(extractor.flush)  # as its own ConversationMemory gets it
    return extractor


def _turn(i: int, role: str | None = None, **extra) -> dict:
    """A router.turn_log entry (C-42) with a fake text."""
    entry = {"id": f"c52-{i:03d}", "role": role or ("user" if i % 2 == 0 else "assistant"), "text": f"baat {i}",
             "source": "voice", "ts": 1_760_000_000.0 + i}
    entry.update(extra)
    return entry


async def _saved_ids(memory) -> list[str]:
    return [message["id"] for message in ms.flatten_messages(await memory.load_memory())]


async def _settle() -> None:
    """The background memory_update sends finish."""
    pending = agent_memory._background_tasks  # pylint: disable=protected-access
    while pending:
        await asyncio.gather(*list(pending), return_exceptions=True)


class _NextJob:
    """What session_policy.reseed_chat_ctx touches on the next job's assistant before its first session.start."""

    def __init__(self, memory, persona: str | None = None) -> None:
        self._chat_ctx = llm.ChatContext.empty()
        self._activity = None
        self.memory_extractor = types.SimpleNamespace(memory=memory)
        if persona:
            self.persona_manager = types.SimpleNamespace(active_persona_name=persona)

    @property
    def chat_ctx(self):
        return self._chat_ctx

    async def update_chat_ctx(self, ctx) -> None:
        self._chat_ctx = ctx.copy()

    def texts(self) -> list[str]:
        return [item.text_content for item in self._chat_ctx.items][1:]  # after the '[MEMORY]' note


def _router(assistant):
    from src.core.conversation_router import ConversationRouter

    return ConversationRouter(assistant, notify=AsyncMock(), reason=AsyncMock(return_value={}),
                              notify_reasoning=AsyncMock())


def _say(router, role: str, text: str) -> None:
    router.on_conversation_item_added(ConversationItemAddedEvent(item=llm.ChatMessage(role=role, content=[text])))


# ---------------------------------------------------------------- step 9


async def test_a_file_that_cannot_be_read_now_is_never_moved_aside_and_the_save_waits(store, notified, monkeypatch):
    """C-52 verify (20 Oct): a read refused by another program (PermissionError) is not corruption: the memory file
    stays where it is, no '.corrupted_' copy appears, and the save fails (its messages stay pending) instead of
    writing a fresh file over the old one."""
    assert await store.save_messages({"messages": [_turn(0)]})
    path = Path(store.memory_file)
    before = path.read_bytes()

    def refused(self):
        raise PermissionError(13, "the file is in use")

    monkeypatch.setattr(ms.ConversationMemory, "_read_file", refused)
    with pytest.raises(PermissionError):
        await store.load_memory()
    assert await store.save_messages({"messages": [_turn(1)]}) is None
    assert path.read_bytes() == before
    assert not list(path.parent.glob(path.name + ".corrupted_*"))


async def test_more_than_20_messages_are_all_saved_in_order(store, notified):
    """Before C-52 the extractor indexed into a 20-item window and stopped after the 20th message."""
    extractor = _extractor(store)
    log: list[dict] = []
    for i in range(30):
        log.append(_turn(i))
        if i % 4 == 3:
            await extractor.run(log[-20:])  # even the old loop's sliding 20-item window misses nothing now
    await extractor.run(log[-20:])
    await _settle()
    expected = [f"c52-{i:03d}" for i in range(30)]
    assert await _saved_ids(store) == expected
    assert [m["content"] for m in ms.flatten_messages(await store.load_memory())] == [f"baat {i}" for i in range(30)]
    assert extractor.conversation_count == 30
    assert [call["memory_id"] for call in notified] == expected  # one memory_update per saved message, in order


async def test_each_message_is_saved_once_by_its_id(store, notified, tmp_path):
    extractor = _extractor(store)
    items = [_turn(0), _turn(1)]
    assert await extractor.run(items) == 2
    assert await extractor.run(items + [_turn(1)]) == 0  # seen already
    assert len(await store.load_memory()) == 1  # one bundle, one write
    # the next job's extractor knows the saved ids from the file and writes only the new message
    other = _extractor(ms.ConversationMemory(USER, storage_path=str(tmp_path)))
    assert await other.run(items + [_turn(2)]) == 1
    # and the store itself drops an id that is on disk already (never a second copy)
    assert await store.save_messages({"messages": [{"id": "c52-000", "role": "user", "content": "baat 0"}]}) == []
    assert await _saved_ids(store) == ["c52-000", "c52-001", "c52-002"]
    # an item without an id (an old caller) gets a stable one, so it is saved once too
    bare = types.SimpleNamespace(role="user", content="bina id ke")
    assert await extractor.run([bare]) == 1 and await extractor.run([bare]) == 0


async def test_60_saves_keep_the_newest_10_in_order(store):
    for i in range(60):
        assert await store.save_conversation({"messages": [{"id": f"m{i}", "role": "user", "content": f"save {i}"}]})
    recent = await store.get_recent_context(10)
    assert [m["content"] for m in recent] == [f"save {i}" for i in range(50, 60)]
    assert len(store.cache) == ms.CACHE_SIZE == 50 and store.cache[0]["id"] == "m10"  # a deque: no key collisions
    fresh = ms.ConversationMemory(USER, storage_path=store.storage_path)  # another job reads the file once
    assert [m["content"] for m in await fresh.get_recent_context(10)] == [f"save {i}" for i in range(50, 60)]
    assert len(await fresh.get_recent_context(60)) == 60  # more than the cache holds: from the file
    legacy = ms.ConversationMemory(USER + "-old", storage_path=store.storage_path)
    Path(legacy.memory_file).write_bytes(jarvis_crypto.encrypt(json.dumps(
        [{"timestamp": "2026-10-01T10:00:00", "messages": [{"role": "user", "content": "haan"}] * 2}])))
    ids = [m["id"] for m in await legacy.get_recent_context(10)]
    assert len(ids) == len(set(ids)) == 2  # every message has an id, even one saved without


async def test_flush_writes_what_is_pending(store, notified, monkeypatch):
    extractor = _extractor(store)
    real_save = store.save_messages
    monkeypatch.setattr(store, "save_messages", AsyncMock(return_value=None))  # the write fails
    assert await extractor.run([_turn(0), _turn(1)]) == 0  # both stay pending
    monkeypatch.setattr(store, "save_messages", real_save)
    log = [_turn(0), _turn(1)]
    extractor.bind(source=lambda: list(log))  # agent_runner's memory loop binds the router's turn log
    log.append(_turn(2))  # a final transcript the loop has not read yet
    assert await extractor.flush() == 3
    assert await store.save_to_disk() == 0  # nothing left: the storage loop's flush writes nothing
    log.append(_turn(3))
    assert await store.save_to_disk() == 1  # ConversationMemory.save_to_disk runs the extractor's flush
    assert await _saved_ids(store) == ["c52-000", "c52-001", "c52-002", "c52-003"]


async def test_a_probe_context_writes_nothing(store, notified):
    extractor = _extractor(store)
    extractor.bind(source=lambda: [_turn(0), _turn(1)])
    token = probe_context.bridge_suppressed.set(True)
    try:
        assert await extractor.run([_turn(0), _turn(1)]) == 0
        assert await extractor.flush() == 0
        assert await store.save_to_disk() == 0
        assert await store.save_conversation({"messages": [{"id": "p", "role": "user", "content": "x"}]}) is False
    finally:
        probe_context.bridge_suppressed.reset(token)
    assert not Path(store.memory_file).exists() and notified == []


# ---------------------------------------------------------------- what is saved, and how


async def test_only_real_turns_are_saved_and_the_typed_mark_is_stripped(store, notified):
    extractor = _extractor(store)
    items = [
        _turn(0, text="[typed] likha hua sawal", source="text"),
        _turn(1),
        {"id": "s1", "role": "system", "text": "instructions"},
        {"id": "e1", "role": "user", "text": "   "},
        {"id": "j1", "role": "user", "text": "[JARVIS CONTEXT] note"},
        {"id": "p1", "role": "user", "text": "[SYSTEM PROACTIVE] reminder"},
        {"id": "reseed-abc", "role": "user", "text": "purani yaad"},  # C-114: replayed from memory at job start
        llm.ChatMessage(role="user", content=["[typed] chat context se"], id="cc-1"),
    ]
    assert await extractor.run(items) == 3
    saved = ms.flatten_messages(await store.load_memory())
    assert [(m["id"], m["content"]) for m in saved] == [
        ("c52-000", "likha hua sawal"), ("c52-001", "baat 1"), ("cc-1", "chat context se")]
    assert saved[0]["source"] == "text" and saved[2]["source"] == "text"


async def test_an_offline_template_answer_is_saved_but_never_replayed(store, notified):
    """The router's turn log holds its '[offline] ' answer to a typed message that came while no session ran: a chat
    message, so it is saved (source 'system'), but never the model's own turn, so the next job does not replay it."""
    from src.core import session_policy as sp

    router = _router(types.SimpleNamespace(muted=False, process_with_reasoning=AsyncMock(return_value="template")))
    assert await router.handle_text("ZEBRA kal ka plan?", "t-1") == "offline"  # no session attached
    await router.drain()
    extractor = _extractor(store)
    assert await extractor.run(list(router.turn_log)) == 2
    saved = ms.flatten_messages(await store.load_memory())
    assert [(m["id"], m["role"], m.get("source")) for m in saved][0] == ("t-1", "user", "text")
    assert saved[1]["source"] == "system" and saved[1]["content"].startswith("[offline] ")
    assert await sp.reseed_chat_ctx(_NextJob(store)) == 0  # no model turn to replay, and the question never got one


async def test_each_saved_message_sends_one_memory_update(store, notified, monkeypatch):
    extractor = _extractor(store)
    long_text = "lambi baat " * 100
    await extractor.run([_turn(0), _turn(1, text=long_text, persona="jarvis")])
    await _settle()
    assert [call["memory_id"] for call in notified] == ["c52-000", "c52-001"]
    reply = notified[1]
    assert (reply["role"], reply["source"], reply["user_id"], reply["ts"]) == (
        "assistant", "extractor", USER, 1_760_000_001.0)
    sent: list[tuple[str, dict]] = []

    async def notify_event(kind, payload):
        sent.append((kind, payload))

    monkeypatch.setattr(jarvis_bridge, "notify_event", notify_event)
    await jarvis_bridge.notify_memory(long_text, memory_id="c52-001", role="assistant", user_id=USER, ts=5.0)
    assert sent == [("memory_update", {"id": "c52-001", "content": long_text[:500], "source": "extractor",
                                       "ts": 5.0, "role": "assistant", "user_id": USER})]  # K-07


async def test_the_auto_saved_line_appears_only_when_something_was_written(monkeypatch):
    from services.ai_core import agent_loops

    log = MagicMock()
    monkeypatch.setattr(agent_loops, "logger", log)
    assistant = MagicMock()
    assistant.memory_extractor.memory.save_to_disk = AsyncMock(side_effect=[0, 3, 0])
    sleeps = iter([None, None, None, asyncio.CancelledError()])

    async def sleep(_seconds):
        value = next(sleeps)
        if isinstance(value, BaseException):
            raise value

    monkeypatch.setattr(agent_loops.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await agent_loops.start_memory_storage_loop(assistant)
    lines = [call.args for call in log.info.call_args_list]
    assert lines == [("💾 Auto-saved memory snapshot (%d message(s)).", 3)]


async def test_two_jobs_writing_one_file_lose_nothing_and_a_clear_stays_cleared(tmp_path, monkeypatch):
    """Each job has its own ConversationMemory (on Windows the jobs are threads of one process): a write whose file
    changed after its read goes into the file as it is now instead of over it."""
    first = ms.ConversationMemory(USER, storage_path=str(tmp_path))
    second = ms.ConversationMemory(USER, storage_path=str(tmp_path))
    for memory in (first, second):
        monkeypatch.setattr(memory, "_sync_to_vector_db", AsyncMock())
    await first.save_conversation({"messages": [{"id": "a0", "role": "user", "content": "pehla"}]})
    real_first_load, real_second_load = first._load_memory_unlocked, second._load_memory_unlocked

    async def first_reads_then_the_other_job_writes():
        memory = await real_first_load()
        await second.save_conversation({"messages": [{"id": "b1", "role": "user", "content": "doosra job"}]})
        return memory

    monkeypatch.setattr(first, "_load_memory_unlocked", first_reads_then_the_other_job_writes)
    assert await first.save_conversation({"messages": [{"id": "a1", "role": "assistant", "content": "jawab"}]})
    assert await _saved_ids(second) == ["a0", "b1", "a1"]

    monkeypatch.setattr(first, "_load_memory_unlocked", real_first_load)

    async def second_reads_then_the_other_job_clears():
        memory = await real_second_load()
        await first.clear(str(tmp_path / "backups"))
        return memory

    monkeypatch.setattr(second, "_load_memory_unlocked", second_reads_then_the_other_job_clears)
    assert await second.save_conversation({"messages": [{"id": "b2", "role": "user", "content": "naya"}]})
    monkeypatch.setattr(second, "_load_memory_unlocked", real_second_load)
    assert await _saved_ids(second) == ["b2"]  # the clear holds: the stale copy was never written back


# ---------------------------------------------------------------- C-52 extras (a)-(d)


async def test_a_job_ending_soon_after_three_exchanges_leaves_them_for_the_next_reseed(store, notified, monkeypatch):
    """(a) C-114 step 8: the memory loop reads the turn log at least every 10 s, and the job's end flushes what it has
    not read yet (agent_runner._flush_memory: the runtime teardown and LiveKit's shutdown callback), so a job that ends
    90 s after three exchanges still leaves them on disk for the next job's re-seed."""
    from src.core import agent_runner as ar
    from src.core import session_policy as sp

    assert ar.MEMORY_LOOP_INTERVAL_S <= 10
    assistant = types.SimpleNamespace(muted=False, user_id=USER)
    router = _router(assistant)
    extractor = _extractor(store)
    assistant.conversation_router, assistant.memory_extractor = router, extractor
    monkeypatch.setattr(ar, "MEMORY_LOOP_INTERVAL_S", 3600.0)  # the job ends before the loop's next pass
    loop = asyncio.create_task(ar.start_memory_loop(assistant, extractor))
    await asyncio.sleep(0.05)  # its first pass: nothing said yet
    for n in range(3):
        _say(router, "user", f"sawal {n}")
        _say(router, "assistant", f"jawab {n}")
    await router.drain()
    try:
        assert await ar._flush_memory(assistant, "idle") == 6  # pylint: disable=protected-access
        assert await ar._flush_memory(assistant, "idle") == 0  # pylint: disable=protected-access  # each message once
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
    next_job = _NextJob(store)
    assert await sp.reseed_chat_ctx(next_job) == 6
    assert next_job.texts() == ["sawal 0", "jawab 0", "sawal 1", "jawab 1", "sawal 2", "jawab 2"]
    assert await ar._flush_memory(types.SimpleNamespace(), "idle") == 0  # pylint: disable=protected-access


async def test_a_reply_saved_in_the_same_clock_tick_as_its_question_is_kept(store, notified, monkeypatch):
    """(b) ConversationMemory took two one-message conversations with one time stamp for duplicates, and the Windows
    clock moves in 15.6 ms steps, so a reply saved in the same tick as its question was lost; a later conversation
    within 5 minutes with more messages replaced the last one. Message ids decide now."""
    stamp = "2026-10-20T12:00:00.000000"
    assert await store.save_conversation({"messages": [{"role": "user", "content": "kal meeting hai"}],
                                          "timestamp": stamp})
    assert await store.save_conversation({"messages": [{"role": "assistant", "content": "Theek hai Sir"}],
                                          "timestamp": stamp})
    assert await store.save_conversation({"messages": [{"role": "user", "content": "aur?"},
                                                       {"role": "assistant", "content": "bas"}],
                                          "timestamp": "2026-10-20T12:01:00"})
    contents = [m["content"] for m in ms.flatten_messages(await store.load_memory())]
    assert contents == ["kal meeting hai", "Theek hai Sir", "aur?", "bas"]

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 10, 20, 12, 0, 1)

    monkeypatch.setattr(agent_memory, "datetime", Frozen)  # every bundle of the extractor in one clock tick
    extractor = _extractor(store)
    assert await extractor.run([_turn(10)]) == 1
    assert await extractor.run([_turn(10), _turn(11)]) == 1
    assert await extractor.run([_turn(12), _turn(13)]) == 2
    assert (await _saved_ids(store))[-4:] == ["c52-010", "c52-011", "c52-012", "c52-013"]


async def test_every_saved_reply_records_its_persona_and_the_reseed_skips_the_other_one(store, notified, records):
    """(c) The persona that answered goes into every saved reply (the router's turn log, else the bound provider,
    else 'unknown'); the re-seed of a job leaves out the other persona's replies with the questions they answered and
    keeps an old reply without a persona (unknown), within C-114's bounds."""
    from src.core import session_policy as sp
    from src.core.conversation_router import reply_persona

    waiting_switch = types.SimpleNamespace(reply_persona="jarvis",
                                           persona_manager=types.SimpleNamespace(active_persona_name="anna"))
    assert reply_persona(waiting_switch) == "jarvis"  # the live session still answers as JARVIS
    assert reply_persona(types.SimpleNamespace(persona_manager=types.SimpleNamespace(active_persona_name="anna"))) \
        == "anna"
    assert reply_persona(types.SimpleNamespace()) is None
    await store.save_conversation({"messages": [{"role": "user", "content": "purana sawal"},
                                                {"role": "assistant", "content": "purana jawab"}]})  # before C-52
    assistant = types.SimpleNamespace(muted=False, reply_persona="jarvis")
    router = _router(assistant)
    _say(router, "user", "Jarvis, time kya hai?")
    _say(router, "assistant", "Saade baara")
    assistant.reply_persona = "anna"
    _say(router, "user", "Anna, kaisi ho?")
    _say(router, "assistant", "Main theek hoon")
    assistant.reply_persona = "jarvis"
    _say(router, "user", "Jarvis, weather?")
    _say(router, "assistant", "Dhoop hai")
    await router.drain()
    extractor = _extractor(store, persona=lambda: "jarvis")
    assert await extractor.run(list(router.turn_log)) == 6
    saved = ms.flatten_messages(await store.load_memory())
    assert [m.get("persona") for m in saved if m["role"] == "assistant"] == [None, "jarvis", "anna", "jarvis"]
    assert all("persona" not in m for m in saved if m["role"] == "user")
    reply = llm.ChatMessage(role="assistant", content=["jawab"])  # a chat-context item: the bound provider
    assert extractor.message_of(reply)["persona"] == "jarvis"
    assert MemoryExtractor(USER).message_of(reply)["persona"] == "unknown"

    jarvis_job = _NextJob(store)
    assert await sp.reseed_chat_ctx(jarvis_job, persona="jarvis") == 6
    assert jarvis_job.texts() == ["purana sawal", "purana jawab", "Jarvis, time kya hai?", "Saade baara",
                                  "Jarvis, weather?", "Dhoop hai"]
    anna_job = _NextJob(store, persona="anna")  # no persona given: the agent's active one
    assert await sp.reseed_chat_ctx(anna_job) == 4
    assert anna_job.texts() == ["purana sawal", "purana jawab", "Anna, kaisi ho?", "Main theek hoon"]
    assert "SESSION_POLICY re-seed left out 1 reply of the other persona (the job starts as jarvis)" in records.messages
    assert not any("Saade" in line or "theek" in line for line in records.messages)  # counts only
    assert (sp.RESEED_MAX_AGE_S, sp.RESEED_WINDOW_S) == (3600.0, 1800.0)  # C-114's bounds stay


async def test_the_file_stays_encrypted_and_clear_still_moves_it_to_backups(store, notified, tmp_path):
    """(d) jarvis_crypto encrypts the file exactly as before, and C-51's clear() still moves it, unchanged, to
    backups/memory/<UTC ts>_<user>_memory.json (the job's clear_memory runs it through its extractor); the cleared
    messages are never written back, and only a message said after the clear goes into the fresh file."""
    extractor = _extractor(store)
    assert await extractor.run([_turn(0), _turn(1)]) == 2
    raw = Path(store.memory_file).read_bytes()
    assert b"baat" not in raw and b"c52-000" not in raw  # nothing readable at rest
    assert [m["id"] for m in json.loads(jarvis_crypto.decrypt(raw))[0]["messages"]] == ["c52-000", "c52-001"]
    backup = Path(await extractor.clear_memory(str(tmp_path / "backups" / "memory")))
    assert re.fullmatch(rf"\d{{8}}T\d{{6}}Z_{USER}_memory\.json", backup.name) and backup.read_bytes() == raw
    assert not Path(store.memory_file).exists() and not store.cache
    assert await extractor.run([_turn(0), _turn(1), _turn(2)]) == 1  # c52-002 is said after the clear
    assert await _saved_ids(store) == ["c52-002"]


# ---------------------------------------------------------------- C-52 review: clear_memory forgets what came before


async def test_a_clear_forgets_the_turns_the_memory_loop_has_not_written_yet(store, notified, tmp_path):
    """The owner says something, gets the reply and clears memory before the 10 s loop's next pass. The clear goes
    through the job's extractor (control_commands), so that exchange never reaches the fresh file, the vector store or
    a memory_update, and the next job re-seeds nothing; what is said after the clear is saved as usual."""
    from src.core import session_policy as sp

    extractor = _extractor(store)
    log = [_turn(0), _turn(1)]
    extractor.bind(source=lambda: list(log))  # the memory loop binds the router's turn log
    assert await extractor.run(list(log)) == 2  # a pass of the loop
    log += [_turn(2), _turn(3)]  # said and answered after it: in the turn log, not read yet
    backup = Path(await extractor.clear_memory(str(tmp_path / "backups")))
    assert ms.saved_ids(json.loads(jarvis_crypto.decrypt(backup.read_bytes()))) == {"c52-000", "c52-001"}
    assert await extractor.run(list(log)) == 0 and await extractor.flush() == 0  # the next pass writes nothing
    assert await store.save_to_disk() == 0 and not Path(store.memory_file).exists()
    assert store._sync_to_vector_db.call_count == 1  # pylint: disable=protected-access  # the first save's only
    assert await sp.reseed_chat_ctx(_NextJob(store)) == 0
    log.append(_turn(4))  # said after the clear
    assert await extractor.flush() == 1
    await _settle()
    assert await _saved_ids(store) == ["c52-004"]
    assert [call["memory_id"] for call in notified] == ["c52-000", "c52-001", "c52-004"]


async def test_a_clear_lets_a_running_save_land_first_and_drops_the_pending_messages(store, notified, tmp_path,
                                                                                    monkeypatch):
    """A save the job's cancelled loop left running lands before the move (in the backup, never in the fresh file),
    and a message whose save failed (pending) is forgotten with the rest of what was said before the clear. A move
    that fails forgets nothing."""
    extractor = _extractor(store)
    log = [_turn(0), _turn(1)]
    extractor.bind(source=lambda: list(log))
    real_save = store.save_messages
    started, gate = asyncio.Event(), asyncio.Event()

    async def slow_save(bundle, **kwargs):
        started.set()
        await gate.wait()
        return await real_save(bundle, **kwargs)

    monkeypatch.setattr(store, "save_messages", slow_save)
    loop_pass = asyncio.create_task(extractor.run(list(log)))
    await asyncio.wait_for(started.wait(), 5)
    loop_pass.cancel()  # the job's loops end: the pass is cancelled, its save goes on
    with pytest.raises(asyncio.CancelledError):
        await loop_pass
    clear = asyncio.create_task(extractor.clear_memory(str(tmp_path / "backups")))
    await asyncio.sleep(0.05)
    assert not clear.done()  # it waits for the running save
    gate.set()
    backup = Path(await clear)
    assert ms.saved_ids(json.loads(jarvis_crypto.decrypt(backup.read_bytes()))) == {"c52-000", "c52-001"}
    assert not Path(store.memory_file).exists()

    real_clear = store.clear
    monkeypatch.setattr(store, "save_messages", AsyncMock(return_value=None))  # the next write fails
    log.append(_turn(2))
    assert await extractor.flush() == 0  # c52-002 stays pending
    monkeypatch.setattr(store, "clear", AsyncMock(side_effect=PermissionError("locked")))
    with pytest.raises(PermissionError):
        await extractor.clear_memory()  # the move failed: nothing is forgotten ...
    monkeypatch.setattr(store, "clear", real_clear)
    monkeypatch.setattr(store, "save_messages", real_save)
    assert await extractor.flush() == 1  # ... so c52-002 still reaches the file
    monkeypatch.setattr(store, "save_messages", AsyncMock(return_value=None))
    log.append(_turn(3))
    assert await extractor.flush() == 0  # c52-003 stays pending
    monkeypatch.setattr(store, "save_messages", real_save)
    second = Path(await extractor.clear_memory(str(tmp_path / "backups")))
    assert ms.saved_ids(json.loads(jarvis_crypto.decrypt(second.read_bytes()))) == {"c52-002"}
    assert await extractor.flush() == 0 and not Path(store.memory_file).exists()  # c52-003 is forgotten


async def test_a_clear_waits_for_another_jobs_write_that_is_under_way(tmp_path, monkeypatch):
    """Another job's ConversationMemory of the same user holds the file's lock from its 'changed?' check to its
    os.replace, and the clear's move takes that lock too, so the move never lands in between: that write goes into
    the backup, and the cleared history never comes back into the live file."""
    clearing = ms.ConversationMemory(USER, storage_path=str(tmp_path))  # the job where the owner clears
    writing = ms.ConversationMemory(USER, storage_path=str(tmp_path))   # another job's, saving at that moment
    for memory in (clearing, writing):
        monkeypatch.setattr(memory, "_sync_to_vector_db", AsyncMock())
    assert await writing.save_conversation({"messages": [{"id": "old-1", "role": "user", "content": "purani"}]})
    in_write, move_tried = threading.Event(), threading.Event()
    writer: list[int] = []
    real_encrypt, real_file_lock, real_move = ms.jarvis_crypto.encrypt, ms._file_lock, clearing._move_to_backup

    def encrypt(data):  # the writer, under the file's lock, past its 'changed?' check
        if not in_write.is_set():
            writer.append(threading.get_ident())
            in_write.set()
            assert move_tried.wait(5), "the clear never tried to move the file"
        return real_encrypt(data)

    class _Announced:
        """The file's real lock; the clear's thread says it is about to take it."""

        def __init__(self, lock) -> None:
            self.lock = lock

        def __enter__(self):
            if in_write.is_set() and threading.get_ident() not in writer:
                move_tried.set()
            self.lock.acquire()
            return self

        def __exit__(self, *_exc) -> bool:
            self.lock.release()
            return False

    def move(target_dir):  # a move made without the lock says so once it is done
        try:
            return real_move(target_dir)
        finally:
            move_tried.set()

    monkeypatch.setattr(ms.jarvis_crypto, "encrypt", encrypt)
    monkeypatch.setattr(ms, "_file_lock", lambda path: _Announced(real_file_lock(path)))
    monkeypatch.setattr(clearing, "_move_to_backup", move)
    save = asyncio.create_task(writing.save_conversation(
        {"messages": [{"id": "new-2", "role": "user", "content": "nayi"}]}))
    assert await asyncio.to_thread(in_write.wait, 5)
    backup = Path(await clearing.clear(str(tmp_path / "backups")))
    assert await save
    assert ms.saved_ids(json.loads(jarvis_crypto.decrypt(backup.read_bytes()))) == {"old-1", "new-2"}
    assert not Path(writing.memory_file).exists()  # not 'old-1' back in the live file
    assert await writing.save_conversation({"messages": [{"id": "new-3", "role": "user", "content": "phir"}]})
    assert await _saved_ids(ms.ConversationMemory(USER, storage_path=str(tmp_path))) == ["new-3"]


# ---------------------------------------------------------------- C-52 review 2: update order, backoff, another clear


async def test_a_clear_lets_the_memory_updates_of_earlier_saves_reach_the_bridge_first(store, tmp_path, monkeypatch):
    """A save's memory_updates go out in the background, and the bridge empties its stored memories on memory_cleared
    (control_commands sends it once clear_memory returns) but stores every later memory_update again, which showed
    cleared messages in init_state.memories. clear_memory lets them all out before the move; a send that hangs is
    cancelled after UPDATES_WAIT_S instead of holding the clear."""
    events: list[str] = []
    gate = asyncio.Event()

    async def notify(_content, **kwargs):
        await gate.wait()
        events.append(f"memory_update {kwargs['memory_id']}")

    monkeypatch.setattr(agent_memory, "notify_memory", notify)
    real_clear = store.clear

    async def clear(*args):
        backup = await real_clear(*args)
        events.append("moved")  # memory_cleared goes out after this
        return backup

    monkeypatch.setattr(store, "clear", clear)
    extractor = _extractor(store)
    assert await extractor.run([_turn(0), _turn(1)]) == 2  # its memory_updates wait in the background
    clearing = asyncio.create_task(extractor.clear_memory(str(tmp_path / "backups")))
    await asyncio.sleep(0.05)
    assert not clearing.done() and events == []  # the clear waits for them
    gate.set()
    assert await asyncio.wait_for(clearing, 5)
    assert events == ["memory_update c52-000", "memory_update c52-001", "moved"]

    gate.clear()  # a bridge that never answers: the clear goes on after UPDATES_WAIT_S and cancels the send
    monkeypatch.setattr(agent_memory, "UPDATES_WAIT_S", 0.05)
    events.clear()
    assert await extractor.run([_turn(2)]) == 1
    assert await asyncio.wait_for(extractor.clear_memory(str(tmp_path / "backups")), 5)
    await _settle()
    assert events == ["moved"] and not extractor._sends  # pylint: disable=protected-access


async def test_a_save_that_keeps_failing_backs_off_and_logs_once_per_streak(store, notified, records, monkeypatch):
    """A memory file locked by antivirus or sync software (a full disk, a permission) failed every 10 s pass of the
    memory loop with two ERROR lines, for the job's whole life (WARNING+ lines reach the UI log panel). Now the loop's
    next write waits 20 s, doubling up to 5 minutes (the messages of the passes in between are collected and stay
    pending), a flush (the job's end) still tries at once, each streak is logged once and its end in one line."""
    import logging

    now = [1000.0]
    monkeypatch.setattr(agent_memory, "_clock", lambda: now[0])
    writes: list[float] = []
    broken = [True]
    real_write = store._write_encrypted  # pylint: disable=protected-access

    def write(*args):
        writes.append(now[0] - 1000.0)
        if broken[0]:
            raise PermissionError(13, "locked")
        return real_write(*args)

    monkeypatch.setattr(store, "_write_encrypted", write)

    def loud() -> list[tuple[str, str]]:
        return [(name, message) for name, level, message in records.entries if level >= logging.WARNING]

    extractor = _extractor(store)
    log = [_turn(0)]
    for second in range(0, 1200, 10):  # 20 minutes of the memory loop's 10 s passes
        now[0] = 1000.0 + second
        if second == 600:
            log.append(_turn(1))  # said while the saves fail: collected, pending
        assert await extractor.run(list(log)) == 0
    assert writes == [0, 20, 60, 140, 300, 600, 900]  # before: every pass
    [(store_name, store_line), (extractor_name, extractor_line)] = loud()
    assert store_name == "JARVIS-MEMORY-STORE" and "PermissionError" in store_line
    assert extractor_name == "JARVIS-MEMORY-EXTRACTOR" and "1 message(s) stay pending" in extractor_line

    now[0] = 2195.0
    assert await extractor.flush() == 0 and len(writes) == 8  # the job's end tries at once, though the loop waits
    broken[0] = False
    now[0] = 2196.0
    assert await extractor.run(list(log)) == 0 and len(writes) == 8  # the loop still waits
    assert await extractor.flush() == 2
    assert await _saved_ids(store) == ["c52-000", "c52-001"]
    assert [message for _name, level, message in records.entries if "saving works again" in message
            and level >= logging.INFO] == ["✅ MEMORY saving works again after 8 failed attempt(s)"]
    assert len(loud()) == 2

    log.append(_turn(2))
    assert await extractor.run(list(log)) == 1  # saving works: no backoff left
    broken[0] = True
    log.append(_turn(3))
    now[0] = 2210.0
    assert await extractor.run(list(log)) == 0
    now[0] = 2220.0
    assert await extractor.run(list(log)) == 0  # backing off again: no second try, no second line
    assert len(writes) == 11 and len(loud()) == 4  # a new streak is logged once more
    broken[0] = False
    now[0] = 2230.0
    assert await extractor.run(list(log)) == 1
    assert not any("baat" in line for line in records.messages)  # K-29: counts only


async def test_the_clear_mark_advances_only_when_a_clear_worked(tmp_path, monkeypatch):
    """C-52: every clear of a memory file that worked (one that found no file too) advances the file's clear mark,
    which every ConversationMemory of that file in this process sees; a move that fails leaves it as it was."""
    import time

    memory = ms.ConversationMemory(USER, storage_path=str(tmp_path))
    twin = ms.ConversationMemory(USER, storage_path=str(tmp_path / "sub" / ".."))  # the same file, another instance
    assert ms.clear_mark(twin.memory_file) == ms.NEVER_CLEARED
    assert await memory.clear(str(tmp_path / "backups")) is None  # no file: nothing to move, still a clear
    generation, cleared_at = ms.clear_mark(twin.memory_file)
    assert generation == 1 and abs(cleared_at - time.time()) < 60
    Path(memory.memory_file).write_bytes(b"keep me")

    def replace(_src, _dst):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(ms.os, "replace", replace)
    with pytest.raises(OSError):
        await twin.clear(str(tmp_path / "backups"))
    assert ms.clear_mark(memory.memory_file) == (1, cleared_at)


async def test_a_clear_in_another_job_reaches_its_extractor_too(tmp_path, monkeypatch, notified, records):
    """Two jobs of the owner live in one worker process for a moment (a restart) and the owner clears in one of them.
    The other job's extractor learns of the clear through the file's clear mark, so its turns from before the clear
    (collected and pending after a failed save, said but not read yet, in a save under way that lands after the move)
    never reach the fresh file, and its memory_updates of a save before the clear stop; what it hears after the clear
    is saved as usual."""
    import time

    owner_store = ms.ConversationMemory(USER, storage_path=str(tmp_path))
    other_store = ms.ConversationMemory(USER, storage_path=str(tmp_path))
    for memory in (owner_store, other_store):
        monkeypatch.setattr(memory, "_sync_to_vector_db", AsyncMock())
    owner, other = _extractor(owner_store), _extractor(other_store)
    backups = str(tmp_path / "backups")
    assert await owner.run([_turn(0)]) == 1  # the file exists
    log = [_turn(10, ts=time.time() - 30)]
    other.bind(source=lambda: list(log))  # the other job's turn log
    real_save = other_store.save_messages
    monkeypatch.setattr(other_store, "save_messages", AsyncMock(return_value=None))
    assert await other.flush() == 0  # c52-010 collected; its save failed, so it is pending
    log.append(_turn(11, ts=time.time() - 1))  # said before the clear, not read by the other job yet
    await owner.clear_memory(backups)
    assert ms.clear_generation(other_store.memory_file) == 1
    log.append(_turn(12, ts=time.time() + 0.01))  # said after it
    monkeypatch.setattr(other_store, "save_messages", real_save)
    assert await other.flush() == 1
    assert await _saved_ids(other_store) == ["c52-012"]
    assert await other.flush() == 0  # the dropped turns never come back from the turn log

    real_load = other_store._load_memory_unlocked  # pylint: disable=protected-access

    async def other_reads_then_the_owner_clears():
        memory = await real_load()
        await owner.clear_memory(backups)  # the move lands between the other job's read and its write
        return memory

    monkeypatch.setattr(other_store, "_load_memory_unlocked", other_reads_then_the_owner_clears)
    log.append(_turn(13, ts=time.time() + 0.01))
    assert await other.flush() == 0  # collected before that clear: nothing written, not through the merge either
    monkeypatch.setattr(other_store, "_load_memory_unlocked", real_load)
    assert not Path(other_store.memory_file).exists()

    gate = asyncio.Event()
    sent: list[str] = []

    async def slow_notify(_content, **kwargs):
        await gate.wait()
        sent.append(kwargs["memory_id"])

    monkeypatch.setattr(agent_memory, "notify_memory", slow_notify)
    log += [_turn(14, ts=time.time() + 0.01), _turn(15, ts=time.time() + 0.01)]
    assert await other.flush() == 2  # its memory_updates wait in the background
    for _ in range(5):
        await asyncio.sleep(0)  # c52-014's is under way
    await owner.clear_memory(backups)
    gate.set()
    await _settle()
    assert sent == ["c52-014"]  # c52-015's is not sent after the clear
    assert [call["memory_id"] for call in notified] == ["c52-000", "c52-012"]
    assert not any("baat" in line for line in records.messages)  # K-29: counts only
