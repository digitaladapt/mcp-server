# MCP Server – Modular Command Provider

A **FastAPI** server that exposes arbitrary terminal commands — plus
CalDAV calendars, ICS feeds, Gitea repositories, and notification
providers — as reusable tools for a language model.  CLI programs are
registered by dropping a YAML file into `registry/`; integrations are
enabled by setting environment variables.  The model discovers
available tools via the OpenAPI schema and calls them through typed
HTTP endpoints.

## Why

- **Language-agnostic** – wrap any script, binary, or compiled program.
- **Self-describing** – each command carries a JSON schema of its args.
- **Discoverable** – `GET /commands` lists everything; OpenAPI at `/openapi.json`.
- **Secure execution** – arguments are validated against the schema before
  the command is ever run; a 30 s timeout prevents hangs.
- **Conditional registration** – endpoints only exist when their backing
  service is configured.  The LLM never sees routes that would return 503.
- **Optional API key** – set `MCP_API_KEY` to require authentication on all
  endpoints except `/api/health` and `/api/about`.

## Quick start

```bash
cd ~/projects/mcp-server
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Optional: set an API key to secure the server
export MCP_API_KEY="your-secret-key"

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The server now listens on `http://127.0.0.1:8000`.

If `MCP_API_KEY` is set, all endpoints except `/api/health` and `/api/about`
require an `X-API-Key` header matching the key.  If unset, the server runs
open (suitable for local development or trusted networks).

> **Startup safety:** if nothing is configured (no calendar providers, no
> Gitea, no notify providers, no weather, and no registry commands), the
> server refuses to start.  At least one feature must be enabled.

## Architecture

The server uses a **factory pattern** (`create_app()`) that inspects
environment variables at startup and conditionally registers routers
for each configured integration.  This means the OpenAPI schema only
contains endpoints that will actually work — the LLM never discovers
routes that would return 503.

### Provider system

Calendar integrations (CalDAV and ICS) are implemented as **providers**
that implement a common protocol.  A global `provider_registry` holds all
active providers.  The **unified router** (`unified_routes.py`) exposes
`/events`, `/calendars`, and (when ICS is configured) `/calendars/refresh`
across all providers.  Write operations (create/update/delete events) are
only registered when an editable provider exists (i.e. CalDAV with
`CALDAV_EDITABLE_CALENDAR` set).

### Background jobs

A lightweight job scheduler (`jobs.py`) runs periodic background tasks
during the app's lifespan.  Currently used for ICS cache refresh.  Job
status is visible at `GET /jobs`.

## API

Endpoints are conditionally registered based on configuration.  The
table below shows all possible endpoints; only those for configured
features will be present.

### Core (always present)

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/api/health`        | Liveness probe (no auth required)            |
| GET    | `/api/about`         | App name & version (no auth required)         |
| GET    | `/commands`          | List all registered commands                 |
| GET    | `/commands/{name}`   | Retrieve one command's schema                |
| GET    | `/validate`          | Validate all registry files (detailed report) |
| GET    | `/jobs`              | List status of periodic background jobs      |
| POST   | `/{command}`         | Dedicated route per registry command (auto-gen) |

### Calendar (when CalDAV or ICS is configured)

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/events`            | List events across all calendar providers    |
| GET    | `/events/{uid}`      | Get a single event by UID                    |
| GET    | `/calendars`         | List accessible calendars with metadata      |
| POST   | `/calendars/refresh` | Refresh ICS cache (when ICS configured)      |
| POST   | `/events`            | Create an event (only if editable provider)  |
| PUT    | `/events/{uid}`      | Update an event (only if editable provider)  |
| DELETE | `/events/{uid}`      | Delete an event (only if editable provider)  |

