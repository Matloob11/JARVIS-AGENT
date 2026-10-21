"""
# services/utils/jarvis_adaptive.py
The adaptive engine: the load tiers (analyze_system_load, cgroup-aware in a container) and the learning state
(conversations/learning_state.json: A/B results, intent weights, successful patterns).

Cross-process (C-125 step 1, K-77): every change of the learning state is one read-modify-write under the file lock
(services.utils.jarvis_filelock.locked, '<file>.lock'): the file is read again when another process changed it (its
mtime, inode or size differ from what this process last saw), the change is applied and the whole file is written
through a temp file and a replace. Readers (select_strategy, get_intent_adjustment) reload on such a change too.
File access goes through pathlib only (tests swap this module's os and time names).
"""
import asyncio
import json
import os
import random
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from services.utils.jarvis_filelock import locked
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-ADAPTIVE")

FileStamp = tuple[int, int, int]  # (st_mtime_ns, st_ino, st_size)

_CGROUP_ROOT = Path("/sys/fs/cgroup")
LOAD_WARN_REPEAT_S = 600.0  # UI-CLEAN: the same stress level is logged again at most every 10 min
_NO_LIMIT = 1 << 60  # cgroup v1 reports "no memory limit" as a huge number


def in_container() -> bool:
    """True inside a Docker/OCI container: Docker's /.dockerenv marker, or the 'container' variable that
    Podman and systemd-nspawn set."""
    return Path("/.dockerenv").exists() or bool(os.getenv("container"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_int(path: Path) -> int | None:
    try:
        return int(_read_text(path))
    except ValueError:  # missing file, or 'max'
        return None


def _host_memory_bytes() -> int | None:
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "MemTotal:" and parts[1].isdigit():
            return int(parts[1]) * 1024
    return None


def _cgroup_figures(root: Path) -> tuple[int | None, int | None, float | None, float]:
    """(memory bytes, memory limit bytes or None, CPU seconds used, CPUs the cgroup may use) from cgroup v2,
    else v1. Unreadable figures are None."""
    cpus = float(os.cpu_count() or 1)
    if (root / "cgroup.controllers").exists():
        memory = _read_int(root / "memory.current")
        limit = _read_int(root / "memory.max")
        cpu_s = None
        for line in _read_text(root / "cpu.stat").splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec" and value.strip().isdigit():
                cpu_s = int(value) / 1e6
        quota, _, period = _read_text(root / "cpu.max").partition(" ")
        if quota.isdigit() and period.strip().isdigit() and int(period) > 0:
            cpus = min(cpus, int(quota) / int(period))
    else:
        memory = _read_int(root / "memory" / "memory.usage_in_bytes")
        limit = _read_int(root / "memory" / "memory.limit_in_bytes")
        usage_ns = _read_int(root / "cpuacct" / "cpuacct.usage")
        cpu_s = usage_ns / 1e9 if usage_ns is not None else None
        quota_us = _read_int(root / "cpu" / "cpu.cfs_quota_us")
        period_us = _read_int(root / "cpu" / "cpu.cfs_period_us")
        if quota_us and quota_us > 0 and period_us:
            cpus = min(cpus, quota_us / period_us)
    if limit is not None and (limit <= 0 or limit >= _NO_LIMIT):
        limit = None
    return memory, limit, cpu_s, max(cpus, 0.01)


class JarvisAdaptiveEngine:
    """
    Autonomous Adaptive Learning Engine for JARVIS.
    Learns from user feedback and interaction history to refine behavior.
    """
    def __init__(self, data_path: str = "conversations/learning_state.json") -> None:
        """Initializes the adaptive engine with state path."""
        self._lock = threading.Lock()
        self.data_path: Path = Path(data_path)
        if self.data_path.parent:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self._stamp: FileStamp | None = None
        self.state: dict[str, Any] = self._load_state()
        self.stress_level: str = "NORMAL" # NORMAL, STRESSED, CRITICAL
        # K-72: in a container the engine reads only its own cgroup and never cancels loops (the host's
        # load belongs to other apps); on the PC it throttles as before.
        self.observe_only: bool = in_container()
        self.last_cgroup: dict[str, float] = {}
        self._cpu_sample: tuple[float, float] | None = None
        self._load_logged: tuple[str, float] | None = None  # (level, monotonic time) of the last load WARNING

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "intent_weights": {},
            "ab_results": {},
            "successful_patterns": [],
        }

    def _file_stamp(self) -> FileStamp | None:
        try:
            st = self.data_path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_ino, st.st_size)

    def _load_state(self) -> dict[str, Any]:
        """Loads persistent learning state."""
        if self.data_path.exists():
            stamp = self._file_stamp()
            try:
                with self.data_path.open(encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._stamp = stamp
                    state = self._default_state()
                    state.update(loaded)
                    return state
            except (OSError, json.JSONDecodeError):
                pass
            logger.warning("Failed to load learning state. resetting.")

        return self._default_state()

    def _refresh(self) -> None:
        """Reloads the learning state when another process changed the file since this one last read or wrote it
        (an unreadable file keeps the state in memory)."""
        stamp = self._file_stamp()
        if stamp is None or stamp == self._stamp:
            return
        try:
            with self.data_path.open(encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            state = self._default_state()
            state.update(loaded)
            self.state = state
            self._stamp = stamp
            logger.info("Learning state reloaded: the file changed on disk.")

    def _write_state(self) -> None:
        """Writes the state whole through a temp file and a replace; the caller holds both locks."""
        temp = self.data_path.with_name(f".{self.data_path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            temp.replace(self.data_path)
        except OSError as e:
            logger.error("Failed to save learning state: %s", type(e).__name__)
            try:
                temp.unlink()
            except OSError:
                pass
            return
        self._stamp = self._file_stamp()

    def _change(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        """One read-modify-write of the learning state: this process's lock, then the file lock (C-125 step 1);
        reload on a change by another process, apply change, write. A file lock that stays busy applies the change
        in memory only."""
        with self._lock:
            try:
                with locked(self.data_path):
                    self._refresh()
                    result = change(self.state)
                    self._write_state()
                    return result
            except TimeoutError:
                logger.error("Failed to save learning state: its lock stayed busy")
                return change(self.state)

    def _save_state(self) -> None:
        """Persists learning state with thread-safety (kept for older callers)."""
        with self._lock:
            try:
                with locked(self.data_path):
                    self._write_state()
            except TimeoutError:
                logger.error("Failed to save learning state: its lock stayed busy")

    def analyze_system_load(self) -> str:
        """
        AUTONOMOUS ADAPTATION: Detects system stress via task health, circuit states, and real-time vitals.
        Transitions system between performance tiers based on holistic health.
        """
        from services.utils.jarvis_autonomous import all_task_status
        from services.utils.jarvis_health import health_monitor
        from services.utils.jarvis_resilience import resilience_manager

        # 1. Component Level Health (C-33: the global protector plus every live job's own protector)
        status = all_task_status()
        failures = sum(s["failures"] for s in status.values())
        open_circuits = len(resilience_manager._breakers) # Simplified check for total breakers if needed
        # Actually resilience_manager doesn't have a get_open_circuits in the view I saw
        # Let's check the code again... wait, it used len(resilience_manager.get_open_circuits())
        # I should probably use what was there but add Vitals.

        open_circuits = len([b for b in resilience_manager._breakers.values() if b.state.value == "OPEN"])

        # 2. Resource Level Health (Vitals); in a container the cgroup's own figures, never the host's
        if self.observe_only:
            figures = self.cgroup_vitals()
            cpu_usage = figures["cpu_percent"]
            mem_usage = figures["memory_percent"]
            disk_usage = 0.0
        else:
            vitals = health_monitor.get_system_vitals()
            cpu_usage = vitals.get("cpu", {}).get("usage", 0.0)
            mem_usage = vitals.get("memory", {}).get("percent", 0.0)
            disk_usage = vitals.get("disk", {}).get("percent", 0.0)

        # 3. Decision Matrix — Windows hybrid desktops often sit at 70-85% RAM with browsers;
        # do not treat that alone as overload (it was canceling useful loops every 30s).
        if open_circuits > 5 or failures > 20 or cpu_usage > 95 or mem_usage > 95:
            self.stress_level = "CRITICAL"
        elif open_circuits > 2 or failures > 5 or cpu_usage > 90 or mem_usage > 92 or disk_usage > 97:
            self.stress_level = "STRESSED"
        else:
            self.stress_level = "NORMAL"

        if self.stress_level != "NORMAL":
            self._log_load(self.stress_level, cpu_usage, mem_usage, failures)
        elif self._load_logged is not None:
            self._load_logged = None
            logger.info("🧠 Load back to NORMAL: CPU:%.1f%% | MEM:%.1f%%", cpu_usage, mem_usage)

        return self.stress_level

    def _log_load(self, level: str, cpu_usage: float, mem_usage: float, failures: int) -> None:
        """UI-CLEAN: the load WARNING when the stress level changes, then at most every LOAD_WARN_REPEAT_S while it
        stays (a laptop near its RAM limit logged it every 30 s, and each one reached the dashboard's alerts)."""
        now = time.monotonic()
        last = self._load_logged
        if last is not None and last[0] == level and now - last[1] < LOAD_WARN_REPEAT_S:
            return
        self._load_logged = (level, now)
        logger.warning("🧠 Load Adaptation Triggered [%s]: CPU:%.1f%% | MEM:%.1f%% | FAIL:%d",
                       level, cpu_usage, mem_usage, failures)

    def cgroup_vitals(self, root: Path = _CGROUP_ROOT) -> dict[str, float]:
        """This container's own use: memory_mb, memory_percent (of the cgroup limit, or of host memory
        when no limit is set) and cpu_percent (of the CPUs the cgroup may use, since the previous call;
        0.0 on the first). Unreadable figures count as 0.0. The result is kept in last_cgroup."""
        memory, limit, cpu_s, cpus = _cgroup_figures(root)
        total = limit or _host_memory_bytes()
        memory_percent = 100.0 * memory / total if memory is not None and total else 0.0
        cpu_percent = 0.0
        now = time.monotonic()
        if cpu_s is not None:
            previous = self._cpu_sample
            if previous is not None and now > previous[1]:
                used = max(cpu_s - previous[0], 0.0)
                cpu_percent = min(100.0 * used / (now - previous[1]) / cpus, 100.0)
            self._cpu_sample = (cpu_s, now)
        self.last_cgroup = {
            "memory_mb": round((memory or 0) / (1024 * 1024), 1),
            "memory_percent": round(memory_percent, 1),
            "cpu_percent": round(cpu_percent, 1),
        }
        return self.last_cgroup

    def select_strategy(self, intent_name: str) -> str:
        """
        Selects a reasoning strategy (A or B) based on historical success.
        Used for autonomous A/B testing of behaviors.
        """
        strategies: list[str] = ["strategy_a", "strategy_b"]
        self._refresh()
        history: dict[str, float] = self.state["ab_results"].get(intent_name, {"a": 0.0, "b": 0.0})

        # 1. Exploration phase (if no data, randomize)
        if sum(history.values()) < 10:
            return random.choice(strategies)

        # 2. Exploitation phase (select best performing)
        return "strategy_a" if history["a"] >= history["b"] else "strategy_b"

    def record_feedback(self, intent_name: str, strategy: str, score: float) -> None:
        """Updates internal weights based on interaction score."""
        def change(state: dict[str, Any]) -> float:
            ab_results = state.setdefault("ab_results", {})
            if intent_name not in ab_results:
                ab_results[intent_name] = {"a": 0.0, "b": 0.0}

            strat_key: str = "a" if strategy == "strategy_a" else "b"
            ab_results[intent_name][strat_key] += score

            # Update General Intent Weight
            weights = state.setdefault("intent_weights", {})
            current_weight: float = float(weights.get(intent_name, 1.0))
            new_weight: float = current_weight + (0.05 * score)
            weights[intent_name] = max(0.2, min(2.0, new_weight))
            return float(weights[intent_name])

        weight = self._change(change)
        logger.info("Adaptive update for %s: Weight=%.2f", intent_name, weight)

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def log_interaction(self, _session_id: str, _user_input: str,
                               response: str | None, success: bool,
                               error_message: str | None = None) -> None:
        """Async helper to log interaction results."""
        pattern = {
            "has_response": response is not None,
            "error": error_message,
        }

        def change(state: dict[str, Any]) -> None:
            if success:
                patterns: list[dict[str, Any]] = cast(list[dict[str, Any]],
                                                      state.setdefault("successful_patterns", []))
                patterns.append(pattern)
                if len(patterns) > 1000:
                    patterns.pop(0)

        await asyncio.to_thread(self._change, change)

    def get_intent_adjustment(self, intent_name: str) -> float:
        """Returns a multiplier for intent confidence scoring."""
        self._refresh()
        return float(self.state["intent_weights"].get(intent_name, 1.0))


# Global Singleton
adaptive_engine: JarvisAdaptiveEngine = JarvisAdaptiveEngine()
