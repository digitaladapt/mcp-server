# MCP Server – Modular Command Provider

A small **FastAPI** server that exposes arbitrary terminal commands as
reusable tools for a language model.  Any CLI program — regardless of the
language it's written in — can be registered by dropping a YAML (or JSON)
file into the `registry/` directory.  The model then discovers commands via
the HTTP API and executes them through a validated, sandboxed endpoint.

## Why

- **Language-agnostic** – wrap any script, binary, or compiled program.
- **Self-describing** – each command carries a JSON schema of its args.
- **Discoverable** – `GET /commands` lists everything; OpenAPI at `/openapi.json`.
- **Secure execution** – arguments are validated against the schema before
  the command is ever run; a 30 s timeout prevents hangs.
- **Optional API key** – set `MCP_API_KEY` to require authentication on all
  endpoints except `/health`.

## Quick start

```bash
cd ~/projects/mcp_server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Optional: set an API key to secure the server
export MCP_API_KEY="your-secret-key"

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The server now listens on `http://127.0.0.1:8000`.

If `MCP_API_KEY` is set, all endpoints except `/health` require an
`X-API-Key` header matching the key.  If unset, the server runs open
(suitable for local development or trusted networks).

## API

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/health`            | Liveness probe (no auth required)            |
| GET    | `/commands`          | List all registered commands                 |
| GET    | `/commands/{name}`   | Retrieve one command's schema                |
| GET    | `/validate`          | Validate all registry files (detailed report) |
| POST   | `/execute`           | Validate arguments and run a command (generic) |
| POST   | `/{command}`         | Dedicated route per registry command (auto-gen) |
| GET    | `/calendars`         | List accessible CalDAV calendars             |
| GET    | `/events`            | List calendar events (optional date range)   |
| GET    | `/events/{uid}`      | Get a single event by UID                    |
| POST   | `/events`            | Create an event (editable calendar only)     |
| PUT    | `/events/{uid}`      | Update an event (editable calendar only)     |
| DELETE | `/events/{uid}`      | Delete an event (editable calendar only)     |
| GET    | `/tasks`             | List calendar tasks (VTODO)                  |
| GET    | `/tasks/{uid}`       | Get a single task by UID                     |
| POST   | `/tasks`             | Create a task (editable calendar only)       |
| PUT    | `/tasks/{uid}`       | Update a task (editable calendar only)       |
| DELETE | `/tasks/{uid}`       | Delete a task (editable calendar only)       |

### Example

```bash
# List available commands
curl http://127.0.0.1:8000/commands

# Execute the `log` command
curl -X POST http://127.0.0.1:8000/execute \
     -H 'Content-Type: application/json' \
     -d '{"command": "log", "arguments": {"message": "Server started"}}'
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

  ✓ discord.yaml → discord
  ✓ hello.yaml → hello
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

| Field      | Type            | Notes                                              |
|------------|-----------------|----------------------------------------------------|
| `name`     | string          | Positional placeholder or `--flag` name.           |
| `type`     | string          | `string`, `int`, `float`, `bool`, or `flag`.       |
| `required` | bool            | Default `false`.                                   |
| `choices`  | list            | Optional allowed-value whitelist.                  |
| `default`  | any             | Optional default value, auto-applied when the arg  |
|            |                 | is omitted by the caller.                           |
| `help`     | string          | Human-readable description.                        |
| `field_name`| string         | Optional clean name for the native tool parameter. |
|            |                 | When set, this becomes the OpenAPI property name   |
|            |                 | (e.g. `title` instead of `-t`).  The original      |
|            |                 | `name` is still used as the CLI flag.              |

A `flag` type means presence-only (no value); the flag name is appended to
the command line when the argument is truthy.

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
defaults), rather than hiding them behind the generic `POST /execute`
endpoint.

For example, `registry/discord.yaml` generates:

```
POST /discord
  Body: discord_Request
    quiet:   boolean (default: true)   — quiet mode (maps to -q)
    alert:   boolean                   — alert mode (maps to -a)
    color:   enum[22 colors]           — embed color (maps to -c)
    title:   string                    — title (maps to -t)
    message: string (required)         — message body
```

The `field_name` YAML key controls the parameter name shown to the model.
When omitted, the arg `name` is used (with leading dashes stripped).

The generic `POST /execute` endpoint remains available as a fallback.
If a registry command's name collides with an existing route (e.g.
`events`, `issues`), the dedicated route is skipped with a warning and
the command is only accessible via `POST /execute`.

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
discord = mc.tool("discord")
discord(message="Deploy complete", **{"-c": "green", "-t": "CI"})
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

## Project layout

