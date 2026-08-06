"""Tests for the :class:`MCPClient` in ``app/client.py``.

The client normally talks to a live HTTP server via ``httpx.Client``.
To avoid spinning up a real server we replace the client's underlying
``httpx.Client`` with Starlette's :class:`~starlette.testclient.TestClient`,
which is itself an ``httpx.Client`` subclass that routes requests directly
to the FastAPI ASGI app in-process via a synchronous transport.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.client import MCPClient, MCPError
from app.main import app

# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #

def _make_test_client() -> TestClient:
    """Build a Starlette ``TestClient`` bound to the real FastAPI app."""
    return TestClient(app, base_url="http://test")


@pytest.fixture
def mcp_client() -> MCPClient:
    """An :class:`MCPClient` wired to the FastAPI app via ``TestClient``."""
    client = MCPClient("http://test")
    client._client = _make_test_client()
    yield client
    client.close()


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #

class TestHealth:
    def test_returns_ok(self, mcp_client: MCPClient) -> None:
        result = mcp_client.health()
        assert result == {"status": "ok"}


# --------------------------------------------------------------------------- #
# list_commands
# --------------------------------------------------------------------------- #

class TestListCommands:
    def test_returns_list(self, mcp_client: MCPClient) -> None:
        cmds = mcp_client.list_commands()
        assert isinstance(cmds, list)

    def test_at_least_three_commands(self, mcp_client: MCPClient) -> None:
        cmds = mcp_client.list_commands()
        assert len(cmds) >= 3

    def test_includes_log_and_discord(self, mcp_client: MCPClient) -> None:
        names = {c["name"] for c in mcp_client.list_commands()}
        assert "log" in names
        assert "discord" in names


# --------------------------------------------------------------------------- #
# get_command
# --------------------------------------------------------------------------- #

class TestGetCommand:
    def test_get_log(self, mcp_client: MCPClient) -> None:
        cmd = mcp_client.get_command("log")
        assert cmd["name"] == "log"

    def test_get_nonexistent_raises(self, mcp_client: MCPClient) -> None:
        with pytest.raises(MCPError):
            mcp_client.get_command("nonexistent")

    def test_get_nonexistent_is_404(self, mcp_client: MCPClient) -> None:
        with pytest.raises(MCPError) as exc_info:
            mcp_client.get_command("nonexistent")
        assert "404" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# execute
# --------------------------------------------------------------------------- #

class TestExecute:
    def test_log_world(self, mcp_client: MCPClient) -> None:
        result = mcp_client.execute("log", message="World")
        assert result["success"] is True
        assert "World" in result["stdout"]
        assert result["exit_code"] == 0

    def test_log_error_level(self, mcp_client: MCPClient) -> None:
        result = mcp_client.execute("log", message="oops", **{"--level": "error"})
        assert result["success"] is True
        assert "ERROR" in result["stdout"]

    def test_execute_nonexistent_raises(self, mcp_client: MCPClient) -> None:
        with pytest.raises(MCPError):
            mcp_client.execute("nonexistent")

    def test_execute_nonexistent_is_404(self, mcp_client: MCPClient) -> None:
        with pytest.raises(MCPError) as exc_info:
            mcp_client.execute("nonexistent")
        assert "404" in str(exc_info.value)

    def test_log_missing_message_raises(self, mcp_client: MCPClient) -> None:
        with pytest.raises(MCPError):
            mcp_client.execute("log")

    def test_log_missing_message_is_400(self, mcp_client: MCPClient) -> None:
        with pytest.raises(MCPError) as exc_info:
            mcp_client.execute("log")
        assert "400" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# tool
# --------------------------------------------------------------------------- #

class TestTool:
    def test_returns_callable(self, mcp_client: MCPClient) -> None:
        log = mcp_client.tool("log")
        assert callable(log)

    def test_call_works(self, mcp_client: MCPClient) -> None:
        log = mcp_client.tool("log")
        result = log(message="Test")
        assert result["success"] is True
        assert "Test" in result["stdout"]

    def test_name_attribute(self, mcp_client: MCPClient) -> None:
        log = mcp_client.tool("log")
        assert log.__name__ == "log"

    def test_qualname_attribute(self, mcp_client: MCPClient) -> None:
        log = mcp_client.tool("log")
        assert log.__qualname__ == "MCPClient.tool<log>"


# --------------------------------------------------------------------------- #
# context manager
# --------------------------------------------------------------------------- #

class TestContextManager:
    def test_context_manager_works(self) -> None:
        with MCPClient("http://test") as mc:
            mc._client = _make_test_client()
            assert mc.health() == {"status": "ok"}

    def test_exit_closes_client(self) -> None:
        mc = MCPClient("http://test")
        mc._client = _make_test_client()
        with mc as ctx:
            assert ctx is mc
        # After __exit__ the underlying httpx client should be closed.
        assert mc._client.is_closed
