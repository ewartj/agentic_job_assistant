"""job-apply: assess a posting, draft the letter plan and the CV, and save them on approval.

    uv run job-apply data/evals/postings/acme_ds.md     # run until the review, then ask you
    uv run job-apply --list                             # every run and its status
    uv run job-apply --resume acme_ds-20261112-101500   # come back to a paused (or stopped) run

At the review you can approve (saved to the tracker), revise with notes (redrafted,
checked and shown again), reject (nothing saved), or leave it for later: the run is
kept in data/runs.db and --resume picks it up from the review, without redrafting.
"""
import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from job_agent.agent import OLLAMA_NUM_CTX, ROOT, utf8_output
from job_agent.cv import EMPLOYERS, TEMPLATE, Employers
from job_agent.graph_agent import make_model, server
from job_agent.letter import DRAFTS_DIR
from job_agent.workflow.graph import ApplySettings, build_apply_graph
from job_agent.workflow.models import Decision, RunView
from job_agent.workflow.service import ApplyService, open_checkpointer

RUNS_DB = ROOT / "data" / "runs.db"


@asynccontextmanager
async def open_service(args: argparse.Namespace) -> AsyncIterator[ApplyService]:
    """The service with its servers, models and checkpointer, all closed on exit."""
    think = False if args.no_think else None
    model = make_model(args.provider, args.model, args.num_ctx, think=think if args.provider == "ollama" else None)
    checker = args.checker or args.provider
    checker_model = (make_model(checker, args.checker_model, args.num_ctx, think=think if checker == "ollama" else None)
                     if (checker, args.checker_model) != (args.provider, None) else None)
    concurrency = 1 if "ollama" in (args.provider, checker) else None

    async with AsyncExitStack() as stack:
        async def connect(name: str) -> ClientSession:
            read, write = await stack.enter_async_context(stdio_client(server(name)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session

        settings = ApplySettings(model=model, cv=await connect("cv-server"), tracker=await connect("tracker-server"),
                                 employers=Employers.load(EMPLOYERS), template=TEMPLATE, drafts_dir=DRAFTS_DIR,
                                 checker_model=checker_model, max_concurrency=concurrency,
                                 progress=lambda line: print(line, file=sys.stderr, flush=True))
        checkpointer = await stack.enter_async_context(open_checkpointer(RUNS_DB))
        yield ApplyService(build_apply_graph(settings, checkpointer))


def show(view: RunView) -> None:
    print(f"\n{view.run_id}: {view.status}")
    if view.status == "saved":
        print(f"  saved to the tracker as application {view.application_id}")
    if not view.review:
        return
    r = view.review
    print(f"  {r.company or '(company?)'} - {r.role or '(role?)'}   review {r.round}")
    print("  assessment: " + ", ".join(f"{n} {label}" for label, n in r.verdicts.items()))
    for a in r.artifacts:
        state = "verified" if a.verified else f"{a.unsupported} UNSUPPORTED"
        print(f"  {a.kind}: {a.points} points, {state}, {a.drafts} draft(s), {a.dropped} dropped")
        print(f"    {a.document_path or a.plan_path}")
        for w in a.warnings:
            print(f"    warning: {w}")


def ask() -> Decision | None:
    """Your decision at the review, or None to leave the run paused (also when there's
    no terminal to ask on, e.g. input is piped: the run stays safely paused)."""
    try:
        return _ask()
    except EOFError:
        return None


def _ask() -> Decision | None:
    while True:
        choice = input("\n[a]pprove and save / [r]evise / re[j]ect / [l]ater: ").strip().lower()
        if choice in ("a", "approve"):
            return Decision(action="approve")
        if choice in ("j", "reject"):
            return Decision(action="reject")
        if choice in ("l", "later", ""):
            return None
        if choice in ("r", "revise"):
            notes = input("What should change? ").strip()
            target = {"l": "cover_letter_plan", "c": "cv"}.get(input("[b]oth / [l]etter / [c]v? ").strip().lower(),
                                                               "both")
            if notes:
                return Decision(action="revise", notes=notes, target=target)
            print("Revising needs a note on what to change.")


async def review_loop(service: ApplyService, view: RunView) -> None:
    """Show the review and act on your decisions until the run ends or you leave it."""
    show(view)
    while view.status == "awaiting_review":
        decision = ask()
        if decision is None:
            print(f"Left for later: uv run job-apply --resume {view.run_id}")
            return
        view = await service.decide(view.run_id, decision)
        show(view)


async def amain(args: argparse.Namespace) -> None:
    async with open_service(args) as service:
        if args.list:
            for view in await service.runs():
                print(f"{view.run_id:45} {view.status}")
            return
        if args.resume:
            view = await service.view(args.resume)
            if view.status == "stopped":
                print("Carrying on from the last saved step...", file=sys.stderr)
                view = await service.retry(args.resume)
        else:
            view = await service.start(args.posting)
        await review_loop(service, view)


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Assess a posting, draft the letter plan and CV, and save them on approval.")
    parser.add_argument("posting", type=Path, nargs="?", help="text/markdown file containing the job posting")
    parser.add_argument("--resume", metavar="RUN_ID", help="come back to a paused or stopped run")
    parser.add_argument("--list", action="store_true", help="list every run and its status")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--checker", choices=["claude", "ollama"],
                        help="model provider for checking points (default: the same as --provider)")
    parser.add_argument("--checker-model", help="override the checker provider's default model")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")
    args = parser.parse_args()
    if sum(map(bool, (args.posting, args.resume, args.list))) != 1:
        parser.error("give a posting, --resume RUN_ID or --list")
    if args.no_think and "ollama" not in (args.provider, args.checker):
        parser.error("--no-think only applies when --provider or --checker is ollama")
    asyncio.run(amain(args))
