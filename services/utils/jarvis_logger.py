"""
# services/utils/jarvis_logger.py
JARVIS logging (contract K-29).

- setup_logger(name): a console handler of its own, the process's one shared rotating handler of LOG_FILE
  (shared_file_handler(): 10 MiB x 5 at LOG_FILE_LEVEL, default INFO) and, in the agent role, the one shared
  UIBridgeHandler.
- SharedRotatingFileHandler: opened on the first record. A rollover that another process or a reader refuses
  (Windows WinError 32) is postponed ROLLOVER_RETRY_S and the record is still written; a path that another process
  rotated is reopened within PATH_CHECK_EVERY_S.
- UIBridgeHandler forwards WARNING+ (UI_LOG_LEVEL) plus the INFO lines of UI_LOG_INFO_ALLOW to the UI log panel. It
  only appends to ui_log_shipper's buffer; the shipper's own thread posts at most one vortex_log_batch a second
  (C-64, K-40), never on a job or voice loop. start_ui_log_shipping() starts it; a process that never calls it
  (tests, scripts, a bridge) only buffers.
- Every handler masks secrets (RedactingFormatter), and C-124 private lines never reach the file or the UI.
"""
import atexit
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

from services.utils.jarvis_security import redact_secrets
from services.utils.probe_context import bridge_suppressed  # K-57 (C-39)

# Import config safely
try:
    from services.utils.jarvis_config import ROLE_AGENT, config, runtime_role
    _HAS_CONFIG: bool = True
except ImportError:
    # Fallback for early startup or standalone scripts
    config = cast(Any, None)
    _HAS_CONFIG = False
    ROLE_AGENT = "agent"

    def runtime_role() -> str:
        """JARVIS_ROLE without the config module (agent when unset)."""
        return (os.getenv("JARVIS_ROLE") or "").strip().lower() or ROLE_AGENT

# Unused since C-64 (UIBridgeHandler no longer posts per record; the shipper does). Kept as a name only: tests
# still monkeypatch it.
_BRIDGE_NOTIFY_LOG: Callable[[str, str], Any] | None = None

