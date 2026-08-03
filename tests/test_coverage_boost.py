"""Additional unit tests to boost coverage on under-tested modules.

Targets:
- validation/invoice.py (metadata validation, required fields)
- auth/oauth2.py (token lifecycle)
- transport/http_client.py (hooks, 401 refresh)
- observability/logger.py (JSON formatting)
- transport/serialization.py (edge cases)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest
import respx

from orwin_sdk.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FieldError,
    RequestError,
    TransmissionError,
    ValidationError,
)
from orwin_sdk.models.enums import FluxStatus, FluxType, InvoiceFormat
from orwin_sdk.models.invoice import InvoiceSubmission


# ---------------------------------------------------------------------------
# Validation: required fields & metadata
# ---------------------------------------------------------------------------


class TestValidationRequiredFields:
    """Tests for required field validation in invoice validator."""

    def test_empty_content_produces_error(self) -> None:
        """Empty content string triggers validation error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content="",
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
        )
        errors = validate(invoice)
        codes = [e.code for e in errors]
        assert "content_missing" in codes

    def test_empty_target_connector_produces_error(self) -> None:
        """Empty target_connector_id triggers validation error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="",
            enterprise_siret="73282932000074",
        )
        errors = validate(invoice)
        codes = [e.code for e in errors]
        assert "target_connector_id_missing" in codes


class TestValidationMetadata:
    """Tests for metadata field validation."""

    def test_invalid_date_format_in_metadata(self) -> None:
        """Non-ISO 8601 date in metadata triggers error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"invoice_date": "15/06/2024"},
        )
        errors = validate(invoice)
        codes = [e.code for e in errors]
        assert "date_format_invalid" in codes

    def test_valid_date_in_metadata_no_error(self) -> None:
        """Valid ISO 8601 date in metadata produces no error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"invoice_date": "2024-06-15"},
        )
        errors = validate(invoice)
        date_errors = [e for e in errors if e.code == "date_format_invalid"]
        assert date_errors == []

    def test_invalid_amount_format_in_metadata(self) -> None:
        """Non-numeric amount in metadata triggers error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"total_ht": "not-a-number"},
        )
        errors = validate(invoice)
        codes = [e.code for e in errors]
        assert "amount_invalid_format" in codes

    def test_amount_out_of_range_in_metadata(self) -> None:
        """Amount exceeding max range triggers error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"total_ht": "9999999999.99"},
        )
        errors = validate(invoice)
        codes = [e.code for e in errors]
        assert "amount_out_of_range" in codes

    def test_amount_below_min_in_metadata(self) -> None:
        """Amount below minimum triggers error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"amount": "0.001"},
        )
        errors = validate(invoice)
        codes = [e.code for e in errors]
        assert "amount_out_of_range" in codes

    def test_valid_amount_in_metadata_no_error(self) -> None:
        """Valid amount in metadata produces no error."""
        from orwin_sdk.validation.invoice import validate

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"total_ht": "1500.50"},
        )
        errors = validate(invoice)
        amount_errors = [e for e in errors if "amount" in e.code]
        assert amount_errors == []

    def test_validate_invoice_with_amounts(self) -> None:
        """validate_invoice_with_amounts combines basic + amount validation."""
        from orwin_sdk.validation.amounts import InvoiceAmounts, InvoiceLine
        from orwin_sdk.validation.invoice import validate_invoice_with_amounts

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
        )
        amounts = InvoiceAmounts(
            total_ht=Decimal("1000.00"),
            total_ttc=Decimal("1200.00"),
            lines=[
                InvoiceLine(
                    amount_ht=Decimal("1000.00"),
                    vat_amount=Decimal("200.00"),
                    vat_rate=Decimal("0.20"),
                )
            ],
        )
        errors = validate_invoice_with_amounts(invoice, amounts)
        # Should have no amount errors for valid data
        amount_errors = [e for e in errors if "coherence" in e.code]
        assert amount_errors == []


# ---------------------------------------------------------------------------
# OAuth2 authenticator tests
# ---------------------------------------------------------------------------


