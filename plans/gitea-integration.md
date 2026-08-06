# Gitea Integration — Plan for v0.6.0

## Vision

Give Lyra a complete development workflow against your Gitea instance:

> See an issue → create a branch → do the work → push → open a PR →
> watch CI → merge → close the issue.

All through the MCP server's HTTP API and client library, same pattern
as the CalDAV integration.

## Gitea API Reference

Target server: `code.devgnome.com` (Gitea 1.27.1)
API base: `https://code.devgnome.com/api/v1`
Auth: token-based (`Authorization: token <TOKEN>`), configured via env var.

The Gitea API is GitHub-compatible.  Key endpoint groups we'll use:

| Resource     | Endpoints                                                        |
|--------------|------------------------------------------------------------------|
| Pull Requests| `GET/POST /repos/{owner}/{repo}/pulls`                           |
|              | `GET/PATCH /repos/{owner}/{repo}/pulls/{index}`                  |
|              | `POST /repos/{owner}/{repo}/pulls/{index}/merge`                 |
|              | `GET/POST /repos/{owner}/{repo}/pulls/{index}/reviews`           |
|              | `GET/POST /repos/{owner}/{repo}/pulls/{index}/comments`          |
| Issues       | `GET/POST /repos/{owner}/{repo}/issues`                          |
|              | `GET/PATCH /repos/{owner}/{repo}/issues/{index}`                 |
|              | `GET/POST /repos/{owner}/{repo}/issues/{index}/comments`         |
|              | `GET/POST /repos/{owner}/{repo}/issues/{index}/labels`           |
| Branches     | `GET/POST /repos/{owner}/{repo}/branches`                        |
|              | `DELETE /repos/{owner}/{repo}/branches/{branch}`                 |
| Actions/CI   | `GET /repos/{owner}/{repo}/actions/tasks`                        |
|              | `GET /repos/{owner}/{repo}/actions/artifacts`                    |
| Commits      | `GET /repos/{owner}/{repo}/commits`                              |
|              | `GET /repos/{owner}/{repo}/compare/{base}...{head}`             |
| Releases     | `GET/POST /repos/{owner}/{repo}/releases`                        |
|              | `GET/PATCH/DELETE /repos/{owner}/{repo}/releases/{id}`           |
| Repos        | `GET /repos/{owner}/{repo}`                                      |
|              | `GET /user/repos`                                                |
| Labels       | `GET /repos/{owner}/{repo}/labels`                               |
| Milestones   | `GET/POST /repos/{owner}/{repo}/milestones`                      |

---

## Architecture

Follow the same pattern as CalDAV:

```
app/
├─ gitea_service.py      # Gitea API client + business logic
├─ gitea_models.py       # Pydantic request/response models
├─ gitea_routes.py       # FastAPI router (/repos, /issues, /prs, /branches, /actions)
├─ client.py             # Add typed convenience methods to MCPClient
└─ main.py               # Mount the router when GITEA_URL is set

tests/
└─ test_gitea.py         # Unit tests (mocked httpx responses)
```

### Configuration (env vars)

```
GITEA_URL=https://code.devgnome.com
GITEA_TOKEN=<api-token>
GITEA_DEFAULT_OWNER=lyra
GITEA_DEFAULT_REPO=mcp_server
```

When `GITEA_URL` is not set, all Gitea endpoints return `503 Service
Unavailable` — same pattern as CalDAV.

The default owner/repo means most calls don't need to specify them
explicitly, but every endpoint accepts `owner` and `repo` overrides
for multi-repo workflows.

---

## Feature Breakdown

### 1. Issues

The starting point of the workflow — "what should I work on?"

| Endpoint | Method | Description                                    |
|----------|--------|------------------------------------------------|
| `/issues` | GET    | List issues (filter by state, labels, assignee) |
| `/issues/{index}` | GET | Get a single issue with full details     |
| `/issues` | POST   | Create a new issue                             |
| `/issues/{index}` | PATCH | Update an issue (close, reopen, set title) |
| `/issues/{index}/comments` | POST | Comment on an issue              |

**Models:**
- `IssueListResponse` — paginated list with total count
- `IssueDetail` — number, title, body, state, labels, assignees, milestone, created/updated/closed dates
- `IssueCreate` — title, body, labels, assignees, milestone
- `IssueUpdate` — state (open/closed), title, body
- `CommentCreate` — body

**Why this matters:** This is the entry point. "Show me open issues" →
pick one → start working. Without issues, there's no workflow to drive.

---

### 2. Branches

Needed to isolate work before opening a PR.

