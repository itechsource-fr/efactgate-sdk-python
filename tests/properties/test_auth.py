"""Property-based tests for SDK authentication.

Tests validate:
- Property 8: API Key → X-API-Key header, OAuth2 → Authorization: Bearer header
- Property 9: After 401, refresh() is called and headers are re-obtained
- Property 10: Credentials never appear in str(), repr(), or error messages
- Property 11: Multi-tenant JWT with tenant_id → X-Tenant-ID header injected
- Property 30: Invalid credentials raise AuthenticationError with type info (no secret exposed)

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 13.6
"""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from orwin_sdk.auth.api_key import ApiKeyAuthenticator
from orwin_sdk.auth.oauth2 import OAuth2Authenticator
from orwin_sdk.exceptions import AuthenticationError

# --- Strategies ---

# Non-empty API key strings — use a prefix to ensure uniqueness and avoid
# accidental substring matches in repr/error messages (e.g. hex addresses).
_CREDENTIAL_PREFIX = "CRED_"

api_key_st = st.text(
    min_size=8,
    max_size=128,
    alphabet=st.characters(whitelist_categories=("L", "N")),
).filter(lambda s: s.strip() != "").map(lambda s: f"{_CREDENTIAL_PREFIX}{s}")

# OAuth2 client credentials (non-empty, with unique prefix)
client_id_st = st.text(
    min_size=4,
    max_size=64,
    alphabet=st.characters(whitelist_categories=("L", "N")),
).filter(lambda s: s.strip() != "").map(lambda s: f"CID_{s}")

client_secret_st = st.text(
    min_size=8,
    max_size=128,
    alphabet=st.characters(whitelist_categories=("L", "N")),
).filter(lambda s: s.strip() != "").map(lambda s: f"SEC_{s}")

# Token endpoint URL strategy
token_endpoint_st = st.sampled_from([
    "https://auth.orwin.io/oauth/token",
    "https://id.example.com/token",
    "https://sso.test.local/connect/token",
])

# Tenant ID strategy (non-empty alphanumeric)
tenant_id_st = st.text(
    min_size=1,
    max_size=64,
    alphabet=st.characters(whitelist_categories=("L", "N")),
).filter(lambda s: s.strip() != "")


