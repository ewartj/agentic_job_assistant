"""job-score: score a posting's runs against its answer key, and print the tables."""
import argparse
import json
from collections import defaultdict
from statistics import mean

from job_assistant.config import get_settings
from job_assistant.evals.scoring import Requirement, RunScore, load_case, score_run, since


def pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value:5.0%}"


def avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def row(label: str, width: int, n: str, values: list[float | None], made_up: int, note: str = "") -> str:
    cells = "  ".join(f"{pct(v):>9}" for v in values)
    return f"{label:<{width}}  {n:>3}  {cells}  {made_up or '':>7}{note}"


def report(scores: list[RunScore], requirements: list[Requirement], per_run: bool) -> None:
    columns = "  ".join(f"{c:>9}" for c in ("retrieved", "cited", "precision", "covered"))
    by_config: dict[str, list[RunScore]] = defaultdict(list)
    for s in scores:
        by_config[s.config].append(s)

    if per_run:
        width = max(len(f"{s.started_at}  {s.config}") for s in scores)
        print("Per run")
        print(f"{'':<{width}}  {'':>3}  {columns}  {'made-up':>7}")
        for s in scores:
            values = [s.retrieval_recall, s.citation_recall, s.citation_precision, s.requirements_covered]
            note = f"  stop={s.stop}" if s.stop != "done" else ""
            print(row(f"{s.started_at}  {s.config}", width, "", values, len(s.made_up), note))
        print()

    width = max(len(c) for c in by_config)
    print("Averages by setup (retrieved/cited = recall of must-cite ids; precision/covered also count acceptable)")
    print(f"{'':<{width}}  {'n':>3}  {columns}  {'made-up':>7}")
    for config, runs in by_config.items():
        values = [avg([getattr(r, f) for r in runs])
                  for f in ("retrieval_recall", "citation_recall", "citation_precision", "requirements_covered")]
        print(row(config, width, str(len(runs)), values, sum(len(r.made_up) for r in runs)))

    print("\nMost-missed must-cite ids, per requirement (how many runs missed each)")
    for req in requirements:
        if not req.valid:
            print(f"  {req.name}: known gap (no ids)")
            continue
        if not req.expected:
            print(f"  {req.name}: acceptable ids only (nothing must be cited)")
            continue
        misses = defaultdict(int)
        for s in scores:
            for cv_id in s.missed.get(req.name, []):
                misses[cv_id] += 1
        detail = ", ".join(f"{i} {n}/{len(scores)}" for i, n in sorted(misses.items())) or "none missed"
        print(f"  {req.name}: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score agent runs against your answer key.")
    parser.add_argument("posting", help="posting name, e.g. bbc_mle")
    parser.add_argument("--runs", action="store_true", help="also show one line per run")
    parser.add_argument("--since", metavar="TIME", help="only runs started at or after this, e.g. 2026-10-16T15:50")
    args = parser.parse_args()

    settings = get_settings()
    case_file = settings.evals_dir / f"{args.posting}.yaml"
    runs_file = settings.traces_dir / f"{args.posting}.jsonl"
    for f in (case_file, runs_file):
        if not f.exists():
            raise SystemExit(f"Not found: {f}")

    known_ids = {e["id"] for e in json.loads(settings.cv_file.read_text(encoding="utf-8"))}
    requirements = load_case(case_file, known_ids)
    runs = [json.loads(line) for line in runs_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    runs = since(runs, args.since)
    if not runs:
        raise SystemExit(f"No runs since {args.since}")
    report([score_run(run, requirements) for run in runs], requirements, args.runs)
