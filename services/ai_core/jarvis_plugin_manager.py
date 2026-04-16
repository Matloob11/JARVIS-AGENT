"""
# jarvis_plugin_manager.py
Handles dynamic discovery and registration of AI tools.
"""

import asyncio
import functools
import importlib
import inspect
import pkgutil
import threading
from collections.abc import Callable
from typing import Any, Optional, TypeVar, cast

from livekit.agents import llm

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_resilience import resilience_manager
from services.utils.jarvis_security import vortex_guard
from services.utils.plugin_manifest import Permission, get_permissions_for_module

logger = setup_logger("PLUGIN-MANAGER")

F = TypeVar("F", bound=Callable[..., Any])

class JarvisPluginManager:
    """
    Manages the lifecycle of AI tools, including registration and discovery.
    """
    _instance: Optional['JarvisPluginManager'] = None
    _lock = threading.Lock()
    _tools_lock = threading.RLock()
    _initialized: bool = False

    def __new__(cls) -> 'JarvisPluginManager':
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize attributes only once for the singleton instance."""
        if not getattr(self, "_initialized", False):
            self.tools: list[Callable[..., Any]] = []
            self.discovered: bool = False
            self._initialized = True
            # Bounded concurrency to prevent OOM/RateLimit during stress
            self._tool_semaphore = asyncio.Semaphore(5)

    def register_tool(self, func: F, permissions: list[str] | None = None,
                      retry_attempts: int = 0, retry_delay: float = 1.0,
                      execution_timeout: float = 15.0) -> F:
        """Registers a function as a tool for autonomous discovery."""

        # Resolve required permissions
        required_perms = set()
        if permissions:
            for p in permissions:
                try:
                    required_perms.add(Permission(p))
                except ValueError as e:
                    logger.critical("❌ SECURITY ERROR: Invalid permission '%s' requested by '%s'", p, func.__name__)
                    raise RuntimeError(f"Tool registration failed: {p} is not a valid permission.") from e

        sig = inspect.signature(func)

        @functools.wraps(func)
        async def tool_wrapper(*args: Any, **kwargs: Any) -> Any:
            # SECURITY ENFORCEMENT
            module_name = getattr(func, "__module__", "unknown")
            allowed_perms = get_permissions_for_module(module_name)

            missing = required_perms - allowed_perms
            if missing:
                error_msg = f"SECURITY BLOCK: Tool '{func.__name__}' requires permissions {missing} which are not granted."
                logger.critical(error_msg)
                return error_msg

            # RECOVERY & RESILIENCE ENFORCEMENT
            breaker = resilience_manager.get_breaker(func.__name__, execution_timeout=execution_timeout)

            async def _execute_with_retries(*a: Any, **k: Any) -> Any:
                # Apply Tool Parameter Validation (Self-Defense)
                sanitized_k = vortex_guard.validate_tool_args(func.__name__, k)

                attempts = 0
                while True:
                    try:
                        async with self._tool_semaphore:
                            if asyncio.iscoroutinefunction(func):
                                return await func(*a, **sanitized_k)
                            return func(*a, **sanitized_k)
                    except asyncio.CancelledError:
                        logger.warning("Tool '%s' execution CANCELLED.", func.__name__)
                        raise
                    except (ValueError, KeyError, RuntimeError, TypeError, OSError) as e:
                        attempts += 1
                        if attempts <= retry_attempts:
                            logger.warning(
                                "Tool '%s' failed (Attempt %d/%d). Retrying in %.1fs... Error: %s",
                                func.__name__, attempts, retry_attempts + 1, retry_delay, e)
                            await asyncio.sleep(retry_delay)
                            continue
                        raise e

            try:
                # Wrap execution in Circuit Breaker (Self-Healing)
                raw_result = await breaker.call(_execute_with_retries, *args, **kwargs)

                # Apply Tool Output Validation (Self-Defense)
                return vortex_guard.validate_tool_output(func.__name__, raw_result)
            except Exception as e:
                error_summary = f"Tool '{func.__name__}' failure: {e!s}"
                logger.error(error_summary, exc_info=True)
                # Standardized JSON-like error response for stable agent parsing
                return {
                    "status": "error",
                    "tool": func.__name__,
                    "error_type": type(e).__name__,
                    "message": str(e),
                }

        # Preserving original name and signature for LLM understanding
        tool_wrapper.__is_jarvis_tool__ = True
        tool_wrapper.__signature__ = sig

        # Store as a raw function. We will convert to LiveKit tools on demand.
        with self._tools_lock:
            if not hasattr(self, "tools"):
                self.tools = []  # pylint: disable=attribute-defined-outside-init

            # Prevent duplicates
            for existing in self.tools:
                if getattr(existing, "__name__", "") == func.__name__:
                    logger.debug(
                        "Tool '%s' already registered, skipping.", func.__name__)
                    return func

            self.tools.append(tool_wrapper)
        logger.debug("Registered tool: %s", func.__name__)
        return func

    def get_tools(self) -> list[Callable[..., Any]]:
        """Returns all registered raw tools."""
        return getattr(self, "tools", [])

    def get_livekit_tools(self) -> list[llm.FunctionTool]:
        """Converts raw tools into LiveKit-compatible FunctionTool objects."""
        lk_tools: list[llm.FunctionTool] = []
        with self._tools_lock:
            tools = getattr(self, "tools", [])

            for tool_func in tools:
                # Check for generic wrapper name and try to get the original function name
                func_name = getattr(tool_func, "__name__", "unknown")
                if func_name == "tool_wrapper" and hasattr(tool_func, "__wrapped__"):
                    func_name = tool_func.__wrapped__.__name__

                logger.info("Converting tool to LiveKit format: %s", func_name)

                try:
                    # Use the factory function to create a tool
                    # In this LiveKit version, llm.function_tool(func) is used directly
                    lk_tool = llm.function_tool(tool_func)
                    lk_tools.append(lk_tool)
                except (ValueError, TypeError, AttributeError, KeyError) as e:
                    logger.error("Failed to register tool '%s': %s", func_name, e)
                    # If it's the 'self' error, we definitely want to know which tool it is
                    if "self" in str(e) or isinstance(e, KeyError):
                        logger.critical(
                            "CRITICAL: Tool '%s' has unhinted 'self' or signature issues! Context: %s", func_name, tool_func)
        return lk_tools


    def discover_plugins(self, package_path: str) -> None:
        """Recursively discovers and imports plugins synchronously."""
        with self._tools_lock:
            if self.discovered:
                return
            self.discovered = True

        def _scan() -> None:
            for _, module_name, _ in pkgutil.walk_packages([package_path], "services."):
                try:
                    importlib.import_module(module_name)
                    logger.debug("Discovered module: %s", module_name)
                except (ImportError, ValueError, AttributeError, RuntimeError) as e:
                    logger.warning("Failed to load module %s: %s", module_name, e)

        _scan()
        logger.info("Plugin discovery complete. Total tools: %d", len(self.tools))


plugin_manager = JarvisPluginManager()


def jarvis_tool(permissions: list[str] | Callable[..., Any] | None = None,
                retry_attempts: int = 0, retry_delay: float = 1.0,
                execution_timeout: float = 15.0) -> Any:
    """
    Decorator to register a function as a Jarvis AI tool.
    Supports optional retries for delicate operations like API calls and permission controls.
    Usage:
    @jarvis_tool
    OR
    @jarvis_tool(permissions=["filesystem:read"], retry_attempts=3)
    """
    if callable(permissions):
        # Case where @jarvis_tool is used without parentheses
        func = cast(Callable[..., Any], permissions)
        return plugin_manager.register_tool(func)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # 'permissions' is either None or a List[str] because of 'if callable(permissions)' guard above
        perms = cast(list[str] | None, permissions)
        return plugin_manager.register_tool(func, permissions=perms,
                                            retry_attempts=retry_attempts,
                                            retry_delay=retry_delay,
                                            execution_timeout=execution_timeout)

    return decorator
