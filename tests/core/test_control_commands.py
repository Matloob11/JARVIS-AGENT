"""C-51 (K-60 CT-VC-CONTROL): the control commands.

- 'stop' interrupts the current and queued speech (forced only for a speech that does not allow interruptions), drops
  the announcements still waiting to be spoken, clears the dashboard's speaking state and is audited; it never ends
  the job.
- 'mute'/'unmute' switch the live session's microphone input (livekit-agents AgentInput) and pause/restart the legacy
  voice-ID tap; typed chat is still answered while muted.
- 'clear_memory' needs the owner's confirmation and moves the memory file to backups/memory (never a delete); the
  vector wipe needs a second confirmation.
- 'shutdown_agent' needs the owner's confirmation and then ends the job as 'terminated_by_owner' through the runtime.
- A guarded command's card never outlives its job: the job's end (its listener closing) or a cancel of the flow
  cancels it (confirm_resolved 'cancelled'), so a late Allow runs nothing.

Commands go through the job's real UIBridgeListener (agent_loops, the C-34 registry) into the flows of
services/ai_core/control_commands.py. No LiveKit room, bridge or model: the assistant is the real BrainAssistant
(LiveKit's Agent.__init__ stubbed), the runtime the real JarvisRuntime, speech handles livekit's own SpeechHandle, and
the confirm broker the real ConfirmationBroker with a recording stand-in for the bridge.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import errno
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from livekit import rtc
from livekit.agents import StopResponse, llm
from livekit.agents.voice.io import AgentInput, AudioInput
from livekit.agents.voice.speech_handle import SpeechHandle

from services.ai_core import agent_loops, control_commands, jarvis_confirm, memory_store
from services.utils import jarvis_announcer, jarvis_audit, jarvis_bridge
from services.utils.jarvis_config import config
from services.utils.ui_command_registry import UI_COMMANDS, reset_dedupe
from src.core import agent_core
from src.core import conversation_router as cr
from src.core.jarvis_runtime import JarvisRuntime

USER = "c51-user"


# ---------------------------------------------------------------- stand-ins


class _Mic(AudioInput):
    """RoomIO's audio input in miniature: its frames reach the model only while it is attached."""

    def __init__(self) -> None:
        super().__init__(label="c51-mic")
        self.attached = True

    def on_attached(self) -> None:
        self.attached = True

    def on_detached(self) -> None:
        self.attached = False


class _Session:
    """The parts of livekit-agents 1.4.6's AgentSession the control commands use: interrupt(force) over real
    SpeechHandles (a handle that does not allow interruptions refuses unless forced, as AgentActivity.interrupt does),
    current_speech, the AgentInput microphone switch (enabled when a session starts) and generate_reply."""

    def __init__(self, current: SpeechHandle | None = None, queued: tuple[SpeechHandle, ...] = ()) -> None:
        self.current_speech = current
        self.queued = list(queued)
        self._activity = object()  # a running session
        self.interrupts: list[bool] = []
        self.replies: list[str] = []
        self.mic = _Mic()
        self.input = AgentInput(lambda: None, lambda: None)
        self.input.audio = self.mic

    def interrupt(self, *, force: bool = False) -> asyncio.Future[None]:
        self.interrupts.append(force)
        speeches = [speech for speech in (self.current_speech, *self.queued) if speech is not None]
        for speech in speeches:
            speech.interrupt(force=force)  # RuntimeError for a handle that does not allow interruptions
        for speech in speeches:
            speech._mark_done()  # the playout is over
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        return done

    def generate_reply(self, **kwargs: Any) -> MagicMock:
        self.replies.append(kwargs.get("user_input") or kwargs.get("instructions"))
        return MagicMock()


