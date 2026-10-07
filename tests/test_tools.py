"""Tool behaviour against the fixture data in tests/fixtures/."""
from cv_server.server import get_cv_evidence, get_past_letters, list_past_applications


def ids(results: list[dict]) -> list[str]:
    return [r["id"] for r in results]


# --- get_cv_evidence ---------------------------------------------------------

def test_results_are_ranked_by_score():
    # cv-001 has both terms in its tags (score 4); cv-002 only mentions Terraform in text (1).
    assert ids(get_cv_evidence("terraform aws")) == ["cv-001", "cv-002"]


def test_limit_is_respected():
    assert ids(get_cv_evidence("terraform", limit=1)) == ["cv-001"]


def test_no_match_returns_empty_list():
    assert get_cv_evidence("kubernetes") == []


def test_stopword_only_query_returns_empty_list():
    assert get_cv_evidence("experience with the") == []


def test_short_term_only_hits_whole_words():
    # "R" must find the R Shiny entry but not "research" in cv-004.
    assert ids(get_cv_evidence("R")) == ["cv-003"]


def test_results_do_not_expose_tags():
    for result in get_cv_evidence("terraform"):
        assert set(result) == {"id", "category", "text", "source"}


# --- get_past_letters --------------------------------------------------------

def test_letters_match_partial_company_name_case_insensitively():
    assert ids(get_past_letters("FUTURE")) == ["letter-001"]


def test_letters_include_notes_when_present():
    (letter,) = get_past_letters("bbc")
    assert letter["notes"] == "Fixture draft letter."


def test_unknown_company_returns_empty_list():
    assert get_past_letters("Acme") == []


# --- list_past_applications --------------------------------------------------

def test_application_list_omits_letter_text():
    apps = list_past_applications()
    assert ids(apps) == ["letter-001", "letter-002"]
    assert all("text" not in app for app in apps)
