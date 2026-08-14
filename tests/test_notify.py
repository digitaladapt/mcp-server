"""Tests for the unified notify system."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.notify_models import COLOR_MAP, NotifyRequest, NotifyResult
from app.notify_service import (
    DiscordProvider,
    NotifyRegistry,
    reset_notify_registry,
)

# --------------------------------------------------------------------------- #
# Model tests
# --------------------------------------------------------------------------- #

class TestNotifyModels:
    def test_minimal_request(self):
        req = NotifyRequest(message="Hello world")
        assert req.message == "Hello world"
        assert req.priority == "default"
        assert req.title is None
        assert req.color is None
        assert req.channels is None

    def test_full_request(self):
        req = NotifyRequest(
            message="Deploy complete",
            title="CI",
            priority="urgent",
            color="red",
            channels=["discord"],
        )
        assert req.priority == "urgent"
        assert req.color == "red"
        assert req.channels == ["discord"]

    def test_empty_message_rejected(self):
        with pytest.raises(ValueError, match="message must not be empty"):
            NotifyRequest(message="")

    def test_whitespace_message_rejected(self):
        with pytest.raises(ValueError, match="message must not be empty"):
            NotifyRequest(message="   ")

    def test_color_map_has_all_colors(self):
        expected = {"red", "orange", "yellow", "green", "blue", "purple", "brown", "black", "white"}
        assert set(COLOR_MAP.keys()) == expected

    def test_color_map_has_emoji_for_ntfy(self):
        for color_int, emoji in COLOR_MAP.values():
            assert isinstance(color_int, int)
            assert isinstance(emoji, str)
            assert len(emoji) > 0


# --------------------------------------------------------------------------- #
# DiscordProvider tests
# --------------------------------------------------------------------------- #

class TestDiscordProvider:
    def test_from_env_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("DISCORD_GENERAL_HOOK", raising=False)
        assert DiscordProvider.from_env() is None

    def test_from_env_creates_provider(self, monkeypatch):
        monkeypatch.setenv("DISCORD_GENERAL_HOOK", "https://discord.com/api/webhooks/123/abc?wait=true")
        monkeypatch.setenv("DISCORD_ALERT_HOOK", "https://discord.com/api/webhooks/123/alert?wait=true")
        monkeypatch.setenv("DISCORD_SERVER_NAME", "test-bot")
        monkeypatch.setenv("DISCORD_TITLE_SUFFIX", "· test-bot")

        provider = DiscordProvider.from_env()
        assert provider is not None
        assert provider.name == "discord"
        assert provider.is_configured is True

    def test_from_env_alert_defaults_to_general(self, monkeypatch):
        monkeypatch.setenv("DISCORD_GENERAL_HOOK", "https://discord.com/api/webhooks/123/abc?wait=true")
        monkeypatch.delenv("DISCORD_ALERT_HOOK", raising=False)

        provider = DiscordProvider.from_env()
        assert provider is not None
        assert provider._webhook_url == provider._alert_webhook_url

    @patch("app.notify_service.httpx.Client")
    def test_send_basic_message(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = DiscordProvider(webhook_url="https://discord.com/api/webhooks/123/abc?wait=true")
        req = NotifyRequest(message="Hello world")
        result = provider.send(req)

        assert result.success is True
        assert result.provider == "discord"
        assert result.error is None

        # Check payload
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert "embeds" in payload
        assert payload["embeds"][0]["description"] == "Hello world"
        assert "color" not in payload["embeds"][0]
        assert "title" not in payload["embeds"][0]

    @patch("app.notify_service.httpx.Client")
    def test_send_with_color_and_title(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = DiscordProvider(
            webhook_url="https://discord.com/api/webhooks/123/abc?wait=true",
            username="test-bot",
            title_suffix="· test-bot",
        )
        req = NotifyRequest(message="Deploy failed", title="CI", color="red")
        result = provider.send(req)

        assert result.success is True
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["embeds"][0]["color"] == COLOR_MAP["red"][0]
        assert payload["embeds"][0]["title"] == "CI · test-bot"
        assert payload["username"] == "test-bot"

    @patch("app.notify_service.httpx.Client")
    def test_high_priority_uses_alert_webhook(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        general = "https://discord.com/api/webhooks/123/general?wait=true"
        alert = "https://discord.com/api/webhooks/123/alert?wait=true"

        provider = DiscordProvider(webhook_url=general, alert_webhook_url=alert)
        req = NotifyRequest(message="Critical alert", priority="urgent")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == alert

    @patch("app.notify_service.httpx.Client")
    def test_default_priority_uses_general_webhook(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        general = "https://discord.com/api/webhooks/123/general?wait=true"
        alert = "https://discord.com/api/webhooks/123/alert?wait=true"

        provider = DiscordProvider(webhook_url=general, alert_webhook_url=alert)
        req = NotifyRequest(message="Normal message")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == general

    @patch("app.notify_service.httpx.Client")
    def test_api_error_returns_failure(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"message": "Bad Request"}'
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = DiscordProvider(webhook_url="https://discord.com/api/webhooks/123/abc?wait=true")
        req = NotifyRequest(message="Hello")
        result = provider.send(req)

        assert result.success is False
        assert "400" in result.error

    @patch("app.notify_service.httpx.Client")
    def test_http_exception_returns_failure(self, mock_client_cls):
        import httpx
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = DiscordProvider(webhook_url="https://discord.com/api/webhooks/123/abc?wait=true")
        req = NotifyRequest(message="Hello")
        result = provider.send(req)

        assert result.success is False
        assert "HTTP error" in result.error

    @patch("app.notify_service.httpx.Client")
    def test_long_message_splits(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = DiscordProvider(webhook_url="https://discord.com/api/webhooks/123/abc?wait=true")
        # Create a message longer than DISCORD_MAX_CONTENT
        long_msg = "A" * 5000
        req = NotifyRequest(message=long_msg)
        result = provider.send(req)

        assert result.success is True
        # Should have been called at least twice (original + overflow)
        assert mock_client.post.call_count >= 2


# --------------------------------------------------------------------------- #
# NotifyRegistry tests
# --------------------------------------------------------------------------- #

class TestNotifyRegistry:
    def test_empty_registry(self):
        reg = NotifyRegistry()
        assert reg.has_providers is False
        assert reg.provider_names == []

    def test_register_and_send(self):
        reg = NotifyRegistry()
        mock_provider = MagicMock()
        mock_provider.name = "test"
        mock_provider.send.return_value = NotifyResult(provider="test", success=True)
        reg.register(mock_provider)

        assert reg.has_providers is True
        assert reg.provider_names == ["test"]

        req = NotifyRequest(message="Hello")
        results = reg.send(req)
        assert len(results) == 1
        assert results[0].success is True

    def test_channel_filter(self):
        reg = NotifyRegistry()
        p1 = MagicMock()
        p1.name = "discord"
        p1.send.return_value = NotifyResult(provider="discord", success=True)
        p2 = MagicMock()
        p2.name = "ntfy"
        p2.send.return_value = NotifyResult(provider="ntfy", success=True)
        reg.register(p1)
        reg.register(p2)

        req = NotifyRequest(message="Hello", channels=["discord"])
        results = reg.send(req)

        assert len(results) == 1
        assert results[0].provider == "discord"
        p2.send.assert_not_called()

    def test_provider_exception_caught(self):
        reg = NotifyRegistry()
        mock_provider = MagicMock()
        mock_provider.name = "flaky"
        mock_provider.send.side_effect = RuntimeError("Boom")
        reg.register(mock_provider)

        req = NotifyRequest(message="Hello")
        results = reg.send(req)

        assert len(results) == 1
        assert results[0].success is False
        assert "Unexpected error" in results[0].error
        assert "Boom" in results[0].error

    def test_clear(self):
        reg = NotifyRegistry()
        mock_provider = MagicMock()
        mock_provider.name = "test"
        reg.register(mock_provider)
        reg.clear()
        assert reg.has_providers is False


# --------------------------------------------------------------------------- #
# API endpoint tests
# --------------------------------------------------------------------------- #

@pytest.fixture
def notify_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with Discord notify configured (mocked HTTP)."""
    monkeypatch.setenv("DISCORD_GENERAL_HOOK", "https://discord.com/api/webhooks/123/abc?wait=true")
    monkeypatch.setenv("DISCORD_ALERT_HOOK", "https://discord.com/api/webhooks/123/alert?wait=true")
    monkeypatch.setenv("DISCORD_SERVER_NAME", "test-bot")

    # Clean up other providers to avoid interference.
    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)
    monkeypatch.delenv("GITEA_URL", raising=False)

    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client

    reset_notify_registry()


