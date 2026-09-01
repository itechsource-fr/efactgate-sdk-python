"""Integration tests for EFactGateClient using respx to mock HTTP.

These tests exercise the full client → transport → serialization pipeline
without hitting a real API. They cover the submit/get/poll lifecycle and
ensure proper error handling.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
import respx

from efactgate_sdk.client import EFactGateClient
from efactgate_sdk.exceptions import (
    NotFoundError,
    TransmissionError,
    ValidationError,
)
from efactgate_sdk.models.enums import FluxStatus, FluxType, InvoiceFormat
from efactgate_sdk.models.invoice import InvoiceSubmission


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_BASE_URL = "https://api.test.efactgate.fr/api/v1"
VALID_SIRET = "73282932000074"  # Luhn-valid


@pytest.fixture
def valid_invoice() -> InvoiceSubmission:
    """Create a valid invoice submission for testing."""
    return InvoiceSubmission(
        content='{"numero": "FA-2024-001", "montant_ttc": "1200.00"}',
        format=InvoiceFormat.EFACTGATE_JSON,
        target_connector_id="connector-test",
        enterprise_siret=VALID_SIRET,
        flux_type=FluxType.B2B_INVOICE,
    )


# ---------------------------------------------------------------------------
# Submit invoice tests
# ---------------------------------------------------------------------------


class TestSubmitInvoice:
    """Test EFactGateClient.submit_invoice with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_submit_invoice_success(self, valid_invoice: InvoiceSubmission) -> None:
        """Successful invoice submission returns FluxCreatedResponse."""
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.post(f"{MOCK_BASE_URL}/invoices").mock(
            return_value=httpx.Response(
                201,
                json={
                    "flux_id": flux_id,
                    "status": "emis",
                    "submitted_at": "2024-06-15T10:30:00Z",
                },
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            result = await client.submit_invoice(valid_invoice)

        assert str(result.flux_id) == flux_id
        assert result.status == FluxStatus.EMIS
        assert result.submitted_at == datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_submit_invoice_validation_error(self) -> None:
        """Invoice with invalid SIRET fails local validation."""
        bad_invoice = InvoiceSubmission(
            content='{"numero": "FA-001"}',
            format=InvoiceFormat.EFACTGATE_JSON,
            target_connector_id="connector-test",
            enterprise_siret="12345678901234",  # Invalid Luhn
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            with pytest.raises(ValidationError) as exc_info:
                await client.submit_invoice(bad_invoice)

        assert exc_info.value.code == "validation_error"
        assert len(exc_info.value.errors) > 0


# ---------------------------------------------------------------------------
# Get status tests
# ---------------------------------------------------------------------------


class TestGetStatus:
    """Test EFactGateClient.get_status with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_status_success(self) -> None:
        """Successful status retrieval returns FluxStatusResponse."""
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{MOCK_BASE_URL}/status/{flux_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "flux_id": flux_id,
                    "status": "en_transit",
                    "flux_type": "b2b_invoice",
                    "submitted_at": "2024-06-15T10:30:00Z",
                    "transitions": [
                        {
                            "from_status": "emis",
                            "to_status": "en_transit",
                            "reason": "Transmitted to target",
                            "transitioned_at": "2024-06-15T10:31:00Z",
                        }
                    ],
                },
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            status = await client.get_status(flux_id)

        assert status.status == FluxStatus.EN_TRANSIT
        assert status.flux_type == FluxType.B2B_INVOICE
        assert len(status.transitions) == 1
        assert status.transitions[0].from_status == FluxStatus.EMIS

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_status_not_found(self) -> None:
        """404 response raises NotFoundError."""
        flux_id = "nonexistent-flux-id"
        respx.get(f"{MOCK_BASE_URL}/status/{flux_id}").mock(
            return_value=httpx.Response(
                404,
                json={"error_code": "NOT_FOUND", "message": "Flux not found"},
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            with pytest.raises(NotFoundError) as exc_info:
                await client.get_status(flux_id)

        assert exc_info.value.flux_id == flux_id


# ---------------------------------------------------------------------------
# Get ACK tests
# ---------------------------------------------------------------------------


class TestGetAck:
    """Test EFactGateClient.get_ack with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_ack_success(self) -> None:
        """Successful ACK retrieval returns AckResponse."""
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{MOCK_BASE_URL}/ack/{flux_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "flux_id": flux_id,
                    "ack_payload": {"status": "ok", "code": 200},
                    "received_at": "2024-06-15T11:00:00Z",
                },
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            ack = await client.get_ack(flux_id)

        assert ack is not None
        assert str(ack.flux_id) == flux_id
        assert ack.ack_payload == {"status": "ok", "code": 200}

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_ack_not_ready(self) -> None:
        """204 response returns None (ACK not yet available)."""
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{MOCK_BASE_URL}/ack/{flux_id}").mock(
            return_value=httpx.Response(204)
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            ack = await client.get_ack(flux_id)

        assert ack is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_ack_not_found(self) -> None:
        """404 response raises NotFoundError."""
        flux_id = "nonexistent-flux-id"
        respx.get(f"{MOCK_BASE_URL}/ack/{flux_id}").mock(
            return_value=httpx.Response(
                404,
                json={"error_code": "NOT_FOUND", "message": "Flux not found"},
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            with pytest.raises(NotFoundError):
                await client.get_ack(flux_id)


# ---------------------------------------------------------------------------
# Batch and import tests
# ---------------------------------------------------------------------------


class TestBatchAndImport:
    """Test EFactGateClient batch and import methods."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_submit_batch_success(self, valid_invoice: InvoiceSubmission) -> None:
        """Successful batch submission returns BatchResponse."""
        respx.post(f"{MOCK_BASE_URL}/batch").mock(
            return_value=httpx.Response(
                201,
                json={
                    "flux_ids": [
                        "11111111-1111-1111-1111-111111111111",
                        "22222222-2222-2222-2222-222222222222",
                    ],
                    "total_submitted": 2,
                    "total_errors": 0,
                },
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            result = await client.submit_batch([valid_invoice, valid_invoice])

        assert result.total_submitted == 2
        assert result.total_errors == 0
        assert len(result.flux_ids) == 2

    @pytest.mark.asyncio
    async def test_submit_batch_empty_raises_validation(self) -> None:
        """Empty batch raises ValidationError."""
        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            with pytest.raises(ValidationError) as exc_info:
                await client.submit_batch([])

        assert "batch_size_invalid" in exc_info.value.errors[0].code


# ---------------------------------------------------------------------------
# Poll until final tests
# ---------------------------------------------------------------------------


class TestPollUntilFinal:
    """Test EFactGateClient.poll_until_final."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_reaches_terminal_status(self) -> None:
        """Polling returns when terminal status is reached."""
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        # First call: en_transit, second call: accepte
        route = respx.get(f"{MOCK_BASE_URL}/status/{flux_id}")
        route.side_effect = [
            httpx.Response(
                200,
                json={
                    "flux_id": flux_id,
                    "status": "en_transit",
                    "flux_type": "b2b_invoice",
                    "submitted_at": "2024-06-15T10:30:00Z",
                    "transitions": [],
                },
            ),
            httpx.Response(
                200,
                json={
                    "flux_id": flux_id,
                    "status": "accepte",
                    "flux_type": "b2b_invoice",
                    "submitted_at": "2024-06-15T10:30:00Z",
                    "transitions": [
                        {
                            "from_status": "en_transit",
                            "to_status": "accepte",
                            "reason": "Accepted by target",
                            "transitioned_at": "2024-06-15T10:35:00Z",
                        }
                    ],
                },
            ),
        ]

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            result = await client.poll_until_final(
                flux_id, timeout=10.0, interval=0.1
            )

        assert result.status == FluxStatus.ACCEPTE

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_timeout_raises(self) -> None:
        """Polling beyond timeout raises TimeoutError."""
        from efactgate_sdk.exceptions import TimeoutError

        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        # Always returns non-terminal status
        respx.get(f"{MOCK_BASE_URL}/status/{flux_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "flux_id": flux_id,
                    "status": "en_transit",
                    "flux_type": "b2b_invoice",
                    "submitted_at": "2024-06-15T10:30:00Z",
                    "transitions": [],
                },
            )
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            with pytest.raises(TimeoutError) as exc_info:
                await client.poll_until_final(
                    flux_id, timeout=0.3, interval=0.1
                )

        assert exc_info.value.flux_id == flux_id


# ---------------------------------------------------------------------------
# Transport / retry tests
# ---------------------------------------------------------------------------


class TestTransportRetry:
    """Test HTTP transport retry behavior through the client."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_retry_on_500_then_success(self, valid_invoice: InvoiceSubmission) -> None:
        """Transient 500 followed by success completes normally."""
        route = respx.post(f"{MOCK_BASE_URL}/invoices")
        route.side_effect = [
            httpx.Response(500, json={"error": "internal"}),
            httpx.Response(
                201,
                json={
                    "flux_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "status": "emis",
                    "submitted_at": "2024-06-15T10:30:00Z",
                },
            ),
        ]

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
            max_retries=3,
            retry_delays=(0.01, 0.02, 0.04),
        ) as client:
            result = await client.submit_invoice(valid_invoice)

        assert result.status == FluxStatus.EMIS

    @respx.mock
    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises_transmission_error(
        self, valid_invoice: InvoiceSubmission
    ) -> None:
        """All retries exhausted raises TransmissionError."""
        respx.post(f"{MOCK_BASE_URL}/invoices").mock(
            return_value=httpx.Response(503, json={"error": "service unavailable"})
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
            max_retries=2,
            retry_delays=(0.01, 0.02),
        ) as client:
            with pytest.raises(TransmissionError) as exc_info:
                await client.submit_invoice(valid_invoice)

        assert exc_info.value.attempts > 0


# ---------------------------------------------------------------------------
# Validation (local, no network)
# ---------------------------------------------------------------------------


class TestLocalValidation:
    """Test EFactGateClient.validate (synchronous, no network)."""

    def test_validate_valid_invoice(self, valid_invoice: InvoiceSubmission) -> None:
        """Valid invoice passes validation."""
        client = EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        )
        errors = client.validate(valid_invoice)
        assert errors == []

    def test_validate_invalid_siret(self) -> None:
        """Invalid SIRET produces validation errors."""
        invalid_invoice = InvoiceSubmission(
            content='{"numero": "FA-001"}',
            format=InvoiceFormat.EFACTGATE_JSON,
            target_connector_id="connector-test",
            enterprise_siret="12345678901111",  # Luhn-invalid
        )
        client = EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        )
        errors = client.validate(invalid_invoice)
        assert len(errors) > 0
        assert any("siret" in e.path.lower() for e in errors)


# ---------------------------------------------------------------------------
# E-Reporting tests
# ---------------------------------------------------------------------------


class TestEReporting:
    """Test EFactGateClient.submit_ereporting."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_submit_ereporting_success(self) -> None:
        """Successful e-reporting submission returns FluxCreatedResponse."""
        from efactgate_sdk.models.ereporting import EReportingSubmission

        respx.post(f"{MOCK_BASE_URL}/ereporting").mock(
            return_value=httpx.Response(
                201,
                json={
                    "flux_id": "b2c2d3e4-f5a6-7890-bcde-fa1234567890",
                    "status": "emis",
                    "submitted_at": "2024-06-15T12:00:00Z",
                },
            )
        )

        submission = EReportingSubmission(
            content='{"period": "2024-06", "total_b2c": "5000.00"}',
            format=InvoiceFormat.EFACTGATE_JSON,
            enterprise_siret=VALID_SIRET,
        )

        async with EFactGateClient(
            base_url=MOCK_BASE_URL,
            api_key="test-key",
        ) as client:
            result = await client.submit_ereporting(submission)

        assert result.status == FluxStatus.EMIS
