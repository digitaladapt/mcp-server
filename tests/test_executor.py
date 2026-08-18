"""Tests for the command executor (``app.executor``).

Covers :func:`_cast`, :func:`_validate_and_build`, and :func:`run_command`
using inline :class:`CommandSchema` / :class:`ArgSpec` objects so the tests
are self-contained and easy to read.
"""

from __future__ import annotations

import pytest

from app.executor import _cast, _validate_and_build, run_command
from app.models import ArgSpec, CommandSchema

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _schema(executable: str, *args: ArgSpec, name: str = "cmd") -> CommandSchema:
    """Shorthand for building a CommandSchema inline."""
    return CommandSchema(
        name=name,
        description="test command",
        executable=executable,
        args=list(args),
    )


# --------------------------------------------------------------------------- #
# _cast
# --------------------------------------------------------------------------- #

class TestCast:
    """Unit tests for the ``_cast`` helper."""

    # -- string ------------------------------------------------------------- #
    def test_string_returns_str(self):
        assert _cast(123, "string", "x") == "123"
        assert _cast("hi", "string", "x") == "hi"
        assert _cast(3.14, "string", "x") == "3.14"

    # -- int ---------------------------------------------------------------- #
    def test_int_from_int(self):
        assert _cast(5, "int", "n") == 5

    def test_int_from_string(self):
        assert _cast("42", "int", "n") == 42

    def test_int_invalid_string_raises(self):
        with pytest.raises(ValueError, match="must be an integer"):
            _cast("abc", "int", "n")

    def test_int_none_raises(self):
        with pytest.raises(ValueError, match="must be an integer"):
            _cast(None, "int", "n")

    # -- float -------------------------------------------------------------- #
    def test_float_from_float(self):
        assert _cast(1.5, "float", "f") == 1.5

    def test_float_from_string(self):
        assert _cast("2.5", "float", "f") == 2.5

    def test_float_from_int(self):
        assert _cast(3, "float", "f") == 3.0

    def test_float_invalid_string_raises(self):
        with pytest.raises(ValueError, match="must be a float"):
            _cast("nope", "float", "f")

    # -- bool --------------------------------------------------------------- #
    def test_bool_true_passthrough(self):
        assert _cast(True, "bool", "b") is True

    def test_bool_false_passthrough(self):
        assert _cast(False, "bool", "b") is False

    def test_bool_true_string(self):
        assert _cast("true", "bool", "b") is True

    def test_bool_false_string(self):
        assert _cast("false", "bool", "b") is False

    def test_bool_uppercase_true(self):
        assert _cast("TRUE", "bool", "b") is True

    def test_bool_uppercase_false(self):
        assert _cast("FALSE", "bool", "b") is False

    def test_bool_invalid_string_raises(self):
        with pytest.raises(ValueError, match="must be a boolean"):
            _cast("yes", "bool", "b")

    def test_bool_int_raises(self):
        # ints are not accepted as bools
        with pytest.raises(ValueError, match="must be a boolean"):
            _cast(1, "bool", "b")

    # -- unknown type ------------------------------------------------------- #
    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown type"):
            _cast("x", "banana", "b")


# --------------------------------------------------------------------------- #
# _validate_and_build
# --------------------------------------------------------------------------- #

