"""Pydantic models for CalDAV calendar operations.

These models cover events (VEVENT) and tasks (VTODO), supporting
the unified calendar view where some calendars are read-only.

Both events and tasks support VALARM components (calendar
alarms/reminders).  By default, newly created events get a single
DISPLAY alarm at the start time, and tasks with a due date get a
DISPLAY alarm at the due time (noon for date-only values), unless
``enable_alarms`` is set to ``False``.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# --------------------------------------------------------------------------- #
# Alarm / Reminder support (VALARM)
# --------------------------------------------------------------------------- #

class AlarmSpec(BaseModel):
    """Specification for a single calendar alarm (VALARM).

    Alarms are attached to events and trigger reminders.  Multiple
    alarms can be attached to a single event.

    Parameters
    ----------
    trigger_minutes:
        When the alarm fires, relative to ``related_to``.
        Negative = before, 0 = at the time, positive = after.
        e.g. ``-15`` means 15 minutes before the event start.
        Defaults to ``0`` (at event start).
    action:
        The alarm action type.  ``DISPLAY`` shows a message on screen,
        ``AUDIO`` plays a sound.  Defaults to ``DISPLAY``.
    description:
        Optional text shown with the alarm.  Defaults to the event
        summary when not provided.
    related_to:
        Whether the trigger is relative to ``START`` or ``END`` of the
        event.  Defaults to ``START``.
    """

    trigger_minutes: int = 0
    action: str = "DISPLAY"
    description: str | None = None
    related_to: str = "START"

    @field_validator("action")
    @classmethod
    def action_valid(cls, v: str) -> str:
        allowed = {"DISPLAY", "AUDIO"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"action must be one of {allowed}")
        return v_upper

    @field_validator("related_to")
    @classmethod
    def related_to_valid(cls, v: str) -> str:
        allowed = {"START", "END"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"related_to must be one of {allowed}")
        return v_upper

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
    categories: list[str] = []
    status: str | None = None  # TENTATIVE, CONFIRMED, CANCELLED
    priority: int | None = None  # 1 (highest) – 9 (lowest)
    calendar_name: str
    editable: bool
    alarms: list[AlarmSpec] = []


class CreateEventRequest(BaseModel):
    """Payload for creating a new event on the editable calendar.

    By default a single DISPLAY alarm at the event start time is
    added automatically.  Provide ``alarms`` for custom alarms, or
    set ``enable_alarms=False`` to suppress alarms entirely.
    """

    summary: str
    description: str | None = None
    start: str  # ISO 8601 datetime (e.g. "2026-01-15T10:00:00")
    end: str  # ISO 8601 datetime
    location: str | None = None
    all_day: bool = False
    categories: list[str] | None = None
    status: str | None = None  # defaults to CONFIRMED on create
    priority: int | None = None
    alarms: list[AlarmSpec] | None = None
    enable_alarms: bool = True

    @field_validator("summary")
    @classmethod
    def summary_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must not be empty")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"TENTATIVE", "CONFIRMED", "CANCELLED"}
            if v.upper() not in allowed:
                raise ValueError(f"status must be one of {allowed}")
            return v.upper()
        return v

    @field_validator("priority")
    @classmethod
    def priority_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 9):
            raise ValueError("priority must be between 1 and 9")
        return v


class UpdateEventRequest(BaseModel):
    """Payload for updating an existing event.

    All fields are optional — only provided fields are updated.

    Alarm handling:
    - If ``alarms`` is provided (including an empty list), all existing
      alarms are replaced with the new set.
    - If ``alarms`` is ``None`` (omitted), existing alarms are preserved.
    - If ``enable_alarms`` is ``False``, all alarms are removed.
    """

    summary: str | None = None
    description: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    all_day: bool | None = None
    categories: list[str] | None = None
    status: str | None = None
    priority: int | None = None
    alarms: list[AlarmSpec] | None = None
    enable_alarms: bool | None = None

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"TENTATIVE", "CONFIRMED", "CANCELLED"}
            if v.upper() not in allowed:
                raise ValueError(f"status must be one of {allowed}")
            return v.upper()
        return v

    @field_validator("priority")
    @classmethod
    def priority_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 9):
            raise ValueError("priority must be between 1 and 9")
        return v


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
    percent_complete: int | None = None  # 0–100
    categories: list[str] = []
    calendar_name: str
    editable: bool
    alarms: list[AlarmSpec] = []


class CreateTaskRequest(BaseModel):
    """Payload for creating a new task on the editable calendar.

    By default, if a due date is present, a single DISPLAY alarm is
    added at the due time.  For date-only due values (no time), the
    alarm fires at noon.  Provide ``alarms`` for custom alarms, or
    set ``enable_alarms=False`` to suppress alarms entirely.
    """

    summary: str
    description: str | None = None
    due: str | None = None
    priority: int | None = None
    percent_complete: int | None = None
    categories: list[str] | None = None
    alarms: list[AlarmSpec] | None = None
    enable_alarms: bool = True

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

    @field_validator("percent_complete")
    @classmethod
    def percent_complete_range(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("percent_complete must be between 0 and 100")
        return v


class UpdateTaskRequest(BaseModel):
    """Payload for updating an existing task.

    All fields are optional — only provided fields are updated.

    Alarm handling (same semantics as events):
    - If ``alarms`` is provided (including an empty list), all existing
      alarms are replaced with the new set.
    - If ``alarms`` is ``None`` (omitted), existing alarms are preserved.
    - If ``enable_alarms`` is ``False``, all alarms are removed.
    """

    summary: str | None = None
    description: str | None = None
    due: str | None = None
    priority: int | None = None
    status: str | None = None
    percent_complete: int | None = None
    categories: list[str] | None = None
    alarms: list[AlarmSpec] | None = None
    enable_alarms: bool | None = None

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

    @field_validator("percent_complete")
    @classmethod
    def percent_complete_range(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("percent_complete must be between 0 and 100")
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
