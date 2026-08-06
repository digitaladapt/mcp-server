"""Pydantic models for CalDAV calendar operations.

These models cover events (VEVENT) and tasks (VTODO), supporting
the unified calendar view where some calendars are read-only.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# --------------------------------------------------------------------------- #
# Calendar metadata
# --------------------------------------------------------------------------- #

class CalendarInfo(BaseModel):
    """Information about a single calendar."""

    name: str
    url: str
    editable: bool
    description: str | None = None


# --------------------------------------------------------------------------- #
# Events (VEVENT)
# --------------------------------------------------------------------------- #

class CalendarEvent(BaseModel):
    """A single calendar event, possibly from a read-only calendar."""

    uid: str
    summary: str
    description: str | None = None
    start: str  # ISO 8601 datetime or date string
    end: str  # ISO 8601 datetime or date string
    all_day: bool = False
    location: str | None = None
    calendar_name: str
    editable: bool


class CreateEventRequest(BaseModel):
    """Payload for creating a new event on the editable calendar."""

    summary: str
    description: str | None = None
    start: str  # ISO 8601 datetime (e.g. "2026-01-15T10:00:00")
    end: str  # ISO 8601 datetime
    location: str | None = None
    all_day: bool = False

    @field_validator("summary")
    @classmethod
    def summary_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must not be empty")
        return v


class UpdateEventRequest(BaseModel):
    """Payload for updating an existing event.

    All fields are optional — only provided fields are updated.
    """

    summary: str | None = None
    description: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    all_day: bool | None = None


# --------------------------------------------------------------------------- #
# Tasks (VTODO)
# --------------------------------------------------------------------------- #

class CalendarTask(BaseModel):
    """A single calendar task (VTODO)."""

    uid: str
    summary: str
    description: str | None = None
    due: str | None = None  # ISO 8601
    priority: int | None = None  # 1 (highest) – 9 (lowest)
    status: str | None = None  # NEEDS-ACTION, IN-PROCESS, COMPLETED, CANCELLED
    calendar_name: str
    editable: bool


class CreateTaskRequest(BaseModel):
    """Payload for creating a new task on the editable calendar."""

    summary: str
    description: str | None = None
    due: str | None = None
    priority: int | None = None

    @field_validator("summary")
    @classmethod
    def summary_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must not be empty")
        return v

    @field_validator("priority")
    @classmethod
    def priority_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 9):
            raise ValueError("priority must be between 1 and 9")
        return v


class UpdateTaskRequest(BaseModel):
    """Payload for updating an existing task.

    All fields are optional — only provided fields are updated.
    """

    summary: str | None = None
    description: str | None = None
    due: str | None = None
    priority: int | None = None
    status: str | None = None

    @field_validator("priority")
    @classmethod
    def priority_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 9):
            raise ValueError("priority must be between 1 and 9")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"}
            if v.upper() not in allowed:
                raise ValueError(f"status must be one of {allowed}")
            return v.upper()
        return v


# --------------------------------------------------------------------------- #
# Response wrappers
# --------------------------------------------------------------------------- #

class CalendarListResponse(BaseModel):
    """Response for listing calendars."""

    calendars: list[CalendarInfo]
    editable_count: int
    readonly_count: int


class EventListResponse(BaseModel):
    """Response for listing events."""

    events: list[CalendarEvent]
    total: int


class TaskListResponse(BaseModel):
    """Response for listing tasks."""

    tasks: list[CalendarTask]
    total: int


class DeleteResponse(BaseModel):
    """Response for delete operations."""

    deleted: bool
    uid: str


class CalDAVConfig(BaseModel):
    """Configuration for CalDAV connections."""

    url: str
    username: str
    password: str
    editable_calendar: str
    readonly_calendars: list[str] = []  # empty = all others are read-only

    @classmethod
    def from_env(cls) -> CalDAVConfig | None:
        """Build config from environment variables.

        Returns None if CALDAV_URL is not set (CalDAV disabled).
        """
        import os

        url = os.environ.get("CALDAV_URL", "").strip()
        if not url:
            return None

        username = os.environ.get("CALDAV_USERNAME", "")
        password = os.environ.get("CALDAV_PASSWORD", "")
        editable = os.environ.get("CALDAV_EDITABLE_CALENDAR", "Lyra")
        readonly_raw = os.environ.get("CALDAV_READONLY_CALENDARS", "")
        readonly = [
            name.strip() for name in readonly_raw.split(",") if name.strip()
        ]

        return cls(
            url=url,
            username=username,
            password=password,
            editable_calendar=editable,
            readonly_calendars=readonly,
        )