class _Bridge:
    """The bridge's side of K-14 for the real ConfirmationBroker: it keeps every confirm_request and confirm_resolved
    and answers {status: 'ok', ui_clients}; unreachable, it raises like a refused connection."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.resolved: list[dict[str, Any]] = []
        self.ui_clients = 1
        self.reachable = True

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.reachable:
            raise ConnectionError("bridge down")
        (self.requests if event_type == "confirm_request" else self.resolved).append(dict(payload))
        return {"status": "ok", "ui_clients": self.ui_clients}


class _JobCtx:
    """What JarvisRuntime needs of a JobContext: connect, a room for its handlers and the job id (the end_job hook
    takes the place of ctx.shutdown)."""

    def __init__(self) -> None:
        self.room = SimpleNamespace(name="jarvis-hybrid", on=lambda *_a: None, off=lambda *_a: None)
        self.job = SimpleNamespace(id="AJ_c51test")

    async def connect(self) -> None:
        return None


def _publication(sid: str, kind: Any) -> SimpleNamespace:
    return SimpleNamespace(sid=sid, kind=kind, track=object(), set_subscribed=MagicMock())


def _room(*publications: SimpleNamespace) -> SimpleNamespace:
    owner = SimpleNamespace(identity="owner-c51", track_publications={p.sid: p for p in publications})
    return SimpleNamespace(remote_participants={owner.identity: owner})


async def _until(predicate: Any, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


async def _flows(assistant: Any) -> None:
    """Waits for the guarded command flows the listener spawned (kept by the assistant's _spawned_tasks)."""
    await asyncio.wait_for(asyncio.gather(*list(assistant._spawned_tasks)), 5)


async def _started_runtime(assistant: Any, session: _Session) -> JarvisRuntime:
    """A started JarvisRuntime serving `session` that watches the assistant's stop event, as in the entrypoint; its
    end_job hook records the reason the job ends with (runtime.ended)."""
    ended: list[str] = []

    async def end_job(reason: str) -> None:
        ended.append(reason)

    runtime = JarvisRuntime(_JobCtx(), session_factory=lambda: session, start_session=lambda _s: None,
                            end_job=end_job)
    runtime.assistant = assistant
    assert await runtime.start()
    runtime.ended = ended  # type: ignore[attr-defined]
    return runtime


def _listener(monkeypatch, assistant: Any, runtime: Any = None) -> agent_loops.UIBridgeListener:
    monkeypatch.setattr(agent_loops.socketio, "AsyncClient", MagicMock(return_value=MagicMock()))
    reset_dedupe()  # the C-34 (type, id) LRU is process-wide: another test's ids never reach this one
    return agent_loops.UIBridgeListener(assistant, runtime=runtime)


async def _answer(listener: agent_loops.UIBridgeListener, card: dict[str, Any], allow: bool) -> None:
    """The owner's Allow/Deny on the dashboard, as the bridge forwards it (K-45 v10)."""
    await listener._handle_command({"type": "confirm_response", "payload": {"id": card["id"], "allow": allow}})


def _audited(lines: list[tuple[str, str, str]], event: str) -> list[str]:
    return [details for name, details, _severity in lines if name == event]


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _no_bridge_posts(monkeypatch):
    """No test here posts to the bridge (Windows refuses the closed test port only after ~2 s): the control flows'
    notify_ui and notify_event and the listener's notify_event and notify_log are recorders; a test that reads one
    patches it again."""
    for name in ("notify_ui", "notify_event"):
        monkeypatch.setattr(control_commands, name, AsyncMock())
    for name in ("notify_event", "notify_log"):
        monkeypatch.setattr(agent_loops, name, AsyncMock())


@pytest.fixture
def brain(monkeypatch, tmp_path) -> agent_core.BrainAssistant:
    """A real BrainAssistant (LiveKit's Agent.__init__, the plugins, the identity file and the task monitor stubbed;
    built outside the event loop, so no task monitor starts) whose memory is a real ConversationMemory in tmp_path."""
    for name in ("JARVIS_TURN_MODE", "JARVIS_VOICE_JARVIS", "JARVIS_VOICE_ANNA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agent_core, "bridge_notifier", AsyncMock())  # mute_sync and the rest: no bridge POST
    monkeypatch.setattr(agent_core, "task_manager", MagicMock(get_due_tasks=MagicMock(return_value=[])))
    with patch("livekit.agents.Agent.__init__", return_value=None), \
            patch.object(agent_core.jarvis_id, "get_context", return_value="C-51 context"), \
            patch.object(agent_core, "JarvisPluginManager"), patch.object(agent_core, "MemoryExtractor"):
        assistant = agent_core.BrainAssistant(chat_ctx=MagicMock(), llm_instance=MagicMock(), tools=[])
    assistant.vision_handler = MagicMock(stop_loop=AsyncMock())
    (tmp_path / "conversations").mkdir()
    assistant.memory_extractor = SimpleNamespace(
        memory=memory_store.ConversationMemory(USER, storage_path=str(tmp_path / "conversations")))
    return assistant


@pytest.fixture
async def broker(monkeypatch):
    """The real K-14 ConfirmationBroker in place of the process singleton (the agent's handlers import it at call
    time), delivering to a recording bridge stand-in."""
    bridge = _Bridge()
    real = jarvis_confirm.ConfirmationBroker(sender=bridge, on_result=AsyncMock())
    real.bridge = bridge  # type: ignore[attr-defined]
    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", real)
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_RETRY_DELAY_S", 0.0)
    yield real
    await real.shutdown()


@pytest.fixture
def audit(monkeypatch) -> list[tuple[str, str, str]]:
    """Every audit line (logs/security.log) as (event, details, severity)."""
    lines: list[tuple[str, str, str]] = []

    def log_event(_cls: Any, event_type: str, details: str, severity: str = "INFO") -> None:
        lines.append((event_type, details, severity))

    monkeypatch.setattr(jarvis_audit.JarvisAudit, "log_event", classmethod(log_event))
    return lines


@pytest.fixture
def told(monkeypatch) -> list[str]:
    """The dashboard log lines (notify_log) the listener's commands send."""
    lines: list[str] = []

    async def notify_log(message: str, _category: str = "SYSTEM") -> None:
        lines.append(message)

    monkeypatch.setattr(agent_loops, "notify_log", notify_log)
    return lines


@pytest.fixture
def memory_file(brain, monkeypatch, tmp_path) -> Path:
    """The assistant's memory file on disk (its encrypted bytes do not matter here) and a cached message; the project
    root and backups/ are tmp_path's."""
    monkeypatch.setattr(config, "project_root", str(tmp_path))
    monkeypatch.setattr(config, "backups_dir", str(tmp_path / "backups"))
    memory = brain.memory_extractor.memory
    Path(memory.memory_file).write_bytes(b"c51-encrypted-memory")
    memory.cache["m-1"] = {"role": "user", "content": "c51"}
    return Path(memory.memory_file)


# ---------------------------------------------------------------- stop


async def test_stop_interrupts_the_speech_and_never_ends_the_job(brain, monkeypatch, audit):
    current, queued = SpeechHandle.create(), SpeechHandle.create()
    session = _Session(current, (queued,))
    brain.session = session
    runtime = await _started_runtime(brain, session)
    ui = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_ui", ui)
    order: list[str] = []
    dropped = MagicMock(side_effect=lambda: order.append("drop") or 1)
    monkeypatch.setattr(jarvis_announcer, "cancel_pending", dropped)
    interrupt = session.interrupt

    def recorded_interrupt(*, force: bool = False) -> asyncio.Future[None]:
        order.append("interrupt")
        return interrupt(force=force)

    session.interrupt = recorded_interrupt  # type: ignore[method-assign]
    listener = _listener(monkeypatch, brain, runtime)
    try:
        await listener._handle_command({"type": "stop", "payload": {}})
        for _ in range(5):
            await asyncio.sleep(0)

        assert session.interrupts == [False]  # a plain interrupt: nothing needed forcing
        assert current.interrupted and queued.interrupted
        ui.assert_awaited_once_with("STOP")  # the dashboard's speaking state is cleared at once
        dropped.assert_called_once_with()  # announcements still waiting are dropped too ...
        assert order == ["drop", "interrupt"]  # ... first, so none of them starts once the speech is cut
        assert _audited(audit, "STOP_SPEAKING") == [
            "SOURCE=ui | INTERRUPTED=True | ANNOUNCEMENTS_DROPPED=1 | TYPED_RESCUES_CANCELLED=0"]
        # JARVIS keeps listening: no end reason, no stop event, the runtime stays open
        assert brain.end_reason is None and not brain._stop_event.is_set()
        assert not runtime.closed.is_set() and runtime.reason is None and runtime.ended == []
    finally:
        await runtime.aclose("c51_test_end")


@pytest.mark.parametrize(("current_allows", "queued_allows", "expected"), [
    (True, True, [False]),         # every speech allows interruptions: a plain interrupt
    (False, True, [True]),         # the current speech does not: forced at once
    (True, False, [False, True]),  # a queued one refused the plain interrupt: forced on the retry
])
async def test_stop_forces_only_a_speech_that_does_not_allow_interruptions(brain, current_allows, queued_allows,
                                                                           expected):
    current = SpeechHandle.create(allow_interruptions=current_allows)
    queued = SpeechHandle.create(allow_interruptions=queued_allows)
    session = _Session(current, (queued,))
    brain.session = session

    assert await brain.interrupt_speech() is True
    assert session.interrupts == expected
    assert current.interrupted and queued.interrupted
    assert brain.end_reason is None and not brain._stop_event.is_set()


async def test_stop_on_a_session_that_is_not_running_forces_nothing(brain):
    session = _Session()
    session._activity = None

    def not_running(*, force: bool = False) -> None:
        session.interrupts.append(force)
        raise RuntimeError("AgentSession isn't running")

    session.interrupt = not_running  # type: ignore[method-assign]
    brain.session = session
    assert await brain.interrupt_speech() is False
    assert session.interrupts == [False]
    assert brain.end_reason is None


async def test_stop_drops_the_announcements_still_waiting_to_be_spoken(monkeypatch):
    posted: list[str] = []

    async def notify_transcription(_role, text, **_kwargs):
        posted.append(text)

    monkeypatch.setattr(jarvis_bridge, "notify_transcription", notify_transcription)
    monkeypatch.setattr(jarvis_announcer, "SPEAKING_POLL_S", 0.01)
    announcer = jarvis_announcer.Announcer()
    session = SimpleNamespace(agent_state="speaking", replies=[])
    session.generate_reply = lambda *, instructions: session.replies.append(instructions)

    await announcer.announce("queued before the session", priority="high")  # no session yet: queued
    announcer.attach(session)  # flushed: waits for JARVIS to stop speaking
    await announcer.announce("waiting for its turn", priority="high")
    await asyncio.sleep(0.05)
    assert announcer.cancel_pending() == 2  # the user said stop

    session.agent_state = "listening"
    await _until(lambda: not announcer._tasks)
    assert session.replies == []  # nothing starts speaking right after the stop
    assert posted == ["queued before the session", "waiting for its turn"]  # the UI messages stay

    await announcer.announce("after the stop", priority="high")
    await _until(lambda: not announcer._tasks)
    assert session.replies == [jarvis_announcer.SPEAK_PREFIX + "after the stop"]

    later = jarvis_announcer.Announcer()  # a stop while nothing is attached empties the queue too
    await later.announce("queued", priority="high")
    assert later.cancel_pending() == 1
    other = SimpleNamespace(agent_state="listening", replies=[])
    other.generate_reply = lambda *, instructions: other.replies.append(instructions)
    later.attach(other)
    await asyncio.sleep(0.05)
    assert other.replies == []


# ---------------------------------------------------------------- mute


async def test_mute_turns_the_session_microphone_off_and_pauses_the_voice_id_tap(brain, monkeypatch):
    from src.core import agent_runner  # pylint: disable=import-outside-toplevel

    started: list[str] = []
    cancelled: list[str] = []

    async def tap(publication, _assistant):
        started.append(publication.sid)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(publication.sid)
            raise

    monkeypatch.setattr(agent_runner, "_process_user_audio", tap)
    audio = _publication("TR_audio", rtc.TrackKind.KIND_AUDIO)
    video = _publication("TR_video", rtc.TrackKind.KIND_VIDEO)
    taps = agent_runner.UserAudioTaps(brain, _room(audio, video), asyncio.create_task)
    brain.attach_audio_tap(taps)
    session = _Session()
    brain.session = session
    assert taps.add_room_tracks() == 1  # the audio track only
    await _until(lambda: started == ["TR_audio"])
    assert taps.add(audio) is False and taps.add_room_tracks() == 0  # one tap per track (a repeated publish event)
    brain._audio_buffer.extend(b"\x01" * 32)

    brain.set_muted(True)
    await _until(lambda: cancelled == ["TR_audio"])
    assert brain.muted and not session.mic.attached and session.input.audio_enabled is False
    assert taps.running == 0 and len(brain._audio_buffer) == 0
    assert taps.add(audio) is False  # no tap starts while muted
    audio.set_subscribed.assert_not_called()  # RoomIO keeps its track: nothing is unsubscribed behind its back

    brain.set_muted(False)
    await _until(lambda: started == ["TR_audio", "TR_audio"])
    assert not brain.muted and session.mic.attached and session.input.audio_enabled is True
    assert taps.running == 1
    audio.set_subscribed.assert_not_called()
    await _until(lambda: brain.bridge_notifier.notify_event.await_count == 2)
    assert brain.bridge_notifier.notify_event.await_args_list == [call("mute_sync", True), call("mute_sync", False)]
    taps.pause()


async def test_a_rebuilt_session_of_a_muted_assistant_starts_with_its_microphone_off(brain):
    """attach_session's backstop. agent_runner already switches the microphone off before session.start
    (test_agent_runner.py::test_a_session_rebuilt_while_muted_starts_with_its_microphone_off)."""
    brain.session = _Session()
    brain.set_muted(True)
    rebuilt = _Session()  # livekit starts every session with its microphone input on
    assert rebuilt.mic.attached
    brain.attach_session(rebuilt)
    assert not rebuilt.mic.attached
    brain.set_muted(False)
    assert rebuilt.mic.attached


async def test_a_microphone_switched_off_before_roomio_attaches_queues_no_frame():
    """Why agent_runner switches the microphone of a session started while muted off BEFORE session.start, with
    livekit-agents 1.4.6's own classes: AgentInput keeps set_audio_enabled(False) while no stream is set, so RoomIO's
    input stream (set inside start()) starts detached and queues none of the owner's frames. Switched off only after
    frames arrived (attach_session alone, after start()), the frames already queued stay there for the model."""
    from livekit.agents.voice.room_io._input import \
        _ParticipantAudioInputStream  # pylint: disable=import-outside-toplevel

    speech = rtc.AudioFrame(b"\x01\x00" * 480, sample_rate=24000, num_channels=1, samples_per_channel=480)
    owner = SimpleNamespace(identity="owner-c51")
    publication = SimpleNamespace(source=rtc.TrackSource.SOURCE_MICROPHONE, track=None)

    class _OwnerTrack:
        """The owner's rtc.AudioStream: three frames of speech, then its end."""

        def __init__(self) -> None:
            self.events = [SimpleNamespace(frame=speech) for _ in range(3)]

        def __aiter__(self) -> "_OwnerTrack":
            return self

        async def __anext__(self) -> SimpleNamespace:
            if not self.events:
                raise StopAsyncIteration
            return self.events.pop(0)

    async def queued_speech(switch_off: str) -> int:
        """The speech frames RoomIO's input stream queued for the model, with the session's microphone switched off
        'before' RoomIO sets the stream, 'after' the frames came, or 'never'."""
        session_input = AgentInput(lambda: None, lambda: None)
        if switch_off == "before":
            session_input.set_audio_enabled(False)
        stream = _ParticipantAudioInputStream(SimpleNamespace(on=lambda *_a: None), sample_rate=24000, num_channels=1,
                                              noise_cancellation=None, pre_connect_audio_handler=None)
        session_input.audio = stream  # what RoomIO.start does inside session.start()
        await stream._forward_task(None, _OwnerTrack(), publication, owner)
        if switch_off == "after":
            session_input.set_audio_enabled(False)
        frames = [stream._data_ch.recv_nowait() for _ in range(stream._data_ch.qsize())]
        return sum(1 for frame in frames if any(bytes(frame.data)))  # the silence pushed at the end never counts

    assert await queued_speech("never") == 3
    assert await queued_speech("before") == 0  # agent_runner's order: nothing reaches the model
    assert await queued_speech("after") == 3  # switched off after start() only: the queued frames still go


async def test_without_a_session_mute_unsubscribes_the_audio_tracks_and_unmute_brings_them_back(brain, monkeypatch):
    import livekit.agents as lk_agents  # pylint: disable=import-outside-toplevel

    audio = _publication("TR_audio", rtc.TrackKind.KIND_AUDIO)
    video = _publication("TR_video", rtc.TrackKind.KIND_VIDEO)
    room = _room(audio, video)
    monkeypatch.setattr(lk_agents, "get_job_context", lambda: SimpleNamespace(room=room))
    assert brain.session is None

    brain.set_muted(True)
    audio.set_subscribed.assert_called_once_with(False)
    video.set_subscribed.assert_not_called()

    session = _Session()
    brain.attach_session(session)  # a session that arrives while muted keeps its microphone off
    assert not session.mic.attached
    brain.set_muted(False)
    assert session.mic.attached
    assert audio.set_subscribed.call_args_list == [call(False), call(True)]
    video.set_subscribed.assert_not_called()


async def test_typed_chat_is_answered_while_muted_and_a_spoken_turn_is_not(brain):
    session = _Session()
    brain.session = session
    router = cr.ConversationRouter(brain, notify=AsyncMock(), reason=AsyncMock(return_value={"plan": []}),
                                   notify_reasoning=AsyncMock(), dispatch_command=AsyncMock(return_value=True))
    brain.attach_router(router)
    router.attach(session)
    brain.set_muted(True)
    assert not session.mic.attached  # 'Jarvis, time?' spoken now never reaches the model

    assert await router.handle_text("Jarvis, time?", "u-c51-typed") == "accepted"
    await router.drain()
    assert session.replies == [cr.TYPED_PREFIX + "Jarvis, time?"]  # the typed question is answered

    # a spoken turn that still reached the assistant is refused before the model answers
    with pytest.raises(StopResponse):
        await brain.on_user_turn_completed(MagicMock(), llm.ChatMessage(role="user", content=["Jarvis, time?"]))
    assert session.replies == [cr.TYPED_PREFIX + "Jarvis, time?"]


async def test_a_paused_tap_closes_its_audio_stream_and_keeps_the_track_subscribed(monkeypatch):
    from src.core import agent_runner  # pylint: disable=import-outside-toplevel

    streams: list[Any] = []

    class _Stream:
        def __init__(self, _track: Any, **_kwargs: Any) -> None:
            self.closed = False
            streams.append(self)

        def __aiter__(self) -> "_Stream":
            return self

        async def __anext__(self) -> Any:
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(agent_runner.rtc, "AudioStream", _Stream)
    publication = _publication("TR_audio", rtc.TrackKind.KIND_AUDIO)
    assistant = SimpleNamespace(muted=False, add_audio_frame=MagicMock())
    task = asyncio.create_task(agent_runner._process_user_audio(publication, assistant))
    await _until(lambda: streams)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert streams[0].closed
    publication.set_subscribed.assert_called_once_with(True)  # never unsubscribed: RoomIO reads the same track


# ---------------------------------------------------------------- clear_memory


async def test_clear_memory_asks_first_and_then_moves_the_file_to_backups(brain, broker, memory_file, monkeypatch,
                                                                          audit):
    events = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_event", events)
    listener = _listener(monkeypatch, brain)

    await listener._handle_command({"type": "clear_memory", "payload": {}})
    await _until(lambda: broker.bridge.requests)
    [card] = broker.bridge.requests
    assert (card["tool"], card["risk"], card["voice_allowed"]) == ("clear_memory", "medium", False)
    assert "backups/memory" in card["summary"]
    assert memory_file.exists() and brain.memory_extractor.memory.cache  # nothing before the owner's answer
    events.assert_not_awaited()

    await _answer(listener, card, True)
    await _flows(brain)
    [backup] = list((Path(config.backups_dir) / "memory").iterdir())
    assert re.fullmatch(rf"\d{{8}}T\d{{6}}Z_{USER}_memory\.json", backup.name)
    assert backup.read_bytes() == b"c51-encrypted-memory" and not memory_file.exists()  # moved, never deleted
    assert not brain.memory_extractor.memory.cache
    [(event, payload)] = [c.args for c in events.await_args_list]
    assert event == "memory_cleared"  # never memory_sync
    assert payload["scope"] == "short_term" and payload["backup"] == f"backups/memory/{backup.name}"
    assert isinstance(payload["ts"], float)
    assert _audited(audit, "CLEAR_MEMORY") == [
        f"ALLOW=True | SOURCE=broker | REASON=approved | SCOPE=short_term | BACKUP=backups/memory/{backup.name}"]


@pytest.mark.parametrize("answer", ["deny", "expire"])
async def test_clear_memory_without_the_confirmation_clears_nothing(brain, broker, memory_file, monkeypatch, audit,
                                                                    told, answer):
    events = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_event", events)
    if answer == "expire":
        monkeypatch.setattr(config, "confirm_timeout_s", 0.2)
    listener = _listener(monkeypatch, brain)

    await listener._handle_command({"type": "clear_memory", "payload": {"confirm": True}})  # no bypass of the card
    await _until(lambda: broker.bridge.requests)
    if answer == "deny":
        await _answer(listener, broker.bridge.requests[0], False)
    await _flows(brain)

    assert memory_file.read_bytes() == b"c51-encrypted-memory" and brain.memory_extractor.memory.cache
    assert not (Path(config.backups_dir) / "memory").exists()
    events.assert_not_awaited()
    reason = "denied" if answer == "deny" else "expired"
    assert _audited(audit, "CLEAR_MEMORY") == [f"ALLOW=False | SOURCE=broker | REASON={reason} | SCOPE=short_term"]
    assert told == [f"Clear memory not confirmed ({reason}); nothing was cleared."]


@pytest.mark.parametrize("second_allow", [False, True])
async def test_the_vector_wipe_needs_a_second_high_risk_confirmation(brain, broker, memory_file, monkeypatch, audit,
                                                                     second_allow):
    events = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_event", events)
    vectors = SimpleNamespace(clear_memory=AsyncMock(return_value=True))
    monkeypatch.setattr(control_commands, "jarvis_vector_db", vectors)
    listener = _listener(monkeypatch, brain)

    await listener._handle_command({"type": "clear_memory", "payload": {"include_vector": True}})
    await _until(lambda: len(broker.bridge.requests) == 1)
    first = broker.bridge.requests[0]
    assert first["args_preview"] == "scope=short_term include_vector=true"
    await _answer(listener, first, True)
    await _until(lambda: len(broker.bridge.requests) == 2)
    second = broker.bridge.requests[1]
    assert (second["tool"], second["risk"], second["voice_allowed"]) == ("clear_memory", "high", False)
    assert second["id"] != first["id"] and "vector" in second["summary"].lower()
    assert not memory_file.exists()  # the short-term clear did not wait for the second answer
    vectors.clear_memory.assert_not_awaited()

    await _answer(listener, second, second_allow)
    await _flows(brain)
    scopes = [c.args[1]["scope"] for c in events.await_args_list if c.args[0] == "memory_cleared"]
    lines = _audited(audit, "CLEAR_MEMORY")
    if second_allow:
        vectors.clear_memory.assert_awaited_once_with()
        assert scopes == ["short_term", "all"]
        assert lines[-1] == "ALLOW=True | SOURCE=broker | REASON=approved | SCOPE=vector | RESULT=cleared"
    else:
        vectors.clear_memory.assert_not_awaited()
        assert scopes == ["short_term"]
        assert lines[-1] == "ALLOW=False | SOURCE=broker | REASON=denied | SCOPE=vector"


async def test_without_a_broker_clear_memory_needs_payload_confirm_and_never_wipes_the_vectors(
        brain, memory_file, monkeypatch, audit):
    monkeypatch.setattr(control_commands, "_confirm_broker", lambda: None)
    events = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_event", events)
    vectors = SimpleNamespace(clear_memory=AsyncMock(return_value=True))
    monkeypatch.setattr(control_commands, "jarvis_vector_db", vectors)
    ctx = _listener(monkeypatch, brain)._ctx

    for payload in ({}, {"confirm": "true"}, {"confirm": 1}, None):
        assert await control_commands.run_clear_memory(payload, ctx) is None
    assert memory_file.exists()

    cleared = await control_commands.run_clear_memory({"confirm": True, "include_vector": True}, ctx)
    assert cleared is not None and cleared["scope"] == "short_term" and not memory_file.exists()
    vectors.clear_memory.assert_not_awaited()  # one payload flag cannot be the second confirmation
    assert [c.args[1]["scope"] for c in events.await_args_list] == ["short_term"]
    lines = _audited(audit, "CLEAR_MEMORY")
    assert lines[0] == "ALLOW=False | SOURCE=ui_payload | REASON=no_confirmation | SCOPE=short_term"
    assert lines[-2].startswith("ALLOW=True | SOURCE=ui_payload | REASON=ui_confirm | SCOPE=short_term | BACKUP=")
    assert lines[-1] == "ALLOW=False | SOURCE=ui_payload | REASON=needs_broker | SCOPE=vector"


async def test_a_clear_whose_move_fails_reports_it_and_deletes_nothing(brain, memory_file, monkeypatch, audit, told):
    monkeypatch.setattr(control_commands, "_confirm_broker", lambda: None)
    events = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_event", events)
    real_replace = os.replace

    def replace(src, dst):
        if os.path.abspath(src) == os.path.abspath(str(memory_file)):
            raise PermissionError(errno.EACCES, "locked")
        return real_replace(src, dst)

    monkeypatch.setattr(memory_store.os, "replace", replace)
    ctx = _listener(monkeypatch, brain)._ctx

    assert await control_commands.run_clear_memory({"confirm": True}, ctx) is None
    assert memory_file.read_bytes() == b"c51-encrypted-memory" and brain.memory_extractor.memory.cache
    events.assert_not_awaited()
    assert _audited(audit, "CLEAR_MEMORY") == [
        "ALLOW=True | SOURCE=ui_payload | REASON=ui_confirm | SCOPE=short_term | RESULT=failed (PermissionError)"]
    assert told == ["Clear memory failed: the memory file could not be moved; nothing was deleted."]


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 10, 9, 1, 2, 3, tzinfo=timezone.utc)


async def test_clear_moves_the_file_and_never_overwrites_an_earlier_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_store, "datetime", _FixedDatetime)
    memory = memory_store.ConversationMemory(USER, storage_path=str(tmp_path))
    backups = tmp_path / "backups" / "memory"  # missing: clear() creates it

    Path(memory.memory_file).write_bytes(b"first")
    memory.cache["a"] = {"content": "x"}
    first = Path(await memory.clear(str(backups)))
    assert first.name == f"20261009T010203Z_{USER}_memory.json" and first.read_bytes() == b"first"
    assert not Path(memory.memory_file).exists() and not memory.cache

    Path(memory.memory_file).write_bytes(b"second")  # a second clear in the same second
    second = Path(await memory.clear(str(backups)))
    assert second.name == f"20261009T010203Z-1_{USER}_memory.json" and second.read_bytes() == b"second"
    assert first.read_bytes() == b"first"
    assert await memory.clear(str(backups)) is None  # no file: nothing to move


