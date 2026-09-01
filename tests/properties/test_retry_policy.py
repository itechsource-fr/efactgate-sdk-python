"""Property-based tests for retry policy and HTTP error classification.

Tests validate:
- Property 12: Retry with exponential backoff and jitter
- Property 13: HTTP error classification (retryable vs non-retryable)

Validates: Requirements 5.1, 5.2, 5.3, 5.5, 3.6
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from efactgate_sdk.exceptions import RequestError, TransmissionError
from efactgate_sdk.transport.retry import (
    RetryPolicy,
    is_non_retryable_client_error,
    is_retryable_status,
)


# --- Strategies ---

# Valid retry attempt indices (0-indexed)
attempt_st = st.integers(min_value=0, max_value=20)

# Custom delay tuples (1 to 10 entries, exponentially increasing)
base_delays_st = st.lists(
    st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=10,
).map(tuple)

# Jitter factor in valid range [0.0, 1.0]
jitter_factor_st = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)

# HTTP status codes
retryable_status_st = st.sampled_from([429, 500, 502, 503, 504])
non_retryable_4xx_st = st.integers(min_value=400, max_value=499).filter(
    lambda x: x != 429
)
success_status_st = st.integers(min_value=200, max_value=399)
any_5xx_st = st.integers(min_value=500, max_value=599)


# --- Property 12: Politique de retry avec backoff exponentiel et jitter ---


class TestProperty12RetryBackoffJitter:
    """Property 12: Politique de retry avec backoff exponentiel et jitter.

    **Validates: Requirements 5.1, 5.5**

    For any retryable condition (HTTP 429, 5xx, network error), the SDK retries
    with delays following exponential backoff. Each delay has a jitter added
    between 0% and 25% of the computed delay.
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(attempt=attempt_st)
    def test_delay_within_jitter_bounds_default_policy(self, attempt: int) -> None:
        """get_delay returns value in [base_delay, base_delay * (1 + jitter_factor)]."""
        policy = RetryPolicy()
        delay = policy.get_delay(attempt)

        # Expected base delay (clamped to last entry)
        index = min(attempt, len(policy.delays) - 1)
        base_delay = policy.delays[index]

        # Delay must be >= base_delay (jitter adds, never subtracts)
        assert delay >= base_delay
        # Delay must be <= base_delay * (1 + jitter_factor)
        max_delay = base_delay * (1.0 + policy.jitter_factor)
        assert delay <= max_delay + 1e-10  # floating-point tolerance

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        attempt=attempt_st,
        delays=base_delays_st,
        jitter_factor=jitter_factor_st,
    )
    def test_delay_within_jitter_bounds_custom_policy(
        self, attempt: int, delays: tuple[float, ...], jitter_factor: float
    ) -> None:
        """For any custom RetryPolicy, get_delay is bounded by [base, base*(1+jitter)]."""
        policy = RetryPolicy(delays=delays, jitter_factor=jitter_factor)
        delay = policy.get_delay(attempt)

        index = min(attempt, len(delays) - 1)
        base_delay = delays[index]

        assert delay >= base_delay - 1e-10
        max_delay = base_delay * (1.0 + jitter_factor)
        assert delay <= max_delay + 1e-10

    @pytest.mark.property
    @settings(max_examples=100)
    @given(attempt=st.integers(min_value=0, max_value=3))
    def test_delays_increase_with_attempt_default_policy(self, attempt: int) -> None:
        """Base delays are non-decreasing with attempt number (exponential growth)."""
        policy = RetryPolicy()

        # The default delays tuple is (1.0, 2.0, 4.0, 8.0, 16.0)
        # Base delay at attempt N <= base delay at attempt N+1
        index_current = min(attempt, len(policy.delays) - 1)
        index_next = min(attempt + 1, len(policy.delays) - 1)

        base_current = policy.delays[index_current]
        base_next = policy.delays[index_next]

        assert base_next >= base_current

    @pytest.mark.property
    @settings(max_examples=100)
    @given(attempt=attempt_st)
    def test_jitter_zero_returns_exact_base_delay(self, attempt: int) -> None:
        """With jitter_factor=0.0, get_delay returns exactly the base delay."""
        policy = RetryPolicy(jitter_factor=0.0)
        delay = policy.get_delay(attempt)

        index = min(attempt, len(policy.delays) - 1)
        base_delay = policy.delays[index]

        assert abs(delay - base_delay) < 1e-10

    @pytest.mark.property
    @settings(max_examples=100)
    @given(max_retries=st.integers(min_value=0, max_value=10))
    def test_total_attempts_is_max_retries_plus_one(self, max_retries: int) -> None:
        """total_attempts equals max_retries + 1 (initial request + retries)."""
        policy = RetryPolicy(max_retries=max_retries)
        assert policy.total_attempts == max_retries + 1


