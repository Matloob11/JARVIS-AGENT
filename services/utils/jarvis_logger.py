import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

from services.utils.jarvis_security import redact_secrets

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

# Internal UI Bridge link for automated logging
_BRIDGE_NOTIFY_LOG: Callable[[str, str], Any] | None = None

_bg_tasks: set[asyncio.Task[Any]] = set()

_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Folder of the fixed-name logs below (bridge.log, runner.log, and jarvis_default.log when the config module
# is missing): LOG_DIR, default 'logs' (relative to the working directory, exactly as before). C-138: the
# test suite points it at a temp folder, so no test writes the live logs/.
LOG_DIR_ENV = "LOG_DIR"
DEFAULT_LOG_DIR = "logs"


def log_dir() -> Path:
    """LOG_DIR, or 'logs' when it is unset or empty."""
    return Path((os.getenv(LOG_DIR_ENV) or "").strip() or DEFAULT_LOG_DIR)


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


# C-124 step 8: window titles, clipboard text, locations and tool arguments are never logged at INFO,
# because UIBridgeHandler forwards INFO and above to the UI log panel. A call site marks such a line with
# extra=PRIVATE (or logs it at DEBUG); the INFO templates below, which tool modules use today, are
# demoted the same way. Demoted lines stay available at LOG_LEVEL=DEBUG on the console only.
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
    # reminder and task text (set_reminder's message, and the same text when it falls due)
    "Setting reminder: %s at %s",
    "\U0001f514 Triggering proactive reminder: %s",
    "\U0001f514 Task Due: %s",
    "\U0001f514 Notifying UI of due task: %s",
    # locations
    "City ke liye weather fetch kiya ja raha hai: %s (forecast=%s)",
    "Attempting weather fallback via search for: %s",
    "Updating map to: %s (zoom=%s)",
    "Map sync successful for %s",
})


def is_private_record(record: logging.LogRecord) -> bool:
    """True for a line that carries a window title, clipboard text, a location or tool arguments."""
    return bool(getattr(record, PRIVATE_ATTR, False)) or (
        isinstance(record.msg, str) and record.msg in PRIVATE_INFO_TEMPLATES)


