"""Tests for the vital-pulse and penny-track registry integrations.

Verifies:
- The registry commands only load when their required env vars are set.
- The scripts build correct URLs and handle missing configuration.
- The secret guard treats the new API keys as secrets.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"


# --------------------------------------------------------------------------- #
# Registry loading (conditional registration)
# --------------------------------------------------------------------------- #

class TestRegistryLoading:
    """New commands appear only when their env vars are set."""

    def test_commands_hidden_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import registry as reg

        monkeypatch.delenv("VITAL_PULSE_URL", raising=False)
        monkeypatch.delenv("VITAL_PULSE_API_KEY", raising=False)
        monkeypatch.delenv("PENNY_TRACK_URL", raising=False)
        monkeypatch.delenv("PENNY_TRACK_API_KEY", raising=False)
        reg.load_registry()
        assert "vital_readings" not in reg.COMMANDS
        assert "penny_transactions" not in reg.COMMANDS
        reg.load_registry()  # restore real registry

    def test_vital_readings_loads_when_configured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import registry as reg

        monkeypatch.setenv("VITAL_PULSE_URL", "http://vitals:8080")
        monkeypatch.setenv("VITAL_PULSE_API_KEY", "ro-key")
        monkeypatch.delenv("PENNY_TRACK_URL", raising=False)
        monkeypatch.delenv("PENNY_TRACK_API_KEY", raising=False)
        reg.load_registry()
        assert "vital_readings" in reg.COMMANDS
        assert "penny_transactions" not in reg.COMMANDS
        schema = reg.get_command_schema("vital_readings")
        assert schema is not None
        assert schema.requires == ["VITAL_PULSE_URL", "VITAL_PULSE_API_KEY"]
        arg_names = [a.name for a in schema.args]
        assert arg_names == ["from", "to"]
        assert all(a.required for a in schema.args)
        # Python-safe field names for the MCP/OpenAPI surface
        assert [a.field_name for a in schema.args] == ["from_date", "to_date"]
        reg.load_registry()  # restore

    def test_penny_transactions_loads_when_configured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import registry as reg

        monkeypatch.setenv("PENNY_TRACK_URL", "http://penny:80")
        monkeypatch.setenv("PENNY_TRACK_API_KEY", "ro-key")
        monkeypatch.delenv("VITAL_PULSE_URL", raising=False)
        monkeypatch.delenv("VITAL_PULSE_API_KEY", raising=False)
        reg.load_registry()
        assert "penny_transactions" in reg.COMMANDS
        assert "vital_readings" not in reg.COMMANDS
        schema = reg.get_command_schema("penny_transactions")
        assert schema is not None
        assert schema.requires == ["PENNY_TRACK_URL", "PENNY_TRACK_API_KEY"]
        arg_names = [a.name for a in schema.args]
        assert arg_names == ["from", "to"]
        assert all(a.required for a in schema.args)
        # Python-safe field names for the MCP/OpenAPI surface
        assert [a.field_name for a in schema.args] == ["from_date", "to_date"]
        reg.load_registry()  # restore

    def test_all_four_commands_load_when_configured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import registry as reg

        monkeypatch.setenv("VITAL_PULSE_URL", "http://vitals:8080")
        monkeypatch.setenv("VITAL_PULSE_API_KEY", "ro-key")
        monkeypatch.setenv("PENNY_TRACK_URL", "http://penny:80")
        monkeypatch.setenv("PENNY_TRACK_API_KEY", "ro-key")
        reg.load_registry()
        assert {"log", "log_read", "vital_readings", "penny_transactions"} <= set(
            reg.COMMANDS.keys()
        )
        reg.load_registry()  # restore


# --------------------------------------------------------------------------- #
# Script smoke tests (no network — only argument/env validation paths)
# --------------------------------------------------------------------------- #

class TestScriptValidation:
    """Scripts fail fast with clear messages on bad input."""

    def test_vital_missing_args(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "vital_readings.sh"), "2025-01-01"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 2
        assert "both 'from' and 'to'" in result.stderr

    def test_penny_missing_args(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "penny_transactions.sh"), "2025-01-01"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 2
        assert "both 'from' and 'to'" in result.stderr

    def test_vital_missing_env(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "vital_readings.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 2
        assert "VITAL_PULSE_URL is not set" in result.stderr

    def test_penny_missing_env(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "penny_transactions.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 2
        assert "PENNY_TRACK_URL is not set" in result.stderr

    def test_vital_missing_api_key(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "vital_readings.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin", "VITAL_PULSE_URL": "http://x"},
        )
        assert result.returncode == 2
        assert "VITAL_PULSE_API_KEY is not set" in result.stderr

    def test_penny_missing_api_key(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "penny_transactions.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin", "PENNY_TRACK_URL": "http://x"},
        )
        assert result.returncode == 2
        assert "PENNY_TRACK_API_KEY is not set" in result.stderr


# --------------------------------------------------------------------------- #
# Secret guard
# --------------------------------------------------------------------------- #

class TestSecretGuard:
    """The vitals/penny API keys count as secrets for the auth guard."""

    def test_vital_api_key_triggers_secret_guard(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.auth import detect_configured_secrets

        monkeypatch.setenv("VITAL_PULSE_API_KEY", "ro-key")
        found = detect_configured_secrets()
        assert "VITAL_PULSE_API_KEY" in found

    def test_penny_api_key_triggers_secret_guard(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.auth import detect_configured_secrets

        monkeypatch.setenv("PENNY_TRACK_API_KEY", "ro-key")
        found = detect_configured_secrets()
        assert "PENNY_TRACK_API_KEY" in found

    def test_guard_requires_mcp_api_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.auth import require_auth_if_secrets

        monkeypatch.setenv("VITAL_PULSE_API_KEY", "ro-key")
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MCP_API_KEY"):
            require_auth_if_secrets()

    def test_guard_passes_with_mcp_api_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.auth import require_auth_if_secrets

        monkeypatch.setenv("VITAL_PULSE_API_KEY", "ro-key")
        monkeypatch.setenv("PENNY_TRACK_API_KEY", "ro-key")
        monkeypatch.setenv("MCP_API_KEY", "mcp-secret")
        require_auth_if_secrets()  # should not raise
