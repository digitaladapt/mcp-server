"""Unified/aggregate events API router.

This router provides a single set of endpoints that fan out across all
registered calendar providers (CalDAV, ICS, future sources), merge
results, and present a unified view.

Exposes (when at least one provider is registered):
  GET    /events              – list events across all providers
  GET    /events/{uid}        – get a single event by UID

Exposes (only when an editable provider is registered):
  POST   /events              – create an event (editable calendar only)
  PUT    /events/{uid}        – update an event (editable calendar only)
  DELETE /events/{uid}        – delete an event (editable calendar only)

The router is built dynamically via :func:`create_unified_router` so
that endpoints only exist when their prerequisites are met.  If no
provider is registered, the router has no routes.  If only read-only
providers exist, only GET endpoints are included.

All endpoints require API key authentication when MCP_API_KEY is set.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from .auth import verify_api_key
from .caldav_models import (
    CalendarEvent,
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
) -> APIRouter:
    """Build the unified events router.

    Parameters
    ----------
    include_read:
        Whether to include GET endpoints (list events, get event).
    include_write:
        Whether to include POST/PUT/DELETE endpoints (create/update/delete).
        Only set this to True when an editable provider is registered.
    """
    router = APIRouter(
        prefix="",
        tags=["calendar"],
        dependencies=[Depends(verify_api_key)],
    )

    if not include_read and not include_write:
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
            """List events across all configured calendar providers.

            Fans out to every registered provider (CalDAV, ICS, etc.),
            merges results, sorts by start time, and deduplicates by UID.
            """
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
            except CalDAVError as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Calendar error: {exc}")

        @router.put("/events/{uid}", response_model=CalendarEvent)
        async def update_event(uid: str, req: UpdateEventRequest) -> CalendarEvent:
            """Update an existing event on the editable calendar."""
            from .caldav_routes import _get_service as _get_caldav_service
            from .caldav_service import CalDAVError

            svc = _get_caldav_service()
            try:
                return await run_in_threadpool(svc.update_event, uid, req)
            except CalDAVError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Calendar error: {exc}")

        @router.delete("/events/{uid}", response_model=DeleteResponse)
        async def delete_event(uid: str) -> DeleteResponse:
            """Delete an event from the editable calendar."""
            from .caldav_routes import _get_service as _get_caldav_service
            from .caldav_service import CalDAVError

            svc = _get_caldav_service()
            try:
                deleted = await run_in_threadpool(svc.delete_event, uid)
            except CalDAVError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Calendar error: {exc}")
            if not deleted:
                raise HTTPException(status_code=404, detail="Event not found")
            return DeleteResponse(deleted=True, uid=uid)

    return router
