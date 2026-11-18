"""job-web: the API on http://127.0.0.1:8000, with interactive docs at /docs.

    uv run job-web
    uv run job-web --provider ollama --no-think --checker claude

It listens on 127.0.0.1 only - on purpose there's no option to listen more widely,
since the API reads your CV and writes your tracker and has no login yet.

Each run's progress, and every error with its traceback, goes to data/logs/job-web.log
(API keys hidden); warnings and errors are shown in the terminal too.
"""
import argparse
import copy
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from uvicorn.config import LOGGING_CONFIG

from job_assistant.config import ROOT, get_settings
from job_assistant.console import utf8_output
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


def log_config(path: Path) -> dict[str, Any]:
    """uvicorn's usual logging (startup lines and requests in the terminal), plus the log file
    for everything: the app's own loggers and uvicorn's errors. uvicorn applies it at start-up,
    so it isn't overwritten the way a handler added beforehand would be."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["redacted"] = {"()": "job_assistant.logs.Redacting", "fmt": "%(levelname)s: %(message)s"}
    config["handlers"]["file"] = {"()": "job_assistant.logs.file_handler", "path": str(path)}
    config["handlers"]["warnings"] = {"class": "logging.StreamHandler", "stream": "ext://sys.stderr",
                                      "formatter": "redacted", "level": "WARNING"}
    config["loggers"]["uvicorn"]["handlers"].append("file")
    config["root"] = {"handlers": ["file", "warnings"], "level": "INFO"}
    return config
