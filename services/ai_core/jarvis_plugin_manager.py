"""
# jarvis_plugin_manager.py
Handles dynamic discovery and registration of AI tools.
"""

import asyncio
import contextvars
import functools
import hashlib
import importlib
import inspect
import json
import pkgutil
import re
import sys
import threading
import time
import types
import typing
import uuid
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, Optional, TypeVar, cast

from livekit.agents import llm

from services.utils import jarvis_audit, jarvis_security
from services.utils.jarvis_config import (
    DESKTOP_MODE_REMOTE,
    ROLE_DESKTOP,
    config,
    desktop_mode,
    runtime_role,
)
from services.utils.jarvis_logger import setup_logger
from services.ai_core import jarvis_guard  # C-29: K-45 policy (imports only src.core.confirm_policy)
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

# K-11: how long a call waits for its exclusive lock before DESKTOP_BUSY, and (as K-25
# run_cancellable does) how long a cancelled or timed-out sync tool waits for its worker thread.
EXCLUSIVE_WAIT_S = 30.0
WORKER_GRACE_S = 5.0
DESKTOP_INPUT = "desktop_input"
# K-13 ToolSyncEvent limits.
SYNC_DETAILS_MAX = 160
SYNC_RESULT_MAX = 200
_PREVIEW_VALUE_MAX = 60
_FREE_TEXT_POLICIES = frozenset({"raw", "multiline"})
_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")

_DESKTOP_DISABLED_MESSAGE = "Sir, desktop control abhi band hai, settings mein on kar dein."
_DESKTOP_BUSY_MESSAGE = ("Sir, desktop par abhi doosra kaam chal raha hai, "
                         "thori der baad dobara koshish karein.")

# K-14 ConfirmFlow: the risk levels that wait for Sir's approval, the internal flag of an approved
# re-run (never part of an LLM schema) and the confirm_summary limits.
CONFIRM_RISKS = ("medium", "high")
CONFIRMED_KWARG = "_confirmed"
CONFIRM_SUMMARY_TIMEOUT_S = 5.0
CONFIRM_SUMMARY_MAX = 300
_CONFIRM_FAILED_MESSAGE = "Sir, confirmation shuru nahi ho saki, is liye yeh kaam nahi kiya."

# Tool status words (K-12 plus legacy returns) -> K-13 end status; anything else is 'error'.
_SYNC_STATUS = {
    "success": "success", "ok": "success", "done": "success", "completed": "success",
    "empty": "success", "info": "success", "pass": "success", "active": "success",
    "not_found": "not_found", "degraded": "degraded", "partial_success": "degraded",
    "warning": "degraded", "started": "started", "pending": "started",
    "needs_confirmation": "needs_confirmation", "cancelled": "cancelled",
    "blocked": "blocked", "denied": "blocked", "timeout": "timeout",
    # C-83 autonomous_self_repair: a diff proposed, a patch applied ('rolled_back' stays an error).
    "proposed": "success", "applied": "success",
}
# A legacy tool reports a failure as plain text: '❌ ...', 'Error: ...' or 'ERROR: ...'.
_FAILURE_TEXT = re.compile(r"\s*(?:❌|error\b)", re.IGNORECASE)

# The LLM tool call running in this context: {'id': K-13 event id, 'tool': name}.
current_tool_call: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "jarvis_current_tool_call", default=None)

_running_calls: dict[str, str] = {}  # call id -> tool name (running_tools)
_running_lock = threading.Lock()
_sync_tasks: set[asyncio.Task[None]] = set()  # fire-and-forget tool_sync posts, kept referenced

# One asyncio.Lock per lock name and event loop: an asyncio.Lock can only be awaited in one loop.
_exclusive_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = \
    weakref.WeakKeyDictionary()
_exclusive_guard = threading.Lock()


class _ExclusiveHold:
    """An exclusive lock held by one tool call."""
    __slots__ = ("active", "lock", "name", "token")

    def __init__(self, name: str, lock: asyncio.Lock) -> None:
        self.name = name
        self.lock = lock
        self.active = True
        self.token: contextvars.Token[tuple[_ExclusiveHold, ...]] | None = None


