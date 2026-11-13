"""The apply workflow as one graph: assess, plan the letter and the CV, pause, then save.

    START -> extract -> match (xN) -> assess -> letter --+
                                          \\--> cv ------+--> review --approve--> save -> END
                                                ^           (interrupt)  \\--reject--> END
                                                +--- revise: letter and/or cv, with your notes

    assess   the requirement verdicts, matched once and shared by the letter and the CV
    letter   letter.py's plan, draft and check loop, from that assessment
    cv       cv.py's tailored CV, from the same assessment, in parallel with the letter
    review   interrupt(): the run stops here with a Review, and is saved by the
             checkpointer, until a Decision resumes it - from this process or another
    save     the first writes anywhere in the agent, so only after you approve:
             log_application, then the assessment, letter plan and CV as drafts

New compared with letter.py:
    interrupt()   a node that waits for a person instead of a model
    checkpointer  every step is saved to SQLite, so a paused (or failed) run carries on
                  from where it was rather than starting again
"""
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt
from mcp import ClientSession

from job_agent import pipeline
from job_agent.cv import Employers, cv_drafting, read_template, save_cv
from job_agent.events import Extracted, Matched, ProgressEvent
from job_agent.letter import is_verified, letter_title, plan_document, run_letter
from job_agent.pipeline import PipelineState, assessment_nodes, render
from job_agent.workflow.models import Artifact, ArtifactKind, Decision, Review

KEY: dict[ArtifactKind, str] = {"cover_letter_plan": "letter", "cv": "cv"}  # artifact kind -> state key


class ApplyState(PipelineState, total=False):
    run_id: str
    started_at: float  # time.time() when the run started, for progress timings
    posting_name: str  # the saved posting's file name, without .md
    company: str
    role: str
    url: str
    assessment: str  # the verdicts as Markdown
    letter: dict[str, Any]  # an Artifact
    cv: dict[str, Any]  # an Artifact
    decision: dict[str, Any]  # the latest Decision
    review_round: int
    application_id: int


@dataclass
class ApplySettings:
    """Everything the graph needs that isn't part of a run's state."""
    model: BaseChatModel
    cv: ClientSession
    tracker: ClientSession
    employers: Employers
    template: Path
    drafts_dir: Path
    checker_model: BaseChatModel | None = None
    limit: int = 10
    max_concurrency: int | None = None
    progress: Callable[[ProgressEvent], None] | None = None


def artifact(kind: ArtifactKind, result: dict[str, Any], plan: str, plan_path: Path,
             document_path: Path | None = None, warnings: list[str] | None = None) -> Artifact:
    final = result["rounds"][-1] if result.get("rounds") else {"claims": 0, "failed": []}
    return Artifact(kind=kind, verified=is_verified(result.get("rounds", [])), points=final["claims"],
                    unsupported=len(final["failed"]), dropped=len(result.get("dropped", [])),
                    drafts=result.get("attempt", 0), plan=plan, plan_path=str(plan_path),
                    document_path=str(document_path) if document_path else None, warnings=warnings or [])


def verdict_counts(matches: list[dict[str, Any]]) -> dict[str, int]:
    return {label: sum(m["verdict"] == verdict for m in matches)
            for verdict, label in (("met", "met"), ("partly", "partly met"), ("gap", "not met"))}


