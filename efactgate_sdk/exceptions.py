"""Exception hierarchy for the Efactgate SDK Client.

All SDK exceptions inherit from EfactgateSDKError, providing a unified
error handling experience with structured error codes and messages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldError:
    """Structured validation error for a single field.

    Attributes:
        path: Dot-notation path to the offending field (e.g. "lines[0].amount").
        code: Machine-readable error code (e.g. "invalid_siret").
        description: Human-readable description of the validation failure.
    """

    path: str
    code: str
    description: str


class EfactgateSDKError(Exception):
    """Base exception for all SDK errors.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable error description.
    """

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ConfigurationError(EfactgateSDKError):
    """Raised when SDK configuration is invalid.

    Examples: missing required parameters, values out of accepted bounds,
    invalid URL format.
    """

    def __init__(self, *, code: str = "configuration_error", message: str) -> None:
        super().__init__(code=code, message=message)


class AuthenticationError(EfactgateSDKError):
    """Raised when authentication fails.

    Examples: invalid API key, OAuth2 token refresh failure,
    rejected credentials.
    """

    def __init__(self, *, code: str = "authentication_error", message: str) -> None:
        super().__init__(code=code, message=message)


class ValidationError(EfactgateSDKError):
    """Raised when local validation of input data fails.

    Contains a structured list of field-level errors describing each
    validation failure.

    Attributes:
        errors: List of FieldError instances detailing each invalid field.
    """

    def __init__(
        self,
        *,
        code: str = "validation_error",
        message: str,
        errors: list[FieldError] | None = None,
    ) -> None:
        self.errors: list[FieldError] = errors if errors is not None else []
        super().__init__(code=code, message=message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"errors={self.errors!r})"
        )


class ApiError(EfactgateSDKError):
    """Raised when the API returns an error response.

    Attributes:
        http_code: HTTP status code from the API response.
        flux_id: Associated flux identifier, if available.
    """

    def __init__(
        self,
        *,
        code: str = "api_error",
        message: str,
        http_code: int,
        flux_id: str | None = None,
    ) -> None:
        self.http_code = http_code
        self.flux_id = flux_id
        super().__init__(code=code, message=message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"http_code={self.http_code!r}, flux_id={self.flux_id!r})"
        )


class RequestError(ApiError):
    """Raised on non-retryable HTTP 4xx errors (excluding 429).

    The request was rejected by the server and will not be retried.

    Attributes:
        body: Response body (truncated to 1024 chars).
        url: The request URL.
    """

    def __init__(
        self,
        *,
        code: str = "request_error",
        message: str,
        http_code: int,
        flux_id: str | None = None,
        body: str = "",
        url: str = "",
    ) -> None:
        self.body = body
        self.url = url
        super().__init__(code=code, message=message, http_code=http_code, flux_id=flux_id)


class TransmissionError(ApiError):
    """Raised when all retry attempts are exhausted.

    Attributes:
        attempts: Total number of attempts made before giving up.
        body: Last response body (truncated to 1024 chars).
    """

    def __init__(
        self,
        *,
        code: str = "transmission_error",
        message: str,
        http_code: int,
        flux_id: str | None = None,
        attempts: int,
        body: str = "",
    ) -> None:
        self.attempts = attempts
        self.body = body
        super().__init__(code=code, message=message, http_code=http_code, flux_id=flux_id)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"http_code={self.http_code!r}, flux_id={self.flux_id!r}, "
            f"attempts={self.attempts!r})"
        )


class TimeoutError(EfactgateSDKError):  # noqa: A001
    """Raised when poll_until_final exceeds its timeout.

    Attributes:
        flux_id: The flux being polled.
        last_status: Last observed status before timeout.
        elapsed_seconds: Total time elapsed in seconds.
    """

    def __init__(
        self,
        *,
        code: str = "timeout_error",
        message: str,
        flux_id: str,
        last_status: str,
        elapsed_seconds: float,
    ) -> None:
        self.flux_id = flux_id
        self.last_status = last_status
        self.elapsed_seconds = elapsed_seconds
        super().__init__(code=code, message=message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"flux_id={self.flux_id!r}, last_status={self.last_status!r}, "
            f"elapsed_seconds={self.elapsed_seconds!r})"
        )


class NotFoundError(EfactgateSDKError):
    """Raised when a flux_id is not found or not accessible.

    Attributes:
        flux_id: The flux identifier that was not found.
    """

    def __init__(
        self,
        *,
        code: str = "not_found",
        message: str,
        flux_id: str,
    ) -> None:
        self.flux_id = flux_id
        super().__init__(code=code, message=message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"flux_id={self.flux_id!r})"
        )


class DeserializationError(EfactgateSDKError):
    """Raised when JSON deserialization fails.

    Attributes:
        field: The field that caused the deserialization failure.
        reason: Description of why deserialization failed.
    """

    def __init__(
        self,
        *,
        code: str = "deserialization_error",
        message: str,
        field: str,
        reason: str,
    ) -> None:
        self.field = field
        self.reason = reason
        super().__init__(code=code, message=message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"field={self.field!r}, reason={self.reason!r})"
        )


__all__ = [
    "ApiError",
    "AuthenticationError",
    "ConfigurationError",
    "DeserializationError",
    "FieldError",
    "NotFoundError",
    "EfactgateSDKError",
    "RequestError",
    "TimeoutError",
    "TransmissionError",
    "ValidationError",
]
