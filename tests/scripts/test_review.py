"""The review agents' script (scripts/review.py): which reviewers run, what they may read, and
which account they use. Pure functions only - claude is never started."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "review.py"
spec = importlib.util.spec_from_file_location("review", SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


# --- Which account: never a pay-per-use API key by accident ----------------------------------

def test_on_claude_any_api_key_or_other_endpoint_is_removed():
    env = review.environment(None, "localhost", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_AUTH_TOKEN": "t",
                                                 "ANTHROPIC_BASE_URL": "https://example.com", "PATH": "/bin"})
    assert env == {"PATH": "/bin"}  # so Claude Code uses the plan you're signed in with


def test_with_a_local_model_claude_code_is_pointed_at_ollama():
    env = review.environment("qwen3.5:9b", "gpu-box:11434", {"ANTHROPIC_API_KEY": "sk-test"})
    assert env["ANTHROPIC_BASE_URL"] == "http://gpu-box:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert env["ANTHROPIC_API_KEY"] == ""


@pytest.mark.parametrize("host, url", [
    ("localhost", "http://localhost:11434"),
    ("0.0.0.0:11434", "http://localhost:11434"),  # where Ollama listens, not where to connect
    ("http://localhost:11500", "http://localhost:11500"),
    ("gpu-box", "http://gpu-box:11434"),
])
def test_ollama_host_is_read_the_way_people_write_it(host, url):
    assert review.ollama_url(host) == url


def test_another_machine_is_told_apart_from_this_one():
    assert review.is_this_machine("http://localhost:11434")
    assert not review.is_this_machine("http://gpu-box:11434")


# --- What reviewers may read ------------------------------------------------------------------

def test_personal_data_and_secrets_are_denied_to_every_tool():
    assert {"Read(./data/**)", "Grep(./data/**)", "Glob(./data/**)", "Read(./.env)"} <= set(review.PRIVATE)
    assert review.TOOLS == "Read,Grep,Glob"  # nothing that runs, writes or browses


def test_generated_files_are_listed_but_their_diffs_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DIFFS", tmp_path / "diff")
    monkeypatch.setattr(review, "git", lambda *args: f"diff of {args[-1]}")
    review.write_diffs("base", ["src/a.py", "uv.lock", "frontend/package-lock.json", "docs/my notes.md"])
    written = sorted(p.relative_to(tmp_path / "diff").as_posix() for p in (tmp_path / "diff").rglob("*.diff"))
    assert written == ["docs/my notes.md.diff", "src/a.py.diff"]


def test_changed_file_names_with_spaces_come_through_whole(monkeypatch):
    monkeypatch.setattr(review, "git", lambda *args: "docs/my notes.md\0src/café.py\0")
    assert review.changed_files("base") == ["docs/my notes.md", "src/café.py"]


# --- Which reviewers run ------------------------------------------------------------------------

@pytest.mark.parametrize("files, roles", [
    (["docs/setup.md"], ["security", "tests-docs"]),
    (["src/job_assistant/web/app.py"], ["security", "backend", "tests-docs"]),
    (["frontend/src/App.tsx"], ["security", "frontend", "tests-docs"]),
    (["uv.lock", "frontend/package.json"], ["security", "backend", "frontend", "tests-docs"]),
])
def test_each_reviewer_runs_when_its_area_changes(files, roles):
    assert review.choose_roles(files) == roles


def test_asked_for_roles_are_deduplicated_and_checked():
    assert review.choose_roles(["docs/x.md"], "security, backend,security,") == ["security", "backend"]
    with pytest.raises(SystemExit, match="unknown roles"):
        review.choose_roles([], "security,nope")
    with pytest.raises(SystemExit):
        review.choose_roles([], " , ")


# --- Roles and results ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(review.AREAS))
def test_every_role_file_loads_without_its_front_matter(name):
    role = review.load_role(name)
    assert role.model in {"sonnet", "haiku", "opus"}
    assert role.prompt and not role.prompt.startswith("---") and "tools:" not in role.prompt


def test_findings_are_counted_from_the_reply_headings():
    reply = "### [high] A\n`a.py:1` - x\n\n### [Low] B\n`b.py:2` - y\n\nNot a ### [medium] heading"
    assert review.Result("security", reply, 1.0).findings == 2
    assert review.Result("security", "No issues found.", 1.0).findings == 0
