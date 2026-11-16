"""The checker: does a point say only what the CV entries it cites say?

One model call per point, with structured output (reasoning first, then the verdict).
Used by the drafting loop for every point, and scored on its own by job-verify-eval.
"""
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

CHECK_PROMPT = """\
You check one point from a cover letter plan or a CV against the CV entries it cites.

The point is supported if every specific fact in it - employer, project, role, \
tool, technology, number, outcome, date - is stated in the cited entries or follows \
directly from them. General framing ("I enjoy", "I'm keen to") needs no support. A \
fact that may be true but isn't in these cited entries counts as unsupported.

Only facts about the user need support. Facts about the employer, the role or the \
posting itself (the company's product, its location, its team, its plans) come from \
the posting, not the CV, so ignore them when deciding.

When the cited entries describe different projects or jobs, the point may mention \
both, but it mustn't merge them into one piece of work: details from one project \
can't be presented as happening in, or alongside, the other.

If anything is unsupported, set supported to false and say exactly what, e.g. \
"cv-007 is from Acme, not Globex" or "no cited entry mentions AWS CDK"."""

UNCITED_PROMPT = """\
You check one point from a cover letter plan or a CV that cites no CV entries.

Motivation and interest never need evidence, even though they're about the user: \
what they want, what draws them to the company or role, what they're keen to learn \
("I'd love to help pharmacy teams focus on patients", "Your focus on AI is what \
draws me to this role"). Nor does anything about the posting or the company itself.

Only a fact about the user's experience, skills, projects, employers, \
qualifications or results needs evidence. If the point states one, set supported \
to false and say "states <the fact> without citing evidence"."""


class Check(BaseModel):
    # Fields are generated in this order, so the reasoning comes before the verdict.
    # With supported first, the model committed to a verdict and then sometimes argued
    # itself out of it ("...Re-evaluating: supported") while the field still said false.
    reasoning: str = Field(description="Compare each fact in the point with the cited entries before deciding")
    supported: bool
    problem: str = Field(description="One short sentence: what the cited entries don't support; "
                                     "empty if supported. Your working goes in reasoning, not here")


async def verify(checker: Any, sentence: str, texts: dict[str, str]) -> tuple[bool | None, str, Any]:
    """One claim against its cited entries' text: (supported, problem, raw reply).

    With no entries (texts empty), the question is instead whether the claim states
    a fact about the user at all. `checker` is a model with structured output for
    Check (include_raw=True).
    supported is None when the reply couldn't be parsed, so the claim is unchecked.
    Shared by the letter graph and job-verify-eval, so the eval tests exactly this.
    """
    cited = "\n".join(f"[{i}] {t}" for i, t in texts.items()) or "(none)"
    out = await checker.ainvoke([SystemMessage(CHECK_PROMPT if texts else UNCITED_PROMPT),
                                 HumanMessage(f"Sentence: {sentence}\n\nCited entries:\n{cited}")])
    result: Check | None = out["parsed"]
    if result is None:
        return None, f"Could not check: {out['parsing_error']}", out["raw"]
    return result.supported, "" if result.supported else short(result.problem), out["raw"]


def short(problem: str, limit: int = 300) -> str:
    """The first line of a problem, capped: small models sometimes write their whole
    working into it, which would then be sent back to the planner on every redraft."""
    line = problem.strip().split("\n", 1)[0]
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + "..."
