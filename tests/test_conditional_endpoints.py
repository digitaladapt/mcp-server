"""Tests for conditional endpoint registration.

Verifies the core modularity principle: commands (and their dedicated
HTTP routes) only appear when their ``requires`` conditions are met.

The primary use case is ``MCP_LOG_ENABLED=false`` hiding the ``log``
and ``log_read`` endpoints, but the tests also cover the general
``requires`` mechanism with arbitrary env vars.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.registry import load_registry

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Remove logging env vars so tests start from a clean slate."""
    monkeypatch.delenv("MCP_LOG_ENABLED", raising=False)
    monkeypatch.delenv("MCP_LOG_FILE", raising=False)
    monkeypatch.delenv("MCP_LOG_DIR", raising=False)
    # Also clean up any test env vars
    for key in list(monkeypatch._setenv.keys() if hasattr(monkeypatch, '_setenv') else []):
        if key.startswith("TEST_"):
            monkeypatch.delenv(key, raising=False)


def _make_app_with_registry(
    monkeypatch: pytest.MonkeyPatch,
    registry_dir: Path,
) -> TestClient:
    """Create a TestClient with the given registry dir loaded fresh."""
    monkeypatch.setenv("MCP_REGISTRY_DIR", str(registry_dir))
    load_registry(registry_dir)
    from app.main import create_app
    app = create_app()
    return TestClient(app)


