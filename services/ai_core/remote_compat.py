"""
# services/ai_core/remote_compat.py
K-72 compat layer of the VPS brain (plan task C-113). Cursor tools that run in the VPS agent still call
os.startfile, webbrowser.open and the ipinfo location, which answers for the data centre. Until X-64 and
X-65 give those modules their own hooks, install() rebinds them in remote mode only
(JARVIS_DESKTOP_MODE=remote). It is the first call of src/core/agent_runner.py, so it runs before that
module or discovery binds anything by value:

- os.startfile -> services.utils.jarvis_delivery.open_artifact: the laptop opens the file (pc.fetch).
  It raises only OSError, DESKTOP_OFFLINE while the laptop is offline, so the tool reports the truth.
- webbrowser.open -> pc.open_url, only inside a tool flagged opens_urls (K-71 placement table) and only
  for an http(s) URL. Otherwise, or when the laptop cannot take it, it logs and returns False like a
  browser that did not start; it never raises.
- services.info.jarvis_search.get_current_city / get_current_city_data and every by-value consumer
  (agent_runner and any other loaded JARVIS module that holds them) ->
  services.utils.jarvis_location: the device location cache, else USER_CITY; never ipinfo.

A loaded JARVIS module (services.*, src.*) that holds one of the replaced originals by value, the
openers included, is rebound as well. self_test(), which agent_runner runs after discovery, checks every
shimmed symbol and looks for such bindings again. It also requires that install() replaced
jarvis_search's own location functions: one renamed or moved away would leave its by-value consumers
unseen. Each symbol that fails logs ERROR 'COMPAT_SHIM_MISSING <symbol>', and health() then reports
the layer degraded for module_health (K-06). The shimmed symbols are frozen until X-64/X-65 replace
the shims.

C-124: install_arg_hooks(plugin_manager), which agent_runner runs after discovery and before the tools
are converted for LiveKit, wraps the tools that have VPS argument hooks (tool_placement.ARG_HOOKS side
'vps'): send_email's attachment must lie inside Jarvis_Outputs, Jarvis_Shared or the blob dir and never
in /proc, /sys, /etc or the agent data folders; a recipient other than the owner makes the call K-45
'high' (ConfirmFlow first); download_images' folder_name, the QR filename and the zip name become
basenames. A refused argument returns a K-12 error and the tool never runs.

Local mode (the PC brain) installs nothing: install() returns False and install_arg_hooks() hooks no
tool. Importing this module patches nothing.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import os
import sys
import threading
import types
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from services.ai_core import tool_placement
from services.utils import jarvis_delivery, jarvis_location
from services.utils.jarvis_config import DESKTOP_MODE_REMOTE, desktop_mode
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("REMOTE-COMPAT")

MISSING_TAG = "COMPAT_SHIM_MISSING"
SEARCH_MODULE = "services.info.jarvis_search"
LOCATION_NAMES = ("get_current_city", "get_current_city_data")
# The by-value consumers: each binds a location name at import. X-64 dropped jarvis_get_weather's;
# it now calls jarvis_search.get_current_city() at runtime and so rides the jarvis_search shim.
LOCATION_CONSUMERS = (
    ("src.core.agent_runner", "get_current_city"),
    ("src.core.agent_runner", "get_current_city_data"),
)
# The modules install() and self_test() scan for bindings: JARVIS's own code, a script's __main__ too.
_OWN_PREFIXES = ("services.", "src.")
_OWN_MAINS = ("__main__", "__mp_main__")
_PLUGIN_MANAGER = "services.ai_core.jarvis_plugin_manager"
_ABSENT = object()


@dataclass(frozen=True)
class CompatReport:
    """One self-test: ok, what is shimmed (for the startup log) and the symbols that are not."""

    ok: bool
    shimmed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    def detail(self) -> str:
        return "" if self.ok else f"{MISSING_TAG}: {', '.join(self.missing)}"


@dataclass(frozen=True)
class _Binding:
    module: types.ModuleType
    name: str
    had: bool
    original: Any


_lock = threading.RLock()
_bindings: list[_Binding] = []
# 'os.startfile' / 'webbrowser.open' -> (the original install() replaced, its shim)
_openers: dict[str, tuple[Any, Any]] = {}
_installed = False
_report: CompatReport | None = None


# ---------------------------------------------------------------- the shims


def open_file_shim(filepath: Any, operation: str | None = None, arguments: str = "",
                   cwd: Any = None, show_cmd: int = 1) -> None:
    """os.startfile in remote mode (the builtin's signature, keywords included):
    jarvis_delivery.open_artifact on the laptop. Raises only OSError."""
    if arguments or cwd is not None or show_cmd != 1:  # a program start, never done for the VPS
        raise jarvis_delivery.DeliveryError(jarvis_delivery.BAD_ARGS)
    try:
        jarvis_delivery.open_artifact(filepath, operation, remote=True)
    except OSError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("COMPAT os.startfile failed: %s", type(e).__name__)
        raise jarvis_delivery.DeliveryError(jarvis_delivery.DELIVERY_FAILED) from e


def _opens_urls(name: Any) -> bool:
    entry = tool_placement.PLACEMENTS.get(name) if isinstance(name, str) else None
    return bool(entry is not None and entry.opens_urls)


def url_opening_tool() -> str | None:
    """
    The running tool whose webbrowser.open may reach the laptop (K-72: only inside a tool flagged
    opens_urls), else None. The tool call of this context decides: a tool's own tasks and callbacks
    inherit it, and asyncio.to_thread copies it. Without one, code on an event loop is outside every
    tool. A thread without one (a tool may start its own) gets a running tool only while every
    running tool is flagged.
    """
    manager = sys.modules.get(_PLUGIN_MANAGER)
    if manager is None:
        return None
    call = manager.current_tool_call.get()
    if call:
        name = call.get("tool")
        return name if _opens_urls(name) else None
    if jarvis_delivery.in_event_loop():
        return None
    running = sorted(manager.plugin_manager.running_tools())
    return running[0] if running and all(_opens_urls(name) for name in running) else None


def open_url_shim(url: Any, new: int = 0, autoraise: bool = True) -> bool:
    """
    webbrowser.open in remote mode (same signature): pc.open_url on the laptop inside a tool flagged
    opens_urls. False, logged, outside such a tool, for a URL that is not http(s) or when the laptop
    cannot take it. Never raises.
    """
    tool: str | None = None
    try:
        tool = url_opening_tool()
        if tool is None:
            logger.warning("COMPAT webbrowser.open refused: not inside a tool flagged opens_urls")
            return False
        jarvis_delivery.open_url(url, remote=True)
    except OSError as e:
        logger.warning("COMPAT webbrowser.open in %s not sent to the laptop: %s", tool, e)
        return False
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("COMPAT webbrowser.open in %s failed: %s", tool, type(e).__name__)
        return False
    logger.info("COMPAT webbrowser.open in %s sent to the laptop (pc.open_url)", tool)
    return True


def _providers() -> dict[str, Callable[..., Any]]:
    return {"get_current_city": jarvis_location.get_current_city,
            "get_current_city_data": jarvis_location.get_current_city_data}


# ---------------------------------------------------------------- bindings


def _rebind(module: types.ModuleType, name: str, value: Any) -> None:
    """Sets module.name, remembering what it was the first time (uninstall)."""
    if not any(binding.module is module and binding.name == name for binding in _bindings):
        namespace = vars(module)
        _bindings.append(_Binding(module, name, name in namespace, namespace.get(name)))
    setattr(module, name, value)


def _own_modules() -> Iterator[tuple[str, types.ModuleType]]:
    for name, module in list(sys.modules.items()):
        if (name.startswith(_OWN_PREFIXES) or name in _OWN_MAINS) and isinstance(module, types.ModuleType):
            yield name, module


def _ipinfo_name(value: Any) -> str | None:
    """The name of one of jarvis_search's own (ipinfo) location functions, else None."""
    if not isinstance(value, types.FunctionType) or getattr(value, "__module__", None) != SEARCH_MODULE:
        return None
    name = getattr(value, "__qualname__", None)
    return name if name in LOCATION_NAMES else None


def _stale_bindings() -> list[tuple[str, types.ModuleType, str, Any]]:
    """
    (module name, module, attribute, replacement) of every binding in a loaded JARVIS module that still
    holds what install() replaces: jarvis_search's own location functions (the source before install(),
    a by-value consumer, a reloaded copy) or the original os.startfile / webbrowser.open bound by value.
    """
    providers = _providers()
    openers = list(_openers.values())
    found = []
    for module_name, module in _own_modules():
        for attr, value in list(vars(module).items()):
            which = _ipinfo_name(value)
            replacement = providers[which] if which is not None else next(
                (shim for original, shim in openers if value is original), None)
            if replacement is not None:
                found.append((module_name, module, attr, replacement))
    return found


def _install_opener(module: types.ModuleType, name: str, shim: Any) -> None:
    original = vars(module).get(name)
    if original is not None and original is not shim:
        _openers[f"{module.__name__}.{name}"] = (original, shim)
    _rebind(module, name, shim)


def _install_startfile() -> None:
    _install_opener(os, "startfile", open_file_shim)


def _install_browser() -> None:
    _install_opener(importlib.import_module("webbrowser"), "open", open_url_shim)


def _install_location() -> None:
    providers = _providers()
    search = importlib.import_module(SEARCH_MODULE)
    for name in LOCATION_NAMES:
        _rebind(search, name, providers[name])
    for module_name, name in LOCATION_CONSUMERS:
        module = sys.modules.get(module_name)
        if module is not None and name in vars(module):
            _rebind(module, name, providers[name])


def _install_consumers() -> None:
    for _, module, attr, replacement in _stale_bindings():
        _rebind(module, attr, replacement)


def install(mode: str | None = None) -> bool:
    """
    Rebinds the shimmed symbols when mode (default JARVIS_DESKTOP_MODE) is remote; returns whether it
    did. Idempotent, and it never raises: a step that fails logs ERROR COMPAT_SHIM_MISSING for its
    symbols, and self_test() reports them again.
    """
    global _installed  # pylint: disable=global-statement
    if (mode or desktop_mode()) != DESKTOP_MODE_REMOTE:
        return False
    steps = ((_install_startfile, ("os.startfile",)), (_install_browser, ("webbrowser.open",)),
             (_install_location, tuple(f"{SEARCH_MODULE}.{name}" for name in LOCATION_NAMES)),
             (_install_consumers, ("by-value consumers",)))
    failed: list[str] = []
    with _lock:
        for step, symbols in steps:
            try:
                step()
            except Exception as e:  # pylint: disable=broad-exception-caught
                failed.extend(symbols)
                for symbol in symbols:
                    logger.error("%s %s (install: %s)", MISSING_TAG, symbol, type(e).__name__)
        _installed = True
    if failed:
        logger.warning("COMPAT shims installed (remote mode) except: %s", ", ".join(failed))
    else:
        logger.info("COMPAT shims installed (remote mode): os.startfile, webbrowser.open, location")
    return True


def is_installed() -> bool:
    return _installed


def uninstall() -> None:
    """Puts every rebound symbol back and forgets the last self-test (tests: a process keeps its mode)."""
    global _installed, _report  # pylint: disable=global-statement
    with _lock:
        while _bindings:
            binding = _bindings.pop()
            if binding.had:
                setattr(binding.module, binding.name, binding.original)
            else:
                with suppress(AttributeError):
                    delattr(binding.module, binding.name)
        _openers.clear()
        _installed = False
        _report = None


# ---------------------------------------------------------------- self-test and health


def _bound(module_name: str, name: str) -> Any:
    """module_name.name; _ABSENT when the module is not loaded or has no such attribute."""
    module = sys.modules.get(module_name)
    return _ABSENT if module is None else vars(module).get(name, _ABSENT)


def _replaced(module_name: str, name: str) -> Any:
    """What install() replaced at module_name.name; _ABSENT when it replaced nothing there."""
    for binding in _bindings:
        if binding.module.__name__ == module_name and binding.name == name:
            return binding.original if binding.had else _ABSENT
    return _ABSENT


def _audit() -> tuple[list[str], list[str]]:
    shimmed: list[str] = []
    missing: list[str] = []

    def expect(symbol: str, value: Any, shim: Any, target: str) -> None:
        if value is shim:
            shimmed.append(f"{symbol} -> {target}")
        elif symbol not in missing:
            missing.append(symbol)

    expect("os.startfile", vars(os).get("startfile"), open_file_shim, "jarvis_delivery.open_artifact")
    flagged = sorted(name for name, entry in tool_placement.PLACEMENTS.items() if entry.opens_urls)
    expect("webbrowser.open", _bound("webbrowser", "open"), open_url_shim,
           f"pc.open_url inside {', '.join(flagged) or 'no tool'}")
    providers = _providers()
    for name in LOCATION_NAMES:
        symbol = f"{SEARCH_MODULE}.{name}"
        if _ipinfo_name(_replaced(SEARCH_MODULE, name)) != name:
            missing.append(symbol)  # renamed or moved: the scan would miss its by-value consumers
        else:
            expect(symbol, _bound(SEARCH_MODULE, name), providers[name], f"jarvis_location.{name}")
    for module_name, name in LOCATION_CONSUMERS:
        value = _bound(module_name, name)
        if value is not _ABSENT:  # a module that no longer binds the name by value is no consumer
            expect(f"{module_name}.{name}", value, providers[name], f"jarvis_location.{name}")
    for module_name, _, attr, _ in _stale_bindings():
        symbol = f"{module_name}.{attr}"
        if symbol not in missing:
            missing.append(symbol)
    return shimmed, missing


def self_test() -> CompatReport:
    """
    Remote mode: every shimmed symbol must still resolve to its shim, install() must have replaced
    jarvis_search's own location functions there, and no loaded JARVIS module may hold those functions
    or a replaced opener by value. Each failure logs ERROR 'COMPAT_SHIM_MISSING <symbol>' and leaves
    health() degraded; a pass logs what is shimmed. Never raises. In local mode, without install(),
    there is nothing to check.
    """
    global _report  # pylint: disable=global-statement
    with _lock:
        try:
            if not _installed and desktop_mode() != DESKTOP_MODE_REMOTE:
                report = CompatReport(True)
            else:
                shimmed, missing = _audit()
                report = CompatReport(not missing, tuple(shimmed), tuple(missing))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("COMPAT self-test could not run: %s", type(e).__name__)
            report = CompatReport(False, missing=("self_test",))
        _report = report
    if not report.ok:
        for symbol in report.missing:
            logger.error("%s %s", MISSING_TAG, symbol)
        logger.error("COMPAT self-test failed (%d symbols): module_health degraded", len(report.missing))
    elif report.shimmed:
        link = "online" if jarvis_delivery.desktop_online() else "offline: opens answer DESKTOP_OFFLINE"
        logger.info("COMPAT self-test passed, laptop link %s: %s", link, "; ".join(report.shimmed))
    return report


# ---------------------------------------------------------------- VPS argument hooks (C-124)

HOOKED_FLAG = "__vps_arg_hooks__"
_CONFIRMED_KWARG = "_confirmed"  # jarvis_plugin_manager.CONFIRMED_KWARG (K-14 approved re-run)
_CONFIRM_RISKS = ("medium", "high")


def _hook_roots() -> tool_placement.HookRoots:
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel
    return tool_placement.vps_hook_roots(config.project_root, config.outputs_dir, config.shared_dir)


def owner_addresses() -> tuple[str, ...]:
    """The owner's own e-mail address (EMAIL_USER, the account JARVIS sends from); empty when unset, so
    every recipient then counts as a non-owner."""
    value = (os.getenv("EMAIL_USER") or "").strip()
    return (value,) if value else ()


def _hook_refusal(tool: str, error: tool_placement.ArgHookError) -> dict[str, Any]:
    return {"status": "error", "tool": tool, "error_code": error.code,
            "message": f"Sir, yeh kaam VPS par nahi ho sakta: {error}"}


def _desktop_online() -> bool:
    try:
        return bool(jarvis_delivery.desktop_online())
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def hook_tool(tool: Callable[..., Any]) -> Callable[..., Any]:
    """
    tool (a registered plugin-manager wrapper) with its VPS argument hooks applied before every call:
    a refused path returns a K-12 error without running the tool, a basename replaces a name, and a
    non-owner recipient makes the call K-45 'high', so it goes through ConfirmFlow first (unless the
    tool's own risk already asks). The signature, metadata and name stay the tool's own.
    """
    name = getattr(tool, "__name__", "")
    if getattr(tool, HOOKED_FLAG, False) or not tool_placement.vps_hooks(name):
        return tool
    sig = getattr(tool, "__signature__", None) or inspect.signature(tool)
    own_risk = (getattr(tool, "__jarvis_meta__", None) or {}).get("risk")
    asks_itself = isinstance(own_risk, str) and own_risk in _CONFIRM_RISKS

    @functools.wraps(tool)
    async def hooked(*args: Any, **kwargs: Any) -> Any:
        confirmed = kwargs.pop(_CONFIRMED_KWARG, None) is True
        try:
            bound = sig.bind_partial(*args, **kwargs)
        except TypeError:
            return await tool(*args, **kwargs)  # the tool reports the bad call itself
        try:
            outcome = tool_placement.apply_vps_arg_hooks(
                name, bound.arguments, roots=_hook_roots(), owners=owner_addresses(),
                online=_desktop_online())
        except tool_placement.ArgHookError as e:
            logger.warning("ARG_HOOK %s refused %s:%s (%s)", name, e.hook, e.arg, e.code)
            return _hook_refusal(name, e)
        call_kwargs = dict(outcome.arguments)
        if confirmed:
            return await tool(**call_kwargs, **{_CONFIRMED_KWARG: True})
        if outcome.risk in _CONFIRM_RISKS and not asks_itself:
            logger.info("ARG_HOOK %s: a recipient other than the owner, K-45 %s", name, outcome.risk)
            return await _confirm_then_run(hooked, name, call_kwargs, outcome.risk)
        return await tool(**call_kwargs)

    setattr(hooked, HOOKED_FLAG, True)
    return hooked


async def _confirm_then_run(hooked: Callable[..., Any], name: str, arguments: dict[str, Any],
                            risk: str) -> Any:
    """K-14: the call becomes a pending ConfirmFlow action; it runs (with the hooks again) once Sir
    approves it."""
    try:
        broker = importlib.import_module("services.ai_core.jarvis_confirm").confirmation_broker
        return await broker.create_pending(
            name, f"{name}: recipient is not the owner", risk, arguments=arguments,
            args_preview=", ".join(sorted(arguments)),
            run=functools.partial(hooked, **arguments, **{_CONFIRMED_KWARG: True}))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("ARG_HOOK confirmation of %s failed: %s", name, type(e).__name__)
        return {"status": "error", "tool": name, "error_code": "CONFIRM_UI_UNREACHABLE",
                "message": "Sir, confirmation shuru nahi ho saki, is liye yeh kaam nahi kiya."}


def install_arg_hooks(manager: Any, mode: str | None = None) -> list[str]:
    """
    Remote mode only: replaces every registered tool that has VPS argument hooks (K-71 ARG_HOOKS side
    'vps') with its hooked wrapper, before the tools are converted for LiveKit. Returns the hooked
    names; local mode (the PC brain) hooks nothing. Idempotent.
    """
    if (mode or desktop_mode()) != DESKTOP_MODE_REMOTE:
        return []
    hooked_names: list[str] = []
    lock = getattr(manager, "_tools_lock", None) or threading.RLock()
    with lock:
        tools = list(getattr(manager, "tools", []) or [])
        for index, tool in enumerate(tools):
            wrapped = hook_tool(tool)
            if getattr(wrapped, HOOKED_FLAG, False):
                hooked_names.append(getattr(tool, "__name__", ""))
            tools[index] = wrapped
        manager.tools = tools
    expected = sorted(name for name in tool_placement.PLACEMENTS if tool_placement.vps_hooks(name))
    absent = [name for name in expected if name not in hooked_names]
    logger.info("ARG_HOOKS on the VPS: %d tools hooked", len(hooked_names))
    if absent:
        logger.warning("ARG_HOOKS: no registered tool for %s", ", ".join(absent))
    return hooked_names


def health() -> dict[str, Any]:
    """
    The compat layer's part of module_health (K-06) for its emitter (C-93): {ok, active, detail,
    missing}. ok is False after a failed self-test, or in remote mode without the shims.
    """
    with _lock:
        report, installed = _report, _installed
    if report is not None:
        return {"ok": report.ok, "active": installed, "detail": report.detail(),
                "missing": list(report.missing)}
    if installed or desktop_mode() != DESKTOP_MODE_REMOTE:
        return {"ok": True, "active": installed, "detail": "", "missing": []}
    return {"ok": False, "active": False, "detail": "compat shims not installed", "missing": []}
