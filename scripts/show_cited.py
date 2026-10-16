"""Show the CV ids each agent run cited, from the traces in data/evals/runs/.

    uv run python scripts/show_cited.py                 # every run, every posting
    uv run python scripts/show_cited.py bbc_mle         # one posting
    uv run python scripts/show_cited.py bbc_mle --last 2 --text   # latest 2 runs, with each entry's text
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "data" / "evals" / "runs"
CV_FILE = ROOT / "data" / "cv.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("posting", nargs="?", help="posting name, e.g. bbc_mle (default: all)")
    parser.add_argument("--last", type=int, help="only the most recent N runs per posting")
    parser.add_argument("--text", action="store_true", help="print the CV text for each cited id")
    args = parser.parse_args()

    files = [RUNS_DIR / f"{args.posting}.jsonl"] if args.posting else sorted(RUNS_DIR.glob("*.jsonl"))
    missing = [f for f in files if not f.exists()]
    if not files or missing:
        raise SystemExit(f"No runs found: {missing or RUNS_DIR}")
    cv = {e["id"]: e["text"] for e in json.loads(CV_FILE.read_text(encoding="utf-8"))} if args.text else {}

    for file in files:
        runs = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
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
