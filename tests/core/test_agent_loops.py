import asyncio
import importlib
import socket
import uuid
import weakref
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import socketio
import uvicorn

from services.ai_core import agent_loops, control_commands
from services.ai_core.agent_loops import (
    start_memory_storage_loop,
    start_reminder_loop,
    start_bug_hunter_loop,
    start_ui_command_listener
)
from services.utils import jarvis_bridge
from services.utils.jarvis_config import config
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore


def _transcription_role_and_text(mock_notify):
    """Return (role, text) of the single awaited notify_transcription call.

    Reads positional or keyword arguments, so extra kwargs (msg_id, source) do not break the check.
    """
    mock_notify.assert_awaited_once()
    args, kwargs = mock_notify.await_args
    role = args[0] if len(args) > 0 else kwargs.get("role")
    text = args[1] if len(args) > 1 else kwargs.get("text")
    return role, text

@pytest.mark.asyncio
async def test_start_memory_storage_loop():
    mock_assistant = MagicMock()
    mock_assistant.memory_extractor.memory.save_to_disk = AsyncMock()

    # Run loop once and then cancel
    with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
        try:
            await start_memory_storage_loop(mock_assistant)
        except asyncio.CancelledError:
            pass

    mock_assistant.memory_extractor.memory.save_to_disk.assert_called()

@pytest.mark.asyncio
async def test_start_reminder_loop():
    mock_session = MagicMock()
    mock_session.say = AsyncMock()

    # Mock check_due_reminders to return one item
    mock_due = [{"message": "Test Reminder"}]
    # Reminders reach the UI as text via notify_transcription (no TTS / session.say)
    with patch("services.ai_core.agent_loops.notify_transcription", new_callable=AsyncMock) as mock_notify:
        with patch("asyncio.to_thread", side_effect=[mock_due, asyncio.CancelledError()]):
            with patch("asyncio.sleep"): # To skip the sleep(30)
                try:
                    await start_reminder_loop(mock_session)
                except asyncio.CancelledError:
                    pass

    role, text = _transcription_role_and_text(mock_notify)
    assert role == "assistant"
    assert "Test Reminder" in text
    assert mock_notify.await_args.kwargs.get("source") == "system"  # C-MSG-1: reminders are system messages
    mock_session.say.assert_not_called()

@pytest.mark.asyncio
async def test_start_bug_hunter_loop():
    mock_session = MagicMock()
    mock_session.say = AsyncMock()

    # Mock monitor_logs to call the callback once and then escape loop
    async def mock_monitor(callback):
        await callback("Test Error")
        raise asyncio.CancelledError()

    # Detected errors reach the UI as text via notify_transcription (no TTS / session.say)
    with patch("services.ai_core.agent_loops.notify_transcription", new_callable=AsyncMock) as mock_notify:
        with patch("services.ai_core.agent_loops.monitor_logs", side_effect=mock_monitor):
            try:
                await start_bug_hunter_loop(mock_session)
            except asyncio.CancelledError:
                pass

    role, text = _transcription_role_and_text(mock_notify)
    assert role == "assistant"
    assert "Test Error" in text
    # C-124 (C-64 review SP-4): the log excerpt persists in chat history, so it is framed as untrusted data.
    assert "[LOG DATA, untrusted]" in text and text.rstrip().endswith("[END LOG DATA]")
    assert text.index("[LOG DATA, untrusted]") < text.index("Test Error") < text.index("[END LOG DATA]")
    assert mock_notify.await_args.kwargs.get("source") == "system"
    mock_session.say.assert_not_called()

@pytest.mark.asyncio
async def test_start_ui_command_listener():
    mock_assistant = MagicMock()
    mock_assistant.set_muted = MagicMock()

    mock_sio = MagicMock()
    mock_sio.connect = AsyncMock()
    mock_sio.wait = AsyncMock()

    handlers = {}
    def mock_on(event, handler):
        handlers[event] = handler
        return handler
    mock_sio.on.side_effect = mock_on

    with patch("socketio.AsyncClient", return_value=mock_sio):
        # We start the listener as a task because it blocks on wait()
        task = asyncio.create_task(start_ui_command_listener(mock_assistant))
        await asyncio.sleep(0.1)

        # Trigger mute
        if 'agent_command' in handlers:
            await handlers['agent_command']({"type": "mute"})
            mock_assistant.set_muted.assert_called_with(True)

            # Trigger unmute
            await handlers['agent_command']({"type": "unmute"})
            mock_assistant.set_muted.assert_called_with(False)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ================================================================ 2026-10-06: overload debounce (adaptation loop)
