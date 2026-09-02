"""SDK-native MCP tool surface.

Builds the MCP tool set for the official ``mcp`` Python SDK (v2) by
adapting **our project** to the SDK's idioms — we register first-class
``@mcp.tool()`` / ``add_tool()`` handlers that call our existing service
layer, rather than wrapping or monkey-patching the SDK.

Every tool name here is deliberate and stable (``list_events``,
``get_event_by_uid``, ``create_issue``, ``log``, ...) and mirrors the
OpenAPI operation IDs so a model sees the *same* flat tool namespace
over both transports.

Handlers are thin adapters: they parse/validate arguments, call the
existing service singletons via ``run_in_threadpool`` (exactly like the
FastAPI routes), and return structured dicts — the SDK wraps them into
MCP ``content`` + ``structured_content``.  Catchable service errors are
converted to ``ToolError`` so the calling model sees a clean tool
failure; validation problems raise the same style of error.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Collision guard (mirrors app.tool_names.DuplicateToolNameError)
# --------------------------------------------------------------------------- #

class DuplicateMCPToolNameError(RuntimeError):
    """Raised when two MCP tools would register under the same name.

    Mirrors the existing OpenAPI operation-ID guard: MCP tools live in the
    same flat namespace a model sees, so a collision must abort startup
    loudly, never silently shadow one tool with another.
    """


class _ToolRegistry:
    """Tracks MCP tool names to enforce global uniqueness at build time."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def check(self, name: str, source: str) -> None:
        other = self._seen.get(name)
        if other is not None:
            raise DuplicateMCPToolNameError(
                f"Duplicate MCP tool name '{name}': {other} and {source} "
                "both register the same tool. Every MCP tool exposed by "
                "mcp-server must have a unique name."
            )
        self._seen[name] = source


_tool_registry = _ToolRegistry()


# A per-build registry can be injected by the app factory (mcp_app) so the
# uniqueness guard is scoped to one server build — matching how FastAPI's
# operation-ID guard is per-app.  When not injected (direct use in tests),
# it defaults to the module-global registry above.
def _set_tool_registry(registry: _ToolRegistry) -> None:
    """Replace the active tool registry (used by the MCP app factory)."""
    global _tool_registry
    _tool_registry = registry


def add_tool_guarded(mcp, fn, name: str, source: str) -> None:
    """Register an SDK tool with the per-build collision guard.

    Wraps ``mcp.add_tool(fn, name=name)`` after checking that ``name`` is
    unique within this server build (mirrors the OpenAPI operation-ID
    guard).  Use this for every tool registration so a collision aborts
    startup loudly instead of silently shadowing one tool.
    """
    _tool_registry.check(name, source)
    mcp.add_tool(fn, name=name)


def _tool_error(message: str) -> Exception:
    from mcp.server.mcpserver.exceptions import ToolError
    return ToolError(message)


def _parse_dt(value: str | None):
    """Parse an ISO 8601 string into datetime or date, or None."""
    from datetime import date, datetime
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise _tool_error(f"Invalid date/time format: {value}. Use ISO 8601.")


# --------------------------------------------------------------------------- #
# Calendar tools (unified provider registry)
# --------------------------------------------------------------------------- #

