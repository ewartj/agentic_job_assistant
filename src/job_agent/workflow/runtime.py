"""Opening the apply service for real: models, the two MCP servers and the checkpointer.

Shared by job-apply and job-web, so both choose models the same way and start the
service the same way. Tests build ApplyService directly with fakes instead.
"""
import argparse
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from job_agent.agent import OLLAMA_NUM_CTX, ROOT
from job_agent.cv import EMPLOYERS, TEMPLATE, Employers
from job_agent.graph_agent import make_model, server
from job_agent.letter import DRAFTS_DIR
from job_agent.workflow.graph import ApplySettings, build_apply_graph
from job_agent.workflow.service import ApplyService, open_checkpointer

RUNS_DB = ROOT / "data" / "runs.db"
POSTINGS_DIR = ROOT / "data" / "evals" / "postings"


@dataclass
class ModelChoice:
    """Which models draft and which check, as chosen on the command line."""
    provider: str = "claude"
    model: str | None = None
    checker: str | None = None  # None: the same as provider
    checker_model: str | None = None
    num_ctx: int = OLLAMA_NUM_CTX
    no_think: bool = False

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--provider", choices=["claude", "ollama"], default="claude")
        parser.add_argument("--model", help="override the provider's default model")
        parser.add_argument("--checker", choices=["claude", "ollama"],
                            help="model provider for checking points (default: the same as --provider)")
        parser.add_argument("--checker-model", help="override the checker provider's default model")
        parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="Ollama context window in tokens")
        parser.add_argument("--no-think", action="store_true", help="turn off the Ollama model's thinking")

    @classmethod
    def from_args(cls, parser: argparse.ArgumentParser, args: argparse.Namespace) -> "ModelChoice":
        choice = cls(args.provider, args.model, args.checker, args.checker_model, args.num_ctx, args.no_think)
        if choice.no_think and "ollama" not in (choice.provider, choice.checker):
            parser.error("--no-think only applies when --provider or --checker is ollama")
        return choice

    @property
    def concurrency(self) -> int | None:
        """Ollama on one machine answers one request at a time, so the work takes turns."""
        return 1 if "ollama" in (self.provider, self.checker or self.provider) else None

    def models(self) -> tuple[BaseChatModel, BaseChatModel | None]:
        """(drafting model, checker model or None to check with the drafting one)."""
        def make(provider: str, name: str | None) -> BaseChatModel:
            think = False if self.no_think and provider == "ollama" else None
            return make_model(provider, name, self.num_ctx, think=think)

        checker = self.checker or self.provider
        separate = (checker, self.checker_model) != (self.provider, None)
        return make(self.provider, self.model), make(checker, self.checker_model) if separate else None


@asynccontextmanager
async def open_service(choice: ModelChoice, progress: Callable[[str], None] | None = None,
                       runs_db: Path = RUNS_DB) -> AsyncIterator[ApplyService]:
    """The service with its servers and checkpointer, all closed on exit."""
    model, checker_model = choice.models()
    async with AsyncExitStack() as stack:
        async def connect(name: str) -> ClientSession:
            read, write = await stack.enter_async_context(stdio_client(server(name)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session

        settings = ApplySettings(model=model, cv=await connect("cv-server"), tracker=await connect("tracker-server"),
                                 employers=Employers.load(EMPLOYERS), template=TEMPLATE, drafts_dir=DRAFTS_DIR,
                                 checker_model=checker_model, max_concurrency=choice.concurrency, progress=progress)
        checkpointer = await stack.enter_async_context(open_checkpointer(runs_db))
        yield ApplyService(build_apply_graph(settings, checkpointer), max_concurrency=choice.concurrency)
