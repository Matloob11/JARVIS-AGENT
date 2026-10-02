import asyncio
import importlib
import socket
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import socketio
import uvicorn

from services.ai_core import agent_loops
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
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
    monkeypatch.setattr(agent_loops, "RECONNECT_FIRST_S", 0.1)
    monkeypatch.setattr(agent_loops, "RECONNECT_MAX_S", 0.5)
    monkeypatch.setattr(jarvis_bridge, "NOTIFY_TIMEOUT_S", 1.0)
    monkeypatch.setattr(jarvis_bridge, "RETRY_FIRST_S", 30.0)  # only a wake (the socket coming back) flushes
    monkeypatch.setattr(jarvis_bridge, "HTTP_CLIENT", None)  # a client of this event loop
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
    if jarvis_bridge.HTTP_CLIENT is not None:
        await jarvis_bridge.HTTP_CLIENT.aclose()
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
