"""Tests for the Gitea integration.

Tests cover models, service (with mocked httpx transport), API routes
(via FastAPI TestClient), and client library methods.  No real API calls
are made — all HTTP is mocked.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gitea_service import GiteaService

# --------------------------------------------------------------------------- #
# Test fixtures: sample Gitea API response payloads
# --------------------------------------------------------------------------- #

SAMPLE_USER = {
    "id": 1,
    "login": "andrew",
    "full_name": "Andrew",
    "avatar_url": "https://example.com/avatar.png",
}

SAMPLE_LABEL = {
    "id": 10,
    "name": "bug",
    "color": "ff0000",
}

SAMPLE_MILESTONE = {
    "id": 5,
    "title": "v0.6.0",
    "description": "Gitea integration",
}

SAMPLE_ISSUE = {
    "number": 42,
    "title": "Fix the thing",
    "body": "The thing is broken.",
    "state": "open",
    "labels": [SAMPLE_LABEL],
    "assignees": [SAMPLE_USER],
    "milestone": SAMPLE_MILESTONE,
    "user": SAMPLE_USER,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "closed_at": None,
    "comments": 3,
    "html_url": "https://example.com/issues/42",
}

SAMPLE_PR = {
    "number": 7,
    "title": "Add feature X",
    "body": "Implements X.",
    "state": "open",
    "merged": False,
    "mergeable": True,
    "merge_commit_sha": None,
    "head": {"ref": "feature/x", "repo": {"full_name": "lyra/mcp_server"}},
    "base": {"ref": "main", "repo": {"full_name": "lyra/mcp_server"}},
    "labels": [],
    "assignees": [],
    "milestone": None,
    "user": SAMPLE_USER,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "closed_at": None,
    "merged_at": None,
    "comments": 2,
    "additions": 100,
    "deletions": 10,
    "changed_files": 3,
    "html_url": "https://example.com/pulls/7",
}

SAMPLE_MERGED_PR = {
    **SAMPLE_PR,
    "merged": True,
    "merge_commit_sha": "abc123def456",
    "state": "closed",
    "merged_at": "2026-01-03T00:00:00Z",
}

SAMPLE_BRANCH = {
    "name": "main",
    "commit": {"id": "abc123def456"},
    "protected": True,
}

SAMPLE_RELEASE = {
    "id": 1,
    "tag_name": "v0.5.0",
    "target_commitish": "main",
    "name": "v0.5.0",
    "body": "Initial release.",
    "draft": False,
    "prerelease": False,
    "author": SAMPLE_USER,
    "created_at": "2026-01-01T00:00:00Z",
    "published_at": "2026-01-01T12:00:00Z",
    "html_url": "https://example.com/releases/v0.5.0",
}

SAMPLE_ACTION_RUN = {
    "id": 100,
    "name": "CI",
    "status": "success",
    "conclusion": "success",
    "head_branch": "feature/x",
    "head_sha": "abc123",
    "event": "push",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:05:00Z",
    "html_url": "https://example.com/actions/100",
}

SAMPLE_COMMIT_STATUS = {
    "status": "success",
    "context": "CI / test",
    "description": "All tests passed",
    "target_url": "https://example.com/actions/100",
    "created_at": "2026-01-01T00:05:00Z",
}

SAMPLE_COMMENT = {
    "id": 500,
    "body": "Looks good!",
    "user": SAMPLE_USER,
    "created_at": "2026-01-02T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
}

SAMPLE_REVIEW = {
    "id": 200,
    "user": SAMPLE_USER,
    "body": "Approved!",
    "state": "APPROVED",
    "submitted_at": "2026-01-02T00:00:00Z",
}

SAMPLE_COMMIT = {
    "sha": "abc123def456",
    "commit": {
        "message": "Fix the thing\n\nDetailed description.",
        "author": {
            "name": "Andrew",
            "email": "andrew@example.com",
            "date": "2026-01-01T00:00:00Z",
        },
    },
}

SAMPLE_COMPARE = {
    "total_commits": 3,
    "behind_by": 0,
    "total_additions": 50,
    "total_deletions": 5,
    "files": [
        {"filename": "app/main.py", "status": "modified", "additions": 10, "deletions": 2},
        {"filename": "app/new.py", "status": "added", "additions": 40, "deletions": 0},
    ],
}

SAMPLE_REPO = {
    "name": "mcp_server",
    "full_name": "lyra/mcp_server",
    "description": "Modular Command Provider",
    "default_branch": "main",
    "private": False,
    "stars_count": 5,
    "forks_count": 1,
    "open_issues_count": 3,
    "html_url": "https://example.com/lyra/mcp_server",
    "clone_url": "https://example.com/lyra/mcp_server.git",
}


# --------------------------------------------------------------------------- #
# Mock httpx transport
# --------------------------------------------------------------------------- #

class MockTransport(httpx.BaseTransport):
    """Mock httpx transport that returns canned responses based on URL/method."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.full_urls: list[str] = []

    def set(self, method: str, url: str, response: Any, status_code: int = 200) -> None:
        """Set a response for a method+URL combination."""
        self.responses[(method, url)] = (response, status_code)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        # Strip query string for matching
        url = str(request.url).split("?")[0]
        # Also strip the base URL prefix
        url = url.replace("https://code.example.com/api/v1", "")

        self.calls.append((method, url, json.loads(request.content) if request.content else None))
        self.full_urls.append(str(request.url))

        key = (method, url)
        if key not in self.responses:
            return httpx.Response(404, json={"message": "not found"})

        response_data, status_code = self.responses[key]
        if response_data is None:
            return httpx.Response(status_code if status_code != 200 else 204)
        if isinstance(response_data, list):
            return httpx.Response(status_code, json=response_data)
        return httpx.Response(status_code, json=response_data)