def _make_jwt(claims: dict) -> str:
    """Create a fake unsigned JWT (header.payload.signature) for testing."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"fake-sig").rstrip(b"=").decode()
    return f"{header}.{payload}.{signature}"


# --- Property 8: Sélection du mode d'authentification ---


class TestProperty8AuthModeSelection:
    """Property 8: Sélection du mode d'authentification.

    **Validates: Requirements 2.1**

    For any ApiKeyCredentials, headers include X-API-Key.
    For any OAuth2Credentials, headers include Authorization: Bearer.
    """

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(max_examples=100)
    @given(api_key=api_key_st)
    async def test_api_key_produces_x_api_key_header(self, api_key: str) -> None:
        """ApiKeyAuthenticator always returns X-API-Key header with the key value."""
        auth = ApiKeyAuthenticator(api_key=api_key)
        headers = await auth.get_headers()

        assert "X-API-Key" in headers
        assert headers["X-API-Key"] == api_key

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_oauth2_produces_bearer_header(
        self, client_id: str, client_secret: str, token_endpoint: str
    ) -> None:
        """OAuth2Authenticator always returns Authorization: Bearer header."""
        fake_token = _make_jwt({"sub": client_id, "exp": int(time.time()) + 3600})

        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        )

        # Mock the token request to return a valid token
        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "access_token": fake_token,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            headers = await auth.get_headers()

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Authorization"] == f"Bearer {fake_token}"


# --- Property 9: Rafraîchissement automatique du jeton sur 401 ---


class TestProperty9TokenRefreshOn401:
    """Property 9: Rafraîchissement automatique du jeton sur 401.

    **Validates: Requirements 2.2**

    After a 401, refresh() is called and headers are re-obtained with new token.
    """

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_refresh_obtains_new_token(
        self, client_id: str, client_secret: str, token_endpoint: str
    ) -> None:
        """After calling refresh(), get_headers returns a new Bearer token."""
        old_token = _make_jwt({"sub": client_id, "exp": int(time.time()) - 100})
        new_token = _make_jwt({"sub": client_id, "exp": int(time.time()) + 3600})

        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        )

        # Simulate initial expired token state
        auth._access_token = old_token
        auth._token_expiry = time.time() - 100

        # Mock refresh to return a new token
        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "access_token": new_token,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            # Token is expired, so get_headers triggers refresh
            headers = await auth.get_headers()

        assert headers["Authorization"] == f"Bearer {new_token}"
        # Confirm it was called (refresh triggered)
        mock_req.assert_called_once()

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_refresh_resets_attempt_counter_on_success(
        self, client_id: str, client_secret: str, token_endpoint: str
    ) -> None:
        """Successful refresh resets the attempt counter to 0."""
        token = _make_jwt({"sub": client_id, "exp": int(time.time()) + 3600})

        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        )

        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            await auth.refresh()

        assert auth._refresh_attempt_count == 0


# --- Property 10: Credentials jamais exposés dans les sorties ---


class TestProperty10CredentialsNeverExposed:
    """Property 10: Credentials jamais exposés dans les sorties.

    **Validates: Requirements 2.4, 13.6**

    For any credential value, it never appears in str(), repr(), or error messages.
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(api_key=api_key_st)
    def test_api_key_not_in_str_repr(self, api_key: str) -> None:
        """API key value never appears in str() or repr() of the authenticator."""
        auth = ApiKeyAuthenticator(api_key=api_key)
        str_repr = str(auth)
        repr_repr = repr(auth)

        # The raw API key value must not be exposed
        assert api_key not in str_repr
        assert api_key not in repr_repr

    @pytest.mark.property
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    def test_oauth2_secret_not_in_str_repr(
        self, client_id: str, client_secret: str, token_endpoint: str
    ) -> None:
        """OAuth2 client_secret never appears in str() or repr() of authenticator."""
        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        )
        str_repr = str(auth)
        repr_repr = repr(auth)

        assert client_secret not in str_repr
        assert client_secret not in repr_repr

    @pytest.mark.property
    @settings(max_examples=100)
    @given(api_key=api_key_st)
    def test_credentials_not_in_error_messages_api_key(self, api_key: str) -> None:
        """When ApiKeyAuthenticator is created, its key doesn't leak in any error."""
        auth = ApiKeyAuthenticator(api_key=api_key)
        # Try various string representations
        outputs = [str(auth), repr(auth)]
        try:
            # Attempt to serialize
            outputs.append(f"{auth}")
        except Exception as e:
            outputs.append(str(e))

        for output in outputs:
            assert api_key not in output

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_oauth2_error_does_not_expose_secret(
        self, client_secret: str, token_endpoint: str
    ) -> None:
        """AuthenticationError from OAuth2 never exposes client_secret in message."""
        auth = OAuth2Authenticator(
            client_id="CID_testclient",
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        )
        # Exhaust refresh attempts to trigger AuthenticationError
        auth._refresh_attempt_count = auth._max_refresh_attempts

        with pytest.raises(AuthenticationError) as exc_info:
            await auth.refresh()

        error_msg = str(exc_info.value)
        error_repr = repr(exc_info.value)
        assert client_secret not in error_msg
        assert client_secret not in error_repr


# --- Property 11: Injection tenant_id depuis JWT en mode multi-tenant ---


