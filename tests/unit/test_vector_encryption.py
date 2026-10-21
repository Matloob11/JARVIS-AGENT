"""C-124 step 6: Chroma documents Fernet-encrypted with JARVIS_ENCRYPTION_KEY, and a single opener.

A real Chroma PersistentClient in tmp_path (telemetry off) with the module's own decrypting embedding
function on a small deterministic model, so nothing is downloaded: on the VPS brain (remote mode) the
round trip is encrypted on disk and still recalls by meaning. Plain-text memories from before (every PC
memory today) are recalled as they are; a document that does not decrypt is returned as stored, never
dropped; the PC brain keeps writing plain text. On POSIX the opener holds an fcntl lock on
chroma_db/.jarvis.lock and any other process runs without vector recall; Windows keeps the single worker
of the agent_ctl runtime lock. The key never reaches a log line.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import chromadb
import numpy as np
import pytest
from chromadb.config import Settings
from cryptography.fernet import Fernet

from services.ai_core import jarvis_vector_memory as jvm
from src.core import agent_runner

SECRET_FACT = "c124 Sir Matloob ki bank PIN 4321 hai aur favourite colour black"
OTHER_FACT = "c124 the VPS hosts the bridge and LiveKit"
DIM = 64


class _HashingModel:
    """A tiny deterministic bag-of-words 'sentence model': shared words give close vectors."""

    def encode(self, texts: list[str], convert_to_numpy: bool = True,
               normalize_embeddings: bool = False) -> np.ndarray:
        vectors = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in str(text).lower().split():
                vectors[row, int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
            norm = np.linalg.norm(vectors[row])
            if norm:
                vectors[row] /= norm
        return vectors


class _TestEmbedding(jvm.SentenceTransformerEmbeddingFunction):
    """The module's decrypting embedding function with the hashing model instead of MiniLM."""

    def __init__(self, model_name: str = jvm.MODEL_NAME, **_: Any) -> None:  # pylint: disable=super-init-not-called
        self.model_name, self.device, self.normalize_embeddings, self.kwargs = model_name, "cpu", False, {}
        self._model = _HashingModel()
        self.seen: list[str] = []

    def __call__(self, input: Any) -> Any:  # pylint: disable=redefined-builtin
        self.seen.extend(jvm.open_document(text) for text in input)
        return super().__call__(input)

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> _TestEmbedding:
        # Chroma's is_legacy() check rebuilds the function from its config: never the real MiniLM here.
        return _TestEmbedding(config.get("model_name", jvm.MODEL_NAME))


@pytest.fixture
def key(monkeypatch) -> str:
    value = Fernet.generate_key().decode()
    monkeypatch.setenv(jvm.ENCRYPTION_KEY_ENV, value)
    jvm._warned.clear()
    return value


@pytest.fixture
def chroma(monkeypatch, tmp_path) -> Path:
    """The real Chroma client, telemetry off, in tmp_path; the test embedding function."""
    db = tmp_path / "chroma_db"

    def persistent_client(path: str) -> Any:
        return chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))

    monkeypatch.setattr(jvm, "chromadb", SimpleNamespace(PersistentClient=persistent_client))
    monkeypatch.setattr(jvm, "DB_PATH", str(db))
    monkeypatch.setattr(jvm, "SentenceTransformerEmbeddingFunction", _TestEmbedding)
    return db


