"""Async HTTP transport with retry, backoff, and token refresh.

Provides the low-level HTTP layer used by EFactGateClient to communicate with
the eFactGate API. Handles:
- Request execution with configurable timeout
- Retry on transient errors (429, 5xx, network)
- Exponential backoff with jitter
- Token refresh on 401 (one retry)
- Error classification (RequestError vs TransmissionError)

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 2.2
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from efactgate_sdk.exceptions import (
    AuthenticationError,
    RequestError,
    TransmissionError,
)
from efactgate_sdk.transport.retry import (
    RetryPolicy,
    is_non_retryable_client_error,
    is_retryable_status,
)

if TYPE_CHECKING:
    from efactgate_sdk.auth.base import AuthenticatorBase
    from efactgate_sdk.observability.hooks import EventHooks

logger = logging.getLogger(__name__)

# Max response body length included in error messages
_MAX_ERROR_BODY_LENGTH: int = 1024


class HttpTransport:
    """Async HTTP client with retry logic, backoff, and auth refresh.

    Encapsulates all HTTP communication with the API, providing:
    - Automatic retries with exponential backoff on transient errors
    - Token refresh + single retry on 401
    - Immediate error on non-retryable 4xx
    - Structured error information in exceptions

    Args:
        base_url: Base URL of the API (all requests are relative to this).
        authenticator: Auth strategy providing headers and refresh capability.
        timeout: Per-request timeout in seconds.
        retry_policy: Retry configuration (delays, max attempts, jitter).
        hooks: Optional event hooks for observability.
    """

    def __init__(
        self,
        base_url: str,
        authenticator: AuthenticatorBase,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        hooks: EventHooks | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._authenticator = authenticator
        self._timeout = timeout
        self._retry_policy = retry_policy or RetryPolicy()
        self._hooks = hooks
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily create and return the httpx AsyncClient."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public request method
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request with retry and auth refresh.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            path: URL path relative to base_url.
            json: JSON body payload.
            params: Query parameters.
            content: Raw bytes body (for file uploads).
            headers: Additional request headers.

        Returns:
            The successful httpx.Response.

        Raises:
            RequestError: On non-retryable 4xx (except 429).
            TransmissionError: After all retries exhausted.
            AuthenticationError: If token refresh fails.
        """
        total_attempts = self._retry_policy.total_attempts
        last_exception: BaseException | None = None
        last_status_code: int | None = None
        last_body: str = ""
        refreshed_on_401: bool = False

        for attempt in range(total_attempts):
            start_time = time.monotonic()
            try:
                # Get auth headers
                auth_headers = await self._authenticator.get_headers()
                merged_headers = {**(headers or {}), **auth_headers}

                # Execute request
                client = self._get_client()
                response = await client.request(
                    method=method,
                    url=path,
                    json=json,
                    params=params,
                    content=content,
                    headers=merged_headers,
                )

                elapsed_ms = (time.monotonic() - start_time) * 1000

                # Notify hooks
                if self._hooks:
                    await self._hooks.on_response_received(
                        status_code=response.status_code,
                        elapsed_ms=elapsed_ms,
                    )

                # Success
                if response.status_code < 400:
                    return response

                # 401 — try token refresh once
                if response.status_code == 401 and not refreshed_on_401:
                    refreshed_on_401 = True
                    logger.warning(
                        "Received 401 on %s %s — attempting token refresh",
                        method,
                        path,
                    )
                    try:
                        await self._authenticator.refresh()
                    except Exception as refresh_exc:
                        raise AuthenticationError(
                            code="auth_refresh_failed",
                            message=(
                                "Token refresh failed after receiving 401. "
                                f"Cause: {refresh_exc}"
                            ),
                        ) from refresh_exc
                    # Retry with new token (don't count as a retry attempt)
                    continue

                # Non-retryable 4xx (except 429) — raise immediately
                if is_non_retryable_client_error(response.status_code):
                    body = response.text[:_MAX_ERROR_BODY_LENGTH]
                    raise RequestError(
                        code="request_error",
                        message=(
                            f"API returned {response.status_code} on {method} {path}"
                        ),
                        http_code=response.status_code,
                        flux_id=None,
                        body=body,
                        url=f"{self._base_url}/{path.lstrip('/')}",
                    )

                # Retryable error (429, 5xx)
                if is_retryable_status(response.status_code):
                    last_status_code = response.status_code
                    last_body = response.text[:_MAX_ERROR_BODY_LENGTH]
                    last_exception = None

                    if attempt < total_attempts - 1:
                        delay = self._retry_policy.get_delay(attempt)
                        logger.warning(
                            "Retryable error %d on %s %s (attempt %d/%d), "
                            "retrying in %.1fs",
                            response.status_code,
                            method,
                            path,
                            attempt + 1,
                            total_attempts,
                            delay,
                        )
                        if self._hooks:
                            await self._hooks.on_retry_triggered(
                                attempt=attempt + 1,
                                delay_ms=delay * 1000,
                            )
                        await asyncio.sleep(delay)
                        continue

            except (httpx.TimeoutException, httpx.ConnectError, httpx.TransportError) as exc:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                last_exception = exc
                last_status_code = None
                last_body = str(exc)

                if attempt < total_attempts - 1:
                    delay = self._retry_policy.get_delay(attempt)
                    logger.warning(
                        "Network error on %s %s (attempt %d/%d): %s, "
                        "retrying in %.1fs",
                        method,
                        path,
                        attempt + 1,
                        total_attempts,
                        exc,
                        delay,
                    )
                    if self._hooks:
                        await self._hooks.on_retry_triggered(
                            attempt=attempt + 1,
                            delay_ms=delay * 1000,
                        )
                    await asyncio.sleep(delay)
                    continue

            except (RequestError, AuthenticationError):
                # These should propagate immediately
                raise

        # All retries exhausted
        logger.error(
            "All %d attempts exhausted for %s %s. "
            "Last status: %s, last error: %s",
            total_attempts,
            method,
            path,
            last_status_code,
            last_exception,
        )

        if self._hooks:
            await self._hooks.on_final_error(
                error_type="transmission_error",
                message=last_body[:200],
            )

        raise TransmissionError(
            code="transmission_error",
            message=(
                f"Request {method} {path} failed after {total_attempts} attempts. "
                f"Last status: {last_status_code}."
            ),
            http_code=last_status_code or 0,
            flux_id=None,
            attempts=total_attempts,
            body=last_body,
        )


__all__ = ["HttpTransport"]
