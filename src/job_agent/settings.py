"""Every path and default in one place, overridable with JOB_* environment variables or .env.

    JOB_DATA_DIR=D:/job-data uv run job-apply ...      # all personal data somewhere else
    JOB_PROVIDER=ollama JOB_CHECKER=claude uv run job-web

Paths are all derived from data_dir, so moving the data (or, later, pointing at cloud
storage) is one setting. Read once per process with get_settings().

The MCP servers run as separate processes that don't inherit the environment, so
server_env() hands them the paths they need explicitly.
"""
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # the repository
DEFAULT_NUM_CTX = 32768  # Ollama context window, in tokens: the posting plus several rounds of results

Provider = Literal["claude", "ollama"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JOB_", env_file=ROOT / ".env", env_file_encoding="utf-8",
                                      extra="ignore")

    data_dir: Path = ROOT / "data"  # personal data: gitignored, never in the repo

    # Default models: command-line flags override these.
    provider: Provider = "claude"
    model: str | None = None  # None: the provider's default
    checker: Provider | None = None  # None: the same as provider
    checker_model: str | None = None
    num_ctx: int = DEFAULT_NUM_CTX

    port: int = 8000  # job-web; it always listens on 127.0.0.1 only

    # --- Inputs ---
    @property
    def cv_file(self) -> Path:
        return self.data_dir / "cv.json"

    @property
    def template(self) -> Path:
        """Your CV as a Word document, for job-cv and job-apply."""
        return self.data_dir / "cv_template.docx"

    @property
    def employers(self) -> Path:
        """Which job each CV entry belongs to."""
        return self.data_dir / "cv_employers.yaml"

    @property
    def evals_dir(self) -> Path:
        """Answer keys (<posting>.yaml), and the folders below."""
        return self.data_dir / "evals"

    @property
    def postings_dir(self) -> Path:
        return self.evals_dir / "postings"

    @property
    def claims_file(self) -> Path:
        """Labelled claims for job-verify-eval."""
        return self.evals_dir / "claims.yaml"

    # --- Outputs ---
    @property
    def drafts_dir(self) -> Path:
        """Letter plans, CV plans and CVs, to read and send."""
        return self.data_dir / "drafts"

    @property
    def traces_dir(self) -> Path:
        """Assessment traces (one JSON line per run), scored by job-score."""
        return self.evals_dir / "runs"

    @property
    def letter_traces_dir(self) -> Path:
        return self.evals_dir / "letters"

    @property
    def cv_traces_dir(self) -> Path:
        return self.evals_dir / "cvs"

    @property
    def runs_db(self) -> Path:
        """The checkpointer: job-apply and job-web runs, step by step."""
        return self.data_dir / "runs.db"

    @property
    def tracker_db(self) -> Path:
        return self.data_dir / "tracker.db"

    def server_env(self) -> dict[str, str]:
        """What the MCP servers need to find the data, passed to their processes."""
        return {"CV_SERVER_DATA_DIR": str(self.data_dir), "TRACKER_DB": str(self.tracker_db)}


@cache
def get_settings() -> Settings:
    return Settings()
