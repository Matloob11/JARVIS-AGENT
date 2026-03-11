import pytest
import asyncio
import socket
import json
from unittest.mock import MagicMock, patch, AsyncMock
import agent_loops


@pytest.mark.asyncio
async def test_start_memory_storage_loop():
    assistant = MagicMock()
    assistant.memory_extractor.memory.save_to_disk = AsyncMock()

    # Run one iteration and then cancel
    with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
        try:
            await agent_loops.start_memory_storage_loop(assistant)
        except asyncio.CancelledError:
            pass
        assistant.memory_extractor.memory.save_to_disk.assert_called_once()


@pytest.mark.asyncio
async def test_start_ui_command_listener():
    assistant = MagicMock()
    assistant._muted = False

    # Mock socket
    mock_sock = MagicMock()
    mock_sock.recvfrom.side_effect = [
        (json.dumps({"command": "MUTE"}).encode(), ("127.0.0.1", 1234)),
        (json.dumps({"command": "UNMUTE"}).encode(), ("127.0.0.1", 1234)),
        asyncio.CancelledError
    ]

    with patch("socket.socket", return_value=mock_sock):
        # We need to mock the loop behavior
        with patch("asyncio.to_thread", side_effect=[
            (json.dumps({"command": "MUTE"}).encode(), None),
            (json.dumps({"command": "UNMUTE"}).encode(), None),
            asyncio.CancelledError
        ]):
            try:
                await agent_loops.start_ui_command_listener(assistant)
            except asyncio.CancelledError:
                pass
            assert assistant._muted is False  # Final state after UNMUTE
