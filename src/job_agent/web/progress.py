"""Live progress for each run, for the page to follow over server-sent events.

The workflow reports progress as lines of text; the hub keeps each run's recent events
and hands them to every page watching that run - the backlog first, so a page opened
part-way through still sees the whole run, then new events as they happen.
"""
import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

HISTORY = 1000  # events kept per run
HEARTBEAT = 15.0  # seconds between keep-alives while a run is quiet

Event = dict[str, Any]  # a message from schemas.py (progress, status or end), as a dict


class ProgressHub:
    def __init__(self) -> None:
        self._history: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=HISTORY))
        self._watchers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    def reset(self, run_id: str) -> None:
        """A new stretch of work on the run (a start, decision or retry) starts a fresh log."""
        self._history.pop(run_id, None)

    def publish(self, run_id: str | None, event: Event) -> None:
        if run_id is None:  # progress from outside any run, e.g. a test calling a node directly
            return
        self._history[run_id].append(event)
        for queue in self._watchers[run_id]:
            queue.put_nowait(event)

    def has_ended(self, run_id: str) -> bool:
        history = self._history.get(run_id)
        return not history or history[-1]["type"] == "end"

    async def follow(self, run_id: str) -> AsyncIterator[Event | None]:
        """The run's events until it ends; None is a heartbeat, so the connection stays open."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._watchers[run_id].add(queue)
        try:
            for event in list(self._history.get(run_id, ())):
                yield event
                if event["type"] == "end":
                    return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), HEARTBEAT)
                except TimeoutError:
                    yield None
                    continue
                yield event
                if event["type"] == "end":
                    return
        finally:
            self._watchers[run_id].discard(queue)
