"""Fixtures shared by the job_assistant tests."""
import docx
import pytest

from job_assistant.drafting.cv import Employers

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
