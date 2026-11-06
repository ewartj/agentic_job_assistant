"""The assessment as a multi-step graph (Phase 3, step 5a).

Instead of one agent deciding what to search, the work is split into steps:

    START -> extract -> match (one branch per requirement, in parallel) -> report -> END

    extract  one model call with structured output: the posting's requirements,
             each with the search queries that would find evidence for it
    match    per requirement: run its queries against cv-server, then one model
             call with structured output judging met / partly / gap and the ids
    report   no model call: the verdicts written up as the assessment

New compared with state_graph.py:
    structured output  model.with_structured_output(Schema) returns a validated
                       Pydantic object, not text to parse
    Send               extract's router returns one Send per requirement, so
                       "match" runs N times in parallel, each with its own input
    reducers           the branches' results are merged with operator.add, the
                       same way state_graph.py merges retrieved_ids

The trace has the same format as the agents', so job-score scores it as
`pipeline-claude` / `pipeline-ollama`:

    uv run job-pipeline data/evals/postings/bbc_mle.md
    uv run job-pipeline data/evals/postings/bbc_mle.md --provider ollama
"""
import argparse
import asyncio
import operator
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from cv_server.postings import split_front_matter
from job_agent.agent import (
    CV_ID,
    OLLAMA_NUM_CTX,
    ROOT,
    RUNS_DIR,
    ToolRequest,
    Trace,
    Turn,
    call_mcp_tool,
    utf8_output,
)
from job_agent.graph_agent import make_model, model_name, server

# --- What the model returns (structured output) ------------------------------

class Requirement(BaseModel):
    name: str = Field(description="The requirement in a few words, e.g. 'AWS (SageMaker, S3)'")
    kind: Literal["essential", "desired"]
    queries: list[str] = Field(
        description="2-4 short keyword searches (1-3 words each) for evidence in the user's CV: "
                    "the skill itself, a synonym, and the kind of outcome or story that would show it",
    )


class Requirements(BaseModel):
    requirements: list[Requirement]


class Judgement(BaseModel):
    verdict: Literal["met", "partly", "gap"]
    cv_ids: list[str] = Field(description="Ids of the entries that support the verdict, e.g. ['cv-012']; "
                                          "only ids shown in the evidence; empty for a gap")
    summary: str = Field(description="One or two sentences to the user, as 'you', on what the evidence shows")


EXTRACT_PROMPT = """\
You read job postings and list what they ask for.

List the posting's main requirements - skills, experience and behaviours - as \
essential or desired. Merge near-duplicates.

Never list logistics, wherever the posting mentions them: salary, benefits, \
location, office attendance or hybrid working, visas and right to work, start \
date. A CV can't show these, so they can't be met.

Read the whole posting, not only its lists. Requirements are often stated in \
prose about the team, the product or the domain: how the team works, what the \
domain demands (e.g. "data integrity matters"), or what you'll need to pick up. \
Each of those is a requirement too.

When the posting says something isn't required (e.g. "you don't need to know \
our stack"), don't list that thing as a requirement. List what it asks for \
instead, such as learning unfamiliar technology quickly.

For each requirement, write 2-4 search queries for a keyword search over the \
user's CV. Keep each query to 1-3 words. Include the skill itself, a synonym or \
broader term, and the kind of outcome or story that would show it (for \
"ownership": "sole ownership", "research to production"; for "mentoring": \
"mentored", "onboarding")."""

MATCH_PROMPT = """\
You judge whether the user's CV evidence meets one requirement of a job posting.

You are given the requirement and the CV entries that searches for it returned. \
Decide whether it is met, partly met or a gap, based only on these entries. \
List the ids of the entries that support your verdict - every one that is good \
evidence, and only ids shown below. A gap has no ids.

Write the summary to the user as "you". Never refer to them in the third person \
or assume their pronouns."""


# --- The state ----------------------------------------------------------------

class Match(TypedDict):
    index: int  # the requirement's position, so the report keeps the posting's order
    requirement: Requirement
    verdict: str  # met | partly | gap | error
    cv_ids: list[str]
    summary: str
    turn: Turn  # this branch's searches and model call, for the trace


class PipelineState(TypedDict, total=False):
    posting: str
    requirements: list[Requirement]
    # Written by every match branch in the same step, so each needs a reducer:
    # without one, LangGraph raises rather than pick one branch's value.
    matches: Annotated[list[Match], operator.add]
    extract_turn: Turn  # extract's model call, for the trace
    report: str


class MatchInput(TypedDict):
    """What each match branch receives - not the whole state, just its own Send payload."""
    index: int
    requirement: Requirement


# A dropped connection (e.g. Ollama closing a queued request) reruns just that node,
# with backoff, instead of failing the whole run. LangGraph's default retry_on skips
# programming errors such as ValueError and KeyError, so a bug still fails at once.
RETRY = RetryPolicy(max_attempts=3, initial_interval=5.0)


