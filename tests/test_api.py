"""HTTP API tests for the MCP Server FastAPI endpoints.

Uses the ``app_client`` fixture from ``conftest.py`` which spins up a
``TestClient`` against the real app and real registry (commands: discord,
log, log_read).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    """Liveness probe."""

    def test_returns_ok(self, app_client):
        resp = app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /commands
# ---------------------------------------------------------------------------


class TestCommands:
    """Listing and inspecting registered commands."""

    def test_list_returns_200_with_at_least_three(self, app_client):
        resp = app_client.get("/commands")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 3

    def test_each_item_has_required_fields(self, app_client):
        data = app_client.get("/commands").json()
        for item in data:
            assert "name" in item
            assert "description" in item
            assert "executable" in item
            assert "args" in item

    def test_expected_command_names_present(self, app_client):
        names = {c["name"] for c in app_client.get("/commands").json()}
        assert "discord" in names
        assert "log" in names

    def test_get_valid_command(self, app_client):
        resp = app_client.get("/commands/log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "log"
        assert "description" in data
        assert "executable" in data
        assert isinstance(data["args"], list)

    def test_get_unknown_command_returns_404(self, app_client):
        resp = app_client.get("/commands/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Command not found"


# ---------------------------------------------------------------------------
# GET /validate
# ---------------------------------------------------------------------------


class TestValidate:
    """Registry validation endpoint."""

    def test_validate_returns_200_with_expected_fields(self, app_client):
        resp = app_client.get("/validate")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("valid", "total", "errors", "warnings", "issues"):
            assert field in data, f"missing field: {field}"

    def test_validate_registry_is_clean(self, app_client):
        data = app_client.get("/validate").json()
        assert data["valid"] is True

    def test_validate_total_at_least_three(self, app_client):
        data = app_client.get("/validate").json()
        assert data["total"] >= 3

    def test_validate_issues_structure(self, app_client):
        data = app_client.get("/validate").json()
        assert isinstance(data["issues"], list)
        for issue in data["issues"]:
            assert "file" in issue
            assert "status" in issue
            assert "command" in issue


# ---------------------------------------------------------------------------
# POST /execute
# ---------------------------------------------------------------------------


class TestExecute:
    """Command execution endpoint."""

    def test_execute_log_basic(self, app_client):
        resp = app_client.post(
            "/execute",
            json={"command": "log", "arguments": {"message": "World"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "World" in data["stdout"]

    def test_execute_log_error_level(self, app_client):
        resp = app_client.post(
            "/execute",
            json={
                "command": "log",
                "arguments": {"message": "something broke", "--level": "error"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "ERROR" in data["stdout"]

    def test_execute_log_missing_required_arg(self, app_client):
        resp = app_client.post(
            "/execute",
            json={"command": "log", "arguments": {}},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "missing required argument" in detail.lower()

    def test_execute_log_unknown_argument(self, app_client):
        resp = app_client.post(
            "/execute",
            json={
                "command": "log",
                "arguments": {"message": "World", "bogus": "value"},
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "unknown argument" in detail.lower()

    def test_execute_unknown_command_returns_404(self, app_client):
        resp = app_client.post(
            "/execute",
            json={"command": "nonexistent", "arguments": {}},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Command not found"

    def test_execute_log_read(self, app_client):
        resp = app_client.post(
            "/execute",
            json={"command": "log_read", "arguments": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert isinstance(data["stdout"], str)