class _Log(logging.Handler):
    """Collects the vector memory logger's lines (setup_logger loggers do not propagate)."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return " | ".join(self.lines)


@pytest.fixture
def memory_log():
    handler = _Log()
    jvm.logger.addHandler(handler)
    yield handler
    jvm.logger.removeHandler(handler)


def _disk_bytes(folder: Path) -> bytes:
    return b"".join(path.read_bytes() for path in folder.rglob("*") if path.is_file())


async def test_the_vps_round_trip_is_encrypted_on_disk_and_recalls_by_meaning(monkeypatch, key, chroma):
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    assert jvm.encrypts_writes()
    memory = jvm.VectorMemory()
    assert await memory.add_memory(SECRET_FACT, {"user": "c124"})
    assert await memory.add_memory(OTHER_FACT, {"user": "c124"})
    hits = await memory.query_memory("bank PIN favourite colour", n_results=2)
    # C-53: the metadata also holds the memory's own id and its source (never the text)
    assert hits[0]["document"] == SECRET_FACT and hits[0]["metadata"] == {
        "user": "c124", "id": hits[0]["metadata"].get("id"), "source": jvm.DEFAULT_SOURCE}
    assert isinstance(hits[0]["metadata"]["id"], str) and hits[0]["metadata"]["id"]
    assert hits[0]["score"] > hits[1]["score"]
    stored = memory.collection.get(include=["documents"])["documents"]
    assert all(doc.startswith(jvm.ENC_PREFIX) for doc in stored)
    assert SECRET_FACT in memory.embedding_func.seen  # the vectors come from the plain text
    raw = _disk_bytes(chroma)
    assert raw and b"bank PIN 4321" not in raw and b"VPS hosts the bridge" not in raw
    assert jvm.ENC_PREFIX.encode() in raw
    assert key.encode() not in raw


async def test_plain_text_memories_from_before_are_recalled_as_they_are(monkeypatch, key, chroma):
    jvm.VectorMemory()  # nothing opened yet
    legacy = jvm.VectorMemory()
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)  # written by the PC brain, as today
    assert await legacy.add_memory(SECRET_FACT, {"user": "c124"})
    assert legacy.collection.get(include=["documents"])["documents"] == [SECRET_FACT]
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")  # the VPS brain reads the same store
    assert await legacy.add_memory(OTHER_FACT, {"user": "c124"})
    documents = [hit["document"] for hit in await legacy.query_memory("bank PIN colour VPS bridge", 5)]
    assert sorted(documents) == sorted([SECRET_FACT, OTHER_FACT])


async def test_a_document_that_does_not_decrypt_is_kept_and_never_crashes(monkeypatch, key, chroma, memory_log):
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    memory = jvm.VectorMemory()
    assert await memory.add_memory(SECRET_FACT, {"user": "c124"})
    monkeypatch.setenv(jvm.ENCRYPTION_KEY_ENV, Fernet.generate_key().decode())  # another key
    hits = await memory.query_memory("bank PIN", 3)
    assert len(hits) == 1 and hits[0]["document"].startswith(jvm.ENC_PREFIX)  # kept, as stored
    assert "does not decrypt" in memory_log.text
    monkeypatch.delenv(jvm.ENCRYPTION_KEY_ENV)
    assert jvm.open_document(hits[0]["document"]) == hits[0]["document"]


def test_documents_without_a_key_or_on_the_pc_stay_plain_text(monkeypatch, key):
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)
    assert jvm.seal_document("pc fact") == "pc fact" and not jvm.encrypts_writes()
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    sealed = jvm.seal_document("vps fact")
    assert sealed.startswith(jvm.ENC_PREFIX) and jvm.open_document(sealed) == "vps fact"
    assert jvm.open_document("legacy plain text") == "legacy plain text"
    assert jvm.open_document(None) is None
    monkeypatch.delenv(jvm.ENCRYPTION_KEY_ENV)
    assert jvm.seal_document("vps fact") == "vps fact" and not jvm.encrypts_writes()


def test_an_invalid_key_is_never_logged_and_writes_plain_text(monkeypatch, memory_log):
    jvm._warned.clear()
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    monkeypatch.setenv(jvm.ENCRYPTION_KEY_ENV, "c124-not-a-fernet-key")
    assert jvm.seal_document("fact") == "fact"
    assert "not a valid Fernet key" in memory_log.text and "c124-not-a-fernet-key" not in memory_log.text


# ---------------------------------------------------------------- the single opener


class _FakeFcntl:
    LOCK_EX, LOCK_NB = 2, 4

    def __init__(self, held_elsewhere: bool) -> None:
        self.held_elsewhere = held_elsewhere
        self.calls: list[int] = []

    def flock(self, fd: int, flags: int) -> None:
        self.calls.append(flags)
        if self.held_elsewhere:
            raise BlockingIOError(11, "Resource temporarily unavailable")


async def test_another_process_holding_the_lock_means_no_vector_recall(monkeypatch, chroma, memory_log):
    fake = _FakeFcntl(held_elsewhere=True)
    monkeypatch.setattr(jvm, "fcntl", fake)
    opened: list[str] = []
    monkeypatch.setattr(jvm, "chromadb", SimpleNamespace(PersistentClient=lambda path: opened.append(path)))
    memory = jvm.VectorMemory()
    assert await jvm._await_future(memory.prewarm(), 10) is False
    assert opened == [] and memory.collection is None and "VECTOR_LOCK" in memory_log.text
    assert fake.calls == [fake.LOCK_EX | fake.LOCK_NB] and (chroma / jvm.LOCK_FILE).is_file()
    assert await memory.query_memory("anything") == [] and not await memory.add_memory("x")
    assert fake.calls == [fake.LOCK_EX | fake.LOCK_NB]  # no new try within LOCK_RETRY_S
    assert memory.prewarm().done()


async def test_the_lock_holder_opens_chroma_and_keeps_the_lock(monkeypatch, key, chroma):
    fake = _FakeFcntl(held_elsewhere=False)
    monkeypatch.setattr(jvm, "fcntl", fake)
    memory = jvm.VectorMemory()
    assert await memory.add_memory(OTHER_FACT, {"user": "c124"})
    assert memory._lock_handle is not None and not memory._lock_handle.closed
    assert memory._acquire_db_lock() and len(fake.calls) == 1  # taken once for the process
    memory._lock_handle.close()


def test_windows_needs_no_file_lock(monkeypatch, chroma):
    monkeypatch.setattr(jvm, "fcntl", None)
    assert jvm.VectorMemory()._acquire_db_lock() is True
    assert not (chroma / jvm.LOCK_FILE).exists()


def test_a_job_for_another_room_never_opens_chroma_on_the_vps(monkeypatch):
    calls: list[str] = []
    fake_db = SimpleNamespace(prewarm=lambda: calls.append("prewarm"),
                              disable=lambda reason: calls.append("disable"))
    monkeypatch.setattr(jvm, "jarvis_vector_db", fake_db)
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    monkeypatch.delenv("JARVIS_ROOM_NAME", raising=False)
    agent_runner._prewarm_vector_memory("jarvis-canary-1700000000")
    agent_runner._prewarm_vector_memory("jarvis-hybrid")
    assert calls == ["disable", "prewarm"]
    monkeypatch.delenv("JARVIS_DESKTOP_MODE")
    agent_runner._prewarm_vector_memory("jarvis-probe-1700000000")  # the PC brain: as today
    assert calls[-1] == "prewarm"


async def test_a_disabled_memory_answers_at_once(chroma):
    memory = jvm.VectorMemory()
    memory.disable("test")
    assert memory.prewarm().done() and not memory.prewarm().result()
    assert await asyncio.wait_for(memory.query_memory("x"), 1) == []
    assert await memory.add_memory("x") is False