| Endpoint | Method | Description                                    |
|----------|--------|------------------------------------------------|
| `/branches` | GET  | List branches in the repo                     |
| `/branches` | POST | Create a branch from a ref (default: main)    |
| `/branches/{branch}` | DELETE | Delete a merged branch             |

**Models:**
- `BranchListResponse` — list of branch names + last commit SHA
- `BranchCreate` — name, from (ref to branch from, defaults to default branch)

**Why this matters:** The workflow is: see issue → create branch →
commit work → push → PR. Creating a branch through the API (rather than
shell `git checkout -b`) keeps it tracked and clean. Deleting after
merge keeps the repo tidy.

---

### 3. Pull Requests

The core feature — review and merge code changes.

| Endpoint | Method | Description                                    |
|----------|--------|------------------------------------------------|
| `/prs` | GET      | List PRs (filter by state: open/closed/merged)  |
| `/prs/{index}` | GET | Get a single PR with diff stats, merge status |
| `/prs` | POST     | Create a PR (head branch → base branch)         |
| `/prs/{index}` | PATCH | Update a PR (title, body, state)           |
| `/prs/{index}/merge` | POST | Merge a PR (squash, merge, rebase)      |
| `/prs/{index}/reviews` | GET | List reviews on a PR                    |
| `/prs/{index}/comments` | POST | Comment on a PR (review feedback)     |

**Models:**
- `PRListResponse` — paginated list
- `PRDetail` — number, title, body, state, head/base, mergeable, merge_commit_sha, labels, review status
- `PRCreate` — title, body, head (source branch), base (target branch)
- `PRUpdate` — title, body, state
- `PRMerge` — do (merge/squash/rebase), merge_commit_message
- `ReviewListResponse` — list of reviews with status (approved, changes_requested, commented)

**Why this matters:** This was the pain point — I could push code but
couldn't open the PR to merge it. This is the centerpiece of v0.6.

---

### 4. Actions / CI

Check whether CI passed before merging.

| Endpoint | Method | Description                                    |
|----------|--------|------------------------------------------------|
| `/actions/tasks` | GET | List CI workflow runs with status (success/failure/running) |

**Models:**
- `ActionTaskListResponse` — list of runs with workflow name, status, conclusion, branch, SHA, timestamps

**Note:** Gitea's Actions API is still maturing in 1.27.  The tasks
endpoint returns workflow run status.  If the API is limited, we may
also scrape the commit status API (`GET /repos/{owner}/{repo}/commits/{sha}/statuses`)
which gives CI pass/fail as a simple check.

**Why this matters:** "Did CI pass?" is the gate between "PR opened"
and "PR merged." Without this, I'd be merging blind.

---

### 5. Releases & Tags

Manage version releases — like the v0.5.0 tag we just created.

| Endpoint | Method | Description                                    |
|----------|--------|------------------------------------------------|
| `/releases` | GET  | List releases                                  |
| `/releases/{id}` | GET | Get a single release                      |
| `/releases` | POST | Create a release (tag, title, body, draft)     |
| `/releases/{id}` | PATCH | Update a release (publish, edit body)     |
| `/releases/{id}` | DELETE | Delete a release                          |

**Models:**
- `ReleaseListResponse` — list of releases
- `ReleaseDetail` — tag, name, body, draft, prerelease, assets, timestamps
- `ReleaseCreate` — tag_name, target (commitish), name, body, draft, prerelease

**Why this matters:** Being able to tag and release programmatically
closes the loop — fix issue → PR → merge → tag release. Right now I
had to ask you to click through the Gitea UI for the PR, and I tagged
v0.5.0 via git CLI. Having this in the API makes it a first-class
workflow.

---

### 6. Repository Info & Commits

Context-gathering endpoints — "what's in this repo, what changed?"

| Endpoint | Method | Description                                    |
|----------|--------|------------------------------------------------|
| `/repos/{owner}/{repo}` | GET | Get repo info (default branch, stats)  |
| `/user/repos` | GET | List repos accessible to the token        |
| `/commits` | GET    | List recent commits                          |
| `/compare/{base}...{head}` | GET | Compare two refs (diff summary)      |

**Models:**
- `RepoDetail` — name, full_name, default_branch, description, star/fork count
- `RepoListResponse` — list of accessible repos
- `CommitListResponse` — paginated list of recent commits
- `CompareResponse` — commits ahead/behind, files changed, additions, deletions

**Why this matters:** Before creating a PR, I should be able to check
"what's on this branch vs main?" The compare endpoint gives a summary
diff without needing to run `git diff` locally. And listing accessible
repos is useful when working across multiple projects.

