"""
# agent.py
Main entrypoint for the JARVIS agent.
"""

from agent_runner import entrypoint
from livekit import agents
import os
import sys

# Crucial: Suppress expensive metadata scan in Google API core on Windows
os.environ["GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK"] = "1"


if __name__ == "__main__":
    # Ensure UTF-8 for Windows console
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    OPTS = agents.WorkerOptions(entrypoint_fnc=entrypoint)
    agents.cli.run_app(OPTS)
