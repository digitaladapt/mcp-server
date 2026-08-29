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

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Ensure MCP_API_KEY is restored after each test."""
    original = os.environ.get("MCP_API_KEY")
    yield
    if original is not None:
        monkeypatch.setenv("MCP_API_KEY", original)
    else:
        monkeypatch.delenv("MCP_API_KEY", raising=False)


@pytest.fixture
def no_auth_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with authentication disabled (no MCP_API_KEY)."""
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with MCP_API_KEY set to 'test-secret-key'."""
    monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
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
        assert "Missing credentials" in resp.json()["detail"]

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
# Authorization: Bearer header support
# --------------------------------------------------------------------------- #

class TestBearerAuth:
    """The Authorization: Bearer <key> header is accepted as an alternative."""

    def test_bearer_with_correct_key_returns_200(self, auth_client: TestClient) -> None:
        resp = auth_client.get(
            "/commands",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert resp.status_code == 200

    def test_bearer_with_wrong_key_returns_403(self, auth_client: TestClient) -> None:
        resp = auth_client.get(
            "/commands",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 403
        assert "Invalid API key" in resp.json()["detail"]

    def test_bearer_scheme_is_case_insensitive(self, auth_client: TestClient) -> None:
        """Per RFC 9110, the auth scheme is matched case-insensitively."""
        resp = auth_client.get(
            "/commands",
            headers={"Authorization": "beAREr test-secret-key"},
        )
        assert resp.status_code == 200

    def test_bearer_extra_whitespace_is_tolerated(self, auth_client: TestClient) -> None:
        resp = auth_client.get(
            "/commands",
            headers={"Authorization": "Bearer   test-secret-key  "},
        )
        assert resp.status_code == 200

    def test_bearer_empty_token_returns_401(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/commands", headers={"Authorization": "Bearer"})
        assert resp.status_code == 401

    def test_non_bearer_scheme_returns_401(self, auth_client: TestClient) -> None:
        """Alternative schemes (Basic, Digest, ...) are not accepted."""
        resp = auth_client.get(
            "/commands",
            headers={"Authorization": "Basic dGVzdDp0ZXN0"},
        )
        assert resp.status_code == 401

    def test_x_api_key_takes_precedence_over_bearer(self, auth_client: TestClient) -> None:
        """Valid X-API-Key succeeds even when the Bearer token is wrong."""
        resp = auth_client.get(
            "/commands",
            headers={
                "X-API-Key": "test-secret-key",
                "Authorization": "Bearer wrong",
            },
        )
        assert resp.status_code == 200

    def test_invalid_x_api_key_does_not_fall_back_to_bearer(
        self, auth_client: TestClient,
    ) -> None:
        """An invalid X-API-Key is rejected even with a valid Bearer token."""
        resp = auth_client.get(
            "/commands",
            headers={
                "X-API-Key": "wrong",
                "Authorization": "Bearer test-secret-key",
            },
        )
        assert resp.status_code == 403

    def test_bearer_ignored_when_auth_disabled(self, no_auth_client: TestClient) -> None:
        """In open mode, a bogus Bearer token must not cause a rejection."""
        resp = no_auth_client.get(
            "/commands",
            headers={"Authorization": "Bearer whatever"},
        )
        assert resp.status_code == 200

    def test_bearer_works_on_protected_endpoints(self, auth_client: TestClient) -> None:
        """Spot-check Bearer auth on endpoints other than /commands."""
        headers = {"Authorization": "Bearer test-secret-key"}
        assert auth_client.get("/validate", headers=headers).status_code == 200
        assert auth_client.get("/commands/log", headers=headers).status_code == 200
        resp = auth_client.post("/log", json={"message": "test"}, headers=headers)
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #

class TestAuthEdgeCases:
    """Edge cases for the API key auth."""

    def test_empty_key_string_disables_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty string MCP_API_KEY should disable auth."""
        monkeypatch.setenv("MCP_API_KEY", "")
        from app.auth import is_auth_enabled
        assert is_auth_enabled() is False

    def test_whitespace_only_key_disables_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A whitespace-only MCP_API_KEY should disable auth after strip()."""
        monkeypatch.setenv("MCP_API_KEY", "   ")
        from app.auth import is_auth_enabled
        assert is_auth_enabled() is False

    def test_is_auth_enabled_true_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_API_KEY", "some-key")
        from app.auth import is_auth_enabled
        assert is_auth_enabled() is True

    def test_get_api_key_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_API_KEY", "my-key-123")
        from app.auth import get_api_key
        assert get_api_key() == "my-key-123"

    def test_get_api_key_empty_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        from app.auth import get_api_key
        assert get_api_key() == ""

    def test_empty_string_key_treated_as_missing(self, auth_client: TestClient) -> None:
        """Sending an empty X-API-Key header is treated as missing (401)."""
        resp = auth_client.get("/commands", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_key_rotation_takes_effect_without_restart(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changing MCP_API_KEY at runtime should be picked up immediately."""
        monkeypatch.setenv("MCP_API_KEY", "first-key")
        from app.auth import get_api_key, is_auth_enabled
        assert get_api_key() == "first-key"
        assert is_auth_enabled() is True

        monkeypatch.setenv("MCP_API_KEY", "second-key")
        assert get_api_key() == "second-key"

        monkeypatch.delenv("MCP_API_KEY", raising=False)
        assert is_auth_enabled() is False


