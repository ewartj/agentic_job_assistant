"""The extract -> match -> report pipeline with a fake model against the real cv-server.

No API calls: the fake returns structured output from a script, while the
searches go through the in-process cv-server (fixture data).
"""
import asyncio

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.types import RetryPolicy
from mcp import Client

from cv_server.server import mcp as cv_mcp
from job_agent import pipeline
from job_agent.agent import CV_ID
from job_agent.pipeline import Judgement, Requirement, Requirements, build_pipeline, render, run_pipeline

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(pipeline, "RETRY", RetryPolicy(max_attempts=3, initial_interval=0.01, jitter=False))


class FakeModel:
    """Stands in for a chat model's with_structured_output.

    extract returns the scripted requirements; each judge call cites the ids it
    was shown (or `judge_ids` for a requirement name), after `delay` seconds.
    """

    def __init__(self, requirements, judge_ids=None, delay=None, fail=(), drop=None):
        self.requirements = requirements
        self.judge_ids = judge_ids or {}
        self.delay = delay or {}
        self.fail = set(fail)
        self.drop = dict(drop or {})  # requirement name -> times to drop the connection first
        self.judged = []  # (requirement name, evidence ids shown) in the order calls started
        self.finished = []  # requirement names in the order calls finished

    def with_structured_output(self, schema, **kwargs):
        async def run(messages):
            raw = AIMessage("", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
            if schema is Requirements:
                return {"raw": raw, "parsed": self.requirements, "parsing_error": None}
            prompt = messages[-1].content
            name = prompt.split(": ", 1)[1].split("\n", 1)[0]
            shown = sorted(set(CV_ID.findall(prompt)))
            self.judged.append((name, shown))
            if self.drop.get(name):
                self.drop[name] -= 1
                raise httpx.ReadError("connection dropped")
            await asyncio.sleep(self.delay.get(name, 0))
            self.finished.append(name)
            if name in self.fail:
                return {"raw": raw, "parsed": None, "parsing_error": "bad json"}
            ids = self.judge_ids.get(name, shown)
            verdict = "met" if ids else "gap"
            return {"raw": raw, "parsed": Judgement(verdict=verdict, cv_ids=ids, summary=f"You have {name}."),
                    "parsing_error": None}

        return RunnableLambda(run)


def req(name, *queries, kind="essential"):
    return Requirement(name=name, kind=kind, queries=list(queries))


TERRAFORM = req("Terraform", "terraform")
R = req("R", "R", kind="desired")
KUBERNETES = req("Kubernetes", "kubernetes")


async def run(model):
    async with Client(cv_mcp) as session:
        return await run_pipeline(model, session, "the posting")


async def test_each_requirement_is_searched_and_judged_on_its_own_evidence():
    model = FakeModel(Requirements(requirements=[TERRAFORM, R]))
    trace = await run(model)

    assert sorted(model.judged) == [("R", ["cv-003"]), ("Terraform", ["cv-001", "cv-002"])]
    assert trace.stop == "done"
    assert len(trace.turns) == 3  # extract, then one per requirement
    assert [c["args"]["query"] for c in trace.turns[1].tool_calls] == ["terraform"]
    assert trace.retrieved_ids == ["cv-001", "cv-002", "cv-003"]
    assert trace.cited_ids == ["cv-001", "cv-002", "cv-003"]


async def test_all_of_a_requirements_queries_run_and_their_ids_are_recorded():
    model = FakeModel(Requirements(requirements=[req("Infra", "terraform", "R")]))
    trace = await run(model)

    assert [c["cv_ids"] for c in trace.turns[1].tool_calls] == [["cv-001", "cv-002"], ["cv-003"]]
    assert model.judged == [("Infra", ["cv-001", "cv-002", "cv-003"])]


async def test_matches_run_in_parallel_but_report_in_the_postings_order():
    # Terraform's judge is slowest, so it finishes last - but it's first in the posting.
    model = FakeModel(Requirements(requirements=[TERRAFORM, KUBERNETES, R]), delay={"Terraform": 0.2})
    trace = await run(model)

    assert model.finished[-1] == "Terraform"  # run one after another, it would have finished first
    assert [t.text.split(":")[0] for t in trace.turns[1:]] == ["Terraform", "Kubernetes", "R"]
    report = trace.final_answer
    assert report.index("Terraform") < report.index("Kubernetes") < report.index("Desired") < report.index("**R:")


async def test_a_requirement_with_no_evidence_is_a_gap_with_no_citations():
    trace = await run(FakeModel(Requirements(requirements=[KUBERNETES])))

    assert "**Kubernetes: Not met.**" in trace.final_answer
    assert trace.cited_ids == [] and trace.retrieved_ids == []
    assert "0 met, 0 partly met, 1 not met of 1" in trace.final_answer


async def test_a_judgement_that_fails_to_parse_is_reported_and_the_rest_continue():
    trace = await run(FakeModel(Requirements(requirements=[TERRAFORM, R]), fail={"Terraform"}))

    assert "**Terraform: Not judged.** Could not judge: bad json" in trace.final_answer
    assert "**R: Met.**" in trace.final_answer
    assert trace.cited_ids == ["cv-003"]


async def test_an_id_the_judge_was_never_shown_shows_up_as_made_up():
    trace = await run(FakeModel(Requirements(requirements=[R]), judge_ids={"R": ["cv-003", "cv-008"]}))

    assert sorted(set(trace.cited_ids) - set(trace.retrieved_ids)) == ["cv-008"]


async def test_no_requirements_skips_matching():
    trace = await run(FakeModel(Requirements(requirements=[])))

    assert trace.stop == "no_requirements"
    assert len(trace.turns) == 1
    assert trace.final_answer == "No requirements were found in the posting."


async def test_extract_that_fails_to_parse_is_no_requirements():
    model = FakeModel(None)
    trace = await run(model)

    assert trace.stop == "no_requirements"
    assert model.judged == []


async def test_progress_reports_extract_then_each_match_as_it_finishes():
    lines = []
    model = FakeModel(Requirements(requirements=[TERRAFORM, R]), delay={"Terraform": 0.2})
    async with Client(cv_mcp) as session:
        await run_pipeline(model, session, "the posting", progress=lines.append)

    assert len(lines) == 4
    assert "extracting requirements" in lines[0]
    assert lines[1].endswith("extracted 2 requirements")
    assert "R: met ['cv-003']" in lines[2]  # R finishes first, so it's reported first
    assert "Terraform: met ['cv-001', 'cv-002']" in lines[3]


async def test_a_dropped_connection_is_retried_and_the_run_completes():
    model = FakeModel(Requirements(requirements=[TERRAFORM, R]), drop={"Terraform": 2})
    trace = await run(model)

    assert [name for name, _ in model.judged].count("Terraform") == 3  # two drops, then success
    assert "**Terraform: Met.**" in trace.final_answer and trace.stop == "done"


async def test_a_connection_that_keeps_dropping_still_fails_the_run():
    with pytest.raises(BaseException) as raised:  # surfaces wrapped in ExceptionGroups
        await run(FakeModel(Requirements(requirements=[TERRAFORM]), drop={"Terraform": 3}))
    assert "connection dropped" in repr(raised.value) or raised.group_contains(httpx.ReadError)


async def test_max_concurrency_1_runs_the_matches_one_at_a_time():
    model = FakeModel(Requirements(requirements=[TERRAFORM, R]), delay={"Terraform": 0.2})
    async with Client(cv_mcp) as session:
        await run_pipeline(model, session, "the posting", max_concurrency=1)

    assert model.finished == ["Terraform", "R"]  # in parallel, the slow Terraform would finish last


def test_render_groups_by_kind_and_counts_verdicts():
    def m(i, r, verdict, ids):
        return {"index": i, "requirement": r, "verdict": verdict, "cv_ids": ids, "summary": "S.", "turn": None}

    text = render([m(1, R, "partly", ["cv-003"]), m(0, TERRAFORM, "met", ["cv-001", "cv-002"])])
    assert text.splitlines()[:2] == ["## Essential requirements", ""]
    assert "- **Terraform: Met.** S. [cv-001] [cv-002]" in text
    assert "- **R: Partly met.** S. [cv-003]" in text
    assert text.endswith("**Overall:** 1 met, 1 partly met, 0 not met of 2.")


def test_the_graph_fans_out_to_match_and_joins_at_report():
    diagram = build_pipeline(FakeModel(None), session=None).get_graph().draw_mermaid()
    assert "extract -.-> match" in diagram and "extract -.-> report" in diagram
    assert "match --> report" in diagram
