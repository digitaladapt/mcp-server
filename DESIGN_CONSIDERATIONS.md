# Design Considerations — MCP Server

## Summary

The MCP Server is a well-structured FastAPI application that exposes CLI scripts as typed HTTP tools for LLM consumption, with additional CalDAV and Gitea integration layers. The codebase demonstrates solid engineering fundamentals — clean separation of concerns (models, services, routes, executor), a thoughtful registry-driven design for CLI commands, comprehensive test coverage, and careful subprocess management (process-group killing on timeout). The areas below identify opportunities where modern best practices could further improve the codebase's maintainability, security posture, and operational reliability.

---

## 1. Architecture

### 1.1 Module-level singletons with global mutable state [High Impact]

**Current state:** Both `caldav_routes.py` and `gitea_routes.py` use module-level global variables (`_service`, `_service_inited`) to implement lazy singleton services. The `_get_service()` functions mutate these globals directly. Tests must call `_reset_service()` to clean up between runs.

**Suggested change:** Use FastAPI's dependency injection system for service lifecycle. Define a `get_caldav_service()` and `get_gitea_service()` as `Depends()`-compatible callables, potentially using `functools.lru_cache` or an `app.state` pattern. This eliminates global mutable state.

**Why:** Module-level globals create hidden coupling — any module that imports the routes implicitly depends on the singleton's state. They make testing harder (requiring manual resets), prevent proper isolation between requests, and introduce subtle ordering bugs if the environment changes after the first initialization (e.g., if env vars are set programmatically in tests). FastAPI's DI system was designed exactly for this pattern and would make the dependency explicit and testable.

### 1.2 Import-time side effects in registry.py [Medium Impact]

**Current state:** `registry.py` calls `load_registry()` at the bottom of the module, executing on import. Similarly, `registry_routes.py` calls `create_registry_router()` at module level. This means importing `app.registry` (even transitively) triggers filesystem I/O.

**Suggested change:** Move the initial `load_registry()` call into an application lifespan/startup event in `main.py`. Expose `load_registry()` as an explicit initialization step. The router creation can happen at import time since it depends on the already-loaded registry, or also move to startup.

**Why:** Import-time side effects make testing fragile (tests must remember to call `reg.load_registry()` to restore state after modifying `COMMANDS`), complicate debugging (a broken registry file can crash an import chain), and prevent lazy initialization patterns. Explicit startup hooks make the lifecycle clear and allow graceful degradation — a broken registry file could be logged without preventing the app from starting.

### 1.3 `__init__.py` version mismatch [Low Impact]

**Current state:** `app/__init__.py` declares `__version__ = "0.1.0"` while `main.py` and `pyproject.toml` both use `0.9.0`.

**Suggested change:** Remove the version from `__init__.py` or import it from a single source of truth (e.g., `pyproject.toml` via `importlib.metadata`).

**Why:** Version mismatches cause confusion when debugging or reporting issues. Having a single source of truth for the version prevents drift.

### 1.4 `client.py` mixes concerns and has inconsistent error handling [Medium Impact]

**Current state:** The `MCPClient` class is 381 lines and covers three distinct API surfaces: registry commands, CalDAV calendar operations, and Gitea operations. Some methods use the generic `_get`/`_post` helpers, while others (e.g., `list_events`, `list_issues`) construct `httpx` requests inline with their own error handling, duplicating the pattern.

**Suggested change:** Either (a) split into separate client mixins/classes (e.g., `CalendarMixin`, `GiteaMixin`) composed into `MCPClient`, or (b) at minimum, route all methods through the existing `_get`/`_post`/`_put`/`_delete`/`_patch` helpers consistently and add a `_get_with_params()` helper for query-param endpoints.

**Why:** The inline `httpx` calls in methods like `list_events` and `list_issues` bypass the centralized error handling in `_get()`/`_post()`, meaning error messages and response handling differ subtly across methods. This inconsistency makes the client harder to maintain and debug. Splitting by concern would also make it possible to use just the Gitea or CalDAV client without pulling in all dependencies.

### 1.5 `_parse_repo` defined after the class that uses it [Low Impact]

**Current state:** In `gitea_service.py`, the `_parse_repo()` function is defined at the very bottom of the file with a comment explaining it avoids "circular ref at module load," even though `RepoDetail` is defined at the top of the same module.

