# pylint: disable=wrong-import-position, ungrouped-imports
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

running_under_pytest = "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ
if (
    not running_under_pytest
    and os.path.exists(venv_python)
    and os.path.abspath(sys.executable).lower() != venv_python.lower()
):
    print("[JARVIS] Environment mismatch. Auto-activating: .venv_312")
    # Using os.execv to replace current process smoothly on Linux/Windows
    sys.exit(subprocess.call([venv_python] + sys.argv))
# ----------------------------

# Add the project root to sys.path to allow absolute imports
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Single worker (K-53): lock before the heavy imports so a second start exits 3 within seconds. The PC brain
# also takes the shared desktop actor lock (K-53 v4), so it never runs beside the Desktop Node (exit 3).
if __name__ == "__main__":
    from src.core.agent_lock import HOLDER_PC_BRAIN, acquire_actor_or_exit, acquire_or_exit
    acquire_or_exit()
    acquire_actor_or_exit(HOLDER_PC_BRAIN)

from livekit import agents

# Use absolute import since root_dir is in sys.path
from src.core.agent_runner import build_worker_options, entrypoint

STARTUP_SOUND_DEFAULT = os.path.join("assets", "audio", "Installing... Ironman Jarvis Ai.mp3")
STARTUP_CHIME = os.path.join("assets", "audio", "jarvis_startup_chime.wav")  # X-02, tracked in git


def startup_sound_candidates():
    """{path: log name} of the startup sound files in priority order: JARVIS_STARTUP_SOUND (default
    the Ironman MP3), the same name as .wav, then the bundled chime. Relative paths start at the repo
    root, not the cwd. K-69: a configured path is logged by the variable's name, never its value."""
    configured = os.getenv("JARVIS_STARTUP_SOUND", "").strip()
    first = os.path.normpath(os.path.join(root_dir, os.path.expanduser(configured or STARTUP_SOUND_DEFAULT)))
    twin = os.path.splitext(first)[0] + ".wav"
    chime = os.path.normpath(os.path.join(root_dir, STARTUP_CHIME))
    names = ("JARVIS_STARTUP_SOUND", "JARVIS_STARTUP_SOUND as .wav") if configured else (first, twin)
    candidates = {}
    for path, name in zip((first, twin, chime), (*names, chime)):
        candidates.setdefault(path, name)
    return candidates


def _play_startup_sound(delay_s):
    import time  # pylint: disable=import-outside-toplevel
    try:
        # 1. Wait 10 seconds after command starts (User's specific requirement)
        time.sleep(delay_s)

        # 2. Pick the files only now. flush=True: __main__ swaps sys.stdout for a block-buffered
        # wrapper, which kept these lines out of logs/agent_stdout.log until the worker exited.
        candidates = startup_sound_candidates()
        found = [path for path in candidates if os.path.isfile(path)]
        if not found:
            # One line, no retries (C-92 adds the spoken fallback here)
            print("[JARVIS-INFO] Startup voice not found; tried: "
                  + " | ".join(candidates.values()), flush=True)
            return
        try:
            import pygame  # pylint: disable=import-outside-toplevel  # optional; hybrid installs may skip it
        except ImportError:
            print("[JARVIS-WARN] pygame not installed; skipping startup voice", flush=True)
            return
        pygame.mixer.init()
        for voice_file in found:
            name = os.path.basename(candidates[voice_file])
            try:
                pygame.mixer.music.load(voice_file)
            except pygame.error as ex:  # pylint: disable=no-member  # defined in a C extension
                # Empty or corrupt: try the next file. Some pygame errors name the path (K-69).
                print(f"[JARVIS-WARN] Startup voice {name} unplayable: "
                      f"{str(ex).replace(voice_file, name)}", flush=True)
                continue
            pygame.mixer.music.set_volume(0.85) # Premium volume
            pygame.mixer.music.play()
            # Only after play(): in the log this line is the proof that the sound started.
            print(f"[JARVIS-SYSTEM] initializing neural voice: {name}", flush=True)

            # Keep thread alive during playback
            while pygame.mixer.music.get_busy():
                time.sleep(1)
            return
    except Exception as ex:  # pylint: disable=broad-exception-caught
        # Catch-all to prevent startup crash
        print(f"[JARVIS-ERROR] System voice fail: {ex}", flush=True)


def play_startup_voice(delay_s=10):
    """
    Plays the first startup sound pygame can load (startup_sound_candidates) after a 10-second
    delay as requested by the user.
    Runs separately in a background thread to avoid blocking agent connection.
    Returns that thread; under pytest none is started (tests never play real audio), and in remote
    mode none either: the VPS has no speaker, the Desktop Node plays it on the laptop (K-72). Nor for a
    worker that the LiveKit link recovery started (JARVIS_STARTED_BY=worker, C-136): that comes at any hour.
    """
    if running_under_pytest:
        return None
    from services.utils.jarvis_config import is_local_mode  # pylint: disable=import-outside-toplevel
    if not is_local_mode():
        return None
    if os.getenv("JARVIS_STARTED_BY") == "worker":
        print("[JARVIS-INFO] Startup voice skipped: automatic restart (C-136 LiveKit link recovery)", flush=True)
        return None
    import threading  # pylint: disable=import-outside-toplevel
    thread = threading.Thread(target=_play_startup_sound, args=(delay_s,),
                              name="jarvis-startup-sound", daemon=True)
    thread.start()
    return thread

if __name__ == "__main__":
    # Initiate startup voice sequence (Background)
    play_startup_voice()
    
    # Ensure UTF-8 for Windows console
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    OPTS = build_worker_options(entrypoint)
    # W-room-watchdog: re-runs ensure_room.py when the room loses its agent (pc mode, this process only).
    from src.core.room_watchdog import start_room_watchdog
    start_room_watchdog()
    agents.cli.run_app(OPTS)
