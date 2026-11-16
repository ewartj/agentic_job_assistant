"""A CV tailored to a posting, written into a Word template (Phase 3, step 5b-2).

The same graph as letter.py - extract, match, draft, check each point, fix by patching -
with three differences, set through letter.Drafting:

    the prompt    sections instead of paragraphs: Profile, Key Skills, then one section
                  per job in the template, holding that job's bullets
    the evidence  every CV entry, grouped by the job it belongs to, not just the
                  matched ones: each job needs bullets whatever the posting asks
    allowed ids   a job's bullets may cite only that job's entries, enforced in the check
                  node without a model call, so work can't move to the wrong employer

Facts are never generated: the name, contact line, job headers and dates, education,
publications and projects are left exactly as they are in the template. Only the
profile, the skills lines and each job's bullets are replaced, cloning the template's
own paragraphs so the fonts and bullet styles carry over.

Both inputs are personal and live in data/ (gitignored):
    data/cv_template.docx   your CV, used as the template
    data/cv_employers.yaml  which job each CV entry belongs to, and how each job's
                            header reads in the template

    uv run job-cv data/evals/postings/bbc_mle.md
    uv run job-cv data/evals/postings/bbc_mle.md --provider ollama --no-think --checker claude
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docx
import yaml
from docx.text.paragraph import Paragraph

from job_assistant.assessment.pipeline import LABELS
from job_assistant.drafting.engine import Drafting, DraftState, EntryText

PROFILE, SKILLS = "Profile", "Key Skills"

CV_PROMPT = """\
You tailor the user's CV to a job posting. Its layout, job titles, dates, education \
and publications are fixed: you write only these sections, using exactly these titles:

- Profile: 3-4 points, one sentence each, which together make the profile paragraph. \
Lead with what the posting needs most. Written without "I".
- Key Skills: 4-6 points, each a skills line written as "Category: item, item, item", \
e.g. "Languages & Data Science: Python (8 years), R, SQL". Put the categories the \
posting cares about first.
- One section per job, titled with the job's key: {jobs}. Each point is one bullet: \
what was done and the outcome, starting with a past-tense verb, at most about 30 \
words, without "I". Keep each job to about as many bullets as the current CV has \
(given in brackets), so the CV stays the same length.

Rules:
- Every point needs its cv_ids. A job's bullets may cite only the entries listed under \
that job below. The Profile and Key Skills may cite any entry.
- Say only what the cited entries say. Don't add employers, tools, numbers or outcomes \
that aren't in them, and don't merge different projects into one bullet.
- Choose and order each job's bullets by what the posting asks for; every job keeps \
at least one bullet.
- purpose: one short sentence on what the section emphasises for this posting.
- UK English, in the style of the current CV."""


# --- Inputs -----------------------------------------------------------------------

@dataclass
class Employers:
    """data/cv_employers.yaml: entry id -> job key, and job key -> its header text."""
    of_entry: dict[str, str]
    headers: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> "Employers":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(of_entry=data.get("employers", {}), headers=data.get("jobs", {}))

    def entries(self, job: str) -> set[str]:
        return {i for i, e in self.of_entry.items() if e == job}

    def section(self, title: str) -> str:
        """A section title as written by the model, as a job key, PROFILE, SKILLS or itself.
        Accepts the key ("acme") or the header text ("Acme Health ...")."""
        t = title.strip().lower()
        if t == PROFILE.lower():
            return PROFILE
        if t == SKILLS.lower():
            return SKILLS
        for key, header in self.headers.items():
            if t == key.lower() or header.lower() in t or t in header.lower():
                return key
        return title


@dataclass
class Template:
    """Where the replaceable parts of the template are."""
    doc: Any
    profile: Paragraph
    skills: list[Paragraph]
    jobs: dict[str, tuple[str, list[Paragraph]]] = field(default_factory=dict)  # key -> (header text, bullets)

    def current(self) -> str:
        """The template's current text for these parts, shown to the model for style."""
        lines = [f"{PROFILE}: {self.profile.text}", f"{SKILLS}:", *[f"  {p.text}" for p in self.skills]]
        for key, (header, bullets) in self.jobs.items():
            lines += [f"{key} ({header}):", *[f"  - {p.text}" for p in bullets]]
        return "\n".join(lines)


def read_template(path: Path, employers: Employers) -> Template:
    doc = docx.Document(path)
    paragraphs = doc.paragraphs

    def after(heading: str) -> int:
        for i, p in enumerate(paragraphs):
            if p.text.strip() == heading:
                return i + 1
        raise SystemExit(f"{path.name}: no '{heading}' heading - is this the right template?")

    i = after(PROFILE)
    while not paragraphs[i].text.strip():
        i += 1
    profile = paragraphs[i]

    i, skills = after(SKILLS), []
    while i < len(paragraphs) and paragraphs[i].text.strip():
        skills.append(paragraphs[i])
        i += 1

    jobs: dict[str, tuple[str, list[Paragraph]]] = {}
    i = after("Employment History")
    while i < len(paragraphs) and (paragraphs[i].text.strip() or not jobs):
        p = paragraphs[i]
        key = next((k for k, h in employers.headers.items() if h.lower() in p.text.lower()), None)
        if key and p.style.name != "List Paragraph":
            bullets = []
            i += 1
            while i < len(paragraphs) and paragraphs[i].style.name == "List Paragraph":
                bullets.append(paragraphs[i])
                i += 1
            jobs[key] = (p.text.replace("\t", "  ").strip(), bullets)
            continue
        i += 1
    missing = set(employers.headers) - set(jobs)
    if missing:
        raise SystemExit(f"{path.name}: no job header matches {sorted(missing)} - check 'jobs' in the employers file")
    return Template(doc=doc, profile=profile, skills=skills, jobs=jobs)


# --- The drafting settings --------------------------------------------------------------

def cv_drafting(employers: Employers, template: Template) -> Drafting:
    jobs = list(template.jobs)
    general = {i for i, e in employers.of_entry.items() if e not in jobs}

    async def evidence(state: DraftState, entry_text: EntryText) -> str:
        matches = sorted(state.get("matches", []), key=lambda m: m["index"])
        verdicts = "\n".join(f"- {m['requirement'].name} ({m['requirement'].kind}): {LABELS[m['verdict']]}. "
                             f"{m['cv_ids']}" for m in matches)
        blocks = [f"Requirement verdicts (what the posting asks for, and the strongest evidence):\n"
                  f"{verdicts or '(none)'}"]
        for key in jobs:
            header = template.jobs[key][0]
            lines = [f"[{i}] {await entry_text(i)}" for i in sorted(employers.entries(key))]
            blocks.append(f"Section '{key}' - {header}. Its bullets may cite only these entries:\n" + "\n".join(lines))
        lines = [f"[{i}] {await entry_text(i)}" for i in sorted(general)]
        blocks.append("Other entries (profile, skills, education, personal projects) - the Profile and Key Skills "
                      "may cite these or any entry above:\n" + "\n".join(lines))
        return "\n\n".join(blocks)

    def allowed(title: str) -> set[str] | None:
        section = employers.section(title)
        if section in (PROFILE, SKILLS):
            return None
        # A job's own entries; an unknown section title can cite nothing, so it fails
        # and gets reported rather than slipping a bullet past the employer rule.
        return employers.entries(section) if section in jobs else set()

    counts = ", ".join(f"{key} ({len(template.jobs[key][1])} bullets)" for key in jobs)
    return Drafting(prompt=CV_PROMPT.format(jobs=counts), evidence=evidence, allowed=allowed,
                    examples_label="The current CV (for style only; its facts aren't evidence)",
                    noun="CV")
