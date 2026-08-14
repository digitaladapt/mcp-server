"""ICS calendar service singleton management.

This module manages the ICSService singleton used by the ICS provider
adapter and the unified calendar router's refresh endpoint.  The ICS
event/calendar listing endpoints have been merged into the unified
router (:mod:`app.unified_routes`); this module retains only the
service lifecycle functions.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from .ics_models import ICSConfig
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


# The ICS router has been merged into the unified calendar router.
# This module now only manages the ICSService singleton.
