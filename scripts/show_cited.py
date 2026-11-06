"""Show the CV ids each agent run cited, from the traces in data/evals/runs/.

    uv run python scripts/show_cited.py                 # every run, every posting
    uv run python scripts/show_cited.py bbc_mle         # one posting
    uv run python scripts/show_cited.py bbc_mle --last 2 --text   # latest 2 runs, with each entry's text
    uv run python scripts/show_cited.py bbc_mle --last 3 --outside-key   # ids cited that the answer key lacks
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "data" / "evals" / "runs"
CV_FILE = ROOT / "data" / "cv.json"
EVALS_DIR = ROOT / "data" / "evals"
CV_ID = re.compile(r"\bcv-\d{3}\b")


def outside_key(posting: str, runs: list[dict], cv: dict[str, str]) -> None:
    """Every id cited that the answer key lists nowhere, for reviewing the key.

    For pipeline runs, also shows which extracted requirement it was cited for
    (each match turn's text is "<requirement>: <verdict> [<ids>]").
    """
    key = yaml.safe_load((EVALS_DIR / f"{posting}.yaml").read_text(encoding="utf-8")) or {}
    in_key = {i for r in key.get("requirements", []) for i in (r.get("expected") or []) + (r.get("acceptable") or [])}
    runs_citing: dict[str, int] = defaultdict(int)
    cited_for: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        for cv_id in set(run["cited_ids"]) - in_key:
            runs_citing[cv_id] += 1
        if run["provider"].startswith("pipeline"):
            for turn in run["turns"][1:]:
                name, _, rest = turn["text"].partition(": ")
                for cv_id in set(CV_ID.findall(rest)) - in_key:
                    cited_for[cv_id].add(name)
    print(f"=== {posting}: cited but not in the answer key, over {len(runs)} runs")
    for cv_id, n in sorted(runs_citing.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"\n{cv_id}  cited in {n}/{len(runs)} runs")
        print(f"    {cv.get(cv_id, '(not in cv.json)')}")
        if cited_for[cv_id]:
            print(f"    cited for: {'; '.join(sorted(cited_for[cv_id]))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("posting", nargs="?", help="posting name, e.g. bbc_mle (default: all)")
    parser.add_argument("--last", type=int, help="only the most recent N runs per posting")
    parser.add_argument("--text", action="store_true", help="print the CV text for each cited id")
    parser.add_argument("--outside-key", action="store_true",
                        help="list ids cited that the posting's answer key doesn't include, with their text")
    args = parser.parse_args()

    files = [RUNS_DIR / f"{args.posting}.jsonl"] if args.posting else sorted(RUNS_DIR.glob("*.jsonl"))
    missing = [f for f in files if not f.exists()]
    if not files or missing:
        raise SystemExit(f"No runs found: {missing or RUNS_DIR}")
    if args.outside_key and not args.posting:
        raise SystemExit("--outside-key needs a posting name")
    show_text = args.text or args.outside_key
    cv = {e["id"]: e["text"] for e in json.loads(CV_FILE.read_text(encoding="utf-8"))} if show_text else {}

    for file in files:
        runs = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.outside_key:
            outside_key(file.stem, runs[-args.last:] if args.last else runs, cv)
            continue
        print(f"=== {file.stem} ({len(runs)} runs)")
        for run in runs[-args.last:] if args.last else runs:
            cited = run["cited_ids"]
            made_up = sorted(set(cited) - set(run["retrieved_ids"])) if run["mode"] == "tools" else []
            print(f"\n{run['started_at']}  {run['provider']}/{run['model']}  [{run['mode']}]  stop={run['stop']}")
            print(f"  cited ({len(cited)}): {', '.join(cited) or '-'}")
            if made_up:
                print(f"  WARNING cited but never retrieved: {', '.join(made_up)}")
            for cv_id in cited if args.text else []:
                print(f"    {cv_id}  {cv.get(cv_id, '(not in cv.json)')[:110]}")
        print()


if __name__ == "__main__":
    main()
