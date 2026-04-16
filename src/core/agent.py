# pylint: disable=wrong-import-position
# ruff: noqa: E402
# Crucial: Suppress expensive metadata scan in Google API core on Windows
import io
import logging
import os
import signal
import subprocess
import sys

# Windows Signal Patch to prevent watchfiles/SIGKILL crash
if sys.platform == "win32" and not hasattr(signal, "SIGKILL"):
    # Map SIGKILL to SIGTERM on Windows as a fallback for libraries (like watchfiles)
    signal.SIGKILL = signal.SIGTERM 

# SET THIS BEFORE ANY OTHER IMPORTS TO SUPPRESS GOOGLE API METADATA SCANNING ON WINDOWS
os.environ["GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK"] = "1"

# SQLite3 Patch for older Linux systems (CentOS/Ubuntu)
# ChromaDB requires sqlite3 >= 3.35.0
if os.name != 'nt':
    try:
        __import__('pysqlite3')
        sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
    except ImportError:
        pass

# Force global logging to show status while debugging
logging.basicConfig(level=logging.INFO, force=True)
for logger_name in ["livekit", "livekit.agents", "livekit.rtc"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# --- Auto-Venv Activation ---
# Ensure we are running with the project's .venv_312 interpreter
if os.name == 'nt':
    venv_python = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".venv_312", "Scripts", "python.exe"))
else:
    venv_python = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".venv_312", "bin", "python"))

if os.path.exists(venv_python) and os.path.abspath(sys.executable).lower() != venv_python.lower():
    print("[JARVIS] Environment mismatch. Auto-activating: .venv_312")
    # Using os.execv to replace current process smoothly on Linux/Windows
    sys.exit(subprocess.call([venv_python] + sys.argv))
# ----------------------------

# Add the project root to sys.path to allow absolute imports
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from livekit import agents

# Use absolute import since root_dir is in sys.path
from src.core.agent_runner import entrypoint

def play_startup_voice():
    """
    Plays the Ironman Jarvis startup voice from the assets folder
    after a 10-second delay as requested by the user.
    Runs separately in a background thread to avoid blocking agent connection.
    """
    def _run_sequence():
        import threading
        import time
        import os
        try:
            # 1. Wait 10 seconds after command starts (User's specific requirement)
            time.sleep(10)
            
            # 2. Resolve absolute path to the audio file
            # Root is 2 levels up from src/core/agent.py
            current_path = os.path.dirname(os.path.abspath(__file__))
            p_root = os.path.abspath(os.path.join(current_path, "..", ".."))
            # File: assets/audio/Installing... Ironman Jarvis Ai.mp3
            voice_file = os.path.join(p_root, "assets", "audio", "Installing... Ironman Jarvis Ai.mp3")

            if os.path.exists(voice_file):
                print(f"[JARVIS-SYSTEM] initializing neural voice: {os.path.basename(voice_file)}")
                import pygame
                pygame.mixer.init()
                pygame.mixer.music.load(voice_file)
                pygame.mixer.music.set_volume(0.85) # Premium volume
                pygame.mixer.music.play()
                
                # Keep thread alive during playback
                while pygame.mixer.music.get_busy():
                    time.sleep(1)
            else:
                # Silently log error to console for Matloob to see but don't stop system
                print(f"[JARVIS-WARN] Startup voice not found at: {voice_file}")
        except Exception as ex:
            # Catch-all to prevent startup crash
            print(f"[JARVIS-ERROR] System voice fail: {ex}")

    # Launch daemon thread
    import threading
    threading.Thread(target=_run_sequence, daemon=True).start()

if __name__ == "__main__":
    # Initiate startup voice sequence (Background)
    play_startup_voice()
    
    # Ensure UTF-8 for Windows console
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    OPTS = agents.WorkerOptions(entrypoint_fnc=entrypoint)
    agents.cli.run_app(OPTS)
