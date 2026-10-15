"""Hand-written agent loop over the cv-server MCP tools.

No framework: this is the loop that LangGraph's create_react_agent hides.
This will be rebuilt as a LangGraph graph - keep this version to compare against.

    uv run job-agent data/evals/postings/bbc_mle.md --provider claude
    uv run job-agent data/evals/postings/bbc_mle.md --provider ollama --mode full-context
"""
import argparse
import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import anthropic
import ollama
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "data" / "evals" / "runs"  # gitignored, like the rest of data/
MAX_TURNS = 8
CV_ID = re.compile(r"\bcv-\d{3}\b")
# Stop reasons that mean "finished normally" (Claude: end_turn, Ollama: stop).
# Anything else without tool calls - refusal, max_tokens, length - is recorded as-is.
NORMAL_STOPS = {"end_turn", "stop"}

SYSTEM_PROMPT = """\
You assess how well the user's experience fits a job posting.

Break the posting into its main requirements. For each one, search the user's \
CV evidence and judge whether it is met, partly met or not met. Back every claim \
about the user with the id of the evidence it comes from, written like [cv-012]. \
Only cite ids you have actually seen. If you find no evidence for a requirement, \
say so - don't guess.

Finish with a short overall assessment."""

FULL_CONTEXT_SUFFIX = """

The user's complete CV evidence is below as JSON. There are no tools in this mode; \
cite ids from this list.

{cv}"""


# --- What a run records -------------------------------------------------------

@dataclass
class ToolRequest:
    """A tool call the model asked for."""
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    """What we send back to the model for one ToolRequest."""
    call_id: str
    name: str
    content: str  # text the model reads
    is_error: bool = False
    cv_ids: list[str] = field(default_factory=list)  # evidence ids returned (for evals)


@dataclass
class LLMStep:
    """One model response, normalised across providers."""
    text: str
    tool_calls: list[ToolRequest]
    stop_reason: str  # provider's own value, e.g. "end_turn", "tool_use", "refusal"
    input_tokens: int
    output_tokens: int


@dataclass
class Turn:
    text: str
    tool_calls: list[dict[str, Any]]  # name, args, cv_ids, is_error
    input_tokens: int
    output_tokens: int
    seconds: float

    @classmethod
    def from_step(cls, step: LLMStep, results: list[ToolResult], seconds: float) -> "Turn":
        """Pair each tool request with its result (strict: a mismatch is a bug, not something to skip)."""
        return cls(
            text=step.text,
            tool_calls=[
                {"name": call.name, "args": call.args, "cv_ids": result.cv_ids, "is_error": result.is_error}
                for call, result in zip(step.tool_calls, results, strict=True)
            ],
            input_tokens=step.input_tokens,
            output_tokens=step.output_tokens,
            seconds=round(seconds, 2),
        )


@dataclass
class Trace:
    task: str
    provider: str
    model: str
    mode: str
    turns: list[Turn] = field(default_factory=list)
    final_answer: str = ""
    stop: str = ""  # "done" | "turn_cap" | the provider's stop reason (e.g. "refusal")
    cited_ids: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def retrieved_ids(self) -> list[str]:
        """Every evidence id any tool call returned - citations outside this set were never seen."""
        return sorted({i for t in self.turns for c in t.tool_calls for i in c["cv_ids"]})

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "retrieved_ids": self.retrieved_ids}, ensure_ascii=False)


# --- Providers: each owns its message history in its own format ---------------

class Provider(Protocol):
    name: str
    model: str

    def start(self, system: str, task: str, tools: list[dict[str, Any]]) -> None: ...
    async def step(self) -> LLMStep: ...
    def add_tool_results(self, results: list[ToolResult]) -> None: ...


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str = "claude-haiku-5-5", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY (or an `ant auth login` profile)
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, task: str, tools: list[dict[str, Any]]) -> None:
        self.system, self.tools = system, tools
        self.messages = [{"role": "user", "content": task}]

    async def step(self) -> LLMStep:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": self.system,
            "messages": self.messages,
            "output_config": {"effort": self.effort},
        }
        if self.tools:
            request["tools"] = self.tools
        response = await self.client.messages.create(**request)

        # Append the whole response unchanged - including thinking blocks, which
        # Haiku 5.5 produces by default. Never edit earlier turns: history is append-only.
        self.messages.append({"role": "assistant", "content": response.content})
        return LLMStep(
            text="".join(b.text for b in response.content if b.type == "text"),
            tool_calls=[ToolRequest(b.id, b.name, b.input) for b in response.content if b.type == "tool_use"],
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # All results for one turn go back in ONE user message, matched by tool_use_id.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]})


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str = "qwen3.5:9b", host: str | None = None):
        self.model = model
        self.client = ollama.AsyncClient(host=host)  # host=None -> OLLAMA_HOST env var, else localhost
        self.messages: list[Any] = []

    def start(self, system: str, task: str, tools: list[dict[str, Any]]) -> None:
        self.tools = tools
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]

    async def step(self) -> LLMStep:
        response = await self.client.chat(model=self.model, messages=self.messages, tools=self.tools or None)
        self.messages.append(response.message)
        # Ollama has no call ids, so make our own; results are matched by tool_name instead.
        calls = [
            ToolRequest(f"call-{i}", tc.function.name, dict(tc.function.arguments))
            for i, tc in enumerate(response.message.tool_calls or [])
        ]
        return LLMStep(
            text=response.message.content or "",
            tool_calls=calls,
            stop_reason="tool_use" if calls else (response.done_reason or "stop"),
            input_tokens=response.prompt_eval_count or 0,
            output_tokens=response.eval_count or 0,
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            self.messages.append({"role": "tool", "tool_name": r.name, "content": r.content})


# --- MCP tools -> LLM tools ---------------------------------------------------

def to_ollama_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """Worked example: Ollama wants OpenAI-style function definitions."""
    return [
        {"type": "function", "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.input_schema,
        }}
        for t in mcp_tools
    ]