def build_apply_graph(s: ApplySettings, checkpointer: Any = None):
    template = read_template(s.template, s.employers)  # fails now, not after the letter, if it's wrong
    extract, fan_out, match = assessment_nodes(s.model, s.cv, s.limit, if_none="assess")
    s.drafts_dir.mkdir(parents=True, exist_ok=True)

    def report(event: ProgressEvent) -> None:
        if s.progress:
            s.progress(event)

    def about(kind: ArtifactKind) -> Callable[[ProgressEvent], None]:
        """Progress from the letter or CV loop, labelled with which one it is."""
        return lambda event: report(event.model_copy(update={"artifact": kind}))

    def since(started_at: float | None) -> float:
        return round(time.time() - started_at, 1) if started_at else 0.0

    # The shared assessment's own steps, reported like the letter's and CV's.
    async def extract_reported(state: ApplyState) -> dict[str, Any]:
        update = await extract(state)
        report(Extracted(requirements=len(update["requirements"]), elapsed=since(state.get("started_at"))))
        return update

    def fan_out_timed(state: ApplyState) -> list[Send] | str:
        # Each match branch only gets its Send payload, so the run's start time rides along.
        sends = fan_out(state)
        if isinstance(sends, str):
            return sends
        return [Send(send.node, {**send.arg, "started_at": state.get("started_at")}) for send in sends]

    async def match_reported(branch: dict[str, Any]) -> dict[str, Any]:
        update = await match(branch)
        (m,) = update["matches"]
        report(Matched(requirement=m["requirement"].name, kind=m["requirement"].kind, verdict=m["verdict"],
                       elapsed=since(branch.get("started_at"))))
        return update

    def shared_assessment(state: ApplyState) -> dict[str, Any]:
        return {k: state[k] for k in ("requirements", "matches", "extract_turn") if k in state}

    def revision(state: ApplyState, kind: ArtifactKind) -> tuple[str, str]:
        """(notes, previous plan) when the latest decision asks to revise this artifact."""
        decision = state.get("decision") or {}
        if decision.get("action") == "revise" and decision.get("target") in ("both", kind):
            return decision["notes"], (state.get(KEY[kind]) or {}).get("plan", "")
        return "", ""

    def assess(state: ApplyState) -> dict[str, Any]:
        return {"assessment": render(state.get("matches", []))}

    async def letter(state: ApplyState) -> dict[str, Any]:
        notes, previous = revision(state, "cover_letter_plan")
        result = await run_letter(s.model, s.cv, state["posting"], tracker=s.tracker, company=state["company"],
                                  role=state["role"], limit=s.limit, max_concurrency=s.max_concurrency,
                                  checker_model=s.checker_model, assessment=shared_assessment(state),
                                  notes=notes, previous=previous, progress=about("cover_letter_plan"))
        plan = plan_document(result, letter_title(state["company"], state["role"]))
        path = s.drafts_dir / f"{state['posting_name']}.cover_letter_plan.md"
        path.write_text(plan, encoding="utf-8")
        return {"letter": artifact("cover_letter_plan", result, plan, path).model_dump()}

    async def cv(state: ApplyState) -> dict[str, Any]:
        notes, previous = revision(state, "cv")
        result = await run_letter(s.model, s.cv, state["posting"], company=state["company"], role=state["role"],
                                  limit=s.limit, max_concurrency=s.max_concurrency, checker_model=s.checker_model,
                                  drafting=cv_drafting(s.employers, template), examples=[template.current()],
                                  assessment=shared_assessment(state), notes=notes, previous=previous,
                                  progress=about("cv"))
        docx_path, plan_path, warnings = save_cv(result, s.template, s.employers, s.drafts_dir,
                                                 state["posting_name"], state["role"])
        plan = plan_path.read_text(encoding="utf-8")
        return {"cv": artifact("cv", result, plan, plan_path, docx_path, warnings).model_dump()}

    def review(state: ApplyState) -> dict[str, Any]:
        # On resume, LangGraph runs this node again from the top, and interrupt() then
        # returns the Decision it was resumed with - so nothing before it may have side effects.
        round_ = state.get("review_round", 0) + 1
        shown = Review(run_id=state["run_id"], posting=state["posting_name"], company=state["company"],
                       role=state["role"], assessment=state.get("assessment", ""),
                       verdicts=verdict_counts(state.get("matches", [])),
                       artifacts=[Artifact(**state["letter"]), Artifact(**state["cv"])], round=round_)
        decision = Decision.model_validate(interrupt(shown.model_dump()))
        return {"decision": decision.model_dump(), "review_round": round_}

    def after_review(state: ApplyState) -> str | list[str]:
        decision = Decision.model_validate(state["decision"])
        if decision.action == "approve":
            return "save"
        if decision.action == "reject":
            return END
        return ["letter", "cv"] if decision.target == "both" else [KEY[decision.target]]

    async def save(state: ApplyState) -> dict[str, Any]:
        application = await call(s.tracker, "log_application", {
            "company": state["company"], "role": state["role"], "posting": state["posting_name"],
            "url": state.get("url") or None})
        cv_text = state["cv"]["plan"] + f"\n\nWord document: {state['cv']['document_path']}\n"
        for kind, text in (("assessment", state["assessment"]), ("cover_letter_plan", state["letter"]["plan"]),
                           ("cv", cv_text)):
            await call(s.tracker, "save_draft", {"application_id": application["id"], "kind": kind, "text": text})
        return {"application_id": application["id"]}

    graph = StateGraph(ApplyState)
    graph.add_node("extract", extract_reported, retry_policy=pipeline.RETRY)
    graph.add_node("match", match_reported, retry_policy=pipeline.RETRY)
    graph.add_node("assess", assess)
    graph.add_node("letter", letter, retry_policy=pipeline.RETRY)
    graph.add_node("cv", cv, retry_policy=pipeline.RETRY)
    graph.add_node("review", review)
    graph.add_node("save", save)
    graph.add_edge(START, "extract")
    graph.add_conditional_edges("extract", fan_out_timed, ["match", "assess"])
    graph.add_edge("match", "assess")
    graph.add_edge("assess", "letter")  # two edges out: the letter and the CV run in parallel
    graph.add_edge("assess", "cv")
    graph.add_edge("letter", "review")  # review waits for whichever of them ran this step
    graph.add_edge("cv", "review")
    graph.add_conditional_edges("review", after_review, ["save", "letter", "cv", END])
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)


async def call(session: ClientSession, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """A tracker write; a tool error raises, so the run stops and can be retried."""
    result = await session.call_tool(tool, args)
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise RuntimeError(f"{tool} failed: {text}")
    return result.structured_content
