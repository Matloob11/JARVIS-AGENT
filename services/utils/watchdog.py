"""
# services/utils/watchdog.py
Process supervisor for critical JARVIS services.
Ensures that if the Bridge or Runner crashes, they are automatically restarted.
"""

import time
import subprocess
import signal
import sys
from services.utils.jarvis_logger import setup_logger

log = setup_logger("JARVIS-WATCHDOG")


class JarvisWatchdog:
    """
    Process supervisor for critical JARVIS services.
    Ensures that if the Bridge or Runner crashes, they are automatically restarted.
    """

    def __init__(self):
        """Initializes the watchdog with default services."""
        self.processes = {}
        self.services = {
            "UI_BRIDGE": ["python", "ui_bridge.py"],
            "AGENT_RUNNER": ["python", "agent_runner.py"]
        }
        self.running = True

    def start_service(self, name):
        """Starts a specified service and tracks its process."""
        cmd = self.services[name]
        log.info("[START] Starting service: %s (%s)", name, " ".join(cmd))
        try:
            # We use subprocess.Popen to start services in the background
            with subprocess.Popen(cmd) as proc:  # pylint: disable=consider-using-with
                self.processes[name] = proc
                return proc
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            log.error("[ERROR] Failed to start %s: %s", name, e)
            return None

    def monitor(self):
        """Main loop to monitor and restart crashed services."""
        log.info("[WATCHDOG] Monitoring active. Press Ctrl+C to stop.")

        # Initial startup
        for name in self.services:
            self.start_service(name)

        while self.running:
            try:
                for name, proc in list(self.processes.items()):
                    if proc.poll() is not None:
                        # Process exited
                        exit_code = proc.returncode
                        log.warning("[CRASH] Service %s exited with code %s. Restarting...",
                                    name, exit_code)
                        self.start_service(name)

                time.sleep(5)  # Check every 5 seconds
            except KeyboardInterrupt:
                self.stop_all()
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
                log.error("[ERROR] Watchdog Error loop: %s", e)

    def stop_all(self):
        """Terminates all managed services and exits."""
        log.info("[STOP] Stopping all services...")
        self.running = False
        for name, proc in self.processes.items():
            log.info("Terminating %s...", name)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("Force killing %s...", name)
                proc.kill()
        sys.exit(0)


if __name__ == "__main__":
    watchdog_instance = JarvisWatchdog()

    # Handler for system signals
    def handle_signal(sig, _frame):
        """Signal handler to ensure clean shutdown."""
        log.info("Received signal %s, shutting down...", sig)
        watchdog_instance.stop_all()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    watchdog_instance.monitor()
