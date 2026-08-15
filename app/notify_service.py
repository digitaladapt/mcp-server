"""Unified notify service with provider-based routing.

Providers implement the NotifyProvider protocol.  The service fans out
to all configured providers (or a filtered subset) and collects results.

Discord and Ntfy are built-in providers; others can be added by
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

# Ntfy topic env vars, one per severity level.
_NTFY_TOPIC_ENV_KEYS: dict[str, str] = {
    "info":      "NTFY_INFO_TOPIC",
    "notice":    "NTFY_NOTICE_TOPIC",
    "critical":  "NTFY_CRITICAL_TOPIC",
    "emergency": "NTFY_EMERGENCY_TOPIC",
}

# Map our severity levels to ntfy's 1–5 priority scale.
# https://docs.ntfy.sh/publish/#message-priority
_NTFY_PRIORITY: dict[str, int] = {
    "info":      2,
    "notice":    3,
    "critical":  4,
    "emergency": 5,
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
# Ntfy provider
# --------------------------------------------------------------------------- #

class NtfyProvider:
    """Send notifications to an ntfy server (https://ntfy.sh).

    Each severity level can publish to its own topic, or a single
    topic can be shared by all levels.  Like Discord, if a topic for
    the requested level is not configured, the provider falls back to
    the nearest lower configured level.

    Color is conveyed via ntfy named tags (e.g. "red_circle") which
    ntfy renders as emoji on the client side, rather than an embed
    colour integer, since ntfy has no concept of embed colours.
    """

    def __init__(
        self,
        base_url: str,
        topics: dict[str, str],
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        title_suffix: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._topics: dict[str, str] = dict(topics)
        self._token = token
        self._username = username
        self._password = password
        self._title_suffix = title_suffix

    @classmethod
    def from_env(cls) -> NtfyProvider | None:
        """Build from environment variables.  Returns None if not configured."""
        base_url = os.environ.get("NTFY_URL", "").strip()
        if not base_url:
            return None

        topics: dict[str, str] = {}
        for level, env_key in _NTFY_TOPIC_ENV_KEYS.items():
            topic = os.environ.get(env_key, "").strip()
            if topic:
                topics[level] = topic

        if not topics:
            return None

        token = os.environ.get("NTFY_TOKEN", "").strip() or None
        username = os.environ.get("NTFY_USERNAME", "").strip() or None
        password = os.environ.get("NTFY_PASSWORD", "").strip() or None
        title_suffix = os.environ.get("NTFY_TITLE_SUFFIX", "").strip() or None

        return cls(
            base_url=base_url,
            topics=topics,
            token=token,
            username=username,
            password=password,
            title_suffix=title_suffix,
        )

    @property
    def name(self) -> str:
        return "ntfy"

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._topics)

    def _resolve_topic(self, level: str) -> str | None:
        """Resolve a topic for the given level.

        Falls back to the nearest lower configured level if the
        exact level is not available (same logic as Discord).
        """
        if level in self._topics:
            return self._topics[level]

        idx = LEVEL_ORDER.index(level)
        for candidate in reversed(LEVEL_ORDER[:idx]):
            if candidate in self._topics:
                return self._topics[candidate]

        # Fall back to the lowest configured level.
        for candidate in LEVEL_ORDER:
            if candidate in self._topics:
                return self._topics[candidate]

        return None

    def _build_auth_headers(self) -> dict[str, str]:
        """Build auth headers for the ntfy request."""
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif self._username and self._password is not None:
            import base64
            cred = base64.b64encode(
                f"{self._username}:{self._password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {cred}"
        return headers

    def send(self, req: NotifyRequest) -> NotifyResult:
        """Send notification to ntfy using JSON publishing.

        Uses ntfy's JSON publishing format (POST to the base URL with
        a JSON body) instead of header-based publishing.  This avoids
        encoding issues with non-ASCII characters (emoji, unicode) in
        the Title and Tags headers — Python's HTTP stack enforces
        Latin-1 on header values, which can't represent emoji.
        """
        topic = self._resolve_topic(req.level)
        if not topic:
            return NotifyResult(
                provider=self.name,
                success=False,
                error="No ntfy topic configured",
            )

        # The level we resolved to (may differ from req.level due to
        # fallback) determines the ntfy priority.
        resolved_level = req.level
        if topic != self._topics.get(req.level):
            for lvl, t in self._topics.items():
                if t == topic:
                    resolved_level = lvl
                    break

        # Build the JSON publish payload.
        # See: https://docs.ntfy.sh/publish/#publish-as-json
        payload: dict = {
            "topic": topic,
            "message": req.message,
            "priority": _NTFY_PRIORITY[resolved_level],
        }

        if req.title:
            title = req.title
            if self._title_suffix:
                title = f"{title} {self._title_suffix}"
            payload["title"] = title

        # Color is conveyed as an ntfy named tag (e.g. "red_circle").
        # ntfy renders these as emoji on the client side.
        if req.color:
            payload["tags"] = [COLOR_MAP[req.color][1]]

        headers = self._build_auth_headers()
        headers["Content-Type"] = "application/json"

        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = client.post(self._base_url, json=payload, headers=headers)

                if resp.status_code >= 400:
                    return NotifyResult(
                        provider=self.name,
                        success=False,
                        error=f"ntfy API error {resp.status_code}: {resp.text[:200]}",
                    )

        except httpx.HTTPError as exc:
            return NotifyResult(
                provider=self.name,
                success=False,
                error=f"HTTP error: {exc}",
            )

        return NotifyResult(provider=self.name, success=True)


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
        """Fan out to all providers (or filtered subset).

        The ``"all"`` channel (the default) broadcasts to every
        configured provider.  Any other channel name selects only
        the matching provider.  Invalid names are dropped by
        ``NotifyRequest.normalize_channels`` before reaching here.
        """
        targets = self._providers
        if req.channels and "all" not in req.channels:
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

    ntfy = NtfyProvider.from_env()
    if ntfy is not None:
        providers.append(ntfy)

    return providers


# Module-level singleton
notify_registry = NotifyRegistry()


def reset_notify_registry() -> None:
    """Clear the global notify registry (used in tests)."""
    notify_registry.clear()
    # Reset channel choices so tests start from a clean state.
    NotifyRequest.set_channel_choices([])


def init_notify_registry() -> bool:
    """Discover and register providers.  Returns True if any configured.

    Also updates ``NotifyRequest.set_channel_choices`` so the
    OpenAPI schema exposes a proper enum of valid channel names.
    When more than one provider is configured, ``"all"`` is added
    as an explicit option for broadcasting to every provider.
    """
    reset_notify_registry()
    providers = _discover_providers()
    for p in providers:
        notify_registry.register(p)

    # Update the channel enum for the schema and validator.
    names = notify_registry.provider_names
    if len(names) > 1:
        names = [*names, "all"]
    NotifyRequest.set_channel_choices(names)

    return notify_registry.has_providers
