import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from agent_memory import MemoryExtractor


@pytest.fixture
def extractor():
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
    assert extractor.memory.save_conversation.call_count == 2


@pytest.mark.asyncio
async def test_extractor_run_error_handling(extractor):
    msg = MagicMock()
    msg.role = "u"
    msg.content = "a"
    chat_ctx = [msg]

    # Trigger error
    extractor.memory.save_conversation = AsyncMock(
        side_effect=AttributeError("Fail"))

    # Should not raise
    await extractor.run(chat_ctx)


@pytest.mark.asyncio
async def test_extractor_run_cancelled(extractor):
    msg = MagicMock()
    msg.role = "u"
    msg.content = "a"
    chat_ctx = [msg]

    extractor.memory.save_conversation = AsyncMock(
        side_effect=asyncio.CancelledError())

    # Should not raise
    await extractor.run(chat_ctx)


def test_extractor_clear_context(extractor):
    extractor.conversation_count = 5
    extractor.clear_context()
    assert extractor.conversation_count == 0
