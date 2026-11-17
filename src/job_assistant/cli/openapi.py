"""job-openapi: write the API's schema to frontend/openapi.json, for the frontend's types.

    uv run job-openapi                  # then, in frontend/: npm run gen:api

Run it after changing anything the API takes or returns. A test fails while the
committed schema is out of date, so a change can't reach the frontend unnoticed.
"""
import argparse
import json
from pathlib import Path

from job_assistant.config import ROOT
from job_assistant.web.app import openapi_schema

SCHEMA = ROOT / "frontend" / "openapi.json"


def schema_text() -> str:
    """The schema as it's committed: stable key order, so diffs show only real changes."""
    return json.dumps(openapi_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the API's OpenAPI schema for the frontend.")
    parser.add_argument("--out", type=Path, default=SCHEMA)
    args = parser.parse_args()
    args.out.write_text(schema_text(), encoding="utf-8")
    print(f"wrote {args.out}")