async def test_two_clears_at_once_in_the_same_second_keep_both_memory_files(tmp_path, monkeypatch):
    """Two ConversationMemory instances of the same user (two conversation folders, one backup folder) clear in the
    same second, each in its own thread, and both pick a backup name before either moves its file. Each name is
    reserved atomically, so neither move lands on the other's backup: both memory files are kept."""
    monkeypatch.setattr(memory_store, "datetime", _FixedDatetime)
    memories = []
    for folder, content in (("pc", b"first"), ("vps", b"second")):
        (tmp_path / folder).mkdir()
        memory = memory_store.ConversationMemory(USER, storage_path=str(tmp_path / folder))
        Path(memory.memory_file).write_bytes(content)
        memories.append(memory)
    sources = {os.path.abspath(memory.memory_file) for memory in memories}
    both_named = threading.Barrier(2, timeout=5)
    real_replace = os.replace

    def replace(src, dst):
        if os.path.abspath(src) in sources:
            both_named.wait()  # the other clear has chosen its backup name too
        return real_replace(src, dst)

    monkeypatch.setattr(memory_store.os, "replace", replace)
    backups = tmp_path / "backups"
    paths = await asyncio.gather(*(memory.clear(str(backups)) for memory in memories))

    assert len(set(paths)) == 2
    assert sorted(Path(path).read_bytes() for path in paths) == [b"first", b"second"]  # neither was overwritten
    assert sorted(path.name for path in backups.iterdir()) == [f"20261009T010203Z-1_{USER}_memory.json",
                                                               f"20261009T010203Z_{USER}_memory.json"]
    assert not any(Path(memory.memory_file).exists() for memory in memories)


