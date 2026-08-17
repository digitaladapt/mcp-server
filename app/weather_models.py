"""Pydantic models for the weather data enrichment endpoint.

The weather endpoint fetches current conditions and a multi-day forecast
from the Open-Meteo API (free, no API key required).  A single location
is configured via the ``WEATHER_LOCATION`` environment variable in
``"lat,long"`` format (e.g. ``"45.5,-122.6"``).

The response structure is **identical** regardless of how many forecast
days are requested — the ``forecast`` list simply contains more or fewer
entries (1–7).  This consistency makes the endpoint easy to consume from
scripts and the LLM without conditional parsing.
"""

from __future__ import annotations

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

class WeatherConfig(BaseModel):
    """Configuration for the weather endpoint.

    Read from environment variables:

      WEATHER_LOCATION – ``"lat,long"`` string (e.g. ``"45.5,-122.6"``)
    """

    latitude: float
    longitude: float

    @classmethod
    def from_env(cls) -> WeatherConfig | None:
        """Build config from environment variables.

        Returns ``None`` if ``WEATHER_LOCATION`` is not set or malformed
        (weather endpoint disabled).
        """
        import os

        raw = os.environ.get("WEATHER_LOCATION", "").strip()
        if not raw:
            return None

        parts = raw.split(",")
        if len(parts) != 2:
            return None

        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            return None

        # Basic sanity: latitude [-90, 90], longitude [-180, 180].
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None

        return cls(latitude=lat, longitude=lon)


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class CurrentWeather(BaseModel):
    """Current weather conditions at the configured location."""

    temperature: float
    apparent_temperature: float
    humidity: int
    wind_speed: float
    wind_direction: int
    weather_code: int
    is_day: bool


class DayForecast(BaseModel):
    """Single-day forecast entry.

    The structure is the same for every day in the ``forecast`` list,
    including the first entry which represents "today".
    """

    date: str
    weather_code: int
    temp_max: float
    temp_min: float
    precipitation_sum: float
    precipitation_probability: int
    wind_speed_max: float
    sunrise: str
    sunset: str


class WeatherResponse(BaseModel):
    """Top-level response for ``GET /weather``.

    The ``forecast`` list always contains at least one entry (today).
    The ``current`` object is always present.  This structure does not
    change based on the ``days`` parameter — only the length of
    ``forecast`` varies.
    """

    location: str
    latitude: float
    longitude: float
    current: CurrentWeather
    forecast: list[DayForecast]
