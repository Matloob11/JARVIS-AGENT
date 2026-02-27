import pytest
import asyncio
import json
import socket
from unittest.mock import MagicMock, patch, AsyncMock
from agent_loops import (
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
        await start_memory_storage_loop(mock_assistant)
    
    mock_assistant.memory_extractor.memory.save_to_disk.assert_called()

@pytest.mark.asyncio
async def test_start_reminder_loop():
    mock_session = MagicMock()
    mock_session.say = MagicMock()
    
    # Mock check_due_reminders to return one item
    mock_due = [{"message": "Test Reminder"}]
    with patch("asyncio.to_thread", side_effect=[mock_due, asyncio.CancelledError()]):
        with patch("asyncio.sleep"): # To skip the sleep(30)
            await start_reminder_loop(mock_session)
    
    mock_session.say.assert_called()
    assert "Test Reminder" in mock_session.say.call_args[0][0]

@pytest.mark.asyncio
async def test_start_bug_hunter_loop():
    mock_session = MagicMock()
    mock_session.say = MagicMock()
    
    # Mock monitor_logs to call the callback once
    async def mock_monitor(callback):
        await callback("Test Error")
        
    with patch("agent_loops.monitor_logs", side_effect=mock_monitor):
        await start_bug_hunter_loop(mock_session)
    
    mock_session.say.assert_called()
    assert "Test Error" in mock_session.say.call_args[0][0]

@pytest.mark.asyncio
async def test_start_ui_command_listener_mute():
    mock_assistant = MagicMock()
    mock_assistant._muted = False
    
    mock_sock = MagicMock()
    # Mock receive MUTE command then CancelledError
    mock_data = (json.dumps({"command": "MUTE"}).encode(), ("127.0.0.1", 5006))
    
    with patch("socket.socket", return_value=mock_sock):
        with patch("asyncio.to_thread", side_effect=[mock_data, asyncio.CancelledError()]):
            await start_ui_command_listener(mock_assistant)
            
    assert mock_assistant._muted is True

@pytest.mark.asyncio
async def test_start_ui_command_listener_unmute():
    mock_assistant = MagicMock()
    mock_assistant._muted = True
    
    mock_sock = MagicMock()
    # Mock receive UNMUTE command then CancelledError
    mock_data = (json.dumps({"command": "UNMUTE"}).encode(), ("127.0.0.1", 5006))
    
    with patch("socket.socket", return_value=mock_sock):
        with patch("asyncio.to_thread", side_effect=[mock_data, asyncio.CancelledError()]):
            await start_ui_command_listener(mock_assistant)
            
    assert mock_assistant._muted is False