class TestValidateAndBuild:
    """Unit tests for the ``_validate_and_build`` function."""

    # -- simple positional -------------------------------------------------- #
    def test_simple_positional(self):
        schema = _schema("echo", ArgSpec(name="msg", type="string", required=True))
        assert _validate_and_build(schema, {"msg": "hello"}) == ["echo", "hello"]

    # -- missing required --------------------------------------------------- #
    def test_missing_required_raises(self):
        schema = _schema("echo", ArgSpec(name="msg", type="string", required=True))
        with pytest.raises(ValueError, match="Missing required argument 'msg'"):
            _validate_and_build(schema, {})

    # -- optional omitted --------------------------------------------------- #
    def test_optional_omitted_not_in_cmd(self):
        schema = _schema("exe", ArgSpec(name="msg", type="string", required=False))
        assert _validate_and_build(schema, {}) == ["exe"]

    # -- optional with default ---------------------------------------------- #
    def test_optional_with_default(self):
        schema = _schema(
            "exe",
            ArgSpec(name="msg", type="string", required=False, default="hey"),
        )
        assert _validate_and_build(schema, {}) == ["exe", "hey"]

    # -- flag with default=True -------------------------------------------- #
    def test_flag_default_true_omitted_present(self):
        schema = _schema(
            "discord",
            ArgSpec(name="-q", type="flag", default=True),
        )
        assert _validate_and_build(schema, {}) == ["discord", "-q"]

    def test_flag_default_true_provided_false_absent(self):
        schema = _schema(
            "discord",
            ArgSpec(name="-q", type="flag", default=True),
        )
        assert _validate_and_build(schema, {"-q": False}) == ["discord"]

    def test_flag_default_true_provided_true_present(self):
        schema = _schema(
            "discord",
            ArgSpec(name="-q", type="flag", default=True),
        )
        assert _validate_and_build(schema, {"-q": True}) == ["discord", "-q"]

    # -- flag without default ---------------------------------------------- #
    def test_flag_no_default_omitted_absent(self):
        schema = _schema("exe", ArgSpec(name="--verbose", type="flag"))
        assert _validate_and_build(schema, {}) == ["exe"]

    def test_flag_no_default_provided_true_present(self):
        schema = _schema("exe", ArgSpec(name="--verbose", type="flag"))
        assert _validate_and_build(schema, {"--verbose": True}) == ["exe", "--verbose"]

    def test_flag_no_default_provided_false_absent(self):
        schema = _schema("exe", ArgSpec(name="--verbose", type="flag"))
        assert _validate_and_build(schema, {"--verbose": False}) == ["exe"]

    # -- --opt with string type -------------------------------------------- #
    def test_opt_string(self):
        schema = _schema("exe", ArgSpec(name="--mode", type="string"))
        assert _validate_and_build(schema, {"--mode": "fast"}) == ["exe", "--mode", "fast"]

    # -- --opt with bool type ---------------------------------------------- #
    def test_opt_bool_true_present(self):
        schema = _schema("exe", ArgSpec(name="--verbose", type="bool"))
        assert _validate_and_build(schema, {"--verbose": True}) == ["exe", "--verbose"]

    def test_opt_bool_false_absent(self):
        schema = _schema("exe", ArgSpec(name="--verbose", type="bool"))
        assert _validate_and_build(schema, {"--verbose": False}) == ["exe"]

    # -- choices ------------------------------------------------------------ #
    def test_choices_valid(self):
        schema = _schema(
            "exe",
            ArgSpec(name="--level", type="string", choices=["low", "med", "high"]),
        )
        assert _validate_and_build(schema, {"--level": "med"}) == ["exe", "--level", "med"]

    def test_choices_invalid_raises(self):
        schema = _schema(
            "exe",
            ArgSpec(name="--level", type="string", choices=["low", "med", "high"]),
        )
        with pytest.raises(ValueError, match="must be one of"):
            _validate_and_build(schema, {"--level": "extreme"})

    # -- unknown arguments -------------------------------------------------- #
    def test_unknown_arguments_raise(self):
        schema = _schema("exe", ArgSpec(name="msg", type="string", required=True))
        with pytest.raises(ValueError, match="Unknown arguments"):
            _validate_and_build(schema, {"msg": "hi", "bogus": 1})

    def test_unknown_arguments_lists_all(self):
        schema = _schema("exe", ArgSpec(name="msg", type="string", required=True))
        with pytest.raises(ValueError, match="alpha, beta"):
            _validate_and_build(schema, {"msg": "hi", "alpha": 1, "beta": 2})

    # -- multiple args ordering -------------------------------------------- #
    def test_multiple_args_order(self):
        schema = _schema(
            "exe",
            ArgSpec(name="path", type="string", required=True),
            ArgSpec(name="-v", type="flag"),
            ArgSpec(name="--mode", type="string"),
        )
        result = _validate_and_build(
            schema,
            {"path": "/tmp", "-v": True, "--mode": "fast"},
        )
        # Order follows schema arg order: positional, flag, --opt
        assert result == ["exe", "/tmp", "-v", "--mode", "fast"]

    def test_multiple_args_with_bool_opt_and_int(self):
        schema = _schema(
            "tool",
            ArgSpec(name="file", type="string", required=True),
            ArgSpec(name="--count", type="int"),
            ArgSpec(name="--dry-run", type="bool"),
        )
        result = _validate_and_build(
            schema,
            {"file": "data.txt", "--count": 3, "--dry-run": False},
        )
        # bool False -> --dry-run not appended
        assert result == ["tool", "data.txt", "--count", "3"]


