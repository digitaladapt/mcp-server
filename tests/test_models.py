"""Tests for the Pydantic models in ``app.models``.

Covers :class:`ArgSpec`, :class:`CommandSchema`, :class:`ExecuteResult`,
:class:`ValidationIssue`, and :class:`ValidationResult`.  Every test is
self-contained — no fixtures or external state required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    ArgSpec,
    CommandSchema,
    ExecuteResult,
    ValidationIssue,
    ValidationResult,
)

# --------------------------------------------------------------------------- #
# ArgSpec
# --------------------------------------------------------------------------- #

class TestArgSpec:
    """Unit tests for :class:`ArgSpec`."""

    # -- valid types -------------------------------------------------------- #
    @pytest.mark.parametrize("arg_type", ["string", "int", "float", "bool", "flag"])
    def test_valid_types(self, arg_type):
        arg = ArgSpec(name="x", type=arg_type)
        assert arg.type == arg_type

    # -- invalid type ------------------------------------------------------- #
    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ArgSpec(name="x", type="bogus")
        # The underlying ValueError message should mention the allowed set.
        assert "arg type must be one of" in str(exc_info.value)

    def test_invalid_type_empty_string(self):
        with pytest.raises(ValidationError):
            ArgSpec(name="x", type="")

    # -- has_default property ---------------------------------------------- #
    def test_has_default_false_when_unset(self):
        arg = ArgSpec(name="x", type="string")
        assert arg.has_default is False

    def test_has_default_true_when_explicit_null(self):
        # Explicitly setting default: null in YAML should be distinguishable
        # from not setting a default at all.
        arg = ArgSpec(name="x", type="string", default=None)
        assert arg.default is None
        assert arg.has_default is True

    def test_has_default_true_when_set(self):
        arg = ArgSpec(name="x", type="string", default="hello")
        assert arg.has_default is True

    def test_has_default_true_when_zero(self):
        # 0 is not None — has_default should be True.
        arg = ArgSpec(name="x", type="int", default=0)
        assert arg.has_default is True

    def test_has_default_true_when_empty_string(self):
        arg = ArgSpec(name="x", type="string", default="")
        assert arg.has_default is True

    def test_has_default_true_when_false(self):
        arg = ArgSpec(name="x", type="bool", default=False)
        assert arg.has_default is True

    # -- is_flag property --------------------------------------------------- #
    def test_is_flag_true(self):
        arg = ArgSpec(name="--verbose", type="flag")
        assert arg.is_flag is True

    def test_is_flag_false_for_string(self):
        arg = ArgSpec(name="--verbose", type="string")
        assert arg.is_flag is False

    def test_is_flag_false_for_int(self):
        arg = ArgSpec(name="count", type="int")
        assert arg.is_flag is False

    # -- is_positional property -------------------------------------------- #
    def test_is_positional_true_for_plain_name(self):
        assert ArgSpec(name="path", type="string").is_positional is True

    def test_is_positional_false_for_long_flag(self):
        assert ArgSpec(name="--verbose", type="flag").is_positional is False

    def test_is_positional_false_for_short_flag(self):
        assert ArgSpec(name="-l", type="flag").is_positional is False

    # -- field defaults ----------------------------------------------------- #
    def test_defaults(self):
        arg = ArgSpec(name="x", type="string")
        assert arg.required is False
        assert arg.choices is None
        assert not arg.has_default
        assert arg.help is None

    # -- full construction -------------------------------------------------- #
    def test_full_construction(self):
        arg = ArgSpec(
            name="--mode",
            type="string",
            required=True,
            choices=["fast", "slow"],
            default="fast",
            help="select mode",
        )
        assert arg.name == "--mode"
        assert arg.type == "string"
        assert arg.required is True
        assert arg.choices == ["fast", "slow"]
        assert arg.default == "fast"
        assert arg.help == "select mode"
        assert arg.is_flag is False
        assert arg.is_positional is False
        assert arg.has_default is True


# --------------------------------------------------------------------------- #
# CommandSchema
# --------------------------------------------------------------------------- #

class TestCommandSchema:
    """Unit tests for :class:`CommandSchema`."""

    def test_minimal_defaults_empty_args(self):
        schema = CommandSchema(
            name="hello",
            description="say hello",
            executable="/bin/echo",
        )
        assert schema.name == "hello"
        assert schema.description == "say hello"
        assert schema.executable == "/bin/echo"
        assert schema.args == []

    def test_with_args(self):
        args = [
            ArgSpec(name="name", type="string", required=True),
            ArgSpec(name="--verbose", type="flag"),
        ]
        schema = CommandSchema(
            name="hello",
            description="say hello",
            executable="/bin/echo",
            args=args,
        )
        assert schema.args == args
        assert len(schema.args) == 2
        assert schema.args[0].name == "name"
        assert schema.args[1].is_flag is True


# --------------------------------------------------------------------------- #
# ExecuteResult
# --------------------------------------------------------------------------- #

class TestExecuteResult:
    """Unit tests for :class:`ExecuteResult`."""

    def test_creation_all_fields(self):
        result = ExecuteResult(
            stdout="hello\n",
            stderr="",
            exit_code=0,
            success=True,
        )
        assert result.stdout == "hello\n"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.success is True

    def test_failure_result(self):
        result = ExecuteResult(
            stdout="",
            stderr="command not found\n",
            exit_code=127,
            success=False,
        )
        assert result.stdout == ""
        assert result.stderr == "command not found\n"
        assert result.exit_code == 127
        assert result.success is False


# --------------------------------------------------------------------------- #
# ValidationIssue
# --------------------------------------------------------------------------- #

class TestValidationIssue:
    """Unit tests for :class:`ValidationIssue`."""

    def test_required_fields(self):
        issue = ValidationIssue(file="cmd.yaml", status="ok")
        assert issue.file == "cmd.yaml"
        assert issue.status == "ok"
        assert issue.command is None
        assert issue.message is None

    def test_optional_fields_default_none(self):
        issue = ValidationIssue(file="cmd.yaml", status="warning")
        assert issue.command is None
        assert issue.message is None

    def test_full_construction(self):
        issue = ValidationIssue(
            file="bad.yaml",
            status="error",
            command="broken",
            message="missing executable",
        )
        assert issue.file == "bad.yaml"
        assert issue.status == "error"
        assert issue.command == "broken"
        assert issue.message == "missing executable"


# --------------------------------------------------------------------------- #
# ValidationResult
# --------------------------------------------------------------------------- #

class TestValidationResult:
    """Unit tests for :class:`ValidationResult`."""

    def test_creation_no_issues(self):
        result = ValidationResult(
            valid=True,
            total=3,
            errors=0,
            warnings=0,
        )
        assert result.valid is True
        assert result.total == 3
        assert result.errors == 0
        assert result.warnings == 0
        assert result.issues == []

    def test_defaults_empty_issues(self):
        result = ValidationResult(
            valid=True,
            total=0,
            errors=0,
            warnings=0,
        )
        assert result.issues == []

    def test_with_issues(self):
        issues = [
            ValidationIssue(file="a.yaml", status="error", message="boom"),
            ValidationIssue(file="b.yaml", status="warning", message="hmm"),
        ]
        result = ValidationResult(
            valid=False,
            total=2,
            errors=1,
            warnings=1,
            issues=issues,
        )
        assert result.valid is False
        assert result.total == 2
        assert result.errors == 1
        assert result.warnings == 1
        assert result.issues == issues
        assert len(result.issues) == 2
        assert result.issues[0].status == "error"
        assert result.issues[1].status == "warning"
