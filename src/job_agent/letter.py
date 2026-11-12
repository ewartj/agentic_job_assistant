"""A cover letter plan, drafted and verified as a graph (Phase 3, step 5b).

The output is a plan to write the letter from, not the letter: for each paragraph,
a sentence on what it should be about and suggested points, each citing its evidence.

    START -> examples -> extract -> match (xN) -> draft -> check (one per claim) -> review
                                                    ^                                 |
                                                    +---- problems, up to 3 drafts ---+--> END

    examples  letters from past applications that reached interview or offer (the
              tracker's status history), for tone and structure
    extract, match   the assessment pipeline's nodes, unchanged (pipeline.py)
    draft     plans the letter (structured output) from the matches and the cited
              entries' text only; each point has its cv_ids. On a redraft, the model
              sees only the failed points and returns a fix or a drop for each; code
              applies them, so points that passed can't be lost or changed
    check     one branch per new or changed point: does it say only what its cited
              entries say? Each entry's text comes from cv-server's cv://entry/{id}
              resource. Points unchanged since the last draft keep their verdict
    review    collects this draft's failed checks; the router sends the plan back to
              draft with them, or ends once it passes or after MAX_DRAFTS drafts

New compared with pipeline.py:
    a cycle      review -> draft is a loop, bounded by an `attempt` counter in the state
    two servers  cv-server for evidence and letters, tracker-server for outcomes

    uv run job-letter data/evals/postings/bbc_mle.md
"""
import argparse
import asyncio
import json
import operator
import re
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from cv_server.postings import split_front_matter
from job_agent import pipeline
from job_agent.agent import CV_ID, OLLAMA_NUM_CTX, ROOT, Turn, utf8_output
from job_agent.graph_agent import make_model, model_name, server
from job_agent.pipeline import LABELS, PipelineState, assessment_nodes, usage

MAX_DRAFTS = 3  # the first draft plus up to two redrafts
EXAMPLES = 2  # past letters shown for tone and structure
LETTERS_DIR = ROOT / "data" / "evals" / "letters"  # traces, for evaluating the verifier
DRAFTS_DIR = ROOT / "data" / "drafts"  # the letters themselves

PLAN_PROMPT = """\
You plan cover letters for the user. Write a plan the user will write the letter \
from, not the letter itself.

Plan 4-5 paragraphs for a letter for the role below, using only the evidence given: \
the requirement verdicts and the CV entries. For each paragraph give:
- title: a few words, e.g. "Opening", "NLP in production", "Why this team".
- purpose: one sentence on what the paragraph should be about and why it matters \
for this role.
- points: 2-4 suggested points, each short and specific (the project, the tool, the \
outcome), written as the user would say them ("I took ..."). Each point has its \
text and cv_ids: the ids of the entries it comes from.

Rules:
- Every point about the user's experience, skills or results needs its cv_ids, \
e.g. ["cv-012", "cv-031"]. Only use ids listed below. Leave cv_ids empty only for \
points about motivation or the posting.
- Say only what the cited entries say. Don't add employers, tools, numbers or \
outcomes that aren't in them.
- Lead with what the posting needs most (its essential requirements), and choose \
the strongest evidence rather than listing everything.
- Don't present a requirement judged "not met" as a strength. A point may suggest \
saying you're keen to learn it, without a citation.
- Points about motivation (why this company, why this role) need no citation; base \
them on the posting.
- Shape the plan like the example letters, in UK English."""

FIX_PROMPT = """\
You fix points in a cover letter plan or a CV that a checker found unsupported by the \
CV entries they cite.

For each point listed, return a fix with its id. Make the smallest change that makes \
the point true, keeping as much of it as you can:
- remove or correct the unsupported detail ("design patterns" that no entry mentions);
- if it merges two projects, keep the stronger one, or name them as separate pieces \
of work;
- if it states a fact without evidence, cite the entry it comes from, or rewrite it \
as motivation that claims nothing about past work.
Set drop=true only if nothing true would remain. Return nothing for points that \
aren't listed - the rest of the plan stays as it is.

The plan's rules still apply: use only ids from the CV entries below - for a CV, only \
the entries listed under the point's own section - and say only what they say; don't \
merge details from different projects into one; leave cv_ids empty only for motivation \
or the posting; write as the user would, in UK English."""

