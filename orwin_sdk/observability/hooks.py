"""Event hooks system for SDK observability.

Provides callback-based hooks that allow integrators to plug their own
metrics/monitoring systems into the SDK lifecycle events.

Validates: Requirements 13.4, 13.5
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

# Type aliases for hook callbacks
AsyncCallback = Callable[..., Coroutine[Any, Any, None]]


@dataclass
class EventHooks:
    """Event hooks for SDK lifecycle events.

    Integrators register async callbacks that are invoked at specific
    points in the request lifecycle. All callbacks are optional.

    Attributes:
        on_request_sent: Called when a request is about to be sent.
            Signature: (method: str, url: str) -> None
        on_response_received: Called when a response is received.
            Signature: (status_code: int, elapsed_ms: float) -> None
        on_retry_triggered: Called when a retry is about to be attempted.
            Signature: (attempt: int, delay_ms: float) -> None
        on_final_error: Called when all retries are exhausted.
            Signature: (error_type: str, message: str) -> None
    """

    _request_sent_hooks: list[AsyncCallback] = field(default_factory=list)
    _response_received_hooks: list[AsyncCallback] = field(default_factory=list)
    _retry_triggered_hooks: list[AsyncCallback] = field(default_factory=list)
    _final_error_hooks: list[AsyncCallback] = field(default_factory=list)

    def add_request_sent(self, callback: AsyncCallback) -> None:
        """Register a callback for request_sent events."""
        self._request_sent_hooks.append(callback)

    def add_response_received(self, callback: AsyncCallback) -> None:
        """Register a callback for response_received events."""
        self._response_received_hooks.append(callback)

    def add_retry_triggered(self, callback: AsyncCallback) -> None:
        """Register a callback for retry_triggered events."""
        self._retry_triggered_hooks.append(callback)

    def add_final_error(self, callback: AsyncCallback) -> None:
        """Register a callback for final_error events."""
        self._final_error_hooks.append(callback)

    async def on_request_sent(self, *, method: str, url: str) -> None:
        """Invoke all request_sent callbacks."""
        for hook in self._request_sent_hooks:
            await hook(method=method, url=url)

    async def on_response_received(
        self, *, status_code: int, elapsed_ms: float
    ) -> None:
        """Invoke all response_received callbacks."""
        for hook in self._response_received_hooks:
            await hook(status_code=status_code, elapsed_ms=elapsed_ms)

    async def on_retry_triggered(self, *, attempt: int, delay_ms: float) -> None:
        """Invoke all retry_triggered callbacks."""
        for hook in self._retry_triggered_hooks:
            await hook(attempt=attempt, delay_ms=delay_ms)

    async def on_final_error(self, *, error_type: str, message: str) -> None:
        """Invoke all final_error callbacks."""
        for hook in self._final_error_hooks:
            await hook(error_type=error_type, message=message)


__all__ = ["EventHooks"]
