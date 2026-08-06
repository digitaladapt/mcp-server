"""Command executor.

Validates incoming arguments against a :class:`CommandSchema`, builds the
command line, and runs it via :func:`subprocess.run`.

All validation failures raise ``ValueError`` so the API layer can map them
to HTTP 400 responses.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Dict

from .models import CommandSchema, ExecuteResult

#: Hard timeout (seconds) for every executed command.
DEFAULT_TIMEOUT = 30


def _cast(value: Any, expected: str, arg_name: str) -> Any:
    """Cast ``value`` to the type expected by ``arg_name``.

    Raises ``ValueError`` on a failed cast.
    """
    if expected == "string":
        return str(value)
    if expected == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Argument '{arg_name}' must be an integer, got {value!r}"
            )
    if expected == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Argument '{arg_name}' must be a float, got {value!r}"
            )
    if expected == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError(
            f"Argument '{arg_name}' must be a boolean, got {value!r}"
        )
    # 'flag' handled separately; should not reach here.
    raise ValueError(f"Unknown type '{expected}' for argument '{arg_name}'")


def _validate_and_build(
    schema: CommandSchema, arguments: Dict[str, Any]
) -> list[str]:
    """Validate ``arguments`` and return the constructed argv list.

    Raises ``ValueError`` on any validation problem.
    """
    cmd: list[str] = [schema.executable]
    provided = dict(arguments)

    for spec in schema.args:
        name = spec.name

        # ---- flag (presence-only) -----------------------------------
        if spec.is_flag:
            val = provided.pop(name, False)
            if val:
                cmd.append(name)
            continue

        # ---- required check -----------------------------------------
        if name not in provided:
            if spec.required:
                raise ValueError(f"Missing required argument '{name}'")
            # Use default if one is defined, otherwise skip.
            if spec.has_default:
                provided[name] = spec.default
            else:
                continue

        val = provided.pop(name)

        # ---- choices check ------------------------------------------
        if spec.choices is not None and val not in spec.choices:
            raise ValueError(
                f"Argument '{name}' must be one of {spec.choices}, "
                f"got {val!r}"
            )

        # ---- type cast ----------------------------------------------
        cast_val = _cast(val, spec.type, name)

        # ---- assemble argv ------------------------------------------
        if spec.is_positional:
            cmd.append(str(cast_val))
        else:
            # --opt style flag
            if spec.type == "bool":
                if cast_val:
                    cmd.append(name)
            else:
                cmd.extend([name, str(cast_val)])

    # Reject anything the schema didn't define.
    if provided:
        raise ValueError(
            f"Unknown arguments: {', '.join(sorted(provided))}"
        )

    return cmd


def run_command(
    schema: CommandSchema,
    arguments: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> ExecuteResult:
    """Validate, build, and execute ``schema`` with ``arguments``.

    Returns an :class:`ExecuteResult`.  Never raises for a failing command
    (non-zero exit) — only raises ``ValueError`` for validation problems.
    """
    cmd = _validate_and_build(schema, arguments)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"Command timed out after {timeout}s: "
            f"{' '.join(shlex.quote(c) for c in cmd)}"
        )
    except FileNotFoundError:
        raise ValueError(
            f"Executable not found: {schema.executable}"
        )

    return ExecuteResult(
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        success=proc.returncode == 0,
    )
