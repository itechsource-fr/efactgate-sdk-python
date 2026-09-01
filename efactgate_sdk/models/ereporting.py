"""E-Reporting submission model for the eFactGate SDK Client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from efactgate_sdk.models.enums import InvoiceFormat


@dataclass(frozen=True, slots=True)
class EReportingSubmission:
    """Data required to submit an e-Reporting document.

    Attributes:
        content: Base64-encoded content or JSON string of the e-Reporting data.
        format: The format of the document.
        enterprise_siret: 14-digit SIRET number (Luhn-valid).
        metadata: Optional key-value metadata.
    """

    content: str
    format: InvoiceFormat
    enterprise_siret: str
    metadata: dict[str, str] | None = None


__all__ = [
    "EReportingSubmission",
]