async def test_across_file_systems_the_backup_is_complete_before_the_original_goes(tmp_path, monkeypatch):
    memory = memory_store.ConversationMemory(USER, storage_path=str(tmp_path))
    Path(memory.memory_file).write_bytes(b"keep me")
    real_replace = os.replace
    renamed: list[str] = []

    def replace(src, dst):
        if os.path.abspath(src) == os.path.abspath(memory.memory_file):
            raise OSError(errno.EXDEV, "Invalid cross-device link")  # the VPS: conversations and backups volumes
        renamed.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(memory_store.os, "replace", replace)
    backup = Path(await memory.clear(str(tmp_path / "other_volume")))
    assert backup.read_bytes() == b"keep me" and not Path(memory.memory_file).exists()
    assert renamed == [backup.name + ".partial"]  # a complete copy took its final name first
    assert not list(backup.parent.glob("*.partial"))


async def test_a_move_that_fails_keeps_the_file_and_the_cache(tmp_path, monkeypatch):
    memory = memory_store.ConversationMemory(USER, storage_path=str(tmp_path))
    Path(memory.memory_file).write_bytes(b"keep me")
    memory.cache["a"] = {"content": "x"}

    def replace(_src, _dst):
        raise PermissionError(errno.EACCES, "locked")

    monkeypatch.setattr(memory_store.os, "replace", replace)
    with pytest.raises(OSError):
        await memory.clear(str(tmp_path / "backups"))
    assert Path(memory.memory_file).read_bytes() == b"keep me" and memory.cache


