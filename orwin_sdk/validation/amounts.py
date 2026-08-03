"""Invoice amount coherence validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from orwin_sdk.exceptions import FieldError

# Tolerance for floating-point comparisons on amounts (±0.01€)
_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class InvoiceLine:
    """A single invoice line with HT amount, VAT rate, and VAT amount.

    Attributes:
        amount_ht: Net amount (hors taxe).
        vat_rate: VAT rate as a decimal (e.g. Decimal("0.20") for 20%).
        vat_amount: VAT amount for this line.
    """

    amount_ht: Decimal
    vat_rate: Decimal
    vat_amount: Decimal


@dataclass(frozen=True)
class InvoiceAmounts:
    """Aggregated invoice amounts for validation.

    Attributes:
        total_ht: Total net amount declared on the invoice.
        total_ttc: Total amount including tax declared on the invoice.
        lines: Individual invoice lines.
    """

    total_ht: Decimal
    total_ttc: Decimal
    lines: list[InvoiceLine]


def validate_amounts(amounts: InvoiceAmounts, field_path: str = "amounts") -> list[FieldError]:
    """Validate coherence of invoice amounts.

    Checks:
    - Total HT ≈ sum of line HT amounts (±0.01€)
    - Each line VAT amount ≈ line HT × VAT rate (±0.01€)
    - Total TTC ≈ Total HT + sum of VAT amounts (±0.01€)

    Args:
        amounts: The invoice amounts to validate.
        field_path: Dot-notation base path for error reporting.

    Returns:
        List of FieldError; empty if valid.
    """
    errors: list[FieldError] = []

    # Validate total HT vs sum of lines
    sum_ht = sum((line.amount_ht for line in amounts.lines), start=Decimal("0"))
    if abs(amounts.total_ht - sum_ht) > _TOLERANCE:
        errors.append(
            FieldError(
                path=f"{field_path}.total_ht",
                code="total_ht_mismatch",
                description=(
                    f"Le total HT ({amounts.total_ht}) ne correspond pas "
                    f"à la somme des lignes ({sum_ht}), écart > 0.01€."
                ),
            )
        )

    # Validate each line's VAT amount
    sum_vat = Decimal("0")
    for i, line in enumerate(amounts.lines):
        expected_vat = line.amount_ht * line.vat_rate
        if abs(line.vat_amount - expected_vat) > _TOLERANCE:
            errors.append(
                FieldError(
                    path=f"{field_path}.lines[{i}].vat_amount",
                    code="vat_amount_mismatch",
                    description=(
                        f"Le montant TVA de la ligne {i} ({line.vat_amount}) "
                        f"ne correspond pas à HT × taux "
                        f"({line.amount_ht} × {line.vat_rate} = {expected_vat}), "
                        f"écart > 0.01€."
                    ),
                )
            )
        sum_vat += line.vat_amount

    # Validate total TTC vs total HT + sum of VAT
    expected_ttc = amounts.total_ht + sum_vat
    if abs(amounts.total_ttc - expected_ttc) > _TOLERANCE:
        errors.append(
            FieldError(
                path=f"{field_path}.total_ttc",
                code="total_ttc_mismatch",
                description=(
                    f"Le total TTC ({amounts.total_ttc}) ne correspond pas "
                    f"à HT + TVA ({expected_ttc}), écart > 0.01€."
                ),
            )
        )

    return errors


__all__ = ["InvoiceAmounts", "InvoiceLine", "validate_amounts"]
