"""Tests for the weather data enrichment endpoint."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.weather_models import (
    WeatherConfig,
)
from app.weather_service import (
    WeatherError,
    WeatherService,
    _clamp_days,
    _next_midnight,
    get_weather_service,
    reset_weather_service,
)


def _get_route_paths(app) -> set[str]:
    """Extract all route paths from a FastAPI app, including nested routers.

    In newer FastAPI/Starlette versions, ``include_router`` wraps the
    original router in an ``_IncludedRouter`` proxy that exposes the
    sub-routes via ``original_router`` (not ``routes``).  We handle both
    attribute names for compatibility across versions.
    """
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
        # Check for nested routes in included routers.  Newer Starlette
        # wraps included routers in an ``_IncludedRouter`` proxy that
        # exposes the original ``APIRouter`` via ``original_router``;
        # older versions expose a ``routes`` list directly.
        for attr in ("original_router", "routes"):
            inner = getattr(route, attr, None)
            if inner is None:
                continue
            # ``original_router`` is an APIRouter — its routes are in
            # ``.routes``.  ``routes`` may be a list directly.
            sub_routes = getattr(inner, "routes", inner)
            if sub_routes is None:
                continue
            for sub_route in sub_routes:
                sub_path = getattr(sub_route, "path", None)
                if sub_path is not None:
                    paths.add(sub_path)
    return paths


# --------------------------------------------------------------------------- #
# Sample Open-Meteo API response (for mocking)
# --------------------------------------------------------------------------- #

SAMPLE_API_RESPONSE_1DAY = {
    "current": {
        "temperature_2m": 15.3,
        "apparent_temperature": 14.1,
        "relative_humidity_2m": 65,
        "wind_speed_10m": 12.5,
        "wind_direction_10m": 180,
        "weather_code": 3,
        "is_day": 1,
    },
    "daily": {
        "time": ["2026-08-17"],
        "weather_code": [3],
        "temperature_2m_max": [22.0],
        "temperature_2m_min": [10.5],
        "precipitation_sum": [0.0],
        "precipitation_probability_max": [10],
        "wind_speed_10m_max": [15.0],
        "sunrise": ["2026-08-17T06:15"],
        "sunset": ["2026-08-17T20:30"],
    },
}

SAMPLE_API_RESPONSE_3DAY = {
    "current": {
        "temperature_2m": 15.3,
        "apparent_temperature": 14.1,
        "relative_humidity_2m": 65,
        "wind_speed_10m": 12.5,
        "wind_direction_10m": 180,
        "weather_code": 3,
        "is_day": 1,
    },
    "daily": {
        "time": ["2026-08-17", "2026-08-18", "2026-08-19"],
        "weather_code": [3, 61, 2],
        "temperature_2m_max": [22.0, 18.5, 25.1],
        "temperature_2m_min": [10.5, 12.0, 13.3],
        "precipitation_sum": [0.0, 2.5, 0.0],
        "precipitation_probability_max": [10, 80, 5],
        "wind_speed_10m_max": [15.0, 20.0, 10.0],
        "sunrise": ["2026-08-17T06:15", "2026-08-18T06:16", "2026-08-19T06:17"],
        "sunset": ["2026-08-17T20:30", "2026-08-18T20:28", "2026-08-19T20:26"],
    },
}


# --------------------------------------------------------------------------- #
# WeatherConfig tests
# --------------------------------------------------------------------------- #

class TestWeatherConfig:
    def test_from_env_valid(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "45.5,-122.6")
        cfg = WeatherConfig.from_env()
        assert cfg is not None
        assert cfg.latitude == 45.5
        assert cfg.longitude == -122.6

    def test_from_env_negative_coords(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "-33.8,151.2")
        cfg = WeatherConfig.from_env()
        assert cfg is not None
        assert cfg.latitude == -33.8
        assert cfg.longitude == 151.2

    def test_from_env_not_set(self, monkeypatch):
        monkeypatch.delenv("WEATHER_LOCATION", raising=False)
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_empty(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "")
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_whitespace(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "  ")
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_missing_longitude(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "45.5")
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_non_numeric(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "abc,def")
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_lat_out_of_range(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "91.0,0.0")
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_lon_out_of_range(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "0.0,181.0")
        cfg = WeatherConfig.from_env()
        assert cfg is None

    def test_from_env_extra_whitespace(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", " 45.5 , -122.6 ")
        cfg = WeatherConfig.from_env()
        assert cfg is not None
        assert cfg.latitude == 45.5
        assert cfg.longitude == -122.6

    def test_from_env_boundary_values(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "90.0,180.0")
        cfg = WeatherConfig.from_env()
        assert cfg is not None
        assert cfg.latitude == 90.0
        assert cfg.longitude == 180.0

    def test_from_env_negative_boundary(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "-90.0,-180.0")
        cfg = WeatherConfig.from_env()
        assert cfg is not None
        assert cfg.latitude == -90.0
        assert cfg.longitude == -180.0


# --------------------------------------------------------------------------- #
# _clamp_days tests
# --------------------------------------------------------------------------- #

class TestClampDays:
    def test_none_defaults_to_1(self):
        assert _clamp_days(None) == 1

    def test_zero_clamped_to_1(self):
        assert _clamp_days(0) == 1

    def test_negative_clamped_to_1(self):
        assert _clamp_days(-5) == 1

    def test_one(self):
        assert _clamp_days(1) == 1

    def test_three(self):
        assert _clamp_days(3) == 3

    def test_seven(self):
        assert _clamp_days(7) == 7

    def test_eight_clamped_to_7(self):
        assert _clamp_days(8) == 7

    def test_large_value_clamped_to_7(self):
        assert _clamp_days(100) == 7


# --------------------------------------------------------------------------- #
# _next_midnight tests
# --------------------------------------------------------------------------- #

class TestNextMidnight:
    def test_morning_returns_same_day_midnight(self):
        now = datetime(2026, 8, 17, 6, 0, 0, tzinfo=UTC)
        midnight = _next_midnight(now)
        assert midnight == datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)

    def test_evening_returns_next_day_midnight(self):
        now = datetime(2026, 8, 17, 23, 59, 59, tzinfo=UTC)
        midnight = _next_midnight(now)
        assert midnight == datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)

    def test_noon_returns_next_midnight(self):
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
        midnight = _next_midnight(now)
        assert midnight == datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)

    def test_month_boundary(self):
        now = datetime(2026, 8, 31, 15, 0, 0, tzinfo=UTC)
        midnight = _next_midnight(now)
        assert midnight == datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# WeatherService — parsing tests
# --------------------------------------------------------------------------- #

class TestWeatherServiceParsing:
    def _make_service(self) -> WeatherService:
        cfg = WeatherConfig(latitude=45.5, longitude=-122.6)
        return WeatherService(cfg)

    def test_parse_1_day(self):
        svc = self._make_service()
        resp = svc._parse(SAMPLE_API_RESPONSE_1DAY)
        assert resp.latitude == 45.5
        assert resp.longitude == -122.6
        assert resp.location == "45.5,-122.6"
        assert resp.current.temperature == 15.3
        assert resp.current.apparent_temperature == 14.1
        assert resp.current.humidity == 65
        assert resp.current.wind_speed == 12.5
        assert resp.current.wind_direction == 180
        assert resp.current.weather_code == 3
        assert resp.current.is_day is True
        assert len(resp.forecast) == 1
        assert resp.forecast[0].date == "2026-08-17"
        assert resp.forecast[0].temp_max == 22.0
        assert resp.forecast[0].temp_min == 10.5
        assert resp.forecast[0].precipitation_sum == 0.0
        assert resp.forecast[0].precipitation_probability == 10
        assert resp.forecast[0].wind_speed_max == 15.0
        assert resp.forecast[0].sunrise == "2026-08-17T06:15"
        assert resp.forecast[0].sunset == "2026-08-17T20:30"

    def test_parse_3_day(self):
        svc = self._make_service()
        resp = svc._parse(SAMPLE_API_RESPONSE_3DAY)
        assert len(resp.forecast) == 3
        assert resp.forecast[0].date == "2026-08-17"
        assert resp.forecast[1].date == "2026-08-18"
        assert resp.forecast[2].date == "2026-08-19"
        assert resp.forecast[1].precipitation_sum == 2.5
        assert resp.forecast[1].precipitation_probability == 80
        assert resp.forecast[2].temp_max == 25.1

    def test_parse_is_day_false(self):
        svc = self._make_service()
        raw = {
            "current": {
                "temperature_2m": 5.0,
                "apparent_temperature": 2.0,
                "relative_humidity_2m": 80,
                "wind_speed_10m": 20.0,
                "wind_direction_10m": 270,
                "weather_code": 2,
                "is_day": 0,
            },
            "daily": {
                "time": ["2026-08-17"],
                "weather_code": [2],
                "temperature_2m_max": [10.0],
                "temperature_2m_min": [1.0],
                "precipitation_sum": [0.5],
                "precipitation_probability_max": [30],
                "wind_speed_10m_max": [25.0],
                "sunrise": ["2026-08-17T06:15"],
                "sunset": ["2026-08-17T20:30"],
            },
        }
        resp = svc._parse(raw)
        assert resp.current.is_day is False

    def test_parse_rounds_floats(self):
        svc = self._make_service()
        raw = {
            "current": {
                "temperature_2m": 15.3456,
                "apparent_temperature": 14.1234,
                "relative_humidity_2m": 65.7,
                "wind_speed_10m": 12.567,
                "wind_direction_10m": 180.3,
                "weather_code": 3,
                "is_day": 1,
            },
            "daily": {
                "time": ["2026-08-17"],
                "weather_code": [3],
                "temperature_2m_max": [22.044],
                "temperature_2m_min": [10.555],
                "precipitation_sum": [0.033],
                "precipitation_probability_max": [10.6],
                "wind_speed_10m_max": [15.044],
                "sunrise": ["2026-08-17T06:15"],
                "sunset": ["2026-08-17T20:30"],
            },
        }
        resp = svc._parse(raw)
        assert resp.current.temperature == 15.3
        assert resp.current.apparent_temperature == 14.1
        assert resp.current.humidity == 66
        assert resp.current.wind_speed == 12.6
        assert resp.current.wind_direction == 180
        assert resp.forecast[0].temp_max == 22.0
        assert resp.forecast[0].temp_min == 10.6
        assert resp.forecast[0].precipitation_sum == 0.0
        assert resp.forecast[0].precipitation_probability == 11
        assert resp.forecast[0].wind_speed_max == 15.0

    def test_consistent_structure_across_day_counts(self):
        """Response model structure is identical regardless of forecast length."""
        svc = self._make_service()
        resp1 = svc._parse(SAMPLE_API_RESPONSE_1DAY)
        resp3 = svc._parse(SAMPLE_API_RESPONSE_3DAY)

        # Both must have the same top-level fields.
        assert type(resp1).model_fields.keys() == type(resp3).model_fields.keys()

        # Both must have a current object with the same fields.
        assert type(resp1.current).model_fields.keys() == type(resp3.current).model_fields.keys()

        # Both must have non-empty forecast lists.
        assert len(resp1.forecast) >= 1
        assert len(resp3.forecast) >= 1

        # Each forecast entry must have the same fields.
        assert (type(resp1.forecast[0]).model_fields.keys()
                == type(resp3.forecast[0]).model_fields.keys())


# --------------------------------------------------------------------------- #
# WeatherService — caching tests
# --------------------------------------------------------------------------- #

class TestWeatherServiceCaching:
    def _make_service(self) -> WeatherService:
        cfg = WeatherConfig(latitude=45.5, longitude=-122.6)
        return WeatherService(cfg)

    def test_cache_miss_then_hit(self):
        svc = self._make_service()
        with patch.object(svc, "_fetch", return_value=SAMPLE_API_RESPONSE_1DAY) as mock_fetch:
            # First call: cache miss, should fetch.
            resp1 = svc.get_weather(days=1)
            assert mock_fetch.call_count == 1

            # Second call: cache hit, should not fetch.
            resp2 = svc.get_weather(days=1)
            assert mock_fetch.call_count == 1

            # Both responses should be identical.
            assert resp1 == resp2

    def test_different_days_cached_separately(self):
        svc = self._make_service()
        with patch.object(svc, "_fetch", return_value=SAMPLE_API_RESPONSE_3DAY) as mock_fetch:
            svc.get_weather(days=1)
            assert mock_fetch.call_count == 1
            svc.get_weather(days=3)
            assert mock_fetch.call_count == 2
            # Both now cached.
            svc.get_weather(days=1)
            svc.get_weather(days=3)
            assert mock_fetch.call_count == 2

    def test_cache_expires_at_midnight(self):
        svc = self._make_service()
        # Mock _fetch so we don't hit the network.
        with patch.object(svc, "_fetch", return_value=SAMPLE_API_RESPONSE_1DAY) as mock_fetch:
            # Manually populate cache with an expiry in the past.
            fake_resp = svc._parse(SAMPLE_API_RESPONSE_1DAY)
            past_expiry = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
            svc._cache[1] = (fake_resp, past_expiry)

            # First call: cache expired, should fetch.
            svc.get_weather(days=1)
            assert mock_fetch.call_count == 1

            # Second call: cache fresh (set by previous fetch), should not fetch.
            svc.get_weather(days=1)
            assert mock_fetch.call_count == 1

            # Now expire the cache again by setting expiry to past.
            for key in list(svc._cache.keys()):
                resp, _ = svc._cache[key]
                svc._cache[key] = (resp, past_expiry)

            # Third call: cache expired again, should refetch.
            svc.get_weather(days=1)
            assert mock_fetch.call_count == 2

    def test_clear_cache(self):
        svc = self._make_service()
        with patch.object(svc, "_fetch", return_value=SAMPLE_API_RESPONSE_1DAY) as mock_fetch:
            svc.get_weather(days=1)
            assert mock_fetch.call_count == 1
            svc.clear_cache()
            svc.get_weather(days=1)
            assert mock_fetch.call_count == 2

    def test_days_clamped_in_get_weather(self):
        svc = self._make_service()
        with patch.object(svc, "_fetch", return_value=SAMPLE_API_RESPONSE_1DAY) as mock_fetch:
            # 0 days → clamped to 1.
            svc.get_weather(days=0)
            # Check the URL used forecast_days=1.
            called_url = mock_fetch.call_args[0][0]
            assert called_url == 1

    def test_days_none_defaults_to_1(self):
        svc = self._make_service()
        with patch.object(svc, "_fetch", return_value=SAMPLE_API_RESPONSE_1DAY) as mock_fetch:
            svc.get_weather(days=None)
            assert mock_fetch.call_args[0][0] == 1


# --------------------------------------------------------------------------- #
# WeatherService — error handling tests
# --------------------------------------------------------------------------- #

class TestWeatherServiceErrors:
    def _make_service(self) -> WeatherService:
        cfg = WeatherConfig(latitude=45.5, longitude=-122.6)
        return WeatherService(cfg)

    def test_http_status_error_raises_weather_error(self):
        import httpx

        svc = self._make_service()
        mock_response = httpx.Response(500, request=httpx.Request("GET", "https://example.com"))
        with (
            patch("app.weather_service.httpx.get",
                   side_effect=httpx.HTTPStatusError("Server error", request=mock_response.request, response=mock_response)),
            pytest.raises(WeatherError, match="HTTP 500"),
        ):
            svc._fetch(1)

    def test_connection_error_raises_weather_error(self):
        import httpx

        svc = self._make_service()
        with (
            patch("app.weather_service.httpx.get",
                   side_effect=httpx.ConnectError("Connection refused")),
            pytest.raises(WeatherError, match="Failed to reach"),
        ):
            svc._fetch(1)

    def test_get_weather_propagates_error(self):
        svc = self._make_service()
        with (
            patch.object(svc, "_fetch", side_effect=WeatherError("Boom")),
            pytest.raises(WeatherError, match="Boom"),
        ):
            svc.get_weather(days=1)


# --------------------------------------------------------------------------- #
# Singleton management tests
# --------------------------------------------------------------------------- #

class TestWeatherSingleton:
    def test_reset_clears_singleton(self):
        reset_weather_service()
        from app.weather_service import _service, _service_inited
        assert _service is None
        assert _service_inited is False

    def test_get_service_raises_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("WEATHER_LOCATION", raising=False)
        reset_weather_service()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_weather_service()
        assert exc_info.value.status_code == 503

    def test_get_service_returns_configured_service(self, monkeypatch):
        monkeypatch.setenv("WEATHER_LOCATION", "45.5,-122.6")
        reset_weather_service()
        svc = get_weather_service()
        assert svc.latitude == 45.5
        assert svc.longitude == -122.6
        # Second call returns the same singleton.
        svc2 = get_weather_service()
        assert svc is svc2
        reset_weather_service()


# --------------------------------------------------------------------------- #
# Integration tests (full app with weather endpoint)
# --------------------------------------------------------------------------- #

@pytest.fixture
def weather_app_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with weather enabled and all other features disabled."""
    # Disable all other features to isolate weather.
    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)
    monkeypatch.delenv("GITEA_URL", raising=False)
    monkeypatch.delenv("DISCORD_INFO_HOOK", raising=False)
    monkeypatch.delenv("DISCORD_NOTICE_HOOK", raising=False)
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("NTFY_INFO_TOPIC", raising=False)
    monkeypatch.delenv("MCP_LOG_ENABLED", raising=False)

    # Enable weather.
    monkeypatch.setenv("WEATHER_LOCATION", "45.5,-122.6")

    # Reset singletons.
    from app.caldav_routes import _reset_service as _reset_caldav
    from app.ics_routes import _reset_service as _reset_ics
    from app.jobs import job_scheduler
    from app.notify_service import reset_notify_registry
    _reset_caldav()
    _reset_ics()
    reset_notify_registry()
    reset_weather_service()

    # Clean up any lingering background jobs from prior test apps so
    # they don't leak sockets into subsequent tests.
    for name in list(job_scheduler.job_names):
        job_scheduler.unregister(name)

    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client

    # Cleanup.
    _reset_caldav()
    _reset_ics()
    reset_notify_registry()
    reset_weather_service()


