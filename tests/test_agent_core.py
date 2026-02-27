import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from agent_core import BrainAssistant


@pytest.fixture
def mock_agent_deps():
    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock):
        with patch("livekit.agents.Agent.__init__", return_value=None):
            with patch("livekit.plugins.google.realtime.RealtimeModel"):
                with patch("jarvis_identity.jarvis_id.get_context", return_value="Test Context"):
                    with patch("agent_memory.MemoryExtractor"):
                        yield


def test_brain_assistant_init(mock_agent_deps):
    chat_ctx = MagicMock()
    assistant = BrainAssistant(chat_ctx=chat_ctx)
    setattr(assistant, "_instructions", "Test Context")
    assert assistant._wake_word_mode is True
    assert assistant._gf_mode_active is False
    assert assistant.instructions == "Test Context"


@pytest.mark.asyncio
async def test_tool_set_wake_word_mode(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    result = await assistant.tool_set_wake_word_mode(False)
    assert result["status"] == "success"
    assert assistant._wake_word_mode is False


@pytest.mark.asyncio
async def test_tool_toggle_gf_mode(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._instructions = "Test Context"
    with patch.object(assistant, "update_instructions", new_callable=AsyncMock) as mock_update:
        result = await assistant.tool_toggle_gf_mode(True)
        assert result["status"] == "success"
        assert assistant._gf_mode_active is True
        mock_update.assert_called_once()


def test_extract_text_from_message(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    msg_simple = MagicMock()
    msg_simple.content = "Hello Jarvis"
    assert assistant._extract_text_from_message(msg_simple) == "hello jarvis"

    msg_complex = MagicMock()
    part1 = MagicMock()
    part1.text = "Hello"
    part2 = MagicMock()
    part2.text = "World"
    msg_complex.content = [part1, part2]
    assert assistant._extract_text_from_message(msg_complex) == "hello world"


@pytest.mark.asyncio
async def test_handle_wake_word(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._instructions = "Test Context"
    assistant._activity = MagicMock()
    detected, is_anna = await assistant._handle_wake_word("Hey Jarvis how are you")
    assert detected is True
    assert is_anna is False

    detected, is_anna = await assistant._handle_wake_word("Babu help me")
    assert detected is True
    assert is_anna is True


@pytest.mark.asyncio
async def test_process_with_reasoning_fallback(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    with patch("agent_core.analyze_user_intent", side_effect=ValueError("Error")):
        result = await assistant.process_with_reasoning("test")
        assert result == "Sir, main samajh gaya."


@pytest.mark.asyncio
async def test_inject_emotional_context(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._gf_mode_active = True
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.items = []
    with patch("agent_core.context_analyzer.analyze_context", return_value={"user_mood": "upset"}):
        with patch.object(assistant.memory_extractor.memory, "get_recent_context", new_callable=AsyncMock):
            await assistant._inject_emotional_context("I am sad", turn_ctx)
            assert len(turn_ctx.chat_ctx.items) == 1


@pytest.mark.asyncio
async def test_handle_anna_upset_state(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._gf_mode_active = True
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.items = []
    with patch("jarvis_identity.jarvis_id.get_anna_state", return_value={"is_upset": True}):
        with patch("jarvis_identity.jarvis_id.set_anna_mood", new_callable=AsyncMock) as mock_set_mood:
            with patch.object(assistant, "tool_toggle_gf_mode", new_callable=AsyncMock) as mock_toggle:
                await assistant._handle_anna_upset_state("I am sorry", turn_ctx)
                mock_set_mood.assert_called_with("loving", False)
                mock_toggle.assert_called_with(True)


@pytest.mark.asyncio
async def test_inject_reasoning_and_memory(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.items = []
    mock_plan = [{"step": 1, "action": "test"}]
    with patch.object(assistant.memory_extractor.memory, "get_semantic_context", new_callable=AsyncMock, return_value=["memory1"]):
        with patch("agent_core.process_with_advanced_reasoning", new_callable=AsyncMock, return_value={"is_agentic": True, "plan": mock_plan}):
            await assistant._inject_reasoning_and_memory("test", turn_ctx, is_anna=False)
            all_content = "".join([str(i.content)
                                  for i in turn_ctx.chat_ctx.items])
            assert "[LONG-TERM MEMORY CONTEXT]" in all_content


@pytest.mark.asyncio
async def test_tool_change_voice(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._active_session = MagicMock()
    assistant._active_session._activity = MagicMock()
    assistant._active_session._activity._rt_session = MagicMock()

    result = await assistant.tool_change_voice("Ash")
    assert result["status"] == "success"
    assistant._active_session._activity._rt_session.update_options.assert_called_with(
        voice="ash")


@pytest.mark.asyncio
async def test_handle_persona_switch(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._instructions = "Test"
    with patch.object(assistant, "_update_persona_instructions", new_callable=AsyncMock) as mock_update:
        await assistant._handle_persona_switch(is_anna=True, is_jarvis=False)
        assert assistant._gf_mode_active is True
        mock_update.assert_called()

        mock_update.reset_mock()
        await assistant._handle_persona_switch(is_anna=False, is_jarvis=True)
        assert assistant._gf_mode_active is False
        mock_update.assert_called()
