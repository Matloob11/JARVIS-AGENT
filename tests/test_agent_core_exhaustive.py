import pytest
import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock
from agent_core import BrainAssistant
from livekit.agents import StopResponse


@pytest.fixture
def mock_agent_deps():
    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock):
        with patch("livekit.agents.Agent.__init__", return_value=None):
            with patch("livekit.plugins.google.realtime.RealtimeModel"):
                with patch("jarvis_identity.jarvis_id.get_context", return_value="Test Context"):
                    with patch("agent_memory.MemoryExtractor"):
                        yield


@pytest.mark.asyncio
async def test_brain_assistant_init_with_params(mock_agent_deps):
    chat_ctx = MagicMock()
    # Test line 79: if current_date and current_city:
    assistant = BrainAssistant(
        chat_ctx=chat_ctx, current_date="2026-02-27", current_city="London")
    assert assistant._wake_word_mode is True


@pytest.mark.asyncio
async def test_tool_change_voice_invalid(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test line 138: Invalid voice
    result = await assistant.tool_change_voice("invalid_voice")
    assert result["status"] == "error"
    assert "invalid" in result["message"]


@pytest.mark.asyncio
async def test_tool_change_voice_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._active_session = MagicMock()
    assistant._active_session._activity = MagicMock()
    # Test lines 150-151: Exception handling
    mock_rt = MagicMock()
    mock_rt.update_options.side_effect = Exception("Test Exception")
    assistant._active_session._activity._rt_session = mock_rt

    result = await assistant.tool_change_voice("Charon")
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_update_persona_instructions_jarvis_path(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._gf_mode_active = False
    # Test lines 171-172
    with patch.object(assistant, "update_instructions", new_callable=AsyncMock) as mock_update:
        await assistant._update_persona_instructions()
        mock_update.assert_called()


@pytest.mark.asyncio
async def test_update_persona_instructions_session_sync_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._active_session = MagicMock()
    assistant._active_session._activity = MagicMock()
    # Test lines 179-189: session sync exception
    mock_rt = MagicMock()
    mock_rt.update_options.side_effect = Exception("Sync Error")
    assistant._active_session._activity._rt_session = mock_rt

    await assistant._update_persona_instructions()


@pytest.mark.asyncio
async def test_attach_session(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    mock_session = MagicMock()
    # Test line 200
    assistant.attach_session(mock_session)
    assert assistant._active_session == mock_session


@pytest.mark.asyncio
async def test_process_with_reasoning_success(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test lines 206-208
    with patch("agent_core.analyze_user_intent", new_callable=AsyncMock, return_value="intent"):
        with patch.object(assistant.memory_extractor.memory, "get_recent_context", new_callable=AsyncMock, return_value="mem"):
            with patch.object(assistant.memory_extractor.memory, "get_semantic_context", new_callable=AsyncMock, return_value="sem"):
                with patch("agent_core.generate_smart_response", new_callable=AsyncMock, return_value="smart response"):
                    result = await assistant.process_with_reasoning("test input")
                    assert result == "smart response"


@pytest.mark.asyncio
async def test_extract_text_from_message_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test lines 228-229
    mock_msg = MagicMock()
    # Use a side_effect on getattr to raise error when accessing 'content'
    with patch("agent_core.getattr", side_effect=TypeError("Test Error")):
        result = assistant._extract_text_from_message(mock_msg)
        assert result == ""


@pytest.mark.asyncio
async def test_handle_wake_word_none_detected(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test line 237
    detected, is_anna = await assistant._handle_wake_word("no wake word here")
    assert detected is False
    assert is_anna is False


@pytest.mark.asyncio
async def test_inject_emotional_context_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test lines 249-250
    with patch.object(assistant.memory_extractor.memory, "get_recent_context", side_effect=Exception("DB Error")):
        await assistant._inject_emotional_context("test", MagicMock())


@pytest.mark.asyncio
async def test_handle_anna_upset_state_branches(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Line 255
    assistant._gf_mode_active = False
    await assistant._handle_anna_upset_state("sorry", MagicMock())

    assistant._gf_mode_active = True
    # Line 258
    with patch("jarvis_identity.jarvis_id.get_anna_state", return_value={"is_upset": False, "mood": "loving"}):
        await assistant._handle_anna_upset_state("sorry", MagicMock())

    # Line 263
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.items = []
    with patch("jarvis_identity.jarvis_id.get_anna_state", return_value={"is_upset": True, "mood": "loving"}):
        await assistant._handle_anna_upset_state("something else", turn_ctx)
        assert len(turn_ctx.chat_ctx.items) == 1


@pytest.mark.asyncio
async def test_inject_reasoning_and_memory_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test lines 277-278
    with patch.object(assistant.memory_extractor.memory, "get_semantic_context", side_effect=Exception("Error")):
        await assistant._inject_reasoning_and_memory("test", MagicMock(), False)


@pytest.mark.asyncio
async def test_on_user_turn_completed_exhaustive(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._wake_word_mode = True
    turn_ctx = MagicMock()
    new_message = MagicMock()

    # Branch: Detected, not muted
    with patch.object(assistant, "_extract_text_from_message", return_value="jarvis"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(True, False)):
            with patch.object(assistant, "_handle_anna_upset_state", new_callable=AsyncMock):
                with patch.object(assistant, "_inject_emotional_context", new_callable=AsyncMock):
                    with patch.object(assistant, "_inject_reasoning_and_memory", new_callable=AsyncMock):
                        with patch("livekit.agents.Agent.on_user_turn_completed", new_callable=AsyncMock) as mock_super:
                            await assistant.on_user_turn_completed(turn_ctx, new_message)
                            mock_super.assert_called_once()

    # Branch: Not detected
    with patch.object(assistant, "_extract_text_from_message", return_value="hello"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(False, False)):
            with pytest.raises(StopResponse):
                await assistant.on_user_turn_completed(turn_ctx, new_message)

    # Branch: Muted
    assistant._muted = True
    with patch.object(assistant, "_extract_text_from_message", return_value="jarvis"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(True, False)):
            with pytest.raises(StopResponse):
                await assistant.on_user_turn_completed(turn_ctx, new_message)
    assistant._muted = False

    # Branch: Exception during turn
    with patch.object(assistant, "_extract_text_from_message", return_value="jarvis"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(True, False)):
            with patch("livekit.agents.Agent.on_user_turn_completed", side_effect=ImportError("Fail")):
                with pytest.raises(StopResponse):
                    await assistant.on_user_turn_completed(turn_ctx, new_message)

    # Branch: History pop (Line 295)
    assistant.conversation_history = [
        {"role": "user", "content": f"msg{i}"} for i in range(20)]
    with patch.object(assistant, "_extract_text_from_message", return_value="jarvis"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(True, False)):
            with patch.object(assistant, "_handle_anna_upset_state", new_callable=AsyncMock):
                with patch.object(assistant, "_inject_emotional_context", new_callable=AsyncMock):
                    with patch.object(assistant, "_inject_reasoning_and_memory", new_callable=AsyncMock):
                        with patch("livekit.agents.Agent.on_user_turn_completed", new_callable=AsyncMock):
                            await assistant.on_user_turn_completed(turn_ctx, new_message)
                            assert len(assistant.conversation_history) == 20
                            assert assistant.conversation_history[0]["content"] == "msg1"

    # Branch: StopResponse handled (Line 299)
    with patch.object(assistant, "_extract_text_from_message", return_value="jarvis"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(True, False)):
            with patch("livekit.agents.Agent.on_user_turn_completed", side_effect=StopResponse()):
                with pytest.raises(StopResponse):
                    await assistant.on_user_turn_completed(turn_ctx, new_message)

    # Branch: asyncio.CancelledError handled (Line 299)
    with patch.object(assistant, "_extract_text_from_message", return_value="jarvis"):
        with patch.object(assistant, "_handle_wake_word", new_callable=AsyncMock, return_value=(True, False)):
            with patch("livekit.agents.Agent.on_user_turn_completed", side_effect=asyncio.CancelledError()):
                with pytest.raises(asyncio.CancelledError):
                    await assistant.on_user_turn_completed(turn_ctx, new_message)

    # Branch: _wake_word_mode is False
    assistant._wake_word_mode = False
    with patch("livekit.agents.Agent.on_user_turn_completed", new_callable=AsyncMock) as mock_super:
        await assistant.on_user_turn_completed(turn_ctx, new_message)
        mock_super.assert_called_once()
