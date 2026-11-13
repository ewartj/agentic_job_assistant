"""The web API end to end: real app, real routes, real background runs and checkpointer,
with fake models and in-process servers - no network and no API calls.
"""
import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from langchain_core.runnables import RunnableLambda
from mcp import Client
from test_apply import ApplyModel
from test_cv import files  # noqa: F401 - the generated CV template and employers, as a fixture

from cv_server.server import mcp as cv_mcp
from job_agent.letter import Outline
from job_agent.pipeline import Requirements
from job_agent.web.app import create_app
from job_agent.workflow.graph import ApplySettings, build_apply_graph
from job_agent.workflow.service import ApplyService, open_checkpointer
from tracker_server.server import mcp as tracker_mcp

pytestmark = pytest.mark.anyio

POSTING = {"name": "acme_eng", "text": "We need Terraform.", "company": "Acme Health", "role": "Engineer",
           "url": "https://example.com/job"}


class GatedModel(ApplyModel):
    """Holds each run at its first model call until the test opens the gate, and can fail
    its first letter or CV plan once - to test what happens while a run is in progress."""

    def __init__(self, fail_plan_once: bool = False):
        super().__init__()
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail_plan_once = fail_plan_once

    def with_structured_output(self, schema, **kwargs):
        inner = super().with_structured_output(schema, **kwargs)

        async def run(messages):
            if schema is Requirements:
                await self.gate.wait()
            if schema is Outline and self.fail_plan_once:
                self.fail_plan_once = False
                raise RuntimeError("model went away")  # not retried: RuntimeError is a "bug" to LangGraph
            return await inner.ainvoke(messages)

        return RunnableLambda(run)


@pytest.fixture
def make_api(tmp_path, monkeypatch, files):  # noqa: F811
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "tracker.db"))
    template, employers = files

    @asynccontextmanager
    async def api(model):
        @asynccontextmanager
        async def open_service(progress):
            async with Client(cv_mcp) as cv, Client(tracker_mcp) as tracker, \
                    open_checkpointer(tmp_path / "runs.db") as checkpointer:
                settings = ApplySettings(model=model, cv=cv, tracker=tracker, employers=employers, template=template,
                                         drafts_dir=tmp_path / "drafts", progress=progress)
                yield ApplyService(build_apply_graph(settings, checkpointer))

        app = create_app(open_service, postings_dir=tmp_path / "postings", drafts_dir=tmp_path / "drafts")
        async with app.router.lifespan_context(app), \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.manager = app.state.manager
            yield client

    return api


async def started(client, posting="acme_eng"):
    """Save the posting (if needed) and start a run; returns the run id."""
    if (await client.get(f"/api/postings/{posting}")).status_code == 404:
        assert (await client.post("/api/postings", json=POSTING | {"name": posting})).status_code == 201
    response = await client.post("/api/runs", json={"posting": posting})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


async def finished(client, run_id):
    await client.manager.wait(run_id)
    return (await client.get(f"/api/runs/{run_id}")).json()


async def test_health(make_api):
    async with make_api(GatedModel()) as client:
        assert (await client.get("/api/health")).json() == {"status": "ok"}


async def test_postings_are_saved_listed_and_read_back(make_api):
    async with make_api(GatedModel()) as client:
        created = await client.post("/api/postings", json=POSTING)
        assert created.status_code == 201
        assert created.json()["text"] == "We need Terraform." and created.json()["company"] == "Acme Health"

        listed = (await client.get("/api/postings")).json()
        assert [(p["name"], p["role"]) for p in listed] == [("acme_eng", "Engineer")]
        assert (await client.post("/api/postings", json=POSTING)).status_code == 409  # exists
        replaced = await client.post("/api/postings", json=POSTING | {"text": "New advert.", "replace": True})
        assert replaced.json()["text"] == "New advert." and replaced.json()["company"] == "Acme Health"


@pytest.mark.parametrize(("request_", "code"), [
    (("post", "/api/postings", POSTING | {"name": "../escape"}), 422),
    (("post", "/api/postings", POSTING | {"text": "  "}), 422),
    (("get", "/api/postings/missing", None), 404),
    (("get", "/api/postings/a.b", None), 422),
    (("post", "/api/runs", {"posting": "missing"}), 404),
    (("get", "/api/runs/no-such-run", None), 404),
    (("post", "/api/runs/no-such-run/decision", {"action": "approve"}), 404),
])
async def test_bad_requests_get_clear_status_codes(make_api, request_, code):
    method, url, body = request_
    async with make_api(GatedModel()) as client:
        response = await client.request(method, url, json=body)
    assert response.status_code == code, response.text
    assert response.json()["detail"]