### CalDAV Tasks (when CalDAV is configured)

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/tasks`             | List calendar tasks (VTODO)                  |
| GET    | `/tasks/{uid}`       | Get a single task by UID                     |
| POST   | `/tasks`             | Create a task (only if editable provider)    |
| PUT    | `/tasks/{uid}`       | Update a task (only if editable provider)    |
| DELETE | `/tasks/{uid}`       | Delete a task (only if editable provider)    |

### Gitea (when `GITEA_URL` is set)

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/repos/{owner}/{repo}` | Get repository info                      |
| GET    | `/user/repos`        | List accessible repositories                 |
| GET    | `/repos/{owner}/{repo}/commits` | List recent commits                |
| GET    | `/repos/{owner}/{repo}/compare` | Compare two refs (extra tools)      |
| GET    | `/issues`            | List issues (default repo or owner/repo)     |
| GET    | `/issues/{index}`   | Get a single issue by number                  |
| POST   | `/issues`            | Create a new issue                           |
| PATCH  | `/issues/{index}`   | Update an issue (e.g. close it)              |
| GET    | `/issues/{index}/comments` | List comments on an issue             |
| POST   | `/issues/{index}/comments` | Comment on an issue                  |
| GET    | `/branches`          | List branches (default repo or owner/repo)   |
| POST   | `/branches`          | Create a new branch                          |
| DELETE | `/branches/{name}`   | Delete a branch                              |
| GET    | `/prs`               | List pull requests                           |
| POST   | `/prs`               | Create a pull request                        |
| GET    | `/prs/{index}`       | Get a single PR                              |
| PATCH  | `/prs/{index}`       | Update a PR (e.g. close it)                  |
| POST   | `/prs/{index}/merge` | Merge a pull request                         |
| GET    | `/prs/{index}/reviews` | List reviews on a PR (extra tools)        |
| POST   | `/prs/{index}/comments` | Comment on a PR                           |
| GET    | `/actions`           | List CI workflow runs                        |
| GET    | `/commits/{sha}/statuses` | Get CI status checks (extra tools)     |
| GET    | `/releases`          | List releases                                |
| POST   | `/releases`          | Create a release                             |
| GET    | `/releases/{release_id}` | Get a single release                     |
| PATCH  | `/releases/{release_id}` | Update a release                         |
| DELETE | `/releases/{release_id}` | Delete a release                         |

> **Extra tools:** `/repos/.../compare`, `/prs/{index}/reviews`, and
> `/commits/{sha}/statuses` are hidden from the OpenAPI schema by default
> to reduce token count.  Set `MCP_GITEA_EXTRA_TOOLS=1` to expose them.

### Notify (when Discord or Ntfy is configured)

| Method | Path       | Description                                  |
|--------|------------|----------------------------------------------|
| POST   | `/notify`  | Send a notification to configured providers  |

### Weather (when `WEATHER_LOCATION` is set)

| Method | Path       | Description                                  |
|--------|------------|----------------------------------------------|
| GET    | `/weather` | Current conditions and multi-day forecast    |

### Example

```bash
# List available commands
curl http://127.0.0.1:8000/commands

# Execute the `log` command (dedicated route — the only way to run it)
curl -X POST http://127.0.0.1:8000/log \
     -H 'Content-Type: application/json' \
     -d '{"message": "Server started"}'
```

Response:
```json
{"stdout": "[2026-01-15T10:30:00-0500] [INFO] Server started\n", "stderr": "", "exit_code": 0, "success": true}
```

If an API key is set, include it in the header:
```bash
curl -H "X-API-Key: your-secret-key" http://127.0.0.1:8000/commands
```

## Validating the registry

Before restarting the server after editing registry files, you can
validate them — like `caddy validate` does for Caddy's config.

### CLI

```bash
python -m app.validate
```

Optionally pass a custom registry directory:

```bash
python -m app.validate /path/to/registry
```

Output:
```
MCP Server registry validation: /app/registry

  ✓ log.yaml → log
  ✓ log_read.yaml → log_read
  ✗ broken.yaml: mapping values are not allowed here
  ⚠ noprogram.yaml → noprogram: Executable not found: /usr/bin/nonexistent

  4 file(s) checked · 1 error(s) · 1 warning(s)

  Registry has errors — fix them before restarting.
```