CHECK_PROMPT = """\
You check one point from a cover letter plan or a CV against the CV entries it cites.

The point is supported if every specific fact in it - employer, project, role, \
tool, technology, number, outcome, date - is stated in the cited entries or follows \
directly from them. General framing ("I enjoy", "I'm keen to") needs no support. A \
fact that may be true but isn't in these cited entries counts as unsupported.

Only facts about the user need support. Facts about the employer, the role or the \
posting itself (the company's product, its location, its team, its plans) come from \
the posting, not the CV, so ignore them when deciding.

When the cited entries describe different projects or jobs, the point may mention \
both, but it mustn't merge them into one piece of work: details from one project \
can't be presented as happening in, or alongside, the other.

If anything is unsupported, set supported to false and say exactly what, e.g. \
"cv-007 is from Acme, not Globex" or "no cited entry mentions AWS CDK"."""

UNCITED_PROMPT = """\
You check one point from a cover letter plan or a CV that cites no CV entries.

Motivation and interest never need evidence, even though they're about the user: \
what they want, what draws them to the company or role, what they're keen to learn \
("I'd love to help pharmacy teams focus on patients", "Your focus on AI is what \
draws me to this role"). Nor does anything about the posting or the company itself.

Only a fact about the user's experience, skills, projects, employers, \
qualifications or results needs evidence. If the point states one, set supported \
to false and say "states <the fact> without citing evidence"."""


class Check(BaseModel):
    # Fields are generated in this order, so the reasoning comes before the verdict.
    # With supported first, the model committed to a verdict and then sometimes argued
    # itself out of it ("...Re-evaluating: supported") while the field still said false.
    reasoning: str = Field(description="Compare each fact in the point with the cited entries before deciding")
    supported: bool
    problem: str = Field(description="One short sentence: what the cited entries don't support; "
                                     "empty if supported. Your working goes in reasoning, not here")


class Point(BaseModel):
    text: str
    cv_ids: list[str] = Field(description="Ids of the entries this point comes from, e.g. ['cv-012']; "
                                          "empty only for points about motivation or the posting")


class Paragraph(BaseModel):
    title: str
    purpose: str = Field(description="One sentence: what this paragraph should be about, and why")
    points: list[Point] = Field(description="2-4 suggested points")


class Outline(BaseModel):
    paragraphs: list[Paragraph]


class Fix(BaseModel):
    id: str = Field(description="The point's id, e.g. 'p3'")
    drop: bool = Field(description="True to remove the point instead of fixing it")
    text: str = Field(description="The corrected point; empty if dropped")
    cv_ids: list[str] = Field(description="Ids of the entries the corrected point comes from")


class Fixes(BaseModel):
    fixes: list[Fix]


class PlanPoint(TypedDict):
    pid: str  # stable across redrafts, so fixes and verdicts can refer to it
    section: str  # the paragraph (letter) or CV section it belongs to
    text: str  # without citations
    cv_ids: list[str]


class PlanParagraph(TypedDict):
    title: str
    purpose: str
    points: list[PlanPoint]


class Claim(TypedDict):
    pid: str
    section: str
    index: int  # the point's position in the plan
    sentence: str  # the point, without its citations
    cv_ids: list[str]


class ClaimCheck(Claim, total=False):
    attempt: int  # which draft this check was for
    supported: bool | None  # None: the check itself failed, so it's unknown
    problem: str
    carried: bool  # True: the point was unchanged, so this is the last draft's verdict


class LetterState(PipelineState, total=False):
    company: str
    role: str
    examples: list[str]
    plan: list[PlanParagraph]  # the current plan, owned by code: the model only proposes changes
    letter: str  # the plan as Markdown, with its [cv-012] citations
    to_check: list[Claim]  # points that are new or changed in this draft
    # Points dropped by a fix, with the draft that dropped them, so a shrinking plan shows.
    dropped: Annotated[list[dict[str, Any]], operator.add]
    removed: Annotated[list[dict[str, Any]], operator.add]  # paragraphs left empty by drops
    attempt: int  # drafts written so far
    # Every round's checks accumulate here (operator.add), so each carries its attempt
    # number and review looks only at the current draft's.
    checks: Annotated[list[ClaimCheck], operator.add]
    turns: Annotated[list[Turn], operator.add]  # draft and check calls, for the trace


