"""Calendar provider adapter implementations.

These adapters wrap existing services (CalDAVService, ICSService) and
expose them through the uniform :class:`CalendarProvider` protocol.

Adapters are thin wrappers — they delegate to the underlying service
singleton via the route module's ``_get_service()`` function, so that
tests can inject mocks the same way they always have.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from .caldav_models import CalendarEvent, CalendarInfo

logger = logging.getLogger(__name__)


class CalDAVProvider:
    """Calendar provider adapter for CalDAV.

    Delegates to the CalDAVService singleton managed by
    :mod:`app.caldav_routes`.  The provider is always editable when
    registered — CalDAV config guarantees an editable calendar name.
    """

    @property
    def name(self) -> str:
        return "CalDAV"

    @property
    def is_editable(self) -> bool:
        """CalDAV is always editable when configured."""
        return True

    def list_events(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
    ) -> list[CalendarEvent]:
        from .caldav_routes import _get_service
        return _get_service().list_events(start=start, end=end)

    def get_event(self, uid: str) -> CalendarEvent | None:
        from .caldav_routes import _get_service
        return _get_service().get_event(uid)

    def list_calendars(self) -> list[CalendarInfo]:
        from .caldav_routes import _get_service
        return _get_service().list_calendars()


class ICSProvider:
    """Calendar provider adapter for ICS feeds.

    Delegates to the ICSService singleton managed by
    :mod:`app.ics_routes`.  ICS providers are always read-only.
    """

    @property
    def name(self) -> str:
        from .ics_routes import _get_service
        return f"ICS ({_get_service().config.name})"

    @property
    def is_editable(self) -> bool:
        return False

    def list_events(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
    ) -> list[CalendarEvent]:
        from .ics_routes import _get_service
        return _get_service().list_events(start=start, end=end)

    def get_event(self, uid: str) -> CalendarEvent | None:
        from .ics_routes import _get_service
        return _get_service().get_event(uid)

    def list_calendars(self) -> list[CalendarInfo]:
        from .ics_routes import _get_service
        return [_get_service().calendar_info()]
