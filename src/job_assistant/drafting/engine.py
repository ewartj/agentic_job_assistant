"""The drafting engine: plan a document from evidence, check every point, fix what fails.

The same graph drafts a cover letter plan (letter.py) and a tailored CV (cv.py); each
supplies a Drafting with its prompt, evidence and rules. The engine knows neither.

    START -> examples -> extract -> match (xN) -> draft -> check (one per point) -> review
                                                    ^                                 |
                                                    +---- problems, up to 3 drafts ---+--> END

    examples  whatever the Drafting's find_examples looks up (the letter: past letters)
    extract, match   the assessment's nodes; skipped when an assessment is passed in
    draft     plans the document (structured output) from the evidence only; each point
              has its cv_ids. On a redraft, the model sees only the failed points and
              returns a fix or a drop for each; code applies them, so points that passed
              can't be lost or changed
    check     one branch per new or changed point (checker.py). Each entry's text comes
              from cv-server's cv://entry/{id} resource. Points unchanged since the last
              draft keep their verdict
    review    collects this draft's failed checks; the router sends the plan back to
              draft with them, or ends once it passes or after MAX_DRAFTS drafts
"""
import json
import operator
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from mcp import ClientSession
from pydantic import BaseModel, Field

from job_assistant.assessment import pipeline
from job_assistant.assessment.pipeline import LABELS, PipelineState, assessment_nodes
from job_assistant.drafting.checker import Check, verify
from job_assistant.events import Checked, Drafted, Extracted, Matched, ProgressEvent, Started
from job_assistant.llm import model_name, usage
from job_assistant.traces import CV_ID, Turn

MAX_DRAFTS = 3  # the first draft plus up to two redrafts

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


class DraftState(PipelineState, total=False):
    company: str
    role: str
    examples: list[str]
    notes: str  # the user's requested changes, when revising an approved-for-review version
    previous: str  # that version, as Markdown
    plan: list[PlanParagraph]  # the current plan, owned by code: the model only proposes changes
    document: str  # the plan as Markdown, with its [cv-012] citations
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
    """What a document type sets (letter.py, cv.py): everything else - matching,
    checking, patch-based fixing, the loop - is shared, and knows nothing of either.

    prompt         the planner's system prompt
    evidence       builds the evidence block the planner and fixer see (default: the
                   requirement verdicts and the matched entries' text)
    allowed        section -> the only ids its points may cite, or None for any. A point
                   citing anything else fails without a model call: on a CV, a bullet
                   must come from its own job
    find_examples  looks up examples of tone and structure (the letter's: past letters,
                   via the tracker); None uses any examples passed in with the input
    """
    prompt: str
    evidence: Callable[["DraftState", EntryText], Awaitable[str]] | None = None
    allowed: Callable[[str], set[str] | None] | None = None
    find_examples: Callable[[], Awaitable[list[str]]] | None = None
    examples_label: str = "Examples (for tone and structure only)"
    noun: str = "document"


