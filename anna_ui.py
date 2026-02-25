"""
Standalone Anna UI with Warm Aesthetics and Glassmorphism
"""
import os
import platform
import struct
import threading
import datetime
import math
import socket
import json
import pygame
import pyaudio
import psutil
from PIL import Image

# Setup directories
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Anna Theme Colors (Rose Gold / Soft Gold / Warm White)
ROSE_GOLD = (183, 110, 121)
SOFT_GOLD = (212, 175, 55)
WARM_WHITE = (245, 245, 220)
DEEP_MAROON = (40, 10, 20)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GOLD_GLOW = (212, 175, 55, 40)
TRANSPARENT_BG = (0, 0, 0, 0)


class AnnaUI:
    """
    Standalone Anna UI with elegant aesthetics and audio reactivity.
    """

    def __init__(self):
        pygame.init()
        self.screen_width, self.screen_height = 1280, 720
        self.screen = pygame.display.set_mode(
            (self.screen_width, self.screen_height), pygame.RESIZABLE)
        pygame.display.set_caption("A.N.N.A - Core Interface")
        self.clock = pygame.time.Clock()
        self.running = True
        self.fullscreen = False

        # Metrics and Status
        self.metrics = {
            'cpu': 0,
            'ram': 0,
            'data_stream': ["ANNA_CORE_LOADED", "EMBODIMENT_READY", "AESTHETIC_SYNC_OK", "VIBE_CHECK_PASS"],
            'stream_timer': 0
        }

        # Animation Group
        self.anim = {
            'frame_idx': 0,
            'angle': 0,
            'is_speaking': False,
            'pulse_phase': 0.0,
            'ui_frames': []
        }

        # Audio setup
        self.audio = {
            'available': False,
            'stream': None,
            'p_audio': None
        }

        # IPC and Control
        self.udp_port = 5005
        self.agent_cmd_port = 5006
        self.muted = False
        self.stop_event = threading.Event()

        self.init_audio()
        threading.Thread(target=self.udp_listener, daemon=True).start()

        # Load Resources
        self.fonts = {}
        self.load_fonts()
        self.load_assets()

        # Start metric update thread
        threading.Thread(target=self.update_metrics_loop, daemon=True).start()

    def udp_listener(self):
        """Listens for speech status from the agent."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", self.udp_port))
            print(f"ANNA UI: Listening on port {self.udp_port}")
        except socket.error:
            print(
                "ANNA UI: Port already in use, assuming another instance or JARVIS UI is running.")
            return

        while not self.stop_event.is_set():
            try:
                data, _ = sock.recvfrom(1024)
                message = json.loads(data.decode())
                if message.get("status") == "START":
                    self.anim['is_speaking'] = True
                elif message.get("status") == "STOP":
                    self.anim['is_speaking'] = False
            except (json.JSONDecodeError, socket.error):
                pass

    def load_fonts(self):
        """Loads elegant typography."""
        try:
            fonts = ["Gabriola", "Palatino", "Georgia", "Arial"]
            chosen = None
            for f in fonts:
                try:
                    pygame.font.SysFont(f, 10)
                    chosen = f
                    break
                except:
                    continue

            self.fonts = {
                'clock': pygame.font.SysFont(chosen, 95),
                'date': pygame.font.SysFont(chosen, 32),
                'metrics': pygame.font.SysFont(chosen, 22),
                'button': pygame.font.SysFont(chosen, 20, bold=True)
            }
        except:
            fallback = pygame.font.SysFont(None, 24)
            self.fonts = {'clock': fallback, 'date': fallback,
                          'metrics': fallback, 'button': fallback}

    def load_assets(self):
        """Loads the Anna UI gif."""
        gif_path = os.path.join(SCRIPT_DIR, 'ui_gifs', 'anna_ui.gif')
        if os.path.exists(gif_path):
            self.anim['ui_frames'] = self.load_gif_safe(gif_path, (1280, 720))
        else:
            print(f"Error: {gif_path} not found.")
            self.anim['ui_frames'] = self.create_fallback_frames((1280, 720))

    def load_gif_safe(self, path, target_size):
        img = Image.open(path)
        frames = []
        for i in range(getattr(img, 'n_frames', 1)):
            img.seek(i)
            frame = img.copy().convert("RGBA").resize(
                target_size, Image.Resampling.LANCZOS)
            pygame_surface = pygame.image.frombuffer(
                frame.tobytes(), frame.size, "RGBA")
            frames.append(pygame_surface)
        return frames

    def create_fallback_frames(self, size):
        frames = []
        for i in range(10):
            surf = pygame.Surface(size, pygame.SRCALPHA)
            pygame.draw.circle(
                surf, ROSE_GOLD, (size[0]//2, size[1]//2), 150 + int(10 * math.sin(i * 0.6)), 1)
            frames.append(surf)
        return frames

    def init_audio(self):
        try:
            self.audio['p_audio'] = pyaudio.PyAudio()
            self.audio['stream'] = self.audio['p_audio'].open(
                format=pyaudio.paInt16, channels=1, rate=44100,
                input=True, frames_per_buffer=1024)
            self.audio['available'] = True
        except:
            self.audio['available'] = False

    def get_volume(self):
        if not self.audio['available'] or not self.audio['stream']:
            return 0
        try:
            data = self.audio['stream'].read(1024, exception_on_overflow=False)
            count = len(data) // 2
            shorts = struct.unpack(f"{count}h", data)
            sum_squares = sum(s**2 for s in shorts)
            return (sum_squares / count)**0.5
        except:
            return 0

    def update_metrics_loop(self):
        while not self.stop_event.is_set():
            self.metrics['cpu'] = psutil.cpu_percent(interval=1)
            self.metrics['ram'] = psutil.virtual_memory().percent

    def draw_glass_panel(self, rect, color=(255, 255, 255, 30), border_color=WHITE):
        """Draws a semi-transparent 'glass' panel with a light border."""
        shape_surf = pygame.Surface(pygame.Rect(rect).size, pygame.SRCALPHA)
        pygame.draw.rect(shape_surf, color,
                         shape_surf.get_rect(), border_radius=10)
        self.screen.blit(shape_surf, rect)
        pygame.draw.rect(self.screen, border_color, rect, 1, border_radius=10)

    def render(self):
        self.screen.fill(DEEP_MAROON)

        # 1. Background GIF
        vol = self.get_volume()
        pulse = 1.0
        if self.anim['is_speaking']:
            self.anim['pulse_phase'] += 0.15
            pulse = 1.0 + 0.03 * \
                math.sin(self.anim['pulse_phase']) + (vol / 20000)

        if self.anim['ui_frames']:
            current_frame = self.anim['ui_frames'][self.anim['frame_idx']]
            sw, sh = self.screen.get_size()
            fw, fh = current_frame.get_size()
            scale = max(sw/fw, sh/fh) * pulse
            scaled_frame = pygame.transform.smoothscale(
                current_frame, (int(fw * scale), int(fh * scale)))
            rect = scaled_frame.get_rect(center=(sw//2, sh//2))
            self.screen.blit(scaled_frame, rect)

        # 2. Transparent Top Header (Clock/Date)
        sw, sh = self.screen.get_size()
        now = datetime.datetime.now()
        time_str = now.strftime("%I:%M:%S %p")
        date_str = now.strftime("%A, %B %d, %Y")

        time_surf = self.fonts['clock'].render(time_str, True, WHITE)
        time_rect = time_surf.get_rect(center=(sw // 2, 80))
        # Subtle glow for time
        glow_surf = self.fonts['clock'].render(time_str, True, SOFT_GOLD)
        glow_surf.set_alpha(100)
        self.screen.blit(glow_surf, (time_rect.x+2, time_rect.y+2))
        self.screen.blit(time_surf, time_rect)

        date_surf = self.fonts['date'].render(date_str, True, ROSE_GOLD)
        date_rect = date_surf.get_rect(center=(sw // 2, 140))
        self.screen.blit(date_surf, date_rect)

        # 3. Glassmorphic Metrics (Bottom Left)
        panel_rect = pygame.Rect(30, sh - 130, 220, 100)
        self.draw_glass_panel(
            panel_rect, (255, 182, 193, 40), (255, 255, 255, 150))

        cpu_text = self.fonts['metrics'].render(
            f"Core Load: {self.metrics['cpu']}%", True, WARM_WHITE)
        ram_text = self.fonts['metrics'].render(
            f"Essence: {self.metrics['ram']}%", True, WARM_WHITE)
        self.screen.blit(cpu_text, (panel_rect.x + 15, panel_rect.y + 20))
        self.screen.blit(ram_text, (panel_rect.x + 15, panel_rect.y + 55))

        # 4. Mute Button (Bottom Right)
        btn_x, btn_y = sw - 180, sh - 70
        btn_w, btn_h = 150, 40
        btn_color = (200, 50, 50, 180) if self.muted else (212, 175, 55, 180)
        self.draw_glass_panel((btn_x, btn_y, btn_w, btn_h), btn_color, WHITE)
        label = "SILENCE" if self.muted else "MICROPHONE"
        txt = self.fonts['button'].render(label, True, WHITE)
        txt_rect = txt.get_rect(center=(btn_x + btn_w//2, btn_y + btn_h//2))
        self.screen.blit(txt, txt_rect)

        # 5. Visualizer (Center Bottom)
        if self.anim['is_speaking']:
            for i in range(15):
                h = 10 + (vol / 500) * \
                    math.sin(self.anim['pulse_phase'] + i * 0.5)
                bar_rect = pygame.Rect(
                    sw//2 - 75 + i*10, sh-100, 5, -abs(h))
                pygame.draw.rect(self.screen, SOFT_GOLD,
                                 bar_rect, border_radius=2)

        # Update and flip
        self.anim['frame_idx'] = (
            self.anim['frame_idx'] + 1) % len(self.anim['ui_frames'])
        pygame.display.flip()

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    sw, sh = self.screen.get_size()
                    btn_rect = pygame.Rect(
                        sw - 180, sh - 70, 150, 40)
                    if btn_rect.collidepoint(event.pos):
                        self.muted = not self.muted
                        cmd = "MUTE" if self.muted else "UNMUTE"
                        self.send_agent_command(cmd)

    def send_agent_command(self, command: str):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            message = json.dumps({"command": command})
            sock.sendto(message.encode(), ("127.0.0.1", self.agent_cmd_port))
        except:
            pass

    def run(self):
        while self.running:
            self.handle_events()
            self.render()
            self.clock.tick(30)
        pygame.quit()


if __name__ == "__main__":
    app = AnnaUI()
    app.run()
