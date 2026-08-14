"""Tests for ICS recurrence expansion.

These tests verify that the ICSService correctly expands recurring
events (RRULE, RDATE, EXDATE) into individual occurrences using the
``recurring-ical-events`` library, and that composite UIDs allow
individual occurrences to be retrieved via ``get_event``.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.ics_models import ICSConfig
from app.ics_service import ICSService

# --------------------------------------------------------------------------- #
# Sample ICS data with recurring events
# --------------------------------------------------------------------------- #

SAMPLE_ICS_RECURRING = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurring-1@test
SUMMARY:Weekly Friday Meeting
DTSTART:20200522T090000Z
DTEND:20200522T100000Z
DESCRIPTION:Containers with Friends
LOCATION:zoom
RRULE:FREQ=WEEKLY;BYDAY=FR
END:VEVENT
BEGIN:VEVENT
UID:recurring-2@test
SUMMARY:Daily Standup
DTSTART:20260101T090000Z
DTEND:20260101T091500Z
RRULE:FREQ=DAILY;COUNT=10
END:VEVENT
BEGIN:VEVENT
UID:non-recurring-1@test
SUMMARY:One-off Meeting
DTSTART:20260115T140000Z
DTEND:20260115T150000Z
END:VEVENT
END:VCALENDAR
"""

SAMPLE_ICS_RECURRING_WITH_END = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurring-ended@test
SUMMARY:Ended Series
DTSTART:20260101T090000
DTEND:20260101T100000
RRULE:FREQ=DAILY;UNTIL=20260105T090000
END:VEVENT
END:VCALENDAR
"""

SAMPLE_ICS_RECURRING_EXDATE = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurring-exdate@test
SUMMARY:Daily with Exclusion
DTSTART:20260101T090000Z
DTEND:20260101T100000Z
RRULE:FREQ=DAILY;COUNT=5
EXDATE:20260102T090000Z
EXDATE:20260104T090000Z
END:VEVENT
END:VCALENDAR
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

async def _make_service(raw_ics: str, name: str = "Test") -> ICSService:
    """Create an ICSService with the given ICS data already cached."""
    config = ICSConfig(url="https://example.com/cal.ics", name=name)
    svc = ICSService(config)
    with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=raw_ics):
        await svc.refresh()
    return svc


# --------------------------------------------------------------------------- #
# Recurrence expansion tests
# --------------------------------------------------------------------------- #

class TestRecurrenceExpansion:
    """Tests for RRULE expansion in list_events()."""

    @pytest.mark.asyncio
    async def test_weekly_recurring_expanded(self) -> None:
        """A weekly RRULE should produce one event per Friday in range."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        # Aug 10–16 2026 contains Friday Aug 14
        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        friday_events = [e for e in events if e.summary == "Weekly Friday Meeting"]
        assert len(friday_events) == 1
        assert friday_events[0].start.startswith("2026-08-14")

    @pytest.mark.asyncio
    async def test_daily_recurring_with_count(self) -> None:
        """FREQ=DAILY;COUNT=10 should produce exactly 10 occurrences."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 15),
        )
        daily = [e for e in events if e.summary == "Daily Standup"]
        assert len(daily) == 10
        # First occurrence is Jan 1
        assert daily[0].start.startswith("2026-01-01")
        # Last occurrence is Jan 10
        assert daily[-1].start.startswith("2026-01-10")

    @pytest.mark.asyncio
    async def test_non_recurring_event_also_returned(self) -> None:
        """Non-recurring events should appear alongside expanded ones."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
        )
        one_off = [e for e in events if e.summary == "One-off Meeting"]
        assert len(one_off) == 1
        assert one_off[0].uid == "non-recurring-1@test"

    @pytest.mark.asyncio
    async def test_recurring_until(self) -> None:
        """RRULE with UNTIL should stop producing occurrences after that date."""
        svc = await _make_service(SAMPLE_ICS_RECURRING_WITH_END)

        # Jan 1–31 should contain exactly 5 occurrences (Jan 1–5)
        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
        )
        assert len(events) == 5
        assert events[0].start.startswith("2026-01-01")
        assert events[-1].start.startswith("2026-01-05")

    @pytest.mark.asyncio
    async def test_exdate_skips_occurrences(self) -> None:
        """EXDATE should remove specific occurrences from the expansion."""
        svc = await _make_service(SAMPLE_ICS_RECURRING_EXDATE)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 10),
        )
        # COUNT=5, minus 2 EXDATEs = 3 occurrences
        assert len(events) == 3
        dates = [e.start[:10] for e in events]
        assert "2026-01-02" not in dates
        assert "2026-01-04" not in dates
        assert "2026-01-01" in dates
        assert "2026-01-03" in dates
        assert "2026-01-05" in dates

    @pytest.mark.asyncio
    async def test_no_events_outside_range(self) -> None:
        """Recurring events should not appear outside the queried range."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 6, 1),
            end=date(2026, 6, 30),
        )
        # The daily standup only has 10 occurrences in January
        daily = [e for e in events if e.summary == "Daily Standup"]
        assert len(daily) == 0

    @pytest.mark.asyncio
    async def test_events_sorted_by_start(self) -> None:
        """Expanded events should be sorted by start time."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
        )
        starts = [e.start for e in events]
        assert starts == sorted(starts)


