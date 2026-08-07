"""Auto-generated FastAPI routes for registry commands.

For each command defined in ``registry/*.yaml``, this module creates a
dedicated ``POST /{command_name}`` route with a Pydantic request model
derived from the command's argument specs.  This means the platform can
read the OpenAPI schema and surface each command as a **native tool** —
no more generic ``POST /execute`` indirection.

The generic ``POST /execute`` endpoint in ``main.py`` remains fully
functional as a fallback.

Exposes (dynamically, one per registry command):
  POST /{command_name}  – execute the command with typed arguments

All routes require API key authentication when MCP_API_KEY is set.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, create_model

from .auth import verify_api_key
from .executor import run_command
from .models import CommandSchema, ExecuteResult
from .registry import list_commands

logger = logging.getLogger(__name__)

# ── Reserved paths that already have dedicated routes ──────────────── #
# If a registry command's name collides with one of these, we skip
# generating a dedicated route (the command still works via POST /execute).
_RESERVED_PATHS: set[str] = {
    "health",
    "commands",
    "validate",
    "execute",
    "calendars",
    "events",
    "tasks",
    "repos",
    "user",
    "issues",
    "branches",
    "prs",
    "actions",
    "commits",
    "releases",
}


def _safe_field_name(arg_name: str) -> str:
    """Convert a CLI arg name to a valid Python identifier.

    ``-c``      → ``c``
    ``--level`` → ``level``
    ``message`` → ``message``
    """
    return arg_name.lstrip("-").replace("-", "_")


def _get_field_name(spec) -> str:
    """Return the Pydantic field name for an arg spec.

    If ``spec.field_name`` is set (via the YAML ``field_name`` key),
    use that clean name.  Otherwise, derive one from the CLI arg name.
    """
    if spec.field_name:
        return spec.field_name
    return _safe_field_name(spec.name)


def _type_for_spec(spec_type: str, choices: list[Any] | None) -> type:
    """Map a YAML arg type to a Python/Pydantic type.

    When ``choices`` is provided, use ``Literal`` so OpenAPI shows an enum.
    """
    if choices:
        # Literal["a", "b", "c"] — all choice values are strings in our YAML
        return Literal[tuple(choices)]  # type: ignore[valid-type]

    type_map = {
        "string": str,
        "int": int,
        "float": float,
        "bool": bool,
        "flag": bool,
    }
    return type_map.get(spec_type, str)


def _field_info(
    schema: CommandSchema,
    arg_name: str,
) -> tuple[Any, Any]:
    """Build a ``(type, default)`` tuple for ``create_model``.

    Returns ``(python_type, FieldInfo)`` where FieldInfo carries the alias
    (original CLI arg name), default value, and description.
    """
    spec = next(s for s in schema.args if s.name == arg_name)

    py_type = _type_for_spec(spec.type, spec.choices)

    # Build field kwargs.
    # When field_name is set, we DON'T use alias — the clean name is
    # both the Python field name and the OpenAPI property name.
    # When field_name is NOT set, we use alias to preserve the original
    # CLI arg name (e.g. "-c") as the OpenAPI property name.
    field_kwargs: dict[str, Any] = {
        "description": spec.help or "",
    }
    if not spec.field_name:
        field_kwargs["alias"] = arg_name

    if spec.required:
        # Required field — no default, must be provided.
        # But if it has a default (like -q with default: true), use it.
        if spec.has_default:
            field_kwargs["default"] = spec.default
            return (py_type, Field(**field_kwargs))
        # Truly required — no default.
        return (py_type, Field(**field_kwargs))

    # Optional field
    if spec.has_default:
        field_kwargs["default"] = spec.default
    else:
        field_kwargs["default"] = None
    return (py_type | None, Field(**field_kwargs))


def build_request_model(schema: CommandSchema) -> type[BaseModel]:
    """Build a Pydantic model from a command's argument specs.

    Each arg becomes a field.  CLI-style names (``-c``, ``--level``) are
    mapped to clean Python identifiers via ``alias``, so the OpenAPI
    schema uses the original arg names as property names.
    """
    field_definitions: dict[str, Any] = {}

    for spec in schema.args:
        if spec.hidden:
            continue
        field_name = _get_field_name(spec)
        py_type, field_info = _field_info(schema, spec.name)
        field_definitions[field_name] = (py_type, field_info)

    model = create_model(
        f"{schema.name}_Request",
        **field_definitions,
        __base__=BaseModel,
    )
    # Allow population by field name AND alias so callers can use either.
    model.model_config = {
        "populate_by_name": True,
    }
    return model


def _model_to_args(model: BaseModel, schema: CommandSchema) -> dict[str, Any]:
    """Convert a validated request model back to a dict of CLI arguments.

    Maps Pydantic field names back to the original CLI arg names, and
    omits ``None`` values for optional fields that the caller didn't
    provide.
    """
    # Dump by field name (not alias) so we can look up by _get_field_name().
    raw = model.model_dump(exclude_defaults=False)
    result: dict[str, Any] = {}

    for spec in schema.args:
        field_name = _get_field_name(spec)
        val = raw.get(field_name)
        if val is None:
            continue
        # For bool/flag args, only include if truthy
        if spec.type in ("bool", "flag") and not val:
            continue
        # Use the original CLI arg name as the key for the executor.
        result[spec.name] = val

    return result


def create_registry_router() -> APIRouter:
    """Create an APIRouter with one route per registered command.

    Commands whose names collide with reserved paths (existing CalDAV,
    Gitea, or core routes) are skipped with a warning.
    """
    router = APIRouter(
        prefix="",
        tags=["commands"],
        dependencies=[Depends(verify_api_key)],
    )

    for schema in list_commands():
        name = schema.name

        if name in _RESERVED_PATHS:
            logger.warning(
                "Skipping dedicated route for command '%s' — name "
                "collides with a reserved path. Use POST /execute instead.",
                name,
            )
            continue

        # Check for duplicate field names after sanitization
        field_names = [_get_field_name(s) for s in schema.args]
        if len(field_names) != len(set(field_names)):
            logger.error(
                "Skipping command '%s' — two or more args map to the "
                "same Python field name: %s",
                name, field_names,
            )
            continue

        request_model = build_request_model(schema)

        # Build a handler whose type annotation references the specific
        # dynamically-created model.  FastAPI reads the annotation to
        # generate the OpenAPI request-body schema, so it MUST be the
        # concrete model, not the base class.
        #
        # We use a factory function to capture the schema and model via
        # default arguments (avoiding Python's late-binding closure trap),
        # then set __annotations__ manually so FastAPI sees the concrete
        # model as the body type.
        def _make_handler(
            cmd_schema: CommandSchema = schema,
            req_model: type[BaseModel] = request_model,
        ) -> Callable[..., ExecuteResult]:
            """Create a route handler bound to a specific command schema."""

            def handler(req: Any) -> ExecuteResult:
                arguments = _model_to_args(req, cmd_schema)
                try:
                    return run_command(cmd_schema, arguments)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))

            handler.__name__ = f"{cmd_schema.name}_command"
            handler.__qualname__ = f"registry_routes.{cmd_schema.name}_command"
            handler.__annotations__ = {"req": req_model, "return": ExecuteResult}
            return handler

        _handler = _make_handler()

        router.add_api_route(
            path=f"/{name}",
            endpoint=_handler,
            methods=["POST"],
            response_model=ExecuteResult,
            summary=f"{schema.description}",
            description=(
                f"Execute the **{name}** command.\n\n"
                f"Executable: `{schema.executable}`\n\n"
                "This route is auto-generated from the registry YAML. "
                "The generic `POST /execute` endpoint also works."
            ),
        )

        logger.info("Registered dedicated route: POST /%s", name)

    return router


# Build the router on import so main.py can just include it.
router = create_registry_router()
