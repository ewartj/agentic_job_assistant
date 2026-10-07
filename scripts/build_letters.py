"""Build data/letters.json from the cover letters listed in data/letters.yaml.

Add a letter: add an entry to data/letters.yaml, then
    uv run python scripts/build_letters.py [--config path/to/letters.yaml]
See letters.example.yaml for the format. Reads .docx / .odt by unzipping the XML.
"""
import argparse
import html
import json
import re
import sys
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "data" / "letters.yaml"
OUT = ROOT / "data" / "letters.json"

REQUIRED = ("file", "company", "category", "status")
OPTIONAL = ("role", "date", "outcome", "notes", "reflow", "keep_case")
CATEGORIES = {"cover_letter", "motivation", "application_answers"}
STATUSES = {"final", "draft"}

SALUTATION = re.compile(r"^(dear|to whom)", re.IGNORECASE)


def read_text(path: Path) -> str:
    archive = zipfile.ZipFile(path)
    if path.suffix == ".odt":
        member = "content.xml"
    else:
        # Usually word/document.xml, but some Word versions write word/document22.xml.
        member = next(n for n in archive.namelist() if re.fullmatch(r"word/document\d*\.xml", n))
    xml = archive.read(member).decode("utf-8")
    xml = re.sub(r"</w:p>|</text:p>|</text:h>|<w:br/>|<text:line-break/>", "\n", xml)
    xml = re.sub(r"<w:tab/>|<text:tab/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).replace("\ufeff", "")


def reflow(text: str, keep_case: set[str]) -> str:
    """Join lines that were hard-wrapped mid-sentence (with a capitalised next word).

    The wrapped-on word is lowercased unless it's in `keep_case` (proper nouns).
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out: list[str] = []
    for line in lines:
        if (out and not re.search(r"[.!?:;)]$", out[-1]) and not SALUTATION.match(out[-1])
                and not line.startswith("#")):
            first, _, rest = line.partition(" ")
            if first not in keep_case and first[:1].isupper() and first[1:].islower():
                first = first.lower()
            out[-1] = f"{out[-1]} {first} {rest}".rstrip()
        else:
            out.append(line)
    return "\n\n".join(out)


def clean(text: str, do_reflow: bool, keep_case: set[str]) -> str:
    lines = text.splitlines()
    # Drop name/address/phone header above the salutation.
    for i, line in enumerate(lines):
        if SALUTATION.match(line.strip()):
            lines = lines[i:]
            break
    text = "\n".join(lines)
    if do_reflow:
        return reflow(text, keep_case)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def load_config(path: Path) -> tuple[Path, list[dict]]:
    """Read and validate the manifest, reporting every problem at once."""
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    jobs_dir = Path(config.get("jobs_dir", ""))
    letters = config.get("letters") or []

    errors = []
    if not jobs_dir.is_dir():
        errors.append(f"jobs_dir does not exist: {jobs_dir}")
    for n, row in enumerate(letters, start=1):
        where = f"letter {n} ({row.get('file', '?')})"
        missing = [key for key in REQUIRED if not row.get(key)]
        unknown = set(row) - set(REQUIRED) - set(OPTIONAL)
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
        if unknown:
            errors.append(f"{where}: unknown keys {', '.join(sorted(unknown))}")
        if row.get("category") and row["category"] not in CATEGORIES:
            errors.append(f"{where}: category must be one of {sorted(CATEGORIES)}")
        if row.get("status") and row["status"] not in STATUSES:
            errors.append(f"{where}: status must be one of {sorted(STATUSES)}")
        if "keep_case" in row:
            keep_case = row["keep_case"]
            if not (isinstance(keep_case, list) and all(isinstance(w, str) for w in keep_case)):
                errors.append(f"{where}: keep_case must be a list of words")
            if not row.get("reflow"):
                errors.append(f"{where}: keep_case only applies with reflow: true")
        if row.get("file") and jobs_dir.is_dir() and not (jobs_dir / row["file"]).is_file():
            errors.append(f"{where}: file not found under jobs_dir")
    if errors:
        sys.exit("Invalid config " + str(path) + ":\n  " + "\n  ".join(errors))
    return jobs_dir, letters


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    jobs_dir, letters = load_config(args.config)
    entries = []
    for i, row in enumerate(letters, start=1):
        entries.append({
            "id": f"letter-{i:03d}",
            "category": row["category"],
            "company": row["company"],
            "role": row.get("role"),
            # Guard against unquoted YAML dates/years becoming date objects or ints.
            "date": str(row["date"]) if row.get("date") is not None else None,
            "status": row["status"],
            "text": clean(read_text(jobs_dir / row["file"]), row.get("reflow", False),
                          set(row.get("keep_case", []))),
            "source": row["file"],
            **({"outcome": row["outcome"]} if row.get("outcome") else {}),
            **({"notes": row["notes"]} if row.get("notes") else {}),
        })
    OUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(entries)} letters to {OUT}")


if __name__ == "__main__":
    main()
