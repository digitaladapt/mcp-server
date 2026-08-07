# Configurable Auth Header — Plan for v0.8.0

## Problem

The MCP server's authentication layer (`app/auth.py`) hardcodes the use
of an `X-API-Key` header.  While this works fine for internal use, it
clashes with standard authentication conventions used by most API
gateways, proxies, and platform integrations.

Specifically:

1. **Non-standard header name.**  `X-API-Key` is a custom header.  Many
   reverse proxies (nginx, Caddy, Traefik) and API platforms expect
   authentication via the standard `Authorization` header.

2. **No bearer token support.**  The current scheme sends the raw key
   value with no scheme prefix (e.g. `X-API-Key: my-secret`).  Bearer
   token format (`Authorization: Bearer my-secret`) is the de facto
   standard for API authentication and is what most clients, SDKs, and
   platform integrations default to.

3. **Inflexible for deployment.**  When the server sits behind a proxy
   that strips or rewrites auth headers, there's no way to adapt without
   code changes.

## Vision

> Make the API key header name and scheme fully configurable via
> environment variables, with sensible defaults that follow standard
> conventions.

**Default behavior changes to `Authorization: Bearer <token>`**, which
is what most platforms and clients expect out of the box.

## Configuration

### New environment variables

```
# Header name (default: Authorization)
MCP_AUTH_HEADER=Authorization

# Auth scheme/prefix (default: Bearer)
# Set to empty string for raw key with no prefix
MCP_AUTH_SCHEME=Bearer

# The secret key itself (already exists, unchanged)
MCP_API_KEY=your-secret-key-here
```

### Behavior matrix

| `MCP_API_KEY` | `MCP_AUTH_HEADER` | `MCP_AUTH_SCHEME` | Expected header                           |
|---------------|-------------------|-------------------|-------------------------------------------|
| unset         | *(any)*           | *(any)*           | *(auth disabled — open access)*           |
| set           | *(default)*       | *(default)*       | `Authorization: Bearer <key>`             |
| set           | `X-API-Key`       | *(empty)*         | `X-API-Key: <key>`  *(legacy behavior)*   |
| set           | `X-API-Token`     | `Token`           | `X-API-Token: Token <key>`                |
| set           | `Authorization`   | *(empty)*         | `Authorization: <key>` *(raw, no scheme)* |

### Migration / backwards compatibility

Existing deployments using `X-API-Key` can preserve the old behavior:

```env
MCP_AUTH_HEADER=X-API-Key
MCP_AUTH_SCHEME=
```

Or transition to the new default by updating clients to send
`Authorization: Bearer <key>` and simply removing the two new vars
(letting them default).

## Architecture

### Changes to `app/auth.py`

#### 1. Read configuration at import time

```python
_API_KEY: str = os.environ.get("MCP_API_KEY", "").strip()
_AUTH_HEADER: str = os.environ.get("MCP_AUTH_HEADER", "Authorization").strip()
_AUTH_SCHEME: str = os.environ.get("MCP_AUTH_SCHEME", "Bearer").strip()
```

#### 2. Dynamic `APIKeyHeader` scheme

Currently:
```python
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
```

Becomes:
```python
_api_key_header = APIKeyHeader(name=_AUTH_HEADER, auto_error=False)
```

This keeps OpenAPI documentation accurate — the security scheme will
advertise whatever header name is configured.

#### 3. Updated `verify_api_key` dependency

The key change: when a scheme is configured, extract the token from the
header value by stripping the scheme prefix.

```python
def _extract_key(provided: str | None) -> str | None:
    """Extract the API key from the header value, accounting for scheme."""
    if provided is None:
        return None
    if not _AUTH_SCHEME:
        # No scheme — the raw header value IS the key.
        return provided.strip()
    # Expected format: "Scheme <key>"
    prefix = f"{_AUTH_SCHEME} "
    if provided.startswith(prefix):
        return provided[len(prefix):].strip()
    return None  # Wrong scheme — treat as missing key
```