**Suggested change:** Move `_parse_repo()` up with the other `_parse_*` functions near the top of the file.

**Why:** The comment's rationale doesn't hold — `RepoDetail` is defined before the `GiteaService` class. The function is only called at runtime (inside method bodies), not at class-definition time, so there's no circular dependency. The misplaced function disrupts the file's logical organization.

---

## 2. Security

### 2.1 API key read once at import time, never refreshable [Medium Impact]

**Current state:** `auth.py` reads `MCP_API_KEY` at module import time into `_API_KEY`. If the environment variable changes (e.g., key rotation), the running server won't pick it up without a full restart.

**Suggested change:** Read the API key per-request inside `verify_api_key()`, or cache it with a short TTL. Alternatively, document this as a known limitation and provide a `/reload` endpoint.

**Why:** For long-running production deployments, key rotation without downtime is a common operational requirement. While a restart is acceptable for a small dev server, making the limitation explicit (or fixing it) prevents surprises during security incidents when keys need to be rotated quickly.

### 2.2 No rate limiting on endpoints [Medium Impact]

**Current state:** All endpoints are open to brute-force attacks on the API key when `MCP_API_KEY` is set. There's no rate limiting or account lockout mechanism.

**Suggested change:** Add a lightweight rate limiter (e.g., `slowapi` or a simple in-memory token bucket) on auth-failed responses. Even a simple "N failed attempts per IP per minute" would significantly raise the bar.

