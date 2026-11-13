"""Runs in the background, so no request waits a minute (or ten, on Ollama) for a model.

Starting, deciding or retrying launches a task and returns at once; the page follows
the task's progress over server-sent events. A run's real state always comes from the
service (and so the checkpointer): this class only adds what a web server knows on top -
which runs are going right now, and why a run that stopped did.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from cv_server.postings import posting_path
from job_agent.web.progress import ProgressHub
from job_agent.workflow.models import Decision, RunView
from job_agent.workflow.service import ApplyService, NotAwaitingReview, NotStopped, RunNotFound

log = logging.getLogger(__name__)


class PostingNotFound(LookupError):
    pass


class RunConflict(RuntimeError):
    """The request clashes with work already going, e.g. a second run of the same posting."""


class RunManager:
    def __init__(self, service: ApplyService, hub: ProgressHub, postings_dir: Path):
        self.service = service
        self.hub = hub
        self.postings_dir = postings_dir
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._postings: dict[str, str] = {}  # run id -> posting, for runs not yet checkpointed
        self._errors: dict[str, str] = {}  # run id -> why it stopped, from this process

    async def start(self, posting: str) -> RunView:
        path = posting_path(self.postings_dir, posting)  # ValueError for an unsafe name
        if not path.exists():
            raise PostingNotFound(f"no saved posting named {posting!r}")
        if posting in {self._postings.get(run_id) for run_id in self._tasks}:
            raise RunConflict(f"{posting} is already being worked on")
        run_id = self.service.new_run_id(path)
        self._postings[run_id] = posting
        self._launch(run_id, lambda: self.service.start(path, run_id))
        return RunView(run_id=run_id, posting=posting, status="running")

    async def decide(self, run_id: str, decision: Decision) -> RunView:
        view = await self.view(run_id)
        if view.status != "awaiting_review":
            raise NotAwaitingReview(f"run {run_id} isn't waiting for a review (it's {view.status})")
        self._launch(run_id, lambda: self.service.decide(run_id, decision))
        return view.model_copy(update={"status": "running", "review": None})

    async def retry(self, run_id: str) -> RunView:
        view = await self.view(run_id)
        if view.status != "stopped":
            raise NotStopped(f"run {run_id} hasn't stopped part-way (it's {view.status})")
        self._launch(run_id, lambda: self.service.retry(run_id))
        return view.model_copy(update={"status": "running", "error": None})

    def _launch(self, run_id: str, work: Callable[[], Awaitable[RunView]]) -> None:
        self._errors.pop(run_id, None)
        self.hub.reset(run_id)

        async def run() -> None:
            try:
                await work()
            except asyncio.CancelledError:
                self._errors[run_id] = "the server shut down - retry to carry on"
                raise
            except Exception as error:  # noqa: BLE001 - any failure stops this run, not the server
                log.exception("run %s failed", run_id)
                self._errors[run_id] = f"{type(error).__name__}: {error}"
            finally:
                self._tasks.pop(run_id, None)
                await self._announce_end(run_id)

        self._tasks[run_id] = asyncio.create_task(run(), name=f"run {run_id}")

    async def _announce_end(self, run_id: str) -> None:
        try:
            view = await self.view(run_id)
            self.hub.publish(run_id, {"type": "status", "status": view.status, "error": view.error})
        except RunNotFound:  # failed before its first step was saved
            self.hub.publish(run_id, {"type": "status", "status": "stopped", "error": self._errors.get(run_id)})
        self.hub.publish(run_id, {"type": "end"})

    def is_running(self, run_id: str) -> bool:
        return run_id in self._tasks

    async def view(self, run_id: str) -> RunView:
        if run_id in self._tasks:
            try:
                view = await self.service.view(run_id)
            except RunNotFound:  # just started: nothing checkpointed yet
                view = RunView(run_id=run_id, posting=self._postings[run_id], status="running")
            return view.model_copy(update={"status": "running", "review": None})
        view = await self.service.view(run_id)
        if view.status == "stopped":
            view.error = self._errors.get(run_id)
        return view

    async def runs(self) -> list[RunView]:
        saved = {view.run_id: view for view in await self.service.runs()}
        for run_id in self._tasks:
            saved[run_id] = await self.view(run_id)
        return sorted(saved.values(), key=lambda v: v.run_id.rsplit("-", 2)[-2:], reverse=True)

    async def wait(self, run_id: str) -> None:
        """Until the run's current work finishes (for tests, and a clean shutdown)."""
        task = self._tasks.get(run_id)
        if task:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        """Stop work in progress; each run keeps its last saved step and can be retried."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
