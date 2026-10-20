import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import agent_memory
from agent_memory import MemoryExtractor


@pytest.fixture
def extractor(monkeypatch):
    monkeypatch.setattr(agent_memory, "notify_memory", AsyncMock())  # C-52: memory_update never reaches a bridge
    with patch("agent_memory.ConversationMemory"):
        return MemoryExtractor(user_id="test_user")


def test_extractor_init(extractor):
    assert extractor.user_id == "test_user"
    assert extractor.conversation_count == 0


@pytest.mark.asyncio
async def test_extractor_run_empty(extractor):
    await extractor.run([])
    assert extractor.conversation_count == 0


@pytest.mark.asyncio
async def test_extractor_run_success(extractor):
    # Mock chat messages
    msg1 = MagicMock()
    msg1.role = "user"
    msg1.content = "hello"

    msg2 = MagicMock()
    msg2.role = "assistant"
    msg2.content = [{"text": "hi there"}]  # List content branch

    chat_ctx = [msg1, msg2]

    # extractor.memory is already a mock from the fixture
    extractor.memory.save_conversation = AsyncMock(return_value=True)

    await extractor.run(chat_ctx)

    assert extractor.conversation_count == 2
    # C-52: one bundle per pass (one encrypted write), with both messages in their order
    assert extractor.memory.save_conversation.call_count == 1
    bundle = extractor.memory.save_conversation.await_args.args[0]
    assert [(m["role"], m["content"]) for m in bundle["messages"]] == [("user", "hello"), ("assistant", "hi there")]
    await extractor.run(chat_ctx)  # the same messages again: each is saved once
    assert extractor.memory.save_conversation.call_count == 1


@pytest.mark.asyncio
async def test_extractor_run_error_handling(extractor):
    msg = MagicMock()
    msg.role = "user"
    msg.content = "a"
    chat_ctx = [msg]

    # Trigger error
    extractor.memory.save_conversation = AsyncMock(
        side_effect=AttributeError("Fail"))

    # Should not raise; the message stays pending for the next pass (C-52)
    assert await extractor.run(chat_ctx) == 0
    extractor.memory.save_conversation = AsyncMock(return_value=True)
    assert await extractor.flush() == 1


@pytest.mark.asyncio
async def test_extractor_run_cancelled(extractor):
    msg = MagicMock()
    msg.role = "user"
    msg.content = "a"
    chat_ctx = [msg]

    extractor.memory.save_conversation = AsyncMock(
        side_effect=asyncio.CancelledError())

    # Should not raise: a save cancelled on its own (not the caller) counts as failed
    assert await extractor.run(chat_ctx) == 0


def test_extractor_clear_context(extractor):
    extractor.conversation_count = 5
    extractor.clear_context()
    assert extractor.conversation_count == 0
