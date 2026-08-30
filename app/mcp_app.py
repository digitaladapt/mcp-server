"""MCP (Model Context Protocol) Streamable HTTP integration via the SDK.

Adapts **our project** to the official ``mcp`` Python SDK (v2).  We build
an ``MCPServer``, register our existing tool surface as first-class SDK
tools, mount the SDK's ``streamable_http_app()`` onto our FastAPI app at
``/mcp``, and run the SDK's session manager from our app's lifespan.

SDK integration notes (verified against mcp 2.1.1):
- ``MCPServer.streamable_http_app()`` returns a Starlette app with a
  default route at ``/mcp``.  Set ``streamable_http_path=\"/\"`` so the
  mount prefix is the whole path (we mount at ``/mcp``).
- Mounting a sub-application disables its built-in lifespan, so the host
  app must enter ``mcp.session_manager.run()`` in its own lifespan — the
  first request would otherwise fail with
  ``RuntimeError: Task group is not initialized``.
- ``mcp.session_manager`` only exists after ``streamable_http_app()`` has
  been called, so we build the mount at module/creation time and only
  touch the manager inside the lifespan.
- DNS rebinding protection is armed by default for ``127.0.0.1``; serving
  behind a real hostname needs ``transport_security=`` (an allowlist),
  otherwise every request is rejected with ``421``.
- Bearer auth: MCP clients send ``Authorization: Bearer <api-key>``.  The
  SDK natively verifies bearer tokens via ``token_verifier=``; we supply
  a thin adapter over our existing key check so auth behavior matches the
  rest of the server exactly.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .auth import is_auth_enabled

logger = logging.getLogger(__name__)

#: Path the MCP endpoint is served on (mounted inside the FastAPI app).
MCP_MOUNT_PATH = "/mcp"

#: Sentinel set on the app state once the MCP server is wired up so the
#: lifespan knows which session manager to run.
_MCP_STATE_KEY = "mcp_server"


# --------------------------------------------------------------------------- #
# Auth adapter — reuse our API key verification as the SDK token verifier
# --------------------------------------------------------------------------- #

class _ApiKeyVerifier(TokenVerifier):
    """Verify MCP bearer tokens against the same API key as the REST API.

    The SDK calls ``verify_token(token)`` for every protected request.  We
    compare the token to ``MCP_API_KEY`` using the same constant-time
    comparison as :func:`app.auth.verify_api_key` and return an
    ``AccessToken`` on a match.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        import secrets

        from .auth import get_api_key

        key = get_api_key()
        if not key:
            # Auth disabled — any / absence of token is accepted (open mode).
            return AccessToken(token=token, client_id="anonymous", scopes=[], subject="anonymous")
        if secrets.compare_digest(token, key):
            return AccessToken(
                token=token,
                client_id="mcp-api-key",
                scopes=[],
                subject="mcp-api-key",
            )
        return None


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #

def _maybe(fn, *args, **kwargs):
    """Call *fn* if it's not None (used for conditional registration)."""
    if fn is not None:
        fn(*args, **kwargs)


