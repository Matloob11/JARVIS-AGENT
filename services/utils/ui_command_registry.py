"""
# services/utils/ui_command_registry.py
UI command registry (C-34, contract K-60 CT-VC-CONTROL).

Stdlib only — NO project imports, so any module can import it without cycles (K-60: "other areas
register commands from their own modules instead of editing agent_loops"). agent_loops re-exports
`register_ui_command` and `dispatch` in the wiring step so older imports keep working.

- `UI_COMMANDS`: name -> handler. A handler takes (payload, ctx) and may be sync or async.
- `register_ui_command(name)`: decorator; re-registration logs and replaces (last one wins).
- `dispatch(cmd_type, payload, ctx) -> bool`: False = unknown type or duplicate; True = handled
  (a handler that raised is logged, never propagated — same guarantee as the old `_handle_command`).
- Dedupe: OrderedDict LRU(512) keyed (type, payload.id | payload.client_msg_id). Only a dict payload
  with a usable id (str, 1..128 chars, printable) is ever deduped; an id-less command (mute, stop, ...)
  is always dispatched. A repeat is logged at DEBUG 'duplicate ui_command ignored'.
- Unknown type -> logger.warning + ctx.notify_log('Unknown UI command: <type>', 'SYSTEM'). The
  notify_log callable arrives on the ctx so this module stays import-free.
- `CommandContext`: the one shape every handler sees (built by the job's listener). Fields are
  Any-typed on purpose: handlers duck-type against the live objects, tests pass spies.
- `JobListenerHandle`: a job-scoped handle over the bridge socket client. `aclose()` flags the
  listener loop to end (`closing`) and disconnects the client, bounded and never raising, so a dead
  job's AsyncClient can no longer auto-reconnect with handlers bound to a dead assistant (C-34).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("JARVIS-UI-REGISTRY")

Handler = Callable[[Any, "CommandContext"], Any]

UI_COMMANDS: dict[str, Handler] = {}

DEDUPE_MAX = 512
_ID_KEYS = ("id", "client_msg_id")
_ID_MAX_LEN = 128
_TYPE_LOG_MAX = 64

_seen: "OrderedDict[tuple[str, str], None]" = OrderedDict()
_seen_lock = threading.Lock()


@dataclass
class CommandContext:
    """What a UI command handler gets besides its payload. Built by the job's listener; all fields
    are duck-typed (Any) so unit tests need no livekit or project objects."""
    assistant: Any = None
    runtime: Any = None
    notify_log: Any = None  # callable (message: str, level: str) -> None | awaitable
    extras: dict[str, Any] = field(default_factory=dict)


def register_ui_command(name: str) -> Callable[[Handler], Handler]:
    """Decorator: registers a handler for `name`. Re-registration logs and replaces."""
    if not isinstance(name, str) or not name:
        raise ValueError("register_ui_command needs a non-empty command name")

    def _decorator(handler: Handler) -> Handler:
        old = UI_COMMANDS.get(name)
        if old is not None and old is not handler:
            logger.info("UI command %r re-registered: %s replaces %s",
                        name, getattr(handler, "__qualname__", handler),
                        getattr(old, "__qualname__", old))
        UI_COMMANDS[name] = handler
        return handler

    return _decorator


def _usable_id(payload: Any) -> str | None:
    """The dedupe id of a payload: payload.id or payload.client_msg_id when it is a printable
    string of 1..128 chars; anything else means 'never deduped'."""
    if not isinstance(payload, dict):
        return None
    for key in _ID_KEYS:
        value = payload.get(key)
        if value is None or value == "":
            continue  # falsy id falls through to the next key (K-60: payload.id or payload.client_msg_id)
        if isinstance(value, str) and 1 <= len(value) <= _ID_MAX_LEN and value.isprintable():
            return value
        return None  # an id field that exists but is unusable is not replaced by the next key
    return None


def _is_duplicate(cmd_type: str, payload: Any) -> bool:
    msg_id = _usable_id(payload)
    if msg_id is None:
        return False
    key = (cmd_type, msg_id)
    with _seen_lock:
        if key in _seen:
            _seen.move_to_end(key)
            return True
        _seen[key] = None
        while len(_seen) > DEDUPE_MAX:
            _seen.popitem(last=False)
    return False


def reset_dedupe() -> None:
    """Clears the dedupe LRU (tests; a wiring step may also call it at job start)."""
    with _seen_lock:
        _seen.clear()


def _loggable_type(cmd_type: Any) -> str:
    text = cmd_type if isinstance(cmd_type, str) else repr(cmd_type)
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:_TYPE_LOG_MAX] or "<empty>"


async def _notify(ctx: Any, message: str, level: str) -> None:
    """Best-effort ctx.notify_log(message, level); never raises."""
    notify = getattr(ctx, "notify_log", None)
    if not callable(notify):
        return
    try:
        result = notify(message, level)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("notify_log failed: %s", exc)


async def dispatch(cmd_type: Any, payload: Any, ctx: Any) -> bool:
    """Runs the registered handler for `cmd_type`. Returns False for an unknown type or a duplicate
    (type,id); True when a handler ran (even if it raised — the error is logged, never propagated,
    matching the old `_handle_command` contract)."""
    name = cmd_type if isinstance(cmd_type, str) else None
    handler = UI_COMMANDS.get(name) if name else None
    if handler is None:
        shown = _loggable_type(cmd_type)
        logger.warning("Unknown UI command: %s", shown)
        await _notify(ctx, f"Unknown UI command: {shown}", "SYSTEM")
        return False
    if _is_duplicate(name, payload):
        logger.debug("duplicate ui_command ignored type=%s id=%s", name, _usable_id(payload))
        return False
    try:
        result = handler(payload, ctx)
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Error processing UI command %s: %s", name, exc, exc_info=True)
    return True


class JobListenerHandle:
    """
    Job-scoped lifetime of the bridge listener (C-34). The listener loop owns ONE socket client per
    job; this handle is how the runtime ends it: `aclose()` sets `closing` (the loop's exit flag,
    checked instead of today's cancel-only exit), disconnects the client bounded by
    `disconnect_timeout`, never raises and is idempotent. `closed` is set once aclose finished.
    """

    def __init__(self, client: Any = None, *, disconnect_timeout: float = 5.0) -> None:
        self._client = client
        self._disconnect_timeout = disconnect_timeout
        self._closing = False
        self.closed = asyncio.Event()

    @property
    def closing(self) -> bool:
        """True once aclose() was requested: the listener loop must return instead of reconnecting."""
        return self._closing

    def attach_client(self, client: Any) -> None:
        """Late-binds the socket client (the listener builds it in its own __init__)."""
        self._client = client

    async def aclose(self) -> None:
        """Ends the listener: sets `closing`, disconnects the client (bounded), never raises."""
        if self._closing:
            return
        self._closing = True
        try:
            client = self._client
            disconnect = getattr(client, "disconnect", None) if client is not None else None
            if callable(disconnect):
                result = disconnect()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, self._disconnect_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("listener disconnect during aclose: %s", exc)
        finally:
            self.closed.set()
