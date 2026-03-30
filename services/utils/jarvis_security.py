"""
# services/utils/jarvis_security.py
Security module for JARVIS, handling input sanitization and threat detection.
"""
import hashlib
import hmac
import logging
import re
import typing
import unicodedata
from typing import Any

logger = logging.getLogger("JARVIS-SECURITY")


class VortexGuard:
    """
    Advanced Threat Protection layer for JARVIS.
    Detects prompt injection, malicious code, and sanitizes input.
    """

    # Common prompt injection patterns
    INJECTION_PATTERNS: typing.ClassVar[list[str]] = [
        r"ignore all previous instructions",
        r"system prompt",
        r"database administrative",
        r"sudo rm -rf",
        r"format c:",
        r"delete all files",
        r"reveal your backend",
        r"bypass security",
    ]

    @classmethod
    def sanitize_input(cls, text: str) -> str:
        """Sanitize user input to prevent XSS or shell command injection."""
        if not text:
            return ""
        # 1. Normalize unicode (prevents 'visual' bypasses)
        text = str(unicodedata.normalize('NFKC', text))

        # 2. Block shell injection sequences (including backticks and shell expansion)
        # We also block null bytes and control characters
        text = re.sub(r'[\0\r\x00-\x1f]', '', text)
        sanitized = re.sub(r'[;&|`$<>]', '', text)
        return sanitized.strip()

    @classmethod
    def validate_tool_args(cls, tool_name: str, args: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        """
        Validates and sanitizes tool arguments before execution.
        Prevents tools from being used as injection vectors and handles recursion depth.
        """
        if depth > 10:
            logger.warning("🚨 SECURITY LIMIT: Recursion depth exceeded for tool %s", tool_name)
            return {"error": "RECURSION_DEPTH_EXCEEDED"}

        logger.debug("🛡️ Validating args for tool: %s (Depth: %d)", tool_name, depth)
        sanitized_args: dict[str, Any] = {}

        for key, value in args.items():
            if isinstance(value, str):
                sanitized_args[key] = cls.sanitize_input(value)
            elif isinstance(value, dict):
                sanitized_args[key] = cls.validate_tool_args(f"{tool_name}.{key}", value, depth + 1)
            elif isinstance(value, list):
                # FIXED: Deep sanitize lists to prevent bypasses
                sanitized_args[key] = [
                    cls.validate_tool_args(f"{tool_name}.{key}[{i}]", v, depth + 1) if isinstance(v, dict)
                    else cls.sanitize_input(v) if isinstance(v, str)
                    else v for i, v in enumerate(value)
                ]
            else:
                sanitized_args[key] = value

        return sanitized_args

    @classmethod
    def validate_tool_output(cls, tool_name: str, output: Any, depth: int = 0) -> Any:
        """
        Hardens the Tool-to-Agent boundary.
        Protects LLM from OOM, poisoning, or malformed tool results.
        """
        # 1. Depth check to prevent RecursionError on malformed tool returns
        if depth > 5:
            return "ERROR: Tool output depth limit exceeded."

        # 2. Critical Protection: Hard size limit (500KB per tool return)
        try:
            # We use repr() as it handles circular refs better by default in many types
            output_str = str(output)[:600000] # Cap early to avoid massive allocation
            if len(output_str) > 500 * 1024:  # 500KB limit
                logger.critical("🚨 SECURITY BLOCK: Tool %s returned massive payload (%d bytes)", tool_name, len(output_str))
                return {"error": "TOOL_PAYLOAD_TOO_LARGE", "message": "Result truncated for safety."}
        except Exception:
            return "ERROR: Undecipherable tool output."

        # 3. Recursive check for dangerous content in structure
        if isinstance(output, str):
            threat = cls.detect_threat(output)
            if threat:
                logger.warning("☣️ Tool %s returned suspicious content: %s", tool_name, threat)
                return "UNSAFE_CONTENT_OVERSIGHT: Result filtered for security reasons."
            return output
        if isinstance(output, dict):
            # FIXED: Handle possible recursion or massive dicts
            return {
                str(k): cls.validate_tool_output(f"{tool_name}.{k}", v, depth + 1)
                for k, v in list(output.items())[:100] # Cap dict size
            }
        if isinstance(output, list):
            # FIXED: Handle massive lists
            return [
                cls.validate_tool_output(f"{tool_name}[{i}]", v, depth + 1)
                for i, v in enumerate(output[:200]) # Cap list size
            ]

        return output

    @classmethod
    def detect_threat(cls, text: str) -> str | None:
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
    @classmethod
    def _get_secret_key(cls) -> str:
        """Lazily fetches the secret key from config to avoid import-time race conditions."""
        from services.utils.jarvis_config import config
        token = config.security_token
        if not token:
            logger.critical("❌ SECURITY VIOLATION: VORTEX_SECURITY_TOKEN not set!")
            return "insecure_default_secret_token_change_immediately"
        return str(token)

    @classmethod
    def generate_signature(cls, payload: str) -> str:
        """Generate HMAC-SHA256 signature for a payload."""
        return hmac.new(
            cls._get_secret_key().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def verify_signature(cls, payload: str, signature: str) -> bool:
        """Verify the integrity of a signed payload."""
        expected = cls.generate_signature(payload)
        return hmac.compare_digest(expected, signature)


# Global instances
vortex_guard = VortexGuard()
security_manager = SecurityManager()