**Why:** The constant-time comparison in `verify_api_key` is good practice but irrelevant if an attacker can make thousands of guesses per second. Since this server is designed to be network-accessible (it's an LLM tool provider), rate limiting on auth failures is a baseline security measure.

### 2.3 Error messages leak internal details [Medium Impact]

**Current state:** Exception handlers in `caldav_routes.py` and `gitea_routes.py` pass raw exception messages directly to HTTP responses: `detail=f"CalDAV error: {exc}""` and `detail=str(exc)`. Similarly, the executor's `ValueError` messages include the full command line: `f"Command timed out after {timeout}s: {' '.join(...)}"`.

**Suggested change:** Log the full exception details server-side and return a sanitized message to the client. For example, return `"CalDAV operation failed"` with the specific error logged at `logger.error()` level.

**Why:** Raw exception messages can leak internal paths, server configurations, credential fragments, or stack details that help attackers understand the system's internals. This is especially important since the server is designed to be called by LLMs, and error messages flow back into the model's context window.

### 2.4 `.env` file committed to repository [High Impact]

**Current state:** The `.env` file at the project root contains real CalDAV credentials (`CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD`). While `.gitignore` lists `.env`, the file exists in the working directory with what appear to be development credentials.

**Suggested change:** Verify `.env` is not tracked in git history. If it was ever committed, rotate the credentials. Consider using `.env.local` for actual secrets and keeping `.env` as a non-secret defaults file, or rely solely on `.env.example`.

**Why:** Even if `.env` is currently gitignored, having real credentials in a file that's one `git add -f` away from being committed is risky. The CalDAV password "andrew" for user "andrew" suggests weak credentials on the CalDAV server itself. This is a dev instance, but the pattern should be clean.

### 2.5 No CORS configuration [Low Impact]

**Current state:** The FastAPI app has no CORS middleware configured. If the server is accessed from a browser-based client (e.g., a web UI for the LLM), requests will be blocked.

**Suggested change:** Add `CORSMiddleware` with configurable allowed origins via an environment variable (defaulting to `*` for dev, restrictive for production).

**Why:** While the primary consumers are LLMs and CLI tools (not browsers), adding CORS support is trivial and prevents surprises if a web-based client is ever added. Making it configurable avoids the "CORS wide open in production" anti-pattern.

---

## 3. Error Handling

### 3.1 Overly broad `except Exception` with `noqa: BLE001` [Medium Impact]

**Current state:** Both `caldav_routes.py` and `caldav_service.py` use `except Exception as exc:  # noqa: BLE001` extensively (20+ occurrences). This catches everything including `KeyboardInterrupt`, `SystemExit`, `MemoryError`, etc.

**Suggested change:** Catch specific exception types where possible (e.g., `caldav.lib.error.DAVError`, `ConnectionError`, `TimeoutError`). For the truly "catch-all" cases, at minimum re-raise `SystemExit` and `KeyboardInterrupt` or use `except Exception` with explicit logging of the exception type.

**Why:** Broad exception catches mask bugs — a `TypeError` from a code change looks identical to a CalDAV server error. The `noqa: BLE001` comments acknowledge the linting violation but don't address the underlying issue. Catching specific exceptions makes the error handling more precise and helps debugging.

### 3.2 CalDAV connection recovery only catches `DAVError` [Low Impact]

**Current state:** The `_with_connection_recovery` decorator in `caldav_service.py` only catches `caldav.lib.error.DAVError` and retries. Connection errors (e.g., `requests.ConnectionError`, `socket.timeout`) are not caught.

**Suggested change:** Also catch `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout`, and potentially `OSError` in the recovery decorator.

**Why:** The decorator's stated purpose is to handle stale connections when "the CalDAV server restarts or the network hiccups." But network hiccups typically manifest as `ConnectionError`, not `DAVError`. The recovery logic doesn't cover the most common failure mode it claims to address.

### 3.3 Gitea service `_request` doesn't handle network timeouts distinctly [Low Impact]

**Current state:** In `gitea_service.py`, all `httpx.HTTPError` exceptions are caught and wrapped in `GiteaError(f"Network error: {exc}")`. The httpx client has a 30-second timeout.

**Suggested change:** Distinguish between connection errors, timeouts, and other HTTP errors. Return appropriate HTTP status codes (504 for timeout, 502 for connection error) from the route layer.

**Why:** A timeout and a 404 look the same to the client (both become `GiteaError`). Distinguishing them helps the LLM understand whether to retry, wait, or report a configuration problem.

---

## 4. Testing

### 4.1 No tests for `registry_routes.py` route generation logic [High Impact]

**Current state:** While `test_api.py` tests that dedicated routes work (e.g., `POST /log`), there are no unit tests for `registry_routes.py`'s core functions: `build_request_model()`, `_model_to_args()`, `_field_info()`, `_safe_field_name()`. The collision detection, hidden arg handling, and `field_name` aliasing logic are only tested indirectly.

**Suggested change:** Add a `test_registry_routes.py` that directly tests: model generation for various arg type combinations, `field_name` aliasing, hidden arg exclusion, duplicate field name detection, reserved path collision handling, and `_model_to_args` round-trip conversion.

**Why:** `registry_routes.py` contains the most complex dynamic code in the project (Pydantic model generation, alias mapping, closure-based route handlers). A regression here could silently break all registry commands. The indirect coverage from `test_api.py` only exercises the happy path with the three existing commands.

### 4.2 Auth tests rely on module reloading [Medium Impact]

**Current state:** `test_auth.py` uses `importlib.sys.modules.pop()` and re-imports modules to test auth with different `MCP_API_KEY` values. This is fragile and depends on Python import internals.

**Suggested change:** Refactor `auth.py` to read the API key lazily (per-request or via a configurable provider) so tests can simply set/unset the environment variable without reloading modules.

**Why:** Module reloading is a test smell — it indicates the code has import-time side effects that make it non-idempotent. If the import order changes or new modules are added to the `_AUTH_DEPENDENT_MODULES` tuple, tests will silently break. This is the same root cause as finding 1.1.

### 4.3 No integration test for the executor's timeout/process-group kill [Medium Impact]

**Current state:** `test_executor.py` tests `_cast`, `_validate_and_build`, and basic `run_command` (echo, false, missing executable), but doesn't test the timeout behavior or the `_kill_process_group` logic — arguably the most critical safety mechanism in the codebase.

**Suggested change:** Add a test that registers a command pointing at a script that sleeps for 60 seconds, calls `run_command()` with `timeout=1`, and verifies: (a) a `ValueError` is raised with the timeout message, (b) the process and any children are actually killed.

**Why:** The process-group kill is the single most important safety feature — without it, a hanging subprocess would hold the event loop indefinitely. It's currently untested. A regression here could cause the server to hang in production with no obvious cause.

### 4.4 CI doesn't run ruff with `--fix` or format checking [Low Impact]

**Current state:** The CI workflow runs `ruff check .` but doesn't run `ruff format --check` or configure ruff format rules in `pyproject.toml`.

**Suggested change:** Add `ruff format --check .` to the CI lint job and configure formatting rules in `pyproject.toml` under `[tool.ruff.format]`.

**Why:** Consistent formatting reduces noise in diffs and reviews. Ruff's formatter is fast and already installed in CI; enabling format checking is essentially free.

### 4.5 Test count claim in README is stale [Low Impact]

**Current state:** The README states "233 tests" but the actual count may differ as tests are added/removed.

**Suggested change:** Remove the specific count or replace with "a comprehensive pytest suite."

**Why:** Stale documentation numbers erode trust. The test layout description is accurate and more useful than a count.

---

## 5. API Design

### 5.1 Flat route namespace risks collisions and lacks versioning [Medium Impact]

**Current state:** All routes are at the root level: `/health`, `/commands`, `/calendars`, `/events`, `/issues`, `/prs`, `/discord`, `/log`, etc. There's no API version prefix and no grouping by integration. The `_RESERVED_PATHS` set in `registry_routes.py` is a manual workaround for this flat namespace.

**Suggested change:** Consider an `/api/v1` prefix for all endpoints (the health endpoint is already at `/api/health`, suggesting this was partially considered). Alternatively, group by domain: `/calendar/events`, `/gitea/issues`, `/commands/discord`, etc.

**Why:** The flat namespace means every new integration must be checked against a growing reserved-paths list. Without versioning, breaking changes to the API require a full server replacement. The health endpoint already uses `/api/health`, creating an inconsistency where some endpoints are under `/api/` and most aren't. Note: the current design works because all consumers are controlled (the LLM platform reads the OpenAPI spec), but versioning would future-proof it.

### 5.2 `DELETE /branches/{name}` uses `{name:path}` but `DELETE /releases/{release_id}` uses int [Low Impact]

**Current state:** Branch deletion accepts a path parameter (`{name:path}`) allowing slashes, while release deletion uses an integer `{release_id}`. This is correct functionally but the routing patterns are inconsistent.

**Suggested change:** No change needed — this is a necessary consequence of branch names potentially containing slashes. Document the inconsistency.

**Why:** Worth noting as a conscious design decision rather than an oversight.

### 5.3 Gitea endpoints use query params for `owner`/`repo` on some routes but path params on others [Medium Impact]

**Current state:** Repository-scoped endpoints like `/repos/{owner}/{repo}/commits` take owner/repo as path parameters, but issue/branch/PR/release endpoints take them as optional query parameters (`?owner=x&repo=y`) that default to the configured values. This creates two patterns for the same concept.

**Suggested change:** Standardize. Either all repo-scoped endpoints use path params (`/repos/{owner}/{repo}/issues`) with the default-repo shortcut available as a separate route, or all use query params. The query-param approach is acceptable for the "default repo" use case, but then `/repos/{owner}/{repo}/commits` should also accept query params for consistency.

**Why:** Two patterns for the same concept is confusing for API consumers and the LLM. The LLM has to learn both patterns and may use the wrong one. Consistency in API design reduces the cognitive load on the model and improves tool-call accuracy.

### 5.4 `client.py` `compare()` method constructs URL with string interpolation [Low Impact]

**Current state:** The `compare()` method in `client.py` builds the URL path manually: `f"/repos/{owner or ''}/{repo or ''}/compare?base={base}&head={head}"`. If `owner` or `repo` is empty, this produces a malformed path like `/repos///compare?...`.

**Suggested change:** Use httpx's params argument and handle empty owner/repo by falling back to defaults, matching the pattern used by other Gitea client methods.

**Why:** The manual URL construction bypasses httpx's URL encoding, making it vulnerable to injection if `base` or `head` contain special characters. It also doesn't handle the default-owner/repo fallback that other methods use.

---

## 6. Configuration Management

### 6.1 Configuration scattered across `os.environ.get()` calls [Medium Impact]

**Current state:** Environment variables are read in multiple places: `auth.py` reads `MCP_API_KEY` at import time, `registry.py` reads `MCP_REGISTRY_DIR` at import time, `caldav_models.py` reads CalDAV vars in `CalDAVConfig.from_env()`, `gitea_models.py` reads Gitea vars in `GiteaConfig.from_env()`, and `log.sh` reads `MCP_LOG_FILE`/`MCP_LOG_DIR` at runtime. There's no centralized configuration object.

**Suggested change:** Create a `config.py` module (or use Pydantic Settings) that loads all environment variables into a single validated settings object. Pass this object to services via dependency injection.

**Why:** A centralized config makes it easy to: validate all settings at startup (fail fast on missing required values), document all environment variables in one place, test with different configurations, and add new settings without scattering `os.environ.get()` calls. Pydantic Settings (`pydantic-settings` package) would integrate naturally with the existing Pydantic models and provide type validation, defaults, and `.env` file support out of the box.

### 6.2 `CALDAV_EDITABLE_CALENDAR` defaults to "Lyra" [Low Impact]

**Current state:** The default value for the editable calendar name is hardcoded to "Lyra" in `caldav_models.py` — a personal/person-specific default.

**Suggested change:** Change the default to an empty string or a generic name like "default", and require it to be set when `CALDAV_URL` is configured. Or document that "Lyra" is just a development default and must be overridden.

**Why:** A personal calendar name as a code default is confusing for new users. If someone sets up CalDAV without specifying the editable calendar, they'll get a cryptic "Editable calendar 'Lyra' not found" error.

### 6.3 Docker healthcheck hits `/health` but app defines `/api/health` [Medium Impact]

**Current state:** `docker-compose.yml` has `test: ["CMD", "curl", "-sf", "http://localhost:8000/health"]`, but `main.py` defines the health endpoint at `/api/health`. There is no `/health` route.

**Suggested change:** Change the healthcheck URL to `http://localhost:8000/api/health`, or add a `/health` redirect/alias.

**Why:** The healthcheck is currently broken — it will always fail (404), meaning Docker will consider the container unhealthy after the start period. This affects `restart: unless-stopped` behavior and any orchestration that checks health status.

---

## 7. Docker & Deployment

### 7.1 `.dockerignore` excludes `pyproject.toml` [Medium Impact]

**Current state:** `.dockerignore` lists `pyproject.toml`, but the Dockerfile only uses `requirements.txt` for installation. However, `pyproject.toml` defines the package metadata and ruff configuration. If anyone ever does `pip install -e .` inside the container (e.g., for development), it will fail.

**Suggested change:** Remove `pyproject.toml` from `.dockerignore`, or add a comment explaining it's intentionally excluded because the Dockerfile uses `requirements.txt` only.

**Why:** Excluding `pyproject.toml` means the Docker image can't be used for development or testing (the CI workflow installs with `pip install -e ".[dev]"` which requires `pyproject.toml`). The `tests/` directory is also excluded, so the image is production-only — which is fine, but should be documented.

### 7.2 No health check for the Gitea/CalDAV service availability [Low Impact]

**Current state:** The `/api/health` endpoint always returns `{"status": "healthy"}` regardless of whether CalDAV or Gitea backends are reachable.

**Suggested change:** Add a `/api/health/ready` endpoint that checks backend connectivity, or include backend status in the health response: `{"status": "healthy", "caldav": "configured", "gitea": "configured"}`.

**Why:** For production deployments, a liveness probe that always returns "healthy" doesn't provide actionable information. A readiness check that verifies backend connectivity would help operators distinguish "server is up" from "server can actually serve calendar/git requests."

### 7.3 No graceful shutdown handling [Low Impact]

**Current state:** The Dockerfile uses `tini` as PID 1 and uvicorn as the command. There's no explicit shutdown signal handling for the CalDAV/Gitea service clients (e.g., closing the `httpx.Client` in `GiteaService`).

**Suggested change:** Add a FastAPI `lifespan` context manager that closes service connections on shutdown.

**Why:** While `httpx.Client` will eventually be garbage-collected, explicit cleanup ensures in-flight requests are properly terminated and connections are released. This matters more for the Gitea service (which holds an `httpx.Client` with connection pooling) than for CalDAV (which creates a new client per connection).

---

## 8. Code Quality & Maintainability

### 8.1 `has_default` property conflates "explicitly set" with "not None" [Medium Impact]

**Current state:** In `models.py`, `ArgSpec.has_default` returns `self.default is not None`. This means an argument with `default: 0` or `default: ""` or `default: False` is treated as "has a default" — which is correct. However, an argument that genuinely has no default also has `default = None`, which is indistinguishable from `default: null` in YAML.

**Suggested change:** Use a sentinel value (e.g., `UNSET = object()`) instead of `None` for "no default specified." The code already references this idea in a comment (`#: Sentinel used by CommandSchema to distinguish...`) but never implements it.

**Why:** The comment in `registry.py` acknowledges this issue but the sentinel was never implemented. If someone explicitly sets `default: null` in YAML (perhaps to override a previously-set default), it's silently treated as "no default." Using a proper sentinel makes the distinction explicit and removes the ambiguity.

### 8.2 Duplicate `write_registry_file` helper in conftest.py and test_registry.py [Low Impact]

**Current state:** The `write_registry_file()` helper function is duplicated nearly identically in both `conftest.py` and `test_registry.py`.

**Suggested change:** Keep only the `conftest.py` version and import it in `test_registry.py`, or move it to a `tests/helpers.py` module.

**Why:** Code duplication means fixes must be applied in two places. The `test_registry.py` version explicitly says it "mirrors the helper in conftest.py so this module is self-contained," but test modules should share fixtures through conftest, not duplicate them.

### 8.3 README has duplicate "## Logging" headers and stale content [Low Impact]

**Current state:** The README has two `## Logging` headers — one near the top (empty, just a header) and one later with actual content. The project layout section references `test_caldav.py` but not `test_gitea.py`. The test count "233" may be stale.

**Suggested change:** Remove the empty `## Logging` header. Update the project layout to include `test_gitea.py`, `gitea_models.py`, `gitea_service.py`, `gitea_routes.py`, and `registry_routes.py`. Update or remove the test count.

**Why:** Documentation accuracy builds confidence. The empty header is likely a leftover from editing. The missing Gitea files in the layout suggest the README wasn't fully updated when the Gitea integration was added.

### 8.4 `planning.md` and `implementation.md` are stale [Low Impact]

**Current state:** These files describe the original design with `POST /execute` (since removed), reference `hello.yaml` and `list_files.yaml` (since removed), and don't mention CalDAV, Gitea, or the registry routes system.

**Suggested change:** Either archive them in a `docs/history/` directory with a note, or update them to reflect the current architecture. The `plans/` directory serves the forward-looking planning role better.

**Why:** Stale design documents mislead anyone reading the codebase for the first time. They describe a `POST /execute` endpoint that no longer exists and don't mention major features (CalDAV, Gitea, native routes). The README is the current source of truth; these files create confusion.

### 8.5 No type checking (mypy/pyright) in CI [Low Impact]

**Current state:** The codebase uses type hints extensively but there's no static type checker in the CI pipeline. Ruff handles linting but not type checking.

**Suggested change:** Add `mypy` or `pyright` to the CI pipeline, even in a non-strict mode initially.

**Why:** The codebase already uses modern Python type hints (`str | None`, `dict[str, Any]`, etc.). A type checker would catch issues like the `client.py` `compare()` method accepting `owner: str | None` but then interpolating it directly into a URL string. The investment is low since types are already written.

---

## 9. CalDAV & Gitea Integration Architecture

### 9.1 CalDAV calendar cache never expires [Low Impact]

**Current state:** `_calendars_cache` in `CalDAVService` is populated once and never invalidated except by `_reset_connection()` (which only fires on `DAVError`). If a calendar is added or renamed on the server, the MCP server won't see it until restarted or until a connection error triggers a reset.

**Suggested change:** Add a TTL to the calendar cache (e.g., 5 minutes) or provide a manual refresh mechanism.

**Why:** For a long-running server, a stale calendar cache means new calendars created outside the MCP server are invisible. This is a usability issue rather than a correctness bug, but it could confuse users who add a calendar and don't see it appear.

### 9.2 Gitea `list_actions` silently returns empty list on API failure [Low Impact]

**Current state:** In `gitea_service.py`, `list_actions()` catches `GiteaError` and returns an empty list with a log warning. The route handler then returns this as a normal 200 response.

**Suggested change:** Distinguish between "Actions API not available on this Gitea version" (return empty list with a note) and "Actions API failed due to error" (return 502).

**Why:** Silently returning an empty list on error means the LLM can't distinguish "no CI runs" from "CI API is broken." This could lead to incorrect conclusions about build status.

### 9.3 Gitea `create_pr_comment` uses issues endpoint instead of PRs endpoint [Low Impact]

**Current state:** In `gitea_service.py`, `create_pr_comment()` posts to `/repos/{owner}/{repo}/issues/{index}/comments` rather than `/repos/{owner}/{repo}/pulls/{index}/comments`. This works because Gitea treats PRs as a subset of issues for commenting, but it's semantically surprising.

**Suggested change:** This is technically correct for Gitea's API (PR comments use the issues comment endpoint). Add a code comment explaining why.

**Why:** Without the comment, a reader might think this is a bug and "fix" it to use the pulls endpoint, which could break functionality. Documenting the Gitea API quirk prevents well-intentioned mistakes.

### 9.4 No connection pooling or retry logic for Gitea httpx client [Low Impact]

**Current state:** The `GiteaService` creates a single `httpx.Client` with a 30-second timeout but no retry logic, connection pool limits, or circuit breaker.

**Suggested change:** Configure `httpx.Client` with explicit pool limits (`max_connections`, `max_keepalive_connections`) and consider adding retry logic for transient failures (5xx, connection errors).

**Why:** For a server that may make many sequential Gitea API calls (e.g., listing issues, then PRs, then branches), connection reuse improves performance. Without retries, a single transient Gitea 502 causes a full operation failure.

---

## 10. Documentation

### 10.1 No API documentation beyond README [Low Impact]

**Current state:** The README is comprehensive but manually maintained. The OpenAPI schema is available at `/openapi.json` but there's no rendered documentation.

**Suggested change:** Consider enabling FastAPI's built-in `/docs` (Swagger UI) and `/redoc` endpoints, at least in development mode. They're disabled by default if not explicitly configured.

**Why:** While the LLM consumes the OpenAPI schema directly, human developers benefit from interactive API documentation during development and debugging. FastAPI provides this for free — it just needs to not be disabled.

### 10.2 `.env.example` doesn't document all environment variables [Low Impact]

**Current state:** `.env.example` covers Discord, server, logging, CalDAV, and Gitea vars, but doesn't mention `MCP_REGISTRY_DIR` (used in `registry.py`) or the planned `MCP_AUTH_HEADER`/`MCP_AUTH_SCHEME` from the configurable auth header plan.

**Suggested change:** Add all environment variables to `.env.example`, even internal ones like `MCP_REGISTRY_DIR`.

**Why:** Complete environment documentation helps operators understand all configuration knobs without reading source code.

---

## Prioritization Summary

| Priority | Findings |
|----------|----------|
| **High** | 1.1 (global singletons), 2.4 (`.env` with credentials), 4.1 (missing registry_routes tests), 6.3 (broken Docker healthcheck) |
| **Medium** | 1.2 (import-time side effects), 1.4 (client.py consistency), 2.1 (API key at import time), 2.2 (no rate limiting), 2.3 (error message leakage), 3.1 (broad exception catching), 4.2 (auth test fragility), 4.3 (untested process-group kill), 5.1 (flat route namespace), 5.3 (inconsistent Gitea owner/repo pattern), 6.1 (scattered config), 7.1 (`.dockerignore` excludes pyproject.toml), 8.1 (`has_default` sentinel) |
| **Low** | 1.3 (version mismatch), 1.5 (`_parse_repo` placement), 2.5 (no CORS), 3.2 (CalDAV recovery scope), 3.3 (Gitea timeout handling), 4.4 (no format check in CI), 4.5 (stale test count), 5.2 (DELETE route patterns), 5.4 (client compare() URL), 6.2 (Lyra default), 7.2 (health/ready endpoint), 7.3 (graceful shutdown), 8.2 (duplicate test helper), 8.3 (README issues), 8.4 (stale planning docs), 8.5 (no type checker), 9.1–9.4 (CalDAV/Gitea edge cases), 10.1–10.2 (documentation) |

---

*Review conducted by a thorough design review agent. Each finding is intended as a constructive suggestion, not a mandate — the project's overall quality is solid and these are refinements, not fundamental flaws.*
