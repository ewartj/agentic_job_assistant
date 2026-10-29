"""The Phase 2 agent rebuilt on LangChain + LangGraph (Phase 3, step 3).

Same prompt, same MCP servers, same trace format as agent.py, so `job-score`
scores both and the comparison is like for like:

    uv run job-graph data/evals/postings/bbc_mle.md
    uv run job-graph data/evals/postings/bbc_mle.md --provider ollama
    uv run job-graph data/evals/postings/bbc_mle.md --with-tracker   # + tracker-server's read-only tools

What LangChain replaces from agent.py:
    ClaudeProvider / OllamaProvider   -> ChatAnthropic / ChatOllama (one interface)
    to_claude_tools / to_ollama_tools -> model.bind_tools(), done inside create_agent
    run_agent's for-loop              -> create_agent(): a two-node graph, model <-> tools
    the turn cap                      -> LangGraph's recursion_limit

With --graph explicit, the same graph is built by hand instead (state_graph.py).

What stays ours: the prompt, the MCP connection, and the trace that job-score reads.

Why there's an adapter here: langchain-mcp-adapters (the usual way to load MCP
tools into LangChain) requires mcp<2.0, and this project uses mcp 2.x. So
mcp_tools() below does the same job: each MCP tool becomes a StructuredTool whose
schema is the tool's own JSON schema and whose body calls the MCP session.
"""
import argparse
import asyncio
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.errors import GraphRecursionError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cv_server.postings import split_front_matter
from job_agent.agent import (
    CV_ID,
    FULL_CONTEXT_SUFFIX,
    MAX_TURNS,
    OLLAMA_NUM_CTX,
    ROOT,
    RUNS_DIR,
    SYSTEM_PROMPT,
    TASK_TEMPLATE,
    ToolRequest,
    Trace,
    Turn,
    answer_stop,
    call_mcp_tool,
    load_full_cv,
)
from job_agent.state_graph import build_graph

# --- MCP tools -> LangChain tools ---------------------------------------------

def to_langchain_tool(session: ClientSession, tool: Any) -> StructuredTool:
    """Wrap one MCP tool. The model sees the MCP name, description and JSON schema unchanged."""

    async def run(**args: Any) -> tuple[str, list[str]]:
        result = await call_mcp_tool(session, ToolRequest(call_id="", name=tool.name, args=args))
        if result.is_error:
            raise ToolException(result.content)  # becomes an error ToolMessage the model can read
        # (content for the model, artifact for us): the cv ids ride along on the ToolMessage
        # without being shown to the model, so the trace can record what was retrieved.
        return result.content, result.cv_ids

    return StructuredTool(
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.input_schema,
        coroutine=run,
        response_format="content_and_artifact",
        handle_tool_error=True,  # errors go back to the model as text instead of crashing the graph
    )


async def mcp_tools(session: ClientSession, read_only: bool = False) -> list[StructuredTool]:
    """All of a server's tools, or with read_only=True only those annotated read_only_hint."""
    tools = (await session.list_tools()).tools
    if read_only:
        tools = [t for t in tools if t.annotations and t.annotations.read_only_hint]
    return [to_langchain_tool(session, t) for t in tools]


# --- Models -------------------------------------------------------------------

def make_model(provider: str, model: str | None, num_ctx: int = OLLAMA_NUM_CTX) -> BaseChatModel:
    """The same settings as agent.py's providers, so only the framework differs."""
    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model or "claude-haiku-5-5",
            max_tokens=16000,
            output_config={"effort": "medium"},  # same effort as agent.py's ClaudeProvider
        )
    from langchain_ollama import ChatOllama

    # num_ctx matters as much here as in agent.py: Ollama silently drops the oldest
    # messages (the system prompt and posting) when the context overflows.
    return ChatOllama(model=model or "qwen3.5:9b", base_url=os.environ.get("OLLAMA_HOST"), num_ctx=num_ctx)


def model_name(model: BaseChatModel) -> str:
    return getattr(model, "model", None) or getattr(model, "model_name", "unknown")


# --- Messages -> the trace job-score reads ------------------------------------

def stop_reason(message: AIMessage) -> str:
    meta = message.response_metadata or {}
    return meta.get("stop_reason") or meta.get("done_reason") or "unknown"


def to_turns(updates: list[tuple[float, str, list[Any]]]) -> list[Turn]:
    """One Turn per model response, paired with the ToolMessages that answered it.

    `updates` is (seconds, node, messages) for each graph step, in order.
    seconds is the time since the previous model response, i.e. this model call
    plus the tool calls before it - close to, not exactly, agent.py's timing.
    """
    results = {m.tool_call_id: m for _, node, msgs in updates if node == "tools" for m in msgs
               if isinstance(m, ToolMessage)}
    turns = []
    for seconds, node, msgs in updates:
        for msg in msgs:
            if node != "model" or not isinstance(msg, AIMessage):
                continue
            usage = msg.usage_metadata or {}
            calls = []
            for call in msg.tool_calls:
                result = results.get(call["id"])
                calls.append({
                    "name": call["name"],
                    "args": call["args"],
                    "cv_ids": (result.artifact or []) if result else [],
                    "is_error": result is None or result.status == "error",
                })
            turns.append(Turn(text=msg.text, tool_calls=calls, input_tokens=usage.get("input_tokens", 0),
                              output_tokens=usage.get("output_tokens", 0), seconds=round(seconds, 2)))
    return turns


# --- Run ----------------------------------------------------------------------

