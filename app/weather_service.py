"""Weather data service backed by the Open-Meteo API.

Open-Meteo (https://open-meteo.com) is a free weather API that requires
no API key and provides global coverage.  This service fetches current
conditions and a multi-day forecast for a single configured location.

Caching
-------
Responses are cached in memory and invalidated at local midnight.  Since
the server may reboot up to once a week, in-memory caching is acceptable
— the worst case is one extra API call per day after a reboot.

The cache key includes the number of forecast days requested, so a call
for 3 days and a call for 7 days are cached independently.  However, all
cache entries expire at the same time: the next local midnight after the
first entry was written.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from .weather_models import (
    CurrentWeather,
    DayForecast,
    WeatherConfig,
    WeatherResponse,
)

logger = logging.getLogger(__name__)

#: Open-Meteo endpoint URL.
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

#: Maximum number of forecast days supported (also used as the default
#: request size so that a 7-day cache entry can serve smaller requests).
_MAX_DAYS = 7

#: HTTP request timeout in seconds.
_TIMEOUT = 10.0


class WeatherError(Exception):
    """Raised on weather fetch or parse failures."""


def _clamp_days(days: int | None) -> int:
    """Clamp the ``days`` parameter to the valid range [1, 7].

    ``None`` or values outside the range are silently adjusted to the
    nearest bound.  Zero or negative values become 1; values above 7
    become 7.
    """
    if days is None or days < 1:
        return 1
    return min(days, _MAX_DAYS)


def _next_midnight(now: datetime) -> datetime:
    """Return the next local midnight after *now*."""
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, datetime.min.time())
    # Preserve the timezone of the input datetime.
    if now.tzinfo is not None:
        return midnight.replace(tzinfo=now.tzinfo)
    return midnight


class WeatherService:
    """Service for fetching and caching weather data from Open-Meteo.

    The service caches responses in memory.  Cache entries expire at the
    next local midnight, ensuring data is refreshed at least once per day.
    """

    def __init__(self, config: WeatherConfig) -> None:
        self._config = config
        # Cache: maps days (int) → (WeatherResponse, expiry datetime)
        self._cache: dict[int, tuple[WeatherResponse, datetime]] = {}

    @property
    def config(self) -> WeatherConfig:
        return self._config

    @property
    def latitude(self) -> float:
        return self._config.latitude

    @property
    def longitude(self) -> float:
        return self._config.longitude

    @property
    def location_label(self) -> str:
        """Human-readable location string for response metadata."""
        return f"{self._config.latitude},{self._config.longitude}"

    # ------------------------------------------------------------------ #
    # Cache management
    # ------------------------------------------------------------------ #

    def _purge_expired(self, now: datetime) -> None:
        """Remove all cache entries whose expiry has passed."""
        expired = [d for d, (_, exp) in self._cache.items() if exp <= now]
        for d in expired:
            del self._cache[d]

    def _get_cached(self, days: int, now: datetime) -> WeatherResponse | None:
        """Return a cached response if still valid, else None."""
        self._purge_expired(now)
        entry = self._cache.get(days)
        if entry is None:
            return None
        resp, expiry = entry
        if now >= expiry:
            del self._cache[days]
            return None
        return resp

    def _set_cached(self, days: int, resp: WeatherResponse, now: datetime) -> None:
        """Store a response in the cache with a midnight expiry."""
        expiry = _next_midnight(now)
        self._cache[days] = (resp, expiry)

    def clear_cache(self) -> None:
        """Clear all cached data (used in tests)."""
        self._cache.clear()

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #

    def _build_url(self, days: int) -> str:
        """Build the Open-Meteo API URL for the configured location."""
        params = (
            f"?latitude={self._config.latitude}"
            f"&longitude={self._config.longitude}"
            # Current weather fields
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
            "wind_speed_10m,wind_direction_10m,weather_code,is_day"
            # Daily forecast fields
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,precipitation_probability_max,"
            "wind_speed_10m_max,sunrise,sunset"
            f"&forecast_days={days}"
            "&timezone=auto&temperature_unit=fahrenheit"
        )
        return f"{_OPEN_METEO_URL}{params}"

    def _fetch(self, days: int) -> dict:
        """Fetch raw JSON data from Open-Meteo."""
        url = self._build_url(days)
        try:
            resp = httpx.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WeatherError(
                f"Open-Meteo returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise WeatherError(f"Failed to reach Open-Meteo: {exc}") from exc

        return resp.json()

    def _parse(self, raw: dict) -> WeatherResponse:
        """Parse Open-Meteo JSON into our response models."""
        cur_raw = raw.get("current", {})
        current = CurrentWeather(
            temperature=round(cur_raw["temperature_2m"], 1),
            apparent_temperature=round(cur_raw["apparent_temperature"], 1),
            humidity=round(cur_raw["relative_humidity_2m"]),
            wind_speed=round(cur_raw["wind_speed_10m"], 1),
            wind_direction=round(cur_raw["wind_direction_10m"]),
            weather_code=cur_raw["weather_code"],
            is_day=bool(cur_raw["is_day"]),
        )

        daily_raw = raw.get("daily", {})
        times = daily_raw.get("time", [])
        forecast: list[DayForecast] = []

        for i, day_str in enumerate(times):
            forecast.append(DayForecast(
                date=day_str,
                weather_code=daily_raw["weather_code"][i],
                temp_max=round(daily_raw["temperature_2m_max"][i], 1),
                temp_min=round(daily_raw["temperature_2m_min"][i], 1),
                precipitation_sum=round(daily_raw["precipitation_sum"][i], 1),
                precipitation_probability=round(
                    daily_raw["precipitation_probability_max"][i]
                ),
                wind_speed_max=round(daily_raw["wind_speed_10m_max"][i], 1),
                sunrise=daily_raw["sunrise"][i],
                sunset=daily_raw["sunset"][i],
            ))

        return WeatherResponse(
            location=self.location_label,
            latitude=self._config.latitude,
            longitude=self._config.longitude,
            current=current,
            forecast=forecast,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get_weather(self, days: int = 1) -> WeatherResponse:
        """Return weather data for the configured location.

        Serves from cache when available; otherwise fetches from
        Open-Meteo and caches the result until local midnight.

        Parameters
        ----------
        days:
            Number of forecast days (clamped to [1, 7]).
        """
        days = _clamp_days(days)
        now = datetime.now(UTC).astimezone()

        # Try cache first.
        cached = self._get_cached(days, now)
        if cached is not None:
            logger.debug("Weather cache hit for %d days", days)
            return cached

        # Cache miss — fetch fresh data.
        logger.debug("Weather cache miss for %d days, fetching", days)
        raw = self._fetch(days)
        resp = self._parse(raw)
        self._set_cached(days, resp, now)
        return resp


# --------------------------------------------------------------------------- #
# Singleton management (same pattern as ics_routes.py)
# --------------------------------------------------------------------------- #

_service: WeatherService | None = None
_service_inited: bool = False


def get_weather_service() -> WeatherService:
    """Return the WeatherService singleton, or raise 503 if not configured."""
    from fastapi import HTTPException, status

    global _service, _service_inited
    if not _service_inited:
        config = WeatherConfig.from_env()
        if config is None:
            _service_inited = True
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Weather is not configured. Set WEATHER_LOCATION env var "
                       "(format: 'lat,long', e.g. '45.5,-122.6').",
            )
        _service = WeatherService(config)
        _service_inited = True

    if _service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather is not configured. Set WEATHER_LOCATION env var.",
        )
    return _service


def reset_weather_service() -> None:
    """Reset the singleton (used in tests)."""
    global _service, _service_inited
    _service = None
    _service_inited = False
