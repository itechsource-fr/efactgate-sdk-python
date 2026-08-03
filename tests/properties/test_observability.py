"""Property-based tests for observability: logging and event hooks.

Tests validate:
- Property 27: Respect du niveau de log configuré
- Property 28: Invocation des hooks d'événements

Also tests:
- sanitize_url removes auth params from URLs
- sanitize_headers masks sensitive header values

Validates: Requirements 13.4, 13.5
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orwin_sdk.observability.hooks import EventHooks
from orwin_sdk.observability.logger import (
    StructuredLogger,
    sanitize_headers,
    sanitize_url,
)


# --- Strategies ---

log_level_names_st = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

log_level_numeric_st = st.sampled_from(
    [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
)

# Pair of (configured_level, emitted_level)
level_pair_st = st.tuples(log_level_numeric_st, log_level_numeric_st)

# Log messages (non-empty printable text)
log_message_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)

# URL strategies
base_url_st = st.sampled_from([
    "https://api.orwin.io/v1/invoices",
    "http://localhost:8080/status/123",
    "https://example.com/path",
])

auth_param_names_st = st.sampled_from([
    "api_key",
    "token",
    "secret",
    "password",
    "auth",
    "key",
    "access_token",
    "client_secret",
])

auth_param_value_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
)

# Sensitive header names
sensitive_header_st = st.sampled_from([
    "Authorization",
    "X-API-Key",
    "X-Auth-Token",
    "Cookie",
    "authorization",
    "x-api-key",
    "x-auth-token",
    "cookie",
])

# Non-sensitive header names
non_sensitive_header_st = st.sampled_from([
    "Content-Type",
    "Accept",
    "X-Request-ID",
    "User-Agent",
    "X-Correlation-ID",
])

header_value_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
)

# HTTP method names
http_method_st = st.sampled_from(["GET", "POST", "PUT", "DELETE", "PATCH"])

# Status codes
status_code_st = st.integers(min_value=100, max_value=599)

# Elapsed time in ms
elapsed_ms_st = st.floats(min_value=0.1, max_value=60000.0, allow_nan=False, allow_infinity=False)

# Retry attempt numbers
attempt_st = st.integers(min_value=1, max_value=20)

# Delay in ms
delay_ms_st = st.floats(min_value=0.1, max_value=60000.0, allow_nan=False, allow_infinity=False)

# Error types and messages
error_type_st = st.sampled_from(["timeout", "dns_failure", "connection_refused", "http_500"])
error_message_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# Number of hooks to register per event type
num_hooks_st = st.integers(min_value=1, max_value=5)


# --- Property 27: Respect du niveau de log configuré ---


class TestProperty27LogLevelEnforcement:
    """Property 27: Respect du niveau de log configuré.

    **Validates: Requirements 13.5**

    For any configured log level, no log with severity below that level is emitted.
    The logger strictly respects the hierarchy: DEBUG < INFO < WARNING < ERROR < CRITICAL.
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        configured_level=log_level_names_st,
        emit_level=log_level_numeric_st,
        message=log_message_st,
    )
    def test_logs_below_configured_level_not_emitted(
        self, configured_level: str, emit_level: int, message: str
    ) -> None:
        """Logs with severity below configured level are never emitted."""
        # Use a unique logger name to avoid state pollution between tests
        import uuid

        logger_name = f"orwin_test_{uuid.uuid4().hex[:8]}"
        logger = StructuredLogger(name=logger_name, level=configured_level)
        configured_numeric = getattr(logging, configured_level.upper())

        # Capture handler output
        handler = logging.handlers_module = None  # noqa: F841
        test_handler = _CapturingHandler()
        logger._logger.handlers = [test_handler]

        # Emit log at the given level
        logger._log(emit_level, message)

        if emit_level < configured_numeric:
            # Should NOT have been emitted
            assert len(test_handler.records) == 0, (
                f"Log at level {logging.getLevelName(emit_level)} should not be emitted "
                f"when configured level is {configured_level}"
            )
        else:
            # Should have been emitted
            assert len(test_handler.records) == 1, (
                f"Log at level {logging.getLevelName(emit_level)} should be emitted "
                f"when configured level is {configured_level}"
            )

    @pytest.mark.property
    @settings(max_examples=100)
    @given(configured_level=log_level_names_st, message=log_message_st)
    def test_logs_at_configured_level_are_emitted(
        self, configured_level: str, message: str
    ) -> None:
        """Logs at exactly the configured level are always emitted."""
        import uuid

        logger_name = f"orwin_test_{uuid.uuid4().hex[:8]}"
        logger = StructuredLogger(name=logger_name, level=configured_level)
        configured_numeric = getattr(logging, configured_level.upper())

        test_handler = _CapturingHandler()
        logger._logger.handlers = [test_handler]

        logger._log(configured_numeric, message)

        assert len(test_handler.records) == 1

    @pytest.mark.property
    @settings(max_examples=100)
    @given(configured_level=log_level_names_st, message=log_message_st)
    def test_logs_above_configured_level_are_emitted(
        self, configured_level: str, message: str
    ) -> None:
        """Logs above the configured level are always emitted."""
        import uuid

        logger_name = f"orwin_test_{uuid.uuid4().hex[:8]}"
        logger = StructuredLogger(name=logger_name, level=configured_level)
        configured_numeric = getattr(logging, configured_level.upper())

        test_handler = _CapturingHandler()
        logger._logger.handlers = [test_handler]

        # Emit at CRITICAL (always the highest)
        logger._log(logging.CRITICAL, message)

        assert len(test_handler.records) == 1

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        configured_level=log_level_names_st,
        message=log_message_st,
    )
    def test_debug_method_respects_level(
        self, configured_level: str, message: str
    ) -> None:
        """The debug() convenience method respects the configured level."""
        import uuid

        logger_name = f"orwin_test_{uuid.uuid4().hex[:8]}"
        logger = StructuredLogger(name=logger_name, level=configured_level)
        configured_numeric = getattr(logging, configured_level.upper())

        test_handler = _CapturingHandler()
        logger._logger.handlers = [test_handler]

        logger.debug(message)

        if logging.DEBUG < configured_numeric:
            assert len(test_handler.records) == 0
        else:
            assert len(test_handler.records) == 1


