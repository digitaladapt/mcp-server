"""Unified calendar API router.

Provides a single set of endpoints that fan out across all registered
calendar providers (CalDAV, ICS, future sources), merge results, and
present a unified view.

Exposes (when at least one provider is registered):
  GET    /events              – list events across all providers
  GET    /events/{uid}        – get a single event by UID
  GET    /calendars           – list all calendars with metadata

Exposes (only when an editable provider is registered):
  POST   /events              – create an event (editable calendar only)
  PUT    /events/{uid}        – update an event (editable calendar only)
  DELETE /events/{uid}        – delete an event (editable calendar only)

Exposes (only when ICS is configured):
  POST   /calendars/refresh   – manually trigger an ICS cache refresh

The router is built dynamically via :func:`create_unified_router` so
that endpoints only exist when their prerequisites are met.

All endpoints require API key authentication when MCP_API_KEY is set.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

from .auth import verify_api_key
from .caldav_models import (
    CalendarEvent,
    CalendarListResponse,
    CreateEventRequest,
    DeleteResponse,
    EventListResponse,
    UpdateEventRequest,
)
from .providers import provider_registry


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


def create_unified_router(
    *,
    include_read: bool = True,
    include_write: bool = False,
    include_refresh: bool = False,
) -> APIRouter:
    """Build the unified calendar router.

    Parameters
    ----------
    include_read:
        Whether to include GET endpoints (list events, get event, list calendars).
    include_write:
        Whether to include POST/PUT/DELETE event endpoints.
        Only set this to True when an editable provider is registered.
    include_refresh:
        Whether to include the ICS cache refresh endpoint.
        Only set this to True when ICS is configured.
    """
    router = APIRouter(
        prefix="",
        tags=["calendar"],
        dependencies=[Depends(verify_api_key)],
    )

    if not include_read and not include_write and not include_refresh:
        return router

    # ------------------------------------------------------------------ #
    # Read endpoints
    # ------------------------------------------------------------------ #

    if include_read:
        @router.get("/events", response_model=EventListResponse)
        async def list_events(
            start: str | None = Query(None, description="ISO 8601 start datetime/date"),
            end: str | None = Query(None, description="ISO 8601 end datetime/date"),
        ) -> EventListResponse:
            """List events across all calendar providers."""
            start_dt = _parse_dt_param(start)
            end_dt = _parse_dt_param(end)
            events = await run_in_threadpool(
                provider_registry.list_all_events,
                start=start_dt,
                end=end_dt,
            )
            return EventListResponse(events=events, total=len(events))

        @router.get("/events/{uid}", response_model=CalendarEvent)
        async def get_event(uid: str) -> CalendarEvent:
            """Get a single event by UID across all providers."""
            event = await run_in_threadpool(provider_registry.get_event, uid)
            if event is None:
                raise HTTPException(status_code=404, detail="Event not found")
            return event

        @router.get("/calendars", response_model=CalendarListResponse)
        async def list_calendars() -> CalendarListResponse:
            """List all accessible calendars with editability info."""
            cals = await run_in_threadpool(provider_registry.list_all_calendars)
            editable = [c for c in cals if c.editable]
            readonly = [c for c in cals if not c.editable]
            return CalendarListResponse(
                calendars=cals,
                editable_count=len(editable),
                readonly_count=len(readonly),
            )

    # ------------------------------------------------------------------ #
    # Write endpoints — only included when an editable provider exists
    # ------------------------------------------------------------------ #

    if include_write:
        @router.post("/events", response_model=CalendarEvent, status_code=201)
        async def create_event(req: CreateEventRequest) -> CalendarEvent:
            """Create a new event on the editable calendar."""
            from .caldav_routes import _get_service as _get_caldav_service
            from .caldav_service import CalDAVError

            svc = _get_caldav_service()
            try:
                return await run_in_threadpool(svc.create_event, req)
            except CalDAVError:
                logger.exception("CalDAV error creating event")
                raise HTTPException(status_code=502, detail="Calendar service error")
            except Exception:
                logger.exception("Unexpected error creating event")
                raise HTTPException(status_code=502, detail="Calendar service error")

        @router.put("/events/{uid}", response_model=CalendarEvent)
        async def update_event(uid: str, req: UpdateEventRequest) -> CalendarEvent:
            """Update an existing event on the editable calendar."""
            from .caldav_routes import _get_service as _get_caldav_service
            from .caldav_service import CalDAVError

            svc = _get_caldav_service()
            try:
                return await run_in_threadpool(svc.update_event, uid, req)
            except CalDAVError:
                logger.exception("CalDAV error updating event %s", uid)
                raise HTTPException(status_code=400, detail="Calendar service error")
            except Exception:
                logger.exception("Unexpected error updating event %s", uid)
                raise HTTPException(status_code=502, detail="Calendar service error")

        @router.delete("/events/{uid}", response_model=DeleteResponse)
        async def delete_event(uid: str) -> DeleteResponse:
            """Delete an event from the editable calendar."""
            from .caldav_routes import _get_service as _get_caldav_service
            from .caldav_service import CalDAVError

            svc = _get_caldav_service()
            try:
                deleted = await run_in_threadpool(svc.delete_event, uid)
            except CalDAVError:
                logger.exception("CalDAV error deleting event %s", uid)
                raise HTTPException(status_code=400, detail="Calendar service error")
            except Exception:
                logger.exception("Unexpected error deleting event %s", uid)
                raise HTTPException(status_code=502, detail="Calendar service error")
            if not deleted:
                raise HTTPException(status_code=404, detail="Event not found")
            return DeleteResponse(deleted=True, uid=uid)

    # ------------------------------------------------------------------ #
    # ICS cache refresh — only included when ICS is configured
    # ------------------------------------------------------------------ #

    if include_refresh:
        @router.post("/calendars/refresh")
        async def refresh_cache() -> dict:
            """Manually trigger a cache refresh of ICS feeds."""
            from .ics_routes import _get_service as _get_ics_service

            svc = _get_ics_service()
            try:
                result = await svc.refresh()
            except Exception:
                logger.exception("ICS cache refresh failed")
                raise HTTPException(status_code=502, detail="ICS service error")
            return {
                "success": result.success,
                "events_cached": result.events_cached,
                "error": result.error,
                "refreshed_at": result.refreshed_at,
            }

    return router