def build_agent(model: BaseChatModel, tools: list[StructuredTool], system: str = SYSTEM_PROMPT,
                max_turns: int = MAX_TURNS, graph: str = "prebuilt"):
    if graph == "explicit":
        return build_graph(model, tools, system, max_turns)
    return create_agent(model, tools, system_prompt=system)


async def run_graph_agent(
    model: BaseChatModel,
    tools: list[StructuredTool],
    task: str,
    system: str = SYSTEM_PROMPT,
    mode: str = "tools",
    max_turns: int = MAX_TURNS,
    provider: str = "langgraph",
    graph: str = "prebuilt",
) -> Trace:
    """Run either graph: "prebuilt" (create_agent) or "explicit" (state_graph.build_graph)."""
    trace = Trace(task=task, provider=provider, model=model_name(model), mode=mode)
    agent = build_agent(model, tools, system, max_turns, graph)

    # Each turn is two graph steps (model, then tools), so this caps turns like agent.py's loop:
    # the last turn's tools run, then the next model call is refused. (2 * max_turns + 1, used
    # until step 4, allowed one extra model call.) The explicit graph caps turns itself and
    # ends just before this is reached.
    config = {"recursion_limit": 2 * max_turns}
    updates: list[tuple[float, str, list[Any]]] = []
    last = time.perf_counter()
    try:
        # stream_mode="updates" yields what each node added, as it finishes - which is
        # what lets us time and record each step rather than only see the final state.
        async for chunk in agent.astream({"messages": [{"role": "user", "content": task}]}, config,
                                         stream_mode="updates"):
            for node, update in chunk.items():
                now = time.perf_counter()
                updates.append((now - last if node == "model" else 0.0, node, (update or {}).get("messages", [])))
                if node == "model":
                    last = now
    except GraphRecursionError:
        trace.stop = "turn_cap"

    trace.turns = to_turns(updates)
    final = next((m for _, node, msgs in reversed(updates) if node == "model" for m in reversed(msgs)
                  if isinstance(m, AIMessage)), None)
    if final is not None and not trace.stop:
        if final.tool_calls:
            trace.stop = "turn_cap"  # the explicit graph ended after the last turn's tools
        else:
            trace.final_answer = final.text
            trace.stop = answer_stop(final.text, stop_reason(final))
    trace.stop = trace.stop or "unknown"
    trace.cited_ids = sorted(set(CV_ID.findall(trace.final_answer)))
    return trace


def server(name: str) -> StdioServerParameters:
    return StdioServerParameters(command="uv", args=["run", name], cwd=str(ROOT))


async def amain(args: argparse.Namespace) -> None:
    _, posting = split_front_matter(args.posting.read_text(encoding="utf-8"))
    task = TASK_TEMPLATE.format(posting=posting)
    model = make_model(args.provider, args.model, args.num_ctx)

    async with AsyncExitStack() as stack:
        async def connect(name: str) -> ClientSession:
            read, write = await stack.enter_async_context(stdio_client(server(name)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session

        cv = await connect("cv-server")
        if args.mode == "tools":
            system, tools = SYSTEM_PROMPT, await mcp_tools(cv)
            if args.with_tracker:
                # Read-only tracker tools only: an assessment shouldn't write to the tracker.
                tools += await mcp_tools(await connect("tracker-server"), read_only=True)
        else:
            system, tools = SYSTEM_PROMPT + FULL_CONTEXT_SUFFIX.format(cv=await load_full_cv(cv)), []
        if args.draw:
            print(build_agent(model, tools, system, args.max_turns, args.graph).get_graph().draw_mermaid())
            return
        label = "stategraph" if args.graph == "explicit" else "langgraph"
        trace = await run_graph_agent(model, tools, task, system, args.mode, args.max_turns,
                                      provider=f"{label}-{args.provider}", graph=args.graph)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{args.posting.stem}.jsonl"
    with out.open("a", encoding="utf-8") as f:
        f.write(trace.to_json() + "\n")

    calls = [c for t in trace.turns for c in t.tool_calls]
    print(trace.final_answer)
    print("\n---")
    print(f"{trace.provider}/{trace.model} [{args.mode}]  stop={trace.stop}  turns={len(trace.turns)}")
    print(f"tools offered: {[t.name for t in tools]}")
    print(f"tool calls: {[(c['name'], c['args']) for c in calls]}")
    print(f"retrieved:  {trace.retrieved_ids}")
    print(f"cited:      {trace.cited_ids}")
    unseen = sorted(set(trace.cited_ids) - set(trace.retrieved_ids))
    if args.mode == "tools" and unseen:
        print(f"WARNING cited but never retrieved: {unseen}")
    print(f"tokens:     in={sum(t.input_tokens for t in trace.turns)} out={sum(t.output_tokens for t in trace.turns)}")
    print(f"trace appended to {out}")


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Assess fit for a job posting - LangGraph version of job-agent.")
    parser.add_argument("posting", type=Path, help="text/markdown file containing the job posting")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--mode", choices=["tools", "full-context"], default="tools")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--with-tracker", action="store_true",
                        help="also offer tracker-server's read-only tools (applications, drafts)")
    parser.add_argument("--graph", choices=["prebuilt", "explicit"], default="prebuilt",
                        help="create_agent's graph, or the same graph built by hand (state_graph.py)")
    parser.add_argument("--draw", action="store_true", help="print the graph as a Mermaid diagram and exit")
    asyncio.run(amain(parser.parse_args()))
