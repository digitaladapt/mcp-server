"""Tests for the unified notify system."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.notify_models import COLOR_MAP, LEVEL_ORDER, NotifyRequest, NotifyResult
from app.notify_service import (
    DiscordProvider,
    NotifyRegistry,
    NtfyProvider,
    reset_notify_registry,
)

# --------------------------------------------------------------------------- #
# Model tests
# --------------------------------------------------------------------------- #

class TestNotifyModels:
    def test_minimal_request(self):
        req = NotifyRequest(message="Hello world")
        assert req.message == "Hello world"
        assert req.level == "notice"
        assert req.title is None
        assert req.color is None
        assert req.channels is None

    def test_full_request(self):
        req = NotifyRequest(
            message="Deploy complete",
            title="CI",
            level="emergency",
            color="red",
            channels=["discord"],
        )
        assert req.level == "emergency"
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

    def test_level_order_is_ascending(self):
        assert LEVEL_ORDER == ["info", "notice", "critical", "emergency"]


# --------------------------------------------------------------------------- #
# DiscordProvider tests
# --------------------------------------------------------------------------- #

class TestDiscordProvider:
    def test_from_env_returns_none_when_not_configured(self, monkeypatch):
        for key in ("DISCORD_INFO_HOOK", "DISCORD_NOTICE_HOOK", "DISCORD_CRITICAL_HOOK", "DISCORD_EMERGENCY_HOOK"):
            monkeypatch.delenv(key, raising=False)
        assert DiscordProvider.from_env() is None

    def test_from_env_creates_provider(self, monkeypatch):
        monkeypatch.setenv("DISCORD_INFO_HOOK", "https://discord.com/api/webhooks/123/info?wait=true")
        monkeypatch.setenv("DISCORD_NOTICE_HOOK", "https://discord.com/api/webhooks/123/notice?wait=true")
        monkeypatch.setenv("DISCORD_CRITICAL_HOOK", "https://discord.com/api/webhooks/123/critical?wait=true")
        monkeypatch.setenv("DISCORD_EMERGENCY_HOOK", "https://discord.com/api/webhooks/123/emergency?wait=true")
        monkeypatch.setenv("DISCORD_SERVER_NAME", "test-bot")
        monkeypatch.setenv("DISCORD_TITLE_SUFFIX", "· test-bot")

        provider = DiscordProvider.from_env()
        assert provider is not None
        assert provider.name == "discord"
        assert provider.is_configured is True
        assert len(provider._webhooks) == 4

    def test_from_env_single_webhook(self, monkeypatch):
        """Only info hook configured — all levels fall back to it."""
        monkeypatch.delenv("DISCORD_NOTICE_HOOK", raising=False)
        monkeypatch.delenv("DISCORD_CRITICAL_HOOK", raising=False)
        monkeypatch.delenv("DISCORD_EMERGENCY_HOOK", raising=False)
        monkeypatch.setenv("DISCORD_INFO_HOOK", "https://discord.com/api/webhooks/123/abc?wait=true")

        provider = DiscordProvider.from_env()
        assert provider is not None
        assert len(provider._webhooks) == 1
        assert "info" in provider._webhooks

    @patch("app.notify_service.httpx.Client")
    def test_send_basic_message(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = DiscordProvider(webhooks={"notice": "https://discord.com/api/webhooks/123/abc?wait=true"})
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
            webhooks={"notice": "https://discord.com/api/webhooks/123/abc?wait=true"},
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
    def test_each_level_routes_to_correct_webhook(self, mock_client_cls):
        """All four levels route to their own webhook when configured."""
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhooks = {
            "info":      "https://discord.com/api/webhooks/123/info?wait=true",
            "notice":    "https://discord.com/api/webhooks/123/notice?wait=true",
            "critical":  "https://discord.com/api/webhooks/123/critical?wait=true",
            "emergency": "https://discord.com/api/webhooks/123/emergency?wait=true",
        }
        provider = DiscordProvider(webhooks=webhooks)

        for level in ("info", "notice", "critical", "emergency"):
            mock_client.post.reset_mock()
            req = NotifyRequest(message=f"Test {level}", level=level)
            provider.send(req)
            call_url = mock_client.post.call_args.args[0]
            assert call_url == webhooks[level], f"{level} should route to its own webhook"

    @patch("app.notify_service.httpx.Client")
    def test_emergency_falls_back_to_critical(self, mock_client_cls):
        """Emergency falls back to critical when emergency hook is missing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhooks = {
            "info":     "https://discord.com/api/webhooks/123/info?wait=true",
            "notice":   "https://discord.com/api/webhooks/123/notice?wait=true",
            "critical": "https://discord.com/api/webhooks/123/critical?wait=true",
        }
        provider = DiscordProvider(webhooks=webhooks)
        req = NotifyRequest(message="Emergency!", level="emergency")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == webhooks["critical"]

    @patch("app.notify_service.httpx.Client")
    def test_critical_falls_back_to_notice(self, mock_client_cls):
        """Critical falls back to notice when critical hook is missing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhooks = {
            "info":   "https://discord.com/api/webhooks/123/info?wait=true",
            "notice": "https://discord.com/api/webhooks/123/notice?wait=true",
        }
        provider = DiscordProvider(webhooks=webhooks)
        req = NotifyRequest(message="Critical!", level="critical")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == webhooks["notice"]

    @patch("app.notify_service.httpx.Client")
    def test_notice_falls_back_to_info(self, mock_client_cls):
        """Notice falls back to info when notice hook is missing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhooks = {
            "info": "https://discord.com/api/webhooks/123/info?wait=true",
        }
        provider = DiscordProvider(webhooks=webhooks)
        req = NotifyRequest(message="Notice!", level="notice")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == webhooks["info"]

    @patch("app.notify_service.httpx.Client")
    def test_emergency_falls_back_all_the_way_to_info(self, mock_client_cls):
        """Emergency falls back to the lowest available level."""
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhooks = {
            "info": "https://discord.com/api/webhooks/123/info?wait=true",
        }
        provider = DiscordProvider(webhooks=webhooks)
        req = NotifyRequest(message="Emergency!", level="emergency")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == webhooks["info"]

    @patch("app.notify_service.httpx.Client")
    def test_default_level_is_notice(self, mock_client_cls):
        """Default level is notice, routes to notice webhook."""
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhooks = {
            "info":   "https://discord.com/api/webhooks/123/info?wait=true",
            "notice": "https://discord.com/api/webhooks/123/notice?wait=true",
        }
        provider = DiscordProvider(webhooks=webhooks)
        req = NotifyRequest(message="Normal message")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == webhooks["notice"]

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

        provider = DiscordProvider(webhooks={"notice": "https://discord.com/api/webhooks/123/abc?wait=true"})
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

        provider = DiscordProvider(webhooks={"notice": "https://discord.com/api/webhooks/123/abc?wait=true"})
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

        provider = DiscordProvider(webhooks={"notice": "https://discord.com/api/webhooks/123/abc?wait=true"})
        # Create a message longer than DISCORD_MAX_CONTENT
        long_msg = "A" * 5000
        req = NotifyRequest(message=long_msg)
        result = provider.send(req)

        assert result.success is True
        # Should have been called at least twice (original + overflow)
        assert mock_client.post.call_count >= 2


