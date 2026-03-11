"""
# services/utils/jarvis_metrics.py
Handles system metrics collection (CPU, RAM, Track info) for JARVIS.
"""

import platform
import subprocess
import threading
import psutil
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-METRICS")


class MetricsCollector:
    """
    Collector for system and application performance metrics.
    """
    def __init__(self):
        """Initializes thresholds and state for metrics collection."""
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
            self.track = self._get_current_track()
            self.stop_event.wait(5)

    def _get_current_track(self) -> str:
        """Helper to fetch current track based on OS."""
        new_track = ""
        try:
            system = platform.system()
            if system == "Darwin":  # macOS Spotify integration
                new_track = self._get_macos_spotify_track()
        except (subprocess.SubprocessError, OSError) as e:
            logger.error("Track update error: %s", e)
        return new_track

    def _get_macos_spotify_track(self) -> str:
        """Mac-specific Spotify track retrieval."""
        try:
            # Check if Spotify is running
            proc = subprocess.run(
                ["pgrep", "-f", "MacOS/Spotify"],
                capture_output=True, text=True, check=False
            )
            if proc.returncode == 0:
                script = 'tell application "Spotify" to return artist of ' \
                         'current track & " - " & name of current track'
                track_proc = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, check=False
                )
                if track_proc.returncode == 0:
                    return track_proc.stdout.strip()
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            logger.debug("Spotify check failed: %s", e)
        return ""

    def get_metrics_dict(self):
        """Returns current metrics."""
        return {
            'cpu': self.cpu,
            'ram': self.ram,
            'track': self.track
        }
