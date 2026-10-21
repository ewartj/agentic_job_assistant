"""Tracker rules, tested directly against SQLite (tracker_server.store)."""
import sqlite3
from contextlib import closing

import pytest

from tracker_server import store


@pytest.fixture
def conn(tmp_path):
    with closing(store.connect(tmp_path / "tracker.db")) as c:
        yield c


def log(conn, company="Acme Health", role="Data Scientist", **kw):
    return store.log_application(conn, company, role, **kw)


# --- log_application ---------------------------------------------------------

def test_log_creates_an_application_with_history(conn):
    app, created = log(conn, posting="acme_ds")
    assert created
    assert (app["company"], app["role"], app["status"], app["posting"]) == (
        "Acme Health", "Data Scientist", "interested", "acme_ds")
    assert [h["status"] for h in app["history"]] == ["interested"]
    assert app["applied_on"] is None


def test_logging_again_returns_the_existing_application_unchanged(conn):
    first, _ = log(conn, notes="original")
    again, created = log(conn, company="  acme   HEALTH ", role="data scientist", notes="changed")
    assert not created
    assert again["id"] == first["id"] and again["notes"] == "original"
    assert len(store.list_applications(conn)) == 1


def test_same_company_different_role_is_a_new_application(conn):
    log(conn)
    _, created = log(conn, role="ML Engineer")
    assert created


def test_logging_as_applied_sets_the_applied_date(conn):
    app, _ = log(conn, status="applied")
    assert app["applied_on"] is not None


@pytest.mark.parametrize("when", ["2024-05-24", "2024-05", "2022"])
def test_past_applications_keep_their_date(conn, when):
    app, _ = log(conn, status="applied", applied_on=when)
    assert app["applied_on"] == when


@pytest.mark.parametrize(("kw", "message"), [
    ({"company": "  "}, "company is required"),
    ({"role": ""}, "role is required"),
    ({"status": "hired"}, "Invalid status"),
    ({"applied_on": "last week"}, "Invalid applied_on"),
])
def test_invalid_input_is_rejected(conn, kw, message):
    with pytest.raises(ValueError, match=message):
        log(conn, **kw)


def test_database_blocks_duplicates_even_without_the_check(conn):
    log(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO applications (company, role, status, created_at, updated_at)"
                     " VALUES ('ACME HEALTH', 'data scientist', 'interested', 'now', 'now')")


# --- update_status -----------------------------------------------------------

def test_status_changes_are_recorded_in_history(conn):
    app, _ = log(conn)
    store.update_status(conn, app["id"], "applied")
    updated = store.update_status(conn, app["id"], "interviewing", note="first interview 3 Nov")
    assert updated["status"] == "interviewing"
    assert [h["status"] for h in updated["history"]] == ["interested", "applied", "interviewing"]
    assert updated["history"][-1]["note"] == "first interview 3 Nov"


def test_setting_the_same_status_again_changes_nothing(conn):
    app, _ = log(conn)
    store.update_status(conn, app["id"], "applied")
    again = store.update_status(conn, app["id"], "applied")
    assert len(again["history"]) == 2


def test_applied_date_is_kept_from_the_first_time(conn):
    app, _ = log(conn, status="applied")
    first_date = app["applied_on"]
    store.update_status(conn, app["id"], "interviewing")
    assert store.update_status(conn, app["id"], "applied")["applied_on"] == first_date


def test_unknown_application_is_a_helpful_error(conn):
    with pytest.raises(ValueError, match="No application with id 99 - call list_applications"):
        store.update_status(conn, 99, "applied")


# --- list_applications -------------------------------------------------------

def test_list_filters_by_status(conn):
    a, _ = log(conn)
    log(conn, company="Beta")
    store.update_status(conn, a["id"], "applied")
    assert [x["company"] for x in store.list_applications(conn, "applied")] == ["Acme Health"]
    assert len(store.list_applications(conn)) == 2


# --- drafts ------------------------------------------------------------------

def test_saving_a_draft_again_adds_a_version(conn):
    app, _ = log(conn)
    v1 = store.save_draft(conn, app["id"], "cover_letter", "Dear Acme, first")
    v2 = store.save_draft(conn, app["id"], "cover_letter", "Dear Acme, second")
    assert (v1["version"], v2["version"]) == (1, 2)
    assert store.get_draft(conn, app["id"], "cover_letter")["text"] == "Dear Acme, second"
    assert store.get_draft(conn, app["id"], "cover_letter", version=1)["text"] == "Dear Acme, first"


def test_versions_are_counted_per_kind(conn):
    app, _ = log(conn)
    store.save_draft(conn, app["id"], "cover_letter", "letter")
    assert store.save_draft(conn, app["id"], "motivation", "why us")["version"] == 1


def test_application_lists_drafts_without_their_text(conn):
    app, _ = log(conn)
    store.save_draft(conn, app["id"], "cover_letter", "Dear Acme")
    (draft,) = store.get_application(conn, app["id"])["drafts"]
    assert draft["characters"] == len("Dear Acme") and "text" not in draft


@pytest.mark.parametrize(("kind", "text", "message"), [
    ("cover_letter", "  ", "draft text is empty"),
    ("essay", "text", "Invalid kind"),
])
def test_invalid_drafts_are_rejected(conn, kind, text, message):
    app, _ = log(conn)
    with pytest.raises(ValueError, match=message):
        store.save_draft(conn, app["id"], kind, text)


def test_missing_draft_is_a_helpful_error(conn):
    app, _ = log(conn)
    with pytest.raises(ValueError, match="No cover_letter draft"):
        store.get_draft(conn, app["id"], "cover_letter")
