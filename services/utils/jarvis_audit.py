"""
# services/utils/jarvis_audit.py
Enterprise Audit Trail and Security Monitoring.
Logs security events, threat detections and risky tool runs (TOOL_EXEC) to a dedicated audit
trail in <project_root>/logs/security.log.
"""

import logging
import os
import re

from services.utils.jarvis_config import config

SECURITY_LOG_PATH = os.path.join(config.project_root, "logs", "security.log")
_FORMAT = '%(asctime)s - [%(levelname)s] - %(message)s'
# Marks the security.log handler on the logger, which outlives a re-import of this module.
_HANDLER_MARK = "jarvis_security_log"
_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")

# Setup dedicated security logger
security_logger = logging.getLogger("JARVIS-AUDIT")
security_logger.setLevel(logging.INFO)


def _formatter() -> logging.Formatter:
    """K-29 RedactingFormatter once jarvis_logger has it (C-25), else a plain Formatter."""
    from services.utils import jarvis_logger  # pylint: disable=import-outside-toplevel
    formatter_cls = getattr(jarvis_logger, "RedactingFormatter", None)
    if isinstance(formatter_cls, type) and issubclass(formatter_cls, logging.Formatter):
        try:
            return formatter_cls(_FORMAT)
        except TypeError:
            pass
    return logging.Formatter(_FORMAT)


def security_log_handler(path: str = SECURITY_LOG_PATH) -> logging.FileHandler:
    """A security.log file handler; the file is opened on the first record."""
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.setFormatter(_formatter())
    setattr(handler, _HANDLER_MARK, True)
    return handler


def _attach_file_handler() -> None:
    """Adds the security.log handler exactly once, also when this module is imported again."""
    if any(getattr(handler, _HANDLER_MARK, False) for handler in security_logger.handlers):
        return
    try:
        os.makedirs(os.path.dirname(SECURITY_LOG_PATH), exist_ok=True)
        security_logger.addHandler(security_log_handler())
    except OSError as e:
        logging.getLogger(__name__).warning("security.log is not available: %s", e)


_attach_file_handler()


def _one_line(text: object) -> str:
    """Keeps one audit record on one line and masks secrets (K-29) when that is available."""
    clean = " ".join(_CONTROL_RUN.sub(" ", str(text)).split())
    from services.utils import jarvis_security  # pylint: disable=import-outside-toplevel
    redactor = getattr(jarvis_security, "redact_secrets", None)
    if callable(redactor):
        try:
            clean = str(redactor(clean))
        except Exception:  # pylint: disable=broad-exception-caught
            clean = "[redaction failed]"
    return clean


class JarvisAudit:
    """
    Enterprise Audit Trail and Security Monitoring.
    """

    @classmethod
    def log_event(cls, event_type: str, details: str, severity: str = "INFO") -> None:
        """Log a security event to the audit trail."""
        message = f"EVENT={event_type} | DETAILS={details}"
        if severity == "CRITICAL":
            security_logger.critical(message)
        elif severity == "WARNING":
            security_logger.warning(message)
        else:
            security_logger.info(message)

    @classmethod
    def log_threat(cls, threat_description: str, source: str = "UserProxy") -> None:
        """Log a detected threat with higher priority."""
        cls.log_event("THREAT_DETECTED",
                      f"SOURCE={source} | DESC={threat_description}",
                      "CRITICAL")

    @classmethod
    def log_tool(cls, tool: str, risk: str, status: str, summary: str = "") -> None:
        """
        One TOOL_EXEC line for a tool run. summary must not hold raw argument values: the
        plugin wrapper sends the arguments only as a sha256 prefix and a length.
        """
        fields = [f"TOOL={_one_line(tool)}", f"RISK={_one_line(risk)}",
                  f"STATUS={_one_line(status)}"]
        if summary:
            fields.append(_one_line(summary))
        security_logger.info("EVENT=TOOL_EXEC | %s", " | ".join(fields))


# Global singleton
jarvis_audit = JarvisAudit()
log_tool = JarvisAudit.log_tool
