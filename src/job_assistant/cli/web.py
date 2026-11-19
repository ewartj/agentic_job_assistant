"""job-web: the API on http://127.0.0.1:8000, with interactive docs at /docs.

    uv run job-web
    uv run job-web --provider ollama --no-think --checker claude

It listens on 127.0.0.1 only - on purpose there's no option to listen more widely,
since the API reads your CV and writes your tracker and has no login yet.

Each run's progress, and every error with its traceback, goes to data/logs/job-web.log;
warnings and errors are shown in the terminal too. API keys are hidden in both.
"""
import argparse
import copy
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter as UvicornAccessFormatter
from uvicorn.logging import DefaultFormatter

from job_assistant.config import ROOT, get_settings
from job_assistant.console import utf8_output
from job_assistant.logs import RedactingMixin
from job_assistant.web.app import create_app
from job_assistant.workflow.runtime import ModelChoice, open_service

HOST = "127.0.0.1"


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Serve the job assistant's API on this machine only.")
    parser.add_argument("--port", type=int, default=get_settings().port)
    ModelChoice.add_arguments(parser)
    args = parser.parse_args()
    choice = ModelChoice.from_args(parser, args)
    app = create_app(lambda progress: open_service(choice, progress))
    log = get_settings().web_log
    print(f"Logging to {log}")
    uvicorn.run(app, host=HOST, port=args.port, log_config=log_config(log))


class TerminalFormatter(RedactingMixin, DefaultFormatter):
    """uvicorn's terminal lines, API keys hidden."""


class AccessFormatter(RedactingMixin, UvicornAccessFormatter):
    """uvicorn's request lines, API keys hidden."""


def log_config(path: Path) -> dict[str, Any]:
    """uvicorn's usual logging (startup lines and requests in the terminal), plus the log file
    for everything: the app's own loggers and uvicorn's errors. Every line, in the terminal or
    the file, has API keys hidden. uvicorn applies this at start-up, so it isn't overwritten the
    way a handler added beforehand would be."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["default"]["()"] = TerminalFormatter
    config["formatters"]["access"]["()"] = AccessFormatter
    config["handlers"]["file"] = {"()": "job_assistant.logs.file_handler", "path": str(path)}
    config["handlers"]["warnings"] = {"class": "logging.StreamHandler", "stream": "ext://sys.stderr",
                                      "formatter": "default", "level": "WARNING"}
    config["loggers"]["uvicorn"]["handlers"].append("file")
    config["root"] = {"handlers": ["file", "warnings"], "level": "INFO"}
    return config
