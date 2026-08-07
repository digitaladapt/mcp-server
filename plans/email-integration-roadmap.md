# Email Integration — Roadmap to v1.0

## Vision

Give Lyra **selective, safe access to email** so she can triage, summarise,
and act on the user's behalf — without ever touching something she
shouldn't.

> *"Your automated payment for Backblaze went through, and I've logged
> the expense for you."*

That's the dream. Getting there means layering in IMAP access, strict
permission boundaries, smart review workflows, and eventually cross-tool
automation that ties email into the rest of the MCP ecosystem (CalDAV,
Gitea, penny-track, Discord/ntfy).

---

## Design Principles

1. **Secure by default.** Unconfigured = read-only, and even "read-only"
   means *no mutations at all* — not even marking messages as read.
2. **Explicit allow-listing.** Every destructive or outbound action
   (send, forward, archive, mark read, delete) must be explicitly
   enabled in config.
3. **Folder-scoped.** Lyra can only see and touch messages in folders
   the user has explicitly listed.
4. **Auditable.** Every mutation (archive, mark read, send, forward,
   delete) is logged with timestamp, message ID, and action taken.

---

## Configuration (env / config file)

All email behaviour is driven by environment variables or a config
section in the existing `.env` / settings system.

### Connection

| Variable             | Purpose                                  |
|----------------------|------------------------------------------|
| `IMAP_HOST`          | IMAP server hostname                     |
| `IMAP_PORT`          | IMAP server port (default 993)           |
| `IMAP_USER`          | Login username                           |
| `IMAP_PASS`          | Login password / app password            |
| `IMAP_SSL`           | Use SSL (default `true`)                 |
| `SMTP_HOST`          | SMTP server hostname (for sending)       |
| `SMTP_PORT`          | SMTP server port (default 587)           |
| `SMTP_USER`          | SMTP login username                      |
| `SMTP_PASS`          | SMTP login password / app password       |
| `SMTP_TLS`           | Use STARTTLS (default `true`)            |

### Permissions

| Variable                  | Default   | Purpose                                                |
|---------------------------|-----------|--------------------------------------------------------|
| `EMAIL_MODE`              | `readonly`| `readonly`, `readwrite`, or `full` (see below)        |
| `EMAIL_FOLDERS`           | *(empty)* | Comma-separated list of folders Lyra may access        |
| `EMAIL_ARCHIVE_FOLDER`    | *(empty)* | Folder name to use when archiving messages             |
| `EMAIL_SEND_ALLOWLIST`    | *(empty)* | Comma-separated list of recipient addresses Lyra may send to |
| `EMAIL_FORWARD_ALLOWLIST` | *(empty)* | Comma-separated list of addresses Lyra may forward to  |
| `EMAIL_CAN_MARK_READ`     | `false`   | Allow marking messages as read/unread                  |
| `EMAIL_CAN_DELETE`        | `false`   | Allow deleting messages (moves to trash)               |

#### Access Modes

| Mode        | Read | Mark Read | Archive | Send | Forward | Delete |
|-------------|------|-----------|---------|------|---------|--------|
| `readonly`  | ✅   | ❌        | ❌      | ❌   | ❌      | ❌     |
| `readwrite` | ✅   | if flag   | if set  | if allowlist | if allowlist | if flag |
| `full`      | ✅   | ✅        | ✅      | if allowlist | if allowlist | ✅     |

> **Unconfigured = `readonly` with empty folder list = Lyra can't do
> anything.** This is the safest possible default.

---

## API Endpoints

Following the same pattern as CalDAV and Gitea integrations — native
FastAPI routes, documented in OpenAPI.

| Method | Path                          | Description                                           |
|--------|-------------------------------|-------------------------------------------------------|
| GET    | `/emails/folders`             | List accessible folders (filtered by allow-list)      |
| GET    | `/emails`                     | List/search messages in accessible folders            |
| GET    | `/emails/{message_id}`        | Get a single message (headers + body)                 |
| PATCH  | `/emails/{message_id}`        | Mark read/unread (if permitted)                       |
| POST   | `/emails/{message_id}/archive`| Move message to archive folder (if permitted)         |
| DELETE | `/emails/{message_id}`        | Delete message (if permitted)                         |
| POST   | `/emails`                     | Send a new email (only to allow-listed recipients)    |
| POST   | `/emails/{message_id}/forward`| Forward a message (only to allow-listed recipients)   |

