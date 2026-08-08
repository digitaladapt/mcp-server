"""Command executor.

Validates incoming arguments against a :class:`CommandSchema`, builds the
command line, and runs it via :func:`asyncio.create_subprocess_exec`.

All validation failures raise ``ValueError`` so the API layer can map them
to HTTP 400 responses.

Key design decisions:
- Uses ``asyncio.create_subprocess_exec`` with ``start_new_session=True``
  so the child and all its descendants form a new process group.
- On timeout, sends ``SIGKILL`` to the **entire process group** via
  ``os.killpg`` — this ensures zombie grandchildren (e.g. ``curl`` spawned
  by a shell script) are reaped and don't hold the stdout pipe open.
- The coroutine is truly async: it never blocks the event loop while
  waiting for the subprocess to complete.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
from typing import Any

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
    schema: CommandSchema, arguments: dict[str, Any]
) -> list[str]:
    """Validate ``arguments`` and return the constructed argv list.

    Raises ``ValueError`` on any validation problem.
    """
    cmd: list[str] = [schema.executable]
    provided = dict(arguments)

    for spec in schema.args:
        name = spec.name

        # ---- hidden arg (always applied, never visible to caller) ----
        if spec.hidden:
            if spec.is_flag:
                if spec.has_default and spec.default:
                    cmd.append(name)
            elif spec.has_default:
                if spec.is_positional:
                    cmd.append(str(spec.default))
                else:
                    cmd.extend([name, str(spec.default)])
            continue

        # ---- flag (presence-only) -----------------------------------
        if spec.is_flag:
            if name in provided:
                val = provided.pop(name)
            else:
                val = spec.default if spec.has_default else False
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


async def run_command(
    schema: CommandSchema,
    arguments: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> ExecuteResult:
    """Validate, build, and execute ``schema`` with ``arguments``.

    Returns an :class:`ExecuteResult`.  Never raises for a failing command
    (non-zero exit) — only raises ``ValueError`` for validation problems.

    The subprocess is started in a new session (``start_new_session=True``)
    so that on timeout we can kill the **entire process group** — including
    any grandchildren (e.g. ``curl`` spawned by a shell script) that would
    otherwise survive as zombies and hold the stdout pipe open.
    """
    cmd = _validate_and_build(schema, arguments)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise ValueError(
            f"Executable not found: {schema.executable}"
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutExpired:
        # Kill the entire process group — not just the direct child.
        # This is critical: if discord.sh spawns curl, and curl hangs,
        # killing only bash leaves curl alive as an orphan, still
        # holding the stdout pipe fd open.  communicate() would then
        # block forever waiting for EOF, and the 30s timeout becomes
        # meaningless.
        _kill_process_group(proc.pid)

        # Best-effort: try to read whatever output was produced.
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=2,
            )
        except (asyncio.TimeoutExpired, Exception):  # noqa: BLE001
            stdout, stderr = b"", b""

        raise ValueError(
            f"Command timed out after {timeout}s: "
            f"{' '.join(shlex.quote(c) for c in cmd)}"
        )

    return ExecuteResult(
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        exit_code=proc.returncode if proc.returncode is not None else -1,
        success=proc.returncode == 0,
    )


def _kill_process_group(pid: int) -> None:
    """Send SIGKILL to the entire process group of ``pid``.

    The subprocess must have been started with ``start_new_session=True``
    so that it became a process-group leader.
    """
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Process already exited or we don't have permission — nothing to do.
        pass
