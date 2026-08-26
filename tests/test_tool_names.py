"""Tests for tool-name (operation ID) generation and collision guarding.

Verifies:
- OpenAPI operation IDs (the names MCP clients use as tool names) are the
  clean, deliberate route function names — not FastAPI's path-mangled
  default (``get_event_events__uid__get``).
- Registry commands surface as their command name (``log``, not ``log_command``).
- The ``_by_uid`` / ``_by_index`` / ``_by_id`` renames for
  fetch-by-identifier endpoints.
- Duplicate tool names raise a fatal error at app-creation time.
"""

from __future__ import annotations

import pytest

from app.tool_names import DuplicateToolNameError, make_operation_id_factory


def _operation_ids(app) -> dict[str, str]:
    """Return {operationId: path} for every documented path operation."""
    schema = app.openapi()
    result: dict[str, str] = {}
    for path, methods in schema["paths"].items():
        for op in methods.values():
            result[op.get("operationId")] = path
    return result


# --------------------------------------------------------------------------- #
# Full app: clean operation IDs
# --------------------------------------------------------------------------- #

class TestCleanOperationIDs:
    """With calendar + Gitea configured, operation IDs are short and human."""

    @pytest.fixture
    def app(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")
        monkeypatch.setenv("CALDAV_EDITABLE_CALENDAR", "Lyra")
        monkeypatch.setenv("GITEA_URL", "https://code.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "tok")
        monkeypatch.setenv("GITEA_DEFAULT_OWNER", "lyra")
        monkeypatch.setenv("GITEA_DEFAULT_REPO", "repo")

        from app.caldav_routes import _reset_service
        _reset_service()
        from app import gitea_routes
        gitea_routes._reset_service()

        from app.main import create_app
        return create_app()

    def test_no_path_mangle(self, app):
        """No operation ID contains the path-mangled '_events__uid__' pattern."""
        for opid in _operation_ids(app):
            assert "__" not in opid, f"path-mangled operation ID: {opid}"

    def test_clean_list_names(self, app):
        ids = _operation_ids(app)
        assert ids["list_events"] == "/events"
        assert ids["list_tasks"] == "/tasks"
        assert ids["list_issues"] == "/issues"
        assert ids["list_prs"] == "/prs"
        assert ids["list_releases"] == "/releases"
        assert ids["list_repos"] == "/user/repos"
        assert ids["list_calendars"] == "/calendars"

    def test_verb_names(self, app):
        ids = _operation_ids(app)
        assert ids["create_event"] == "/events"
        assert ids["update_event"] == "/events/{uid}"
        assert ids["delete_event"] == "/events/{uid}"
        assert ids["create_issue_comment"] == "/issues/{index}/comments"
        assert ids["merge_pr"] == "/prs/{index}/merge"
        assert ids["get_repo"] == "/repos/{owner}/{repo}"

    def test_by_uid_renames(self, app):
        """Fetch-by-identifier endpoints say what they identify by."""
        ids = _operation_ids(app)
        assert ids["get_event_by_uid"] == "/events/{uid}"
        assert ids["get_task_by_uid"] == "/tasks/{uid}"
        assert ids["get_issue_by_index"] == "/issues/{index}"
        assert ids["get_pr_by_index"] == "/prs/{index}"
        assert ids["get_release_by_id"] == "/releases/{release_id}"

    def test_registry_commands_use_command_names(self, app):
        """Registry-driven tools surface as their command name, e.g. 'log'."""
        ids = _operation_ids(app)
        assert ids["log"] == "/log"
        assert ids["log_read"] == "/log_read"
        assert "log_command" not in ids

    def test_all_operation_ids_unique(self, app):
        ids = _operation_ids(app)
        assert len(ids) == len(set(ids))
        assert len(ids) >= 25  # sanity: a rich surface is registered


# --------------------------------------------------------------------------- #
# Fail-fast: duplicate tool names
# --------------------------------------------------------------------------- #

class TestDuplicateToolName:
    """Two routes resolving to the same tool name must abort startup."""

    def _make_dup_app(self) -> None:
        """Force two routes to resolve to the same operation ID."""
        from fastapi import APIRouter, FastAPI

        app = FastAPI(generate_unique_id_function=make_operation_id_factory())
        router = APIRouter()

        @router.get("/a")
        async def same_name() -> dict:
            return {"route": "a"}

        @router.get("/b")
        async def same_name() -> dict:  # noqa: F811  (deliberate shadow)
            return {"route": "b"}

        app.include_router(router)
        app.openapi()  # forces generation

    def test_duplicate_raises(self):
        with pytest.raises(DuplicateToolNameError) as exc_info:
            self._make_dup_app()
        msg = str(exc_info.value)
        assert "Duplicate tool name" in msg
        assert "same_name" in msg
        # The message should name both conflicting routes.
        assert "/a" in msg and "/b" in msg
