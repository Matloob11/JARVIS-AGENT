"""
# jarvis_logger.py
Centralized Logging Utility for JARVIS

Provides a unified logging configuration where:
- INFO messages go to the console.
- ERROR and CRITICAL messages (including tracebacks) go to 'jarvis_errors.log'.
"""

import logging
from logging.handlers import RotatingFileHandler


def setup_logger(name, log_file="jarvis_errors.log", level=logging.INFO):
    """
    Sets up a logger with console and file handlers.
    Errors are directed to the specified log file.
    """
    # Ensure log directory exists if we decide to move it, but for now PROJECT_ROOT is fine

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Console handler (Shows INFO and above)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # File handler (Records ERROR and above to the log file)
    # RotatingFileHandler prevents the log file from growing infinitely
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,  # 5MB
        backupCount=2,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.ERROR)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Clear existing handlers to prevent duplicates during module reloads
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Prevent propagation to the root logger to avoid double logging
    logger.propagate = False

    return logger
