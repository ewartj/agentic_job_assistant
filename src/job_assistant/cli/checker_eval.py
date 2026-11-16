"""job-verify-eval: score the checker against hand-labelled claims."""
import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from job_assistant.config import DEFAULT_NUM_CTX, ROOT, get_settings
from job_assistant.console import utf8_output
from job_assistant.evals.checker_eval import load_claims, run_eval, score
from job_assistant.llm import make_model, model_name
from job_assistant.mcp import server


async def amain(args: argparse.Namespace) -> None:
    claims = load_claims(args.file)
    model = make_model(args.provider, args.model, args.num_ctx, think=False if args.no_think else None)
    concurrency = args.max_concurrency or (1 if args.provider == "ollama" else None)
    async with stdio_client(server("cv-server")) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        results = await run_eval(model, session, claims, concurrency)

    s = score(results)
    print(f"{args.provider}/{model_name(model)} on {len(claims)} claims")
    print(f"  unsupported claims caught: {s['caught']}/{s['false_claims']}")
    print(f"  supported claims wrongly flagged: {s['false_alarms']}/{s['true_claims']}")
    if s["unchecked"]:
        print(f"  unchecked (reply didn't parse): {s['unchecked']}")
    for r in s["mistakes"]:
        kind = "MISSED " if not r["supported"] else "FALSE ALARM"
        print(f"\n{kind} \"{r['sentence']}\" {r['cv_ids']}")
        if r.get("note"):
            print(f"    label note: {r['note']}")
        if r["problem"]:
            print(f"    checker said: {r['problem']}")


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Score the letter checker against hand-labelled claims.")
    parser.add_argument("--file", type=Path, default=get_settings().claims_file,
                        help="labelled claims (default data/evals/claims.yaml)")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="checks run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")
    args = parser.parse_args()
    if args.no_think and args.provider != "ollama":
        parser.error("--no-think only applies to --provider ollama")
    asyncio.run(amain(args))