### Query parameters for `GET /emails`

| Parameter    | Type     | Description                                    |
|--------------|----------|------------------------------------------------|
| `folder`     | string   | Must be in `EMAIL_FOLDERS` allow-list          |
| `unseen`     | bool     | Filter to unread messages only                 |
| `since`      | datetime | Only messages received after this date         |
| `before`     | datetime | Only messages received before this date        |
| `from`       | string   | Filter by sender address                       |
| `subject`    | string   | Filter by subject (substring match)            |
| `limit`      | int      | Max results (default 50, max 200)              |
| `offset`     | int      | Pagination offset                              |

---

## Smart Review Workflows

The real power isn't just "read email" — it's *understanding* what's
been read and taking appropriate action. These workflows run as
scheduled automations or on-demand prompts.

### Workflow 1: Bank Folder Triage

**Trigger:** Scheduled (e.g., every 2 hours during business hours) or
on-demand.

**Logic:**

1. Fetch all unread messages in the "Bank" folder.
2. Classify each message:
   - **⚠️ Alert** — "unusual login activity", "large expenditure",
     "failed login", "suspicious transaction", etc.
   - **💤 Routine** — "payment received", "statement available",
     "pre-approved loan offer", etc.
   - **❓ Unknown** — anything that doesn't match known patterns.
3. For alerts: immediately notify via Discord/ntfy with the subject,
   sender, and a brief snippet.
4. For routine: archive and note in a running "morning summary" buffer.
5. For unknown: leave unread, note in morning summary for manual review.

**Classification approach (iterative):**

- **v1:** Keyword/regex rules — simple patterns against subject lines.
- **v2:** LLM-assisted classification — pass the message body to the
  model with a system prompt defining categories.
- **v3:** User-trainable rules — let the user correct misclassifications
  and save them as persistent rules.

### Workflow 2: Morning Summary

**Trigger:** Scheduled daily (e.g., 7:00 AM).

**Output:** A Discord message (or ntfy notification) that combines:

- Today's weather (from a weather API or existing integration).
- Today's calendar agenda (from CalDAV events).
- Email digest from overnight triage:
  - "4 emails about payments received (archived)"
  - "2 pre-approved loan offers (archived)"
  - "1 unusual login alert from Chase — **needs your attention**"
  - "3 emails in Bank folder I couldn't classify — please review"

### Workflow 3: Cross-Tool Action — Expense Logging

**Trigger:** Part of the Bank folder triage, when a "payment processed"
email is detected.

**Logic:**

1. Parse the email body for transaction details:
   - Merchant name
   - Amount
   - Date
   - Transaction/reference ID (if available)
2. Query the penny-track API to check if a matching transaction already
   exists.
3. If no match found, create the transaction in penny-track.
4. Notify the user: *"Your automated payment for Backblaze for backup
   storage went through ($6.00), and I've logged the expense for you."*

**Dependencies:**
- penny-track API must be integrated into the MCP server as a set of
  native endpoints (see **Penny-Track MCP Integration** below).
- The MCP server manages the penny-track API key on behalf of Lyra —
  she never sees the raw key.

### Penny-Track MCP Integration

penny-track is a Symfony 8.1 PHP app with a REST API secured by API key
auth. The existing endpoints are:

| Method | Endpoint                          | Purpose                                    |
|--------|-----------------------------------|--------------------------------------------|
| GET    | `/api/receipts`                   | Paginated list (newest first)              |
| GET    | `/api/receipts/{id}`              | Single receipt by ID                       |
| POST   | `/api/receipts`                   | Create receipt (amount, business, category, location, tags, notes, created_at) |
| PUT    | `/api/receipts/{id}`              | Update receipt                             |
| DELETE | `/api/receipts/{id}`              | Delete receipt                             |
| POST   | `/api/receipts/parse`             | LLM-parse text into receipt                |
| GET    | `/api/dashboard/summary`          | Monthly stats (totals, count, avg, MoM %)  |
| GET    | `/api/dashboard/spending-by-category` | Breakdown by category (1–12 months)   |