def _register_conditional_tools(mcp: MCPServer) -> None:
    """Register tools conditionally, mirroring the FastAPI create_app() logic.

    Only tools whose backing feature is configured get registered, so the
    MCP surface matches the OpenAPI surface exactly (no 503-when-called
    tools).  Registry commands are always present when registry files
    exist.
    """
    import os

    from . import mcp_tools
    from .mcp_tools import _set_tool_registry, _ToolRegistry
    from .providers import provider_registry

    # Scoped per server build (like FastAPI's generate_unique_id), so
    # repeated create_app() calls in tests don't trip the guard.
    registry = _ToolRegistry()
    _set_tool_registry(registry)

    has_calendar = provider_registry.has_providers
    has_editable = provider_registry.has_editable
    has_caldav = any(p.name.lower() == "caldav" for p in provider_registry.providers)
    has_gitea = bool(os.environ.get("GITEA_URL", "").strip())
    has_weather = os.environ.get("WEATHER_LOCATION", "").strip() != ""

    # Local helper wiring every registration through the per-build guard.
    def add(fn, name: str, source: str) -> None:
        mcp_tools.add_tool_guarded(mcp, fn, name=name, source=source)

    # Calendar / CalDAV
    if has_calendar:
        # Read tools
        add(mcp_tools.list_events, "list_events", "calendar")
        add(mcp_tools.get_event_by_uid, "get_event_by_uid", "calendar")
        add(mcp_tools.list_calendars, "list_calendars", "calendar")
        if has_editable:
            add(mcp_tools.create_event, "create_event", "calendar:write")
            add(mcp_tools.update_event, "update_event", "calendar:write")
            add(mcp_tools.delete_event, "delete_event", "calendar:write")

    if has_caldav:
        add(mcp_tools.list_tasks, "list_tasks", "caldav")
        add(mcp_tools.get_task_by_uid, "get_task_by_uid", "caldav")
        if has_editable:
            add(mcp_tools.create_task, "create_task", "caldav:write")
            add(mcp_tools.update_task, "update_task", "caldav:write")
            add(mcp_tools.delete_task, "delete_task", "caldav:write")

    # Gitea
    if has_gitea:
        add(mcp_tools.search_repos, "search_repos", "gitea")
        add(mcp_tools.get_repo, "get_repo", "gitea")
        add(mcp_tools.list_repos, "list_repos", "gitea")
        add(mcp_tools.list_issues, "list_issues", "gitea")
        add(mcp_tools.get_issue, "get_issue", "gitea")
        add(mcp_tools.create_issue, "create_issue", "gitea:write")
        add(mcp_tools.update_issue, "update_issue", "gitea:write")
        add(mcp_tools.create_issue_comment, "create_issue_comment", "gitea:write")
        add(mcp_tools.list_issue_comments, "list_issue_comments", "gitea")
        add(mcp_tools.list_branches, "list_branches", "gitea")
        add(mcp_tools.create_branch, "create_branch", "gitea:write")
        add(mcp_tools.delete_branch, "delete_branch", "gitea:write")
        add(mcp_tools.list_prs, "list_prs", "gitea")
        add(mcp_tools.get_pr, "get_pr", "gitea")
        add(mcp_tools.create_pr, "create_pr", "gitea:write")
        add(mcp_tools.update_pr, "update_pr", "gitea:write")
        add(mcp_tools.merge_pr, "merge_pr", "gitea:write")
        add(mcp_tools.list_pr_reviews, "list_pr_reviews", "gitea")
        add(mcp_tools.create_pr_comment, "create_pr_comment", "gitea:write")
        add(mcp_tools.list_actions, "list_actions", "gitea")
        add(mcp_tools.get_commit_statuses, "get_commit_statuses", "gitea")
        add(mcp_tools.list_releases, "list_releases", "gitea")
        add(mcp_tools.get_release, "get_release", "gitea")
        add(mcp_tools.create_release, "create_release", "gitea:write")
        add(mcp_tools.update_release, "update_release", "gitea:write")
        add(mcp_tools.delete_release, "delete_release", "gitea:write")
        add(mcp_tools.list_commits, "list_commits", "gitea")
        add(mcp_tools.compare, "compare", "gitea")

    # Notify
    from .notify_service import notify_registry
    if notify_registry.has_providers:
        add(mcp_tools.notify, "notify", "notify")

    # Weather
    if has_weather:
        add(mcp_tools.get_weather, "get_weather", "weather")

    # Registry commands (always registered when files exist)
    from .registry import list_commands
    commands = list_commands()
    if commands:
        mcp_tools.register_registry_command_tools(mcp, commands)


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def build_mcp_server() -> MCPServer:
    """Build an MCPServer with our tool surface registered.

    The SDK reports our server identity (name/version) so clients show the
    right name.  Tool registration is conditional on configuration, mirroring
    ``create_app()``.  All SDK-native, no overrides of SDK internals.

    Auth: when ``MCP_API_KEY`` is set we enable the SDK's Bearer auth by
    supplying ``auth=AuthSettings(...)`` + our ``token_verifier=`` (the SDK
    requires both together).  ``MCP_PUBLIC_URL`` (defaults to localhost) is
    used in the auth metadata URLs.
    """
    kwargs: dict[str, Any] = {}
    if is_auth_enabled():
        from mcp.server.auth.settings import AuthSettings
        base = os.environ.get("MCP_PUBLIC_URL", "http://localhost").rstrip("/")
        kwargs["auth"] = AuthSettings(
            issuer_url=f"{base}{MCP_MOUNT_PATH}",
            resource_server_url=f"{base}{MCP_MOUNT_PATH}",
        )
        kwargs["token_verifier"] = _ApiKeyVerifier()

    mcp = MCPServer(
        "mcp-server",
        title="MCP Server",
        description=(
            "Modular Command Provider — exposes CLI commands, calendar, "
            "Gitea, notify, and weather as model tools."
        ),
        version=__version__,
        **kwargs,
    )

    _register_conditional_tools(mcp)
    return mcp


def transport_security() -> TransportSecuritySettings | None:
    """Build transport-security settings for deployed hostnames.

    The SDK arms DNS-rebinding protection by default when ``host`` is
    127.0.0.1, which rejects every non-localhost request with a 421.  When
    an explicit ``MCP_ALLOWED_HOSTS`` (comma-separated) list is provided we
    translate it into the SDK's allowlist.  Localhost is always allowed.
    """
    import os

    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None  # localhost-only default (safe for dev / docker-on-host)

    allowed_hosts = [h.strip() for h in raw.split(",") if h.strip()]
    allowed_hosts = ["*"] if "*" in allowed_hosts else allowed_hosts
    return TransportSecuritySettings(allowed_hosts=allowed_hosts or ["*"])


def mount_mcp(app) -> MCPServer:
    """Mount the MCP endpoint onto the FastAPI app.

    The MCP server is registered on ``app.state`` so the app's lifespan can
    enter ``session_manager.run()`` (required once mounted).  Returns the
    MCPServer instance.

    ``streamable_http_path=\"/\"`` makes the mount prefix (``/mcp``) the
    whole endpoint, matching the standard MCP URL convention.
    """
    mcp = build_mcp_server()
    security = transport_security()
    kwargs: dict[str, Any] = {"streamable_http_path": "/"}
    if security is not None:
        kwargs["transport_security"] = security

    mcp_app = mcp.streamable_http_app(**kwargs)
    app.mount(MCP_MOUNT_PATH, mcp_app, name="mcp")
    setattr(app.state, _MCP_STATE_KEY, mcp)
    logger.info("Mounted MCP Streamable HTTP endpoint at %s", MCP_MOUNT_PATH)
    return mcp


def get_mounted_server(app) -> MCPServer | None:
    """Return the mounted MCPServer for a FastAPI app, or None."""
    return getattr(app.state, _MCP_STATE_KEY, None)
