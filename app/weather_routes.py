"""FastAPI router for the weather data enrichment endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .auth import verify_api_key
from .weather_models import WeatherResponse
from .weather_service import WeatherError, _clamp_days, get_weather_service, reset_weather_service

__all__ = ["create_weather_router", "reset_weather_service"]


def create_weather_router() -> APIRouter:
    """Build the weather router.

    The router is only mounted when ``WEATHER_LOCATION`` is configured
    (see :func:`app.main.create_app`).
    """
    router = APIRouter(
        prefix="",
        tags=["weather"],
        dependencies=[Depends(verify_api_key)],
    )

    @router.get("/weather", response_model=WeatherResponse)
    async def get_weather(
        days: str | None = Query(
            default=None,
            description="Number of forecast days (1–7). Defaults to 1. "
                        "Invalid values are silently discarded.",
        ),
    ) -> WeatherResponse:
        """Get current weather and forecast for the configured location.

        Returns current conditions plus a multi-day forecast.  The
        response structure is identical regardless of the number of days
        requested — only the length of the ``forecast`` list changes.

        The ``days`` parameter accepts an integer (1–7).  Values outside
        this range are clamped.  Non-integer or missing values default
        to 1.  No error is ever returned for bad input.
        """
        # Parse days silently: any non-integer value becomes None → 1.
        parsed_days: int | None = None
        if days is not None:
            try:
                parsed_days = int(days)
            except (ValueError, TypeError):
                parsed_days = None

        svc = get_weather_service()
        try:
            return await run_in_threadpool(svc.get_weather, _clamp_days(parsed_days))
        except WeatherError as exc:
            return JSONResponse(
                status_code=502,
                content={"detail": str(exc)},
            )

    return router
