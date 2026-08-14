"""ICS calendar service.

Fetches a remote .ics (iCalendar) feed, parses it into
:class:`~app.caldav_models.CalendarEvent` objects, and caches them
in memory for fast read access.  The cache can be refreshed manually
or automatically via the periodic job system.

The service is read-only — it only supports listing and retrieving
events, never creating, updating, or deleting them.

Recurrence expansion
--------------------
ICS feeds from Outlook and similar providers publish a single VEVENT
with an ``RRULE`` for recurring meetings, rather than one VEVENT per
occurrence.  This service uses the `recurring-ical-events`_ library
to expand those rules into individual event instances at query time.

Each expanded occurrence gets a composite UID of the form
``{original_uid}__{start_iso}`` so that individual instances can be
addressed via :meth:`get_event` without colliding with the master
event or other occurrences.

.. _recurring-ical-events: https://pypi.org/project/recurring-ical-events/
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import recurring_ical_events
from icalendar import Calendar as ICalCalendar

from .caldav_models import CalendarEvent, CalendarInfo
from .ics_models import ICSConfig, ICSRefreshResult

logger = logging.getLogger(__name__)


class ICSError(Exception):
    """Raised on ICS fetch or parse failures."""


class ICSService:
    """Service for fetching, parsing, and caching an ICS calendar feed.

    The service holds an in-memory cache of parsed events.  Call
    :meth:`refresh` to (re)download and parse the feed.  All read
    methods (:meth:`list_events`, :meth:`get_event`) operate on the
    cache — they never perform network I/O.
    """

    def __init__(self, config: ICSConfig) -> None:
        self._config = config
        self._events: list[CalendarEvent] = []
        self._calendar: ICalCalendar | None = None
        self._last_refreshed: datetime | None = None
        self._last_error: str | None = None
        self._raw_data: str | None = None

    @property
    def config(self) -> ICSConfig:
        return self._config

    @property
    def events(self) -> list[CalendarEvent]:
        """Return the cached events (shallow copy)."""
        return list(self._events)

    @property
    def last_refreshed(self) -> datetime | None:
        return self._last_refreshed

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------ #
    # Refresh
    # ------------------------------------------------------------------ #

    async def refresh(self) -> ICSRefreshResult:
        """Download the ICS feed and rebuild the in-memory cache.

        Returns an :class:`ICSRefreshResult` describing the outcome.
        On failure the previous cache is preserved and the error is
        recorded in ``last_error``.
        """
        now = datetime.now(UTC)
        try:
            raw = await self._fetch()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.error("Failed to fetch ICS feed '%s': %s", self._config.url, exc)
            return ICSRefreshResult(
                success=False,
                events_cached=len(self._events),
                error=str(exc),
                refreshed_at=now.isoformat(),
            )

        try:
            cal, events = self._parse(raw)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.error("Failed to parse ICS feed: %s", exc)
            return ICSRefreshResult(
                success=False,
                events_cached=len(self._events),
                error=str(exc),
                refreshed_at=now.isoformat(),
            )

        self._raw_data = raw
        self._events = events
        self._calendar = cal
        self._last_refreshed = now
        self._last_error = None
        logger.info(
            "ICS refresh '%s': %d events cached", self._config.name, len(events),
        )
        return ICSRefreshResult(
            success=True,
            events_cached=len(events),
            refreshed_at=now.isoformat(),
        )

    async def _fetch(self) -> str:
        """Download the raw ICS text from the configured URL."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self._config.url)
            resp.raise_for_status()
            return resp.text

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    def _parse(self, raw: str) -> tuple[ICalCalendar, list[CalendarEvent]]:
        """Parse raw ICS text into a Calendar and a list of CalendarEvents.

        Returns a tuple of ``(calendar, events)``.  The calendar object
        is retained so that recurrence expansion can be performed at
        query time via :meth:`list_events`.
        """
        cal = ICalCalendar.from_ical(raw)
        events: list[CalendarEvent] = []
        name = self._config.name

        for component in cal.walk("VEVENT"):
            ev = self._parse_event(component, name)
            if ev is not None:
                events.append(ev)

        events.sort(key=lambda e: e.start)
        return cal, events

    @staticmethod
    def _parse_event(
        component: Any,
        calendar_name: str,
        *,
        is_expanded: bool = False,
    ) -> CalendarEvent | None:
        """Parse a single VEVENT component into a CalendarEvent.

        When *is_expanded* is ``True`` and the component has an ``RRULE``
        (meaning it is a recurring event expanded by
        ``recurring-ical-events``), the UID is suffixed with
        ``__{start_iso}`` to make each occurrence uniquely addressable.
        """
        uid = str(component.get("uid", ""))
        summary = str(component.get("summary", ""))
        if not uid:
            # Some feeds omit UID; generate one from summary + dtstart
            dtstart = component.get("dtstart")
            uid = f"generated-{summary}-{dtstart}"

        dtstart = component.get("dtstart")
        dtend = component.get("dtend")

        description = component.get("description")
        location = component.get("location")

        # When processing expanded events from recurring-ical-events,
        # create a composite UID for recurring instances so each
        # occurrence is uniquely addressable.  We check for RRULE
        # (retained via keep_recurrence_attributes=True) to distinguish
        # recurring from non-recurring events.
        if is_expanded:
            rrule = component.get("rrule")
            if rrule is not None:
                start_iso = ICSService._format_dt(dtstart)
                uid = f"{uid}__{start_iso}"

        return CalendarEvent(
            uid=uid,
            summary=summary,
            description=str(description) if description else None,
            start=ICSService._format_dt(dtstart),
            end=ICSService._format_dt(dtend),
            all_day=ICSService._is_all_day(dtstart),
            location=str(location) if location else None,
            calendar_name=calendar_name,
            editable=False,
        )

    # ------------------------------------------------------------------ #
    # Datetime helpers (mirroring CalDAVService)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_dt(dt_obj: Any) -> str:
        """Format an icalendar date/datetime property as ISO 8601 string."""
        if dt_obj is None:
            return ""
        if hasattr(dt_obj, "dt"):
            dt_obj = dt_obj.dt
        if isinstance(dt_obj, datetime):
            return dt_obj.isoformat()
        if isinstance(dt_obj, date):
            return dt_obj.isoformat()
        return str(dt_obj)

    @staticmethod
    def _is_all_day(dt_obj: Any) -> bool:
        """Determine if an icalendar DTSTART represents an all-day event."""
        if dt_obj is None:
            return False
        if hasattr(dt_obj, "dt"):
            dt_obj = dt_obj.dt
        return isinstance(dt_obj, date) and not isinstance(dt_obj, datetime)

    # ------------------------------------------------------------------ #
    # Read operations (cache-only)
    # ------------------------------------------------------------------ #

    def list_events(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
    ) -> list[CalendarEvent]:
        """List events, optionally filtered by date range.

        When a date range is provided **and** a parsed calendar is
        cached, recurring events are expanded into individual occurrences
        using the ``recurring-ical-events`` library.  Non-recurring
        events pass through unchanged.

        When no date range is provided, the raw cached events (master
        VEVENTs only, no recurrence expansion) are returned.

        When a bare *date* (no time) is passed as ``end``, the entire
        day is included by extending the bound to end-of-day.
        """
        # Without a date range, return the raw cached events.
        if start is None and end is None:
            return list(self._events)

        # With a date range, expand recurring events from the parsed
        # calendar if available.  Fall back to flat filtering if the
        # calendar object is missing (e.g. refresh failed but old
        # events remain).
        if self._calendar is not None:
            return self._list_events_expanded(start, end)
        return self._list_events_flat(start, end)

    def _list_events_expanded(
        self,
        start: date | datetime | None,
        end: date | datetime | None,
    ) -> list[CalendarEvent]:
        """Expand recurring events using ``recurring-ical-events``."""
        # Determine the query bounds for the library.
        # Default to a wide range if one side is missing.
        query_start = self._to_query_dt(start) or datetime(1970, 1, 1)
        query_end = self._to_query_dt(end, end_of_day=True) or datetime(2038, 1, 1)

        name = self._config.name
        try:
            expanded = recurring_ical_events.of(
                self._calendar, keep_recurrence_attributes=True,
            ).between(query_start, query_end)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Recurrence expansion failed, falling back to flat: %s", exc)
            return self._list_events_flat(start, end)

        result: list[CalendarEvent] = []
        for component in expanded:
            ev = self._parse_event(component, name, is_expanded=True)
            if ev is not None:
                result.append(ev)

        result.sort(key=lambda e: e.start)
        return result

    def _list_events_flat(
        self,
        start: date | datetime | None,
        end: date | datetime | None,
    ) -> list[CalendarEvent]:
        """Filter cached events by date range without recurrence expansion."""
        if not self._events:
            return []

        start_cmp = self._normalise_filter(start)
        end_cmp = self._normalise_filter(end, end_of_day=True)

        result = []
        for ev in self._events:
            ev_start = self._parse_iso(ev.start)
            ev_end = self._parse_iso(ev.end) if ev.end else ev_start

            if start_cmp is not None and ev_end is not None and ev_end < start_cmp:
                continue
            if end_cmp is not None and ev_start is not None and ev_start > end_cmp:
                continue
            result.append(ev)

        return result

    @staticmethod
    def _to_query_dt(
        dt: date | datetime | None,
        *,
        end_of_day: bool = False,
    ) -> datetime | None:
        """Convert a date/datetime filter bound for the expansion library.

        Bare dates are expanded to midnight (start-of-day) or
        23:59:59 (end-of-day).  Timezone-aware datetimes are converted
        to UTC.  Returns ``None`` if *dt* is ``None``.
        """
        if dt is None:
            return None
        if isinstance(dt, datetime):
            if dt.tzinfo is not None:
                dt = dt.astimezone(UTC)
            if end_of_day and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                return dt.replace(hour=23, minute=59, second=59)
            return dt
        if end_of_day:
            return datetime.combine(dt, datetime.max.time())
        return datetime.combine(dt, datetime.min.time())

    @staticmethod
    def _normalise_filter(
        dt: date | datetime | None,
        end_of_day: bool = False,
    ) -> datetime | None:
        """Normalise a filter bound to a naive ``datetime`` for comparison.

        Bare dates are expanded to midnight (start-of-day) or
        23:59:59 (end-of-day) depending on *end_of_day*.
        Timezone-aware datetimes are converted to UTC then made naive
        so they can be compared with event datetimes uniformly.
        """
        if dt is None:
            return None
        if isinstance(dt, datetime):
            if dt.tzinfo is not None:
                dt = dt.astimezone(UTC).replace(tzinfo=None)
            if end_of_day and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                return dt.replace(hour=23, minute=59, second=59)
            return dt
        # Bare date → expand.
        if end_of_day:
            return datetime.combine(dt, datetime.max.time())
        return datetime.combine(dt, datetime.min.time())

    @staticmethod
    def _parse_iso(s: str) -> datetime | None:
        """Parse an ISO 8601 string to a naive ``datetime``.

        Bare dates (no time) are expanded to midnight.
        Timezone-aware datetimes are converted to UTC then made naive
        for uniform comparison.
        Returns ``None`` if the string cannot be parsed.
        """
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                d = date.fromisoformat(s)
                return datetime.combine(d, datetime.min.time())
            except ValueError:
                return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt

    def get_event(self, uid: str) -> CalendarEvent | None:
        """Find a single event by UID in the cache.

        Supports both plain UIDs (master events from the flat cache) and
        composite UIDs of the form ``{uid}__{start_iso}`` that are
        produced by recurrence expansion in :meth:`list_events`.
        """
        # Check the flat cache first for an exact match.
        for ev in self._events:
            if ev.uid == uid:
                return ev

        # Handle composite UIDs ({original}__{start_iso}).
        if "__" in uid and self._calendar is not None:
            base_uid, _, start_iso = uid.rpartition("__")
            try:
                occurrence_start = datetime.fromisoformat(start_iso)
            except ValueError:
                return None
            # Expand a narrow window around the occurrence start.
            window_start = occurrence_start - timedelta(minutes=1)
            window_end = occurrence_start + timedelta(minutes=1)
            try:
                expanded = recurring_ical_events.of(
                    self._calendar, keep_recurrence_attributes=True,
                ).between(window_start, window_end)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Recurrence expansion for get_event failed: %s", exc)
                return None
            name = self._config.name
            for component in expanded:
                ev = self._parse_event(component, name, is_expanded=True)
                if ev is not None and ev.uid == uid:
                    return ev

        return None

    def calendar_info(self) -> CalendarInfo:
        """Return CalendarInfo metadata for this ICS source."""
        return CalendarInfo(
            name=self._config.name,
            url=self._config.url,
            editable=False,
        )