class TestWeatherEndpoint:
    def test_weather_endpoint_registered(self, weather_app_client):
        """The /weather route exists when WEATHER_LOCATION is set."""
        routes = _get_route_paths(weather_app_client.app)
        assert "/weather" in routes

    def test_weather_endpoint_not_registered_when_unconfigured(self, monkeypatch):
        """The /weather route does NOT exist when WEATHER_LOCATION is unset."""
        monkeypatch.delenv("CALDAV_URL", raising=False)
        monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)
        monkeypatch.delenv("GITEA_URL", raising=False)
        monkeypatch.delenv("DISCORD_INFO_HOOK", raising=False)
        monkeypatch.delenv("DISCORD_NOTICE_HOOK", raising=False)
        monkeypatch.delenv("NTFY_URL", raising=False)
        monkeypatch.delenv("NTFY_INFO_TOPIC", raising=False)
        monkeypatch.delenv("MCP_LOG_ENABLED", raising=False)
        monkeypatch.delenv("WEATHER_LOCATION", raising=False)

        from app.caldav_routes import _reset_service as _reset_caldav
        from app.ics_routes import _reset_service as _reset_ics
        from app.notify_service import reset_notify_registry
        _reset_caldav()
        _reset_ics()
        reset_notify_registry()
        reset_weather_service()

        # Need at least one feature to start. Use a registry command.
        from pathlib import Path

        from app.registry import load_registry
        reg_dir = Path(__file__).parent / ".." / "registry"
        load_registry(reg_dir)

        from app.main import create_app
        app = create_app()
        with TestClient(app) as client:
            routes = _get_route_paths(client.app)
            assert "/weather" not in routes

    def test_get_weather_default_days(self, weather_app_client):
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latitude"] == 45.5
        assert data["longitude"] == -122.6
        assert data["location"] == "45.5,-122.6"
        assert "current" in data
        assert "forecast" in data
        assert len(data["forecast"]) == 1
        assert data["current"]["temperature"] == 15.3
        assert data["current"]["is_day"] is True

    def test_get_weather_3_days(self, weather_app_client):
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_3DAY)
            resp = weather_app_client.get("/weather?days=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["forecast"]) == 3
        assert data["forecast"][0]["date"] == "2026-08-17"
        assert data["forecast"][1]["date"] == "2026-08-18"
        assert data["forecast"][2]["date"] == "2026-08-19"

    def test_get_weather_days_7(self, weather_app_client):
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather?days=7")
        assert resp.status_code == 200

    def test_get_weather_days_0_defaults_to_1(self, weather_app_client):
        """days=0 is silently clamped to 1 — no error."""
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather?days=0")
        assert resp.status_code == 200
        assert len(resp.json()["forecast"]) == 1

    def test_get_weather_days_negative_defaults_to_1(self, weather_app_client):
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather?days=-5")
        assert resp.status_code == 200

    def test_get_weather_days_above_7_clamped(self, weather_app_client):
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather?days=100")
        assert resp.status_code == 200

    def test_get_weather_days_non_integer_defaults(self, weather_app_client):
        """Non-integer days value is silently discarded → defaults to 1."""
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather?days=abc")
        assert resp.status_code == 200
        assert len(resp.json()["forecast"]) == 1

    def test_get_weather_days_float_truncated(self, weather_app_client):
        """Float '2.9' is parsed as int(2.9)=2 via int()."""
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather?days=2.9")
        # int("2.9") raises ValueError → defaults to 1.
        assert resp.status_code == 200

    def test_get_weather_no_days_param(self, weather_app_client):
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp = weather_app_client.get("/weather")
        assert resp.status_code == 200
        assert len(resp.json()["forecast"]) == 1

    def test_weather_serves_from_cache(self, weather_app_client):
        """Second call within the same day should not hit the API."""
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            weather_app_client.get("/weather")
            assert mock_get.call_count == 1
            weather_app_client.get("/weather")
            assert mock_get.call_count == 1

    def test_weather_api_error_returns_502(self, weather_app_client):
        """When Open-Meteo is unreachable, the endpoint returns 502."""
        import httpx

        with patch("app.weather_service.httpx.get",
                   side_effect=httpx.ConnectError("Connection refused")):
            resp = weather_app_client.get("/weather")
        assert resp.status_code == 502

    def test_weather_response_structure_consistency(self, weather_app_client):
        """Verify the response JSON structure is the same for 1 and 3 days."""
        with patch("app.weather_service.httpx.get") as mock_get:
            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_1DAY)
            resp1 = weather_app_client.get("/weather?days=1").json()

            # Clear cache so the next call fetches fresh data.
            from app.weather_service import get_weather_service
            get_weather_service().clear_cache()

            mock_get.return_value = httpx_mock_response(SAMPLE_API_RESPONSE_3DAY)
            resp3 = weather_app_client.get("/weather?days=3").json()

        # Top-level keys must match.
        assert set(resp1.keys()) == set(resp3.keys())

        # Current keys must match.
        assert set(resp1["current"].keys()) == set(resp3["current"].keys())

        # Forecast entry keys must match.
        assert set(resp1["forecast"][0].keys()) == set(resp3["forecast"][0].keys())

    def test_weather_in_openapi_schema(self, weather_app_client):
        """Weather endpoint should appear in the OpenAPI schema."""
        resp = weather_app_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "/weather" in schema["paths"]
        assert "get" in schema["paths"]["/weather"]


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #

def httpx_mock_response(json_data: dict):
    """Create a mock httpx.Response with the given JSON data."""
    import httpx

    resp = httpx.Response(200, json=json_data, request=httpx.Request("GET", "https://api.open-meteo.com"))
    return resp
