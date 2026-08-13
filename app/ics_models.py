"""Pydantic models for ICS calendar operations.

The ICS calendar is a read-only source that fetches and parses a remote
.ics (iCalendar) feed.  It reuses the existing :class:`CalendarEvent`
model from :mod:`app.caldav_models` so events from both CalDAV and ICS
sources can be presented through a unified API.
"""

from __future__ import annotations

from pydantic import BaseModel

from .caldav_models import CalendarEvent, CalendarInfo


class ICSConfig(BaseModel):
    """Configuration for a single ICS calendar feed.

    Read from environment variables:

      ICS_CALENDAR_URL  – URL of the published .ics feed
      ICS_CALENDAR_NAME – display name for the calendar (default: "ICS")
      ICS_REFRESH_INTERVAL – seconds between cache refreshes (default: 300)
    """

    url: str
    name: str = "ICS"
    refresh_interval: int = 300  # 5 minutes

    @classmethod
    def from_env(cls) -> ICSConfig | None:
        """Build config from environment variables.

        Returns ``None`` if ``ICS_CALENDAR_URL`` is not set (ICS disabled).
        """
        import os

        url = os.environ.get("ICS_CALENDAR_URL", "").strip()
        if not url:
            return None

        name = os.environ.get("ICS_CALENDAR_NAME", "ICS").strip() or "ICS"
        refresh_raw = os.environ.get("ICS_REFRESH_INTERVAL", "300").strip()
        try:
            refresh = int(refresh_raw)
            refresh = max(refresh, 30)  # safety floor
        except ValueError:
            refresh = 300

        return cls(url=url, name=name, refresh_interval=refresh)


class ICSRefreshResult(BaseModel):
    """Result of an ICS cache refresh operation."""

    success: bool
    events_cached: int
    error: str | None = None
    refreshed_at: str  # ISO 8601 timestamp


class ICSCalendarInfo(BaseModel):
    """Metadata about the ICS calendar source."""

    name: str
    url: str
    editable: bool = False
    events_cached: int
    last_refreshed: str | None = None
    last_error: str | None = None


class EventListResponse(BaseModel):
    """Re-exported from caldav_models for convenience."""

    events: list[CalendarEvent]
    total: int


class CalendarListResponse(BaseModel):
    """Re-exported for ICS endpoint responses."""

    calendars: list[CalendarInfo]
    editable_count: int
    readonly_count: int
