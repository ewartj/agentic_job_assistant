"""Settings: one place for paths and defaults, overridable from the environment."""
import argparse
import json
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from test_cv import files  # noqa: F401 - the generated CV template and employers

from job_agent.graph_agent import server
from job_agent.settings import Settings, get_settings
from job_agent.workflow.runtime import ModelChoice, open_service

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fresh_settings():
    """get_settings() is cached per process; clear it around tests that change the environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_every_path_follows_the_data_dir(tmp_path):
    s = Settings(data_dir=tmp_path)
    paths = [s.cv_file, s.template, s.employers, s.postings_dir, s.claims_file, s.drafts_dir, s.traces_dir,
             s.letter_traces_dir, s.cv_traces_dir, s.runs_db, s.tracker_db]
    assert all(p.is_relative_to(tmp_path) for p in paths)
    assert s.server_env() == {"CV_SERVER_DATA_DIR": str(tmp_path), "TRACKER_DB": str(tmp_path / "tracker.db")}


def test_settings_come_from_job_environment_variables(monkeypatch, tmp_path, fresh_settings):
    monkeypatch.setenv("JOB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOB_PROVIDER", "ollama")
    monkeypatch.setenv("JOB_PORT", "9001")
    s = get_settings()
    assert (s.data_dir, s.provider, s.port) == (tmp_path, "ollama", 9001)


def test_command_line_defaults_come_from_settings_and_flags_override_them(monkeypatch, fresh_settings):
    monkeypatch.setenv("JOB_PROVIDER", "ollama")
    monkeypatch.setenv("JOB_CHECKER", "claude")
    parser = argparse.ArgumentParser()
    ModelChoice.add_arguments(parser)

    defaults = ModelChoice.from_args(parser, parser.parse_args([]))
    assert (defaults.provider, defaults.checker) == ("ollama", "claude")
    flagged = ModelChoice.from_args(parser, parser.parse_args(["--provider", "claude"]))
    assert flagged.provider == "claude"


@pytest.mark.anyio
async def test_a_server_process_is_told_where_the_data_is(monkeypatch, fresh_settings):
    # The MCP server is a separate process that doesn't inherit our environment; it used to
    # read the default data/ whatever JOB_DATA_DIR said. Now it's given the path.
    monkeypatch.setenv("JOB_DATA_DIR", str(FIXTURES))
    async with stdio_client(server("cv-server")) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        index = await session.read_resource("postings://index")
    assert {p["name"] for p in json.loads(index.contents[0].text)} == {"acme_ds", "plain"}  # the fixtures'


@pytest.mark.anyio
async def test_the_real_service_opens_from_settings(monkeypatch, tmp_path, files, fresh_settings):  # noqa: F811
    # What job-apply and job-web do at start-up: real server processes, the checkpointer,
    # the template and employers file - all found through JOB_DATA_DIR. No model is called.
    template, _ = files  # the fixture writes cv.docx and employers.yaml into tmp_path
    for name in ("cv.json", "letters.json"):
        (tmp_path / name).write_text((FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")
    template.replace(tmp_path / "cv_template.docx")
    (tmp_path / "employers.yaml").replace(tmp_path / "cv_employers.yaml")
    monkeypatch.setenv("JOB_DATA_DIR", str(tmp_path))

    async with open_service(ModelChoice(provider="ollama")) as service:
        assert await service.runs() == []
    assert (tmp_path / "runs.db").exists()  # the checkpointer went where the settings said