# A single CPU spike (first vision call + model load) used to fire the 'SYSTEM OVERLOAD' throttle at once; now it
# takes OVERLOAD_CONSECUTIVE_SAMPLES consecutive high readings. C-33 step 5: only the bug hunter is ever throttled
# (memory and reminders never stop), at WARNING, and each loop (one per job) keeps its own streak.


def _adaptation_run(monkeypatch, readings):
    """Runs start_adaptation_loop over the given stress readings with a job protector stand-in, then cancels it on
    the next sleep. Returns (logger mock, protector stand-in, task mock, fake sleep)."""
    monkeypatch.setattr(agent_loops.adaptive_engine, "observe_only", False)
    remaining = list(readings)
    monkeypatch.setattr(agent_loops.adaptive_engine, "analyze_system_load", lambda: remaining.pop(0))
    task = MagicMock()
    task.done.return_value = False
    protector = SimpleNamespace(get_task=MagicMock(return_value=task), restart_task=MagicMock(return_value=True))
    # recovery must never trigger the heavy vector preload in a test
    monkeypatch.setattr(agent_loops, "jarvis_vector_db", SimpleNamespace(client=object()))
    log = MagicMock()
    monkeypatch.setattr(agent_loops, "logger", log)

    async def fake_sleep(_delay):
        if not remaining:
            raise asyncio.CancelledError

    return log, protector, task, fake_sleep


@pytest.mark.asyncio
async def test_a_single_cpu_spike_only_warns_and_cancels_nothing(monkeypatch):
    log, protector, task, fake_sleep = _adaptation_run(monkeypatch, ["CRITICAL", "NORMAL"])
    with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await agent_loops.start_adaptation_loop(protector)

    task.cancel.assert_not_called()
    log.critical.assert_not_called()
    assert log.warning.call_count == 1  # exactly one WARNING for the spike
    assert "High system load" in log.warning.call_args_list[0].args[0]
    protector.restart_task.assert_not_called()  # nothing was throttled, so nothing is restored


@pytest.mark.asyncio
async def test_sustained_overload_throttles_only_the_bug_hunter_at_warning(monkeypatch):
    assert agent_loops.OVERLOAD_CONSECUTIVE_SAMPLES == 2
    assert agent_loops.NON_ESSENTIAL_TASKS == ("bug_hunter_loop",)
    log, protector, task, fake_sleep = _adaptation_run(monkeypatch, ["CRITICAL", "CRITICAL"])
    with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await agent_loops.start_adaptation_loop(protector)

    asked = [c.args[0] for c in protector.get_task.call_args_list]
    assert asked == ["bug_hunter_loop"]  # memory_loop and reminder_loop are never even looked up
    assert task.cancel.call_count == 1
    log.critical.assert_not_called()
    overload = [c for c in log.warning.call_args_list if "SYSTEM OVERLOAD" in c.args[0]]
    assert len(overload) == 1 and "bug_hunter_loop" in overload[0].args


@pytest.mark.asyncio
async def test_recovery_after_sustained_overload_restores_the_bug_hunter(monkeypatch):
    _log, protector, _task, fake_sleep = _adaptation_run(monkeypatch, ["CRITICAL", "CRITICAL", "NORMAL"])
    with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await agent_loops.start_adaptation_loop(protector)

    protector.restart_task.assert_called_once_with("bug_hunter_loop")


@pytest.mark.asyncio
async def test_observe_only_mode_never_counts_toward_the_overload_streak(monkeypatch):
    log, protector, task, fake_sleep = _adaptation_run(monkeypatch, ["CRITICAL", "CRITICAL"])
    monkeypatch.setattr(agent_loops.adaptive_engine, "observe_only", True)
    with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await agent_loops.start_adaptation_loop(protector)

    task.cancel.assert_not_called()
    log.critical.assert_not_called()
    log.warning.assert_not_called()


@pytest.mark.asyncio
async def test_two_jobs_readings_never_add_up_to_one_streak(monkeypatch):
    """C-33 step A (07 Oct log): two job threads' readings 73 ms apart counted as 'consecutive' through a module
    global and throttled the live job. Each loop now has its own streak: one high reading per loop throttles nothing."""
    for _job in range(2):
        log, protector, task, fake_sleep = _adaptation_run(monkeypatch, ["CRITICAL"])
        with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
            await agent_loops.start_adaptation_loop(protector)
        task.cancel.assert_not_called()
        assert log.warning.call_count == 1