class TestNotifyEndpoint:
    def test_notify_success(self, notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = notify_client.post("/notify", json={"message": "Hello world"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] is True
        assert len(data["results"]) == 1
        assert data["results"][0]["provider"] == "discord"
        assert data["results"][0]["success"] is True

    def test_notify_with_color_and_title(self, notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = notify_client.post("/notify", json={
                "message": "Deploy failed",
                "title": "CI",
                "color": "red",
                "priority": "urgent",
            })

        assert resp.status_code == 200
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["embeds"][0]["color"] == COLOR_MAP["red"][0]
        assert "CI" in payload["embeds"][0]["title"]

    def test_notify_empty_message_rejected(self, notify_client):
        resp = notify_client.post("/notify", json={"message": ""})
        assert resp.status_code == 422

    def test_notify_invalid_color_rejected(self, notify_client):
        resp = notify_client.post("/notify", json={"message": "Hi", "color": "teal"})
        assert resp.status_code == 422

    def test_notify_invalid_priority_rejected(self, notify_client):
        resp = notify_client.post("/notify", json={"message": "Hi", "priority": "critical"})
        assert resp.status_code == 422

    def test_notify_missing_message_rejected(self, notify_client):
        resp = notify_client.post("/notify", json={"title": "No message"})
        assert resp.status_code == 422

    def test_notify_channel_filter(self, notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = notify_client.post("/notify", json={
                "message": "Hello",
                "channels": ["discord"],
            })

        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_notify_nonexistent_channel(self, notify_client):
        with patch("app.notify_service.httpx.Client"):
            resp = notify_client.post("/notify", json={
                "message": "Hello",
                "channels": ["nonexistent"],
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] is False
        assert len(data["results"]) == 0

    def test_notify_api_error(self, notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Unauthorized"
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = notify_client.post("/notify", json={"message": "Hello"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] is False
        assert data["results"][0]["success"] is False
        assert "401" in data["results"][0]["error"]

    def test_notify_in_openapi_schema(self, notify_client):
        """The /notify endpoint should be visible in the OpenAPI schema."""
        resp = notify_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "/notify" in schema["paths"]
        assert "post" in schema["paths"]["/notify"]

    def test_notify_supports_markdown(self, notify_client):
        """Verify markdown content is passed through without ANSI wrapping."""
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            markdown_msg = "**Bold** and `code` and [link](https://example.com)"
            resp = notify_client.post("/notify", json={"message": markdown_msg})

        assert resp.status_code == 200
        payload = mock_client.post.call_args.kwargs["json"]
        # Message should be the raw markdown, no ANSI code block wrapping
        assert payload["embeds"][0]["description"] == markdown_msg
        assert "```" not in payload["embeds"][0]["description"]
        assert "\\u001b" not in payload["embeds"][0]["description"]
