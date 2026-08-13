"""FastAPI router for ICS calendar endpoints.

When ICS is configured, this router provides:
  GET /ics/calendars   – list ICS calendar sources
  GET /ics/events      – list cached events (optional date range)
  GET /ics/events/{uid} – get a single cached event
  POST /ics/refresh    – manually trigger a cache refresh
  GET /ics/status      – cache status and last refresh info

All endpoints require API key authentication when MCP_API_KEY is set.
Returns 503 if ICS is not configured (ICS_CALENDAR_URL unset) — but in
practice, this router is only mounted when ICS IS configured.

ICS calendars are inherently read-only — there are no create/update/delete
endpoints.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

from .auth import verify_api_key
from .ics_models import (
    ICSCalendarInfo,
    ICSConfig,
    ICSRefreshResult,
)
from .ics_service import ICSService

# Singleton service — lazily initialised from env vars.
_service: ICSService | None = None
_service_inited: bool = False


def _get_service() -> ICSService:
    """Return the ICSService singleton, or raise 503 if not configured."""
    global _service, _service_inited
    if not _service_inited:
        config = ICSConfig.from_env()
        if config is None:
            _service_inited = True
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ICS calendar is not configured. Set ICS_CALENDAR_URL env var.",
            )
        _service = ICSService(config)
        _service_inited = True

    if _service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ICS calendar is not configured. Set ICS_CALENDAR_URL env var.",
        )
    return _service


def _reset_service() -> None:
    """Reset the singleton (used in tests)."""
    global _service, _service_inited
    _service = None
    _service_inited = False


# --------------------------------------------------------------------------- #
# Parse helper
# --------------------------------------------------------------------------- #

def _parse_dt_param(value: str | None) -> datetime | date | None:
    """Parse a query parameter as a datetime or date."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date/time format: {value}. Use ISO 8601.",
            )


def create_ics_router() -> APIRouter:
    """Build the ICS router.

    This router is only mounted when ICS is configured.
    """
    router = APIRouter(
        prefix="/ics",
        tags=["ics-calendar"],
        dependencies=[Depends(verify_api_key)],
    )

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #

    @router.get("/calendars", response_model=list[ICSCalendarInfo])
    async def list_calendars() -> list[ICSCalendarInfo]:
        """List ICS calendar sources with cache status."""
        svc = _get_service()
        info = svc.calendar_info()
        return [
            ICSCalendarInfo(
                name=info.name,
                url=info.url,
                editable=False,
                events_cached=len(svc.events),
                last_refreshed=svc.last_refreshed.isoformat() if svc.last_refreshed else None,
                last_error=svc.last_error,
            )
        ]

    @router.get("/events")
    async def list_events(
        start: str | None = Query(None, description="ISO 8601 start datetime/date"),
        end: str | None = Query(None, description="ISO 8601 end datetime/date"),
    ) -> dict:
        """List cached ICS events, optionally filtered by date range."""
        svc = _get_service()
        start_dt = _parse_dt_param(start)
        end_dt = _parse_dt_param(end)
        events = await run_in_threadpool(svc.list_events, start=start_dt, end=end_dt)
        return {"events": [e.model_dump() for e in events], "total": len(events)}

    @router.get("/events/{uid}")
    async def get_event(uid: str) -> dict:
        """Get a single cached ICS event by UID."""
        svc = _get_service()
        event = await run_in_threadpool(svc.get_event, uid)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return event.model_dump()

    @router.post("/refresh", response_model=ICSRefreshResult)
    async def refresh_cache() -> ICSRefreshResult:
        """Manually trigger a cache refresh of the ICS feed."""
        svc = _get_service()
        try:
            result = await svc.refresh()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"ICS error: {exc}")
        return result

    @router.get("/status")
    async def cache_status() -> dict:
        """Return cache status: last refresh, event count, errors."""
        svc = _get_service()
        return {
            "name": svc.config.name,
            "url": svc.config.url,
            "events_cached": len(svc.events),
            "last_refreshed": svc.last_refreshed.isoformat() if svc.last_refreshed else None,
            "last_error": svc.last_error,
        }

    return router


# Legacy module-level router (for backward compatibility).
router = create_ics_router()