# --------------------------------------------------------------------------- #
# NtfyProvider tests
# --------------------------------------------------------------------------- #

class TestNtfyProvider:
    def test_from_env_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("NTFY_URL", raising=False)
        for key in ("NTFY_INFO_TOPIC", "NTFY_NOTICE_TOPIC", "NTFY_CRITICAL_TOPIC", "NTFY_EMERGENCY_TOPIC"):
            monkeypatch.delenv(key, raising=False)
        assert NtfyProvider.from_env() is None

    def test_from_env_returns_none_without_topics(self, monkeypatch):
        """URL set but no topics → not configured."""
        monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
        for key in ("NTFY_INFO_TOPIC", "NTFY_NOTICE_TOPIC", "NTFY_CRITICAL_TOPIC", "NTFY_EMERGENCY_TOPIC"):
            monkeypatch.delenv(key, raising=False)
        assert NtfyProvider.from_env() is None

    def test_from_env_creates_provider(self, monkeypatch):
        monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
        monkeypatch.setenv("NTFY_INFO_TOPIC", "myserver-info")
        monkeypatch.setenv("NTFY_NOTICE_TOPIC", "myserver-notice")
        monkeypatch.setenv("NTFY_CRITICAL_TOPIC", "myserver-critical")
        monkeypatch.setenv("NTFY_EMERGENCY_TOPIC", "myserver-emergency")
        monkeypatch.setenv("NTFY_TOKEN", "tk_test123")
        monkeypatch.setenv("NTFY_TITLE_SUFFIX", "· my-server")

        provider = NtfyProvider.from_env()
        assert provider is not None
        assert provider.name == "ntfy"
        assert provider.is_configured is True
        assert len(provider._topics) == 4
        assert provider._token == "tk_test123"

    def test_from_env_basic_auth(self, monkeypatch):
        monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
        monkeypatch.setenv("NTFY_INFO_TOPIC", "myserver-info")
        monkeypatch.setenv("NTFY_USERNAME", "andrew")
        monkeypatch.setenv("NTFY_PASSWORD", "secret")
        monkeypatch.delenv("NTFY_TOKEN", raising=False)

        provider = NtfyProvider.from_env()
        assert provider is not None
        assert provider._username == "andrew"
        assert provider._password == "secret"
        assert provider._token is None

    def test_from_env_single_topic(self, monkeypatch):
        """Only info topic configured — all levels fall back to it."""
        monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
        monkeypatch.setenv("NTFY_INFO_TOPIC", "myserver-info")
        monkeypatch.delenv("NTFY_NOTICE_TOPIC", raising=False)
        monkeypatch.delenv("NTFY_CRITICAL_TOPIC", raising=False)
        monkeypatch.delenv("NTFY_EMERGENCY_TOPIC", raising=False)

        provider = NtfyProvider.from_env()
        assert provider is not None
        assert len(provider._topics) == 1
        assert "info" in provider._topics

    @patch("app.notify_service.httpx.Client")
    def test_send_basic_message(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "myserver-notice"},
        )
        req = NotifyRequest(message="Hello world")
        result = provider.send(req)

        assert result.success is True
        assert result.provider == "ntfy"
        assert result.error is None

        call_args = mock_client.post.call_args
        # URL should be base_url/topic
        assert call_args.args[0] == "https://ntfy.example.com/myserver-notice"
        # Message body is sent as raw content
        assert call_args.kwargs["content"] == "Hello world"
        # Priority header for notice level
        assert call_args.kwargs["headers"]["Priority"] == "3"

    @patch("app.notify_service.httpx.Client")
    def test_send_with_color_and_title(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "myserver-notice"},
            title_suffix="· my-server",
        )
        req = NotifyRequest(message="Deploy failed", title="CI", color="red")
        result = provider.send(req)

        assert result.success is True
        call_args = mock_client.post.call_args
        headers = call_args.kwargs["headers"]
        # Color is conveyed as emoji tag
        assert headers["Tags"] == COLOR_MAP["red"][1]  # 🔴
        assert headers["Title"] == "CI · my-server"
        assert headers["Priority"] == "3"

    @patch("app.notify_service.httpx.Client")
    def test_color_emoji_used_not_integer(self, mock_client_cls):
        """Verify the emoji from COLOR_MAP is used, not the integer."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "myserver-notice"},
        )
        req = NotifyRequest(message="Test", color="green")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Tags"] == COLOR_MAP["green"][1]  # 🟢
        assert isinstance(headers["Tags"], str)

    @patch("app.notify_service.httpx.Client")
    def test_send_without_color_has_no_tags(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "myserver-notice"},
        )
        req = NotifyRequest(message="No color")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "Tags" not in headers

    @patch("app.notify_service.httpx.Client")
    def test_each_level_routes_to_correct_topic(self, mock_client_cls):
        """All four levels route to their own topic when configured."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {
            "info":      "myserver-info",
            "notice":    "myserver-notice",
            "critical":  "myserver-critical",
            "emergency": "myserver-emergency",
        }
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )

        for level in ("info", "notice", "critical", "emergency"):
            mock_client.post.reset_mock()
            req = NotifyRequest(message=f"Test {level}", level=level)
            provider.send(req)
            call_url = mock_client.post.call_args.args[0]
            assert call_url == f"https://ntfy.example.com/{topics[level]}", \
                f"{level} should route to its own topic"

    @patch("app.notify_service.httpx.Client")
    def test_priority_header_matches_level(self, mock_client_cls):
        """Each level maps to the correct ntfy priority (2-5)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {
            "info":      "t-info",
            "notice":    "t-notice",
            "critical":  "t-critical",
            "emergency": "t-emergency",
        }
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )

        expected_priorities = {"info": "2", "notice": "3", "critical": "4", "emergency": "5"}
        for level in ("info", "notice", "critical", "emergency"):
            mock_client.post.reset_mock()
            req = NotifyRequest(message=f"Test {level}", level=level)
            provider.send(req)
            headers = mock_client.post.call_args.kwargs["headers"]
            assert headers["Priority"] == expected_priorities[level]

    @patch("app.notify_service.httpx.Client")
    def test_emergency_falls_back_to_critical(self, mock_client_cls):
        """Emergency falls back to critical when emergency topic is missing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {
            "info":     "t-info",
            "notice":   "t-notice",
            "critical": "t-critical",
        }
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )
        req = NotifyRequest(message="Emergency!", level="emergency")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "https://ntfy.example.com/t-critical"
        # Priority should be that of the resolved level (critical=4),
        # not the requested level (emergency=5).
        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Priority"] == "4"

    @patch("app.notify_service.httpx.Client")
    def test_critical_falls_back_to_notice(self, mock_client_cls):
        """Critical falls back to notice when critical topic is missing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {
            "info":   "t-info",
            "notice": "t-notice",
        }
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )
        req = NotifyRequest(message="Critical!", level="critical")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "https://ntfy.example.com/t-notice"

    @patch("app.notify_service.httpx.Client")
    def test_notice_falls_back_to_info(self, mock_client_cls):
        """Notice falls back to info when notice topic is missing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {"info": "t-info"}
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )
        req = NotifyRequest(message="Notice!", level="notice")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "https://ntfy.example.com/t-info"

    @patch("app.notify_service.httpx.Client")
    def test_emergency_falls_back_all_the_way_to_info(self, mock_client_cls):
        """Emergency falls back to the lowest available level."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {"info": "t-info"}
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )
        req = NotifyRequest(message="Emergency!", level="emergency")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "https://ntfy.example.com/t-info"

    @patch("app.notify_service.httpx.Client")
    def test_default_level_is_notice(self, mock_client_cls):
        """Default level is notice, routes to notice topic."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        topics = {
            "info":   "t-info",
            "notice": "t-notice",
        }
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics=topics,
        )
        req = NotifyRequest(message="Normal message")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "https://ntfy.example.com/t-notice"

    @patch("app.notify_service.httpx.Client")
    def test_token_auth_header(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
            token="tk_secret123",
        )
        req = NotifyRequest(message="Hello")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tk_secret123"

    @patch("app.notify_service.httpx.Client")
    def test_basic_auth_header(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
            username="andrew",
            password="secret",
        )
        req = NotifyRequest(message="Hello")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Authorization"].startswith("Basic ")

    @patch("app.notify_service.httpx.Client")
    def test_no_auth_header_when_not_configured(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
        )
        req = NotifyRequest(message="Hello")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "Authorization" not in headers

    @patch("app.notify_service.httpx.Client")
    def test_token_auth_preferred_over_basic(self, mock_client_cls):
        """When both token and basic auth are configured, token wins."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
            token="tk_secret",
            username="andrew",
            password="secret",
        )
        req = NotifyRequest(message="Hello")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tk_secret"

    @patch("app.notify_service.httpx.Client")
    def test_api_error_returns_failure(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"error": "Forbidden"}'
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
        )
        req = NotifyRequest(message="Hello")
        result = provider.send(req)

        assert result.success is False
        assert "403" in result.error

    @patch("app.notify_service.httpx.Client")
    def test_http_exception_returns_failure(self, mock_client_cls):
        import httpx
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
        )
        req = NotifyRequest(message="Hello")
        result = provider.send(req)

        assert result.success is False
        assert "HTTP error" in result.error

    @patch("app.notify_service.httpx.Client")
    def test_base_url_trailing_slash_stripped(self, mock_client_cls):
        """Trailing slash in base URL should be stripped."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com/",
            topics={"notice": "t-notice"},
        )
        req = NotifyRequest(message="Hello")
        provider.send(req)

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "https://ntfy.example.com/t-notice"

    @patch("app.notify_service.httpx.Client")
    def test_markdown_passed_through_as_content(self, mock_client_cls):
        """Message body is sent as raw content, no wrapping."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        markdown_msg = "**Bold** and `code` and [link](https://example.com)"
        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
        )
        req = NotifyRequest(message=markdown_msg)
        provider.send(req)

        content = mock_client.post.call_args.kwargs["content"]
        assert content == markdown_msg

    @patch("app.notify_service.httpx.Client")
    def test_send_without_title_has_no_title_header(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = NtfyProvider(
            base_url="https://ntfy.example.com",
            topics={"notice": "t-notice"},
        )
        req = NotifyRequest(message="No title")
        provider.send(req)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "Title" not in headers


# --------------------------------------------------------------------------- #
# Multi-provider (Discord + Ntfy) integration tests
# --------------------------------------------------------------------------- #

@pytest.fixture
def dual_notify_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with both Discord and Ntfy configured (mocked HTTP)."""
    monkeypatch.setenv("DISCORD_INFO_HOOK", "https://discord.com/api/webhooks/123/info?wait=true")
    monkeypatch.setenv("DISCORD_NOTICE_HOOK", "https://discord.com/api/webhooks/123/notice?wait=true")
    monkeypatch.setenv("DISCORD_CRITICAL_HOOK", "https://discord.com/api/webhooks/123/critical?wait=true")
    monkeypatch.setenv("DISCORD_EMERGENCY_HOOK", "https://discord.com/api/webhooks/123/emergency?wait=true")

    monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
    monkeypatch.setenv("NTFY_INFO_TOPIC", "myserver-info")
    monkeypatch.setenv("NTFY_NOTICE_TOPIC", "myserver-notice")
    monkeypatch.setenv("NTFY_CRITICAL_TOPIC", "myserver-critical")
    monkeypatch.setenv("NTFY_EMERGENCY_TOPIC", "myserver-emergency")
    monkeypatch.setenv("NTFY_TOKEN", "tk_test123")

    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)
    monkeypatch.delenv("GITEA_URL", raising=False)

    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client

    reset_notify_registry()


