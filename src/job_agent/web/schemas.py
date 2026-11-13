"""Request and response bodies the API adds to the workflow's own models (RunView,
Review, Decision), which it returns as they are."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, RootModel

from job_agent.events import ProgressEvent
from job_agent.workflow.models import RunStatus

# --- Messages on /runs/{run_id}/events, one per "data:" line ----------------------------

class ProgressMessage(BaseModel):
    type: Literal["progress"] = "progress"
    event: ProgressEvent


class StatusMessage(BaseModel):
    """Sent when a stretch of work ends: where the run now stands."""
    type: Literal["status"] = "status"
    status: RunStatus
    error: str | None = None


class EndMessage(BaseModel):
    """The last message: the stream closes after it."""
    type: Literal["end"] = "end"


class StreamMessage(RootModel[Annotated[ProgressMessage | StatusMessage | EndMessage, Field(discriminator="type")]]):
    """Any message on the events stream - documented in the API schema for the frontend's types."""


class PostingSummary(BaseModel):
    name: str
    company: str | None = None
    role: str | None = None
    url: str | None = None
    saved: str | None = None


class Posting(PostingSummary):
    text: str


class NewPosting(BaseModel):
    name: str = Field(description="Letters, digits, '_' and '-', starting with a letter or digit, e.g. acme_data_eng")
    text: str = Field(description="The advert, pasted")
    company: str | None = None
    role: str | None = None
    url: str | None = None
    replace: bool = Field(default=False, description="Overwrite a saved posting with this name")


class StartRun(BaseModel):
    posting: str = Field(description="A saved posting's name")


class Health(BaseModel):
    status: str = "ok"
