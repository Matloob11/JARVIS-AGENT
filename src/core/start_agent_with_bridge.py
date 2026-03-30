"""
# start_agent_with_bridge.py
Helper script to start both the JARVIS Agent and the UI Bridge backend simultaneously.
"""

import subprocess
import sys
import time


def main() -> None:
    # Ensure project root is in path for absolute imports
    import os
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

    # Ensure UTF-8 output for emojis on Windows
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("🚀 Starting JARVIS Environment...")

    bridge_process = None
    try:
        print("🌐 [1/2] Starting STONIX UI Bridge (Port 5001)...")
        # Ensure child processes can find 'src'
        import os
        env = os.environ.copy()
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env["PYTHONPATH"] = root_dir + os.pathsep + env.get("PYTHONPATH", "")

        # Start bridge in the background
        # pylint: disable=consider-using-with
        bridge_process = subprocess.Popen([sys.executable, "-m", "src.core.ui_bridge"], env=env)
        time.sleep(3) # Give bridge time to bind to port

        if bridge_process.poll() is not None:
            print("❌ UI Bridge failed to start!")
            sys.exit(1)

        print("🤖 [2/2] Starting JARVIS Agent Core...")
        # Run agent in foreground with updated env
        subprocess.run([sys.executable, "-m", "src.core.agent", "dev"], check=True, env=env)

    except KeyboardInterrupt:
        print("\n🛑 Shutting down JARVIS...")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Agent exited with error code {e.returncode}")
    finally:
        if bridge_process and bridge_process.poll() is None:
            print("🔌 Terminating UI Bridge...")
            bridge_process.terminate()
            bridge_process.wait()
            print("✅ Shutdown complete.")

if __name__ == "__main__":
    main()
