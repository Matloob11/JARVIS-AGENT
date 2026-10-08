import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock
from livekit.agents import llm
from src.core.agent_core import BrainAssistant

@pytest.fixture
def mock_agent_deps(monkeypatch):
    monkeypatch.delenv("JARVIS_TURN_MODE", raising=False)  # C-49: the prompt's wake rule follows it
    monkeypatch.delenv("JARVIS_VOICE_JARVIS", raising=False)  # C-50: the persona voices follow these
    monkeypatch.delenv("JARVIS_VOICE_ANNA", raising=False)
    # C-50: a persona switch tells the dashboard in the background; no bridge POST from a test
    monkeypatch.setattr("src.core.agent_core.bridge_notifier", AsyncMock())
    # C-61: the assistant starts its task monitor inside the test's loop; keep it off the real conversations/tasks.db
    monkeypatch.setattr("src.core.agent_core.task_manager", MagicMock(get_due_tasks=MagicMock(return_value=[])))
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

    msg_mixed = MagicMock()
    msg_mixed.content = ["Hello", SimpleNamespace(text="Camera"), SimpleNamespace(content="Context")]
    extracted = assistant._extract_text_from_message(msg_mixed)
    assert "hello" in extracted
    assert "camera" in extracted

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
    turn_ctx = llm.ChatContext.empty()  # C-50: what livekit hands on_user_turn_completed
    with patch("services.ai_core.jarvis_reasoning.context_analyzer.analyze_context", return_value={"user_mood": "upset"}):
        await assistant.persona_manager.inject_emotional_context("I am sad", turn_ctx, assistant.conversation_history)
        assert [item.text_content for item in turn_ctx.items] == ["Manao him."]