_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Folder of the fixed-name logs below (bridge.log, runner.log, and jarvis_default.log when the config module
# is missing): LOG_DIR, default 'logs' (relative to the working directory, exactly as before). C-138: the
# test suite points it at a temp folder, so no test writes the live logs/.
LOG_DIR_ENV = "LOG_DIR"
DEFAULT_LOG_DIR = "logs"

# K-29 (C-64): the main log file and its level (the file was ERROR-only before).
LOG_FILE_LEVEL_ENV = "LOG_FILE_LEVEL"
DEFAULT_LOG_FILE_LEVEL = logging.INFO
MAIN_LOG_MAX_BYTES = 10 * 1024 * 1024
MAIN_LOG_BACKUPS = 5
ROLLOVER_RETRY_S = 60.0          # a refused rollover is tried again after this long
ROLLOVER_NOTICE_EVERY_S = 3600.0  # at most one stderr line per file in this time
PATH_CHECK_EVERY_S = 1.0          # how often a handler checks that its path is still its file

# K-40 (C-64): what reaches the UI log panel, and how fast.
UI_LOG_LEVEL_ENV = "UI_LOG_LEVEL"
UI_LOG_INFO_ALLOW_ENV = "UI_LOG_INFO_ALLOW"
DEFAULT_UI_LOG_LEVEL = logging.WARNING
DEFAULT_UI_LOG_INFO_ALLOW = frozenset({"AGENT-RUNNER", "JARVIS-CORE", "PLUGIN-MANAGER", "JARVIS-LOOPS"})
# INFO templates of allow-listed loggers that are never forwarded below UI_LOG_LEVEL=INFO (about 80 lines at every
# session start); they stay in the file.
UI_NOISE_INFO_TEMPLATES: frozenset[str] = frozenset({"Converting tool to LiveKit format: %s"})
# Loggers never forwarded: the bridge and the notify path themselves, the shipper, and chatty libraries.
UI_SKIP_LOGGERS = frozenset({"UI-BRIDGE", "JARVIS-BRIDGE", "BRIDGE-NOTIFIER", "UI-LOG-SHIPPER"})
UI_SKIP_PREFIXES = ("httpx", "httpcore", "livekit", "socketio", "engineio", "asyncio", "urllib3")
UI_TEXT_MAX = 1000
UI_BUFFER_MAX = 200
UI_LINES_PER_S = 5.0
UI_LINES_BURST = 20.0
UI_BATCH_MAX = 50
UI_BATCH_MAX_BYTES = 128 * 1024
UI_FLUSH_EVERY_S = 1.0
UI_EVENT_OUTBOX_MAX = 20
# C-64 review LS-1: at most this many queued events are POSTed per tick (a wedged bridge costs each post its
# timeout, so an uncapped drain of 20 could hold one round 40 s), and at most UI_EXIT_OUTBOX_MAX at exit, so a
# graceful agent_ctl stop never turns into a taskkill waiting for the flush.
UI_OUTBOX_PER_TICK = 5
UI_EXIT_OUTBOX_MAX = 2
UI_RETRY_MAX_S = 30.0
UI_PAUSE_AFTER_REFUSAL_S = 60.0
UI_REFUSAL_WARN_EVERY_S = 600.0
UI_POST_TIMEOUT_S = 2.0
UI_EXIT_FLUSH_TIMEOUT_S = 1.0

# Outcomes of one POST /notify, as services/utils/jarvis_bridge names them.
OUTCOME_DELIVERED = "delivered"
OUTCOME_NOT_SENT = "retry"       # the bridge cannot have it (connect error, 429, 502, 503): send again later
OUTCOME_UNCERTAIN = "uncertain"  # it may have it (read timeout, 500, 504): never sent twice
OUTCOME_REFUSED = "failed"       # 400, 401, 403, 413: the same answer again


def log_dir() -> Path:
    """LOG_DIR, or 'logs' when it is unset or empty."""
    return Path((os.getenv(LOG_DIR_ENV) or "").strip() or DEFAULT_LOG_DIR)


def _env_level(name: str, default: int) -> int:
    """A level from the environment: a name (WARNING, warn, info ...) or a number; default when unset or unknown."""
    raw = (os.getenv(name) or "").strip().upper()
    if not raw:
        return default
    if raw.isdigit():
        return int(raw)
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else default


def log_file_level() -> int:
    """LOG_FILE_LEVEL (default INFO): the level of the main log file."""
    return _env_level(LOG_FILE_LEVEL_ENV, DEFAULT_LOG_FILE_LEVEL)


def ui_log_level() -> int:
    """UI_LOG_LEVEL (default WARNING): records at or above it reach the UI log panel."""
    return _env_level(UI_LOG_LEVEL_ENV, DEFAULT_UI_LOG_LEVEL)


def ui_log_info_allow() -> frozenset[str]:
    """UI_LOG_INFO_ALLOW: the loggers whose INFO lines reach the UI below UI_LOG_LEVEL ('' = none)."""
    raw = os.getenv(UI_LOG_INFO_ALLOW_ENV)
    if raw is None:
        return DEFAULT_UI_LOG_INFO_ALLOW
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


class RedactingFormatter(logging.Formatter):
    """
    Contract K-29: masks secrets with redact_secrets() in the finished line, so the message, its
    args, exc_info tracebacks and stack_info are all covered. Every JARVIS handler uses it.
    With inner it wraps another formatter (for example LiveKit's JSON one) and keeps its layout.
    """

    def __init__(self, *args: Any, inner: logging.Formatter | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.inner = inner
        if inner is not None:
            self.datefmt = inner.datefmt

    def format(self, record: logging.LogRecord) -> str:
        if self.inner is None:
            return redact_secrets(super().format(record))
        if record.exc_info and not record.exc_text:
            # Formatter.format caches the traceback on the record; caching the masked one means
            # handlers that print record.exc_text themselves (LiveKit's console) never see a secret.
            record.exc_text = self.formatException(record.exc_info)
        return redact_secrets(self.inner.format(record))

    def formatException(self, ei: Any) -> str:
        return redact_secrets(super().formatException(ei))

    def formatStack(self, stack_info: str) -> str:
        return redact_secrets(super().formatStack(stack_info))


class UIMessageFormatter(RedactingFormatter):
    """The masked message alone, without traceback or stack: what the UI log panel shows."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(record.getMessage())


class JSONFormatter(RedactingFormatter):
    """
    Structured JSON formatter for production-ready logs (message and traceback masked, K-29).
    """
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)),
            "level": record.levelname,
            "name": record.name,
            "message": redact_secrets(record.getMessage()),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


# C-124 step 8: window titles, clipboard text, locations and tool arguments are never logged at INFO, because
# INFO lines go to the main log file and (allow-listed, or with UI_LOG_LEVEL=INFO) to the UI log panel. A call site
# marks such a line with extra=PRIVATE (or logs it at DEBUG); the INFO templates below, which tool modules use
# today, are demoted the same way. Demoted lines stay available at LOG_LEVEL=DEBUG on the console only.
PRIVATE_ATTR = "jarvis_private"
PRIVATE: dict[str, Any] = {PRIVATE_ATTR: True}
PRIVATE_INFO_TEMPLATES: frozenset[str] = frozenset({
    # window titles
    "Focused window: %s",
    "Window focus mein hai: %s",
    "OPEN → raw='%s' clean='%s' match='%s'",
    # tool arguments (queries, names, paths, recipients, URLs, prompts)
    "Generating Stylish QR (Dots) for: %s",
    "QR Code saved successfully to: %s",
    "Searching for %d images for query: '%s'...",
    "Downloading images to %s",
    "Folder '%s' zipped successfully.",
    "Real Email sent to %s successfully.",
    "Opening WhatsApp via URI: %s",
    "Searching for contact: %s",
    "Sending message via type_text_tool: %s",
    "Searching for video URL: %s",
    "YouTube Automation: Action=%s, Query=%s",
    "Attempting to open URL in App Mode: %s",
    "Found YouTube URL: %s",
    "Query detected, searching for video: %s",
    "Executing yt-dlp: %s",
    "Controller Action: %s",
    "Matched '%s' to '%s' (score: %d)",
    "File khol rahe hain: %s",
    "Direct path detected, skipping index: %s",
    "File saved: %s",
    "Starting deep research for query: %s",
    "Starting Agentic Workflow: Research + Email for %s",
    "Searching Tavily for: '%s'",
    "Searching Google for: '%s'",
    "Attempting DuckDuckGo fallback for: '%s'",
    "Generating image (model=%s, provider=%s) for prompt: %s",
    "Polling Pollinations.ai: %s",
    "Semantic search initiated for query: %s",
    "\U0001f6e0️ Tool: analyze_surroundings called with query: %s",
    "RAG-OPT: Searching for '%s' (Filter: %s)",
    # reminder and task text (set_reminder's message, and the same text when it falls due); a due task's lines in
    # agent_core and the bridge log its id and length only since C-86, so they need no template here
    "Setting reminder: %s at %s",
    "\U0001f514 Triggering proactive reminder: %s",
    # locations
    "City ke liye weather fetch kiya ja raha hai: %s (forecast=%s)",
    "Attempting weather fallback via search for: %s",
    "Updating map to: %s (zoom=%s)",
    "Map sync successful for %s",
    # user transcripts and chat text (C-64 review SP-1): previews and full texts never persist in the file log
    # or the UI log panel; the chat panel (messages table) already carries this content by design. The demoted
    # preview lines stay visible on the console at LOG_LEVEL=DEBUG.
    "\U0001f3a4 User Transcription (Final, %d chars): %s",
    "\U0001f3a4 User Transcription (Final): %s",
    "\U0001f3a4 Partial Input: %s",
    "CHAT_ROUTER voice id=%s chars=%d: %s",
    "CHAT_ROUTER voice id=%s full text: %s",
    "CHAT_ROUTER reply id=%s turn=%s source=%s chars=%d: %s",
    "CHAT_ROUTER reply id=%s full text: %s",
    "CHAT_ROUTER %s id=%s full text: %s",
})


def is_private_record(record: logging.LogRecord) -> bool:
    """True for a line that carries a window title, clipboard text, a location or tool arguments."""
    return bool(getattr(record, PRIVATE_ATTR, False)) or (
        isinstance(record.msg, str) and record.msg in PRIVATE_INFO_TEMPLATES)


class PrivateInfoFilter(logging.Filter):
    """
    A logger filter (it runs before the handlers' level checks): a private INFO record becomes DEBUG, so
    the console at INFO, the file at INFO and the UI handler all leave it out. Higher levels are kept as they
    are: a WARNING or ERROR line must stay visible.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.INFO and is_private_record(record):
            record.levelno, record.levelname = logging.DEBUG, logging.getLevelName(logging.DEBUG)
        return True


_PRIVATE_FILTER = PrivateInfoFilter()


class PrivateFileFilter(logging.Filter):
    """A file handler filter: a private record below INFO (a demoted one included) never reaches a log file, even at
    LOG_FILE_LEVEL=DEBUG; it stays on the console (C-124)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.levelno < logging.INFO and is_private_record(record))


_PRIVATE_FILE_FILTER = PrivateFileFilter()


def redact_root_handlers() -> None:
    """
    Contract K-29: puts a RedactingFormatter on every root handler and on logging.lastResort,
    keeping the layout of the formatter each one already has. Safe to call again, for example
    after LiveKit's CLI has added its own root handler.
    """
    handlers = list(logging.getLogger().handlers)
    if logging.lastResort is not None:
        handlers.append(logging.lastResort)
    for handler in handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(inner=handler.formatter))


# ---------------------------------------------------------------- one rotating handler per file (C-64)

_rollover_notices: dict[str, float] = {}  # normcased path -> clock time of its last stderr notice
_notice_lock = threading.Lock()


class SharedRotatingFileHandler(RotatingFileHandler):
    """
    The one rotating handler of a log file in this process (shared_file_handler()), UTF-8 and opened on its first
    record. Windows-safe rollover: the live file is moved aside before the backups shift, so a rollover that another
    process or a reader refuses (WinError 32) changes nothing; it is postponed ROLLOVER_RETRY_S, the record is still
    written to the current file and handleError is never called. At most one stderr line per file and hour says so
    (never through logging). A path that another process rotated or removed is reopened within PATH_CHECK_EVERY_S, so
    job processes that share one file keep writing to the current one. The base class's shouldRollover, doRollover
    and _open still run (the test suite's rotation guard and file redirect rely on them).
    """

    def __init__(self, filename: str | os.PathLike[str], *, max_bytes: int = MAIN_LOG_MAX_BYTES,
                 backup_count: int = MAIN_LOG_BACKUPS, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(os.fspath(filename), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
                         delay=True)
        self._clock = clock
        self._blocked_until = float("-inf")
        self._next_path_check = float("-inf")
        self._staged: str | None = None
        self._staged_count = 0
        self.registry_key = _handler_key(self.baseFilename)
        self.postponed = 0  # rollovers postponed so far

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._clock() < self._blocked_until:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        try:
            self._rotate_files()
        except OSError as exc:
            self._postpone(exc)
        if self.stream is None:
            self.stream = self._open()

    def _rotate_files(self) -> None:
        """Moves the live file aside first (on Windows the step another open handle refuses), then lets the base class
        shift .1 .. .N-1; rotate() puts the moved file at .1. A file that is already gone was rotated by another
        process a moment ago: nothing is shifted."""
        if self.backupCount <= 0:
            super().doRollover()
            return
        self._staged_count += 1
        staged = f"{self.baseFilename}.rotating-{os.getpid()}-{self._staged_count}"
        try:
            os.rename(self.baseFilename, staged)
        except FileNotFoundError:
            return
        self._staged = staged
        try:
            super().doRollover()
        finally:
            leftover, self._staged = self._staged, None
            if leftover is not None:
                self._put_back(leftover)

    def rotate(self, source: str, dest: str) -> None:
        staged = self._staged
        if staged is not None and source == self.baseFilename:
            os.replace(staged, dest)
            self._staged = None
            return
        super().rotate(source, dest)

    def _put_back(self, staged: str) -> None:
        """A rollover that stopped half way: the moved file becomes the live file again (never deleted; when that
        is impossible it stays under its own name)."""
        try:
            if not os.path.exists(self.baseFilename):
                os.rename(staged, self.baseFilename)
                return
        except OSError:
            pass
        self._notice(f"rollover of {os.path.basename(self.baseFilename)} left {os.path.basename(staged)}; "
                     "it is kept as it is", force=True)

    def _postpone(self, exc: OSError) -> None:
        self._blocked_until = self._clock() + ROLLOVER_RETRY_S
        self.postponed += 1
        code = getattr(exc, "winerror", None) or exc.errno
        self._notice(f"rollover of {os.path.basename(self.baseFilename)} postponed {ROLLOVER_RETRY_S:.0f} s "
                     f"({type(exc).__name__} {code}): another process or a reader holds the file; lines keep "
                     "going to the current file")

    def _notice(self, text: str, force: bool = False) -> None:
        now = self._clock()
        with _notice_lock:
            due = force or now - _rollover_notices.get(self.registry_key, float("-inf")) >= ROLLOVER_NOTICE_EVERY_S
            if due:
                _rollover_notices[self.registry_key] = now
        if due:
            try:
                sys.stderr.write(f"[jarvis_logger] {text}\n")
                sys.stderr.flush()
            except Exception:  # pylint: disable=broad-exception-caught  # stderr gone at exit
                pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._follow_path()
        except Exception:  # pylint: disable=broad-exception-caught  # the check never costs a record
            pass
        super().emit(record)

    def _follow_path(self) -> None:
        """At most once per PATH_CHECK_EVERY_S: when the path no longer names the open file (another process rotated
        or removed it), the stream is closed and the path opened again on this record."""
        stream = self.stream
        if stream is None:
            return
        now = self._clock()
        if now < self._next_path_check:
            return
        self._next_path_check = now + PATH_CHECK_EVERY_S
        name = getattr(stream, "name", None)
        if not isinstance(name, str):
            return
        try:
            on_disk: int | None = os.stat(name).st_ino
        except FileNotFoundError:
            on_disk = None
        except OSError:
            return
        try:
            own = os.fstat(stream.fileno()).st_ino
        except (OSError, ValueError, AttributeError):
            return
        if on_disk == own:
            return
        self.stream = None  # type: ignore[assignment]
        try:
            stream.flush()
        finally:
            stream.close()


# One handler per file for the life of the process. A closed handler stays registered: logging.config.dictConfig
# (uvicorn calls it in the bridge) closes every existing handler while the loggers keep theirs, and a FileHandler in
# append mode reopens its file on the next record. Dropping it here would let a later setup_logger open a second
# handle on the same file, which is what breaks a Windows rollover.
_FILE_HANDLERS: dict[str, SharedRotatingFileHandler] = {}
_FILE_HANDLERS_LOCK = threading.Lock()


def _handler_key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def shared_file_handler(path: str | os.PathLike[str], *, max_bytes: int = MAIN_LOG_MAX_BYTES,
                        backup_count: int = MAIN_LOG_BACKUPS, level: int = logging.NOTSET,
                        formatter: logging.Formatter | None = None) -> SharedRotatingFileHandler:
    """
    K-29 (C-64): the one rotating handler of path in this process; the first call creates it (and the folder), later
    calls return the same object, with level and formatter set to the latest call's (size and backups stay the
    first call's). Every such handler drops C-124 private records below INFO and masks secrets (K-29: a
    RedactingFormatter until a caller sets its own). It stays the file's handler after close() (it reopens the file
    on its next record).
    """
    key = _handler_key(path)
    with _FILE_HANDLERS_LOCK:
        handler = _FILE_HANDLERS.get(key)
        if handler is None:
            full = os.path.abspath(os.fspath(path))
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            handler = SharedRotatingFileHandler(full, max_bytes=max_bytes, backup_count=backup_count)
            handler.addFilter(_PRIVATE_FILE_FILTER)
            handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
            _FILE_HANDLERS[key] = handler
    handler.setLevel(level)
    if formatter is not None:
        handler.setFormatter(formatter)
    return handler


# ---------------------------------------------------------------- the UI log panel (C-64)


class UiLogShipper:
    """
    The UI log lines of this process (K-40 v10): a buffer of at most capacity lines (WARNING+ kept before INFO; on
    overflow the oldest INFO line goes first, then the oldest WARNING, each one counted in dropped) and a token bucket
    of rate lines a second (burst). Every interval its own daemon thread ('ui-log-shipper', its own httpx client,
    never a job or voice loop) sends the queued events (audit_event, at most outbox_max), then at most one
    vortex_log_batch {items <= batch_max, dropped, source} with the body under body_max bytes. Delivered: the
    reported dropped count is settled. Not sent (connect error, 429, 502, 503): the lines go back to the head and the
    next try waits Retry-After (at most 30 s) or a 1..30 s backoff. Uncertain (read timeout, 500, 504) or refused
    (400, 401, 403, 413): the lines count as dropped, so no batch is ever posted twice; a refusal pauses sending
    UI_PAUSE_AFTER_REFUSAL_S with one WARNING per 10 min. offer() never blocks on I/O and never raises.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, capacity: int = UI_BUFFER_MAX,
                 rate: float = UI_LINES_PER_S, burst: float = UI_LINES_BURST, batch_max: int = UI_BATCH_MAX,
                 body_max: int = UI_BATCH_MAX_BYTES, interval: float = UI_FLUSH_EVERY_S,
                 outbox_max: int = UI_EVENT_OUTBOX_MAX,
                 post: Callable[[str, Any, float], tuple[str, str, float | None]] | None = None) -> None:
        self._clock = clock
        self.capacity = capacity
        self.rate = rate
        self.burst = burst
        self.batch_max = batch_max
        self.body_max = body_max
        self.interval = interval
        self.outbox_max = outbox_max
        self._post_override = post
        self.source = "agent"
        self._atexit_registered = False
        self._reset()

    def _reset(self) -> None:
        """Empty buffers, fresh locks and no thread (also in a forked child)."""
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._high: deque[dict[str, Any]] = deque()
        self._info: deque[dict[str, Any]] = deque()
        self._outbox: deque[tuple[str, Any]] = deque()
        self._tokens = float(self.burst)
        self._refilled = self._clock()
        self.dropped = 0      # lines lost since the last delivered report: sent as 'dropped' in the next batch
        self.offered = 0      # lines offered in all
        self.delivered = 0    # lines in delivered batches
        self.discarded = 0    # lines evicted or lost with an uncertain or refused batch
        self.events_dropped = 0
        self._resume_at = float("-inf")
        self._backoff_s = 0.0
        self._last_refusal_warning = float("-inf")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid: int | None = None
        self._client: Any = None

    # ------------------------------------------------------------ producers (any thread)

    def offer(self, item: dict[str, Any], *, high: bool | None = None) -> None:
        """Queues one line {text, category, level, ts}; high (default: level WARNING or above) is kept longer."""
        if high is None:
            high = item.get("level") in ("WARNING", "ERROR", "CRITICAL")
        with self._lock:
            self.offered += 1
            (self._high if high else self._info).append(item)
            self._trim_locked()

    def offer_event(self, event_type: str, payload: Any) -> None:
        """Queues one event (audit_event) for the next round, before the log batch; at most outbox_max wait."""
        with self._lock:
            self._outbox.append((event_type, payload))
            while len(self._outbox) > self.outbox_max:
                self._outbox.popleft()
                self.events_dropped += 1

    def pending(self) -> int:
        with self._lock:
            return len(self._high) + len(self._info)

    def snapshot(self) -> list[dict[str, Any]]:
        """The queued lines, WARNING+ first (tests and diagnostics)."""
        with self._lock:
            return [*self._high, *self._info]

    def queued_events(self) -> list[tuple[str, Any]]:
        with self._lock:
            return list(self._outbox)

    def _trim_locked(self) -> None:
        while len(self._high) + len(self._info) > self.capacity:
            (self._info if self._info else self._high).popleft()
            self.dropped += 1
            self.discarded += 1

    # ------------------------------------------------------------ the sender (its own thread)

    def start(self, source: str = "agent") -> bool:
        """Starts the sender thread once per process; False when it already runs here."""
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive() and self._pid == os.getpid():
                return False
            self.source = source
            self._stop = threading.Event()
            self._pid = os.getpid()
            self._thread = threading.Thread(target=self._run, name="ui-log-shipper", daemon=True)
            self._thread.start()
            if not self._atexit_registered:
                atexit.register(self.flush_at_exit)
                self._atexit_registered = True
        return True

    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and self._pid == os.getpid()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pylint: disable=broad-exception-caught  # the sender never dies
                pass
            self._stop.wait(self.interval)

    def tick(self, *, timeout: float = UI_POST_TIMEOUT_S, ignore_bucket: bool = False,
             max_events: int | None = None) -> bool:
        """One round: at most max_events queued events (default UI_OUTBOX_PER_TICK; the rest wait for the next
        round, LS-1), then at most one log batch. True when something was posted."""
        if self._clock() < self._resume_at:
            return False
        budget = UI_OUTBOX_PER_TICK if max_events is None else max_events
        posted = False
        while budget > 0:
            with self._lock:
                if not self._outbox:
                    break
                event_type, payload = self._outbox.popleft()
            outcome, reason, retry_after = self._post(event_type, payload, timeout)
            posted = True
            budget -= 1
            if outcome == OUTCOME_NOT_SENT:
                with self._lock:
                    self._outbox.appendleft((event_type, payload))
                    while len(self._outbox) > self.outbox_max:
                        self._outbox.pop()
                        self.events_dropped += 1
                self._back_off(retry_after)
                return posted
            if outcome == OUTCOME_REFUSED:
                with self._lock:
                    self.events_dropped += 1  # consumed and never retried (SP-5/LS-4: the loss is counted)
                self._refused(event_type, reason)
                return posted
            if outcome == OUTCOME_UNCERTAIN:
                with self._lock:
                    self.events_dropped += 1  # the bridge may have it: never sent twice (SP-5/LS-4)
        with self._lock:
            waiting = len(self._high) + len(self._info)
            items, reported = self._take_batch_locked(ignore_bucket)
        if not items and (waiting or not reported):
            return posted  # nothing to say, or lines wait for the bucket (a dropped count alone goes when none wait)
        outcome, reason, retry_after = self._post(
            "vortex_log_batch", {"items": items, "dropped": reported, "source": self.source}, timeout)
        with self._lock:
            if outcome == OUTCOME_DELIVERED:
                self.dropped = max(0, self.dropped - reported)
                self.delivered += len(items)
            elif outcome == OUTCOME_NOT_SENT:
                self._put_back_locked(items)
            else:
                self.dropped += len(items)
                self.discarded += len(items)
        if outcome == OUTCOME_DELIVERED:
            self._backoff_s = 0.0
        elif outcome == OUTCOME_NOT_SENT:
            self._back_off(retry_after)
        elif outcome == OUTCOME_REFUSED:
            self._refused("vortex_log_batch", reason)
        elif items:  # OUTCOME_UNCERTAIN (COR-1): the lines are lost with no retry; say so, rate-limited
            self._uncertain("vortex_log_batch", reason, len(items))
        return True

    def _take_batch_locked(self, ignore_bucket: bool) -> tuple[list[dict[str, Any]], int]:
        now = self._clock()
        self._tokens = min(float(self.burst), self._tokens + max(0.0, now - self._refilled) * self.rate)
        self._refilled = now
        budget = self.batch_max if ignore_bucket else min(self.batch_max, int(self._tokens))
        chosen: list[dict[str, Any]] = []
        size = 128  # {"type":"vortex_log_batch","payload":{"items":[],"dropped":N,"source":"..."}}
        while budget > 0 and (self._high or self._info):
            queue = self._high if self._high else self._info
            item_size = _json_size(queue[0]) + 1
            if chosen and size + item_size > self.body_max:
                break
            chosen.append(queue.popleft())
            size += item_size
            budget -= 1
        if not ignore_bucket:
            self._tokens -= len(chosen)
        # K-40 promises order only within one batch (sorted by ts here). Across batches WARNING+ ships first, so
        # under a sustained WARNING flow older INFO lines can arrive a batch later than younger WARNINGs; the UI
        # may sort its panel by the ts field (C-64 review COR-4, X-lane).
        chosen.sort(key=lambda item: item.get("ts") or 0)
        return chosen, self.dropped

    def _put_back_locked(self, items: list[dict[str, Any]]) -> None:
        """Lines of a batch the bridge never got: back at the head of their queues, in order; the bucket refunded."""
        for item in reversed(items):
            high = item.get("level") in ("WARNING", "ERROR", "CRITICAL")
            (self._high if high else self._info).appendleft(item)
        self._tokens = min(float(self.burst), self._tokens + len(items))
        self._trim_locked()  # newer lines arrived meanwhile: the usual order, oldest INFO first

    def _back_off(self, retry_after: float | None) -> None:
        if retry_after is not None:
            wait = min(max(float(retry_after), 0.0), UI_RETRY_MAX_S)
        else:
            self._backoff_s = min(max(self._backoff_s * 2, 1.0), UI_RETRY_MAX_S)
            wait = self._backoff_s
        self._resume_at = self._clock() + wait

    def _refused(self, event_type: str, reason: str) -> None:
        now = self._clock()
        self._resume_at = now + UI_PAUSE_AFTER_REFUSAL_S
        if now - self._last_refusal_warning >= UI_REFUSAL_WARN_EVERY_S:
            self._last_refusal_warning = now
            logging.getLogger("UI-LOG-SHIPPER").warning(
                "The bridge refused %s (%s): UI log lines paused %.0f s; every line stays in the log file",
                event_type, reason or "?", UI_PAUSE_AFTER_REFUSAL_S)

    def _uncertain(self, event_type: str, reason: str, lost: int) -> None:
        """C-64 review COR-1: an uncertain batch (read timeout, 500, 504, or any exception before the POST) is
        dropped with no retry; one WARNING per UI_REFUSAL_WARN_EVERY_S (the window shared with _refused) says
        so, so an hour of silent read timeouts is visible in the log file."""
        now = self._clock()
        if now - self._last_refusal_warning >= UI_REFUSAL_WARN_EVERY_S:
            self._last_refusal_warning = now
            logging.getLogger("UI-LOG-SHIPPER").warning(
                "Delivery of %s to the bridge is uncertain (%s): %d UI log line(s) lost (a batch is never "
                "posted twice); every line stays in the log file", event_type, reason or "?", lost)

    def _post(self, event_type: str, payload: Any, timeout: float) -> tuple[str, str, float | None]:
        if self._post_override is not None:
            return self._post_override(event_type, payload, timeout)
        try:
            # pylint: disable=import-outside-toplevel, cyclic-import  # jarvis_bridge imports this module
            import httpx

            from services.utils import jarvis_bridge
            if self._client is None:
                self._client = httpx.Client(timeout=timeout)
            return jarvis_bridge.post_notify_blocking(event_type, payload, client=self._client, timeout=timeout)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return OUTCOME_UNCERTAIN, type(exc).__name__, None

    def flush_at_exit(self) -> None:
        """atexit: one last batch (the bucket ignored) with a short timeout, in the process that started the
        sender. Bounded (LS-1): at most UI_EXIT_OUTBOX_MAX queued events go first (the audit lines are already
        in security.log), then the one batch. When the sender thread is still alive after the join (a slow
        in-flight post), it owns the last flush and no second tick runs here: two concurrent ticks would read
        the same self.dropped and the bridge would double-count it (COR-3)."""
        if self._pid != os.getpid():
            return
        self.stop(timeout=0.5)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            return
        self._resume_at = float("-inf")
        try:
            self.tick(timeout=UI_EXIT_FLUSH_TIMEOUT_S, ignore_bucket=True, max_events=UI_EXIT_OUTBOX_MAX)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _after_fork_in_child(self) -> None:
        self._reset()


def _json_size(item: Any) -> int:
    try:
        return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace"))
    except (TypeError, ValueError):
        return UI_TEXT_MAX * 4


# The process's UI log lines; its thread runs only after start_ui_log_shipping().
ui_log_shipper = UiLogShipper()


def start_ui_log_shipping(source: str = "agent") -> bool:
    """
    C-64: starts sending this process's UI log lines (once per process; a forked job process starts its own). The
    agent calls it in src/core/agent.py before run_app and at the top of agent_runner.entrypoint. A process that
    never calls it (tests, scripts, ensure_room.py, a bridge) keeps at most UI_BUFFER_MAX lines in memory and never
    posts.
    """
    return ui_log_shipper.start(source=source)


def _ui_category(name: str) -> str:
    return name.replace("JARVIS-", "").replace("-RUNNER", "").upper()


class UIBridgeHandler(logging.Handler):
    """
    Forwards log lines to the STONIX UI log panel (K-29, K-40 v10): records at or above UI_LOG_LEVEL (default
    WARNING), plus the INFO lines of the loggers in UI_LOG_INFO_ALLOW (except UI_NOISE_INFO_TEMPLATES). Never the
    bridge's and notify path's own loggers or the chatty libraries, and never a C-124 private line below WARNING.
    emit() only appends {text (masked message, at most UI_TEXT_MAX chars), category, level, ts} to the shipper's
    buffer: no I/O, never raises. The re-entrancy guard is per thread.
    """

    def __init__(self, level: int = logging.NOTSET, *, shipper: UiLogShipper | None = None,
                 min_level: int | None = None, info_allow: Iterable[str] | None = None) -> None:
        super().__init__(level)
        self.setFormatter(UIMessageFormatter())
        self._shipper = shipper
        self._local = threading.local()
        self.min_level = DEFAULT_UI_LOG_LEVEL
        self.info_allow: frozenset[str] = DEFAULT_UI_LOG_INFO_ALLOW
        self.configure(min_level=min_level, info_allow=info_allow)

    def configure(self, *, min_level: int | None = None, info_allow: Iterable[str] | None = None) -> None:
        """Reads UI_LOG_LEVEL and UI_LOG_INFO_ALLOW (unless given)."""
        self.min_level = ui_log_level() if min_level is None else int(min_level)
        self.info_allow = ui_log_info_allow() if info_allow is None else frozenset(info_allow)

    def wants(self, record: logging.LogRecord) -> bool:
        """True for a record the UI log panel gets."""
        name = record.name
        if name in UI_SKIP_LOGGERS or any(name == prefix or name.startswith(prefix + ".")
                                          for prefix in UI_SKIP_PREFIXES):
            return False
        level = record.levelno
        if level < logging.WARNING and is_private_record(record):
            return False  # C-124: never a title, clipboard text, location or tool argument
        if level >= self.min_level:
            return True
        return (level == logging.INFO and name in self.info_allow
                and not (isinstance(record.msg, str) and record.msg in UI_NOISE_INFO_TEMPLATES))

    def handle(self, record: logging.LogRecord) -> Any:
        """As logging.Handler.handle, without the handler lock: this one handler serves every logger and thread, and
        emit() is thread-safe on its own (the shipper's lock, a per-thread guard), so no thread waits for another's
        formatting."""
        rv = self.filter(record)
        if isinstance(rv, logging.LogRecord):
            record = rv
        if rv:
            self.emit(record)
        return rv

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "busy", False):
            return  # a record logged while one is being handled on this thread
        if bridge_suppressed.get():
            return  # K-57 (C-39): a probe or canary job's lines never reach the dashboard's log panel
        self._local.busy = True
        try:
            if self.wants(record):
                text = self.format(record)[:UI_TEXT_MAX]
                item = {"text": text, "category": _ui_category(record.name), "level": record.levelname,
                        "ts": round(record.created, 3)}
                (self._shipper or ui_log_shipper).offer(item, high=record.levelno >= logging.WARNING)
        except Exception:  # pylint: disable=broad-exception-caught  # a UI line is never worth an error
            pass
        finally:
            self._local.busy = False


