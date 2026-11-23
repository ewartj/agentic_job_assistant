"""Your applications, for the tracker pages: read from tracker-server over MCP, as typed models.

The app reaches the tracker only through its MCP tools, as the apply graph does - never by
opening its database - so the server could run elsewhere without this changing. The models
here are what the API returns, so the frontend gets types for them from the schema.
"""
from typing import Any, Literal

from mcp import ClientSession
from pydantic import BaseModel, Field

# The tracker's own lists (tracker_server/store.py). This package may not import the server,
# so they're repeated here; a test checks the two stay the same.
ApplicationStatus = Literal["interested", "applied", "interviewing", "offer", "rejected", "withdrawn"]
DraftKind = Literal["cover_letter", "motivation", "application_answers", "assessment", "cover_letter_plan", "cv"]


class ApplicationNotFound(LookupError):
    """No such application, or no such draft of it."""


class TrackerRefused(ValueError):
    """The tracker turned the request down, e.g. an unknown status."""


class Application(BaseModel):
    id: int
    company: str
    role: str
    posting: str | None = Field(default=None, description="The saved posting it came from, if any")
    url: str | None = None
    status: ApplicationStatus
    applied_on: str | None = Field(default=None, description="YYYY-MM-DD, or just YYYY-MM or YYYY for old ones")
    notes: str | None = None
    created_at: str
    updated_at: str


class StatusChange(BaseModel):
    status: ApplicationStatus
    note: str | None = None
    changed_at: str


class DraftSummary(BaseModel):
    id: int
    kind: DraftKind
    version: int = Field(description="1, then one more each time a draft of this kind is saved again")
    characters: int
    created_at: str


class ApplicationDetail(Application):
    history: list[StatusChange] = Field(description="Every status it has had, oldest first")
    drafts: list[DraftSummary] = Field(description="Every version of every draft, without the text")


class Draft(BaseModel):
    application_id: int
    kind: DraftKind
    version: int
    text: str
    created_at: str


class StatusUpdate(BaseModel):
    status: ApplicationStatus
    note: str | None = Field(default=None, description="Why it changed, e.g. 'first interview on 3 Nov'")


class Tracker:
    def __init__(self, session: ClientSession):
        self.session = session

    async def applications(self) -> list[Application]:
        """Most recently updated first."""
        result = await self._call("list_applications", {})
        return [Application.model_validate(a) for a in result["result"]]

    async def application(self, application_id: int) -> ApplicationDetail:
        return ApplicationDetail.model_validate(await self._call("get_application", {"application_id": application_id}))

    async def update_status(self, application_id: int, update: StatusUpdate) -> ApplicationDetail:
        """Kept in the history; setting the status it already has changes nothing."""
        args = {"application_id": application_id, "status": update.status, "note": update.note}
        return ApplicationDetail.model_validate(await self._call("update_status", args))

    async def draft(self, application_id: int, kind: DraftKind, version: int | None = None) -> Draft:
        """The latest version, unless one is given."""
        args = {"application_id": application_id, "kind": kind, "version": version}
        return Draft.model_validate(await self._call("get_draft", args))

    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(tool, args)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content).strip()
            text = text.removeprefix(f"Error executing tool {tool}: ")  # added by the MCP server
            # The tracker's message for a missing application or draft starts "No ..." - the
            # one thing that says which error it is, since MCP tool errors carry only text.
            raise (ApplicationNotFound if text.startswith("No ") else TrackerRefused)(text)
        return result.structured_content or {}