def build_draft_graph(model: BaseChatModel, cv: ClientSession, drafting: Drafting, limit: int = 10,
                      checker_model: BaseChatModel | None = None):
    """checker_model, if given, checks the points; `model` does everything else. The
    check is small but it's where accuracy matters most, so it can be a stronger model."""
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

    async def examples(state: DraftState) -> dict[str, Any]:
        if drafting.find_examples is None:
            return {}  # none to look up: any examples came with the input
        return {"examples": await drafting.find_examples()}

    async def evidence_block(state: DraftState) -> str:
        if drafting.evidence:
            return await drafting.evidence(state, entry_text)
        return await matched_evidence(state)

    async def matched_evidence(state: DraftState) -> str:
        matches = sorted(state.get("matches", []), key=lambda m: m["index"])
        ids = list(dict.fromkeys(i for m in matches for i in m["cv_ids"]))
        texts = {i: await entry_text(i) for i in ids}
        verdicts = "\n".join(f"- {m['requirement'].name} ({m['requirement'].kind}): {LABELS[m['verdict']]}. "
                             f"{m['summary']} {m['cv_ids']}" for m in matches)
        evidence = "\n".join(f"[{i}] {t}" for i, t in texts.items() if t)
        return f"Requirement verdicts:\n{verdicts or '(none)'}\n\nCV entries:\n{evidence or '(none)'}"

    async def draft(state: DraftState) -> dict[str, Any]:
        started = time.perf_counter()
        attempt = state.get("attempt", 0) + 1
        if attempt == 1:
            return await first_draft(state, started)
        return await redraft(state, attempt, started)

    async def first_draft(state: DraftState, started: float) -> dict[str, Any]:
        shown = "\n\n---\n\n".join(state.get("examples", [])) or "(none available)"
        out = await planner.ainvoke([
            SystemMessage(drafting.prompt),
            HumanMessage(f"Company: {state.get('company') or 'see the posting'}\n"
                         f"Role: {state.get('role') or 'see the posting'}\n\n{await evidence_block(state)}\n\n"
                         f"{drafting.examples_label}:\n\n{shown}\n\n"
                         f"The posting:\n\n{state['posting']}"),
            *revision_request(state),
        ])
        turn = model_turn("draft 1", out["raw"], started)
        outline: Outline | None = out["parsed"]
        if outline is None:  # nothing to check; the run ends and reports it
            return {"plan": [], "document": f"(Could not plan the {drafting.noun}: {out['parsing_error']})",
                    "to_check": [],
                    "attempt": 1, "turns": [turn]}
        plan = to_plan(outline)
        return {"plan": plan, "document": render_plan(plan), "to_check": plan_claims(plan), "attempt": 1,
                "turns": [turn]}

    def revision_request(state: DraftState) -> list[HumanMessage]:
        if not state.get("notes"):
            return []
        return [HumanMessage(f"The user reviewed this version:\n\n{state.get('previous') or '(not available)'}\n\n"
                             f"and asks for these changes:\n\n{state['notes']}\n\n"
                             f"Write the {drafting.noun} again with those changes. Every rule still applies: "
                             "if a change asks for something the entries don't support, leave it out.")]

    async def redraft(state: DraftState, attempt: int, started: float) -> dict[str, Any]:
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
        return {"plan": plan, "document": render_plan(plan), "to_check": [c for c in claims if c["pid"] in changed],
                "checks": carried, "attempt": attempt,
                "dropped": [{**c, "attempt": attempt} for c in failed if c["pid"] in dropped],
                "removed": [{"attempt": attempt, "title": t} for t in removed],
                "turns": [model_turn(f"draft {attempt}: {len(changed)} fixed, {len(dropped)} dropped",
                                     out["raw"], started)]}

    def fan_out_checks(state: DraftState) -> list[Send] | str:
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

    def review(state: DraftState) -> dict[str, Any]:
        return {}  # a join point: everything it needs is already in the state

    def after_review(state: DraftState) -> str:
        failed = [c for c in state.get("checks", []) if c["attempt"] == state["attempt"] and c["supported"] is False]
        return "draft" if failed and state["attempt"] < MAX_DRAFTS else END

    graph = StateGraph(DraftState)
    graph.add_node("examples", examples)
    graph.add_node("extract", extract, retry_policy=pipeline.RETRY)
    graph.add_node("match", match, retry_policy=pipeline.RETRY)
    graph.add_node("draft", draft, retry_policy=pipeline.RETRY)
    graph.add_node("check", check, retry_policy=pipeline.RETRY)
    graph.add_node("review", review)
    graph.add_edge(START, "examples")
    # An assessment already done elsewhere (the apply workflow matches once for the letter
    # and the CV) comes in with the input, so extract and match are skipped.
    graph.add_conditional_edges("examples", lambda state: "draft" if "requirements" in state else "extract",
                                ["extract", "draft"])
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


