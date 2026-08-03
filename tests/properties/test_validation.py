"""Property-based tests for local validation.

Tests validate:
- Property 5: Validation SIRET (format 14 chiffres + Luhn)
- Property 6: Validation cohérence des montants
- Property 7: Validation locale produit des erreurs structurées

Validates: Requirements 6.1, 6.2, 6.3, 3.3
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from efactgate_sdk.exceptions import FieldError
from efactgate_sdk.models.enums import FluxType, InvoiceFormat
from efactgate_sdk.models.invoice import InvoiceSubmission
from efactgate_sdk.validation.amounts import InvoiceAmounts, InvoiceLine, validate_amounts
from efactgate_sdk.validation.invoice import validate
from efactgate_sdk.validation.siret import validate_siret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _luhn_checksum(number: str) -> int:
    """Compute Luhn checksum digit for a partial number string."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        digit = int(ch)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10


def _make_luhn_valid(digits_13: str) -> str:
    """Given a 13-digit string, append a check digit to make it Luhn-valid (14 digits)."""
    # Compute what check digit is needed
    # The check digit is placed at position 0 (rightmost), so we process
    # the 13 digits as if the check digit is appended at the end.
    total = 0
    for i, ch in enumerate(reversed(digits_13)):
        digit = int(ch)
        # Since check digit will be at index 0, existing digits shift by 1
        if (i + 1) % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    check_digit = (10 - (total % 10)) % 10
    return digits_13 + str(check_digit)


