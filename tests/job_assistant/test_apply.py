"""The apply workflow: pause for review, then approve, revise or reject - with real
persistence (a SQLite checkpointer on disk), fake models, and the in-process servers.
"""
from contextlib import asynccontextmanager

import pytest
from fakes import ApplyModel
from mcp import Client
from pydantic import ValidationError

from cv_server.server import mcp as cv_mcp
from job_assistant.workflow.graph import ApplySettings, build_apply_graph
from job_assistant.workflow.models import Decision
from job_assistant.workflow.service import ApplyService, NotAwaitingReview, RunNotFound, open_checkpointer
from tracker_server.server import mcp as tracker_mcp

pytestmark = pytest.mark.anyio

POSTING = "---\ncompany: Acme Health\nrole: Engineer\nurl: https://example.com/job\n---\nWe need Terraform.\n"


@pytest.fixture
def setup(tmp_path, monkeypatch, files):
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "tracker.db"))
    posting = tmp_path / "acme_eng.md"
    posting.write_text(POSTING, encoding="utf-8")
    template, employers = files
    return tmp_path, posting, template, employers


class DropsOnce:
    """The tracker, with the connection dropping once at a chosen call - as when job-web
    stops, or the server crashes, part-way through saving."""

    def __init__(self, tracker, tool: str, kind: str):
        self.tracker, self.tool, self.kind, self.dropped = tracker, tool, kind, False

    async def call_tool(self, tool, args):
        if not self.dropped and (tool, args.get("kind")) == (self.tool, self.kind):
            self.dropped = True
            raise ConnectionError("tracker-server went away")
        return await self.tracker.call_tool(tool, args)


@asynccontextmanager
async def service(setup, model, wrap_tracker=lambda tracker: tracker):
    """An ApplyService over the given model; a new one each time, sharing the database on disk."""
    tmp_path, _, template, employers = setup
    async with Client(cv_mcp) as cv, Client(tracker_mcp) as tracker, \
            open_checkpointer(tmp_path / "runs.db") as checkpointer:
        settings = ApplySettings(model=model, cv=cv, tracker=wrap_tracker(tracker), employers=employers,
                                 template=template, drafts_dir=tmp_path / "drafts")
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


async def test_a_save_retried_after_failing_part_way_saves_each_draft_once(setup):
    def drops(tracker):
        return DropsOnce(tracker, "save_draft", "cv")  # the last of the three drafts

    async with service(setup, ApplyModel(), drops) as (svc, tracker):
        paused = await svc.start(setup[1])
        with pytest.raises(ConnectionError):
            await svc.decide(paused.run_id, Decision(action="approve"))
        assert (await svc.view(paused.run_id)).status == "stopped"

        view = await svc.retry(paused.run_id)  # save runs again from the top

        assert view.status == "saved"
        (app,) = await applications(tracker)
        detail = (await tracker.call_tool("get_application", {"application_id": app["id"]})).structured_content
        assert sorted((d["kind"], d["version"]) for d in detail["drafts"]) == \
            [("assessment", 1), ("cover_letter_plan", 1), ("cv", 1)]


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


async def test_a_resumed_run_gets_its_own_types_back_not_dicts(setup):
    # Checkpoints only reload approved types; if the list goes stale (a class moved),
    # they come back as dicts and the next step breaks. Revising reads them after a resume.
    async with service(setup, ApplyModel()) as (svc, _):
        run_id = (await svc.start(setup[1])).run_id
        state = (await svc.graph.aget_state(svc._config(run_id))).values
        assert type(state["matches"][0]["requirement"]).__name__ == "Requirement"
        assert type(state["extract_turn"]).__name__ == "Turn"
        assert type(state["letter"]).__name__ == type(state["cv"]).__name__ == "Artifact"

        await svc.decide(run_id, Decision(action="revise", notes="shorter", target="cv"))
        state = (await svc.graph.aget_state(svc._config(run_id))).values
        assert type(state["decision"]).__name__ == "Decision"


async def test_a_run_saved_before_the_state_held_models_still_carries_on(setup):
    """Runs paused before the letter, CV and decision were models hold them as dicts."""
    model = ApplyModel()
    async with service(setup, model) as (svc, tracker):
        run_id = (await svc.start(setup[1])).run_id
        config = svc._config(run_id)
        state = (await svc.graph.aget_state(config)).values
        # Put the run back as an older version saved it: plain dicts, and a revise decision.
        old = {"letter": state["letter"].model_dump(), "cv": state["cv"].model_dump(),
               "decision": {"action": "revise", "notes": "mention Terraform", "target": "cv"}}
        await svc.graph.aupdate_state(config, old, as_node="cv")
        assert isinstance((await svc.graph.aget_state(config)).values["letter"], dict)

        # Rewritten as cv's output, the run now stands before the review: carrying on runs
        # the review from the old dicts, then the save after you approve.
        view = await svc.retry(run_id)
        assert view.status == "awaiting_review" and view.review.artifacts[0].kind == "cover_letter_plan"
        view = await svc.decide(run_id, Decision(action="approve"))

        assert view.status == "saved" and len(await applications(tracker)) == 1
