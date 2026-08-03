"""Retry policy with exponential backoff and jitter.

Implements the retry logic for transient HTTP errors:
- HTTP 429 (Too Many Requests)
- HTTP 5xx (Server errors)
- Network errors (timeout, DNS, connection refused)

Non-retryable errors (4xx except 429) raise immediately.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configuration for the retry strategy.

    Attributes:
        max_retries: Maximum number of retry attempts (bounds: [0, 10]).
        delays: Tuple of base delay durations in seconds for each retry attempt.
        jitter_factor: Maximum jitter as a fraction of the delay (0.0 to 1.0).
                       Default 0.25 means up to 25% random addition.
    """

    max_retries: int = 5
    delays: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
    jitter_factor: float = 0.25

    @property
    def total_attempts(self) -> int:
        """Total attempts including the initial request."""
        return self.max_retries + 1

    def get_delay(self, attempt: int) -> float:
        """Calculate the delay before the next retry with jitter.

        Args:
            attempt: The retry attempt number (0-indexed: 0 = first retry).

        Returns:
            Delay in seconds with random jitter applied.
        """
        # Pick the base delay from the delays tuple (clamped to last value)
        index = min(attempt, len(self.delays) - 1)
        base_delay = self.delays[index]

        # Apply random jitter: [0, jitter_factor * base_delay]
        jitter = random.uniform(0, self.jitter_factor * base_delay)
        return base_delay + jitter


# Retryable HTTP status codes
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Non-retryable 4xx codes (all 4xx except 429)
NON_RETRYABLE_4XX_MIN: int = 400
NON_RETRYABLE_4XX_MAX: int = 499
RETRYABLE_429: int = 429


def is_retryable_status(status_code: int) -> bool:
    """Determine if an HTTP status code should trigger a retry.

    Retryable: 429, 5xx.
    Non-retryable: all other 4xx (raise immediately).

    Args:
        status_code: The HTTP response status code.

    Returns:
        True if the request should be retried.
    """
    return status_code in RETRYABLE_STATUS_CODES


def is_non_retryable_client_error(status_code: int) -> bool:
    """Check if a status code is a non-retryable client error (4xx except 429).

    Args:
        status_code: The HTTP response status code.

    Returns:
        True if this is a 4xx error that should NOT be retried.
    """
    return (
        NON_RETRYABLE_4XX_MIN <= status_code <= NON_RETRYABLE_4XX_MAX
        and status_code != RETRYABLE_429
    )


__all__ = [
    "RETRYABLE_STATUS_CODES",
    "RetryPolicy",
    "is_non_retryable_client_error",
    "is_retryable_status",
]
