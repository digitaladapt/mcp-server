"""Tests for the calendar provider registry merge/dedup logic.

The registry is responsible for fusing results from multiple calendar
providers (CalDAV, ICS, ...).  Recurrence expansion can produce many
occurrences sharing one UID (each with a distinct start), so dedup must
key on ``(uid, start)`` rather than ``uid`` alone.
"""

from __future__ import annotations

from app.caldav_models import CalendarEvent
from app.providers import ProviderRegistry


class _FakeProvider:
    """Minimal CalendarProvider that returns a fixed event list."""

    def __init__(self, name: str, events: list[CalendarEvent], *, editable: bool = False) -> None:
        self.name = name
        self._events = events
        self.is_editable = editable

    def list_events(self, start=None, end=None) -> list[CalendarEvent]:
        return list(self._events)

    def get_event(self, uid: str) -> CalendarEvent | None:
        return None

    def list_calendars(self) -> list:
        return []


def _ev(uid: str, start: str, summary: str = "Event") -> CalendarEvent:
    return CalendarEvent(
        uid=uid,
        summary=summary,
        start=start,
        end="2026-09-11T10:00:00",
        calendar_name="Work",
        editable=False,
    )


class TestProviderRegistryDedup:
    def test_recurring_occurrences_all_kept(self) -> None:
        """Multiple occurrences of one recurring series (same UID, different
        starts) must all survive the merge."""
        reg = ProviderRegistry()
        reg.register(_FakeProvider("CalDAV", [
            _ev("series-1@test", "2026-09-04T09:00:00"),
            _ev("series-1@test", "2026-09-11T09:00:00"),
            _ev("series-1@test", "2026-09-18T09:00:00"),
            _ev("series-1@test", "2026-09-25T09:00:00"),
        ]))

        events = reg.list_all_events()
        assert len(events) == 4
        assert [e.start[:10] for e in events] == [
            "2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25",
        ]

    def test_exact_duplicate_across_providers_removed(self) -> None:
        """The same event (same UID and start) mirrored by two providers
        must be deduplicated, keeping the first (editable) version."""
        editable = _ev("dup-1@test", "2026-09-11T09:00:00", summary="Editable version")
        readable = _ev("dup-1@test", "2026-09-11T09:00:00", summary="Readable version")

        reg = ProviderRegistry()
        reg.register(_FakeProvider("CalDAV", [editable], editable=True))
        reg.register(_FakeProvider("ICS", [readable]))

        events = reg.list_all_events()
        assert len(events) == 1
        assert events[0].summary == "Editable version"

    def test_same_uid_different_start_not_deduped(self) -> None:
        """An occurrence at a different time is NOT a duplicate even if the
        UID matches (recurrence instances)."""
        reg = ProviderRegistry()
        reg.register(_FakeProvider("CalDAV", [
            _ev("series-1@test", "2026-09-04T09:00:00"),
            _ev("series-1@test", "2026-09-11T09:00:00"),
        ]))

        events = reg.list_all_events()
        assert len(events) == 2

    def test_sort_by_start(self) -> None:
        reg = ProviderRegistry()
        reg.register(_FakeProvider("CalDAV", [
            _ev("b@test", "2026-09-11T10:00:00"),
            _ev("a@test", "2026-09-04T09:00:00"),
        ]))

        events = reg.list_all_events()
        assert [e.uid for e in events] == ["a@test", "b@test"]
