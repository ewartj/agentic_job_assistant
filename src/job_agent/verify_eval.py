"""How well does the letter's checker tell supported claims from unsupported ones?

Recall and precision can't see a false claim that cites a valid id ("I used Vault at
the BBC [cv-007]" when cv-007 is from another employer). This scores the checker
itself against hand-labelled claims in data/evals/claims.yaml:

    claims:
      - sentence: I secured database credentials with HashiCorp Vault at the BBC.
        cv_ids: [cv-007]
        supported: false
        note: cv-007 is from a different employer   # optional, why

    uv run job-verify-eval
    uv run job-verify-eval --provider ollama --no-think

It reports how many unsupported claims the checker catches (it should catch them
all) and how many supported ones it wrongly flags (each one costs a needless redraft).
"""
import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from job_agent.agent import OLLAMA_NUM_CTX, ROOT, utf8_output
from job_agent.graph_agent import make_model, model_name, server
from job_agent.letter import Check, verify
from job_agent.settings import get_settings


def load_claims(path: Path) -> list[dict[str, Any]]:
    claims = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("claims", [])
    for n, c in enumerate(claims, 1):
        missing = {"sentence", "cv_ids", "supported"} - set(c)
        if missing:
            raise SystemExit(f"{path.name}: claim {n} is missing {', '.join(sorted(missing))}")
    return claims


def score(results: list[dict[str, Any]]) -> dict[str, Any]:
    """results: each claim plus `verdict` (the checker's True / False / None)."""
    false = [r for r in results if not r["supported"]]
    true = [r for r in results if r["supported"]]
    return {
        "caught": sum(r["verdict"] is False for r in false), "false_claims": len(false),
        "false_alarms": sum(r["verdict"] is False for r in true), "true_claims": len(true),
        "unchecked": sum(r["verdict"] is None for r in results),
        "mistakes": [r for r in results if r["verdict"] is not None and r["verdict"] != r["supported"]],
    }


async def run_eval(model: Any, session: ClientSession, claims: list[dict[str, Any]],
                   max_concurrency: int | None = None) -> list[dict[str, Any]]:
    checker = model.with_structured_output(Check, method="json_schema", include_raw=True)
    limit = asyncio.Semaphore(max_concurrency or len(claims) or 1)

    async def one(claim: dict[str, Any]) -> dict[str, Any]:
        texts = {}
        for cv_id in claim["cv_ids"]:
            resource = await session.read_resource(f"cv://entry/{cv_id}")
            texts[cv_id] = json.loads(resource.contents[0].text)["text"]
        async with limit:
            verdict, problem, _ = await verify(checker, claim["sentence"], texts)
        return {**claim, "verdict": verdict, "problem": problem}

    return await asyncio.gather(*(one(c) for c in claims))


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
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="checks run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")
    args = parser.parse_args()
    if args.no_think and args.provider != "ollama":
        parser.error("--no-think only applies to --provider ollama")
    asyncio.run(amain(args))
