"""Import past applications into the tracker from the letters manifest.

    uv run python scripts/import_applications.py --dry-run   # show what would be logged
    uv run python scripts/import_applications.py

Two sources, both from data/letters.yaml (gitignored):
  - letters (via data/letters.json): one application per company + role. Status
    comes from the letters' `outcome`, otherwise "applied" if any letter is final
    and "interested" if they're all drafts.
  - `applications:`: past applications with no letter in letters.json, e.g.
        - {company: Acme, role: Data Scientist, date: '2022', outcome: [interview, rejected]}

`outcome` is one stage or a list of stages in order: interview, offer, rejected,
withdrawn. Safe to re-run: existing applications are left as they are and only
stages they haven't reached yet are added, so nothing is duplicated.
"""
import argparse
import json
import os
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import yaml

from tracker_server import store
from tracker_server.server import DEFAULT_DB

ROOT = Path(__file__).resolve().parent.parent
LETTERS = ROOT / "data" / "letters.json"
MANIFEST = ROOT / "data" / "letters.yaml"
OUTCOMES = {"interview": "interviewing", "offer": "offer", "rejected": "rejected", "withdrawn": "withdrawn"}
APPLICATION_KEYS = {"company", "role", "date", "outcome", "notes"}
NO_ROLE = "(role not recorded)"


def stages(outcome: str | list[str] | None, where: str) -> list[str]:
    """Tracker statuses for an outcome: "rejected" -> ["rejected"], [interview, rejected] -> both."""
    names = [] if outcome is None else [outcome] if isinstance(outcome, str) else list(outcome)
    if unknown := [n for n in names if n not in OUTCOMES]:
        raise SystemExit(f"{where}: unknown outcome {unknown} - use {', '.join(OUTCOMES)}")
    return [OUTCOMES[n] for n in names]


def from_letters(letters: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for letter in letters:
        groups[letter["company"], letter.get("role") or NO_ROLE].append(letter)

    applications = []
    for (company, role), group in groups.items():
        outcomes = {json.dumps(letter["outcome"]) for letter in group if letter.get("outcome")}
        if len(outcomes) > 1:
            raise SystemExit(f"{company} / {role}: letters disagree on the outcome: {outcomes}")
        steps = stages(json.loads(outcomes.pop()) if outcomes else None, f"{company} / {role}")
        sent = bool(steps) or any(letter["status"] == "final" for letter in group)
        # Undated letters fall back to their folder's year (sources look like "2026/acme/letter.docx"),
        # since log_application would otherwise record today as the applied date.
        dates = sorted(str(letter["date"]) if letter.get("date") else letter["source"][:4]
                       for letter in group if letter.get("date") or letter["source"][:4].isdigit())
        applications.append({
            "company": company,
            "role": role,
            "status": "applied" if sent else "interested",
            "stages": steps,
            "applied_on": dates[0] if dates and sent else None,
            "notes": "Imported from letters: " + ", ".join(letter["id"] for letter in group),
        })
    return applications


def from_manifest(entries: list[dict]) -> list[dict]:
    applications = []
    for i, entry in enumerate(entries, 1):
        where = f"applications #{i} ({entry.get('company')})"
        if unknown := set(entry) - APPLICATION_KEYS:
            raise SystemExit(f"{where}: unknown keys {sorted(unknown)} - quote values that contain commas")
        if not entry.get("company"):
            raise SystemExit(f"{where}: company is required")
        applications.append({
            "company": entry["company"],
            "role": entry.get("role") or NO_ROLE,
            "status": "applied",
            "stages": stages(entry.get("outcome"), where),
            "applied_on": str(entry["date"]) if entry.get("date") else None,
            "notes": entry.get("notes") or "Imported from the applications list (no letter on file)",
        })
    return applications


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing anything")
    args = parser.parse_args()

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    applications = (from_letters(json.loads(LETTERS.read_text(encoding="utf-8")))
                    + from_manifest(manifest.get("applications") or []))
    db = Path(os.environ.get("TRACKER_DB", DEFAULT_DB))
    print(f"{'Would import' if args.dry_run else 'Importing'} {len(applications)} applications into {db}\n")
    if args.dry_run:
        for a in applications:
            path = " -> ".join([a["status"], *a["stages"]])
            print(f"  {path:<36} {a['applied_on'] or '':<10}  {a['company']} - {a['role']}")
        return

    with closing(store.connect(db)) as conn:
        for a in applications:
            app, created = store.log_application(conn, a["company"], a["role"], status=a["status"],
                                                 notes=a["notes"], applied_on=a["applied_on"])
            reached = {h["status"] for h in app["history"]}
            for status in a["stages"]:  # one step each, so the history reads applied -> interviewing -> ...
                if status not in reached:
                    app = store.update_status(conn, app["id"], status, note="imported outcome")
            print(f"  {'added ' if created else 'exists'}  {app['status']:<12} {app['company']} - {app['role']}")


if __name__ == "__main__":
    main()