def usage(raw: Any) -> tuple[int, int]:
    meta = getattr(raw, "usage_metadata", None) or {}
    return meta.get("input_tokens", 0), meta.get("output_tokens", 0)


def build_pipeline(model: BaseChatModel, session: ClientSession, limit: int = 10):
    # include_raw keeps the AIMessage alongside the parsed object, for token counts,
    # and returns a parsing error instead of raising one.
    extractor = model.with_structured_output(Requirements, method="json_schema", include_raw=True)
    judge = model.with_structured_output(Judgement, method="json_schema", include_raw=True)

    async def extract(state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        out = await extractor.ainvoke([SystemMessage(EXTRACT_PROMPT), HumanMessage(state["posting"])])
        found = out["parsed"].requirements if out["parsed"] else []
        tokens_in, tokens_out = usage(out["raw"])
        text = ("\n".join(f"{r.kind}: {r.name} {r.queries}" for r in found)
                or f"(no requirements: {out['parsing_error']})")
        turn = Turn(text=text, tool_calls=[], input_tokens=tokens_in, output_tokens=tokens_out,
                    seconds=round(time.perf_counter() - started, 2))
        return {"requirements": found, "extract_turn": turn}

    def fan_out(state: PipelineState) -> list[Send] | str:
        # One Send per requirement: LangGraph runs "match" once for each, in parallel,
        # and waits for them all before moving on to "report".
        if not state.get("requirements"):
            return "report"
        return [Send("match", MatchInput(index=i, requirement=r)) for i, r in enumerate(state["requirements"])]

    async def match(branch: MatchInput) -> dict[str, Any]:
        started = time.perf_counter()
        requirement = branch["requirement"]
        # The searches are chosen by extract, not by the model here, so they all run at once.
        results = await asyncio.gather(*(
            call_mcp_tool(session, ToolRequest(call_id="", name="get_cv_evidence", args={"query": q, "limit": limit}))
            for q in requirement.queries
        ))
        calls = [{"name": r.name, "args": {"query": q, "limit": limit}, "cv_ids": r.cv_ids, "is_error": r.is_error}
                 for q, r in zip(requirement.queries, results, strict=True)]

        evidence = "\n\n".join(f"Search: {q}\n{r.content}" for q, r in zip(requirement.queries, results, strict=True))
        out = await judge.ainvoke([
            SystemMessage(MATCH_PROMPT),
            HumanMessage(f"Requirement ({requirement.kind}): {requirement.name}\n\nCV evidence:\n\n{evidence}"),
        ])
        judgement: Judgement | None = out["parsed"]
        verdict = judgement.verdict if judgement else "error"
        cv_ids = judgement.cv_ids if judgement else []
        tokens_in, tokens_out = usage(out["raw"])
        turn = Turn(text=f"{requirement.name}: {verdict} {cv_ids}", tool_calls=calls, input_tokens=tokens_in,
                    output_tokens=tokens_out, seconds=round(time.perf_counter() - started, 2))
        result = Match(index=branch["index"], requirement=requirement, verdict=verdict, cv_ids=cv_ids,
                       summary=judgement.summary if judgement else f"Could not judge: {out['parsing_error']}",
                       turn=turn)
        return {"matches": [result]}

    def report(state: PipelineState) -> dict[str, Any]:
        return {"report": render(state.get("matches", []))}

    graph = StateGraph(PipelineState)
    graph.add_node("extract", extract, retry_policy=RETRY)
    graph.add_node("match", match, retry_policy=RETRY)
    graph.add_node("report", report)
    graph.add_edge(START, "extract")
    graph.add_conditional_edges("extract", fan_out, ["match", "report"])
    graph.add_edge("match", "report")
    graph.add_edge("report", END)
    return graph.compile()


# --- The report ---------------------------------------------------------------

LABELS = {"met": "Met", "partly": "Partly met", "gap": "Not met", "error": "Not judged"}


def render(matches: list[Match]) -> str:
    """The verdicts as the assessment, in the posting's order. Citations are written
    [cv-012] so job-score reads them like any agent's answer."""
    if not matches:
        return "No requirements were found in the posting."
    ordered = sorted(matches, key=lambda m: m["index"])
    lines = []
    for kind, title in (("essential", "Essential requirements"), ("desired", "Desired requirements")):
        group = [m for m in ordered if m["requirement"].kind == kind]
        if group:
            lines.append(f"## {title}\n")
            for m in group:
                cites = " ".join(f"[{i}]" for i in m["cv_ids"])
                lines.append(f"- **{m['requirement'].name}: {LABELS[m['verdict']]}.** {m['summary']} {cites}".rstrip())
            lines.append("")
    counts = {v: sum(m["verdict"] == v for m in ordered) for v in LABELS}
    lines.append(f"**Overall:** {counts['met']} met, {counts['partly']} partly met, {counts['gap']} not met"
                 + (f", {counts['error']} not judged" if counts["error"] else "") + f" of {len(ordered)}.")
    return "\n".join(lines)


# --- Run ----------------------------------------------------------------------

def describe(node: str, update: dict[str, Any], elapsed: float) -> str | None:
    """A progress line for one finished node, or None for nodes not worth a line."""
    if node == "extract":
        return f"[{elapsed:4.0f}s] extracted {len(update.get('requirements', []))} requirements"
    if node == "match":
        (m,) = update["matches"]
        return f"[{elapsed:4.0f}s]   {m['requirement'].name}: {m['verdict']} {m['cv_ids']}  ({m['turn'].seconds:.0f}s)"
    return None


async def run_pipeline(model: BaseChatModel, session: ClientSession, posting: str, provider: str = "pipeline",
                       limit: int = 10, progress: Callable[[str], None] | None = None,
                       max_concurrency: int | None = None) -> Trace:
    """max_concurrency caps how many match branches run at once (None: all of them)."""
    trace = Trace(task=posting, provider=provider, model=model_name(model), mode="tools")
    # Two stream modes at once: "updates" says what each node added as it finishes (for
    # progress), "values" is the whole state after each step (the last one is the result).
    state: dict[str, Any] = {}
    started = time.perf_counter()
    if progress:
        progress(f"[   0s] extracting requirements with {model_name(model)}...")
    async for mode, chunk in build_pipeline(model, session, limit).astream(
            {"posting": posting}, {"max_concurrency": max_concurrency}, stream_mode=["updates", "values"]):
        if mode == "values":
            state = chunk
        elif progress:
            for node, update in chunk.items():
                if line := describe(node, update or {}, time.perf_counter() - started):
                    progress(line)
    # One turn per model call: extract's, then each match branch's in the posting's order.
    matches = sorted(state.get("matches", []), key=lambda m: m["index"])
    trace.turns = [state["extract_turn"], *(m["turn"] for m in matches)]
    trace.final_answer = state["report"]
    trace.cited_ids = sorted(set(CV_ID.findall(trace.final_answer)))
    trace.stop = "done" if state.get("requirements") else "no_requirements"
    return trace


async def amain(args: argparse.Namespace) -> None:
    _, posting = split_front_matter(args.posting.read_text(encoding="utf-8"))
    model = make_model(args.provider, args.model, args.num_ctx, think=False if args.no_think else None)
    # Ollama on one machine answers one request at a time anyway; sending them all at once
    # only fills its queue, where long waits can end in a dropped connection.
    concurrency = args.max_concurrency or (1 if args.provider == "ollama" else None)
    provider = f"pipeline-{args.provider}" + ("-nothink" if args.no_think else "")

    async with stdio_client(server("cv-server")) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        if args.draw:
            print(build_pipeline(model, session).get_graph().draw_mermaid())
            return
        # Progress goes to stderr, so it still shows when the report is sent to /dev/null.
        trace = await run_pipeline(model, session, posting, provider=provider, limit=args.limit,
                                   progress=lambda line: print(line, file=sys.stderr, flush=True),
                                   max_concurrency=concurrency)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{args.posting.stem}.jsonl"
    with out.open("a", encoding="utf-8") as f:
        f.write(trace.to_json() + "\n")

    extract_turn, *match_turns = trace.turns
    print(trace.final_answer)
    print("\n---")
    print(f"{trace.provider}/{trace.model}  stop={trace.stop}  requirements={len(match_turns)}  "
          f"model calls={len(trace.turns)}")
    for t in match_turns:
        print(f"  {t.text}   searches: {[c['args']['query'] for c in t.tool_calls]}")
    print(f"retrieved:  {trace.retrieved_ids}")
    print(f"cited:      {trace.cited_ids}")
    unseen = sorted(set(trace.cited_ids) - set(trace.retrieved_ids))
    if unseen:
        print(f"WARNING cited but never retrieved: {unseen}")
    print(f"tokens:     in={sum(t.input_tokens for t in trace.turns)} out={sum(t.output_tokens for t in trace.turns)}")
    print(f"trace appended to {out}")


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Assess fit for a job posting as a multi-step graph.")
    parser.add_argument("posting", type=Path, help="text/markdown file containing the job posting")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--limit", type=int, default=10, help="results per search (default 10)")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="match branches run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--no-think", action="store_true",
                        help="turn off the Ollama model's thinking (recorded as pipeline-ollama-nothink)")
    parser.add_argument("--draw", action="store_true", help="print the graph as a Mermaid diagram and exit")
    args = parser.parse_args()
    if args.no_think and args.provider != "ollama":
        parser.error("--no-think only applies to --provider ollama")
    asyncio.run(amain(args))