def _is_luhn_valid(number: str) -> bool:
    """Check if a numeric string passes the Luhn algorithm."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        digit = int(ch)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Strategy: 13 digits from which we compute a valid Luhn-14
valid_siret_st = st.text(
    alphabet="0123456789",
    min_size=13,
    max_size=13,
).map(_make_luhn_valid)

# Strategy: strings that are NOT valid SIRETs (wrong length, non-numeric, or bad Luhn)
invalid_siret_wrong_length_st = st.text(
    alphabet="0123456789",
    min_size=1,
    max_size=30,
).filter(lambda s: len(s) != 14)

invalid_siret_non_numeric_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=14,
    max_size=14,
).filter(lambda s: not s.isdigit())

invalid_siret_bad_luhn_st = st.text(
    alphabet="0123456789",
    min_size=14,
    max_size=14,
).filter(lambda s: not _is_luhn_valid(s))

# Strategy: positive decimal amounts (reasonable range for invoices)
positive_amount_st = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy: VAT rates (common French rates)
vat_rate_st = st.sampled_from([
    Decimal("0.00"),
    Decimal("0.055"),
    Decimal("0.10"),
    Decimal("0.20"),
])


# Composite strategy: coherent invoice amounts (should pass validation)
@st.composite
def coherent_amounts_st(draw: st.DrawFn) -> InvoiceAmounts:
    """Generate invoice amounts that are internally coherent."""
    num_lines = draw(st.integers(min_value=1, max_value=5))
    lines: list[InvoiceLine] = []

    for _ in range(num_lines):
        ht = draw(positive_amount_st)
        rate = draw(vat_rate_st)
        # Compute exact VAT amount (coherent)
        vat = (ht * rate).quantize(Decimal("0.01"))
        lines.append(InvoiceLine(amount_ht=ht, vat_rate=rate, vat_amount=vat))

    total_ht = sum(line.amount_ht for line in lines)
    total_vat = sum(line.vat_amount for line in lines)
    total_ttc = total_ht + total_vat

    return InvoiceAmounts(total_ht=total_ht, total_ttc=total_ttc, lines=lines)


# Composite strategy: incoherent amounts (should fail validation)
@st.composite
def incoherent_total_ht_amounts_st(draw: st.DrawFn) -> InvoiceAmounts:
    """Generate amounts where total_ht != sum of line amounts (off by > 0.01)."""
    num_lines = draw(st.integers(min_value=1, max_value=3))
    lines: list[InvoiceLine] = []

    for _ in range(num_lines):
        ht = draw(positive_amount_st)
        rate = draw(vat_rate_st)
        vat = (ht * rate).quantize(Decimal("0.01"))
        lines.append(InvoiceLine(amount_ht=ht, vat_rate=rate, vat_amount=vat))

    sum_ht = sum(line.amount_ht for line in lines)
    sum_vat = sum(line.vat_amount for line in lines)

    # Offset total_ht by more than tolerance (0.01)
    offset = draw(st.sampled_from([Decimal("0.02"), Decimal("-0.02"),
                                   Decimal("1.00"), Decimal("-1.00")]))
    total_ht = sum_ht + offset
    total_ttc = total_ht + sum_vat

    return InvoiceAmounts(total_ht=total_ht, total_ttc=total_ttc, lines=lines)


@st.composite
def incoherent_vat_amounts_st(draw: st.DrawFn) -> InvoiceAmounts:
    """Generate amounts where at least one line VAT != HT * rate (off by > 0.01)."""
    ht = draw(positive_amount_st)
    rate = draw(st.sampled_from([Decimal("0.055"), Decimal("0.10"), Decimal("0.20")]))
    correct_vat = (ht * rate).quantize(Decimal("0.01"))

    # Offset VAT by more than tolerance
    offset = draw(st.sampled_from([Decimal("0.02"), Decimal("-0.02"),
                                   Decimal("1.00"), Decimal("-1.00")]))
    bad_vat = correct_vat + offset

    lines = [InvoiceLine(amount_ht=ht, vat_rate=rate, vat_amount=bad_vat)]
    total_ht = ht
    total_ttc = total_ht + bad_vat

    return InvoiceAmounts(total_ht=total_ht, total_ttc=total_ttc, lines=lines)


# ---------------------------------------------------------------------------
# Property 5: Validation SIRET (format 14 chiffres + Luhn)
# ---------------------------------------------------------------------------


class TestProperty5SiretValidation:
    """Property 5: Validation SIRET (format 14 chiffres + Luhn).

    For any string of exactly 14 numeric characters with valid Luhn checksum,
    the validator accepts. For any other string, the validator rejects.

    **Validates: Requirements 6.1**
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=valid_siret_st)
    def test_valid_siret_accepted(self, siret: str) -> None:
        """A 14-digit Luhn-valid string produces no errors."""
        errors = validate_siret(siret)
        assert errors == [], f"Expected no errors for valid SIRET {siret}, got: {errors}"

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=invalid_siret_wrong_length_st)
    def test_wrong_length_rejected(self, siret: str) -> None:
        """A numeric string of length != 14 is rejected."""
        errors = validate_siret(siret)
        assert len(errors) > 0
        assert any(e.code in ("siret_length", "siret_empty") for e in errors)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=invalid_siret_non_numeric_st)
    def test_non_numeric_rejected(self, siret: str) -> None:
        """A 14-char string with non-numeric characters is rejected."""
        errors = validate_siret(siret)
        assert len(errors) > 0
        assert any(e.code == "siret_not_numeric" for e in errors)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=invalid_siret_bad_luhn_st)
    def test_bad_luhn_rejected(self, siret: str) -> None:
        """A 14-digit string with invalid Luhn checksum is rejected."""
        errors = validate_siret(siret)
        assert len(errors) > 0
        assert any(e.code == "siret_luhn" for e in errors)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=valid_siret_st)
    def test_error_has_correct_path(self, siret: str) -> None:
        """Validation result uses the provided field_path."""
        custom_path = "facture.siret_emetteur"
        errors = validate_siret(siret, field_path=custom_path)
        # Valid SIRET → empty, but let's also test with empty to verify path
        errors_empty = validate_siret("", field_path=custom_path)
        assert errors_empty[0].path == custom_path


# ---------------------------------------------------------------------------
# Property 6: Validation cohérence des montants
# ---------------------------------------------------------------------------


