"""job-cv: tailor your CV to a posting, in your Word template, checking every bullet."""
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
from job_assistant.drafting.cv import Employers, cv_drafting, read_template
from job_assistant.drafting.engine import is_verified, round_report, run_draft
from job_assistant.events import print_progress
from job_assistant.llm import make_model, model_name
from job_assistant.mcp import server
from job_postings import split_front_matter


async def amain(args: argparse.Namespace) -> None:
    meta, posting = split_front_matter(args.posting.read_text(encoding="utf-8"))
    employers = Employers.load(args.employers)
    template = read_template(args.template, employers)
    think = False if args.no_think else None
    model = make_model(args.provider, args.model, args.num_ctx, think=think if args.provider == "ollama" else None)
    checker = args.checker or args.provider
    checker_model = (make_model(checker, args.checker_model, args.num_ctx, think=think if checker == "ollama" else None)
                     if (checker, args.checker_model) != (args.provider, None) else None)
    concurrency = args.max_concurrency or (1 if "ollama" in (args.provider, checker) else None)

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server("cv-server")))
        cv = await stack.enter_async_context(ClientSession(read, write))
        await cv.initialize()
        state = await run_draft(model, cv, posting, cv_drafting(employers, template),
                                company=meta.get("company") or "", role=meta.get("role") or "",
                                limit=args.limit, max_concurrency=concurrency, checker_model=checker_model,
                                examples=[template.current()], progress=print_progress)

    verified = is_verified(state["rounds"])
    stem = args.posting.stem
    settings = get_settings()
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    from job_assistant.drafting.docx import save_cv  # leaves with the CLI, in cli/cv.py

    out, plan_path, warnings = save_cv(state, args.template, employers, settings.drafts_dir, stem, meta.get("role"))
    plan_md = plan_path.read_text(encoding="utf-8")

    settings.cv_traces_dir.mkdir(parents=True, exist_ok=True)
    record = {"started_at": datetime.now().isoformat(timespec="seconds"),
              "provider": f"cv-{args.provider}" + ("-nothink" if args.no_think else ""), "model": model_name(model),
              "checker": f"{checker}/{model_name(checker_model or model)}", "posting": stem,
              "drafts": state.get("attempt", 0), "verified": verified, "rounds": state["rounds"],
              "plan": state.get("document", ""), "warnings": warnings}
    with (settings.cv_traces_dir / f"{stem}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    print(plan_md)
    print("---")
    print(f"{record['provider']}/{record['model']}  checker={record['checker']}  drafts={record['drafts']}  "
          + ("VERIFIED" if verified else "NOT VERIFIED"))
    print("\n".join(round_report(state["rounds"])))
    for w in warnings:
        print(f"WARNING: {w}")
    if not verified:
        print("WARNING: the CV still has unsupported points (listed above and in the plan file).")
    print(f"CV saved to {out}\nplan (with citations) saved to {plan_path}")


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Tailor your CV to a job posting, checking every bullet.")
    parser.add_argument("posting", type=Path, help="text/markdown file containing the job posting")
    parser.add_argument("--template", type=Path, default=get_settings().template,
                        help="your CV as a .docx (default data/cv_template.docx)")
    parser.add_argument("--employers", type=Path, default=get_settings().employers,
                        help="entry -> job mapping (default data/cv_employers.yaml)")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--checker", choices=["claude", "ollama"],
                        help="model provider for checking bullets (default: the same as --provider)")
    parser.add_argument("--checker-model", help="override the checker provider's default model")
    parser.add_argument("--limit", type=int, default=10, help="results per search (default 10)")
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="branches run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")
    args = parser.parse_args()
    if args.no_think and "ollama" not in (args.provider, args.checker):
        parser.error("--no-think only applies when --provider or --checker is ollama")
    asyncio.run(amain(args))
