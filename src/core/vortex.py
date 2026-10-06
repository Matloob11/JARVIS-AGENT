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

from src.core import agent_lock  # pylint: disable=wrong-import-position

logging.basicConfig(level=logging.INFO, format="[VORTEX] %(message)s")
logger = logging.getLogger("VORTEX")

SKIP_AGENT_MESSAGE = "agent worker is managed by scripts/agent_ctl.py - not starting a second one"
# C-64 (K-29): the local bridge never shares the agent's main log file (one rotating handler per file and process).
BRIDGE_LOCAL_LOG_FILE = os.path.join("logs", "ui_bridge_local.log")


def _env_value(name: str) -> str | None:
    """`name` from the environment, else from the root .env (dotenv when available)."""
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        from dotenv import dotenv_values  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    return dotenv_values(os.path.join(project_root, ".env")).get(name)


def _hybrid_mode() -> bool:
    """JARVIS_HYBRID=1 in the environment or, when it is unset there, in the root .env."""
    return (_env_value("JARVIS_HYBRID") or "").strip() == "1"


def _brain() -> str:
    """The brain this laptop serves (K-72): JARVIS_BRAIN from the environment or the root .env, default
    'pc'. In vps mode the laptop runtime is the Desktop Node ('agent_ctl node'), not this orchestrator."""
    value = (_env_value("JARVIS_BRAIN") or "pc").strip().lower()
    return value if value in ("vps", "pc") else "pc"


def start_process(command: str, name: str, extra_env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start a subprocess with the correct environment."""
    print(f"Starting {name}...")
    # Build env: inherit current env and inject PYTHONPATH so modules resolve
    env = os.environ.copy()
    # Attempt to find project root by looking for 'src' folder
    # Current file is at (root)/src/core/vortex.py
    current_dir = os.path.dirname(os.path.abspath(__file__)) # src/core
    # Go up 2 levels: src/core -> src -> root
    root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
    
    # Validation: Check if 'src' exists in the resolved root
    if not os.path.isdir(os.path.join(root_dir, 'src')):
        # Fallback to searching upwards if we're deeper than expected
        search_root = current_dir
        while search_root and os.path.dirname(search_root) != search_root:
            if os.path.isdir(os.path.join(search_root, 'src')):
                root_dir = search_root
                break
            search_root = os.path.dirname(search_root)
            
    print(f"[VORTEX] Project Root: {root_dir}")
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

    # VPS brain (K-72): in vps mode the laptop runs the Desktop Node, managed by 'agent_ctl node', and the
    # brain (agent + bridge) lives on the VPS. This orchestrator starts nothing then.
    if _brain() == "vps":
        print("[VORTEX] JARVIS_BRAIN=vps: the laptop runs the Desktop Node (scripts/agent_ctl.py node), and "
              "the agent + bridge run on the VPS. Nothing to start here.")
        return

    # Single worker (K-53): never start a second agent next to a running one or the hybrid one.
    hybrid = _hybrid_mode()
    start_agent = not hybrid and not agent_lock.is_locked()
    if not start_agent:
        print(f"[VORTEX] {SKIP_AGENT_MESSAGE}")
        if hybrid:
            print("[VORTEX] JARVIS_HYBRID=1: the UI bridge runs on the VPS, nothing to start here.")
            return

    # Start UI Bridge (its own LOG_FILE, C-64)
    bridge_proc = start_process(f"{python_cmd} -m src.core.ui_bridge", "UI-BRIDGE",
                                extra_env={"LOG_FILE": BRIDGE_LOCAL_LOG_FILE})
    processes: list[dict[str, Any]] = [{"proc": bridge_proc, "name": "UI-BRIDGE"}]

    if start_agent:
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
        agent_proc = start_process(
            f"{python_cmd} -m src.core.agent dev",
            "AGENT-RUNNER",
            extra_env={"JARVIS_STARTED_BY": os.environ.get("JARVIS_STARTED_BY") or "vortex"},
        )
        processes.append({"proc": agent_proc, "name": "AGENT-RUNNER"})

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
            for p in list(processes):
                # Poll for exit
                proc_obj = p["proc"]
                retcode = proc_obj.poll()
                if retcode is None:
                    continue
                if p["name"] == "AGENT-RUNNER" and retcode == agent_lock.EXIT_ALREADY_RUNNING:
                    # Another worker took the lock first: not a crash, keep serving the bridge.
                    print(f"[VORTEX] AGENT-RUNNER exited 3 (already running): {SKIP_AGENT_MESSAGE}")
                    processes.remove(p)
                    continue
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