class CheckInput(TypedDict):
    attempt: int
    claim: Claim


# --- Citations ------------------------------------------------------------------

CITATION = re.compile(r"\[\s*cv-\d{3}(?:\s*[,;]\s*cv-\d{3})*\s*\]")


LOOSE_ID = re.compile(r"cv-?0*(\d{1,3})", re.IGNORECASE)


def clean_ids(ids: list[str]) -> list[str]:
    """Ids as cv-012, without repeats. Small models fill the field with "" for "none",
    or write cv-31 for cv-031; those mustn't fail a point as "not a CV entry"."""
    found = (LOOSE_ID.fullmatch(i.strip()) for i in ids)
    return list(dict.fromkeys(f"cv-{int(m.group(1)):03d}" for m in found if m))


def text_ids(text: str, ids: list[str]) -> list[str]:
    """The cv_ids field plus any [cv-012] the model also wrote into the text."""
    return clean_ids([*ids, *CV_ID.findall(" ".join(CITATION.findall(text)))])


def point_ids(point: Point) -> list[str]:
    return text_ids(point.text, point.cv_ids)


def to_plan(outline: Outline) -> list[PlanParagraph]:
    """The model's outline as the plan code owns, with an id for every point."""
    n = 0
    plan = []
    for paragraph in outline.paragraphs:
        points = []
        for point in paragraph.points:
            n += 1
            points.append(PlanPoint(pid=f"p{n}", section=paragraph.title,
                                    text=strip_citations(point.text).strip(), cv_ids=point_ids(point)))
        plan.append(PlanParagraph(title=paragraph.title, purpose=paragraph.purpose, points=points))
    return plan


def plan_claims(plan: list[PlanParagraph]) -> list[Claim]:
    """Every point is a claim, cited or not: a point with no ids is checked for stating
    a fact without evidence, so leaving out citations can't get past the checker.
    The purpose sentences are guidance for the user, not claims."""
    return [Claim(pid=pt["pid"], section=pt["section"], index=n, sentence=pt["text"], cv_ids=pt["cv_ids"])
            for n, pt in enumerate(pt for paragraph in plan for pt in paragraph["points"])]


def apply_fixes(plan: list[PlanParagraph], fixes: list[Fix], allowed: set[str]) -> tuple[
        list[PlanParagraph], set[str], list[str], list[str]]:
    """(new plan, ids of changed points, ids of dropped points, titles of removed paragraphs).

    Only fixes for `allowed` ids (the failed points) are applied: the model can't change
    or remove a point that passed. A paragraph left with no points is removed.
    """
    by_id = {f.id: f for f in fixes if f.id in allowed}
    changed, dropped, removed, new_plan = set(), [], [], []
    for paragraph in plan:
        points = []
        for pt in paragraph["points"]:
            fix = by_id.get(pt["pid"])
            if fix is None:
                points.append(pt)
            elif fix.drop or not fix.text.strip():
                dropped.append(pt["pid"])
            else:
                points.append(PlanPoint(pid=pt["pid"], section=pt["section"], text=strip_citations(fix.text).strip(),
                                        cv_ids=text_ids(fix.text, fix.cv_ids)))
                changed.add(pt["pid"])
        if points:
            new_plan.append(PlanParagraph(title=paragraph["title"], purpose=paragraph["purpose"], points=points))
        else:
            removed.append(paragraph["title"])
    return new_plan, changed, dropped, removed


def render_plan(plan: list[PlanParagraph]) -> str:
    """The plan as Markdown, citations kept so each point can be traced to its evidence."""
    blocks = []
    for n, paragraph in enumerate(plan, 1):
        points = "\n".join(f"- {pt['text']}" + "".join(f" [{i}]" for i in pt["cv_ids"]) for pt in paragraph["points"])
        blocks.append(f"## {n}. {paragraph['title']}\n\n*{paragraph['purpose']}*\n\n{points}")
    return "\n\n".join(blocks)


