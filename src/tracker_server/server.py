"""tracker-server: job applications and drafts in SQLite, over MCP (stdio).

The first server here with write tools. Each tool declares annotations - hints
that let a client decide, for example, to ask the user before running a write:

    read_only_hint    doesn't change anything
    destructive_hint  may delete or overwrite data (none of these tools do)
    idempotent_hint   calling it twice with the same arguments = calling it once
    open_world_hint   talks to the outside world (no: a local database)

They're hints, not guarantees - a client shouldn't trust them from a server it doesn't trust.

Errors: only a ToolError's message reaches the model. Any other exception counts
as a crash, and the model just sees "Error executing tool X" (so internals don't
leak). store.py raises ValueError for bad input; _db() turns those into ToolErrors.
"""
import os
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from tracker_server import store
from tracker_server.store import DraftKind, Status

# data/ is gitignored (personal data); tests point TRACKER_DB at a temporary file.
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "tracker.db"

mcp = MCPServer("tracker-server")

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@contextmanager
def _db() -> Iterator[Connection]:
    # Read the path on every call (not at import), so tests can switch databases.
    with closing(store.connect(Path(os.environ.get("TRACKER_DB", DEFAULT_DB)))) as conn:
        try:
            yield conn
        except ValueError as e:  # bad input: tell the model what was wrong, so it can retry
            raise ToolError(str(e)) from e


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                      idempotent_hint=True, open_world_hint=False))
def log_application(
    company: str,
    role: str,
    posting: str | None = None,
    url: str | None = None,
    status: Status = "interested",
    notes: str | None = None,
    applied_on: str | None = None,
) -> dict[str, Any]:
    """Record a job application (or one the user is considering) in the tracker.

    There is one application per company + role. If it's already logged, nothing
    changes and the existing record comes back with `created: false` - use
    update_status to change it.

    Args:
        company: Company name, e.g. "Acme Health".
        role: Job title, e.g. "Senior Data Scientist".
        posting: Name of the saved posting, if there is one, e.g. "acme_ds".
        url: Link to the job advert.
        status: Where the application is now. Defaults to "interested" (not applied yet).
        notes: Anything worth remembering, e.g. a referral or closing date.
        applied_on: When the user applied, as YYYY-MM-DD, if it wasn't today.
    """
    with _db() as conn:
        application, created = store.log_application(conn, company, role, posting, url, status, notes, applied_on)
    return application | {"created": created}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                      idempotent_hint=True, open_world_hint=False))
def update_status(application_id: int, status: Status, note: str | None = None) -> dict[str, Any]:
    """Move an application to a new status, e.g. "applied" -> "interviewing".

    Every change is kept in the application's history, with the optional note.

    Args:
        application_id: The application's `id`, from list_applications or log_application.
        status: The new status.
        note: Why it changed, e.g. "first interview on 3 Nov".
    """
    with _db() as conn:
        return store.update_status(conn, application_id, status, note)


@mcp.tool(annotations=READ)
def list_applications(status: Status | None = None) -> list[dict[str, Any]]:
    """List tracked applications, most recently updated first.

    Args:
        status: Only applications with this status. Leave out for all of them.
    """
    with _db() as conn:
        return store.list_applications(conn, status)


@mcp.tool(annotations=READ)
def get_application(application_id: int) -> dict[str, Any]:
    """Get one application with its full status history and a list of its drafts.

    Draft text isn't included - use get_draft for that.

    Args:
        application_id: The application's `id`.
    """
    with _db() as conn:
        return store.get_application(conn, application_id)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                      idempotent_hint=False, open_world_hint=False))
def save_draft(application_id: int, kind: DraftKind, text: str) -> dict[str, Any]:
    """Save a draft cover letter or application answers for an application.

    Saving again adds a new version; earlier versions are kept.

    Args:
        application_id: The application's `id`.
        kind: What the draft is: a cover letter, a short "why us" motivation, or answers to application questions.
        text: The full draft text.
    """
    with _db() as conn:
        return store.save_draft(conn, application_id, kind, text)


@mcp.tool(annotations=READ)
def get_draft(application_id: int, kind: DraftKind, version: int | None = None) -> dict[str, Any]:
    """Get a draft's text - the latest version unless `version` is given.

    Args:
        application_id: The application's `id`.
        kind: Which kind of draft.
        version: A specific version number, from get_application's draft list.
    """
    with _db() as conn:
        return store.get_draft(conn, application_id, kind, version)


def main() -> None:
    mcp.run()  # stdio by default
