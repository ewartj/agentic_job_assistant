"""Scoring agent runs against an answer key (job_agent.scoring)."""
import pytest

from job_agent.scoring import Requirement, load_case, score_run, since

REQUIREMENTS = [
    Requirement("AWS", {"cv-001", "cv-002"}),
    Requirement("Mentoring", {"cv-005"}),
    Requirement("Kubernetes", set()),  # known gap
]


def run(cited, retrieved, mode="tools"):
    return {"started_at": "t", "provider": "p", "model": "m", "mode": mode, "stop": "done",
            "cited_ids": cited, "retrieved_ids": retrieved}


def test_perfect_run():
    s = score_run(run(["cv-001", "cv-002", "cv-005"], ["cv-001", "cv-002", "cv-005"]), REQUIREMENTS)
    assert (s.retrieval_recall, s.citation_recall, s.citation_precision) == (1.0, 1.0, 1.0)
    assert s.requirements_covered == 1.0  # the known gap isn't counted
    assert s.made_up == [] and s.missed == {}


def test_recall_and_precision():
    # Retrieved 2 of 3 expected, cited 1 of 3 expected plus one extra.
    s = score_run(run(["cv-001", "cv-004"], ["cv-001", "cv-004", "cv-005"]), REQUIREMENTS)
    assert s.retrieval_recall == pytest.approx(2 / 3)
    assert s.citation_recall == pytest.approx(1 / 3)
    assert s.citation_precision == 0.5
    assert s.requirements_covered == 0.5  # AWS covered by cv-001, mentoring not
    assert s.missed == {"AWS": ["cv-002"], "Mentoring": ["cv-005"]}


def test_made_up_citations_are_cited_but_never_retrieved():
    s = score_run(run(["cv-001", "cv-009"], ["cv-001"]), REQUIREMENTS)
    assert s.made_up == ["cv-009"]


def test_full_context_has_no_retrieval_and_no_made_up():
    s = score_run(run(["cv-001", "cv-009"], [], mode="full-context"), REQUIREMENTS)
    assert s.retrieval_recall is None
    assert s.made_up == []


def test_an_old_empty_done_trace_is_reported_as_empty_answer():
    assert score_run({**run([], ["cv-001"]), "final_answer": ""}, REQUIREMENTS).stop == "empty_answer"
    assert score_run({**run([], ["cv-001"]), "final_answer": "No fit."}, REQUIREMENTS).stop == "done"
    assert score_run({**run([], []), "stop": "turn_cap", "final_answer": ""}, REQUIREMENTS).stop == "turn_cap"


def test_nothing_cited():
    s = score_run(run([], ["cv-001"]), REQUIREMENTS)
    assert s.citation_recall == 0.0
    assert s.citation_precision is None
    assert s.requirements_covered == 0.0


def test_unknown_ids_in_answer_key_are_rejected(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("requirements:\n  - requirement: Python\n    expected: [cv-001, cv-0035]\n")
    with pytest.raises(SystemExit, match="cv-0035"):
        load_case(case, known_ids={"cv-001", "cv-035"})


def test_answer_key_loads(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("requirements:\n  - requirement: Python\n    expected: [cv-001]\n"
                    "  - requirement: Kubernetes\n    expected: []\n")
    reqs = load_case(case, known_ids={"cv-001"})
    assert [(r.name, r.expected) for r in reqs] == [("Python", {"cv-001"}), ("Kubernetes", set())]


# --- expected (must cite) vs acceptable (fine to cite) --------------------------

SPLIT = [
    Requirement("AWS", expected={"cv-001"}, acceptable={"cv-002", "cv-003"}),
    Requirement("Mentoring", expected=set(), acceptable={"cv-005"}),  # nothing required
]


def test_recall_counts_only_must_cite_ids():
    # Cited the one must-cite id; skipping the acceptable ones doesn't hurt recall.
    s = score_run(run(["cv-001"], ["cv-001"]), SPLIT)
    assert s.citation_recall == 1.0
    assert s.retrieval_recall == 1.0


def test_acceptable_ids_are_not_penalised_in_precision():
    s = score_run(run(["cv-001", "cv-002", "cv-005"], ["cv-001", "cv-002", "cv-005"]), SPLIT)
    assert s.citation_precision == 1.0


def test_ids_outside_both_lists_lower_precision():
    s = score_run(run(["cv-001", "cv-009"], ["cv-001", "cv-009"]), SPLIT)
    assert s.citation_precision == 0.5


def test_acceptable_evidence_counts_as_covered():
    s = score_run(run(["cv-005"], ["cv-005"]), SPLIT)
    assert s.requirements_covered == 0.5  # Mentoring covered via acceptable cv-005; AWS not
    assert s.missed == {"AWS": ["cv-001"]}  # only must-cite ids are reported as missed


def test_id_in_both_lists_is_rejected(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("requirements:\n  - requirement: AWS\n    expected: [cv-001]\n    acceptable: [cv-001]\n")
    with pytest.raises(SystemExit, match="both expected and acceptable"):
        load_case(case, known_ids={"cv-001"})


def test_unknown_acceptable_id_is_rejected(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("requirements:\n  - requirement: AWS\n    expected: [cv-001]\n    acceptable: [cv-0099]\n")
    with pytest.raises(SystemExit, match="cv-0099"):
        load_case(case, known_ids={"cv-001"})


# --- --since -----------------------------------------------------------------

RUNS = [{"started_at": "2026-10-15T22:50:04"}, {"started_at": "2026-10-16T15:53:35"}]


def test_since_keeps_runs_at_or_after_the_time():
    assert since(RUNS, "2026-10-16T15:53:35") == RUNS[1:]


def test_since_accepts_a_date_prefix():
    assert since(RUNS, "2026-10-16") == RUNS[1:]


def test_since_none_keeps_everything():
    assert since(RUNS, None) == RUNS
