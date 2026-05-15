import asyncio
import functools
import time
import traceback
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_resilience import resilience_manager
from services.utils.jarvis_security import vortex_guard

logger = setup_logger("JARVIS-AUTONOMOUS")

_bg_tasks: set[asyncio.Task[Any]] = set()

class TaskProtector:
    """
    Manages background tasks with automatic recovery, failure analysis, and health tracking.
    """
    _tasks: dict[str, asyncio.Task[Any]]
    _task_factories: dict[str, tuple[Callable[..., Coroutine[Any, Any, Any]], tuple[Any, ...]]]
    _retry_counts: dict[str, int]
    _last_crash_info: dict[str, dict[str, Any]]
    _failure_threshold: int
    shutdown_signal: asyncio.Event
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._task_factories: dict[str, tuple[Callable[..., Coroutine[Any, Any, Any]], tuple[Any, ...]]] = {}
        self._retry_counts: dict[str, int] = {}
        self._last_crash_info: dict[str, dict[str, Any]] = {}
        self._failure_threshold = 10  # Critical threshold for alerting
        self.shutdown_signal = asyncio.Event() # For global shutdown coordination

    def is_task_healthy(self, name: str) -> bool:
        """Checks if a protected task is currently running."""
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def get_task(self, name: str) -> asyncio.Task[Any] | None:
        """Safely retrieve a protected task by name."""
        return self._tasks.get(name)

    @property
    def task_names(self) -> list[str]:
        """Returns list of currently managed task names."""
        return list(self._tasks.keys())

    async def run_protected(self, name: str, coro_func: Callable[..., Coroutine[Any, Any, Any]], *args: Any) -> None:
        """
        Runs a coroutine function as a background task.
        If it crashes, it will be automatically restarted with exponential backoff.
        """
        self._task_factories[name] = (coro_func, args)
        self._retry_counts[name] = 0

        # We wrap the underlying coro function in a resilient loop
        async def _resilient_loop() -> None:
            retry_delay = 1.0
            while True:
                try:
                    logger.info("🚀 Starting Protected Task: %s", name)
                    await coro_func(*args)
                    # If coro_func returns naturally, break the loop
                    break
                except asyncio.CancelledError:
                    logger.info("🛑 Task %s cancelled by system.", name)
                    break
                except Exception as e:
                    self._retry_counts[name] += 1
                    error_msg = str(e)
                    stack = traceback.format_exc()

                    self._last_crash_info[name] = {
                        "error": error_msg,
                        "time": time.time(),
                        "stack": stack,
                    }

                    logger.error("🚑 Protected Task %s Crashed! (Failures: %d)", name, self._retry_counts[name])
                    logger.error("Error Details: %s", error_msg)

                    # Self-Healing Trigger: If we exceed threshold, notify UI as critical
                    if self._retry_counts[name] >= self._failure_threshold:
                        from services.utils.jarvis_bridge import notify_log
                        # We use fire-and-forget for notification
                        t = asyncio.create_task(notify_log(
                            f"🛑 CRITICAL FAILURE: Task {name} failed {self._retry_counts[name]} times. Autonomous system attempting extended recovery.",
                            category="CRITICAL",
                        ))
                        _bg_tasks.add(t)
                        t.add_done_callback(_bg_tasks.discard)

                    # Exponential backoff: 1s, 2s, 4s, 8s, 16s... cap at 2 mins for standard, 5 mins max
                    retry_delay = min(retry_delay * 2.0, 120.0)
                    logger.info("🔄 Task %s will restart in %.1fs...", name, retry_delay)
                    await asyncio.sleep(retry_delay)

        # Create and track the task
        task = asyncio.create_task(_resilient_loop(), name=f"protected_{name}")
        self._tasks[name] = task

    def restart_task(self, name: str) -> bool:
        """Force manual restart of a registered task."""
        if name not in self._task_factories:
            return False

        coro_func, args = self._task_factories[name]
        task = self._tasks.get(name)
        if task and not task.done():
            task.cancel()

        t = asyncio.create_task(self.run_protected(name, coro_func, *args))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
        return True

    async def cancel_all(self) -> None:
        """Cancels all protected tasks and waits for completion."""
        logger.info("🛑 Cancelling all protected tasks...")
        real_tasks = []
        for task in self._tasks.values():
            if not isinstance(task, asyncio.Future):
                logger.debug("Skipping non-asyncio task during cleanup: %s", type(task))
                continue
            real_tasks.append(task)
            if not task.done():
                task.cancel()

        # Wait for all tasks to finish cancellation
        if real_tasks:
            await asyncio.gather(*real_tasks, return_exceptions=True)
        self._tasks.clear()

    def get_task_status(self) -> dict[str, dict[str, Any]]:
        """Returns diagnostic data for all protected tasks."""
        status = {}
        for name, task in self._tasks.items():
            status[name] = {
                "running": not task.done(),
                "failures": self._retry_counts.get(name, 0),
                "last_crash": self._last_crash_info.get(name),
            }
        return status

T = TypeVar("T")

def resilient_tool(tool_name: str) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator/Wrapper to make a tool self-defending and self-healing.
    - Uses VortexGuard for input validation.
    - Uses CircuitBreaker to prevent cascading failures.
    """
    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def _wrapper(*args: Any, **kwargs: Any) -> T:
            # 1. Self-Defense: Validate Tool Arguments
            all_args = {**kwargs}
            # Note: positional args are harder to map without inspect,
            # but tools usually use kwargs for LLM parameters.
            sanitized_args = vortex_guard.validate_tool_args(tool_name, all_args)

            # 2. Self-Healing: Execute through Circuit Breaker
            breaker = resilience_manager.get_breaker(tool_name)

            try:
                # We prioritize sanitized kwargs
                logger.debug("🛡️ Executing RESILIENT tool: %s", tool_name)
                return await breaker.call(func, *args, **sanitized_args)
            except Exception as e:
                logger.error("🚨 Resilient Tool FAIL: %s | Error: %s", tool_name, str(e))
                # Provide a structured failure response for the LLM
                return {"status": "error", "message": f"Circuit Breaker Triggered or Execution Failed: {e!s}"} # type: ignore

        return _wrapper
    return decorator


# Global Singleton for ease of use across modules
autonomous_protector = TaskProtector()
