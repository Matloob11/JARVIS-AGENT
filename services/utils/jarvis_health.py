import time
from pathlib import Path
from typing import Any

import psutil

from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-HEALTH")


class JarvisHealthMonitor:
    """
    Real-time system health monitor for JARVIS.
    Tracks resource usage, process status, and service heartbeats.
    """
    def __init__(self) -> None:
        """Initializes thresholds and start time."""
        self.start_time: float = time.time()
        self.heartbeats: dict[str, float] = {
            "ui_bridge": 0.0,
            "agent_runner": 0.0,
            "backend_core": time.time(),
            "vector_db": 0.0,
            "autonomous_protector": time.time(),
        }
        self.thresholds: dict[str, float] = {
            "cpu_percent": 85.0,
            "mem_percent": 90.0,
            "disk_percent": 95.0,
        }

    def get_system_vitals(self) -> dict[str, Any]:
        """Collects current system resource metrics."""
        try:
            cpu_freq = psutil.cpu_freq()

            # Disk check logic using pathlib
            is_windows = Path("C:/").exists()
            c_path = "C:/" if is_windows else "/"
            d_path = Path("D:/")
            has_d = d_path.exists() and is_windows

            return {
                "cpu": {
                    "usage": psutil.cpu_percent(),
                    "cores": psutil.cpu_count(),
                    "freq": cpu_freq.current if cpu_freq else 0,
                },
                "memory": {
                    "total": psutil.virtual_memory().total,
                    "available": psutil.virtual_memory().available,
                    "percent": psutil.virtual_memory().percent,
                },
                "disk": {
                    "c_percent": psutil.disk_usage(c_path).percent,
                    "d_percent": psutil.disk_usage("D:").percent if has_d else 0.0,
                    "percent": psutil.disk_usage("D:").percent if has_d else psutil.disk_usage(c_path).percent,
                },
                "uptime": time.time() - self.start_time,
            }
        except (psutil.Error, OSError, ValueError) as e:
            logger.error("Failed to collect vitals: %s", e)
            return {}

    def record_heartbeat(self, service_name: str) -> None:
        """Records a heartbeat from a specific service."""
        if service_name in self.heartbeats:
            self.heartbeats[service_name] = time.time()

    def get_service_status(self) -> dict[str, str]:
        """Calculates status based on last heartbeat."""
        now: float = time.time()
        status: dict[str, str] = {}
        for service, last_ping in self.heartbeats.items():
            if last_ping == 0:
                status[service] = "INITIALIZING"
            elif now - last_ping > 30:
                status[service] = "OFFLINE"
            elif now - last_ping > 10:
                status[service] = "DEGRADED"
            else:
                status[service] = "HEALTHY"
        return status

    def check_anomalies(self) -> list[str]:
        """Identifies potential issues based on thresholds."""
        vitals: dict[str, Any] = self.get_system_vitals()
        anomalies: list[str] = []

        cpu_usage: Any = vitals.get("cpu", {}).get("usage", 0.0)
        if cpu_usage > self.thresholds["cpu_percent"]:
            anomalies.append(f"High CPU Usage: {cpu_usage}%")

        mem_percent: Any = vitals.get("memory", {}).get("percent", 0.0)
        if mem_percent > self.thresholds["mem_percent"]:
            anomalies.append(f"Low Memory: {mem_percent}%")

        status: dict[str, str] = self.get_service_status()
        for svc, state in status.items():
            if state in ["OFFLINE", "DEGRADED"]:
                anomalies.append(f"Service {svc} is {state}")

        return anomalies

    def generate_health_report(self) -> dict[str, Any]:
        """Compiles a complete health overview."""
        cur_anomalies: list[str] = self.check_anomalies()
        report: dict[str, Any] = {
            "timestamp": time.time(),
            "vitals": self.get_system_vitals(),
            "services": self.get_service_status(),
            "anomalies": cur_anomalies,
        }

        # Add autonomous task status if possible
        try:
            from services.utils.jarvis_autonomous import autonomous_protector
            report["protected_tasks"] = autonomous_protector.get_task_status()
        except ImportError:
            pass

        report["status"] = "CRITICAL" if any("OFFLINE" in a for a in cur_anomalies) else "STABLE"
        return report


# Global Singleton
health_monitor: JarvisHealthMonitor = JarvisHealthMonitor()