# --- Property 28: Invocation des hooks d'événements ---


class TestProperty28HookInvocation:
    """Property 28: Invocation des hooks d'événements.

    **Validates: Requirements 13.4**

    For any registered hook (request_sent, response_received, retry_triggered,
    final_error), the callback is invoked with correct arguments when the event occurs.
    All registered callbacks for a given event type are called.
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(method=http_method_st, url=base_url_st)
    @pytest.mark.anyio
    async def test_request_sent_hook_invoked_with_args(
        self, method: str, url: str
    ) -> None:
        """on_request_sent invokes all registered callbacks with method and url."""
        hooks = EventHooks()
        mock_callback = AsyncMock()
        hooks.add_request_sent(mock_callback)

        await hooks.on_request_sent(method=method, url=url)

        mock_callback.assert_called_once_with(method=method, url=url)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(status_code=status_code_st, elapsed_ms=elapsed_ms_st)
    @pytest.mark.anyio
    async def test_response_received_hook_invoked_with_args(
        self, status_code: int, elapsed_ms: float
    ) -> None:
        """on_response_received invokes all registered callbacks with status and elapsed."""
        hooks = EventHooks()
        mock_callback = AsyncMock()
        hooks.add_response_received(mock_callback)

        await hooks.on_response_received(status_code=status_code, elapsed_ms=elapsed_ms)

        mock_callback.assert_called_once_with(
            status_code=status_code, elapsed_ms=elapsed_ms
        )

    @pytest.mark.property
    @settings(max_examples=100)
    @given(attempt=attempt_st, delay_ms=delay_ms_st)
    @pytest.mark.anyio
    async def test_retry_triggered_hook_invoked_with_args(
        self, attempt: int, delay_ms: float
    ) -> None:
        """on_retry_triggered invokes all registered callbacks with attempt and delay."""
        hooks = EventHooks()
        mock_callback = AsyncMock()
        hooks.add_retry_triggered(mock_callback)

        await hooks.on_retry_triggered(attempt=attempt, delay_ms=delay_ms)

        mock_callback.assert_called_once_with(attempt=attempt, delay_ms=delay_ms)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(error_type=error_type_st, message=error_message_st)
    @pytest.mark.anyio
    async def test_final_error_hook_invoked_with_args(
        self, error_type: str, message: str
    ) -> None:
        """on_final_error invokes all registered callbacks with error_type and message."""
        hooks = EventHooks()
        mock_callback = AsyncMock()
        hooks.add_final_error(mock_callback)

        await hooks.on_final_error(error_type=error_type, message=message)

        mock_callback.assert_called_once_with(error_type=error_type, message=message)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(num_hooks=num_hooks_st, method=http_method_st, url=base_url_st)
    @pytest.mark.anyio
    async def test_all_registered_hooks_are_called(
        self, num_hooks: int, method: str, url: str
    ) -> None:
        """All registered callbacks for a given event are invoked."""
        hooks = EventHooks()
        mock_callbacks = [AsyncMock() for _ in range(num_hooks)]

        for cb in mock_callbacks:
            hooks.add_request_sent(cb)

        await hooks.on_request_sent(method=method, url=url)

        for cb in mock_callbacks:
            cb.assert_called_once_with(method=method, url=url)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        num_hooks=num_hooks_st,
        status_code=status_code_st,
        elapsed_ms=elapsed_ms_st,
    )
    @pytest.mark.anyio
    async def test_multiple_response_hooks_all_called(
        self, num_hooks: int, status_code: int, elapsed_ms: float
    ) -> None:
        """Multiple response_received hooks are all invoked."""
        hooks = EventHooks()
        mock_callbacks = [AsyncMock() for _ in range(num_hooks)]

        for cb in mock_callbacks:
            hooks.add_response_received(cb)

        await hooks.on_response_received(status_code=status_code, elapsed_ms=elapsed_ms)

        for cb in mock_callbacks:
            cb.assert_called_once_with(
                status_code=status_code, elapsed_ms=elapsed_ms
            )

    @pytest.mark.property
    @settings(max_examples=100)
    @given(method=http_method_st, url=base_url_st)
    @pytest.mark.anyio
    async def test_no_hooks_registered_no_error(
        self, method: str, url: str
    ) -> None:
        """Triggering events with no registered hooks does not raise errors."""
        hooks = EventHooks()
        # Should not raise
        await hooks.on_request_sent(method=method, url=url)
        await hooks.on_response_received(status_code=200, elapsed_ms=10.0)
        await hooks.on_retry_triggered(attempt=1, delay_ms=1000.0)
        await hooks.on_final_error(error_type="timeout", message="timed out")


# --- Additional: sanitize_url and sanitize_headers (Property 10 partial) ---


class TestSanitizeUrl:
    """sanitize_url removes authentication parameters from URLs.

    Partial validation of Property 10: Credentials never exposed in outputs.

    **Validates: Requirements 13.6, 2.4**
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        base_url=base_url_st,
        param_name=auth_param_names_st,
        param_value=auth_param_value_st,
    )
    def test_auth_params_replaced_with_stars(
        self, base_url: str, param_name: str, param_value: str
    ) -> None:
        """Auth-related query params have their values replaced by '***'."""
        url = f"{base_url}?{param_name}={param_value}"
        sanitized = sanitize_url(url)

        # The expected pattern after sanitization: param_name=***
        expected_sanitized_param = f"{param_name}=***"
        assert expected_sanitized_param in sanitized
        # The original param=value pattern must not be present
        original_param = f"{param_name}={param_value}"
        assert original_param not in sanitized

    @pytest.mark.property
    @settings(max_examples=100)
    @given(base_url=base_url_st)
    def test_url_without_auth_params_unchanged(self, base_url: str) -> None:
        """URLs without auth params pass through unchanged."""
        url = f"{base_url}?page=1&limit=50"
        sanitized = sanitize_url(url)
        assert sanitized == url

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        base_url=base_url_st,
        param_name=auth_param_names_st,
        param_value=auth_param_value_st,
    )
    def test_multiple_auth_params_all_sanitized(
        self, base_url: str, param_name: str, param_value: str
    ) -> None:
        """Multiple auth params in the same URL are all sanitized."""
        url = f"{base_url}?{param_name}={param_value}&token=mysecret123"
        sanitized = sanitize_url(url)

        # Both auth params should be sanitized (value replaced by ***)
        assert f"{param_name}={param_value}" not in sanitized
        assert "token=mysecret123" not in sanitized
        assert f"{param_name}=***" in sanitized
        assert "token=***" in sanitized


