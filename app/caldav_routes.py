"""FastAPI router for CalDAV calendar endpoints.

Exposes:
  GET    /calendars              – list all accessible calendars
  GET    /events                 – list events (optional date range)
  GET    /events/{uid}           – get a single event
  POST   /events                 – create an event (editable calendar only)
  PUT    /events/{uid}           – update an event (editable calendar only)
  DELETE /events/{uid}           – delete an event (editable calendar only)
  GET    /tasks                  – list tasks
  GET    /tasks/{uid}            – get a single task
  POST   /tasks                  – create a task (editable calendar only)
  PUT    /tasks/{uid}            – update a task (editable calendar only)
  DELETE /tasks/{uid}            – delete a task (editable calendar only)

All endpoints require API key authentication when MCP_API_KEY is set.
Returns 503 if CalDAV is not configured (CALDAV_URL unset).
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .auth import verify_api_key
from .caldav_models import (
    CalendarEvent,
    CalendarListResponse,
    CalendarTask,
    CreateEventRequest,
    CreateTaskRequest,
    DeleteResponse,
    EventListResponse,
    TaskListResponse,
    UpdateEventRequest,
    UpdateTaskRequest,
)
from .caldav_service import CalDAVError, CalDAVService

router = APIRouter(prefix="", tags=["calendar"], dependencies=[Depends(verify_api_key)])

# Singleton service — lazily initialised from env vars.
_service: CalDAVService | None = None
_service_inited: bool = False


def _get_service() -> CalDAVService:
    """Return the CalDAVService singleton, or raise 503 if not configured."""
    global _service, _service_inited
    if not _service_inited:
        from .caldav_models import CalDAVConfig
        config = CalDAVConfig.from_env()
        if config is None:
            _service_inited = True
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CalDAV is not configured. Set CALDAV_URL and related env vars.",
            )
        _service = CalDAVService(config)
        _service_inited = True

    if _service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CalDAV is not configured. Set CALDAV_URL and related env vars.",
        )
    return _service


def _reset_service() -> None:
    """Reset the singleton (used in tests)."""
    global _service, _service_inited
    _service = None
    _service_inited = False


# --------------------------------------------------------------------------- #
# Parse helper for query params
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


# --------------------------------------------------------------------------- #
# Calendar endpoints
# --------------------------------------------------------------------------- #

@router.get("/calendars", response_model=CalendarListResponse)
async def list_calendars() -> CalendarListResponse:
    """List all accessible calendars with editability info."""
    svc = _get_service()
    try:
        cals = svc.list_calendars()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    editable = [c for c in cals if c.editable]
    readonly = [c for c in cals if not c.editable]
    return CalendarListResponse(
        calendars=cals,
        editable_count=len(editable),
        readonly_count=len(readonly),
    )


# --------------------------------------------------------------------------- #
# Event endpoints
# --------------------------------------------------------------------------- #

@router.get("/events", response_model=EventListResponse)
async def list_events(
    start: str | None = Query(None, description="ISO 8601 start datetime/date"),
    end: str | None = Query(None, description="ISO 8601 end datetime/date"),
) -> EventListResponse:
    """List events across all accessible calendars in a date range."""
    svc = _get_service()
    start_dt = _parse_dt_param(start)
    end_dt = _parse_dt_param(end)
    try:
        events = svc.list_events(start=start_dt, end=end_dt)
    except CalDAVError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    return EventListResponse(events=events, total=len(events))


@router.get("/events/{uid}", response_model=CalendarEvent)
async def get_event(uid: str) -> CalendarEvent:
    """Get a single event by UID."""
    svc = _get_service()
    try:
        event = svc.get_event(uid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/events", response_model=CalendarEvent, status_code=201)
async def create_event(req: CreateEventRequest) -> CalendarEvent:
    """Create a new event on the editable calendar."""
    svc = _get_service()
    try:
        return svc.create_event(req)
    except CalDAVError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")


@router.put("/events/{uid}", response_model=CalendarEvent)
async def update_event(uid: str, req: UpdateEventRequest) -> CalendarEvent:
    """Update an existing event on the editable calendar."""
    svc = _get_service()
    try:
        return svc.update_event(uid, req)
    except CalDAVError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")


@router.delete("/events/{uid}", response_model=DeleteResponse)
async def delete_event(uid: str) -> DeleteResponse:
    """Delete an event from the editable calendar."""
    svc = _get_service()
    try:
        deleted = svc.delete_event(uid)
    except CalDAVError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")
    return DeleteResponse(deleted=True, uid=uid)


# --------------------------------------------------------------------------- #
# Task endpoints
# --------------------------------------------------------------------------- #

@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks() -> TaskListResponse:
    """List tasks across all accessible calendars."""
    svc = _get_service()
    try:
        tasks = svc.list_tasks()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    return TaskListResponse(tasks=tasks, total=len(tasks))


@router.get("/tasks/{uid}", response_model=CalendarTask)
async def get_task(uid: str) -> CalendarTask:
    """Get a single task by UID."""
    svc = _get_service()
    try:
        task = svc.get_task(uid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks", response_model=CalendarTask, status_code=201)
async def create_task(req: CreateTaskRequest) -> CalendarTask:
    """Create a new task on the editable calendar."""
    svc = _get_service()
    try:
        return svc.create_task(req)
    except CalDAVError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")


@router.put("/tasks/{uid}", response_model=CalendarTask)
async def update_task(uid: str, req: UpdateTaskRequest) -> CalendarTask:
    """Update an existing task on the editable calendar."""
    svc = _get_service()
    try:
        return svc.update_task(uid, req)
    except CalDAVError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")


@router.delete("/tasks/{uid}", response_model=DeleteResponse)
async def delete_task(uid: str) -> DeleteResponse:
    """Delete a task from the editable calendar."""
    svc = _get_service()
    try:
        deleted = svc.delete_task(uid)
    except CalDAVError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return DeleteResponse(deleted=True, uid=uid)
