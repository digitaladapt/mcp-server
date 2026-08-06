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

## Quick start

```bash
cd ~/projects/mcp_server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The server now listens on `http://127.0.0.1:8000`.

## API

| Method | Path                 | Description                          |
|--------|----------------------|--------------------------------------|
| GET    | `/health`            | Liveness probe                       |
| GET    | `/commands`          | List all registered commands         |
| GET    | `/commands/{name}`   | Retrieve one command's schema        |
| POST   | `/execute`           | Validate arguments and run a command |

### Example

```bash
# List available commands
curl http://127.0.0.1:8000/commands

# Execute the `hello` command
curl -X POST http://127.0.0.1:8000/execute \
     -H 'Content-Type: application/json' \
     -d '{"command": "hello", "arguments": {"name": "World"}}'
```

Response:
```json
{"stdout": "World\n", "stderr": "", "exit_code": 0, "success": true}
```

## Registering a command

Create a file in `registry/` (e.g. `my_tool.yaml`):

```yaml
name: my_tool
description: Does something useful.
executable: /usr/local/bin/my_tool
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

## Client library

A small synchronous `httpx`-based client lives in `app/client.py`.  It
mirrors the HTTP API so a model or script can treat each registered
command as a native Python callable.

```python
from app.client import MCPClient

mc = MCPClient("http://127.0.0.1:8000")

# Discover available commands
for cmd in mc.list_commands():
    print(cmd["name"], "-", cmd["description"])

# Execute a command
result = mc.execute("hello", name="World")
print(result["stdout"])

# Bind a command to a reusable callable
discord = mc.tool("discord")
discord(message="Deploy complete", **{"-c": "green", "-t": "CI"})
```

Flag names that start with `-` aren't valid Python identifiers, so pass
them via dict unpacking: `**{"-c": "green"}`.

The client also works as a context manager:

```python
with MCPClient() as mc:
    mc.execute("list_files", path="/etc", **{"-l": True})
```

## Project layout

```
mcp_server/
├─ app/
│   ├─ __init__.py      # package marker
│   ├─ main.py          # FastAPI app + endpoints
│   ├─ models.py        # Pydantic schemas
│   ├─ executor.py      # validation + subprocess wrapper
│   ├─ registry.py      # YAML/JSON command loader
│   └─ client.py        # httpx client library (M6)
├─ registry/            # command definitions (one file per command)
│   ├─ hello.yaml
│   ├─ list_files.yaml
│   ├─ discord.yaml
│   ├─ php_eval.yaml     # PHP variant example
│   └─ node_run.yaml     # Node variant example
├─ Dockerfile             # multi-arch base image definition
├─ variants/              # variant Dockerfiles (PHP, Node, etc.)
│   ├─ Dockerfile.php
│   └─ Dockerfile.node
├─ docker-compose.yml     # easy local run with volumes
├─ .dockerignore          # excludes venv, secrets, etc.
├─ requirements.txt
├─ planning.md
└─ implementation.md
```

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
  -v ./registry:/app/registry \
  -v ./config.sh:/app/config.sh:ro \
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
| `/app/config.sh`   | Secrets/config for wrapped scripts (e.g. discord.sh).    |

**Never** bake `config.sh` into the image — it holds webhook URLs and
other secrets. The `.dockerignore` file excludes it automatically.

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
  -v ./registry:/app/registry \
  -v ./config.sh:/app/config.sh:ro \
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

## Security notes

- Only commands present in `registry/` can be executed — there is no
  arbitrary-command endpoint.
- Arguments are validated (type, required, choices) before the subprocess is
  spawned, and unknown arguments are rejected.
- Every command has a hard 30 s timeout.
- Run the server under a limited user account; do not grant it sudo.

---

*Built by Lyra — your silver-haired assistant in the corner. ✨*
