"""Tests for the command registry loader (``app/registry.py``)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from pydantic import ValidationError

from app import registry as reg
from app.models import CommandSchema, ValidationResult


def write_registry_file(path: Path, name: str, **fields: object) -> None:
    """Write a minimal valid YAML registry file to *path*.

    Mirrors the helper in ``conftest.py`` so this module is self-contained.
    """
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


# --------------------------------------------------------------------------- #
# _load_file
# --------------------------------------------------------------------------- #
class TestLoadFile:
    """Unit tests for the single-file parser ``_load_file``."""

    def test_valid_yaml_file(self, tmp_registry: Path) -> None:
        """A well-formed YAML file yields a CommandSchema with correct fields."""
        path = tmp_registry / "hello.yaml"
        write_registry_file(
            path, "hello",
            executable="/bin/echo",
            args=[{"name": "name", "type": "string", "required": True}],
        )
        schema = reg._load_file(path)
        assert isinstance(schema, CommandSchema)
        assert schema.name == "hello"
        assert schema.description.startswith("Test command")
        assert schema.executable == "/bin/echo"
        assert len(schema.args) == 1
        assert schema.args[0].name == "name"
        assert schema.args[0].required is True

    def test_valid_json_file(self, tmp_registry: Path) -> None:
        """A well-formed JSON file is parsed into a CommandSchema."""
        path = tmp_registry / "ping.json"
        data = {
            "name": "ping",
            "description": "A ping command.",
            "executable": "/bin/echo",
            "args": [
                {"name": "host", "type": "string", "required": True},
            ],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        schema = reg._load_file(path)
        assert schema.name == "ping"
        assert schema.description == "A ping command."
        assert schema.executable == "/bin/echo"
        assert schema.args[0].name == "host"

    def test_relative_executable_resolved_against_project_root(
        self, tmp_registry: Path,
    ) -> None:
        """Relative executable paths are made absolute via PROJECT_ROOT."""
        path = tmp_registry / "rel.yaml"
        write_registry_file(path, "relcmd", executable="bin/run.sh")
        schema = reg._load_file(path)
        assert os.path.isabs(schema.executable)
        # The resolved path must live under the project root.
        assert schema.executable == str(reg.PROJECT_ROOT / "bin/run.sh")

    def test_absolute_executable_unchanged(self, tmp_registry: Path) -> None:
        """Absolute executable paths are left untouched."""
        path = tmp_registry / "abs.yaml"
        write_registry_file(path, "abscmd", executable="/usr/bin/echo")
        schema = reg._load_file(path)
        assert schema.executable == "/usr/bin/echo"

    def test_unsupported_extension_raises(self, tmp_registry: Path) -> None:
        """Non-YAML/JSON files raise ValueError."""
        path = tmp_registry / "cmd.txt"
        path.write_text("name: x\ndescription: y\nexecutable: /bin/echo\n",
                        encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported registry file type"):
            reg._load_file(path)

    def test_invalid_yaml_raises(self, tmp_registry: Path) -> None:
        """Malformed YAML content raises during parsing."""
        path = tmp_registry / "broken.yaml"
        path.write_text("name: broken\n  bad: yaml: structure\n",
                        encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            reg._load_file(path)

    def test_missing_required_fields_raises(self, tmp_registry: Path) -> None:
        """A file missing required CommandSchema fields raises ValidationError."""
        path = tmp_registry / "incomplete.yaml"
        path.write_text("name: incomplete\n# no description or executable\n",
                        encoding="utf-8")
        with pytest.raises(ValidationError):
            reg._load_file(path)


# --------------------------------------------------------------------------- #
# load_registry
# --------------------------------------------------------------------------- #
class TestLoadRegistry:
    """Tests for the directory scanner ``load_registry``."""

    def test_good_registry_loads_two_commands(self, good_registry: Path) -> None:
        """The ``good_registry`` fixture produces exactly two commands."""
        reg.load_registry(good_registry)
        assert len(reg.COMMANDS) == 2
        assert "hello" in reg.COMMANDS
        assert "log_read" in reg.COMMANDS
        reg.load_registry()  # restore real registry

    def test_bad_files_are_skipped(self, registry_with_bad_files: Path) -> None:
        """Broken / incomplete / duplicate files are skipped, 'good' survives."""
        reg.load_registry(registry_with_bad_files)
        assert len(reg.COMMANDS) == 1
        assert "good" in reg.COMMANDS
        assert "broken" not in reg.COMMANDS
        assert "incomplete" not in reg.COMMANDS
        reg.load_registry()  # restore

    def test_empty_dir_leaves_commands_empty(self, tmp_registry: Path) -> None:
        """Loading from an empty directory clears COMMANDS."""
        reg.load_registry(tmp_registry)
        assert reg.COMMANDS == {}
        reg.load_registry()  # restore

    def test_nonexistent_dir_no_crash(self, tmp_path: Path) -> None:
        """Pointing at a missing path should not raise."""
        missing = tmp_path / "does_not_exist"
        assert not missing.exists()
        reg.load_registry(missing)
        assert reg.COMMANDS == {}
        reg.load_registry()  # restore

    def test_non_yaml_json_files_skipped(self, tmp_registry: Path) -> None:
        """A ``.txt`` file in the registry dir is ignored."""
        # One valid command plus an unrelated text file.
        write_registry_file(
            tmp_registry / "hello.yaml", "hello", executable="/bin/echo",
        )
        (tmp_registry / "notes.txt").write_text("ignore me", encoding="utf-8")
        reg.load_registry(tmp_registry)
        assert len(reg.COMMANDS) == 1
        assert "hello" in reg.COMMANDS
        reg.load_registry()  # restore

    def test_get_command_schema_after_load(self, good_registry: Path) -> None:
        """After loading, ``get_command_schema`` returns the right schema."""
        reg.load_registry(good_registry)
        schema = reg.get_command_schema("hello")
        assert schema is not None
        assert schema.name == "hello"
        assert schema.executable == "/bin/echo"
        # Unknown command returns None.
        assert reg.get_command_schema("nope") is None
        reg.load_registry()  # restore


# --------------------------------------------------------------------------- #
# validate_registry
# --------------------------------------------------------------------------- #
class TestValidateRegistry:
    """Tests for the non-mutating ``validate_registry`` report."""

    def test_good_registry_valid(self, good_registry: Path) -> None:
        """A clean registry is fully valid with no errors or warnings."""
        result = reg.validate_registry(good_registry)
        assert isinstance(result, ValidationResult)
        assert result.valid is True
        assert result.total == 2
        assert result.errors == 0
        assert result.warnings == 0
        statuses = [i.status for i in result.issues]
        assert statuses == ["ok", "ok"]

    def test_bad_registry_invalid(self, registry_with_bad_files: Path) -> None:
        """Broken and incomplete files surface as errors."""
        result = reg.validate_registry(registry_with_bad_files)
        assert result.valid is False
        assert result.errors >= 2  # broken.yaml + incomplete.yaml + dup
        files_with_errors = {i.file for i in result.issues if i.status == "error"}
        assert "broken.yaml" in files_with_errors
        assert "incomplete.yaml" in files_with_errors

    def test_duplicate_detection_mentions_both_files(
        self, registry_with_bad_files: Path,
    ) -> None:
        """A duplicate command name produces an error referencing both files.

        Files are processed in sorted order, so ``dup.yaml`` (name "good")
        is registered first; ``good.yaml`` is then flagged as the duplicate.
        The resulting issue's ``file`` is the duplicate (good.yaml) and its
        message references the original (dup.yaml).
        """
        result = reg.validate_registry(registry_with_bad_files)
        dup_issues = [
            i for i in result.issues
            if i.status == "error" and i.command == "good"
        ]
        assert len(dup_issues) == 1
        issue = dup_issues[0]
        msg = issue.message or ""
        # The command name appears in the message.
        assert "good" in msg
        # Both file names are represented across the issue and its message.
        assert issue.file == "good.yaml"          # the duplicate file
        assert "dup.yaml" in msg                    # the original file

    def test_executable_not_found_warning(self, tmp_registry: Path) -> None:
        """A missing executable is reported as a warning (not an error)."""
        write_registry_file(
            tmp_registry / "ghost.yaml", "ghost",
            executable="/usr/bin/nonexistent-xyz",
        )
        result = reg.validate_registry(tmp_registry)
        assert result.valid is True  # warnings don't invalidate
        assert result.errors == 0
        assert result.warnings == 1
        warn = result.issues[0]
        assert warn.status == "warning"
        assert warn.command == "ghost"
        assert "not found" in (warn.message or "").lower()

    def test_nonexistent_registry_dir(self, tmp_path: Path) -> None:
        """Validating a missing directory returns a single error."""
        missing = tmp_path / "nope"
        result = reg.validate_registry(missing)
        assert result.valid is False
        assert result.errors == 1
        assert len(result.issues) == 1
        assert "does not exist" in (result.issues[0].message or "").lower()

    def test_does_not_mutate_commands(self, good_registry: Path) -> None:
        """``validate_registry`` must not change the global COMMANDS state."""
        reg.load_registry(good_registry)
        before = dict(reg.COMMANDS)
        _ = reg.validate_registry(good_registry)
        after = reg.COMMANDS
        assert dict(after) == before
        reg.load_registry()  # restore


# --------------------------------------------------------------------------- #
# Real registry
# --------------------------------------------------------------------------- #
class TestRealRegistry:
    """Smoke tests against the project's actual ``registry/`` directory."""

    EXPECTED_COMMANDS: ClassVar[set[str]] = {
        "discord", "log", "log_read",
    }

    def test_real_commands_load(self) -> None:
        """The five real commands are present after import-time loading."""
        reg.load_registry()  # uses the real REGISTRY_DIR
        assert set(reg.COMMANDS.keys()) == self.EXPECTED_COMMANDS

    def test_log_schema_executable(self) -> None:
        """The ``log`` command points at a script named ``log.sh``."""
        reg.load_registry()
        schema = reg.get_command_schema("log")
        assert schema is not None
        assert "log.sh" in schema.executable