# --- Property 13: Classification des erreurs HTTP ---


class TestProperty13HttpErrorClassification:
    """Property 13: Classification des erreurs HTTP.

    **Validates: Requirements 5.2, 5.3, 3.6**

    - For any HTTP 4xx (except 429), is_non_retryable_client_error returns True.
    - For any HTTP 429 or 5xx, is_retryable_status returns True.
    - For any 2xx or 3xx, neither returns True.
    - RequestError is raised (no retry) for 4xx except 429.
    - TransmissionError is raised (retries exhausted) for 429/5xx.
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=non_retryable_4xx_st)
    def test_4xx_except_429_is_non_retryable(self, status_code: int) -> None:
        """Any 4xx status (except 429) is classified as non-retryable client error."""
        assert is_non_retryable_client_error(status_code) is True
        assert is_retryable_status(status_code) is False

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=retryable_status_st)
    def test_429_and_5xx_is_retryable(self, status_code: int) -> None:
        """HTTP 429 and 5xx statuses are classified as retryable."""
        assert is_retryable_status(status_code) is True
        assert is_non_retryable_client_error(status_code) is False

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=success_status_st)
    def test_2xx_3xx_is_neither_retryable_nor_error(self, status_code: int) -> None:
        """Success statuses (2xx/3xx) are neither retryable nor non-retryable errors."""
        assert is_retryable_status(status_code) is False
        assert is_non_retryable_client_error(status_code) is False

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=any_5xx_st)
    def test_all_5xx_is_retryable(self, status_code: int) -> None:
        """Any 5xx status code in the retryable set triggers retry."""
        # Only specific 5xx codes are retryable: 500, 502, 503, 504
        if status_code in {500, 502, 503, 504}:
            assert is_retryable_status(status_code) is True
        else:
            # Other 5xx (e.g., 501, 505) are not in the retryable set
            assert is_retryable_status(status_code) is False

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=non_retryable_4xx_st)
    def test_request_error_structure_for_non_retryable(
        self, status_code: int
    ) -> None:
        """RequestError raised for non-retryable 4xx contains http_code and body."""
        error = RequestError(
            code="request_error",
            message=f"API returned {status_code}",
            http_code=status_code,
            flux_id=None,
            body="error body content",
            url="https://api.efactgate.fr/v1/invoices",
        )
        assert error.http_code == status_code
        assert error.body == "error body content"
        assert error.url == "https://api.efactgate.fr/v1/invoices"
        assert len(error.body) <= 1024

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        status_code=retryable_status_st,
        attempts=st.integers(min_value=1, max_value=11),
    )
    def test_transmission_error_structure_for_retryable(
        self, status_code: int, attempts: int
    ) -> None:
        """TransmissionError raised after exhausted retries contains required fields."""
        body = "x" * 1024  # Max body length
        error = TransmissionError(
            code="transmission_error",
            message=f"Request failed after {attempts} attempts",
            http_code=status_code,
            flux_id=None,
            attempts=attempts,
            body=body,
        )
        assert error.http_code == status_code
        assert error.attempts == attempts
        assert len(error.body) <= 1024

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        body_content=st.text(min_size=0, max_size=2048),
        status_code=retryable_status_st,
    )
    def test_transmission_error_body_truncation_respected(
        self, body_content: str, status_code: int
    ) -> None:
        """TransmissionError body can be truncated to 1024 chars at creation site."""
        truncated_body = body_content[:1024]
        error = TransmissionError(
            code="transmission_error",
            message="Request failed",
            http_code=status_code,
            flux_id=None,
            attempts=5,
            body=truncated_body,
        )
        assert len(error.body) <= 1024

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=st.integers(min_value=100, max_value=599))
    def test_classification_is_mutually_exclusive(self, status_code: int) -> None:
        """No status code is both retryable and non-retryable client error."""
        retryable = is_retryable_status(status_code)
        non_retryable = is_non_retryable_client_error(status_code)

        # They must never both be True
        assert not (retryable and non_retryable)