def strip_citations(text: str) -> str:
    """Text without its citations, spacing tidied."""
    text = re.sub(r"\s*" + CITATION.pattern, "", text)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", text)


# --- The graph ------------------------------------------------------------------

EntryText = Callable[[str], Awaitable[str | None]]


@dataclass
class Drafting:
    """What changes between a letter plan and a CV (cv.py): everything else - matching,
    checking, patch-based fixing, the loop - is shared.

    evidence  builds the evidence block the planner and fixer see (default: the
              requirement verdicts and the matched entries' text)
    allowed   section -> the only ids its points may cite, or None for any. A point
              citing anything else fails without a model call: on a CV, a bullet must
              come from its own job
    examples  fetch past letters from the tracker; False uses the examples passed in
    """
    prompt: str = ""
    evidence: Callable[["LetterState", EntryText], Awaitable[str]] | None = None
    allowed: Callable[[str], set[str] | None] | None = None
    examples: bool = True
    examples_label: str = "Example letters (for tone and structure only)"
    noun: str = "letter"


def build_letter_graph(model: BaseChatModel, cv: ClientSession, tracker: ClientSession | None = None,
                       limit: int = 10, checker_model: BaseChatModel | None = None,
                       drafting: Drafting | None = None):
    """checker_model, if given, checks the points; `model` does everything else. The
    check is small but it's where accuracy matters most, so it can be a stronger model."""
    drafting = drafting or Drafting(prompt=PLAN_PROMPT)
    extract, fan_out, match = assessment_nodes(model, cv, limit, if_none="draft")
    checker = (checker_model or model).with_structured_output(Check, method="json_schema", include_raw=True)
    planner = model.with_structured_output(Outline, method="json_schema", include_raw=True)
    fixer = model.with_structured_output(Fixes, method="json_schema", include_raw=True)
    entries: dict[str, str | None] = {}

    async def entry_text(cv_id: str) -> str | None:
        """An entry's text from cv-server's resource, cached; None if there's no such entry."""
        if cv_id not in entries:
            try:
                resource = await cv.read_resource(f"cv://entry/{cv_id}")
                entries[cv_id] = json.loads(resource.contents[0].text)["text"]
            except Exception:  # noqa: BLE001 - an unknown id is an answer here, not a crash
                entries[cv_id] = None
        return entries[cv_id]

    async def examples(state: LetterState) -> dict[str, Any]:
        if not drafting.examples:
            return {}  # the caller passed its own examples in the input state
        return {"examples": await interview_letters(cv, tracker)}

    async def evidence_block(state: LetterState) -> str:
        if drafting.evidence:
            return await drafting.evidence(state, entry_text)
        return await matched_evidence(state)

    async def matched_evidence(state: LetterState) -> str:
        matches = sorted(state.get("matches", []), key=lambda m: m["index"])
        ids = list(dict.fromkeys(i for m in matches for i in m["cv_ids"]))
        texts = {i: await entry_text(i) for i in ids}
        verdicts = "\n".join(f"- {m['requirement'].name} ({m['requirement'].kind}): {LABELS[m['verdict']]}. "
                             f"{m['summary']} {m['cv_ids']}" for m in matches)
        evidence = "\n".join(f"[{i}] {t}" for i, t in texts.items() if t)
        return f"Requirement verdicts:\n{verdicts or '(none)'}\n\nCV entries:\n{evidence or '(none)'}"

    async def draft(state: LetterState) -> dict[str, Any]:
        started = time.perf_counter()
        attempt = state.get("attempt", 0) + 1
        if attempt == 1:
            return await first_draft(state, started)
        return await redraft(state, attempt, started)

    async def first_draft(state: LetterState, started: float) -> dict[str, Any]:
        shown = "\n\n---\n\n".join(state.get("examples", [])) or "(none available)"
        out = await planner.ainvoke([
            SystemMessage(drafting.prompt),
            HumanMessage(f"Company: {state.get('company') or 'see the posting'}\n"
                         f"Role: {state.get('role') or 'see the posting'}\n\n{await evidence_block(state)}\n\n"
                         f"{drafting.examples_label}:\n\n{shown}\n\n"
                         f"The posting:\n\n{state['posting']}"),
        ])
        turn = model_turn("draft 1", out["raw"], started)
        outline: Outline | None = out["parsed"]
        if outline is None:  # nothing to check; the run ends and reports it
            return {"plan": [], "letter": f"(Could not plan the {drafting.noun}: {out['parsing_error']})",
                    "to_check": [],
                    "attempt": 1, "turns": [turn]}
        plan = to_plan(outline)
        return {"plan": plan, "letter": render_plan(plan), "to_check": plan_claims(plan), "attempt": 1,
                "turns": [turn]}

    async def redraft(state: LetterState, attempt: int, started: float) -> dict[str, Any]:
        previous = [c for c in state.get("checks", []) if c["attempt"] == attempt - 1]
        failed = [c for c in previous if c["supported"] is False]
        problems = "\n".join(f'- {c["pid"]}: "{c["sentence"]}" {c["cv_ids"]}: {c["problem"]}' for c in failed)
        out = await fixer.ainvoke([SystemMessage(FIX_PROMPT), HumanMessage(await evidence_block(state)),
                                   HumanMessage(f"Points to fix:\n{problems}")])
        fixes: Fixes | None = out["parsed"]
        plan, changed, dropped, removed = apply_fixes(state["plan"], fixes.fixes if fixes else [],
                                                      {c["pid"] for c in failed})
        claims = plan_claims(plan)
        # Unchanged points keep their verdict from the last draft, under this draft's number;
        # only the changed ones go to the checker.
        verdicts = {c["pid"]: c for c in previous}
        carried = [ClaimCheck(**{**verdicts[c["pid"]], **c, "attempt": attempt, "carried": True})
                   for c in claims if c["pid"] not in changed and c["pid"] in verdicts]
        return {"plan": plan, "letter": render_plan(plan), "to_check": [c for c in claims if c["pid"] in changed],
                "checks": carried, "attempt": attempt,
                "dropped": [{**c, "attempt": attempt} for c in failed if c["pid"] in dropped],
                "removed": [{"attempt": attempt, "title": t} for t in removed],
                "turns": [model_turn(f"draft {attempt}: {len(changed)} fixed, {len(dropped)} dropped",
                                     out["raw"], started)]}

    def fan_out_checks(state: LetterState) -> list[Send] | str:
        claims = state.get("to_check", [])
        if not claims:  # nothing new: every verdict was carried over (or there's no plan)
            return "review"
        return [Send("check", CheckInput(attempt=state["attempt"], claim=c)) for c in claims]

    async def check(branch: CheckInput) -> dict[str, Any]:
        started = time.perf_counter()
        claim = branch["claim"]
        texts = {i: await entry_text(i) for i in claim["cv_ids"]}
        unknown = [i for i, t in texts.items() if t is None]
        allowed = drafting.allowed(claim["section"]) if drafting.allowed else None
        outside = [i for i in claim["cv_ids"] if allowed is not None and i not in allowed]
        turns = []
        if unknown:  # no model call needed: the citation itself is wrong
            supported, problem = False, f"{', '.join(unknown)} " + ("is not a CV entry" if len(unknown) == 1
                                                                    else "are not CV entries")
        elif outside:  # no model call needed either: the entry belongs to another section
            supported, problem = False, (f"{', '.join(outside)} " + ("isn't" if len(outside) == 1 else "aren't")
                                         + f" from {claim['section']}; cite only {claim['section']} entries")
        else:
            supported, problem, raw = await verify(checker, claim["sentence"], texts)
            tokens_in, tokens_out = usage(raw)
            turns.append(Turn(text=f"check {branch['attempt']}.{claim['index']}: {supported}", tool_calls=[],
                              input_tokens=tokens_in, output_tokens=tokens_out,
                              seconds=round(time.perf_counter() - started, 2)))
        return {"checks": [ClaimCheck(**claim, attempt=branch["attempt"], supported=supported, problem=problem)],
                "turns": turns}

    def review(state: LetterState) -> dict[str, Any]:
        return {}  # a join point: everything it needs is already in the state

    def after_review(state: LetterState) -> str:
        failed = [c for c in state.get("checks", []) if c["attempt"] == state["attempt"] and c["supported"] is False]
        return "draft" if failed and state["attempt"] < MAX_DRAFTS else END

    graph = StateGraph(LetterState)
    graph.add_node("examples", examples)
    graph.add_node("extract", extract, retry_policy=pipeline.RETRY)
    graph.add_node("match", match, retry_policy=pipeline.RETRY)
    graph.add_node("draft", draft, retry_policy=pipeline.RETRY)
    graph.add_node("check", check, retry_policy=pipeline.RETRY)
    graph.add_node("review", review)
    graph.add_edge(START, "examples")
    graph.add_edge("examples", "extract")
    graph.add_conditional_edges("extract", fan_out, ["match", "draft"])
    graph.add_edge("match", "draft")
    graph.add_conditional_edges("draft", fan_out_checks, ["check", "review"])
    graph.add_edge("check", "review")
    graph.add_conditional_edges("review", after_review, ["draft", END])
    return graph.compile()


