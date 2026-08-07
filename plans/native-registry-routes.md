# Native Registry Routes — Plan for v0.7.0

## Problem

The MCP server has two classes of tools:

1. **Route-based tools** (CalDAV, Gitea) — each endpoint is a dedicated
   FastAPI route with a typed Pydantic model.  The platform reads
   `GET /openapi.json`, sees each route as a distinct operation with
   typed parameters, and surfaces each one as a **native tool**
   (e.g. `create_event_events_post`, `list_issues_issues_get`).

2. **Registry-based tools** (discord, log, log_read) — defined in YAML,
   executed through the generic `POST /execute` endpoint.  The platform
   sees only one route (`POST /execute`) with a generic
   `{command, arguments}` body.  It can only expose this as the single
   `execute_execute_post` tool — the individual commands are invisible.

This means registry commands can never become native tools under the
current design, no matter how many YAML files are added.

## Vision

> Drop a YAML file in `registry/` → restart the server → the command
> appears as a native, individually-typed tool in the model's tool list.

Each registry command gets its own dedicated FastAPI route
(`POST /discord`, `POST /log`, `POST /log_read`) with a Pydantic request
model generated from the YAML arg specs.  The OpenAPI schema then
describes each command's parameters with proper types, defaults, and
enum choices — and the platform generates a native tool for each.

## Architecture

### New module: `app/registry_routes.py`

A FastAPI router that auto-generates one route per registered command.

```
registry/discord.yaml  →  POST /discord  (Pydantic model with typed args)
registry/log.yaml      →  POST /log      (Pydantic model with typed args)
registry/log_read.yaml →  POST /log_read (Pydantic model with typed args)
```

Each route:
1. Accepts a Pydantic model whose fields are derived from the command's
   `ArgSpec` list (type, required, choices, default).
2. Converts the model fields back to a dict of arguments.
3. Delegates to the existing `executor.run_command()` — no logic duplicated.
4. Returns the same `ExecuteResult` (`stdout`, `stderr`, `exit_code`, `success`).

### Dynamic Pydantic model generation

Use `pydantic.create_model()` to build a request model per command:

| YAML arg type | Python type      | Notes                              |
|---------------|------------------|------------------------------------|
| `string`      | `str`            |                                    |
| `int`         | `int`            |                                    |
| `float`       | `float`          |                                    |
| `bool`        | `bool`           |                                    |
| `flag`        | `bool`           | Defaults to `False` unless `default: true` |

- `choices` → `Literal[...]` so OpenAPI shows an enum.
- `required: true` → field is mandatory.
- `required: false` + `default` → field has a default.
- `required: false` + no default → field is `Optional`, defaults `None`.

Arg names like `-c`, `-q`, `--level` are not valid Python identifiers.
Pydantic supports `Field(alias=...)` to map a clean Python field name
(e.g. `color`) to the CLI arg name (e.g. `-c`).  We generate a safe
field name by stripping leading dashes.

### Integration in `main.py`

```python
from .registry_routes import router as registry_router
app.include_router(registry_router)
```

### What stays the same

- `POST /execute` remains fully functional — backwards compatible.
- The registry YAML format is unchanged — no migration needed.
- The executor, models, and existing routes are untouched.
- The client library's `mc.execute("discord", ...)` still works.

### Route naming and conflicts

- Route path: `POST /{command_name}` (e.g. `POST /discord`).
- A collision check prevents registering a route that clashes with
  existing CalDAV/Gitea routes (e.g. if someone names a command
  `events`, it would conflict with `POST /events`).
- On collision, the command is skipped with a logged warning, and the
  generic `POST /execute` path still works as a fallback.

## Implementation steps

1. **Write `app/registry_routes.py`**
   - `build_request_model(schema: CommandSchema) -> type[BaseModel]`
   - `create_registry_router() -> APIRouter`
   - One dynamic route per command

2. **Wire into `app/main.py`**
   - Import and include the registry router

3. **Test**
   - Verify OpenAPI schema shows `POST /discord`, `POST /log`, `POST /log_read`
   - Verify each route works via TestClient
   - Verify `POST /execute` still works (no regression)
   - Verify collision handling (register a command named `events`)

4. **Update docs**
   - README: mention that registry commands now get their own routes
   - Note that `POST /execute` is still available as a fallback

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Dynamic Pydantic models don't serialize well in OpenAPI | FastAPI handles `create_model` output natively; verify in `/openapi.json` |
| Arg name collisions (two args map to same clean name) | Detect and raise at startup |
| Route path conflicts with CalDAV/Gitea | Skip + warn, fall back to `/execute` |
| Platform doesn't pick up new routes | Verify by checking tool list after server restart |
