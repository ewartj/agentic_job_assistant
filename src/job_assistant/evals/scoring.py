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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