@pytest.mark.asyncio
async def test_a_job_protector_is_used_instead_of_the_global_one(monkeypatch):
    _log, protector, task, fake_sleep = _adaptation_run(monkeypatch, ["CRITICAL", "CRITICAL"])
    global_get = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(agent_loops.autonomous_protector, "get_task", global_get)
    with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await agent_loops.start_adaptation_loop(protector)

    global_get.assert_not_called()
    task.cancel.assert_called_once()


# ================================================================ plan task C-15: agent link v2 on the listener
# Throwaway credentials only; the end-to-end tests run the real bridge (src/core/ui_bridge.py) under uvicorn on a
# free 127.0.0.1 port with a temporary bridge.db, so nothing reaches the live bridge or a real env file.
# pylint: disable=protected-access,redefined-outer-name

AGENT_TOKEN = "c15-loops-agent-token-" + "d" * 24
HMAC_SECRET = "c15-loops-hmac-secret-" + "e" * 24
LEGACY_TOKEN = "c15-loops-legacy-token-" + "f" * 24


@pytest.fixture
def v2_keys(monkeypatch):
    """Agent link v2 on with throwaway keys and an empty retry queue."""
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setattr(jarvis_bridge, "_queues", type(jarvis_bridge._queues)())


def _listener(monkeypatch, sio=None, assistant=None) -> agent_loops.UIBridgeListener:
    monkeypatch.setattr(agent_loops.socketio, "AsyncClient", MagicMock(return_value=sio or MagicMock()))
    return agent_loops.UIBridgeListener(assistant or MagicMock())


def test_with_the_v2_keys_the_public_bridge_gets_the_agent_token(v2_keys, monkeypatch):
    monkeypatch.setattr(agent_loops, "_job_identity", lambda: ("jarvis-hybrid", "AJ_c15test"))
    listener = _listener(monkeypatch)
    host, version = agent_loops._agent_identity()
    assert host == (socket.gethostname() or "unknown")[:64] and 0 < len(version) <= 12
    for url in ("http://203.0.113.10:5101", "https://jarvis.example/vortex"):
        # the room is declared too: K-38 v4 sends ui_command only to agents whose room is JARVIS_ROOM_NAME
        assert listener._auth(url) == {"agent_token": AGENT_TOKEN, "agent_id": host, "version": version,
                                       "room": "jarvis-hybrid", "job_id": "AJ_c15test"}
    # the VPS brain's internal listener keeps its own agent role (K-38 v4)
    assert listener._auth("http://jarvis-bridge:5002") == {"role": "agent", "room": "jarvis-hybrid",
                                                           "job_id": "AJ_c15test"}


@pytest.mark.parametrize("missing", ["agent_token", "bridge_hmac_secret"])
def test_without_the_v2_keys_the_legacy_token_stays(v2_keys, monkeypatch, missing):
    monkeypatch.setattr(config, missing, None)
    listener = _listener(monkeypatch)
    assert listener._auth("https://jarvis.example/vortex") == {"token": LEGACY_TOKEN}


def test_the_agent_version_comes_from_git_sha_when_set(monkeypatch):
    agent_loops._agent_identity.cache_clear()
    monkeypatch.setenv("GIT_SHA", "abcdef1234567890")
    try:
        assert agent_loops._agent_identity()[1] == "abcdef123456"
    finally:
        agent_loops._agent_identity.cache_clear()


