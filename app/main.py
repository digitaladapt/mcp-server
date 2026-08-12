"""FastAPI entry point for the MCP Server.

Exposes:
  GET  /api/health        – liveness probe (no auth required)
  GET  /api/about          – app name & version (no auth required)
  GET  /commands          – list all registered commands (auth if configured)
  GET  /commands/{name}   – retrieve a single command's schema
  GET  /validate          – validate all registry files
  POST /{command}         – dedicated route per registry command (auto-generated)

  ICS calendar endpoints (read-only, cached):
  GET  /ics/calendars     – list ICS calendar sources
  GET  /ics/events        – list cached events
  GET  /ics/events/{uid}  – get a single cached event
  POST /ics/refresh       – manually refresh the ICS cache
  GET  /ics/status        – cache status

Registry commands are only exposed through their dedicated routes, so the
platform surfaces each one as a native, typed tool.  There is no generic
execute endpoint.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from . import __version__
from .auth import verify_api_key
from .caldav_routes import router as caldav_router
from .gitea_routes import router as gitea_router
from .ics_models import ICSConfig
from .ics_routes import router as ics_router
from .jobs import job_scheduler
from .models import CommandSchema, ValidationResult
from .registry import get_command_schema, list_commands, validate_registry
from .registry_routes import router as registry_router

# Configure logging so registry warnings (and any other log messages)
# are visible alongside uvicorn's output.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup/shutdown of periodic background jobs.

    On startup, registers the ICS cache refresh job (if configured) and
    starts all registered jobs.  On shutdown, cleanly cancels them.
    """
    # Register ICS refresh job if configured.
    ics_config = ICSConfig.from_env()
    if ics_config is not None:
        from .ics_routes import _get_service
        try:
            svc = _get_service()
        except HTTPException:
            svc = None
        if svc is not None:
            async def _refresh_ics() -> None:
                await svc.refresh()
            job_scheduler.register(
                name="ics-cache-refresh",
                interval=ics_config.refresh_interval,
                func=_refresh_ics,
            )

    await job_scheduler.start_all()
    logger.info("Background jobs started: %s", job_scheduler.job_names)

    yield

    await job_scheduler.stop_all()
    logger.info("Background jobs stopped")


app = FastAPI(
    title="MCP Server",
    description="Modular Command Provider – exposes CLI commands as model tools.",
    version=__version__,
    lifespan=lifespan,
)

# Register the CalDAV calendar router (endpoints return 503 if unconfigured).
app.include_router(caldav_router)

# Register the ICS calendar router (read-only, cached; 503 if unconfigured).
app.include_router(ics_router)

# Register the Gitea integration router (endpoints return 503 if unconfigured).
app.include_router(gitea_router)

# Register auto-generated routes for registry commands (discord, log, etc.).
# Each command gets its own POST /{name} route with typed parameters.
app.include_router(registry_router)


@app.get("/api/health")
async def health() -> dict:
    """Liveness probe.  Always accessible — no API key required."""
    return {"status": "healthy"}


@app.get("/api/about")
async def about() -> dict:
    """Return app name and version.  No API key required."""
    return {"name": "mcp-server", "version": app.version}


@app.get("/commands", response_model=list[CommandSchema])
async def get_all_commands(_: bool = Depends(verify_api_key)) -> list[CommandSchema]:
    """List every registered command."""
    return list_commands()


@app.get("/commands/{name}", response_model=CommandSchema)
async def get_command(
    name: str,
    _: bool = Depends(verify_api_key),
) -> CommandSchema:
    """Retrieve the schema for a single command."""
    schema = get_command_schema(name)
    if schema is None:
        raise HTTPException(status_code=404, detail="Command not found")
    return schema


@app.get("/validate", response_model=ValidationResult)
async def validate(_: bool = Depends(verify_api_key)) -> ValidationResult:
    """Validate all registry files and return a detailed report."""
    return validate_registry()


@app.get("/jobs")
async def list_jobs(_: bool = Depends(verify_api_key)) -> list[dict]:
    """List status of all periodic background jobs."""
    return [j.to_dict() for j in job_scheduler.status()]