class TestDualProviderEndpoint:
    def test_both_providers_receive_notification(self, dual_notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = dual_notify_client.post("/notify", json={"message": "Hello both"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] is True
        assert len(data["results"]) == 2
        provider_names = {r["provider"] for r in data["results"]}
        assert provider_names == {"discord", "ntfy"}

    def test_channel_filter_ntfy_only(self, dual_notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = dual_notify_client.post("/notify", json={
                "message": "Ntfy only",
                "channels": ["ntfy"],
            })

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["provider"] == "ntfy"
        assert data["results"][0]["success"] is True

    def test_channel_filter_discord_only(self, dual_notify_client):
        with patch("app.notify_service.httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = dual_notify_client.post("/notify", json={
                "message": "Discord only",
                "channels": ["discord"],
            })

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["provider"] == "discord"

    def test_ntfy_only_configured(self, monkeypatch):
        """When only Ntfy is configured (no Discord), it works standalone."""
        monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
        monkeypatch.setenv("NTFY_INFO_TOPIC", "myserver-info")
        monkeypatch.setenv("NTFY_NOTICE_TOPIC", "myserver-notice")

        # Ensure Discord is not configured
        for key in ("DISCORD_INFO_HOOK", "DISCORD_NOTICE_HOOK", "DISCORD_CRITICAL_HOOK", "DISCORD_EMERGENCY_HOOK"):
            monkeypatch.delenv(key, raising=False)

        monkeypatch.delenv("CALDAV_URL", raising=False)
        monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)
        monkeypatch.delenv("GITEA_URL", raising=False)

        from app.main import create_app
        app = create_app()
        with TestClient(app) as client:
            with patch("app.notify_service.httpx.Client") as mock_client_cls:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_client = MagicMock()
                mock_client.post.return_value = mock_resp
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client_cls.return_value = mock_client

                resp = client.post("/notify", json={"message": "Ntfy standalone"})

            assert resp.status_code == 200
            data = resp.json()
            assert data["sent"] is True
            assert len(data["results"]) == 1
            assert data["results"][0]["provider"] == "ntfy"

        reset_notify_registry()


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
    monkeypatch.setenv("DISCORD_INFO_HOOK", "https://discord.com/api/webhooks/123/info?wait=true")
    monkeypatch.setenv("DISCORD_NOTICE_HOOK", "https://discord.com/api/webhooks/123/notice?wait=true")
    monkeypatch.setenv("DISCORD_CRITICAL_HOOK", "https://discord.com/api/webhooks/123/critical?wait=true")
    monkeypatch.setenv("DISCORD_EMERGENCY_HOOK", "https://discord.com/api/webhooks/123/emergency?wait=true")
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
                "level": "emergency",
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

    def test_notify_invalid_level_rejected(self, notify_client):
        resp = notify_client.post("/notify", json={"message": "Hi", "level": "urgent"})
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
