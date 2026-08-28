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
        days: int | None = Query(1, ge=1, le=7, description="Days to forecast"),
    ) -> WeatherResponse:
        "Get current weather and days of forecast."
        svc = get_weather_service()
        try:
            return await run_in_threadpool(svc.get_weather, _clamp_days(days))
        except WeatherError as exc:
            return JSONResponse(
                status_code=502,
                content={"detail": str(exc)},
            )

    return router
