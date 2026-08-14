"""Unified notify service with provider-based routing.

Providers implement the NotifyProvider protocol.  The service fans out
to all configured providers (or a filtered subset) and collects results.

Discord is the first provider; Ntfy and others can be added later by
implementing the protocol and registering in _discover_providers().
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import httpx

from .notify_models import COLOR_MAP, NotifyRequest, NotifyResult

logger = logging.getLogger(__name__)

#: Discord message content limit (safe margin below 4096).
DISCORD_MAX_CONTENT = 3600


# --------------------------------------------------------------------------- #
# Provider protocol
# --------------------------------------------------------------------------- #

@runtime_checkable
class NotifyProvider(Protocol):
    """Protocol that all notify providers implement."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. 'discord', 'ntfy')."""
        ...

    @property
    def is_configured(self) -> bool:
        """Whether this provider has the required configuration."""
        ...

    def send(self, req: NotifyRequest) -> NotifyResult:
        """Send a notification via this provider."""
        ...


# --------------------------------------------------------------------------- #
# Discord provider
# --------------------------------------------------------------------------- #

class DiscordProvider:
    """Send notifications to Discord via webhook.

    Replaces the old discord.sh script.  Sends markdown-formatted
    messages as embeds, with optional color and title.
    """

    def __init__(
        self,
        webhook_url: str,
        alert_webhook_url: str | None = None,
        username: str | None = None,
        title_suffix: str | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._alert_webhook_url = alert_webhook_url or webhook_url
        self._username = username
        self._title_suffix = title_suffix

    @classmethod
    def from_env(cls) -> DiscordProvider | None:
        """Build from environment variables.  Returns None if not configured."""
        url = os.environ.get("DISCORD_GENERAL_HOOK", "").strip()
        if not url:
            return None

        alert_url = os.environ.get("DISCORD_ALERT_HOOK", "").strip() or None
        server_name = os.environ.get("DISCORD_SERVER_NAME", "").strip() or None
        title_suffix = os.environ.get("DISCORD_TITLE_SUFFIX", "").strip() or None

        return cls(
            webhook_url=url,
            alert_webhook_url=alert_url,
            username=server_name,
            title_suffix=title_suffix,
        )

    @property
    def name(self) -> str:
        return "discord"

    @property
    def is_configured(self) -> bool:
        return bool(self._webhook_url)

    def send(self, req: NotifyRequest) -> NotifyResult:
        """Send notification to Discord webhook."""
        # High/urgent priority routes to the alert webhook.
        webhook = self._alert_webhook_url if req.priority in ("high", "urgent") else self._webhook_url

        # Build embed.
        embed: dict = {"description": req.message}

        if req.color:
            embed["color"] = COLOR_MAP[req.color][0]

        title = req.title
        if title and self._title_suffix:
            title = f"{title} {self._title_suffix}"
        if title:
            embed["title"] = title

        payload: dict = {"embeds": [embed]}
        if self._username:
            payload["username"] = self._username

        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = client.post(webhook, json=payload)

            if resp.status_code >= 400:
                return NotifyResult(
                    provider=self.name,
                    success=False,
                    error=f"Discord API error {resp.status_code}: {resp.text[:200]}",
                )

            # Handle message splitting for long content.
            remaining = req.message[DISCORD_MAX_CONTENT:]
            if remaining:
                self._send_remaining(remaining, req, webhook)

        except httpx.HTTPError as exc:
            return NotifyResult(
                provider=self.name,
                success=False,
                error=f"HTTP error: {exc}",
            )

        return NotifyResult(provider=self.name, success=True)

    def _send_remaining(self, content: str, req: NotifyRequest, webhook: str) -> None:
        """Send overflow content as additional messages."""
        # Try to break at a newline near the split point.
        chunk = content
        extra = ""
        if len(content) > DISCORD_MAX_CONTENT:
            chunk = content[:DISCORD_MAX_CONTENT]
            extra = content[DISCORD_MAX_CONTENT:]
            # Try to break at last newline in chunk.
            last_nl = chunk.rfind("\n")
            if last_nl > 1000:
                extra = chunk[last_nl + 1:] + extra
                chunk = chunk[:last_nl]

        embed: dict = {"description": chunk}
        if req.color:
            embed["color"] = COLOR_MAP[req.color][0]
        if req.title and self._title_suffix:
            embed["title"] = f"{req.title} {self._title_suffix}"

        payload: dict = {"embeds": [embed]}
        if self._username:
            payload["username"] = self._username

        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                client.post(webhook, json=payload)
        except httpx.HTTPError:
            pass  # best-effort for overflow

        if extra:
            self._send_remaining(extra, req, webhook)


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #

class NotifyRegistry:
    """Manages notify providers and routes requests."""

    def __init__(self) -> None:
        self._providers: list[NotifyProvider] = []

    def register(self, provider: NotifyProvider) -> None:
        self._providers.append(provider)
        logger.info("Registered notify provider: %s", provider.name)

    def clear(self) -> None:
        self._providers.clear()

    @property
    def has_providers(self) -> bool:
        return len(self._providers) > 0

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    def send(self, req: NotifyRequest) -> list[NotifyResult]:
        """Fan out to all providers (or filtered subset)."""
        targets = self._providers
        if req.channels:
            targets = [p for p in self._providers if p.name in req.channels]

        results: list[NotifyResult] = []
        for provider in targets:
            try:
                result = provider.send(req)
            except Exception as exc:  # noqa: BLE001
                result = NotifyResult(
                    provider=provider.name,
                    success=False,
                    error=f"Unexpected error: {exc}",
                )
            results.append(result)

        return results


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def _discover_providers() -> list[NotifyProvider]:
    """Discover and instantiate providers from environment variables."""
    providers: list[NotifyProvider] = []

    discord = DiscordProvider.from_env()
    if discord is not None:
        providers.append(discord)

    # Future: NtfyProvider.from_env(), etc.

    return providers


# Module-level singleton
notify_registry = NotifyRegistry()


def reset_notify_registry() -> None:
    """Clear the global notify registry (used in tests)."""
    notify_registry.clear()


def init_notify_registry() -> bool:
    """Discover and register providers.  Returns True if any configured."""
    reset_notify_registry()
    providers = _discover_providers()
    for p in providers:
        notify_registry.register(p)
    return notify_registry.has_providers
