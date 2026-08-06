"""CalDAV service layer.

Provides read/write access to CalDAV calendars with the concept of one
editable calendar and multiple read-only calendars.  All calendars are
presented through a unified interface where write operations are only
allowed on the editable calendar.

Configuration is read from environment variables via
:class:`~app.caldav_models.CalDAVConfig`:

  CALDAV_URL              – CalDAV server URL
  CALDAV_USERNAME         – username
  CALDAV_PASSWORD         – password
  CALDAV_EDITABLE_CALENDAR – name of the writable calendar (default: Lyra)
  CALDAV_READONLY_CALENDARS – comma-separated names of read-only calendars
                              (empty = all calendars except the editable one)
"""

from __future__ import annotations

import functools
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import caldav
from icalendar import Calendar as ICalCalendar
from icalendar import Event as ICalEvent
from icalendar import Todo as ICalTodo

from .caldav_models import (
    CalDAVConfig,
    CalendarEvent,
    CalendarInfo,
    CalendarTask,
    CreateEventRequest,
    CreateTaskRequest,
    UpdateEventRequest,
    UpdateTaskRequest,
)

logger = logging.getLogger(__name__)


class CalDAVError(Exception):
    """Raised on CalDAV operation failures."""


# --------------------------------------------------------------------------- #
# Connection-recovery decorator
# --------------------------------------------------------------------------- #

