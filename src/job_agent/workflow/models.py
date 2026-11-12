"""What the apply workflow exchanges with whoever drives it: the CLI now, the web API next.

Plain Pydantic models with no LangGraph or web types in them, so the API can validate
requests with them and publish a schema the frontend's TypeScript types come from.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# running          the graph is working (only known to the process running it)
# awaiting_review  paused at the review, waiting for a Decision
# stopped          ended part-way through, e.g. a model call failed; retry continues it
# saved            approved and written to the tracker
# rejected         rejected at the review; nothing was written
RunStatus = Literal["running", "awaiting_review", "stopped", "saved", "rejected"]

ArtifactKind = Literal["cover_letter_plan", "cv"]


class Artifact(BaseModel):
    """One output under review: the letter plan or the CV."""
    kind: ArtifactKind
    verified: bool = Field(description="Every point was checked and none is unsupported")
    points: int
    unsupported: int
    dropped: int
    drafts: int
    plan: str = Field(description="The plan as Markdown, with each point's citations")
    plan_path: str
    document_path: str | None = Field(default=None, description="The .docx, for the CV")
    warnings: list[str] = Field(default_factory=list)


class Review(BaseModel):
    """What a run shows when it pauses for your decision."""
    run_id: str
    posting: str
    company: str
    role: str
    assessment: str = Field(description="Requirement-by-requirement verdicts, as Markdown")
    verdicts: dict[str, int] = Field(description="How many requirements are met, partly met, not met")
    artifacts: list[Artifact]
    round: int = Field(description="1 for the first review, then 1 more after each revision")


class Decision(BaseModel):
    action: Literal["approve", "revise", "reject"]
    notes: str = Field(default="", description="What to change - required to revise")
    target: Literal["both", "cover_letter_plan", "cv"] = "both"

    @model_validator(mode="after")
    def revise_needs_notes(self) -> "Decision":
        if self.action == "revise" and not self.notes.strip():
            raise ValueError("say what to change when revising")
        return self


class RunView(BaseModel):
    """A run as anyone outside the workflow sees it."""
    run_id: str
    posting: str
    status: RunStatus
    review: Review | None = None
    application_id: int | None = None
