"""
# logger.py
Legacy logger redirection for backward compatibility.
Unified implementation moved to jarvis_logger.py
"""

from services.utils.jarvis_logger import setup_logger

# Backward compatibility loggers
jarvis_log = setup_logger("JARVIS", "logs/jarvis_main.log")
bridge_log = setup_logger("BRIDGE", "logs/bridge.log")
runner_log = setup_logger("RUNNER", "logs/runner.log")
