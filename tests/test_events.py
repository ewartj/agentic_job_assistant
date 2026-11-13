"""Progress events: what each step reports, and how the terminal shows it."""
import pytest
from mcp import Client
from test_letter import TERRAFORM, FakeModel

from cv_server.server import mcp as cv_mcp
from job_agent.events import Checked, Drafted, Extracted, Matched, Started, describe
from job_agent.letter import run_letter

pytestmark = pytest.mark.anyio


async def test_a_letter_run_reports_each_step_in_order():
    events = []
    model = FakeModel([["I provisioned AWS with Terraform [cv-001].", "I'd love to join."]])
    async with Client(cv_mcp) as cv:
        await run_letter(model, cv, "We need Terraform.", progress=events.append)

    assert [e.step for e in events] == ["started", "extracted", "matched", "drafted", "checked", "checked"]
    assert events[2].requirement == TERRAFORM.name and events[2].verdict == "met"
    assert events[3].attempt == 1 and events[3].to_check == 2
    assert all(e.supported for e in events[4:])  # passed checks are reported too, for the UI to count
    assert all(e.artifact is None for e in events)  # labelled by whoever knows which document it is


def test_the_terminal_shows_failures_and_milestones_but_not_passed_checks():
    def check(supported):
        return Checked(attempt=1, point="Point", cv_ids=["cv-001"], supported=supported, problem="no", elapsed=3)

    assert describe(Started(model="m", shared_assessment=True)) == (
        "[   0s] drafting from the shared assessment with m...")
    assert describe(Extracted(requirements=4, elapsed=2.4)) == "[   2s] extracted 4 requirements"
    assert describe(Matched(requirement="Python", kind="essential", verdict="met", elapsed=5)) == (
        "[   5s]   Python: met")
    assert describe(Drafted(attempt=1, to_check=9, artifact="cv", elapsed=12)) == (
        "[cv] [  12s] plan written; checking its 9 points...")
    assert describe(Drafted(attempt=2, to_check=2, dropped=1, artifact="cover_letter_plan", elapsed=30)) == (
        "[letter] [  30s] draft 2: 2 points fixed, 1 dropped; rechecking the fixed ones...")
    assert describe(check(False)) == '[   3s]   unsupported: "Point..." [\'cv-001\']: no'
    assert describe(check(True)) is None
