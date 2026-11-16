"""Progress as typed events, not lines of text.

The graphs report what they're doing as these events. The terminal turns them into
the familiar lines (describe); the web API sends them as they are, so the React app
can show each requirement being matched and each point being checked, not just a log.
They're Pydantic models, so they appear in the API schema and the frontend's types.
"""
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal["cover_letter_plan", "cv"]
LABELS = {"cover_letter_plan": "letter", "cv": "cv"}


class _Event(BaseModel):
    artifact: ArtifactKind | None = Field(default=None, description="Which document this is about; none for the "
                                                                    "shared assessment")
    elapsed: float = Field(default=0.0, description="Seconds since this piece of work started")


class Started(_Event):
    step: Literal["started"] = "started"
    model: str
    shared_assessment: bool = Field(description="Drafting from an assessment already done, rather than a fresh one")


class Extracted(_Event):
    step: Literal["extracted"] = "extracted"
    requirements: int


class Matched(_Event):
    step: Literal["matched"] = "matched"
    requirement: str
    kind: str = Field(description="essential or desired")
    verdict: str = Field(description="met, partly, gap, or error")


class Drafted(_Event):
    step: Literal["drafted"] = "drafted"
    attempt: int = Field(description="1 for the first draft, then each redraft")
    to_check: int = Field(description="Points that are new or changed, so will be checked")
    dropped: int = 0


class Checked(_Event):
    step: Literal["checked"] = "checked"
    attempt: int
    point: str
    cv_ids: list[str]
    supported: bool | None = Field(description="None when the check itself couldn't be completed")
    problem: str = ""


ProgressEvent = Annotated[Started | Extracted | Matched | Drafted | Checked, Field(discriminator="step")]


def describe(event: ProgressEvent) -> str | None:
    """The event as a line for the terminal, or None for ones it doesn't show (passed checks)."""
    prefix = (f"[{LABELS[event.artifact]}] " if event.artifact else "") + f"[{event.elapsed:4.0f}s] "
    match event:
        case Started(shared_assessment=True):
            return f"{prefix}drafting from the shared assessment with {event.model}..."
        case Started():
            return f"{prefix}finding example letters, then extracting requirements with {event.model}..."
        case Extracted():
            return f"{prefix}extracted {event.requirements} requirements"
        case Matched():
            return f"{prefix}  {event.requirement}: {event.verdict}"
        case Drafted(attempt=1):
            return f"{prefix}plan written; checking its {event.to_check} points..."
        case Drafted():
            return (f"{prefix}draft {event.attempt}: {event.to_check} points fixed, {event.dropped} dropped; "
                    "rechecking the fixed ones...")
        case Checked(supported=False):
            return f"{prefix}  unsupported: \"{event.point[:70]}...\" {event.cv_ids}: {event.problem}"
    return None


def print_progress(event: ProgressEvent) -> None:
    """The progress callback for the terminal commands: lines go to stderr, so a
    command's output can still be redirected on its own."""
    if line := describe(event):
        print(line, file=sys.stderr, flush=True)
