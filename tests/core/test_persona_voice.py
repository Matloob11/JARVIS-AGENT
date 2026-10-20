"""C-50 (VR-14): real Gemini voices and a voice switch that actually restarts the Gemini session.

tool_change_voice set a non-existent attribute and reported success; RealtimeModel.update_options sets the options a
live RealtimeSession shares first, so the session saw no change and never restarted. Now a voice resolves to one of the
30 Gemini voices (or an OpenAI-style alias), waits for the next safe point and reaches the session through the
session's own update_options plus a fresh reconnect. No LiveKit room or model: fakes stand in for both, and the fake
model owns the options its sessions share, as livekit-plugins-google 1.4.6 does.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import ast
import asyncio
import typing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from livekit.agents import llm
from livekit.agents.llm.chat_context import _ReadOnlyChatContext
from livekit.plugins.google.realtime import api_proto

from src.core import persona_manager as pm
from src.core import voices
from src.core.agent_core import BrainAssistant

AGENT_RUNNER = Path(__file__).resolve().parents[2] / "src" / "core" / "agent_runner.py"


class FakeRealtimeSession:
    """The google RealtimeSession's voice handling: it SHARES the model's options and restarts only on a change."""

    def __init__(self, opts: SimpleNamespace) -> None:
        self._opts = opts
        self._session_resumption_handle = "resume-handle"
        self.voice_changes = 0  # restarts the session's own update_options asked for
        self.restarts = 0  # every _mark_restart_needed (restart_fresh's included)

    def update_options(self, *, voice: str | None = None) -> None:
        if voice is not None and self._opts.voice != voice:
            self._opts.voice = voice
            self.voice_changes += 1
            self._mark_restart_needed()

    def _mark_restart_needed(self) -> None:
        self.restarts += 1


class FakeModel:
    """The google RealtimeModel's voice handling: it owns the options; update_options sets them first, then asks each
    session, which then compares the new voice with itself."""

    def __init__(self) -> None:
        self._opts = SimpleNamespace(voice="Charon", instructions=None)
        self.sessions: list[FakeRealtimeSession] = []
        self.option_updates: list[str] = []

    def session(self) -> FakeRealtimeSession:
        session = FakeRealtimeSession(self._opts)
        self.sessions.append(session)
        return session

    def update_options(self, *, voice: str) -> None:
        self.option_updates.append(voice)
        self._opts.voice = voice
        for session in self.sessions:
            session.update_options(voice=self._opts.voice)


class _Speech:
    """A running reply (a realtime tool call runs while the agent state may still say 'listening')."""

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


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def assistant(monkeypatch, model):
    monkeypatch.delenv("JARVIS_TURN_MODE", raising=False)
    monkeypatch.delenv("JARVIS_VOICE_JARVIS", raising=False)
    monkeypatch.delenv("JARVIS_VOICE_ANNA", raising=False)
    with patch("livekit.agents.Agent.__init__", return_value=None), \
            patch("src.core.agent_core.jarvis_id.get_context", return_value="Test Context"), \
            patch("src.core.agent_core.JarvisPluginManager") as plugins, \
            patch("src.core.agent_core.MemoryExtractor"), \
            patch("livekit.agents.Agent.llm", new_callable=PropertyMock) as llm_prop:
        plugins.return_value.discover_plugins.return_value = None
        llm_prop.return_value = model
        brain = BrainAssistant(chat_ctx=MagicMock(), llm_instance=model, tools=[])
        brain._llm = model
        brain._activity = None  # what livekit's Agent.__init__ sets
        brain.bridge_notifier = AsyncMock()  # no bridge POST from a test
        brain.vision_handler = MagicMock()  # attach_session starts no vision loop
        yield brain


def _live(brain: BrainAssistant, model: FakeModel, state: str = "listening",
          speech: _Speech | None = None) -> FakeRealtimeSession:
    """A running activity, and so a Gemini session of the job's model, behind the assistant."""
    rt = model.session()
    brain._activity = SimpleNamespace(update_instructions=AsyncMock(), realtime_llm_session=rt)
    brain._active_session = SimpleNamespace(agent_state=state, current_speech=speech)
    return rt


