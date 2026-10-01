"""
# jarvis_plugin_manager.py
Handles dynamic discovery and registration of AI tools.
"""

import asyncio
import functools
import importlib
import inspect
import pkgutil
import sys
import threading
import types
import typing
from collections.abc import Callable
from typing import Any, Optional, TypeVar, cast

from livekit.agents import llm

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_resilience import resilience_manager
from services.utils.jarvis_security import (
    DEFAULT_MAX_OUTPUT_CHARS,
    ArgPolicyError,
    VortexGuard,
    vortex_guard,
)
from services.utils.plugin_manifest import Permission, get_permissions_for_module

logger = setup_logger("PLUGIN-MANAGER")

F = TypeVar("F", bound=Callable[..., Any])

RISK_LEVELS = ("low", "medium", "high")
_POSITIONAL_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
_VARIADIC_KINDS = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


def _is_str_hint(hint: Any) -> bool:
    """True for str, Optional[str], str | None, Annotated[str, ...] and other unions with str."""
    if hint is str:
        return True
    if isinstance(hint, str):  # an annotation that could not be evaluated
        return hint.replace(" ", "") in {"str", "Optional[str]", "typing.Optional[str]",
                                         "str|None", "None|str"}
    origin = typing.get_origin(hint)
    if origin is typing.Annotated:
        return _is_str_hint(typing.get_args(hint)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_is_str_hint(arg) for arg in typing.get_args(hint))
    return False


def _str_param_names(func: Callable[..., Any], sig: inspect.Signature) -> list[str]:
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except (NameError, TypeError, AttributeError, SyntaxError):
        hints = {}
    return [name for name, param in sig.parameters.items()
            if param.kind not in _VARIADIC_KINDS
            and _is_str_hint(hints.get(name, param.annotation))]


def _resolve_arg_policies(label: str, func: Callable[..., Any], sig: inspect.Signature,
                          raw_args: Any, arg_policies: Any) -> dict[str, str]:
    """Merges raw_args and arg_policies (arg_policies wins for the same argument)."""
    policies: dict[str, str] = {}
    if raw_args is True:
        policies.update(dict.fromkeys(_str_param_names(func, sig), "raw"))
    elif isinstance(raw_args, str):
        policies[raw_args] = "raw"
    elif isinstance(raw_args, (tuple, list, set, frozenset)):
        policies.update(dict.fromkeys((str(name) for name in raw_args), "raw"))
    elif raw_args not in (False, None):
        logger.error("jarvis_tool raw_args must be True or a tuple of argument names in %s; ignored",
                     label)

    if isinstance(arg_policies, dict):
        for name, policy in arg_policies.items():
            clean = policy.strip().lower() if isinstance(policy, str) else policy
            if not isinstance(clean, str) or clean not in VortexGuard.ARG_POLICIES:
                logger.error("unknown arg policy %r for argument %s in %s; using 'plain'",
                             policy, name, label)
                clean = "plain"
            policies[str(name)] = clean
    elif arg_policies is not None:
        logger.error("jarvis_tool arg_policies must be a dict in %s; ignored", label)

    params = sig.parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        for name in [n for n in policies if n not in params]:
            logger.error("jarvis_tool policy for unknown argument %s in %s; ignored", name, label)
            del policies[name]
    return policies


def _resolve_risk(label: str, risk: Any, risky: bool, confirm: Any,
                  confirm_summary: Any) -> tuple[Any, Any]:
    """
    Returns (risk, confirm_summary) after the K-11 aliases. Risk precedence: risky=True ('high'),
    then risk, then confirm['risk'] ('medium' when confirm omits it), else 'low'. confirm_summary
    wins over confirm['summary'].
    """
    summary = confirm_summary
    confirm_risk = None
    if isinstance(confirm, dict):
        confirm_risk = confirm.get("risk") or "medium"
        if summary is None:
            summary = confirm.get("summary")
    elif confirm is True:
        confirm_risk = "medium"
    elif confirm not in (None, False):
        logger.error("jarvis_tool confirm must be a dict like {'risk', 'summary'} in %s; ignored",
                     label)

    if risky:
        level = "high"
    elif risk is not None:
        level = risk
    else:
        level = confirm_risk or "low"
    if not callable(level):
        clean = str(level).strip().lower()
        if clean not in RISK_LEVELS:
            logger.error("invalid jarvis_tool risk %r in %s; using 'high'", level, label)
            clean = "high"
        level = clean

    if summary is not None and not isinstance(summary, str) and not callable(summary):
        logger.error("jarvis_tool confirm_summary must be a str template or a callable in %s; ignored",
                     label)
        summary = None
    return level, summary