@pytest.mark.asyncio
async def test_the_listener_reconnects_after_a_failed_connect_and_after_a_disconnect(v2_keys, monkeypatch):
    sio = MagicMock(disconnect=AsyncMock(), wait=AsyncMock())  # wait() returning = the bridge dropped us
    sio.connect = AsyncMock(side_effect=[ConnectionError("bridge down"), None, None, ConnectionError("down")])
    listener = _listener(monkeypatch, sio)
    monkeypatch.setattr(config, "bridge_url", "https://jarvis.example/vortex")
    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)
        if len(slept) == 4:
            raise asyncio.CancelledError  # the loop never ends by itself

    with patch.object(agent_loops.asyncio, "sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await listener.start()

    assert sio.connect.await_count == 4 and sio.wait.await_count == 2
    calls = sio.connect.await_args_list
    assert all(call.args == ("https://jarvis.example",) for call in calls)
    assert all(call.kwargs["socketio_path"] == "vortex/socket.io" for call in calls)
    auths = [call.kwargs["auth"] for call in calls]
    assert all(auth["agent_token"] == AGENT_TOKEN and "token" not in auth for auth in auths)
    assert len({id(auth) for auth in auths}) == 4  # a fresh auth for every connect
    assert slept == [1, 2, 4, 8]


@pytest.mark.asyncio
async def test_a_connect_wakes_the_notify_retry_queue(monkeypatch):
    wake = MagicMock()
    monkeypatch.setattr(agent_loops, "wake_retry_queue", wake)
    listener = _listener(monkeypatch)
    await listener.on_connect()
    wake.assert_called_once_with()


@pytest.fixture
def chat(monkeypatch):
    """A listener with a recorded notify_transcription and an assistant that answers 'Ji Sir'."""
    notify = AsyncMock()
    monkeypatch.setattr(agent_loops, "notify_transcription", notify)
    assistant = MagicMock()
    assistant._spawned_tasks = set()
    assistant.handle_user_query = AsyncMock(return_value="Ji Sir")
    return SimpleNamespace(listener=_listener(monkeypatch, assistant=assistant), assistant=assistant, notify=notify)


async def _send_chat(chat, payload) -> list[Any]:
    await chat.listener._handle_command({"type": "chat", "payload": payload})
    await asyncio.gather(*list(chat.assistant._spawned_tasks))
    return chat.notify.await_args_list


@pytest.mark.asyncio
async def test_a_chat_dict_keeps_its_id(chat):
    user, reply = await _send_chat(chat, {"id": "u-42", "text": "salam; Sir"})

    assert user.args == ("user", "salam; Sir")  # the echo carries the text as typed
    assert user.kwargs == {"msg_id": "u-42", "source": "text"}  # the UI's id: one bubble, not two
    assert reply.args == ("assistant", "Ji Sir")
    assert reply.kwargs["source"] == "text" and reply.kwargs["turn_id"] == "u-42"
    assert reply.kwargs["msg_id"] not in (None, "u-42")
    chat.assistant.handle_user_query.assert_awaited_once_with("salam Sir")  # sanitized for the query only


@pytest.mark.asyncio
async def test_a_legacy_chat_string_gets_a_new_id(chat):
    user, reply = await _send_chat(chat, "hi")
    new_id = user.kwargs["msg_id"]
    assert user.args == ("user", "hi") and str(uuid.UUID(new_id)) == new_id
    assert user.kwargs["source"] == "text" and reply.kwargs["turn_id"] == new_id


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", [None, 5, "", "a" * 129, "bad\nid"])
async def test_a_chat_dict_without_a_usable_id_gets_a_new_one(chat, bad_id):
    user, _ = await _send_chat(chat, {"id": bad_id, "text": "hi"})
    assert user.kwargs["msg_id"] != bad_id and str(uuid.UUID(user.kwargs["msg_id"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, "", "   ", 42, {"id": "u-1"}, {"id": "u-1", "text": ""},
                                     {"id": "u-1", "text": 5}, ["hi"]])
async def test_a_chat_without_text_is_ignored(chat, payload):
    assert await _send_chat(chat, payload) == []
    chat.assistant.handle_user_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_query_still_answers_the_same_turn(chat):
    chat.assistant.handle_user_query.side_effect = RuntimeError("model down")
    user, reply = await _send_chat(chat, {"id": "u-7", "text": "hi"})
    assert user.kwargs["msg_id"] == "u-7" and reply.kwargs["turn_id"] == "u-7"
    assert "issue" in reply.args[1]


# ================================================================ C-23 (K-60 v9): stop interrupts, pause_agent ends the job


class _EndingAssistant:
    """The parts of BrainAssistant the listener uses for stop and pause_agent (the real methods, bound here)."""

    def __init__(self) -> None:
        from src.core.agent_core import BrainAssistant  # pylint: disable=import-outside-toplevel

        self._stop_event = asyncio.Event()
        self.end_reason = None
        self._active_session = None
        self._activity = None  # no realtime session: the C-51 server-side cut has nothing to restart
        self.request_end = BrainAssistant.request_end.__get__(self)
        self.interrupt_speech = BrainAssistant.interrupt_speech.__get__(self)
        self._session_busy = BrainAssistant._session_busy.__get__(self)
        self._cut_server_reply = BrainAssistant._cut_server_reply.__get__(self)


@pytest.mark.asyncio
async def test_a_confirm_response_from_the_dashboard_resolves_the_broker(monkeypatch):
    """C-29 (K-14): the owner's Allow/Deny reaches the broker as a 'ui' decision; a bad payload never does."""
    from services.ai_core import jarvis_confirm  # pylint: disable=import-outside-toplevel

    resolve = AsyncMock(return_value={"status": "started"})
    monkeypatch.setattr(jarvis_confirm.confirmation_broker, "resolve", resolve)
    listener = _listener(monkeypatch)

    await listener._handle_command({"type": "confirm_response", "payload": {"id": "c" * 32, "allow": True}})
    await listener._handle_command({"type": "confirm_response", "payload": {"id": "d" * 32, "allow": False}})
    for bad in ({"id": "e" * 32, "allow": "yes"}, {"allow": True}, "allow", None):
        await listener._handle_command({"type": "confirm_response", "payload": bad})

    assert [call.args + tuple(sorted(call.kwargs.items())) for call in resolve.await_args_list] == [
        ("c" * 32, True, ("source", "ui")), ("d" * 32, False, ("source", "ui"))]


@pytest.mark.asyncio
async def test_stop_only_interrupts_speech_and_never_ends_the_session(monkeypatch):
    assistant = _EndingAssistant()
    session = MagicMock()
    interrupted = asyncio.get_running_loop().create_future()
    interrupted.set_result(None)
    session.interrupt = MagicMock(return_value=interrupted)
    assistant._active_session = session
    shutdown_signal = MagicMock()
    monkeypatch.setattr(agent_loops.autonomous_protector, "shutdown_signal", shutdown_signal)
    monkeypatch.setattr(control_commands, "notify_ui", AsyncMock())  # C-51: stop clears the UI's speaking state
    listener = _listener(monkeypatch, assistant=assistant)

    await listener._handle_command({"type": "stop", "payload": {}})

    session.interrupt.assert_called_once_with()
    assert not assistant._stop_event.is_set() and assistant.end_reason is None  # no session restart, no job end
    shutdown_signal.set.assert_not_called()


BRIDGE_PAUSE = {"type": "pause_agent", "payload": {"reason": "paused", "by": "bridge"}}  # bridge_rooms.PAUSE_COMMAND


@pytest.mark.asyncio
async def test_pause_agent_ends_the_job_as_paused_by_the_owner(monkeypatch):
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel

    assert BRIDGE_PAUSE == bridge_rooms.PAUSE_COMMAND  # the agent and the bridge agree on the hold marker
    assistant = _EndingAssistant()
    listener = _listener(monkeypatch, assistant=assistant)
    await listener._handle_command(dict(BRIDGE_PAUSE))
    assert assistant._stop_event.is_set() and assistant.end_reason == "paused_by_owner"
    # a later request keeps the first reason
    assistant.request_end("terminated_by_owner")
    assert assistant.end_reason == "paused_by_owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"reason": "paused"}, {"by": "ui"}, None, "bridge"])
async def test_a_pause_agent_the_bridge_does_not_hold_changes_nothing(monkeypatch, payload):
    """K-60 v9 (SEC-2/LS-1): a bridge before C-23 forwards the UI's own pause_agent; nothing would keep the agent out
    or bring it back on Wake, so only the bridge's own command (payload.by 'bridge') ends the job."""
    assistant = _EndingAssistant()
    listener = _listener(monkeypatch, assistant=assistant)
    await listener._handle_command({"type": "pause_agent", "payload": payload})
    assert not assistant._stop_event.is_set() and assistant.end_reason is None


@pytest.mark.asyncio
async def test_pause_agent_on_an_assistant_before_c23_sets_its_stop_event(monkeypatch):
    old = SimpleNamespace(_stop_event=asyncio.Event())
    listener = _listener(monkeypatch, assistant=old)
    await listener._handle_command(dict(BRIDGE_PAUSE))
    assert old._stop_event.is_set()


@pytest.mark.asyncio
async def test_start_changes_nothing_here(monkeypatch):
    """'start' (Wake) belongs to the bridge's room owner. shutdown_agent ends the job only after the owner's
    confirmation since C-51 (tests/core/test_control_commands.py)."""
    assistant = _EndingAssistant()
    listener = _listener(monkeypatch, assistant=assistant)
    await listener._handle_command({"type": "start", "payload": {}})
    assert not assistant._stop_event.is_set() and assistant.end_reason is None


@pytest.mark.asyncio
async def test_interrupt_speech_without_a_session_or_with_a_closed_one_reports_false():
    assistant = _EndingAssistant()
    assert await assistant.interrupt_speech() is False
    session = MagicMock()
    session.interrupt = MagicMock(side_effect=RuntimeError("AgentSession isn't running"))
    assistant._active_session = session
    assert await assistant.interrupt_speech() is False
    never_done = asyncio.get_running_loop().create_future()  # a playout that never finishes: waited at most wait_s
    session.interrupt = MagicMock(return_value=never_done)
    assert await assistant.interrupt_speech(wait_s=0.01) is True
    never_done.cancel()


# ---------------------------------------------------------------- end to end with the real bridge


class _Peer:
    """A legacy dashboard socket that records what it receives."""

    def __init__(self) -> None:
        self.sio = socketio.AsyncClient(reconnection=False)
        self.events: list[tuple[str, Any]] = []
        self.sio.on("*", self._record)

    async def _record(self, event: str, *args: Any) -> None:
        self.events.append((event, args[0] if args else None))

    def of(self, name: str) -> list[Any]:
        return [data for event, data in self.events if event == name]


async def _until(predicate, timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.02)


async def _idle() -> None:
    await asyncio.Event().wait()


class _RealBridge:
    """src/core/ui_bridge.py under uvicorn on one 127.0.0.1 port in this event loop; stop() and start() again
    behave like 'docker restart jarvis-bridge'."""

    def __init__(self, module: Any) -> None:
        self.module = module
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        eio = self.module.sio.eio
        eio.start_service_task, eio.service_task_handle, eio.service_task_event = True, None, None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for _ in range(50):
            try:
                sock.bind(("127.0.0.1", self.port))
                break
            except OSError:  # the old listener is still going away
                await asyncio.sleep(0.1)
        sock.listen()
        self.server = uvicorn.Server(uvicorn.Config(self.module.app_asgi, log_level="warning", lifespan="on",
                                                    timeout_graceful_shutdown=2))
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
        await _until(lambda: self.server.started or self.task.done(), 10)
        assert self.server.started, "the test bridge did not start"

    async def stop(self) -> None:
        await self.module.sio.shutdown()
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 15)

    def agents(self) -> list[Any]:
        return [c for c in self.module._clients.values() if c.role == "agent"]


@pytest.fixture
async def real_bridge(v2_keys, monkeypatch, tmp_path):
    """The real bridge with a temporary store and idle periodic loops, plus fast reconnect and retry timing on the
    agent side (the backoff values themselves are covered above and in tests/unit/test_notify_signing.py)."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    for name, value in (("store", store), ("bridge_manager", module.BridgeStateManager()), ("_clients", {}),
                        ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)):
        monkeypatch.setattr(module, name, value)
    for name in ("vitals_heartbeat", "telemetry_heartbeat", "user_speaking_monitor", "store_retention_loop"):
        monkeypatch.setattr(module, name, _idle)
    for name in ("start_service_task", "service_task_handle", "service_task_event"):
        monkeypatch.setattr(module.sio.eio, name, getattr(module.sio.eio, name))  # restored afterwards
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    monkeypatch.setattr(agent_loops, "RECONNECT_FIRST_S", 0.1)
    monkeypatch.setattr(agent_loops, "RECONNECT_MAX_S", 0.5)
    monkeypatch.setattr(jarvis_bridge, "NOTIFY_TIMEOUT_S", 1.0)
    monkeypatch.setattr(jarvis_bridge, "RETRY_FIRST_S", 30.0)  # only a wake (the socket coming back) flushes
    monkeypatch.setattr(jarvis_bridge, "_http_by_loop", weakref.WeakKeyDictionary())  # this loop's client only
    bridge = _RealBridge(module)
    monkeypatch.setattr(config, "bridge_url", bridge.url)
    await bridge.start()
    peers: list[_Peer] = []
    yield SimpleNamespace(bridge=bridge, store=store, peers=peers)
    for peer in peers:
        if peer.sio.connected:
            await peer.sio.disconnect()
    if bridge.server is not None and not bridge.task.done():
        await bridge.stop()
    await jarvis_bridge.aclose_loop_client()
    await store.close()


async def _start_agent(monkeypatch, assistant) -> tuple[agent_loops.UIBridgeListener, asyncio.Task[Any]]:
    listener = agent_loops.UIBridgeListener(assistant)
    seen: list[str] = []

    async def record(event: str, *_args: Any) -> None:
        seen.append(event)

    listener.sio.on("*", record)
    listener.seen = seen
    return listener, asyncio.create_task(listener.start())


@pytest.mark.asyncio
async def test_end_to_end_the_agent_joins_as_role_agent_and_a_typed_chat_keeps_its_id(real_bridge, monkeypatch):
    assistant = MagicMock()
    assistant._spawned_tasks = set()
    assistant.handle_user_query = AsyncMock(return_value="Ji Sir")
    listener, task = await _start_agent(monkeypatch, assistant)
    bridge = real_bridge.bridge
    try:
        await _until(lambda: len(bridge.agents()) == 1)
        [agent] = bridge.agents()
        assert (agent.host, agent.version) == agent_loops._agent_identity()

        ui = _Peer()
        real_bridge.peers.append(ui)
        await ui.sio.connect(bridge.url, auth={"token": LEGACY_TOKEN}, transports=["websocket"], wait_timeout=5)
        await _until(lambda: ui.of("init_state"))
        ack = await ui.sio.call("ui_command", {"type": "chat", "payload": {"id": "u-42", "text": "salam"}},
                                timeout=5)
        assert ack == {"status": "sent"}
        await _until(lambda: len(ui.of("new_message")) == 2)

        user, reply = ui.of("new_message")
        assert (user["id"], user["role"], user["text"], user["source"]) == ("u-42", "user", "salam", "text")
        assert (reply["role"], reply["text"], reply["source"], reply["turn_id"]) == (
            "assistant", "Ji Sir", "text", "u-42")
        messages, _ = await real_bridge.store.list_messages()
        assert [m["id"] for m in messages] == ["u-42", reply["id"]]  # the UI's id, stored once
        assistant.handle_user_query.assert_awaited_once_with("salam")
        # room 'agent' only: the agent never gets the dashboard's events
        assert not {"init_state", "new_message", "transcription_update"} & set(listener.seen)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_end_to_end_after_a_bridge_restart_the_agent_reconnects_and_queued_transcripts_arrive(
        real_bridge, monkeypatch):
    listener, task = await _start_agent(monkeypatch, MagicMock())
    bridge = real_bridge.bridge
    try:
        await _until(lambda: len(bridge.agents()) == 1)
        await jarvis_bridge.notify_transcription("user", "before", msg_id="t-0")

        await bridge.stop()  # 'docker restart jarvis-bridge': the bridge is gone for a moment
        await asyncio.gather(*(jarvis_bridge.notify_transcription("assistant", f"while down {n}", msg_id=f"t-{n}")
                               for n in (1, 2, 3)))
        assert jarvis_bridge.retry_queue_size() == 3
        bridge.module._clients.clear()
        await bridge.start()

        await _until(lambda: len(bridge.agents()) == 1)  # the listener came back by itself, as role 'agent'
        await _until(lambda: jarvis_bridge.retry_queue_size() == 0)  # its connect flushed the queue
        messages, _ = await real_bridge.store.list_messages()
        assert sorted(m["id"] for m in messages) == ["t-0", "t-1", "t-2", "t-3"]  # none lost, none twice
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert listener.sio.connected is False


# ---------------------------------------------------------------- C-34: registry, dedupe and the job's listener

LEGACY_COMMANDS = {"mute", "unmute", "wake_word_toggle", "stop", "pause_agent", "persona_change", "clear_memory",
                   "open_sim_panel", "sim_lookup", "chat", "confirm_response", "swarm", "job_cancel",
                   "open_job_artifact"}


def test_every_legacy_command_has_its_registered_handler():
    from services.utils.ui_command_registry import UI_COMMANDS

    assert LEGACY_COMMANDS <= set(UI_COMMANDS)
    assert agent_loops.register_ui_command is not None and agent_loops.dispatch is not None  # re-exported (K-60)
    assert not (agent_loops.FORWARDED_UNHANDLED & LEGACY_COMMANDS)


@pytest.mark.asyncio
async def test_a_repeated_chat_id_runs_once(chat):
    """C-34 (K-60): the same (type, id) twice - two tabs, a resent command - is answered once."""
    payload = {"id": "c34-same-id", "text": "Jarvis, 7 times 8?"}
    await _send_chat(chat, payload)
    await _send_chat(chat, dict(payload))
    assert chat.assistant.handle_user_query.await_count == 1


@pytest.mark.asyncio
async def test_an_unknown_command_is_logged_and_shown_but_a_forwarded_one_is_not(monkeypatch):
    shown: list[tuple[str, str]] = []

    async def notify_log(message, category="SYSTEM"):
        shown.append((message, category))

    monkeypatch.setattr(agent_loops, "notify_log", notify_log)
    listener = _listener(monkeypatch)
    await listener._handle_command({"type": "c34_nonsense", "payload": {}})
    assert shown == [("Unknown UI command: c34_nonsense", "SYSTEM")]
    for forwarded in ("start", "settings_update", "request_state", "voice_enroll"):
        await listener._handle_command({"type": forwarded, "payload": {}})
    assert len(shown) == 1  # the bridge forwards these; no agent handler yet, no dashboard noise


@pytest.mark.asyncio
async def test_aclose_ends_the_listener_without_a_reconnect(v2_keys, monkeypatch):
    """C-34: the job's listener stops with the job: aclose() disconnects, wait() returns and start() returns
    instead of reconnecting on the old client."""
    released = asyncio.Event()

    async def wait():
        await released.wait()

    async def disconnect():
        released.set()

    sio = MagicMock(connect=AsyncMock(), wait=wait, disconnect=disconnect)
    listener = _listener(monkeypatch, sio)
    monkeypatch.setattr(config, "bridge_url", "https://jarvis.example/vortex")
    runner = asyncio.ensure_future(listener.start())
    for _ in range(50):
        if sio.connect.await_count:
            break
        await asyncio.sleep(0.01)
    await listener.aclose()
    await asyncio.wait_for(runner, 2)
    assert sio.connect.await_count == 1 and listener.closing


def test_each_job_gets_its_own_client(monkeypatch):
    clients: list[Any] = []
    monkeypatch.setattr(agent_loops.socketio, "AsyncClient", lambda **_k: clients.append(MagicMock()) or clients[-1])
    first, second = agent_loops.UIBridgeListener(MagicMock()), agent_loops.UIBridgeListener(MagicMock())
    assert first.sio is clients[0] and second.sio is clients[1] and first.sio is not second.sio


# ---------------------------------------------------------------- C-125 (K-77 step 1): agent_command 'announce'


def _announce_ctx(linked: str | None) -> Any:
    voice_link = SimpleNamespace(linked_identity=lambda: linked)
    return agent_loops.CommandContext(assistant=MagicMock(), runtime=SimpleNamespace(voice_link=voice_link))


@pytest.mark.asyncio
async def test_announce_with_a_linked_participant_is_shown_spoken_and_reported(monkeypatch):
    from services.utils import jarvis_announcer
    from services.utils.ui_command_registry import reset_dedupe

    reset_dedupe()
    announced, results = [], []
    monkeypatch.setattr(jarvis_announcer, "announce",
                        AsyncMock(side_effect=lambda text, **kw: announced.append((text, kw))))
    monkeypatch.setattr(agent_loops, "notify_event", AsyncMock(side_effect=lambda t, p: results.append((t, p))))
    payload = {"id": "r:rem_1:ab#a1", "text": " pani\n peena ", "kind": "reminder"}
    assert await agent_loops.dispatch("announce", payload, _announce_ctx("owner-1a2b3c4d")) is True
    assert announced == [("Reminder: pani peena", {"priority": "high", "kind": "reminder"})]
    assert results == [("announce_result", {"id": "r:rem_1:ab#a1", "delivered": True})]


@pytest.mark.asyncio
async def test_announce_without_a_participant_reports_no_participant(monkeypatch):
    from services.utils import jarvis_announcer
    from services.utils.ui_command_registry import reset_dedupe

    reset_dedupe()
    results = []
    monkeypatch.setattr(jarvis_announcer, "announce", AsyncMock(side_effect=AssertionError("spoken to nobody")))
    monkeypatch.setattr(agent_loops, "notify_event", AsyncMock(side_effect=lambda t, p: results.append((t, p))))
    await agent_loops.dispatch("announce", {"id": "t:7:aa#a1", "text": "x", "kind": "task"}, _announce_ctx(None))
    await agent_loops.dispatch("announce", {"id": "t:7:aa#a2", "text": "x"}, agent_loops.CommandContext())
    await agent_loops.dispatch("announce", {"id": "", "text": "x"}, _announce_ctx("owner-1"))
    await agent_loops.dispatch("announce", {"id": "t:7:aa#a3", "text": "  "}, _announce_ctx("owner-1"))
    assert results == [
        ("announce_result", {"id": "t:7:aa#a1", "delivered": False, "reason": "no_participant"}),
        ("announce_result", {"id": "t:7:aa#a2", "delivered": False, "reason": "no_participant"}),
        ("announce_result", {"id": "t:7:aa#a3", "delivered": False, "reason": "bad_text"}),
    ]


def test_announce_results_are_retried_by_id():
    assert "announce_result" in jarvis_bridge.CRITICAL_TYPES
    assert jarvis_bridge._idempotent("announce_result", {"id": "r:1#a1"})
    assert not jarvis_bridge._idempotent("announce_result", {})