async def list_events(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """List calendar events across all configured providers.

    Args:
        start: ISO 8601 start datetime/date filter.
        end: ISO 8601 end datetime/date filter.
    """
    from .providers import provider_registry
    start_dt, end_dt = _parse_dt(start), _parse_dt(end)
    events = await run_in_threadpool(
        provider_registry.list_all_events, start=start_dt, end=end_dt
    )
    return {"events": [e.model_dump() for e in events], "total": len(events)}


async def get_event_by_uid(uid: str) -> dict[str, Any]:
    """Get a single calendar event by its UID.

    Args:
        uid: The event UID.
    """
    from .providers import provider_registry
    event = await run_in_threadpool(provider_registry.get_event, uid)
    if event is None:
        raise _tool_error(f"Event not found: {uid}")
    return event.model_dump()


async def list_calendars() -> dict[str, Any]:
    """List all calendars with their editability flag."""
    from .providers import provider_registry
    cals = await run_in_threadpool(provider_registry.list_all_calendars)
    editable = [c for c in cals if c.editable]
    readonly = [c for c in cals if not c.editable]
    return {
        "calendars": [c.model_dump() for c in cals],
        "editable_count": len(editable),
        "readonly_count": len(readonly),
    }


async def create_event(
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    all_day: bool = False,
    categories: list[str] | None = None,
    status: str | None = None,
    priority: int | None = None,
    alarms: list[dict] | None = None,
    enable_alarms: bool = True,
) -> dict[str, Any]:
    """Create a new calendar event on the editable calendar.

    Args:
        summary: Event title.
        start: ISO 8601 start datetime.
        end: ISO 8601 end datetime.
        description: Optional description.
        location: Optional location.
        all_day: Whether this is an all-day event.
        categories: Optional list of category strings.
        status: TENTATIVE, CONFIRMED (default) or CANCELLED.
        priority: 1 (highest) – 9 (lowest).
        alarms: Optional list of alarm dicts
            (trigger_minutes, action, description, related_to).
        enable_alarms: Add the default start-time alarm when True.
    """
    from .caldav_models import CreateEventRequest
    from .caldav_routes import _get_service as _get_caldav_service
    from .caldav_service import CalDAVError

    req = CreateEventRequest(
        summary=summary, start=start, end=end, description=description,
        location=location, all_day=all_day, categories=categories,
        status=status, priority=priority, alarms=alarms,
        enable_alarms=enable_alarms,
    )
    svc = _get_caldav_service()
    try:
        event = await run_in_threadpool(svc.create_event, req)
    except CalDAVError as exc:
        logger.exception("CalDAV error creating event")
        raise _tool_error(f"Calendar service error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error creating event")
        raise _tool_error(f"Calendar service error: {exc}") from exc
    return event.model_dump()


async def update_event(
    uid: str,
    summary: str | None = None,
    description: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
    categories: list[str] | None = None,
    status: str | None = None,
    priority: int | None = None,
    alarms: list[dict] | None = None,
    enable_alarms: bool | None = None,
) -> dict[str, Any]:
    """Update an existing calendar event.

    Only provided fields are changed.

    Args:
        uid: The event UID.
        summary: New title.
        start: New ISO 8601 start datetime.
        end: New ISO 8601 end datetime.
        description: New description.
        location: New location.
        all_day: New all-day flag.
        categories: New category list.
        status: New status.
        priority: New priority.
        alarms: Replace alarms; None preserves existing.
        enable_alarms: False removes all alarms.
    """
    from .caldav_models import UpdateEventRequest
    from .caldav_routes import _get_service as _get_caldav_service
    from .caldav_service import CalDAVError

    req = UpdateEventRequest(
        summary=summary, description=description, start=start, end=end,
        location=location, all_day=all_day, categories=categories,
        status=status, priority=priority, alarms=alarms,
        enable_alarms=enable_alarms,
    )
    svc = _get_caldav_service()
    try:
        event = await run_in_threadpool(svc.update_event, uid, req)
    except CalDAVError as exc:
        logger.exception("CalDAV error updating event %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error updating event %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    return event.model_dump()


async def delete_event(uid: str) -> dict[str, Any]:
    """Delete a calendar event.

    Args:
        uid: The event UID.
    """
    from .caldav_routes import _get_service as _get_caldav_service
    from .caldav_service import CalDAVError

    svc = _get_caldav_service()
    try:
        deleted = await run_in_threadpool(svc.delete_event, uid)
    except CalDAVError as exc:
        logger.exception("CalDAV error deleting event %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error deleting event %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    if not deleted:
        raise _tool_error(f"Event not found: {uid}")
    return {"deleted": True, "uid": uid}


async def list_tasks(completed: bool | None = None) -> dict[str, Any]:
    """List calendar tasks (CalDAV only)."""
    from .providers import provider_registry
    tasks = await run_in_threadpool(
        provider_registry.list_all_tasks, completed=completed
    )
    return {"tasks": [t.model_dump() for t in tasks], "total": len(tasks)}


async def get_task_by_uid(uid: str) -> dict[str, Any]:
    """Get a single calendar task by UID.

    Args:
        uid: The task UID.
    """
    from .providers import provider_registry
    task = await run_in_threadpool(provider_registry.get_task, uid)
    if task is None:
        raise _tool_error(f"Task not found: {uid}")
    return task.model_dump()


async def create_task(
    summary: str,
    description: str | None = None,
    due: str | None = None,
    priority: int | None = None,
    percent_complete: int | None = None,
    categories: list[str] | None = None,
    alarms: list[dict] | None = None,
    enable_alarms: bool = True,
) -> dict[str, Any]:
    """Create a new calendar task.

    Args:
        summary: Task title.
        description: Optional description.
        due: ISO 8601 due datetime.
        priority: 1 (highest) – 9 (lowest).
        percent_complete: 0–100.
        categories: Optional category list.
        alarms: Optional list of alarm dicts.
        enable_alarms: Add default due-time alarm when True.
    """
    from .caldav_models import CreateTaskRequest
    from .caldav_routes import _get_service as _get_caldav_service
    from .caldav_service import CalDAVError

    req = CreateTaskRequest(
        summary=summary, description=description, due=due, priority=priority,
        percent_complete=percent_complete, categories=categories,
        alarms=alarms, enable_alarms=enable_alarms,
    )
    svc = _get_caldav_service()
    try:
        task = await run_in_threadpool(svc.create_task, req)
    except CalDAVError as exc:
        logger.exception("CalDAV error creating task")
        raise _tool_error(f"Calendar service error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error creating task")
        raise _tool_error(f"Calendar service error: {exc}") from exc
    return task.model_dump()


async def update_task(
    uid: str,
    summary: str | None = None,
    description: str | None = None,
    due: str | None = None,
    priority: int | None = None,
    status: str | None = None,
    percent_complete: int | None = None,
    categories: list[str] | None = None,
    alarms: list[dict] | None = None,
    enable_alarms: bool | None = None,
) -> dict[str, Any]:
    """Update an existing calendar task.

    Only provided fields are changed.

    Args:
        uid: The task UID.
        summary: New title.
        description: New description.
        due: New ISO 8601 due datetime.
        priority: New priority.
        status: New status.
        percent_complete: New 0–100 completion.
        categories: New category list.
        alarms: Replace alarms; None preserves existing.
        enable_alarms: False removes all alarms.
    """
    from .caldav_models import UpdateTaskRequest
    from .caldav_routes import _get_service as _get_caldav_service
    from .caldav_service import CalDAVError

    req = UpdateTaskRequest(
        summary=summary, description=description, due=due, priority=priority,
        status=status, percent_complete=percent_complete, categories=categories,
        alarms=alarms, enable_alarms=enable_alarms,
    )
    svc = _get_caldav_service()
    try:
        task = await run_in_threadpool(svc.update_task, uid, req)
    except CalDAVError as exc:
        logger.exception("CalDAV error updating task %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error updating task %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    return task.model_dump()


async def delete_task(uid: str) -> dict[str, Any]:
    """Delete a calendar task.

    Args:
        uid: The task UID.
    """
    from .caldav_routes import _get_service as _get_caldav_service
    from .caldav_service import CalDAVError

    svc = _get_caldav_service()
    try:
        deleted = await run_in_threadpool(svc.delete_task, uid)
    except CalDAVError as exc:
        logger.exception("CalDAV error deleting task %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error deleting task %s", uid)
        raise _tool_error(f"Calendar service error: {exc}") from exc
    if not deleted:
        raise _tool_error(f"Task not found: {uid}")
    return {"deleted": True, "uid": uid}


# --------------------------------------------------------------------------- #
# Gitea tools
# --------------------------------------------------------------------------- #

def _gitea_service():
    from .gitea_routes import _get_service
    return _get_service()


async def search_repos(
    query: str = "",
    owner: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """Search repositories across the whole Gitea instance.

    Args:
        query: Search query (name, description).
        owner: Filter by owner/org.
        page: Page number.
        limit: Results per page.
    """
    svc = _gitea_service()
    repos, total = await run_in_threadpool(
        svc.search_repos, query, owner=owner, page=page, limit=limit
    )
    return {"repos": [r.model_dump() for r in repos], "total": total}


async def get_repo(owner: str | None = None, repo: str | None = None) -> dict[str, Any]:
    """Get repository information.

    Args:
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    result = await run_in_threadpool(svc.get_repo, owner=owner, repo=repo)
    if result is None:
        raise _tool_error(f"Repository not found: {owner or '?'}/{repo or '?'}")
    return result.model_dump()


async def list_issues(
    state: str = "open",
    labels: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List issues in the repository.

    Args:
        state: open, closed, or all.
        labels: Comma-separated label list.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    issues = await run_in_threadpool(
        svc.list_issues, state=state, labels=labels, owner=owner, repo=repo,
        page=page, limit=limit,
    )
    return {"issues": [i.model_dump() for i in issues], "total": len(issues)}


async def get_issue(
    index: int,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Get a single issue by its number.

    Args:
        index: Issue number.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    issue = await run_in_threadpool(svc.get_issue, index, owner=owner, repo=repo)
    if issue is None:
        raise _tool_error(f"Issue #{index} not found")
    return issue.model_dump()


async def create_issue(
    title: str,
    body: str | None = None,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    milestone: int | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create a new issue.

    Args:
        title: Issue title.
        body: Issue body.
        labels: Optional label names.
        assignees: Optional assignee usernames.
        milestone: Optional milestone id.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import IssueCreate
    svc = _gitea_service()
    req = IssueCreate(title=title, body=body, labels=labels, assignees=assignees, milestone=milestone)
    try:
        issue = await run_in_threadpool(
            svc.create_issue, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error creating issue: {exc}") from exc
    return issue.model_dump()


async def update_issue(
    index: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    milestone: int | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Update an existing issue.

    Args:
        index: Issue number.
        title: New title.
        body: New body.
        state: open or closed.
        milestone: New milestone id (0 to clear).
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import IssueUpdate
    svc = _gitea_service()
    req = IssueUpdate(title=title, body=body, state=state, milestone=milestone)
    try:
        issue = await run_in_threadpool(
            svc.update_issue, index, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error updating issue: {exc}") from exc
    return issue.model_dump()


async def create_issue_comment(
    index: int,
    body: str,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Comment on an issue.

    Args:
        index: Issue number.
        body: Comment text.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import CommentCreate
    svc = _gitea_service()
    req = CommentCreate(body=body)
    try:
        comment = await run_in_threadpool(
            svc.create_issue_comment, index, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error commenting on issue: {exc}") from exc
    return comment.model_dump()


async def list_issue_comments(
    index: int,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """List comments on an issue.

    Args:
        index: Issue number.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    comments = await run_in_threadpool(
        svc.list_issue_comments, index, owner=owner, repo=repo
    )
    return {"comments": [c.model_dump() for c in comments]}


async def list_branches(
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """List branches in the repository.

    Args:
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    branches = await run_in_threadpool(svc.list_branches, owner=owner, repo=repo)
    if branches is None:
        raise _tool_error(f"Repository not found: {owner or '?'}/{repo or '?'}")
    return {"branches": [b.model_dump() for b in branches]}


async def create_branch(
    name: str,
    from_ref: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create a new branch.

    Args:
        name: New branch name.
        from_ref: Source branch/tag/SHA (defaults to repo default).
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import BranchCreate
    svc = _gitea_service()
    req = BranchCreate(name=name, from_ref=from_ref)
    try:
        branch = await run_in_threadpool(
            svc.create_branch, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error creating branch: {exc}") from exc
    return branch.model_dump()


async def delete_branch(
    name: str,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Delete a branch.

    Args:
        name: Branch name.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    try:
        deleted = await run_in_threadpool(
            svc.delete_branch, name, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error deleting branch: {exc}") from exc
    if not deleted:
        raise _tool_error(f"Branch not found: {name}")
    return {"deleted": True, "name": name}


async def list_prs(
    state: str = "open",
    owner: str | None = None,
    repo: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List pull requests in the repository.

    Args:
        state: open, closed, or all.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    prs = await run_in_threadpool(
        svc.list_prs, state=state, owner=owner, repo=repo, page=page, limit=limit
    )
    return {"prs": [p.model_dump() for p in prs], "total": len(prs)}


async def get_pr(
    index: int,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Get a single pull request by its number.

    Args:
        index: PR number.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    pr = await run_in_threadpool(svc.get_pr, index, owner=owner, repo=repo)
    if pr is None:
        raise _tool_error(f"Pull request #{index} not found")
    return pr.model_dump()


async def create_pr(
    title: str,
    head: str,
    base: str,
    body: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create a new pull request.

    Args:
        title: PR title.
        head: Head branch.
        base: Base branch.
        body: Optional PR body.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import PRCreate
    svc = _gitea_service()
    req = PRCreate(title=title, head=head, base=base, body=body)
    try:
        pr = await run_in_threadpool(svc.create_pr, req, owner=owner, repo=repo)
    except Exception as exc:
        raise _tool_error(f"Gitea error creating PR: {exc}") from exc
    return pr.model_dump()


async def update_pr(
    index: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Update a pull request.

    Args:
        index: PR number.
        title: New title.
        body: New body.
        state: open or closed.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import PRUpdate
    svc = _gitea_service()
    req = PRUpdate(title=title, body=body, state=state)
    try:
        pr = await run_in_threadpool(svc.update_pr, index, req, owner=owner, repo=repo)
    except Exception as exc:
        raise _tool_error(f"Gitea error updating PR: {exc}") from exc
    return pr.model_dump()


async def merge_pr(
    index: int,
    do: str = "merge",
    merge_commit_message: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Merge a pull request.

    Args:
        index: PR number.
        do: Merge method — merge, rebase, rebase-merge, squash, or manually-merged.
        merge_commit_message: Optional merge commit message.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import PRMerge
    svc = _gitea_service()
    req = PRMerge(do=do, merge_commit_message=merge_commit_message)
    try:
        ok = await run_in_threadpool(svc.merge_pr, index, req, owner=owner, repo=repo)
    except Exception as exc:
        raise _tool_error(f"Gitea error merging PR: {exc}") from exc
    if not ok:
        raise _tool_error(f"PR #{index} could not be merged")
    return {"merged": True, "index": index}


async def list_pr_reviews(
    index: int,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """List reviews on a pull request.

    Args:
        index: PR number.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    reviews = await run_in_threadpool(svc.list_pr_reviews, index, owner=owner, repo=repo)
    return {"reviews": [r.model_dump() for r in reviews]}


async def create_pr_comment(
    index: int,
    body: str,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Comment on a pull request.

    Args:
        index: PR number.
        body: Comment text.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import CommentCreate
    svc = _gitea_service()
    req = CommentCreate(body=body)
    try:
        comment = await run_in_threadpool(
            svc.create_pr_comment, index, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error commenting on PR: {exc}") from exc
    return comment.model_dump()


async def list_actions(
    owner: str | None = None,
    repo: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List CI workflow runs in the repository.

    Args:
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    runs = await run_in_threadpool(
        svc.list_actions, owner=owner, repo=repo, page=page, limit=limit
    )
    return {"runs": [r.model_dump() for r in runs], "total": len(runs)}


async def get_commit_statuses(
    sha: str,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Get CI status checks for a specific commit.

    Args:
        sha: Commit SHA.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    statuses = await run_in_threadpool(
        svc.get_commit_statuses, sha, owner=owner, repo=repo
    )
    return {"statuses": [s.model_dump() for s in statuses]}


async def list_releases(
    owner: str | None = None,
    repo: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List releases in the repository.

    Args:
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    releases = await run_in_threadpool(
        svc.list_releases, owner=owner, repo=repo, page=page, limit=limit
    )
    return {"releases": [r.model_dump() for r in releases], "total": len(releases)}


async def get_release(
    release_id: int,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Get a single release by ID.

    Args:
        release_id: Release ID.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    release = await run_in_threadpool(
        svc.get_release, release_id, owner=owner, repo=repo
    )
    if release is None:
        raise _tool_error(f"Release {release_id} not found")
    return release.model_dump()


async def create_release(
    tag_name: str,
    name: str | None = None,
    body: str | None = None,
    target: str | None = None,
    draft: bool = False,
    prerelease: bool = False,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create a new release.

    Args:
        tag_name: Tag name.
        name: Optional release name.
        body: Optional release body.
        target: Target commitish.
        draft: Whether this is a draft.
        prerelease: Whether this is a prerelease.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import ReleaseCreate
    svc = _gitea_service()
    req = ReleaseCreate(
        tag_name=tag_name, name=name, body=body, target=target,
        draft=draft, prerelease=prerelease,
    )
    try:
        release = await run_in_threadpool(
            svc.create_release, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error creating release: {exc}") from exc
    return release.model_dump()


async def update_release(
    release_id: int,
    name: str | None = None,
    body: str | None = None,
    draft: bool | None = None,
    prerelease: bool | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Update an existing release.

    Args:
        release_id: Release ID.
        name: New name.
        body: New body.
        draft: New draft flag.
        prerelease: New prerelease flag.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    from .gitea_models import ReleaseUpdate
    svc = _gitea_service()
    req = ReleaseUpdate(name=name, body=body, draft=draft, prerelease=prerelease)
    try:
        release = await run_in_threadpool(
            svc.update_release, release_id, req, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error updating release: {exc}") from exc
    return release.model_dump()


async def delete_release(
    release_id: int,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Delete a release.

    Args:
        release_id: Release ID.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    try:
        deleted = await run_in_threadpool(
            svc.delete_release, release_id, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error deleting release: {exc}") from exc
    if not deleted:
        raise _tool_error(f"Release {release_id} not found")
    return {"deleted": True, "release_id": release_id}


async def list_commits(
    sha: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List recent commits in the repository.

    Args:
        sha: Branch, tag, or SHA (defaults to default branch).
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    commits = await run_in_threadpool(
        svc.list_commits, sha=sha, owner=owner, repo=repo, page=page, limit=limit
    )
    return {"commits": [c.model_dump() for c in commits], "total": len(commits)}


async def compare(
    base: str,
    head: str,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Compare two refs (branches, tags, or SHAs).

    Args:
        base: Base ref.
        head: Head ref.
        owner: Repo owner/org (defaults to configured).
        repo: Repo name (defaults to configured).
    """
    svc = _gitea_service()
    try:
        result = await run_in_threadpool(
            svc.compare, base, head, owner=owner, repo=repo
        )
    except Exception as exc:
        raise _tool_error(f"Gitea error comparing {base}...{head}: {exc}") from exc
    return result.model_dump()


# --------------------------------------------------------------------------- #
# Notify tools
# --------------------------------------------------------------------------- #

async def notify(
    message: str,
    title: str | None = None,
    level: str = "notice",
    color: str | None = None,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """Send a notification to configured providers (Discord / ntfy).

    Args:
        message: The message to send.
        title: Optional title.
        level: info, notice, critical, or emergency.
        color: Optional color (red, orange, yellow, green, blue, purple,
            brown, black, white).
        channels: Provider names to target; defaults to all.
    """
    from .notify_models import NotifyRequest
    from .notify_service import notify_registry

    req = NotifyRequest(
        message=message, title=title, level=level, color=color, channels=channels
    )
    results = await run_in_threadpool(notify_registry.send, req)
    return {
        "sent": any(r.success for r in results),
        "results": [r.model_dump() for r in results],
    }


# --------------------------------------------------------------------------- #
# Weather tools
# --------------------------------------------------------------------------- #

async def weather(days: int = 1) -> dict[str, Any]:
    """Get current weather and multi-day forecast.

    Args:
        days: Number of forecast days (1–7, clamped).
    """
    from .weather_service import get_weather_service

    svc = get_weather_service()
    resp = await run_in_threadpool(svc.get_weather, days)
    return resp.model_dump()


# --------------------------------------------------------------------------- #
# Registry command tools
# --------------------------------------------------------------------------- #

def register_registry_command_tools(mcp, commands) -> None:
    """Register one MCP tool per registry command.

    Each tool derives its schema from the command's ``ArgSpec`` list and
    calls the existing :func:`app.executor.run_command` — the exact same
    code path the dedicated HTTP routes use.

    Only non-hidden args become tool parameters, matching the OpenAPI
    request model.  Names are the stable command names (``log``,
    ``log_read``) so they match the existing tool namespace.
    """
    import inspect as _inspect

    from .executor import run_command

    for schema in commands:
        name = schema.name
        _tool_registry.check(name, f"registry:{name}")

        async def handler_for_command(
            _schema=schema,
            **kwargs: Any,
        ) -> dict[str, Any]:
            # kwargs arrive keyed by the SDK-derived field names (the
            # signature parameters above).  Map them back to the original
            # CLI arg names the executor expects (mirrors
            # registry_routes._model_to_args).
            arguments: dict[str, Any] = {}
            for spec in _schema.args:
                if spec.hidden:
                    continue
                field_name = spec.field_name or _safe_field_name(spec.name)
                if field_name in kwargs:
                    val = kwargs[field_name]
                    if val is None:
                        continue
                    if spec.type in ("bool", "flag") and not val:
                        continue
                    arguments[spec.name] = val
            try:
                result = await run_command(_schema, arguments)
            except ValueError as exc:
                raise _tool_error(str(exc)) from exc
            return result.model_dump()

        # Expose one parameter per non-hidden arg (with the field_name
        # alias) so the SDK derives the input schema from the function
        # signature.  The executor receives kwargs keyed by the original
        # CLI arg name, which _model_to_args already handles; here we
        # map field_name -> name when calling run_command.
        # Build one parameter per non-hidden arg.  Python requires required
        # params before optional ones in a signature, and the registry YAML
        # may list them in any order (e.g. optional --level before required
        # message), so we sort required-first here.
        params: list[_inspect.Parameter] = []
        for spec in sorted(schema.args, key=lambda s: not (s.required and not s.has_default)):
            if spec.hidden:
                continue
            field_name = spec.field_name or _safe_field_name(spec.name)
            default = _py_default(spec)
            annotation = _py_annotation(spec)
            params.append(
                _inspect.Parameter(
                    field_name,
                    _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=default,
                    annotation=annotation,
                )
            )
        handler_for_command.__signature__ = _inspect.Signature(params)  # type: ignore[attr-defined]
        handler_for_command._mcp_tool_name = name  # type: ignore[attr-defined]
        mcp.add_tool(handler_for_command, name=name)


def _safe_field_name(arg_name: str) -> str:
    """Convert a CLI arg name to a valid Python identifier."""
    return arg_name.lstrip("-").replace("-", "_")


def _py_annotation(spec) -> Any:
    """Map an ArgSpec to a Python type annotation for the tool schema."""
    if spec.choices:
        from typing import Literal
        return Literal[tuple(spec.choices)]  # type: ignore[valid-type]
    type_map = {"string": str, "int": int, "float": float, "bool": bool, "flag": bool}
    return type_map.get(spec.type, str)


def _py_default(spec):
    """Map an ArgSpec to a Python default value for the tool schema."""
    import inspect as _inspect
    if spec.required and not spec.has_default:
        return _inspect.Parameter.empty
    if spec.has_default:
        return spec.default
    return None


def get_registry_commands():
    """Return the currently-registered command schemas."""
    from .registry import list_commands
    return list_commands()
