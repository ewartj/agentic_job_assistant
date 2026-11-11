"""The draft -> check -> redraft letter graph with a fake model against the real servers.

No API calls: drafts are scripted and the checker's verdicts come from a rule, while
evidence, entry text and past letters come from the in-process cv-server (fixtures)
and outcomes from tracker-server (a temporary database).
"""
from contextlib import closing
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.types import RetryPolicy
from mcp import Client

from cv_server.server import mcp as cv_mcp
from job_agent import pipeline
from job_agent.agent import CV_ID
from job_agent.letter import (
    MAX_DRAFTS,
    Check,
    Fix,
    Fixes,
    Outline,
    Paragraph,
    Point,
    apply_fixes,
    build_letter_graph,
    interview_letters,
    plan_claims,
    render_plan,
    run_letter,
    strip_citations,
    to_plan,
)
from job_agent.pipeline import Judgement, Requirement, Requirements
from tracker_server import store
from tracker_server.server import mcp as tracker_mcp

pytestmark = pytest.mark.anyio

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
TERRAFORM = Requirement(name="Terraform", kind="essential", queries=["terraform"])


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(pipeline, "RETRY", RetryPolicy(max_attempts=3, initial_interval=0.01, jitter=False))


def point(text):
    """A point; "[cv-001]" in the text becomes its cv_ids, as the schema holds them."""
    return Point(text=strip_citations(text), cv_ids=CV_ID.findall(text))


def plan(*points):
    """A one-paragraph outline with these points."""
    return Outline(paragraphs=[Paragraph(title="Evidence", purpose="Show the Terraform work.",
                                         points=[point(t) for t in points])])


def fix(pid, text=None):
    """A fix for point `pid`: new text (with [cv-001] citations), or None to drop it."""
    if text is None:
        return Fix(id=pid, drop=True, text="", cv_ids=[])
    return Fix(id=pid, drop=False, text=strip_citations(text), cv_ids=CV_ID.findall(text))


class FakeModel:
    """A scripted first plan (a list of points, or None for a reply that doesn't parse),
    scripted fixes for each redraft (a list of fix(), or None), and a rule for the
    checker (returns a problem, or None if the point is supported)."""

    def __init__(self, drafts, problem_with=lambda sentence: None, requirements=(TERRAFORM,), fixes=()):
        self.drafts = list(drafts)
        self.fixes = list(fixes)
        self.problem_with = problem_with
        self.requirements = list(requirements)
        self.draft_prompts = []  # the messages the first plan was asked with
        self.fix_prompts = []  # the messages each redraft was asked with
        self.checked = []  # sentences the checker was asked about
        self.uncited = []  # those asked with the uncited-point prompt

    def with_structured_output(self, schema, **kwargs):
        async def run(messages):
            raw = AIMessage("", usage_metadata=USAGE)
            prompt = messages[-1].content
            if schema is Requirements:
                parsed = Requirements(requirements=self.requirements)
            elif schema is Outline:
                self.draft_prompts.append([m.content for m in messages])
                points = self.drafts.pop(0)
                if points is None:
                    return {"raw": raw, "parsed": None, "parsing_error": "bad json"}
                parsed = plan(*points)
            elif schema is Fixes:
                self.fix_prompts.append([m.content for m in messages])
                fixes = self.fixes.pop(0)
                if fixes is None:
                    return {"raw": raw, "parsed": None, "parsing_error": "bad json"}
                parsed = Fixes(fixes=fixes)
            elif schema is Judgement:
                ids = sorted(set(CV_ID.findall(prompt)))
                parsed = Judgement(verdict="met" if ids else "gap", cv_ids=ids, summary="You have it.")
            else:
                sentence = prompt.split("Sentence: ", 1)[1].split("\n", 1)[0]
                self.checked.append(sentence)
                if "cites no CV entries" in messages[0].content:
                    self.uncited.append(sentence)
                problem = self.problem_with(sentence)
                parsed = Check(reasoning="", supported=problem is None, problem=problem or "")
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return RunnableLambda(run)


async def run(model, **kwargs):
    async with Client(cv_mcp) as cv:
        return await run_letter(model, cv, "We need Terraform.", company="Acme", role="Engineer", **kwargs)


def round_summary(state):
    """Per draft: (points, rechecked, dropped, failed)."""
    return [(r["claims"], r["rechecked"], len(r["dropped"]), len(r["failed"])) for r in state["rounds"]]


# --- The plan -------------------------------------------------------------------