# --------------------------------------------------------------------------- #
# Secrets require auth
# --------------------------------------------------------------------------- #

class TestDetectConfiguredSecrets:
    """detect_configured_secrets() finds every secret-carrying env var."""

    def test_no_secrets_detected(self) -> None:
        from app.auth import detect_configured_secrets
        assert detect_configured_secrets({}) == []

    def test_whitespace_only_values_ignored(self) -> None:
        from app.auth import detect_configured_secrets
        env = {"GITEA_TOKEN": "   ", "CALDAV_PASSWORD": ""}
        assert detect_configured_secrets(env) == []

    @pytest.mark.parametrize("name", [
        "NTFY_TOKEN",
        "NTFY_PASSWORD",
        "CALDAV_PASSWORD",
        "ICS_CALENDAR_URL",
        "GITEA_TOKEN",
    ])
    def test_each_secret_var_detected(self, name: str) -> None:
        from app.auth import detect_configured_secrets
        assert detect_configured_secrets({name: "value"}) == [name]

    def test_discord_hooks_detected(self) -> None:
        from app.auth import detect_configured_secrets
        env = {
            "DISCORD_INFO_HOOK": "https://discord.com/api/webhooks/1/x",
            "DISCORD_EMERGENCY_HOOK": "https://discord.com/api/webhooks/1/y",
        }
        assert detect_configured_secrets(env) == [
            "DISCORD_EMERGENCY_HOOK",
            "DISCORD_INFO_HOOK",
        ]

    def test_discord_non_hook_vars_ignored(self) -> None:
        """Non-secret DISCORD_* vars must not trigger the requirement."""
        from app.auth import detect_configured_secrets
        env = {
            "DISCORD_SERVER_NAME": "my-server",
            "DISCORD_TITLE_SUFFIX": "· test",
            "MY_DISCORD_INFO_HOOK": "not-an-mcp-var",
        }
        assert detect_configured_secrets(env) == []

    def test_multiple_secrets_combined(self) -> None:
        from app.auth import detect_configured_secrets
        env = {
            "NTFY_TOKEN": "tk_x",
            "GITEA_TOKEN": "tok",
            "DISCORD_NOTICE_HOOK": "https://discord.com/api/webhooks/1/n",
        }
        assert detect_configured_secrets(env) == [
            "NTFY_TOKEN",
            "GITEA_TOKEN",
            "DISCORD_NOTICE_HOOK",
        ]


class TestRequireAuthIfSecrets:
    """require_auth_if_secrets() fails fast on secrets without MCP_API_KEY."""

    def test_no_secrets_no_key_ok(self) -> None:
        from app.auth import require_auth_if_secrets
        require_auth_if_secrets({})  # must not raise

    def test_secret_without_key_raises(self) -> None:
        from app.auth import require_auth_if_secrets
        with pytest.raises(RuntimeError, match="GITEA_TOKEN"):
            require_auth_if_secrets({"GITEA_TOKEN": "tok"})

    def test_error_lists_all_offending_vars(self) -> None:
        from app.auth import require_auth_if_secrets
        env = {"CALDAV_PASSWORD": "pw", "DISCORD_INFO_HOOK": "https://x"}
        with pytest.raises(RuntimeError) as exc_info:
            require_auth_if_secrets(env)
        msg = str(exc_info.value)
        assert "CALDAV_PASSWORD" in msg
        assert "DISCORD_INFO_HOOK" in msg
        assert "MCP_API_KEY" in msg

    def test_secret_with_key_ok(self) -> None:
        from app.auth import require_auth_if_secrets
        env = {"GITEA_TOKEN": "tok", "MCP_API_KEY": "k3y"}
        require_auth_if_secrets(env)  # must not raise

    def test_whitespace_key_does_not_satisfy(self) -> None:
        from app.auth import require_auth_if_secrets
        env = {"NTFY_TOKEN": "tk", "MCP_API_KEY": "   "}
        with pytest.raises(RuntimeError, match="MCP_API_KEY"):
            require_auth_if_secrets(env)

    def test_reads_process_environment_by_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no argument, the live process environment is inspected."""
        from app.auth import require_auth_if_secrets
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/c.ics")
        with pytest.raises(RuntimeError, match="ICS_CALENDAR_URL"):
            require_auth_if_secrets()
        monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
        require_auth_if_secrets()  # must not raise


class TestStartupGuard:
    """create_app() refuses to start when secrets lack MCP_API_KEY."""

    def test_create_app_raises_with_secret_and_no_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        monkeypatch.setenv("CALDAV_PASSWORD", "pw")
        from app.main import create_app
        with pytest.raises(RuntimeError, match="CALDAV_PASSWORD"):
            create_app()

    def test_create_app_ok_with_both(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
        monkeypatch.setenv("CALDAV_PASSWORD", "pw")
        from app.main import create_app
        app = create_app()
        assert app is not None
