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

def test_vector_memory_initialization(mock_chroma):
    vm = VectorMemory()
    vm._ensure_initialized()
    assert vm.client is not None
    assert vm.collection is not None

def test_vector_memory_add_memory(mock_chroma):
    mock_client, mock_collection = mock_chroma
    vm = VectorMemory()
    vm._ensure_initialized()
    
    vm.add_memory("test memory", {"source": "test"})
    assert mock_collection.add.called

def test_vector_memory_search(mock_chroma):
    mock_client, mock_collection = mock_chroma
    mock_collection.query.return_value = {"documents": [["result 1"]]}
    
    vm = VectorMemory()
    vm._ensure_initialized()
    
    results = vm.query_memory("query")
    assert results == ["result 1"]

@pytest.mark.asyncio
async def test_conversation_memory_save_load():
    # Mock file operations to avoid real I/O
    user_id = "test_user"
    mem = ConversationMemory(user_id=user_id, storage_path="test_conversations")
    conversation = {"id": "1", "messages": [{"role": "user", "content": "hello"}]}
    
    with patch("builtins.open", MagicMock()):
        with patch("json.dump") as mock_dump:
            with patch("os.path.exists", return_value=True):
                with patch("json.load", return_value=[]) as mock_load:
                    with patch("os.makedirs"):
                        with patch("os.replace"):
                            # Mock vector sync
                            with patch.object(mem, "_sync_to_vector_db", new_callable=AsyncMock):
                                success = await mem.save_conversation(conversation)
                                assert success is True
                                
                                # Now mock load with the saved data
                                mock_load.return_value = [conversation]
                                history = await mem.load_memory()
                                assert len(history) == 1
                                assert history[0]["id"] == "1"

    # Cleanup test dir mock (even though we mocked makedirs, let's be safe)
    if os.path.exists("test_conversations"):
        shutil.rmtree("test_conversations")
