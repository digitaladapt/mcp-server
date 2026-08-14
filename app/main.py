"""FastAPI entry point for the MCP Server.

Exposes a modular set of endpoints that are conditionally registered at
startup based on what is configured.  The LLM only sees endpoints that
will actually work — unconfigured features simply don't have routes.

Core endpoints (always present when any feature is configured):
  GET  /api/health        – liveness probe (no auth required)
  GET  /api/about          – app name & version (no auth required)
  GET  /commands          – list all registered commands
  GET  /commands/{name}   – retrieve a single command's schema
  GET  /validate          – validate all registry files
  POST /{command}         – dedicated route per registry command

Conditionally-registered endpoint groups:

  Calendar (when any calendar provider is configured):
    GET    /events              – list events across all providers
    GET    /events/{uid}        – get a single event
    GET    /calendars           – list all calendars with metadata
    POST   /calendars/refresh   – refresh ICS cache (when ICS configured)
    POST   /events              – create (only if editable provider)
    PUT    /events/{uid}        – update (only if editable provider)
    DELETE /events/{uid}        – delete (only if editable provider)
    GET    /tasks               – list tasks (CalDAV only)
    GET    /tasks/{uid}         – get a task (CalDAV only)
    POST   /tasks               – create a task (CalDAV editable only)
    PUT    /tasks/{uid}         – update a task (CalDAV editable only)
    DELETE /tasks/{uid}         – delete a task (CalDAV editable only)

  Gitea (when GITEA_URL is set):
    /repos, /issues, /prs, /branches, /actions, /releases, etc.

Startup safety: if nothing is configured and no registry commands are
available, the server refuses to start (no usable endpoints).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from . import __version__
from .auth import verify_api_key
from .caldav_models import CalDAVConfig
from .ics_models import ICSConfig
from .jobs import job_scheduler
from .models import CommandSchema, ValidationResult
from .notify_service import init_notify_registry
from .provider_adapters import CalDAVProvider, ICSProvider
from .providers import provider_registry, reset_provider_registry
from .registry import get_command_schema, list_commands, validate_registry
from .registry_routes import create_registry_router

# Configure logging so registry warnings (and any other log messages)
# are visible alongside uvicorn's output.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Provider discovery
# --------------------------------------------------------------------------- #

def _discover_providers() -> tuple[bool, bool]:
    """Register calendar providers based on environment configuration.

    Returns ``(has_caldav, has_ics)`` so the caller knows which
    sub-routers to mount.

    This function is called once at app creation time.  It checks
    environment variables and registers adapters for each configured
    calendar source into the global :data:`provider_registry`.
    """
    has_caldav = False
    has_ics = False

    # CalDAV provider
    caldav_config = CalDAVConfig.from_env()
    if caldav_config is not None:
        # Initialize the CalDAV service singleton so the provider
        # adapter can use it via _get_service().
        from .caldav_routes import _reset_service
        _reset_service()
        provider_registry.register(CalDAVProvider())
        has_caldav = True

    # ICS provider
    ics_config = ICSConfig.from_env()
    if ics_config is not None:
        # Initialize the ICS service singleton
        from .ics_routes import _reset_service
        _reset_service()
        provider_registry.register(ICSProvider())
        has_ics = True

    return has_caldav, has_ics


# --------------------------------------------------------------------------- #
# Lifespan (background jobs)
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #

def create_app() -> FastAPI:
    """Create the FastAPI application with conditional endpoint registration.

    This factory pattern allows us to inspect environment variables at
    creation time and only mount routers for features that are actually
    configured.  The LLM (or any API consumer) never sees endpoints
    that would return 503 — they simply don't exist.

    If nothing is configured (no calendar providers, no Gitea, and no
    registry commands), the server refuses to start.
    """
    # Reset state for clean app creation (important in tests).
    reset_provider_registry()

    # Discover and register calendar providers.
    has_caldav, has_ics = _discover_providers()

    # Check what's available.
    has_calendar = provider_registry.has_providers
    has_editable = provider_registry.has_editable
    has_gitea = _is_gitea_configured()

    # Registry commands are always available (from registry/*.yaml).
    registry_commands = list_commands()

    # Discover notify providers (Discord, future Ntfy, etc.).
    has_notify = init_notify_registry()

    # Fail-fast: if nothing is configured, refuse to start.
    if not has_calendar and not has_gitea and not registry_commands and not has_notify:
        raise RuntimeError(
            "No features configured.  Set at least one of: "
            "CALDAV_URL, ICS_CALENDAR_URL, GITEA_URL, DISCORD_GENERAL_HOOK, "
            "or ensure registry command YAML files are present."
        )

    app = FastAPI(
        title="MCP Server",
        description="Modular Command Provider – exposes CLI commands as model tools.",
        version=__version__,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ #
    # Unified calendar endpoints (conditional)
    # ------------------------------------------------------------------ #
    if has_calendar:
        from .unified_routes import create_unified_router
        unified_router = create_unified_router(
            include_read=True,
            include_write=has_editable,
            include_refresh=has_ics,
        )
        app.include_router(unified_router)

        # Mount CalDAV-specific endpoints (tasks only) when configured.
        # Calendar listing is now handled by the unified router.
        if has_caldav:
            from .caldav_routes import create_caldav_router
            app.include_router(create_caldav_router())

    # ------------------------------------------------------------------ #
    # Gitea endpoints (conditional)
    # ------------------------------------------------------------------ #
    if has_gitea:
        from .gitea_routes import router as gitea_router
        app.include_router(gitea_router)

    # ------------------------------------------------------------------ #
    # Notify endpoint (conditional)
    # ------------------------------------------------------------------ #
    if has_notify:
        from .notify_routes import create_notify_router
        app.include_router(create_notify_router())

    # ------------------------------------------------------------------ #
    # Registry command routes (always present when commands exist)
    # ------------------------------------------------------------------ #
    if registry_commands:
        app.include_router(create_registry_router())

    # ------------------------------------------------------------------ #
    # Core endpoints (always present)
    # ------------------------------------------------------------------ #

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict:
        """Liveness probe."""
        return {"status": "healthy"}

    @app.get("/api/about", include_in_schema=False)
    async def about() -> dict:
        """Return app name and version."""
        return {"name": "mcp-server", "version": app.version}

    @app.get("/commands", response_model=list[CommandSchema], include_in_schema=False)
    async def get_all_commands(
        _: bool = Depends(verify_api_key),
    ) -> list[CommandSchema]:
        """List every registered command."""
        return list_commands()

    @app.get("/commands/{name}", response_model=CommandSchema, include_in_schema=False)
    async def get_command(
        name: str,
        _: bool = Depends(verify_api_key),
    ) -> CommandSchema:
        """Retrieve the schema for a single command."""
        schema = get_command_schema(name)
        if schema is None:
            raise HTTPException(status_code=404, detail="Command not found")
        return schema

    @app.get("/validate", response_model=ValidationResult, include_in_schema=False)
    async def validate(_: bool = Depends(verify_api_key)) -> ValidationResult:
        """Validate all registry files."""
        return validate_registry()

    @app.get("/jobs", include_in_schema=False)
    async def list_jobs(_: bool = Depends(verify_api_key)) -> list[dict]:
        """List status of all periodic background jobs."""
        return [j.to_dict() for j in job_scheduler.status()]

    return app


def _is_gitea_configured() -> bool:
    """Check if Gitea is configured (GITEA_URL is set)."""
    import os
    return bool(os.environ.get("GITEA_URL", "").strip())


# Create the app instance for uvicorn / imports.
app = create_app()
