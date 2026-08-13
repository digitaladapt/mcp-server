"""Tests for ICS calendar models, service, and routes.

The ICS service is tested with mocked HTTP responses to avoid requiring
a live ICS feed.  The API endpoint tests verify routing, auth integration,
503 when unconfigured, and proper error handling.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.ics_models import ICSConfig
from app.ics_service import ICSService

# --------------------------------------------------------------------------- #
# Sample ICS data
# --------------------------------------------------------------------------- #

SAMPLE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-1@test
SUMMARY:Team Standup
DTSTART:20260115T090000Z
DTEND:20260115T093000Z
DESCRIPTION:Daily standup
LOCATION:Room A
END:VEVENT
BEGIN:VEVENT
UID:event-2@test
SUMMARY:Quarterly Review
DTSTART:20260120T140000Z
DTEND:20260120T150000Z
END:VEVENT
BEGIN:VEVENT
UID:event-3@test
SUMMARY:All Day Workshop
DTSTART:20260125
DTEND:20260126
END:VEVENT
END:VCALENDAR
"""

SAMPLE_ICS_EMPTY = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
END:VCALENDAR
"""


# --------------------------------------------------------------------------- #
# ICSConfig tests
# --------------------------------------------------------------------------- #

class TestICSConfig:
    """Tests for ICSConfig.from_env()."""

    def test_returns_none_when_url_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)
        assert ICSConfig.from_env() is None

    def test_returns_none_when_url_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "   ")
        assert ICSConfig.from_env() is None

    def test_returns_config_when_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.setenv("ICS_CALENDAR_NAME", "Work")
        monkeypatch.delenv("ICS_REFRESH_INTERVAL", raising=False)

        config = ICSConfig.from_env()
        assert config is not None
        assert config.url == "https://example.com/cal.ics"
        assert config.name == "Work"
        assert config.refresh_interval == 300

    def test_defaults_name_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.setenv("ICS_CALENDAR_NAME", "")
        config = ICSConfig.from_env()
        assert config is not None
        assert config.name == "ICS"

    def test_defaults_name_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.delenv("ICS_CALENDAR_NAME", raising=False)
        config = ICSConfig.from_env()
        assert config is not None
        assert config.name == "ICS"

    def test_custom_refresh_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.setenv("ICS_REFRESH_INTERVAL", "600")
        config = ICSConfig.from_env()
        assert config is not None
        assert config.refresh_interval == 600

    def test_refresh_interval_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.setenv("ICS_REFRESH_INTERVAL", "10")
        config = ICSConfig.from_env()
        assert config is not None
        assert config.refresh_interval == 30

    def test_refresh_interval_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.setenv("ICS_REFRESH_INTERVAL", "not-a-number")
        config = ICSConfig.from_env()
        assert config is not None
        assert config.refresh_interval == 300


# --------------------------------------------------------------------------- #
# ICSService tests
# --------------------------------------------------------------------------- #

class TestICSServiceRefresh:
    """Tests for ICSService.refresh()."""

    @pytest.mark.asyncio
    async def test_refresh_parses_events(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics", name="Work")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            result = await svc.refresh()

        assert result.success is True
        assert result.events_cached == 3
        assert svc.last_error is None
        assert svc.last_refreshed is not None
        assert len(svc.events) == 3

    @pytest.mark.asyncio
    async def test_refresh_handles_fetch_error(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, side_effect=ConnectionError("network down")):
            result = await svc.refresh()

        assert result.success is False
        assert "network down" in result.error
        assert svc.last_error == "network down"
        assert len(svc.events) == 0  # cache stays empty

    @pytest.mark.asyncio
    async def test_refresh_preserves_cache_on_parse_error(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics")
        svc = ICSService(config)

        # First refresh succeeds
        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            await svc.refresh()
        assert len(svc.events) == 3

        # Second refresh fails to parse — old cache should be preserved
        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value="GARBAGE"):
            result = await svc.refresh()

        assert result.success is False
        assert len(svc.events) == 3  # old cache preserved
        assert svc.last_error is not None

    @pytest.mark.asyncio
    async def test_refresh_empty_calendar(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS_EMPTY):
            result = await svc.refresh()

        assert result.success is True
        assert result.events_cached == 0
        assert len(svc.events) == 0


class TestICSServiceParse:
    """Tests for ICS event parsing."""

    @pytest.mark.asyncio
    async def test_event_fields(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics", name="Work")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            await svc.refresh()

        events = svc.events
        assert events[0].uid == "event-1@test"
        assert events[0].summary == "Team Standup"
        assert events[0].description == "Daily standup"
        assert events[0].location == "Room A"
        assert events[0].calendar_name == "Work"
        assert events[0].editable is False
        assert events[0].all_day is False

    @pytest.mark.asyncio
    async def test_all_day_event(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            await svc.refresh()

        all_day = [e for e in svc.events if e.uid == "event-3@test"]
        assert len(all_day) == 1
        assert all_day[0].all_day is True
        assert all_day[0].summary == "All Day Workshop"

    @pytest.mark.asyncio
    async def test_events_sorted_by_start(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            await svc.refresh()

        starts = [e.start for e in svc.events]
        assert starts == sorted(starts)

    @pytest.mark.asyncio
    async def test_optional_fields_are_none(self) -> None:
        config = ICSConfig(url="https://example.com/cal.ics")
        svc = ICSService(config)

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            await svc.refresh()

        review = next(e for e in svc.events if e.uid == "event-2@test")
        assert review.description is None
        assert review.location is None


class TestICSServiceListEvents:
    """Tests for ICSService.list_events() with date filtering."""

    @pytest.mark.asyncio
    async def setup_service(self) -> ICSService:
        config = ICSConfig(url="https://example.com/cal.ics", name="Test")
        svc = ICSService(config)
        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            await svc.refresh()
        return svc

    @pytest.mark.asyncio
    async def test_list_all_events(self) -> None:
        svc = await self.setup_service()
        events = svc.list_events()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_list_events_date_range_filter(self) -> None:
        svc = await self.setup_service()
        # Only events on 2026-01-15
        events = svc.list_events(
            start=date(2026, 1, 15),
            end=date(2026, 1, 15),
        )
        assert len(events) == 1
        assert events[0].summary == "Team Standup"

    @pytest.mark.asyncio
    async def test_list_events_start_filter(self) -> None:
        svc = await self.setup_service()
        events = svc.list_events(start=datetime(2026, 1, 18, tzinfo=UTC))
        # Should include Quarterly Review (Jan 20) and All Day Workshop (Jan 25)
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_list_events_end_filter(self) -> None:
        svc = await self.setup_service()
        events = svc.list_events(end=datetime(2026, 1, 16, tzinfo=UTC))
        # Should include only Team Standup (Jan 15)
        assert len(events) == 1
        assert events[0].summary == "Team Standup"

    @pytest.mark.asyncio
    async def test_list_events_empty_cache(self) -> None:
        svc = ICSService(ICSConfig(url="https://example.com/cal.ics"))
        assert svc.list_events() == []

    @pytest.mark.asyncio
    async def test_get_event_by_uid(self) -> None:
        svc = await self.setup_service()
        ev = svc.get_event("event-2@test")
        assert ev is not None
        assert ev.summary == "Quarterly Review"

    @pytest.mark.asyncio
    async def test_get_event_not_found(self) -> None:
        svc = await self.setup_service()
        assert svc.get_event("nonexistent") is None

    @pytest.mark.asyncio
    async def test_calendar_info(self) -> None:
        svc = await self.setup_service()
        info = svc.calendar_info()
        assert info.name == "Test"
        assert info.url == "https://example.com/cal.ics"
        assert info.editable is False


# --------------------------------------------------------------------------- #
# ICS API endpoint tests
# --------------------------------------------------------------------------- #

class TestICSRoutesUnconfigured:
    """Endpoints return 404 when ICS is not configured (routes don't exist)."""

    def test_calendars_404(self, app_client_no_caldav: TestClient) -> None:
        resp = app_client_no_caldav.get("/ics/calendars")
        assert resp.status_code == 404

    def test_events_404(self, app_client_no_caldav: TestClient) -> None:
        resp = app_client_no_caldav.get("/ics/events")
        assert resp.status_code == 404

    def test_refresh_404(self, app_client_no_caldav: TestClient) -> None:
        resp = app_client_no_caldav.post("/ics/refresh")
        assert resp.status_code == 404

    def test_status_404(self, app_client_no_caldav: TestClient) -> None:
        resp = app_client_no_caldav.get("/ics/status")
        assert resp.status_code == 404


class TestICSRoutesConfigured:
    """Endpoints work when ICS is configured (with mocked service)."""

    @pytest.fixture(autouse=True)
    def _setup_ics(self, monkeypatch: pytest.MonkeyPatch):
        """Configure ICS with a test URL and reset the singleton."""
        from app.ics_routes import _reset_service
        monkeypatch.setenv("ICS_CALENDAR_URL", "https://example.com/cal.ics")
        monkeypatch.setenv("ICS_CALENDAR_NAME", "Work")
        _reset_service()
        yield
        _reset_service()

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        """Build app with ICS configured."""
        from app.main import create_app
        app = create_app()
        with TestClient(app) as c:
            yield c

    def test_calendars_returns_info(self, client: TestClient) -> None:
        resp = client.get("/ics/calendars")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "Work"
        assert data[0]["url"] == "https://example.com/cal.ics"
        assert data[0]["editable"] is False
        assert data[0]["events_cached"] == 0  # not refreshed yet

    def test_status_returns_info(self, client: TestClient) -> None:
        resp = client.get("/ics/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Work"
        assert data["url"] == "https://example.com/cal.ics"
        assert data["events_cached"] == 0
        assert data["last_refreshed"] is None

    def test_refresh_success(self, client: TestClient) -> None:
        # Mock the _fetch method on the service instance
        from app.ics_routes import _get_service
        svc = _get_service()

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            resp = client.post("/ics/refresh")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["events_cached"] == 3
        assert data["error"] is None

    def test_refresh_failure(self, client: TestClient) -> None:
        from app.ics_routes import _get_service
        svc = _get_service()

        with patch.object(svc, "_fetch", new_callable=AsyncMock, side_effect=ConnectionError("down")):
            resp = client.post("/ics/refresh")

        assert resp.status_code == 200  # ICSRefreshResult returned, not 502
        data = resp.json()
        assert data["success"] is False
        assert "down" in data["error"]

    def test_events_empty_before_refresh(self, client: TestClient) -> None:
        resp = client.get("/ics/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["events"] == []

    def test_events_after_refresh(self, client: TestClient) -> None:
        from app.ics_routes import _get_service
        svc = _get_service()

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            client.post("/ics/refresh")

        resp = client.get("/ics/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        summaries = {e["summary"] for e in data["events"]}
        assert "Team Standup" in summaries
        assert "All Day Workshop" in summaries

    def test_events_with_date_filter(self, client: TestClient) -> None:
        from app.ics_routes import _get_service
        svc = _get_service()

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            client.post("/ics/refresh")

        resp = client.get("/ics/events", params={"start": "2026-01-15", "end": "2026-01-15"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["summary"] == "Team Standup"

    def test_get_event_by_uid(self, client: TestClient) -> None:
        from app.ics_routes import _get_service
        svc = _get_service()

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            client.post("/ics/refresh")

        resp = client.get("/ics/events/event-2@test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uid"] == "event-2@test"
        assert data["summary"] == "Quarterly Review"
        assert data["editable"] is False
        assert data["calendar_name"] == "Work"

    def test_get_event_not_found(self, client: TestClient) -> None:
        from app.ics_routes import _get_service
        svc = _get_service()

        with patch.object(svc, "_fetch", new_callable=AsyncMock, return_value=SAMPLE_ICS):
            client.post("/ics/refresh")

        resp = client.get("/ics/events/nonexistent")
        assert resp.status_code == 404

    def test_invalid_date_returns_400(self, client: TestClient) -> None:
        resp = client.get("/ics/events", params={"start": "not-a-date"})
        assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Jobs endpoint tests
# --------------------------------------------------------------------------- #

class TestJobsEndpoint:
    """Tests for the /jobs status endpoint."""

    def test_jobs_returns_200(self, app_client_no_caldav: TestClient) -> None:
        resp = app_client_no_caldav.get("/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
