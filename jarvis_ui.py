"""
Jarvis UI Module
Handles the graphical user interface and visualizations for the AI Assistant.
"""
# pylint: disable=no-member
import os
import sys
import platform
import struct
import threading
import datetime
import subprocess
import pygame
import pyaudio
from PIL import Image

script_dir = os.path.dirname(__file__)

# Global variables
GRAB_ACTIVE = False

# Color definitions
CYAN = (0, 255, 255)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
LIGHT_CYAN = (0, 200, 200)
DARK_BLUE = (10, 20, 40)
DARK_GRAY = (40, 40, 40)
HIGHLIGHT_ALPHA = 80

# Initialize pygame
pygame.init()

# Cross-platform font handling


def get_font_path():
    """Returns the font path based on the operating system."""
    system = platform.system()
    if system == "Darwin":  # macOS
        return "Orbitron-VariableFont_wght.ttf"
    return None


font_path = get_font_path()
if font_path and os.path.exists(font_path):
    clock_font = pygame.font.Font(font_path, 72)
    clock_shadow_font = pygame.font.Font(font_path, 72)
    description_font = pygame.font.Font(font_path, 16)
    todo_font = pygame.font.Font(font_path, 28)
else:
    # Fallback fonts
    clock_font = pygame.font.SysFont("Arial", 72, bold=True)
    clock_shadow_font = pygame.font.SysFont("Arial", 72, bold=True)
    description_font = pygame.font.SysFont("Arial", 16)
    todo_font = pygame.font.SysFont("Arial", 28)
track_font = pygame.font.SysFont("Arial", 26)

# Screen setup - Fixed size for consistent scaling
screen_width, screen_height = 1280, 720  # Standard HD resolution
screen = pygame.display.set_mode(
    (screen_width, screen_height), pygame.RESIZABLE)
pygame.display.set_caption('J.A.R.V.I.S')


def load_image_safe(path, default_size=(200, 200)):
    """Safely load images with fallback"""
    if os.path.exists(path):
        try:
            img = pygame.image.load(path).convert_alpha()
            return pygame.transform.scale(img, default_size)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    surf = pygame.Surface(default_size)
    surf.fill(CYAN)
    return surf