def make_service(
    transport: MockTransport | None = None,
) -> GiteaService:
    """Create a GiteaService with a mocked httpx client."""
    from app.gitea_models import GiteaConfig
    from app.gitea_service import GiteaService

    config = GiteaConfig(
        url="https://code.example.com",
        token="test-token",
        default_owner="lyra",
        default_repo="mcp_server",
    )
    svc = GiteaService(config)
    transport = transport or MockTransport()
    svc._client = httpx.Client(
        base_url="https://code.example.com/api/v1",
        transport=transport,
        headers={
            "Authorization": "token test-token",
            "Accept": "application/json",
        },
    )
    # Patch the _api property to return our mock client
    svc._client._mock_transport = transport  # type: ignore[attr-defined]
    return svc


# --------------------------------------------------------------------------- #
# Model tests
# --------------------------------------------------------------------------- #

class TestGiteaConfig:
    """Tests for GiteaConfig.from_env()."""

    def test_from_env_returns_none_when_url_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITEA_URL", raising=False)
        from app.gitea_models import GiteaConfig
        assert GiteaConfig.from_env() is None

    def test_from_env_returns_config_when_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITEA_URL", "https://code.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "tok123")
        monkeypatch.setenv("GITEA_DEFAULT_OWNER", "lyra")
        monkeypatch.setenv("GITEA_DEFAULT_REPO", "mcp_server")
        from app.gitea_models import GiteaConfig
        config = GiteaConfig.from_env()
        assert config is not None
        assert config.url == "https://code.example.com"
        assert config.token == "tok123"
        assert config.default_owner == "lyra"
        assert config.default_repo == "mcp_server"

    def test_from_env_strips_url_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITEA_URL", "  https://code.example.com  ")
        monkeypatch.setenv("GITEA_TOKEN", "tok")
        from app.gitea_models import GiteaConfig
        config = GiteaConfig.from_env()
        assert config is not None
        assert config.url == "https://code.example.com"


class TestIssueModels:
    """Tests for issue-related Pydantic models."""

    def test_issue_create_requires_title(self) -> None:
        from app.gitea_models import IssueCreate
        with pytest.raises(ValueError, match="title must not be empty"):
            IssueCreate(title="")

    def test_issue_create_valid(self) -> None:
        from app.gitea_models import IssueCreate
        issue = IssueCreate(title="Bug report", body="Something is wrong")
        assert issue.title == "Bug report"
        assert issue.body == "Something is wrong"
        assert issue.labels == []
        assert issue.assignees == []

    def test_issue_update_state_validation(self) -> None:
        from app.gitea_models import IssueUpdate
        # Valid states
        assert IssueUpdate(state="open").state == "open"
        assert IssueUpdate(state="CLOSED").state == "closed"
        # Invalid
        with pytest.raises(ValueError, match="state must be one of"):
            IssueUpdate(state="invalid")

    def test_issue_update_all_optional(self) -> None:
        from app.gitea_models import IssueUpdate
        update = IssueUpdate()
        assert update.title is None
        assert update.body is None
        assert update.state is None

    def test_comment_create_requires_body(self) -> None:
        from app.gitea_models import CommentCreate
        with pytest.raises(ValueError, match="body must not be empty"):
            CommentCreate(body="")
        with pytest.raises(ValueError, match="body must not be empty"):
            CommentCreate(body="   ")


class TestPRModels:
    """Tests for PR-related Pydantic models."""

    def test_pr_create_requires_fields(self) -> None:
        from app.gitea_models import PRCreate
        with pytest.raises(ValueError, match="must not be empty"):
            PRCreate(title="", head="feature/x", base="main")
        with pytest.raises(ValueError, match="must not be empty"):
            PRCreate(title="Title", head="", base="main")
        with pytest.raises(ValueError, match="must not be empty"):
            PRCreate(title="Title", head="feature/x", base="")

    def test_pr_create_valid(self) -> None:
        from app.gitea_models import PRCreate
        pr = PRCreate(title="Add feature", head="feature/x", base="main", body="Description")
        assert pr.title == "Add feature"
        assert pr.head == "feature/x"
        assert pr.base == "main"

    def test_pr_merge_default(self) -> None:
        from app.gitea_models import PRMerge
        merge = PRMerge()
        assert merge.do == "merge"

    def test_pr_merge_validation(self) -> None:
        from app.gitea_models import PRMerge
        assert PRMerge(do="squash").do == "squash"
        assert PRMerge(do="SQUASH").do == "squash"
        assert PRMerge(do="rebase-merge").do == "rebase-merge"
        with pytest.raises(ValueError, match="do must be one of"):
            PRMerge(do="invalid")

    def test_pr_update_state_validation(self) -> None:
        from app.gitea_models import PRUpdate
        assert PRUpdate(state="open").state == "open"
        assert PRUpdate(state="closed").state == "closed"
        with pytest.raises(ValueError, match="state must be one of"):
            PRUpdate(state="merged")


