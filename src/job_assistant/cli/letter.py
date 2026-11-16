"""job-letter: plan a cover letter for a posting, checking every point."""
import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from job_assistant.config import DEFAULT_NUM_CTX, ROOT, get_settings
from job_assistant.console import utf8_output
from job_assistant.drafting.engine import (
    build_draft_graph,
    is_verified,
    plan_document,
    round_report,
)
from job_assistant.drafting.letter import letter_drafting, letter_title, run_letter
from job_assistant.events import print_progress
from job_assistant.llm import make_model, model_name
from job_assistant.mcp import server
from job_postings import split_front_matter


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
            graph = build_draft_graph(model, cv, letter_drafting(cv, tracker), checker_model=checker_model)
            print(graph.get_graph().draw_mermaid())
            return
        state = await run_letter(model, cv, posting, tracker=tracker, company=args.company or meta.get("company") or "",
                                 role=args.role or meta.get("role") or "", limit=args.limit,
                                 max_concurrency=concurrency,
                                 progress=print_progress,
                                 checker_model=checker_model)

    final = state["rounds"][-1] if state["rounds"] else {"failed": [], "unchecked": [], "claims": 0, "cited": 0}
    verified = is_verified(state["rounds"])
    company, role = args.company or meta.get("company"), args.role or meta.get("role")
    plan = plan_document(state, letter_title(company, role))
    removed = [r["title"] for r in state.get("removed", [])]
    turns = [state["extract_turn"], *(m["turn"] for m in state.get("matches", [])), *state.get("turns", [])]

    stem, now = args.posting.stem, datetime.now().isoformat(timespec="seconds")
    settings = get_settings()
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    out = settings.drafts_dir / f"{stem}.cover_letter_plan.md"
    out.write_text(plan, encoding="utf-8")
    settings.letter_traces_dir.mkdir(parents=True, exist_ok=True)
    record = {"started_at": now, "provider": f"letter-{args.provider}" + ("-nothink" if args.no_think else ""),
              "model": model_name(model), "checker": f"{checker}/{model_name(checker_model or model)}",
              "posting": stem, "examples": len(state.get("examples", [])),
              "drafts": state.get("attempt", 0), "verified": verified, "rounds": state["rounds"],
              "letter": state.get("document", ""),
              "input_tokens": sum(t.input_tokens for t in turns), "output_tokens": sum(t.output_tokens for t in turns)}
    with (settings.letter_traces_dir / f"{stem}.jsonl").open("a", encoding="utf-8") as f:
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
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="Ollama context window in tokens")
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