def model_turn(text: str, raw: Any, started: float) -> Turn:
    tokens_in, tokens_out = usage(raw)
    return Turn(text=text, tool_calls=[], input_tokens=tokens_in, output_tokens=tokens_out,
                seconds=round(time.perf_counter() - started, 2))


async def verify(checker: Any, sentence: str, texts: dict[str, str]) -> tuple[bool | None, str, Any]:
    """One claim against its cited entries' text: (supported, problem, raw reply).

    With no entries (texts empty), the question is instead whether the claim states
    a fact about the user at all. `checker` is a model with structured output for
    Check (include_raw=True).
    supported is None when the reply couldn't be parsed, so the claim is unchecked.
    Shared by the letter graph and job-verify-eval, so the eval tests exactly this.
    """
    cited = "\n".join(f"[{i}] {t}" for i, t in texts.items()) or "(none)"
    out = await checker.ainvoke([SystemMessage(CHECK_PROMPT if texts else UNCITED_PROMPT),
                                 HumanMessage(f"Sentence: {sentence}\n\nCited entries:\n{cited}")])
    result: Check | None = out["parsed"]
    if result is None:
        return None, f"Could not check: {out['parsing_error']}", out["raw"]
    return result.supported, "" if result.supported else short(result.problem), out["raw"]


