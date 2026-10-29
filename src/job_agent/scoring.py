"""Score agent runs against your answer key for a posting (Phase 2, step 5).

    uv run job-score bbc_mle            # every run of data/evals/runs/bbc_mle.jsonl
    uv run job-score bbc_mle --runs     # also one line per run
    uv run job-score bbc_mle --since 2026-10-16T15:50   # only runs started at or after this

The answer key is data/evals/<posting>.yaml:

    posting: postings/bbc_mle.md
    requirements:
      - requirement: AWS (SageMaker, S3, ...)
        expected: [cv-018, cv-031]      # must cite: the strongest evidence
        acceptable: [cv-035, cv-040]    # optional: fine to cite, not required
      - requirement: Kubernetes
        expected: []                    # a genuine gap - nothing should be cited

Recall (retrieved / cited) is measured against `expected` only, so it can reach
100% without citing everything plausible. Precision and coverage count
`expected` and `acceptable` together, so citing acceptable evidence isn't penalised.
"""
import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
EVALS_DIR = ROOT / "data" / "evals"
CV_FILE = ROOT / "data" / "cv.json"


@dataclass
class Requirement:
    name: str
    expected: set[str]  # must cite
    acceptable: set[str] = field(default_factory=set)  # fine to cite, not required

    @property
    def valid(self) -> set[str]:
        """Every id that counts as correct evidence for this requirement."""
        return self.expected | self.acceptable


@dataclass
class RunScore:
    started_at: str
    config: str  # provider/model [mode]
    stop: str
    retrieval_recall: float | None  # None in full-context mode (nothing is retrieved)
    citation_recall: float
    citation_precision: float | None  # None when nothing was cited
    requirements_covered: float  # share of requirements with at least one valid id cited
    made_up: list[str]  # cited but never retrieved (tools mode only)
    missed: dict[str, list[str]]  # requirement -> expected (must-cite) ids not cited


def load_case(path: Path, known_ids: set[str]) -> list[Requirement]:
    """Read the answer key; mistakes are errors, not silent misses."""
    case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    requirements = [
        Requirement(r["requirement"], set(r.get("expected") or []), set(r.get("acceptable") or []))
        for r in case.get("requirements", [])
    ]
    if not requirements:
        raise SystemExit(f"{path.name}: no requirements listed")
    errors = []
    unknown = {i for r in requirements for i in r.valid} - known_ids
    if unknown:
        errors.append(f"ids not in cv.json: {', '.join(sorted(unknown))}")
    for r in requirements:
        if both := r.expected & r.acceptable:
            errors.append(f"{r.name!r}: in both expected and acceptable: {', '.join(sorted(both))}")
    if errors:
        raise SystemExit(f"{path.name}:\n  " + "\n  ".join(errors))
    return requirements


def ratio(part: set[str], whole: set[str]) -> float | None:
    return len(part & whole) / len(whole) if whole else None


def score_run(run: dict[str, Any], requirements: list[Requirement]) -> RunScore:
    expected = {i for r in requirements for i in r.expected}  # recall: must-cite ids only
    valid = {i for r in requirements for i in r.valid}  # precision: must-cite + acceptable
    cited, retrieved = set(run["cited_ids"]), set(run["retrieved_ids"])
    tools_mode = run["mode"] == "tools"
    # Requirements with no ids at all are known gaps: they can't be "covered".
    scorable = [r for r in requirements if r.valid]
    return RunScore(
        started_at=run["started_at"],
        config=f"{run['provider']}/{run['model']} [{run['mode']}]",
        # Traces from before empty_answer existed recorded an empty reply as "done".
        stop="empty_answer" if run["stop"] == "done" and not run.get("final_answer", "x").strip() else run["stop"],
        retrieval_recall=ratio(retrieved, expected) if tools_mode else None,
        citation_recall=ratio(cited, expected) or 0.0,
        citation_precision=ratio(valid, cited),
        requirements_covered=sum(bool(r.valid & cited) for r in scorable) / len(scorable) if scorable else 0.0,
        made_up=sorted(cited - retrieved) if tools_mode else [],
        missed={r.name: sorted(r.expected - cited) for r in scorable if r.expected - cited},
    )


def since(runs: list[dict[str, Any]], start: str | None) -> list[dict[str, Any]]:
    """Runs started at or after `start`. ISO timestamps sort as strings, so a date
    ("2026-10-16") or any prefix of a timestamp works."""
    return [r for r in runs if start is None or r["started_at"] >= start]


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

    case_file = EVALS_DIR / f"{args.posting}.yaml"
    runs_file = EVALS_DIR / "runs" / f"{args.posting}.jsonl"
    for f in (case_file, runs_file):
        if not f.exists():
            raise SystemExit(f"Not found: {f}")

    known_ids = {e["id"] for e in json.loads(CV_FILE.read_text(encoding="utf-8"))}
    requirements = load_case(case_file, known_ids)
    runs = [json.loads(line) for line in runs_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    runs = since(runs, args.since)
    if not runs:
        raise SystemExit(f"No runs since {args.since}")
    report([score_run(run, requirements) for run in runs], requirements, args.runs)