# The one UI handler of this process, attached to every setup_logger logger in the agent role.
_UI_HANDLER = UIBridgeHandler()


def ui_bridge_handler() -> UIBridgeHandler:
    """The process's shared UIBridgeHandler."""
    return _UI_HANDLER


def _after_fork_in_child() -> None:
    """A forked child (a LiveKit job process on Linux) starts with no shipper thread, empty buffers and fresh locks;
    its entrypoint starts its own shipper."""
    global _FILE_HANDLERS_LOCK, _notice_lock  # pylint: disable=global-statement
    _FILE_HANDLERS_LOCK = threading.Lock()
    _notice_lock = threading.Lock()
    ui_log_shipper._after_fork_in_child()  # pylint: disable=protected-access


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def setup_logger(name: str, log_file: str | Path | None = None,
                 level: int | None = None, json_format: bool = False) -> logging.Logger:
    """
    The logger name with (K-29, C-64): its own console handler at LOG_LEVEL (or level), the process's one shared
    rotating handler of log_file (default LOG_FILE) at LOG_FILE_LEVEL (default INFO, 10 MiB x 5) and, in the agent
    role only, the shared UIBridgeHandler. Without an explicit level the logger passes min(LOG_LEVEL,
    LOG_FILE_LEVEL, INFO while the UI handler is attached), so allow-listed INFO lines reach the UI even at
    LOG_LEVEL=WARNING. Calling it again replaces the logger's handlers; the shared file handler stays open.
    """
    if json_format is False:
        json_format = os.getenv("LOG_STRUCTURED", "false").lower() == "true"

    explicit_level = level is not None
    if level is None:
        level = _env_level("LOG_LEVEL", logging.INFO) if _HAS_CONFIG else logging.INFO
    file_level = log_file_level()

    if log_file is None:
        if _HAS_CONFIG:
            log_file = config.log_file
        else:
            log_file = log_dir() / "jarvis_default.log"

    # Create formatter (both kinds mask secrets, K-29)
    formatter: RedactingFormatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = RedactingFormatter(_LOG_FORMAT)

    console_handler: logging.StreamHandler[Any] = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    file_handler = shared_file_handler(log_file, max_bytes=MAIN_LOG_MAX_BYTES, backup_count=MAIN_LOG_BACKUPS,
                                       level=file_level, formatter=formatter)

    # Only the agent forwards its logs to the UI: the bridge must not POST its own logs to itself (C-14).
    with_ui = runtime_role() == ROLE_AGENT

    if with_ui:
        _UI_HANDLER.configure()  # the latest UI_LOG_LEVEL / UI_LOG_INFO_ALLOW

    logger: logging.Logger = logging.getLogger(name)
    if explicit_level:
        logger.setLevel(level)
    else:
        floor = min(level, file_level)
        if with_ui:
            floor = min(floor, logging.INFO, _UI_HANDLER.min_level)
        logger.setLevel(floor)
    if not any(isinstance(f, PrivateInfoFilter) for f in logger.filters):
        logger.addFilter(_PRIVATE_FILTER)  # C-124: private INFO lines become DEBUG

    # Replace the handlers of an earlier setup. A per-logger file handler of older code is closed, so its file
    # handle is not left open; the shared ones stay open for the other loggers.
    for old in list(logger.handlers):
        logger.removeHandler(old)
        if isinstance(old, logging.FileHandler) and not isinstance(old, SharedRotatingFileHandler):
            old.close()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    if with_ui:
        logger.addHandler(_UI_HANDLER)

    # Prevent propagation to the root logger
    logger.propagate = False

    return logger


# Shorthand for general use
jarvis_log: logging.Logger = setup_logger("JARVIS")
bridge_log: logging.Logger = setup_logger("BRIDGE", log_dir() / "bridge.log")
runner_log: logging.Logger = setup_logger("RUNNER", log_dir() / "runner.log")
ui_shipper_log: logging.Logger = setup_logger("UI-LOG-SHIPPER")
