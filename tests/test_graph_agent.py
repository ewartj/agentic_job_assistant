"""The LangGraph agent with a scripted fake model against the real MCP servers.

No API calls: the model's replies are scripted, while tools go through the
in-process cv-server (fixture data) and tracker-server (a temporary database).
Most tests run against both graphs - create_agent's and the hand-built one -
since the two should behave the same.
"""
from itertools import count

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from mcp import Client
from pydantic import Field

from cv_server.server import mcp as cv_mcp
from job_agent.graph_agent import build_agent, mcp_tools, run_graph_agent, to_turns
from job_agent.state_graph import build_graph
from tracker_server.server import mcp as tracker_mcp

pytestmark = pytest.mark.anyio


class ScriptedModel(GenericFakeChatModel):
    """Replies from a script, and records what it was sent.

    Both graphs call bind_tools, which a fake can ignore beyond noting the arguments.
    """

    bound_with: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bound_with.append(([t.name for t in tools], kwargs))
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append([(m.type, m.content) for m in messages])
        return super()._generate(messages, *args, **kwargs)


@pytest.fixture(params=["prebuilt", "explicit"])
def graph(request):
    return request.param


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id}


def reply(text="", *calls, stop="end_turn"):
    return AIMessage(content=text, tool_calls=list(calls), response_metadata={"stop_reason": stop},
                     usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


async def run(script, graph="prebuilt", **kwargs):
    async with Client(cv_mcp) as session:
        tools = await mcp_tools(session)
        return await run_graph_agent(ScriptedModel(messages=iter(script)), tools, "assess this", graph=graph, **kwargs)


async def test_tools_keep_their_mcp_name_and_schema():
    async with Client(cv_mcp) as session:
        mcp_side = {t.name: t.input_schema for t in (await session.list_tools()).tools}
        tools = {t.name: t for t in await mcp_tools(session)}
    assert set(tools) == set(mcp_side)
    assert tools["get_cv_evidence"].args_schema == mcp_side["get_cv_evidence"]


async def test_trace_records_tool_calls_retrieved_ids_and_citations(graph):
    trace = await run([
        reply("", call("get_cv_evidence", {"query": "terraform"}, "c1")),
        reply("You provisioned AWS with Terraform [cv-001]."),
    ], graph)
    assert trace.stop == "done"
    assert [c["name"] for t in trace.turns for c in t.tool_calls] == ["get_cv_evidence"]
    assert trace.turns[0].tool_calls[0]["cv_ids"] == ["cv-001", "cv-002"]  # ids come back as the artifact
    assert trace.retrieved_ids == ["cv-001", "cv-002"]
    assert trace.cited_ids == ["cv-001"]
    assert trace.turns[0].input_tokens == 10


async def test_parallel_tool_calls_in_one_turn_are_all_recorded(graph):
    trace = await run([
        reply("", call("get_cv_evidence", {"query": "terraform"}, "c1"), call("get_cv_evidence", {"query": "R"}, "c2")),
        reply("Done [cv-003]."),
    ], graph)
    assert [c["cv_ids"] for c in trace.turns[0].tool_calls] == [["cv-001", "cv-002"], ["cv-003"]]


async def test_tool_errors_reach_the_model_and_the_run_continues(graph):
    trace = await run([
        reply("", call("get_cv_evidence", {}, "c1")),  # missing the required query
        reply("Couldn't search, sorry."),
    ], graph)
    assert trace.turns[0].tool_calls[0]["is_error"]
    assert trace.stop == "done"


async def test_runaway_tool_use_stops_at_the_turn_cap(graph):
    # A model that never stops asking for tools (each call needs its own id, as real ones do).
    script = (reply("", call("get_cv_evidence", {"query": "python"}, f"c{i}")) for i in count())
    trace = await run(script, graph, max_turns=3)
    assert trace.stop == "turn_cap"
    assert len(trace.turns) == 3  # as in agent.py: three model calls, and the last turn's tools run
    assert not any(c["is_error"] for t in trace.turns for c in t.tool_calls)
    assert trace.final_answer == "" and trace.cited_ids == []


async def test_a_non_normal_stop_reason_is_kept(graph):
    trace = await run([reply("I can't help with that.", stop="refusal")], graph)
    assert trace.stop == "refusal"


async def test_a_normal_stop_with_no_text_is_an_empty_answer(graph):
    trace = await run([reply("", call("get_cv_evidence", {"query": "python"}, "c1")), reply("")], graph)
    assert trace.stop == "empty_answer"
    assert trace.final_answer == ""


async def test_read_only_filter_uses_the_tool_annotations(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "tracker.db"))
    async with Client(tracker_mcp) as session:
        all_tools = {t.name for t in await mcp_tools(session)}
        read_only = {t.name for t in await mcp_tools(session, read_only=True)}
    assert read_only == {"list_applications", "get_application", "get_draft"}
    assert all_tools - read_only == {"log_application", "update_status", "save_draft"}