def test_every_point_is_a_claim_with_a_stable_id():
    outline = Outline(paragraphs=[
        Paragraph(title="Opening", purpose="Why this role.", points=[point("I'd love to join your team.")]),
        Paragraph(title="Infra", purpose="Show the cloud work.",
                  points=[point("I provisioned AWS with Terraform [cv-001][cv-002]."),
                          Point(text="I mentored engineers [cv-007].", cv_ids=["cv-005"])]),
    ])
    assert plan_claims(to_plan(outline)) == [
        {"pid": "p1", "index": 0, "sentence": "I'd love to join your team.", "cv_ids": []},
        {"pid": "p2", "index": 1, "sentence": "I provisioned AWS with Terraform.", "cv_ids": ["cv-001", "cv-002"]},
        # ids in the field and any also written into the text both count
        {"pid": "p3", "index": 2, "sentence": "I mentored engineers.", "cv_ids": ["cv-005", "cv-007"]},
    ]


def test_the_plan_renders_a_purpose_and_points_with_their_citations():
    text = render_plan(to_plan(plan("I provisioned AWS [cv-001].", "I'm keen to learn GCP.")))
    assert text == ("## 1. Evidence\n\n*Show the Terraform work.*\n\n"
                    "- I provisioned AWS. [cv-001]\n- I'm keen to learn GCP.")


def test_fixes_apply_only_to_failed_points_and_empty_paragraphs_go():
    current = to_plan(Outline(paragraphs=[
        Paragraph(title="A", purpose="a", points=[point("One [cv-001]."), point("Two [cv-002].")]),
        Paragraph(title="B", purpose="b", points=[point("Three [cv-003].")]),
    ]))
    fixes = [fix("p1", "One, fixed [cv-001]."), fix("p2", "Two, sneakily changed [cv-002]."), fix("p3")]
    new, changed, dropped, removed = apply_fixes(current, fixes, allowed={"p1", "p3"})  # p2 passed

    assert changed == {"p1"} and dropped == ["p3"] and removed == ["B"]
    assert [[pt["text"] for pt in para["points"]] for para in new] == [["One, fixed.", "Two."]]


def test_ids_are_cleaned_and_normalised():
    from job_agent.letter import clean_ids

    ids = ["", " ", "cv-31", "CV-005", "cv-005", "cv005", "none", "cv-0123"]
    assert clean_ids(ids) == ["cv-031", "cv-005", "cv-123"]


def test_stripping_citations():
    assert strip_citations("I built it [cv-004]. I led it. [cv-005] Thanks [cv-001, cv-002]!") == \
        "I built it. I led it. Thanks!"


# --- The loop -------------------------------------------------------------------

async def test_a_plan_whose_points_check_out_is_drafted_once():
    model = FakeModel([["I provisioned AWS with Terraform [cv-001].", "I'd love to join."]])
    state = await run(model)

    assert state["attempt"] == 1
    assert round_summary(state) == [(2, 2, 0, 0)]
    assert sorted(model.checked) == ["I provisioned AWS with Terraform.", "I'd love to join."]
    assert model.uncited == ["I'd love to join."]  # asked whether it states a fact, not against entries


async def test_an_unsupported_point_is_fixed_and_only_it_is_rechecked():
    model = FakeModel(
        [["I secured credentials at BBC [cv-001].", "I'd love to join."]],
        fixes=[[fix("p1", "I provisioned AWS with Terraform [cv-001].")]],
        problem_with=lambda s: "cv-001 doesn't mention BBC" if "BBC" in s else None,
    )
    state = await run(model)

    assert state["attempt"] == 2
    assert round_summary(state) == [(2, 2, 0, 1), (2, 1, 0, 0)]  # draft 2 rechecks only the fix
    asked = model.fix_prompts[0][-1]
    assert 'p1: "I secured credentials at BBC."' in asked and "cv-001 doesn't mention BBC" in asked
    assert "I'd love to join" not in asked  # a point that passed is never sent back
    assert "- I provisioned AWS with Terraform. [cv-001]" in state["letter"]
    assert "- I'd love to join." in state["letter"]


async def test_a_fix_cannot_touch_points_that_passed():
    # What qwen did: asked to fix one point, it returned a plan without the others.
    model = FakeModel(
        [["I worked at BBC [cv-001].", "I provisioned AWS [cv-001].", "I'd love to join."]],
        fixes=[[fix("p1", "I provisioned cloud infrastructure [cv-001]."), fix("p2"), fix("p3")]],
        problem_with=lambda s: "not at BBC" if "BBC" in s else None,
    )
    state = await run(model)

    assert round_summary(state) == [(3, 3, 0, 1), (3, 1, 0, 0)]  # nothing lost: the drops were for passed points
    assert [line for line in state["letter"].splitlines() if line.startswith("- ")] == [
        "- I provisioned cloud infrastructure. [cv-001]", "- I provisioned AWS. [cv-001]", "- I'd love to join."]


