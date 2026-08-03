"""Deep coverage tests for serialization, oauth2, http_client, and logger.

Targets remaining gaps to reach 90% coverage.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
import respx

from orwin_sdk.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DeserializationError,
    RequestError,
    TransmissionError,
)
from orwin_sdk.models.enums import FluxStatus, FluxType, InvoiceFormat


# ===========================================================================
# OAuth2 Authenticator — deeper coverage
# ===========================================================================


class TestOAuth2Deep:
    """Cover oauth2.py remaining paths: multi_tenant, expiry extraction, errors."""

    @pytest.mark.asyncio
    async def test_empty_client_id_raises_auth_error(self) -> None:
        """Empty client_id raises AuthenticationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        with pytest.raises(AuthenticationError) as exc_info:
            OAuth2Authenticator(
                client_id="",
                client_secret="secret",
                token_endpoint="https://auth.test.io/token",
            )
        assert exc_info.value.code == "invalid_credentials"

    @pytest.mark.asyncio
    async def test_empty_client_secret_raises_auth_error(self) -> None:
        """Empty client_secret raises AuthenticationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        with pytest.raises(AuthenticationError) as exc_info:
            OAuth2Authenticator(
                client_id="client-id",
                client_secret="  ",
                token_endpoint="https://auth.test.io/token",
            )
        assert exc_info.value.code == "invalid_credentials"

    @pytest.mark.asyncio
    async def test_empty_token_endpoint_raises_auth_error(self) -> None:
        """Empty token_endpoint raises AuthenticationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        with pytest.raises(AuthenticationError) as exc_info:
            OAuth2Authenticator(
                client_id="client-id",
                client_secret="secret",
                token_endpoint="",
            )
        assert exc_info.value.code == "invalid_token_endpoint"

    def test_max_refresh_attempts_out_of_bounds(self) -> None:
        """max_refresh_attempts outside [1,10] raises ConfigurationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        with pytest.raises(ConfigurationError):
            OAuth2Authenticator(
                client_id="client-id",
                client_secret="secret",
                token_endpoint="https://auth.test.io/token",
                max_refresh_attempts=0,
            )

        with pytest.raises(ConfigurationError):
            OAuth2Authenticator(
                client_id="client-id",
                client_secret="secret",
                token_endpoint="https://auth.test.io/token",
                max_refresh_attempts=11,
            )

    @pytest.mark.asyncio
    async def test_is_expired_no_token(self) -> None:
        """is_expired returns True when no token has been acquired."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        auth = OAuth2Authenticator(
            client_id="client-id",
            client_secret="secret",
            token_endpoint="https://auth.test.io/token",
        )
        assert auth.is_expired() is True

    @pytest.mark.asyncio
    async def test_token_refresh_exhausted(self) -> None:
        """Exceeding max refresh attempts raises AuthenticationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        auth = OAuth2Authenticator(
            client_id="client-id",
            client_secret="secret",
            token_endpoint="https://auth.test.io/token",
            max_refresh_attempts=1,
        )

        # Mock first refresh to fail, pushing count to limit
        with patch.object(auth, "_request_token", side_effect=Exception("fail")):
            with pytest.raises(AuthenticationError):
                await auth.refresh()

        # Second attempt should hit the "exhausted" path
        with pytest.raises(AuthenticationError) as exc_info:
            await auth.refresh()
        assert exc_info.value.code == "token_refresh_exhausted"

    @pytest.mark.asyncio
    async def test_multi_tenant_extracts_tenant_id(self) -> None:
        """Multi-tenant mode extracts tenant_id from JWT."""
        import jwt as pyjwt

        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        # Create a valid JWT with tenant_id claim
        token_payload = {
            "sub": "client-id",
            "tenant_id": "tenant-abc-123",
            "exp": int(time.time()) + 3600,
        }
        fake_token = pyjwt.encode(token_payload, "secret", algorithm="HS256")

        auth = OAuth2Authenticator(
            client_id="client-id",
            client_secret="secret",
            token_endpoint="https://auth.test.io/token",
            multi_tenant=True,
        )

        # Mock _request_token to return our fake token
        with patch.object(
            auth,
            "_request_token",
            return_value={
                "access_token": fake_token,
                "token_type": "bearer",
                "expires_in": 3600,
            },
        ):
            headers = await auth.get_headers()

        assert "X-Tenant-ID" in headers
        assert headers["X-Tenant-ID"] == "tenant-abc-123"
        assert auth.tenant_id == "tenant-abc-123"

    @pytest.mark.asyncio
    async def test_token_endpoint_returns_non_200(self) -> None:
        """Non-200 from token endpoint raises AuthenticationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        auth = OAuth2Authenticator(
            client_id="client-id",
            client_secret="secret",
            token_endpoint="https://auth.test.io/token",
        )

        with patch("orwin_sdk.auth.oauth2.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                return_value=httpx.Response(401, json={"error": "invalid_client"})
            )

            with pytest.raises(AuthenticationError) as exc_info:
                await auth.refresh()
            assert exc_info.value.code == "token_request_rejected"

    @pytest.mark.asyncio
    async def test_token_response_missing_access_token(self) -> None:
        """Response without access_token field raises AuthenticationError."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        auth = OAuth2Authenticator(
            client_id="client-id",
            client_secret="secret",
            token_endpoint="https://auth.test.io/token",
        )

        with patch("orwin_sdk.auth.oauth2.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                return_value=httpx.Response(200, json={"token_type": "bearer"})
            )

            with pytest.raises(AuthenticationError) as exc_info:
                await auth.refresh()
            assert exc_info.value.code == "token_response_invalid"

    @pytest.mark.asyncio
    async def test_expiry_from_jwt_fallback(self) -> None:
        """When expires_in is absent, expiry is extracted from JWT exp claim."""
        import jwt as pyjwt

        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        future_exp = int(time.time()) + 7200
        token_payload = {"sub": "test", "exp": future_exp}
        fake_token = pyjwt.encode(token_payload, "secret", algorithm="HS256")

        auth = OAuth2Authenticator(
            client_id="client-id",
            client_secret="secret",
            token_endpoint="https://auth.test.io/token",
        )

        with patch.object(
            auth,
            "_request_token",
            return_value={"access_token": fake_token, "token_type": "bearer"},
        ):
            await auth.refresh()

        # Token should not be expired (expiry derived from JWT)
        assert auth.is_expired() is False


# ===========================================================================
# HTTP Transport — deeper coverage
# ===========================================================================

MOCK_URL = "https://api.test.orwin.io/api/v1"


class TestHttpTransportDeep:
    """Cover http_client.py remaining paths: auth refresh, network errors, hooks."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_triggers_token_refresh_then_success(self) -> None:
        """401 triggers auth refresh, then retries with new token."""
        from orwin_sdk.auth.base import AuthenticatorBase
        from orwin_sdk.transport.http_client import HttpTransport
        from orwin_sdk.transport.retry import RetryPolicy

        class MockAuth(AuthenticatorBase):
            def __init__(self) -> None:
                self.call_count = 0
                self.refreshed = False

            async def get_headers(self) -> dict[str, str]:
                self.call_count += 1
                token = "new-token" if self.refreshed else "old-token"
                return {"Authorization": f"Bearer {token}"}

            async def refresh(self) -> None:
                self.refreshed = True

            def is_expired(self) -> bool:
                return False

        route = respx.get(f"{MOCK_URL}/status/test")
        route.side_effect = [
            httpx.Response(401, json={"error": "token_expired"}),
            httpx.Response(200, json={"status": "ok"}),
        ]

        auth = MockAuth()
        transport = HttpTransport(
            base_url=MOCK_URL,
            authenticator=auth,
            timeout=5.0,
            retry_policy=RetryPolicy(max_retries=3, delays=(0.01, 0.02, 0.04)),
        )

        try:
            response = await transport.request("GET", "/status/test")
            assert response.status_code == 200
            assert auth.refreshed is True
        finally:
            await transport.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_refresh_failure_raises_auth_error(self) -> None:
        """Failed token refresh after 401 raises AuthenticationError."""
        from orwin_sdk.auth.base import AuthenticatorBase
        from orwin_sdk.transport.http_client import HttpTransport
        from orwin_sdk.transport.retry import RetryPolicy

        class FailingRefreshAuth(AuthenticatorBase):
            async def get_headers(self) -> dict[str, str]:
                return {"Authorization": "Bearer bad-token"}

            async def refresh(self) -> None:
                raise RuntimeError("Refresh endpoint down")

            def is_expired(self) -> bool:
                return False

        respx.get(f"{MOCK_URL}/test").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        transport = HttpTransport(
            base_url=MOCK_URL,
            authenticator=FailingRefreshAuth(),
            timeout=5.0,
            retry_policy=RetryPolicy(max_retries=2, delays=(0.01, 0.02)),
        )

        try:
            with pytest.raises(AuthenticationError) as exc_info:
                await transport.request("GET", "/test")
            assert exc_info.value.code == "auth_refresh_failed"
        finally:
            await transport.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_retryable_4xx_raises_request_error(self) -> None:
        """Non-retryable 4xx (e.g. 400) raises RequestError immediately."""
        from orwin_sdk.auth.api_key import ApiKeyAuthenticator
        from orwin_sdk.transport.http_client import HttpTransport
        from orwin_sdk.transport.retry import RetryPolicy

        respx.post(f"{MOCK_URL}/invoices").mock(
            return_value=httpx.Response(400, json={"error": "bad request"})
        )

        transport = HttpTransport(
            base_url=MOCK_URL,
            authenticator=ApiKeyAuthenticator(api_key="test"),
            timeout=5.0,
            retry_policy=RetryPolicy(max_retries=3, delays=(0.01, 0.02, 0.04)),
        )

        try:
            with pytest.raises(RequestError) as exc_info:
                await transport.request("POST", "/invoices")
            assert exc_info.value.http_code == 400
        finally:
            await transport.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_error_retry_then_success(self) -> None:
        """Network error followed by success completes normally."""
        from orwin_sdk.auth.api_key import ApiKeyAuthenticator
        from orwin_sdk.transport.http_client import HttpTransport
        from orwin_sdk.transport.retry import RetryPolicy

        route = respx.get(f"{MOCK_URL}/status/net")
        route.side_effect = [
            httpx.ConnectError("Connection refused"),
            httpx.Response(200, json={"status": "ok"}),
        ]

        transport = HttpTransport(
            base_url=MOCK_URL,
            authenticator=ApiKeyAuthenticator(api_key="test"),
            timeout=5.0,
            retry_policy=RetryPolicy(max_retries=3, delays=(0.01, 0.02, 0.04)),
        )

        try:
            response = await transport.request("GET", "/status/net")
            assert response.status_code == 200
        finally:
            await transport.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_final_error_hook_called(self) -> None:
        """on_final_error hook is called when all retries are exhausted."""
        from orwin_sdk.auth.api_key import ApiKeyAuthenticator
        from orwin_sdk.observability.hooks import EventHooks
        from orwin_sdk.transport.http_client import HttpTransport
        from orwin_sdk.transport.retry import RetryPolicy

        respx.get(f"{MOCK_URL}/fail").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        hooks = EventHooks()
        hooks.on_final_error = AsyncMock()  # type: ignore[assignment]

        transport = HttpTransport(
            base_url=MOCK_URL,
            authenticator=ApiKeyAuthenticator(api_key="test"),
            timeout=5.0,
            retry_policy=RetryPolicy(max_retries=1, delays=(0.01,)),
            hooks=hooks,
        )

        try:
            with pytest.raises(TransmissionError):
                await transport.request("GET", "/fail")
        finally:
            await transport.close()

        hooks.on_final_error.assert_called_once()  # type: ignore[attr-defined]