def test_the_default_backup_folder_is_backups_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "backups_dir", str(tmp_path / "backups"))
    assert memory_store.memory_backup_dir() == str(tmp_path / "backups" / "memory")


# ---------------------------------------------------------------- shutdown_agent


@pytest.mark.parametrize(("answer", "reason"), [("deny", "denied"), ("no_ui", "no_ui"),
                                                 ("unreachable", "unreachable")])
async def test_shutdown_without_the_owners_confirmation_does_nothing(brain, broker, monkeypatch, audit, told,
                                                                     answer, reason):
    session = _Session()
    brain.session = session
    runtime = await _started_runtime(brain, session)
    listener = _listener(monkeypatch, brain, runtime)
    broker.bridge.ui_clients = 0 if answer == "no_ui" else 1
    broker.bridge.reachable = answer != "unreachable"
    try:
        # payload confirm:true never replaces the card while the broker is registered
        await listener._handle_command({"type": "shutdown_agent", "payload": {"restart": True, "confirm": True}})
        if answer == "deny":
            await _until(lambda: broker.bridge.requests)
            [card] = broker.bridge.requests
            assert (card["tool"], card["risk"], card["voice_allowed"]) == ("shutdown_agent", "high", False)
            assert card["args_preview"] == "restart=true"
            await _answer(listener, card, False)
        await _flows(brain)
        for _ in range(5):
            await asyncio.sleep(0)

        assert brain.end_reason is None and not brain._stop_event.is_set()
        assert not runtime.closed.is_set() and runtime.ended == []
        assert _audited(audit, "SHUTDOWN_AGENT") == [f"ALLOW=False | SOURCE=broker | REASON={reason} | RESTART=True"]
        assert told == [f"Shutdown not confirmed ({reason}); the session keeps running."]
    finally:
        await runtime.aclose("c51_test_end")


