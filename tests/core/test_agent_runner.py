# pylint: disable=redefined-outer-name, unused-variable, import-outside-toplevel
import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from services.utils.jarvis_bridge import notify_ui
from src.core.agent_runner import perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources


@pytest.fixture
def mock_runner_deps():
    with patch("services.utils.jarvis_logger.setup_logger"):
        with patch("services.utils.jarvis_diagnostics.diagnostics.run_all", new_callable=AsyncMock) as mock_diag:
            yield mock_diag


@pytest.mark.asyncio
async def test_notify_ui():
    with patch("services.utils.jarvis_bridge.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await notify_ui("START")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["payload"] == "START"


@pytest.mark.asyncio
async def test_perform_startup_diagnostics(mock_runner_deps):
    mock_diag_internal = mock_runner_deps
    mock_diag_internal.return_value = ["Health check ok"]

    await perform_startup_diagnostics()
    mock_diag_internal.assert_called_once()


@pytest.mark.asyncio
async def test_start_background_tasks(mock_runner_deps):
    session = MagicMock()
    assistant = MagicMock()
    assistant.memory_extractor = MagicMock()

    with patch("src.core.agent_runner.autonomous_protector.run_protected", new_callable=AsyncMock) as mock_run_protected:
        with patch("asyncio.create_task") as mock_create:
            def close_task_coro(coro):
                if hasattr(coro, "close"):
                    coro.close()
                return MagicMock()

            mock_create.side_effect = close_task_coro
            with patch("src.core.agent_runner.ClipboardMonitor"):
                tasks = await _start_background_tasks(session, assistant)
                assert mock_run_protected.call_count == 8
                assert mock_create.call_count == 1


@pytest.mark.asyncio
async def test_cleanup_session_resources(mock_runner_deps):
    # pylint: disable=unused-argument
    session = AsyncMock()

    # Create a real but simple task
    async def simple_task():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(simple_task())

    await _cleanup_session_resources(session, [task])
    # In Python 3.11, task.cancelling() returns True if cancel was called
    assert task.cancelling() or task.cancelled()
    session.stop.assert_called_once()

    # Final cleanup of the task to avoid warnings
    await asyncio.gather(task, return_exceptions=True)


def test_print_startup_banner():
    with patch("builtins.print") as mock_print:
        from src.core.agent_runner import _print_startup_banner
        _print_startup_banner()
        assert mock_print.call_count >= 5


@pytest.mark.asyncio
async def test_start_memory_loop(mock_runner_deps):
    assistant = MagicMock()
    assistant.chat_ctx = MagicMock()
    assistant.chat_ctx.messages = [
        MagicMock(role="user", content="hello jarvis"),
        MagicMock(role="assistant", content="hi there")
    ]

    with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
        from src.core.agent_runner import start_memory_loop
        extractor = AsyncMock()
        try:
            await start_memory_loop(assistant, extractor)
        except asyncio.CancelledError:
            pass
        extractor.run.assert_called()


@pytest.mark.asyncio
async def test_on_clipboard_detected_logic():
    session = MagicMock()
    assistant = MagicMock()
    assistant.chat_ctx.messages = []

    with patch("asyncio.create_task"):
        with patch("src.core.agent_runner.ClipboardMonitor.start") as mock_start:
            await _start_background_tasks(session, assistant)
            # The callback is the first positional argument to start()
            assert mock_start.called
            callback = mock_start.call_args[0][0]

            # Trigger callback
            await callback("solution text")
            assert len(assistant.chat_ctx.messages) > 0
            assert "CLIPBOARD ERROR DETECTED" in assistant.chat_ctx.messages[0].content[0]
