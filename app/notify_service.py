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

from .notify_models import COLOR_MAP, LEVEL_ORDER, NotifyRequest, NotifyResult

logger = logging.getLogger(__name__)

#: Discord message content limit (safe margin below 4096).
DISCORD_MAX_CONTENT = 3600

# Env var names for each level, ordered low → high.
_DISCORD_ENV_KEYS: dict[str, str] = {
    "info":      "DISCORD_INFO_HOOK",
    "notice":    "DISCORD_NOTICE_HOOK",
    "critical":  "DISCORD_CRITICAL_HOOK",
    "emergency": "DISCORD_EMERGENCY_HOOK",
}


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

    Supports up to four webhooks, one per severity level.  If a
    webhook for the requested level is not configured, falls back to
    the nearest lower configured level.
    """

    def __init__(
        self,
        webhooks: dict[str, str],
        username: str | None = None,
        title_suffix: str | None = None,
    ) -> None:
        # Store only configured levels, keyed by level name.
        self._webhooks: dict[str, str] = dict(webhooks)
        self._username = username
        self._title_suffix = title_suffix

    @classmethod
    def from_env(cls) -> DiscordProvider | None:
        """Build from environment variables.  Returns None if not configured."""
        webhooks: dict[str, str] = {}
        for level, env_key in _DISCORD_ENV_KEYS.items():
            url = os.environ.get(env_key, "").strip()
            if url:
                webhooks[level] = url

        if not webhooks:
            return None

        server_name = os.environ.get("DISCORD_SERVER_NAME", "").strip() or None
        title_suffix = os.environ.get("DISCORD_TITLE_SUFFIX", "").strip() or None

        return cls(
            webhooks=webhooks,
            username=server_name,
            title_suffix=title_suffix,
        )

    @property
    def name(self) -> str:
        return "discord"

    @property
    def is_configured(self) -> bool:
        return bool(self._webhooks)

    def _resolve_webhook(self, level: str) -> str | None:
        """Resolve a webhook URL for the given level.

        Falls back to the nearest lower configured level if the
        exact level is not available.
        """
        if level in self._webhooks:
            return self._webhooks[level]

        idx = LEVEL_ORDER.index(level)
        for candidate in reversed(LEVEL_ORDER[:idx]):
            if candidate in self._webhooks:
                return self._webhooks[candidate]

        # Fall back to the lowest configured level.
        for candidate in LEVEL_ORDER:
            if candidate in self._webhooks:
                return self._webhooks[candidate]

        return None

    def send(self, req: NotifyRequest) -> NotifyResult:
        """Send notification to Discord webhook."""
        webhook = self._resolve_webhook(req.level)
        if not webhook:
            return NotifyResult(
                provider=self.name,
                success=False,
                error="No Discord webhook configured",
            )

        # Split message into chunks that fit Discord's embed limit.
        chunks = self._split_message(req.message)

        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                for i, chunk in enumerate(chunks):
                    embed: dict = {"description": chunk}

                    if req.color:
                        embed["color"] = COLOR_MAP[req.color][0]

                    # Title only on the first chunk.
                    if i == 0 and req.title:
                        title = req.title
                        if self._title_suffix:
                            title = f"{title} {self._title_suffix}"
                        embed["title"] = title

                    payload: dict = {"embeds": [embed]}
                    if self._username:
                        payload["username"] = self._username

                    resp = client.post(webhook, json=payload)

                    if resp.status_code >= 400:
                        return NotifyResult(
                            provider=self.name,
                            success=False,
                            error=f"Discord API error {resp.status_code}: {resp.text[:200]}",
                        )

        except httpx.HTTPError as exc:
            return NotifyResult(
                provider=self.name,
                success=False,
                error=f"HTTP error: {exc}",
            )

        return NotifyResult(provider=self.name, success=True)

    @staticmethod
    def _split_message(message: str) -> list[str]:
        """Split a message into chunks that fit within Discord's limit.

        Tries to break at newlines near the split point for readability.
        """
        if len(message) <= DISCORD_MAX_CONTENT:
            return [message]

        chunks: list[str] = []
        remaining = message

        while remaining:
            if len(remaining) <= DISCORD_MAX_CONTENT:
                chunks.append(remaining)
                break

            chunk = remaining[:DISCORD_MAX_CONTENT]
            remaining = remaining[DISCORD_MAX_CONTENT:]

            # Try to break at the last newline in the chunk,
            # but only if the resulting chunk is still substantial.
            last_nl = chunk.rfind("\n")
            if last_nl > 1000:
                remaining = chunk[last_nl + 1:] + remaining
                chunk = chunk[:last_nl]

            chunks.append(chunk)

        return chunks


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
