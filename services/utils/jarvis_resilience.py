"""
# services/utils/jarvis_resilience.py
Resilience utilities for JARVIS, including Circuit Breakers and Fallback handlers.
"""

import asyncio
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, TypeVar

from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-RESILIENCE")

T = TypeVar("T")

class CircuitState(Enum):
    CLOSED = "CLOSED"  # Normal operation
    OPEN = "OPEN"      # Failure mode, fast-failing
    HALF_OPEN = "HALF_OPEN" # Testing if recovery happened

class CircuitBreaker:
    """
    Prevents cascading failures by stopping calls to failing services.
    """
    def __init__(
        self,
        name: str,
        fail_threshold: int = 5,
        recovery_timeout: float = 30.0,
        rate_limit: float = 10.0,  # Max calls per second
        execution_timeout: float = 15.0, # Max time per call
    ) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.recovery_timeout = recovery_timeout
        self.execution_timeout = execution_timeout

        # Rate Limiting (Token Bucket)
        self.rate_limit = rate_limit
        self.tokens = rate_limit
        self.last_refill = time.time()

        self.state = CircuitState.CLOSED
        self.fail_count = 0
        self.last_failure_time = 0.0

    async def call(self, coro_func: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any) -> T:
        """Executes the coroutine with circuit breaker protection."""

        # 1. Check Circuit State (Self-Healing)
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                logger.warning("🔄 Circuit %s HALF-OPEN: Testing recovery...", self.name)
                self.state = CircuitState.HALF_OPEN
            else:
                raise RuntimeError(f"🚨 Circuit {self.name} is OPEN. Blocking call.")

        # 2. Check Rate Limit (Self-Defending / Abuse Prevention)
        now = time.time()
        # Refill tokens
        time_elapsed = now - self.last_refill
        self.tokens = min(self.rate_limit, self.tokens + (time_elapsed * self.rate_limit))
        self.last_refill = now

        if self.tokens < 1.0:
            logger.warning("⚠️ Rate Limit Triggered for tool: %s", self.name)
            raise RuntimeError(f"ABUSE_BLOCK: Too many requests for {self.name}. Please wait.")

        self.tokens -= 1.0

        try:
            # 3. Execution with STRICT TIMEOUT (Chaos Resilience)
            result = await asyncio.wait_for(
                coro_func(*args, **kwargs),
                timeout=self.execution_timeout,
            )

            # Successful call, reset monitoring
            if self.state == CircuitState.HALF_OPEN:
                logger.info("✅ Circuit %s CLOSED: Recovery confirmed.", self.name)
                self.state = CircuitState.CLOSED
                self.fail_count = 0

            return result
        except (TimeoutError, Exception) as e:
            self.fail_count += 1
            self.last_failure_time = time.time()

            error_type = "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "FAILURE"
            if self.state == CircuitState.HALF_OPEN or self.fail_count >= self.fail_threshold:
                logger.error("🛑 Circuit %s OPENED! Threshold reached. (Reason: %s | Error: %s)",
                             self.name, error_type, str(e))
                self.state = CircuitState.OPEN

            if isinstance(e, asyncio.TimeoutError):
                raise RuntimeError(f"TOOL_TIMEOUT: {self.name} failed to respond in {self.execution_timeout}s.") from e
            raise e

class ResilienceManager:
    """Registry for managing multiple circuit breakers."""
    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name)
        return self._breakers[name]

# Global Singleton
resilience_manager = ResilienceManager()
