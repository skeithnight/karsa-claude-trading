"""Karsa Trading System — Error Classification

Separates fatal configuration/code errors from transient network issues.
Used by:
  - Connection health loop (crypto_main.py) to send actionable alerts
  - Agent runtime (runtime.py) to skip retries on unrecoverable errors

Usage:
  from src.utils.error_classification import classify_error, ErrorSeverity

  severity = classify_error(exc, status_code=resp.status_code)
  if severity == ErrorSeverity.FATAL:
      alert("🛑 FATAL — manual fix required")
"""

from enum import Enum
from typing import Optional


class ErrorSeverity(Enum):
    FATAL = "fatal"          # Config/code bug — will never self-heal
    RECOVERABLE = "recoverable"  # Needs intervention but not urgent
    TRANSIENT = "transient"  # Network/service blip — retry is correct


# Exception types that indicate a code or config bug
_FATAL_EXCEPTION_TYPES = (
    TypeError,       # Missing/wrong arguments (e.g. BybitClient())
    ValueError,      # Bad config value
    KeyError,        # Missing required config key
    AttributeError,  # Wrong object shape (e.g. missing method)
)

# HTTP status codes that indicate auth/config problems (never retry)
_FATAL_HTTP_CODES = {401, 403, 404, 405, 410, 422}

# HTTP status codes that indicate transient server issues (retry is correct)
_TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}

# Exception types that indicate transient network/service issues
_TRANSIENT_EXCEPTION_TYPES = (
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)

# Keywords in error messages that indicate auth failure
_AUTH_KEYWORDS = {"unauthorized", "forbidden", "invalid api key", "invalid api_secret",
                  "authentication failed", "access denied", "401", "403"}

# Human-readable descriptions for fatal error sources
FATAL_DESCRIPTIONS = {
    "bybit_init": "Bybit client initialization failed — check BYBIT_API_KEY and BYBIT_API_SECRET",
    "bybit_auth": "Bybit authentication rejected — API key may be invalid or expired",
    "9router_auth": "9Router/LLM auth rejected — check 9ROUTER_AUTH_TOKEN or LLM credits",
    "9router_config": "9Router configuration error — check 9ROUTER_BASE_URL",
    "config_missing": "Required configuration missing — check .env file",
}


def classify_error(exc: Exception, status_code: Optional[int] = None) -> ErrorSeverity:
    """Classify an exception as fatal, recoverable, or transient.

    Args:
        exc: The exception to classify.
        status_code: Optional HTTP status code (overrides exception type for HTTP errors).

    Returns:
        ErrorSeverity indicating how the error should be handled.
    """
    # HTTP status code takes precedence
    if status_code is not None:
        if status_code in _FATAL_HTTP_CODES:
            return ErrorSeverity.FATAL
        if status_code in _TRANSIENT_HTTP_CODES:
            return ErrorSeverity.TRANSIENT

    # Check exception type
    exc_type = type(exc)

    if exc_type in _FATAL_EXCEPTION_TYPES:
        return ErrorSeverity.FATAL

    if exc_type in _TRANSIENT_EXCEPTION_TYPES:
        return ErrorSeverity.TRANSIENT

    # Check error message for auth keywords
    msg = str(exc).lower()
    if any(kw in msg for kw in _AUTH_KEYWORDS):
        return ErrorSeverity.FATAL

    # Check for Bybit-specific fatal error codes in message
    # e.g. "Bybit fatal error (10001): params error"
    if "fatal error" in msg or "api error" in msg:
        return ErrorSeverity.FATAL

    # Default: treat as recoverable (retry with caution)
    return ErrorSeverity.RECOVERABLE


def describe_error(source: str, exc: Exception, status_code: Optional[int] = None) -> str:
    """Get a human-readable description for an error.

    Args:
        source: Error source key (e.g. "bybit_init", "9router_auth").
        exc: The exception.
        status_code: Optional HTTP status code.

    Returns:
        Human-readable error description.
    """
    severity = classify_error(exc, status_code)
    base = FATAL_DESCRIPTIONS.get(source, str(exc)[:80])

    if severity == ErrorSeverity.FATAL:
        return f"🛑 FATAL: {base}"
    elif severity == ErrorSeverity.TRANSIENT:
        return f"⚠️ TRANSIENT: {base}"
    else:
        return f"⚠️ {base}"