```
mcp_server/
├─ app/
│   ├─ __init__.py      # package marker
│   ├─ main.py          # FastAPI app + endpoints
│   ├─ auth.py          # API key authentication dependency
│   ├─ models.py        # Pydantic schemas (commands)
│   ├─ executor.py      # validation + subprocess wrapper
│   ├─ registry.py      # YAML/JSON command loader + validate_registry()
│   ├─ validate.py      # `python -m app.validate` CLI
│   ├─ client.py        # httpx client library (commands + calendar API)
│   ├─ caldav_models.py # Pydantic models for calendar events/tasks
│   ├─ caldav_service.py # CalDAV service (1 editable + N read-only calendars)
│   ├─ caldav_routes.py # FastAPI router for /calendars, /events, /tasks
│   ├─ gitea_models.py  # Pydantic models for Gitea resources
│   ├─ gitea_service.py  # Gitea API service (issues, PRs, branches, releases)
│   ├─ gitea_routes.py  # FastAPI router for /issues, /prs, /branches, etc.
│   └─ registry_routes.py # Auto-generated native routes for registry commands
├─ registry/            # command definitions (one file per command)
│   ├─ log.yaml          # logging command
│   ├─ log_read.yaml     # read log tail
│   └─ discord.yaml      # Discord webhook sender
├─ scripts/              # helper scripts referenced by registry YAMLs
│   ├─ discord.sh        # Discord webhook sender
│   ├─ log.sh            # append to log file
│   ├─ log_read.sh       # read log tail
│   └─ config.sh.example # template — copy to config.sh and fill in
├─ Dockerfile             # multi-arch base image definition
├─ variants/              # variant Dockerfiles (PHP, Node, etc.)
│   ├─ Dockerfile.php
│   └─ Dockerfile.node
├─ tests/                # pytest test suite
│   ├─ conftest.py
│   ├─ test_models.py
│   ├─ test_executor.py
│   ├─ test_registry.py
│   ├─ test_api.py
│   ├─ test_client.py
│   ├─ test_auth.py
│   └─ test_caldav.py
├─ docker-compose.yml     # easy local run with volumes
├─ .env.example           # Docker env var template (webhook URL, etc.)
├─ .dockerignore          # excludes venv, secrets, tests, etc.
├─ pyproject.toml         # package metadata + pytest config
├─ requirements.txt
├─ planning.md
└─ implementation.md
```

## Discord setup

The `discord` command wraps `scripts/discord.sh`, which sends messages to
Discord via a webhook.  To use it you need a webhook URL from your Discord
server:

**Server Settings → Integrations → Webhooks → New Webhook**

The URL must end with `?wait=true`.

### Local setup

```bash
cp scripts/config.sh.example scripts/config.sh
# Edit scripts/config.sh and set DISCORD_GENERAL_HOOK
docker compose up -d   # or uvicorn directly
```

`discord.sh` reads `config.sh` from its own directory, so the file must be
at `scripts/config.sh`.  If `config.sh` is missing, the script falls back
to environment variables (`DISCORD_GENERAL_HOOK`, etc.).

## Logging

## CalDAV Calendar

The server can connect to a CalDAV server (e.g. Radicale, Baikal, Nextcloud)
to manage calendar events and tasks.  The design uses **one editable
calendar** (where events and tasks can be created, updated, and deleted)
and **multiple read-only calendars** (visible but not writable).

All events and tasks carry an `editable` flag and `calendar_name`, so the
model can see the full unified calendar view but is isolated from
accidentally modifying calendars it shouldn't touch.

### Configuration

Set these environment variables (or uncomment in `.env`):

```
CALDAV_URL=https://caldav.example.com/dav
CALDAV_USERNAME=user
CALDAV_PASSWORD=secret
CALDAV_EDITABLE_CALENDAR=Lyra
# Optional: comma-separated list of read-only calendar names to include.
# If empty, all calendars except the editable one are included as read-only.
CALDAV_READONLY_CALENDARS=Personal,Work
```

When `CALDAV_URL` is not set, all calendar endpoints return `503 Service
Unavailable`.

### Features

- **Events (VEVENT):** list (with date-range filtering), get by UID,
  create, update, delete — all-day and timed events supported.
- **Tasks (VTODO):** list, get by UID, create, update, delete — with
  priority, due date, and status management.
- **Connection recovery:** if the CalDAV server becomes unreachable
  mid-operation, the service automatically resets its connection and
  retries once.
- **Calendar caching:** the calendar list is fetched once per connection
  and cached, avoiding redundant server round-trips.
- **Explicit UUIDs:** created events and tasks always get a `uuid4` UID,
  guaranteeing they can be updated or deleted immediately after creation.

### Client library

The `MCPClient` also provides typed convenience methods for the calendar
API:

```python
from app.client import MCPClient

mc = MCPClient("http://127.0.0.1:8000", api_key="your-secret-key")

# List calendars
cals = mc.list_calendars()
for cal in cals["calendars"]:
    print(f"  {cal['name']} {'✏️' if cal['editable'] else '👁️'}")

# List events in a date range
events = mc.list_events(start="2026-01-01", end="2026-02-01")
for ev in events["events"]:
    print(f"  {ev['start']} {ev['summary']}")

# Create an event
mc.create_event(
    summary="Team standup",
    start="2026-01-15T09:00:00",
    end="2026-01-15T09:30:00",
)

# Create a task
mc.create_task(summary="Review PR", priority=3, due="2026-01-20")

# Update a task status
mc.update_task("<uid>", status="COMPLETED")

# Delete
mc.delete_event("<uid>")
mc.delete_task("<uid>")
```

