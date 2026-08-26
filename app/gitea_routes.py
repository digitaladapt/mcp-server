"""FastAPI router for Gitea integration endpoints.

Core endpoints (always in OpenAPI schema):
  GET    /repos/{owner}/{repo}           – get repo info
  GET    /user/repos                      – list accessible repos
  GET    /repos/{owner}/{repo}/commits    – list commits

  GET    /issues                          – list issues (default repo)
  GET    /issues/{index}                  – get a single issue
  POST   /issues                          – create an issue
  PATCH  /issues/{index}                  – update an issue
  GET    /issues/{index}/comments         – list issue comments
  POST   /issues/{index}/comments         – comment on an issue

  GET    /branches                        – list branches
  POST   /branches                        – create a branch
  DELETE /branches/{name}                 – delete a branch

  GET    /prs                             – list pull requests
  GET    /prs/{index}                     – get a single PR
  POST   /prs                             – create a PR
  PATCH  /prs/{index}                     – update a PR
  POST   /prs/{index}/merge               – merge a PR
  POST   /prs/{index}/comments            – comment on a PR

  GET    /actions                         – list CI workflow runs

  GET    /releases                        – list releases
  GET    /releases/{id}                   – get a single release
  POST   /releases                        – create a release
  PATCH  /releases/{id}                   – update a release
  DELETE /releases/{id}                   – delete a release

Opt-in endpoints (set MCP_GITEA_EXTRA_TOOLS=1 to include in schema):
  GET    /repos/{owner}/{repo}/compare    – compare two refs
  GET    /prs/{index}/reviews             – list reviews on a PR
  GET    /commits/{sha}/statuses          – get commit CI status

All endpoints require API key authentication when MCP_API_KEY is set.
Returns 503 if Gitea is not configured (GITEA_URL unset).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

from .auth import verify_api_key
from .gitea_models import (
    ActionRunListResponse,
    BranchCreate,
    BranchInfo,
    BranchListResponse,
    CommentCreate,
    CommentDetail,
    CommentListResponse,
    CommitListResponse,
    CommitStatusListResponse,
    CompareResult,
    DeleteResponse,
    GiteaConfig,
    IssueCreate,
    IssueDetail,
    IssueListResponse,
    IssueUpdate,
    MergeResponse,
    PRCreate,
    PRDetail,
    PRListResponse,
    PRMerge,
    PRUpdate,
    ReleaseCreate,
    ReleaseDetail,
    ReleaseListResponse,
    ReleaseUpdate,
    RepoDetail,
    RepoListResponse,
    ReviewListResponse,
)
from .gitea_service import GiteaError, GiteaService

router = APIRouter(prefix="", tags=["gitea"], dependencies=[Depends(verify_api_key)])

# Extra/niche Gitea endpoints are hidden from the OpenAPI schema by default.
# Set MCP_GITEA_EXTRA_TOOLS=1 to expose them (compare refs, PR reviews, commit statuses).
import os as _os

_extra_tools = _os.environ.get("MCP_GITEA_EXTRA_TOOLS", "").strip().lower() in ("1", "true", "yes")

# Singleton service — lazily initialised from env vars.
_service: GiteaService | None = None
_service_inited: bool = False


def _get_service() -> GiteaService:
    """Return the GiteaService singleton, or raise 503 if not configured."""
    global _service, _service_inited
    if not _service_inited:
        config = GiteaConfig.from_env()
        if config is None:
            _service_inited = True
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gitea is not configured. Set GITEA_URL and related env vars.",
            )
        _service = GiteaService(config)
        _service_inited = True

    if _service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gitea is not configured. Set GITEA_URL and related env vars.",
        )
    return _service


def _reset_service() -> None:
    """Reset the singleton (used in tests)."""
    global _service, _service_inited
    if _service is not None:
        _service.close()
    _service = None
    _service_inited = False


# --------------------------------------------------------------------------- #
# Repository endpoints
# --------------------------------------------------------------------------- #

@router.get("/repos/{owner}/{repo}", response_model=RepoDetail)
async def get_repo(owner: str, repo: str) -> RepoDetail:
    """Get repo info."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.get_repo, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


@router.get("/user/repos", response_model=RepoListResponse)
async def list_repos() -> RepoListResponse:
    """List accessible repos."""
    svc = _get_service()
    try:
        repos = await run_in_threadpool(svc.list_repos)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return RepoListResponse(repos=repos, total=len(repos))


