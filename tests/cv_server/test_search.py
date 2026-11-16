"""Unit tests for the keyword search rules behind get_cv_evidence."""
import pytest

from cv_server.server import _score, _terms, idf


def entry(text: str, tags: list[str] | None = None) -> dict:
    return {"text": text, "tags": tags or []}


# --- _terms: query -> search terms -------------------------------------------

def test_terms_lowercases_and_drops_stopwords():
    assert _terms("Experience with Terraform and AWS") == ["terraform", "aws"]


def test_terms_keeps_language_symbols():
    assert _terms("C++ and C#") == ["c++", "c#"]


def test_terms_splits_on_slash():
    # "ci" then also matches entries that only say "GitLab CI".
    assert _terms("CI/CD") == ["ci", "cd"]


def test_terms_of_empty_query_is_empty():
    assert _terms("   ") == []


# --- Short terms (<= 2 chars) must match a whole word ------------------------

def test_short_term_matches_whole_word():
    assert _score(entry("Built R Shiny dashboards"), ["r"]) == idf("r")


def test_short_term_does_not_match_inside_a_word():
    # Without the whole-word rule, "r" would match almost every entry.
    assert _score(entry("Led research projects"), ["r"]) == 0


# --- Longer terms match the start of a word ----------------------------------

def test_long_term_matches_word_prefix():
    assert _score(entry("Wrote unit testing guides"), ["test"]) == idf("test")


def test_long_term_does_not_match_mid_word():
    assert _score(entry("Entered a coding contest"), ["test"]) == 0


# --- Tags outrank text, and each term counts once (weighted by idf) ----------

def test_tag_match_scores_two():
    assert _score(entry("Provisioned infrastructure", ["Terraform"]), ["terraform"]) == 2 * idf("terraform")


def test_text_only_match_scores_one():
    assert _score(entry("Used Terraform at home"), ["terraform"]) == idf("terraform")


def test_match_in_tags_and_text_still_scores_two():
    assert _score(entry("Used Terraform daily", ["Terraform"]), ["terraform"]) == 2 * idf("terraform")


def test_scores_add_up_across_terms():
    score = _score(entry("Cloud work", ["Terraform", "AWS"]), ["terraform", "aws"])
    assert score == pytest.approx(2 * idf("terraform") + 2 * idf("aws"))


def test_matching_is_case_insensitive():
    assert _score(entry("Deployed on AWS"), _terms("aws")) == idf("aws")


def test_entry_without_tags_key_is_handled():
    assert _score({"text": "Terraform modules"}, ["terraform"]) == idf("terraform")


# --- Adjacent query terms found together score a phrase bonus ----------------

def test_phrase_in_tags_beats_the_words_apart():
    together = _score(entry("Team practices", ["code review"]), ["code", "review"])
    apart = _score(entry("Team practices", ["open source code", "peer review"]), ["code", "review"])
    assert together > apart


def test_phrase_bonus_is_the_pairs_mean_idf():
    words_only = 2 * idf("code") + 2 * idf("review")
    bonus = 2 * (idf("code") + idf("review")) / 2
    assert _score(entry("x", ["code review"]), ["code", "review"]) == pytest.approx(words_only + bonus)
