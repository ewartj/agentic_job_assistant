"""The apply workflow's one entry point for anything that drives it - CLI or web API.

    service = ApplyService(graph)       # graph from build_apply_graph, with a checkpointer
    view = await service.start(posting) # runs until the review (or the end)
    view = await service.decide(view.run_id, Decision(action="approve"))

Callers see RunView, Review and Decision (models.py), never LangGraph's own types: the
graph and how runs are stored can change without the CLI or the API changing.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from cv_server.postings import split_front_matter
from job_agent.workflow.models import Decision, Review, RunView

# The project's own types inside a run's state. Checkpoints only ever load these
# (plus LangGraph's and the standard ones), so a tampered database can't make the
# loader build arbitrary objects.
CHECKPOINT_TYPES = [("job_agent.pipeline", "Requirement"), ("job_agent.agent", "Turn")]


# The run whose graph is executing in this task. Graph nodes run in tasks created
# inside it, which inherit it, so a progress callback can tell which run a line is for.
_current_run: ContextVar[str | None] = ContextVar("current_run", default=None)


def current_run() -> str | None:
    return _current_run.get()


class RunNotFound(LookupError):
    pass


class NotAwaitingReview(RuntimeError):
    pass


class NotStopped(RuntimeError):
    pass


@asynccontextmanager
async def open_checkpointer(path: Path) -> AsyncIterator[AsyncSqliteSaver]:
    """A SQLite checkpointer that persists runs in `path` across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        saver = AsyncSqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))
        await saver.setup()
        yield saver


class ApplyService:
    def __init__(self, graph: Any, max_concurrency: int | None = None):
        self.graph = graph  # compiled with a checkpointer
        self.max_concurrency = max_concurrency  # e.g. 1 for Ollama: the letter and CV take turns
        self._running: set[str] = set()

    @staticmethod
    def _config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    @staticmethod
    def new_run_id(posting: Path) -> str:
        """A run's id, made before it starts so a caller can hand it out straight away."""
        return f"{posting.stem}-{datetime.now():%Y%m%d-%H%M%S}"

    async def start(self, posting: Path, run_id: str | None = None) -> RunView:
        """Assess the posting and draft both documents; returns when the run pauses for review."""
        meta, text = split_front_matter(posting.read_text(encoding="utf-8"))
        run_id = run_id or self.new_run_id(posting)
        inputs = {"run_id": run_id, "posting": text, "posting_name": posting.stem,
                  "company": str(meta.get("company") or ""), "role": str(meta.get("role") or ""),
                  "url": str(meta.get("url") or "")}
        await self._run(run_id, inputs)
        return await self.view(run_id)

    async def decide(self, run_id: str, decision: Decision) -> RunView:
        """Resume a paused run with your decision: save it, revise it, or reject it."""
        if (await self.view(run_id)).status != "awaiting_review":
            raise NotAwaitingReview(f"run {run_id} isn't waiting for a review")
        await self._run(run_id, Command(resume=decision.model_dump()))
        return await self.view(run_id)

    async def retry(self, run_id: str) -> RunView:
        """Carry on a run that stopped part-way, from its last saved step - not from scratch."""
        if (await self.view(run_id)).status != "stopped":
            raise NotStopped(f"run {run_id} hasn't stopped part-way")
        await self._run(run_id, None)
        return await self.view(run_id)

    async def _run(self, run_id: str, inputs: Any) -> None:
        self._running.add(run_id)
        token = _current_run.set(run_id)
        try:
            await self.graph.ainvoke(inputs, {**self._config(run_id), "max_concurrency": self.max_concurrency})
        finally:
            _current_run.reset(token)
            self._running.discard(run_id)

    async def view(self, run_id: str) -> RunView:
        snapshot = await self.graph.aget_state(self._config(run_id))
        values = snapshot.values
        if not values:
            raise RunNotFound(f"no run {run_id}")
        view = RunView(run_id=run_id, posting=values.get("posting_name", ""), status="saved",
                       application_id=values.get("application_id"))
        if snapshot.interrupts:
            view.status, view.review = "awaiting_review", Review.model_validate(snapshot.interrupts[0].value)
        elif snapshot.next:  # steps left but not paused: still going, or it failed part-way
            view.status = "running" if run_id in self._running else "stopped"
        elif (values.get("decision") or {}).get("action") == "reject":
            view.status = "rejected"
        return view

    async def runs(self) -> list[RunView]:
        """Every run, newest first."""
        ids: dict[str, None] = {}
        async for checkpoint in self.graph.checkpointer.alist(None):
            ids.setdefault(checkpoint.config["configurable"]["thread_id"], None)
        views = [await self.view(run_id) for run_id in ids]
        return sorted(views, key=lambda v: v.run_id.rsplit("-", 2)[-2:], reverse=True)