@router.get("/repos/{owner}/{repo}/commits", response_model=CommitListResponse)
async def list_commits(
    owner: str,
    repo: str,
    sha: str | None = Query(None, description="Branch or tag"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> CommitListResponse:
    """List commits in a repo."""
    svc = _get_service()
    try:
        commits = await run_in_threadpool(svc.list_commits, sha=sha, owner=owner, repo=repo, page=page, limit=limit)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return CommitListResponse(commits=commits, total=len(commits))


@router.get("/repos/{owner}/{repo}/compare", response_model=CompareResult, include_in_schema=_extra_tools)
async def compare_refs(owner: str, repo: str, base: str, head: str) -> CompareResult:
    """Compare two refs (branches, tags, or SHAs) in a repository."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.compare, base, head, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


# --------------------------------------------------------------------------- #
# Issue endpoints (default repo)
# --------------------------------------------------------------------------- #

@router.get("/issues", response_model=IssueListResponse)
async def list_issues(
    state: str = Query("open", description="State filter"),
    labels: str | None = Query(None, description="Label names"),
    owner: str | None = Query(None, description="Repo owner"),
    repo: str | None = Query(None, description="Repo name"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> IssueListResponse:
    """List issues."""
    svc = _get_service()
    try:
        issues = await run_in_threadpool(
            svc.list_issues,
            state=state, labels=labels, owner=owner, repo=repo, page=page, limit=limit,
        )
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return IssueListResponse(issues=issues, total=len(issues))


@router.get("/issues/{index}", response_model=IssueDetail)
async def get_issue_by_index(
    index: int,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> IssueDetail:
    """Get an issue."""
    svc = _get_service()
    try:
        issue = await run_in_threadpool(svc.get_issue, index, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


@router.post("/issues", response_model=IssueDetail, status_code=201)
async def create_issue(
    req: IssueCreate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> IssueDetail:
    """Create an issue."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.create_issue, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


@router.patch("/issues/{index}", response_model=IssueDetail)
async def update_issue(
    index: int,
    req: IssueUpdate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> IssueDetail:
    """Update an issue."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.update_issue, index, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=400, detail="Gitea service error")


@router.get("/issues/{index}/comments", response_model=CommentListResponse)
async def list_issue_comments(
    index: int,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> CommentListResponse:
    """List issue comments."""
    svc = _get_service()
    try:
        comments = await run_in_threadpool(svc.list_issue_comments, index, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return CommentListResponse(comments=comments, total=len(comments))


@router.post("/issues/{index}/comments", response_model=CommentDetail, status_code=201)
async def create_issue_comment(
    index: int,
    req: CommentCreate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> CommentDetail:
    """Comment on an issue."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.create_issue_comment, index, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


# --------------------------------------------------------------------------- #
# Branch endpoints
# --------------------------------------------------------------------------- #

@router.get("/branches", response_model=BranchListResponse)
async def list_branches(
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> BranchListResponse:
    """List branches."""
    svc = _get_service()
    try:
        branches = await run_in_threadpool(svc.list_branches, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return BranchListResponse(branches=branches, total=len(branches))


@router.post("/branches", response_model=BranchInfo, status_code=201)
async def create_branch(
    req: BranchCreate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> BranchInfo:
    """Create a branch."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.create_branch, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


@router.delete("/branches/{name:path}", response_model=DeleteResponse)
async def delete_branch(
    name: str,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> DeleteResponse:
    """Delete a branch."""
    svc = _get_service()
    try:
        deleted = await run_in_threadpool(svc.delete_branch, name, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=400, detail="Gitea service error")
    if not deleted:
        raise HTTPException(status_code=404, detail="Branch not found")
    return DeleteResponse(deleted=True, resource="branch", identifier=name)


# --------------------------------------------------------------------------- #
# Pull Request endpoints
# --------------------------------------------------------------------------- #

@router.get("/prs", response_model=PRListResponse)
async def list_prs(
    state: str = Query("open", description="State filter"),
    owner: str | None = Query(None),
    repo: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> PRListResponse:
    """List pull requests."""
    svc = _get_service()
    try:
        prs = await run_in_threadpool(svc.list_prs, state=state, owner=owner, repo=repo, page=page, limit=limit)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return PRListResponse(pulls=prs, total=len(prs))


@router.get("/prs/{index}", response_model=PRDetail)
async def get_pr_by_index(
    index: int,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> PRDetail:
    """Get a pull request."""
    svc = _get_service()
    try:
        pr = await run_in_threadpool(svc.get_pr, index, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    if pr is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    return pr


@router.post("/prs", response_model=PRDetail, status_code=201)
async def create_pr(
    req: PRCreate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> PRDetail:
    """Create a pull request."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.create_pr, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


@router.patch("/prs/{index}", response_model=PRDetail)
async def update_pr(
    index: int,
    req: PRUpdate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> PRDetail:
    """Update a pull request."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.update_pr, index, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=400, detail="Gitea service error")


@router.post("/prs/{index}/merge", response_model=MergeResponse)
async def merge_pr(
    index: int,
    req: PRMerge,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> MergeResponse:
    """Merge a pull request."""
    svc = _get_service()
    try:
        merged = await run_in_threadpool(svc.merge_pr, index, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=409, detail="Gitea service error")
    # Fetch the updated PR to get merge_commit_sha
    pr = await run_in_threadpool(svc.get_pr, index, owner=owner, repo=repo)
    return MergeResponse(
        merged=merged,
        pr_number=index,
        merge_commit_sha=pr.merge_commit_sha if pr else None,
    )


@router.get("/prs/{index}/reviews", response_model=ReviewListResponse, include_in_schema=_extra_tools)
async def list_pr_reviews(
    index: int,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> ReviewListResponse:
    """List PR reviews."""
    svc = _get_service()
    try:
        reviews = await run_in_threadpool(svc.list_pr_reviews, index, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return ReviewListResponse(reviews=reviews, total=len(reviews))


@router.post("/prs/{index}/comments", response_model=CommentDetail, status_code=201)
async def create_pr_comment(
    index: int,
    req: CommentCreate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> CommentDetail:
    """Comment on a PR."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.create_pr_comment, index, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


# --------------------------------------------------------------------------- #
# Actions / CI endpoints
# --------------------------------------------------------------------------- #

@router.get("/actions", response_model=ActionRunListResponse)
async def list_actions(
    owner: str | None = Query(None),
    repo: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> ActionRunListResponse:
    """List CI workflow runs."""
    svc = _get_service()
    try:
        runs = await run_in_threadpool(svc.list_actions, owner=owner, repo=repo, page=page, limit=limit)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return ActionRunListResponse(runs=runs, total=len(runs))


@router.get("/commits/{sha}/statuses", response_model=CommitStatusListResponse, include_in_schema=_extra_tools)
async def get_commit_statuses(
    sha: str,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> CommitStatusListResponse:
    """Get commit CI status."""
    svc = _get_service()
    try:
        statuses = await run_in_threadpool(svc.get_commit_statuses, sha, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return CommitStatusListResponse(statuses=statuses, total=len(statuses))


# --------------------------------------------------------------------------- #
# Release endpoints
# --------------------------------------------------------------------------- #

@router.get("/releases", response_model=ReleaseListResponse)
async def list_releases(
    owner: str | None = Query(None),
    repo: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> ReleaseListResponse:
    """List releases."""
    svc = _get_service()
    try:
        releases = await run_in_threadpool(svc.list_releases, owner=owner, repo=repo, page=page, limit=limit)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    return ReleaseListResponse(releases=releases, total=len(releases))


@router.get("/releases/{release_id}", response_model=ReleaseDetail)
async def get_release_by_id(
    release_id: int,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> ReleaseDetail:
    """Get a release."""
    svc = _get_service()
    try:
        release = await run_in_threadpool(svc.get_release, release_id, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


@router.post("/releases", response_model=ReleaseDetail, status_code=201)
async def create_release(
    req: ReleaseCreate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> ReleaseDetail:
    """Create a release."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.create_release, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=502, detail="Gitea service error")


@router.patch("/releases/{release_id}", response_model=ReleaseDetail)
async def update_release(
    release_id: int,
    req: ReleaseUpdate,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> ReleaseDetail:
    """Update a release."""
    svc = _get_service()
    try:
        return await run_in_threadpool(svc.update_release, release_id, req, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=400, detail="Gitea service error")


@router.delete("/releases/{release_id}", response_model=DeleteResponse)
async def delete_release(
    release_id: int,
    owner: str | None = Query(None),
    repo: str | None = Query(None),
) -> DeleteResponse:
    """Delete a release."""
    svc = _get_service()
    try:
        deleted = await run_in_threadpool(svc.delete_release, release_id, owner=owner, repo=repo)
    except GiteaError:
        logger.exception("Gitea service error")
        raise HTTPException(status_code=400, detail="Gitea service error")
    if not deleted:
        raise HTTPException(status_code=404, detail="Release not found")
    return DeleteResponse(deleted=True, resource="release", identifier=str(release_id))
