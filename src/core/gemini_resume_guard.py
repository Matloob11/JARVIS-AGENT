"""
# gemini_resume_guard.py
Drops a stale Gemini Live session-resumption handle so the next (re)connect starts a FRESH session.

Observed (dashboard test, 2026-10-06 15:5x PKT): after a network blip the live session died with
google.genai.errors.APIError / websockets ConnectionClosedError close code 1008
'Requested entity was not found' - livekit-plugins-google replayed the STORED resumption handle on
every reconnect, so each retry failed the same way until the whole session was torn down and rebuilt.

livekit-plugins-google 1.4.6 (private members pinned, plan.md section 12) keeps the handle in
RealtimeSession._session_resumption_handle and offers no callback between a failed attempt and the
next retry. The seam our layer does get: every failed attempt is logged on the plugin's own logger
('livekit.plugins.google'), synchronously in the session's event loop, right before the retry sleep.
So this guard

- wraps RealtimeModel.session (the instance attribute only, agent_runner's model) so every
  RealtimeSession the model builds is registered here (a WeakSet; nothing keeps a session alive), and
- installs one logging.Filter on the plugin logger. When a record reports a stale-handle failure
  (close code 1008 / 'Requested entity was not found') the filter clears the stored handle of every
  registered session and logs one WARNING (SESSION_RESUME_DROPPED reason=stale_handle). The plugin's
  NEXT retry then connects without the handle - inside the plugin's existing retry budget and the
  job's 5-attempt session cap (K-60 v9). No new restart loop, no site-packages edit; any other error
  keeps today's behaviour (the filter never drops or changes a record, it always returns True).
"""

import functools
import logging
import threading
import weakref
from typing import Any

from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-RESUME-GUARD")

# The logger livekit-plugins-google logs every realtime failure on (plugins/google/log.py).
PLUGIN_LOGGER_NAME = "livekit.plugins.google"
# The pinned private member that carries the resumption handle (realtime_api.RealtimeSession).
_HANDLE_ATTR = "_session_resumption_handle"
_WRAPPED_FLAG = "_jarvis_resume_guard_wrapped"

STALE_HANDLE_CLOSE_CODE = "1008"
STALE_HANDLE_MESSAGE = "requested entity was not found"
# '1008' alone is too loose for an arbitrary string; on the plugin logger it must come with one of
# these (the plugin's own 'Gemini Realtime API error: ...' lines, an APIError or a websockets close).
_CODE_CONTEXT_MARKERS = ("policy violation", "apierror", "api error", "connectionclosed",
                         "websocket")

# Sessions to clear on a stale-handle failure. Jobs run in their own threads on Windows
# (livekit-agents' THREAD executor), so the set is shared under a lock.
_sessions: "weakref.WeakSet[Any]" = weakref.WeakSet()
_sessions_lock = threading.Lock()
_install_lock = threading.Lock()
_filter_installed = False
_dropped_total = 0


def is_stale_handle_error(text: str) -> bool:
    """True when text (a log line, exception string or both) reports the stale-resume failure."""
    lowered = (text or "").lower()
    if STALE_HANDLE_MESSAGE in lowered:
        return True
    return STALE_HANDLE_CLOSE_CODE in lowered and any(m in lowered for m in _CODE_CONTEXT_MARKERS)


def dropped_total() -> int:
    """How many stale handles this process has dropped (for diagnostics and tests)."""
    return _dropped_total


def watch_session(rt_session: Any) -> None:
    """Registers a RealtimeSession whose stale handle this guard may drop."""
    try:
        with _sessions_lock:
            _sessions.add(rt_session)
    except TypeError:  # not weakref-able (a test double): the guard simply does not watch it
        logger.debug("Resume guard cannot watch a %s", type(rt_session).__name__)


def drop_stale_handles() -> int:
    """
    Clears the stored resumption handle of every watched session, so each one's next (re)connect
    starts fresh instead of replaying a stale handle. Returns how many handles were dropped and
    logs one WARNING when any were. Never raises, never logs a handle value.
    """
    global _dropped_total  # pylint: disable=global-statement
    dropped = 0
    with _sessions_lock:
        watched = list(_sessions)
    for rt_session in watched:
        try:
            if getattr(rt_session, _HANDLE_ATTR, None):
                setattr(rt_session, _HANDLE_ATTR, None)
                dropped += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    if dropped:
        _dropped_total += dropped
        logger.warning(
            "SESSION_RESUME_DROPPED reason=stale_handle handles=%d total=%d: the next Gemini Live "
            "(re)connect starts a fresh session instead of replaying the stale resumption handle.",
            dropped, _dropped_total)
    return dropped


class _StaleHandleFilter(logging.Filter):
    """Watches the plugin logger; on a stale-handle failure it drops the stored handles.
    It never suppresses or edits a record (filter always returns True) and never raises."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            parts = [record.getMessage()]
            if record.exc_info and record.exc_info[1] is not None:
                parts.append(str(record.exc_info[1]))
            if is_stale_handle_error(" ".join(parts)):
                drop_stale_handles()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return True


def install_log_hook() -> None:
    """Puts the stale-handle filter on the plugin logger, once per process."""
    global _filter_installed  # pylint: disable=global-statement
    with _install_lock:
        if _filter_installed:
            return
        logging.getLogger(PLUGIN_LOGGER_NAME).addFilter(_StaleHandleFilter())
        _filter_installed = True


def guard_realtime_model(model: Any) -> Any:
    """
    Arms the guard for model (google.realtime.RealtimeModel): installs the log hook and wraps the
    instance's session() so every RealtimeSession it builds is watched. Returns the same model;
    idempotent, and a model that cannot be wrapped is left exactly as it was (the session still
    rebuilds through the existing recovery path, only without the stale-handle drop).
    """
    install_log_hook()
    original = getattr(model, "session", None)
    if not callable(original) or getattr(original, _WRAPPED_FLAG, False):
        return model

    @functools.wraps(original)
    def session(*args: Any, **kwargs: Any) -> Any:
        rt_session = original(*args, **kwargs)
        watch_session(rt_session)
        return rt_session

    setattr(session, _WRAPPED_FLAG, True)
    try:
        model.session = session  # instance attribute: the class and other models stay untouched
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Resume guard could not wrap RealtimeModel.session: %s", type(e).__name__)
    return model