**The gap:** There's no search/filter endpoint. The `GET /api/receipts`
endpoint only supports `page` and `limit` — no filtering by business,
amount, date range, category, or tags. For the email → expense logging
workflow to work reliably (checking whether a transaction was already
logged), we need a way to search for existing receipts by merchant name
and/or amount and/or date.

**Required penny-track API additions** (tracked in the penny-track
roadmap at `projects/penny-track/ROADMAP.md`):

- `GET /api/receipts/search` — full-text / field-level search:
  - `q` (string) — general search across business, notes, tags
  - `business` (string) — exact or fuzzy match
  - `category` (string) — exact match
  - `tags` (comma-separated) — receipts matching any/all tags
  - `amount_min` / `amount_max` (float) — amount range
  - `date_from` / `date_to` (ISO 8601) — date range
  - `created_at_from` / `created_at_to` (ISO 8601) — creation date range
  - Returns same paginated format as `GET /api/receipts`

**MCP server endpoints** (wrapping penny-track's API, key managed
server-side via `PENNY_TRACK_API_KEY` env var):

| Method | MCP Endpoint                          | Maps to penny-track                        |
|--------|---------------------------------------|--------------------------------------------|
| GET    | `/penny-track/receipts`               | `GET /api/receipts` (list)                 |
| GET    | `/penny-track/receipts/search`        | `GET /api/receipts/search` (new endpoint)  |
| GET    | `/penny-track/receipts/{id}`          | `GET /api/receipts/{id}`                   |
| POST   | `/penny-track/receipts`               | `POST /api/receipts` (create)              |
| PUT    | `/penny-track/receipts/{id}`          | `PUT /api/receipts/{id}` (update)          |
| DELETE | `/penny-track/receipts/{id}`          | `DELETE /api/receipts/{id}`                |
| GET    | `/penny-track/dashboard/summary`      | `GET /api/dashboard/summary`               |
| GET    | `/penny-track/dashboard/by-category`  | `GET /api/dashboard/spending-by-category`  |

The `POST /api/receipts/parse` endpoint is **not** proxied — the MCP
server's own LLM can handle parsing if needed, and we don't want to
expose a text-to-receipt auto-create without explicit user intent.

**Future expansion:** This pattern generalises to any "email → action"
pipeline:

| Email trigger                     | Action                                      |
|-----------------------------------|---------------------------------------------|
| "Payment processed"               | Log expense in penny-track                  |
| "Invoice due"                     | Create calendar event / task with due date  |
| "Shipping notification"           | Create calendar event for expected delivery |
| "Pull request review requested"   | Create Gitea PR review task / notify        |
| "Calendar invite"                 | Parse and create CalDAV event               |

---

## Phased Delivery

### Phase 1 — IMAP Read-Only Core (v0.7.0)

**Goal:** Lyra can list folders, list messages, and read message
content. No mutations at all.

- [ ] IMAP connection management (connect, idle, reconnect)
- [ ] `GET /emails/folders` — list accessible folders (filtered)
- [ ] `GET /emails` — list/search messages with filters
- [ ] `GET /emails/{message_id}` — fetch full message (headers + body)
- [ ] Config validation — refuse to start if folders list is empty
- [ ] Tests with mocked IMAP server
- [ ] Documentation in README

### Phase 2 — Mutations & SMTP (v0.8.0)

**Goal:** Lyra can archive, mark read, send, and forward — all gated by
permissions.

- [ ] `PATCH /emails/{message_id}` — mark read/unread
- [ ] `POST /emails/{message_id}/archive` — move to archive folder
- [ ] `DELETE /emails/{message_id}` — delete (if permitted)
- [ ] `POST /emails` — send (validate recipient against allow-list)
- [ ] `POST /emails/{message_id}/forward` — forward (validate recipient)
- [ ] Audit log for every mutation
- [ ] Permission enforcement tests (verify denials)
- [ ] SMTP connection management

### Phase 3 — Smart Review (v0.9.0)

**Goal:** Automated triage workflows with classification and alerting.

