"""
# jarvis_logger.py
Centralized Logging Utility for JARVIS
"""

import logging
import os
import asyncio
from logging.handlers import RotatingFileHandler
try:
    from services.utils.jarvis_config import config
except ImportError:
    # Fallback for early startup or standalone scripts
    config = None

# Internal UI Bridge link for automated logging
_BRIDGE_NOTIFY_LOG = None

class UIBridgeHandler(logging.Handler):
    """
    Custom logging handler that forwards logs to the STONIX UI Bridge.
    Only forwards INFO and higher levels to avoid UI noise.
    """
    _emitting = False  # Class-level re-entrancy guard

    def emit(self, record):
        # pylint: disable=global-statement
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

            message = self.format(record)
            category = record.name.replace("JARVIS-", "").replace("-RUNNER", "").upper()

            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(_BRIDGE_NOTIFY_LOG(message, category))
            except (RuntimeError, AssertionError):
                # No running event loop or loop closed
                pass
        except Exception: # pylint: disable=broad-exception-caught
            pass
        finally:
            UIBridgeHandler._emitting = False


def setup_logger(name, log_file=None, level=None):
    """
    Sets up a logger with console and rotating file handlers.
    Integration with JarvisConfig for levels and paths.
    """
    if level is None:
        if config:
            level_str = os.getenv("LOG_LEVEL", "INFO").upper()
            level = getattr(logging, level_str, logging.INFO)
        else:
            level = logging.INFO

    if log_file is None:
        if config:
            log_file = os.getenv("LOG_FILE", "logs/jarvis_main.log")
        else:
            log_file = "logs/jarvis_default.log"

    # Ensure log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # File handler (RotatingFileHandler prevents infinite growth)
    # Defaulting to 10MB per file, 5 backups
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    # File logs focus on errors/warnings in prod
    file_handler.setLevel(logging.ERROR)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Clear existing handlers to prevent duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Attach UI Bridge handler if config allows and not already attached
    if not any(isinstance(h, UIBridgeHandler) for h in logger.handlers):
        ui_handler = UIBridgeHandler()
        ui_handler.setLevel(logging.INFO)
        logger.addHandler(ui_handler)

    # Prevent propagation to the root logger
    logger.propagate = False

    return logger


# Shorthand for general use
jarvis_log = setup_logger("JARVIS")
bridge_log = setup_logger("BRIDGE", "logs/bridge.log")
runner_log = setup_logger("RUNNER", "logs/runner.log")
