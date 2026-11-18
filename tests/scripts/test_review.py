"""The review agents' script (scripts/review.py): which reviewers run, what they may read, and
which account they use. Pure functions only - claude is never started."""
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

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
    ("http://[::1]:11434", "http://[::1]:11434"),  # IPv6 keeps its brackets
])
def test_ollama_host_is_read_the_way_people_write_it(host, url):
    assert review.ollama_url(host) == url


def test_a_bad_ollama_port_is_a_message_not_a_crash():
    with pytest.raises(SystemExit, match="invalid port"):
        review.ollama_url("gpu-box:abc")


def test_another_machine_is_told_apart_from_this_one():
    assert review.is_this_machine("http://localhost:11434")
    assert not review.is_this_machine("http://gpu-box:11434")


# --- What reviewers may read ------------------------------------------------------------------

def flag_values(argv, flag):
    """The values after a flag, up to the next flag."""
    values = []
    for arg in argv[argv.index(flag) + 1:]:
        if arg.startswith("--"):
            break
        values.append(arg)
    return values


def test_the_command_lets_reviewers_only_read_and_never_see_private_files(tmp_path):
    argv = review.command("claude", "sonnet", tmp_path / "role.md")
    assert flag_values(argv, "--tools") == ["Read,Grep,Glob"]  # nothing that runs, writes or browses
    assert flag_values(argv, "--allowedTools") == ["Read,Grep,Glob"]
    assert flag_values(argv, "--permission-mode") == ["dontAsk"]  # anything not allowed is refused
    assert {"--restricted", "--strict-mcp-config", "--no-session-persistence"} <= set(argv)
    denied = set(flag_values(argv, "--disallowedTools"))
    for tool in ("Read", "Grep", "Glob"):
        assert {f"{tool}(./data/**)", f"{tool}(**/.env)", f"{tool}(**/.env.*)", f"{tool}(./.git/**)"} <= denied


def test_generated_files_are_listed_but_their_diffs_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DIFFS", tmp_path / "diff")
    monkeypatch.setattr(review, "git", lambda *args: f"diff of {args[-1]}")
    review.write_diffs("base", ["src/a.py", "uv.lock", "frontend/package-lock.json",
                                "frontend/src/api/schema.d.ts", "docs/my notes.md"])
    written = sorted(p.relative_to(tmp_path / "diff").as_posix() for p in (tmp_path / "diff").rglob("*.diff"))
    assert written == ["docs/my notes.md.diff", "src/a.py.diff"]


def test_a_committed_private_file_is_flagged_but_its_contents_never_copied(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DIFFS", tmp_path / "diff")
    monkeypatch.setattr(review, "git", lambda *args: "+MY REAL CV TEXT")
    review.write_diffs("base", ["data/cv.json", ".env", "frontend/.env.local", "docs/env.md", ".env.example"])
    for private in ("data/cv.json", ".env", "frontend/.env.local"):
        text = (tmp_path / "diff" / f"{private}.diff").read_text(encoding="utf-8")
        assert "MY REAL CV TEXT" not in text and "high-severity" in text
    for public in ("docs/env.md", ".env.example"):  # the committed template isn't a secret
        assert (tmp_path / "diff" / f"{public}.diff").read_text(encoding="utf-8") == "+MY REAL CV TEXT"


def test_model_output_cannot_send_terminal_control_codes():
    assert review.printable("ok\x1b[2K\x1b]0;title\x07 done\r\nnext\tline") == "ok[2K]0;title done\nnext\tline"


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


def test_each_reviewer_has_a_pre_push_hook_that_runs_for_the_same_area():
    """The hooks choose by pre-commit's files: patterns, a manual run by AREAS - keep them in step."""
    config = yaml.safe_load((SCRIPT.parents[1] / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {h["id"]: h for repo in config["repos"] for h in repo["hooks"] if h["id"].startswith("review-")}
    assert set(hooks) == {f"review-{name}" for name in review.AREAS}
    samples = ["docs/setup.md", "src/job_assistant/web/app.py", "tests/conftest.py", "scripts/review.py",
               "pyproject.toml", "uv.lock", "frontend/src/App.tsx", "README.md"]
    for name, paths in review.AREAS.items():
        hook = hooks[f"review-{name}"]
        assert hook["stages"] == ["pre-push"] and hook["entry"].endswith(f"--hook --roles {name}")
        for sample in samples:
            runs = hook.get("always_run", False) or re.search(hook.get("files", "^$"), sample) is not None
            assert runs == (paths is None or sample.startswith(paths)), (name, sample)


# --- Roles and results ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(review.AREAS))
def test_every_role_file_loads_without_its_front_matter(name):
    role = review.load_role(name)
    assert role.model in {"sonnet", "haiku", "opus"}
    assert role.prompt and not role.prompt.startswith("---") and "tools:" not in role.prompt


def test_role_files_with_windows_line_endings_load(tmp_path, monkeypatch):
    (tmp_path / "x-reviewer.md").write_bytes(b"---\r\nname: x\r\nmodel: haiku\r\n---\r\nCheck things.\r\n")
    monkeypatch.setattr(review, "AGENTS", tmp_path)
    assert review.load_role("x") == review.Role("x", "haiku", "Check things.")


def test_findings_are_counted_from_the_reply_headings():
    reply = "### [high] A\n`a.py:1` - x\n\n### [Low] B\n`b.py:2` - y\n\nNot a ### [medium] heading"
    assert review.Result("security", reply, 1.0).findings == 2
    assert review.Result("security", "No issues found.", 1.0).findings == 0


# --- When a reviewer fails ------------------------------------------------------------------------

ROLE = review.Role("security", "sonnet", "Check things.")


def finished(stdout, returncode=0, stderr=""):
    return lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout, stderr)


def run_review(tmp_path, monkeypatch, fake_run):
    monkeypatch.setattr(review, "OUT", tmp_path)
    return review.review(ROLE, ["src/a.py"], "claude", "sonnet", {}, timeout=60, run=fake_run)


def test_a_review_comes_back_as_its_text(tmp_path, monkeypatch):
    result = run_review(tmp_path, monkeypatch, finished(json.dumps({"result": " No issues found. "})))
    assert (result.text, result.error) == ("No issues found.", None)


def timed_out(*args, **kwargs):
    raise subprocess.TimeoutExpired("claude", 60)


def not_installed(*args, **kwargs):
    raise FileNotFoundError("claude not found")


@pytest.mark.parametrize("fake_run, error", [
    (timed_out, "no answer after 1 minutes"),
    (not_installed, "claude not found"),
    (finished(json.dumps({"is_error": True, "result": "Not logged in"})), "Not logged in"),
    (finished("", 1, "error: unknown option\n"), "error: unknown option"),
])
def test_a_failed_review_is_reported_as_failed_not_as_no_findings(tmp_path, monkeypatch, fake_run, error):
    result = run_review(tmp_path, monkeypatch, fake_run)
    assert result.error == error and result.text == ""


@pytest.mark.parametrize("problem", [RuntimeError("boom"), SystemExit("Can't find Claude Code")])
def test_as_a_hook_nothing_blocks_the_push(monkeypatch, problem):
    def broken(args):
        raise problem
    monkeypatch.setattr(review, "run", broken)
    assert review.main(["--hook"]) == 0  # the push goes ahead
    assert review.main([]) == 1  # by hand, the failure shows in the exit code
