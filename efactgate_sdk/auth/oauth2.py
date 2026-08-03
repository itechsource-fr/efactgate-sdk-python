"""OAuth2 authenticator — client_credentials flow with automatic token refresh."""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt

from efactgate_sdk.auth.base import AuthenticatorBase
from efactgate_sdk.exceptions import (
    AuthenticationError,
    ConfigurationError,
)

_EXPIRY_MARGIN_SECONDS = 30
"""Safety margin (in seconds) before actual token expiry to trigger refresh."""

_MIN_REFRESH_ATTEMPTS = 1
_MAX_REFRESH_ATTEMPTS = 10
_DEFAULT_REFRESH_ATTEMPTS = 3


class OAuth2Authenticator(AuthenticatorBase):
    """Authenticator using OAuth2 client_credentials flow.

    Obtains a Bearer token from the token endpoint using client_id and
    client_secret. Supports automatic token refresh on expiry or 401
    responses, and optional tenant_id extraction from the JWT.

    Args:
        client_id: OAuth2 client identifier.
        client_secret: OAuth2 client secret.
        token_endpoint: URL of the OAuth2 token endpoint.
        max_refresh_attempts: Maximum number of refresh attempts before
            raising AuthenticationError. Must be in [1, 10]. Defaults to 3.
        multi_tenant: If True, extract tenant_id from the JWT and include
            it in the X-Tenant-ID header. Defaults to False.

    Raises:
        ConfigurationError: If max_refresh_attempts is out of bounds.
        AuthenticationError: If client_id or client_secret is empty.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_endpoint: str,
        *,
        max_refresh_attempts: int = _DEFAULT_REFRESH_ATTEMPTS,
        multi_tenant: bool = False,
    ) -> None:
        if not client_id or not client_id.strip():
            raise AuthenticationError(
                code="invalid_credentials",
                message="OAuth2 client_id must be a non-empty string.",
            )
        if not client_secret or not client_secret.strip():
            raise AuthenticationError(
                code="invalid_credentials",
                message="OAuth2 client_secret must be a non-empty string.",
            )
        if not token_endpoint or not token_endpoint.strip():
            raise AuthenticationError(
                code="invalid_token_endpoint",
                message="OAuth2 token_endpoint must be a non-empty string.",
            )
        if not (_MIN_REFRESH_ATTEMPTS <= max_refresh_attempts <= _MAX_REFRESH_ATTEMPTS):
            raise ConfigurationError(
                message=(
                    f"max_refresh_attempts must be between {_MIN_REFRESH_ATTEMPTS} "
                    f"and {_MAX_REFRESH_ATTEMPTS}, got {max_refresh_attempts}."
                ),
            )

        self._client_id = client_id
        self._client_secret = client_secret
        self._token_endpoint = token_endpoint
        self._max_refresh_attempts = max_refresh_attempts
        self._multi_tenant = multi_tenant

        self._access_token: str | None = None
        self._token_expiry: float = 0.0
        self._tenant_id: str | None = None
        self._refresh_attempt_count: int = 0

    async def get_headers(self) -> dict[str, str]:
        """Return Authorization (and optionally X-Tenant-ID) headers.

        If no token has been obtained yet or the current token is expired,
        a new token is fetched from the token endpoint.

        Returns:
            Dictionary with Bearer Authorization header and optionally
            X-Tenant-ID header when multi_tenant is enabled.

        Raises:
            AuthenticationError: If token acquisition fails.
        """
        if self._access_token is None or self.is_expired():
            await self.refresh()

        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}
        if self._multi_tenant and self._tenant_id is not None:
            headers["X-Tenant-ID"] = self._tenant_id

        return headers

    async def refresh(self) -> None:
        """Refresh the OAuth2 token via the token endpoint.

        Uses the client_credentials grant type. If multi_tenant is enabled,
        extracts the tenant_id from the JWT claims.

        Raises:
            AuthenticationError: If the token endpoint rejects the request
                or the maximum refresh attempts are exceeded.
        """
        self._refresh_attempt_count += 1

        if self._refresh_attempt_count > self._max_refresh_attempts:
            self._reset_token_state()
            raise AuthenticationError(
                code="token_refresh_exhausted",
                message=(
                    f"OAuth2 token refresh failed after "
                    f"{self._max_refresh_attempts} attempt(s). "
                    f"No further refresh will be attempted until the next explicit call."
                ),
            )

        try:
            token_data = await self._request_token()
        except AuthenticationError:
            raise
        except Exception as exc:
            self._reset_token_state()
            raise AuthenticationError(
                code="token_refresh_failed",
                message="OAuth2 token refresh failed due to a network or server error.",
            ) from exc

        self._apply_token_data(token_data)
        # Reset counter on success
        self._refresh_attempt_count = 0

    def is_expired(self) -> bool:
        """Check if the current token is expired or about to expire.

        Uses a safety margin to trigger refresh slightly before actual expiry.

        Returns:
            True if the token is expired or within the safety margin.
        """
        if self._access_token is None:
            return True
        return time.time() >= (self._token_expiry - _EXPIRY_MARGIN_SECONDS)

    @property
    def tenant_id(self) -> str | None:
        """The tenant_id extracted from the last JWT, if multi_tenant is enabled."""
        return self._tenant_id

    @property
    def max_refresh_attempts(self) -> int:
        """Maximum number of token refresh attempts allowed."""
        return self._max_refresh_attempts

    async def _request_token(self) -> dict[str, Any]:
        """Send a client_credentials token request to the token endpoint.

        Returns:
            Parsed JSON response from the token endpoint.

        Raises:
            AuthenticationError: If the token endpoint returns an error response.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )

        if response.status_code != 200:
            self._reset_token_state()
            raise AuthenticationError(
                code="token_request_rejected",
                message=(
                    "OAuth2 token request was rejected by the token endpoint "
                    f"(HTTP {response.status_code})."
                ),
            )

        data: dict[str, Any] = response.json()
        if "access_token" not in data:
            self._reset_token_state()
            raise AuthenticationError(
                code="token_response_invalid",
                message="OAuth2 token response is missing the access_token field.",
            )

        return data

    def _apply_token_data(self, token_data: dict[str, Any]) -> None:
        """Extract and store token information from the token endpoint response.

        Args:
            token_data: Parsed JSON from the token endpoint response.
        """
        self._access_token = token_data["access_token"]

        # Calculate expiry time
        expires_in = token_data.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            self._token_expiry = time.time() + float(expires_in)
        else:
            # If no explicit expiry, try to decode from the JWT itself
            self._token_expiry = self._extract_expiry_from_jwt(self._access_token)

        # Extract tenant_id from JWT if multi-tenant mode is active
        if self._multi_tenant:
            self._tenant_id = self._extract_tenant_id_from_jwt(self._access_token)

    def _extract_expiry_from_jwt(self, token: str) -> float:
        """Decode the JWT (without verification) to extract the exp claim.

        Args:
            token: The JWT access token string.

        Returns:
            The expiry timestamp. Falls back to current time + 3600s if
            the exp claim is not present.
        """
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["HS256", "RS256", "ES256"],
            )
            exp = payload.get("exp")
            if isinstance(exp, (int, float)):
                return float(exp)
        except (jwt.DecodeError, jwt.InvalidTokenError):
            pass
        # Default: assume 1 hour validity
        return time.time() + 3600.0

    def _extract_tenant_id_from_jwt(self, token: str) -> str | None:
        """Decode the JWT (without verification) to extract the tenant_id claim.

        Args:
            token: The JWT access token string.

        Returns:
            The tenant_id string if present in claims, otherwise None.
        """
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["HS256", "RS256", "ES256"],
            )
            tenant_id = payload.get("tenant_id")
            if isinstance(tenant_id, str) and tenant_id:
                return tenant_id
        except (jwt.DecodeError, jwt.InvalidTokenError):
            pass
        return None

    def _reset_token_state(self) -> None:
        """Clear all token state."""
        self._access_token = None
        self._token_expiry = 0.0
        self._tenant_id = None
