"""The HTTP API. Each route validates the request, calls the run manager or the postings
store, and returns a Pydantic model - no workflow logic lives here.

Errors are mapped to status codes in app.py: unknown run or posting 404; a request that
clashes with the run's state (deciding on a run that isn't paused, a second run of the
same posting) 409; an invalid name or decision 422.
"""
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from cv_server.postings import NAME, list_postings, posting_path, save_posting, split_front_matter
from job_agent.web.runs import RunManager
from job_agent.web.schemas import Health, NewPosting, Posting, PostingSummary, StartRun
from job_agent.workflow.models import Decision, RunView

router = APIRouter()

# What can be downloaded for a run: never a path from the request, only these names,
# built from the run's own posting name.
FILES: dict[str, tuple[str, str]] = {
    "cover_letter_plan": ("{}.cover_letter_plan.md", "text/markdown; charset=utf-8"),
    "cv_plan": ("{}.cv_plan.md", "text/markdown; charset=utf-8"),
    "cv": ("{}.cv.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}
FileKind = Literal["cover_letter_plan", "cv_plan", "cv"]


def manager(request: Request) -> RunManager:
    return request.app.state.manager


def postings_dir(request: Request) -> Path:
    return request.app.state.postings_dir


def drafts_dir(request: Request) -> Path:
    return request.app.state.drafts_dir


Runs = Annotated[RunManager, Depends(manager)]
PostingsDir = Annotated[Path, Depends(postings_dir)]
DraftsDir = Annotated[Path, Depends(drafts_dir)]


@router.get("/health")
async def health() -> Health:
    return Health()


# --- Postings ------------------------------------------------------------------------

@router.get("/postings")
async def get_postings(directory: PostingsDir) -> list[PostingSummary]:
    return [PostingSummary(**p) for p in list_postings(directory)]


@router.get("/postings/{name}")
async def get_posting(name: str, directory: PostingsDir) -> Posting:
    path = _posting_file(directory, name)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no saved posting named {name!r}")
    meta, text = split_front_matter(path.read_text(encoding="utf-8"))
    return Posting(name=name, text=text.strip(), **{k: None if meta.get(k) is None else str(meta[k])
                                            for k in ("company", "role", "url", "saved")})


@router.post("/postings", status_code=status.HTTP_201_CREATED)
async def add_posting(new: NewPosting, directory: PostingsDir) -> Posting:
    try:
        save_posting(directory, new.name, new.text, replace=new.replace,
                     company=new.company, role=new.role, url=new.url)
    except ValueError as error:  # unsafe name, or no text
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return await get_posting(new.name, directory)


def _posting_file(directory: Path, name: str) -> Path:
    try:
        return posting_path(directory, name)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


# --- Runs ----------------------------------------------------------------------------

@router.get("/runs")
async def get_runs(runs: Runs) -> list[RunView]:
    return await runs.runs()


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: StartRun, runs: Runs) -> RunView:
    """Starts the run in the background; follow it at /runs/{run_id}/events."""
    try:
        return await runs.start(body.posting)
    except ValueError as error:  # unsafe posting name
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@router.get("/runs/{run_id}")
async def get_run(run_id: str, runs: Runs) -> RunView:
    return await runs.view(run_id)


@router.post("/runs/{run_id}/decision", status_code=status.HTTP_202_ACCEPTED)
async def decide(run_id: str, decision: Decision, runs: Runs) -> RunView:
    """Approve (save to the tracker), revise with notes, or reject a run waiting for review."""
    return await runs.decide(run_id, decision)


@router.post("/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry(run_id: str, runs: Runs) -> RunView:
    """Carry on a run that stopped part-way, from its last saved step."""
    return await runs.retry(run_id)


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def events(run_id: str, runs: Runs) -> StreamingResponse:
    """Server-sent events: progress lines while the run works, then its status, then end."""
    await runs.view(run_id)  # 404 for an unknown run, before the stream starts

    async def stream() -> AsyncIterator[str]:
        if not runs.is_running(run_id) and runs.hub.has_ended(run_id):
            # Nothing going on (or the server restarted): just say where the run is.
            view = await runs.view(run_id)
            for event in ({"type": "status", "status": view.status, "error": view.error}, {"type": "end"}):
                yield f"data: {json.dumps(event)}\n\n"
            return
        async for event in runs.hub.follow(run_id):
            yield ": keep-alive\n\n" if event is None else f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/files/{kind}", response_class=FileResponse)
async def download(run_id: str, kind: FileKind, runs: Runs,
                   directory: DraftsDir) -> FileResponse:
    view = await runs.view(run_id)
    if not NAME.fullmatch(view.posting):  # the posting name comes from the run, but check anyway
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    pattern, media_type = FILES[kind]
    path = (directory / pattern.format(view.posting)).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {kind} for run {run_id} yet")
    return FileResponse(path, media_type=media_type, filename=path.name)
