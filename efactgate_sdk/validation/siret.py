"""SIRET number validation (14 digits + Luhn checksum)."""

from __future__ import annotations

from efactgate_sdk.exceptions import FieldError


def validate_siret(siret: str, field_path: str = "enterprise_siret") -> list[FieldError]:
    """Validate a SIRET number: exactly 14 numeric digits with valid Luhn checksum.

    Args:
        siret: The SIRET string to validate.
        field_path: Dot-notation path for error reporting.

    Returns:
        List of FieldError; empty if valid.
    """
    errors: list[FieldError] = []

    if not siret:
        errors.append(
            FieldError(
                path=field_path,
                code="siret_empty",
                description="Le SIRET est obligatoire.",
            )
        )
        return errors

    if len(siret) != 14:
        errors.append(
            FieldError(
                path=field_path,
                code="siret_length",
                description="Le SIRET doit contenir exactement 14 caractères.",
            )
        )
        return errors

    if not siret.isdigit():
        errors.append(
            FieldError(
                path=field_path,
                code="siret_not_numeric",
                description="Le SIRET ne doit contenir que des chiffres.",
            )
        )
        return errors

    if not _luhn_check(siret):
        errors.append(
            FieldError(
                path=field_path,
                code="siret_luhn",
                description="La clé de contrôle Luhn du SIRET est invalide.",
            )
        )

    return errors


def _luhn_check(number: str) -> bool:
    """Check the Luhn algorithm validity of a numeric string.

    The algorithm processes digits from right to left. Every second digit
    (from the right, starting at position 2) is doubled; if the result
    exceeds 9, subtract 9. The total sum must be divisible by 10.
    """
    total = 0
    for i, ch in enumerate(reversed(number)):
        digit = int(ch)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


__all__ = ["validate_siret"]