@pytest.mark.parametrize("restart", [True, False])
async def test_a_confirmed_shutdown_ends_the_job_as_terminated_by_owner(brain, broker, monkeypatch, audit, told,
                                                                        restart):
    session = _Session()
    brain.session = session
    runtime = await _started_runtime(brain, session)
    listener = _listener(monkeypatch, brain, runtime)

    await listener._handle_command({"type": "shutdown_agent", "payload": {"restart": restart}})
    await _until(lambda: broker.bridge.requests)
    [card] = broker.bridge.requests
    assert card["args_preview"] == f"restart={str(restart).lower()}"
    assert ("restart=false" in card["summary"]) is (not restart)
    assert not runtime.closed.is_set()  # nothing before the Allow

    await _answer(listener, card, True)
    await _flows(brain)
    assert await asyncio.wait_for(runtime.wait_closed(), 2) == control_commands.SHUTDOWN_REASON == "terminated_by_owner"
    assert brain.end_reason == "terminated_by_owner"
    await runtime.aclose(runtime.reason)  # the entrypoint's teardown: job_end and ctx.shutdown with this reason
    assert runtime.ended == ["terminated_by_owner"]
    assert _audited(audit, "SHUTDOWN_AGENT") == [f"ALLOW=True | SOURCE=broker | REASON=approved | RESTART={restart}"]
    assert bool(told) is (not restart)  # restart=false is not supported by the bridge yet: the owner is told


async def test_without_a_broker_only_payload_confirm_true_ends_the_job(brain, monkeypatch, audit):
    monkeypatch.setattr(control_commands, "_confirm_broker", lambda: None)
    ctx = _listener(monkeypatch, brain)._ctx

    for payload in ({}, {"confirm": "true"}, {"confirm": 1}, None, "confirm"):
        assert await control_commands.run_shutdown_agent(payload, ctx) is False
    assert brain.end_reason is None and not brain._stop_event.is_set()

    assert await control_commands.run_shutdown_agent({"confirm": True}, ctx) is True
    assert brain.end_reason == "terminated_by_owner" and brain._stop_event.is_set()
    lines = _audited(audit, "SHUTDOWN_AGENT")
    assert lines[0] == "ALLOW=False | SOURCE=ui_payload | REASON=no_confirmation | RESTART=True"
    assert lines[-1] == "ALLOW=True | SOURCE=ui_payload | REASON=ui_confirm | RESTART=True"


