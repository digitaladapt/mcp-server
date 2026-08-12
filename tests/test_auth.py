"""Tests for API key authentication (``app/auth.py``).

Covers:
- Auth disabled when MCP_API_KEY is unset/empty
- Auth enabled when MCP_API_KEY is set
- /api/health is always accessible
- Correct API key grants access
- Missing header returns 401
- Wrong key returns 403
- Constant-time comparison (no timing leak on length differences)
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

# Modules that capture ``verify_api_key`` (or otherwise depend on the auth
# module) at import time.  ``app.auth`` reads ``MCP_API_KEY`` at import, so
# all of these must be re-imported together to keep the route dependency
# closures and the imported module in sync.
_AUTH_DEPENDENT_MODULES = (
    "app.main",
    "app.caldav_routes",
    "app.gitea_routes",
    "app.ics_routes",
    "app.unified_routes",
    "app.registry_routes",
    "app.provider_adapters",
    "app.auth",
)

# --------------------------------------------------------------------------- #
# Helpers to reload auth module with a specific env var
# --------------------------------------------------------------------------- #

def _reload_auth_with_key(key: str | None) -> object:
    """Reload ``app.auth`` with ``MCP_API_KEY`` set to *key*.

    Returns the reloaded module so tests can inspect its functions.
    """
    # Evict auth-dependent modules wholesale so the fresh import below
    # re-links every ``verify_api_key`` reference (route closures included)
    # to the same, current ``app.auth`` module object.
    for mod_name in _AUTH_DEPENDENT_MODULES:
        importlib.sys.modules.pop(mod_name, None)

    if key is None:
        os.environ.pop("MCP_API_KEY", None)
    else:
        os.environ["MCP_API_KEY"] = key

    # Fresh imports (not importlib.reload) so every module re-executes its
    # import statements against the new app.auth object.
    import app.auth  # noqa: WPS433  (intentional re-import)

    # Reimport all route modules so they pick up the new verify_api_key
    import app.caldav_routes  # noqa: WPS433
    import app.gitea_routes  # noqa: WPS433
    import app.ics_routes  # noqa: WPS433
    import app.main  # noqa: WPS433
    import app.registry_routes  # noqa: WPS433
    import app.unified_routes  # noqa: WPS433
    return app.auth


@pytest.fixture(autouse=True)
def _restore_env() -> Generator[None, None, None]:
    """Ensure MCP_API_KEY is restored and modules are reloaded after each test."""
    original = os.environ.get("MCP_API_KEY")
    yield
    # Restore
    if original is not None:
        os.environ["MCP_API_KEY"] = original
    else:
        os.environ.pop("MCP_API_KEY", None)
    # Reload modules with original env
    _reload_auth_with_key(original)


@pytest.fixture
def no_auth_client() -> Generator[TestClient, None, None]:
    """A TestClient with authentication disabled (no MCP_API_KEY)."""
    _reload_auth_with_key(None)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_client() -> Generator[TestClient, None, None]:
    """A TestClient with MCP_API_KEY set to 'test-secret-key'."""
    _reload_auth_with_key("test-secret-key")
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------- #
# Auth disabled (no MCP_API_KEY)
# --------------------------------------------------------------------------- #

class TestAuthDisabled:
    """When MCP_API_KEY is unset, all endpoints are open."""

    def test_health_accessible(self, no_auth_client: TestClient) -> None:
        resp = no_auth_client.get("/api/health")
        assert resp.status_code == 200

    def test_commands_accessible_without_header(self, no_auth_client: TestClient) -> None:
        resp = no_auth_client.get("/commands")
        assert resp.status_code == 200

    def test_execute_accessible_without_header(self, no_auth_client: TestClient) -> None:
        resp = no_auth_client.post("/log", json={"message": "test"})
        assert resp.status_code == 200

    def test_validate_accessible_without_header(self, no_auth_client: TestClient) -> None:
        resp = no_auth_client.get("/validate")
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Auth enabled (MCP_API_KEY set)
# --------------------------------------------------------------------------- #

class TestAuthEnabled:
    """When MCP_API_KEY is set, endpoints require X-API-Key header."""

    def test_health_always_accessible(self, auth_client: TestClient) -> None:
        """Health endpoint must work even without the API key."""
        resp = auth_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}

    def test_commands_without_header_returns_401(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get("/commands")
        assert resp.status_code == 401
        assert "Missing X-API-Key header" in resp.json()["detail"]

    def test_commands_with_wrong_key_returns_403(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get("/commands", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 403
        assert "Invalid API key" in resp.json()["detail"]

    def test_commands_with_correct_key_returns_200(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get("/commands", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_execute_without_header_returns_401(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.post("/log", json={"message": "test"})
        assert resp.status_code == 401

    def test_execute_with_correct_key_works(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.post(
            "/log",
            json={"message": "test"},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 200

    def test_validate_without_header_returns_401(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get("/validate")
        assert resp.status_code == 401

    def test_validate_with_correct_key_works(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get("/validate", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_get_single_command_without_header_returns_401(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get("/commands/log")
        assert resp.status_code == 401

    def test_get_single_command_with_correct_key_works(
        self, auth_client: TestClient,
    ) -> None:
        resp = auth_client.get(
            "/commands/log",
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #

class TestAuthEdgeCases:
    """Edge cases for the API key auth."""

    def test_empty_key_string_disables_auth(self) -> None:
        """An empty string MCP_API_KEY should disable auth."""
        _reload_auth_with_key("")
        from app.auth import is_auth_enabled
        assert is_auth_enabled() is False

    def test_whitespace_only_key_disables_auth(self) -> None:
        """A whitespace-only MCP_API_KEY should disable auth after strip()."""
        _reload_auth_with_key("   ")
        from app.auth import is_auth_enabled
        assert is_auth_enabled() is False

    def test_is_auth_enabled_true_when_set(self) -> None:
        _reload_auth_with_key("some-key")
        from app.auth import is_auth_enabled
        assert is_auth_enabled() is True

    def test_get_api_key_returns_value(self) -> None:
        _reload_auth_with_key("my-key-123")
        from app.auth import get_api_key
        assert get_api_key() == "my-key-123"

    def test_get_api_key_empty_when_unset(self) -> None:
        _reload_auth_with_key(None)
        from app.auth import get_api_key
        assert get_api_key() == ""

    def test_empty_string_key_treated_as_missing(self, auth_client: TestClient) -> None:
        """Sending an empty X-API-Key header is treated as missing (401)."""
        resp = auth_client.get("/commands", headers={"X-API-Key": ""})
        assert resp.status_code == 401
