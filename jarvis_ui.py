"""
Standalone JARVIS UI with System Metrics and Audio Reactivity
"""
import os
import platform
import struct
import threading
import datetime
import subprocess
import math
import socket
import json
import pygame
import pyaudio
import psutil
from PIL import Image

# Setup directories
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(SCRIPT_DIR, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)
os.environ["TEMP"] = TMP_DIR
os.environ["TMP"] = TMP_DIR

# Global Constants
CYAN = (0, 255, 255)
DARK_CYAN = (0, 150, 150)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
DARK_BLUE = (10, 20, 40)
DARK_GRAY = (30, 30, 30)
GLOW_CYAN = (0, 255, 255, 50)


class JarvisUI:
    """
    Standalone JARVIS UI with System Metrics and Audio Reactivity.
    Manages the graphical interface, animations, and system monitoring.
    """

    def __init__(self):
        """Initialize the JARVIS UI components and start background threads."""
        pygame.init()
        self.screen_width, self.screen_height = 1280, 720
        self.screen = pygame.display.set_mode(
            (self.screen_width, self.screen_height), pygame.RESIZABLE)
        pygame.display.set_caption("J.A.R.V.I.S")
        self.clock = pygame.time.Clock()
        self.running = True
        self.fullscreen = False

        # Metrics and Status Group
        self.metrics = {
            'cpu': 0,
            'ram': 0,
            'track': "",
            'data_stream': ["SYSTEM_INITIALIZED", "LINK_ESTABLISHED",
                            "OS_LOADED_OK", "ENCRYPTION_ACTIVE", "CORE_SYNC_READY"],
            'stream_timer': 0
        }

        # Animation Group
        self.anim = {
            'frame_idx': 0,
            'gif_scale': 1.0,
            'angle': 0,
            'is_speaking': False,
            'pulse_phase': 0.0,
            'ui_frames': []
        }

        # Audio setup Group
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

        # Start metric update threads
        threading.Thread(target=self.update_metrics_loop, daemon=True).start()
        threading.Thread(target=self.update_track_loop, daemon=True).start()

    def udp_listener(self):
        """Listens for speech status from the agent."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", self.udp_port))
        print(f"UDP Link established on port {self.udp_port}")

        while not self.stop_event.is_set():
            try:
                data, _ = sock.recvfrom(1024)
                message = json.loads(data.decode())
                if message.get("status") == "START":
                    self.anim['is_speaking'] = True
                elif message.get("status") == "STOP":
                    self.anim['is_speaking'] = False
            except (json.JSONDecodeError, socket.error) as e:
                print(f"IPC Error: {e}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"Unexpected IPC Error: {e}")

    def load_fonts(self):
        """Loads responsive fonts based on OS."""
        try:
            # Try to find a techy font
            tech_fonts = ["Orbitron", "Impact", "Segoe UI", "Arial"]
            # Find the first available font
            chosen_font = None
            for font_name in tech_fonts:
                try:
                    pygame.font.SysFont(font_name, 10)  # Test if font exists
                    chosen_font = font_name
                    break
                except (pygame.error, ImportError):
                    continue

            if not chosen_font:
                chosen_font = None  # Fallback to default SysFont

            self.fonts = {
                'clock': pygame.font.SysFont(chosen_font, 82, bold=True),
                'date': pygame.font.SysFont(chosen_font, 24, bold=True),
                'metrics': pygame.font.SysFont(chosen_font, 20),
                'track': pygame.font.SysFont(chosen_font, 23, italic=True),
                'button': pygame.font.SysFont(chosen_font, 18, bold=True)
            }
        except (pygame.error, ImportError) as e:
            print(f"Font loading error: {e}")
            fallback = pygame.font.SysFont(None, 24)
            self.fonts = {'clock': fallback, 'date': fallback,
                          'metrics': fallback, 'track': fallback}

    def load_assets(self):
        """Loads the UI gif and other visual assets."""
        gif_path = os.path.join(SCRIPT_DIR, 'ui_gifs', 'jarvis-ui.gif')
        self.anim['ui_frames'] = self.load_gif_safe(gif_path, (1280, 720))

    def load_gif_safe(self, path, target_size):
        """Safely loads and resizes GIF frames."""
        if not os.path.exists(path):
            return self.create_fallback_frames(target_size)
        try:
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
        except (IOError, ValueError, pygame.error) as e:
            print(f"GIF Load Error: {e}")
            return self.create_fallback_frames(target_size)

    def create_fallback_frames(self, size):
        """Standard fallback animation."""
        frames = []
        for i in range(10):
            surf = pygame.Surface(size, pygame.SRCALPHA)
            pygame.draw.circle(
                surf, CYAN, (size[0]//2, size[1]//2), 150 + int(20 * math.sin(i * 0.6)), 2)
            frames.append(surf)
        return frames

    def init_audio(self):
        """Initializes PyAudio stream."""
        try:
            self.audio['p_audio'] = pyaudio.PyAudio()
            self.audio['stream'] = self.audio['p_audio'].open(
                format=pyaudio.paInt16, channels=1, rate=44100,
                input=True, frames_per_buffer=1024)
            self.audio['available'] = True
        except (pyaudio.PyAudioError, socket.error):  # pylint: disable=no-member
            self.audio['available'] = False
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Unexpected audio init error: {e}")
            self.audio['available'] = False

    def get_volume(self):
        """Calculates volume from microphone."""
        if not self.audio['available'] or not self.audio['stream']:
            return 0
        try:
            data = self.audio['stream'].read(1024, exception_on_overflow=False)
            count = len(data) // 2
            shorts = struct.unpack(f"{count}h", data)
            sum_squares = sum(s**2 for s in shorts)
            return (sum_squares / count)**0.5
        except (struct.error, pyaudio.PyAudioError, socket.error):  # pylint: disable=no-member
            return 0
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Unexpected volume error: {e}")
            return 0

    def update_metrics_loop(self):
        """Updates CPU/RAM metrics periodically."""
        while not self.stop_event.is_set():
            self.metrics['cpu'] = psutil.cpu_percent(interval=1)
            self.metrics['ram'] = psutil.virtual_memory().percent

    def update_track_loop(self):
        """Updates currently playing track info."""
        while not self.stop_event.is_set():
            new_track = ""
            try:
                system = platform.system()
                if system == "Windows":
                    # Placeholder for Windows track info.
                    pass
                elif system == "Darwin":
                    # macOS logic from original file
                    running = subprocess.check_output(
                        'ps -ef | grep "MacOS/Spotify" | grep -v "grep" | wc -l',
                        shell=True, text=True
                    ).strip()
                    if running != "0":
                        new_track = subprocess.check_output(
                            "osascript -e 'tell application \"Spotify\"\n"
                            "set t to current track\n"
                            "return artist of t & \" - \" & name of t\n"
                            "end tell'",
                            shell=True, text=True
                        ).strip()
            except (subprocess.SubprocessError, OSError):
                new_track = ""
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"Unexpected track update error: {e}")
                new_track = ""

            self.metrics['track'] = new_track
            self.stop_event.wait(5)  # Wait for 5 seconds

    def handle_events(self):
        """Processes Pygame events like window resizing and exit commands."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    self.fullscreen = not self.fullscreen
                    if self.fullscreen:
                        info = pygame.display.Info()
                        self.screen = pygame.display.set_mode(
                            (info.current_w, info.current_h), pygame.FULLSCREEN)
                    else:
                        self.screen = pygame.display.set_mode(
                            (1280, 720), pygame.RESIZABLE)
                    self.screen_width, self.screen_height = self.screen.get_size()
                elif event.key == pygame.K_ESCAPE:
                    self.running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:  # Left click
                    self.handle_clicks(event.pos)
            elif event.type == pygame.VIDEORESIZE:
                if not self.fullscreen:
                    self.screen = pygame.display.set_mode(
                        (event.w, event.h), pygame.RESIZABLE)
                    self.screen_width, self.screen_height = event.w, event.h

    def send_agent_command(self, command: str):
        """Sends a UDP command to the Agent (MUTE/UNMUTE)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            message = json.dumps({"command": command})
            sock.sendto(message.encode(), ("127.0.0.1", self.agent_cmd_port))
            print(f"Sent command to agent: {command}")
        except socket.error as e:
            print(f"Command Error: {e}")

    def handle_clicks(self, pos):
        """Handles mouse clicks on UI elements like buttons."""
        # Check Mute Button Area (Top Right)
        btn_rect = pygame.Rect(self.screen_width - 160, 30, 130, 40)
        if btn_rect.collidepoint(pos):
            self.muted = not self.muted
            cmd = "MUTE" if self.muted else "UNMUTE"
            self.send_agent_command(cmd)

    def draw_hud_elements(self):
        """Draws decorative HUD circles and lines."""
        cx, cy = self.screen_width // 2, self.screen_height // 2
        self.anim['angle'] = (self.anim['angle'] + 2) % 360

        # 1. Rotating Inner Ring (Dashed appearance)
        rect_inner = pygame.Rect(0, 0, 450, 450)
        rect_inner.center = (cx, cy)
        for i in range(0, 360, 30):
            start_angle = math.radians(self.anim['angle'] + i)
            end_angle = math.radians(self.anim['angle'] + i + 15)
            pygame.draw.arc(self.screen, DARK_CYAN, rect_inner,
                            start_angle, end_angle, 2)

        # 2. Outer Static HUD (Hexagonal pattern or corners)
        margin = 30  # Define margin for this section
        corner_len = 50
        pygame.draw.lines(self.screen, DARK_CYAN, False, [
                          # Top Left
                          (margin, margin + corner_len), (margin, margin), (margin + corner_len, margin)], 2)
        pygame.draw.lines(self.screen, DARK_CYAN, False, [(self.screen_width - margin, margin + corner_len), (
            # Top Right
            self.screen_width - margin, margin), (self.screen_width - margin - corner_len, margin)], 2)

        # 3. Scanning line
        scan_y = (pygame.time.get_ticks() // 15) % self.screen_height
        scan_surf = pygame.Surface((self.screen_width, 1), pygame.SRCALPHA)
        scan_surf.fill((0, 255, 255, 40))
        self.screen.blit(scan_surf, (0, scan_y))

    def draw_data_stream(self):
        """Draws a 'scrolling' data stream in the bottom right."""
        now = pygame.time.get_ticks()
        if now - self.metrics['stream_timer'] > 2000:
            new_log = f"LOG_{hex(now)[2:].upper()}: {hex(id(self))[2:].upper()} OK"
            self.metrics['data_stream'].append(new_log)
            if len(self.metrics['data_stream']) > 8:
                self.metrics['data_stream'].pop(0)
            self.metrics['stream_timer'] = now

        for i, line in enumerate(self.metrics['data_stream']):
            alpha = int(100 + 155 * (i / len(self.metrics['data_stream'])))
            text_surf = self.fonts['metrics'].render(line, True, CYAN)
            text_surf.set_alpha(alpha)
            self.screen.blit(text_surf, (self.screen_width -
                             250, self.screen_height - 250 + (i * 25)))

    def draw_metrics(self):
        """Draws CPU and RAM usage bars."""
        margin = 30
        bar_width = 150
        bar_height = 10

        # CPU
        cpu_text = self.fonts['metrics'].render(
            f"CPU: {self.metrics['cpu']}%", True, CYAN)
        self.screen.blit(cpu_text, (margin, margin))
        pygame.draw.rect(self.screen, DARK_GRAY,
                         (margin, margin + 25, bar_width, bar_height))
        pygame.draw.rect(self.screen, CYAN, (margin, margin + 25,
                         int(bar_width * (self.metrics['cpu'] / 100)), bar_height))

        # RAM
        ram_text = self.fonts['metrics'].render(
            f"RAM: {self.metrics['ram']}%", True, CYAN)
        self.screen.blit(ram_text, (margin, margin + 50))
        pygame.draw.rect(self.screen, DARK_GRAY,
                         (margin, margin + 75, bar_width, bar_height))
        pygame.draw.rect(self.screen, CYAN, (margin, margin + 75,
                         int(bar_width * (self.metrics['ram'] / 100)), bar_height))

    def draw_noise_layer(self):
        """Adds subtle tech noise/grain."""
        noise_surf = pygame.Surface(
            (self.screen_width, self.screen_height), pygame.SRCALPHA)
        for _ in range(200):
            x = pygame.time.get_ticks() % self.screen_width
            y = (pygame.time.get_ticks() * 1.5) % self.screen_height
            pygame.draw.rect(noise_surf, (255, 255, 255, 10), (x, y, 1, 1))
        self.screen.blit(noise_surf, (0, 0))

    def draw_mute_button(self):
        """Draws a techy Mute/Unmute button in the top right."""
        btn_x = self.screen_width - 160
        btn_y = 30
        btn_w = 130
        btn_h = 40

        color = (255, 50, 50) if self.muted else CYAN
        label = "MUTED" if self.muted else "MICROPHONE"

        # Glow Effect
        glow_rect = pygame.Rect(btn_x - 2, btn_y - 2, btn_w + 4, btn_h + 4)
        pygame.draw.rect(self.screen, (*color, 50), glow_rect, border_radius=5)

        # Main Button
        pygame.draw.rect(self.screen, BLACK, (btn_x, btn_y,
                         btn_w, btn_h), border_radius=5)
        pygame.draw.rect(self.screen, color, (btn_x, btn_y,
                         btn_w, btn_h), 2, border_radius=5)

        # Label
        text = self.fonts['button'].render(label, True, color)
        text_rect = text.get_rect(
            center=(btn_x + btn_w // 2, btn_y + btn_h // 2))
        self.screen.blit(text, text_rect)

    def render(self):
        """Renders the entire UI frame by frame."""
        self.screen.fill(BLACK)

        # 1. Background Pulse/Glow
        vol = self.get_volume()
        # Only pulse with mic if JARVIS is speaking (Prevents constant vibration)
        if self.anim['is_speaking']:
            pulse = 1.0 + min(vol / 8000, 0.1)
        else:
            pulse = 1.0

        # 2. Main GIF UI
        if self.anim['ui_frames']:
            current_frame = self.anim['ui_frames'][self.anim['frame_idx']]
            sw, sh = self.screen.get_size()
            fw, fh = current_frame.get_size()

            # Apply Speaking Animation (Zoom in/out)
            zoom_effect = 1.0
            if self.anim['is_speaking']:
                self.anim['pulse_phase'] += 0.2  # Speed of zoom
                zoom_effect = 1.0 + 0.05 * math.sin(self.anim['pulse_phase'])
            else:
                self.anim['pulse_phase'] = 0  # Reset phase when not speaking

            scale = max(sw/fw, sh/fh) * pulse * zoom_effect
            scaled_frame = pygame.transform.smoothscale(
                current_frame, (int(fw * scale), int(fh * scale)))
            rect = scaled_frame.get_rect(center=(sw//2, sh//2))
            self.screen.blit(scaled_frame, rect)

        # 3. HUD and Effects
        self.draw_hud_elements()
        self.draw_metrics()
        self.draw_data_stream()
        self.draw_mute_button()
        self.draw_noise_layer()

        # 4. Clock
        now = datetime.datetime.now()
        time_str = now.strftime("%H:%M:%S")
        date_str = now.strftime("%A, %B %d")

        time_surf = self.fonts['clock'].render(time_str, True, CYAN)
        time_rect = time_surf.get_rect(center=(self.screen_width // 2, 100))
        self.screen.blit(time_surf, time_rect)

        date_surf = self.fonts['date'].render(date_str, True, WHITE)
        date_rect = date_surf.get_rect(center=(self.screen_width // 2, 160))
        self.screen.blit(date_surf, date_rect)

        # 5. Track Info
        if self.metrics['track']:
            track_surf = self.fonts['track'].render(
                f"🎵 {self.metrics['track']}", True, DARK_CYAN)
            self.screen.blit(
                track_surf, (30, self.screen_height - track_surf.get_height() - 30))

        # Update frame index
        self.anim['frame_idx'] = (
            self.anim['frame_idx'] + 1) % len(self.anim['ui_frames'])

        pygame.display.flip()

    def cleanup(self):
        """Stops all threads and cleans up resources before exit."""
        self.stop_event.set()
        if self.audio['stream']:
            self.audio['stream'].stop_stream()
            self.audio['stream'].close()
        if self.audio['p_audio']:
            self.audio['p_audio'].terminate()
        pygame.quit()

    def run(self):
        """Main application loop."""
        while self.running:
            self.handle_events()
            self.render()
            self.clock.tick(30)
        self.cleanup()


if __name__ == "__main__":
    app = JarvisUI()
    app.run()