Exit codes:
- `0` — all files valid (warnings are OK)
- `1` — one or more files have errors
- `2` — registry directory does not exist

### HTTP

```bash
curl http://127.0.0.1:8000/validate
```

Returns a JSON report with per-file results, including duplicate name
detection and executable existence checks.

## Registering a command

Create a file in `registry/` (e.g. `my_tool.yaml`):

```yaml
name: my_tool
description: Does something useful.
executable: /usr/local/bin/my_tool
# (relative paths like scripts/my_tool.sh are resolved against
#  the project root, so they work in any clone or Docker image)
args:
  - name: input
    type: string
    required: true
    help: Path to the input file.
  - name: --verbose
    type: flag
    required: false
    help: Enable verbose output.
  - name: --mode
    type: string
    required: false
    choices: [fast, slow]
    help: Execution mode.
```

### Argument spec fields

| Field        | Type   | Notes                                              |
|--------------|--------|----------------------------------------------------|
| `name`       | string | Positional placeholder or `--flag` name.           |
| `type`       | string | `string`, `int`, `float`, `bool`, or `flag`.       |
| `required`   | bool   | Default `false`.                                   |
| `choices`    | list   | Optional allowed-value whitelist.                  |
| `default`    | any    | Optional default value, auto-applied when the arg  |
|              |        | is omitted by the caller.                           |
| `help`       | string | Human-readable description.                        |
| `field_name` | string | Optional clean name for the native tool parameter. |
|              |        | When set, this becomes the OpenAPI property name   |
|              |        | (e.g. `title` instead of `-t`).  The original      |
|              |        | `name` is still used as the CLI flag.              |
| `hidden`     | bool   | When `true`, the arg is invisible in the tool      |
|              |        | surface but always applied with its `default`      |
|              |        | value.  Use for flags that must always be passed   |
|              |        | but should never be controllable by the model.     |

A `flag` type means presence-only (no value); the flag name is appended to
the command line when the argument is truthy.

### Conditional commands (`requires`)

Commands can declare a `requires` list of environment-variable
conditions.  If the conditions are unmet, the command is loaded but its
route is not registered (it won't appear in `GET /commands`).

```yaml
requires:
  - "MCP_LOG_ENABLED != false"
```

This is used by `log` and `log_read` to disappear when logging is
disabled via `MCP_LOG_ENABLED=false`.

### Defaults

Any argument may carry a `default` value.  When the caller omits that
argument, the executor fills it in automatically — useful for forcing
flags that should always be on (e.g. `discord.sh -q` for quiet mode):

```yaml
args:
  - name: -q
    type: flag
    default: true
    help: Quiet mode — forced on by default.
```

## Native routes for registry commands

Each command defined in `registry/` is automatically exposed as its own
dedicated FastAPI route — `POST /{command_name}` — with a Pydantic
request model generated from the YAML arg specs.  This means the
platform can read the OpenAPI schema and surface each command as a
**native tool** with properly typed parameters (strings, enums, flags,
defaults).

These dedicated routes are the *only* way to execute registry commands —
there is no generic `POST /execute` endpoint.  Registry files still feed
`GET /commands` and `GET /validate` so you can discover and inspect
commands, but execution happens through the typed per-command routes
only.

Unknown fields are rejected (`extra: forbid`) with a 422 response, and
missing required arguments also return 422.

The `field_name` YAML key controls the parameter name shown to the model.
When omitted, the arg `name` is used (with leading dashes stripped).

If a registry command's name collides with an existing route (e.g.
`events`, `issues`), the dedicated route is skipped with a warning and
the command cannot be executed over HTTP (it still appears in
`GET /commands`).  Rename the command in the registry to enable
execution.

## Client library

A small synchronous `httpx`-based client lives in `app/client.py`.  It
mirrors the HTTP API so a model or script can treat each registered
command as a native Python callable.