# --- Run ----------------------------------------------------------------------

async def run_draft(model: BaseChatModel, cv: ClientSession, posting: str, drafting: Drafting, *,
                    company: str = "", role: str = "", limit: int = 10, max_concurrency: int | None = None,
                    progress: Callable[[ProgressEvent], None] | None = None,
                    checker_model: BaseChatModel | None = None, examples: list[str] | None = None,
                    assessment: dict[str, Any] | None = None, notes: str = "", previous: str = "") -> dict[str, Any]:
    """Run the graph; returns the final state plus `rounds`, a per-draft summary of the checks.

    assessment: requirements, matches and extract_turn from a run already done, to
    reuse instead of extracting and matching again. notes and previous: a revision.
    progress: called with an event as each step finishes (events.py).
    """
    report = progress or (lambda event: None)
    started = time.perf_counter()
    report(Started(model=model_name(model), shared_assessment=assessment is not None))
    state: dict[str, Any] = {}
    inputs = {"posting": posting, "company": company, "role": role, **(assessment or {})}
    if examples is not None:
        inputs["examples"] = examples
    if notes:
        inputs |= {"notes": notes, "previous": previous}
    async for mode, chunk in build_draft_graph(model, cv, drafting, limit, checker_model).astream(
            inputs,
            {"max_concurrency": max_concurrency}, stream_mode=["updates", "values"]):
        if mode == "values":
            state = chunk
            continue
        elapsed = round(time.perf_counter() - started, 1)
        for node, update in chunk.items():
            if event := progress_event(node, update or {}, elapsed):
                report(event)
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


def progress_event(node: str, update: dict[str, Any], elapsed: float) -> ProgressEvent | None:
    """The event for a node that just finished, from what it added to the state."""
    if node == "extract":
        return Extracted(requirements=len(update.get("requirements", [])), elapsed=elapsed)
    if node == "match":
        (m,) = update["matches"]
        return Matched(requirement=m["requirement"].name, kind=m["requirement"].kind, verdict=m["verdict"],
                       elapsed=elapsed)
    if node == "draft":
        return Drafted(attempt=update["attempt"], to_check=len(update["to_check"]),
                       dropped=len(update.get("dropped", [])), elapsed=elapsed)
    if node == "check":
        (c,) = update["checks"]
        return Checked(attempt=c["attempt"], point=c["sentence"], cv_ids=c["cv_ids"], supported=c["supported"],
                       problem=c["problem"], elapsed=elapsed)
    return None


def is_verified(rounds: list[dict[str, Any]]) -> bool:
    """Points were checked and none failed - never "nothing to check"."""
    final = rounds[-1] if rounds else {"failed": [], "claims": 0}
    return bool(final["claims"]) and not final["failed"]


def plan_document(state: dict[str, Any], title: str, what: str = "Paragraphs") -> str:
    """The plan as the Markdown file you work from: the plan, then anything still
    unsupported, then what was dropped while checking, so nothing is lost silently."""
    failed = state["rounds"][-1]["failed"] if state.get("rounds") else []
    doc = f"# {title}\n\n{state.get('document', '')}\n"
    if failed:
        doc += "\n## Still unsupported - fix or drop before writing\n\n" + "\n".join(
            f"- \"{c['sentence']}\" {c['cv_ids']}: {c['problem']}" for c in failed) + "\n"
    dropped = state.get("dropped", [])
    removed = [r["title"] for r in state.get("removed", [])]
    if dropped:  # kept so you can rescue a point by hand, with what was wrong with it
        doc += ("\n## Dropped during checking\n\n"
                + (f"{what} removed because all their points were dropped: {', '.join(removed)}.\n\n"
                   if removed else "")
                + "\n".join(f"- \"{d['sentence']}\" {d['cv_ids']}: {d['problem']}" for d in dropped) + "\n")
    return doc


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


