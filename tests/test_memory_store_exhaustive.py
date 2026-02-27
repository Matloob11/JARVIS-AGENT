import pytest
import asyncio
import json
import os
from unittest.mock import MagicMock, patch, mock_open, AsyncMock
from memory_store import ConversationMemory

@pytest.fixture
def memory():
    with patch("os.makedirs"):
        return ConversationMemory(user_id="test_user", storage_path="test_mem")

@pytest.mark.asyncio
async def test_memory_init(memory):
    assert memory.user_id == "test_user"
    assert memory.storage_path == "test_mem"

@pytest.mark.asyncio
async def test_load_memory_success(memory):
    data = [{"role": "user", "content": "hello"}]
    # Mocking os.path.exists for the to_thread call
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(data))):
        mem = await memory.load_memory()
        assert len(mem) == 1
        assert mem[0]["content"] == "hello"

@pytest.mark.asyncio
async def test_load_memory_corrupt(memory):
    # Mocking os.path.exists and open to simulate corruption
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="invalid json")), \
         patch("os.rename"):
        mem = await memory.load_memory()
        assert mem == []

@pytest.mark.asyncio
async def test_save_conversation_success(memory):
    conv = {"role": "user", "content": "new msg"}
    # Mock load_memory to return empty list
    with patch.object(memory, "load_memory", AsyncMock(return_value=[])), \
         patch("builtins.open", mock_open()), \
         patch("os.replace"), \
         patch("memory_store.jarvis_vector_db.add_memory") as mock_vector:
        res = await memory.save_conversation(conv)
        assert res is True

@pytest.mark.asyncio
async def test_save_conversation_update(memory):
    last_conv = {"role": "user", "content": "hello", "timestamp": "2023-01-01T00:00:00"}
    new_conv = {"role": "user", "content": "hello again", "timestamp": "2023-01-01T00:01:00"}
    
    memory.memory_cache = [last_conv] # Note: cache isn't really used this way in code, it calls load_memory
    
    with patch.object(memory, "load_memory", AsyncMock(return_value=[last_conv])), \
         patch("builtins.open", mock_open()), \
         patch("os.replace"):
        res = await memory.save_conversation(new_conv)
        assert res is True

@pytest.mark.asyncio
async def test_clear_duplicates(memory):
    mem_data = [
        {"timestamp": "2023-01-01", "role": "u", "content": "a"},
        {"timestamp": "2023-01-01", "role": "u", "content": "a"},
        {"timestamp": "2023-01-02", "role": "u", "content": "b"}
    ]
    with patch.object(memory, "load_memory", AsyncMock(return_value=mem_data)), \
         patch("builtins.open", mock_open()), \
         patch("os.replace"):
        removed = await memory.clear_duplicates()
        assert removed == 1

@pytest.mark.asyncio
async def test_get_semantic_context(memory):
    with patch("memory_store.jarvis_vector_db.query_memory", return_value=["some context"]):
        ctx = await memory.get_semantic_context("hello")
        assert ctx == ["some context"]
