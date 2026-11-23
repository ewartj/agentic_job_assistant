"""The app's tracker client repeats the tracker's lists (it may not import the server):
keep them the same, or a status the tracker accepts would fail the API's validation."""
from typing import get_args

from job_assistant.workflow import tracker
from tracker_server import store


def test_the_statuses_and_draft_kinds_match_the_trackers():
    assert get_args(tracker.ApplicationStatus) == store.STATUSES
    assert get_args(tracker.DraftKind) == store.DRAFT_KINDS
