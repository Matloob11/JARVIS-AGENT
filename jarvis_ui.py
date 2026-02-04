import pygame
import pyaudio
import struct
import threading
import os
import sys
import platform
import cv2
from PIL import Image, ImageSequence
import datetime
import subprocess
import time

script_dir = os.path.dirname(__file__)

# Global variables
grab_active = False

# Color definitions
CYAN = (0, 255, 255)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
LIGHT_CYAN = (0, 200, 200)
DARK_BLUE = (10, 20, 40)
HIGHLIGHT_ALPHA = 80

# Initialize pygame
pygame.init()

# Cross-platform font handling
def get_font_path():
    system = platform.system()
    if system == "Darwin":  # macOS
        return "Orbitron-VariableFont_wght.ttf"
    else:  # Windows/Linux fallback
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
screen = pygame.display.set_mode((screen_width, screen_height), pygame.RESIZABLE)
pygame.display.set_caption('J.A.R.V.I.S')

def load_image_safe(path, default_size=(200, 200)):
    """Safely load images with fallback"""
    if os.path.exists(path):
        try:
            img = pygame.image.load(path).convert_alpha()
            return pygame.transform.scale(img, default_size)
        except:
            pass
    surf = pygame.Surface(default_size)
    surf.fill(CYAN)
    return surf

# Load GIFs safely with better error handling
def load_gif_safe(gif_path, fallback_frames=10):
    print(f"Attempting to load GIF: {gif_path}")
    print(f"File exists: {os.path.exists(gif_path)}")
    
    if not os.path.exists(gif_path):
        print(f"ERROR: GIF file not found at {gif_path}")
        return create_fallback_frames(fallback_frames)
    
    try:
        # Load GIF using PIL
        gif_image = Image.open(gif_path)
        print(f"GIF opened successfully. Size: {gif_image.size}, Frames: {gif_image.n_frames}")
        
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
        
    except Exception as e:
        print(f"ERROR loading GIF {gif_path}: {str(e)}")
        print(f"Exception type: {type(e).__name__}")
        return create_fallback_frames(fallback_frames)

def create_fallback_frames(num_frames=10):
    """Create animated fallback frames when GIF loading fails"""
    frames = []
    size = (200, 200)
    
    for i in range(num_frames):
        surf = pygame.Surface(size, pygame.SRCALPHA)
        
        # Create pulsing animation
        pulse = 0.5 + 0.5 * pygame.math.cos(i * 0.5)
        radius = int(50 + 30 * pulse)
        alpha = int(100 + 155 * pulse)
        
        # Create color with alpha
        color = (*CYAN, alpha)
        
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
p = None
stream = None
def init_audio():
    global p, stream
    try:
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100, 
                       input=True, frames_per_buffer=512)
        return True
    except:
        print("Audio input not available, running without microphone")
        return False

audio_available = init_audio()

def get_volume(data):
    if not data:
        return 0
    count = len(data) // 2
    format_str = f"%dh" % count
    shorts = struct.unpack(format_str, data)
    sum_squares = sum(s**2 for s in shorts)
    return (sum_squares / count)**0.5

track = ""
track_lock = threading.Lock()

def fetch_track():
    global track
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
    except:
        new_track = ""
    
    with track_lock:
        track = new_track

def toggle_fullscreen(screen):
    """Toggle between windowed and fullscreen mode"""
    info = pygame.display.Info()
    fullscreen_width, fullscreen_height = info.current_w, info.current_h
    screen = pygame.display.set_mode((fullscreen_width, fullscreen_height), pygame.FULLSCREEN)
    return screen

def toggle_windowed(screen):
    """Return to windowed mode"""
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    return screen