class TestCompositeUIDs:
    """Tests for composite UIDs on expanded recurrence instances."""

    @pytest.mark.asyncio
    async def test_expanded_event_has_composite_uid(self) -> None:
        """Expanded occurrences should have UIDs suffixed with __{start_iso}."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        friday = next(e for e in events if e.summary == "Weekly Friday Meeting")
        assert "__" in friday.uid
        assert friday.uid.startswith("recurring-1@test__")

    @pytest.mark.asyncio
    async def test_non_recurring_keeps_original_uid(self) -> None:
        """Non-recurring events should keep their original UID unchanged."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
        )
        one_off = next(e for e in events if e.summary == "One-off Meeting")
        assert one_off.uid == "non-recurring-1@test"
        assert "__" not in one_off.uid

    @pytest.mark.asyncio
    async def test_get_event_by_composite_uid(self) -> None:
        """get_event should find an expanded occurrence by its composite UID."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        friday = next(e for e in events if e.summary == "Weekly Friday Meeting")

        retrieved = svc.get_event(friday.uid)
        assert retrieved is not None
        assert retrieved.summary == "Weekly Friday Meeting"
        assert retrieved.start == friday.start

    @pytest.mark.asyncio
    async def test_get_event_by_original_uid_still_works(self) -> None:
        """get_event should still find master events by their original UID."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        # Non-recurring event
        ev = svc.get_event("non-recurring-1@test")
        assert ev is not None
        assert ev.summary == "One-off Meeting"

    @pytest.mark.asyncio
    async def test_get_event_nonexistent_composite_uid(self) -> None:
        """get_event should return None for a non-existent composite UID."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        result = svc.get_event("recurring-1@test__1999-01-01T00:00:00+00:00")
        assert result is None

    @pytest.mark.asyncio
    async def test_different_occurrences_have_different_uids(self) -> None:
        """Two occurrences of the same recurring event should have different UIDs."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 15),
        )
        daily = [e for e in events if e.summary == "Daily Standup"]
        assert len(daily) >= 2
        uids = {e.uid for e in daily}
        assert len(uids) == len(daily)  # all unique


class TestNoDateRange:
    """Tests for list_events() without a date range (no expansion)."""

    @pytest.mark.asyncio
    async def test_no_range_returns_master_events(self) -> None:
        """Without a date range, master VEVENTs are returned (no expansion)."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events()
        # 3 VEVENTs in the ICS: 2 recurring + 1 non-recurring
        assert len(events) == 3
        uids = {e.uid for e in events}
        assert "recurring-1@test" in uids
        assert "recurring-2@test" in uids
        assert "non-recurring-1@test" in uids

    @pytest.mark.asyncio
    async def test_no_range_no_composite_uids(self) -> None:
        """Without a date range, UIDs should not have __ suffixes."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events()
        for ev in events:
            assert "__" not in ev.uid


class TestExpandedEventFields:
    """Tests that expanded events preserve all event fields correctly."""

    @pytest.mark.asyncio
    async def test_description_preserved(self) -> None:
        """Description should be carried through to expanded occurrences."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        friday = next(e for e in events if e.summary == "Weekly Friday Meeting")
        assert friday.description == "Containers with Friends"

    @pytest.mark.asyncio
    async def test_location_preserved(self) -> None:
        """Location should be carried through to expanded occurrences."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        friday = next(e for e in events if e.summary == "Weekly Friday Meeting")
        assert friday.location == "zoom"

    @pytest.mark.asyncio
    async def test_calendar_name_preserved(self) -> None:
        """Calendar name should be carried through to expanded occurrences."""
        svc = await _make_service(SAMPLE_ICS_RECURRING, name="Work")

        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        friday = next(e for e in events if e.summary == "Weekly Friday Meeting")
        assert friday.calendar_name == "Work"

    @pytest.mark.asyncio
    async def test_editable_is_false(self) -> None:
        """ICS events should always be non-editable."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 8, 10),
            end=date(2026, 8, 16),
        )
        for ev in events:
            assert ev.editable is False

    @pytest.mark.asyncio
    async def test_event_duration_preserved(self) -> None:
        """The duration (end - start) should be the same for all occurrences."""
        svc = await _make_service(SAMPLE_ICS_RECURRING)

        events = svc.list_events(
            start=date(2026, 1, 1),
            end=date(2026, 1, 15),
        )
        daily = [e for e in events if e.summary == "Daily Standup"]
        assert len(daily) >= 2
        # All should have 15-minute duration
        for ev in daily:
            start_dt = datetime.fromisoformat(ev.start)
            end_dt = datetime.fromisoformat(ev.end)
            duration = end_dt - start_dt
            assert duration.total_seconds() == 900  # 15 minutes
