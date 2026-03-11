"""
# services/utils/jarvis_audit.py
Enterprise Audit Trail and Security Monitoring.
Logs security events and threat detections to a dedicated audit trail.
"""

import logging
import os

# Setup dedicated security logger
security_logger = logging.getLogger("JARVIS-AUDIT")
security_logger.setLevel(logging.INFO)

# File handler for security.log
if not os.path.exists("logs"):
    os.makedirs("logs")

fh = logging.FileHandler("logs/security.log")
formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s')
fh.setFormatter(formatter)
security_logger.addHandler(fh)


class JarvisAudit:
    """
    Enterprise Audit Trail and Security Monitoring.
    """

    @classmethod
    def log_event(cls, event_type: str, details: str, severity: str = "INFO"):
        """Log a security event to the audit trail."""
        message = f"EVENT={event_type} | DETAILS={details}"
        if severity == "CRITICAL":
            security_logger.critical(message)
        elif severity == "WARNING":
            security_logger.warning(message)
        else:
            security_logger.info(message)

    @classmethod
    def log_threat(cls, threat_description: str, source: str = "UserProxy"):
        """Log a detected threat with higher priority."""
        cls.log_event("THREAT_DETECTED",
                      f"SOURCE={source} | DESC={threat_description}",
                      "CRITICAL")


# Global singleton
jarvis_audit = JarvisAudit()