---

## The Full Workflow

Here's how all the pieces fit together — this is the target user story:

```
1. GET /issues?state=open          → "Show me what needs doing"
2. POST /branches                   → Create feature branch from main
3. (do the work — code, test, commit, push via existing tools)
4. GET /compare/main...feature-xyz  → Verify what changed
5. POST /prs                        → Open a pull request
6. GET /actions/tasks?branch=feature-xyz → Check CI status
7. POST /prs/{index}/merge          → Merge once CI is green
8. PATCH /issues/{index}            → Close the issue
9. DELETE /branches/{branch}        → Clean up the branch
10. POST /releases                  → Tag a release (if applicable)
```

Every step is an API call. No UI clicking, no context switching.

---

## Client Library

Add convenience methods to `MCPClient`, mirroring the CalDAV pattern:

```python
mc = MCPClient("http://127.0.0.1:8000", api_key="...")

# Issues
issues = mc.list_issues(state="open")
issue = mc.get_issue(42)
mc.create_issue(title="Fix bug", body="Description")
mc.close_issue(42)

# Branches
mc.create_branch("feature/fix-bug", from_ref="main")
mc.delete_branch("feature/fix-bug")

# Pull Requests
prs = mc.list_prs(state="open")
pr = mc.get_pr(7)
mc.create_pr(title="Fix bug", head="feature/fix-bug", base="main")
mc.merge_pr(7, method="squash")

# Actions
runs = mc.list_actions(branch="feature/fix-bug")

# Releases
mc.create_release(tag="v0.6.0", name="Gitea Integration", body="...")

# Compare
diff = mc.compare("main", "feature/fix-bug")
```

---

## Implementation Phases

### Phase 1: Foundation (gitea_service.py + gitea_models.py)
- GiteaService class with httpx client, token auth, error handling
- Connection to Gitea API, base URL + token from env
- Pydantic models for all request/response types
- Unit tests with mocked httpx responses

### Phase 2: Issues + Branches
- Issues: list, get, create, update, comment
- Branches: list, create, delete
- Tests for each

### Phase 3: Pull Requests
- PRs: list, get, create, update, merge
- Reviews: list
- Comments on PRs
- Tests

### Phase 4: Actions + Releases
- Actions: list workflow runs / commit statuses
- Releases: list, create, update, delete
- Compare: diff between branches
- Tests

### Phase 5: Integration
- Mount router in main.py
- Add client methods to MCPClient
- Update README, .env.example
- Full test suite passing
- Commit and (finally!) open a PR using our own Gitea integration

---

## Security Considerations

- **Token storage:** `GITEA_TOKEN` in env, never committed. `.gitignore`
  already covers `.env`. `.env.example` will have a placeholder.
- **Token scope:** The token should have minimal scopes — `repo` (read/write
  to repos, issues, PRs) and `write:repository`. No admin scopes needed.
- **Read vs write:** Consider a `GITEA_READ_ONLY` mode that disables
  POST/PATCH/DELETE endpoints (like CalDAV's read-only calendars). Useful
  for production deployments where you want the model to observe but not
  modify.
- **No secrets in responses:** API responses may contain user emails, etc.
  The service should strip sensitive fields before returning to the model.

---

## What's NOT in Scope for v0.6

- **Wiki management** — Gitea has a wiki API, but it's low priority.
- **Organization management** — org settings, member management.
- **User administration** — creating/deleting users, admin tasks.
- **Webhook management** — registering/unregistering webhooks.
- **Gitea Actions workflow file management** — editing `.gitea/workflows/*.yml`.
  That's code editing, which is already handled by the file tools.

These can come in v0.7+ if needed.

---

## Testing Strategy

Same as CalDAV — mocked `httpx` responses, no real API calls in tests:

- Each endpoint gets happy-path + error-case tests
- Mock Gitea API responses using `respx` or `httpx.MockTransport`
- Test pagination, filtering, and error handling
- Test 503 when `GITEA_URL` is not set
- Target: ~80+ unit tests, same standard as CalDAV

---

## Success Criteria

- [ ] Can list and create issues
- [ ] Can create and delete branches
- [ ] Can create, review, and merge pull requests
- [ ] Can check CI/Actions status
- [ ] Can create releases/tags
- [ ] Can compare branches
- [ ] Full workflow works end-to-end (issue → branch → PR → CI → merge → release)
- [ ] All tests passing, ruff clean
- [ ] README updated with Gitea section
- [ ] v0.6.0 tagged and released **using our own Gitea integration**
