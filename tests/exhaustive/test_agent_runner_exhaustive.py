import pytest
import asyncio
import socket
from unittest.mock import MagicMock, patch, AsyncMock
from services.utils.jarvis_bridge import notify_ui
from src.core.agent_runner import start_memory_loop, perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources, entrypoint


@pytest.fixture
def signed_link(monkeypatch):
    """C-138: agent link v2 with throwaway keys, so these tests never depend on the real .env secrets."""
    from services.utils import jarvis_bridge
    monkeypatch.setattr(jarvis_bridge.config, "agent_token", "c138-test-agent-token")
    monkeypatch.setattr(jarvis_bridge.config, "bridge_hmac_secret", "c138-test-bridge-hmac-secret")
    monkeypatch.setattr(jarvis_bridge.config, "env", "development")
    monkeypatch.setattr(jarvis_bridge, "_link_logged", set())


@pytest.mark.asyncio
async def test_notify_ui_success(signed_link):
    with patch("services.utils.jarvis_bridge.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await notify_ui("START")
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_notify_ui_failure(signed_link):
    with patch("services.utils.jarvis_bridge.httpx.AsyncClient.post", side_effect=socket.error("Fail")) as mock_post:
        await notify_ui("STOP")  # a failed POST is swallowed, never raised
        mock_post.assert_called_once()


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
    # C-138: patches the instance agent_runner really calls (the old target was a duplicate module, so the
    # test passed without reaching the error branch); a failing check is logged and never raised.
    with patch("src.core.agent_runner.diagnostics_instance.run_all", new_callable=AsyncMock,
               side_effect=RuntimeError("Fail")) as mock_run_all, \
            patch("src.core.agent_runner.logger") as mock_logger:
        await perform_startup_diagnostics()
    mock_run_all.assert_awaited_once_with()
    mock_logger.error.assert_called_once()
    template, error = mock_logger.error.call_args.args
    assert template == "Startup diagnostics failed: %s"
    assert isinstance(error, RuntimeError) and str(error) == "Fail"


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
