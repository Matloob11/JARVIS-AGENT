"""
# services/utils/jarvis_health.py
Real-time system health monitor for JARVIS.
Tracks resource usage, process status, and service heartbeats.
"""

import os
import time
from typing import Dict, Any
import psutil
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-HEALTH")


class JarvisHealthMonitor:
    """
    Real-time system health monitor for JARVIS.
    Tracks resource usage, process status, and service heartbeats.
    """
    def __init__(self):
        """Initializes thresholds and start time."""
        self.start_time = time.time()
        self.heartbeats = {
            "ui_bridge": 0,
            "agent_runner": 0,
            "backend_core": time.time()
        }
        self.thresholds = {
            "cpu_percent": 85.0,
            "mem_percent": 90.0,
            "disk_percent": 95.0
        }

    def get_system_vitals(self) -> Dict[str, Any]:
        """Collects current system resource metrics."""
        try:
            return {
                "cpu": {
                    "usage": psutil.cpu_percent(),
                    "cores": psutil.cpu_count(),
                    "freq": psutil.cpu_freq().current if psutil.cpu_freq() else 0
                },
                "memory": {
                    "total": psutil.virtual_memory().total,
                    "available": psutil.virtual_memory().available,
                    "percent": psutil.virtual_memory().percent
                },
                "disk": {
                    "percent": psutil.disk_usage('C:' if os.name == 'nt' else '/').percent,
                    "free": psutil.disk_usage('C:' if os.name == 'nt' else '/').free
                },
                "uptime": time.time() - self.start_time
            }
        except (psutil.Error, OSError, ValueError) as e:
            logger.error("Failed to collect vitals: %s", e)
            return {}

    def record_heartbeat(self, service_name: str):
        """Records a heartbeat from a specific service."""
        if service_name in self.heartbeats:
            self.heartbeats[service_name] = time.time()

    def get_service_status(self) -> Dict[str, str]:
        """Calculates status based on last heartbeat."""
        now = time.time()
        status = {}
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

    def check_anomalies(self) -> list:
        """Identifies potential issues based on thresholds."""
        vitals = self.get_system_vitals()
        anomalies = []

        if vitals.get("cpu", {}).get("usage", 0) > self.thresholds["cpu_percent"]:
            anomalies.append(f"High CPU Usage: {vitals['cpu']['usage']}%")

        if vitals.get("memory", {}).get("percent", 0) > self.thresholds["mem_percent"]:
            anomalies.append(f"Low Memory: {vitals['memory']['percent']}%")

        status = self.get_service_status()
        for svc, state in status.items():
            if state in ["OFFLINE", "DEGRADED"]:
                anomalies.append(f"Service {svc} is {state}")

        return anomalies

    def generate_health_report(self) -> Dict[str, Any]:
        """Compiles a complete health overview."""
        cur_anomalies = self.check_anomalies()
        return {
            "timestamp": time.time(),
            "vitals": self.get_system_vitals(),
            "services": self.get_service_status(),
            "anomalies": cur_anomalies,
            "status": "CRITICAL" if any("OFFLINE" in a for a in cur_anomalies) else "STABLE"
        }


# Global Singleton
health_monitor = JarvisHealthMonitor()
