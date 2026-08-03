"""Abstract base class for SDK authentication strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuthenticatorBase(ABC):
    """Contract for authentication strategies.

    All authenticators must implement get_headers, refresh, and is_expired.
    The SDK selects the appropriate authenticator based on the credential
    type provided at initialization.
    """

    @abstractmethod
    async def get_headers(self) -> dict[str, str]:
        """Return authentication headers for the current request.

        Returns:
            Dictionary of HTTP headers to include in the request.
        """
        ...

    @abstractmethod
    async def refresh(self) -> None:
        """Refresh the authentication token.

        Raises:
            AuthenticationError: If the refresh operation fails.
        """
        ...

    @abstractmethod
    def is_expired(self) -> bool:
        """Check whether the current credentials/token are expired.

        Returns:
            True if the token is expired or about to expire, False otherwise.
        """
        ...
