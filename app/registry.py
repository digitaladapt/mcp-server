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

# Directory holding command-definition files.
REGISTRY_DIR = Path(__file__).resolve().parent.parent / "registry"

# In-memory store: {command_name: CommandSchema}
COMMANDS: dict[str, CommandSchema] = {}


def _load_file(path: Path) -> CommandSchema:
    """Parse a single YAML or JSON definition file."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unsupported registry file type: {path}")
    return CommandSchema(**data)


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
