"""
# vortex.py
Orchestrator for JARVIS Systems.
Starts the UI Bridge (Socket.IO) and the Agent Runner (LiveKit).
"""

import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any
# --- Bootstrap: Ensure src is findable even if run as a script ---
current_file_path = os.path.abspath(__file__)
# src/core/vortex.py -> up 3 levels to get project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# -------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="[VORTEX] %(message)s")
logger = logging.getLogger("VORTEX")


def start_process(command: str, name: str, extra_env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start a subprocess with the correct environment."""
    print(f"Starting {name}...")
    # Build env: inherit current env and inject PYTHONPATH so modules resolve
    env = os.environ.copy()
    # Attempt to find project root by looking for 'src' folder
    # Current file is at (root)/src/core/vortex.py
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
    
    # If the above doesn't have 'src', fallback to CWD if it looks like project root
    if not os.path.isdir(os.path.join(root_dir, 'src')):
        cwd = os.getcwd()
        if os.path.isdir(os.path.join(cwd, 'src')):
            root_dir = cwd
            
    print(f"[VORTEX] Root Directory detected: {root_dir}")
    python_path_parts = [root_dir]
    existing = env.get('PYTHONPATH', '')
    if existing:
        python_path_parts.append(existing)
    env['PYTHONPATH'] = os.pathsep.join(python_path_parts)
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        cwd=root_dir,
        env=env,
    )


def main() -> None:
    # Ensure UTF-8 for Windows console via env var (safer than replacing sys.stdout)
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    print("="*50)
    print("JARVIS ORCHESTRATOR | SIR MATLOOB EDITION")
    print("="*50)

    python_cmd = sys.executable

    # Start UI Bridge
    bridge_proc = start_process(f"{python_cmd} -m src.core.ui_bridge", "UI-BRIDGE")

    # Wait for bridge to be healthy before starting Agent Runner
    print("[VORTEX] Waiting for UI Bridge to initialize...")
    max_retries = 10
    for i in range(max_retries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:5001/health", timeout=1) as response:
                if response.status == 200:
                    print("[VORTEX] UI Bridge is ONLINE.")
                    break
        except Exception:  # pylint: disable=broad-exception-caught
            if i < max_retries - 1:
                time.sleep(1)
            else:
                print("[VORTEX] Warning: UI Bridge did not respond after 10s. Starting agent anyway.")

    # Start Agent Runner
    agent_proc = start_process(f"{python_cmd} -m src.core.agent dev", "AGENT-RUNNER")

    processes: list[dict[str, Any]] = [
        {"proc": bridge_proc, "name": "UI-BRIDGE"},
        {"proc": agent_proc, "name": "AGENT-RUNNER"},
    ]

    def stream_reader(pipe: Any, prefix: str) -> None:
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    print(f"[{prefix}] {line.strip()}")
        except (OSError, ValueError) as e:
            print(f"[{prefix}] Reader Error: {e}")
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("[%s] Unexpected Reader Error: %s", prefix, e)
        finally:
            pipe.close()

    try:
        # Start reader threads
        for p in processes:
            t = threading.Thread(target=stream_reader, args=(p["proc"].stdout, p["name"]), daemon=True)
            t.start()
            p["thread"] = t

        while True:
            for p in processes:
                # Poll for exit
                proc_obj = p["proc"]
                retcode = proc_obj.poll()
                if retcode is not None:
                    print(f"\n[ERROR] {p['name']} EXITED with code {retcode}")
                    raise KeyboardInterrupt
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Shutting down JARVIS systems...")
        for p in processes:
            print(f"Terminating {p['name']}...")
            if p["proc"].poll() is None:  # Only kill if still running
                if sys.platform == "win32":
                    # Use taskkill with /T (tree) and suppress errors if process is already gone
                    subprocess.call(
                        ['taskkill', '/F', '/T', '/PID', str(p["proc"].pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    p["proc"].terminate()
            else:
                print(f"{p['name']} was already terminated.")
        print("[INFO] All systems offline.")
        sys.exit(1) # Ensure non-zero exit code on failure/interrupt if it was triggered by a crash


if __name__ == "__main__":
    main()
