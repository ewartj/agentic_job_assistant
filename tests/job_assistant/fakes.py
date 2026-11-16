"""Fake models for the graphs: scripted replies, no API calls.

FakeModel answers each structured-output schema the drafting graph asks for; ApplyModel
also tells the letter and CV prompts apart, for the apply workflow.
"""
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from job_assistant.assessment.pipeline import Judgement, Requirement, Requirements
from job_assistant.drafting.checker import Check
from job_assistant.drafting.engine import Fix, Fixes, Outline, Paragraph, Point, strip_citations
from job_assistant.traces import CV_ID

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
TERRAFORM = Requirement(name="Terraform", kind="essential", queries=["terraform"])


def point(text):
    """A point; "[cv-001]" in the text becomes its cv_ids, as the schema holds them."""
    return Point(text=strip_citations(text), cv_ids=CV_ID.findall(text))


def plan(*points):
    """A one-paragraph outline with these points."""
    return Outline(paragraphs=[Paragraph(title="Evidence", purpose="Show the Terraform work.",
                                         points=[point(t) for t in points])])


def fix(pid, text=None):
    """A fix for point `pid`: new text (with [cv-001] citations), or None to drop it."""
    if text is None:
        return Fix(id=pid, drop=True, text="", cv_ids=[])
    return Fix(id=pid, drop=False, text=strip_citations(text), cv_ids=CV_ID.findall(text))


class FakeModel:
    """A scripted first plan (a list of points, or None for a reply that doesn't parse),
    scripted fixes for each redraft (a list of fix(), or None), and a rule for the
    checker (returns a problem, or None if the point is supported)."""

    def __init__(self, drafts, problem_with=lambda sentence: None, requirements=(TERRAFORM,), fixes=()):
        self.drafts = list(drafts)
        self.fixes = list(fixes)
        self.problem_with = problem_with
        self.requirements = list(requirements)
        self.draft_prompts = []  # the messages the first plan was asked with
        self.fix_prompts = []  # the messages each redraft was asked with
        self.checked = []  # sentences the checker was asked about
        self.uncited = []  # those asked with the uncited-point prompt

    def with_structured_output(self, schema, **kwargs):
        async def run(messages):
            raw = AIMessage("", usage_metadata=USAGE)
            prompt = messages[-1].content
            if schema is Requirements:
                parsed = Requirements(requirements=self.requirements)
            elif schema is Outline:
                self.draft_prompts.append([m.content for m in messages])
                points = self.drafts.pop(0)
                if points is None:
                    return {"raw": raw, "parsed": None, "parsing_error": "bad json"}
                parsed = plan(*points)
            elif schema is Fixes:
                self.fix_prompts.append([m.content for m in messages])
                fixes = self.fixes.pop(0)
                if fixes is None:
                    return {"raw": raw, "parsed": None, "parsing_error": "bad json"}
                parsed = Fixes(fixes=fixes)
            elif schema is Judgement:
                ids = sorted(set(CV_ID.findall(prompt)))
                parsed = Judgement(verdict="met" if ids else "gap", cv_ids=ids, summary="You have it.")
            else:
                sentence = prompt.split("Sentence: ", 1)[1].split("\n", 1)[0]
                self.checked.append(sentence)
                if "cites no CV entries" in messages[0].content:
                    self.uncited.append(sentence)
                problem = self.problem_with(sentence)
                parsed = Check(reasoning="", supported=problem is None, problem=problem or "")
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return RunnableLambda(run)


LETTER = Outline(paragraphs=[Paragraph(title="Opening", purpose="Why.", points=[
    Point(text="I provisioned AWS with Terraform.", cv_ids=["cv-001"]),
    Point(text="I'd love to join.", cv_ids=[])])])


CV = Outline(paragraphs=[
    Paragraph(title="Profile", purpose="Lead.",
              points=[Point(text="Engineer who uses Terraform.", cv_ids=["cv-001"])]),
    Paragraph(title="acme", purpose="Infra.",
              points=[Point(text="Provisioned AWS with Terraform.", cv_ids=["cv-001"])]),
])


class ApplyModel(FakeModel):
    """Plans the letter or the CV depending on which prompt asks; records each request."""

    def __init__(self):
        super().__init__([])
        self.planned = []  # ("letter" | "cv", the messages)

    def with_structured_output(self, schema, **kwargs):
        if schema is not Outline:
            return super().with_structured_output(schema, **kwargs)

        async def plan(messages):
            kind = "cv" if "tailor the user's CV" in messages[0].content else "letter"
            self.planned.append((kind, [m.content for m in messages]))
            return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": CV if kind == "cv" else LETTER,
                    "parsing_error": None}

        return RunnableLambda(plan)
