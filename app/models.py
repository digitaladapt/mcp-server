"""Pydantic models for command schemas and request/response payloads.

Used by the registry loader, executor, API layer, and client library.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator


class ValidationIssue(BaseModel):
    """A single issue found when validating a registry file."""

    file: str
    status: str  # "ok", "error", "warning"
    command: Optional[str] = None
    message: Optional[str] = None


class ValidationResult(BaseModel):
    """Overall result of validating the registry."""

    valid: bool
    total: int
    errors: int
    warnings: int
    issues: List[ValidationIssue] = []


class ArgSpec(BaseModel):
    """Specification for a single CLI argument.

    ``name`` may be a positional placeholder (e.g. ``"path"``) or a flag
    (e.g. ``"--verbose"``).  The ``type`` field controls how values are
    validated and cast before being passed to the subprocess.
    """

    name: str
    type: str
    required: bool = False
    choices: Optional[List[Any]] = None
    default: Optional[Any] = None
    help: Optional[str] = None

    @property
    def has_default(self) -> bool:
        """True when a default value was explicitly set."""
        return self.default is not None

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        allowed = {"string", "int", "float", "bool", "flag"}
        if v not in allowed:
            raise ValueError(
                f"arg type must be one of {allowed}, got '{v}'"
            )
        return v

    @property
    def is_flag(self) -> bool:
        """True when this arg is a presence-only flag (no value)."""
        return self.type == "flag"

    @property
    def is_positional(self) -> bool:
        """True when the arg name does not start with ``-``."""
        return not self.name.startswith("-")


class CommandSchema(BaseModel):
    """Full description of a registrable command."""

    name: str
    description: str
    executable: str
    args: List[ArgSpec] = []


class ExecuteRequest(BaseModel):
    """Payload sent to ``POST /execute``."""

    command: str
    arguments: Dict[str, Any] = {}


class ExecuteResult(BaseModel):
    """Structured result returned after running a command."""

    stdout: str
    stderr: str
    exit_code: int
    success: bool
