"""Writing a tailored CV into the Word template, keeping its formatting.

Only the profile, the skills lines and each job's bullets are replaced; everything else
in the template - name, contact line, job headers and dates, education - is left as it is.
The template's own paragraphs are cloned for extra lines, so fonts and bullet styles carry over.
"""
from copy import deepcopy
from pathlib import Path
from typing import Any

from docx.text.paragraph import Paragraph

from job_assistant.drafting.cv import PROFILE, SKILLS, Employers, read_template
from job_assistant.drafting.engine import plan_document


def set_text(paragraph: Paragraph, *parts: str) -> None:
    """Replace the text but keep the formatting: the first runs keep their own styles
    (e.g. a bold "Category:" run, then a plain one), and any further runs are removed."""
    runs = paragraph.runs
    for run, text in zip(runs, parts, strict=False):
        run.text = text
    for run in runs[len(parts):]:
        run._r.getparent().remove(run._r)
    if len(runs) < len(parts):  # fewer runs than parts: the rest goes in the last run
        runs[-1].text += "".join(parts[len(runs):])


def fill(paragraphs: list[Paragraph], texts: list[tuple[str, ...]]) -> None:
    """Put one text in each of `paragraphs`, cloning the last one for extra texts and
    removing any left over, so the count can change while the style doesn't."""
    last = paragraphs[-1]
    for n, parts in enumerate(texts):
        if n < len(paragraphs):
            target = paragraphs[n]
        else:
            new = deepcopy(last._p)
            last._p.addnext(new)
            target = last = Paragraph(new, last._parent)
        set_text(target, *parts)
    for extra in paragraphs[len(texts):]:
        extra._p.getparent().remove(extra._p)


def skill_parts(line: str) -> tuple[str, ...]:
    """'Category: a, b' as the template's two runs: bold 'Category: ', then 'a, b'."""
    category, sep, items = line.partition(":")
    return (f"{category.strip()}: ", items.strip()) if sep else ("", line.strip())


def write_cv(template_path: Path, employers: Employers, plan: list[dict[str, Any]], out: Path) -> list[str]:
    """Fill a copy of the template from the plan; returns warnings for anything skipped."""
    template = read_template(template_path, employers)
    sections: dict[str, list[str]] = {}
    for paragraph in plan:
        sections.setdefault(employers.section(paragraph["title"]), []).extend(pt["text"] for pt in paragraph["points"])
    warnings = [f"section '{s}' isn't in the template, so it was left out" for s in sections
                if s not in (PROFILE, SKILLS, *template.jobs)]

    if sections.get(PROFILE):
        set_text(template.profile, " ".join(sections[PROFILE]))
    else:
        warnings.append("no Profile written - the template's is kept")
    if sections.get(SKILLS) and template.skills:
        fill(template.skills, [skill_parts(line) for line in sections[SKILLS]])
    else:
        warnings.append("no Key Skills written - the template's are kept")
    for key, (_, bullets) in template.jobs.items():
        if sections.get(key) and bullets:
            fill(bullets, [(text,) for text in sections[key]])
        else:
            warnings.append(f"no bullets for {key} - the template's are kept")
    out.parent.mkdir(parents=True, exist_ok=True)
    template.doc.save(out)
    return warnings


def save_cv(state: dict[str, Any], template_path: Path, employers: Employers, drafts_dir: Path, stem: str,
            role: str | None) -> tuple[Path, Path, list[str]]:
    """Write the .docx and its plan (with citations); returns both paths and any warnings."""
    docx_path, plan_path = drafts_dir / f"{stem}.cv.docx", drafts_dir / f"{stem}.cv_plan.md"
    warnings = write_cv(template_path, employers, state.get("plan", []), docx_path)
    plan_path.write_text(plan_document(state, f"CV plan: {role or stem}", what="Sections"), encoding="utf-8")
    return docx_path, plan_path, warnings