async def test_a_dropped_point_is_reported():
    model = FakeModel(
        [["I worked at BBC [cv-001].", "I provisioned AWS [cv-001]."]],
        fixes=[[fix("p1")]],
        problem_with=lambda s: "not at BBC" if "BBC" in s else None,
    )
    state = await run(model)

    assert round_summary(state) == [(2, 2, 0, 1), (1, 0, 1, 0)]
    assert state["rounds"][1]["dropped"][0]["sentence"] == "I worked at BBC."
    assert "BBC" not in state["letter"]


async def test_redrafting_stops_after_max_drafts():
    model = FakeModel([["I worked at BBC [cv-001]."]], fixes=[[fix("p1", "I worked at BBC [cv-001].")]] * MAX_DRAFTS,
                      problem_with=lambda s: "not supported")
    state = await run(model)

    assert state["attempt"] == MAX_DRAFTS
    assert all(r["failed"] for r in state["rounds"])  # still failing: reported, not looped forever


async def test_fixes_that_fail_to_parse_leave_the_plan_as_it_was():
    model = FakeModel([["I worked at BBC [cv-001]."]], fixes=[None] * (MAX_DRAFTS - 1),
                      problem_with=lambda s: "not at BBC")
    state = await run(model)

    assert [len(r["failed"]) for r in state["rounds"]] == [1] * MAX_DRAFTS
    assert "- I worked at BBC. [cv-001]" in state["letter"]


async def test_a_citation_of_an_unknown_entry_fails_without_a_model_call():
    model = FakeModel([["I did it [cv-999]."]], fixes=[[fix("p1", "I provisioned AWS [cv-001].")]])
    state = await run(model)

    assert state["rounds"][0]["failed"][0]["problem"] == "cv-999 is not a CV entry"
    assert model.checked == ["I provisioned AWS."]  # only the fixed point reached the checker


async def test_an_uncited_fact_fails_and_is_sent_back():
    # Real experience, but no citations - which once skipped the checker entirely.
    model = FakeModel(
        [["I took sole ownership of Thermiator.", "I'd love to join."]],
        fixes=[[fix("p1", "I provisioned AWS with Terraform [cv-001].")]],
        problem_with=lambda s: "states sole ownership without citing evidence" if "Thermiator" in s else None,
    )
    state = await run(model)

    assert state["attempt"] == 2
    assert state["rounds"][0]["failed"][0]["sentence"] == "I took sole ownership of Thermiator."
    assert "without citing evidence" in model.fix_prompts[0][-1]
    assert not state["rounds"][1]["failed"]
    assert model.checked.count("I'd love to join.") == 1  # carried over, not checked again


async def test_an_empty_string_id_is_no_citation_not_an_unknown_entry():
    # What qwen did: motivation points with cv_ids [""] failed as "not a CV entry".
    model = FakeModel([[]])
    model.drafts = [None]  # replaced below with a hand-built outline
    outline = Outline(paragraphs=[Paragraph(title="Why", purpose="Motivation.",
                                            points=[Point(text="I'd love to join.", cv_ids=[""])])])

    async def planned(messages):
        return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": outline, "parsing_error": None}

    original = model.with_structured_output
    model.with_structured_output = lambda schema, **kw: (
        RunnableLambda(planned) if schema is Outline else original(schema, **kw))
    state = await run(model)

    assert model.uncited == ["I'd love to join."]  # reached the motivation check
    assert not state["rounds"][0]["failed"]


async def test_a_paragraph_emptied_by_drops_is_reported():
    model = FakeModel(
        [["I worked at BBC [cv-001]."]],
        fixes=[[fix("p1")]],
        problem_with=lambda s: "not at BBC",
    )
    state = await run(model)

    assert state["rounds"][1]["removed"] == ["Evidence"]
    assert state["plan"] == []


async def test_a_plan_that_fails_to_parse_ends_the_run_and_says_so():
    state = await run(FakeModel([None]))

    assert state["letter"] == "(Could not plan the letter: bad json)"
    assert round_summary(state) == [(0, 0, 0, 0)]


