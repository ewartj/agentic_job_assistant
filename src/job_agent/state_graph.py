"""create_agent's graph built by hand with StateGraph (Phase 3, step 4).

The same two nodes and the same request to the model, so traces and scores
match graph_agent.py's prebuilt version - but every part is now visible:

    START -> model --(tool calls)--> tools --(under the turn cap)--> model
                   \\--(answered)--> END     \\--(turn cap reached)--> END

What create_agent was doing for us, and where it lives here:
    the state (messages + append reducer) -> AgentState
    calling the model with the tools bound -> call_model
    running the tool calls                 -> run_tools (ToolNode, written out)
    deciding what runs next                -> after_model, after_tools
    the turn cap (recursion_limit)         -> a `turns` counter in the state

The state also carries `retrieved_ids`, gathered as tools return. Nothing reads it
yet; step 5's verifier will, to check citations without re-reading the messages.
"""
import asyncio
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from job_agent.agent import MAX_TURNS


class AgentState(TypedDict, total=False):
    # Each field's reducer says how a node's return value is merged into the state:
    # add_messages appends (and replaces by message id); operator.add concatenates.
    # A field without one, like `turns`, is simply overwritten.
    messages: Annotated[list[AnyMessage], add_messages]
    turns: int
    retrieved_ids: Annotated[list[str], operator.add]


def build_graph(model: BaseChatModel, tools: list[BaseTool], system: str, max_turns: int = MAX_TURNS):
    # The same binding create_agent does: no tool_choice, so the model decides.
    bound = model.bind_tools(tools) if tools else model
    by_name = {t.name: t for t in tools}

    async def call_model(state: AgentState) -> dict[str, Any]:
        # The system prompt isn't stored in the state; it's added to every call,
        # as create_agent does, so the state holds only the conversation.
        reply = await bound.ainvoke([SystemMessage(system), *state["messages"]])
        return {"messages": [reply], "turns": state.get("turns", 0) + 1}

    async def call_tool(call: dict[str, Any]) -> ToolMessage:
        tool = by_name.get(call["name"])
        if tool is None:
            return ToolMessage(f"Unknown tool {call['name']!r}", tool_call_id=call["id"], status="error")
        # Invoking with the whole tool call (not just its args) returns a ToolMessage
        # carrying the call id and the artifact; handle_tool_error turns a
        # ToolException into an error ToolMessage instead of raising.
        return await tool.ainvoke({**call, "type": "tool_call"})

    async def run_tools(state: AgentState) -> dict[str, Any]:
        calls = state["messages"][-1].tool_calls
        # All of a turn's calls run concurrently (agent.py ran them one by one).
        results = await asyncio.gather(*(call_tool(c) for c in calls))
        return {"messages": list(results), "retrieved_ids": [i for r in results for i in (r.artifact or [])]}

    def after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END  # the model has answered

    def after_tools(state: AgentState) -> str:
        # The turn cap, decided by us rather than by an exception. As in agent.py, the
        # last turn's tools still run, and the model never sees their results.
        return END if state.get("turns", 0) >= max_turns else "model"

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "model")
    # The lists name each router's possible results, so the drawn graph shows every edge.
    graph.add_conditional_edges("model", after_model, ["tools", END])
    graph.add_conditional_edges("tools", after_tools, ["model", END])
    return graph.compile()
