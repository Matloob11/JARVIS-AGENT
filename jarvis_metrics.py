"""
# jarvis_metrics.py
Handles system metrics collection (CPU, RAM, Track info) for JARVIS.
"""

import platform
import subprocess
import psutil
import threading
from jarvis_logger import setup_logger

logger = setup_logger("JARVIS-METRICS")


class MetricsCollector:
    def __init__(self):
        self.cpu = 0
        self.ram = 0
        self.track = ""
        self.running = True
        self.stop_event = threading.Event()

    def start(self):
        """Starts metric collection threads."""
        threading.Thread(target=self._update_metrics_loop, daemon=True).start()
        threading.Thread(target=self._update_track_loop, daemon=True).start()

    def stop(self):
        """Stops metric collection."""
        self.stop_event.set()
        self.running = False

    def _update_metrics_loop(self):
        """Updates CPU/RAM metrics periodically."""
        while not self.stop_event.is_set():
            self.cpu = psutil.cpu_percent(interval=1)
            self.ram = psutil.virtual_memory().percent

    def _update_track_loop(self):
        """Updates currently playing track info safely."""
        while not self.stop_event.is_set():
            new_track = ""
            try:
                system = platform.system()
                if system == "Windows":
                    # Future implementation for Windows media info
                    pass
                elif system == "Darwin":  # macOS
                    # Spotify check
                    # Safer subprocess call without shell=True
                    try:
                        # Use pgrep or ps to find spotify
                        proc = subprocess.run(
                            ["pgrep", "-f", "MacOS/Spotify"], capture_output=True, text=True, check=False)  # nosec B607
                        if proc.returncode == 0:
                            # Get track via osascript
                            track_proc = subprocess.run([
                                "osascript", "-e",
                                'tell application "Spotify" to return artist of current track & " - " & name of current track'
                            ], capture_output=True, text=True, check=False)  # nosec B607
                            if track_proc.returncode == 0:
                                new_track = track_proc.stdout.strip()
                    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
                        logger.debug("Spotify check failed: %s", e)
            except (subprocess.SubprocessError, OSError) as e:
                logger.error("Track update error: %s", e)

            self.track = new_track
            self.stop_event.wait(5)

    def get_metrics_dict(self):
        """Returns current metrics."""
        return {
            'cpu': self.cpu,
            'ram': self.ram,
            'track': self.track
        }