def short(problem: str, limit: int = 300) -> str:
    """The first line of a problem, capped: small models sometimes write their whole
    working into it, which would then be sent back to the planner on every redraft."""
    line = problem.strip().split("\n", 1)[0]
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + "..."


async def interview_letters(cv: ClientSession, tracker: ClientSession | None, n: int = EXAMPLES) -> list[str]:
    """The n most recent cover letters from applications that reached interview or offer."""
    if tracker is None:
        return []
    applications = (await tracker.call_tool("list_applications", {})).structured_content["result"]
    companies = []
    for application in applications:
        detail = (await tracker.call_tool("get_application", {"application_id": application["id"]})).structured_content
        if any(h["status"] in ("interviewing", "offer") for h in detail.get("history", [])):
            companies.append(application["company"])
    letters = []
    for company in dict.fromkeys(companies):
        found = (await cv.call_tool("get_past_letters", {"company": company})).structured_content["result"]
        letters += [x for x in found if x.get("category") == "cover_letter" and x.get("status") != "draft"]
    newest = sorted({x["id"]: x for x in letters}.values(), key=lambda x: x.get("date") or "", reverse=True)
    # The same letter is sometimes sent to two roles at one company; show it once.
    return list(dict.fromkeys(x["text"] for x in newest))[:n]


# --- Run ----------------------------------------------------------------------

