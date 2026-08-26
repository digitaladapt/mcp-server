"""Tool-name (operation ID) generation and collision guarding.

FastAPI derives OpenAPI operation IDs — which MCP clients use as tool
names — from each route.  The default generator mangles the route's
path into the name (``get_event`` + ``/events/{uid}`` becomes
``get_event_events__uid__get``), which is verbose and burns context
every time tool schemas are sent to a model.

We instead use each route's *function name* as the tool name, e.g.:

    list_events, get_event_by_uid, create_issue, ...

These are declared deliberately in the route modules, are short, and
read like verbs — not path dumps.

Because tool names must be globally unique (a model sees one flat
namespace), ``make_operation_id`` fails fast at app-creation time if
two routes ever resolve to the same name.  This is the same guard the
proxy/allow-list feature will rely on: a proxied tool that collides
with a built-in one should abort startup loudly, never silently shadow
one another.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)


class DuplicateToolNameError(RuntimeError):
    """Raised when two routes resolve to the same tool (operation) name."""


def make_operation_id_factory() -> Callable[[APIRoute], str]:
    """Build a ``generate_unique_id`` factory that returns clean names
    and enforces global uniqueness.

    The returned callable is passed to ``FastAPI(..., generate_unique_id=...)``.
    FastAPI invokes it once per route while building the OpenAPI schema
    (which the TestClient does automatically on first access to ``/openapi.json``).
    """

    seen: dict[str, str] = {}

    def _unique_id(route: APIRoute) -> str:
        # The registry's dynamically-created routes have auto-tracked
        # function names (``handler.__name__``).  Re-create them here from
        # the underlying command schema so registry commands surface under
        # their command name, not ``{command}_command``.
        name = getattr(route.endpoint, "_registry_command", None) or route.name

        other = seen.get(name)
        if other is not None:
            # Two distinct routes want the same tool name.  This is a
            # systemic misconfiguration (e.g. a proxied tool colliding
            # with a built-in one) — address it at boot, not at runtime.
            raise DuplicateToolNameError(
                f"Duplicate tool name '{name}': routes '{other}' and "
                f"'{route.path}' both resolve to the same operation ID. "
                "Every tool exposed by mcp-server must have a unique name. "
                "Rename one of the routes (or its registry command) and restart."
            )

        seen[name] = route.path
        return name

    return _unique_id


def registry_tool_name(name: str) -> str:
    """Return the tool name for a registry command.

    Registry commands surface by their command name (``log``, ``log_read``),
    not FastAPI's default ``{command}_command`` handler name.
    """
    return name
