"""What a run records: one Turn per model call, and the Trace that job-score reads.

Shared by every agent and graph, so their runs are scored the same way.
"""
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

CV_ID = re.compile(r"\bcv-\d{3}\b")


@dataclass
class Turn:
    text: str
    tool_calls: list[dict[str, Any]]  # name, args, cv_ids, is_error
    input_tokens: int
    output_tokens: int
    seconds: float



@dataclass
class Trace:
    task: str
    provider: str
    model: str
    mode: str
    turns: list[Turn] = field(default_factory=list)
    final_answer: str = ""
    stop: str = ""  # "done" | "turn_cap" | "empty_answer" | the provider's stop reason (e.g. "refusal")
    cited_ids: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def retrieved_ids(self) -> list[str]:
        """Every evidence id any tool call returned - citations outside this set were never seen."""
        return sorted({i for t in self.turns for c in t.tool_calls for i in c["cv_ids"]})

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "retrieved_ids": self.retrieved_ids}, ensure_ascii=False)
