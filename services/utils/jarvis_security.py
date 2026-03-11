"""
# services/utils/jarvis_security.py
Security module for JARVIS, handling input sanitization and threat detection.
"""
import os
import re
import hmac
import hashlib
import logging
from typing import Optional

logger = logging.getLogger("JARVIS-SECURITY")


class VortexGuard:
    """
    Advanced Threat Protection layer for JARVIS.
    Detects prompt injection, malicious code, and sanitizes input.
    """

    # Common prompt injection patterns
    INJECTION_PATTERNS = [
        r"ignore all previous instructions",
        r"system prompt",
        r"database administrative",
        r"sudo rm -rf",
        r"format c:",
        r"delete all files",
        r"reveal your backend",
        r"bypass security"
    ]

    @classmethod
    def sanitize_input(cls, text: str) -> str:
        """Sanitize user input to prevent XSS or basic command injection."""
        if not text:
            return ""
        # Remove potentially dangerous characters for shell-like contexts
        sanitized = re.sub(r'[;&|`$]', '', text)
        return sanitized.strip()

    @classmethod
    def detect_threat(cls, text: str) -> Optional[str]:
        """Detect potential threats in user queries."""
        text_lower = text.lower()
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, text_lower):
                return f"Prompt Injection Pattern Detected: {pattern}"

        # Check for extremely long inputs (Potential DoS/Context Overflow)
        if len(text) > 5000:
            return "Input length exceeds safety threshold (DoS risk)"

        return None


class SecurityManager:
    """
    Zero Trust Security Manager for service-to-service auth and IPC signing.
    """
    _secret_key = os.getenv("VORTEX_SECURITY_TOKEN", "default_secret_token")

    @classmethod
    def generate_signature(cls, payload: str) -> str:
        """Generate HMAC-SHA256 signature for a payload."""
        return hmac.new(
            cls._secret_key.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

    @classmethod
    def verify_signature(cls, payload: str, signature: str) -> bool:
        """Verify the integrity of a signed payload."""
        expected = cls.generate_signature(payload)
        return hmac.compare_digest(expected, signature)


# Global instances
vortex_guard = VortexGuard()
security_manager = SecurityManager()