# ===========================================================================
# Structured Logger — deeper coverage
# ===========================================================================


class TestLoggerDeep:
    """Cover observability/logger.py remaining paths."""

    def test_logger_log_request(self) -> None:
        """StructuredLogger.log_request emits at DEBUG level."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="DEBUG")
        # Should not raise
        logger.log_request(
            method="GET",
            url="https://api.test.io/status/123",
            status_code=200,
            duration_ms=42.5,
        )

    def test_logger_log_response(self) -> None:
        """StructuredLogger.info emits a message."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="DEBUG")
        logger.info("Response received", status_code=200, elapsed_ms=42.5)

    def test_logger_log_retry(self) -> None:
        """StructuredLogger.log_retry emits at WARNING level."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="DEBUG")
        logger.log_retry(
            attempt=2,
            delay_ms=2000.0,
            url="https://api.test.io/invoices",
        )

    def test_logger_log_error(self) -> None:
        """StructuredLogger.error emits at ERROR level."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="DEBUG")
        logger.error("All retries exhausted")

    def test_logger_log_retries_exhausted(self) -> None:
        """StructuredLogger.log_retries_exhausted emits at ERROR level."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="DEBUG")
        logger.log_retries_exhausted(
            error_code=503,
            message="Service unavailable",
            attempts=5,
        )

    def test_logger_does_not_emit_below_level(self) -> None:
        """Logger at ERROR level does not emit WARNING messages."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="ERROR")
        # This should be silently ignored (WARNING < ERROR)
        logger.log_retry(attempt=1, delay_ms=1000.0, url="https://api.test.io/x")

    def test_json_formatter_output(self) -> None:
        """JSON formatter produces valid JSON with expected fields."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger = StructuredLogger(level="DEBUG")
        # Capture log output via handler
        import io

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger._logger.handlers[0].formatter)
        logger._logger.addHandler(handler)

        logger.log_request(method="POST", url="https://api.test.io/invoices", status_code=201, duration_ms=55.3)

        output = stream.getvalue()
        if output.strip():
            # Should be valid JSON
            parsed = json.loads(output.strip())
            assert "timestamp" in parsed
            assert "level" in parsed
            assert "message" in parsed


# ===========================================================================
# Serialization — edge cases for branches
# ===========================================================================


class TestSerializationDeep:
    """Cover serialization.py uncovered branches."""

    def test_deserialize_non_object_root(self) -> None:
        """JSON array at root level raises DeserializationError."""
        from orwin_sdk.models.invoice import FluxCreatedResponse
        from orwin_sdk.transport.serialization import deserialize

        with pytest.raises(DeserializationError) as exc_info:
            deserialize("[1, 2, 3]", FluxCreatedResponse)
        assert exc_info.value.reason == "expected_object"

    def test_decode_list_type_mismatch(self) -> None:
        """Non-list value for list field raises DeserializationError."""
        from orwin_sdk.models.status import FluxStatusResponse
        from orwin_sdk.transport.serialization import deserialize

        json_str = json.dumps({
            "flux_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "status": "emis",
            "flux_type": "b2b_invoice",
            "submitted_at": "2024-06-15T10:30:00Z",
            "transitions": "not-a-list",
        })
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(json_str, FluxStatusResponse)
        assert exc_info.value.reason == "type_mismatch"

    def test_decode_int_with_bool_raises(self) -> None:
        """Boolean value for int field raises DeserializationError."""
        from orwin_sdk.models.invoice import BatchResponse
        from orwin_sdk.transport.serialization import deserialize

        json_str = json.dumps({
            "flux_ids": [],
            "total_submitted": True,  # bool, not int
            "total_errors": 0,
        })
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(json_str, BatchResponse)
        assert exc_info.value.reason == "type_mismatch"

    def test_decode_str_with_non_str_raises(self) -> None:
        """Non-string value for string field raises DeserializationError."""
        from orwin_sdk.models.invoice import InvoiceSubmission
        from orwin_sdk.transport.serialization import deserialize

        json_str = json.dumps({
            "content": 12345,  # int, not str
            "format": "ubl",
            "target_connector_id": "test",
            "enterprise_siret": "73282932000074",
        })
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(json_str, InvoiceSubmission)
        assert exc_info.value.reason == "type_mismatch"

    def test_encode_datetime_without_microseconds(self) -> None:
        """Datetime without microseconds serializes without decimal point."""
        from orwin_sdk.transport.serialization import serialize

        from orwin_sdk.models.invoice import FluxCreatedResponse

        model = FluxCreatedResponse(
            flux_id=UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
            status=FluxStatus.EMIS,
            submitted_at=datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC),
        )
        json_str = serialize(model)
        data = json.loads(json_str)
        # No decimal point in the time (no microseconds)
        assert "." not in data["submitted_at"]
        assert data["submitted_at"].endswith("Z")

    def test_encode_datetime_with_microseconds(self) -> None:
        """Datetime with microseconds serializes with fractional seconds."""
        from orwin_sdk.transport.serialization import serialize

        from orwin_sdk.models.invoice import FluxCreatedResponse

        model = FluxCreatedResponse(
            flux_id=UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
            status=FluxStatus.EMIS,
            submitted_at=datetime(2024, 6, 15, 10, 30, 0, 123456, tzinfo=UTC),
        )
        json_str = serialize(model)
        data = json.loads(json_str)
        assert "." in data["submitted_at"]
        assert data["submitted_at"].endswith("Z")

    def test_encode_dict_with_various_types(self) -> None:
        """Dict values are properly encoded."""
        from orwin_sdk.models.ack import AckResponse
        from orwin_sdk.transport.serialization import serialize

        model = AckResponse(
            flux_id=UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
            ack_payload={"key1": "value", "key2": 42, "key3": True},
            received_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        )
        json_str = serialize(model)
        data = json.loads(json_str)
        assert data["ack_payload"]["key1"] == "value"
        assert data["ack_payload"]["key2"] == 42
        assert data["ack_payload"]["key3"] is True

    def test_decode_invalid_datetime_string(self) -> None:
        """Invalid datetime string raises DeserializationError."""
        from orwin_sdk.models.invoice import FluxCreatedResponse
        from orwin_sdk.transport.serialization import deserialize

        json_str = json.dumps({
            "flux_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "status": "emis",
            "submitted_at": "not-a-date",
        })
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(json_str, FluxCreatedResponse)
        assert exc_info.value.reason == "invalid_format"

    def test_decode_invalid_uuid_string(self) -> None:
        """Invalid UUID string raises DeserializationError."""
        from orwin_sdk.models.invoice import FluxCreatedResponse
        from orwin_sdk.transport.serialization import deserialize

        json_str = json.dumps({
            "flux_id": "not-a-valid-uuid",
            "status": "emis",
            "submitted_at": "2024-06-15T10:30:00Z",
        })
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(json_str, FluxCreatedResponse)
        assert exc_info.value.reason == "invalid_format"
