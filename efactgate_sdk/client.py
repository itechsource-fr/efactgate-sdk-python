"""EfactgateClient — Point d'entrée principal du SDK API Universelle.

Assembles authentication, transport, validation, and observability
into a single async client for the GW-eFactures API.

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 4.5, 11.1, 11.3
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import TracebackType
from typing import Any

from efactgate_sdk.auth.api_key import ApiKeyAuthenticator
from efactgate_sdk.auth.base import AuthenticatorBase
from efactgate_sdk.auth.oauth2 import OAuth2Authenticator
from efactgate_sdk.config import (
    ApiKeyCredentials,
    ClientConfig,
    OAuth2Credentials,
    load_config,
)
from efactgate_sdk.exceptions import (
    ApiError,
    NotFoundError,
    TimeoutError,
    ValidationError,
)
from efactgate_sdk.models.ack import AckResponse
from efactgate_sdk.models.enums import FluxStatus, ImportFormat
from efactgate_sdk.models.ereporting import EReportingSubmission
from efactgate_sdk.models.errors import FieldError
from efactgate_sdk.models.invoice import (
    BatchResponse,
    FluxCreatedResponse,
    ImportReport,
    InvoiceSubmission,
)
from efactgate_sdk.models.status import FluxStatusResponse
from efactgate_sdk.observability.hooks import EventHooks
from efactgate_sdk.observability.logger import StructuredLogger
from efactgate_sdk.transport.http_client import HttpTransport
from efactgate_sdk.transport.retry import RetryPolicy
from efactgate_sdk.transport.serialization import deserialize, serialize
from efactgate_sdk.validation.invoice import validate

# Terminal statuses that end the polling loop
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    FluxStatus.ACCEPTE.value,
    FluxStatus.REJETE.value,
    FluxStatus.ECHOUE.value,
})


class EfactgateClient:
    """Async client for the GW-eFactures API Universelle.

    Provides methods for:
    - Submitting invoices (B2B) and e-reporting data (B2C)
    - Batch submission and file import
    - Status polling and ACK retrieval
    - Local validation (without network calls)

    Supports async context manager for proper resource cleanup.

    Example:
        async with EfactgateClient(
            base_url="https://api.gw-efactures.efactgate.io/api/v1",
            api_key="my-api-key",
        ) as client:
            result = await client.submit_invoice(invoice)
            status = await client.poll_until_final(result.flux_id)
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_token_endpoint: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        sandbox: bool = False,
        log_level: str = "WARNING",
        hooks: EventHooks | None = None,
        retry_delays: tuple[float, ...] | None = None,
    ) -> None:
        """Initialize the SDK client.

        Args:
            base_url: API base URL (or set EFACTGATE_API_URL env var).
            api_key: API key for X-API-Key auth (or set EFACTGATE_API_KEY).
            oauth_client_id: OAuth2 client ID (or set EFACTGATE_OAUTH_CLIENT_ID).
            oauth_client_secret: OAuth2 client secret (or set EFACTGATE_OAUTH_CLIENT_SECRET).
            oauth_token_endpoint: OAuth2 token endpoint URL.
            timeout: Request timeout in seconds (default 30, bounds [1, 300]).
            max_retries: Max retry attempts (default 5, bounds [0, 10]).
            sandbox: Force all requests to sandbox URL.
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            hooks: Optional event hooks for custom observability.
            retry_delays: Custom retry delay sequence.

        Raises:
            ConfigurationError: If configuration is invalid or incomplete.
        """
        # Load and validate configuration
        self._config: ClientConfig = load_config(
            base_url=base_url,
            api_key=api_key,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_token_endpoint=oauth_token_endpoint,
            timeout=timeout,
            max_retries=max_retries,
            sandbox=sandbox,
            log_level=log_level,
            retry_delays=retry_delays,
        )

        # Initialize observability
        self._logger = StructuredLogger(level=self._config.log_level)
        self._hooks = hooks

        # Initialize authenticator
        self._authenticator: AuthenticatorBase = self._build_authenticator()

        # Initialize transport
        self._transport = HttpTransport(
            base_url=self._config.base_url,
            authenticator=self._authenticator,
            timeout=self._config.timeout,
            retry_policy=RetryPolicy(
                max_retries=self._config.max_retries,
                delays=self._config.retry_delays,
            ),
            hooks=self._hooks,
        )

    # ------------------------------------------------------------------
    # Submission methods
    # ------------------------------------------------------------------

    async def submit_invoice(self, invoice: InvoiceSubmission) -> FluxCreatedResponse:
        """Submit a B2B invoice to the API.

        Validates the invoice locally before sending. If validation fails,
        raises ValidationError without making a network call.

        Args:
            invoice: The invoice data to submit.

        Returns:
            FluxCreatedResponse with flux_id and initial status.

        Raises:
            ValidationError: If local validation fails.
            RequestError: On non-retryable 4xx from API.
            TransmissionError: If all retries are exhausted.
        """
        # Local validation first
        errors = validate(invoice)
        if errors:
            raise ValidationError(
                code="validation_error",
                message="Invoice validation failed",
                errors=errors,
            )

        response = await self._transport.request(
            "POST",
            "/invoices",
            json=self._serialize_to_dict(invoice),
        )
        return deserialize(response.text, FluxCreatedResponse)

    async def submit_ereporting(
        self, data: EReportingSubmission
    ) -> FluxCreatedResponse:
        """Submit B2C e-reporting data to the API.

        Args:
            data: The e-reporting submission data.

        Returns:
            FluxCreatedResponse with flux_id and initial status.

        Raises:
            RequestError: On non-retryable 4xx from API.
            TransmissionError: If all retries are exhausted.
        """
        response = await self._transport.request(
            "POST",
            "/ereporting",
            json=self._serialize_to_dict(data),
        )
        return deserialize(response.text, FluxCreatedResponse)

    async def submit_batch(
        self, documents: list[InvoiceSubmission | EReportingSubmission]
    ) -> BatchResponse:
        """Submit a batch of documents (1 to 1000).

        Args:
            documents: List of invoices and/or e-reporting submissions.

        Returns:
            BatchResponse with results per document.

        Raises:
            ValidationError: If batch is empty or exceeds 1000 documents.
            RequestError: On non-retryable 4xx from API.
            TransmissionError: If all retries are exhausted.
        """
        if not documents or len(documents) > 1000:
            raise ValidationError(
                code="validation_error",
                message=f"Batch must contain 1 to 1000 documents, got {len(documents)}",
                errors=[
                    FieldError(
                        path="documents",
                        code="batch_size_invalid",
                        description=f"Expected 1-1000 documents, got {len(documents)}",
                    )
                ],
            )

        payload = [self._serialize_to_dict(doc) for doc in documents]
        response = await self._transport.request(
            "POST",
            "/batch",
            json={"documents": payload},
        )
        return deserialize(response.text, BatchResponse)

    async def import_file(
        self, file_path: Path, format: ImportFormat
    ) -> ImportReport:
        """Import a file (CSV, XML UBL, XML CII, PDF Factur-X).

        Args:
            file_path: Path to the file to import.
            format: Import format identifier.

        Returns:
            ImportReport with counts and error details.

        Raises:
            RequestError: On non-retryable 4xx from API.
            TransmissionError: If all retries are exhausted.
            FileNotFoundError: If the file does not exist.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Import file not found: {file_path}")

        content = file_path.read_bytes()
        response = await self._transport.request(
            "POST",
            f"/import/{format.value}",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        return deserialize(response.text, ImportReport)

    # ------------------------------------------------------------------
    # Status and ACK retrieval
    # ------------------------------------------------------------------

    async def get_status(self, flux_id: str) -> FluxStatusResponse:
        """Get the current status of a flux.

        Args:
            flux_id: The flux identifier.

        Returns:
            FluxStatusResponse with current status and transition history.

        Raises:
            NotFoundError: If flux_id does not exist or belongs to another tenant.
            RequestError: On non-retryable 4xx from API.
            TransmissionError: If all retries are exhausted.
        """
        try:
            response = await self._transport.request("GET", f"/status/{flux_id}")
        except ApiError as exc:
            if exc.http_code == 404:
                raise NotFoundError(
                    code="not_found",
                    message=f"Flux '{flux_id}' not found",
                    flux_id=flux_id,
                ) from exc
            raise
        return deserialize(response.text, FluxStatusResponse)

    async def get_ack(self, flux_id: str) -> AckResponse | None:
        """Retrieve the acknowledgement for a flux.

        Args:
            flux_id: The flux identifier.

        Returns:
            AckResponse if available, None if ACK is not yet ready.

        Raises:
            NotFoundError: If flux_id does not exist.
            RequestError: On non-retryable 4xx from API.
            TransmissionError: If all retries are exhausted.
        """
        try:
            response = await self._transport.request("GET", f"/ack/{flux_id}")
        except ApiError as exc:
            if exc.http_code == 404:
                raise NotFoundError(
                    code="not_found",
                    message=f"Flux '{flux_id}' not found",
                    flux_id=flux_id,
                ) from exc
            raise

        if response.status_code == 204:
            return None

        return deserialize(response.text, AckResponse)

    async def poll_until_final(
        self,
        flux_id: str,
        *,
        timeout: float = 300.0,
        interval: float = 5.0,
    ) -> FluxStatusResponse:
        """Poll status until a terminal state is reached or timeout expires.

        Terminal states: accepté, rejeté, échoué.

        Args:
            flux_id: The flux identifier to poll.
            timeout: Maximum polling duration in seconds (default 300s).
            interval: Delay between polls in seconds (default 5s).

        Returns:
            FluxStatusResponse at a terminal status.

        Raises:
            TimeoutError: If timeout expires without reaching a terminal status.
            NotFoundError: If flux_id does not exist.
        """
        start = time.monotonic()
        last_status: FluxStatusResponse | None = None

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                raise TimeoutError(
                    code="poll_timeout",
                    message=(
                        f"Polling timeout after {elapsed:.1f}s for flux '{flux_id}'. "
                        f"Last status: {last_status.status.value if last_status else 'unknown'}"
                    ),
                    flux_id=flux_id,
                    last_status=(
                        last_status.status.value if last_status else "unknown"
                    ),
                    elapsed_seconds=elapsed,
                )

            last_status = await self.get_status(flux_id)

            if last_status.status.value in _TERMINAL_STATUSES:
                return last_status

            await asyncio.sleep(interval)

    # ------------------------------------------------------------------
    # Local validation
    # ------------------------------------------------------------------

    def validate(self, invoice: InvoiceSubmission) -> list[FieldError]:
        """Validate an invoice locally without network calls.

        Args:
            invoice: The invoice to validate.

        Returns:
            List of validation errors. Empty list means validation passed.
        """
        return validate(invoice)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP transport and release resources."""
        await self._transport.close()

    async def __aenter__(self) -> EfactgateClient:
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager, closing the transport."""
        await self.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_authenticator(self) -> AuthenticatorBase:
        """Create the appropriate authenticator based on credentials type."""
        creds = self._config.credentials
        if isinstance(creds, ApiKeyCredentials):
            return ApiKeyAuthenticator(api_key=creds.api_key)
        if isinstance(creds, OAuth2Credentials):
            return OAuth2Authenticator(
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                token_endpoint=creds.token_endpoint,
                max_refresh_attempts=creds.max_refresh_attempts,
            )
        msg = f"Unsupported credentials type: {type(creds)}"
        raise TypeError(msg)

    @staticmethod
    def _serialize_to_dict(model: Any) -> dict[str, Any]:
        """Serialize a model to a JSON-compatible dict.

        Uses the SDK serialization module for consistent format handling
        (datetime→ISO 8601 Z, UUID→lowercase, Enum→value).
        """
        import json
        from typing import cast

        json_str = serialize(model)
        return cast("dict[str, Any]", json.loads(json_str))


__all__ = ["EfactgateClient"]
