"""A cover letter plan: the drafting engine with the letter's prompt and examples.

The plan is paragraphs, each with a purpose and points, every point citing its
evidence. Examples of tone and structure are past letters from applications that
reached interview or offer - looked up through the tracker, which only the letter needs.

    uv run job-letter data/evals/postings/bbc_mle.md
"""
from typing import Any

from langchain_core.language_models import BaseChatModel
from mcp import ClientSession

from job_assistant.drafting.engine import (
    Drafting,
    run_draft,
)

EXAMPLES = 2  # past letters shown for tone and structure

PLAN_PROMPT = """\
You plan cover letters for the user. Write a plan the user will write the letter \
from, not the letter itself.

Plan 4-5 paragraphs for a letter for the role below, using only the evidence given: \
the requirement verdicts and the CV entries. For each paragraph give:
- title: a few words, e.g. "Opening", "NLP in production", "Why this team".
- purpose: one sentence on what the paragraph should be about and why it matters \
for this role.
- points: 2-4 suggested points, each short and specific (the project, the tool, the \
outcome), written as the user would say them ("I took ..."). Each point has its \
text and cv_ids: the ids of the entries it comes from.

Rules:
- Every point about the user's experience, skills or results needs its cv_ids, \
e.g. ["cv-012", "cv-031"]. Only use ids listed below. Leave cv_ids empty only for \
points about motivation or the posting.
- Say only what the cited entries say. Don't add employers, tools, numbers or \
outcomes that aren't in them.
- Lead with what the posting needs most (its essential requirements), and choose \
the strongest evidence rather than listing everything.
- Don't present a requirement judged "not met" as a strength. A point may suggest \
saying you're keen to learn it, without a citation.
- Points about motivation (why this company, why this role) need no citation; base \
them on the posting.
- Shape the plan like the example letters, in UK English."""


def letter_drafting(cv: ClientSession, tracker: ClientSession | None = None) -> Drafting:
    """The engine set up for a letter plan; with a tracker, past letters are its examples."""
    return Drafting(prompt=PLAN_PROMPT, examples_label="Example letters (for tone and structure only)",
                    noun="letter", find_examples=(lambda: interview_letters(cv, tracker)) if tracker else None)


async def run_letter(model: BaseChatModel, cv: ClientSession, posting: str, *,
                     tracker: ClientSession | None = None, **kwargs: Any) -> dict[str, Any]:
    """Plan a cover letter for the posting; see run_draft for the other arguments."""
    return await run_draft(model, cv, posting, letter_drafting(cv, tracker), **kwargs)


async def interview_letters(cv: ClientSession, tracker: ClientSession | None, n: int = EXAMPLES) -> list[str]:
    """The n most recent cover letters from applications that reached interview or offer."""
    if tracker is None:
        return []
    applications = (await tracker.call_tool("list_applications", {})).structured_content["result"]
    companies = []
    for application in applications:
        detail = (await tracker.call_tool("get_application", {"application_id": application["id"]})).structured_content
        if any(h["status"] in ("interviewing", "offer") for h in detail.get("history", [])):
            companies.append(application["company"])
    letters = []
    for company in dict.fromkeys(companies):
        found = (await cv.call_tool("get_past_letters", {"company": company})).structured_content["result"]
        letters += [x for x in found if x.get("category") == "cover_letter" and x.get("status") != "draft"]
    newest = sorted({x["id"]: x for x in letters}.values(), key=lambda x: x.get("date") or "", reverse=True)
    # The same letter is sometimes sent to two roles at one company; show it once.
    return list(dict.fromkeys(x["text"] for x in newest))[:n]


def letter_title(company: str | None, role: str | None) -> str:
    return "Cover letter plan" + (f": {role}" if role else "") + (f" at {company}" if company else "")
