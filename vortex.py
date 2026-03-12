"""
# vortex.py
Orchestrator for JARVIS Systems.
Starts the UI Bridge (Socket.IO) and the Agent Runner (LiveKit).
"""

import subprocess
import time
import sys
import io


def start_process(command, name):
    print(f"Starting {name}...")
    return subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )


def main():
    # Ensure UTF-8 for Windows console
    if sys.platform == "win32":
        # Use pre-imported io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("="*50)
    print("JARVIS ORCHESTRATOR | SIR MATLOOB EDITION")
    print("="*50)

    python_cmd = sys.executable

    # Start UI Bridge
    bridge_proc = start_process(f"{python_cmd} ui_bridge.py", "UI-BRIDGE")

    # Wait a moment for bridge to initialize
    time.sleep(2)

    # Start Agent Runner
    agent_proc = start_process(f"{python_cmd} agent.py dev", "AGENT-RUNNER")

    processes = [
        {"proc": bridge_proc, "name": "UI-BRIDGE"},
        {"proc": agent_proc, "name": "AGENT-RUNNER"}
    ]

    try:
        while True:
            for p in processes:
                # Check for output
                retcode = p["proc"].poll()
                if retcode is not None:
                    print(f"[ERROR] {p['name']} EXITED with code {retcode}")
                    # Auto-restart logic if needed, but for now just exit
                    raise KeyboardInterrupt

                # Non-blocking read (optional)

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Shutting down JARVIS systems...")
        for p in processes:
            print(f"Terminating {p['name']}...")
            if sys.platform == "win32":
                subprocess.call(
                    ['taskkill', '/F', '/T', '/PID', str(p["proc"].pid)])
            else:
                p["proc"].terminate()
        print("[INFO] All systems offline.")


if __name__ == "__main__":
    main()
