"""
# jarvis_plugin_manager.py
Handles dynamic discovery and registration of AI tools.
"""

import functools
import importlib
import pkgutil
import time
from typing import Callable, Any, List
from livekit.agents import llm
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("PLUGIN-MANAGER")


class JarvisPluginManager:
    """
    Manages the lifecycle of AI tools, including registration and discovery.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(JarvisPluginManager, cls).__new__(cls)
            # Initialize attributes safely
            cls._instance.tools = []
            cls._instance.discovered = False
        return cls._instance

    def __init__(self):
        """Attributes are handled in __new__ for singleton consistency."""
        if not hasattr(self, "tools"):
            self.tools = []
        if not hasattr(self, "discovered"):
            self.discovered = False

    def register_tool(self, func: Callable, retry_attempts: int = 0, retry_delay: float = 1.0):
        """Registers a function as a tool for autonomous discovery."""

        @functools.wraps(func)
        def tool_wrapper(*args, **kwargs):
            attempts = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except (ValueError, KeyError, RuntimeError, TypeError, IOError, OSError) as e:
                    attempts += 1
                    if attempts <= retry_attempts:
                        logger.warning(
                            "Tool '%s' failed (Attempt %d/%d). Retrying in %.1fs... Error: %s",
                            func.__name__, attempts, retry_attempts + 1, retry_delay, e)
                        time.sleep(retry_delay)
                        continue

                    error_msg = f"Tool '{func.__name__}' failure after {attempts} attempts: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    return f"SYSTEM ERROR: {error_msg}. Please try an alternative or retry later."
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error(
                        "Unexpected error in tool '%s': %s", func.__name__, e)
                    return f"SYSTEM UNEXPECTED ERROR: {str(e)}"

        # Mark as a Jarvis tool for discovery
        setattr(tool_wrapper, "__is_jarvis_tool__", True)

        # Store as a raw function. We will convert to LiveKit tools on demand.
        if not hasattr(self, "tools"):
            self.tools = []

        # Prevent duplicates
        for existing in self.tools:
            if getattr(existing, "__name__", "") == func.__name__:
                logger.debug(
                    "Tool '%s' already registered, skipping.", func.__name__)
                return func

        self.tools.append(tool_wrapper)
        logger.debug("Registered tool: %s", func.__name__)
        return func

    def get_tools(self) -> List[Any]:
        """Returns all registered raw tools."""
        return getattr(self, "tools", [])

    def get_livekit_tools(self) -> List[llm.FunctionTool]:
        """Converts raw tools into LiveKit-compatible FunctionTool objects."""
        lk_tools = []
        tools = getattr(self, "tools", [])
        seen_names = set()

        for tool_func in tools:
            # Check for generic wrapper name and try to get the original function name
            func_name = getattr(tool_func, "__name__", "unknown")
            if func_name == "tool_wrapper" and hasattr(tool_func, "__wrapped__"):
                func_name = tool_func.__wrapped__.__name__

            if func_name in seen_names:
                logger.debug("Skipping duplicate LiveKit tool: %s", func_name)
                continue

            logger.info("Converting tool to LiveKit format: %s", func_name)

            try:
                # Use the decorator to create a FunctionTool
                lk_tool = llm.function_tool(tool_func)
                lk_tools.append(lk_tool)
                seen_names.add(func_name)
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                logger.error("Failed to register tool '%s': %s", func_name, e)
                # If it's the 'self' error, we definitely want to know which tool it is
                if "self" in str(e) or isinstance(e, KeyError):
                    logger.critical(
                        "CRITICAL: Tool '%s' has unhinted 'self' or signature issues!", func_name)
        return lk_tools

    def discover_plugins(self, package_path: str):
        """Recursively discovers and imports plugins from the given package path."""
        if self.discovered:
            return

        self.discovered = True

        # We assume package_path is an absolute path to the 'services' directory
        for _, module_name, _ in pkgutil.walk_packages([package_path], "services."):
            try:
                # Import the module to trigger decorators
                importlib.import_module(module_name)
                logger.debug("Discovered module: %s", module_name)
            except (ImportError, ValueError, AttributeError, RuntimeError) as e:
                logger.warning("Failed to load module %s: %s", module_name, e)

        logger.info(
            "Plugin discovery complete. Total tools: %d", len(self.tools))


plugin_manager = JarvisPluginManager()


def jarvis_tool(retry_attempts=0, retry_delay=1.0):
    """
    Decorator to register a function as a Jarvis AI tool.
    Supports optional retries for delicate operations like API calls.
    Usage:
    @jarvis_tool
    OR
    @jarvis_tool(retry_attempts=3, retry_delay=2.0)
    """
    if callable(retry_attempts):
        # Case where @jarvis_tool is used without parentheses
        func = retry_attempts
        return plugin_manager.register_tool(func)

    def decorator(func: Callable):
        return plugin_manager.register_tool(func, retry_attempts=retry_attempts,
                                            retry_delay=retry_delay)

    return decorator