```python
from app.client import MCPClient

mc = MCPClient("http://127.0.0.1:8000", api_key="your-secret-key")

# Discover available commands
for cmd in mc.list_commands():
    print(cmd["name"], "-", cmd["description"])

# Execute a command
result = mc.execute("log", message="Server started")
print(result["stdout"])

# Bind a command to a reusable callable
log = mc.tool("log")
log(message="Deploy complete")
```

Flag names that start with `-` aren't valid Python identifiers, so pass
them via dict unpacking: `**{"-c": "green"}`.

If the server has `MCP_API_KEY` set, pass `api_key=` to the client —
it will be sent as `X-API-Key` on every request.

The client also works as a context manager:

```python
with MCPClient() as mc:
    mc.execute("log_read", lines="10")
```

The client also provides typed convenience methods for the calendar,
task, and Gitea APIs (`list_events`, `create_task`, `list_issues`, etc.).

## Project layout

```
mcp-server/
├─ app/
│   ├─ __init__.py            # package marker, resolves version via importlib.metadata
│   ├─ main.py                # FastAPI app factory + conditional router registration
│   ├─ auth.py                # API key authentication dependency
│   ├─ models.py              # Pydantic schemas (commands, args, validation)
│   ├─ executor.py            # validation + subprocess wrapper with timeout
│   ├─ registry.py            # YAML/JSON command loader + validate_registry()
│   ├─ validate.py            # `python -m app.validate` CLI
│   ├─ client.py              # httpx client library (commands + calendar + Gitea API)
│   ├─ registry_routes.py     # Auto-generated native routes for registry commands
│   ├─ caldav_models.py       # Pydantic models for CalDAV events/tasks
│   ├─ caldav_service.py      # CalDAV service (1 editable + N read-only calendars)
│   ├─ caldav_routes.py       # FastAPI router for /tasks (CalDAV-specific)
│   ├─ ics_models.py          # Pydantic models for ICS feed config
│   ├─ ics_service.py         # ICS feed fetcher, parser, cache
│   ├─ ics_routes.py          # ICS service singleton management
│   ├─ unified_routes.py      # Unified /events, /calendars router across providers
│   ├─ provider_adapters.py   # CalDAVProvider, ICSProvider adapters
│   ├─ providers.py           # Global provider registry
│   ├─ gitea_models.py        # Pydantic models for Gitea resources
│   ├─ gitea_service.py       # Gitea API service (issues, PRs, branches, releases)
│   ├─ gitea_routes.py        # FastAPI router for /issues, /prs, /branches, etc.
│   ├─ notify_models.py       # Pydantic models for notifications
│   ├─ notify_service.py      # Discord + Ntfy notify providers
│   ├─ notify_routes.py       # FastAPI router for /notify
│   ├─ weather_models.py      # Pydantic models for weather config
│   ├─ weather_service.py     # Open-Meteo API client
│   ├─ weather_routes.py      # FastAPI router for /weather
│   └─ jobs.py                # Lightweight background job scheduler
├─ registry/                  # command definitions (one file per command)
│   ├─ log.yaml               # logging command
│   └─ log_read.yaml          # read log tail
├─ scripts/                   # helper scripts referenced by registry YAMLs
│   ├─ log.sh                 # append to log file
│   ├─ log_read.sh            # read log tail
│   └─ config.sh.example      # template (unused in Docker; for reference)
├─ tests/                     # pytest test suite
│   ├─ conftest.py
│   ├─ test_models.py
│   ├─ test_executor.py
│   ├─ test_registry.py
│   ├─ test_api.py
│   ├─ test_client.py
│   ├─ test_auth.py
│   ├─ test_caldav.py
│   ├─ test_ics.py
│   ├─ test_ics_recurrence.py
│   ├─ test_gitea.py
│   ├─ test_notify.py
│   ├─ test_weather.py
│   ├─ test_logging.py
│   ├─ test_jobs.py
│   └─ test_conditional_endpoints.py
├─ Dockerfile                 # multi-arch base image definition
├─ LICENSE                    # MIT license
├─ variants/                  # variant Dockerfiles (PHP, Node, etc.)
│   ├─ Dockerfile.php
│   └─ Dockerfile.node
├─ docker-compose.yml         # easy local run with volumes
├─ .env.example               # environment variable template
├─ .dockerignore              # excludes venv, secrets, tests, etc.
├─ pyproject.toml             # package metadata + pytest/ruff config
└─ requirements.txt           # pip dependencies (used by Dockerfile)
```