class TestProperty11TenantIdInjection:
    """Property 11: Injection tenant_id depuis JWT en mode multi-tenant.

    **Validates: Requirements 2.5**

    When multi-tenant mode is active and the JWT contains a tenant_id claim,
    the X-Tenant-ID header is injected.
    """

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
        tenant_id=tenant_id_st,
    )
    async def test_tenant_id_extracted_from_jwt(
        self,
        client_id: str,
        client_secret: str,
        token_endpoint: str,
        tenant_id: str,
    ) -> None:
        """Multi-tenant mode extracts tenant_id from JWT and includes X-Tenant-ID."""
        token_with_tenant = _make_jwt({
            "sub": client_id,
            "exp": int(time.time()) + 3600,
            "tenant_id": tenant_id,
        })

        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
            multi_tenant=True,
        )

        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "access_token": token_with_tenant,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            headers = await auth.get_headers()

        assert "X-Tenant-ID" in headers
        assert headers["X-Tenant-ID"] == tenant_id

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_no_tenant_id_when_not_multi_tenant(
        self,
        client_id: str,
        client_secret: str,
        token_endpoint: str,
    ) -> None:
        """Without multi-tenant mode, X-Tenant-ID is never present."""
        token = _make_jwt({
            "sub": client_id,
            "exp": int(time.time()) + 3600,
            "tenant_id": "should-not-appear",
        })

        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
            multi_tenant=False,
        )

        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            headers = await auth.get_headers()

        assert "X-Tenant-ID" not in headers

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_id=client_id_st,
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_no_tenant_id_header_when_jwt_lacks_claim(
        self,
        client_id: str,
        client_secret: str,
        token_endpoint: str,
    ) -> None:
        """Multi-tenant mode with JWT missing tenant_id claim → no X-Tenant-ID."""
        token_no_tenant = _make_jwt({
            "sub": client_id,
            "exp": int(time.time()) + 3600,
        })

        auth = OAuth2Authenticator(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
            multi_tenant=True,
        )

        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "access_token": token_no_tenant,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            headers = await auth.get_headers()

        assert "X-Tenant-ID" not in headers


# --- Property 30: Credentials invalides lèvent AuthenticationError typée ---


class TestProperty30InvalidCredentialsRaiseAuthError:
    """Property 30: Credentials invalides lèvent AuthenticationError typée.

    **Validates: Requirements 2.3**

    Empty/null API key raises AuthenticationError.
    Invalid OAuth2 credentials at token endpoint raise AuthenticationError
    with type info but without exposing the secret.
    """

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        empty_key=st.one_of(
            st.just(""),
            st.text(
                min_size=1, max_size=20,
                alphabet=st.just(" "),
            ),
        ),
    )
    def test_empty_api_key_raises_authentication_error(self, empty_key: str) -> None:
        """Empty or whitespace-only API key raises AuthenticationError."""
        with pytest.raises(AuthenticationError) as exc_info:
            ApiKeyAuthenticator(api_key=empty_key)

        assert exc_info.value.code == "invalid_api_key"
        assert "api key" in exc_info.value.message.lower()

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        empty_id=st.one_of(
            st.just(""),
            st.text(min_size=1, max_size=10, alphabet=st.just(" ")),
        ),
    )
    def test_empty_client_id_raises_authentication_error(self, empty_id: str) -> None:
        """Empty or whitespace-only client_id raises AuthenticationError."""
        with pytest.raises(AuthenticationError) as exc_info:
            OAuth2Authenticator(
                client_id=empty_id,
                client_secret="valid-secret",
                token_endpoint="https://auth.orwin.io/token",
            )

        assert exc_info.value.code == "invalid_credentials"

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        empty_secret=st.one_of(
            st.just(""),
            st.text(min_size=1, max_size=10, alphabet=st.just(" ")),
        ),
    )
    def test_empty_client_secret_raises_authentication_error(
        self, empty_secret: str
    ) -> None:
        """Empty or whitespace-only client_secret raises AuthenticationError."""
        with pytest.raises(AuthenticationError) as exc_info:
            OAuth2Authenticator(
                client_id="valid-id",
                client_secret=empty_secret,
                token_endpoint="https://auth.orwin.io/token",
            )

        assert exc_info.value.code == "invalid_credentials"

    @pytest.mark.property
    @pytest.mark.anyio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        client_secret=client_secret_st,
        token_endpoint=token_endpoint_st,
    )
    async def test_token_endpoint_rejection_raises_auth_error_without_secret(
        self, client_secret: str, token_endpoint: str
    ) -> None:
        """Token endpoint rejection raises AuthenticationError without exposing secret."""
        auth = OAuth2Authenticator(
            client_id="CID_testclient",
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        )

        # Mock a rejected token request (HTTP 401)
        with patch.object(auth, "_request_token", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = AuthenticationError(
                code="token_request_rejected",
                message="OAuth2 token request was rejected by the token endpoint (HTTP 401).",
            )
            with pytest.raises(AuthenticationError) as exc_info:
                await auth.refresh()

        # The secret must not appear in the error
        assert client_secret not in str(exc_info.value)
        assert client_secret not in repr(exc_info.value)
        assert exc_info.value.code == "token_request_rejected"
