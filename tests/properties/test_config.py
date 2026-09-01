"""Property-based tests for ClientConfig and environment variable loading.

Tests validate:
- Property 14: Bounds validation for timeout and max_retries
- Property 15: Explicit parameters take priority over environment variables
- Property 16: Sandbox mode always forces sandbox URL

Validates: Requirements 11.2, 11.3, 11.4, 11.7
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from efactgate_sdk.config import (
    SANDBOX_URL,
    ApiKeyCredentials,
    ClientConfig,
    OAuth2Credentials,
    load_config,
)
from efactgate_sdk.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Generator

# --- Helpers ---

_ENV_KEYS = (
    "EFACTGATE_API_URL",
    "EFACTGATE_API_KEY",
    "EFACTGATE_OAUTH_CLIENT_ID",
    "EFACTGATE_OAUTH_CLIENT_SECRET",
)


@contextmanager
def patched_env(env_vars: dict[str, str]) -> Generator[None, None, None]:
    """Context manager that temporarily sets env vars and restores originals."""
    originals: dict[str, str | None] = {}
    for key in _ENV_KEYS:
        originals[key] = os.environ.get(key)
        # Clear all env keys first to avoid cross-contamination
        os.environ.pop(key, None)
    for key, value in env_vars.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
            if originals[key] is not None:
                os.environ[key] = originals[key]


# --- Strategies ---

# Valid timeout: float within [1.0, 300.0]
valid_timeout_st = st.floats(
    min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False
)

# Invalid timeout: float outside [1.0, 300.0]
invalid_timeout_st = st.one_of(
    st.floats(max_value=0.99, allow_nan=False, allow_infinity=False),
    st.floats(min_value=300.01, max_value=1e6, allow_nan=False, allow_infinity=False),
)

# Valid max_retries: int within [0, 10]
valid_max_retries_st = st.integers(min_value=0, max_value=10)

# Invalid max_retries: int outside [0, 10]
invalid_max_retries_st = st.one_of(
    st.integers(min_value=-100, max_value=-1),
    st.integers(min_value=11, max_value=100),
)

# Valid URL strategy
valid_url_st = st.sampled_from([
    "https://api.efactgate.fr/v1",
    "https://api.efactgate.fr/api/v1",
    "http://localhost:8000",
    "https://staging.example.com/api",
])

# Valid API key strategy (non-empty strings)
api_key_st = st.text(
    min_size=1, max_size=64,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)

# Env var value strategy for URL
env_url_st = st.sampled_from([
    "https://env-api.efactgate.fr/v1",
    "https://env-staging.efactgate.fr/v1",
    "http://env-localhost:9000",
])

# Env var value strategy for API key
env_api_key_st = st.text(
    min_size=1, max_size=64,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)


# --- Property 14: Validation des bornes de configuration ---


class TestProperty14BoundsValidation:
    """Property 14: Validation des bornes de configuration.

    **Validates: Requirements 11.2, 11.7**

    For any timeout in [1, 300] and max_retries in [0, 10], init succeeds.
    For values outside bounds, ConfigurationError is raised.
    """

    @pytest.mark.property
    @settings(max_examples=150)
    @given(timeout=valid_timeout_st, max_retries=valid_max_retries_st)
    def test_valid_bounds_succeed(self, timeout: float, max_retries: int) -> None:
        """Valid timeout and max_retries values produce a successful config."""
        config = load_config(
            base_url="https://api.efactgate.fr/v1",
            api_key="test-key-123",
            timeout=timeout,
            max_retries=max_retries,
        )
        assert isinstance(config, ClientConfig)
        assert config.timeout == timeout
        assert config.max_retries == max_retries

    @pytest.mark.property
    @settings(max_examples=150)
    @given(timeout=invalid_timeout_st)
    def test_invalid_timeout_raises(self, timeout: float) -> None:
        """Timeout outside [1, 300] raises ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(
                base_url="https://api.efactgate.fr/v1",
                api_key="test-key-123",
                timeout=timeout,
            )
        assert "timeout" in exc_info.value.message.lower()
        assert "bounds" in exc_info.value.message.lower()

    @pytest.mark.property
    @settings(max_examples=150)
    @given(max_retries=invalid_max_retries_st)
    def test_invalid_max_retries_raises(self, max_retries: int) -> None:
        """max_retries outside [0, 10] raises ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(
                base_url="https://api.efactgate.fr/v1",
                api_key="test-key-123",
                max_retries=max_retries,
            )
        assert "max_retries" in exc_info.value.message.lower()
        assert "bounds" in exc_info.value.message.lower()


# --- Property 15: Priorité des paramètres sur les variables d'environnement ---


class TestProperty15ParameterPriority:
    """Property 15: Priorité des paramètres sur les variables d'environnement.

    **Validates: Requirements 11.4**

    Explicit params always take priority over env vars.
    """

    @pytest.mark.property
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        explicit_url=valid_url_st,
        env_url=env_url_st,
    )
    def test_explicit_url_overrides_env(
        self, explicit_url: str, env_url: str
    ) -> None:
        """Explicit base_url always takes priority over EFACTGATE_API_URL env var."""
        with patched_env({"EFACTGATE_API_URL": env_url, "EFACTGATE_API_KEY": "env-key"}):
            config = load_config(
                base_url=explicit_url,
                api_key="test-key",
            )
            assert config.base_url == explicit_url

    @pytest.mark.property
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        explicit_key=api_key_st,
        env_key=env_api_key_st,
    )
    def test_explicit_api_key_overrides_env(
        self, explicit_key: str, env_key: str
    ) -> None:
        """Explicit api_key always takes priority over EFACTGATE_API_KEY env var."""
        with patched_env({"EFACTGATE_API_KEY": env_key}):
            config = load_config(
                base_url="https://api.efactgate.fr/v1",
                api_key=explicit_key,
            )
            assert isinstance(config.credentials, ApiKeyCredentials)
            assert config.credentials.api_key == explicit_key

    @pytest.mark.property
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(env_url=env_url_st)
    def test_env_var_used_when_no_explicit_param(self, env_url: str) -> None:
        """Env vars are used as fallback when explicit params are not provided."""
        with patched_env({"EFACTGATE_API_URL": env_url, "EFACTGATE_API_KEY": "env-key-123"}):
            config = load_config()
            assert config.base_url == env_url
            assert isinstance(config.credentials, ApiKeyCredentials)
            assert config.credentials.api_key == "env-key-123"

    @pytest.mark.property
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        explicit_client_id=st.text(
            min_size=1, max_size=32,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
        env_client_id=st.text(
            min_size=1, max_size=32,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
    )
    def test_explicit_oauth_overrides_env(
        self,
        explicit_client_id: str,
        env_client_id: str,
    ) -> None:
        """Explicit OAuth2 params take priority over env vars."""
        with patched_env({
            "EFACTGATE_OAUTH_CLIENT_ID": env_client_id,
            "EFACTGATE_OAUTH_CLIENT_SECRET": "env-secret",
        }):
            config = load_config(
                base_url="https://api.efactgate.fr/v1",
                oauth_client_id=explicit_client_id,
                oauth_client_secret="explicit-secret",
                oauth_token_endpoint="https://auth.efactgate.fr/token",
            )
            assert isinstance(config.credentials, OAuth2Credentials)
            assert config.credentials.client_id == explicit_client_id
            assert config.credentials.client_secret == "explicit-secret"


# --- Property 16: Isolation sandbox ---


class TestProperty16SandboxIsolation:
    """Property 16: Isolation sandbox.

    **Validates: Requirements 11.3**

    When sandbox=True, the URL is always the sandbox URL.
    """

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        any_url=valid_url_st,
        api_key=api_key_st,
    )
    def test_sandbox_always_uses_sandbox_url(
        self, any_url: str, api_key: str
    ) -> None:
        """When sandbox=True, base_url is always SANDBOX_URL regardless of input."""
        config = load_config(
            base_url=any_url,
            api_key=api_key,
            sandbox=True,
        )
        assert config.base_url == SANDBOX_URL
        assert config.sandbox is True

    @pytest.mark.property
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(api_key=api_key_st)
    def test_sandbox_ignores_env_url(self, api_key: str) -> None:
        """When sandbox=True, EFACTGATE_API_URL env var is ignored; sandbox URL is used."""
        with patched_env({"EFACTGATE_API_URL": "https://production.efactgate.fr/api/v1"}):
            config = load_config(
                api_key=api_key,
                sandbox=True,
            )
            assert config.base_url == SANDBOX_URL
            assert config.base_url != "https://production.efactgate.fr/api/v1"

    @pytest.mark.property
    @settings(max_examples=150)
    @given(api_key=api_key_st)
    def test_sandbox_no_explicit_url_still_works(self, api_key: str) -> None:
        """Sandbox mode does not require explicit base_url — sandbox URL is used."""
        with patched_env({}):
            config = load_config(
                api_key=api_key,
                sandbox=True,
            )
            assert config.base_url == SANDBOX_URL
