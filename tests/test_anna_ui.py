import pytest
import pygame
import struct
from unittest.mock import MagicMock, patch, AsyncMock
from anna_ui import AnnaUI

@pytest.fixture
def m_pygame():
    with patch("pygame.init"):
        with patch("pygame.display.set_mode") as mock_set_mode:
            mock_set_mode.return_value = MagicMock()
            with patch("pygame.display.set_caption"):
                with patch("pygame.time.Clock") as mock_clock:
                    mock_clock.return_value.tick.return_value = 30
                    with patch("pygame.font.SysFont") as mock_font:
                        mock_font_inst = MagicMock()
                        mock_font.return_value = mock_font_inst
                        with patch("pygame.image.frombuffer"):
                            with patch("pygame.transform.smoothscale") as mock_scale:
                                mock_scale.return_value = MagicMock()
                                with patch("pygame.display.flip"):
                                    with patch("pygame.Surface") as mock_surf:
                                        mock_surf.return_value = MagicMock()
                                        with patch("pygame.draw"):
                                            yield

@pytest.fixture
def m_audio():
    with patch("pyaudio.PyAudio"):
        yield

@pytest.fixture
def m_socket():
    with patch("socket.socket") as mock_sock:
        mock_sock_inst = MagicMock()
        mock_sock.return_value = mock_sock_inst
        yield mock_sock_inst

@pytest.fixture
def m_threading():
    with patch("threading.Thread") as mock_thread:
        yield mock_thread

def test_anna_ui_init(m_pygame, m_audio, m_socket, m_threading):
    with patch("anna_ui.AnnaUI.load_assets"):
        ui = AnnaUI()
        assert ui.running is True
        assert ui.anim['is_speaking'] is False

def test_anna_ui_render(m_pygame, m_audio, m_socket, m_threading):
    with patch("anna_ui.AnnaUI.load_assets"):
        ui = AnnaUI()
        ui.screen = MagicMock()
        ui.screen.get_size.return_value = (1280, 720)
        mock_frame = MagicMock()
        mock_frame.get_size.return_value = (1280, 720)
        ui.anim['ui_frames'] = [mock_frame]
        
        ui.audio['available'] = True
        ui.audio['stream'] = MagicMock()
        ui.audio['stream'].read.return_value = b'\x00' * 2048
        
        ui.render()
        pygame.display.flip.assert_called()

def test_handle_events(m_pygame, m_audio, m_socket, m_threading):
    with patch("anna_ui.AnnaUI.load_assets"):
        ui = AnnaUI()
        ui.screen = MagicMock()
        ui.screen.get_size.return_value = (1280, 720)
        
        # Mock a mouse click on the mute button
        mock_event = MagicMock()
        mock_event.type = pygame.MOUSEBUTTONDOWN
        mock_event.button = 1
        # Button is at sw - 180, sh - 70. sw=1280, sh=720 -> 1100, 650
        mock_event.pos = (1180, 670) 
        
        with patch("pygame.event.get", return_value=[mock_event]):
            with patch("anna_ui.AnnaUI.send_agent_command") as mock_cmd:
                ui.handle_events()
                assert ui.muted is True
                mock_cmd.assert_called_with("MUTE")

def test_get_volume(m_pygame, m_audio, m_socket, m_threading):
    with patch("anna_ui.AnnaUI.load_assets"):
        ui = AnnaUI()
        ui.audio['available'] = True
        ui.audio['stream'] = MagicMock()
        
        mock_data = struct.pack("hh", 200, -200)
        ui.audio['stream'].read.return_value = mock_data
        
        vol = ui.get_volume()
        assert vol > 0
