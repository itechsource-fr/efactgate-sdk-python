"""Local data validation (SIRET, amounts, invoices).

Public API:
- validate(invoice) -> list[FieldError]: validate an InvoiceSubmission locally.
- validate_invoice_with_amounts(invoice, amounts) -> list[FieldError]:
  validate with amount coherence.
- validate_siret(siret) -> list[FieldError]: validate a SIRET number.
- validate_amounts(amounts) -> list[FieldError]: validate amount coherence.
- InvoiceLine, InvoiceAmounts: intermediate models for amount validation.
"""

from efactgate_sdk.validation.amounts import InvoiceAmounts, InvoiceLine, validate_amounts
from efactgate_sdk.validation.invoice import validate, validate_invoice_with_amounts
from efactgate_sdk.validation.siret import validate_siret

__all__ = [
    "InvoiceAmounts",
    "InvoiceLine",
    "validate",
    "validate_amounts",
    "validate_invoice_with_amounts",
    "validate_siret",
]