async def run_letter(model: BaseChatModel, cv: ClientSession, posting: str, *, tracker: ClientSession | None = None,
                     company: str = "", role: str = "", limit: int = 10, max_concurrency: int | None = None,
                     progress=None, checker_model: BaseChatModel | None = None,
                     drafting: Drafting | None = None, examples: list[str] | None = None) -> dict[str, Any]:
    """Run the graph; returns the final state plus `rounds`, a per-draft summary of the checks."""
    say = progress or (lambda line: None)
    started = time.perf_counter()
    say(f"[   0s] finding example letters, then extracting requirements with {model_name(model)}...")
    state: dict[str, Any] = {}
    inputs = {"posting": posting, "company": company, "role": role}
    if examples is not None:
        inputs["examples"] = examples
    async for mode, chunk in build_letter_graph(model, cv, tracker, limit, checker_model, drafting).astream(
            inputs,
            {"max_concurrency": max_concurrency}, stream_mode=["updates", "values"]):
        if mode == "values":
            state = chunk
            continue
        elapsed = time.perf_counter() - started
        for node, update in chunk.items():
            update = update or {}
            if node == "extract":
                say(f"[{elapsed:4.0f}s] extracted {len(update.get('requirements', []))} requirements")
            elif node == "match":
                (m,) = update["matches"]
                say(f"[{elapsed:4.0f}s]   {m['requirement'].name}: {m['verdict']}")
            elif node == "draft" and update["attempt"] == 1:
                say(f"[{elapsed:4.0f}s] plan written; checking its {len(update['to_check'])} points...")
            elif node == "draft":
                say(f"[{elapsed:4.0f}s] draft {update['attempt']}: {len(update['to_check'])} points fixed, "
                    f"{len(update.get('dropped', []))} dropped; rechecking the fixed ones...")
            elif node == "check":
                (c,) = update["checks"]
                if c["supported"] is False:
                    say(f"[{elapsed:4.0f}s]   unsupported: \"{c['sentence'][:70]}...\" {c['cv_ids']}: {c['problem']}")
    state["rounds"] = [
        {"attempt": a, "claims": len(cs), "cited": sum(bool(c["cv_ids"]) for c in cs),
         "rechecked": sum(not c.get("carried") for c in cs),
         "dropped": [d for d in state.get("dropped", []) if d["attempt"] == a],
         "removed": [r["title"] for r in state.get("removed", []) if r["attempt"] == a],
         "failed": [c for c in cs if c["supported"] is False],
         "unchecked": [c for c in cs if c["supported"] is None]}
        for a in range(1, state.get("attempt", 0) + 1)
        for cs in [[c for c in state.get("checks", []) if c["attempt"] == a]]
    ]
    return state


def round_report(rounds: list[dict[str, Any]]) -> list[str]:
    """One line per draft, then what was dropped, removed or still unsupported in it."""
    lines = []
    for r in rounds:
        changes = "" if r["attempt"] == 1 else f", {r['rechecked']} rechecked, {len(r['dropped'])} dropped"
        unchecked = f", {len(r['unchecked'])} unchecked" if r["unchecked"] else ""
        lines.append(f"  draft {r['attempt']}: {r['claims']} points ({r['cited']} cited{changes}), "
                     f"{len(r['failed'])} unsupported{unchecked}")
        lines += [f"    dropped: \"{d['sentence']}\" {d['cv_ids']}" for d in r["dropped"]]
        lines += [f"    REMOVED SECTION: \"{title}\" - every point in it was dropped" for title in r["removed"]]
        lines += [f"    - \"{c['sentence']}\" {c['cv_ids']}: {c['problem']}" for c in r["failed"]]
    return lines