## Configuration

All configuration is via environment variables.  See `.env.example`
for a complete reference with comments.  The server reads these at
startup and conditionally registers endpoints.

| Variable                       | Feature        | Description                                    |
|--------------------------------|----------------|------------------------------------------------|
| `MCP_API_KEY`                  | Auth           | API key for endpoints (unset = open access)    |
| `MCP_REGISTRY_DIR`             | Registry       | Custom registry directory                      |
| `MCP_LOG_FILE`                 | Logging        | Log file path                                  |
| `MCP_LOG_DIR`                  | Logging        | Log directory (file is `mcp.log` inside)       |
| `MCP_LOG_LEVEL`                | Logging        | Log level (default: INFO)                      |
| `MCP_LOG_ENABLED`              | Logging        | Set to `false` to disable log commands         |
| `CALDAV_URL`                   | CalDAV         | CalDAV server URL                              |
| `CALDAV_USERNAME`              | CalDAV         | CalDAV username                                |
| `CALDAV_PASSWORD`              | CalDAV         | CalDAV password                                |
| `CALDAV_EDITABLE_CALENDAR`     | CalDAV         | Editable calendar name (unset = all read-only) |
| `CALDAV_READONLY_CALENDARS`    | CalDAV         | Comma-separated read-only calendar names       |
| `ICS_CALENDAR_URL`             | ICS            | Read-only ICS feed URL                         |
| `ICS_CALENDAR_NAME`            | ICS            | Display name for ICS feed                      |
| `ICS_REFRESH_INTERVAL`         | ICS            | Cache refresh interval in seconds (default 300)|
| `GITEA_URL`                    | Gitea          | Gitea server URL                               |
| `GITEA_TOKEN`                  | Gitea          | API token                                      |
| `GITEA_DEFAULT_OWNER`          | Gitea          | Default repo owner                             |
| `GITEA_DEFAULT_REPO`           | Gitea          | Default repo name                              |
| `MCP_GITEA_EXTRA_TOOLS`        | Gitea          | Expose niche endpoints in OpenAPI schema       |
| `DISCORD_*_HOOK`               | Notify         | Discord webhook URLs (per severity level)      |
| `DISCORD_SERVER_NAME`          | Notify         | Bot display name override                      |
| `DISCORD_TITLE_SUFFIX`         | Notify         | Title suffix for Discord messages              |
| `NTFY_URL`                     | Notify         | Ntfy server URL                                |
| `NTFY_*_TOPIC`                 | Notify         | Ntfy topics (per severity level)               |
| `NTFY_TOKEN`                   | Notify         | Ntfy access token                              |
| `NTFY_USERNAME` / `NTFY_PASSWORD` | Notify      | Ntfy basic auth                                |
| `NTFY_TITLE_SUFFIX`            | Notify         | Title suffix for ntfy messages                 |
| `WEATHER_LOCATION`             | Weather        | "lat,long" for weather data                    |
| `TZ`                           | Server         | Timezone (defaults to UTC)                     |

## CalDAV Calendar

The server can connect to a CalDAV server (e.g. Radicale, Baikal,
Nextcloud) to manage calendar events and tasks.  The design uses **one
editable calendar** (where events and tasks can be created, updated, and
deleted) and **multiple read-only calendars** (visible but not writable).

When `CALDAV_EDITABLE_CALENDAR` is not set, all calendars are read-only
and no create/update/delete endpoints are registered.

