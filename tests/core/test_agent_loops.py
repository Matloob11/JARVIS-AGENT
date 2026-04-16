import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from services.ai_core.agent_loops import (
    start_memory_storage_loop,
    start_reminder_loop,
    start_bug_hunter_loop,
    start_ui_command_listener
)

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
    with patch("asyncio.to_thread", side_effect=[mock_due, asyncio.CancelledError()]):
        with patch("asyncio.sleep"): # To skip the sleep(30)
            try:
                await start_reminder_loop(mock_session)
            except asyncio.CancelledError:
                pass

    mock_session.say.assert_called()

@pytest.mark.asyncio
async def test_start_bug_hunter_loop():
    mock_session = MagicMock()
    mock_session.say = AsyncMock()

    # Mock monitor_logs to call the callback once and then escape loop
    async def mock_monitor(callback):
        await callback("Test Error")
        raise asyncio.CancelledError()

    with patch("services.ai_core.agent_loops.monitor_logs", side_effect=mock_monitor):
        try:
            await start_bug_hunter_loop(mock_session)
        except asyncio.CancelledError:
            pass

    mock_session.say.assert_called()

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
