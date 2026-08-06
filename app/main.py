"""FastAPI entry point for the MCP Server.

Exposes:
  GET  /health            – liveness probe (no auth required)
  GET  /commands          – list all registered commands (auth if configured)
  GET  /commands/{name}   – retrieve a single command's schema
  GET  /validate          – validate all registry files
  POST /execute           – validate payload and run a command
"""

import logging

from fastapi import Depends, FastAPI, HTTPException

from .auth import verify_api_key
from .caldav_routes import router as caldav_router
from .executor import run_command
from .models import CommandSchema, ExecuteRequest, ExecuteResult, ValidationResult
from .registry import get_command_schema, list_commands, validate_registry

# Configure logging so registry warnings (and any other log messages)
# are visible alongside uvicorn's output.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="MCP Server",
    description="Modular Command Provider – exposes CLI commands as model tools.",
    version="0.3.0",
)

# Register the CalDAV calendar router (endpoints return 503 if unconfigured).
app.include_router(caldav_router)


@app.get("/health")
async def health() -> dict:
    """Liveness probe.  Always accessible — no API key required."""
    return {"status": "ok"}


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


@app.post("/execute", response_model=ExecuteResult)
async def execute(
    req: ExecuteRequest,
    _: bool = Depends(verify_api_key),
) -> ExecuteResult:
    """Validate and execute a registered command."""
    schema = get_command_schema(req.command)
    if schema is None:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        return run_command(schema, req.arguments)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
