"""job-pipeline: assess a posting with the extract -> parallel match -> report pipeline."""
import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from job_assistant.assessment.pipeline import build_pipeline, run_pipeline
from job_assistant.config import DEFAULT_NUM_CTX, ROOT, get_settings
from job_assistant.console import utf8_output
from job_assistant.llm import make_model
from job_assistant.mcp import server
from job_postings import split_front_matter


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

    traces = get_settings().traces_dir
    traces.mkdir(parents=True, exist_ok=True)
    out = traces / f"{args.posting.stem}.jsonl"
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
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="match branches run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--no-think", action="store_true",
                        help="turn off the Ollama model's thinking (recorded as pipeline-ollama-nothink)")
    parser.add_argument("--draw", action="store_true", help="print the graph as a Mermaid diagram and exit")
    args = parser.parse_args()
    if args.no_think and args.provider != "ollama":
        parser.error("--no-think only applies to --provider ollama")
    asyncio.run(amain(args))