async def amain(args: argparse.Namespace) -> None:
    meta, posting = split_front_matter(args.posting.read_text(encoding="utf-8"))
    think = False if args.no_think else None
    model = make_model(args.provider, args.model, args.num_ctx, think=think if args.provider == "ollama" else None)
    checker = args.checker or args.provider
    checker_model = (make_model(checker, args.checker_model, args.num_ctx, think=think if checker == "ollama" else None)
                     if (checker, args.checker_model) != (args.provider, None) else None)
    concurrency = args.max_concurrency or (1 if "ollama" in (args.provider, checker) else None)

    async with AsyncExitStack() as stack:
        async def connect(name: str) -> ClientSession:
            read, write = await stack.enter_async_context(stdio_client(server(name)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session

        cv = await connect("cv-server")
        tracker = None if args.no_examples else await connect("tracker-server")
        if args.draw:
            print(build_letter_graph(model, cv, tracker, checker_model=checker_model).get_graph().draw_mermaid())
            return
        state = await run_letter(model, cv, posting, tracker=tracker, company=args.company or meta.get("company") or "",
                                 role=args.role or meta.get("role") or "", limit=args.limit,
                                 max_concurrency=concurrency,
                                 progress=lambda line: print(line, file=sys.stderr, flush=True),
                                 checker_model=checker_model)

    final = state["rounds"][-1] if state["rounds"] else {"failed": [], "unchecked": [], "claims": 0, "cited": 0}
    # Verified means points were checked and none failed - never "nothing to check".
    verified = bool(final["claims"]) and not final["failed"]
    company, role = args.company or meta.get("company"), args.role or meta.get("role")
    title = "Cover letter plan" + (f": {role}" if role else "") + (f" at {company}" if company else "")
    plan = f"# {title}\n\n{state.get('letter', '')}\n"
    if final["failed"]:  # never leave an unsupported point unmarked in the file you write from
        plan += "\n## Still unsupported - fix or drop before writing\n\n" + "\n".join(
            f"- \"{c['sentence']}\" {c['cv_ids']}: {c['problem']}" for c in final["failed"]) + "\n"
    dropped = state.get("dropped", [])
    removed = [r["title"] for r in state.get("removed", [])]
    if dropped:  # kept so you can rescue a point by hand, with what was wrong with it
        plan += ("\n## Dropped during checking\n\n"
                 + (f"Paragraphs removed because all their points were dropped: {', '.join(removed)}.\n\n"
                    if removed else "")
                 + "\n".join(f"- \"{d['sentence']}\" {d['cv_ids']}: {d['problem']}" for d in dropped) + "\n")
    turns = [state["extract_turn"], *(m["turn"] for m in state.get("matches", [])), *state.get("turns", [])]

    stem, now = args.posting.stem, datetime.now().isoformat(timespec="seconds")
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"{stem}.cover_letter_plan.md"
    out.write_text(plan, encoding="utf-8")
    LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    record = {"started_at": now, "provider": f"letter-{args.provider}" + ("-nothink" if args.no_think else ""),
              "model": model_name(model), "checker": f"{checker}/{model_name(checker_model or model)}",
              "posting": stem, "examples": len(state.get("examples", [])),
              "drafts": state.get("attempt", 0), "verified": verified, "rounds": state["rounds"],
              "letter": state.get("letter", ""),
              "input_tokens": sum(t.input_tokens for t in turns), "output_tokens": sum(t.output_tokens for t in turns)}
    with (LETTERS_DIR / f"{stem}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    print(plan)
    print("---")
    print(f"{record['provider']}/{record['model']}  checker={record['checker']}  drafts={record['drafts']}  "
          f"examples={record['examples']}  "
          + ("VERIFIED" if verified else "NOTHING CHECKED" if not final["claims"] else "NOT VERIFIED"))
    print("\n".join(round_report(state["rounds"])))
    if not verified:
        print("WARNING: the final plan still has unsupported points (listed above, and at the end of the file).")
    if removed:
        print(f"WARNING: {len(removed)} paragraph(s) were removed while fixing - the plan may be missing a section "
              "(e.g. the closing). Dropped points are listed at the end of the file.")
    print(f"tokens:  in={record['input_tokens']} out={record['output_tokens']}")
    print(f"plan saved to {out}")


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Plan a cover letter for a job posting, paragraph by paragraph, and verify its cited points.")
    parser.add_argument("posting", type=Path, help="text/markdown file containing the job posting")
    parser.add_argument("--company", help="defaults to the posting's header")
    parser.add_argument("--role", help="defaults to the posting's header")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--limit", type=int, default=10, help="results per search (default 10)")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="branches run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--checker", choices=["claude", "ollama"],
                        help="model provider for checking points (default: the same as --provider)")
    parser.add_argument("--checker-model", help="override the checker provider's default model")
    parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")
    parser.add_argument("--no-examples", action="store_true",
                        help="don't show past letters (skips connecting to tracker-server)")
    parser.add_argument("--draw", action="store_true", help="print the graph as a Mermaid diagram and exit")
    args = parser.parse_args()
    if args.no_think and "ollama" not in (args.provider, args.checker):
        parser.error("--no-think only applies when --provider or --checker is ollama")
    asyncio.run(amain(args))
