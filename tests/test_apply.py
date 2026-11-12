"""The apply workflow: pause for review, then approve, revise or reject - with real
persistence (a SQLite checkpointer on disk), fake models, and the in-process servers.
"""
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from mcp import Client
from pydantic import ValidationError
from test_cv import files  # noqa: F401 - the generated CV template and employers, as a fixture
from test_letter import USAGE, FakeModel

from cv_server.server import mcp as cv_mcp
from job_agent.letter import Outline, Paragraph, Point
from job_agent.workflow.graph import ApplySettings, build_apply_graph
from job_agent.workflow.models import Decision
from job_agent.workflow.service import ApplyService, NotAwaitingReview, RunNotFound, open_checkpointer
from tracker_server.server import mcp as tracker_mcp

pytestmark = pytest.mark.anyio

POSTING = "---\ncompany: Acme Health\nrole: Engineer\nurl: https://example.com/job\n---\nWe need Terraform.\n"
LETTER = Outline(paragraphs=[Paragraph(title="Opening", purpose="Why.", points=[
    Point(text="I provisioned AWS with Terraform.", cv_ids=["cv-001"]),
    Point(text="I'd love to join.", cv_ids=[])])])
CV = Outline(paragraphs=[
    Paragraph(title="Profile", purpose="Lead.",
              points=[Point(text="Engineer who uses Terraform.", cv_ids=["cv-001"])]),
    Paragraph(title="acme", purpose="Infra.",
              points=[Point(text="Provisioned AWS with Terraform.", cv_ids=["cv-001"])]),
])


class ApplyModel(FakeModel):
    """Plans the letter or the CV depending on which prompt asks; records each request."""

    def __init__(self):
        super().__init__([])
        self.planned = []  # ("letter" | "cv", the messages)

    def with_structured_output(self, schema, **kwargs):
        if schema is not Outline:
            return super().with_structured_output(schema, **kwargs)

        async def plan(messages):
            kind = "cv" if "tailor the user's CV" in messages[0].content else "letter"
            self.planned.append((kind, [m.content for m in messages]))
            return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": CV if kind == "cv" else LETTER,
                    "parsing_error": None}

        return RunnableLambda(plan)


@pytest.fixture
def setup(tmp_path, monkeypatch, files):  # noqa: F811
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "tracker.db"))
    posting = tmp_path / "acme_eng.md"
    posting.write_text(POSTING, encoding="utf-8")
    template, employers = files
    return tmp_path, posting, template, employers


@asynccontextmanager
async def service(setup, model):
    """An ApplyService over the given model; a new one each time, sharing the database on disk."""
    tmp_path, _, template, employers = setup
    async with Client(cv_mcp) as cv, Client(tracker_mcp) as tracker, \
            open_checkpointer(tmp_path / "runs.db") as checkpointer:
        settings = ApplySettings(model=model, cv=cv, tracker=tracker, employers=employers, template=template,
                                 drafts_dir=tmp_path / "drafts")
        yield ApplyService(build_apply_graph(settings, checkpointer)), tracker


async def applications(tracker):
    return (await tracker.call_tool("list_applications", {})).structured_content["result"]


async def test_a_run_pauses_for_review_with_both_documents_and_writes_nothing(setup):
    model = ApplyModel()
    async with service(setup, model) as (svc, tracker):
        view = await svc.start(setup[1])

        assert view.status == "awaiting_review"
        review = view.review
        assert (review.company, review.role, review.round) == ("Acme Health", "Engineer", 1)
        assert review.verdicts == {"met": 1, "partly met": 0, "not met": 0}
        letter, cv = review.artifacts
        assert (letter.kind, letter.verified, letter.points) == ("cover_letter_plan", True, 2)
        assert (cv.kind, cv.verified, cv.points) == ("cv", True, 2)
        assert cv.document_path.endswith("acme_eng.cv.docx")
        assert (setup[0] / "drafts" / "acme_eng.cover_letter_plan.md").exists()
        assert await applications(tracker) == []  # nothing saved before you approve
    assert sorted(kind for kind, _ in model.planned) == ["cv", "letter"]  # matched once, both planned


async def test_approving_saves_the_application_and_all_three_drafts(setup):
    async with service(setup, ApplyModel()) as (svc, tracker):
        paused = await svc.start(setup[1])
        view = await svc.decide(paused.run_id, Decision(action="approve"))

        assert view.status == "saved" and view.review is None
        (app,) = await applications(tracker)
        assert (app["company"], app["role"], app["posting"], app["url"], app["status"]) == (
            "Acme Health", "Engineer", "acme_eng", "https://example.com/job", "interested")
        detail = (await tracker.call_tool("get_application", {"application_id": app["id"]})).structured_content
        assert sorted(d["kind"] for d in detail["drafts"]) == ["assessment", "cover_letter_plan", "cv"]
        assert view.application_id == app["id"]


async def test_rejecting_writes_nothing(setup):
    async with service(setup, ApplyModel()) as (svc, tracker):
        paused = await svc.start(setup[1])
        view = await svc.decide(paused.run_id, Decision(action="reject"))

        assert view.status == "rejected"
        assert await applications(tracker) == []


async def test_revising_the_letter_redrafts_only_the_letter_with_your_notes(setup):
    model = ApplyModel()
    async with service(setup, model) as (svc, _):
        paused = await svc.start(setup[1])
        view = await svc.decide(paused.run_id, Decision(action="revise", target="cover_letter_plan",
                                                        notes="Lead with the Terraform work."))

    assert view.status == "awaiting_review" and view.review.round == 2
    assert [kind for kind, _ in model.planned].count("cv") == 1  # the CV wasn't redone
    kind, messages = model.planned[-1]
    assert kind == "letter"
    assert "Lead with the Terraform work." in messages[-1]
    assert "I provisioned AWS with Terraform. [cv-001]" in messages[-1]  # the version you reviewed


async def test_a_paused_run_resumes_from_a_new_process(setup):
    model = ApplyModel()
    async with service(setup, model) as (svc, _):
        run_id = (await svc.start(setup[1])).run_id
    # A new service, graph and database connection: only what's on disk carries over.
    async with service(setup, model) as (svc, tracker):
        assert (await svc.view(run_id)).status == "awaiting_review"
        view = await svc.decide(run_id, Decision(action="approve"))

        assert view.status == "saved" and len(await applications(tracker)) == 1
    assert len(model.planned) == 2  # nothing was drafted again


async def test_runs_are_listed_and_unknown_or_finished_runs_are_refused(setup):
    async with service(setup, ApplyModel()) as (svc, _):
        run_id = (await svc.start(setup[1])).run_id
        assert [v.run_id for v in await svc.runs()] == [run_id]
        await svc.decide(run_id, Decision(action="reject"))

        with pytest.raises(NotAwaitingReview):
            await svc.decide(run_id, Decision(action="approve"))
        with pytest.raises(RunNotFound):
            await svc.view("no-such-run")


def test_revising_without_notes_is_refused():
    with pytest.raises(ValidationError, match="say what to change"):
        Decision(action="revise")