# Locks held in this context, so a tool that awaits another tool's wrapper under the same lock
# name does not wait for itself.
_held_exclusive: contextvars.ContextVar[tuple[_ExclusiveHold, ...]] = contextvars.ContextVar(
    "jarvis_held_exclusive", default=())


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


def _bind_arguments(sig: inspect.Signature, positional_names: list[str], args: tuple[Any, ...],
                    kwargs: dict[str, Any]) -> dict[str, Any]:
    """
    The call's arguments by name with omitted ones at their defaults (K-11:
    sig.bind_partial(...).apply_defaults()), so an omitted auto_run reads as True.
    """
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except TypeError:  # a call the tool itself will reject; keep what can be named
        return {**dict(zip(positional_names, args)), **kwargs}
    bound.apply_defaults()
    return dict(bound.arguments)


def _call_risk(risk: Any, arguments: dict[str, Any], label: str) -> str:
    """
    K-11 risk of one call: the fixed level, or what the risk callable answers for the call's
    arguments. A callable that raises or answers anything but low/medium/high counts as 'high'.
    """
    if not callable(risk):
        return cast(str, risk)
    try:
        level = risk(dict(arguments))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("risk callable of %s failed (%s); the call counts as high risk", label, e)
        return "high"
    if inspect.iscoroutine(level):
        level.close()
    clean = level.strip().lower() if isinstance(level, str) else None
    if clean not in RISK_LEVELS:
        logger.warning("risk callable of %s returned %r; the call counts as high risk",
                       label, type(level).__name__ if inspect.isawaitable(level) else level)
        return "high"
    return clean


def _redact(text: str) -> str:
    """K-29 redact_secrets when jarvis_security has it (C-25); unchanged text before that."""
    redactor = getattr(jarvis_security, "redact_secrets", None)
    if not callable(redactor):
        return text
    try:
        return str(redactor(text))
    except Exception:  # pylint: disable=broad-exception-caught
        return "[redaction failed]"


