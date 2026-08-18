"""FastAPI router for CalDAV calendar endpoints.

When CalDAV is configured, this router provides task endpoints:

  GET    /tasks                  – list tasks
  GET    /tasks/{uid}            – get a single task
  POST   /tasks                  – create a task (editable calendar only)
  PUT    /tasks/{uid}            – update a task (editable calendar only)
  DELETE /tasks/{uid}            – delete a task (editable calendar only)

Event and calendar listing endpoints are provided by the unified
router (:mod:`app.unified_routes`).

All endpoints require API key authentication when MCP_API_KEY is set.
Returns 503 if CalDAV is not configured (CALDAV_URL unset) — but in
practice, this router is only mounted when CalDAV IS configured.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

from .auth import verify_api_key
from .caldav_models import (
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


def create_caldav_router(*, include_write: bool = True) -> APIRouter:
    """Build the CalDAV-specific router.

    This router is only mounted when CalDAV is configured.  It provides
    calendar listing and task management.  Event endpoints are handled
    by the unified events router.

    When ``include_write`` is False, only read endpoints (GET) are
    registered — POST/PUT/DELETE are omitted.  This is used when
    no editable calendar is configured.
    """
    router = APIRouter(
        prefix="",
        tags=["calendar"],
        dependencies=[Depends(verify_api_key)],
    )

    # ------------------------------------------------------------------ #
    # Task endpoints — read (always available when CalDAV is configured)
    # ------------------------------------------------------------------ #

    @router.get("/tasks", response_model=TaskListResponse)
    async def list_tasks() -> TaskListResponse:
        """List tasks."""
        svc = _get_service()
        try:
            tasks = await run_in_threadpool(svc.list_tasks)
        except Exception:
            logger.exception("Failed to list tasks")
            raise HTTPException(status_code=502, detail="CalDAV service error")
        return TaskListResponse(tasks=tasks, total=len(tasks))

    @router.get("/tasks/{uid}", response_model=CalendarTask)
    async def get_task(uid: str) -> CalendarTask:
        """Get a task by UID."""
        svc = _get_service()
        try:
            task = await run_in_threadpool(svc.get_task, uid)
        except Exception:
            logger.exception("Failed to get task %s", uid)
            raise HTTPException(status_code=502, detail="CalDAV service error")
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    # ------------------------------------------------------------------ #
    # Task endpoints — write (only when an editable calendar is configured)
    # ------------------------------------------------------------------ #

    if include_write:
        @router.post("/tasks", response_model=CalendarTask, status_code=201)
        async def create_task(req: CreateTaskRequest) -> CalendarTask:
            """Create a task."""
            svc = _get_service()
            try:
                return await run_in_threadpool(svc.create_task, req)
            except CalDAVError:
                logger.exception("CalDAV error creating task")
                raise HTTPException(status_code=502, detail="CalDAV service error")
            except Exception:
                logger.exception("Unexpected error creating task")
                raise HTTPException(status_code=502, detail="CalDAV service error")

        @router.put("/tasks/{uid}", response_model=CalendarTask)
        async def update_task(uid: str, req: UpdateTaskRequest) -> CalendarTask:
            """Update a task."""
            svc = _get_service()
            try:
                return await run_in_threadpool(svc.update_task, uid, req)
            except CalDAVError:
                logger.exception("CalDAV error updating task %s", uid)
                raise HTTPException(status_code=400, detail="CalDAV service error")
            except Exception:
                logger.exception("Unexpected error updating task %s", uid)
                raise HTTPException(status_code=502, detail="CalDAV service error")

        @router.delete("/tasks/{uid}", response_model=DeleteResponse)
        async def delete_task(uid: str) -> DeleteResponse:
            """Delete a task."""
            svc = _get_service()
            try:
                deleted = await run_in_threadpool(svc.delete_task, uid)
            except CalDAVError:
                logger.exception("CalDAV error deleting task %s", uid)
                raise HTTPException(status_code=400, detail="CalDAV service error")
            except Exception:
                logger.exception("Unexpected error deleting task %s", uid)
                raise HTTPException(status_code=502, detail="CalDAV service error")
            if not deleted:
                raise HTTPException(status_code=404, detail="Task not found")
            return DeleteResponse(deleted=True, uid=uid)

    return router


# Legacy module-level router (for backward compatibility with any code
# that imports ``router`` directly).  In the new modular design, prefer
# :func:`create_caldav_router` via :func:`app.main.create_app`.
router = create_caldav_router()