The `verify_api_key` dependency then uses `_extract_key()` instead of
comparing the raw header value directly:

```python
async def verify_api_key(
    request: Request,
    provided_key: str | None = Depends(_api_key_header),
) -> bool:
    if not _API_KEY:
        return True  # Auth disabled

    extracted = _extract_key(provided_key)

    if extracted is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or malformed {_AUTH_HEADER} header",
            headers={"WWW-Authenticate": f'{_AUTH_SCHEME} realm="mcp-server"'}
                    if _AUTH_SCHEME
                    else {"WWW-Authenticate": 'ApiKey realm="mcp-server"'},
        )

    if not secrets.compare_digest(extracted, _API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return True
```

#### 4. Helper for clients

Add a function that builds the correct header dict for outbound use
(useful for the client library and internal health checks):

```python
def auth_headers() -> dict[str, str]:
    """Return the auth header dict for the current configuration."""
    if not _API_KEY:
        return {}
    if _AUTH_SCHEME:
        return {_AUTH_HEADER: f"{_AUTH_SCHEME} {_API_KEY}"}
    return {_AUTH_HEADER: _API_KEY}
```

### Changes to `app/client.py`

The MCPClient currently sends `X-API-Key` as a hardcoded header:

```python
headers["X-API-Key"] = api_key
```

Update to accept the header name (and optionally scheme) as constructor
parameters, defaulting to the new standard:

```python
def __init__(
    self,
    base_url: str,
    api_key: str | None = None,
    auth_header: str = "Authorization",
    auth_scheme: str = "Bearer",
    ...
):
    ...
    if api_key:
        if auth_scheme:
            headers[auth_header] = f"{auth_scheme} {api_key}"
        else:
            headers[auth_header] = api_key
```

### Changes to `.env.example`

Add the two new variables with explanatory comments:

```env
# Header name used for API key authentication (default: Authorization).
# Use X-API-Key for legacy behavior.
#MCP_AUTH_HEADER=Authorization

# Auth scheme prefix (default: Bearer).
# Set to empty string for raw key with no prefix (e.g. X-API-Key: <key>).
#MCP_AUTH_SCHEME=Bearer
```

### Changes to `docker-compose.yml`

Add the new env vars to the service definition:

```yaml
environment:
  - MCP_API_KEY=${MCP_API_KEY:-}
  - MCP_AUTH_HEADER=${MCP_AUTH_HEADER:-Authorization}
  - MCP_AUTH_SCHEME=${MCP_AUTH_SCHEME:-Bearer}
```

## Implementation Steps

1. **Update `app/auth.py`**
   - Read `MCP_AUTH_HEADER` and `MCP_AUTH_SCHEME` from env
   - Dynamic `APIKeyHeader` name
   - `_extract_key()` helper with scheme-aware parsing
   - Updated `verify_api_key` dependency
   - Add `auth_headers()` helper

2. **Update `app/client.py`**
   - Accept `auth_header` and `auth_scheme` params
   - Build header value with scheme prefix

3. **Update `.env.example`**
   - Document `MCP_AUTH_HEADER` and `MCP_AUTH_SCHEME`

4. **Update `docker-compose.yml`**
   - Pass through the new env vars

5. **Update tests**
   - Test default config (`Authorization: Bearer <key>`)
   - Test legacy config (`X-API-Key: <key>`)
   - Test custom header + scheme combinations
   - Test scheme-less mode (`MCP_AUTH_SCHEME=`)
   - Test malformed headers (wrong scheme, missing token)
   - Test auth still disabled when `MCP_API_KEY` unset

6. **Update README**
   - Document the new auth configuration
   - Add migration note for existing deployments
   - Update any examples that show `X-API-Key`

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking existing deployments using `X-API-Key` | Document migration; `MCP_AUTH_HEADER=X-API-Key` + `MCP_AUTH_SCHEME=` restores old behavior |
| `APIKeyHeader` doesn't support scheme prefixes natively | We parse the value manually in `_extract_key()` — `APIKeyHeader` just captures the raw header |
| Case sensitivity of scheme (`Bearer` vs `bearer`) | Case-insensitive comparison on the scheme prefix |
| OpenAPI docs show wrong header name | `APIKeyHeader(name=_AUTH_HEADER)` is dynamic — OpenAPI will reflect the configured name |