All events and tasks carry an `editable` flag and `calendar_name`, so the
model can see the full unified calendar view but is isolated from
accidentally modifying calendars it shouldn't touch.

### Configuration

```
CALDAV_URL=https://caldav.example.com/dav
CALDAV_USERNAME=user
CALDAV_PASSWORD=secret
# Optional: set to make a calendar writable.  When unset, all calendars
# are read-only and write endpoints are not registered.
#CALDAV_EDITABLE_CALENDAR=MyCalendar
# Optional: comma-separated list of read-only calendar names to include.
# If empty, all calendars except the editable one are included as read-only.
#CALDAV_READONLY_CALENDARS=Personal,Work
```

When `CALDAV_URL` is not set, calendar endpoints are not registered.

### Features

- **Events (VEVENT):** list (with date-range filtering), get by UID,
  create, update, delete — all-day and timed events supported.
- **Tasks (VTODO):** list, get by UID, create, update, delete — with
  priority, due date, and status management.
- **Connection recovery:** if the CalDAV server becomes unreachable
  mid-operation, the service automatically resets its connection and
  retries once.  Catches `DAVError`, `ConnectionError`, `TimeoutError`,
  and `OSError`.
- **Calendar caching:** the calendar list is fetched once per connection
  and cached, avoiding redundant server round-trips.
- **Explicit UUIDs:** created events and tasks always get a `uuid4` UID,
  guaranteeing they can be updated or deleted immediately after creation.

## ICS Calendar (read-only)

The server can merge a read-only ICS calendar feed (e.g. Outlook
published calendar, Google Calendar iCal) into the unified `/events`
endpoint alongside CalDAV events.

```
ICS_CALENDAR_URL=https://outlook.office365.com/owa/calendar/.../calendar.ics
ICS_CALENDAR_NAME=Work
ICS_REFRESH_INTERVAL=300  # seconds (default 300, minimum 30)
```

The ICS feed is fetched and cached on startup, then refreshed
periodically by a background job.  Use `POST /calendars/refresh` to
manually trigger a cache refresh.

## Gitea Integration

The server can connect to a Gitea instance to manage repositories,
issues, pull requests, branches, releases, and CI actions. When
`GITEA_URL` is not set, Gitea endpoints are not registered.

### Configuration

```
GITEA_URL=https://git.example.com
GITEA_TOKEN=your-api-token
GITEA_DEFAULT_OWNER=your-username
GITEA_DEFAULT_REPO=your-repo
```

Issue, branch, PR, and release endpoints accept optional `owner` and
`repo` query parameters that default to the configured values. Repository
info, commits, and compare endpoints use path parameters
(`/repos/{owner}/{repo}/...`).

## Notify

The server can send notifications via Discord webhooks and/or Ntfy.
Multiple providers can be active simultaneously — a `/notify` call fans
out to all configured providers.

Discord webhooks are configured per severity level (`info`, `notice`,
`critical`, `emergency`).  If a level isn't configured, the system falls
back to the nearest lower configured level.

Ntfy works similarly with topics per severity level.  Authentication
supports either token-based or basic auth.

## Logging

The `log` and `log_read` commands provide a simple logging utility —
append timestamped messages to a file and read them back.

```bash
# Log a message
curl -X POST http://127.0.0.1:8000/log \
     -H 'Content-Type: application/json' \
     -d '{"message": "Deploy complete"}'

# Log with a level
curl -X POST http://127.0.0.1:8000/log \
     -H 'Content-Type: application/json' \
     -d '{"message": "Disk full", "level": "error"}'

# Read the last 20 lines
curl -X POST http://127.0.0.1:8000/log_read \
     -H 'Content-Type: application/json' \
     -d '{"lines": "20"}'
```

The log file path is determined by (in priority order):

1. `MCP_LOG_FILE` environment variable — full path to the log file.
2. `MCP_LOG_DIR` environment variable — directory; file is `mcp.log` inside.
3. Default: `/tmp/mcp/mcp.log`.

Parent directories are created automatically if they don't exist.

