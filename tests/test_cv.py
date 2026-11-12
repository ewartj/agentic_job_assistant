"""The CV builder: template parsing and writing, and the employer rule in the graph.

A small template is generated here with the same shape as a real one (headings,
bold "Category:" runs, tabbed job headers, List Paragraph bullets), so the tests
never touch personal data. The graph runs with a fake model against the fixtures.
"""
import docx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from mcp import Client
from test_letter import USAGE, FakeModel, fix

from cv_server.server import mcp as cv_mcp
from job_agent.cv import PROFILE, SKILLS, Employers, cv_drafting, read_template, write_cv
from job_agent.letter import Outline, Paragraph, Point, run_letter

pytestmark = pytest.mark.anyio

EMPLOYERS = """\
employers:
  cv-001: acme
  cv-002: acme
  cv-003: acme
  cv-004: beta
  cv-005: beta
  cv-006: none
jobs:
  acme: Acme Health
  beta: Beta Labs
"""


@pytest.fixture
def files(tmp_path):
    d = docx.Document()
    d.add_paragraph("Ada Example")
    d.add_paragraph("Profile")
    d.add_paragraph("Old profile.")
    d.add_paragraph("")
    d.add_paragraph("Key Skills")
    for category, items in [("Languages", "Python"), ("Cloud", "AWS"), ("Data", "SQL")]:
        p = d.add_paragraph()
        p.add_run(f"{category}: ").bold = True
        p.add_run(items)
    d.add_paragraph("")
    d.add_paragraph("Employment History")
    for header, bullets in [("Acme Health  ·  Engineer\t2022 - Present", ["Old acme 1", "Old acme 2"]),
                            ("Beta Labs  ·  Intern\t2020 - 2021", ["Old beta 1"])]:
        d.add_paragraph().add_run(header).bold = True
        for b in bullets:
            d.add_paragraph(b, style="List Paragraph")
    d.add_paragraph("")
    d.add_paragraph("Education")
    d.add_paragraph("University of Somewhere  ·  MSc\t2019 - 2020")
    template, employers = tmp_path / "cv.docx", tmp_path / "employers.yaml"
    d.save(template)
    employers.write_text(EMPLOYERS, encoding="utf-8")
    return template, Employers.load(employers)


def section(title, *texts):
    return {"title": title, "purpose": "", "points": [{"pid": "x", "section": title, "text": t, "cv_ids": []}
                                                       for t in texts]}


# --- Template --------------------------------------------------------------------

def test_the_template_is_read_into_its_replaceable_parts(files):
    template, employers = files
    t = read_template(template, employers)

    assert t.profile.text == "Old profile."
    assert [p.text for p in t.skills] == ["Languages: Python", "Cloud: AWS", "Data: SQL"]
    assert {k: [p.text for p in b] for k, (_, b) in t.jobs.items()} == {
        "acme": ["Old acme 1", "Old acme 2"], "beta": ["Old beta 1"]}


def test_a_section_title_is_matched_by_key_or_header(files):
    _, employers = files
    assert [employers.section(t) for t in ["acme", "ACME", "Acme Health", "key skills", "Hobbies"]] == [
        "acme", "acme", "acme", SKILLS, "Hobbies"]


def test_writing_replaces_only_the_generated_parts(files, tmp_path):
    template, employers = files
    plan = [section(PROFILE, "First.", "Second."),
            section(SKILLS, "Cloud: AWS, Terraform", "Languages: Python"),
            section("acme", "New acme 1", "New acme 2", "New acme 3"),
            section("Beta Labs", "New beta 1"),
            section("Hobbies", "Chess")]
    warnings = write_cv(template, employers, plan, tmp_path / "out.docx")
    out = docx.Document(tmp_path / "out.docx").paragraphs

    texts = [p.text for p in out]
    assert "First. Second." in texts and "Old profile." not in texts
    assert texts.count("Acme Health  ·  Engineer\t2022 - Present") == 1  # job headers untouched
    assert "University of Somewhere  ·  MSc\t2019 - 2020" in texts  # education untouched
    acme = texts.index("Acme Health  ·  Engineer\t2022 - Present")
    assert texts[acme + 1:acme + 4] == ["New acme 1", "New acme 2", "New acme 3"]  # a third bullet cloned
    assert all(out[acme + n].style.name == "List Paragraph" for n in (1, 2, 3))
    assert "Old beta 1" not in texts and "New beta 1" in texts
    skills = [p for p in out if p.text.startswith(("Cloud:", "Languages:", "Data:"))]
    assert [p.text for p in skills] == ["Cloud: AWS, Terraform", "Languages: Python"]  # 3 lines became 2
    assert [r.bold for r in skills[0].runs] == [True, None]  # the category keeps its bold run
    assert warnings == ["section 'Hobbies' isn't in the template, so it was left out"]


