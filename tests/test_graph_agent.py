"""The LangGraph agent with a scripted fake model against the real MCP servers.

No API calls: the model's replies are scripted, while tools go through the
in-process cv-server (fixture data) and tracker-server (a temporary database).
"""
from itertools import count

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from mcp import Client

from cv_server.server import mcp as cv_mcp
from job_agent.graph_agent import mcp_tools, run_graph_agent, to_turns
from tracker_server.server import mcp as tracker_mcp

pytestmark = pytest.mark.anyio


class ScriptedModel(GenericFakeChatModel):
    """Replies from a script; create_agent calls bind_tools, which a fake can ignore."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id}


def reply(text="", *calls, stop="end_turn"):
    return AIMessage(content=text, tool_calls=list(calls), response_metadata={"stop_reason": stop},
                     usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


async def run(script, **kwargs):
    async with Client(cv_mcp) as session:
        tools = await mcp_tools(session)
        return await run_graph_agent(ScriptedModel(messages=iter(script)), tools, "assess this", **kwargs)


async def test_tools_keep_their_mcp_name_and_schema():
    async with Client(cv_mcp) as session:
        mcp_side = {t.name: t.input_schema for t in (await session.list_tools()).tools}
        tools = {t.name: t for t in await mcp_tools(session)}
    assert set(tools) == set(mcp_side)
    assert tools["get_cv_evidence"].args_schema == mcp_side["get_cv_evidence"]


async def test_trace_records_tool_calls_retrieved_ids_and_citations():
    trace = await run([
        reply("", call("get_cv_evidence", {"query": "terraform"}, "c1")),
        reply("You provisioned AWS with Terraform [cv-001]."),
    ])
    assert trace.stop == "done"
    assert [c["name"] for t in trace.turns for c in t.tool_calls] == ["get_cv_evidence"]
    assert trace.turns[0].tool_calls[0]["cv_ids"] == ["cv-001", "cv-002"]  # ids come back as the artifact
    assert trace.retrieved_ids == ["cv-001", "cv-002"]
    assert trace.cited_ids == ["cv-001"]
    assert trace.turns[0].input_tokens == 10


async def test_parallel_tool_calls_in_one_turn_are_all_recorded():
    trace = await run([
        reply("", call("get_cv_evidence", {"query": "terraform"}, "c1"), call("get_cv_evidence", {"query": "R"}, "c2")),
        reply("Done [cv-003]."),
    ])
    assert [c["cv_ids"] for c in trace.turns[0].tool_calls] == [["cv-001", "cv-002"], ["cv-003"]]


async def test_tool_errors_reach_the_model_and_the_run_continues():
    trace = await run([
        reply("", call("get_cv_evidence", {}, "c1")),  # missing the required query
        reply("Couldn't search, sorry."),
    ])
    assert trace.turns[0].tool_calls[0]["is_error"]
    assert trace.stop == "done"


async def test_runaway_tool_use_stops_at_the_turn_cap():
    # A model that never stops asking for tools (each call needs its own id, as real ones do).
    script = (reply("", call("get_cv_evidence", {"query": "python"}, f"c{i}")) for i in count())
    trace = await run(script, max_turns=3)
    assert trace.stop == "turn_cap"
    assert trace.final_answer == "" and trace.cited_ids == []


async def test_a_non_normal_stop_reason_is_kept():
    trace = await run([reply("I can't help with that.", stop="refusal")])
    assert trace.stop == "refusal"


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
