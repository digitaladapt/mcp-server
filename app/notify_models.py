"""Pydantic models for the unified notify system."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Color palette shared across all providers.
# Each color maps to a Discord embed color integer and an ntfy
# named tag (e.g. "red_circle") that ntfy renders as emoji on
# the client side.
COLOR_MAP: dict[str, tuple[int, str]] = {
    "red":    (0xE50000, "red_circle"),
    "orange": (0xF97306, "orange_circle"),
    "yellow": (0xFFFF14, "yellow_circle"),
    "green":  (0x15B01A, "green_circle"),
    "blue":   (0x0343DF, "blue_circle"),
    "purple": (0x7E1E9C, "purple_circle"),
    "brown":  (0x653700, "brown_circle"),
    "black":  (0x000000, "black_circle"),
    "white":  (0xFFFFFF, "white_circle"),
}

COLOR_NAMES = list(COLOR_MAP.keys())
ColorName = Literal["red", "orange", "yellow", "green", "blue", "purple", "brown", "black", "white"]

# Severity levels (low → high), aligned with PSR-3 / syslog.
# Maps to Ntfy priorities: info→2, notice→3, critical→4, emergency→5.
Level = Literal["info", "notice", "critical", "emergency"]

# Ordered low → high for webhook fallback resolution.
LEVEL_ORDER: list[str] = ["info", "notice", "critical", "emergency"]


# --------------------------------------------------------------------------- #
# Dynamic channel choices (populated by init_notify_registry at startup)
# --------------------------------------------------------------------------- #

_channel_choices: list[str] = []


def _inject_channel_enum(schema: dict) -> None:
    """json_schema_extra callable: inject dynamic channel enum into items."""
    if _channel_choices:
        schema["items"] = {"type": "string", "enum": list(_channel_choices)}


class NotifyRequest(BaseModel):
    """Request payload for POST /notify.

    Invalid ``color`` values are silently ignored (treated as if not
    provided).  Invalid ``level`` values fall back to ``"notice"``.
    Invalid ``channel`` names are dropped; if none remain, the request
    is sent to all providers (``"all"``).
    """

    message: str
    title: str | None = None
    level: Level = "notice"
    color: ColorName | None = None
    channels: list[str] | None = Field(
        default=["all"],
        json_schema_extra=_inject_channel_enum,
    )

    @field_validator("color", mode="before")
    @classmethod
    def normalize_color(cls, v: object) -> str | None:
        """Silently ignore invalid colors (treat as if not provided)."""
        if v is None:
            return None
        if isinstance(v, str) and v in COLOR_MAP:
            return v
        return None

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, v: object) -> str:
        """Fall back to default 'notice' for invalid levels."""
        if isinstance(v, str) and v in LEVEL_ORDER:
            return v
        return "notice"

    @field_validator("channels", mode="before")
    @classmethod
    def normalize_channels(cls, v: object) -> list[str]:
        """Drop invalid channel names; fall back to ['all'] if none remain."""
        if v is None or v == []:
            return ["all"]
        if isinstance(v, list):
            if _channel_choices:
                valid = set(_channel_choices)
                filtered = [ch for ch in v if isinstance(ch, str) and ch in valid]
                return filtered if filtered else ["all"]
            # No choices configured (e.g. in tests before init) — pass through.
            return v
        # Non-list, non-None — fall back to "all".
        return ["all"]

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be empty")
        return v

    @classmethod
    def set_channel_choices(cls, names: list[str]) -> None:
        """Update the valid channel names for schema enum and validation.

        Called by ``init_notify_registry()`` after providers are discovered.
        If more than one provider is configured, ``"all"`` is appended to
        the list so the LLM can explicitly broadcast to every provider.
        """
        _channel_choices.clear()
        _channel_choices.extend(names)


class NotifyResult(BaseModel):
    """Result from a single provider."""
    provider: str
    success: bool
    error: str | None = None


class NotifyResponse(BaseModel):
    """Aggregated response from all providers."""
    sent: bool
    results: list[NotifyResult]
