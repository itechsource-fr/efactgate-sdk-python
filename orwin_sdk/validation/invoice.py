"""Invoice validation orchestrator.

Validates required fields, date format, amount ranges, SIRET, and amount
coherence. Returns a list of FieldError without making any network call.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from orwin_sdk.exceptions import FieldError
from orwin_sdk.models.invoice import InvoiceSubmission
from orwin_sdk.validation.amounts import InvoiceAmounts, validate_amounts
from orwin_sdk.validation.siret import validate_siret

# Accepted amount range
_MIN_AMOUNT = Decimal("0.01")
_MAX_AMOUNT = Decimal("999999999.99")

# ISO 8601 date pattern (YYYY-MM-DD with optional time component)
_ISO_8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}"  # date part
    r"(?:T\d{2}:\d{2}:\d{2}"  # optional time
    r"(?:\.\d+)?"  # optional fractional seconds
    r"(?:Z|[+-]\d{2}:\d{2})?)?$"  # optional timezone
)


def validate(invoice: Any) -> list[FieldError]:
    """Validate an invoice locally without any network call.

    Performs the following checks:
    - Invoice is not None and is an InvoiceSubmission instance.
    - Required fields present: enterprise_siret, content, format, target_connector_id.
    - SIRET format (14 digits + Luhn).
    - If metadata contains 'date', validates ISO 8601 format.
    - If metadata contains amount fields, validates range [0.01, 999_999_999.99].
    - If metadata contains structured amounts, validates coherence.

    Args:
        invoice: The invoice submission to validate.

    Returns:
        List of FieldError; empty if the invoice is valid.
    """
    errors: list[FieldError] = []

    # Check for None or non-InvoiceSubmission
    if invoice is None:
        errors.append(
            FieldError(
                path="invoice",
                code="invoice_null",
                description="La facture est absente (None).",
            )
        )
        return errors

    if not isinstance(invoice, InvoiceSubmission):
        errors.append(
            FieldError(
                path="invoice",
                code="invoice_invalid_type",
                description="La facture doit être une instance de InvoiceSubmission.",
            )
        )
        return errors

    # Required fields
    errors.extend(_validate_required_fields(invoice))

    # SIRET validation
    errors.extend(validate_siret(invoice.enterprise_siret))

    # Metadata-based validations (dates, amounts)
    if invoice.metadata:
        errors.extend(_validate_metadata(invoice.metadata))

    return errors


def _validate_required_fields(invoice: InvoiceSubmission) -> list[FieldError]:
    """Check that all required fields are present and non-empty."""
    errors: list[FieldError] = []

    if not invoice.content:
        errors.append(
            FieldError(
                path="content",
                code="content_missing",
                description="Le contenu de la facture est obligatoire.",
            )
        )

    if not invoice.target_connector_id:
        errors.append(
            FieldError(
                path="target_connector_id",
                code="target_connector_id_missing",
                description="L'identifiant du connecteur cible est obligatoire.",
            )
        )

    return errors


def _validate_metadata(metadata: dict[str, str]) -> list[FieldError]:
    """Validate metadata fields: dates and amounts."""
    errors: list[FieldError] = []

    # Validate date fields in metadata
    date_fields = ("date", "invoice_date", "due_date")
    for field_name in date_fields:
        if field_name in metadata:
            value = metadata[field_name]
            if not _ISO_8601_PATTERN.match(value):
                errors.append(
                    FieldError(
                        path=f"metadata.{field_name}",
                        code="date_format_invalid",
                        description=(
                            f"La date '{field_name}' ({value}) "
                            f"n'est pas au format ISO 8601."
                        ),
                    )
                )

    # Validate amount fields in metadata
    amount_fields = ("amount", "total_ht", "total_ttc")
    for field_name in amount_fields:
        if field_name in metadata:
            errors.extend(
                _validate_amount_field(metadata[field_name], f"metadata.{field_name}")
            )

    return errors


def _validate_amount_field(value: str, field_path: str) -> list[FieldError]:
    """Validate that an amount string is a valid Decimal within accepted range."""
    errors: list[FieldError] = []

    try:
        amount = Decimal(value)
    except InvalidOperation:
        errors.append(
            FieldError(
                path=field_path,
                code="amount_invalid_format",
                description=f"Le montant '{value}' n'est pas un nombre décimal valide.",
            )
        )
        return errors

    if amount < _MIN_AMOUNT or amount > _MAX_AMOUNT:
        errors.append(
            FieldError(
                path=field_path,
                code="amount_out_of_range",
                description=(
                    f"Le montant ({amount}) doit être compris "
                    f"entre {_MIN_AMOUNT} et {_MAX_AMOUNT}."
                ),
            )
        )

    return errors


def validate_invoice_with_amounts(
    invoice: Any,
    amounts: InvoiceAmounts | None = None,
) -> list[FieldError]:
    """Validate an invoice including structured amount coherence.

    Combines basic invoice validation with amount coherence checks.

    Args:
        invoice: The invoice submission to validate.
        amounts: Optional structured amounts for coherence checking.

    Returns:
        List of FieldError; empty if all validations pass.
    """
    errors = validate(invoice)

    if amounts is not None:
        errors.extend(validate_amounts(amounts))

    return errors


__all__ = ["validate", "validate_invoice_with_amounts"]
