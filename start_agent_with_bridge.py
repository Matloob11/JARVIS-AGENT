"""
# start_agent_with_bridge.py
Helper script to start both the JARVIS Agent and the UI Bridge backend simultaneously.
"""

import subprocess
import sys
import time

def main():
    print("🚀 Starting JARVIS Environment...")

    # Ensure UTF-8 output
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    bridge_process = None
    try:
        print("🌐 [1/2] Starting STONIX UI Bridge (Port 5001)...")
        # Start bridge in the background
        bridge_process = subprocess.Popen([sys.executable, "ui_bridge.py"])
        time.sleep(3) # Give bridge time to bind to port

        if bridge_process.poll() is not None:
            print("❌ UI Bridge failed to start!")
            sys.exit(1)

        print("🤖 [2/2] Starting JARVIS Agent Core...")
        # Run agent in foreground
        subprocess.run([sys.executable, "agent.py", "dev"], check=True)

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
