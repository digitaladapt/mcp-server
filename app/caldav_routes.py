"""FastAPI router for CalDAV calendar endpoints.

When CalDAV is configured, this router provides:

  GET    /calendars              – list all accessible calendars
  GET    /tasks                  – list tasks
  GET    /tasks/{uid}            – get a single task
  POST   /tasks                  – create a task (editable calendar only)
  PUT    /tasks/{uid}            – update a task (editable calendar only)
  DELETE /tasks/{uid}            – delete a task (editable calendar only)

Event endpoints (GET/POST/PUT/DELETE /events) are provided by the
unified events router (:mod:`app.unified_routes`), which fans out
across all registered calendar providers.

All endpoints require API key authentication when MCP_API_KEY is set.
Returns 503 if CalDAV is not configured (CALDAV_URL unset) — but in
practice, this router is only mounted when CalDAV IS configured.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from .auth import verify_api_key
from .caldav_models import (
    CalendarListResponse,
    CalendarTask,
    CreateTaskRequest,
    DeleteResponse,
    TaskListResponse,
    UpdateTaskRequest,
)
from .caldav_service import CalDAVError, CalDAVService

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


def create_caldav_router() -> APIRouter:
    """Build the CalDAV-specific router.

    This router is only mounted when CalDAV is configured.  It provides
    calendar listing and task management.  Event endpoints are handled
    by the unified events router.
    """
    router = APIRouter(
        prefix="",
        tags=["calendar"],
        dependencies=[Depends(verify_api_key)],
    )

    # ------------------------------------------------------------------ #
    # Calendar listing
    # ------------------------------------------------------------------ #

    @router.get("/calendars", response_model=CalendarListResponse)
    async def list_calendars() -> CalendarListResponse:
        """List all accessible calendars with editability info."""
        svc = _get_service()
        try:
            cals = await run_in_threadpool(svc.list_calendars)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
        editable = [c for c in cals if c.editable]
        readonly = [c for c in cals if not c.editable]
        return CalendarListResponse(
            calendars=cals,
            editable_count=len(editable),
            readonly_count=len(readonly),
        )

    # ------------------------------------------------------------------ #
    # Task endpoints
    # ------------------------------------------------------------------ #

    @router.get("/tasks", response_model=TaskListResponse)
    async def list_tasks() -> TaskListResponse:
        """List tasks across all accessible calendars."""
        svc = _get_service()
        try:
            tasks = await run_in_threadpool(svc.list_tasks)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
        return TaskListResponse(tasks=tasks, total=len(tasks))

    @router.get("/tasks/{uid}", response_model=CalendarTask)
    async def get_task(uid: str) -> CalendarTask:
        """Get a single task by UID."""
        svc = _get_service()
        try:
            task = await run_in_threadpool(svc.get_task, uid)
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
            return await run_in_threadpool(svc.create_task, req)
        except CalDAVError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")

    @router.put("/tasks/{uid}", response_model=CalendarTask)
    async def update_task(uid: str, req: UpdateTaskRequest) -> CalendarTask:
        """Update an existing task on the editable calendar."""
        svc = _get_service()
        try:
            return await run_in_threadpool(svc.update_task, uid, req)
        except CalDAVError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")

    @router.delete("/tasks/{uid}", response_model=DeleteResponse)
    async def delete_task(uid: str) -> DeleteResponse:
        """Delete a task from the editable calendar."""
        svc = _get_service()
        try:
            deleted = await run_in_threadpool(svc.delete_task, uid)
        except CalDAVError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"CalDAV error: {exc}")
        if not deleted:
            raise HTTPException(status_code=404, detail="Task not found")
        return DeleteResponse(deleted=True, uid=uid)

    return router


# Legacy module-level router (for backward compatibility with any code
# that imports ``router`` directly).  In the new modular design, prefer
# :func:`create_caldav_router` via :func:`app.main.create_app`.
router = create_caldav_router()