## Next Steps

### CalDAV Calendar Config Clarification

While testing the v0.8 changes, we discovered that the
`CALDAV_EDITABLE_CALENDAR` env var must match the calendar's **display
name** exactly (e.g. `Lyra` with a capital L) — not the URL path or
`owner/name` combo.

On the production instance, all four calendars (including Lyra) showed
as read-only because the editable flag wasn't matching. Once corrected
to use the exact display name, Lyra properly showed as editable.

**Action items:**
- [ ] Document this requirement clearly in the README
- [ ] Add a note in `.env.example` emphasizing exact display name match
- [ ] Consider adding a startup warning if no editable calendar is found
      (log the configured value + list of discovered calendar names to
      help debug mismatches)

### Improve Error Reporting for Non-Editable Calendars

During testing of update and delete operations against read-only
calendars, we found that the server returns misleading error messages:

- **Update** on a read-only event → `HTTP 400: "Event not found"`
- **Delete** on a read-only event → `HTTP 404: "Event not found"`

The operations are correctly **blocked**, but the error message says
"not found" when the event *does* exist — it's just on a non-editable
calendar. This is confusing for anyone debugging permission issues,
as it points them toward a missing-resource problem rather than the
real cause (access/permission).

**Proposed change:** Return a more descriptive error such as:

- `HTTP 403: "Access denied: calendar is not editable"`
- or `HTTP 403: "Cannot modify events on non-editable calendar"`

This accurately tells the caller *why* the operation failed and
would have made our testing today much clearer.

**Action items:**
- [ ] Update error handling in the update/delete event routes to check
      editability *before* returning a not-found error
- [ ] Return `HTTP 403` with an access-denied message for non-editable
      calendar operations
- [ ] Apply the same improvement to task update/delete routes if they
      share the same pattern

## Out of Scope for v0.8

- **OAuth2 / JWT validation** — We're not adding a full OAuth flow. The
  key is still a shared secret, just transmitted via a standard header.
- **Multiple API keys** — One key, one header. Multi-key support would
  be a separate feature.
- **Role-based access control** — All authenticated requests have the
  same permissions. RBAC is a future concern.

## v0.9 Feature Ideas

### Read-Only Access to Outlook Work Calendar

**Goal:** Add read-only visibility into Andrew's work calendar, which is
hosted on Outlook/Microsoft 365.

**Status:** Idea stage — implementation approach is not yet clear.

**Unknowns to investigate:**

- **Microsoft Graph API** — Likely the right path. Need to determine what
  auth flow is appropriate (OAuth2 with delegated permissions, app-only
  with client credentials, etc.).
- **Auth complexity** — This won't be a simple API key. Microsoft auth
  typically requires app registration in Entra ID (Azure AD), client
  ID/secret or certificate, and admin consent for calendar.read scopes.
- **CalDAV gateway** — Alternatively, check if the org has a CalDAV
  gateway or if a tool like the Outlook CalDAV sync could bridge it into
  our existing CalDAV client. Would avoid Microsoft Graph entirely.
- **Read-only enforcement** — Regardless of how we connect, the calendar
  must be treated as strictly read-only at the MCP layer (no create /
  update / delete routed to it).
- **Token refresh** — If OAuth, we'll need a token refresh mechanism for
  long-running sessions.

**Next steps when ready to explore:**
- [ ] Check whether the work tenant allows third-party app registrations
- [ ] Prototype a Microsoft Graph calendar read call
- [ ] Compare effort vs. a CalDAV bridge approach
- [ ] Decide on auth strategy and scope

---

*Prepared by Lyra, your office-side assistant. ✨*
