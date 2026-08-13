"""Shared pytest fixtures for the MCP Server test suite."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Registry helpers
# --------------------------------------------------------------------------- #

def write_registry_file(path: Path, name: str, **fields: str) -> None:
    """Write a minimal valid YAML registry file to *path*."""
    lines = [f"name: {name}"]
    desc = fields.pop("description", f"Test command {name}.")
    lines.append(f"description: {desc}")
    exe = fields.pop("executable", "/bin/echo")
    lines.append(f"executable: {exe}")
    args = fields.pop("args", None)
    if args:
        lines.append("args:")
        for arg in args:
            lines.append(f"  - name: {arg['name']}")
            lines.append(f"    type: {arg.get('type', 'string')}")
            if "required" in arg:
                lines.append(f"    required: {str(arg['required']).lower()}")
            if "default" in arg:
                lines.append(f"    default: {arg['default']}")
            if "choices" in arg:
                lines.append(f"    choices: {arg['choices']}")
            if "help" in arg:
                lines.append(f"    help: {arg['help']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def tmp_registry(tmp_path: Path) -> Path:
    """A clean temp directory that can be used as a registry dir."""
    reg = tmp_path / "registry"
    reg.mkdir()
    return reg


@pytest.fixture
def good_registry(tmp_registry: Path) -> Path:
    """A registry dir with a few valid command files."""
    write_registry_file(
        tmp_registry / "hello.yaml", "hello",
        executable="/bin/echo",
        args=[{"name": "name", "type": "string", "required": True}],
    )
    write_registry_file(
        tmp_registry / "log_read.yaml", "log_read",
        executable="/bin/cat",
        args=[
            {"name": "file", "type": "string", "required": False},
        ],
    )
    return tmp_registry


@pytest.fixture
def registry_with_bad_files(tmp_registry: Path) -> Path:
    """A registry dir containing good, broken, and duplicate files."""
    # Good
    write_registry_file(
        tmp_registry / "good.yaml", "good",
        executable="/bin/echo",
        args=[{"name": "msg", "type": "string", "required": True}],
    )
    # Bad YAML
    (tmp_registry / "broken.yaml").write_text(
        "name: broken\n  bad: yaml: structure\n", encoding="utf-8"
    )
    # Missing required fields
    (tmp_registry / "incomplete.yaml").write_text(
        "name: incomplete\n# no description or executable\n", encoding="utf-8"
    )
    # Duplicate name (same as good)
    write_registry_file(
        tmp_registry / "dup.yaml", "good",
        description="Duplicate of good.",
        executable="/bin/echo",
    )
    return tmp_registry


# --------------------------------------------------------------------------- #
# App / client fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def app_client() -> Generator[TestClient, None, None]:
    """A FastAPI TestClient using the real app + real registry.

    Rebuilds the app from scratch to pick up current env vars.
    This is important for the conditional endpoint registration —
    the app is built at creation time based on what's configured.
    """
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def app_client_no_caldav(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with CalDAV unconfigured.

    Cleans up all calendar-related env vars so the app starts with
    only registry commands (and optionally Gitea if configured).
    """
    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("ICS_CALENDAR_URL", raising=False)

    from app.caldav_routes import _reset_service as _reset_caldav
    from app.ics_routes import _reset_service as _reset_ics
    _reset_caldav()
    _reset_ics()

    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client

    _reset_caldav()
    _reset_ics()