def _resolve_exclusive(label: str, exclusive: Any) -> str | None:
    if exclusive is True:
        return "desktop_input"
    if isinstance(exclusive, str) and exclusive.strip():
        return exclusive.strip()
    if exclusive not in (None, False, ""):
        logger.error("jarvis_tool exclusive must be a lock name such as 'desktop_input' in %s; ignored",
                     label)
    return None


def _positive_or(label: str, kwarg: str, value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    # The upper bound also rejects inf, nan and ints too big for float(), which would
    # otherwise raise at import.
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and 0 < value <= sys.float_info.max:
        return value
    logger.error("invalid jarvis_tool %s=%r in %s; using %s", kwarg, value, label, fallback)
    return fallback


def _apply_arg_policies(tool_name: str, positional_names: list[str], policies: dict[str, str],
                        args: tuple[Any, ...], kwargs: dict[str, Any], *,
                        defaults: dict[str, str]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """
    Sanitizes one call's arguments (K-28); raises ArgPolicyError for a rejected value.
    Keyword arguments get their policy, else 'plain', as before. LiveKit passes every
    positional-or-keyword parameter positionally and those values were never sanitized,
    so a positional value changes only when its parameter has a policy.
    LiveKit also fills every omitted optional argument with its default, so a value equal to
    the str default of a parameter with a policy (defaults) is the author's own and passes
    unchanged: an optional cc='' must not fail 'email_list'.
    """
    def is_default(name: str, value: Any) -> bool:
        return isinstance(value, str) and name in defaults and value == defaults[name]

    kept = {name: value for name, value in kwargs.items() if is_default(name, value)}
    clean_kwargs = vortex_guard.validate_tool_args(
        tool_name, {name: value for name, value in kwargs.items() if name not in kept},
        policies=policies)
    clean_kwargs.update(kept)
    picked = {name: value for name, value in zip(positional_names, args)
              if name in policies and isinstance(value, str) and not is_default(name, value)}
    if not picked:
        return args, clean_kwargs
    cleaned = vortex_guard.validate_tool_args(tool_name, picked, policies=policies)
    clean_args = tuple(cleaned.get(name, value) if name in picked else value
                       for name, value in zip(positional_names, args))
    return clean_args + args[len(clean_args):], clean_kwargs


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

    def register_tool(self, func: F, /, permissions: list[str] | None = None,
                      retry_attempts: int = 0, retry_delay: float = 1.0,
                      execution_timeout: float = 15.0, *,
                      timeout: float | None = None,
                      raw_args: bool | tuple[str, ...] = False,
                      arg_policies: dict[str, str] | None = None,
                      risk: str | Callable[[dict[str, Any]], str] | None = None,
                      risky: bool = False,
                      confirm: dict[str, Any] | None = None,
                      confirm_summary: str | Callable[..., Any] | None = None,
                      voice_confirm: bool | None = None,
                      exclusive: str | None = None,
                      max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
                      long_running: bool = False,
                      **extra: Any) -> F:
        """
        Registers a function as a tool for autonomous discovery.
        The keyword-only options are contract K-11 (JarvisToolDecoratorV2) and are kept as
        metadata (get_tool_meta). The wrapper applies timeout (else execution_timeout), the
        arg policies and max_output_chars; risk, confirm, voice_confirm, exclusive and
        long_running are metadata only here. An unknown or invalid option logs ERROR and is
        ignored, so a module never loses its tools at import.
        """
        module = getattr(func, "__module__", "unknown")
        label = f"{module}.{getattr(func, '__qualname__', func.__name__)}"
        for key in extra:
            logger.error("unknown jarvis_tool kwarg %s in %s", key, label)

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
        level, summary = _resolve_risk(label, risk, risky, confirm, confirm_summary)
        if voice_confirm is not None:
            voice = bool(voice_confirm)
        else:
            # K-11 default for a fixed risk; for a risk callable it is decided per call.
            voice = {"medium": True, "high": False}.get(level) if isinstance(level, str) else None
        timeout_s = _positive_or(label, "timeout", timeout, execution_timeout)
        meta: dict[str, Any] = {
            # execution_timeout=None (no limit) stays None.
            "timeout": float(timeout_s) if isinstance(timeout_s, (int, float)) else timeout_s,
            "risk": level,
            "arg_policies": _resolve_arg_policies(label, func, sig, raw_args, arg_policies),
            "exclusive": _resolve_exclusive(label, exclusive),
            "voice_confirm": voice,
            "max_output_chars": int(_positive_or(label, "max_output_chars", max_output_chars,
                                                 DEFAULT_MAX_OUTPUT_CHARS)),
            "long_running": bool(long_running),
            "confirm_summary": summary,
        }
        effective_timeout = meta["timeout"]
        policies = meta["arg_policies"]
        policy_defaults = {name: param.default for name, param in sig.parameters.items()
                           if name in policies and isinstance(param.default, str)}
        max_chars = meta["max_output_chars"]
        positional_names = [p.name for p in sig.parameters.values() if p.kind in _POSITIONAL_KINDS]

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

            # Tool Parameter Validation (Self-Defense), before the breaker: a rejected
            # argument is the caller's mistake and must not count as a tool failure.
            try:
                args, kwargs = _apply_arg_policies(func.__name__, positional_names, policies,
                                                   args, kwargs, defaults=policy_defaults)
            except ArgPolicyError as e:
                logger.warning("Tool '%s' argument '%s' rejected: %s", func.__name__, e.arg, e.code)
                return {"status": "error", "tool": func.__name__,
                        "error_code": e.code, "message": e.message}

            # RECOVERY & RESILIENCE ENFORCEMENT
            breaker = resilience_manager.get_breaker(func.__name__, execution_timeout=effective_timeout)

            async def _execute_with_retries(*a: Any, **k: Any) -> Any:
                attempts = 0
                while True:
                    try:
                        async with self._tool_semaphore:
                            if asyncio.iscoroutinefunction(func):
                                return await func(*a, **k)
                            return func(*a, **k)
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
                return vortex_guard.validate_tool_output(func.__name__, raw_result, max_chars=max_chars)
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
        tool_wrapper.__jarvis_meta__ = meta

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

    def get_tool_meta(self, name: str) -> dict[str, Any] | None:
        """
        K-11 metadata of the registered tool `name`: timeout, risk (level or callable),
        arg_policies, exclusive, voice_confirm, max_output_chars, long_running, plus
        confirm_summary. Returns a copy, or None when no tool has that name.
        """
        with self._tools_lock:
            for tool_func in getattr(self, "tools", []):
                if getattr(tool_func, "__name__", "") == name:
                    meta = getattr(tool_func, "__jarvis_meta__", None)
                    if meta is None:
                        return None
                    return {**meta, "arg_policies": dict(meta["arg_policies"])}
        return None

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
                except Exception as e:  # pylint: disable=broad-exception-caught
                    # One bad plugin (missing drive, optional deps) must not kill the voice job.
                    logger.warning("Failed to load module %s: %s", module_name, e)

        _scan()
        logger.info("Plugin discovery complete. Total tools: %d", len(self.tools))


plugin_manager = JarvisPluginManager()


def jarvis_tool(permissions: list[str] | Callable[..., Any] | None = None,
                retry_attempts: int = 0, retry_delay: float = 1.0,
                execution_timeout: float = 15.0, *,
                timeout: float | None = None,
                raw_args: bool | tuple[str, ...] = False,
                arg_policies: dict[str, str] | None = None,
                risk: str | Callable[[dict[str, Any]], str] | None = None,
                risky: bool = False,
                confirm: dict[str, Any] | None = None,
                confirm_summary: str | Callable[..., Any] | None = None,
                voice_confirm: bool | None = None,
                exclusive: str | None = None,
                max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
                long_running: bool = False,
                **extra: Any) -> Any:
    """
    Decorator to register a function as a Jarvis AI tool.
    Supports optional retries for delicate operations like API calls and permission controls.
    The keyword-only options are contract K-11 (see JarvisPluginManager.register_tool).
    Usage:
    @jarvis_tool
    OR
    @jarvis_tool(permissions=["filesystem:read"], retry_attempts=3)
    OR
    @jarvis_tool(timeout=60, raw_args=("code",), risk="medium", exclusive="desktop_input")
    """
    options: dict[str, Any] = {
        "retry_attempts": retry_attempts, "retry_delay": retry_delay,
        "execution_timeout": execution_timeout, "timeout": timeout, "raw_args": raw_args,
        "arg_policies": arg_policies, "risk": risk, "risky": risky, "confirm": confirm,
        "confirm_summary": confirm_summary, "voice_confirm": voice_confirm,
        "exclusive": exclusive, "max_output_chars": max_output_chars,
        "long_running": long_running, **extra,
    }
    if callable(permissions):
        # Case where @jarvis_tool is used without parentheses
        func = cast(Callable[..., Any], permissions)
        return plugin_manager.register_tool(func, **options)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # 'permissions' is either None or a List[str] because of 'if callable(permissions)' guard above
        perms = cast(list[str] | None, permissions)
        return plugin_manager.register_tool(func, permissions=perms, **options)

    return decorator