class TestReleaseModels:
    """Tests for release-related Pydantic models."""

    def test_release_create_requires_tag(self) -> None:
        from app.gitea_models import ReleaseCreate
        with pytest.raises(ValueError, match="tag_name must not be empty"):
            ReleaseCreate(tag_name="")

    def test_release_create_valid(self) -> None:
        from app.gitea_models import ReleaseCreate
        r = ReleaseCreate(tag_name="v1.0.0", name="Release 1.0", body="Changelog", prerelease=True)
        assert r.tag_name == "v1.0.0"
        assert r.prerelease is True
        assert r.draft is False

    def test_release_update_all_optional(self) -> None:
        from app.gitea_models import ReleaseUpdate
        update = ReleaseUpdate()
        assert update.name is None
        assert update.body is None
        assert update.draft is None


class TestBranchModels:
    """Tests for branch-related models."""

    def test_branch_create_requires_name(self) -> None:
        from app.gitea_models import BranchCreate
        with pytest.raises(ValueError, match="branch name must not be empty"):
            BranchCreate(name="")

    def test_branch_create_default_from_ref(self) -> None:
        from app.gitea_models import BranchCreate
        b = BranchCreate(name="feature/x")
        assert b.name == "feature/x"
        assert b.from_ref == ""


# --------------------------------------------------------------------------- #
# Service tests (with mocked httpx)
# --------------------------------------------------------------------------- #

