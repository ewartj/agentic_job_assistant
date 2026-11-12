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
import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import docx
import yaml
from docx.text.paragraph import Paragraph
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from cv_server.postings import split_front_matter
from job_agent.agent import OLLAMA_NUM_CTX, ROOT, utf8_output
from job_agent.graph_agent import make_model, model_name, server
from job_agent.letter import (
    DRAFTS_DIR,
    Drafting,
    EntryText,
    LetterState,
    is_verified,
    plan_document,
    round_report,
    run_letter,
)
from job_agent.pipeline import LABELS

TEMPLATE = ROOT / "data" / "cv_template.docx"
EMPLOYERS = ROOT / "data" / "cv_employers.yaml"
CVS_DIR = ROOT / "data" / "evals" / "cvs"  # traces

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
        raise SystemExit(f"{path.name}: no job header matches {sorted(missing)} - check 'jobs' in {EMPLOYERS.name}")
    return Template(doc=doc, profile=profile, skills=skills, jobs=jobs)


# --- Writing the docx ------------------------------------------------------------------

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


# --- The drafting settings --------------------------------------------------------------

def cv_drafting(employers: Employers, template: Template) -> Drafting:
    jobs = list(template.jobs)
    general = {i for i, e in employers.of_entry.items() if e not in jobs}

    async def evidence(state: LetterState, entry_text: EntryText) -> str:
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
                    examples=False, examples_label="The current CV (for style only; its facts aren't evidence)",
                    noun="CV")


# --- Run ----------------------------------------------------------------------------

async def amain(args: argparse.Namespace) -> None:
    meta, posting = split_front_matter(args.posting.read_text(encoding="utf-8"))
    employers = Employers.load(args.employers)
    template = read_template(args.template, employers)
    think = False if args.no_think else None
    model = make_model(args.provider, args.model, args.num_ctx, think=think if args.provider == "ollama" else None)
    checker = args.checker or args.provider
    checker_model = (make_model(checker, args.checker_model, args.num_ctx, think=think if checker == "ollama" else None)
                     if (checker, args.checker_model) != (args.provider, None) else None)
    concurrency = args.max_concurrency or (1 if "ollama" in (args.provider, checker) else None)

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server("cv-server")))
        cv = await stack.enter_async_context(ClientSession(read, write))
        await cv.initialize()
        state = await run_letter(model, cv, posting, company=meta.get("company") or "", role=meta.get("role") or "",
                                 limit=args.limit, max_concurrency=concurrency, checker_model=checker_model,
                                 drafting=cv_drafting(employers, template), examples=[template.current()],
                                 progress=lambda line: print(line, file=sys.stderr, flush=True))

    verified = is_verified(state["rounds"])
    stem = args.posting.stem
    out, plan_path, warnings = save_cv(state, args.template, employers, DRAFTS_DIR, stem, meta.get("role"))
    plan_md = plan_path.read_text(encoding="utf-8")

    CVS_DIR.mkdir(parents=True, exist_ok=True)
    record = {"started_at": datetime.now().isoformat(timespec="seconds"),
              "provider": f"cv-{args.provider}" + ("-nothink" if args.no_think else ""), "model": model_name(model),
              "checker": f"{checker}/{model_name(checker_model or model)}", "posting": stem,
              "drafts": state.get("attempt", 0), "verified": verified, "rounds": state["rounds"],
              "plan": state.get("letter", ""), "warnings": warnings}
    with (CVS_DIR / f"{stem}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    print(plan_md)
    print("---")
    print(f"{record['provider']}/{record['model']}  checker={record['checker']}  drafts={record['drafts']}  "
          + ("VERIFIED" if verified else "NOT VERIFIED"))
    print("\n".join(round_report(state["rounds"])))
    for w in warnings:
        print(f"WARNING: {w}")
    if not verified:
        print("WARNING: the CV still has unsupported points (listed above and in the plan file).")
    print(f"CV saved to {out}\nplan (with citations) saved to {DRAFTS_DIR / f'{stem}.cv_plan.md'}")


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Tailor your CV to a job posting, checking every bullet.")
    parser.add_argument("posting", type=Path, help="text/markdown file containing the job posting")
    parser.add_argument("--template", type=Path, default=TEMPLATE,
                        help="your CV as a .docx (default data/cv_template.docx)")
    parser.add_argument("--employers", type=Path, default=EMPLOYERS,
                        help="entry -> job mapping (default data/cv_employers.yaml)")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--checker", choices=["claude", "ollama"],
                        help="model provider for checking bullets (default: the same as --provider)")
    parser.add_argument("--checker-model", help="override the checker provider's default model")
    parser.add_argument("--limit", type=int, default=10, help="results per search (default 10)")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
    parser.add_argument("--max-concurrency", type=int,
                        help="branches run at once (default: all for Claude, 1 for Ollama)")
    parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")
    args = parser.parse_args()
    if args.no_think and "ollama" not in (args.provider, args.checker):
        parser.error("--no-think only applies when --provider or --checker is ollama")
    asyncio.run(amain(args))