def test_turns_pair_each_call_with_its_result():
    from langchain_core.messages import ToolMessage

    ai = reply("", call("get_cv_evidence", {"query": "x"}, "c1"))
    tool = ToolMessage(content="...", tool_call_id="c1", artifact=["cv-005"])
    (turn,) = to_turns([(1.5, "model", [ai]), (0.0, "tools", [tool])])
    assert turn.tool_calls == [{"name": "get_cv_evidence", "args": {"query": "x"}, "cv_ids": ["cv-005"],
                                "is_error": False}]
    assert turn.seconds == 1.5


# --- The hand-built graph ------------------------------------------------------

SCRIPT = [
    reply("", call("get_cv_evidence", {"query": "terraform"}, "c1")),
    reply("You provisioned AWS with Terraform [cv-001]."),
]


async def test_both_graphs_send_the_model_the_same_thing():
    sent = {}
    async with Client(cv_mcp) as session:
        tools = await mcp_tools(session)
        for graph in ["prebuilt", "explicit"]:
            model = ScriptedModel(messages=iter(SCRIPT))
            await run_graph_agent(model, tools, "assess this", system="be brief", graph=graph)
            # create_agent re-binds the tools on every call and passes tool_choice=None;
            # the explicit graph binds once with nothing. Both mean "the model chooses".
            bindings = {(tuple(names), tuple((k, v) for k, v in kw.items() if v is not None))
                        for names, kw in model.bound_with}
            sent[graph] = (bindings, model.seen)
    assert sent["prebuilt"] == sent["explicit"]
    bindings, seen = sent["explicit"]
    assert bindings == {(tuple(t.name for t in tools), ())}
    assert seen[0] == [("system", "be brief"), ("human", "assess this")]


async def test_state_counts_turns_and_gathers_retrieved_ids():
    async with Client(cv_mcp) as session:
        agent = build_graph(ScriptedModel(messages=iter([
            reply("", call("get_cv_evidence", {"query": "terraform"}, "c1"),
                  call("get_cv_evidence", {"query": "R"}, "c2")),
            reply("Done [cv-003]."),
        ])), await mcp_tools(session), "system")
        state = await agent.ainvoke({"messages": [{"role": "user", "content": "assess this"}]})
    assert state["turns"] == 2
    assert sorted(state["retrieved_ids"]) == ["cv-001", "cv-002", "cv-003"]


async def test_an_unknown_tool_is_an_error_the_model_sees():
    trace = await run([reply("", call("no_such_tool", {}, "c1")), reply("Sorry.")], "explicit")
    assert trace.turns[0].tool_calls[0]["is_error"]
    assert trace.stop == "done"


async def test_full_context_mode_has_no_tools_and_answers_in_one_turn(graph):
    model = ScriptedModel(messages=iter([reply("Fits well [cv-002].")]))
    trace = await run_graph_agent(model, [], "assess this", mode="full-context", graph=graph)
    assert trace.stop == "done" and len(trace.turns) == 1 and trace.cited_ids == ["cv-002"]


def test_the_explicit_graph_draws_with_both_routes():
    diagram = build_agent(ScriptedModel(messages=iter([])), [], graph="explicit").get_graph().draw_mermaid()
    assert "model -.-> tools" in diagram and "model -.-> __end__" in diagram
    assert "tools -.-> model" in diagram and "tools -.-> __end__" in diagram
