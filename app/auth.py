"""API key authentication dependency.

When the ``MCP_API_KEY`` environment variable is set, all endpoints except
``/health`` require authentication matching the configured key.  Two
credential styles are accepted:

- ``X-API-Key: <key>`` header
- ``Authorization: Bearer <key>`` header

If both are present, ``X-API-Key`` takes precedence.  If the variable is
unset or empty, authentication is disabled (open mode).

The API key is read from the environment on every request so that key
rotation takes effect immediately without a server restart.

**Secrets require auth.**  When any integration secret is configured
(a Discord webhook URL, an ntfy token/password, a CalDAV password, an
ICS feed URL, or a Gitea token) the server *refuses to start* unless
``MCP_API_KEY`` is also set.  Holding credentials while serving open
endpoints is a misconfiguration that would leak them, so this is a
hard startup failure (``RuntimeError``), not a warning.

This keeps local development frictionless while allowing production
deployments to secure the server with a simple shared secret.
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

# FastAPI security scheme for OpenAPI documentation.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_BEARER_PREFIX = "bearer "


def _get_configured_key() -> str:
    """Read the API key from the environment (fresh on every call)."""
    return os.environ.get("MCP_API_KEY", "").strip()


# Environment variables that carry (or point at) credentials worth
# protecting.  When any of these is set to a non-empty value, the
# server holds a secret and must require ``MCP_API_KEY``.
#
# - DISCORD_*_HOOK: webhook URLs embed the bot token in the path; anyone
#   with the URL can post as the bot.
# - NTFY_TOKEN / NTFY_PASSWORD: ntfy access token or basic-auth password.
# - CALDAV_PASSWORD: CalDAV account password.
# - ICS_CALENDAR_URL: published calendar feed URLs embed an unguessable
#   token; possession of the URL is possession of the calendar.
# - GITEA_TOKEN: Gitea API access token.
_DISCORD_HOOK_PATTERN = re.compile(r"^DISCORD_.*_HOOK$")

_SECRET_ENV_VARS = (
    "NTFY_TOKEN",
    "NTFY_PASSWORD",
    "CALDAV_PASSWORD",
    "ICS_CALENDAR_URL",
    "GITEA_TOKEN",
)


def detect_configured_secrets(
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return the names of configured env vars that hold secrets.

    A variable counts as configured when it is present and non-empty
    after stripping whitespace.  Defaults to the process environment.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    found = [
        name for name in _SECRET_ENV_VARS
        if env.get(name, "").strip()
    ]
    found.extend(
        sorted(
            name for name in env
            if _DISCORD_HOOK_PATTERN.match(name) and env[name].strip()
        )
    )
    return found


def require_auth_if_secrets(environ: Mapping[str, str] | None = None) -> None:
    """Fail fast at startup when secrets are configured but auth is not.

    Raises ``RuntimeError`` listing the offending variables when any
    secret env var is set while ``MCP_API_KEY`` is unset/empty.  Called
    once from ``create_app()`` so a misconfigured deployment never
    starts serving open endpoints backed by credentials.
    """
    secrets_found = detect_configured_secrets(environ)
    configured_key = (
        environ.get("MCP_API_KEY", "").strip()
        if environ is not None
        else _get_configured_key()
    )
    if secrets_found and not configured_key:
        raise RuntimeError(
            "Secrets are configured but MCP_API_KEY is not set. "
            "The server refuses to hold credentials while running "
            "without authentication. Detected: "
            + ", ".join(secrets_found)
            + ". Either set MCP_API_KEY or unset the variables above."
        )


def is_auth_enabled() -> bool:
    """Return True when API key authentication is active."""
    return bool(_get_configured_key())


def get_api_key() -> str:
    """Return the configured API key (empty string if unset)."""
    return _get_configured_key()


def _extract_bearer_token(request: Request) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    Returns ``None`` when the header is absent, uses a scheme other than
    ``Bearer`` (scheme match is case-insensitive), or carries an empty
    token.
    """
    authorization = request.headers.get("Authorization", "").strip()
    if not authorization.lower().startswith(_BEARER_PREFIX):
        return None
    token = authorization[len(_BEARER_PREFIX):].strip()
    return token or None


async def verify_api_key(
    request: Request,
    provided_key: str | None = Depends(_api_key_header),
) -> bool:
    """FastAPI dependency that validates the request credentials.

    - If ``MCP_API_KEY`` is unset, authentication is disabled (returns True).
    - Accepts the key via ``X-API-Key`` header or
      ``Authorization: Bearer <key>`` header (``X-API-Key`` wins when both
      are present).
    - The key must match using constant-time comparison.
    - Returns 401 on missing credentials, 403 on a wrong key.
    """
    configured_key = _get_configured_key()

    if not configured_key:
        # Auth disabled — open access.
        return True

    provided_key = provided_key or _extract_bearer_token(request)

    if provided_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials (X-API-Key or Authorization: Bearer header required)",
            headers={"WWW-Authenticate": 'Bearer realm="mcp-server"'},
        )

    if not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return True
