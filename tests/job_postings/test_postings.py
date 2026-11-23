"""Saved job postings: header parsing, safe names, saving (cv_server.postings)."""
import pytest

from job_postings import (
    archived_postings,
    list_postings,
    posting_path,
    save_posting,
    set_archived,
    split_front_matter,
)


def test_header_is_split_from_the_posting_text():
    meta, body = split_front_matter("---\ncompany: Acme\nrole: DS\n---\n# Job\n\nText.\n")
    assert meta == {"company": "Acme", "role": "DS"}
    assert body == "# Job\n\nText.\n"


def test_text_without_a_header_is_unchanged():
    assert split_front_matter("# Job\n---\nnot a header") == ({}, "# Job\n---\nnot a header")


def test_windows_line_endings_are_handled():
    meta, body = split_front_matter("---\r\ncompany: Acme\r\n---\r\nText")
    assert (meta, body) == ({"company": "Acme"}, "Text")


def test_header_that_is_not_key_value_pairs_is_an_error():
    with pytest.raises(ValueError, match="key: value"):
        split_front_matter("---\n- a list\n---\nText")


@pytest.mark.parametrize("name", ["../../.env", "a/b", "", ".hidden", "acme ds"])
def test_unsafe_names_are_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="Invalid posting name"):
        posting_path(tmp_path, name)


def test_save_writes_a_header_and_round_trips(tmp_path):
    path = save_posting(tmp_path, "acme_ds", "  Job text\n\n", company="Acme", role=None, url="https://x")
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    assert meta["company"] == "Acme" and meta["url"] == "https://x" and "saved" in meta
    assert "role" not in meta  # empty fields are left out
    assert body == "Job text\n"


def test_save_never_overwrites(tmp_path):
    save_posting(tmp_path, "acme", "first")
    with pytest.raises(FileExistsError):
        save_posting(tmp_path, "acme", "second")


def test_save_rejects_empty_text(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        save_posting(tmp_path, "acme", "   \n")


def test_list_reads_headers_and_tolerates_missing_ones(tmp_path):
    save_posting(tmp_path, "b_job", "text", company="Beta")
    (tmp_path / "a_job.md").write_text("no header", encoding="utf-8")
    listed = list_postings(tmp_path)
    assert [p["name"] for p in listed] == ["a_job", "b_job"]
    assert listed[0]["company"] is None and listed[1]["company"] == "Beta"
    assert isinstance(listed[1]["saved"], str)  # not a YAML date object


def test_replace_overwrites_the_text_and_keeps_the_header(tmp_path):
    save_posting(tmp_path, "acme", "wrong text", company="Acme", url="https://x")
    path = save_posting(tmp_path, "acme", "right text", replace=True, role="DS")
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    assert body == "right text\n"
    assert (meta["company"], meta["url"], meta["role"]) == ("Acme", "https://x", "DS")  # kept, kept, added


def test_replace_can_change_header_fields(tmp_path):
    save_posting(tmp_path, "acme", "text", company="acme ltd")
    path = save_posting(tmp_path, "acme", "text", replace=True, company="Acme Health")
    assert split_front_matter(path.read_text(encoding="utf-8"))[0]["company"] == "Acme Health"


def test_replace_of_a_new_name_just_saves(tmp_path):
    assert save_posting(tmp_path, "fresh", "text", replace=True).exists()


def test_replace_still_rejects_empty_text(tmp_path):
    save_posting(tmp_path, "acme", "good text")
    with pytest.raises(ValueError, match="empty"):
        save_posting(tmp_path, "acme", "  ", replace=True)
    assert "good text" in (tmp_path / "acme.md").read_text(encoding="utf-8")  # untouched


def test_archiving_keeps_the_posting_and_can_be_undone(tmp_path):
    save_posting(tmp_path, "acme_ds", "We need Python.")
    save_posting(tmp_path, "globex_eng", "We need Go.")
    set_archived(tmp_path, "acme_ds", True)
    set_archived(tmp_path, "acme_ds", True)  # twice is the same as once
    assert archived_postings(tmp_path) == {"acme_ds"}
    assert [p["name"] for p in list_postings(tmp_path)] == ["acme_ds", "globex_eng"]  # still saved
    set_archived(tmp_path, "acme_ds", False)
    assert archived_postings(tmp_path) == set()


def test_only_saved_postings_with_safe_names_can_be_archived(tmp_path):
    with pytest.raises(FileNotFoundError):
        set_archived(tmp_path, "missing", True)
    with pytest.raises(ValueError):
        set_archived(tmp_path, "../escape", True)
    assert archived_postings(tmp_path) == set()  # nothing written
