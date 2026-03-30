import asyncio
import json
import logging
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

# Import config safely
try:
    from services.utils.jarvis_config import config
    _HAS_CONFIG: bool = True
except ImportError:
    # Fallback for early startup or standalone scripts
    config = cast(Any, None)
    _HAS_CONFIG = False

# Internal UI Bridge link for automated logging
_BRIDGE_NOTIFY_LOG: Callable[[str, str], Any] | None = None

_bg_tasks: set[asyncio.Task[Any]] = set()

class JSONFormatter(logging.Formatter):
    """
    Structured JSON formatter for production-ready logs.
    """
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)

class UIBridgeHandler(logging.Handler):
    """
    Custom logging handler that forwards logs to the STONIX UI Bridge.
    Only forwards INFO and higher levels to avoid UI noise.
    """
    _emitting: bool = False  # Class-level re-entrancy guard

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

        UIBridgeHandler._emitting = True
        try:
            if _BRIDGE_NOTIFY_LOG is None:
                # pylint: disable=import-outside-toplevel, cyclic-import
                from services.utils.jarvis_bridge import notify_log
                _BRIDGE_NOTIFY_LOG = notify_log

            message: str = record.getMessage() # Sending raw message for UI readability
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


def setup_logger(name: str, log_file: str | Path | None = None,
                 level: int | None = None, json_format: bool = False) -> logging.Logger:
    """
    Sets up a logger with console and rotating file handlers.
    Integration with JarvisConfig for levels and paths.
    """
    if json_format is False:
        # Default to True ONLY if environment variable is set
        import os
        json_format = os.getenv("LOG_STRUCTURED", "false").lower() == "true"

    if level is None:
        import os
        if _HAS_CONFIG:
            level_str: str = os.getenv("LOG_LEVEL", "INFO").upper()
            level = int(getattr(logging, level_str, logging.INFO))
        else:
            level = logging.INFO

    if log_file is None:
        import os
        if _HAS_CONFIG:
            log_file = os.getenv("LOG_FILE", "logs/jarvis_main.log")
        else:
            log_file = "logs/jarvis_default.log"

    log_path: Path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Create formatter
    formatter: logging.Formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        )

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

    # Clear existing handlers to prevent duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Attach UI Bridge handler if config allows and not already attached
    if not any(isinstance(h, UIBridgeHandler) for h in logger.handlers):
        ui_handler: UIBridgeHandler = UIBridgeHandler()
        ui_handler.setLevel(logging.INFO)
        logger.addHandler(ui_handler)

    # Prevent propagation to the root logger
    logger.propagate = False

    return logger


# Shorthand for general use
jarvis_log: logging.Logger = setup_logger("JARVIS")
bridge_log: logging.Logger = setup_logger("BRIDGE", "logs/bridge.log")
runner_log: logging.Logger = setup_logger("RUNNER", "logs/runner.log")
