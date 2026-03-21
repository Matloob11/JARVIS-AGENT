# Crucial: Suppress expensive metadata scan in Google API core on Windows
# Must be set BEFORE importing any google-related modules
os.environ["GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK"] = "1"

# SQLite3 Patch for older Linux systems (CentOS/Ubuntu)
# ChromaDB requires sqlite3 >= 3.35.0
if os.name != 'nt':
    try:
        __import__('pysqlite3')
        import sys
        sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
    except ImportError:
        pass

import sys
import subprocess
import logging

# Force global logging to suppress SDK and root noise
logging.basicConfig(level=logging.WARNING, force=True)
for logger_name in ["livekit", "livekit.agents", "livekit.rtc"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# --- Auto-Venv Activation ---
# Ensure we are running with the project's .venv_312 interpreter
if os.name == 'nt':
    venv_python = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".venv_312", "Scripts", "python.exe"))
else:
    venv_python = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".venv_312", "bin", "python"))

if os.path.exists(venv_python) and os.path.abspath(sys.executable).lower() != venv_python.lower():
    print(f"[JARVIS] Environment mismatch. Auto-activating: .venv_312")
    # Using os.execv to replace current process smoothly on Linux/Windows
    import subprocess
    sys.exit(subprocess.call([venv_python] + sys.argv))
# ----------------------------

# Add the project root to sys.path to allow absolute imports
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from livekit import agents

# Use absolute import since root_dir is in sys.path
from src.core.agent_runner import entrypoint


if __name__ == "__main__":
    # Ensure UTF-8 for Windows console
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    OPTS = agents.WorkerOptions(entrypoint_fnc=entrypoint)
    agents.cli.run_app(OPTS)
