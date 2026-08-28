"""FastAPI router for the unified notify endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from .auth import verify_api_key
from .notify_models import NotifyRequest, NotifyResponse
from .notify_service import notify_registry


def create_notify_router() -> APIRouter:
    """Build the notify router."""
    router = APIRouter(
        prefix="",
        tags=["notify"],
        dependencies=[Depends(verify_api_key)],
    )

    @router.post("/notify", response_model=NotifyResponse)
    async def notify(req: NotifyRequest) -> NotifyResponse:
        """Send a notification."""
        results = await run_in_threadpool(notify_registry.send, req)
        return NotifyResponse(
            sent=any(r.success for r in results),
            results=results,
        )

    return router
