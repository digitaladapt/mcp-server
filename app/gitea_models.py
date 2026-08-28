"""Pydantic models for Gitea integration.

Covers issues, pull requests, branches, releases, CI/Actions status,
and repository comparison — all the pieces needed for a complete
development workflow against a Gitea instance.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

class GiteaConfig(BaseModel):
    """Configuration for Gitea API connections."""

    url: str
    token: str
    default_owner: str
    default_repo: str
    search_endpoint: str = "/repos/search"

    @classmethod
    def from_env(cls) -> GiteaConfig | None:
        """Build config from environment variables.

        Returns None if GITEA_URL is not set (Gitea disabled).
        """
        import os

        url = os.environ.get("GITEA_URL", "").strip()
        if not url:
            return None

        token = os.environ.get("GITEA_TOKEN", "")
        owner = os.environ.get("GITEA_DEFAULT_OWNER", "")
        repo = os.environ.get("GITEA_DEFAULT_REPO", "")
        search_endpoint = os.environ.get("GITEA_SEARCH_ENDPOINT", "/repos/search")

        return cls(
            url=url,
            token=token,
            default_owner=owner,
            default_repo=repo,
            search_endpoint=search_endpoint,
        )


# --------------------------------------------------------------------------- #
# Issues
# --------------------------------------------------------------------------- #

class IssueLabel(BaseModel):
    """A label on an issue or PR."""
    id: int
    name: str
    color: str


class IssueUser(BaseModel):
    """A user referenced in an issue."""
    id: int
    login: str
    full_name: str | None = None
    avatar_url: str | None = None


class IssueMilestone(BaseModel):
    """A milestone attached to an issue."""
    id: int
    title: str
    description: str | None = None


class IssueDetail(BaseModel):
    """A single Gitea issue with full details."""
    number: int
    title: str
    body: str | None = None
    state: str  # open, closed
    labels: list[IssueLabel] = []
    assignees: list[IssueUser] = []
    milestone: IssueMilestone | None = None
    author: IssueUser | None = None
    created_at: str
    updated_at: str
    closed_at: str | None = None
    comments: int = 0
    html_url: str | None = None


class IssueCreate(BaseModel):
    """Payload for creating a new issue."""
    title: str
    body: str | None = None
    labels: list[int] = []  # label IDs
    assignees: list[str] = []  # usernames
    milestone: int | None = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be empty")
        return v


class IssueUpdate(BaseModel):
    """Payload for updating an issue. All fields optional."""
    title: str | None = None
    body: str | None = None
    state: str | None = None  # open, closed
    milestone: int | None = None

    @field_validator("state")
    @classmethod
    def state_valid(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"open", "closed"}
            if v.lower() not in allowed:
                raise ValueError(f"state must be one of {allowed}")
            return v.lower()
        return v


class CommentCreate(BaseModel):
    """Payload for commenting on an issue or PR."""
    body: str

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body must not be empty")
        return v


class CommentDetail(BaseModel):
    """A comment on an issue or PR."""
    id: int
    body: str
    author: IssueUser | None = None
    created_at: str
    updated_at: str | None = None


# --------------------------------------------------------------------------- #
# Branches
# --------------------------------------------------------------------------- #

class BranchInfo(BaseModel):
    """Information about a single branch."""
    name: str
    commit_sha: str
    protected: bool = False


class BranchCreate(BaseModel):
    """Payload for creating a new branch."""
    name: str
    from_ref: str = ""  # ref to branch from (default: repo's default branch)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("branch name must not be empty")
        return v


# --------------------------------------------------------------------------- #
# Pull Requests
# --------------------------------------------------------------------------- #

class PRDetail(BaseModel):
    """A single pull request with full details."""
    number: int
    title: str
    body: str | None = None
    state: str  # open, closed
    merged: bool = False
    mergeable: bool | None = None
    merge_commit_sha: str | None = None
    head_branch: str
    head_repo: str | None = None
    base_branch: str
    base_repo: str | None = None
    labels: list[IssueLabel] = []
    assignees: list[IssueUser] = []
    milestone: IssueMilestone | None = None
    author: IssueUser | None = None
    created_at: str
    updated_at: str
    closed_at: str | None = None
    merged_at: str | None = None
    comments: int = 0
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    html_url: str | None = None


class PRCreate(BaseModel):
    """Payload for creating a pull request."""
    title: str
    body: str | None = None
    head: str  # source branch
    base: str  # target branch

    @field_validator("title", "head", "base")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v


class PRUpdate(BaseModel):
    """Payload for updating a pull request. All fields optional."""
    title: str | None = None
    body: str | None = None
    state: str | None = None  # open, closed

    @field_validator("state")
    @classmethod
    def state_valid(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"open", "closed"}
            if v.lower() not in allowed:
                raise ValueError(f"state must be one of {allowed}")
            return v.lower()
        return v


class PRMerge(BaseModel):
    """Payload for merging a pull request."""
    do: str = "merge"  # merge, squash, rebase, rebase-merge
    merge_commit_message: str | None = None

    @field_validator("do")
    @classmethod
    def do_valid(cls, v: str) -> str:
        allowed = {"merge", "squash", "rebase", "rebase-merge"}
        if v.lower() not in allowed:
            raise ValueError(f"do must be one of {allowed}")
        return v.lower()


class ReviewDetail(BaseModel):
    """A review on a pull request."""
    id: int
    user: IssueUser | None = None
    body: str | None = None
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, PENDING, DISMISSED
    submitted_at: str | None = None


# --------------------------------------------------------------------------- #
# Actions / CI
# --------------------------------------------------------------------------- #

class ActionRun(BaseModel):
    """A single CI/Actions workflow run."""
    id: int
    name: str | None = None
    status: str  # success, failure, running, cancelled, waiting, blocked
    conclusion: str | None = None  # success, failure, neutral, cancelled, skipped
    branch: str | None = None
    commit_sha: str | None = None
    event: str | None = None  # push, pull_request, schedule
    created_at: str | None = None
    updated_at: str | None = None
    html_url: str | None = None


class CommitStatus(BaseModel):
    """A commit status check (CI result)."""
    state: str  # pending, success, error, failure
    context: str | None = None
    description: str | None = None
    target_url: str | None = None
    created_at: str | None = None


# --------------------------------------------------------------------------- #
# Releases
# --------------------------------------------------------------------------- #

class ReleaseDetail(BaseModel):
    """A single release."""
    id: int
    tag_name: str
    target: str | None = None  # commitish the tag points to
    name: str | None = None
    body: str | None = None
    draft: bool = False
    prerelease: bool = False
    author: IssueUser | None = None
    created_at: str
    published_at: str | None = None
    html_url: str | None = None


class ReleaseCreate(BaseModel):
    """Payload for creating a release."""
    tag_name: str
    target: str | None = None  # commitish (branch/SHA), defaults to default branch
    name: str | None = None
    body: str | None = None
    draft: bool = False
    prerelease: bool = False

    @field_validator("tag_name")
    @classmethod
    def tag_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("tag_name must not be empty")
        return v


class ReleaseUpdate(BaseModel):
    """Payload for updating a release. All fields optional."""
    name: str | None = None
    body: str | None = None
    draft: bool | None = None
    prerelease: bool | None = None


# --------------------------------------------------------------------------- #
# Repository & Commits
# --------------------------------------------------------------------------- #

class RepoDetail(BaseModel):
    """Information about a repository."""
    name: str
    full_name: str
    description: str | None = None
    default_branch: str | None = None
    private: bool = False
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    html_url: str | None = None
    clone_url: str | None = None


class CommitInfo(BaseModel):
    """A single commit."""
    sha: str
    message: str
    author: str | None = None
    author_email: str | None = None
    date: str | None = None


class CompareResult(BaseModel):
    """Result of comparing two refs."""
    base: str
    head: str
    commits_ahead: int
    commits_behind: int
    files_changed: list[FileChange] = []
    total_additions: int = 0
    total_deletions: int = 0


class FileChange(BaseModel):
    """A changed file in a comparison."""
    filename: str
    status: str  # added, modified, removed, renamed
    additions: int = 0
    deletions: int = 0


# --------------------------------------------------------------------------- #
# Response wrappers
# --------------------------------------------------------------------------- #

class IssueListResponse(BaseModel):
    """Response for listing issues."""
    issues: list[IssueDetail]
    total: int


class PRListResponse(BaseModel):
    """Response for listing pull requests."""
    pulls: list[PRDetail]
    total: int


class BranchListResponse(BaseModel):
    """Response for listing branches."""
    branches: list[BranchInfo]
    total: int


class ReleaseListResponse(BaseModel):
    """Response for listing releases."""
    releases: list[ReleaseDetail]
    total: int


class ActionRunListResponse(BaseModel):
    """Response for listing CI runs."""
    runs: list[ActionRun]
    total: int


class CommitStatusListResponse(BaseModel):
    """Response for listing commit statuses."""
    statuses: list[CommitStatus]
    total: int


class CommentListResponse(BaseModel):
    """Response for listing comments."""
    comments: list[CommentDetail]
    total: int


class ReviewListResponse(BaseModel):
    """Response for listing reviews."""
    reviews: list[ReviewDetail]
    total: int


class RepoListResponse(BaseModel):
    """Response for listing repos."""
    repos: list[RepoDetail]
    total: int
    page: int = 1
    page_count: int = 1
    limit: int = 50


class CommitListResponse(BaseModel):
    """Response for listing commits."""
    commits: list[CommitInfo]
    total: int


class DeleteResponse(BaseModel):
    """Response for delete operations."""
    deleted: bool
    resource: str  # e.g. "branch", "release"
    identifier: str  # branch name, release id, etc.


class MergeResponse(BaseModel):
    """Response for merge operations."""
    merged: bool
    pr_number: int
    merge_commit_sha: str | None = None
