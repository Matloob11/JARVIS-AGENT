"""
J.A.R.V.I.S Performance Monitoring System
Real-time performance metrics and health monitoring
"""
import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import psutil

from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MONITORING")


@dataclass
class SystemMetrics:
    """System performance metrics"""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_usage_percent: float
    disk_free_gb: float
    network_io_sent_mb: float
    network_io_recv_mb: float
    active_connections: int
    process_count: int


@dataclass
class ApplicationMetrics:
    """Application-specific metrics"""
    timestamp: float
    response_time_ms: float
    request_count: int
    error_count: int
    active_sessions: int
    memory_leaks_detected: bool
    avg_processing_time: float
    queue_size: int


@dataclass
class LLMMetrics:
    """LLM-specific metrics"""
    timestamp: float
    tokens_processed: int
    avg_response_time: float
    cache_hit_rate: float
    model_load_time: float
    api_calls_made: int
    api_errors: int


class PerformanceMonitor:
    """
    Advanced performance monitoring for JARVIS
    Tracks system, application, and LLM metrics
    """

    def __init__(self,
                 max_history_size: int = 1000,
                 monitoring_interval: float = 5.0,
                 enable_alerts: bool = True):
        """
        Initialize performance monitor

        Args:
            max_history_size: Maximum number of metrics to keep in memory
            monitoring_interval: Interval between metric collection (seconds)
            enable_alerts: Enable performance alerts
        """
        self.max_history_size = max_history_size
        self.monitoring_interval = monitoring_interval
        self.enable_alerts = enable_alerts

        # Metric storage
        self.system_metrics: deque[SystemMetrics] = deque(maxlen=max_history_size)
        self.app_metrics: deque[ApplicationMetrics] = deque(maxlen=max_history_size)
        self.llm_metrics: deque[LLMMetrics] = deque(maxlen=max_history_size)

        # Performance counters
        self.request_count = 0
        self.error_count = 0
        self.llm_tokens_processed = 0
        self.llm_api_calls = 0
        self.llm_api_errors = 0

        # Alert thresholds
        self.cpu_alert_threshold = 80.0
        self.memory_alert_threshold = 85.0
        self.disk_alert_threshold = 90.0
        self.response_time_alert_threshold = 5000.0  # 5 seconds

        # Monitoring state
        self._monitoring: bool = False
        self._monitor_thread: threading.Thread | None = None
        self._start_time: float = time.time()

        # Performance callbacks
        self._alert_callbacks: list[Callable[[str], None]] = []

        # Previous network stats for delta calculation
        self._prev_network_io: Any | None = None

        logger.info("📊 Performance Monitor initialized")

    def start_monitoring(self) -> None:
        """Start background monitoring"""
        if self._monitoring:
            logger.warning("⚠️ Monitoring already running")
            return

        self._monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("📊 Performance monitoring started")

    def stop_monitoring(self) -> None:
        """Stop background monitoring"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10)
        logger.info("📊 Performance monitoring stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop"""
        while self._monitoring:
            try:
                # Collect system metrics
                system_metric = self._collect_system_metrics()
                self.system_metrics.append(system_metric)

                # Check for alerts
                if self.enable_alerts:
                    self._check_system_alerts(system_metric)

                # Collect application metrics
                app_metric = self._collect_application_metrics()
                self.app_metrics.append(app_metric)

                # Collect LLM metrics
                llm_metric = self._collect_llm_metrics()
                self.llm_metrics.append(llm_metric)

                time.sleep(self.monitoring_interval)

            except Exception as e: # pylint: disable=broad-exception-caught
                logger.error("❌ Monitoring error: %s", e)
                time.sleep(self.monitoring_interval)

    def _collect_system_metrics(self) -> SystemMetrics:
        """Collect system performance metrics"""
        # CPU and Memory
        cpu_percent: float = cast(float, psutil.cpu_percent(interval=1))
        memory = psutil.virtual_memory()

        # Cross-platform disk check
        root_path = "C:/" if Path("C:/").exists() else "/"
        disk = psutil.disk_usage(root_path)

        # Network I/O
        network_io = psutil.net_io_counters()
        network_sent_mb = network_io.bytes_sent / (1024 * 1024)
        network_recv_mb = network_io.bytes_recv / (1024 * 1024)

        # Network connections and processes
        connections = len(psutil.net_connections())
        processes = len(psutil.pids())

        return SystemMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            memory_used_mb=memory.used / (1024 * 1024),
            memory_available_mb=memory.available / (1024 * 1024),
            disk_usage_percent=disk.percent,
            disk_free_gb=disk.free / (1024 * 1024 * 1024),
            network_io_sent_mb=network_sent_mb,
            network_io_recv_mb=network_recv_mb,
            active_connections=connections,
            process_count=processes,
        )

    def _collect_application_metrics(self) -> ApplicationMetrics:
        """Collect application-specific metrics"""
        # Calculate average response time from recent metrics
        recent_response_times = [
            m.response_time_ms for m in list(self.app_metrics)[-10:]
        ]
        avg_response_time = sum(recent_response_times) / len(recent_response_times) if recent_response_times else 0.0

        # Memory leak detection (simplified)
        memory_leaks_detected = self._detect_memory_leaks()

        return ApplicationMetrics(
            timestamp=time.time(),
            response_time_ms=avg_response_time,
            request_count=self.request_count,
            error_count=self.error_count,
            active_sessions=self._get_active_sessions(),
            memory_leaks_detected=memory_leaks_detected,
            avg_processing_time=avg_response_time,
            queue_size=self._get_queue_size(),
        )

    def _collect_llm_metrics(self) -> LLMMetrics:
        """Collect LLM-specific metrics"""
        return LLMMetrics(
            timestamp=time.time(),
            tokens_processed=self.llm_tokens_processed,
            avg_response_time=self._get_llm_avg_response_time(),
            cache_hit_rate=self._get_cache_hit_rate(),
            model_load_time=self._get_model_load_time(),
            api_calls_made=self.llm_api_calls,
            api_errors=self.llm_api_errors,
        )

    def _detect_memory_leaks(self) -> bool:
        """Simple memory leak detection"""
        if len(self.system_metrics) < 10:
            return False

        # Check if memory usage has been consistently increasing
        recent_memory = [m.memory_percent for m in list(self.system_metrics)[-10:]]
        memory_trend = sum(recent_memory[i+1] > recent_memory[i] for i in range(len(recent_memory)-1))

        # If memory increased in 8 out of last 10 measurements, potential leak
        return memory_trend >= 8

    def _get_active_sessions(self) -> int:
        """Get number of active sessions"""
        # This would be implemented based on actual session management
        return 0

    def _get_queue_size(self) -> int:
        """Get current queue size"""
        # This would be implemented based on actual queue management
        return 0

    def _get_llm_avg_response_time(self) -> float:
        """Get average LLM response time"""
        if len(self.llm_metrics) == 0:
            return 0
        return sum(m.avg_response_time for m in self.llm_metrics) / len(self.llm_metrics)

    def _get_cache_hit_rate(self) -> float:
        """Get cache hit rate"""
        # This would be implemented based on actual cache implementation
        return 0.0

    def _get_model_load_time(self) -> float:
        """Get model load time"""
        # This would be implemented based on actual model loading
        return 0.0

    def _check_system_alerts(self, metrics: SystemMetrics) -> None:
        """Check for system performance alerts"""
        alerts = []

        if metrics.cpu_percent > self.cpu_alert_threshold:
            alerts.append(f"High CPU usage: {metrics.cpu_percent:.1f}%")

        if metrics.memory_percent > self.memory_alert_threshold:
            alerts.append(f"High memory usage: {metrics.memory_percent:.1f}%")

        if metrics.disk_usage_percent > self.disk_alert_threshold:
            alerts.append(f"Low disk space: {metrics.disk_free_gb:.1f}GB free")

        # Trigger alert callbacks
        for alert in alerts:
            self._trigger_alert(alert)

    def _trigger_alert(self, message: str) -> None:
        """Trigger performance alert"""
        logger.warning("🚨 Performance Alert: %s", message)

        for callback in self._alert_callbacks:
            try:
                callback(message)
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.error("❌ Alert callback error: %s", e)

    def record_request(self, response_time_ms: float, success: bool = True) -> None:
        """Record a request for application metrics"""
        self.request_count += 1
        if not success:
            self.error_count += 1

        # Update average response time
        if len(self.app_metrics) > 0:
            latest_metric = self.app_metrics[-1]
            latest_metric.response_time_ms = response_time_ms

    def record_llm_interaction(self, tokens: int, response_time_ms: float, success: bool = True) -> None:
        """Record LLM interaction"""
        self.llm_tokens_processed += tokens
        self.llm_api_calls += 1
        if not success:
            self.llm_api_errors += 1

    def add_alert_callback(self, callback: Callable[[str], None]) -> None:
        """Add alert callback function"""
        self._alert_callbacks.append(callback)

    def get_current_metrics(self) -> dict[str, Any]:
        """Get current performance metrics"""
        return {
            "system": asdict(self.system_metrics[-1]) if self.system_metrics else {},
            "application": asdict(self.app_metrics[-1]) if self.app_metrics else {},
            "llm": asdict(self.llm_metrics[-1]) if self.llm_metrics else {},
            "uptime_seconds": float(time.time() - self._start_time),
        }

    def get_metrics_history(self, minutes: int = 60) -> dict[str, list[dict[str, Any]]]:
        """Get metrics history for specified time period"""
        cutoff_time: float = time.time() - (minutes * 60)

        def filter_metrics(metrics: deque[Any]) -> list[dict[str, Any]]:
            return [asdict(m) for m in metrics if m.timestamp >= cutoff_time]

        return {
            "system": filter_metrics(self.system_metrics),
            "application": filter_metrics(self.app_metrics),
            "llm": filter_metrics(self.llm_metrics),
        }

    def get_performance_summary(self) -> dict[str, Any]:
        """Get performance summary"""
        if not self.system_metrics:
            return {"status": "No data available"}

        latest_system = self.system_metrics[-1]
        latest_app = self.app_metrics[-1] if self.app_metrics else None
        # latest_llm = self.llm_metrics[-1] if self.llm_metrics else None (Removed as unused)

        summary = {
            "status": "healthy",
            "uptime_hours": (time.time() - self._start_time) / 3600,
            "system": {
                "cpu_percent": latest_system.cpu_percent,
                "memory_percent": latest_system.memory_percent,
                "disk_percent": latest_system.disk_usage_percent,
            },
            "application": {
                "total_requests": self.request_count,
                "error_rate": (self.error_count / self.request_count * 100) if self.request_count > 0 else 0,
                "avg_response_time": latest_app.response_time_ms if latest_app else 0,
            },
            "llm": {
                "total_tokens": self.llm_tokens_processed,
                "api_calls": self.llm_api_calls,
                "api_error_rate": (self.llm_api_errors / self.llm_api_calls * 100) if self.llm_api_calls > 0 else 0,
            },
        }

        # Determine overall status
        if (latest_system.cpu_percent > 90 or
            latest_system.memory_percent > 90 or
            latest_system.disk_usage_percent > 95):
            summary["status"] = "critical"
        elif (latest_system.cpu_percent > 70 or
              latest_system.memory_percent > 80 or
              latest_system.disk_usage_percent > 85):
            summary["status"] = "warning"

        return summary

    def export_metrics(self, filepath: str | Path, minutes: int = 60) -> None:
        """Export metrics to file"""
        metrics_data = {
            "export_timestamp": datetime.now().isoformat(),
            "summary": self.get_performance_summary(),
            "history": self.get_metrics_history(minutes),
        }

        path = Path(filepath)
        with path.open("w", encoding="utf-8") as f:
            json.dump(metrics_data, f, indent=2)

        logger.info("📊 Metrics exported to %s", path)


# Global performance monitor instance
_performance_monitor: PerformanceMonitor | None = None

def get_performance_monitor() -> PerformanceMonitor:
    """Get global performance monitor instance"""
    global _performance_monitor # pylint: disable=global-statement
    if _performance_monitor is None:
        _performance_monitor = PerformanceMonitor()
    return _performance_monitor

def start_monitoring() -> None:
    """Start global performance monitoring"""
    monitor = get_performance_monitor()
    monitor.start_monitoring()

def stop_monitoring() -> None:
    """Stop global performance monitoring"""
    monitor = get_performance_monitor()
    monitor.stop_monitoring()