async def test_a_second_shutdown_while_its_card_is_open_asks_only_once(brain, broker, monkeypatch):
    listener = _listener(monkeypatch, brain)
    await listener._handle_command({"type": "shutdown_agent", "payload": {"id": "c51-terminate"}})
    await _until(lambda: broker.bridge.requests)
    await listener._handle_command({"type": "shutdown_agent", "payload": {"id": "c51-terminate"}})  # C-34 dedupe
    await listener._handle_command({"type": "shutdown_agent", "payload": {}})  # another click: the card is open
    await asyncio.sleep(0.05)
    assert len(broker.bridge.requests) == 1

    await _answer(listener, broker.bridge.requests[0], False)
    await _flows(brain)
    assert brain.end_reason is None


async def test_a_guarded_command_of_an_assistant_without_spawned_tasks_still_runs(monkeypatch, audit):
    """An old integration's assistant (no _spawned_tasks, no memory): the flow is kept by agent_loops itself."""
    monkeypatch.setattr(control_commands, "_confirm_broker", lambda: None)
    old = SimpleNamespace(_stop_event=asyncio.Event(), end_reason=None)

    def request_end(reason: str) -> None:
        old.end_reason = old.end_reason or reason
        old._stop_event.set()

    old.request_end = request_end
    listener = _listener(monkeypatch, old)

    await listener._handle_command({"type": "shutdown_agent", "payload": {"confirm": True}})
    assert agent_loops._command_tasks  # kept referenced until it ends
    await asyncio.wait_for(asyncio.gather(*list(agent_loops._command_tasks)), 5)
    assert old.end_reason == "terminated_by_owner" and old._stop_event.is_set()
    assert _audited(audit, "SHUTDOWN_AGENT") == ["ALLOW=True | SOURCE=ui_payload | REASON=ui_confirm | RESTART=True"]


# ---------------------------------------------------------------- a card never outlives its job


@pytest.mark.parametrize("command", ["shutdown_agent", "clear_memory"])
async def test_the_jobs_end_cancels_an_open_card_and_a_late_allow_runs_nothing(brain, broker, memory_file, monkeypatch,
                                                                               audit, told, command):
    """The job ends (its room is gone) while the card of a guarded command is open. The entrypoint's teardown (the
    runtime closes the job's listener) cancels it: confirm_resolved {allow: false, reason: 'cancelled'} closes it on
    the dashboard, the flow ends audited, and an Allow that reaches the process-wide broker afterwards (the next job's
    listener) finds it decided, so nothing runs and no 'started' goes out."""
    events = AsyncMock()
    monkeypatch.setattr(control_commands, "notify_event", events)
    session = _Session()
    brain.session = session
    runtime = await _started_runtime(brain, session)
    listener = _listener(monkeypatch, brain, runtime)
    runtime.listener = listener  # as the entrypoint wires it

    await listener._handle_command({"type": command, "payload": {}})
    await _until(lambda: broker.bridge.requests)
    [card] = broker.bridge.requests
    runtime.request_close("room_disconnected")
    await runtime.aclose(runtime.reason)  # the entrypoint's teardown
    await _until(lambda: broker.bridge.resolved)

    assert broker.bridge.resolved == [{"id": card["id"], "allow": False, "reason": "cancelled"}]
    assert not broker.pending() and not brain._spawned_tasks  # the flow ended with its job
    late = await broker.resolve(card["id"], True)  # the next job's listener forwards an Allow on the old card
    assert (late["status"], late["reason"]) == ("cancelled", "cancelled")
    assert broker.bridge.resolved == [{"id": card["id"], "allow": False, "reason": "cancelled"}]
    assert brain.end_reason is None and runtime.ended == ["room_disconnected"]
    assert memory_file.exists() and brain.memory_extractor.memory.cache  # nothing was cleared
    events.assert_not_awaited()
    if command == "shutdown_agent":
        assert _audited(audit, "SHUTDOWN_AGENT") == ["ALLOW=False | SOURCE=broker | REASON=cancelled | RESTART=True"]
        assert told == []  # never 'the session keeps running': it ended
    else:
        assert _audited(audit, "CLEAR_MEMORY") == [
            "ALLOW=False | SOURCE=broker | REASON=cancelled | SCOPE=short_term"]
        assert told == ["Clear memory not confirmed (cancelled); nothing was cleared."]


async def test_a_cancelled_flow_cancels_its_card(brain, broker, monkeypatch, audit):
    listener = _listener(monkeypatch, brain)
    await listener._handle_command({"type": "shutdown_agent", "payload": {}})
    await _until(lambda: broker.bridge.requests)
    [card] = broker.bridge.requests
    [flow] = list(brain._spawned_tasks)

    flow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flow
    await _until(lambda: broker.bridge.resolved)
    assert broker.bridge.resolved == [{"id": card["id"], "allow": False, "reason": "cancelled"}]
    assert (await broker.resolve(card["id"], True))["status"] == "cancelled"  # a late Allow runs nothing
    assert brain.end_reason is None and not brain._stop_event.is_set()


