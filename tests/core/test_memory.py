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
