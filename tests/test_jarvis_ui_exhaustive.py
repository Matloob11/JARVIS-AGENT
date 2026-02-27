import pytest
import pygame
import threading
import socket
import json
import os
from unittest.mock import MagicMock, patch, mock_open
from jarvis_ui import JarvisUI

@pytest.fixture
def mock_pygame():
    real_surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    with patch("pygame.display.set_mode", return_value=real_surface), \
         patch("pygame.display.set_caption"), \
         patch("pygame.font.SysFont") as mock_font, \
         patch("pygame.image.load", return_value=real_surface), \
         patch("pygame.transform.smoothscale", return_value=real_surface), \
         patch("pygame.Surface", return_value=real_surface), \
         patch("pygame.init"), \
         patch("pygame.event.get", return_value=[]), \
         patch("pygame.time.Clock"):
        
        mock_font_obj = MagicMock()
        mock_font_obj.render.return_value = real_surface
        mock_font.return_value = mock_font_obj
        yield

@pytest.fixture
def mock_audio():
    with patch("pyaudio.PyAudio") as mock_pyaudio:
        mock_instance = mock_pyaudio.return_value
        mock_stream = MagicMock()
        mock_instance.open.return_value = mock_stream
        mock_stream.read.return_value = b'\x00' * 1024
        yield mock_instance

def test_jarvis_ui_init(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector"):
        ui = JarvisUI()
        assert ui.screen_width == 1280
        assert ui.screen_height == 720
        assert ui.muted is False

def test_jarvis_ui_udp_listener(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector"):
        ui = JarvisUI()
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = mock_sock_cls.return_value
            mock_sock.bind.return_value = None
            
            def mock_recv(size):
                mock_sock.recvfrom.side_effect = socket.error("Break loop")
                return (json.dumps({"status": "START"}).encode(), ("127.0.0.1", 5005))
            
            mock_sock.recvfrom.side_effect = mock_recv
            
            try:
                ui.udp_listener()
            except socket.error:
                pass
            assert ui.anim['is_speaking'] is True

def test_jarvis_ui_get_volume(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector"):
        ui = JarvisUI()
        ui.audio['available'] = True
        ui.audio['stream'] = mock_audio.open.return_value
        vol = ui.get_volume()
        assert isinstance(vol, float)

def test_jarvis_ui_handle_clicks(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector"):
        ui = JarvisUI()
        # Mute button is at screen_width - 160, 30 with size 130, 40
        ui.screen_width = 1200 # Fixed width for test
        # rect: (1200-160, 30, 130, 40) -> (1040, 30, 130, 40)
        with patch.object(ui, "send_agent_command") as mock_send:
            ui.handle_clicks((1100, 50))
            assert ui.muted is True
            mock_send.assert_called_with("MUTE")

def test_jarvis_ui_render_cycle(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector"):
        ui = JarvisUI()
        real_surf = pygame.Surface((1,1))
        ui.anim['ui_frames'] = [real_surf]
        
        mock_font_obj = MagicMock()
        mock_font_obj.render.return_value = real_surf
        ui.fonts = {
            'clock': mock_font_obj,
            'date': mock_font_obj,
            'metrics': mock_font_obj,
            'track': mock_font_obj,
            'button': mock_font_obj
        }
        
        with patch("pygame.display.flip"):
            ui.render()

def test_jarvis_ui_cleanup(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector") as mock_coll_cls:
        ui = JarvisUI()
        ui.audio['stream'] = MagicMock()
        ui.audio['p_audio'] = MagicMock()
        ui.cleanup()
        assert ui.audio['stream'].stop_stream.called
        assert ui.audio['p_audio'].terminate.called

def test_jarvis_ui_run_break(mock_pygame, mock_audio):
    with patch("threading.Thread"), \
         patch("jarvis_ui.MetricsCollector"):
        ui = JarvisUI()
        with patch.object(ui, "handle_events"), \
             patch.object(ui, "render"), \
             patch.object(ui, "clock") as mock_clock:
            ui.handle_events.side_effect = Exception("Break loop")
            try:
                ui.run()
            except Exception:
                pass
