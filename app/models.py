"""Pydantic models for command schemas and request/response payloads.

Used by the registry loader, executor, API layer, and client library.
"""

from typing import Any

from pydantic import BaseModel, field_serializer, field_validator

#: Sentinel used to distinguish "no default set" from a falsy default
#: like ``False``, ``0``, ``""``, or ``None`` (explicitly null in YAML).
UNSET: Any = object()


class ValidationIssue(BaseModel):
    """A single issue found when validating a registry file."""

    file: str
    status: str  # "ok", "error", "warning"
    command: str | None = None
    message: str | None = None


class ValidationResult(BaseModel):
    """Overall result of validating the registry."""

    valid: bool
    total: int
    errors: int
    warnings: int
    issues: list[ValidationIssue] = []


class ArgSpec(BaseModel):
    """Specification for a single CLI argument.

    ``name`` may be a positional placeholder (e.g. ``"path"``) or a flag
    (e.g. ``"--verbose"``).  The ``type`` field controls how values are
    validated and cast before being passed to the subprocess.
    """

    name: str
    type: str
    required: bool = False
    choices: list[Any] | None = None
    default: Any = UNSET

    help: str | None = None
    # Optional clean name for the native tool parameter.  When set, this
    # becomes the Pydantic field name (and the OpenAPI property name),
    # while ``name`` is kept as the alias for CLI argument building.
    # e.g. name="-t", field_name="title" → the tool shows ``title``
    # but the executor still passes ``-t`` to the script.
    field_name: str | None = None
    # When True, the arg is hidden from the tool surface (excluded from
    # the Pydantic model / OpenAPI schema) but always applied with its
    # ``default`` value by the executor.  Used for flags that must always
    # be passed but should never be visible to or controllable by the
    # model (e.g. ``-q`` quiet mode on discord.sh).
    hidden: bool = False

    @property
    def has_default(self) -> bool:
        """True when a default value was explicitly set.

        Distinguishes between "no default in YAML" (``UNSET``) and
        "default: null" in YAML (``None``).
        """
        return self.default is not UNSET

    @field_serializer("default")
    def _serialize_default(self, v: Any) -> Any:
        """Serialize UNSET as None for JSON/API output."""
        return None if v is UNSET else v

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
    """Full description of a registrable command.

    ``requires`` is an optional list of environment-variable conditions
    that must all be satisfied for the command to be available.
    When any condition is unmet, the command is filtered out at load
    time — it won't appear in ``GET /commands`` and no dedicated route
    is generated.

    Each condition is a string evaluated as follows:

    * ``"ENV_VAR"`` (shorthand) — the variable must be set and truthy
      (not ``"false"``, ``"0"``, or ``""``).
    * ``"ENV_VAR != value"`` — the variable's value (or empty string
      if unset) must not equal *value* (case-insensitive).
      Useful for vars that default to enabled when unset, e.g.
      ``"MCP_LOG_ENABLED != false"``.
    * ``"ENV_VAR == value"`` — the variable's value must equal *value*.

    Example::

        requires:
          - "MCP_LOG_ENABLED != false"
    """

    name: str
    description: str
    executable: str
    args: list[ArgSpec] = []
    requires: list[str] = []


class ExecuteResult(BaseModel):
    """Structured result returned after running a command."""

    stdout: str
    stderr: str
    exit_code: int
    success: bool