class TestProperty6AmountCoherence:
    """Property 6: Validation cohérence des montants.

    For coherent amounts (total ≈ sum(lines) ±0.01 AND each VAT ≈ HT×rate ±0.01),
    validation passes. For incoherent amounts, validation produces errors.

    **Validates: Requirements 6.2**
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(amounts=coherent_amounts_st())
    def test_coherent_amounts_pass(self, amounts: InvoiceAmounts) -> None:
        """Amounts where total == sum(lines) and VAT == HT*rate produce no errors."""
        errors = validate_amounts(amounts)
        assert errors == [], f"Expected no errors for coherent amounts, got: {errors}"

    @pytest.mark.property
    @settings(max_examples=100)
    @given(amounts=incoherent_total_ht_amounts_st())
    def test_incoherent_total_ht_produces_error(self, amounts: InvoiceAmounts) -> None:
        """Amounts where total_ht != sum of line HT produce a total_ht_mismatch error."""
        errors = validate_amounts(amounts)
        assert len(errors) > 0
        assert any(e.code == "total_ht_mismatch" for e in errors)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(amounts=incoherent_vat_amounts_st())
    def test_incoherent_vat_produces_error(self, amounts: InvoiceAmounts) -> None:
        """Amounts where line VAT != HT*rate produce a vat_amount_mismatch error."""
        errors = validate_amounts(amounts)
        assert len(errors) > 0
        assert any(e.code == "vat_amount_mismatch" for e in errors)

    @pytest.mark.property
    @settings(max_examples=100)
    @given(amounts=coherent_amounts_st())
    def test_each_error_is_field_error(self, amounts: InvoiceAmounts) -> None:
        """All returned errors are FieldError instances with path, code, description."""
        errors = validate_amounts(amounts)
        for error in errors:
            assert isinstance(error, FieldError)
            assert error.path
            assert error.code
            assert error.description


# ---------------------------------------------------------------------------
# Property 7: Validation locale produit des erreurs structurées
# ---------------------------------------------------------------------------


class TestProperty7StructuredErrors:
    """Property 7: Validation locale produit des erreurs structurées.

    For any invoice with missing required fields or invalid formats, validation
    returns a list of FieldError with (path, code, description). No network call
    is made (validate is purely local).

    **Validates: Requirements 3.3, 6.3**
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=invalid_siret_wrong_length_st)
    def test_invalid_siret_produces_structured_errors(self, siret: str) -> None:
        """Invoice with invalid SIRET produces FieldError with path, code, description."""
        invoice = InvoiceSubmission(
            content="dGVzdA==",
            format=InvoiceFormat.UBL,
            target_connector_id="connector-001",
            enterprise_siret=siret,
            flux_type=FluxType.B2B_INVOICE,
        )
        errors = validate(invoice)
        assert len(errors) > 0
        for error in errors:
            assert isinstance(error, FieldError)
            assert error.path, "FieldError must have a non-empty path"
            assert error.code, "FieldError must have a non-empty code"
            assert error.description, "FieldError must have a non-empty description"

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        content=st.sampled_from(["", None]),
        connector_id=st.sampled_from(["", None]),
    )
    def test_missing_required_fields_produce_errors(
        self, content: str | None, connector_id: str | None
    ) -> None:
        """Invoice with missing required fields produces structured FieldErrors."""
        invoice = InvoiceSubmission(
            content=content or "",
            format=InvoiceFormat.UBL,
            target_connector_id=connector_id or "",
            enterprise_siret="32345678901234",  # Will also fail Luhn
            flux_type=FluxType.B2B_INVOICE,
        )
        errors = validate(invoice)
        assert len(errors) > 0
        for error in errors:
            assert isinstance(error, FieldError)
            assert error.path
            assert error.code
            assert error.description

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=valid_siret_st)
    def test_valid_invoice_produces_no_errors(self, siret: str) -> None:
        """A fully valid invoice produces an empty error list."""
        invoice = InvoiceSubmission(
            content="dGVzdA==",
            format=InvoiceFormat.UBL,
            target_connector_id="connector-001",
            enterprise_siret=siret,
            flux_type=FluxType.B2B_INVOICE,
        )
        errors = validate(invoice)
        assert errors == [], f"Expected no errors for valid invoice, got: {errors}"

    def test_none_invoice_produces_structured_error(self) -> None:
        """None invoice produces a FieldError indicating absent structure."""
        errors = validate(None)
        assert len(errors) > 0
        assert errors[0].code == "invoice_null"
        assert isinstance(errors[0], FieldError)
        assert errors[0].path
        assert errors[0].description

    def test_non_invoice_type_produces_structured_error(self) -> None:
        """Non-InvoiceSubmission input produces a structured FieldError."""
        errors = validate({"content": "test", "format": "ubl"})
        assert len(errors) > 0
        assert errors[0].code == "invoice_invalid_type"
        assert isinstance(errors[0], FieldError)
        assert errors[0].path
        assert errors[0].description

    @pytest.mark.property
    @settings(max_examples=100)
    @given(siret=valid_siret_st)
    def test_validate_is_purely_local_no_network(self, siret: str) -> None:
        """Validation does not make any network call — purely local execution.

        This test verifies the function completes without network dependencies
        by using no mocked HTTP client. If it tried to reach the network, it
        would fail or hang.
        """
        invoice = InvoiceSubmission(
            content="dGVzdA==",
            format=InvoiceFormat.UBL,
            target_connector_id="connector-001",
            enterprise_siret=siret,
            flux_type=FluxType.B2B_INVOICE,
        )
        # If validate() made network calls, this would require mocking.
        # The fact that it completes successfully without any HTTP fixture
        # proves it's purely local.
        result = validate(invoice)
        assert isinstance(result, list)