def _write_command(
    reg_dir: Path,
    name: str,
    *,
    requires: list[str] | None = None,
    executable: str = "/bin/echo",
) -> None:
    """Write a minimal registry YAML file."""
    lines = [
        f"name: {name}",
        f"description: Test command {name}.",
        f"executable: {executable}",
    ]
    if requires:
        lines.append("requires:")
        for r in requires:
            lines.append(f'  - "{r}"')
    (reg_dir / f"{name}.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# _eval_condition unit tests
# --------------------------------------------------------------------------- #

class TestEvalCondition:
    """Unit tests for the _eval_condition function."""

    def test_shorthand_var_set_truthy(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "true")
        assert _eval_condition("MY_VAR") is True

    def test_shorthand_var_unset(self):
        from app.registry import _eval_condition
        assert _eval_condition("NONEXISTENT_VAR_XYZ") is False

    def test_shorthand_var_false(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "false")
        assert _eval_condition("MY_VAR") is False

    def test_shorthand_var_zero(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "0")
        assert _eval_condition("MY_VAR") is False

    def test_shorthand_var_empty(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "")
        assert _eval_condition("MY_VAR") is False

    def test_not_equal_unset_passes(self):
        from app.registry import _eval_condition
        # MCP_LOG_ENABLED != false → unset ("") != "false" → True
        assert _eval_condition("MCP_LOG_ENABLED != false") is True

    def test_not_equal_false_fails(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MCP_LOG_ENABLED", "false")
        assert _eval_condition("MCP_LOG_ENABLED != false") is False

    def test_not_equal_true_passes(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MCP_LOG_ENABLED", "true")
        assert _eval_condition("MCP_LOG_ENABLED != false") is True

    def test_equal_true_passes(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "true")
        assert _eval_condition("MY_VAR == true") is True

    def test_equal_wrong_value_fails(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "false")
        assert _eval_condition("MY_VAR == true") is False

    def test_equal_unset_fails(self):
        from app.registry import _eval_condition
        assert _eval_condition("NONEXISTENT == true") is False

    def test_case_insensitive_not_equal(self, monkeypatch):
        from app.registry import _eval_condition
        monkeypatch.setenv("MY_VAR", "FALSE")
        assert _eval_condition("MY_VAR != false") is False

    def test_empty_condition_passes(self):
        from app.registry import _eval_condition
        assert _eval_condition("") is True


# --------------------------------------------------------------------------- #
# Registry filtering: MCP_LOG_ENABLED
# --------------------------------------------------------------------------- #

class TestLogEndpointsConditional:
    """log and log_read endpoints should only exist when logging is enabled."""

    def test_log_endpoints_present_when_enabled(self, tmp_path, monkeypatch):
        """When MCP_LOG_ENABLED is unset or true, log/log_read are available."""
        monkeypatch.delenv("MCP_LOG_ENABLED", raising=False)
        # Use the real registry dir (has log.yaml and log_read.yaml)
        real_registry = Path(__file__).resolve().parent.parent / "registry"
        client = _make_app_with_registry(monkeypatch, real_registry)

        # Commands listed
        names = {c["name"] for c in client.get("/commands").json()}
        assert "log" in names
        assert "log_read" in names

        # Routes exist
        assert client.post("/log", json={"message": "hi"}).status_code == 200
        assert client.post("/log_read", json={}).status_code == 200

    def test_log_endpoints_absent_when_disabled(self, tmp_path, monkeypatch):
        """When MCP_LOG_ENABLED=false, log/log_read endpoints disappear."""
        monkeypatch.setenv("MCP_LOG_ENABLED", "false")
        # Copy the real registry to a temp dir and add a fallback command
        # so the app has at least one endpoint to start.
        real_registry = Path(__file__).resolve().parent.parent / "registry"
        reg = tmp_path / "registry"
        reg.mkdir()
        for f in real_registry.iterdir():
            if f.suffix in (".yaml", ".yml", ".json"):
                (reg / f.name).write_text(f.read_text(), encoding="utf-8")
        _write_command(reg, "fallback")

        client = _make_app_with_registry(monkeypatch, reg)

        # Commands NOT listed
        names = {c["name"] for c in client.get("/commands").json()}
        assert "log" not in names
        assert "log_read" not in names
        assert "fallback" in names

        # Routes don't exist → 404
        assert client.post("/log", json={"message": "hi"}).status_code == 404
        assert client.post("/log_read", json={}).status_code == 404

    def test_log_endpoints_present_when_explicitly_true(self, tmp_path, monkeypatch):
        """When MCP_LOG_ENABLED=true, log/log_read are available."""
        monkeypatch.setenv("MCP_LOG_ENABLED", "true")
        real_registry = Path(__file__).resolve().parent.parent / "registry"
        client = _make_app_with_registry(monkeypatch, real_registry)

        names = {c["name"] for c in client.get("/commands").json()}
        assert "log" in names
        assert "log_read" in names


# --------------------------------------------------------------------------- #
# General requires mechanism
# --------------------------------------------------------------------------- #

class TestRequiresMechanism:
    """The requires field works for arbitrary env vars, not just logging."""

    def test_command_with_met_require_loads(self, tmp_path, monkeypatch):
        """Command loads when its required env var is set and truthy."""
        monkeypatch.setenv("MY_API_KEY", "abc123")
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "greet", requires=["MY_API_KEY"])

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "greet" in names

    def test_command_with_unmet_require_skipped(self, tmp_path, monkeypatch):
        """Command is skipped when its required env var is unset."""
        monkeypatch.delenv("MY_API_KEY", raising=False)
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "greet", requires=["MY_API_KEY"])
        # Always-on command so the app has at least one endpoint.
        _write_command(reg, "fallback")

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "greet" not in names
        assert "fallback" in names

        # Route should 404
        assert client.post("/greet", json={}).status_code == 404

    def test_command_with_not_equal_require(self, tmp_path, monkeypatch):
        """Command with 'VAR != false' loads when var is unset or true."""
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "feature", requires=["FEATURE_ENABLED != false"])

        # Unset → passes
        monkeypatch.delenv("FEATURE_ENABLED", raising=False)
        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "feature" in names

    def test_command_with_not_equal_require_blocked(self, tmp_path, monkeypatch):
        """Command with 'VAR != false' is skipped when var is 'false'."""
        monkeypatch.setenv("FEATURE_ENABLED", "false")
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "feature", requires=["FEATURE_ENABLED != false"])
        _write_command(reg, "fallback")

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "feature" not in names
        assert "fallback" in names

    def test_command_with_multiple_requires_all_met(self, tmp_path, monkeypatch):
        """Command loads when ALL requirements are met."""
        monkeypatch.setenv("VAR_A", "yes")
        monkeypatch.setenv("VAR_B", "1")
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "multi", requires=["VAR_A", "VAR_B"])

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "multi" in names

    def test_command_with_multiple_requires_one_missing(self, tmp_path, monkeypatch):
        """Command is skipped when ANY requirement is unmet."""
        monkeypatch.setenv("VAR_A", "yes")
        monkeypatch.delenv("VAR_B", raising=False)
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "multi", requires=["VAR_A", "VAR_B"])
        _write_command(reg, "fallback")

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "multi" not in names
        assert "fallback" in names

    def test_command_with_no_requires_always_loads(self, tmp_path, monkeypatch):
        """Command with no requires field always loads."""
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "simple")

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "simple" in names

    def test_mixed_commands_some_filtered(self, tmp_path, monkeypatch):
        """Multiple commands: some load, some filtered."""
        monkeypatch.setenv("HAS_TOKEN", "secret")
        monkeypatch.delenv("NO_TOKEN", raising=False)

        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "with_token", requires=["HAS_TOKEN"])
        _write_command(reg, "without_token", requires=["NO_TOKEN"])
        _write_command(reg, "no_reqs")

        client = _make_app_with_registry(monkeypatch, reg)
        names = {c["name"] for c in client.get("/commands").json()}
        assert "with_token" in names
        assert "without_token" not in names
        assert "no_reqs" in names

    def test_get_command_404_when_filtered(self, tmp_path, monkeypatch):
        """GET /commands/{name} returns 404 for filtered commands."""
        monkeypatch.delenv("MY_API_KEY", raising=False)
        reg = tmp_path / "registry"
        reg.mkdir()
        _write_command(reg, "secret_cmd", requires=["MY_API_KEY"])
        _write_command(reg, "fallback")

        client = _make_app_with_registry(monkeypatch, reg)
        assert client.get("/commands/secret_cmd").status_code == 404
