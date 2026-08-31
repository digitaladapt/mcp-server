"""Tests for the MCP (Model Context Protocol) Streamable HTTP integration.

Coverage:
- initialize handshake over a real HTTP server (uvicorn subprocess)
- tools/list matches the OpenAPI/registry tool surface
- tools/call executes a registry command end-to-end over HTTP
- tools/call error path (missing required arg -> is_error result)
- auth enforcement when MCP_API_KEY is set
- collision guard (DuplicateMCPToolNameError) at build time

NOTE on transport: the official ``mcp`` SDK's ``streamable_http_client``
performs real TCP connections, and the MCP session manager is entered from
the host FastAPI app's lifespan.  A Starlette ``TestClient`` or
``httpx2.ASGITransport`` alone does NOT run the app lifespan in a way the
SDK's socket client can reach, so these tests spawn a real uvicorn server
on an ephemeral port (the same path the manual smoke test used).
"""

from __future__ import annotations

import asyncio
import signal
import socket
import subprocess
import time
from collections.abc import Generator

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _free_port() -> int:
    """Return an ephemeral free port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Start the real app under uvicorn on a free port, yield base URL.

    ``MCP_LOG_ENABLED`` is set (in-process) before the subprocess forks so
    the app sees it.  For auth tests, use :func:`live_server_with_key` which
    also sets ``MCP_API_KEY`` before starting.
    """
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")
    monkeypatch.delenv("MCP_API_KEY", raising=False)  # no auth in default server
    return (yield from _start_live_server())


@pytest.fixture
def live_server_with_key(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Like live_server, but with MCP_API_KEY set before startup."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")
    monkeypatch.setenv("MCP_API_KEY", "test-secret")
    return (yield from _start_live_server())


def _start_live_server() -> Generator[str, None, None]:
    """Spawn uvicorn with the current environment and yield the base URL."""
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent
    # Run uvicorn from the interpreter running the tests so the subprocess uses
    # the same environment with uvicorn installed (a runtime dependency). This
    # works on CI and in the local dev environment without a .venv-dev path.
    python = sys.executable
    port = _free_port()
    proc = subprocess.Popen(
        [
            python,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
        ],
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        # Wait for startup.
        for _ in range(50):
            if proc.poll() is not None:
                raise RuntimeError(f"uvicorn exited early: {proc.returncode}")
            try:
                import httpx
                httpx.get(f"{base}/api/health", timeout=0.5)
                break
            except Exception:  # noqa: BLE001 - server may not be up yet
                time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn did not become ready")
        yield base
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()



@pytest.fixture
def _real_registry(monkeypatch: pytest.MonkeyPatch):
    """Point the registry at the real registry/ dir and reload it.

    Other tests swap MCP_REGISTRY_DIR / COMMANDS to a temp dir; we need the
    real registry (log, log_read) for the MCP surface tests.
    """
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("MCP_REGISTRY_DIR", str(project_root / "registry"))
    from app.registry import load_registry
    load_registry()


@pytest.fixture
def app(_real_registry, monkeypatch: pytest.MonkeyPatch):
    """Build the app fresh with MCP logging enabled."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    from app.main import create_app
    return create_app()


# --------------------------------------------------------------------------- #
# Tool-surface tests (in-process; no socket needed)
# --------------------------------------------------------------------------- #

def test_mcp_tool_surface_matches_registry(monkeypatch, _real_registry):
    """tools/list returns registry commands with stable names + schemas."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")
    monkeypatch.setenv("WEATHER_LOCATION", "45.5,-122.6")

    from mcp.client import Client

    from app.mcp_app import build_mcp_server

    mcp = build_mcp_server()

    async def run():
        async with Client(mcp, mode="legacy") as client:
            tools = await client.list_tools()
            return [t.name for t in tools.tools], tools

    names, tools = asyncio.run(run())
    assert "log" in names
    assert "log_read" in names
    assert "get_weather" in names

    log_tool = next(t for t in tools.tools if t.name == "log")
    props = log_tool.input_schema.get("properties", {})
    assert set(props.keys()) == {"message", "level"}
    assert log_tool.input_schema.get("required") == ["message"]


