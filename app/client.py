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

    # Execute one via its dedicated route
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

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = self._client.post(path, json=json or {})
        if resp.status_code >= 400:
            raise MCPError(
                f"POST {path} -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def _put(self, path: str, json: dict[str, Any]) -> Any:
        resp = self._client.put(path, json=json)
        if resp.status_code >= 400:
            raise MCPError(
                f"PUT {path} -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def _delete(self, path: str) -> Any:
        resp = self._client.delete(path)
        if resp.status_code >= 400:
            raise MCPError(
                f"DELETE {path} -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    # -- high-level API -------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Hit ``GET /api/health``."""
        return self._get("/api/health")

    def list_commands(self) -> list[dict[str, Any]]:
        """Return every registered command schema."""
        return self._get("/commands")

    def get_command(self, name: str) -> dict[str, Any]:
        """Return the schema for a single command."""
        return self._get(f"/commands/{name}")

    def execute(self, command: str, **arguments: Any) -> dict[str, Any]:
        """Execute *command* with keyword arguments.

        Sends a POST to the command's dedicated ``/{command}`` route.
        Keyword arguments map to the tool field names from the command's
        schema (e.g. ``message=``, ``level=``, ``color=``); CLI-style
        names (e.g. ``--level``, ``-c``) are translated automatically.
        Returns the ``ExecuteResult`` dict:
        ``{stdout, stderr, exit_code, success}``.
        """
        # Translate CLI-style keys to the clean tool field names.
        clean_args: dict[str, Any] = {}
        for key, value in arguments.items():
            if key.startswith("-"):
                # "--level" -> "level", "-c" -> "c", "--log-file" -> "log_file"
                clean_args[key.lstrip("-").replace("-", "_")] = value
            else:
                clean_args[key] = value
        return self._post(f"/{command}", clean_args)

    # -- calendar / event / task API ----------------------------------

    def list_calendars(self) -> dict[str, Any]:
        """List all accessible CalDAV calendars."""
        return self._get("/calendars")

    def list_events(
        self,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """List calendar events, optionally filtered by date range.

        Parameters
        ----------
        start:
            ISO 8601 datetime/date string (e.g. ``"2026-01-15T00:00:00"``).
        end:
            ISO 8601 datetime/date string.
        """
        params: dict[str, str] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        resp = self._client.get("/events", params=params)
        if resp.status_code >= 400:
            raise MCPError(
                f"GET /events -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def get_event(self, uid: str) -> dict[str, Any]:
        """Get a single event by UID."""
        return self._get(f"/events/{uid}")

    def create_event(self, **fields: Any) -> dict[str, Any]:
        """Create a new event on the editable calendar.

        Keyword arguments map to :class:`~app.caldav_models.CreateEventRequest`
        fields: ``summary`` (required), ``start``, ``end``, ``description``,
        ``location``, ``all_day``.
        """
        return self._post("/events", fields)

    def update_event(self, uid: str, **fields: Any) -> dict[str, Any]:
        """Update an existing event on the editable calendar.

        Keyword arguments map to :class:`~app.caldav_models.UpdateEventRequest`
        fields: ``summary``, ``description``, ``start``, ``end``, ``location``,
        ``all_day``.
        """
        return self._put(f"/events/{uid}", fields)

    def delete_event(self, uid: str) -> dict[str, Any]:
        """Delete an event from the editable calendar."""
        return self._delete(f"/events/{uid}")

    def list_tasks(self) -> dict[str, Any]:
        """List calendar tasks across all accessible calendars."""
        return self._get("/tasks")

    def get_task(self, uid: str) -> dict[str, Any]:
        """Get a single task by UID."""
        return self._get(f"/tasks/{uid}")

    def create_task(self, **fields: Any) -> dict[str, Any]:
        """Create a new task on the editable calendar.

        Keyword arguments map to :class:`~app.caldav_models.CreateTaskRequest`
        fields: ``summary`` (required), ``description``, ``due``, ``priority``.
        """
        return self._post("/tasks", fields)

    def update_task(self, uid: str, **fields: Any) -> dict[str, Any]:
        """Update an existing task on the editable calendar.

        Keyword arguments map to :class:`~app.caldav_models.UpdateTaskRequest`
        fields: ``summary``, ``description``, ``due``, ``priority``, ``status``.
        """
        return self._put(f"/tasks/{uid}", fields)

    def delete_task(self, uid: str) -> dict[str, Any]:
        """Delete a task from the editable calendar."""
        return self._delete(f"/tasks/{uid}")

    def tool(self, command: str) -> Callable[..., dict[str, Any]]:
        """Return a callable bound to *command*.

        Calls the command's dedicated ``/{command}`` route; see
        :meth:`execute`.
        """

        def _call(**kwargs: Any) -> dict[str, Any]:
            return self.execute(command, **kwargs)

        _call.__name__ = command
        _call.__qualname__ = f"MCPClient.tool<{command}>"
        return _call

    # -- Gitea API ----------------------------------------------------

    def _patch(self, path: str, json: dict[str, Any]) -> Any:
        resp = self._client.patch(path, json=json)
        if resp.status_code >= 400:
            raise MCPError(
                f"PATCH {path} -> {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def list_issues(self, **params: Any) -> dict[str, Any]:
        """List issues in the (default) repository.

        Keyword arguments are passed as query params: ``state``, ``labels``,
        ``owner``, ``repo``, ``page``, ``limit``.
        """
        resp = self._client.get("/issues", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /issues -> {resp.status_code}: {resp.text}")
        return resp.json()

    def get_issue(self, index: int, **params: Any) -> dict[str, Any]:
        """Get a single issue by number."""
        resp = self._client.get(f"/issues/{index}", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /issues/{index} -> {resp.status_code}: {resp.text}")
        return resp.json()

    def create_issue(self, **fields: Any) -> dict[str, Any]:
        """Create a new issue. Fields: ``title`` (required), ``body``, ``labels``, ``assignees``, ``milestone``."""
        return self._post("/issues", fields)

    def close_issue(self, index: int, **params: Any) -> dict[str, Any]:
        """Close an issue by number."""
        return self._patch(f"/issues/{index}", {"state": "closed"})

    def list_branches(self, **params: Any) -> dict[str, Any]:
        """List branches in the (default) repository."""
        resp = self._client.get("/branches", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /branches -> {resp.status_code}: {resp.text}")
        return resp.json()

    def create_branch(self, name: str, from_ref: str = "", **params: Any) -> dict[str, Any]:
        """Create a new branch. ``from_ref`` defaults to the repo's default branch."""
        body: dict[str, Any] = {"name": name}
        if from_ref:
            body["from_ref"] = from_ref
        body.update(params)
        return self._post("/branches", body)

    def delete_branch(self, name: str, **params: Any) -> dict[str, Any]:
        """Delete a branch by name."""
        resp = self._client.delete(f"/branches/{name}", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"DELETE /branches/{name} -> {resp.status_code}: {resp.text}")
        return resp.json()

    def list_prs(self, **params: Any) -> dict[str, Any]:
        """List pull requests. Params: ``state``, ``owner``, ``repo``, ``page``, ``limit``."""
        resp = self._client.get("/prs", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /prs -> {resp.status_code}: {resp.text}")
        return resp.json()

    def get_pr(self, index: int, **params: Any) -> dict[str, Any]:
        """Get a single pull request by number."""
        resp = self._client.get(f"/prs/{index}", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /prs/{index} -> {resp.status_code}: {resp.text}")
        return resp.json()

    def create_pr(self, title: str, head: str, base: str, body: str = "", **params: Any) -> dict[str, Any]:
        """Create a pull request."""
        payload: dict[str, Any] = {"title": title, "head": head, "base": base}
        if body:
            payload["body"] = body
        payload.update(params)
        return self._post("/prs", payload)

    def merge_pr(self, index: int, method: str = "merge", message: str = "", **params: Any) -> dict[str, Any]:
        """Merge a pull request. ``method``: merge, squash, rebase, rebase-merge."""
        payload: dict[str, Any] = {"do": method}
        if message:
            payload["merge_commit_message"] = message
        payload.update(params)
        return self._post(f"/prs/{index}/merge", payload)

    def list_actions(self, **params: Any) -> dict[str, Any]:
        """List CI workflow runs."""
        resp = self._client.get("/actions", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /actions -> {resp.status_code}: {resp.text}")
        return resp.json()

    def get_commit_statuses(self, sha: str, **params: Any) -> dict[str, Any]:
        """Get CI status checks for a commit."""
        resp = self._client.get(f"/commits/{sha}/statuses", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /commits/{sha}/statuses -> {resp.status_code}: {resp.text}")
        return resp.json()

    def list_releases(self, **params: Any) -> dict[str, Any]:
        """List releases in the (default) repository."""
        resp = self._client.get("/releases", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"GET /releases -> {resp.status_code}: {resp.text}")
        return resp.json()

    def create_release(self, tag: str, **fields: Any) -> dict[str, Any]:
        """Create a release. Fields: ``tag`` (required), ``name``, ``body``, ``target``, ``draft``, ``prerelease``."""
        body: dict[str, Any] = {"tag_name": tag}
        body.update(fields)
        return self._post("/releases", body)

    def delete_release(self, release_id: int, **params: Any) -> dict[str, Any]:
        """Delete a release by ID."""
        resp = self._client.delete(f"/releases/{release_id}", params=params)
        if resp.status_code >= 400:
            raise MCPError(f"DELETE /releases/{release_id} -> {resp.status_code}: {resp.text}")
        return resp.json()

    def compare(self, base: str, head: str, owner: str | None = None, repo: str | None = None) -> dict[str, Any]:
        """Compare two refs in a repository."""
        path = f"/repos/{owner or ''}/{repo or ''}/compare?base={base}&head={head}"
        resp = self._client.get(path)
        if resp.status_code >= 400:
            raise MCPError(f"GET compare -> {resp.status_code}: {resp.text}")
        return resp.json()

    # -- context-manager sugar -----------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
