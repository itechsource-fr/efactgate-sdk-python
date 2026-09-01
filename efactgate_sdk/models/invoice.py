"""Invoice-related data models for the eFactGate SDK Client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from efactgate_sdk.models.enums import FluxType

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from efactgate_sdk.models.enums import FluxStatus, InvoiceFormat


@dataclass(frozen=True, slots=True)
class InvoiceSubmission:
    """Data required to submit an invoice via the SDK.

    Attributes:
        content: Base64-encoded content or JSON string of the invoice.
        format: The format of the invoice document.
        target_connector_id: Identifier of the target connector.
        enterprise_siret: 14-digit SIRET number (Luhn-valid).
        flux_type: Type of flux (default: B2B invoice).
        metadata: Optional key-value metadata.
    """

    content: str
    format: InvoiceFormat
    target_connector_id: str
    enterprise_siret: str
    flux_type: FluxType = FluxType.B2B_INVOICE
    metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class FluxCreatedResponse:
    """Response returned when an invoice is successfully submitted.

    Attributes:
        flux_id: Unique identifier of the created flux.
        status: Initial status of the flux (typically EMIS).
        submitted_at: UTC timestamp of submission.
    """

    flux_id: UUID
    status: FluxStatus
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class BatchResponse:
    """Response returned for batch submissions.

    Attributes:
        flux_ids: List of flux identifiers created.
        total_submitted: Number of documents successfully submitted.
        total_errors: Number of documents that failed.
    """

    flux_ids: list[UUID]
    total_submitted: int
    total_errors: int


@dataclass(frozen=True, slots=True)
class ImportErrorDetail:
    """Describes an error encountered during file import.

    Attributes:
        line_or_section: Identifier of the line or section where the error occurred.
        code: Machine-readable error code.
        message: Human-readable description of the error.
    """

    line_or_section: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Report returned after a file import operation.

    Attributes:
        total_created: Number of flux successfully created.
        total_errors: Number of errors encountered.
        errors: Detailed list of import errors.
    """

    total_created: int
    total_errors: int
    errors: list[ImportErrorDetail] = field(default_factory=list)


__all__ = [
    "BatchResponse",
    "FluxCreatedResponse",
    "ImportErrorDetail",
    "ImportReport",
    "InvoiceSubmission",
]