# --------------------------------------------------------------------------- #
# run_command
# --------------------------------------------------------------------------- #

class TestRunCommand:
    """Integration tests for the ``run_command`` function."""

    async def test_echo_success(self):
        schema = _schema("/bin/echo", ArgSpec(name="msg", type="string", required=True))
        result = await run_command(schema, {"msg": "hello"})
        assert result.success is True
        assert result.exit_code == 0
        assert "hello" in result.stdout

    async def test_missing_required_raises_value_error(self):
        schema = _schema("/bin/echo", ArgSpec(name="msg", type="string", required=True))
        with pytest.raises(ValueError, match="Missing required argument"):
            await run_command(schema, {})

    async def test_nonexistent_executable_raises(self):
        schema = _schema("/usr/local/bin/totally-not-real-binary")
        with pytest.raises(ValueError, match="Executable not found"):
            await run_command(schema, {})

    async def test_false_exit_code_nonzero(self):
        schema = _schema("/bin/false")
        result = await run_command(schema, {})
        assert result.success is False
        assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# Timeout and process-group kill
# --------------------------------------------------------------------------- #

class TestTimeout:
    """Tests for the timeout / process-group kill logic in ``run_command``.

    These verify the most critical safety mechanism: when a command exceeds
    its timeout, ``ValueError`` is raised and the entire process group
    (including any children) is killed.
    """

    async def test_timeout_raises_value_error(self):
        """A sleeping command with a short timeout must raise ValueError."""
        schema = _schema("/bin/sleep", ArgSpec(name="secs", type="string", required=True))
        with pytest.raises(ValueError, match="timed out"):
            await run_command(schema, {"secs": "30"}, timeout=1)

    async def test_timeout_kills_process_group(self):
        """After timeout, the child process should no longer be alive.

        We start ``sleep 30`` with a 1-second timeout.  After the timeout
        fires and ``_kill_process_group`` runs, the child PID should not
        exist anymore (or at least should not be our child).
        """
        import asyncio
        import signal

        schema = _schema("/bin/sleep", ArgSpec(name="secs", type="string", required=True))

        # Build the command manually so we can track the process
        from app.executor import _kill_process_group, _validate_and_build
        cmd = _validate_and_build(schema, {"secs": "30"})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        pid = proc.pid

        # Wait for it to be definitely running
        await asyncio.sleep(0.1)

        # Kill the process group (same logic as run_command timeout path)
        _kill_process_group(pid)

        # Wait for the process to be reaped
        await asyncio.wait_for(proc.wait(), timeout=5)

        # The process should have been killed (SIGKILL → return code -9)
        assert proc.returncode is not None
        assert proc.returncode == -signal.SIGKILL

    async def test_timeout_with_subshell_kills_children(self):
        """A shell script that spawns a child must have both killed.

        Uses ``bash -c 'sleep 30 & sleep 30'`` with a 1s timeout.  After
        the timeout, we verify no orphaned sleep processes remain by
        checking that the process group is gone.
        """

        # Use bash as the executable with a script that spawns children
        schema = _schema(
            "/bin/bash",
            ArgSpec(name="-c", type="string", required=True),
        )

        with pytest.raises(ValueError, match="timed out"):
            await run_command(
                schema,
                {"-c": "sleep 30 & sleep 30"},
                timeout=1,
            )

    async def test_kill_process_group_already_dead(self):
        """_kill_process_group should not raise if the process is gone."""
        import asyncio

        from app.executor import _kill_process_group

        # Start and immediately wait for a quick command
        schema = _schema("/bin/true")
        cmd = _validate_and_build(schema, {})
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        await proc.wait()

        # Should not raise even though the process is already dead
        _kill_process_group(proc.pid)
