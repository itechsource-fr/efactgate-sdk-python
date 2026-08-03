"""Error models for the Efactgate SDK Client.

Re-exports FieldError from the exceptions module and provides
additional error response models.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from efactgate_sdk.exceptions import FieldError


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    """Structured API error response.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable error description.
        errors: Optional list of field-level validation errors.
    """

    code: str
    message: str
    errors: list[FieldError] = field(default_factory=list)


__all__ = [
    "ErrorResponse",
    "FieldError",
]
