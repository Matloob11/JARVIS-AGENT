import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock
from src.core.agent_core import BrainAssistant

@pytest.fixture
def mock_agent_deps():
    # Mocking Agent.__init__ to avoid real LiveKit setup
    with patch("livekit.agents.Agent.__init__", return_value=None):
        with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock):
            with patch("src.core.agent_core.jarvis_id.get_context", return_value="Test Context"):
                with patch("src.core.agent_core.JarvisPluginManager") as mock_plugins:
                    mock_plugins.return_value.discover_plugins.return_value = None
                    with patch("src.core.agent_core.MemoryExtractor") as mock_mem:
                        inst = mock_mem.return_value
                        inst.memory = MagicMock()
                        inst.memory.get_recent_context = AsyncMock(return_value=[])
                        inst.memory.get_semantic_context = AsyncMock(return_value=[])
                        yield inst

def create_test_assistant(mock_mem_inst):
    chat_ctx = MagicMock()
    with patch("livekit.agents.Agent.llm", new_callable=PropertyMock) as mock_llm_prop:
        mock_llm = MagicMock()
        mock_llm.voice = "charon"
        mock_llm_prop.return_value = mock_llm
        
        assistant = BrainAssistant(chat_ctx=chat_ctx, llm_instance=mock_llm, tools=[])
        assistant._instructions = "Test Context"
        # Fix the property access error by providing the underlying private attribute
        assistant._llm = mock_llm
        # Ensure memory extractor is the one from fixture
        assistant.memory_extractor = mock_mem_inst
        return assistant, mock_llm

@pytest.mark.asyncio
async def test_brain_assistant_init(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    assert assistant._wake_word_mode is True
    assert assistant.persona_manager.is_anna is False
    assert assistant._instructions == "Test Context"

@pytest.mark.asyncio
async def test_tool_set_wake_word_mode(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    result = await assistant.tool_set_wake_word_mode(False)
    assert result["status"] == "success"
    assert assistant._wake_word_mode is False

@pytest.mark.asyncio
async def test_tool_toggle_gf_mode(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch.object(assistant.persona_manager, "update_assistant_persona", new_callable=AsyncMock) as mock_update:
        # Persona switch calls set_persona which uses update_assistant_persona
        result = await assistant.tool_toggle_gf_mode(True)
        assert result["status"] == "success"
        assert assistant.persona_manager.is_anna is True
        mock_update.assert_called_once()

@pytest.mark.asyncio
async def test_extract_text_from_message(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    msg_simple = MagicMock()
    msg_simple.content = "Hello Jarvis"
    assert assistant._extract_text_from_message(msg_simple) == "hello jarvis"

@pytest.mark.asyncio
async def test_handle_wake_word(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch.object(assistant.persona_manager, "set_persona", new_callable=AsyncMock) as mock_update:
        detected, is_anna = await assistant._handle_wake_word("Hey Jarvis")
        assert detected is True
        assert is_anna is False

@pytest.mark.asyncio
async def test_inject_emotional_context(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    assistant.persona_manager._gf_mode_active = True
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.messages = []
    with patch("services.ai_core.jarvis_reasoning.context_analyzer.analyze_context", return_value={"user_mood": "upset"}):
        await assistant.persona_manager.inject_emotional_context("I am sad", turn_ctx, assistant.conversation_history)
        assert len(turn_ctx.chat_ctx.messages) == 1
        assert "Manao him." in turn_ctx.chat_ctx.messages[0].content[0]

@pytest.mark.asyncio
async def test_handle_anna_upset_state(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    assistant.persona_manager._gf_mode_active = True
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.messages = []
    with patch("services.ai_core.jarvis_identity.jarvis_id.get_anna_state", return_value={"is_upset": True}):
        with patch("services.ai_core.jarvis_identity.jarvis_id.set_anna_mood", new_callable=AsyncMock) as mock_set_mood:
            with patch.object(assistant.persona_manager, "set_persona", new_callable=AsyncMock) as mock_toggle:
                await assistant.persona_manager.handle_anna_upset_state("I am sorry", turn_ctx)
                mock_set_mood.assert_called_with("loving", False)
                mock_toggle.assert_called_with(True)

@pytest.mark.asyncio
async def test_inject_reasoning_and_memory(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    turn_ctx = MagicMock()
    turn_ctx.chat_ctx.messages = []
    
    # Correcting the mock response to match Semantic context result structure
    assistant.memory_extractor.memory.get_semantic_context.return_value = [{"document": "memory1", "score": 0.9}]
    
    with patch("services.ai_core.jarvis_reasoning.process_with_advanced_reasoning", new_callable=AsyncMock, return_value={"is_agentic": True, "plan": "test plan"}):
        await assistant._inject_reasoning_and_memory("test", turn_ctx, is_anna=False)
        all_content = "".join([str(i.content[0]) for i in turn_ctx.chat_ctx.messages])
        assert "[LONG-TERM MEMORY CONTEXT]" in all_content

@pytest.mark.asyncio
async def test_tool_change_voice(mock_agent_deps):
    assistant, mock_llm = create_test_assistant(mock_agent_deps)
    assistant._active_session = MagicMock()
    assistant._active_session._activity = MagicMock()
    assistant._active_session._activity._rt_session = MagicMock()

    result = await assistant.tool_change_voice("Ash")
    assert result["status"] == "success"
    assert mock_llm.voice == "ash"

@pytest.mark.asyncio
async def test_handle_persona_switch(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch.object(assistant.persona_manager, "update_assistant_persona", new_callable=AsyncMock) as mock_update:
        await assistant._handle_persona_switch(is_anna=True, is_jarvis=False)
        assert assistant.persona_manager.is_anna is True
        mock_update.assert_called()
