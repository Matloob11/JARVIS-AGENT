"""
# services/utils/jarvis_security.py
Security module for JARVIS, handling input sanitization, threat detection and secret redaction.
"""
import functools
import hashlib
import hmac
import logging
import ntpath
import os
import re
import threading
import typing
import unicodedata
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

logger = logging.getLogger("JARVIS-SECURITY")

# Contract K-11 / K-28 limits.
RAW_ARG_MAX_CHARS = 20000
DEFAULT_MAX_OUTPUT_CHARS = 12000

# Contract K-29: env names whose current values redact_secrets() masks.
SECRET_ENV_NAMES: tuple[str, ...] = (
    "GOOGLE_API_KEY", "GOOGLE_SEARCH_API_KEY", "TAVILY_API_KEY", "OPENROUTER_API_KEY",
    "GROQ_API_KEY", "HF_TOKEN", "EMAIL_APP_PASSWORD", "WEATHER_API_KEY", "OPENWEATHER_API_KEY",
    "VORTEX_SECURITY_TOKEN", "CONTROLLER_TOKEN", "JARVIS_ENCRYPTION_KEY", "LIVEKIT_API_SECRET",
    "AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "SESSION_JWT_SECRET", "POLLINATIONS_TOKEN",
    "PINCHTAB_TOKEN", "SIM_LOOKUP_API_KEY",
)
MIN_SECRET_CHARS = 8
REDACTED = "***"

_C0_EXCEPT_TAB_LF_CR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_URL_HOST = re.compile(r"[\w.:%-]+")
_EMAIL_SEPARATORS = re.compile(r"[,;\s]+")
_EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{port}{n}" for port in ("com", "lpt") for n in (*"0123456789", "¹", "²", "³")},
)
# A URL query value ends at a delimiter or at the quote, bracket or punctuation around the URL.
_QUERY_SECRET = re.compile(
    r"([?&](?:amp;)?(?:api_?key|key|appid|access_token|token|sig)=)[^\s&#\"'<>\\`)\]},;]+",
    re.IGNORECASE)
# Only a token-shaped value (8+ chars with a digit, or 20+ chars), so prose such as
# 'flag bearer Muhammad' or 'bearer securities' is left alone.
_BEARER_TOKEN = re.compile(
    r"\b(bearer\s+)(?=[A-Za-z0-9_.~+/-]*\d|[A-Za-z0-9_.~+/-]{20})[A-Za-z0-9_.~+/-]{8,}=*",
    re.IGNORECASE)

_runtime_secrets: set[str] = set()
_runtime_secrets_lock = threading.Lock()


def register_runtime_secret(value: str) -> None:
    """
    Contract K-29: masks value in every later redact_secrets() call, for a secret that is not in
    the environment (for example the per-start file-server token). Like an env value, a value
    shorter than MIN_SECRET_CHARS is ignored.
    """
    if not isinstance(value, str) or len(value.strip()) < MIN_SECRET_CHARS:
        return
    with _runtime_secrets_lock:
        _runtime_secrets.add(value.strip())


def _current_secrets() -> frozenset[str]:
    # Read on every call: keys rotate, and tests monkeypatch the environment.
    with _runtime_secrets_lock:
        secrets = set(_runtime_secrets)
    for name in SECRET_ENV_NAMES:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= MIN_SECRET_CHARS:
            secrets.add(value)
    return frozenset(secrets)


@functools.lru_cache(maxsize=32)
def _secret_needles(secrets: frozenset[str]) -> tuple[str, ...]:
    """Each secret plus its URL-encoded forms, longest first so no secret is masked only in part."""
    needles = set(secrets)
    for secret in secrets:
        needles.add(quote(secret, safe="", errors="replace"))
        needles.add(quote_plus(secret, safe="", errors="replace"))
    return tuple(sorted(needles, key=len, reverse=True))


