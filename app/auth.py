"""API key authentication dependency.

When the ``MCP_API_KEY`` environment variable is set, all endpoints except
``/health`` require an ``X-API-Key`` header matching the configured key.
If the variable is unset or empty, authentication is disabled (open mode).

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


def _get_configured_key() -> str:
    """Read the API key from the environment (fresh on every call)."""
    return os.environ.get("MCP_API_KEY", "").strip()


def is_auth_enabled() -> bool:
    """Return True when API key authentication is active."""
    return bool(_get_configured_key())


def get_api_key() -> str:
    """Return the configured API key (empty string if unset)."""
    return _get_configured_key()


async def verify_api_key(
    request: Request,
    provided_key: str | None = Depends(_api_key_header),
) -> bool:
    """FastAPI dependency that validates the ``X-API-Key`` header.

    - If ``MCP_API_KEY`` is unset, authentication is disabled (returns True).
    - If set, the header must match using constant-time comparison.
    - Returns 401 on missing key, 403 on wrong key.
    """
    configured_key = _get_configured_key()

    if not configured_key:
        # Auth disabled — open access.
        return True

    if provided_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": 'ApiKey realm="mcp-server"'},
        )

    if not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return True
