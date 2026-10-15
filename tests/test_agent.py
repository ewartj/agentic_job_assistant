"""Agent loop tests with a scripted fake model - no API keys, no cost.

The in-process server (Client(mcp)) uses the fixture data, so ids are cv-001..cv-005.
"""
import pytest
from mcp import Client

from cv_server.server import mcp
from job_agent.agent import (
    LLMStep,
    ToolRequest,
    ToolResult,
    Trace,
    Turn,
    call_mcp_tool,
    run_agent,
    to_claude_tools,
    to_ollama_tools,
)

pytestmark = pytest.mark.anyio


def unwrap(group: BaseException) -> BaseException:
    """The in-process client wraps errors in ExceptionGroups - dig out the real one."""
    while isinstance(group, BaseExceptionGroup) and len(group.exceptions) == 1:
        group = group.exceptions[0]
    return group


async def run_scripted(provider: "ScriptedProvider", **kwargs) -> Trace:
    try:
        async with Client(mcp) as client:
            return await run_agent(client, provider, "assess fit", **kwargs)
    except BaseExceptionGroup as group:
        raise unwrap(group) from None


class ScriptedProvider:
    """Plays back fixed LLMSteps and records what the loop sent it."""

    name = "ollama"  # so run_agent uses to_ollama_tools (already written)
    model = "scripted"

    def __init__(self, steps: list[LLMStep]):
        self.steps = list(steps)
        self.system = ""
        self.tools: list[dict] = []
        self.received: list[list[ToolResult]] = []

    def start(self, system, task, tools):
        self.system, self.tools = system, tools

    async def step(self) -> LLMStep:
        return self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]  # last step repeats

    def add_tool_results(self, results):
        self.received.append(results)


def asks(*calls: tuple[str, dict]) -> LLMStep:
    requests = [ToolRequest(f"call-{i}", name, args) for i, (name, args) in enumerate(calls)]
    return LLMStep("", requests, "tool_use", 10, 5)


def answers(text: str, stop_reason: str = "end_turn") -> LLMStep:
    return LLMStep(text, [], stop_reason, 20, 30)


# --- Helpers ------------------------------------------------------------------

async def test_call_mcp_tool_extracts_evidence_ids():
    async with Client(mcp) as client:
        result = await call_mcp_tool(client, ToolRequest("c1", "get_cv_evidence", {"query": "terraform"}))
    assert result.cv_ids == ["cv-001", "cv-002"]
    assert not result.is_error
    assert "cv-001" in result.content


async def test_call_mcp_tool_reports_errors():
    async with Client(mcp) as client:
        result = await call_mcp_tool(client, ToolRequest("c1", "get_cv_evidence", {}))
    assert result.is_error
    assert result.cv_ids == []


async def test_ollama_tool_conversion():
    async with Client(mcp) as client:
        tools = to_ollama_tools((await client.list_tools()).tools)
    by_name = {t["function"]["name"]: t for t in tools}
    assert set(by_name) == {"list_past_applications", "get_cv_evidence", "get_past_letters"}
    assert by_name["get_cv_evidence"]["function"]["parameters"]["required"] == ["query"]


def test_trace_retrieved_ids_spans_all_turns():
    trace = Trace(task="t", provider="p", model="m", mode="tools", turns=[
        Turn("", [{"name": "a", "args": {}, "cv_ids": ["cv-002", "cv-001"], "is_error": False}], 0, 0, 0.0),
        Turn("", [{"name": "a", "args": {}, "cv_ids": ["cv-001", "cv-003"], "is_error": False}], 0, 0, 0.0),
    ])
    assert trace.retrieved_ids == ["cv-001", "cv-002", "cv-003"]


# --- to_claude_tools --------------------------------------------------------

async def test_claude_tool_conversion():
    async with Client(mcp) as client:
        mcp_tools = (await client.list_tools()).tools
    tools = to_claude_tools(mcp_tools)
    by_name = {t["name"]: t for t in tools}
    assert set(by_name) == {"list_past_applications", "get_cv_evidence", "get_past_letters"}
    assert set(by_name["get_cv_evidence"]) == {"name", "description", "input_schema"}
    assert by_name["get_cv_evidence"]["input_schema"]["required"] == ["query"]


# --- run_agent --------------------------------------------------------------

async def test_tool_call_then_answer():
    provider = ScriptedProvider([
        asks(("get_cv_evidence", {"query": "terraform"})),
        answers("Strong Terraform evidence [cv-001]."),
    ])
    trace = await run_scripted(provider)

    assert trace.stop == "done"
    assert len(trace.turns) == 2
    assert trace.final_answer == "Strong Terraform evidence [cv-001]."
    assert trace.retrieved_ids == ["cv-001", "cv-002"]
    assert trace.cited_ids == ["cv-001"]
    assert [r.name for r in provider.received[0]] == ["get_cv_evidence"]
    assert {t["function"]["name"] for t in provider.tools} >= {"get_cv_evidence"}


async def test_all_calls_in_a_turn_go_back_together():
    provider = ScriptedProvider([
        asks(("get_cv_evidence", {"query": "terraform"}), ("get_cv_evidence", {"query": "mentoring"})),
        answers("done [cv-001] [cv-005]"),
    ])
    trace = await run_scripted(provider)

    assert len(provider.received) == 1 and len(provider.received[0]) == 2
    assert trace.retrieved_ids == ["cv-001", "cv-002", "cv-005"]


async def test_tool_errors_are_sent_back_not_raised():
    provider = ScriptedProvider([asks(("get_cv_evidence", {})), answers("No evidence found.")])
    trace = await run_scripted(provider)

    assert provider.received[0][0].is_error
    assert trace.turns[0].tool_calls[0]["is_error"] is True
    assert trace.stop == "done"


async def test_turn_cap_stops_a_model_that_never_answers():
    provider = ScriptedProvider([asks(("get_cv_evidence", {"query": "terraform"}))])  # repeats forever
    trace = await run_scripted(provider, max_turns=3)

    assert trace.stop == "turn_cap"
    assert len(trace.turns) == 3
    assert trace.final_answer == ""


async def test_abnormal_stop_reason_is_recorded():
    provider = ScriptedProvider([answers("", stop_reason="refusal")])
    trace = await run_scripted(provider)

    assert trace.stop == "refusal"


async def test_full_context_mode_puts_cv_in_prompt_and_offers_no_tools():
    provider = ScriptedProvider([answers("Fits [cv-003].")])
    trace = await run_scripted(provider, mode="full-context")

    assert provider.tools == []
    assert "cv-005" in provider.system  # the whole fixture CV is in the system prompt
    assert trace.cited_ids == ["cv-003"]
    assert trace.retrieved_ids == []