Set `MCP_LOG_ENABLED=false` to disable logging entirely — the `log` and
`log_read` commands won't be registered and their routes won't exist.

## Docker

The server ships with a multi-arch Dockerfile ready for `amd64` and
`arm64`.

### Build

```bash
docker build -t digitaladapt/mcp-server:latest .
```

For multi-arch builds (requires `buildx`):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t digitaladapt/mcp-server:latest .
```

### Run

```bash
docker run -d --name mcp-server -p 8000:8000 \
  --env-file .env \
  -e MCP_API_KEY="your-secret-key" \
  -v ./registry:/app/registry \
  digitaladapt/mcp-server:latest
```

Or with `docker compose`:

```bash
docker compose up -d
```

### Volumes

| Mount              | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| `/app/registry`    | Command definitions — override or extend at runtime.     |
| `/tmp/mcp`         | Default log file location (or set MCP_LOG_FILE).        |

The `scripts/` directory (including `log.sh`) is baked into the image.
Secrets are never baked in — provide them via environment variables
(`--env-file .env`).

### Image details

- **Base**: `python:3.12-slim` (multi-arch)
- **System deps**: `curl`, `jq` (for scripts), `tini`
- **Runs as**: non-root user `mcp` (uid 1000)
- **Entrypoint**: `tini` (proper PID-1 signal handling)

### Building variants (PHP, Node, etc.)

The base `Dockerfile` is designed as a foundation. Variant Dockerfiles live
in `variants/` and layer additional runtimes on top:

| Variant | Dockerfile | Runtime | Example commands |
|---------|------------|---------|------------------|
| PHP | `variants/Dockerfile.php` | PHP CLI + curl, mbstring, xml | `php_eval` |
| Node.js | `variants/Dockerfile.node` | Node.js 22 LTS + npm | `node_run` |

**Build a variant** (from repo root):

```bash
# PHP
docker build -f variants/Dockerfile.php -t digitaladapt/mcp-server:php .

# Node.js
docker build -f variants/Dockerfile.node -t digitaladapt/mcp-server:node .
```

**Run a variant:**

```bash
docker run -p 8000:8000 \
  --env-file .env \
  -v ./registry:/app/registry \
  digitaladapt/mcp-server:php
```

**Creating your own variant:**

```dockerfile
# variants/Dockerfile.ruby
FROM digitaladapt/mcp-server:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ruby && rm -rf /var/lib/apt/lists/*
USER mcp
```

Then add a `registry/ruby_eval.yaml` pointing at `/usr/bin/ruby`.

## Testing

The project includes a comprehensive pytest suite covering models,
executor, registry, API endpoints, client library, authentication,
CalDAV operations, ICS parsing, Gitea integration, notify, weather,
logging, background jobs, and conditional endpoint registration.

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run the full suite
pytest

# Run with verbose output
pytest -v

# Run a single test module
pytest tests/test_executor.py
```

The flag-default regression (e.g. a flag with `default: true`) is
covered by `test_executor.py::TestValidateAndBuild::test_flag_default_true_*`.

The executor's timeout and process-group kill logic is tested in
`test_executor.py`.

## Security notes

- Only commands present in `registry/` can be executed — there is no
  arbitrary-command endpoint.
- Arguments are validated (type, required, choices) before the subprocess is
  spawned, and unknown arguments are rejected.
- Every command has a hard 30 s timeout with process-group kill.
- **API key authentication** — set `MCP_API_KEY` to require an `X-API-Key`
  header on all endpoints except `/api/health` and `/api/about`.  When
  unset, the server is open.
- Error messages are sanitized — internal details are logged server-side
  but not exposed in HTTP responses (important since errors flow back
  into the LLM's context window).
- Run the server under a limited user account; do not grant it sudo.
- Commands that allow introspection of the server filesystem or arbitrary
  code execution have been removed by design — only specific, allowed
  commands should be registered.

---

*Built by Lyra — your silver-haired assistant in the corner. ✨*