def test_a_job_with_no_bullets_written_keeps_the_templates(files, tmp_path):
    template, employers = files
    warnings = write_cv(template, employers, [section("acme", "New acme 1")], tmp_path / "out.docx")
    texts = [p.text for p in docx.Document(tmp_path / "out.docx").paragraphs]

    assert "Old beta 1" in texts and "Old profile." in texts
    assert "no bullets for beta - the template's are kept" in warnings


# --- The employer rule in the graph -----------------------------------------------

def scripted(model, outline):
    """The fake model, with this outline as its first draft."""
    original = model.with_structured_output

    async def planned(messages):
        model.draft_prompts.append([m.content for m in messages])
        return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": outline, "parsing_error": None}

    model.with_structured_output = lambda schema, **kw: (
        RunnableLambda(planned) if schema is Outline else original(schema, **kw))
    return model


def outline(**sections):
    return Outline(paragraphs=[Paragraph(title=title, purpose="p", points=[Point(text=t, cv_ids=ids)
                                                                           for t, ids in points])
                               for title, points in sections.items()])


async def run_cv(model, files):
    template, employers = files
    t = read_template(template, employers)
    async with Client(cv_mcp) as cv:
        return await run_letter(model, cv, "We need Terraform.", drafting=cv_drafting(employers, t),
                                examples=[t.current()])


async def test_a_bullet_citing_another_jobs_entry_fails_without_a_model_call(files):
    model = scripted(FakeModel([], fixes=[[fix("p2", "Wrote Terraform modules [cv-002].")]]), outline(
        acme=[("Provisioned AWS with Terraform.", ["cv-001"]), ("Mentored junior engineers.", ["cv-005"])]))
    state = await run_cv(model, files)

    first = state["rounds"][0]["failed"]
    assert [c["problem"] for c in first] == ["cv-005 isn't from acme; cite only acme entries"]
    assert "Mentored junior engineers." not in model.checked  # rejected in code, not by the checker
    assert not state["rounds"][1]["failed"]  # fixed with an acme entry


async def test_profile_and_skills_may_cite_any_entry_but_unknown_sections_none(files):
    model = scripted(FakeModel([], fixes=[[], []]), outline(**{  # no fixes: the failure stands
        "Profile": [("Engineer who mentors.", ["cv-005"])],
        "Key Skills": [("Cloud: AWS, Terraform", ["cv-001"])],
        "Hobbies": [("Built R Shiny dashboards.", ["cv-003"])],
    }))
    state = await run_cv(model, files)

    failed = {c["section"]: c["problem"] for c in state["rounds"][0]["failed"]}
    assert failed == {"Hobbies": "cv-003 isn't from Hobbies; cite only Hobbies entries"}


async def test_the_planner_sees_each_jobs_entries_and_the_current_cv(files):
    model = scripted(FakeModel([]), outline(acme=[("Provisioned AWS.", ["cv-001"])]))
    await run_cv(model, files)

    prompt = model.draft_prompts[0][1]
    acme = prompt.index("Section 'acme'")
    beta = prompt.index("Section 'beta'")
    assert "[cv-001]" in prompt[acme:beta] and "[cv-004]" not in prompt[acme:beta]
    assert "[cv-004]" in prompt[beta:]
    assert "The current CV (for style only" in prompt and "Old acme 1" in prompt