async def _settle(brain: BrainAssistant) -> None:
    """Waits for the assistant's background work (a deferred apply, a dashboard notify)."""
    for _ in range(10):
        if not brain._background_tasks:
            return
        await asyncio.wait_for(asyncio.gather(*brain._background_tasks), 5)


def test_the_voice_list_is_the_plugins():
    assert voices.GEMINI_VOICES == typing.get_args(api_proto.Voice) == voices._PINNED_VOICES
    assert len(voices.GEMINI_VOICES) == 30 and set(voices.OPENAI_ALIAS.values()) <= set(voices.GEMINI_VOICES)


@pytest.mark.parametrize("name, voice", [("puck", "Puck"), (" CHARON ", "Charon"), ("Zubenelgenubi", "Zubenelgenubi"),
                                         ("verse", "Puck"), ("alloy", "Zephyr"), ("shimmer", "Aoede"),
                                         ("xyz", None), ("", None), (None, None), (5, None)])
def test_resolve_voice_handles_case_aliases_and_junk(name, voice):
    assert voices.resolve_voice(name) == voice


def test_the_persona_voices_come_from_the_env(monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE_JARVIS", raising=False)
    monkeypatch.delenv("JARVIS_VOICE_ANNA", raising=False)
    assert (voices.persona_voice("jarvis"), voices.persona_voice("anna")) == ("Charon", "Aoede")
    monkeypatch.setenv("JARVIS_VOICE_ANNA", "kore")
    monkeypatch.setenv("JARVIS_VOICE_JARVIS", "not-a-voice")
    assert (voices.persona_voice("jarvis"), voices.persona_voice("anna")) == ("Charon", "Kore")


def test_the_model_options_first_leave_the_live_session_nothing_to_restart(model):
    """The VR-14 bug itself: the model's update_options sets the shared options first, so the live session compares
    the new voice with itself and never restarts. Hence the voice goes through the session's own update_options."""
    rt = model.session()
    model.update_options(voice="Puck")
    assert rt._opts.voice == "Puck" and rt.voice_changes == 0 and rt.restarts == 0


@pytest.mark.asyncio
async def test_an_unknown_voice_lists_the_valid_ones(assistant):
    result = await assistant.tool_change_voice("xyz")
    assert result["status"] == "error" and result["message"].startswith("Voice 'xyz' nahi mili. Valid voices: Achernar")
    assert "verse=Puck" in result["message"]
    assert assistant._pending_voice is None and assistant._session_options.voice_id == "jarvis_v2"


@pytest.mark.asyncio
async def test_a_voice_switch_restarts_the_live_session_after_the_reply(assistant, model):
    """'Jarvis, apni awaaz Puck kar do': change_voice runs inside the reply (the state still says 'listening'), the
    confirmation is spoken in the old voice, and the next reply in Puck."""
    speech = _Speech()
    rt = _live(assistant, model, speech=speech)
    result = await assistant.tool_change_voice("verse")
    assert result == {"status": "success", "voice": "Puck", "applies": "next reply",
                      "message": "Sir, agle jawab se meri awaaz Puck hogi."}
    assert rt._opts.voice == "Charon" and rt.restarts == 0  # still inside the reply
    speech.finish()
    await _settle(assistant)
    assert rt._opts.voice == "Puck" and rt.voice_changes == 1  # the session saw the change itself
    assert rt.restarts == 2  # its own restart mark and restart_fresh's: one reconnect
    assert rt._session_resumption_handle is None  # a fresh connect: a resumed session would keep the old voice
    assert model.option_updates == []  # never the shared model options first
    assert model.session()._opts.voice == "Puck"  # a rebuilt session of the same model connects with Puck too


@pytest.mark.asyncio
async def test_the_voice_that_is_live_already_costs_no_reconnect(assistant, model):
    rt = _live(assistant, model)
    result = await assistant.tool_change_voice("echo")  # echo is Charon, the live voice
    assert result["voice"] == "Charon" and rt.restarts == 0
    assert rt._session_resumption_handle == "resume-handle"


@pytest.mark.asyncio
async def test_without_a_live_session_the_voice_waits_in_the_model_options(assistant, model):
    await assistant.tool_change_voice("Kore")
    assert model.option_updates == ["Kore"] and assistant._session_options.voice_id == "Kore"
    assert model.session()._opts.voice == "Kore"  # the next session connects with it


@pytest.mark.asyncio
async def test_a_voice_pending_at_a_rebuild_reaches_the_new_session(assistant, model):
    """The session died (C-33 rebuild) before the reply that ran change_voice ended: the runtime's session factory
    carries the voice into the model options, so the rebuilt session connects with it and needs no restart."""
    old = _live(assistant, model, state="thinking")
    await assistant.tool_change_voice("Puck")
    assert assistant._pending_voice == "Puck"
    assistant._activity = None  # the old activity closed
    await assistant.carry_pending_voice()  # agent_runner._session_factory, before the new AgentSession
    assert assistant._pending_voice is None and model._opts.voice == "Puck"
    new = _live(assistant, model)
    assert new._opts.voice == "Puck" and new.restarts == 0 and old.restarts == 0


@pytest.mark.asyncio
async def test_a_voice_still_pending_at_attach_goes_out_without_another_listening(assistant, model):
    """A new session emits its first 'listening' inside start(), before agent_runner listens for agent states."""
    _live(assistant, model, state="thinking")
    await assistant.tool_change_voice("Puck")
    new = _live(assistant, model)
    assistant.attach_session(assistant._active_session)
    await _settle(assistant)
    assert new._opts.voice == "Puck" and new.voice_changes == 1 and assistant._pending_voice is None


@pytest.mark.asyncio
async def test_a_stored_switch_replaces_a_stale_pending_voice(assistant, model):
    """change_voice('Puck') is pending, the session dies, and the dashboard switches to ANNA during the rebuild: the
    new session stays ANNA's Aoede instead of getting the stale Puck after its first reply."""
    _live(assistant, model, state="thinking")
    await assistant.tool_change_voice("Puck")
    assistant._activity = None
    await assistant.persona_manager.set_persona(True)
    assert assistant._pending_voice is None and model._opts.voice == "Aoede"
    new = _live(assistant, model)
    await assistant.on_agent_state("listening")
    assert new._opts.voice == "Aoede" and new.restarts == 0


@pytest.mark.asyncio
async def test_an_apply_after_the_activity_closed_stores_the_voice(assistant, model):
    """AgentActivity.aclose clears the agent's activity while the closing session still says 'listening'."""
    _live(assistant, model, state="thinking")
    await assistant.tool_change_voice("Puck")
    assistant._activity = None
    assistant._active_session.agent_state = "listening"
    await assistant.on_agent_state("listening")
    assert model.option_updates == ["Puck"] and assistant._pending_voice is None


@pytest.mark.asyncio
async def test_a_persona_switch_uses_the_env_voice_and_tells_the_dashboard_last(assistant, model, monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_ANNA", "Kore")
    rt = _live(assistant, model)
    seen = []

    async def notify(persona: str) -> None:
        seen.append((persona, rt._opts.voice, assistant._activity.update_instructions.await_count, rt.restarts))

    assistant._notify_ui_persona = notify
    await assistant.persona_manager.set_persona(True)
    await _settle(assistant)
    assert seen == [("anna", "Kore", 1, 2)]  # after the instructions, the voice and the fresh reconnect
    assert "- Persona: ANNA" in assistant._activity.update_instructions.await_args.args[0]


@pytest.mark.asyncio
async def test_a_switch_inside_the_tool_call_tells_the_dashboard_once_it_applied(assistant, model):
    """toggle_persona runs inside a reply: neither the switch nor persona_change may go out before it ends."""
    speech = _Speech()
    rt = _live(assistant, model, speech=speech)
    assistant._notify_ui_persona = AsyncMock()
    await assistant.persona_manager.set_persona(True)
    await _settle(assistant)
    assistant._notify_ui_persona.assert_not_awaited()
    assert rt.restarts == 0
    speech.finish()
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("anna")
    assert rt._opts.voice == "Aoede"


@pytest.mark.asyncio
async def test_refreshes_of_the_same_persona_tell_the_dashboard_nothing(assistant, model):
    _live(assistant, model)
    assistant._notify_ui_persona = AsyncMock()
    assistant.announce_persona()  # the job's start (agent_runner): 'jarvis' once
    await assistant.tool_set_wake_word_mode(False)
    await assistant.tool_change_voice("Puck")
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("jarvis")


@pytest.mark.asyncio
async def test_a_new_job_tells_the_dashboard_its_persona_once(assistant):
    """The bridge keeps the last job's persona_change (kv active_persona) while every job starts as JARVIS."""
    assistant._notify_ui_persona = AsyncMock()
    assistant.announce_persona()
    assistant.announce_persona()  # nothing new to say
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("jarvis")


@pytest.mark.asyncio
async def test_asking_for_the_active_persona_tells_the_dashboard_again(assistant):
    """A stale dashboard asks for JARVIS while JARVIS is active (or a UI waits for persona_update before it shows
    the switch): set_persona has nothing to switch, so it only says the persona again."""
    assistant._notify_ui_persona = AsyncMock()
    assistant.announce_persona()
    await assistant.persona_manager.set_persona(False)
    await _settle(assistant)
    assert assistant._notify_ui_persona.await_args_list == [(("jarvis",),), (("jarvis",),)]


@pytest.mark.asyncio
async def test_a_switch_stored_without_a_session_tells_the_dashboard(assistant):
    assistant._notify_ui_persona = AsyncMock()
    await assistant.persona_manager.set_persona(True)  # no activity: stored for the next session
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("anna")


@pytest.mark.asyncio
async def test_a_switch_carried_through_a_rebuild_tells_the_dashboard_once(assistant, model):
    """toggle_persona was deferred inside a reply, then the Gemini session died (C-33 rebuild): the session factory
    carries the voice, and that store is where persona_change goes out; the rebuilt session sends nothing more."""
    _live(assistant, model, state="thinking")
    assistant._notify_ui_persona = AsyncMock()
    await assistant.persona_manager.set_persona(True)
    assistant._activity = None
    await assistant.carry_pending_voice()
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("anna")
    model._opts.instructions = assistant._instructions  # AgentActivity starts the new session with the agent's
    new = _live(assistant, model)
    assistant.attach_session(assistant._active_session)
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("anna")
    assert new._opts.voice == "Aoede" and new.restarts == 0 and assistant._pending_instructions is None


@pytest.mark.asyncio
async def test_a_switch_applied_after_the_activity_closed_tells_the_dashboard(assistant, model):
    _live(assistant, model, state="thinking")
    assistant._notify_ui_persona = AsyncMock()
    await assistant.persona_manager.set_persona(True)
    assistant._activity = None
    assistant._active_session.agent_state = "listening"
    await assistant.on_agent_state("listening")
    await _settle(assistant)
    assistant._notify_ui_persona.assert_awaited_once_with("anna")
    assert model._opts.voice == "Aoede"


@pytest.mark.asyncio
async def test_an_update_waits_while_the_user_speaks(assistant, model):
    """A barge-in over the confirmation: the reply was interrupted ('listening', no speech), but a fresh reconnect
    now would drop what the user is saying; the voice goes out at the next 'listening' after the user's turn."""
    rt = _live(assistant, model)
    assistant._active_session.user_state = "speaking"
    await assistant.tool_change_voice("Puck")
    assert rt.restarts == 0 and assistant._pending_voice == "Puck"
    assistant._active_session.user_state = "listening"
    await assistant.on_agent_state("listening")
    assert rt._opts.voice == "Puck" and rt.restarts == 2


@pytest.mark.asyncio
async def test_a_switch_that_raced_a_rebuild_reaches_the_new_session_whole(assistant, model):
    """The switch came while a rebuilt session was inside start(): its activity was already new, the attached session
    still the old one ('initializing'), and the new Gemini session had connected with the old prompt. attach_session
    must send ANNA's prompt with her voice, not her voice alone."""
    model._opts.instructions = "OLD PROMPT"  # what AgentActivity._start_session gave the new Gemini session
    new = model.session()
    assistant._activity = SimpleNamespace(update_instructions=AsyncMock(), realtime_llm_session=new)
    assistant._active_session = SimpleNamespace(agent_state="initializing", current_speech=None)
    await assistant.persona_manager.set_persona(True)
    assert assistant._pending_instructions is not None and assistant._pending_voice == "Aoede"
    assistant.attach_session(SimpleNamespace(agent_state="listening", current_speech=None))
    await _settle(assistant)
    assert "- Persona: ANNA" in assistant._activity.update_instructions.await_args.args[0]
    assert new._opts.voice == "Aoede" and new.restarts == 2  # one reconnect carries both


@pytest.mark.asyncio
async def test_a_new_session_that_started_with_the_new_prompt_needs_no_reconnect(assistant, model):
    model._opts.instructions = "NEW PROMPT"
    new = model.session()
    assistant._activity = SimpleNamespace(update_instructions=AsyncMock(), realtime_llm_session=new)
    assistant._pending_instructions = "NEW PROMPT"
    assistant.attach_session(SimpleNamespace(agent_state="listening", current_speech=None))
    await _settle(assistant)
    assert assistant._pending_instructions is None and new.restarts == 0
    assistant._activity.update_instructions.assert_not_awaited()


@pytest.mark.asyncio
async def test_annas_sorry_refreshes_her_prompt_and_keeps_a_chosen_voice(assistant, model):
    rt = _live(assistant, model)
    assistant._notify_ui_persona = AsyncMock()
    await assistant.persona_manager.set_persona(True)
    await assistant.tool_change_voice("Puck")
    with patch.object(pm.jarvis_id, "get_anna_state", return_value={"mood": "upset", "is_upset": True}), \
            patch.object(pm.jarvis_id, "set_anna_mood", new_callable=AsyncMock) as set_mood:
        assert await assistant.persona_manager.handle_anna_upset_state("sorry jaan", llm.ChatContext.empty()) == ""
    await _settle(assistant)
    set_mood.assert_awaited_once_with("loving", False)
    assert rt._opts.voice == "Puck"  # the mood refresh sent no persona voice
    assert assistant._activity.update_instructions.await_count == 2  # ANNA's prompt, then her calmer mood
    assistant._notify_ui_persona.assert_awaited_once_with("anna")  # the switch only


def test_notes_reach_livekits_bare_chat_context_and_a_legacy_holder():
    bare = llm.ChatContext.empty()  # what livekit hands on_user_turn_completed
    assert pm.add_context_note(bare, "Manao him.") == "Manao him."
    assert [item.text_content for item in bare.items] == ["Manao him."]
    holder = SimpleNamespace(chat_ctx=SimpleNamespace(messages=[]))
    assert pm.add_context_note(holder, "Demand sorry.") == "Demand sorry." and len(holder.chat_ctx.messages) == 1
    assert pm.add_context_note(SimpleNamespace(chat_ctx=None), "x") == ""
    assert pm.add_context_note(_ReadOnlyChatContext([]), "x") == ""  # a read-only context raises on append


@pytest.mark.asyncio
async def test_anna_notes_reach_a_bare_chat_context(assistant):
    """livekit 1.4.6's ChatContext.messages is a method: '.messages.append' raised AttributeError."""
    manager = assistant.persona_manager
    manager._gf_mode_active = True
    turn = llm.ChatContext.empty()
    with patch.object(pm.jarvis_id, "get_anna_state", return_value={"mood": "upset", "is_upset": True}):
        assert await manager.handle_anna_upset_state("tum se baat nahi karni", turn) == "Demand sorry."
    assistant.memory_extractor.memory.get_recent_context = AsyncMock(return_value=[])
    with patch.object(pm.context_analyzer, "analyze_context", return_value={"user_mood": "upset"}):
        assert await manager.inject_emotional_context("main udas hun", turn, []) == "Manao him."
    assert [item.text_content for item in turn.items] == ["Demand sorry.", "Manao him."]


@pytest.mark.asyncio
async def test_memory_and_the_plan_reach_a_bare_chat_context(assistant):
    turn = llm.ChatContext.empty()
    assistant.memory_extractor.memory.get_semantic_context = AsyncMock(
        return_value=[{"document": "memory1", "score": 0.9}])
    with patch("src.core.agent_core.process_with_advanced_reasoning", new_callable=AsyncMock,
               return_value={"is_agentic": True, "plan": "test plan"}):
        await assistant._inject_reasoning_and_memory("test", turn)
    await _settle(assistant)
    assert [item.text_content for item in turn.items] == ["[LONG-TERM MEMORY CONTEXT]: memory1",
                                                          "[EXECUTION PLAN]: test plan"]


def test_append_turn_context_message_returns_the_text(assistant):
    turn = llm.ChatContext.empty()
    message = llm.ChatMessage(role="system", content=["[LONG-TERM MEMORY CONTEXT]: x"])
    assert assistant._append_turn_context_message(turn, message) == "[LONG-TERM MEMORY CONTEXT]: x"
    assert [item.text_content for item in turn.items] == ["[LONG-TERM MEMORY CONTEXT]: x"]
    assert assistant._append_turn_context_message(SimpleNamespace(chat_ctx=None), message) == ""


def test_the_change_voice_tool_lists_every_voice_and_alias(assistant):  # pylint: disable=unused-argument
    with patch("livekit.agents.Agent.__init__", return_value=None) as init:
        BrainAssistant(chat_ctx=MagicMock(), llm_instance=FakeModel(), tools=[])
    tools = {tool.info.name: tool.info.description for tool in init.call_args.kwargs["tools"]}
    description = next(text for name, text in tools.items() if "change_voice" in name)
    assert "Zubenelgenubi" in description and "verse=Puck" in description


def test_a_job_starts_with_the_configured_jarvis_voice(monkeypatch):
    """agent_runner builds the job's RealtimeModel with the saved voice (C-54, K-61), whose default with nothing saved is
    persona_voice('jarvis') (JARVIS_VOICE_JARVIS, C-50); a source-level check like the resume guard's, because
    importing agent_runner pulls the whole LiveKit CLI stack in (tests/core/test_agent_settings.py runs the entrypoint
    with a saved voice)."""
    from src.core.agent_settings import AgentSettings

    tree = ast.parse(AGENT_RUNNER.read_text(encoding="utf-8"))
    builds = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "RealtimeModel"]
    assert len(builds) == 1
    voice = next(keyword.value for keyword in builds[0].keywords if keyword.arg == "voice")
    assert ast.unparse(voice) == "job_settings.voice or persona_voice('jarvis')"
    monkeypatch.setenv("JARVIS_VOICE_JARVIS", "kore")
    assert AgentSettings().voice == voices.persona_voice("jarvis") == "Kore"  # nothing saved: the configured voice


def test_without_a_model_the_assistant_keeps_the_configured_jarvis_voice(assistant, monkeypatch):
    # pylint: disable=unused-argument
    monkeypatch.setenv("JARVIS_VOICE_JARVIS", "puck")
    brain = BrainAssistant(chat_ctx=MagicMock(), llm_instance=None, tools=[])
    assert brain._local_llm.voice == "Puck"