- [ ] Keyword/regex classification engine
- [ ] Bank folder triage automation script
- [ ] Discord/ntfy alerting for high-priority messages
- [ ] Morning summary generation (weather + agenda + email digest)
- [ ] Classification rules config file (user-editable)
- [ ] "Morning summary" scheduled automation template
- [ ] Tests for classification accuracy

### Phase 4 — Cross-Tool Automation (v1.0.0)

**Goal:** Email triggers actions in other systems.

- [ ] penny-track MCP endpoints (proxy layer with server-side API key)
- [ ] `GET /penny-track/receipts/search` — requires search endpoint in penny-track
- [ ] Email → penny-track expense logging workflow (search before create)
- [ ] Email → CalDAV event creation (invites, shipping notices)
- [ ] Email → Gitea task/issue creation (PR review requests)
- [ ] Configurable action rules ("when email matches X, do Y")
- [ ] End-to-end integration tests
- [ ] Full documentation update

---

## Technical Notes

### IMAP Library

Python's standard `imaplib` works but is low-level. Consider using
[`aioimaplib`](https://github.com/bamthomas/aioimaplib) for async
support that matches FastAPI's async model, or
[`imap-tools`](https://github.com/ikvk/imap_tools) for a cleaner
high-level API.

**Recommendation:** `imap-tools` for v1 — it's synchronous but clean
and well-maintained. Move to async later if performance warrants it.

### Message Parsing

Use Python's built-in `email` module (`email.message_from_bytes`) for
MIME parsing. For HTML-to-text conversion, `beautifulsoup4` is already
likely available.

### IDLE Support

IMAP IDLE allows push notifications for new messages. This would enable
near-real-time alerting instead of polling. Worth investigating in
Phase 3, but polling on a schedule (every 5–15 min) is fine for v1.

### Audit Log

Append-only JSONL file at `logs/email_audit.jsonl`:

```json
{
  "timestamp": "2025-01-15T14:23:01Z",
  "action": "archive",
  "message_id": "<abc123@mail.example.com>",
  "folder": "Bank",
  "from": "alerts@chase.com",
  "subject": "Payment received"
}
```

### Security Considerations

- IMAP/SMTP credentials stored in `.env` (already gitignored).
- App passwords recommended over main passwords (Gmail, etc.).
- The allow-lists are hard boundaries — even if the model is tricked,
  the server enforces the check at the API layer.
- Consider rate-limiting outbound email (e.g., max 5 sends/hour) as a
  safety net against runaway automations.

---

## Open Questions

1. **HTML vs plaintext rendering** — Should we always convert HTML to
   plaintext for the model, or preserve structure? (Likely plaintext
   for v1, preserve raw HTML on demand.)
2. **Attachment handling** — Should Lyra be able to download/save
   attachments? (Probably not in v1 — read-only metadata only.)
3. **Multiple mailboxes** — One user might have multiple accounts
   (personal + work). Config as a list of connection profiles?
4. **~~penny-track API~~** — ✅ Audited. penny-track is a Symfony 8.1
   PHP app with REST API, API-key auth, and SQLite storage. Full audit
   captured in the penny-track roadmap at `projects/penny-track/ROADMAP.md`.
   Missing: a search endpoint needed for deduplication before auto-logging.
5. **Classification training data** — How does the user correct
   misclassifications? A simple "this was wrong" feedback loop, or a
   rules file they edit directly?
6. **Morning summary delivery** — Discord, ntfy, or both? Should it be
   a scheduled automation or triggered manually?

---

## Relationship to Existing MCP Server Features

| Existing Feature   | Email Integration Touchpoint                     |
|--------------------|--------------------------------------------------|
| CalDAV (events/tasks) | Morning summary pulls today's agenda; email invites can create events |
| Gitea (issues/PRs) | PR review request emails can create Gitea tasks  |
| Discord/ntfy       | Alerting for high-priority emails; morning summary delivery |
| Command registry   | penny-track + VitalPulse integrated as native MCP endpoints (keys managed server-side) |
| Scheduled automations | Email triage runs as a scheduled automation      |

---

*Prepared by Lyra, your office-side assistant. ✨*
