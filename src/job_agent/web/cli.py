"""job-web: the API on http://127.0.0.1:8000, with interactive docs at /docs.

    uv run job-web
    uv run job-web --provider ollama --no-think --checker claude

It listens on 127.0.0.1 only - on purpose there's no option to listen more widely,
since the API reads your CV and writes your tracker and has no login yet.
"""
import argparse

import uvicorn
from dotenv import load_dotenv

from job_agent.agent import ROOT, utf8_output
from job_agent.web.app import create_app
from job_agent.workflow.runtime import ModelChoice, open_service

HOST = "127.0.0.1"


def main() -> None:
    utf8_output()
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Serve the job assistant's API on this machine only.")
    parser.add_argument("--port", type=int, default=8000)
    ModelChoice.add_arguments(parser)
    args = parser.parse_args()
    choice = ModelChoice.from_args(parser, args)
    app = create_app(lambda progress: open_service(choice, progress))
    uvicorn.run(app, host=HOST, port=args.port)
