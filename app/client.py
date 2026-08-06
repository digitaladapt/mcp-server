"""MCP Server client library.

A thin :mod:`httpx`-based client that mirrors the server's OpenAPI surface.
Designed so that a language model (or a human) can import it and treat each
registered command as a native Python callable.

Usage
-----
::

    from app.client import MCPClient

    mc = MCPClient("http://127.0.0.1:8000", api_key="your-secret-key")

    # Discover available commands
    for cmd in mc.list_commands():
        print(cmd["name"], "-", cmd["description"])

    # Execute one
    result = mc.execute("log", message="World")
    print(result["stdout"])

The :meth:`MCPClient.tool` helper returns a closure bound to a specific
command, so you can do::

    discord = mc.tool("discord")
    discord(message="Deploy complete", color="green", title="CI")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Self

import httpx


class MCPError(Exception):
    """Raised when the server returns a non-2xx response."""


class MCPClient:
    """Synchronous client for the MCP Server.

    Parameters
    ----------
    base_url:
        Root URL of the running MCP server, e.g. ``http://127.0.0.1:8000``.
    api_key:
        Optional API key.  If provided, sent as ``X-API-Key`` header on
        every request.  Must match the server's ``MCP_API_KEY`` env var.
    timeout:
        Per-request timeout in seconds (should be >= the server's 30 s
        command timeout if you expect long-running commands).
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers,
        )

    # -- low-level ------------------------------------------------------

    def _get(self, path: str) -> Any:
        resp = self._client.get(path)
        if resp.status_code >= 400:
            raise MCPError(
                f"GET {path} -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def _post(self, path: str, json: dict[str, Any]) -> Any:
        resp = self._client.post(path, json=json)
        if resp.status_code >= 400:
            raise MCPError(
                f"POST {path} -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    # -- high-level API -------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Hit ``GET /health``."""
        return self._get("/health")

    def list_commands(self) -> list[dict[str, Any]]:
        """Return every registered command schema."""
        return self._get("/commands")

    def get_command(self, name: str) -> dict[str, Any]:
        """Return the schema for a single command."""
        return self._get(f"/commands/{name}")

    def execute(self, command: str, **arguments: Any) -> dict[str, Any]:
        """Execute *command* with keyword arguments.

        Each keyword maps to an argument name from the command's schema
        (positional names or ``--flag`` names).  Returns the
        ``ExecuteResult`` dict: ``{stdout, stderr, exit_code, success}``.
        """
        return self._post(
            "/execute",
            {"command": command, "arguments": arguments},
        )

    def tool(self, command: str) -> Callable[..., dict[str, Any]]:
        """Return a callable bound to *command*.

        Equivalent to ``lambda **kw: self.execute(command, **kw)`` but
        with a clearer repr and bound name.
        """

        def _call(**kwargs: Any) -> dict[str, Any]:
            return self.execute(command, **kwargs)

        _call.__name__ = command
        _call.__qualname__ = f"MCPClient.tool<{command}>"
        return _call

    # -- context-manager sugar -----------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
