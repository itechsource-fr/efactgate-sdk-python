"""Structured JSON logger for the SDK.

Emits logs in JSON format with sanitized URLs (no auth params).
Respects the configured log level strictly.

Validates: Requirements 13.1, 13.2, 13.3, 13.5, 13.6, 2.4
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

# Pattern to detect auth-related query parameters in URLs
_AUTH_PARAM_PATTERN = re.compile(
    r"((?:api_key|token|secret|password|auth|key|access_token|client_secret)"
    r"=)[^&]*",
    re.IGNORECASE,
)

# Sensitive header names (never log their values)
_SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "x-api-key",
    "x-auth-token",
    "cookie",
})


def sanitize_url(url: str) -> str:
    """Remove authentication parameters from a URL for safe logging.

    Replaces values of auth-related query params with '***'.

    Args:
        url: The URL to sanitize.

    Returns:
        URL with auth param values replaced by '***'.
    """
    return _AUTH_PARAM_PATTERN.sub(r"\1***", url)


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove sensitive header values for safe logging.

    Args:
        headers: Request or response headers.

    Returns:
        Headers dict with sensitive values replaced by '***'.
    """
    return {
        key: "***" if key.lower() in _SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }


class StructuredLogger:
    """JSON-structured logger for SDK HTTP operations.

    Emits structured log entries with fields:
    - method, url (sanitized), status_code, duration_ms
    - Never includes credentials, tokens, or auth headers in log output

    Args:
        name: Logger name (default: "efactgate_sdk").
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """

    def __init__(self, name: str = "efactgate_sdk", level: str = "WARNING") -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.WARNING))

        # Avoid duplicate handlers if already configured
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(_JsonFormatter())
            self._logger.addHandler(handler)

    @property
    def level(self) -> int:
        """Current effective log level."""
        return self._logger.level

    def debug(self, message: str, **kwargs: Any) -> None:
        """Emit a DEBUG-level structured log."""
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Emit an INFO-level structured log."""
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Emit a WARNING-level structured log."""
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Emit an ERROR-level structured log."""
        self._log(logging.ERROR, message, **kwargs)

    def log_request(
        self, *, method: str, url: str, status_code: int, duration_ms: float
    ) -> None:
        """Log an HTTP request at DEBUG level with structured fields.

        Args:
            method: HTTP method.
            url: Request URL (will be sanitized).
            status_code: Response status code.
            duration_ms: Request duration in milliseconds.
        """
        self.debug(
            "HTTP request",
            method=method,
            url=sanitize_url(url),
            status_code=status_code,
            duration_ms=round(duration_ms, 1),
        )

    def log_retry(
        self, *, attempt: int, delay_ms: float, url: str
    ) -> None:
        """Log a retry attempt at WARNING level.

        Args:
            attempt: Retry attempt number.
            delay_ms: Delay before next attempt in milliseconds.
            url: Request URL (will be sanitized).
        """
        self.warning(
            "Retry triggered",
            attempt=attempt,
            delay_ms=round(delay_ms, 1),
            url=sanitize_url(url),
        )

    def log_retries_exhausted(
        self,
        *,
        error_code: str | int,
        message: str,
        attempts: int,
    ) -> None:
        """Log retries exhausted at ERROR level.

        Args:
            error_code: HTTP status code or error type.
            message: Error description.
            attempts: Total attempts performed.
        """
        self.error(
            "Retries exhausted",
            error_code=error_code,
            error_message=message,
            attempts=attempts,
        )

    def _log(self, level: int, message: str, **kwargs: Any) -> None:
        """Internal log emission with structured extra data."""
        if self._logger.isEnabledFor(level):
            self._logger.log(level, message, extra={"structured_data": kwargs})


class _JsonFormatter(logging.Formatter):
    """JSON log formatter for structured output."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string.

        Args:
            record: The log record to format.

        Returns:
            JSON-encoded string with timestamp, level, message, and structured data.
        """
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Add structured data if present
        structured = getattr(record, "structured_data", None)
        if structured:
            log_entry.update(structured)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


__all__ = [
    "StructuredLogger",
    "sanitize_headers",
    "sanitize_url",
]