def redact_secrets(text: str) -> str:
    """
    Contract K-29: replaces secrets in text bound for logs, the UI or the LLM with '***'.
    Masks the current values (8+ chars, longest first, also URL-encoded) of SECRET_ENV_NAMES and
    of register_runtime_secret(), then the values of key=, api_key=, appid=, token= and sig= URL
    query parameters and 'Bearer <token>' (a token has 8+ chars with a digit, or 20+ chars).
    Never raises: None is returned as is, any other non-str value goes through str() first, and
    an unexpected error returns '***' instead of the text.
    """
    if text is None:
        return text
    try:
        if not isinstance(text, str):
            text = str(text)
        for needle in _secret_needles(_current_secrets()):
            if needle in text:
                text = text.replace(needle, REDACTED)
        text = _QUERY_SECRET.sub(r"\g<1>" + REDACTED, text)
        return _BEARER_TOKEN.sub(r"\g<1>" + REDACTED, text)
    except Exception:  # pylint: disable=broad-exception-caught
        return REDACTED


class ArgPolicyError(ValueError):
    """A tool argument rejected by its policy (K-28); code is PATH_NOT_ALLOWED or INVALID_ARG."""

    def __init__(self, code: str, message: str, arg: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.arg = arg


@functools.lru_cache(maxsize=4)
def _injection_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


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

    # Argument policies of contract K-28 (tool_arg_policies).
    ARG_POLICIES: typing.ClassVar[frozenset[str]] = frozenset(
        {"plain", "multiline", "raw", "url_or_text", "path", "email_list"})

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
    def sanitize_arg(cls, value: Any, policy: str = "plain") -> Any:
        """
        Cleans one tool argument according to its K-28 policy:
        plain = sanitize_input; raw = NFC, C0 controls except TAB/LF/CR removed, punctuation kept,
        max 20000 chars; multiline = raw with CRLF and lone CR turned into LF; url_or_text = an
        http(s) URL with a host kept as is (minus control chars and surrounding spaces), anything
        else plain; path = NFC, control chars removed, '..' segments and UNC/device paths
        rejected; email_list = 1-10 addresses joined with ', '.
        A non-str value is returned unchanged and an unknown policy falls back to plain. Only a
        rejected path or email_list value raises, with ArgPolicyError.
        """
        if not isinstance(value, str):
            return value
        handlers: dict[str, typing.Callable[[str], str]] = {
            "raw": cls._sanitize_raw,
            "multiline": cls._sanitize_multiline,
            "url_or_text": cls._sanitize_url_or_text,
            "path": cls._sanitize_path,
            "email_list": cls._sanitize_email_list,
        }
        handler = handlers.get(policy)
        if handler is not None:
            return handler(value)
        if policy != "plain":
            logger.warning("Unknown argument policy %r, using 'plain'", policy)
        return cls.sanitize_input(value)

    @staticmethod
    def _sanitize_raw(text: str) -> str:
        text = _C0_EXCEPT_TAB_LF_CR.sub("", unicodedata.normalize("NFC", text))
        if len(text) > RAW_ARG_MAX_CHARS:
            logger.warning("Tool argument cut to %d of %d chars", RAW_ARG_MAX_CHARS, len(text))
            text = text[:RAW_ARG_MAX_CHARS]
        return text

    @classmethod
    def _sanitize_multiline(cls, text: str) -> str:
        return cls._sanitize_raw(text.replace("\r\n", "\n").replace("\r", "\n"))

    @classmethod
    def _sanitize_url_or_text(cls, text: str) -> str:
        candidate = _CONTROL_CHARS.sub("", text).strip()
        try:
            parts = urlsplit(candidate)
            host = parts.hostname or ""
        except ValueError:  # e.g. a malformed IPv6 host
            return cls.sanitize_input(text)
        if parts.scheme.lower() in ("http", "https") and _URL_HOST.fullmatch(host):
            return candidate
        return cls.sanitize_input(text)

    @staticmethod
    def _sanitize_path(text: str) -> str:
        path = _CONTROL_CHARS.sub("", unicodedata.normalize("NFC", text)).strip()
        unified = path.replace("/", "\\")
        if unified.startswith(("\\\\", "\\??\\")):
            raise ArgPolicyError("PATH_NOT_ALLOWED",
                                 "Network (UNC) ya device path allowed nahi hai.")
        # The drive comes off first, so the '..' in a drive-relative 'C:..\x' is seen.
        for segment in ntpath.splitdrive(unified)[1].split("\\"):
            name = segment.rstrip(" .")
            # Windows trims trailing dots and spaces, so '.. ' or '...' also climbs a level.
            if not name and segment.count(".") >= 2:
                raise ArgPolicyError("PATH_NOT_ALLOWED", "'..' wala path allowed nahi hai.")
            # A device name still counts with an extension or a ':' after it ('CON.txt', 'NUL:x').
            if name.split(".", 1)[0].split(":", 1)[0].rstrip(" ").lower() in _WINDOWS_DEVICE_NAMES:
                raise ArgPolicyError("PATH_NOT_ALLOWED",
                                     "Device path (jaise CON ya NUL) allowed nahi hai.")
        return path

    @staticmethod
    def _sanitize_email_list(text: str) -> str:
        cleaned = _CONTROL_CHARS.sub(" ", unicodedata.normalize("NFC", text))
        addresses = [token.strip("<>\"'") for token in _EMAIL_SEPARATORS.split(cleaned)]
        addresses = [address for address in addresses if address]
        if not 1 <= len(addresses) <= 10 or not all(_EMAIL_ADDRESS.fullmatch(a) for a in addresses):
            raise ArgPolicyError("INVALID_ARG",
                                 "Email address sahi nahi hai, 1 se 10 sahi email addresses dein.")
        return ", ".join(addresses)

    @classmethod
    def validate_tool_args(cls, tool_name: str, args: dict[str, Any], depth: int = 0,
                           policies: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Validates and sanitizes tool arguments before execution.
        Prevents tools from being used as injection vectors and handles recursion depth.
        policies maps a top-level argument name to its K-28 policy (default 'plain'); nested
        values stay plain. Raises ArgPolicyError (with .arg set) when a policy rejects a value.
        """
        if depth > 10:
            logger.warning("🚨 SECURITY LIMIT: Recursion depth exceeded for tool %s", tool_name)
            return {"error": "RECURSION_DEPTH_EXCEEDED"}

        logger.debug("🛡️ Validating args for tool: %s (Depth: %d)", tool_name, depth)
        sanitized_args: dict[str, Any] = {}
        policies = policies or {}

        for key, value in args.items():
            if isinstance(value, str):
                try:
                    sanitized_args[key] = cls.sanitize_arg(value, policies.get(key, "plain"))
                except ArgPolicyError as e:
                    e.arg = e.arg or key
                    raise
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
    def validate_tool_output(cls, tool_name: str, output: Any, depth: int = 0,
                             max_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> Any:
        """
        Hardens the Tool-to-Agent boundary.
        Protects LLM from OOM, poisoning, or malformed tool results.
        Every string gets INJECTION_PATTERNS matches replaced by '[filtered]', then
        redact_secrets() when that exists, then is cut to max_chars with a
        '…[truncated N chars]' line; a string is never replaced because of its length.
        """
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
            max_chars = DEFAULT_MAX_OUTPUT_CHARS

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

        # 3. Recursive filtering of every string in the structure
        if isinstance(output, str):
            return cls._filter_output_text(tool_name, output, max_chars)
        if isinstance(output, dict):
            # FIXED: Handle possible recursion or massive dicts
            return {
                str(k): cls.validate_tool_output(f"{tool_name}.{k}", v, depth + 1, max_chars)
                for k, v in list(output.items())[:100] # Cap dict size
            }
        if isinstance(output, list):
            # FIXED: Handle massive lists
            return [
                cls.validate_tool_output(f"{tool_name}[{i}]", v, depth + 1, max_chars)
                for i, v in enumerate(output[:200]) # Cap list size
            ]

        return output

    @classmethod
    def _filter_output_text(cls, tool_name: str, text: str, max_chars: int) -> str:
        if cls.INJECTION_PATTERNS:
            text, hits = _injection_regex(tuple(cls.INJECTION_PATTERNS)).subn("[filtered]", text)
            if hits:
                logger.warning("☣️ Tool %s returned suspicious content: %d match(es) replaced by [filtered]",
                               tool_name, hits)
        # Looked up at call time, so a replaced module-level redact_secrets (K-29) is the one used.
        redactor = globals().get("redact_secrets")
        if callable(redactor):
            text = redactor(text)
        if len(text) > max_chars:
            logger.info("Tool %s output cut to %d of %d chars", tool_name, max_chars, len(text))
            text = f"{text[:max_chars]}\n…[truncated {len(text) - max_chars} chars]"
        return text

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
