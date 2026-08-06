"""FastAPI entry point for the MCP Server.

Exposes:
  GET  /health            – liveness probe
  GET  /commands          – list all registered commands
  GET  /commands/{name}   – retrieve a single command's schema
  GET  /validate          – validate all registry files
  POST /execute           – validate payload and run a command
"""

from typing import List

from fastapi import FastAPI, HTTPException

from .executor import run_command
from .models import CommandSchema, ExecuteRequest, ExecuteResult, ValidationResult
from .registry import get_command_schema, list_commands, validate_registry

app = FastAPI(
    title="MCP Server",
    description="Modular Command Provider – exposes CLI commands as model tools.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/commands", response_model=List[CommandSchema])
async def get_all_commands() -> List[CommandSchema]:
    """List every registered command."""
    return list_commands()


@app.get("/commands/{name}", response_model=CommandSchema)
async def get_command(name: str) -> CommandSchema:
    """Retrieve the schema for a single command."""
    schema = get_command_schema(name)
    if schema is None:
        raise HTTPException(status_code=404, detail="Command not found")
    return schema


@app.get("/validate", response_model=ValidationResult)
async def validate() -> ValidationResult:
    """Validate all registry files and return a detailed report."""
    return validate_registry()


@app.post("/execute", response_model=ExecuteResult)
async def execute(req: ExecuteRequest) -> ExecuteResult:
    """Validate and execute a registered command."""
    schema = get_command_schema(req.command)
    if schema is None:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        return run_command(schema, req.arguments)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