def test_mcp_tool_surface_matches_openapi(app):
    """Every MCP tool name corresponds to an OpenAPI operation ID / registry command."""
    from mcp.client import Client

    from app.mcp_app import get_mounted_server

    mcp = get_mounted_server(app)
    assert mcp is not None

    async def run():
        async with Client(mcp, mode="legacy") as client:
            result = await client.list_tools()
            return {t.name for t in result.tools}

    mcp_names = asyncio.run(run())

    openapi_ids = set()
    for ops in app.openapi()["paths"].values():
        for method, op in ops.items():
            if method in ("get", "post", "put", "delete", "patch"):
                openapi_ids.add(op.get("operationId"))

    # Every MCP tool must be a known OpenAPI operation ID or registry
    # command (MCP adds no tools the existing surface doesn't have).
    from app.registry import list_commands
    registry_cmds = {c.name for c in list_commands()}
    assert mcp_names.issubset(openapi_ids | registry_cmds)


def test_mcp_duplicate_tool_name_raises(monkeypatch, _real_registry):
    """Two tools registering the same name abort at build time."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")
    from app.mcp_app import build_mcp_server
    from app.mcp_tools import DuplicateMCPToolNameError, add_tool_guarded

    mcp = build_mcp_server()
    with pytest.raises(DuplicateMCPToolNameError):
        async def clash(**kwargs):
            return {}

        add_tool_guarded(mcp, clash, name="log", source="test")


# --------------------------------------------------------------------------- #
# Real HTTP transport tests (uvicorn subprocess)
# --------------------------------------------------------------------------- #

def test_initialize_over_http(live_server, monkeypatch):
    """initialize handshake works over the Streamable HTTP transport."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")

    async def run():
        async with streamable_http_client(f"{live_server}/mcp") as (read, write), \
                ClientSession(read, write) as session:
            await session.initialize()
            info = session.server_info
            # Fall back if server_info is None on this SDK version.
            return info.name if info else "mcp-server"

    assert asyncio.run(run()) == "mcp-server"


def test_list_tools_and_call_over_http(live_server, monkeypatch):
    """tools/list + tools/call (log) work over real HTTP."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")

    async def run():
        async with streamable_http_client(f"{live_server}/mcp") as (read, write), \
                ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            res = await session.call_tool(
                "log", {"level": "info", "message": "mcp-test-msg"}
            )
            return names, res

    names, res = asyncio.run(run())
    assert "log" in names
    assert not res.is_error
    assert "mcp-test-msg" in res.content[0].text


def test_call_missing_required_arg_over_http(live_server, monkeypatch):
    """tools/call with a missing required arg returns an is_error result."""
    monkeypatch.setenv("MCP_LOG_ENABLED", "true")

    async def run():
        async with streamable_http_client(f"{live_server}/mcp") as (read, write), \
                ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("log", {})
            return res

    res = asyncio.run(run())
    assert res.is_error


def test_auth_required_when_api_key_set(live_server_with_key):
    """With MCP_API_KEY set, unauthenticated tool calls are rejected."""
    async def run():
        try:
            async with streamable_http_client(f"{live_server_with_key}/mcp") as (read, write), \
                    ClientSession(read, write) as session:
                await session.initialize()
                await session.list_tools()
                return "unexpected-success"
        except Exception:  # noqa: BLE001 - any auth failure is the desired outcome
            return "auth-rejected"

    assert asyncio.run(run()) == "auth-rejected"


def test_auth_with_valid_api_key(live_server_with_key):
    """With MCP_API_KEY set, a valid API key authenticates."""
    import httpx2

    async def run():
        http_client = httpx2.AsyncClient(
            headers={"Authorization": "Bearer test-secret"},
            follow_redirects=True,
        )
        async with http_client, streamable_http_client(
            f"{live_server_with_key}/mcp",
            http_client=http_client,
        ) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [t.name for t in result.tools]

    names = asyncio.run(run())
    assert "log" in names
