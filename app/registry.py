"""Command registry loader.

Scans ``~/projects/mcp_server/registry/`` for ``.yaml``/``.yml``/``.json``
files on import and parses each into a :class:`CommandSchema`, keyed by
``schema.name``.

Duplicate names raise ``ValueError`` at load time so we fail fast rather
than silently shadowing a command.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

import yaml

from .models import CommandSchema, ValidationIssue, ValidationResult

logger = logging.getLogger(__name__)

# Project root (the parent of the ``app/`` package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Directory holding command-definition files.
# Override via MCP_REGISTRY_DIR env var for non-standard layouts.
REGISTRY_DIR = Path(os.environ.get("MCP_REGISTRY_DIR", PROJECT_ROOT / "registry"))

#: Sentinel used by CommandSchema to distinguish "no default set" from
#: a falsy default like ``False``, ``0``, or ``""``.
#:

# In-memory store: {command_name: CommandSchema}
COMMANDS: dict[str, CommandSchema] = {}


def _load_file(path: Path) -> CommandSchema:
    """Parse a single YAML or JSON definition file.

    If the ``executable`` is a relative path, it is resolved against the
    project root so registry files are portable across machines and
    containers.
    """
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unsupported registry file type: {path}")
    schema = CommandSchema(**data)
    # Resolve relative executable paths against the project root.
    if not os.path.isabs(schema.executable):
        schema.executable = str(PROJECT_ROOT / schema.executable)
    return schema


def load_registry(registry_dir: Path = REGISTRY_DIR) -> None:
    """Scan ``registry_dir`` and populate :data:`COMMANDS`.

    Called automatically on import.  Re-callable to refresh.
    """
    COMMANDS.clear()
    if not registry_dir.is_dir():
        # No registry dir yet — that's fine, just empty.
        return

    for path in sorted(registry_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue
        try:
            schema = _load_file(path)
        except Exception as exc:
            logger.warning("Skipping invalid registry file %s: %s", path.name, exc)
            continue
        if schema.name in COMMANDS:
            logger.warning(
                "Skipping duplicate command '%s' from %s "
                "(already defined in another file)",
                schema.name, path.name,
            )
            continue
        COMMANDS[schema.name] = schema


def validate_registry(
    registry_dir: Path = REGISTRY_DIR,
) -> ValidationResult:
    """Check every registry file and return a detailed report.

    Unlike :func:`load_registry`, this does not mutate :data:`COMMANDS`.
    It parses each file independently, checks for structural errors,
    duplicate names, and whether the referenced executable exists on disk.

    Returns a :class:`ValidationResult` with per-file issues.
    """
    issues: list[ValidationIssue] = []
    seen_names: dict[str, str] = {}  # name -> file that first defined it

    if not registry_dir.is_dir():
        return ValidationResult(
            valid=False,
            total=0,
            errors=1,
            warnings=0,
            issues=[ValidationIssue(
                file=str(registry_dir),
                status="error",
                message=f"Registry directory does not exist: {registry_dir}",
            )],
        )

    for path in sorted(registry_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue

        # --- parse ---
        try:
            schema = _load_file(path)
        except Exception as exc:
            issues.append(ValidationIssue(
                file=path.name,
                status="error",
                message=str(exc).split("\n")[0],  # first line only
            ))
            continue

        # --- duplicate name check ---
        if schema.name in seen_names:
            issues.append(ValidationIssue(
                file=path.name,
                status="error",
                command=schema.name,
                message=(
                    f"Duplicate command name '{schema.name}' "
                    f"(already defined in {seen_names[schema.name]})"
                ),
            ))
            continue

        seen_names[schema.name] = path.name

        # --- executable existence check ---
        exec_path = Path(schema.executable)
        if not exec_path.exists():
            issues.append(ValidationIssue(
                file=path.name,
                status="warning",
                command=schema.name,
                message=f"Executable not found: {schema.executable}",
            ))
            continue

        issues.append(ValidationIssue(
            file=path.name,
            status="ok",
            command=schema.name,
        ))

    errors = sum(1 for i in issues if i.status == "error")
    warnings = sum(1 for i in issues if i.status == "warning")

    return ValidationResult(
        valid=errors == 0,
        total=len(issues),
        errors=errors,
        warnings=warnings,
        issues=issues,
    )


def get_command_schema(name: str) -> Optional[CommandSchema]:
    """Return the schema for ``name`` or ``None``."""
    return COMMANDS.get(name)


def list_commands() -> List[CommandSchema]:
    """Return all registered command schemas."""
    return list(COMMANDS.values())


# Populate on import so the rest of the app sees them immediately.
load_registry()