async def test_the_draft_sees_the_verdicts_and_the_matched_entries_text():
    model = FakeModel([["Thanks."]])
    await run(model)

    prompt = model.draft_prompts[0][1]
    assert "Terraform (essential): Met." in prompt
    assert "[cv-001] Provisioned cloud infrastructure on AWS with Terraform." in prompt
    assert "[cv-003]" not in prompt  # not matched, so not offered as evidence
    assert "Company: Acme" in prompt


def test_the_graph_loops_from_review_back_to_draft():
    diagram = build_letter_graph(FakeModel([]), None).get_graph().draw_mermaid()
    assert "draft -.-> check" in diagram and "check --> review" in diagram
    assert "review -.-> draft" in diagram and "review -.-> __end__" in diagram


# --- Example letters ------------------------------------------------------------

@pytest.fixture
def tracker_db(tmp_path, monkeypatch):
    path = tmp_path / "tracker.db"
    monkeypatch.setenv("TRACKER_DB", str(path))
    return Path(path)


def add(path, company, *statuses):
    with closing(store.connect(path)) as conn:
        app, _ = store.log_application(conn, company, "Engineer")
        for status in statuses:
            store.update_status(conn, app["id"], status)


async def test_examples_are_letters_from_applications_that_reached_interview(tracker_db):
    add(tracker_db, "Our Future Health", "applied", "interviewing", "rejected")  # rejected now, but interviewed
    add(tracker_db, "Acme", "applied", "rejected")
    async with Client(cv_mcp) as cv, Client(tracker_mcp) as tracker:
        letters = await interview_letters(cv, tracker)

    assert letters == ["Dear Hiring Manager, ..."]  # the fixture's Our Future Health letter


async def test_draft_letters_are_not_used_as_examples(tracker_db):
    add(tracker_db, "BBC", "applied", "interviewing")  # the fixture's BBC letter is a draft
    async with Client(cv_mcp) as cv, Client(tracker_mcp) as tracker:
        assert await interview_letters(cv, tracker) == []


async def test_examples_reach_the_draft(tracker_db):
    add(tracker_db, "Our Future Health", "applied", "offer")
    model = FakeModel([["Thanks."]])
    async with Client(cv_mcp) as cv, Client(tracker_mcp) as tracker:
        await run_letter(model, cv, "We need Terraform.", tracker=tracker)

    assert "Example letters (for tone and structure only):" in model.draft_prompts[0][1]
    assert "(none available)" not in model.draft_prompts[0][1]


async def test_no_tracker_means_no_examples():
    async with Client(cv_mcp) as cv:
        assert await interview_letters(cv, None) == []


# --- Scoring the checker (job-verify-eval) --------------------------------------

async def test_verify_eval_counts_catches_and_false_alarms():
    from job_agent.verify_eval import load_claims, run_eval, score

    claims = load_claims(Path(__file__).parent / "fixtures" / "evals" / "claims.yaml")
    # A checker that flags anything mentioning BBC or dashboards: one catch, one false alarm.
    model = FakeModel([], problem_with=lambda s: "no" if "BBC" in s or "dashboards" in s or "years" in s else None)
    async with Client(cv_mcp) as cv:
        results = await run_eval(model, cv, claims)

    s = score(results)
    assert (s["caught"], s["false_claims"], s["false_alarms"], s["true_claims"]) == (2, 2, 1, 3)
    assert [m["sentence"] for m in s["mistakes"]] == ["I built R Shiny dashboards for clinicians."]


def test_verify_eval_rejects_a_claim_without_a_label(tmp_path):
    from job_agent.verify_eval import load_claims

    path = tmp_path / "claims.yaml"
    path.write_text("claims:\n  - sentence: x\n    cv_ids: [cv-001]\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="missing supported"):
        load_claims(path)


# --- A separate checker ----------------------------------------------------------

async def test_a_checker_model_checks_the_points_and_the_main_model_plans():
    planner = FakeModel([["I provisioned AWS with Terraform [cv-001]."]])
    checker = FakeModel([])
    state = await run(planner, checker_model=checker)

    assert planner.checked == [] and checker.checked == ["I provisioned AWS with Terraform."]
    assert len(planner.draft_prompts) == 1 and checker.draft_prompts == []
    assert state["rounds"][0]["claims"] == 1


def test_a_problem_is_cut_to_its_first_line_and_capped():
    from job_agent.letter import short

    assert short("cv-007 is from GOSH.\n\nLet's look again... Trick 1: plural vs singular") == "cv-007 is from GOSH."
    assert len(short("word " * 200)) <= 303 and short("word " * 200).endswith("...")
