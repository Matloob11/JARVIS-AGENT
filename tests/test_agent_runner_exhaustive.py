import pytest
import asyncio
import socket
import json
from unittest.mock import MagicMock, patch, AsyncMock
from agent_runner import notify_ui, start_memory_loop, perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources, entrypoint


@pytest.mark.asyncio
async def test_notify_ui_success():
    with patch("socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        notify_ui("START")
        mock_sock.sendto.assert_called()


@pytest.mark.asyncio
async def test_notify_ui_failure():
    with patch("socket.socket", side_effect=socket.error("Fail")):
        notify_ui("STOP")


@pytest.mark.asyncio
async def test_start_memory_loop_exception_handling():
    session = MagicMock()
    item_user = MagicMock()
    item_user.role = "user"
    item_user.content = "jarvis help"

    item_assistant = MagicMock()
    item_assistant.role = "assistant"
    item_assistant.content = [{"text": "hello"}]

    session.history.items = [item_user, item_assistant]

    with patch("agent_runner.MemoryExtractor") as mock_ext_cls:
        mock_ext = mock_ext_cls.return_value
        mock_ext.run = AsyncMock()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            try:
                await start_memory_loop(session)
            except asyncio.CancelledError:
                pass
            mock_ext.run.assert_called()


@pytest.mark.asyncio
async def test_perform_startup_diagnostics_error():
    with patch("jarvis_diagnostics.diagnostics.run_full_diagnostics", side_effect=RuntimeError("Fail")):
        await perform_startup_diagnostics()


@pytest.mark.asyncio
async def test_cleanup_session_resources_robust():
    async def dummy():
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(dummy())
    tasks = [task]

    session = AsyncMock()
    session.stop.side_effect = RuntimeError("Stop fail")

    await _cleanup_session_resources(session, tasks)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_entrypoint_retry_logic():
    ctx = MagicMock()
    from livekit import rtc
    ctx.room.connection_state = rtc.ConnectionState.CONN_DISCONNECTED
    ctx.connect = AsyncMock()
    ctx.room.sid = "test_sid"

    # Mocking dependencies that come BEFORE ctx.connect logic
    with patch("agent_runner.get_formatted_datetime", AsyncMock(return_value={"formatted": "now"})), \
            patch("agent_runner.get_current_city", AsyncMock(return_value="NY")), \
            patch("agent_runner.AgentSession") as mock_session_cls, \
            patch("agent_runner.perform_startup_diagnostics", AsyncMock(return_value=None)), \
            patch("agent_runner.llm.ChatContext") as mock_chat_ctx_cls, \
            patch("agent_runner.BrainAssistant") as mock_assistant_cls, \
            patch("agent_runner._start_background_tasks", AsyncMock(return_value=[])), \
            patch("agent_runner._print_startup_banner"), \
            patch("asyncio.sleep", AsyncMock()), \
            patch("asyncio.Event", return_value=AsyncMock(wait=AsyncMock(side_effect=asyncio.CancelledError()))):

        mock_session = AsyncMock()
        # session.on is used as a decorator, it should return a function that returns the original function
        mock_session.on = MagicMock(return_value=lambda x: x)
        mock_session.history.items = []
        mock_session.start = AsyncMock()
        mock_session_cls.return_value = mock_session

        mock_assistant = MagicMock()
        mock_assistant_cls.return_value = mock_assistant

        try:
            await entrypoint(ctx)
        except asyncio.CancelledError:
            pass
        ctx.connect.assert_called()
