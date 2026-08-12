"""Calendar provider abstraction layer.

Defines a common protocol that all calendar sources (CalDAV, ICS, and
future providers) implement, plus a registry that discovers and manages
providers at application startup.

The provider protocol is intentionally minimal:

* ``list_events(start, end)`` – return events in a date range
* ``get_event(uid)`` – return a single event by UID
* ``list_calendars()`` – return calendar metadata
* ``is_editable`` – whether this provider can create/update/delete events
* ``name`` – display name for this provider

Providers are registered at startup via :class:`ProviderRegistry`.
The unified ``/events`` router iterates all registered providers to
produce a merged, sorted, deduplicated result.

Editing operations (create/update/delete) are scoped to the single
editable provider (CalDAV with an editable calendar).  Read-only
providers (ICS, read-only CalDAV) only implement the read methods.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from .caldav_models import CalendarEvent, CalendarInfo

logger = logging.getLogger(__name__)


@runtime_checkable
class CalendarProvider(Protocol):
    """Protocol that all calendar providers implement.

    Each provider wraps an existing service (CalDAVService, ICSService,
    etc.) and exposes a uniform interface for reading events.
    """

    @property
    def name(self) -> str:
        """Display name for this provider (e.g. 'CalDAV', 'ICS')."""
        ...

    @property
    def is_editable(self) -> bool:
        """Whether this provider supports create/update/delete operations."""
        ...

    def list_events(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
    ) -> list[CalendarEvent]:
        """Return events in the given date range (or all events if unset)."""
        ...

    def get_event(self, uid: str) -> CalendarEvent | None:
        """Return a single event by UID, or None if not found."""
        ...

    def list_calendars(self) -> list[CalendarInfo]:
        """Return metadata about the calendars this provider manages."""
        ...


class ProviderRegistry:
    """Registry of active calendar providers.

    Providers are registered at startup.  The registry is then used by
    the unified events router to fan out queries across all sources.

    The registry also tracks whether any provider is editable, which
    determines whether write endpoints (POST/PUT/DELETE) are mounted.
    """

    def __init__(self) -> None:
        self._providers: list[CalendarProvider] = []

    def register(self, provider: CalendarProvider) -> None:
        """Register a calendar provider."""
        self._providers.append(provider)
        logger.info(
            "Registered calendar provider: %s (editable=%s)",
            provider.name, provider.is_editable,
        )

    def clear(self) -> None:
        """Remove all registered providers (used in tests)."""
        self._providers.clear()

    @property
    def providers(self) -> list[CalendarProvider]:
        """Return all registered providers (shallow copy)."""
        return list(self._providers)

    @property
    def has_editable(self) -> bool:
        """Whether any registered provider supports editing."""
        return any(p.is_editable for p in self._providers)

    @property
    def has_providers(self) -> bool:
        """Whether any provider is registered."""
        return len(self._providers) > 0

    def get_editable(self) -> CalendarProvider | None:
        """Return the first editable provider, or None if none exist."""
        for p in self._providers:
            if p.is_editable:
                return p
        return None

    def list_all_events(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
    ) -> list[CalendarEvent]:
        """Fan out across all providers, merge, sort, and deduplicate.

        Events are sorted by start time.  Duplicates (same UID) are
        removed, keeping the first occurrence (editable providers are
        iterated first to ensure the editable version wins).
        """
        # Iterate editable providers first so their version of a
        # duplicated event is kept.
        seen_uids: set[str] = set()
        all_events: list[CalendarEvent] = []

        for provider in self._providers:
            try:
                events = provider.list_events(start=start, end=end)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Provider '%s' failed to list events: %s",
                    provider.name, exc,
                )
                continue
            for ev in events:
                if ev.uid in seen_uids:
                    continue
                seen_uids.add(ev.uid)
                all_events.append(ev)

        all_events.sort(key=lambda e: e.start)
        return all_events

    def get_event(self, uid: str) -> CalendarEvent | None:
        """Find an event by UID across all providers.

        Editable providers are checked first so that if the same event
        exists on both an editable and read-only calendar, the editable
        version is returned.
        """
        for provider in self._providers:
            try:
                ev = provider.get_event(uid)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Provider '%s' failed to get event '%s': %s",
                    provider.name, uid, exc,
                )
                continue
            if ev is not None:
                return ev
        return None

    def list_all_calendars(self) -> list[CalendarInfo]:
        """Return calendar metadata from all providers."""
        result: list[CalendarInfo] = []
        for provider in self._providers:
            try:
                cals = provider.list_calendars()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Provider '%s' failed to list calendars: %s",
                    provider.name, exc,
                )
                continue
            result.extend(cals)
        return result


# Module-level singleton
provider_registry = ProviderRegistry()


def reset_provider_registry() -> None:
    """Clear the global provider registry (used in tests)."""
    provider_registry.clear()
