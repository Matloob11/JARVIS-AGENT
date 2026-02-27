import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import socket
import json
from agent_runner import notify_ui, perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources


@pytest.fixture
def mock_runner_deps():
    with patch("jarvis_logger.setup_logger"):
        with patch("jarvis_diagnostics.diagnostics.run_full_diagnostics", new_callable=AsyncMock) as mock_diag:
            yield mock_diag


def test_notify_ui():
    with patch("socket.socket") as mock_socket:
        mock_sock_inst = mock_socket.return_value
        notify_ui("START")

        # Verify JSON message and address
        args, _ = mock_sock_inst.sendto.call_args
        sent_msg = args[0].decode()
        address = args[1]

        assert json.loads(sent_msg) == {"status": "START"}
        assert address == ("127.0.0.1", 5005)


@pytest.mark.asyncio
async def test_perform_startup_diagnostics(mock_runner_deps):
    mock_diag = mock_runner_deps
    mock_diag.return_value = {"summary": "All Ok", "health_score": "100%"}

    await perform_startup_diagnostics()
    mock_diag.assert_called_once()


@pytest.mark.asyncio
async def test_start_background_tasks(mock_runner_deps):
    session = MagicMock()
    assistant = MagicMock()

    with patch("asyncio.create_task") as mock_create:
        with patch("jarvis_clipboard.ClipboardMonitor"):
            tasks = await _start_background_tasks(session, assistant)
            # 4 tasks initially + 1 for clipboard monitor
            assert len(tasks) == 5
            assert mock_create.call_count == 5


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

    with patch("agent_memory.MemoryExtractor.run", new_callable=AsyncMock) as mock_run:
        # We need to stop the loop after one iteration
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
            from agent_runner import start_memory_loop
            try:
                await start_memory_loop(session)
            except asyncio.CancelledError:
                pass
            mock_run.assert_called()


@pytest.mark.asyncio
async def test_on_clipboard_detected_logic():
    from agent_runner import _start_background_tasks
    session = MagicMock()
    assistant = MagicMock()

    with patch("asyncio.create_task"):
        with patch("jarvis_clipboard.ClipboardMonitor.start") as mock_start:
            await _start_background_tasks(session, assistant)
            # The callback is the first argument to start()
            callback = mock_start.call_args[0][0]

            # Trigger callback
            await callback("solution text")
            session.history.append.assert_called()
            session.inference.assert_called()