## Logging

The `log` and `log_read` commands provide a simple logging utility —
append timestamped messages to a file and read them back.

```bash
# Log a message
curl -X POST http://127.0.0.1:8000/execute \
     -H 'Content-Type: application/json' \
     -d '{"command": "log", "arguments": {"message": "Deploy complete"}}'

# Log with a level
curl -X POST http://127.0.0.1:8000/execute \
     -H 'Content-Type: application/json' \
     -d '{"command": "log", "arguments": {"message": "Disk full", "--level": "error"}}'

# Read the last 20 lines
curl -X POST http://127.0.0.1:8000/execute \
     -H 'Content-Type: application/json' \
     -d '{"command": "log_read", "arguments": {"lines": "20"}}'
```

The log file path is determined by (in priority order):

1. `MCP_LOG_FILE` environment variable — full path to the log file.
2. `MCP_LOG_DIR` environment variable — directory; file is `mcp.log` inside.
3. Default: `/tmp/mcp/mcp.log`.

Parent directories are created automatically if they don't exist.

### Docker setup

For Docker, use environment variables instead of `config.sh`:

```bash
cp .env.example .env
# Edit .env and set DISCORD_GENERAL_HOOK
docker compose up -d
```

The `.env` file is loaded by `docker-compose.yml` and the environment
variables are picked up by `discord.sh` automatically.  Only
`DISCORD_GENERAL_HOOK` is required; the rest have fallback defaults.

Alternatively, you can mount a `config.sh` file (see the commented volume
in `docker-compose.yml`).

## Docker

The server ships with a multi-arch Dockerfile ready for `amd64` and
`arm64`.

### Build

```bash
docker build -t mcp-server:latest .
```

For multi-arch builds (requires `buildx`):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t mcp-server:latest .
```

### Run

```bash
docker run -d --name mcp-server -p 8000:8000 \
  --env-file .env \
  -e MCP_API_KEY="your-secret-key" \
  -v ./registry:/app/registry \
  mcp-server:latest
```

Or with `docker compose`:

```bash
docker compose up -d
```

### Volumes

| Mount              | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| `/app/registry`    | Command definitions — override or extend at runtime.     |
| `/app/scripts/config.sh` | Optional: file-based config for discord.sh.        |
| `/tmp/mcp`         | Default log file location (or set MCP_LOG_FILE).        |

The `scripts/` directory (including `discord.sh`) is baked into the image.
Secrets are never baked in — provide them via environment variables
(`--env-file .env`) or by mounting `config.sh` as a read-only volume.

### Image details

- **Base**: `python:3.12-slim` (multi-arch)
- **System deps**: `curl`, `jq` (for `discord.sh` and similar tools), `tini`
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
docker build -f variants/Dockerfile.php -t mcp-server:php .

# Node.js
docker build -f variants/Dockerfile.node -t mcp-server:node .
```

**Run a variant:**

```bash
docker run -p 8000:8000 \
  --env-file .env \
  -v ./registry:/app/registry \
  mcp-server:php
```

The base image already includes `php_eval.yaml` and `node_run.yaml` in the
baked-in registry. These commands will only execute successfully in the
matching variant image (the runtime must be present).

**Creating your own variant:**

```dockerfile
# variants/Dockerfile.ruby
FROM mcp-server:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ruby && rm -rf /var/lib/apt/lists/*
USER mcp
```

Then add a `registry/ruby_eval.yaml` pointing at `/usr/bin/ruby`.

## Testing

The project includes a full pytest suite (233 tests) covering models,
executor, registry, API endpoints, client library, authentication, and
CalDAV calendar operations.

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

### Test layout

```
tests/
├─ conftest.py          # shared fixtures (temp registry dirs, TestClient)
├─ test_models.py       # ArgSpec, CommandSchema, ValidationResult
├─ test_executor.py     # _cast, _validate_and_build, run_command
├─ test_registry.py     # _load_file, load_registry, validate_registry
├─ test_api.py          # HTTP endpoints via FastAPI TestClient
├─ test_client.py       # MCPClient via ASGI transport
├─ test_auth.py         # API key authentication (enabled/disabled/edge cases)
└─ test_caldav.py       # CalDAV models, service, API endpoints, client (mocked)
```

The flag-default regression (discord's `-q` defaulting to `true`) is
covered by `test_executor.py::TestValidateAndBuild::test_flag_default_true_*`.

## Security notes

- Only commands present in `registry/` can be executed — there is no
  arbitrary-command endpoint.
- Arguments are validated (type, required, choices) before the subprocess is
  spawned, and unknown arguments are rejected.
- Every command has a hard 30 s timeout.
- **API key authentication** — set `MCP_API_KEY` to require an `X-API-Key`
  header on all endpoints except `/health`.  When unset, the server is open.
- Run the server under a limited user account; do not grant it sudo.
- Commands that allow introspection of the server filesystem or arbitrary
  code execution (e.g. `list_files`, `php_eval`, `node_run`) have been
  removed by design — only specific, allowed commands should be registered.

---

*Built by Lyra — your silver-haired assistant in the corner. ✨*