class TestSanitizeHeaders:
    """sanitize_headers masks sensitive header values.

    **Validates: Requirements 13.6, 2.4**
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        header_name=sensitive_header_st,
        header_value=header_value_st,
    )
    def test_sensitive_headers_masked(
        self, header_name: str, header_value: str
    ) -> None:
        """Sensitive headers have their values replaced by '***'."""
        headers = {header_name: header_value}
        sanitized = sanitize_headers(headers)

        assert sanitized[header_name] == "***"

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        header_name=non_sensitive_header_st,
        header_value=header_value_st,
    )
    def test_non_sensitive_headers_preserved(
        self, header_name: str, header_value: str
    ) -> None:
        """Non-sensitive headers keep their original values."""
        headers = {header_name: header_value}
        sanitized = sanitize_headers(headers)

        assert sanitized[header_name] == header_value

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        sensitive_name=sensitive_header_st,
        sensitive_value=header_value_st,
        non_sensitive_name=non_sensitive_header_st,
        non_sensitive_value=header_value_st,
    )
    def test_mixed_headers_correctly_handled(
        self,
        sensitive_name: str,
        sensitive_value: str,
        non_sensitive_name: str,
        non_sensitive_value: str,
    ) -> None:
        """In a dict with both sensitive and non-sensitive headers, only sensitive are masked."""
        headers = {
            sensitive_name: sensitive_value,
            non_sensitive_name: non_sensitive_value,
        }
        sanitized = sanitize_headers(headers)

        assert sanitized[sensitive_name] == "***"
        assert sanitized[non_sensitive_name] == non_sensitive_value


# --- Helper class for capturing log records ---


class _CapturingHandler(logging.Handler):
    """A logging handler that captures LogRecord objects for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
