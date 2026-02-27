import pytest
import pygame
import threading
import socket
import json
import os
import math
from unittest.mock import MagicMock, patch, mock_open
from anna_ui import AnnaUI

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
         patch("pygame.time.Clock") as mock_clock_cls:
        
        mock_font_obj = MagicMock()
        # Ensure render returns a Surface
        mock_font_obj.render.return_value = real_surface
        mock_font.return_value = mock_font_obj
        
        mock_clock = mock_clock_cls.return_value
        mock_clock.tick.return_value = 30
        
        yield real_surface

@pytest.fixture
def mock_audio():
    with patch("pyaudio.PyAudio") as mock_pyaudio:
        mock_instance = mock_pyaudio.return_value
        mock_stream = MagicMock()
        mock_instance.open.return_value = mock_stream
        # Return some bytes for read
        mock_stream.read.return_value = b'\x00' * 1024
        yield mock_instance

def test_anna_ui_init(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        assert ui.screen_width == 1280
        assert ui.screen_height == 720

def test_anna_ui_udp_listener(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = mock_sock_cls.return_value
            mock_sock.bind.return_value = None
            
            # Using a side effect that eventually stops
            def mock_recv(size):
                # First call returns data
                mock_sock.recvfrom.side_effect = socket.error("Break loop")
                return (json.dumps({"status": "START"}).encode(), ("127.0.0.1", 5005))
            
            mock_sock.recvfrom.side_effect = mock_recv
            
            try:
                ui.udp_listener()
            except socket.error:
                pass
            assert ui.anim['is_speaking'] is True

def test_anna_ui_load_gif_safe(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        with patch("PIL.Image.open") as mock_img_open:
            mock_img = MagicMock()
            mock_img.size = (100, 100)
            mock_img.is_animated = True
            mock_img.n_frames = 2
            mock_img_open.return_value = mock_img
            
            with patch("PIL.Image.Image.convert"), \
                 patch("pygame.image.frombuffer", return_value=pygame.Surface((100,100))):
                frames = ui.load_gif_safe("dummy.gif", (100, 100))
                assert len(frames) == 2

def test_anna_ui_get_volume(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        ui.audio['available'] = True
        ui.audio['stream'] = mock_audio.open.return_value
        vol = ui.get_volume()
        assert isinstance(vol, float)

def test_anna_ui_render_cycle(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        real_surf = pygame.Surface((10,10))
        ui.anim['ui_frames'] = [real_surf]
        
        mock_font_obj = MagicMock()
        mock_font_obj.render.return_value = real_surf
        ui.fonts = {
            'clock': mock_font_obj,
            'date': mock_font_obj,
            'metrics': mock_font_obj,
            'button': mock_font_obj
        }
        
        with patch("pygame.display.flip"):
            ui.render()

def test_anna_ui_handle_events(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        with patch("pygame.event.get") as mock_get:
            mock_event = MagicMock()
            mock_event.type = pygame.MOUSEBUTTONDOWN
            mock_event.button = 1
            mock_event.pos = (ui.screen_width - 100, ui.screen_height - 50)
            mock_get.return_value = [mock_event]
            
            with patch.object(ui, "send_agent_command") as mock_cmd:
                ui.handle_events()
                assert ui.muted is True
                mock_cmd.assert_called_with("MUTE")

def test_anna_ui_run_break(mock_pygame, mock_audio):
    with patch("threading.Thread"):
        ui = AnnaUI()
        with patch.object(ui, "handle_events"), \
             patch.object(ui, "render"), \
             patch.object(ui, "clock") as mock_clock:
            ui.handle_events.side_effect = Exception("Break loop")
            try:
                ui.run()
            except Exception:
                pass
