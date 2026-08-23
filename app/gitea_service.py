"""Gitea service layer.

Provides a typed interface to the Gitea REST API, covering issues,
branches, pull requests, CI/Actions status, releases, and branch
comparison.

Configuration is read from environment variables via
:class:`~app.gitea_models.GiteaConfig`:

  GITEA_URL              – Gitea server URL (e.g. https://code.devgnome.com)
  GITEA_TOKEN            – API access token
  GITEA_DEFAULT_OWNER    – default repo owner
  GITEA_DEFAULT_REPO     – default repo name
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .gitea_models import (
    ActionRun,
    BranchCreate,
    BranchInfo,
    CommentCreate,
    CommentDetail,
    CommitInfo,
    CommitStatus,
    CompareResult,
    FileChange,
    GiteaConfig,
    IssueCreate,
    IssueDetail,
    IssueLabel,
    IssueMilestone,
    IssueUpdate,
    IssueUser,
    PRCreate,
    PRDetail,
    PRMerge,
    PRUpdate,
    ReleaseCreate,
    ReleaseDetail,
    ReleaseUpdate,
    RepoDetail,
    ReviewDetail,
)

logger = logging.getLogger(__name__)


class GiteaError(Exception):
    """Raised on Gitea API operation failures."""


# --------------------------------------------------------------------------- #
# Helpers: transform raw Gitea API JSON into our Pydantic models
# --------------------------------------------------------------------------- #

def _parse_user(data: dict[str, Any] | None) -> IssueUser | None:
    if not data:
        return None
    return IssueUser(
        id=data.get("id", 0),
        login=data.get("login", ""),
        full_name=data.get("full_name"),
        avatar_url=data.get("avatar_url"),
    )


def _as_list(value: Any) -> list[Any]:
    """Return ``value`` as a list, treating ``None`` as empty.

    Gitea serializes empty collections as ``null`` rather than ``[]`` on
    some endpoints (notably ``labels`` / ``assignees`` on PRs and issues).
    Iterating ``None`` raises ``TypeError: 'NoneType' object is not
    iterable``, so every collection field must be passed through here.
    """
    return value if isinstance(value, list) else []


def _parse_label(data: dict[str, Any]) -> IssueLabel:
    """Parse a label. """
    return IssueLabel(
        id=data.get("id", 0),
        name=data.get("name", ""),
        color=data.get("color", ""),
    )


def _parse_milestone(data: dict[str, Any] | None) -> IssueMilestone | None:
    if not data:
        return None
    return IssueMilestone(
        id=data.get("id", 0),
        title=data.get("title", ""),
        description=data.get("description"),
    )


def _parse_issue(data: dict[str, Any]) -> IssueDetail:
    return IssueDetail(
        number=data.get("number", 0),
        title=data.get("title", ""),
        body=data.get("body"),
        state=data.get("state", "open"),
        labels=[_parse_label(l) for l in _as_list(data.get("labels"))],
        assignees=[_parse_user(u) for u in _as_list(data.get("assignees")) if u],
        milestone=_parse_milestone(data.get("milestone")),
        author=_parse_user(data.get("user")),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        closed_at=data.get("closed_at"),
        comments=data.get("comments", 0),
        html_url=data.get("html_url"),
    )


def _parse_pr(data: dict[str, Any]) -> PRDetail:
    head = data.get("head") or {}
    base = data.get("base") or {}
    head_repo = head.get("repo") or {}
    base_repo = base.get("repo") or {}
    return PRDetail(
        number=data.get("number", 0),
        title=data.get("title", ""),
        body=data.get("body"),
        state=data.get("state", "open"),
        merged=data.get("merged", False),
        mergeable=data.get("mergeable"),
        merge_commit_sha=data.get("merge_commit_sha"),
        head_branch=head.get("ref", ""),
        head_repo=head_repo.get("full_name"),
        base_branch=base.get("ref", ""),
        base_repo=base_repo.get("full_name"),
        labels=[_parse_label(l) for l in _as_list(data.get("labels"))],
        assignees=[_parse_user(u) for u in _as_list(data.get("assignees")) if u],
        milestone=_parse_milestone(data.get("milestone")),
        author=_parse_user(data.get("user")),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        closed_at=data.get("closed_at"),
        merged_at=data.get("merged_at"),
        comments=data.get("comments", 0),
        additions=data.get("additions"),
        deletions=data.get("deletions"),
        changed_files=data.get("changed_files"),
        html_url=data.get("html_url"),
    )


def _parse_release(data: dict[str, Any]) -> ReleaseDetail:
    return ReleaseDetail(
        id=data.get("id", 0),
        tag_name=data.get("tag_name", ""),
        target=data.get("target_commitish"),
        name=data.get("name"),
        body=data.get("body"),
        draft=data.get("draft", False),
        prerelease=data.get("prerelease", False),
        author=_parse_user(data.get("author")),
        created_at=data.get("created_at", ""),
        published_at=data.get("published_at"),
        html_url=data.get("html_url"),
    )


def _parse_action_run(data: dict[str, Any]) -> ActionRun:
    return ActionRun(
        id=data.get("id", 0),
        name=data.get("name"),
        status=data.get("status", ""),
        conclusion=data.get("conclusion"),
        branch=data.get("head_branch") or data.get("branch"),
        commit_sha=data.get("head_sha") or data.get("sha"),
        event=data.get("event"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        html_url=data.get("html_url"),
    )


def _parse_commit_status(data: dict[str, Any]) -> CommitStatus:
    return CommitStatus(
        state=data.get("status", ""),
        context=data.get("context"),
        description=data.get("description"),
        target_url=data.get("target_url"),
        created_at=data.get("created_at"),
    )


def _parse_comment(data: dict[str, Any]) -> CommentDetail:
    return CommentDetail(
        id=data.get("id", 0),
        body=data.get("body", ""),
        author=_parse_user(data.get("user")),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at"),
    )


def _parse_review(data: dict[str, Any]) -> ReviewDetail:
    return ReviewDetail(
        id=data.get("id", 0),
        user=_parse_user(data.get("user")),
        body=data.get("body"),
        state=data.get("state", ""),
        submitted_at=data.get("submitted_at"),
    )


def _parse_commit(data: dict[str, Any]) -> CommitInfo:
    commit_data = data.get("commit", {})
    author_data = commit_data.get("author", {})
    return CommitInfo(
        sha=data.get("sha", ""),
        message=commit_data.get("message", ""),
        author=author_data.get("name"),
        author_email=author_data.get("email"),
        date=author_data.get("date") or commit_data.get("author", {}).get("date"),
    )


def _parse_file_change(data: dict[str, Any]) -> FileChange:
    return FileChange(
        filename=data.get("filename", ""),
        status=data.get("status", ""),
        additions=data.get("additions", 0),
        deletions=data.get("deletions", 0),
    )


def _parse_compare(data: dict[str, Any], base: str, head: str) -> CompareResult:
    files = data.get("files", [])
    return CompareResult(
        base=base,
        head=head,
        commits_ahead=data.get("total_commits", 0),
        commits_behind=data.get("behind_by", 0),
        files_changed=[_parse_file_change(f) for f in files],
        total_additions=data.get("total_additions", sum(f.get("additions", 0) for f in files)),
        total_deletions=data.get("total_deletions", sum(f.get("deletions", 0) for f in files)),
    )


# --------------------------------------------------------------------------- #
# Branch info is returned differently by the API
# --------------------------------------------------------------------------- #

def _parse_branch(data: dict[str, Any]) -> BranchInfo:
    commit = data.get("commit", {})
    return BranchInfo(
        name=data.get("name", ""),
        commit_sha=commit.get("id", ""),
        protected=data.get("protected", False),
    )


def _parse_repo(data: dict[str, Any]) -> RepoDetail:
    return RepoDetail(
        name=data.get("name", ""),
        full_name=data.get("full_name", ""),
        description=data.get("description"),
        default_branch=data.get("default_branch"),
        private=data.get("private", False),
        stars=data.get("stars_count", 0),
        forks=data.get("forks_count", 0),
        open_issues=data.get("open_issues_count", 0),
        html_url=data.get("html_url"),
        clone_url=data.get("clone_url"),
    )


# --------------------------------------------------------------------------- #
# Service class
# --------------------------------------------------------------------------- #

class GiteaService:
    """Service class managing Gitea API operations.

    Holds an ``httpx.Client`` configured with the Gitea API base URL
    and bearer token.  All methods accept optional ``owner`` and ``repo``
    overrides; if omitted, the defaults from config are used.
    """

    def __init__(self, config: GiteaConfig) -> None:
        self._config = config
        self._client: httpx.Client | None = None

    @property
    def config(self) -> GiteaConfig:
        return self._config

    @property
    def _api(self) -> httpx.Client:
        """Lazy-init the httpx client with auth headers."""
        if self._client is None:
            base = self._config.url.rstrip("/") + "/api/v1"
            self._client = httpx.Client(
                base_url=base,
                headers={
                    "Authorization": f"token {self._config.token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    # Low-level request helper
    # ------------------------------------------------------------------ #

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated API request and return parsed JSON.

        Raises :class:`GiteaError` on non-2xx responses.
        """
        try:
            resp = self._api.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise GiteaError(f"Network error: {exc}") from exc

        if resp.status_code == 404:
            raise GiteaError(f"Not found: {method} {path}")
        if resp.status_code == 403:
            raise GiteaError(f"Forbidden (check token permissions): {method} {path}")
        if resp.status_code == 409:
            raise GiteaError(f"Conflict: {method} {path} — {resp.text}")
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("message", resp.text)
            except ValueError:
                pass
            raise GiteaError(f"API error {resp.status_code}: {detail}")

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _repo_path(self, owner: str | None, repo: str | None) -> str:
        """Build the /repos/{owner}/{repo} path segment."""
        o = owner or self._config.default_owner
        r = repo or self._config.default_repo
        if not o or not r:
            raise GiteaError("Owner and repo must be specified or set as defaults in config.")
        return f"/repos/{o}/{r}"

    # ------------------------------------------------------------------ #
    # Issues
    # ------------------------------------------------------------------ #

    def list_issues(
        self,
        *,
        state: str = "open",
        labels: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[IssueDetail]:
        """List issues in the repository."""
        params: dict[str, Any] = {"state": state, "page": page, "limit": limit}
        if labels:
            params["labels"] = labels
        path = f"{self._repo_path(owner, repo)}/issues"
        data = self._request("GET", path, params=params)
        if data is None:
            return []
        return [_parse_issue(d) for d in data]

    def get_issue(
        self,
        index: int,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> IssueDetail | None:
        """Get a single issue by its number."""
        path = f"{self._repo_path(owner, repo)}/issues/{index}"
        try:
            data = self._request("GET", path)
        except GiteaError as exc:
            if "Not found" in str(exc):
                return None
            raise
        if data is None:
            return None
        return _parse_issue(data)

    def create_issue(
        self,
        req: IssueCreate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> IssueDetail:
        """Create a new issue."""
        path = f"{self._repo_path(owner, repo)}/issues"
        body: dict[str, Any] = {"title": req.title}
        if req.body:
            body["body"] = req.body
        if req.labels:
            body["labels"] = req.labels
        if req.assignees:
            body["assignees"] = req.assignees
        if req.milestone is not None:
            body["milestone"] = req.milestone
        data = self._request("POST", path, json_body=body)
        return _parse_issue(data)

    def update_issue(
        self,
        index: int,
        req: IssueUpdate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> IssueDetail:
        """Update an existing issue."""
        path = f"{self._repo_path(owner, repo)}/issues/{index}"
        body: dict[str, Any] = {}
        if req.title is not None:
            body["title"] = req.title
        if req.body is not None:
            body["body"] = req.body
        if req.state is not None:
            body["state"] = req.state
        if req.milestone is not None:
            body["milestone"] = req.milestone
        data = self._request("PATCH", path, json_body=body)
        return _parse_issue(data)

    def create_issue_comment(
        self,
        index: int,
        req: CommentCreate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> CommentDetail:
        """Comment on an issue."""
        path = f"{self._repo_path(owner, repo)}/issues/{index}/comments"
        data = self._request("POST", path, json_body={"body": req.body})
        return _parse_comment(data)

    def list_issue_comments(
        self,
        index: int,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> list[CommentDetail]:
        """List comments on an issue."""
        path = f"{self._repo_path(owner, repo)}/issues/{index}/comments"
        data = self._request("GET", path)
        if data is None:
            return []
        return [_parse_comment(d) for d in data]

    # ------------------------------------------------------------------ #
    # Branches
    # ------------------------------------------------------------------ #

    def list_branches(
        self,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> list[BranchInfo]:
        """List branches in the repository."""
        path = f"{self._repo_path(owner, repo)}/branches"
        data = self._request("GET", path)
        if data is None:
            return []
        return [_parse_branch(d) for d in data]

    def create_branch(
        self,
        req: BranchCreate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> BranchInfo:
        """Create a new branch from an existing ref."""
        path = f"{self._repo_path(owner, repo)}/branches"
        body: dict[str, Any] = {"new_branch_name": req.name}
        if req.from_ref:
            body["old_ref_name"] = req.from_ref
        data = self._request("POST", path, json_body=body)
        return _parse_branch(data)

    def delete_branch(
        self,
        name: str,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> bool:
        """Delete a branch. Returns True if deleted, False if not found."""
        path = f"{self._repo_path(owner, repo)}/branches/{name}"
        try:
            self._request("DELETE", path)
        except GiteaError as exc:
            if "Not found" in str(exc):
                return False
            raise
        return True

    # ------------------------------------------------------------------ #
    # Pull Requests
    # ------------------------------------------------------------------ #

    def list_prs(
        self,
        *,
        state: str = "open",
        owner: str | None = None,
        repo: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[PRDetail]:
        """List pull requests in the repository."""
        params: dict[str, Any] = {"state": state, "page": page, "limit": limit}
        path = f"{self._repo_path(owner, repo)}/pulls"
        data = self._request("GET", path, params=params)
        if data is None:
            return []
        return [_parse_pr(d) for d in data]

    def get_pr(
        self,
        index: int,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> PRDetail | None:
        """Get a single pull request by its number."""
        path = f"{self._repo_path(owner, repo)}/pulls/{index}"
        try:
            data = self._request("GET", path)
        except GiteaError as exc:
            if "Not found" in str(exc):
                return None
            raise
        if data is None:
            return None
        return _parse_pr(data)

    def create_pr(
        self,
        req: PRCreate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> PRDetail:
        """Create a new pull request."""
        path = f"{self._repo_path(owner, repo)}/pulls"
        body: dict[str, Any] = {
            "title": req.title,
            "head": req.head,
            "base": req.base,
        }
        if req.body:
            body["body"] = req.body
        data = self._request("POST", path, json_body=body)
        return _parse_pr(data)

    def update_pr(
        self,
        index: int,
        req: PRUpdate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> PRDetail:
        """Update a pull request."""
        path = f"{self._repo_path(owner, repo)}/pulls/{index}"
        body: dict[str, Any] = {}
        if req.title is not None:
            body["title"] = req.title
        if req.body is not None:
            body["body"] = req.body
        if req.state is not None:
            body["state"] = req.state
        data = self._request("PATCH", path, json_body=body)
        return _parse_pr(data)

    def merge_pr(
        self,
        index: int,
        req: PRMerge,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> bool:
        """Merge a pull request. Returns True on success."""
        path = f"{self._repo_path(owner, repo)}/pulls/{index}/merge"
        body: dict[str, Any] = {"Do": req.do}
        if req.merge_commit_message:
            body["MergeCommitMessage"] = req.merge_commit_message
        try:
            self._request("POST", path, json_body=body)
        except GiteaError as exc:
            if "Conflict" in str(exc):
                raise GiteaError(
                    f"PR #{index} cannot be merged (conflict or already merged): {exc}"
                ) from exc
            raise
        return True

    def list_pr_reviews(
        self,
        index: int,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> list[ReviewDetail]:
        """List reviews on a pull request."""
        path = f"{self._repo_path(owner, repo)}/pulls/{index}/reviews"
        data = self._request("GET", path)
        if data is None:
            return []
        return [_parse_review(d) for d in data]

    def create_pr_comment(
        self,
        index: int,
        req: CommentCreate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> CommentDetail:
        """Comment on a pull request."""
        path = f"{self._repo_path(owner, repo)}/issues/{index}/comments"
        data = self._request("POST", path, json_body={"body": req.body})
        return _parse_comment(data)

    # ------------------------------------------------------------------ #
    # Actions / CI
    # ------------------------------------------------------------------ #

    def list_actions(
        self,
        *,
        owner: str | None = None,
        repo: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[ActionRun]:
        """List CI workflow runs in the repository."""
        path = f"{self._repo_path(owner, repo)}/actions/runs"
        params: dict[str, Any] = {"page": page, "limit": limit}
        try:
            data = self._request("GET", path, params=params)
        except GiteaError as exc:
            # Actions API may not be available on all Gitea versions.
            logger.warning("Actions API not available: %s", exc)
            return []
        if data is None:
            return []
        runs = data if isinstance(data, list) else data.get("workflow_runs", data.get("runs", []))
        return [_parse_action_run(r) for r in runs]

    def get_commit_statuses(
        self,
        sha: str,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> list[CommitStatus]:
        """Get CI status checks for a specific commit."""
        path = f"{self._repo_path(owner, repo)}/commits/{sha}/statuses"
        data = self._request("GET", path)
        if data is None:
            return []
        return [_parse_commit_status(d) for d in data]

    # ------------------------------------------------------------------ #
    # Releases
    # ------------------------------------------------------------------ #

    def list_releases(
        self,
        *,
        owner: str | None = None,
        repo: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[ReleaseDetail]:
        """List releases in the repository."""
        path = f"{self._repo_path(owner, repo)}/releases"
        params: dict[str, Any] = {"page": page, "limit": limit}
        data = self._request("GET", path, params=params)
        if data is None:
            return []
        return [_parse_release(d) for d in data]

    def get_release(
        self,
        release_id: int,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> ReleaseDetail | None:
        """Get a single release by ID."""
        path = f"{self._repo_path(owner, repo)}/releases/{release_id}"
        try:
            data = self._request("GET", path)
        except GiteaError as exc:
            if "Not found" in str(exc):
                return None
            raise
        if data is None:
            return None
        return _parse_release(data)

    def create_release(
        self,
        req: ReleaseCreate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> ReleaseDetail:
        """Create a new release."""
        path = f"{self._repo_path(owner, repo)}/releases"
        body: dict[str, Any] = {
            "tag_name": req.tag_name,
            "draft": req.draft,
            "prerelease": req.prerelease,
        }
        if req.target:
            body["target_commitish"] = req.target
        if req.name:
            body["name"] = req.name
        if req.body:
            body["body"] = req.body
        data = self._request("POST", path, json_body=body)
        return _parse_release(data)

    def update_release(
        self,
        release_id: int,
        req: ReleaseUpdate,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> ReleaseDetail:
        """Update an existing release."""
        path = f"{self._repo_path(owner, repo)}/releases/{release_id}"
        body: dict[str, Any] = {}
        if req.name is not None:
            body["name"] = req.name
        if req.body is not None:
            body["body"] = req.body
        if req.draft is not None:
            body["draft"] = req.draft
        if req.prerelease is not None:
            body["prerelease"] = req.prerelease
        data = self._request("PATCH", path, json_body=body)
        return _parse_release(data)

    def delete_release(
        self,
        release_id: int,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> bool:
        """Delete a release. Returns True if deleted, False if not found."""
        path = f"{self._repo_path(owner, repo)}/releases/{release_id}"
        try:
            self._request("DELETE", path)
        except GiteaError as exc:
            if "Not found" in str(exc):
                return False
            raise
        return True

    # ------------------------------------------------------------------ #
    # Repository info & comparison
    # ------------------------------------------------------------------ #

    def get_repo(
        self,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> RepoDetail:
        """Get repository information."""
        path = self._repo_path(owner, repo)
        data = self._request("GET", path)
        return _parse_repo(data)

    def list_repos(self) -> list[RepoDetail]:
        """List repositories accessible to the token."""
        data = self._request("GET", "/user/repos")
        if data is None:
            return []
        return [_parse_repo(d) for d in data]

    def list_commits(
        self,
        *,
        sha: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[CommitInfo]:
        """List recent commits in the repository."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if sha:
            params["sha"] = sha
        path = f"{self._repo_path(owner, repo)}/commits"
        data = self._request("GET", path, params=params)
        if data is None:
            return []
        return [_parse_commit(d) for d in data]

    def compare(
        self,
        base: str,
        head: str,
        *,
        owner: str | None = None,
        repo: str | None = None,
    ) -> CompareResult:
        """Compare two refs (branches, tags, or SHAs)."""
        path = f"{self._repo_path(owner, repo)}/compare/{base}...{head}"
        try:
            data = self._request("GET", path)
        except GiteaError as exc:
            if "Not found" in str(exc):
                raise GiteaError(f"Comparison not found: {base}...{head}") from exc
            raise
        return _parse_compare(data, base, head)


