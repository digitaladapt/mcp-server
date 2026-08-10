"""HTTP API tests for the MCP Server FastAPI endpoints.

Uses the ``app_client`` fixture from ``conftest.py`` which spins up a
``TestClient`` against the real app and real registry (commands: discord,
log, log_read).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------


class TestHealth:
    """Liveness probe."""

    def test_returns_ok(self, app_client):
        resp = app_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


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
# Generic /execute endpoint (removed)
# ---------------------------------------------------------------------------


class TestExecuteRemoved:
    """The generic ``POST /execute`` endpoint was removed so registry
    commands are only available via their dedicated routes."""

    def test_execute_endpoint_gone(self, app_client):
        resp = app_client.post(
            "/execute",
            json={"command": "log", "arguments": {"message": "World"}},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dedicated command routes (POST /{command})
# ---------------------------------------------------------------------------


class TestDedicatedRoutes:
    """Dedicated execution routes generated from the registry."""

    def test_log_basic(self, app_client):
        resp = app_client.post("/log", json={"message": "World"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "World" in data["stdout"]

    def test_log_error_level_via_field_name(self, app_client):
        resp = app_client.post(
            "/log",
            json={"message": "something broke", "level": "error"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "ERROR" in data["stdout"]

    def test_log_missing_required_arg_returns_422(self, app_client):
        resp = app_client.post("/log", json={})
        assert resp.status_code == 422

    def test_log_unknown_argument_returns_422(self, app_client):
        resp = app_client.post("/log", json={"message": "World", "bogus": "value"})
        assert resp.status_code == 422

    def test_nonexistent_route_returns_404(self, app_client):
        resp = app_client.post("/nonexistent", json={})
        assert resp.status_code == 404

    def test_log_read_default(self, app_client):
        resp = app_client.post("/log_read", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert isinstance(data["stdout"], str)
