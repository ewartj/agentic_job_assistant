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
import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession

from job_assistant.drafting.checker import Check, verify


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
