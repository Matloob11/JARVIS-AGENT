import pytest
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock
from src.core.agent_core import BrainAssistant
from livekit.agents import StopResponse


@pytest.fixture
def mock_agent_deps():
    # C-61: BrainAssistant starts its task monitor inside a running loop; keep that monitor off
    # the real conversations/tasks.db, where it would mark the live agent's due tasks completed.
    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock), \
            patch("src.core.agent_core.task_manager",
                  MagicMock(get_due_tasks=MagicMock(return_value=[]))), \
            patch("src.core.agent_core.bridge_notifier", AsyncMock()):  # C-50: no bridge POST from a test
        with patch("livekit.agents.Agent.__init__", return_value=None):
            with patch("src.core.agent_core.jarvis_id.get_context", return_value="Test Context"):
                with patch("src.core.agent_core.JarvisPluginManager") as mock_plugins:
                    mock_plugins.return_value.discover_plugins.return_value = None
                    with patch("src.core.agent_core.MemoryExtractor") as mock_mem:
                        inst = mock_mem.return_value
                        inst.memory = MagicMock()
                        inst.memory.get_recent_context = AsyncMock(return_value=[])
                        inst.memory.get_semantic_context = AsyncMock(return_value=[])
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
    assert "Valid voices:" in result["message"]  # C-50: the valid names, not just the echoed input
    assert assistant._pending_voice is None


@pytest.mark.asyncio
async def test_tool_change_voice_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # C-50: the live session's own update_options fails: no reconnect, the tool still answers
    rt = SimpleNamespace(_opts=SimpleNamespace(voice="Charon"), _session_resumption_handle="resume-handle",
                         update_options=MagicMock(side_effect=Exception("Test Exception")),
                         _mark_restart_needed=MagicMock())
    assistant._activity = SimpleNamespace(update_instructions=AsyncMock(), realtime_llm_session=rt)
    assistant._active_session = SimpleNamespace(agent_state="listening", current_speech=None)

    with patch("src.core.agent_core.logger") as log:
        result = await assistant.tool_change_voice("Puck")
    assert result["status"] == "success"
    rt.update_options.assert_called_once_with(voice="Puck")
    rt._mark_restart_needed.assert_not_called()
    assert rt._session_resumption_handle == "resume-handle"
    assert any("VOICE_UPDATE" in str(call.args[0]) for call in log.warning.call_args_list)


@pytest.mark.asyncio
async def test_update_persona_instructions_jarvis_path(mock_agent_deps, monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE_JARVIS", raising=False)
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant._gf_mode_active = False
    # C-49/C-50: the persona's instructions and voice go out together through update_session
    with patch.object(assistant, "update_session", new_callable=AsyncMock) as mock_update:
        await assistant._update_persona_instructions()
    mock_update.assert_awaited_once()
    assert "- Persona: JARVIS" in mock_update.await_args.kwargs["instructions"]
    assert mock_update.await_args.kwargs["voice"] == "Charon"


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
    # Test lines 206-208. C-138: the telemetry singleton is patched, so this test never appends its
    # 'test input' row to the live logs/telemetry.jsonl.
    with patch("src.core.agent_core.telemetry") as mock_telemetry:
        mock_telemetry.start_interaction = AsyncMock()
        mock_telemetry.end_interaction = AsyncMock()
        with patch("src.core.agent_core.analyze_user_intent", new_callable=AsyncMock, return_value="intent"):
            with patch.object(assistant.memory_extractor.memory, "get_recent_context", new_callable=AsyncMock, return_value="mem"):
                with patch.object(assistant.memory_extractor.memory, "get_semantic_context", new_callable=AsyncMock, return_value="sem"):
                    with patch("src.core.agent_core.generate_smart_response", new_callable=AsyncMock, return_value="smart response") as mock_generate:
                        result = await assistant.process_with_reasoning("test input")
    assert result == "smart response"
    mock_generate.assert_awaited_once()
    assert mock_generate.await_args.args[:3] == ("test input", "intent", "mem")
    assert mock_generate.await_args.kwargs["semantic_memory"] == ["sem"]
    session_id = mock_telemetry.start_interaction.await_args.args[0]
    mock_telemetry.start_interaction.assert_awaited_once_with(session_id, "test input")
    mock_telemetry.end_interaction.assert_awaited_once_with(session_id, success=True)


@pytest.mark.asyncio
async def test_extract_text_from_message_exception(mock_agent_deps):
    assistant = BrainAssistant(chat_ctx=MagicMock())
    # Test lines 228-229
    mock_msg = MagicMock()
    # Use a side_effect on getattr to raise error when accessing 'content'
    with patch("src.core.agent_core.getattr", side_effect=TypeError("Test Error")):
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
    # C-50 review: the persona manager's own jarvis_id (the old 'jarvis_identity.jarvis_id' patch hit a second copy
    # of the module and left the live identity file in play); its note is checked, not the legacy fallback's
    from livekit.agents import llm

    identity = "src.core.persona_manager.jarvis_id"
    assistant = BrainAssistant(chat_ctx=MagicMock())
    assistant.update_instructions = AsyncMock()
    with patch(f"{identity}.set_anna_mood", new_callable=AsyncMock) as set_mood:
        assistant._gf_mode_active = False  # not ANNA: nothing
        turn = llm.ChatContext.empty()
        with patch(f"{identity}.get_anna_state", return_value={"is_upset": True, "mood": "upset"}):
            await assistant._handle_anna_upset_state("sorry", turn)
        assert turn.items == [] and set_mood.await_count == 0

        assistant._gf_mode_active = True  # ANNA, not upset: nothing
        with patch(f"{identity}.get_anna_state", return_value={"is_upset": False, "mood": "loving"}):
            await assistant._handle_anna_upset_state("sorry", turn)
        assert turn.items == [] and set_mood.await_count == 0

        with patch(f"{identity}.get_anna_state", return_value={"is_upset": True, "mood": "upset"}):
            await assistant._handle_anna_upset_state("something else", turn)  # upset: she asks for a sorry
            assert [item.text_content for item in turn.items] == ["Demand sorry."]
            await assistant._handle_anna_upset_state("sorry jaan", turn)  # the sorry: mood and prompt
        set_mood.assert_awaited_once_with("loving", False)
        assistant.update_instructions.assert_awaited_once()
        assert len(turn.items) == 1


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
    assistant.vision_handler.handle_vision_query = AsyncMock()
    assistant.vision_handler.inject_window_context = AsyncMock()
    assistant._handle_anna_upset_state = AsyncMock()
    assistant._inject_emotional_context = AsyncMock()
    assistant._inject_reasoning_and_memory = AsyncMock()
    assistant.prune_chat_context = AsyncMock()
    def close_background(coro):
        if hasattr(coro, "close"):
            coro.close()
    assistant._run_back = close_background
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