def to_claude_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """Convert MCP tools to Claude's format.

    Claude wants a flat dict per tool: {"name", "description", "input_schema"}.
    Compare with to_ollama_tools above - it's the same three values, different keys.
    (In mcp 2.x the MCP attribute is t.input_schema, not t.inputSchema.)
    """
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.input_schema,
        }
        for t in mcp_tools
    ]


async def call_mcp_tool(session: ClientSession, request: ToolRequest) -> ToolResult:
    """Run one tool call against the MCP server and package the result for the model."""
    result = await session.call_tool(request.name, request.args)
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None)) or "(no content)"
    items = (result.structured_content or {}).get("result", [])
    cv_ids = [i["id"] for i in items if isinstance(i, dict) and str(i.get("id", "")).startswith("cv-")]
    return ToolResult(request.call_id, request.name, text, is_error=bool(result.is_error), cv_ids=cv_ids)


async def load_full_cv(session: ClientSession) -> str:
    """Read the cv://full resource - used by --mode full-context instead of tools."""
    resource = await session.read_resource("cv://full")
    return resource.contents[0].text


async def build_prompt_and_tools(
    session: ClientSession, provider: Provider, mode: str
) -> tuple[str, list[dict[str, Any]]]:
    """Tools mode offers the MCP tools; full-context mode puts the whole CV in the prompt and offers none."""
    if mode == "tools":
        mcp_tools = (await session.list_tools()).tools
        tools = to_claude_tools(mcp_tools) if provider.name == "claude" else to_ollama_tools(mcp_tools)
        return SYSTEM_PROMPT, tools
    return SYSTEM_PROMPT + FULL_CONTEXT_SUFFIX.format(cv=await load_full_cv(session)), []


# --- The loop -----------------------------------------------------------------

async def run_agent(
    session: ClientSession,
    provider: Provider,
    task: str,
    *,
    mode: str = "tools",
    max_turns: int = MAX_TURNS,
) -> Trace:
    """Run the model until it answers without asking for a tool, or max_turns is reached.

    One trip round the loop is one turn: send the conversation, get a reply, run
    whatever tools it asked for, and send all their results back together.
    """
    trace = Trace(task=task, provider=provider.name, model=provider.model, mode=mode)
    system, tools = await build_prompt_and_tools(session, provider, mode)
    provider.start(system, task, tools)

    for _ in range(max_turns):
        started = time.perf_counter()
        step = await provider.step()

        # Run every tool the model asked for this turn ([] when it has answered).
        results = [await call_mcp_tool(session, call) for call in step.tool_calls]

        # Record before deciding whether to stop, so the answering turn is included.
        trace.turns.append(Turn.from_step(step, results, seconds=time.perf_counter() - started))

        if not step.tool_calls:
            # No tool calls means the model has answered. A Claude "refusal" or
            # "max_tokens" stop is kept as-is so it shows up in the trace.
            trace.final_answer = step.text
            trace.stop = "done" if step.stop_reason in NORMAL_STOPS else step.stop_reason
            break

        # All results for the turn go back together, in one message.
        provider.add_tool_results(results)
    else:
        # The loop ran out without a `break`: the model never stopped asking for tools.
        trace.stop = "turn_cap"

    trace.cited_ids = sorted(set(CV_ID.findall(trace.final_answer)))
    return trace


# --- CLI ----------------------------------------------------------------------

TASK_TEMPLATE = """\
Assess my fit for this role using my CV evidence.

<posting>
{posting}
</posting>"""


def make_provider(name: str, model: str | None) -> Provider:
    if name == "claude":
        return ClaudeProvider(model) if model else ClaudeProvider()
    return OllamaProvider(model) if model else OllamaProvider()


async def amain(args: argparse.Namespace) -> None:
    task = TASK_TEMPLATE.format(posting=args.posting.read_text(encoding="utf-8"))
    provider = make_provider(args.provider, args.model)
    server = StdioServerParameters(command="uv", args=["run", "cv-server"], cwd=str(ROOT))

    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        trace = await run_agent(session, provider, task, mode=args.mode, max_turns=args.max_turns)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{args.posting.stem}.jsonl"
    with out.open("a", encoding="utf-8") as f:
        f.write(trace.to_json() + "\n")

    calls = [c for t in trace.turns for c in t.tool_calls]
    print(trace.final_answer)
    print("\n---")
    print(f"{provider.name}/{provider.model} [{args.mode}]  stop={trace.stop}  turns={len(trace.turns)}")
    print(f"tool calls: {[(c['name'], c['args']) for c in calls]}")
    print(f"retrieved:  {trace.retrieved_ids}")
    print(f"cited:      {trace.cited_ids}")
    unseen = sorted(set(trace.cited_ids) - set(trace.retrieved_ids))
    if args.mode == "tools" and unseen:
        print(f"WARNING cited but never retrieved: {unseen}")
    print(f"tokens:     in={sum(t.input_tokens for t in trace.turns)} out={sum(t.output_tokens for t in trace.turns)}")
    print(f"trace appended to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assess fit for a job posting using cv-server tools.")
    parser.add_argument("posting", type=Path, help="text/markdown file containing the job posting")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--mode", choices=["tools", "full-context"], default="tools")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    asyncio.run(amain(parser.parse_args()))
