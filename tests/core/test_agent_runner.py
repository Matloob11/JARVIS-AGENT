import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import socket
import json
from agent_runner import notify_ui, perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources


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
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["payload"] == "START"


@pytest.mark.asyncio
async def test_perform_startup_diagnostics(mock_runner_deps):
    mock_diag = mock_runner_deps
    mock_diag.return_value = ["Health check ok"]

    await perform_startup_diagnostics()
    mock_diag.assert_called_once()


@pytest.mark.asyncio
async def test_start_background_tasks(mock_runner_deps):
    session = MagicMock()
    assistant = MagicMock()
    assistant.memory_extractor = MagicMock()

    with patch("asyncio.create_task") as mock_create:
        with patch("services.automation.jarvis_clipboard.ClipboardMonitor"):
            tasks = await _start_background_tasks(session, assistant)
            # 9 tasks in main list + 1 for clipboard monitor = 10
            assert len(tasks) == 10
            assert mock_create.call_count == 10


@pytest.mark.asyncio
async def test_cleanup_session_resources(mock_runner_deps):
    session = AsyncMock()

    # Create a real but simple task
    async def simple_task():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(simple_task())

    with patch("asyncio.wait_for", new_callable=AsyncMock):
        await _cleanup_session_resources(session, [task])
        # In Python 3.11, task.cancelling() returns True if cancel was called
        assert task.cancelling() or task.cancelled()
        session.stop.assert_called_once()

    # Final cleanup of the task to avoid warnings
    await asyncio.gather(task, return_exceptions=True)


def test_print_startup_banner():
    with patch("builtins.print") as mock_print:
        from agent_runner import _print_startup_banner
        _print_startup_banner()
        assert mock_print.call_count >= 5


@pytest.mark.asyncio
async def test_start_memory_loop(mock_runner_deps):
    session = MagicMock()
    session.history.items = [
        MagicMock(role="user", content="hello jarvis"),
        MagicMock(role="assistant", content="hi there")
    ]

    with patch("services.ai_core.agent_memory.MemoryExtractor.run", new_callable=AsyncMock) as mock_run:
        # We need to stop the loop after one iteration
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
            from agent_runner import start_memory_loop
            extractor = MagicMock()
            try:
                await start_memory_loop(session, extractor)
            except asyncio.CancelledError:
                pass
            extractor.run.assert_called()


@pytest.mark.asyncio
async def test_on_clipboard_detected_logic():
    from agent_runner import _start_background_tasks
    session = MagicMock()
    assistant = MagicMock()
    assistant.chat_ctx.messages = []

    with patch("asyncio.create_task"):
        with patch("services.automation.jarvis_clipboard.ClipboardMonitor.start") as mock_start:
            await _start_background_tasks(session, assistant)
            # The callback is the first positional argument to start()
            assert mock_start.called
            callback = mock_start.call_args[0][0]

            # Trigger callback
            await callback("solution text")
            assert len(assistant.chat_ctx.messages) > 0
            assert "CLIPBOARD ERROR DETECTED" in assistant.chat_ctx.messages[0].content[0]