async def test_a_run_goes_from_start_to_review_to_saved(make_api):
    async with make_api(GatedModel()) as client:
        run_id = await started(client)
        view = await finished(client, run_id)
        assert view["status"] == "awaiting_review"
        assert [a["kind"] for a in view["review"]["artifacts"]] == ["cover_letter_plan", "cv"]

        docx = await client.get(f"/api/runs/{run_id}/files/cv")
        assert docx.status_code == 200 and docx.headers["content-type"].startswith("application/vnd.openxml")
        plan = await client.get(f"/api/runs/{run_id}/files/cover_letter_plan")
        assert "I provisioned AWS with Terraform. [cv-001]" in plan.text

        decided = await client.post(f"/api/runs/{run_id}/decision", json={"action": "approve"})
        assert decided.status_code == 202 and decided.json()["status"] == "running"
        view = await finished(client, run_id)
        assert view["status"] == "saved" and view["application_id"] == 1
        assert [r["status"] for r in (await client.get("/api/runs")).json()] == ["saved"]


async def test_decisions_are_validated_against_the_run(make_api):
    async with make_api(GatedModel()) as client:
        run_id = await started(client)
        await finished(client, run_id)

        no_notes = await client.post(f"/api/runs/{run_id}/decision", json={"action": "revise"})
        assert no_notes.status_code == 422
        await client.post(f"/api/runs/{run_id}/decision", json={"action": "reject"})
        await finished(client, run_id)
        again = await client.post(f"/api/runs/{run_id}/decision", json={"action": "approve"})
        assert again.status_code == 409 and "rejected" in again.json()["detail"]


async def test_a_posting_can_only_have_one_run_going_at_a_time(make_api):
    model = GatedModel()
    model.gate.clear()  # the first run waits at its first model call
    async with make_api(model) as client:
        run_id = await started(client)
        assert (await client.get(f"/api/runs/{run_id}")).json()["status"] == "running"
        second = await client.post("/api/runs", json={"posting": "acme_eng"})
        assert second.status_code == 409
        model.gate.set()
        assert (await finished(client, run_id))["status"] == "awaiting_review"


async def test_progress_streams_as_server_sent_events(make_api):
    async with make_api(GatedModel()) as client:
        run_id = await started(client)
        async with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
            events = [json.loads(line[6:]) async for line in stream.aiter_lines() if line.startswith("data: ")]

    assert any(e["type"] == "progress" and e["line"].startswith("[letter]") for e in events)
    assert any(e["type"] == "progress" and e["line"].startswith("[cv]") for e in events)
    assert events[-2:] == [{"type": "status", "status": "awaiting_review", "error": None}, {"type": "end"}]


async def test_events_for_an_idle_run_just_give_its_status(make_api):
    async with make_api(GatedModel()) as client:
        run_id = await started(client)
        await finished(client, run_id)
        await client.post(f"/api/runs/{run_id}/decision", json={"action": "reject"})
        await finished(client, run_id)
        async with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
            lines = [line async for line in stream.aiter_lines() if line.startswith("data: ")]
    assert [json.loads(line[6:]) for line in lines] == [
        {"type": "status", "status": "rejected", "error": None}, {"type": "end"}]


async def test_a_run_that_fails_part_way_says_why_and_can_be_retried(make_api):
    async with make_api(GatedModel(fail_plan_once=True)) as client:
        run_id = await started(client)
        view = await finished(client, run_id)
        assert view["status"] == "stopped" and "model went away" in view["error"]

        retried = await client.post(f"/api/runs/{run_id}/retry", json={})
        assert retried.status_code == 202
        assert (await finished(client, run_id))["status"] == "awaiting_review"
        assert (await client.post(f"/api/runs/{run_id}/retry", json={})).status_code == 409  # not stopped now


async def test_files_are_only_the_runs_own_by_kind(make_api):
    async with make_api(GatedModel()) as client:
        run_id = await started(client)
        await finished(client, run_id)
        assert (await client.get(f"/api/runs/{run_id}/files/secrets")).status_code == 422  # not a kind
        assert (await client.get(f"/api/runs/{run_id}/files/..%2F..%2F.env")).status_code in (404, 422)
        assert (await client.get("/api/runs/no-such-run/files/cv")).status_code == 404