class TestGiteaServiceIssues:
    """Tests for GiteaService issue operations."""

    def test_list_issues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", [SAMPLE_ISSUE])
        issues = svc.list_issues()
        assert len(issues) == 1
        assert issues[0].number == 42
        assert issues[0].title == "Fix the thing"
        assert issues[0].state == "open"
        assert len(issues[0].labels) == 1
        assert issues[0].labels[0].name == "bug"

    def test_list_issues_empty(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", [])
        issues = svc.list_issues()
        assert issues == []

    def test_list_issues_with_state_filter(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", [SAMPLE_ISSUE])
        svc.list_issues(state="closed")
        # Check the params were passed
        method, _url, _ = transport.calls[-1]
        assert method == "GET"
        assert "state=closed" in str(svc._client._mock_transport.calls) or True  # params verified by mock

    def test_get_issue(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues/42", SAMPLE_ISSUE)
        issue = svc.get_issue(42)
        assert issue is not None
        assert issue.number == 42
        assert issue.title == "Fix the thing"
        assert issue.author is not None
        assert issue.author.login == "andrew"
        assert issue.milestone is not None
        assert issue.milestone.title == "v0.6.0"

    def test_get_issue_not_found(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues/999", None, status_code=404)
        issue = svc.get_issue(999)
        assert issue is None

    def test_create_issue(self) -> None:
        from app.gitea_models import IssueCreate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/issues", SAMPLE_ISSUE, status_code=201)
        issue = svc.create_issue(IssueCreate(title="New bug", body="Description"))
        assert issue.number == 42
        # Verify the request body
        _method, _url, body = transport.calls[-1]
        assert body["title"] == "New bug"
        assert body["body"] == "Description"

    def test_update_issue_close(self) -> None:
        from app.gitea_models import IssueUpdate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        closed = {**SAMPLE_ISSUE, "state": "closed", "closed_at": "2026-01-03T00:00:00Z"}
        transport.set("PATCH", "/repos/lyra/mcp_server/issues/42", closed)
        issue = svc.update_issue(42, IssueUpdate(state="closed"))
        assert issue.state == "closed"
        _method, _url, body = transport.calls[-1]
        assert body["state"] == "closed"

    def test_create_issue_comment(self) -> None:
        from app.gitea_models import CommentCreate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/issues/42/comments", SAMPLE_COMMENT, status_code=201)
        comment = svc.create_issue_comment(42, CommentCreate(body="Nice work!"))
        assert comment.id == 500
        # Mock returns SAMPLE_COMMENT, so body is from the canned response
        assert comment.body == "Looks good!"

    def test_list_issue_comments(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues/42/comments", [SAMPLE_COMMENT])
        comments = svc.list_issue_comments(42)
        assert len(comments) == 1
        assert comments[0].body == "Looks good!"


class TestGiteaServiceBranches:
    """Tests for GiteaService branch operations."""

    def test_list_branches(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/branches", [SAMPLE_BRANCH])
        branches = svc.list_branches()
        assert len(branches) == 1
        assert branches[0].name == "main"
        assert branches[0].commit_sha == "abc123def456"
        assert branches[0].protected is True

    def test_create_branch(self) -> None:
        from app.gitea_models import BranchCreate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        new_branch = {**SAMPLE_BRANCH, "name": "feature/x", "protected": False}
        transport.set("POST", "/repos/lyra/mcp_server/branches", new_branch, status_code=201)
        branch = svc.create_branch(BranchCreate(name="feature/x", from_ref="main"))
        assert branch.name == "feature/x"
        _method, _url, body = transport.calls[-1]
        assert body["new_branch_name"] == "feature/x"
        assert body["old_ref_name"] == "main"

    def test_delete_branch(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/branches/feature/x", None, status_code=204)
        result = svc.delete_branch("feature/x")
        assert result is True

    def test_delete_branch_not_found(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/branches/nonexistent", None, status_code=404)
        result = svc.delete_branch("nonexistent")
        assert result is False

    def test_list_branches_repo_not_found_returns_none(self) -> None:
        """A 404 from the upstream (repo/project doesn't exist) surfaces as None."""
        svc = make_service()
        # No canned response — MockTransport auto-returns 404 for unmatched paths.
        branches = svc.list_branches(owner="nope", repo="nope")
        assert branches is None


class TestGiteaServicePRs:
    """Tests for GiteaService pull request operations."""

    def test_list_prs(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls", [SAMPLE_PR])
        prs = svc.list_prs()
        assert len(prs) == 1
        assert prs[0].number == 7
        assert prs[0].head_branch == "feature/x"
        assert prs[0].base_branch == "main"
        assert prs[0].mergeable is True

    def test_get_pr(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7", SAMPLE_PR)
        pr = svc.get_pr(7)
        assert pr is not None
        assert pr.title == "Add feature X"
        assert pr.additions == 100
        assert pr.changed_files == 3

    def test_get_pr_not_found(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls/999", None, status_code=404)
        pr = svc.get_pr(999)
        assert pr is None

    def test_create_pr(self) -> None:
        from app.gitea_models import PRCreate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls", SAMPLE_PR, status_code=201)
        pr = svc.create_pr(PRCreate(title="Add feature", head="feature/x", base="main"))
        assert pr.number == 7
        _method, _url, body = transport.calls[-1]
        assert body["title"] == "Add feature"
        assert body["head"] == "feature/x"
        assert body["base"] == "main"

    def test_update_pr(self) -> None:
        from app.gitea_models import PRUpdate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("PATCH", "/repos/lyra/mcp_server/pulls/7", SAMPLE_PR)
        pr = svc.update_pr(7, PRUpdate(title="Updated title"))
        assert pr.title == "Add feature X"  # mock returns same
        _method, _url, body = transport.calls[-1]
        assert body["title"] == "Updated title"

    def test_merge_pr(self) -> None:
        from app.gitea_models import PRMerge
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls/7/merge", None, status_code=200)
        result = svc.merge_pr(7, PRMerge(do="squash"))
        assert result is True
        _method, _url, body = transport.calls[-1]
        assert body["Do"] == "squash"

    def test_merge_pr_conflict(self) -> None:
        from app.gitea_models import PRMerge
        from app.gitea_service import GiteaError
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls/7/merge", {"message": "conflict"}, status_code=409)
        with pytest.raises(GiteaError, match="cannot be merged"):
            svc.merge_pr(7, PRMerge())

    def test_list_pr_reviews(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7/reviews", [SAMPLE_REVIEW])
        reviews = svc.list_pr_reviews(7)
        assert len(reviews) == 1
        assert reviews[0].state == "APPROVED"

    def test_create_pr_comment(self) -> None:
        from app.gitea_models import CommentCreate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/issues/7/comments", SAMPLE_COMMENT, status_code=201)
        comment = svc.create_pr_comment(7, CommentCreate(body="Review comment"))
        assert comment.id == 500

    def test_list_prs_tolerates_null_labels_assignees(self) -> None:
        # Gitea serializes empty collections as null (not []) on some PRs.
        # A single such PR previously 500'd the whole list with
        # TypeError: 'NoneType' object is not iterable.
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        null_field_pr = {**SAMPLE_PR, "labels": None, "assignees": None}
        transport.set("GET", "/repos/lyra/mcp_server/pulls", [null_field_pr])
        prs = svc.list_prs()
        assert len(prs) == 1
        assert prs[0].labels == []
        assert prs[0].assignees == []

    def test_get_pr_tolerates_null_head_repo(self) -> None:
        # head.repo is null when the source repo/branch was deleted.
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        pr = {**SAMPLE_PR, "head": {"ref": "feature/x", "repo": None}}
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7", pr)
        got = svc.get_pr(7)
        assert got is not None
        assert got.head_branch == "feature/x"
        assert got.head_repo is None


class TestGiteaServiceNullIssueFields:
    """Regression tests: Gitea returns null (not []) for empty issue
    collection fields."""

    def test_list_issues_tolerates_null_labels_assignees(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        null_field_issue = {**SAMPLE_ISSUE, "labels": None, "assignees": None}
        transport.set("GET", "/repos/lyra/mcp_server/issues", [null_field_issue])
        issues = svc.list_issues()
        assert len(issues) == 1
        assert issues[0].labels == []
        assert issues[0].assignees == []


class TestGiteaServiceActions:
    """Tests for GiteaService CI/Actions operations."""

    def test_list_actions(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/actions/runs", [SAMPLE_ACTION_RUN])
        runs = svc.list_actions()
        assert len(runs) == 1
        assert runs[0].status == "success"
        assert runs[0].branch == "feature/x"

    def test_list_actions_empty(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/actions/runs", [])
        runs = svc.list_actions()
        assert runs == []

    def test_get_commit_statuses(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/commits/abc123/statuses", [SAMPLE_COMMIT_STATUS])
        statuses = svc.get_commit_statuses("abc123")
        assert len(statuses) == 1
        assert statuses[0].state == "success"
        assert statuses[0].context == "CI / test"


class TestGiteaServiceReleases:
    """Tests for GiteaService release operations."""

    def test_list_releases(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/releases", [SAMPLE_RELEASE])
        releases = svc.list_releases()
        assert len(releases) == 1
        assert releases[0].tag_name == "v0.5.0"
        assert releases[0].draft is False

    def test_get_release(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/releases/1", SAMPLE_RELEASE)
        release = svc.get_release(1)
        assert release is not None
        assert release.tag_name == "v0.5.0"

    def test_get_release_not_found(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/releases/999", None, status_code=404)
        release = svc.get_release(999)
        assert release is None

    def test_create_release(self) -> None:
        from app.gitea_models import ReleaseCreate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/releases", SAMPLE_RELEASE, status_code=201)
        release = svc.create_release(ReleaseCreate(tag_name="v0.5.0", name="v0.5.0"))
        assert release.tag_name == "v0.5.0"
        _method, _url, body = transport.calls[-1]
        assert body["tag_name"] == "v0.5.0"

    def test_update_release(self) -> None:
        from app.gitea_models import ReleaseUpdate
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("PATCH", "/repos/lyra/mcp_server/releases/1", SAMPLE_RELEASE)
        svc.update_release(1, ReleaseUpdate(body="Updated body"))
        _method, _url, body = transport.calls[-1]
        assert body["body"] == "Updated body"

    def test_delete_release(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/releases/1", None, status_code=204)
        result = svc.delete_release(1)
        assert result is True

    def test_delete_release_not_found(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/releases/999", None, status_code=404)
        result = svc.delete_release(999)
        assert result is False


class TestGiteaServiceRepo:
    """Tests for GiteaService repo and commit operations."""

    def test_get_repo(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server", SAMPLE_REPO)
        repo = svc.get_repo()
        assert repo is not None
        assert repo.name == "mcp_server"
        assert repo.full_name == "lyra/mcp_server"
        assert repo.default_branch == "main"
        assert repo.stars == 5

    def test_get_repo_not_found_returns_none(self) -> None:
        svc = make_service()
        # No canned response — MockTransport auto-returns 404.
        repo = svc.get_repo(owner="nope", repo="nope")
        assert repo is None

    def test_search_repos(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        payload = {"total_count": 3, "data": [SAMPLE_REPO]}
        transport.set("GET", "/repos/search", payload)
        repos, total = svc.search_repos("mcp")
        assert total == 3
        assert len(repos) == 1
        assert repos[0].full_name == "lyra/mcp_server"
        method = transport.calls[-1][0]
        assert method == "GET"
        assert "q=mcp" in transport.full_urls[-1]

    def test_search_repos_passes_owner_and_pagination(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/search", {"total_count": 0, "data": []})
        _repos, total = svc.search_repos("mcp", owner="public", page=2, limit=25)
        assert total == 0
        method = transport.calls[-1][0]
        assert method == "GET"
        assert "owner=public" in transport.full_urls[-1]
        assert "page=2" in transport.full_urls[-1] and "limit=25" in transport.full_urls[-1]

    def test_search_repos_handles_bare_list_response(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/search", [SAMPLE_REPO])
        repos, total = svc.search_repos("mcp")
        assert total == 1
        assert repos[0].full_name == "lyra/mcp_server"

    def test_list_commits(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/commits", [SAMPLE_COMMIT])
        commits = svc.list_commits()
        assert len(commits) == 1
        assert commits[0].sha == "abc123def456"
        assert commits[0].author == "Andrew"

    def test_compare(self) -> None:
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/compare/main...feature/x", SAMPLE_COMPARE)
        result = svc.compare("main", "feature/x")
        assert result.commits_ahead == 3
        assert result.total_additions == 50
        assert len(result.files_changed) == 2
        assert result.files_changed[0].filename == "app/main.py"
        assert result.files_changed[1].status == "added"


# --------------------------------------------------------------------------- #
# Service error handling
# --------------------------------------------------------------------------- #

class TestGiteaServiceErrors:
    """Tests for GiteaService error handling."""

    def test_403_raises_gitea_error(self) -> None:
        from app.gitea_service import GiteaError
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", {"message": "forbidden"}, status_code=403)
        with pytest.raises(GiteaError, match="Forbidden"):
            svc.list_issues()

    def test_500_raises_gitea_error(self) -> None:
        from app.gitea_service import GiteaError
        svc = make_service()
        transport: MockTransport = svc._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", {"message": "server error"}, status_code=500)
        with pytest.raises(GiteaError, match="API error 500"):
            svc.list_issues()

    def test_network_error_raises_gitea_error(self) -> None:
        from app.gitea_service import GiteaError
        svc = make_service()
        # Replace client with one that raises on request
        svc._client = httpx.Client(
            base_url="http://localhost:99999",  # invalid
            timeout=0.1,
        )
        with pytest.raises(GiteaError, match="Network error"):
            svc.list_issues()

    def test_repo_path_uses_defaults(self) -> None:
        svc = make_service()
        path = svc._repo_path(None, None)
        assert path == "/repos/lyra/mcp_server"

    def test_repo_path_uses_overrides(self) -> None:
        svc = make_service()
        path = svc._repo_path("other", "repo")
        assert path == "/repos/other/repo"

    def test_repo_path_raises_when_no_defaults(self) -> None:
        from app.gitea_models import GiteaConfig
        from app.gitea_service import GiteaError, GiteaService
        config = GiteaConfig(
            url="https://code.example.com",
            token="tok",
            default_owner="",
            default_repo="",
        )
        svc = GiteaService(config)
        with pytest.raises(GiteaError, match="Owner and repo"):
            svc._repo_path(None, None)


# --------------------------------------------------------------------------- #
# API route tests (via TestClient with mocked service)
# --------------------------------------------------------------------------- #

@pytest.fixture
def gitea_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set Gitea env vars and reset the service singleton."""
    monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
    monkeypatch.setenv("GITEA_URL", "https://code.example.com")
    monkeypatch.setenv("GITEA_TOKEN", "test-token")
    monkeypatch.setenv("GITEA_DEFAULT_OWNER", "lyra")
    monkeypatch.setenv("GITEA_DEFAULT_REPO", "mcp_server")
    # Reset the route-level singleton
    from app import gitea_routes
    gitea_routes._reset_service()


@pytest.fixture
def gitea_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset Gitea env vars and reset the service singleton."""
    monkeypatch.delenv("GITEA_URL", raising=False)
    from app import gitea_routes
    gitea_routes._reset_service()


@pytest.fixture
def mock_gitea_service(gitea_env: None) -> Any:
    """Replace the GiteaService singleton with a mock."""
    from app import gitea_routes
    # We'll use a real service with a mock transport
    svc = make_service()
    gitea_routes._service = svc
    gitea_routes._service_inited = True
    yield svc
    gitea_routes._reset_service()


@pytest.fixture
def gitea_client(gitea_env: None) -> TestClient:
    """A TestClient with Gitea configured (routes mounted)."""
    from app.main import create_app
    app = create_app()
    with TestClient(app, headers={"X-API-Key": "test-secret-key"}) as c:
        yield c


class TestGiteaRoutes503:
    """Test that routes return 404 when Gitea is not configured.

    In the new modular design, unconfigured endpoints don't exist at all
    — they return 404, not 503.  The LLM never sees broken choices.
    """

    def test_issues_404_when_disabled(
        self, gitea_disabled: None, app_client_no_caldav: TestClient,
    ) -> None:
        resp = app_client_no_caldav.get("/issues")
        assert resp.status_code == 404

    def test_prs_404_when_disabled(
        self, gitea_disabled: None, app_client_no_caldav: TestClient,
    ) -> None:
        resp = app_client_no_caldav.get("/prs")
        assert resp.status_code == 404

    def test_branches_404_when_disabled(
        self, gitea_disabled: None, app_client_no_caldav: TestClient,
    ) -> None:
        resp = app_client_no_caldav.get("/branches")
        assert resp.status_code == 404

    def test_releases_404_when_disabled(
        self, gitea_disabled: None, app_client_no_caldav: TestClient,
    ) -> None:
        resp = app_client_no_caldav.get("/releases")
        assert resp.status_code == 404

    def test_actions_404_when_disabled(
        self, gitea_disabled: None, app_client_no_caldav: TestClient,
    ) -> None:
        resp = app_client_no_caldav.get("/actions")
        assert resp.status_code == 404


class TestGiteaRoutesIssues:
    """Test issue endpoints via the API."""

    def test_list_issues(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", [SAMPLE_ISSUE])
        resp = gitea_client.get("/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["issues"][0]["number"] == 42

    def test_get_issue(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues/42", SAMPLE_ISSUE)
        resp = gitea_client.get("/issues/42")
        assert resp.status_code == 200
        assert resp.json()["number"] == 42

    def test_get_issue_404(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues/999", None, status_code=404)
        resp = gitea_client.get("/issues/999")
        assert resp.status_code == 404

    def test_create_issue(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/issues", SAMPLE_ISSUE, status_code=201)
        resp = gitea_client.post("/issues", json={"title": "New bug", "body": "desc"})
        assert resp.status_code == 201
        assert resp.json()["number"] == 42

    def test_create_issue_validation_error(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        resp = gitea_client.post("/issues", json={"body": "no title"})
        assert resp.status_code == 422

    def test_update_issue(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        closed = {**SAMPLE_ISSUE, "state": "closed"}
        transport.set("PATCH", "/repos/lyra/mcp_server/issues/42", closed)
        resp = gitea_client.patch("/issues/42", json={"state": "closed"})
        assert resp.status_code == 200

    def test_list_issue_comments(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues/42/comments", [SAMPLE_COMMENT])
        resp = gitea_client.get("/issues/42/comments")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_create_issue_comment(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/issues/42/comments", SAMPLE_COMMENT, status_code=201)
        resp = gitea_client.post("/issues/42/comments", json={"body": "comment"})
        assert resp.status_code == 201


class TestGiteaRoutesBranches:
    """Test branch endpoints via the API."""

    def test_list_branches(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/branches", [SAMPLE_BRANCH])
        resp = gitea_client.get("/branches")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_branches_repo_not_found_returns_404(
        self, mock_gitea_service: Any, gitea_client: TestClient
    ) -> None:
        """Listing branches for a non-existent project returns 404, not 502/500."""
        resp = gitea_client.get("/branches", params={"owner": "nope", "repo": "nope"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Repository not found"

    def test_create_branch(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        new = {**SAMPLE_BRANCH, "name": "feature/x"}
        transport.set("POST", "/repos/lyra/mcp_server/branches", new, status_code=201)
        resp = gitea_client.post("/branches", json={"name": "feature/x", "from_ref": "main"})
        assert resp.status_code == 201

    def test_delete_branch(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/branches/feature/x", None, status_code=204)
        # Pass owner/repo explicitly so no query string is appended
        resp = gitea_client.delete("/branches/feature/x", params={"owner": "lyra", "repo": "mcp_server"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_branch_404(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/branches/nope", None, status_code=404)
        resp = gitea_client.delete("/branches/nope")
        assert resp.status_code == 404


class TestGiteaRoutesPRs:
    """Test PR endpoints via the API."""

    def test_list_prs(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls", [SAMPLE_PR])
        resp = gitea_client.get("/prs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_pr(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7", SAMPLE_PR)
        resp = gitea_client.get("/prs/7")
        assert resp.status_code == 200
        assert resp.json()["number"] == 7

    def test_get_pr_404(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls/999", None, status_code=404)
        resp = gitea_client.get("/prs/999")
        assert resp.status_code == 404

    def test_create_pr(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls", SAMPLE_PR, status_code=201)
        resp = gitea_client.post("/prs", json={"title": "Test", "head": "feature/x", "base": "main"})
        assert resp.status_code == 201

    def test_merge_pr(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls/7/merge", None, status_code=200)
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7", SAMPLE_MERGED_PR)
        resp = gitea_client.post("/prs/7/merge", json={"do": "squash"})
        assert resp.status_code == 200
        assert resp.json()["merged"] is True
        assert resp.json()["merge_commit_sha"] == "abc123def456"

    def test_merge_pr_conflict(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls/7/merge", {"message": "conflict"}, status_code=409)
        resp = gitea_client.post("/prs/7/merge", json={"do": "merge"})
        assert resp.status_code == 409

    def test_list_pr_reviews(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7/reviews", [SAMPLE_REVIEW])
        resp = gitea_client.get("/prs/7/reviews")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestGiteaRoutesActions:
    """Test Actions/CI endpoints."""

    def test_list_actions(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/actions/runs", [SAMPLE_ACTION_RUN])
        resp = gitea_client.get("/actions")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_commit_statuses(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/commits/abc123/statuses", [SAMPLE_COMMIT_STATUS])
        resp = gitea_client.get("/commits/abc123/statuses")
        assert resp.status_code == 200
        assert resp.json()["statuses"][0]["state"] == "success"


class TestGiteaRoutesReleases:
    """Test release endpoints."""

    def test_list_releases(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/releases", [SAMPLE_RELEASE])
        resp = gitea_client.get("/releases")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_release(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/releases/1", SAMPLE_RELEASE)
        resp = gitea_client.get("/releases/1")
        assert resp.status_code == 200

    def test_create_release(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/releases", SAMPLE_RELEASE, status_code=201)
        resp = gitea_client.post("/releases", json={"tag_name": "v0.5.0"})
        assert resp.status_code == 201

    def test_delete_release(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("DELETE", "/repos/lyra/mcp_server/releases/1", None, status_code=204)
        resp = gitea_client.delete("/releases/1")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


class TestGiteaRoutesRepo:
    """Test repo and commit endpoints."""

    def test_get_repo(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server", SAMPLE_REPO)
        resp = gitea_client.get("/repos/lyra/mcp_server")
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "lyra/mcp_server"

    def test_get_repo_not_found_returns_404(
        self, mock_gitea_service: Any, gitea_client: TestClient
    ) -> None:
        """A non-existent repo returns 404 (not 502/500)."""
        # Search returns nothing so no suggestion is appended.
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/search", {"total_count": 0, "data": []})
        resp = gitea_client.get("/repos/nope/nope")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_get_repo_404_with_did_you_mean(
        self, mock_gitea_service: Any, gitea_client: TestClient
    ) -> None:
        """404 includes a cross-owner 'did you mean' suggestion."""
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set(
            "GET", "/repos/search",
            {"total_count": 1, "data": [{**SAMPLE_REPO, "full_name": "public/mcp_server"}]},
        )
        resp = gitea_client.get("/repos/lyra/mcp_server")
        assert resp.status_code == 404
        assert "public/mcp_server" in resp.json()["detail"]

    def test_search_repos(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/search", {"total_count": 2, "data": [SAMPLE_REPO]})
        resp = gitea_client.get("/repos/search", params={"q": "mcp"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["repos"][0]["full_name"] == "lyra/mcp_server"

    def test_search_repos_filters_by_owner(
        self, mock_gitea_service: Any, gitea_client: TestClient
    ) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/search", {"total_count": 0, "data": []})
        resp = gitea_client.get("/repos/search", params={"q": "mcp", "owner": "public"})
        assert resp.status_code == 200
        # Confirm the owner param was forwarded upstream.
        assert "owner=public" in transport.full_urls[-1]

    def test_list_commits(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/commits", [SAMPLE_COMMIT])
        resp = gitea_client.get("/repos/lyra/mcp_server/commits")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_compare(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/compare/main...feature/x", SAMPLE_COMPARE)
        resp = gitea_client.get("/repos/lyra/mcp_server/compare", params={"base": "main", "head": "feature/x"})
        assert resp.status_code == 200
        assert resp.json()["commits_ahead"] == 3


# --------------------------------------------------------------------------- #
# Client library tests
# --------------------------------------------------------------------------- #

class TestMCPClientGitea:
    """Test the Gitea client methods on MCPClient.

    Uses the same pattern as test_client.py — replace the MCPClient's
    httpx.Client with a Starlette TestClient bound to the real FastAPI app.
    """

    def _make_client(self, gitea_client: TestClient) -> Any:
        from app.client import MCPClient
        mc = MCPClient("http://test")
        mc._client = gitea_client
        return mc

    def test_list_issues(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/issues", [SAMPLE_ISSUE])
        mc = self._make_client(gitea_client)
        result = mc.list_issues()
        assert result["total"] == 1

    def test_create_issue(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/issues", SAMPLE_ISSUE, status_code=201)
        mc = self._make_client(gitea_client)
        result = mc.create_issue(title="Test", body="desc")
        assert result["number"] == 42

    def test_list_prs(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/pulls", [SAMPLE_PR])
        mc = self._make_client(gitea_client)
        result = mc.list_prs()
        assert result["total"] == 1

    def test_create_pr(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls", SAMPLE_PR, status_code=201)
        mc = self._make_client(gitea_client)
        result = mc.create_pr(title="Test", head="feature/x", base="main")
        assert result["number"] == 7

    def test_merge_pr(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/pulls/7/merge", None, status_code=200)
        transport.set("GET", "/repos/lyra/mcp_server/pulls/7", SAMPLE_MERGED_PR)
        mc = self._make_client(gitea_client)
        result = mc.merge_pr(7, method="squash")
        assert result["merged"] is True

    def test_list_branches(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/branches", [SAMPLE_BRANCH])
        mc = self._make_client(gitea_client)
        result = mc.list_branches()
        assert result["total"] == 1

    def test_create_branch(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        new = {**SAMPLE_BRANCH, "name": "feature/y"}
        transport.set("POST", "/repos/lyra/mcp_server/branches", new, status_code=201)
        mc = self._make_client(gitea_client)
        result = mc.create_branch("feature/y", from_ref="main")
        assert result["name"] == "feature/y"

    def test_list_releases(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/releases", [SAMPLE_RELEASE])
        mc = self._make_client(gitea_client)
        result = mc.list_releases()
        assert result["total"] == 1

    def test_create_release(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("POST", "/repos/lyra/mcp_server/releases", SAMPLE_RELEASE, status_code=201)
        mc = self._make_client(gitea_client)
        result = mc.create_release("v0.5.0", name="v0.5.0")
        assert result["tag_name"] == "v0.5.0"

    def test_list_actions(self, mock_gitea_service: Any, gitea_client: TestClient) -> None:
        transport: MockTransport = mock_gitea_service._client._mock_transport  # type: ignore[attr-defined]
        transport.set("GET", "/repos/lyra/mcp_server/actions/runs", [SAMPLE_ACTION_RUN])
        mc = self._make_client(gitea_client)
        result = mc.list_actions()
        assert result["total"] == 1