class PrivateInfoFilter(logging.Filter):
    """
    A logger filter (it runs before the handlers' level checks): a private INFO record becomes DEBUG, so
    the console at INFO, the file at ERROR and UIBridgeHandler at INFO all leave it out. Higher levels
    are kept as they are: a WARNING or ERROR line must stay visible.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.INFO and is_private_record(record):
            record.levelno, record.levelname = logging.DEBUG, logging.getLevelName(logging.DEBUG)
        return True


_PRIVATE_FILTER = PrivateInfoFilter()


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


class UIBridgeHandler(logging.Handler):
    """
    Custom logging handler that forwards logs to the STONIX UI Bridge.
    Only forwards INFO and higher levels to avoid UI noise.
    """
    _emitting: bool = False  # Class-level re-entrancy guard

    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self.setFormatter(UIMessageFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        """Forwards the log record to the UI Bridge if appropriate."""
        global _BRIDGE_NOTIFY_LOG

        # Re-entrancy guard: prevent logger → bridge → logger infinite loop
        if UIBridgeHandler._emitting:
            return

        # Avoid recursion and noise
        if record.name in ["UI-BRIDGE", "JARVIS-BRIDGE", "BRIDGE-NOTIFIER", "httpx"]:
            return

        if record.levelno < logging.INFO:
            return
        if record.levelno == logging.INFO and is_private_record(record):
            return  # C-124: never a title, clipboard text or tool argument in the UI log panel

        UIBridgeHandler._emitting = True
        try:
            if _BRIDGE_NOTIFY_LOG is None:
                # pylint: disable=import-outside-toplevel, cyclic-import
                from services.utils.jarvis_bridge import notify_log
                _BRIDGE_NOTIFY_LOG = notify_log

            message: str = self.format(record) # Bare message (masked) for UI readability
            category: str = record.name.replace("JARVIS-", "").replace("-RUNNER", "").upper()

            try:
                loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
                if _BRIDGE_NOTIFY_LOG:
                    t = loop.create_task(_BRIDGE_NOTIFY_LOG(message, category))
                    _bg_tasks.add(t)
                    t.add_done_callback(_bg_tasks.discard)
            except (RuntimeError, AssertionError):
                # No running event loop or loop closed
                pass
        except Exception: # pylint: disable=broad-exception-caught
            pass
        finally:
            UIBridgeHandler._emitting = False


# Global log queue for batch writing (Sir Matloob's PC Performance Patch)
_log_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

async def _batch_writer_worker():
    """Background task that writes logs in batches with context-switching awareness."""
    while True:
        try:
            # Wait for at least one log
            name, formatted_msg = await _log_queue.get()
            batch = [(name, formatted_msg)]
            
            # Try to grab up to 9 more if they are ready (Batch size: 10)
            for _ in range(9):
                try:
                    if _log_queue.empty():
                        break
                    batch.append(_log_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            
            # Map batches to handlers
            by_logger = {}
            for l_name, msg in batch:
                if l_name not in by_logger:
                    by_logger[l_name] = []
                by_logger[l_name].append(msg)
            
            # Write each logger's batch in one thread hop
            def _write_batches(payloads):
                for l_name, msgs in payloads.items():
                    log_obj = logging.getLogger(l_name)
                    for handler in log_obj.handlers:
                        if isinstance(handler, (logging.FileHandler, RotatingFileHandler)):
                            with open(handler.baseFilename, 'a', encoding=handler.encoding) as f:
                                for m in msgs:
                                    f.write(m + '\n')
            
            await asyncio.to_thread(_write_batches, by_logger)
            
            for _ in range(len(batch)):
                _log_queue.task_done()
                
        except Exception:
            await asyncio.sleep(1)

_writer_task = None

def setup_logger(name: str, log_file: str | Path | None = None,
                 level: int | None = None, json_format: bool = False) -> logging.Logger:
    """
    Sets up a logger with console and rotating file handlers.
    Integration with JarvisConfig for levels and paths.
    """
    global _writer_task
    if _writer_task is None:
        try:
            loop = asyncio.get_running_loop()
            _writer_task = loop.create_task(_batch_writer_worker())
        except RuntimeError:
            pass

    # Custom Handler to feed the queue instead of direct writing
    class BatchedFileHandler(logging.Handler):
        def __init__(self, level: int = logging.NOTSET) -> None:
            super().__init__(level)
            self.setFormatter(RedactingFormatter(_LOG_FORMAT))

        def emit(self, record):
            try:
                msg = self.format(record)
                # We can't await in sync emit, so use call_soon_threadsafe or ignore if loop isn't up
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(_log_queue.put_nowait, (name, msg))
                except (RuntimeError, asyncio.QueueFull):
                    pass
            except Exception:
                self.handleError(record)

    # Rest of original setup_logger logic (simplified for diff)
    if json_format is False:
        json_format = os.getenv("LOG_STRUCTURED", "false").lower() == "true"

    if level is None:
        if _HAS_CONFIG:
            level_str: str = os.getenv("LOG_LEVEL", "INFO").upper()
            level = int(getattr(logging, level_str, logging.INFO))
        else:
            level = logging.INFO

    if log_file is None:
        if _HAS_CONFIG:
            log_file = config.log_file
        else:
            log_file = log_dir() / "jarvis_default.log"

    log_path: Path = Path(log_file)
    
    # Phase 7: Ensure log directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Create formatter (both kinds mask secrets, K-29)
    formatter: RedactingFormatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = RedactingFormatter(_LOG_FORMAT)

    # Console handler
    console_handler: logging.StreamHandler[Any] = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # File handler (RotatingFileHandler prevents infinite growth)
    # Defaulting to 10MB per file, 5 backups
    file_handler: RotatingFileHandler = RotatingFileHandler(
        str(log_path),
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    # File logs focus on errors/warnings in prod
    file_handler.setLevel(logging.ERROR)

    logger: logging.Logger = logging.getLogger(name)
    logger.setLevel(level)
    if not any(isinstance(f, PrivateInfoFilter) for f in logger.filters):
        logger.addFilter(_PRIVATE_FILTER)  # C-124: private INFO lines become DEBUG

    # Clear existing handlers to prevent duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Only the agent forwards its logs to the UI: the bridge must not POST its own logs to itself (C-14).
    if runtime_role() == ROLE_AGENT and not any(isinstance(h, UIBridgeHandler) for h in logger.handlers):
        ui_handler: UIBridgeHandler = UIBridgeHandler()
        ui_handler.setLevel(logging.INFO)
        logger.addHandler(ui_handler)

    # Prevent propagation to the root logger
    logger.propagate = False

    return logger


# Shorthand for general use
jarvis_log: logging.Logger = setup_logger("JARVIS")
bridge_log: logging.Logger = setup_logger("BRIDGE", log_dir() / "bridge.log")
runner_log: logging.Logger = setup_logger("RUNNER", log_dir() / "runner.log")
