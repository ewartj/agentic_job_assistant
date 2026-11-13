# MCP servers

[← Back to the README](../README.md)

The two MCP servers the agents use: one that reads (the CV, letters and postings) and one that writes (the application tracker).

## The MCP server: `src/cv_server/`

It's built with the `mcp` 2.x Python SDK. In 2.x, `FastMCP` was renamed `MCPServer`, which most tutorials don't mention yet.

**Tools** (the model decides when to call them):

| Tool | What it does |
|---|---|
| `get_cv_evidence(query, limit=10)` | Searches the CV and returns matching entries, each with an `id` the model must cite |
| `get_past_letters(company)` | Returns cover letters previously written to a company (a case-insensitive substring match) |
| `list_past_applications()` | Lists every application on file, without the letter text |

**Resources** (the client application decides what to read into context):

| Resource | Returns |
|---|---|
| `cv://full`, `cv://entry/{entry_id}` | The whole CV, or one entry |
| `postings://index` | Every saved job posting: name, company, role, url and date saved |
| `postings://posting/{name}` | One posting's text, without its header |

Postings are resources rather than tools because the user chooses the posting, not the model. They're read from disk on every request, so a newly saved posting appears without restarting the server. Posting names are validated before they become file paths, so a request like `postings://posting/../../.env` is rejected.

**Search** is keyword-based. It weights each match by how rare the term is across the CV (IDF), so "parquet" counts for more than "code". On top of that:
- Tag matches score double compared with matches in the text.
- Adjacent query words found together as a phrase, such as "code review", score a bonus.
- Short terms like "R" or "CI" must match a whole word; longer terms match the start of a word, so "test" finds "testing".
- Tags help the search but are never sent to the model.

## The tracker server: `src/tracker_server/`

It keeps job applications, their status history and versioned drafts in SQLite (`data/tracker.db`, gitignored). It's split into `store.py`, plain SQLite functions holding the rules, and `server.py`, a thin MCP layer on top.

| Tool | Writes? | What it does |
|---|---|---|
| `log_application(company, role, ...)` | Yes, idempotent | Records an application. Logging the same company and role again returns the existing record with `created: false`. |
| `update_status(application_id, status, note)` | Yes, idempotent | Changes the status (interested → applied → interviewing → offer / rejected / withdrawn). Every change is kept in the history. |
| `save_draft(application_id, kind, text)` | Yes | Saves a cover letter, motivation or application answers. Saving again adds a new version; nothing is overwritten. |
| `list_applications(status)`, `get_application(id)`, `get_draft(id, kind, version)` | No | Read applications, history and drafts |

What's different about a server whose tools write:
- **Tool annotations.** Each tool declares `read_only_hint`, `destructive_hint`, `idempotent_hint` and `open_world_hint`, so a client can, for example, ask the user before running a write. No tool here deletes or overwrites anything.
- **Idempotency.** A model may retry a call. Logging an application twice or setting the same status twice changes nothing. A database `UNIQUE` constraint (ignoring case) backs up the duplicate check.
- **Enums in the schema.** Statuses and draft kinds are `Literal` types, so the tool schema lists the allowed values and the model sees them before calling.
- **Errors the model can act on.** In mcp 2.x, only a `ToolError`'s message reaches the model; any other exception is reported as a generic crash, so internals don't leak. Bad input becomes a `ToolError` with a message saying what to do, such as "No application with id 99 - call list_applications to see the ids".
