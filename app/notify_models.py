"""Pydantic models for the unified notify system."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

# Color palette shared across all providers.
# Each color maps to a Discord embed color integer and an emoji
# for providers that use emojis (e.g. Ntfy) instead of colors.
COLOR_MAP: dict[str, tuple[int, str]] = {
    "red":    (0xE50000, "🔴"),
    "orange": (0xF97306, "🟠"),
    "yellow": (0xFFFF14, "🟡"),
    "green":  (0x15B01A, "🟢"),
    "blue":   (0x0343DF, "🔵"),
    "purple": (0x7E1E9C, "🟣"),
    "brown":  (0x653700, "🟤"),
    "black":  (0x000000, "⚫"),
    "white":  (0xFFFFFF, "⚪"),
}

COLOR_NAMES = list(COLOR_MAP.keys())
ColorName = Literal["red", "orange", "yellow", "green", "blue", "purple", "brown", "black", "white"]

# Severity levels (low → high), aligned with PSR-3 / syslog.
# Maps to Ntfy priorities: info→2, notice→3, critical→4, emergency→5.
Level = Literal["info", "notice", "critical", "emergency"]

# Ordered low → high for webhook fallback resolution.
LEVEL_ORDER: list[str] = ["info", "notice", "critical", "emergency"]


class NotifyRequest(BaseModel):
    """Request payload for POST /notify."""

    message: str
    title: str | None = None
    level: Level = "notice"
    color: ColorName | None = None
    channels: list[str] | None = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be empty")
        return v


class NotifyResult(BaseModel):
    """Result from a single provider."""
    provider: str
    success: bool
    error: str | None = None


class NotifyResponse(BaseModel):
    """Aggregated response from all providers."""
    sent: bool
    results: list[NotifyResult]