def main():
    global screen, grab_active
    running = True
    fullscreen = False
    ui_frame_idx = 0  # Only one frame index needed
    gif_scale = 1.0
    clock = pygame.time.Clock()
    track_update_ms = 3000
    last_track_ms = 0

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    fullscreen = not fullscreen
                    if fullscreen:
                        screen = toggle_fullscreen(screen)
                    else:
                        screen = toggle_windowed(screen)
                elif event.key == pygame.K_ESCAPE:
                    running = False

        # Audio volume visualization with reduced sensitivity
        try:
            if audio_available and stream:
                audio_data = stream.read(2048, exception_on_overflow=False)
                volume = get_volume(audio_data)
                # Much smaller scale factor for subtle effect
                scale_factor = 1 + min(volume / 5000, 0.05)  # Max 5% change instead of 100%
                gif_scale = 0.95 * gif_scale + 0.05 * scale_factor  # Smoother transition
            else:
                gif_scale = gif_scale * 0.99 + 0.01 * 1.0  # Slowly return to 1.0
        except:
            gif_scale = gif_scale * 0.99 + 0.01 * 1.0

        # Update track info
        now_ms = pygame.time.get_ticks()
        if now_ms - last_track_ms >= track_update_ms:
            threading.Thread(target=fetch_track, daemon=True).start()
            last_track_ms = now_ms

        # **PURE BLACK BACKGROUND with subtle glow effect**
        screen.fill(BLACK)
        
        # Add subtle background glow effect
        glow_surface = pygame.Surface((screen.get_width(), screen.get_height()), pygame.SRCALPHA)
        center_x, center_y = screen.get_width() // 2, screen.get_height() // 2
        
        # Create radial gradient for ambient lighting
        for radius in range(200, 50, -10):
            alpha = max(0, int(20 * (200 - radius) / 150))
            color = (*DARK_BLUE, alpha)
            pygame.draw.circle(glow_surface, color, (center_x, center_y), radius)
        
        screen.blit(glow_surface, (0, 0))

        # Render the new JARVIS UI GIF - PROPERLY FITTED TO SCREEN
        if ui_frame_surfaces:
            ui_frame = ui_frame_surfaces[ui_frame_idx]
            
            # Get screen and frame dimensions
            screen_width, screen_height = screen.get_size()
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
            ui_scaled = pygame.transform.scale(ui_frame, (scaled_width, scaled_height)).convert_alpha()
            
            # Center the UI on screen
            ui_rect = ui_scaled.get_rect(center=(screen_width // 2, screen_height // 2))
            screen.blit(ui_scaled, ui_rect)

        # MODERN CLOCK DISPLAY
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M:%S")
        date_str = now.strftime("%A, %B %d, %Y")
        
        # Time with drop shadow effect
        time_shadow = clock_shadow_font.render(current_time, True, BLACK)
        time_shadow_rect = time_shadow.get_rect(center=(screen.get_width() // 2 + 3, 103))
        screen.blit(time_shadow, time_shadow_rect)
        
        time_surface = clock_font.render(current_time, True, CYAN)
        time_rect = time_surface.get_rect(center=(screen.get_width() // 2, 100))
        screen.blit(time_surface, time_rect)
        
        # Date below time
        date_surface = pygame.font.SysFont("Arial", 24, bold=True).render(date_str, True, WHITE)
        date_rect = date_surface.get_rect(center=(screen.get_width() // 2, 140))
        screen.blit(date_surface, date_rect)

        # Current track - bottom left with icon
        with track_lock:
            current_track = track
        if current_track:
            track_icon = "🎵 "
            track_text = track_icon + current_track
            track_surface = track_font.render(track_text, True, LIGHT_CYAN)
            track_pos = (30, screen.get_height() - track_surface.get_height() - 30)
            screen.blit(track_surface, track_pos)

        pygame.display.flip()
        ui_frame_idx = (ui_frame_idx + 1) % len(ui_frame_surfaces)
        clock.tick(30)

    # Cleanup
    if stream:
        stream.stop_stream()
        stream.close()
    if p:
        p.terminate()
    pygame.quit()
    sys.exit()

if __name__ == '__main__':
    main()
