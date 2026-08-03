"""Client configuration and environment variable loading.

Provides ClientConfig (frozen dataclass) with support for explicit parameters
and environment variable fallback. Validates bounds and URL format, and supports
sandbox mode isolation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from efactgate_sdk.exceptions import ConfigurationError

# Sandbox URL constant — when sandbox=True, all requests target this URL exclusively.
SANDBOX_URL: str = "https://sandbox.gw-efactures.efactgate.io/api/v1"

# Validation bounds
MIN_TIMEOUT: float = 1.0
MAX_TIMEOUT: float = 300.0
MIN_RETRIES: int = 0
MAX_RETRIES: int = 10

# URL pattern: must start with http:// or https://
_URL_PATTERN: re.Pattern[str] = re.compile(r"^https?://[^\s/$.?#].\S*$", re.IGNORECASE)


@dataclass(frozen=True)
class ApiKeyCredentials:
    """API Key based authentication credentials.

    Attributes:
        api_key: The API key string used for X-API-Key header authentication.
    """

    api_key: str


@dataclass(frozen=True)
class OAuth2Credentials:
    """OAuth2 client_credentials authentication credentials.

    Attributes:
        client_id: OAuth2 client identifier.
        client_secret: OAuth2 client secret.
        token_endpoint: URL of the token endpoint for obtaining tokens.
        max_refresh_attempts: Maximum number of token refresh attempts (bounds: [1, 10]).
    """

    client_id: str
    client_secret: str
    token_endpoint: str
    max_refresh_attempts: int = 3


@dataclass(frozen=True)
class ClientConfig:
    """Immutable SDK client configuration.

    Supports initialization via explicit parameters or environment variables.
    Explicit parameters always take priority over environment variables.

    Attributes:
        base_url: Base URL of the GW-eFactures API.
        credentials: Authentication credentials (ApiKey or OAuth2).
        timeout: Request timeout in seconds (bounds: [1.0, 300.0]).
        max_retries: Maximum retry attempts (bounds: [0, 10]).
        sandbox: When True, forces all requests to the sandbox URL.
        log_level: Logging level (default: WARNING).
        retry_delays: Tuple of retry delay durations in seconds.
    """

    base_url: str
    credentials: ApiKeyCredentials | OAuth2Credentials
    timeout: float = 30.0
    max_retries: int = 5
    sandbox: bool = False
    log_level: str = "WARNING"
    retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)


def _validate_url(url: str) -> None:
    """Validate that a URL has a valid http/https scheme format.

    Raises:
        ConfigurationError: If the URL format is invalid.
    """
    if not _URL_PATTERN.match(url):
        raise ConfigurationError(
            code="invalid_url",
            message=(
                f"Invalid base URL format: {url!r}. "
                "URL must start with 'http://' or 'https://' and be well-formed."
            ),
        )


def _validate_timeout(timeout: float) -> None:
    """Validate timeout is within accepted bounds [1.0, 300.0].

    Raises:
        ConfigurationError: If timeout is outside bounds.
    """
    if timeout < MIN_TIMEOUT or timeout > MAX_TIMEOUT:
        raise ConfigurationError(
            code="timeout_out_of_bounds",
            message=(
                f"Timeout value {timeout} is out of accepted bounds. "
                f"Must be between {MIN_TIMEOUT} and {MAX_TIMEOUT} seconds."
            ),
        )


def _validate_max_retries(max_retries: int) -> None:
    """Validate max_retries is within accepted bounds [0, 10].

    Raises:
        ConfigurationError: If max_retries is outside bounds.
    """
    if max_retries < MIN_RETRIES or max_retries > MAX_RETRIES:
        raise ConfigurationError(
            code="max_retries_out_of_bounds",
            message=(
                f"max_retries value {max_retries} is out of accepted bounds. "
                f"Must be between {MIN_RETRIES} and {MAX_RETRIES}."
            ),
        )


def load_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    oauth_client_id: str | None = None,
    oauth_client_secret: str | None = None,
    oauth_token_endpoint: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    sandbox: bool = False,
    log_level: str = "WARNING",
    retry_delays: tuple[float, ...] | None = None,
) -> ClientConfig:
    """Load and validate SDK configuration from explicit params and/or env vars.

    Priority: explicit parameters > environment variables.

    Environment variables supported:
        - EFACTGATE_API_URL: Base URL of the API
        - EFACTGATE_API_KEY: API key for authentication
        - EFACTGATE_OAUTH_CLIENT_ID: OAuth2 client ID
        - EFACTGATE_OAUTH_CLIENT_SECRET: OAuth2 client secret

    Args:
        base_url: API base URL (falls back to EFACTGATE_API_URL env var).
        api_key: API key (falls back to EFACTGATE_API_KEY env var).
        oauth_client_id: OAuth2 client ID (falls back to EFACTGATE_OAUTH_CLIENT_ID).
        oauth_client_secret: OAuth2 client secret (falls back to EFACTGATE_OAUTH_CLIENT_SECRET).
        oauth_token_endpoint: OAuth2 token endpoint URL.
        timeout: Request timeout in seconds (default: 30.0).
        max_retries: Maximum retry count (default: 5).
        sandbox: Enable sandbox mode (forces sandbox URL).
        log_level: Logging level string.
        retry_delays: Custom retry delay tuple.

    Returns:
        A validated, immutable ClientConfig instance.

    Raises:
        ConfigurationError: If required params are missing, bounds exceeded, or URL invalid.
    """
    # Resolve base_url: explicit > env var
    resolved_url = base_url if base_url is not None else os.environ.get("EFACTGATE_API_URL")

    # Resolve credentials: explicit > env var
    resolved_api_key = api_key if api_key is not None else os.environ.get("EFACTGATE_API_KEY")
    resolved_client_id = (
        oauth_client_id
        if oauth_client_id is not None
        else os.environ.get("EFACTGATE_OAUTH_CLIENT_ID")
    )
    resolved_client_secret = (
        oauth_client_secret
        if oauth_client_secret is not None
        else os.environ.get("EFACTGATE_OAUTH_CLIENT_SECRET")
    )

    # Resolve timeout and max_retries with defaults
    resolved_timeout = timeout if timeout is not None else 30.0
    resolved_max_retries = max_retries if max_retries is not None else 5

    # Validate bounds
    _validate_timeout(resolved_timeout)
    _validate_max_retries(resolved_max_retries)

    # Sandbox mode: override URL to sandbox
    if sandbox:
        resolved_url = SANDBOX_URL
    elif resolved_url is None:
        raise ConfigurationError(
            code="missing_base_url",
            message=(
                "Missing required parameter: base_url. "
                "Provide it explicitly or set the EFACTGATE_API_URL environment variable."
            ),
        )
    else:
        _validate_url(resolved_url)

    # Build credentials
    credentials: ApiKeyCredentials | OAuth2Credentials
    if resolved_api_key is not None:
        credentials = ApiKeyCredentials(api_key=resolved_api_key)
    elif resolved_client_id is not None and resolved_client_secret is not None:
        if oauth_token_endpoint is None:
            raise ConfigurationError(
                code="missing_token_endpoint",
                message=(
                    "OAuth2 credentials require a token_endpoint. "
                    "Provide oauth_token_endpoint parameter."
                ),
            )
        credentials = OAuth2Credentials(
            client_id=resolved_client_id,
            client_secret=resolved_client_secret,
            token_endpoint=oauth_token_endpoint,
        )
    else:
        raise ConfigurationError(
            code="missing_credentials",
            message=(
                "Missing required credentials. Provide either api_key (or EFACTGATE_API_KEY env var) "
                "or both oauth_client_id and oauth_client_secret "
                "(or EFACTGATE_OAUTH_CLIENT_ID and EFACTGATE_OAUTH_CLIENT_SECRET env vars)."
            ),
        )

    # Build the config
    final_retry_delays = retry_delays if retry_delays is not None else (1.0, 2.0, 4.0, 8.0, 16.0)

    return ClientConfig(
        base_url=resolved_url,
        credentials=credentials,
        timeout=resolved_timeout,
        max_retries=resolved_max_retries,
        sandbox=sandbox,
        log_level=log_level,
        retry_delays=final_retry_delays,
    )


__all__ = [
    "SANDBOX_URL",
    "ApiKeyCredentials",
    "ClientConfig",
    "OAuth2Credentials",
    "load_config",
]
