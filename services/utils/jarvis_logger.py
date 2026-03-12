"""
# jarvis_logger.py
Centralized Logging Utility for JARVIS
"""

import logging
import os
from logging.handlers import RotatingFileHandler
try:
    from services.utils.jarvis_config import config
except ImportError:
    # Fallback for early startup or standalone scripts
    config = None


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

    # Prevent propagation to the root logger
    logger.propagate = False

    return logger


# Shorthand for general use
jarvis_log = setup_logger("JARVIS")
bridge_log = setup_logger("BRIDGE", "logs/bridge.log")
runner_log = setup_logger("RUNNER", "logs/runner.log")
