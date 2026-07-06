import pytest
import asyncio
import socket
from unittest.mock import MagicMock, patch, AsyncMock
from services.utils.jarvis_bridge import notify_ui
from src.core.agent_runner import start_memory_loop, perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources, entrypoint


@pytest.mark.asyncio
async def test_notify_ui_success():
    with patch("services.utils.jarvis_bridge.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await notify_ui("START")
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_notify_ui_failure():
    with patch("services.utils.jarvis_bridge.httpx.AsyncClient.post", side_effect=socket.error("Fail")):
        await notify_ui("STOP")


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

    with patch("src.core.agent_runner.MemoryExtractor") as mock_ext_cls:
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
    ctx.connect = AsyncMock(side_effect=[TimeoutError("handshake timeout"), asyncio.CancelledError()])

    with patch("asyncio.sleep", AsyncMock()):
        await entrypoint(ctx)

    assert ctx.connect.await_count == 2
