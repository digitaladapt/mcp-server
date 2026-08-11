> **Archived** — This was the original planning document. The project has
> evolved significantly since this was written: `POST /execute` was replaced
> with dedicated per-command routes, CalDAV and Gitea integrations were
> added, and the command registry was expanded. Kept for historical context.

# MCP Server – Modular Command Provider (Original Planning)

## Goal
Create a **Modular Command Provider (MCP) server** that exposes arbitrary terminal commands as reusable tools for a language model. The idea is to let developers register any existing CLI program (regardless of language) simply by describing its arguments and purpose. The model can then discover the command, compose the appropriate invocation, and execute it via a safe `execute_command`‑style function.

## Why this is useful
- **Language‑agnostic** – No need to rewrite tools in a specific SDK; any script, binary, or compiled program can be wrapped.
- **Self‑describing** – Each command is registered with a JSON/YAML schema describing its CLI flags/positional arguments and a short purpose string.
- **Discoverable** – The server can expose an endpoint that the model queries to list available commands and their signatures.
- **Secure execution** – Execution happens through a dedicated sandboxed function that validates arguments against the schema before calling the real command.

## High‑level Architecture
1. **Server (Python FastAPI)** – Handles HTTP API calls:
   - `GET /commands` – list all registered commands.
   - `GET /commands/{name}` – retrieve a command’s schema.
   - `POST /execute` – validate payload and run the command, returning stdout/stderr and exit code.
2. **Command Registry** – Simple JSON/YAML files stored under `~/projects/mcp_server/registry/`. Each file describes one command, e.g.:
   ```yaml
   name: hello_world
   description: Prints a friendly greeting.
   executable: /usr/local/bin/hello_world
   args:
     - name: name
       type: string
       required: true
       help: Name of the person to greet
     - name: --loud
       type: flag
       required: false
       help: Upper‑case output
   ```
3. **Executor** – A thin wrapper that:
   - Loads the command schema.
   - Validates incoming arguments (type checking, required fields, allowed choices).
   - Constructs the exact command line.
   - Runs it via `subprocess.run(..., capture_output=True, text=True, timeout=30)`.
   - Returns a structured response.
4. **Model Integration** – The model is given a system‑prompt pointing at the server’s OpenAPI spec. It can then treat each command as a tool, calling `POST /execute` with the appropriate JSON payload.

## Milestones
- **M1 – Project scaffolding** (folders, FastAPI basics). *Done*.
- **M2 – Command registry format & loader**.
- **M3 – Validation layer (pydantic models).**
- **M4 – Execution endpoint with sandboxing.**
- **M5 – Documentation & example commands.**
- **M6 – Simple client library for the model (auto‑generated via OpenAPI).**

## Next Steps
Create an **implementation details** document that walks through each milestone, lists required packages, file layout, and code snippets.

---
*Prepared by Lyra, your office‑side assistant. ✨*