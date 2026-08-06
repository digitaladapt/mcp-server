"""Tests for CalDAV calendar models and endpoints.

The CalDAV service is tested with mock objects to avoid requiring a live
CalDAV server.  The API endpoint tests verify routing, auth integration,
503 when unconfigured, and proper error handling.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.caldav_models import (
    CalDAVConfig,
    CalendarEvent,
    CalendarInfo,
    CalendarTask,
    CreateEventRequest,
    CreateTaskRequest,
)

# --------------------------------------------------------------------------- #
# Model tests
# --------------------------------------------------------------------------- #

class TestCalDAVConfig:
    """Tests for CalDAVConfig.from_env()."""

    def test_returns_none_when_url_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CALDAV_URL", raising=False)
        assert CalDAVConfig.from_env() is None

    def test_returns_none_when_url_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_URL", "  ")
        assert CalDAVConfig.from_env() is None

    def test_returns_config_when_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")
        monkeypatch.setenv("CALDAV_EDITABLE_CALENDAR", "Lyra")
        monkeypatch.delenv("CALDAV_READONLY_CALENDARS", raising=False)

        config = CalDAVConfig.from_env()
        assert config is not None
        assert config.url == "https://caldav.example.com"
        assert config.username == "user"
        assert config.password == "pass"
        assert config.editable_calendar == "Lyra"
        assert config.readonly_calendars == []

    def test_parses_readonly_calendars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")
        monkeypatch.setenv("CALDAV_EDITABLE_CALENDAR", "Lyra")
        monkeypatch.setenv("CALDAV_READONLY_CALENDARS", "Personal, Work , Team")

        config = CalDAVConfig.from_env()
        assert config is not None
        assert config.readonly_calendars == ["Personal", "Work", "Team"]

    def test_defaults_editable_calendar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.delenv("CALDAV_USERNAME", raising=False)
        monkeypatch.delenv("CALDAV_PASSWORD", raising=False)
        monkeypatch.delenv("CALDAV_EDITABLE_CALENDAR", raising=False)

        config = CalDAVConfig.from_env()
        assert config is not None
        assert config.editable_calendar == "Lyra"
        assert config.username == ""
        assert config.password == ""


class TestCreateEventRequest:
    """Tests for CreateEventRequest validation."""

    def test_valid_event(self) -> None:
        req = CreateEventRequest(
            summary="Meeting",
            start="2026-01-15T10:00:00",
            end="2026-01-15T11:00:00",
        )
        assert req.summary == "Meeting"
        assert req.all_day is False

    def test_empty_summary_rejected(self) -> None:
        with pytest.raises(ValueError, match="summary must not be empty"):
            CreateEventRequest(
                summary="  ",
                start="2026-01-15T10:00:00",
                end="2026-01-15T11:00:00",
            )


class TestCreateTaskRequest:
    """Tests for CreateTaskRequest validation."""

    def test_valid_task(self) -> None:
        req = CreateTaskRequest(summary="Buy milk", priority=5)
        assert req.summary == "Buy milk"
        assert req.priority == 5

    def test_empty_summary_rejected(self) -> None:
        with pytest.raises(ValueError, match="summary must not be empty"):
            CreateTaskRequest(summary="")

    def test_priority_too_low(self) -> None:
        with pytest.raises(ValueError, match="priority must be between 1 and 9"):
            CreateTaskRequest(summary="test", priority=0)

    def test_priority_too_high(self) -> None:
        with pytest.raises(ValueError, match="priority must be between 1 and 9"):
            CreateTaskRequest(summary="test", priority=10)

    def test_priority_none_allowed(self) -> None:
        req = CreateTaskRequest(summary="test")
        assert req.priority is None


class TestUpdateTaskRequest:
    """Tests for UpdateTaskRequest validation."""

    def test_status_uppercased(self) -> None:
        type("R", (), {"status": "completed"})
        # Direct test of the validator
        from app.caldav_models import UpdateTaskRequest as UTR
        req2 = UTR(status="completed")
        assert req2.status == "COMPLETED"

    def test_invalid_status_rejected(self) -> None:
        from app.caldav_models import UpdateTaskRequest as UTR
        with pytest.raises(ValueError, match="status must be one of"):
            UTR(status="BANANA")

    def test_priority_range(self) -> None:
        from app.caldav_models import UpdateTaskRequest as UTR
        with pytest.raises(ValueError, match="priority must be between 1 and 9"):
            UTR(priority=15)


# --------------------------------------------------------------------------- #
# Model serialization
# --------------------------------------------------------------------------- #

class TestModelSerialization:
    """Tests for model serialization."""

    def test_calendar_info(self) -> None:
        info = CalendarInfo(name="Lyra", url="https://cal.example.com/lyra", editable=True)
        assert info.name == "Lyra"
        assert info.editable is True

    def test_calendar_event(self) -> None:
        ev = CalendarEvent(
            uid="abc-123",
            summary="Test Event",
            start="2026-01-15T10:00:00",
            end="2026-01-15T11:00:00",
            all_day=False,
            calendar_name="Lyra",
            editable=True,
        )
        assert ev.uid == "abc-123"
        assert ev.editable is True

    def test_calendar_task(self) -> None:
        task = CalendarTask(
            uid="todo-1",
            summary="Do thing",
            calendar_name="Lyra",
            editable=True,
        )
        assert task.uid == "todo-1"
        assert task.priority is None
        assert task.status is None


# --------------------------------------------------------------------------- #
# API endpoint tests (unconfigured = 503)
# --------------------------------------------------------------------------- #

class TestCalDAVUnconfigured:
    """When CALDAV_URL is not set, calendar endpoints return 503."""

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        """A TestClient with CalDAV unconfigured."""
        monkeypatch.delenv("CALDAV_URL", raising=False)
        # Reset the service singleton
        from app.caldav_routes import _reset_service
        _reset_service()

        from app.main import app
        with TestClient(app) as c:
            yield c

    def test_list_calendars_503(self, client: TestClient) -> None:
        resp = client.get("/calendars")
        assert resp.status_code == 503

    def test_list_events_503(self, client: TestClient) -> None:
        resp = client.get("/events")
        assert resp.status_code == 503

    def test_list_tasks_503(self, client: TestClient) -> None:
        resp = client.get("/tasks")
        assert resp.status_code == 503

    def test_create_event_503(self, client: TestClient) -> None:
        resp = client.post("/events", json={
            "summary": "Test",
            "start": "2026-01-15T10:00:00",
            "end": "2026-01-15T11:00:00",
        })
        assert resp.status_code == 503

    def test_create_task_503(self, client: TestClient) -> None:
        resp = client.post("/tasks", json={"summary": "Test task"})
        assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# API endpoint tests with auth enabled
# --------------------------------------------------------------------------- #

class TestCalDAVAuthIntegration:
    """CalDAV endpoints should respect API key auth."""

    @pytest.fixture
    def auth_client(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> TestClient:
        """A TestClient with MCP_API_KEY set and CalDAV unconfigured."""
        monkeypatch.setenv("MCP_API_KEY", "secret")
        monkeypatch.delenv("CALDAV_URL", raising=False)

        # Reload modules to pick up env
        import importlib
        for mod in ["app.auth", "app.main", "app.caldav_routes"]:
            if mod in importlib.sys.modules:
                del importlib.sys.modules[mod]

        from app.caldav_routes import _reset_service
        _reset_service()

        from app.main import app
        with TestClient(app) as c:
            yield c

        # Cleanup
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        for mod in ["app.auth", "app.main", "app.caldav_routes"]:
            if mod in importlib.sys.modules:
                del importlib.sys.modules[mod]
        import importlib

        import app.auth
        importlib.reload(app.auth)
        import app.caldav_routes
        importlib.reload(app.caldav_routes)
        import app.main
        importlib.reload(app.main)

    def test_calendars_requires_auth(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/calendars")
        assert resp.status_code == 401

    def test_calendars_with_key_still_503(self, auth_client: TestClient) -> None:
        """Even with auth, CalDAV is 503 if unconfigured."""
        resp = auth_client.get("/calendars", headers={"X-API-Key": "secret"})
        assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Service tests with mocks
# --------------------------------------------------------------------------- #

class TestCalDAVServiceMocked:
    """Unit tests for CalDAVService using mocked CalDAV connections."""

    @pytest.fixture
    def config(self) -> CalDAVConfig:
        return CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
            readonly_calendars=["Work"],
        )

    @pytest.fixture
    def mock_caldav(self, config: CalDAVConfig):
        """Patch caldav.DAVClient to return a mock principal."""
        with patch("app.caldav_service.caldav.DAVClient") as MockClient:
            mock_client = MagicMock()
            mock_principal = MagicMock()
            mock_client.principal.return_value = mock_principal
            MockClient.return_value = mock_client

            type("S", (), {})  # placeholder
            from app.caldav_service import CalDAVService
            service = CalDAVService(config)

            # We need to patch the _connect method
            service._principal = mock_principal
            service._client = mock_client

            yield service, mock_principal

    def test_list_calendars(self, mock_caldav) -> None:
        service, mock_principal = mock_caldav

        # Mock two calendars
        mock_cal_lyra = MagicMock()
        mock_cal_lyra.get_display_name.return_value = "Lyra"
        mock_cal_lyra.url = "https://caldav.example.com/lyra"

        mock_cal_work = MagicMock()
        mock_cal_work.get_display_name.return_value = "Work"
        mock_cal_work.url = "https://caldav.example.com/work"

        mock_principal.calendars.return_value = [mock_cal_lyra, mock_cal_work]

        cals = service.list_calendars()
        assert len(cals) == 2

        lyra = next(c for c in cals if c.name == "Lyra")
        assert lyra.editable is True

        work = next(c for c in cals if c.name == "Work")
        assert work.editable is False

    def test_is_editable(self, mock_caldav) -> None:
        service, _ = mock_caldav
        assert service._is_editable("Lyra") is True
        assert service._is_editable("Work") is False
        assert service._is_editable("Other") is False

    def test_get_target_calendar_names_all(self) -> None:
        """When readonly_calendars is empty, all calendars are included."""
        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
            readonly_calendars=[],
        )
        from app.caldav_service import CalDAVService
        service = CalDAVService(config)

        with patch.object(service, "_get_all_calendars") as mock_get:
            mock_cal1 = MagicMock()
            mock_cal1.get_display_name.return_value = "Lyra"
            mock_cal2 = MagicMock()
            mock_cal2.get_display_name.return_value = "Work"
            mock_cal3 = MagicMock()
            mock_cal3.get_display_name.return_value = "Personal"
            mock_get.return_value = [mock_cal1, mock_cal2, mock_cal3]

            names = service._get_target_calendar_names()
            assert set(names) == {"Lyra", "Work", "Personal"}

    def test_get_target_calendar_names_filtered(self) -> None:
        """When readonly_calendars is set, only those + editable are included."""
        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
            readonly_calendars=["Work"],
        )
        from app.caldav_service import CalDAVService
        service = CalDAVService(config)

        with patch.object(service, "_get_all_calendars") as mock_get:
            mock_cal1 = MagicMock()
            mock_cal1.get_display_name.return_value = "Lyra"
            mock_cal2 = MagicMock()
            mock_cal2.get_display_name.return_value = "Work"
            mock_cal3 = MagicMock()
            mock_cal3.get_display_name.return_value = "Personal"
            mock_get.return_value = [mock_cal1, mock_cal2, mock_cal3]

            names = service._get_target_calendar_names()
            assert set(names) == {"Lyra", "Work"}
            assert "Personal" not in names

    def test_parse_dt_iso_datetime(self) -> None:
        from app.caldav_service import CalDAVService
        result = CalDAVService._parse_dt("2026-01-15T10:30:00")
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.hour == 10

    def test_parse_dt_iso_date(self) -> None:
        # datetime.fromisoformat parses "2026-01-15" as midnight datetime,
        # so this returns a datetime, not a bare date.
        from app.caldav_service import CalDAVService
        result = CalDAVService._parse_dt("2026-01-15")
        assert isinstance(result, (date, datetime))
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_parse_dt_invalid(self) -> None:
        from app.caldav_service import CalDAVError, CalDAVService
        with pytest.raises(CalDAVError, match="Could not parse"):
            CalDAVService._parse_dt("not-a-date")

    def test_format_dt_none(self) -> None:
        from app.caldav_service import CalDAVService
        assert CalDAVService._format_dt(None) == ""

    def test_format_dt_datetime(self) -> None:
        from app.caldav_service import CalDAVService
        dt = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
        result = CalDAVService._format_dt(dt)
        assert "2026-01-15" in result
        assert "10:30" in result

    def test_format_dt_with_dt_attr(self) -> None:
        """icalendar properties have a .dt attribute."""
        from app.caldav_service import CalDAVService

        class MockProp:
            dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

        result = CalDAVService._format_dt(MockProp())
        assert "2026-01-15" in result


# --------------------------------------------------------------------------- #
# API endpoint tests with mocked service
# --------------------------------------------------------------------------- #

class TestCalDAVEndpointsMocked:
    """Test API endpoints with a mocked CalDAVService."""

    @pytest.fixture
    def mocked_client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        """A TestClient with a mocked CalDAV service."""
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")
        monkeypatch.setenv("CALDAV_EDITABLE_CALENDAR", "Lyra")

        # Reset service
        from app.caldav_routes import _reset_service
        _reset_service()

        from app.main import app
        with TestClient(app) as c:
            yield c

        _reset_service()

    def test_list_calendars_mocked(self, mocked_client: TestClient) -> None:
        """Test /calendars with a mocked service."""
        from app.caldav_models import CalDAVConfig, CalendarInfo
        from app.caldav_service import CalDAVService

        # Create a mock service
        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.list_calendars = MagicMock(return_value=[
            CalendarInfo(name="Lyra", url="https://cal.example.com/lyra", editable=True),
            CalendarInfo(name="Work", url="https://cal.example.com/work", editable=False),
        ])

        # Inject the mock service
        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.get("/calendars")
        assert resp.status_code == 200
        data = resp.json()
        assert data["editable_count"] == 1
        assert data["readonly_count"] == 1
        assert len(data["calendars"]) == 2

    def test_list_events_mocked(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig, CalendarEvent
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.list_events = MagicMock(return_value=[
            CalendarEvent(
                uid="evt-1",
                summary="Test Meeting",
                start="2026-01-15T10:00:00",
                end="2026-01-15T11:00:00",
                all_day=False,
                calendar_name="Lyra",
                editable=True,
            ),
        ])

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.get("/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["summary"] == "Test Meeting"

    def test_list_tasks_mocked(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig, CalendarTask
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.list_tasks = MagicMock(return_value=[
            CalendarTask(
                uid="task-1",
                summary="Buy groceries",
                priority=3,
                status="NEEDS-ACTION",
                calendar_name="Lyra",
                editable=True,
            ),
        ])

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.get("/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["summary"] == "Buy groceries"

    def test_create_event_validation(self, mocked_client: TestClient) -> None:
        """Empty summary should return 422."""
        resp = mocked_client.post("/events", json={
            "summary": "",
            "start": "2026-01-15T10:00:00",
            "end": "2026-01-15T11:00:00",
        })
        assert resp.status_code == 422

    def test_create_task_validation(self, mocked_client: TestClient) -> None:
        """Invalid priority should return 422."""
        resp = mocked_client.post("/tasks", json={
            "summary": "test",
            "priority": 15,
        })
        assert resp.status_code == 422

    def test_get_event_not_found(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.get_event = MagicMock(return_value=None)

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.get("/events/nonexistent")
        assert resp.status_code == 404

    def test_delete_event_not_found(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.delete_event = MagicMock(return_value=False)

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.delete("/events/nonexistent")
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# New tests for hardening fixes
# --------------------------------------------------------------------------- #

class TestAllDayDetection:
    """Tests for the fixed _is_all_day and _parse_event all_day logic."""

    def test_is_all_day_none(self) -> None:
        from app.caldav_service import CalDAVService
        assert CalDAVService._is_all_day(None) is False

    def test_is_all_day_bare_date(self) -> None:
        from app.caldav_service import CalDAVService
        d = date(2026, 1, 15)
        assert CalDAVService._is_all_day(d) is True

    def test_is_all_day_datetime_is_false(self) -> None:
        from app.caldav_service import CalDAVService
        dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        assert CalDAVService._is_all_day(dt) is False

    def test_is_all_day_with_dt_wrapper(self) -> None:
        """icalendar wraps values in a vDDDLists-like object with .dt."""
        from app.caldav_service import CalDAVService

        class MockProp:
            dt = date(2026, 1, 15)

        assert CalDAVService._is_all_day(MockProp()) is True

    def test_is_all_day_datetime_with_dt_wrapper(self) -> None:
        from app.caldav_service import CalDAVService

        class MockProp:
            dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

        assert CalDAVService._is_all_day(MockProp()) is False

    def test_parse_event_all_day_true(self) -> None:
        """An event with a bare date dtstart should be all_day=True."""
        from app.caldav_service import CalDAVService

        mock_obj = MagicMock()
        mock_obj.icalendar_component = MagicMock()
        # Simulate a VEVENT with date (not datetime) values
        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent
        ev = ICalEvent()
        ev.add("uid", "test-all-day")
        ev.add("summary", "All Day Thing")
        ev.add("dtstart", date(2026, 1, 15))
        ev.add("dtend", date(2026, 1, 16))
        cal = ICalCalendar()
        cal.add_component(ev)
        mock_obj.icalendar_component = cal

        result = CalDAVService._parse_event(
            None,  # type: ignore[arg-type]
            "Lyra", True,
            _caldav_obj_override=mock_obj,  # type: ignore[call-arg]
        ) if hasattr(CalDAVService._parse_event, '__wrapped__') else None
        # _parse_event is an instance method, so we need an instance
        from app.caldav_models import CalDAVConfig
        svc = CalDAVService(CalDAVConfig(
            url="https://ex.com", username="u", password="p",
            editable_calendar="Lyra",
        ))
        result = svc._parse_event(mock_obj, "Lyra", True)
        assert result is not None
        assert result.all_day is True
        assert result.start == "2026-01-15"

    def test_parse_event_timed_all_day_false(self) -> None:
        """An event with datetime dtstart should be all_day=False."""
        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent

        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        ev = ICalEvent()
        ev.add("uid", "test-timed")
        ev.add("summary", "Timed Meeting")
        ev.add("dtstart", datetime(2026, 1, 15, 10, 0, tzinfo=UTC))
        ev.add("dtend", datetime(2026, 1, 15, 11, 0, tzinfo=UTC))
        cal = ICalCalendar()
        cal.add_component(ev)

        mock_obj = MagicMock()
        mock_obj.icalendar_component = cal

        svc = CalDAVService(CalDAVConfig(
            url="https://ex.com", username="u", password="p",
            editable_calendar="Lyra",
        ))
        result = svc._parse_event(mock_obj, "Lyra", True)
        assert result is not None
        assert result.all_day is False


class TestUUIDGeneration:
    """Tests that create_event and create_task generate explicit UUIDs."""

    @pytest.fixture
    def mock_service(self) -> tuple:
        """Return (service, mock_calendar) for testing create methods."""
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)

        mock_cal = MagicMock()
        mock_cal.get_display_name.return_value = "Lyra"
        mock_cal.url = "https://caldav.example.com/lyra"
        mock_cal.save_event = MagicMock()
        mock_cal.save_todo = MagicMock()

        # Patch _get_all_calendars to return our mock
        service._calendars_cache = [mock_cal]

        return service, mock_cal

    def test_create_event_generates_uuid(self, mock_service) -> None:
        service, mock_cal = mock_service
        from app.caldav_models import CreateEventRequest

        req = CreateEventRequest(
            summary="Test Event",
            start="2026-01-15T10:00:00",
            end="2026-01-15T11:00:00",
        )
        result = service.create_event(req)

        # UUID should be a valid uuid4 string
        assert result.uid
        UUID(result.uid)  # raises ValueError if invalid

        # save_event should have been called with iCal data containing the UID
        mock_cal.save_event.assert_called_once()
        saved_data = mock_cal.save_event.call_args[0][0]
        assert result.uid in saved_data

    def test_create_task_generates_uuid(self, mock_service) -> None:
        service, mock_cal = mock_service
        from app.caldav_models import CreateTaskRequest

        req = CreateTaskRequest(summary="Test Task")
        result = service.create_task(req)

        assert result.uid
        UUID(result.uid)

        mock_cal.save_todo.assert_called_once()
        saved_data = mock_cal.save_todo.call_args[0][0]
        assert result.uid in saved_data

    def test_create_event_all_day_uuid(self, mock_service) -> None:
        """All-day events should also get a UUID and use date values."""
        service, mock_cal = mock_service
        from app.caldav_models import CreateEventRequest

        req = CreateEventRequest(
            summary="All Day",
            start="2026-01-15",
            end="2026-01-16",
            all_day=True,
        )
        result = service.create_event(req)

        assert result.uid
        UUID(result.uid)
        assert result.all_day is True

        saved_data = mock_cal.save_event.call_args[0][0]
        # All-day events use VALUE=DATE in the iCal output
        assert "VALUE=DATE" in saved_data or ";VALUE=DATE" in saved_data


class TestGetTaskEndpoint:
    """Tests for the new GET /tasks/{uid} endpoint."""

    @pytest.fixture
    def mocked_client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")
        monkeypatch.setenv("CALDAV_EDITABLE_CALENDAR", "Lyra")

        from app.caldav_routes import _reset_service
        _reset_service()

        from app.main import app
        with TestClient(app) as c:
            yield c

        _reset_service()

    def test_get_task_found(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig, CalendarTask
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.get_task = MagicMock(return_value=CalendarTask(
            uid="task-123",
            summary="Do something",
            status="NEEDS-ACTION",
            calendar_name="Lyra",
            editable=True,
        ))

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.get("/tasks/task-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uid"] == "task-123"
        assert data["summary"] == "Do something"

    def test_get_task_not_found(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.get_task = MagicMock(return_value=None)

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.get("/tasks/nonexistent")
        assert resp.status_code == 404

    def test_get_task_503_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CALDAV_URL", raising=False)
        from app.caldav_routes import _reset_service
        _reset_service()

        from app.main import app
        with TestClient(app) as c:
            resp = c.get("/tasks/some-uid")
        assert resp.status_code == 503


class TestCalendarCaching:
    """Tests that the calendar list is cached to avoid N+1 server fetches."""

    def test_calendar_list_cached(self) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)

        mock_cal = MagicMock()
        mock_cal.get_display_name.return_value = "Lyra"
        mock_cal.url = "https://caldav.example.com/lyra"

        with patch.object(service, "_connect") as mock_connect:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [mock_cal]
            mock_connect.return_value = mock_principal

            # First call — should fetch from server
            cals1 = service._get_all_calendars()
            assert len(cals1) == 1
            assert mock_principal.calendars.call_count == 1

            # Second call — should use cache, not re-fetch
            cals2 = service._get_all_calendars()
            assert len(cals2) == 1
            assert mock_principal.calendars.call_count == 1  # unchanged

    def test_reset_connection_clears_cache(self) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)

        mock_cal = MagicMock()
        mock_cal.get_display_name.return_value = "Lyra"
        mock_cal.url = "https://caldav.example.com/lyra"

        with patch.object(service, "_connect") as mock_connect:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [mock_cal]
            mock_connect.return_value = mock_principal

            service._get_all_calendars()
            assert service._calendars_cache is not None

            service._reset_connection()
            assert service._calendars_cache is None
            assert service._principal is None
            assert service._client is None

    def test_get_target_calendars_uses_cache(self) -> None:
        """list_calendars should not trigger multiple principal.calendars() calls."""
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
            readonly_calendars=[],
        )
        service = CalDAVService(config)

        mock_lyra = MagicMock()
        mock_lyra.get_display_name.return_value = "Lyra"
        mock_lyra.url = "https://caldav.example.com/lyra"
        mock_work = MagicMock()
        mock_work.get_display_name.return_value = "Work"
        mock_work.url = "https://caldav.example.com/work"

        with patch.object(service, "_connect") as mock_connect:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [mock_lyra, mock_work]
            mock_connect.return_value = mock_principal

            cals = service.list_calendars()
            assert len(cals) == 2
            # Only one server fetch for the entire list_calendars call
            assert mock_principal.calendars.call_count == 1


class TestConnectionRecovery:
    """Tests for the _with_connection_recovery decorator."""

    def test_recovery_retries_once(self) -> None:
        import app.caldav_service as svc_module
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )

        mock_lyra = MagicMock()
        mock_lyra.get_display_name.return_value = "Lyra"
        mock_lyra.url = "https://caldav.example.com/lyra"

        service2 = CalDAVService(config)
        service2._calendars_cache = None

        # list_calendars calls _get_target_calendars which calls _get_all_calendars
        # We'll patch _get_all_calendars to raise once then succeed
        attempts2 = 0

        def mock_get_all_2():
            nonlocal attempts2
            attempts2 += 1
            if attempts2 == 1:
                raise svc_module.caldav.lib.error.DAVError("stale")
            return [mock_lyra]

        service2._get_all_calendars = mock_get_all_2
        reset_called = False

        def tracking_reset():
            nonlocal reset_called
            reset_called = True
            # Clear cache so the retry actually re-fetches
            service2._calendars_cache = None

        service2._reset_connection = tracking_reset

        # Since list_calendars is decorated, it will catch the DAVError from
        # _get_target_calendars -> _get_all_calendars, reset, and retry
        result = service2.list_calendars()
        assert reset_called is True
        assert len(result) == 1
        assert result[0].name == "Lyra"

    def test_recovery_does_not_retry_non_dav_error(self) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)

        mock_lyra = MagicMock()
        mock_lyra.get_display_name.return_value = "Lyra"
        mock_lyra.url = "https://caldav.example.com/lyra"

        attempts = 0

        def mock_get_all():
            nonlocal attempts
            attempts += 1
            raise ValueError("not a DAV error")

        service._get_all_calendars = mock_get_all
        service._reset_connection = MagicMock()

        with pytest.raises(ValueError, match="not a DAV error"):
            service.list_calendars()
        assert attempts == 1  # no retry
        service._reset_connection.assert_not_called()


class TestExtractComponent:
    """Tests for the _extract_component shared helper."""

    def test_extracts_vevent_from_icalendar_component(self) -> None:
        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent

        from app.caldav_service import CalDAVService

        ev = ICalEvent()
        ev.add("uid", "evt-1")
        ev.add("summary", "Test")
        cal = ICalCalendar()
        cal.add_component(ev)

        mock_obj = MagicMock()
        mock_obj.icalendar_component = cal

        result = CalDAVService._extract_component(mock_obj, "VEVENT")
        assert result is not None
        assert str(result.get("uid")) == "evt-1"

    def test_extracts_vtodo_from_icalendar_component(self) -> None:
        from icalendar import Calendar as ICalCalendar
        from icalendar import Todo as ICalTodo

        from app.caldav_service import CalDAVService

        todo = ICalTodo()
        todo.add("uid", "todo-1")
        todo.add("summary", "Task")
        cal = ICalCalendar()
        cal.add_component(todo)

        mock_obj = MagicMock()
        mock_obj.icalendar_component = cal

        result = CalDAVService._extract_component(mock_obj, "VTODO")
        assert result is not None
        assert str(result.get("uid")) == "todo-1"

    def test_fallback_to_raw_data(self) -> None:
        """When icalendar_component is None, parse from .data."""
        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent

        from app.caldav_service import CalDAVService

        ev = ICalEvent()
        ev.add("uid", "evt-fallback")
        ev.add("summary", "Fallback")
        cal = ICalCalendar()
        cal.add_component(ev)
        raw_ical = cal.to_ical().decode("utf-8")

        mock_obj = MagicMock()
        mock_obj.icalendar_component = None
        mock_obj.data = raw_ical

        result = CalDAVService._extract_component(mock_obj, "VEVENT")
        assert result is not None
        assert str(result.get("uid")) == "evt-fallback"

    def test_returns_none_when_no_data(self) -> None:
        from app.caldav_service import CalDAVService

        mock_obj = MagicMock()
        mock_obj.icalendar_component = None
        mock_obj.data = None

        result = CalDAVService._extract_component(mock_obj, "VEVENT")
        assert result is None


class TestDatetimeHelpers:
    """Tests for _to_date, _to_datetime, _unwrap_dt helpers."""

    def test_to_date_from_datetime(self) -> None:
        from app.caldav_service import CalDAVService
        dt = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
        result = CalDAVService._to_date(dt)
        assert result == date(2026, 1, 15)

    def test_to_date_from_date(self) -> None:
        from app.caldav_service import CalDAVService
        d = date(2026, 1, 15)
        result = CalDAVService._to_date(d)
        assert result == date(2026, 1, 15)

    def test_to_datetime_from_date(self) -> None:
        from app.caldav_service import CalDAVService
        d = date(2026, 1, 15)
        result = CalDAVService._to_datetime(d)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.hour == 0
        assert result.minute == 0

    def test_to_datetime_from_datetime(self) -> None:
        from app.caldav_service import CalDAVService
        dt = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
        result = CalDAVService._to_datetime(dt)
        assert result == dt

    def test_unwrap_dt_with_dt_attr(self) -> None:
        from app.caldav_service import CalDAVService

        class MockProp:
            dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

        result = CalDAVService._unwrap_dt(MockProp())
        assert isinstance(result, datetime)
        assert result.hour == 10

    def test_unwrap_dt_bare_datetime(self) -> None:
        from app.caldav_service import CalDAVService
        dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        result = CalDAVService._unwrap_dt(dt)
        assert result == dt

    def test_unwrap_dt_invalid_type(self) -> None:
        from app.caldav_service import CalDAVError, CalDAVService
        with pytest.raises(CalDAVError, match="Unexpected datetime type"):
            CalDAVService._unwrap_dt("not a date")


class TestClientCalendarMethods:
    """Tests for the new calendar/event/task client methods."""

    def test_list_calendars(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "calendars": [{"name": "Lyra", "editable": True}],
            "editable_count": 1,
            "readonly_count": 0,
        }
        mc._client.get = MagicMock(return_value=mock_response)

        result = mc.list_calendars()
        assert result["editable_count"] == 1
        mc._client.get.assert_called_with("/calendars")

    def test_list_events_with_params(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [], "total": 0}
        mc._client.get = MagicMock(return_value=mock_response)

        result = mc.list_events(start="2026-01-01T00:00:00", end="2026-02-01T00:00:00")
        assert result["total"] == 0
        # Verify params were passed
        call_kwargs = mc._client.get.call_args
        assert call_kwargs[0][0] == "/events"
        assert call_kwargs[1]["params"]["start"] == "2026-01-01T00:00:00"
        assert call_kwargs[1]["params"]["end"] == "2026-02-01T00:00:00"

    def test_list_events_no_params(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [], "total": 0}
        mc._client.get = MagicMock(return_value=mock_response)

        mc.list_events()
        call_kwargs = mc._client.get.call_args
        assert call_kwargs[1]["params"] == {}

    def test_get_event(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"uid": "evt-1", "summary": "Test"}
        mc._client.get = MagicMock(return_value=mock_response)

        result = mc.get_event("evt-1")
        assert result["uid"] == "evt-1"
        mc._client.get.assert_called_with("/events/evt-1")

    def test_create_event(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"uid": "new-evt", "summary": "New"}
        mc._client.post = MagicMock(return_value=mock_response)

        result = mc.create_event(summary="New", start="2026-01-15T10:00:00", end="2026-01-15T11:00:00")
        assert result["uid"] == "new-evt"
        mc._client.post.assert_called_with(
            "/events",
            json={"summary": "New", "start": "2026-01-15T10:00:00", "end": "2026-01-15T11:00:00"},
        )

    def test_update_event(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"uid": "evt-1", "summary": "Updated"}
        mc._client.put = MagicMock(return_value=mock_response)

        result = mc.update_event("evt-1", summary="Updated")
        assert result["summary"] == "Updated"
        mc._client.put.assert_called_with("/events/evt-1", json={"summary": "Updated"})

    def test_delete_event(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"deleted": True, "uid": "evt-1"}
        mc._client.delete = MagicMock(return_value=mock_response)

        result = mc.delete_event("evt-1")
        assert result["deleted"] is True
        mc._client.delete.assert_called_with("/events/evt-1")

    def test_list_tasks(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tasks": [], "total": 0}
        mc._client.get = MagicMock(return_value=mock_response)

        result = mc.list_tasks()
        assert result["total"] == 0
        mc._client.get.assert_called_with("/tasks")

    def test_get_task(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"uid": "task-1", "summary": "Do thing"}
        mc._client.get = MagicMock(return_value=mock_response)

        result = mc.get_task("task-1")
        assert result["uid"] == "task-1"
        mc._client.get.assert_called_with("/tasks/task-1")

    def test_create_task(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"uid": "new-task", "summary": "New Task"}
        mc._client.post = MagicMock(return_value=mock_response)

        result = mc.create_task(summary="New Task", priority=3)
        assert result["uid"] == "new-task"
        mc._client.post.assert_called_with(
            "/tasks",
            json={"summary": "New Task", "priority": 3},
        )

    def test_update_task(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"uid": "task-1", "status": "COMPLETED"}
        mc._client.put = MagicMock(return_value=mock_response)

        result = mc.update_task("task-1", status="COMPLETED")
        assert result["status"] == "COMPLETED"
        mc._client.put.assert_called_with("/tasks/task-1", json={"status": "COMPLETED"})

    def test_delete_task(self) -> None:
        from app.client import MCPClient
        mc = MCPClient("http://test:8000")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"deleted": True, "uid": "task-1"}
        mc._client.delete = MagicMock(return_value=mock_response)

        result = mc.delete_task("task-1")
        assert result["deleted"] is True
        mc._client.delete.assert_called_with("/tasks/task-1")


class TestDeleteTaskEndpoint:
    """Tests for DELETE /tasks/{uid} (was untested in original suite)."""

    @pytest.fixture
    def mocked_client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")
        monkeypatch.setenv("CALDAV_EDITABLE_CALENDAR", "Lyra")

        from app.caldav_routes import _reset_service
        _reset_service()

        from app.main import app
        with TestClient(app) as c:
            yield c

        _reset_service()

    def test_delete_task_not_found(self, mocked_client: TestClient) -> None:
        from app.caldav_models import CalDAVConfig
        from app.caldav_service import CalDAVService

        config = CalDAVConfig(
            url="https://caldav.example.com",
            username="user",
            password="pass",
            editable_calendar="Lyra",
        )
        service = CalDAVService(config)
        service.delete_task = MagicMock(return_value=False)

        import app.caldav_routes as routes
        routes._service = service
        routes._service_inited = True

        resp = mocked_client.delete("/tasks/nonexistent")
        assert resp.status_code == 404
