"""Build data/letters.json from the cover letters in the Jobs folder.

Re-run after adding a letter: add a row to LETTERS, then
    python scripts/build_letters.py
Stdlib only - reads .docx / .odt by unzipping the XML.
"""
import html
import json
import re
import zipfile
from pathlib import Path

JOBS_DIR = Path(r"C:\Users\cacaw\OneDrive\Documents\mine\Jobs")  # sources are relative to this
OUT = Path(__file__).resolve().parent.parent / "data" / "letters.json"

# One row per *final* letter. Duplicates/earlier drafts are deliberately left out.
# category: cover_letter | motivation (short "why us" answer) | application_answers
LETTERS = [
    dict(file="2026/ai/cover_letter_google_expanded.docx", company="Google",
         role="Software Engineer II, Health and Home Infrastructure", date="2026-04-11",
         category="cover_letter", status="final"),
    dict(file="2026/ai/cover_letter_nhscfa.docx", company="NHS Counter Fraud Authority",
         role="Principal Data Scientist", date="2026-04-11",
         category="cover_letter", status="draft", reflow=True,
         notes="Contains unfinished fragments ('cosupervising???', '# leading and mentoring')."),
    dict(file="2026/ai security institute/ai_security_cover_letter.docx", company="AI Security Institute",
         role="Software Engineer - Core Technology", date=None,
         category="cover_letter", status="final"),
    dict(file="2026/ai security institute/why here.docx", company="AI Security Institute",
         role="Software Engineer - Core Technology", date=None,
         category="motivation", status="final"),
    dict(file="2026/aiAccelerator/Jonathan_Sheldon_Cover_Letter_iAI.docx", company="Incubator for AI (i.AI)",
         role="Applied AI Engineer", date=None,
         category="cover_letter", status="final"),
    dict(file="2026/bbc/Document Copy.docx", company="BBC",
         role="Senior Software Engineer, Machine Learning Enablement", date="2026-03-06",
         category="cover_letter", status="final",
         notes="Opening line says 'Senior Data Engineer' - copy-paste slip."),
    dict(file="2026/bbc/Document1.docx", company="BBC",
         role="Senior Software Engineer, Machine Learning Enablement", date="2026-03-07",
         category="application_answers", status="draft",
         notes="Application-form answers (DevOps/MLOps, AWS, end-to-end MLOps). Includes rough notes."),
    dict(file="2026/GEL/Cover Letter.docx", company="Genomics England",
         role="Software Engineer Python (Research Acquisition and Processing)", date=None,
         category="cover_letter", status="final"),
    dict(file="2026/GSTT/cover_letter.docx", company="Guy's and St Thomas' NHS Foundation Trust",
         role="Senior Data Engineer, AI Centre for Value-Based Healthcare", date="2026-02-20",
         category="cover_letter", status="final",
         notes="Mentions SAFEHR, which is UCLH's programme - possible copy-paste slip."),
    dict(file="2026/Kraken/Why Kraken.docx", company="Kraken",
         role=None, date=None,
         category="motivation", status="final"),
    dict(file="2026/OurFuturehealth/Document1.docx", company="Our Future Health",
         role="Senior Data Scientist", date="2026-01-21",
         category="cover_letter", status="draft",
         notes="Final paragraph ends mid-sentence."),
    dict(file="2026/OurFutureHealth2/Jonathan_Sheldon_Cover_Letter_OurFutureHealth_1.odt", company="Our Future Health",
         role="Data Engineer (Bioinformatics), Clinical Research Recruitment Service", date=None,
         category="cover_letter", status="final"),
    dict(file="2026/Ucl/Cover Letter.docx", company="University College London Hospitals (SAFEHR)",
         role="Senior Software Engineer (Data & AI Enablement)", date=None,
         category="cover_letter", status="final"),
]

SALUTATION = re.compile(r"^(dear|to whom)", re.IGNORECASE)
# Words that stay capitalised when re-joining hard-wrapped lines.
KEEP_CASE = {"Thermiator", "Intelligent", "Healthcare", "Interns", "S3", "Gitlab", "Python", "Podman", "Airflow"}


def read_text(path: Path) -> str:
    member = "word/document.xml" if path.suffix == ".docx" else "content.xml"
    xml = zipfile.ZipFile(path).read(member).decode("utf-8")
    xml = re.sub(r"</w:p>|</text:p>|</text:h>|<w:br/>|<text:line-break/>", "\n", xml)
    xml = re.sub(r"<w:tab/>|<text:tab/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).replace("\ufeff", "")


def reflow(text: str) -> str:
    """Join lines that were hard-wrapped mid-sentence (with a capitalised next word)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out: list[str] = []
    for line in lines:
        if (out and not re.search(r"[.!?:;)]$", out[-1]) and not SALUTATION.match(out[-1])
                and not line.startswith("#")):
            first, _, rest = line.partition(" ")
            if first not in KEEP_CASE and first[:1].isupper() and first[1:].islower():
                first = first.lower()
            out[-1] = f"{out[-1]} {first} {rest}".rstrip()
        else:
            out.append(line)
    return "\n\n".join(out)


def clean(text: str, do_reflow: bool) -> str:
    lines = text.splitlines()
    # Drop name/address/phone header above the salutation.
    for i, line in enumerate(lines):
        if SALUTATION.match(line.strip()):
            lines = lines[i:]
            break
    text = "\n".join(lines)
    if do_reflow:
        return reflow(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def main() -> None:
    entries = []
    for i, row in enumerate(LETTERS, start=1):
        path = JOBS_DIR / row["file"]
        entries.append({
            "id": f"letter-{i:03d}",
            "category": row["category"],
            "company": row["company"],
            "role": row["role"],
            "date": row["date"],
            "status": row["status"],
            "text": clean(read_text(path), row.get("reflow", False)),
            "source": row["file"],
            **({"notes": row["notes"]} if row.get("notes") else {}),
        })
    OUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(entries)} letters to {OUT}")


if __name__ == "__main__":
    main()
