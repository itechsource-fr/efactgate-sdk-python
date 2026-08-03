"""API Key authenticator — static X-API-Key header."""

from __future__ import annotations

from orwin_sdk.auth.base import AuthenticatorBase
from orwin_sdk.exceptions import AuthenticationError


class ApiKeyAuthenticator(AuthenticatorBase):
    """Authenticator using a static API Key in the X-API-Key header.

    The API key is validated at construction time. It never expires and
    refresh is a no-op.

    Args:
        api_key: The API key value. Must be a non-empty string.

    Raises:
        AuthenticationError: If the api_key is empty or None.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.strip():
            raise AuthenticationError(
                code="invalid_api_key",
                message="API key must be a non-empty string.",
            )
        self._api_key = api_key

    async def get_headers(self) -> dict[str, str]:
        """Return the X-API-Key header.

        Returns:
            Dictionary with the X-API-Key header.
        """
        return {"X-API-Key": self._api_key}

    async def refresh(self) -> None:
        """No-op — API keys do not require refresh."""

    def is_expired(self) -> bool:
        """API keys never expire.

        Returns:
            Always False.
        """
        return False
