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

This keeps local development frictionless while allowing production
deployments to secure the server with a simple shared secret.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

# FastAPI security scheme for OpenAPI documentation.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_BEARER_PREFIX = "bearer "


def _get_configured_key() -> str:
    """Read the API key from the environment (fresh on every call)."""
    return os.environ.get("MCP_API_KEY", "").strip()


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
