"""The FastAPI app: opens the apply service at start-up, closes it at shutdown, and maps
the workflow's errors to HTTP status codes.

create_app takes how to open the service as an argument, so tests pass one built from
fake models and in-process servers, while job-web passes the real one (runtime.py).
"""
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from job_agent.events import ProgressEvent
from job_agent.settings import get_settings
from job_agent.web.progress import ProgressHub
from job_agent.web.routes import router
from job_agent.web.runs import PostingNotFound, RunConflict, RunManager
from job_agent.web.schemas import ProgressMessage
from job_agent.workflow.service import ApplyService, NotAwaitingReview, NotStopped, RunNotFound, current_run

# The React dev server (stage 3). Nothing else may call the API from a browser.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

OpenService = Callable[[Callable[[ProgressEvent], None]], AbstractAsyncContextManager[ApplyService]]

ERRORS: dict[type[Exception], int] = {
    RunNotFound: status.HTTP_404_NOT_FOUND,
    PostingNotFound: status.HTTP_404_NOT_FOUND,
    NotAwaitingReview: status.HTTP_409_CONFLICT,
    NotStopped: status.HTTP_409_CONFLICT,
    RunConflict: status.HTTP_409_CONFLICT,
    FileExistsError: status.HTTP_409_CONFLICT,
}


def create_app(open_service: OpenService, postings_dir: Path | None = None, drafts_dir: Path | None = None,
               allowed_origins: list[str] = DEV_ORIGINS) -> FastAPI:
    hub = ProgressHub()

    def progress(event: ProgressEvent) -> None:
        # Called from inside a run's graph: current_run() says which run the event is for.
        hub.publish(current_run(), ProgressMessage(event=event).model_dump())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_service(progress) as service:
            settings = get_settings()
            postings, drafts = postings_dir or settings.postings_dir, drafts_dir or settings.drafts_dir
            app.state.manager = RunManager(service, hub, postings)
            app.state.postings_dir, app.state.drafts_dir = postings, drafts
            try:
                yield
            finally:
                await app.state.manager.shutdown()

    app = FastAPI(title="Job assistant", summary="Assess a posting, plan the letter and CV, approve, save.",
                  lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])
    app.include_router(router, prefix="/api")

    for error, code in ERRORS.items():
        app.add_exception_handler(error, _handler(code))
    return app


def _handler(code: int) -> Callable[[Request, Exception], JSONResponse]:
    def handle(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": str(error)})
    return handle