def draw_rounded_rect(target_screen_ptr, color, rect, radius=15):
    """Draws a rectangle with rounded corners on the given surface."""
    try:
        temp_surface = pygame.Surface(
            (rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(temp_surface, color, (0, 0, rect.width,
                         rect.height), border_radius=radius)
        target_screen_ptr.blit(temp_surface, rect.topleft)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Assuming 'logger' is defined elsewhere or will be added.
        # For now, a simple print can serve as a placeholder.
        print(f"Error drawing rounded rect: {e}")


def draw_status_bar(target_screen_ptr):
    """Draws a status bar at the bottom of the screen."""
    status_rect_area = pygame.Rect(0, screen_height - 30, screen_width, 30)
    draw_rounded_rect(target_screen_ptr, DARK_GRAY, status_rect_area, radius=5)

# Load GIFs safely with better error handling


def load_gif_safe(gif_path, fallback_frames=10):
    """Safely loads a GIF file into a list of pygame surfaces."""
    print(f"Attempting to load GIF: {gif_path}")
    print(f"File exists: {os.path.exists(gif_path)}")

    if not os.path.exists(gif_path):
        print(f"ERROR: GIF file not found at {gif_path}")
        return create_fallback_frames(fallback_frames)

    try:
        # Load GIF using PIL
        gif_image = Image.open(gif_path)
        print(
            f"GIF opened successfully. Size: {gif_image.size}, Frames: {gif_image.n_frames}")

        frames = []
        for frame_num in range(gif_image.n_frames):
            gif_image.seek(frame_num)
            # Convert to RGBA
            frame = gif_image.copy().convert("RGBA")

            # Convert PIL image to pygame surface
            mode = frame.mode
            size = frame.size
            data = frame.tobytes()

            pygame_surface = pygame.image.frombuffer(data, size, mode)
            frames.append(pygame_surface)

        print(f"Successfully loaded {len(frames)} frames from {gif_path}")
        return frames

    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"ERROR loading GIF {gif_path}: {str(e)}")
        print(f"Exception type: {type(e).__name__}")
        return create_fallback_frames(fallback_frames)


def create_fallback_frames(num_frames=10):
    """Creates animated fallback surfaces if GIF loading fails."""
    frames = []
    size = (200, 200)

    for i in range(num_frames):
        surf = pygame.Surface(size, pygame.SRCALPHA)

        # Create pulsing animation
        pulse = 0.5 + 0.5 * pygame.math.cos(i * 0.5)
        radius = int(50 + 30 * pulse)

        # Draw pulsing circle

        # Draw pulsing circle
        pygame.draw.circle(surf, CYAN, (size[0]//2, size[1]//2), radius)

        frames.append(surf)

    return frames


# Load the new JARVIS UI GIF
jarvis_ui_gif_path = os.path.join(script_dir, 'jarvis-ui.gif')

print(f"Script directory: {script_dir}")
print(f"Looking for JARVIS UI GIF at: {jarvis_ui_gif_path}")

# Load only the main UI GIF
ui_frame_surfaces = load_gif_safe(jarvis_ui_gif_path)

print(f"Loaded {len(ui_frame_surfaces)} UI frames")

# PyAudio setup with error handling
P_AUDIO = None
STREAM = None


def init_audio():
    """Initializes the PyAudio input stream."""
    global P_AUDIO, STREAM  # pylint: disable=global-statement
    try:
        P_AUDIO = pyaudio.PyAudio()
        STREAM = P_AUDIO.open(format=pyaudio.paInt16, channels=1, rate=44100,
                              input=True, frames_per_buffer=512)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        print("Audio input not available, running without microphone")
        return False


AUDIO_AVAILABLE = init_audio()


def get_volume(data):
    """Calculates the RMS volume from raw audio data."""
    if not data:
        return 0
    count = len(data) // 2
    format_str = f"{count}h"
    shorts = struct.unpack(format_str, data)
    sum_squares = sum(s**2 for s in shorts)
    return (sum_squares / count)**0.5


TRACK = ""
TRACK_LOCK = threading.Lock()


def fetch_track():
    """Fetches the currently playing track info (Spotify/macOS only)."""
    global TRACK  # pylint: disable=global-statement
    try:
        system = platform.system()
        if system == "Darwin":
            running = subprocess.check_output(
                'ps -ef | grep "MacOS/Spotify" | grep -v "grep" | wc -l',
                shell=True, text=True
            ).strip()
            if running == "0":
                new_track = ""
            else:
                new_track = subprocess.check_output(
                    """osascript -e 'tell application "Spotify"
                    set t to current track
                    return artist of t & " - " & name of t
                    end tell'""",
                    shell=True, text=True
                ).strip()
        else:
            new_track = ""
    except Exception:  # pylint: disable=broad-exception-caught
        new_track = ""

    with TRACK_LOCK:
        TRACK = new_track


def toggle_fullscreen(win_ptr):  # pylint: disable=unused-argument
    """Toggle between windowed and fullscreen mode"""
    info = pygame.display.Info()
    fullscreen_width, fullscreen_height = info.current_w, info.current_h
    new_screen = pygame.display.set_mode(
        (fullscreen_width, fullscreen_height), pygame.FULLSCREEN)
    return new_screen


def toggle_windowed(win_ptr):  # pylint: disable=unused-argument
    """Return to windowed mode"""
    new_screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    return new_screen


def handle_events(running, fullscreen, ui_screen):
    """Handles Pygame events for the UI loop."""
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False, fullscreen, ui_screen
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                fullscreen = not fullscreen
                if fullscreen:
                    ui_screen = toggle_fullscreen(ui_screen)
                else:
                    ui_screen = toggle_windowed(ui_screen)
            elif event.key == pygame.K_ESCAPE:
                return False, fullscreen, ui_screen
    return running, fullscreen, ui_screen


def update_gif_scale(gif_scale):
    """Updates the GIF scale factor based on audio volume."""
    try:
        if AUDIO_AVAILABLE and STREAM:
            audio_data = STREAM.read(2048, exception_on_overflow=False)
            volume = get_volume(audio_data)
            scale_factor = 1 + min(volume / 5000, 0.05)
            gif_scale = 0.95 * gif_scale + 0.05 * scale_factor
        else:
            gif_scale = gif_scale * 0.99 + 0.01 * 1.0
    except Exception:  # pylint: disable=broad-exception-caught
        gif_scale = gif_scale * 0.99 + 0.01 * 1.0
    return gif_scale


def main():
    """Main UI loop for the Jarvis Assistant."""
    global screen  # pylint: disable=global-statement
    running = True
    fullscreen = False
    ui_frame_idx = 0
    gif_scale = 1.0
    clock = pygame.time.Clock()
    track_update_ms = 3000
    last_track_ms = 0

    while running:
        running, fullscreen, screen = handle_events(
            running, fullscreen, screen)
        if not running:
            break

        gif_scale = update_gif_scale(gif_scale)

        # Update track info
        now_ms = pygame.time.get_ticks()
        if now_ms - last_track_ms >= track_update_ms:
            threading.Thread(target=fetch_track, daemon=True).start()
            last_track_ms = now_ms

        # **PURE BLACK BACKGROUND with subtle glow effect**
        screen.fill(BLACK)

        # Add subtle background glow effect
        glow_surface = pygame.Surface(
            (screen.get_width(), screen.get_height()), pygame.SRCALPHA)
        center_x, center_y = screen.get_width() // 2, screen.get_height() // 2

        # Create radial gradient for ambient lighting
        for radius in range(200, 50, -10):
            alpha = max(0, int(20 * (200 - radius) / 150))
            color = (*DARK_BLUE, alpha)
            pygame.draw.circle(glow_surface, color,
                               (center_x, center_y), radius)

        screen.blit(glow_surface, (0, 0))

        # Render the new JARVIS UI GIF - PROPERLY FITTED TO SCREEN
        if ui_frame_surfaces:
            ui_frame = ui_frame_surfaces[ui_frame_idx]

            # Get current dimensions
            curr_w, curr_h = screen.get_size()
            frame_width, frame_height = ui_frame.get_size()

            # Calculate scale to fit screen perfectly (stretch to fill)
            scale_x = screen_width / frame_width
            scale_y = screen_height / frame_height

            # Use the larger scale to fill the entire screen
            base_scale = max(scale_x, scale_y)

            # Apply subtle audio-reactive scaling (much smaller range)
            audio_scale_range = 0.02  # Very small range (2% max change)
            audio_multiplier = 1.0 + (gif_scale - 1.0) * audio_scale_range
            final_scale = base_scale * audio_multiplier

            # Calculate final dimensions
            scaled_width = int(frame_width * final_scale)
            scaled_height = int(frame_height * final_scale)

            # Scale the frame
            ui_scaled = pygame.transform.scale(
                ui_frame, (scaled_width, scaled_height)).convert_alpha()

            # Center the UI on screen
            ui_rect = ui_scaled.get_rect(
                center=(curr_w // 2, curr_h // 2))
            screen.blit(ui_scaled, ui_rect)

        # MODERN CLOCK DISPLAY
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M:%S")
        date_str = now.strftime("%A, %B %d, %Y")

        # Time with drop shadow effect
        time_shadow = clock_shadow_font.render(current_time, True, BLACK)
        time_shadow_rect = time_shadow.get_rect(
            center=(screen.get_width() // 2 + 3, 103))
        screen.blit(time_shadow, time_shadow_rect)

        time_surface = clock_font.render(current_time, True, CYAN)
        time_rect = time_surface.get_rect(
            center=(screen.get_width() // 2, 100))
        screen.blit(time_surface, time_rect)

        # Date below time
        date_surface = pygame.font.SysFont(
            "Arial", 24, bold=True).render(date_str, True, WHITE)
        date_rect = date_surface.get_rect(
            center=(screen.get_width() // 2, 140))
        screen.blit(date_surface, date_rect)

        # Current track - bottom left with icon
        with TRACK_LOCK:
            current_track = TRACK
        if current_track:
            track_icon = "🎵 "
            track_text = track_icon + current_track
            track_surface = track_font.render(track_text, True, LIGHT_CYAN)
            track_pos = (30, screen.get_height() -
                         track_surface.get_height() - 30)
            screen.blit(track_surface, track_pos)

        pygame.display.flip()
        ui_frame_idx = (ui_frame_idx + 1) % len(ui_frame_surfaces)
        clock.tick(30)

    # Cleanup
    if STREAM:
        STREAM.stop_stream()
        STREAM.close()
    if P_AUDIO:
        P_AUDIO.terminate()
    pygame.quit()
    sys.exit()


if __name__ == '__main__':
    main()
