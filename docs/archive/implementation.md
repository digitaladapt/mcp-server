> **Archived** — This was the original implementation plan. Many details have
> changed: the `POST /execute` endpoint was removed in favor of dedicated
> per-command routes, the file layout has grown to include CalDAV and Gitea
> modules, and the example commands (`hello.yaml`, `list_files.yaml`) were
> removed. Kept for historical context.

# Implementation Details for MCP Server (Original Plan)

## File Layout
```
~/projects/mcp_server/
├─ app/                       # FastAPI application package
│   ├─ __init__.py
│   ├─ main.py                # FastAPI entry point
│   ├─ models.py              # Pydantic models (CommandSchema, ExecuteRequest, ExecuteResult)
│   ├─ executor.py            # Wrapper around subprocess with validation
│   └─ registry.py            # Loader for command definitions (YAML/JSON)
├─ registry/                  # Command definition files (one per command)
│   └─ example.yaml
├─ requirements.txt           # Python dependencies
├─ planning.md                # High‑level plan (written earlier)
└─ implementation.md          # This file
```

## Dependencies
- **Python >=3.10** (we have 3.12)
- `fastapi`
- `uvicorn[standard]`
- `pydantic`
- `pyyaml` (for YAML command definitions)

Install with:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Registry changes require a server restart — there is no hot-reload.

## Step‑by‑Step Milestones
### M2 – Command Registry Loader
1. Create `registry/` directory.
2. Each command is a YAML file with keys:
   - `name` (string, unique)
   - `description` (string)
   - `executable` (absolute path to binary/script)
   - `args` (list of argument specs)
3. Argument spec fields:
   - `name` – flag name (`--verbose`) or positional name.
   - `type` – `string`, `int`, `float`, `bool`, or `flag` (no value, just presence).
   - `required` – boolean.
   - `choices` – optional list of allowed values.
   - `help` – human‑readable description.
4. `registry.py` will scan `registry/` on import, load all YAML files into a dict `{name: CommandSchema}`.

### M3 – Validation Layer (Pydantic)
Create `models.py` containing:
```python
from pydantic import BaseModel, Field, validator
from typing import Any, Dict, List, Optional

class ArgSpec(BaseModel):
    name: str
    type: str
    required: bool = False
    choices: Optional[List[Any]] = None
    help: Optional[str] = None

class CommandSchema(BaseModel):
    name: str
    description: str
    executable: str
    args: List[ArgSpec]

class ExecuteRequest(BaseModel):
    command: str                     # name from registry
    arguments: Dict[str, Any] = {}   # map arg name -> value

class ExecuteResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    success: bool
```
The `executor.py` will receive an `ExecuteRequest`, look up the schema, and:
- Ensure every `required` arg is present.
- Verify types (cast if possible).
- Enforce `choices` if supplied.
- Reject unknown arguments.

### M4 – Execution Endpoint
`app/main.py` skeleton:
```python
from fastapi import FastAPI, HTTPException
from .models import ExecuteRequest, ExecuteResult, CommandSchema
from .registry import get_command_schema
from .executor import run_command

app = FastAPI(title="MCP Server", version="0.1.0")

@app.get("/commands", response_model=List[CommandSchema])
async def list_commands():
    return list(CommandRegistry.values())

@app.get("/commands/{name}", response_model=CommandSchema)
async def get_command(name: str):
    schema = get_command_schema(name)
    if not schema:
        raise HTTPException(status_code=404, detail="Command not found")
    return schema

@app.post("/execute", response_model=ExecuteResult)
async def execute(req: ExecuteRequest):
    schema = get_command_schema(req.command)
    if not schema:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        result = run_command(schema, req.arguments)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```
`executor.run_command` will:
1. Build a list `cmd = [schema.executable]`.
2. Iterate over `schema.args` in order:
   - If `type == "flag"` and argument value is truthy, append the flag name.
   - If positional, append the provided value.
   - For `--opt` style flags, add `--opt` and the value (or just the flag for boolean flags).
3. Call `subprocess.run(cmd, capture_output=True, text=True, timeout=30)`.
4. Return an `ExecuteResult`.

### Security/Sandboxing
- **Whitelist**: Only commands present in the registry can be executed.
- **Argument validation** prevents injection.
- **Timeout** (30 s) avoids hanging.
- **User**: Run the server under a limited user account (the current `unknown` user) without sudo.
- **Optional**: Use `subprocess.Popen` with a `preexec_fn` that sets `os.setsid()` and drops privileges if needed.

### M5 – Documentation & Example Commands
Provide two example YAML definitions:
1. `hello.yaml` (simple echo script).
2. `list_files.yaml` (wraps `ls` with optional `-l` flag).
Add a `README.md` with instructions to start the server:
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
The model can then query `http://127.0.0.1:8000/openapi.json` to discover tools.

### M6 – Client Library (optional)
Generate a small Python client using `httpx` that mirrors the OpenAPI spec, exposing:
```python
def execute(command, **kwargs):
    return httpx.post(url, json={"command": command, "arguments": kwargs}).json()
```
The language model can embed this logic in its prompt, treating `execute` as a native tool.

## Next Immediate Action
1. Create the folder structure (`app/`, `registry/`).
2. Add a minimal `app/__init__.py` and a stub `app/main.py` that returns a static "OK" response for `/health`.
3. Add a sample command definition in `registry/example.yaml`.
4. Commit these files so we have a runnable skeleton.

Once the skeleton is verified (run `uvicorn` successfully), we’ll flesh out the registry loader and executor.

---
*Lyra, ready to code whenever you are!*