def _one_line(text: str, limit: int) -> str:
    """text on one line, secrets redacted, cut to limit chars (redacted before it is cut)."""
    text = " ".join(_CONTROL_RUN.sub(" ", text[:limit + 1024]).split())
    text = _redact(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _is_default(value: Any, default: Any) -> bool:
    try:
        return value is default or (type(value) is type(default) and bool(value == default))
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _args_preview(arguments: dict[str, Any], policies: dict[str, str],
                  defaults: dict[str, Any]) -> str:
    """
    K-13 details: name=value for every argument that differs from its default; a raw or
    multiline (free-text) value shows only as '<N chars>'.
    """
    parts: list[str] = []
    try:
        for name, value in arguments.items():
            if name in defaults and _is_default(value, defaults[name]):
                continue
            if isinstance(value, str) and policies.get(name) in _FREE_TEXT_POLICIES:
                shown = f"<{len(value)} chars>"
            else:
                shown = _one_line(value if isinstance(value, str) else repr(value),
                                  _PREVIEW_VALUE_MAX)
            parts.append(f"{name}={shown}")
    except Exception:  # pylint: disable=broad-exception-caught
        return ""
    text = ", ".join(parts)
    return text if len(text) <= SYNC_DETAILS_MAX else text[:SYNC_DETAILS_MAX - 1] + "…"


# C-79 (K-34): UI-facing argument masking for sensitive tools, keyed by registered tool name.
# The confirm_request summary/args_preview and the tool_sync details reach the bridge store,
# init_state and every UI socket, so for these tools the listed argument values never travel
# raw: mode 'digits' shows the K-34 mask (first 3 + stars + last 3 of the digits), mode 'length'
# only '<N chars>'. Other sensitive tools join by adding a row here (Claude lane); the real
# argument values still reach the tool body, the risk callable and the sha256-only audit.
SENSITIVE_TOOL_ARGS: dict[str, dict[str, str]] = {
    "lookup_sim_data": {"phone_number": "digits", "purpose": "length"},
}


def mask_sensitive_digits(value: str) -> str:
    """The K-34 mask rule (same as services/info/jarvis_sim_lookup._mask_query): only the first 3
    and last 3 digits stay visible; 4 digits or fewer are all stars."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:3]}{'*' * max(len(digits) - 6, 3)}{digits[-3:]}"


def _sensitive_masked(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A copy of arguments with the SENSITIVE_TOOL_ARGS values masked for UI-facing strings
    (confirm summary, args preview, tool_sync details). Empty/None values stay as they are, so
    an omitted optional argument still reads as its default. Tools without a row are returned
    unchanged."""
    spec = SENSITIVE_TOOL_ARGS.get(tool_name)
    if not spec:
        return arguments
    masked = dict(arguments)
    for name, mode in spec.items():
        value = masked.get(name)
        if name in masked and value not in (None, ""):
            text = value if isinstance(value, str) else repr(value)
            masked[name] = mask_sensitive_digits(text) if mode == "digits" \
                else f"<{len(text)} chars>"
    return masked


def _result_summary(result: Any) -> str:
    """K-13 result_summary: the result's message (or the result itself), one line, redacted."""
    try:
        if isinstance(result, dict):
            text = result.get("message") or result.get("error") or result
        else:
            text = result
        if text is None:
            return ""
        return _one_line(text if isinstance(text, str) else repr(text), SYNC_RESULT_MAX)
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _sync_status(result: Any) -> str:
    """
    K-13 end status for a tool's own return value. Text counts as success unless it starts like
    a legacy failure ('❌ ...', 'Error ...'); any other non-dict return counts as success.
    """
    if isinstance(result, str):
        return "error" if _FAILURE_TEXT.match(result) else "success"
    if not isinstance(result, dict):
        return "success"
    code = result.get("error_code")
    if code == "DESKTOP_CONTROL_DISABLED":
        return "blocked"
    # The wrapper's own error dict for a timeout carries error_type and a TOOL_TIMEOUT message.
    if code in ("TIMEOUT", "TOOL_TIMEOUT") or ("error_type" in result and str(
            result.get("message", "")).startswith("TOOL_TIMEOUT")):
        return "timeout"
    status = result.get("status")
    if status is None:
        return "error" if "error" in result else "success"
    return _SYNC_STATUS.get(str(status).strip().lower(), "error")


# Shared with the ConfirmFlow broker (services/ai_core/jarvis_confirm.py).
one_line = _one_line
result_status = _sync_status


class _TemplateArgs(dict):
    """Values of a confirm_summary template; an unknown {field} stays as written."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


async def _confirm_summary(spec: Any, arguments: dict[str, Any], tool_name: str, details: str,
                           label: str) -> str:
    """
    K-11 confirm_summary of one call: a str template filled from the bound arguments, or a sync
    or async callable (async capped at CONFIRM_SUMMARY_TIMEOUT_S). None, a failure or an empty
    answer gives '<tool> (<args preview>) chalana hai'. One line, secrets redacted.
    """
    text: Any = None
    try:
        if isinstance(spec, str):
            text = spec.format_map(_TemplateArgs(arguments))
        elif callable(spec):
            text = spec(dict(arguments))
            if inspect.isawaitable(text):
                text = await asyncio.wait_for(text, CONFIRM_SUMMARY_TIMEOUT_S)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("confirm_summary of %s failed (%s); using the default summary", label, e)
        text = None
    if not isinstance(text, str) or not text.strip():
        text = f"{tool_name} ({details}) chalana hai" if details else f"{tool_name} chalana hai"
    return _one_line(text, CONFIRM_SUMMARY_MAX)


def _sync_event(call_id: str, tool_name: str, *, phase: str, status: str, details: str,
                risk: str) -> dict[str, Any]:
    return {"id": call_id, "tool": tool_name, "action": tool_name, "phase": phase,
            "status": status, "details": details, "risk": risk, "source": "plugin",
            "timestamp": time.time()}


def _emit_tool_sync(payload: dict[str, Any],
                    after: asyncio.Task[None] | None = None) -> asyncio.Task[None] | None:
    """Posts one K-13 tool_sync event in the background; it never delays or fails the tool."""
    try:
        task = asyncio.get_running_loop().create_task(_post_tool_sync(payload, after))
    except RuntimeError:  # no running event loop
        return None
    _sync_tasks.add(task)
    task.add_done_callback(_sync_tasks.discard)
    return task


async def _post_tool_sync(payload: dict[str, Any], after: asyncio.Task[None] | None) -> None:
    if after is not None:
        # The bridge relays events in arrival order and the UI keeps the last one per id, so the
        # end event waits for its start event's post however long that takes; notify_event
        # bounds each post with its HTTP timeouts.
        await asyncio.wait({after})
    logger.info("tool_sync %s %s id=%s status=%s", payload["phase"], payload["tool"],
                payload["id"], payload["status"])
    try:
        # pylint: disable=import-outside-toplevel
        from services.utils import jarvis_bridge
        await jarvis_bridge.notify_event("tool_sync", payload)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("tool_sync for %s not sent: %s", payload["tool"], e)


def _audit_tool_call(tool_name: str, risk: str, status: str, arguments: dict[str, Any], *,
                     call_id: str, duration_ms: int) -> None:
    """TOOL_EXEC line in logs/security.log; the arguments only as a sha256 prefix and a length."""
    try:
        encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        encoded = repr(arguments)
    digest = hashlib.sha256(encoded.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    try:
        jarvis_audit.log_tool(tool_name, risk, status,
                              f"ARGS=sha256:{digest} len={len(encoded)} | ID={call_id} | "
                              f"DURATION_MS={duration_ms}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Audit of tool '%s' failed: %s", tool_name, e)


def _desktop_control_enabled() -> bool:
    """K-15 central kill switch of every exclusive='desktop_input' tool."""
    return bool(getattr(config, "desktop_control_enabled", True))


def _remote_discovery() -> bool:
    """K-71 VPS discovery: JARVIS_DESKTOP_MODE=remote in every role but the Desktop Node's, which
    runs the real laptop tools."""
    return desktop_mode() == DESKTOP_MODE_REMOTE and runtime_role() != ROLE_DESKTOP


def _refusal(tool_name: str, error_code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "tool": tool_name, "error_code": error_code, "message": message}


def _exclusive_lock(name: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _exclusive_guard:
        # A lock that has had a waiter references its loop, which keeps the weak key alive.
        for closed in [other for other in list(_exclusive_locks) if other.is_closed()]:
            del _exclusive_locks[closed]
        locks = _exclusive_locks.get(loop)
        if locks is None:
            locks = _exclusive_locks[loop] = {}
        lock = locks.get(name)
        if lock is None:
            lock = locks[name] = asyncio.Lock()
        return lock


def _holds_exclusive(name: str) -> bool:
    return any(hold.active and hold.name == name for hold in _held_exclusive.get())


async def _acquire_exclusive(name: str) -> _ExclusiveHold | None:
    """Waits up to EXCLUSIVE_WAIT_S for the named lock; None when it stays busy."""
    lock = _exclusive_lock(name)
    try:
        async with asyncio.timeout(EXCLUSIVE_WAIT_S):
            await lock.acquire()
    except TimeoutError:
        return None
    hold = _ExclusiveHold(name, lock)
    hold.token = _held_exclusive.set((*_held_exclusive.get(), hold))
    return hold


def _release_exclusive(hold: _ExclusiveHold) -> None:
    hold.active = False
    if hold.token is not None:
        try:
            _held_exclusive.reset(hold.token)
        except ValueError:  # set in another context; the inactive hold is ignored anyway
            pass
    hold.lock.release()


async def _run_sync_tool(func: Callable[..., Any], args: tuple[Any, ...],
                         kwargs: dict[str, Any]) -> Any:
    """
    Runs a sync tool in a worker thread. A cancelled or timed-out call waits up to WORKER_GRACE_S
    for the thread (like K-25 run_cancellable), so its exclusive lock outlives the thread's work.
    """
    worker = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await asyncio.wait({worker}, timeout=WORKER_GRACE_S)
        raise


async def _run_confirmed(wrapper: Callable[..., Any], args: tuple[Any, ...],
                         kwargs: dict[str, Any]) -> Any:
    """
    The run of an approved call (K-14): the wrapper again with _confirmed=True, so the timeout,
    exclusive lock, kill switch, tool_sync and audit apply. It runs in the broker's own task and
    holds no lock or tool call of whoever approved it.
    """
    _held_exclusive.set(())
    current_tool_call.set(None)
    return await wrapper(*args, **kwargs, **{CONFIRMED_KWARG: True})


class JarvisPluginManager:
    """
    Manages the lifecycle of AI tools, including registration and discovery.
    """
    _instance: Optional['JarvisPluginManager'] = None
    _lock = threading.Lock()
    _tools_lock = threading.RLock()
    _initialized: bool = False
    remote_plan: Any = None  # tool_placement.RemotePlan after a remote-mode discovery (K-71)

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
        metadata (get_tool_meta). Per LLM call the wrapper applies the arg policies, resolves
        the risk (a risk callable gets the bound arguments with defaults), takes the exclusive
        lock (and the desktop_input kill switch) outside the timeout (else execution_timeout),
        applies max_output_chars, emits one tool_sync start and end event (K-13) and audits
        medium/high calls. A medium/high call does not run: it returns needs_confirmation from
        the ConfirmFlow broker (K-14, jarvis_confirm; confirm_summary and voice_confirm feed
        it) and runs once Sir approves it, as a re-entry with the internal _confirmed flag.
        long_running is metadata only here. An unknown or invalid option logs ERROR and is
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
        # What apply_defaults() fills in for an omitted argument (*args and **kwargs: empty).
        param_defaults: dict[str, Any] = {
            name: () if param.kind is inspect.Parameter.VAR_POSITIONAL
            else {} if param.kind is inspect.Parameter.VAR_KEYWORD else param.default
            for name, param in sig.parameters.items()
            if param.default is not inspect.Parameter.empty or param.kind in _VARIADIC_KINDS}
        tool_name = func.__name__
        module_name = getattr(func, "__module__", "unknown")
        exclusive_name = meta["exclusive"]
        is_async = inspect.iscoroutinefunction(func)
        voice_option = None if voice_confirm is None else bool(voice_confirm)

        async def _execute_with_retries(*a: Any, **k: Any) -> Any:
            attempts = 0
            while True:
                try:
                    async with self._tool_semaphore:
                        if is_async:
                            return await func(*a, **k)
                        result = await _run_sync_tool(func, a, k)
                        return await result if inspect.isawaitable(result) else result
                except asyncio.CancelledError:
                    logger.warning("Tool '%s' execution CANCELLED.", tool_name)
                    raise
                except (ValueError, KeyError, RuntimeError, TypeError, OSError) as e:
                    attempts += 1
                    if attempts <= retry_attempts:
                        logger.warning(
                            "Tool '%s' failed (Attempt %d/%d). Retrying in %.1fs... Error: %s",
                            tool_name, attempts, retry_attempts + 1, retry_delay, e)
                        await asyncio.sleep(retry_delay)
                        continue
                    raise e

        async def _execute(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, str]:
            # RECOVERY & RESILIENCE ENFORCEMENT
            breaker = resilience_manager.get_breaker(tool_name, execution_timeout=effective_timeout)
            try:
                # Wrap execution in Circuit Breaker (Self-Healing)
                raw_result = await breaker.call(_execute_with_retries, *args, **kwargs)

                # Apply Tool Output Validation (Self-Defense)
                result = vortex_guard.validate_tool_output(tool_name, raw_result, max_chars=max_chars)
            except Exception as e:  # pylint: disable=broad-exception-caught
                error_summary = f"Tool '{tool_name}' failure: {e!s}"
                logger.error(error_summary, exc_info=True)
                timed_out = isinstance(e, RuntimeError) and str(e).startswith("TOOL_TIMEOUT")
                # Standardized JSON-like error response for stable agent parsing
                return {
                    "status": "error",
                    "tool": tool_name,
                    "error_type": type(e).__name__,
                    "message": _redact(str(e)),  # skips validate_tool_output, so mask secrets here (K-29)
                }, "timeout" if timed_out else "error"
            return result, _sync_status(result)

        async def _ask_confirmation(call: tuple[tuple[Any, ...], dict[str, Any]],
                                    arguments: dict[str, Any], details: str,
                                    call_risk: str) -> tuple[Any, str]:
            # K-14: the call becomes a pending action; the tool runs only after Sir approves it.
            # C-79 (K-34): a sensitive tool's summary is built from the masked arguments, so a
            # confirm_summary template can never print a raw value either.
            summary = await _confirm_summary(meta["confirm_summary"],
                                             _sensitive_masked(tool_name, arguments), tool_name,
                                             details, label)
            try:
                # Loaded at call time: jarvis_confirm itself imports this module.
                broker = importlib.import_module(
                    "services.ai_core.jarvis_confirm").confirmation_broker
                result = await broker.create_pending(
                    tool_name, summary, call_risk, voice_confirm=voice_option,
                    args_preview=details, arguments=arguments,
                    run=functools.partial(_run_confirmed, tool_wrapper, *call))
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Confirmation of '%s' failed: %s", tool_name, e, exc_info=True)
                result = _refusal(tool_name, "CONFIRM_UI_UNREACHABLE", _CONFIRM_FAILED_MESSAGE)
            result = vortex_guard.validate_tool_output(tool_name, result, max_chars=max_chars)
            return result, _sync_status(result)

        async def _guarded_call(args: tuple[Any, ...], kwargs: dict[str, Any],
                                rejected: ArgPolicyError | None,
                                confirm: Callable[[], Awaitable[tuple[Any, str]]] | None = None
                                ) -> tuple[Any, str]:
            # SECURITY ENFORCEMENT
            missing = required_perms - get_permissions_for_module(module_name)
            if missing:
                error_msg = f"SECURITY BLOCK: Tool '{tool_name}' requires permissions {missing} which are not granted."
                logger.critical(error_msg)
                return error_msg, "blocked"

            if exclusive_name == DESKTOP_INPUT and not _desktop_control_enabled():
                return _refusal(tool_name, "DESKTOP_CONTROL_DISABLED",
                                _DESKTOP_DISABLED_MESSAGE), "blocked"

            # Tool Parameter Validation (Self-Defense) ran before the breaker: a rejected
            # argument is the caller's mistake and must not count as a tool failure.
            if rejected is not None:
                logger.warning("Tool '%s' argument '%s' rejected: %s", tool_name, rejected.arg,
                               rejected.code)
                return _refusal(tool_name, rejected.code, rejected.message), "error"

            # The lock is taken outside the breaker's timeout and released only after a
            # timed-out or cancelled tool has unwound (K-11). A call that only asks for Sir's
            # approval (confirm) takes no lock and starts no timeout.
            hold = None
            if exclusive_name and confirm is None and not _holds_exclusive(exclusive_name):
                hold = await _acquire_exclusive(exclusive_name)
                if hold is None:
                    logger.warning("Tool '%s' waited %.0fs for the '%s' lock; DESKTOP_BUSY",
                                   tool_name, EXCLUSIVE_WAIT_S, exclusive_name)
                    return _refusal(tool_name, "DESKTOP_BUSY", _DESKTOP_BUSY_MESSAGE), "error"
            try:
                if exclusive_name == DESKTOP_INPUT and not _desktop_control_enabled():
                    # Switched off while this call waited for the lock.
                    return _refusal(tool_name, "DESKTOP_CONTROL_DISABLED",
                                    _DESKTOP_DISABLED_MESSAGE), "blocked"
                return await (_execute(args, kwargs) if confirm is None else confirm())
            finally:
                if hold is not None:
                    _release_exclusive(hold)

        @functools.wraps(func)
        async def tool_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Only an approved re-run (_run_confirmed) passes _confirmed=True; LiveKit drops
            # argument names outside the signature, so the model cannot set it.
            confirmed = kwargs.pop(CONFIRMED_KWARG, None) is True
            call = (args, kwargs)  # as received, so an approved re-run sanitizes them only once
            call_id = uuid.uuid4().hex
            started = time.monotonic()
            arguments = _bind_arguments(sig, positional_names, args, kwargs)
            # C-79 (K-34): the details string reaches tool_sync, confirm_request args_preview,
            # the bridge store and the UI, so sensitive argument values go in masked only.
            details = _args_preview(_sensitive_masked(tool_name, arguments), policies,
                                    param_defaults)
            # C-29 (K-45): the guard raises a K-45 HIGH tool to 'high' whatever its decorator says (never lowers).
            call_risk = jarvis_guard.effective_risk(tool_name, level if isinstance(level, str) else "high")
            start_task: asyncio.Task[None] | None = None
            start_sent = False
            status, result = "error", None
            context_token = current_tool_call.set({"id": call_id, "tool": tool_name})
            with _running_lock:
                _running_calls[call_id] = tool_name
            try:
                rejected: ArgPolicyError | None = None
                try:
                    args, kwargs = _apply_arg_policies(tool_name, positional_names, policies,
                                                       args, kwargs, defaults=policy_defaults)
                    arguments = _bind_arguments(sig, positional_names, args, kwargs)
                except ArgPolicyError as e:
                    rejected = e
                call_risk = jarvis_guard.effective_risk(tool_name, _call_risk(level, arguments, label),
                                                        arguments)  # K-45 v22: run_cmd's READ_ONLY calls
                start_task = _emit_tool_sync(_sync_event(
                    call_id, tool_name, phase="start", status="running", details=details,
                    risk=call_risk))
                start_sent = True
                confirm = None
                if call_risk in CONFIRM_RISKS and not confirmed:
                    confirm = functools.partial(_ask_confirmation, call, arguments, details,
                                                call_risk)
                result, status = await _guarded_call(args, kwargs, rejected, confirm)
                return result
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            finally:
                duration_ms = int((time.monotonic() - started) * 1000)
                with _running_lock:
                    _running_calls.pop(call_id, None)
                current_tool_call.reset(context_token)
                if not start_sent:  # an unexpected error came before the start event
                    start_task = _emit_tool_sync(_sync_event(
                        call_id, tool_name, phase="start", status="running", details=details,
                        risk=call_risk))
                end_event = _sync_event(call_id, tool_name, phase="end", status=status,
                                        details=details, risk=call_risk)
                end_event["duration_ms"] = duration_ms
                summary = _result_summary(result)
                if summary:
                    end_event["result_summary"] = summary
                _emit_tool_sync(end_event, after=start_task)
                # K-45: a MEDIUM-listed tool runs without a question but is audited as medium
                audited = jarvis_guard.audit_risk(tool_name, call_risk)
                if audited in ("medium", "high"):
                    _audit_tool_call(tool_name, audited, status, arguments,
                                     call_id=call_id, duration_ms=duration_ms)

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

    def running_tools(self) -> set[str]:
        """Names of the tools with an LLM call in progress (K-11)."""
        with _running_lock:
            return set(_running_calls.values())

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
        """Recursively discovers and imports plugins synchronously. In remote mode (the VPS brain,
        K-71) laptop-only modules are not imported and remote_plan records what runs where."""
        with self._tools_lock:
            if self.discovered:
                return
            self.discovered = True
        remote = None
        if _remote_discovery():  # local mode (the PC brain) ignores the placement table
            remote = importlib.import_module(
                "services.ai_core.tool_placement").RemoteDiscovery.start(logger)
        skip = remote.skip_modules if remote is not None else frozenset()

        def _scan() -> None:
            for _, module_name, _ in pkgutil.walk_packages([package_path], "services."):
                if module_name in skip:
                    logger.info("Remote discovery skips the laptop-only module %s", module_name)
                    continue
                try:
                    importlib.import_module(module_name)
                    logger.debug("Discovered module: %s", module_name)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    # One bad plugin (missing drive, optional deps) must not kill the voice job.
                    logger.warning("Failed to load module %s: %s", module_name, e)

        _scan()
        if remote is not None:
            with self._tools_lock:
                found = {getattr(tool, "__name__", ""): getattr(tool, "__module__", "")
                         for tool in self.tools}
            self.remote_plan = remote.finish(found, sys.modules, logger)
        logger.info("Plugin discovery complete. Total tools: %d", len(self.tools))

    def discovery_health(self) -> dict[str, Any]:
        """K-06 module_health.tools fields from discovery: ok is False after a remote-mode mismatch
        (K-71: expected names = discovery union manifest)."""
        with self._tools_lock:
            count = len(getattr(self, "tools", []))
        plan = self.remote_plan
        return {"ok": plan is None or bool(plan.ok), "count": count,
                "detail": "" if plan is None else plan.detail()}


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