class TestOAuth2Authenticator:
    """Tests for OAuth2 token refresh flow."""

    @pytest.mark.asyncio
    async def test_oauth2_get_headers_fetches_token(self) -> None:
        """OAuth2 authenticator fetches token on first call."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        with patch("orwin_sdk.auth.oauth2.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "test-token-123",
                        "token_type": "bearer",
                        "expires_in": 3600,
                    },
                )
            )

            auth = OAuth2Authenticator(
                client_id="test-id",
                client_secret="test-secret",
                token_endpoint="https://auth.test.io/oauth2/token",
            )
            headers = await auth.get_headers()

        assert "Authorization" in headers
        assert "Bearer" in headers["Authorization"]

    @pytest.mark.asyncio
    async def test_oauth2_refresh_called(self) -> None:
        """OAuth2 authenticator can refresh token."""
        from orwin_sdk.auth.oauth2 import OAuth2Authenticator

        with patch("orwin_sdk.auth.oauth2.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "new-token-456",
                        "token_type": "bearer",
                        "expires_in": 3600,
                    },
                )
            )

            auth = OAuth2Authenticator(
                client_id="test-id",
                client_secret="test-secret",
                token_endpoint="https://auth.test.io/oauth2/token",
            )
            await auth.refresh()
            headers = await auth.get_headers()

        assert "new-token-456" in headers.get("Authorization", "")


# ---------------------------------------------------------------------------
# HTTP transport: hooks and 401 refresh
# ---------------------------------------------------------------------------

MOCK_BASE_URL = "https://api.test.orwin.io/api/v1"


class TestHttpTransportHooks:
    """Tests for HTTP transport event hooks."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_hooks_on_response_received(self) -> None:
        """Hooks.on_response_received is called on successful response."""
        from orwin_sdk.auth.api_key import ApiKeyAuthenticator
        from orwin_sdk.observability.hooks import EventHooks
        from orwin_sdk.transport.http_client import HttpTransport

        respx.get(f"{MOCK_BASE_URL}/status/test-123").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        hooks = EventHooks()
        hooks.on_response_received = AsyncMock()  # type: ignore[assignment]

        transport = HttpTransport(
            base_url=MOCK_BASE_URL,
            authenticator=ApiKeyAuthenticator(api_key="test-key"),
            timeout=5.0,
            hooks=hooks,
        )

        try:
            await transport.request("GET", "/status/test-123")
        finally:
            await transport.close()

        hooks.on_response_received.assert_called_once()  # type: ignore[attr-defined]

    @respx.mock
    @pytest.mark.asyncio
    async def test_hooks_on_retry_triggered(self) -> None:
        """Hooks.on_retry_triggered is called when a retry occurs."""
        from orwin_sdk.auth.api_key import ApiKeyAuthenticator
        from orwin_sdk.observability.hooks import EventHooks
        from orwin_sdk.transport.http_client import HttpTransport
        from orwin_sdk.transport.retry import RetryPolicy

        route = respx.get(f"{MOCK_BASE_URL}/status/test-retry")
        route.side_effect = [
            httpx.Response(503, json={"error": "unavailable"}),
            httpx.Response(200, json={"status": "ok"}),
        ]

        hooks = EventHooks()
        hooks.on_retry_triggered = AsyncMock()  # type: ignore[assignment]

        transport = HttpTransport(
            base_url=MOCK_BASE_URL,
            authenticator=ApiKeyAuthenticator(api_key="test-key"),
            timeout=5.0,
            retry_policy=RetryPolicy(max_retries=3, delays=(0.01, 0.02, 0.04)),
            hooks=hooks,
        )

        try:
            await transport.request("GET", "/status/test-retry")
        finally:
            await transport.close()

        hooks.on_retry_triggered.assert_called_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Logger: JSON formatting
# ---------------------------------------------------------------------------


class TestStructuredLogger:
    """Tests for the structured JSON logger."""

    def test_logger_respects_level(self) -> None:
        """Logger configured at WARNING doesn't emit INFO messages."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger_inst = StructuredLogger(level="WARNING")
        # Logger should be at WARNING level
        assert logger_inst._logger.level == logging.WARNING

    def test_logger_emits_at_configured_level(self) -> None:
        """Logger emits at and above configured level."""
        from orwin_sdk.observability.logger import StructuredLogger

        logger_inst = StructuredLogger(level="DEBUG")
        assert logger_inst._logger.level == logging.DEBUG


# ---------------------------------------------------------------------------
# Serialization: edge cases
# ---------------------------------------------------------------------------


class TestSerializationEdgeCases:
    """Edge cases for the serialization module."""

    def test_serialize_model_with_none_optional(self) -> None:
        """Models with None optional fields serialize correctly."""
        from orwin_sdk.transport.serialization import serialize

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata=None,
        )
        json_str = serialize(invoice)
        data = json.loads(json_str)
        assert data["metadata"] is None

    def test_serialize_model_with_metadata(self) -> None:
        """Models with dict metadata serialize correctly."""
        from orwin_sdk.transport.serialization import serialize

        invoice = InvoiceSubmission(
            content='{"data": "test"}',
            format=InvoiceFormat.ORWIN_JSON,
            target_connector_id="connector-test",
            enterprise_siret="73282932000074",
            metadata={"key": "value"},
        )
        json_str = serialize(invoice)
        data = json.loads(json_str)
        assert data["metadata"] == {"key": "value"}

    def test_deserialize_all_response_models(self) -> None:
        """All response model types can be deserialized."""
        from orwin_sdk.models.ack import AckResponse
        from orwin_sdk.models.invoice import BatchResponse, FluxCreatedResponse, ImportReport
        from orwin_sdk.models.status import FluxStatusResponse
        from orwin_sdk.transport.serialization import deserialize

        # FluxCreatedResponse
        fcr = deserialize(
            '{"flux_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "emis", "submitted_at": "2024-06-15T10:30:00Z"}',
            FluxCreatedResponse,
        )
        assert fcr.status == FluxStatus.EMIS

        # BatchResponse
        br = deserialize(
            '{"flux_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"], "total_submitted": 1, "total_errors": 0}',
            BatchResponse,
        )
        assert br.total_submitted == 1

        # AckResponse
        ack = deserialize(
            '{"flux_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "ack_payload": {"key": "val"}, "received_at": "2024-06-15T12:00:00Z"}',
            AckResponse,
        )
        assert ack.ack_payload == {"key": "val"}

        # ImportReport
        ir = deserialize(
            '{"total_created": 5, "total_errors": 1, "errors": [{"line_or_section": "3", "code": "FORMAT_ERR", "message": "Bad format"}]}',
            ImportReport,
        )
        assert ir.total_created == 5
        assert len(ir.errors) == 1

        # FluxStatusResponse
        fsr = deserialize(
            '{"flux_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "accepte", "flux_type": "b2b_invoice", "submitted_at": "2024-06-15T10:30:00Z", "transitions": []}',
            FluxStatusResponse,
        )
        assert fsr.status == FluxStatus.ACCEPTE