async def test_a_card_delivered_after_the_jobs_end_is_cancelled_and_no_new_one_is_asked(brain, broker, monkeypatch,
                                                                                       audit):
    """The job's listener closes while a card is still on its way to the bridge: the card is cancelled as soon as its
    delivery returns, and nothing the job asks afterwards (a late command, clear_memory's vector card) opens one."""
    bridge = broker.bridge
    in_flight: list[str] = []
    delivered = asyncio.Event()

    async def slow_bridge(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if event_type == "confirm_request":
            in_flight.append(payload["id"])
            await delivered.wait()
        return await bridge(event_type, payload)

    broker.sender = slow_bridge
    listener = _listener(monkeypatch, brain)
    await listener._handle_command({"type": "shutdown_agent", "payload": {}})
    await _until(lambda: in_flight)  # the card is on its way
    closing = asyncio.ensure_future(listener.aclose())
    await asyncio.sleep(0.02)
    assert not closing.done()  # the job's end waits (bounded) for the flow
    delivered.set()
    await asyncio.wait_for(closing, 5)
    await _flows(brain)
    await _until(lambda: bridge.resolved)

    [card] = bridge.requests
    assert bridge.resolved == [{"id": card["id"], "allow": False, "reason": "cancelled"}] and not broker.pending()
    assert _audited(audit, "SHUTDOWN_AGENT") == ["ALLOW=False | SOURCE=broker | REASON=cancelled | RESTART=True"]
    assert brain.end_reason is None

    await listener._handle_command({"type": "clear_memory", "payload": {}})  # dispatched after the listener closed
    assert await control_commands.confirm_control("clear_memory", {}, "vector", control_commands.CLEAR_VECTOR_RISK,
                                                  ctx=listener._ctx) == (False, "cancelled", "job_end")
    await asyncio.sleep(0.05)
    assert len(bridge.requests) == 1 and not brain._spawned_tasks


def test_the_guarded_commands_have_handlers_and_the_pause_rule_stays():
    assert {"stop", "mute", "unmute", "clear_memory", "shutdown_agent", "pause_agent"} <= set(UI_COMMANDS)
    assert "shutdown_agent" not in agent_loops.FORWARDED_UNHANDLED
    # K-60 v9: only the bridge's own pause_agent ends a job; C-51 leaves that rule alone
    assert agent_loops.bridge_holds_pause({"by": "bridge"}) and not agent_loops.bridge_holds_pause({"by": "ui"})


# ---------------------------------------------------------------- C-51 verify (09 Oct): three more holes


async def test_an_unmute_while_a_rebuilt_session_starts_turns_its_microphone_back_on(brain):
    """A C-33 rebuild: agent_runner switched the new session's microphone off before start() (muted), the owner
    unmuted while start() ran, and that reached only the old session. attach_session matches the new one."""
    brain.session = _Session()
    brain.set_muted(True)
    rebuilt = _Session()
    rebuilt.input.set_audio_enabled(False)  # agent_runner._start_session for a muted assistant
    brain.set_muted(False)  # during start(): only the old session hears it
    brain.attach_session(rebuilt)
    assert rebuilt.input.audio_enabled and rebuilt.mic.attached


async def test_a_mute_while_a_rebuilt_session_starts_turns_its_microphone_off(brain):
    brain.session = _Session()
    rebuilt = _Session()  # started unmuted, microphone on
    brain.set_muted(True)  # during start(): only the old session hears it
    brain.attach_session(rebuilt)
    assert not rebuilt.input.audio_enabled and not rebuilt.mic.attached


@pytest.mark.parametrize(("decided", "expected"), [
    ({"status": "started", "tool": "shutdown_agent", "confirm_id": "c1", "reason": "approved"},
     (True, "approved", "broker")),
    ({"status": "cancelled", "tool": "shutdown_agent", "confirm_id": "c1", "reason": "denied"},
     (False, "denied", "broker")),
    ({"status": "error", "error_code": "CONFIRM_UI_UNREACHABLE", "confirm_id": "c1"}, None),
])
async def test_a_decision_made_while_its_card_was_being_delivered_is_the_owners(broker, monkeypatch, decided, expected):
    """The broker hands back the decision when the owner answered before the delivery returned (a slow bridge answer,
    then the Allow): an Allow there is the owner's Allow, not a refusal; a refusal keeps its own mapping."""
    monkeypatch.setattr(broker, "create_pending", AsyncMock(return_value=decided))
    result = await control_commands.confirm_control("shutdown_agent", {}, "Shutdown", "high")
    if expected is None:
        assert result[0] is False and result[2] == "broker" and result[1] != "approved"
    else:
        assert result == expected


async def test_stop_cancels_the_typed_rescue_so_a_stopped_question_is_not_asked_again(audit):
    forgotten: list[int] = []
    interrupted = await control_commands.stop_speaking(AsyncMock(return_value=True),
                                                       forget_typed=lambda: forgotten.append(1) or 2)
    assert interrupted and forgotten == [1]
    [line] = _audited(audit, "STOP_SPEAKING")
    assert "TYPED_RESCUES_CANCELLED=2" in line


async def test_the_dashboard_stop_reaches_the_jobs_router(brain, monkeypatch, audit):
    """A typed question stopped before its first audio adds no assistant item; the rescue would ask the session again
    ~12 s later and answer it (C-50 x C-51). The UI's stop goes through the job's router."""
    router = cr.ConversationRouter(brain, notify=AsyncMock(), reason=AsyncMock(return_value={"plan": []}),
                                   notify_reasoning=AsyncMock())
    brain.attach_router(router)
    session = _Session()
    brain.session = session
    router.attach(session)
    assert await router.handle_text("Jarvis, lambi report banao", "t-1") == "accepted"
    assert router._awaiting
    listener = _listener(monkeypatch, brain)
    await listener._handle_command({"type": "stop", "payload": None})
    assert router._awaiting == {} and "TYPED_RESCUES_CANCELLED=1" in _audited(audit, "STOP_SPEAKING")[0]


# ---------------------------------------------------------------- C-51 live check (09 Oct): the server's generation


def _realtime(brain, open_generation: bool | None):
    """A live activity whose Gemini session has an open (or done, or no) current generation."""
    generation = None if open_generation is None else SimpleNamespace(_done=not open_generation)
    rt = SimpleNamespace(_current_generation=generation, _session_resumption_handle="h", restarts=0)
    rt._mark_restart_needed = lambda: setattr(rt, "restarts", rt.restarts + 1)
    brain._activity = SimpleNamespace(realtime_llm_session=rt)
    return rt


@pytest.mark.parametrize(("open_generation", "restarts"), [(True, 1), (False, 0), (None, 0)])
async def test_stop_cuts_a_reply_the_server_is_still_generating(brain, open_generation, restarts):
    """Live: with automatic activity detection the plugin's interrupt() sends the server nothing; a stopped answer went
    on server side and the next typed question got no reply within 45 s. An open generation is cut by a fresh
    reconnect; a finished one (only the playout was left) needs none."""
    session = _Session(current=SpeechHandle.create(allow_interruptions=True))
    brain.session = session
    rt = _realtime(brain, open_generation)
    assert await brain.interrupt_speech() is True
    assert rt.restarts == restarts
    assert (rt._session_resumption_handle is None) is (restarts == 1)


async def test_a_stop_while_nothing_is_said_reconnects_nothing(brain):
    session = _Session()
    brain.session = session
    rt = _realtime(brain, open_generation=True)
    await brain.interrupt_speech()
    assert rt.restarts == 0