def _with_connection_recovery(method):
    """Decorator: if a CalDAV call fails with a connection error, reset the
    cached connection/calendar list and retry once.

    This guards against stale connections when the CalDAV server restarts
    or the network hiccups.
    """

    @functools.wraps(method)
    def wrapper(self: CalDAVService, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except caldav.lib.error.DAVError as exc:
            logger.warning(
                "DAVError in %s: %s — resetting connection and retrying",
                method.__name__, exc,
            )
            self._reset_connection()
            return method(self, *args, **kwargs)

    return wrapper


class CalDAVService:
    """Service class managing CalDAV calendar operations.

    Holds a connection to the CalDAV server and provides methods for
    listing, creating, updating, and deleting events and tasks across
    one editable and multiple read-only calendars.
    """

    def __init__(self, config: CalDAVConfig) -> None:
        self._config = config
        self._client: caldav.DAVClient | None = None
        self._principal: caldav.Principal | None = None
        self._calendars_cache: list[caldav.Calendar] | None = None

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> CalDAVConfig:
        return self._config

    def _connect(self) -> caldav.Principal:
        """Connect to the CalDAV server and return the principal."""
        if self._principal is not None:
            return self._principal
        self._client = caldav.DAVClient(
            url=self._config.url,
            username=self._config.username,
            password=self._config.password,
        )
        self._principal = self._client.principal()
        return self._principal

    def _reset_connection(self) -> None:
        """Drop cached connection and calendar list.

        Called by :func:`_with_connection_recovery` when a DAVError
        suggests the connection is stale.
        """
        self._principal = None
        self._client = None
        self._calendars_cache = None

    # ------------------------------------------------------------------ #
    # Calendar discovery (with caching)
    # ------------------------------------------------------------------ #

    def _get_all_calendars(self) -> list[caldav.Calendar]:
        """Return all calendars on the server, cached after first fetch."""
        if self._calendars_cache is not None:
            return self._calendars_cache
        principal = self._connect()
        self._calendars_cache = principal.calendars()
        return self._calendars_cache

    def _get_calendar(self, name: str) -> caldav.Calendar | None:
        """Find a calendar by display name (uses cached list)."""
        for cal in self._get_all_calendars():
            cal_name = self._get_cal_name(cal)
            if cal_name == name:
                return cal
        return None

    @staticmethod
    def _get_cal_name(cal: caldav.Calendar) -> str:
        """Get a calendar's display name, handling caldav deprecation."""
        try:
            name = cal.get_display_name()
            return str(name) if name else ""
        except Exception:  # noqa: BLE001
            return ""

    def _is_editable(self, calendar_name: str) -> bool:
        """Check if a calendar is editable."""
        return calendar_name == self._config.editable_calendar

    def _get_target_calendars(self) -> list[caldav.Calendar]:
        """Return the list of :class:`caldav.Calendar` objects to query.

        If ``readonly_calendars`` is empty, all calendars are included
        (the editable one + all others as read-only).
        Otherwise only the explicitly named read-only calendars plus the
        editable one are returned.
        """
        all_cals = self._get_all_calendars()

        if self._config.readonly_calendars:
            target_names = set(self._config.readonly_calendars)
            target_names.add(self._config.editable_calendar)
            return [
                cal for cal in all_cals
                if self._get_cal_name(cal) in target_names
            ]
        return all_cals

    def _get_target_calendar_names(self) -> list[str]:
        """Return the names of target calendars (for backward compat)."""
        cals = self._get_target_calendars()
        return [self._get_cal_name(cal) for cal in cals]

    # ------------------------------------------------------------------ #
    # Calendar listing
    # ------------------------------------------------------------------ #

    @_with_connection_recovery
    def list_calendars(self) -> list[CalendarInfo]:
        """List all accessible calendars with their editability."""
        result: list[CalendarInfo] = []
        for cal in self._get_target_calendars():
            name = self._get_cal_name(cal)
            if not name:
                continue
            result.append(CalendarInfo(
                name=name,
                url=str(cal.url),
                editable=self._is_editable(name),
            ))
        return result

    # ------------------------------------------------------------------ #
    # Events (VEVENT)
    # ------------------------------------------------------------------ #

    @_with_connection_recovery
    def list_events(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
    ) -> list[CalendarEvent]:
        """List events across all target calendars in a date range."""
        if start is None:
            start = datetime.now(UTC)
        if end is None:
            end = start + timedelta(days=30)
        # Coerce string inputs (e.g. from direct API calls)
        if isinstance(start, str):
            start = self._parse_dt(start)
        if isinstance(end, str):
            end = self._parse_dt(end)

        events: list[CalendarEvent] = []
        for cal in self._get_target_calendars():
            name = self._get_cal_name(cal)
            if not name:
                continue
            editable = self._is_editable(name)

            try:
                found = cal.search(
                    start=start,
                    end=end,
                    event=True,
                    expand=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to search events in '%s': %s", name, exc)
                continue

            for obj in found:
                try:
                    ev = self._parse_event(obj, name, editable)
                    if ev:
                        events.append(ev)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to parse event in '%s': %s", name, exc)

        events.sort(key=lambda e: e.start)
        return events

    def _parse_event(
        self,
        caldav_obj: Any,
        calendar_name: str,
        editable: bool,
    ) -> CalendarEvent | None:
        """Parse a CalDAV object into a CalendarEvent."""
        ev = self._extract_component(caldav_obj, "VEVENT")
        if ev is None:
            return None

        dtstart = ev.get("dtstart")
        dtend = ev.get("dtend")

        return CalendarEvent(
            uid=str(ev.get("uid", "")),
            summary=str(ev.get("summary", "")),
            description=str(ev.get("description")) if ev.get("description") else None,
            start=self._format_dt(dtstart),
            end=self._format_dt(dtend),
            all_day=self._is_all_day(dtstart),
            location=str(ev.get("location")) if ev.get("location") else None,
            calendar_name=calendar_name,
            editable=editable,
        )

    @_with_connection_recovery
    def get_event(self, uid: str) -> CalendarEvent | None:
        """Find a single event by UID across all calendars."""
        for cal in self._get_target_calendars():
            name = self._get_cal_name(cal)
            if not name:
                continue
            editable = self._is_editable(name)
            try:
                found = cal.search(
                    comp_class=caldav.Event,
                    uid=uid,
                )
            except Exception:  # noqa: BLE001
                # Fallback: search all events and filter by UID
                try:
                    found = cal.search(event=True, expand=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to search events in '%s': %s", name, exc)
                    continue

            for obj in found:
                ev = self._parse_event(obj, name, editable)
                if ev and ev.uid == uid:
                    return ev
        return None

    @_with_connection_recovery
    def create_event(self, req: CreateEventRequest) -> CalendarEvent:
        """Create a new event on the editable calendar."""
        cal = self._get_calendar(self._config.editable_calendar)
        if cal is None:
            raise CalDAVError(
                f"Editable calendar '{self._config.editable_calendar}' not found"
            )

        uid = str(uuid.uuid4())
        now = datetime.now(UTC)
        ical = ICalCalendar()
        event = ICalEvent()
        event.add("uid", uid)
        event.add("dtstamp", now)
        event.add("created", now)
        event.add("last-modified", now)

        start_dt = self._parse_dt(req.start)
        end_dt = self._parse_dt(req.end)

        if req.all_day:
            event.add("dtstart", self._to_date(start_dt))
            event.add("dtend", self._to_date(end_dt))
        else:
            event.add("dtstart", start_dt)
            event.add("dtend", end_dt)

        event.add("summary", req.summary)
        if req.description:
            event.add("description", req.description)
        if req.location:
            event.add("location", req.location)

        ical.add_component(event)

        try:
            cal.save_event(ical.to_ical().decode("utf-8"))
        except Exception as exc:
            raise CalDAVError(f"Failed to create event: {exc}") from exc

        return CalendarEvent(
            uid=uid,
            summary=req.summary,
            description=req.description,
            start=req.start,
            end=req.end,
            all_day=req.all_day,
            location=req.location,
            calendar_name=self._config.editable_calendar,
            editable=True,
        )

    @_with_connection_recovery
    def update_event(self, uid: str, req: UpdateEventRequest) -> CalendarEvent:
        """Update an existing event on the editable calendar."""
        cal = self._get_calendar(self._config.editable_calendar)
        if cal is None:
            raise CalDAVError(
                f"Editable calendar '{self._config.editable_calendar}' not found"
            )

        target_obj = self._find_by_uid(cal, uid, "VEVENT")
        if target_obj is None:
            raise CalDAVError(f"Event with UID '{uid}' not found")

        ical_cal = ICalCalendar.from_ical(target_obj.data)
        ev_component = self._find_component_in_calendar(ical_cal, "VEVENT", uid)
        if ev_component is None:
            raise CalDAVError(f"Could not find VEVENT component for UID '{uid}'")

        # Determine whether the event should end up all-day or timed.
        # If req.all_day is explicitly set, use it.  Otherwise infer from
        # the current dtstart or from newly-provided start values.
        current_dtstart = ev_component.get("dtstart")
        currently_all_day = self._is_all_day(current_dtstart)

        if req.all_day is not None:
            target_all_day = req.all_day
        elif req.start is not None:
            target_all_day = self._is_all_day(self._parse_dt(req.start))
        else:
            target_all_day = currently_all_day

        # Apply field updates
        if req.summary is not None:
            ev_component.pop("summary", None)
            ev_component.add("summary", req.summary)
        if req.description is not None:
            ev_component.pop("description", None)
            ev_component.add("description", req.description)
        if req.location is not None:
            ev_component.pop("location", None)
            ev_component.add("location", req.location)

        # Handle dtstart
        if req.start is not None:
            new_start = self._parse_dt(req.start)
            ev_component.pop("dtstart", None)
            ev_component.add("dtstart", self._to_date(new_start) if target_all_day else new_start)
        elif req.all_day is not None and target_all_day != currently_all_day:
            # Convert existing dtstart between date and datetime
            if current_dtstart is not None:
                start_val = self._unwrap_dt(current_dtstart)
                ev_component.pop("dtstart", None)
                ev_component.add("dtstart", self._to_date(start_val) if target_all_day else self._to_datetime(start_val))

        # Handle dtend
        if req.end is not None:
            new_end = self._parse_dt(req.end)
            ev_component.pop("dtend", None)
            ev_component.add("dtend", self._to_date(new_end) if target_all_day else new_end)
        elif req.all_day is not None and target_all_day != currently_all_day:
            current_dtend = ev_component.get("dtend")
            if current_dtend is not None:
                end_val = self._unwrap_dt(current_dtend)
                ev_component.pop("dtend", None)
                ev_component.add("dtend", self._to_date(end_val) if target_all_day else self._to_datetime(end_val))

        # Update DTSTAMP and LAST-MODIFIED
        now = datetime.now(UTC)
        ev_component.pop("dtstamp", None)
        ev_component.add("dtstamp", now)
        ev_component.pop("last-modified", None)
        ev_component.add("last-modified", now)

        # Save
        new_data = ical_cal.to_ical().decode("utf-8")
        target_obj.data = new_data
        try:
            target_obj.save()
        except Exception as exc:
            raise CalDAVError(f"Failed to save event: {exc}") from exc

        result = self._parse_event(target_obj, self._config.editable_calendar, True)
        if result is not None:
            return result
        # Fallback if parsing somehow fails
        return CalendarEvent(
            uid=uid,
            summary=req.summary or "",
            description=req.description,
            start=req.start or "",
            end=req.end or "",
            all_day=target_all_day,
            location=req.location,
            calendar_name=self._config.editable_calendar,
            editable=True,
        )

    @_with_connection_recovery
    def delete_event(self, uid: str) -> bool:
        """Delete an event from the editable calendar."""
        cal = self._get_calendar(self._config.editable_calendar)
        if cal is None:
            raise CalDAVError(
                f"Editable calendar '{self._config.editable_calendar}' not found"
            )

        target_obj = self._find_by_uid(cal, uid, "VEVENT")
        if target_obj is None:
            return False

        try:
            target_obj.delete()
            return True
        except Exception as exc:
            raise CalDAVError(f"Failed to delete event: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Tasks (VTODO)
    # ------------------------------------------------------------------ #

    @_with_connection_recovery
    def list_tasks(self) -> list[CalendarTask]:
        """List tasks across all target calendars."""
        tasks: list[CalendarTask] = []
        for cal in self._get_target_calendars():
            name = self._get_cal_name(cal)
            if not name:
                continue
            editable = self._is_editable(name)

            try:
                found = cal.search(todo=True, include_completed=True)
            except Exception:  # noqa: BLE001
                try:
                    found = cal.search(todo=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to search tasks in '%s': %s", name, exc)
                    continue

            for obj in found:
                try:
                    task = self._parse_task(obj, name, editable)
                    if task:
                        tasks.append(task)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to parse task in '%s': %s", name, exc)

        tasks.sort(key=lambda t: t.due or t.summary)
        return tasks

    def _parse_task(
        self,
        caldav_obj: Any,
        calendar_name: str,
        editable: bool,
    ) -> CalendarTask | None:
        """Parse a CalDAV object into a CalendarTask."""
        todo = self._extract_component(caldav_obj, "VTODO")
        if todo is None:
            return None

        priority = todo.get("priority")
        priority_val = int(priority) if priority else None

        return CalendarTask(
            uid=str(todo.get("uid", "")),
            summary=str(todo.get("summary", "")),
            description=str(todo.get("description")) if todo.get("description") else None,
            due=self._format_dt(todo.get("due")),
            priority=priority_val,
            status=str(todo.get("status")) if todo.get("status") else None,
            calendar_name=calendar_name,
            editable=editable,
        )

    @_with_connection_recovery
    def get_task(self, uid: str) -> CalendarTask | None:
        """Find a single task by UID across all calendars."""
        for cal in self._get_target_calendars():
            name = self._get_cal_name(cal)
            if not name:
                continue
            editable = self._is_editable(name)
            try:
                found = cal.search(
                    comp_class=caldav.Todo,
                    uid=uid,
                )
            except Exception:  # noqa: BLE001
                try:
                    found = cal.search(todo=True, include_completed=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to search tasks in '%s': %s", name, exc)
                    continue

            for obj in found:
                task = self._parse_task(obj, name, editable)
                if task and task.uid == uid:
                    return task
        return None

    @_with_connection_recovery
    def create_task(self, req: CreateTaskRequest) -> CalendarTask:
        """Create a new task on the editable calendar."""
        cal = self._get_calendar(self._config.editable_calendar)
        if cal is None:
            raise CalDAVError(
                f"Editable calendar '{self._config.editable_calendar}' not found"
            )

        uid = str(uuid.uuid4())
        now = datetime.now(UTC)
        ical = ICalCalendar()
        todo = ICalTodo()
        todo.add("uid", uid)
        todo.add("dtstamp", now)
        todo.add("created", now)
        todo.add("last-modified", now)
        todo.add("status", "NEEDS-ACTION")

        todo.add("summary", req.summary)
        if req.description:
            todo.add("description", req.description)
        if req.due:
            todo.add("due", self._parse_dt(req.due))
        if req.priority is not None:
            todo.add("priority", req.priority)

        ical.add_component(todo)

        try:
            cal.save_todo(ical.to_ical().decode("utf-8"))
        except Exception as exc:
            raise CalDAVError(f"Failed to create task: {exc}") from exc

        return CalendarTask(
            uid=uid,
            summary=req.summary,
            description=req.description,
            due=req.due,
            priority=req.priority,
            status="NEEDS-ACTION",
            calendar_name=self._config.editable_calendar,
            editable=True,
        )

    @_with_connection_recovery
    def update_task(self, uid: str, req: UpdateTaskRequest) -> CalendarTask:
        """Update an existing task on the editable calendar."""
        cal = self._get_calendar(self._config.editable_calendar)
        if cal is None:
            raise CalDAVError(
                f"Editable calendar '{self._config.editable_calendar}' not found"
            )

        target_obj = self._find_by_uid(cal, uid, "VTODO")
        if target_obj is None:
            raise CalDAVError(f"Task with UID '{uid}' not found")

        ical_cal = ICalCalendar.from_ical(target_obj.data)
        todo_component = self._find_component_in_calendar(ical_cal, "VTODO", uid)
        if todo_component is None:
            raise CalDAVError(f"Could not find VTODO component for UID '{uid}'")

        if req.summary is not None:
            todo_component.pop("summary", None)
            todo_component.add("summary", req.summary)
        if req.description is not None:
            todo_component.pop("description", None)
            todo_component.add("description", req.description)
        if req.due is not None:
            todo_component.pop("due", None)
            todo_component.add("due", self._parse_dt(req.due))
        if req.priority is not None:
            todo_component.pop("priority", None)
            todo_component.add("priority", req.priority)
        if req.status is not None:
            todo_component.pop("status", None)
            todo_component.add("status", req.status)

        # Update DTSTAMP and LAST-MODIFIED
        now = datetime.now(UTC)
        todo_component.pop("dtstamp", None)
        todo_component.add("dtstamp", now)
        todo_component.pop("last-modified", None)
        todo_component.add("last-modified", now)

        new_data = ical_cal.to_ical().decode("utf-8")
        target_obj.data = new_data
        try:
            target_obj.save()
        except Exception as exc:
            raise CalDAVError(f"Failed to save task: {exc}") from exc

        result = self._parse_task(target_obj, self._config.editable_calendar, True)
        if result is not None:
            return result
        return CalendarTask(
            uid=uid,
            summary=req.summary or "",
            description=req.description,
            due=req.due,
            priority=req.priority,
            status=req.status,
            calendar_name=self._config.editable_calendar,
            editable=True,
        )

    @_with_connection_recovery
    def delete_task(self, uid: str) -> bool:
        """Delete a task from the editable calendar."""
        cal = self._get_calendar(self._config.editable_calendar)
        if cal is None:
            raise CalDAVError(
                f"Editable calendar '{self._config.editable_calendar}' not found"
            )

        target_obj = self._find_by_uid(cal, uid, "VTODO")
        if target_obj is None:
            return False

        try:
            target_obj.delete()
            return True
        except Exception as exc:
            raise CalDAVError(f"Failed to delete task: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Shared parsing helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_component(caldav_obj: Any, component_name: str) -> Any:
        """Extract a single VEVENT / VTODO component from a caldav object.

        Tries the ``icalendar_component`` property first, then falls back
        to parsing raw iCal data.  Returns the component or ``None``.
        """
        # Fast path: caldav >=1.3 exposes icalendar_component
        ical_data = getattr(caldav_obj, "icalendar_component", None)
        if ical_data is not None:
            if hasattr(ical_data, "walk"):
                components = list(ical_data.walk(component_name))
                if components:
                    return components[0]
            return ical_data

        # Fallback: parse raw iCal data
        raw = getattr(caldav_obj, "data", None)
        if raw is None:
            return None
        cal = ICalCalendar.from_ical(raw)
        components = list(cal.walk(component_name))
        return components[0] if components else None

    @staticmethod
    def _find_by_uid(
        cal: caldav.Calendar,
        uid: str,
        component_name: str,
    ) -> Any:
        """Find a caldav object by UID in a calendar.

        ``component_name`` is ``"VEVENT"`` or ``"VTODO"``.
        """
        comp_class = (
            caldav.Event if component_name == "VEVENT"
            else caldav.Todo
        )
        search_kwargs: dict[str, Any] = {"comp_class": comp_class, "uid": uid}
        try:
            found = cal.search(**search_kwargs)
        except Exception:  # noqa: BLE001
            if component_name == "VEVENT":
                found = cal.search(event=True, expand=True)
            else:
                try:
                    found = cal.search(todo=True, include_completed=True)
                except Exception:  # noqa: BLE001
                    found = cal.search(todo=True)

        for obj in found:
            comp = CalDAVService._extract_component(obj, component_name)
            if comp is not None and str(comp.get("uid", "")) == uid:
                return obj
        return None

    @staticmethod
    def _find_component_in_calendar(
        ical_cal: ICalCalendar,
        component_name: str,
        uid: str,
    ) -> Any:
        """Find a component by UID inside an already-parsed ICalCalendar."""
        for comp in ical_cal.walk(component_name):
            if str(comp.get("uid", "")) == uid:
                return comp
        return None

    # ------------------------------------------------------------------ #
    # Datetime helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_dt(value: str) -> datetime | date:
        """Parse an ISO 8601 string into a datetime or date."""
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                raise CalDAVError(f"Could not parse date/time: {value}")

    @staticmethod
    def _format_dt(dt_obj: Any) -> str:
        """Format an icalendar date/datetime property as ISO 8601 string."""
        if dt_obj is None:
            return ""
        # icalendar properties often have a .dt attribute
        if hasattr(dt_obj, "dt"):
            dt_obj = dt_obj.dt
        if isinstance(dt_obj, datetime):
            return dt_obj.isoformat()
        if isinstance(dt_obj, date):
            return dt_obj.isoformat()
        return str(dt_obj)

    @staticmethod
    def _unwrap_dt(dt_obj: Any) -> datetime | date:
        """Unwrap an icalendar property to a bare datetime/date."""
        if hasattr(dt_obj, "dt"):
            dt_obj = dt_obj.dt
        if isinstance(dt_obj, datetime):
            return dt_obj
        if isinstance(dt_obj, date):
            return dt_obj
        raise CalDAVError(f"Unexpected datetime type: {type(dt_obj)}")

    @staticmethod
    def _is_all_day(dt_obj: Any) -> bool:
        """Determine if an icalendar DTSTART/DTEND represents an all-day event.

        All-day events use a bare ``date`` value (no time component).
        Timed events use a ``datetime`` value.  Since ``datetime`` is a
        subclass of ``date``, we check explicitly.
        """
        if dt_obj is None:
            return False
        if hasattr(dt_obj, "dt"):
            dt_obj = dt_obj.dt
        # datetime is a subclass of date, so check datetime first
        return isinstance(dt_obj, date) and not isinstance(dt_obj, datetime)

    @staticmethod
    def _to_date(val: datetime | date) -> date:
        """Coerce a datetime or date to a bare date (for all-day events)."""
        if isinstance(val, datetime):
            return val.date()
        return val

    @staticmethod
    def _to_datetime(val: datetime | date) -> datetime:
        """Coerce a date to a datetime at midnight (for timed events)."""
        if isinstance(val, datetime):
            return val
        return datetime.combine(val, datetime.min.time())
