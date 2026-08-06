"""Command registry loader.

Scans ``~/projects/mcp_server/registry/`` for ``.yaml``/``.yml``/``.json``
files on import and parses each into a :class:`CommandSchema`, keyed by
``schema.name``.

Duplicate names raise ``ValueError`` at load time so we fail fast rather
than silently shadowing a command.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

import yaml

from .models import CommandSchema

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
        schema = _load_file(path)
        if schema.name in COMMANDS:
            raise ValueError(
                f"Duplicate command name '{schema.name}' "
                f"from {path.name}"
            )
        COMMANDS[schema.name] = schema


def get_command_schema(name: str) -> Optional[CommandSchema]:
    """Return the schema for ``name`` or ``None``."""
    return COMMANDS.get(name)


def list_commands() -> List[CommandSchema]:
    """Return all registered command schemas."""
    return list(COMMANDS.values())


# Populate on import so the rest of the app sees them immediately.
load_registry()