@pytest.mark.asyncio
async def test_handle_anna_upset_state(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    assistant.persona_manager._gf_mode_active = True
    turn_ctx = llm.ChatContext.empty()
    with patch("services.ai_core.jarvis_identity.jarvis_id.get_anna_state", return_value={"is_upset": True}):
        with patch("services.ai_core.jarvis_identity.jarvis_id.set_anna_mood", new_callable=AsyncMock) as mock_set_mood:
            with patch.object(assistant, "update_instructions", new_callable=AsyncMock) as mock_refresh, \
                    patch.object(assistant.persona_manager, "update_assistant_persona",
                                 new_callable=AsyncMock) as mock_switch:
                await assistant.persona_manager.handle_anna_upset_state("I am sorry", turn_ctx)
                mock_set_mood.assert_called_with("loving", False)
                mock_refresh.assert_awaited_once()  # C-49: the calmer mood reaches the live prompt
                assert "- Persona: ANNA" in mock_refresh.await_args.args[0]
                mock_switch.assert_not_awaited()  # C-50: no persona voice and no persona_change for a mood
    assert turn_ctx.items == []  # sorry was said: no 'Demand sorry.' note

@pytest.mark.asyncio
async def test_inject_reasoning_and_memory(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    turn_ctx = llm.ChatContext.empty()  # C-50: what livekit hands on_user_turn_completed

    # Correcting the mock response to match Semantic context result structure
    assistant.memory_extractor.memory.get_semantic_context.return_value = [{"document": "memory1", "score": 0.9}]
    
    with patch("src.core.agent_core.process_with_advanced_reasoning", new_callable=AsyncMock, return_value={"is_agentic": True, "plan": "test plan"}):
        await assistant._inject_reasoning_and_memory("test", turn_ctx, is_anna=False)
    assert [item.text_content for item in turn_ctx.items] == ["[LONG-TERM MEMORY CONTEXT]: memory1",
                                                             "[EXECUTION PLAN]: test plan"]

@pytest.mark.asyncio
async def test_tool_change_voice(mock_agent_deps):
    assistant, mock_llm = create_test_assistant(mock_agent_deps)
    result = await assistant.tool_change_voice("Ash")
    assert result["status"] == "success"
    assert result["voice"] == "Orus"  # C-50: the OpenAI-style alias maps to a real Gemini voice
    # no live session: the voice waits in the model options for the next session
    mock_llm.update_options.assert_called_once_with(voice="Orus")


@pytest.mark.asyncio
async def test_verify_speaker_identity_uses_audio_sample_rate(mock_agent_deps, monkeypatch):
    monkeypatch.setenv("JARVIS_TEST_MODE", "false")
    assistant, _ = create_test_assistant(mock_agent_deps)
    assistant._audio_sample_rate = 48000
    assistant._audio_buffer.extend(b"\1" * 96000)
    assistant.voice_id_engine = MagicMock()
    assistant.voice_id_engine.verify_bytes = AsyncMock(return_value=(True, 0.91))

    assert await assistant.verify_speaker_identity() is True
    assistant.voice_id_engine.verify_bytes.assert_awaited_once()
    _, kwargs = assistant.voice_id_engine.verify_bytes.await_args
    assert kwargs["sample_rate"] == 48000
    assert kwargs["threshold"] == 0.65

@pytest.mark.asyncio
async def test_handle_persona_switch(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch.object(assistant.persona_manager, "update_assistant_persona", new_callable=AsyncMock) as mock_update:
        await assistant._handle_persona_switch(is_anna=True, is_jarvis=False)
        assert assistant.persona_manager.is_anna is True
        mock_update.assert_called()


@pytest.mark.asyncio
async def test_handle_user_query_delegates_to_an_attached_router(mock_agent_deps):
    """C-42 step 8: with a router the query goes into the live session; the router emits the messages."""
    assistant, _ = create_test_assistant(mock_agent_deps)
    router = MagicMock()
    router.handle_text = AsyncMock(return_value="accepted")
    assistant.attach_router(router)
    with patch.object(assistant, "process_with_reasoning", new_callable=AsyncMock) as template:
        assert await assistant.handle_user_query("Jarvis, 7 times 8?") == ""
    router.handle_text.assert_awaited_once_with("Jarvis, 7 times 8?", origin="query")
    template.assert_not_awaited()
    assert assistant.conversation_router is router


@pytest.mark.asyncio
async def test_handle_user_query_without_a_router_keeps_the_template_path(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    assert assistant.conversation_router is None
    with patch.object(assistant, "process_with_reasoning", new_callable=AsyncMock,
                      return_value="Ji Sir") as template:
        assert await assistant.handle_user_query("salam; Sir") == "Ji Sir"
    template.assert_awaited_once_with("salam Sir")


# ---------------------------------------------------------------- C-49: instructions reach Gemini

from livekit.agents import Agent as _LiveKitAgent  # noqa: E402

from src.core import persona_manager as _pm  # noqa: E402

REAL_AGENT_UPDATE_INSTRUCTIONS = _LiveKitAgent.update_instructions  # taken before any fixture patches it


class _FakeRealtimeSession:
    """The google RealtimeSession members restart_fresh uses."""

    def __init__(self) -> None:
        self._session_resumption_handle = "resume-handle"
        self.restarts = 0

    def _mark_restart_needed(self) -> None:
        self.restarts += 1


def _live(assistant, state="listening", fail=False):
    """A running activity (and so a Gemini session) behind the assistant."""
    rt = _FakeRealtimeSession()
    update = AsyncMock(side_effect=RuntimeError("session closing") if fail else None)
    assistant._activity = SimpleNamespace(update_instructions=update, realtime_llm_session=rt)
    assistant._active_session = SimpleNamespace(agent_state=state)
    return update, rt


@pytest.mark.asyncio
async def test_new_instructions_replace_the_system_instruction_with_a_fresh_reconnect(mock_agent_deps):
    """livekit's Agent.update_instructions hands them to the activity; the resumption handle is dropped and the session
    reconnects, so they become the system instruction instead of one more appended turn."""
    assistant, _ = create_test_assistant(mock_agent_deps)
    update, rt = _live(assistant)
    with patch("livekit.agents.Agent.update_instructions", REAL_AGENT_UPDATE_INSTRUCTIONS):
        await assistant.update_instructions("NEW RULES")
    update.assert_awaited_once_with("NEW RULES")
    assert rt._session_resumption_handle is None and rt.restarts == 1
    assert assistant._instructions == "NEW RULES" and assistant._pending_instructions is None


@pytest.mark.asyncio
async def test_instructions_wait_until_the_agent_listens(mock_agent_deps):
    """Never inside a reply or a tool call (a tool that switches persona runs while the agent thinks)."""
    assistant, _ = create_test_assistant(mock_agent_deps)
    update, rt = _live(assistant, state="thinking")
    with patch("livekit.agents.Agent.update_instructions", REAL_AGENT_UPDATE_INSTRUCTIONS):
        await assistant.update_instructions("ANNA RULES")
        assert assistant._instructions == "ANNA RULES"  # kept at once for a rebuilt session
        update.assert_not_awaited()
        assistant._active_session.agent_state = "speaking"
        await assistant.on_agent_state("speaking")
        update.assert_not_awaited()
        assistant._active_session.agent_state = "listening"
        await assistant.on_agent_state("listening")
        await assistant.on_agent_state("listening")  # nothing pending any more
    update.assert_awaited_once_with("ANNA RULES")
    assert rt.restarts == 1


@pytest.mark.asyncio
async def test_a_realtime_error_never_breaks_the_switch(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    _update, rt = _live(assistant, fail=True)
    with patch("livekit.agents.Agent.update_instructions", REAL_AGENT_UPDATE_INSTRUCTIONS):
        await assistant.update_instructions("RULES")
    assert assistant._instructions == "RULES" and rt.restarts == 0  # kept for the next session


@pytest.mark.asyncio
async def test_without_a_live_session_the_instructions_are_only_stored(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock) as stored:
        await assistant.update_instructions("RULES")
    stored.assert_awaited_once_with("RULES")
    assert assistant._instructions == "RULES" and assistant._pending_instructions is None


@pytest.mark.asyncio
async def test_a_new_session_drops_pending_instructions(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    assistant._probe = True  # no vision loop
    assistant._pending_instructions = "OLD"
    # the new session's Gemini session started with them (AgentActivity sets the shared options' instructions)
    assistant._activity = SimpleNamespace(update_instructions=AsyncMock(), realtime_llm_session=SimpleNamespace(
        _opts=SimpleNamespace(instructions="OLD")))
    assistant.attach_session(MagicMock())
    assert assistant._pending_instructions is None  # the new session starts with self._instructions
    assistant._activity.update_instructions.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_persona_switch_gives_anna_every_tool_protocol(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock) as sent:
        await assistant.persona_manager.set_persona(True)
        anna = sent.await_args.args[0]
        await assistant.persona_manager.set_persona(False)
        jarvis = sent.await_args.args[0]
    assert "- Persona: ANNA" in anna and "Aap Anna hain" in anna and "EXACT TOOL NAMES REFERENCE" in anna
    assert "PROTOCOLS KA ANDAZ" in anna and "ALWAYS execute tool first, then speak" in anna
    assert "- Persona: JARVIS" in jarvis and "{current_date}" not in jarvis  # the old switch-back sent raw placeholders
    assert assistant._instructions == jarvis


@pytest.mark.asyncio
async def test_the_wake_word_tool_recomposes_the_instructions(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock) as sent:
        await assistant.tool_set_wake_word_mode(False)
        off = sent.await_args.args[0]
        await assistant.tool_set_wake_word_mode(True)
        on = sent.await_args.args[0]
    assert "WAKE WORD WAITING MODE" not in off and "🌟 WAKE WORD: OFF" in off and "- Wake word: OFF" in off
    assert "WAKE WORD WAITING MODE" in on and "- Wake word: ON" in on


@pytest.mark.asyncio
async def test_the_dashboard_wake_toggle_recomposes_too(mock_agent_deps):
    """The UI's wake_word_toggle runs the sync set_wake_word_mode (agent_loops): the refresh runs in the background."""
    assistant, _ = create_test_assistant(mock_agent_deps)
    assistant.bridge_notifier = MagicMock(notify_wake_word=AsyncMock(), notify_persona=AsyncMock())

    async def toggle(active: bool) -> str:
        before = set(assistant._background_tasks)  # the assistant's own long-running loops are in there too
        assistant.set_wake_word_mode(active)
        await asyncio.wait_for(asyncio.gather(*(assistant._background_tasks - before)), 5)
        return sent.await_args.args[0]

    with patch("livekit.agents.Agent.update_instructions", new_callable=AsyncMock) as sent:
        off = await toggle(False)
        on = await toggle(True)
    assert "🌟 WAKE WORD: OFF" in off and "ALWAYS execute tool first, then speak" in off
    assert "WAKE WORD WAITING MODE" in on


@pytest.mark.asyncio
async def test_a_new_assistant_starts_with_the_composed_jarvis_prompt(mock_agent_deps):
    with patch("livekit.agents.Agent.__init__", return_value=None) as init:
        BrainAssistant(chat_ctx=MagicMock(), llm_instance=MagicMock(), tools=[], current_date="2026-10-08",
                       current_city="Lahore")
    instructions = init.call_args.kwargs["instructions"]
    assert "- Persona: JARVIS" in instructions and "- Current Date: 2026-10-08" in instructions
    assert "Test Context" in instructions  # the identity context (the fixture's) is kept
    assert "WAKE WORD WAITING MODE" in instructions and "{current_date}" not in instructions


def test_build_instructions_carries_the_live_context(monkeypatch):
    monkeypatch.setattr(_pm.config, "cv_path", r"C:\cv\Matloob.pdf", raising=False)
    monkeypatch.setattr(_pm.config, "outputs_dir", r"C:\proj\Jarvis_Outputs", raising=False)
    monkeypatch.setattr(_pm.jarvis_id, "get_anna_state", lambda: {"mood": "sad", "is_upset": True})
    monkeypatch.setattr(_pm.jarvis_id, "get_context", lambda: "IDENTITY FACTS")
    monkeypatch.delenv("JARVIS_TURN_MODE", raising=False)
    assistant = SimpleNamespace(current_date="2026-10-08", current_city="Lahore", _wake_word_mode=False)
    text = _pm.build_instructions(assistant, "anna")
    assert 'play_file(name="C:\\cv\\Matloob.pdf")' in text and "C:\\proj\\Jarvis_Outputs" in text
    assert "- Current Mood: sad" in text and "- Upset Status: True" in text and "IDENTITY FACTS" in text
    assert "🌟 WAKE WORD: OFF" in text  # the assistant's own wake-word mode
    assert "WAKE WORD WAITING MODE" in _pm.build_instructions(assistant, "jarvis", wake_word=True)


def test_gated_mode_waits_for_a_gated_runtime(monkeypatch, caplog):
    """JARVIS_TURN_MODE=gated alone never drops the wake rule while no gated runtime exists (C-66)."""
    monkeypatch.setenv("JARVIS_TURN_MODE", "gated")
    monkeypatch.setattr(_pm, "_gated_warned", False)
    assistant = SimpleNamespace(current_date="d", current_city="c", _wake_word_mode=True)
    assert "WAKE WORD WAITING MODE" in _pm.build_instructions(assistant, "jarvis")
    monkeypatch.setattr(_pm, "GATED_RUNTIME_AVAILABLE", True)
    assert "🌟 WAKE WORD: GATED" in _pm.build_instructions(assistant, "jarvis")


class _Speech:
    def __init__(self) -> None:
        self._done, self.callbacks = False, []

    def done(self) -> bool:
        return self._done

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)

    def finish(self) -> None:
        self._done = True
        for callback in self.callbacks:
            callback(self)


@pytest.mark.asyncio
async def test_an_update_inside_a_tool_call_waits_for_the_speech_to_end(mock_agent_deps):
    """Live 08 Oct: toggle_persona runs while the state still says 'listening'; a restart then dropped the tool's
    reply. The running speech (not done) keeps the update pending until it ends."""
    assistant, _ = create_test_assistant(mock_agent_deps)
    update, rt = _live(assistant, state="listening")
    speech = _Speech()
    assistant._active_session.current_speech = speech
    with patch("livekit.agents.Agent.update_instructions", REAL_AGENT_UPDATE_INSTRUCTIONS):
        before = set(assistant._background_tasks)
        await assistant.update_instructions("ANNA RULES")
        update.assert_not_awaited()
        speech.finish()  # the tool call and its reply are over
        await asyncio.wait_for(asyncio.gather(*(assistant._background_tasks - before)), 5)
    update.assert_awaited_once_with("ANNA RULES")
    assert rt.restarts == 1


@pytest.mark.asyncio
async def test_a_persona_switch_applies_instructions_and_voice_in_one_fresh_reconnect(mock_agent_deps):
    assistant, _ = create_test_assistant(mock_agent_deps)
    update, rt = _live(assistant, state="listening")
    rt.voices = []
    rt.update_options = lambda **kwargs: rt.voices.append(kwargs.get("voice"))
    with patch("livekit.agents.Agent.update_instructions", REAL_AGENT_UPDATE_INSTRUCTIONS):
        await assistant.persona_manager.set_persona(True)
    assert "- Persona: ANNA" in update.await_args.args[0]
    assert rt.voices == ["Aoede"] and rt.restarts == 1  # one reconnect carries both
