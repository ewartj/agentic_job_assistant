# Setup, usage and tests

[← Back to the README](../README.md)

Installing, adding your own data, the commands, and how the project is tested.

## Setup

You'll need [uv](https://docs.astral.sh/uv/) and Python 3.13 or later.

```bash
uv sync
cp .env.example .env    # add ANTHROPIC_API_KEY, and OLLAMA_HOST if Ollama runs on another machine
```

### Your data

`data/` is gitignored because it holds personal data. You need to create:

- **`data/cv.json`**: a list of self-contained claims in this format:
  ```json
  {"id": "cv-001", "category": "experience", "text": "...", "source": "...", "tags": ["..."]}
  ```
- **`data/letters.json`**: built from your `.docx` and `.odt` letters by `scripts/build_letters.py`, using a manifest. To set it up, copy [letters.example.yaml](../letters.example.yaml) to `data/letters.yaml` and run:
  ```bash
  uv run python scripts/build_letters.py
  ```

- **`data/evals/postings/<name>.md`**: job postings, pasted in by hand (or saved with `--clipboard`). An optional header records where each one came from. The agent sends only the text below the header to the model.
  ```markdown
  ---
  company: Acme Health
  role: Senior Data Scientist
  url: https://example.com/jobs/123
  saved: '2026-10-19'
  ---
  (the posting text, pasted as-is)
  ```

To see the expected formats, look at [tests/fixtures/](../tests/fixtures/).

## Usage

```bash
# Run an MCP server on its own, e.g. for Claude Desktop or the MCP Inspector
uv run cv-server
npx @modelcontextprotocol/inspector uv run cv-server
npx @modelcontextprotocol/inspector uv run tracker-server

# Copy a job posting from the browser, then save it and assess it in one step
uv run job-agent --clipboard acme_ds --company "Acme" --role "Data Scientist" --url "https://..."
uv run job-agent --clipboard acme_ds --replace   # saved the wrong text? copy the advert again and replace it

# Run the agent on a saved posting (the trace is appended to data/evals/runs/<posting>.jsonl)
uv run job-agent data/evals/postings/example.md
uv run job-agent data/evals/postings/example.md --provider ollama --model qwen3.5:9b
uv run job-agent data/evals/postings/example.md --mode full-context

# The same, with the LangGraph agent
uv run job-graph data/evals/postings/example.md
uv run job-graph data/evals/postings/example.md --provider ollama --with-tracker
uv run job-graph data/evals/postings/example.md --graph explicit   # the hand-built StateGraph

# The multi-step pipeline: extract requirements, match each in parallel, report
uv run job-pipeline data/evals/postings/example.md
uv run job-pipeline data/evals/postings/example.md --provider ollama --no-think   # thinking off: much faster

# Plan a cover letter paragraph by paragraph, checking every cited point against its CV entries
uv run job-letter data/evals/postings/example.md
uv run job-letter data/evals/postings/example.md --provider ollama --no-think --checker claude   # plan locally, check with Claude
uv run job-verify-eval   # score the checker on the labelled claims

# Tailor your CV (data/cv_template.docx) to a posting, checking every bullet
uv run job-cv data/evals/postings/example.md

# Assess, plan the letter and tailor the CV, then approve before anything is saved
uv run job-apply data/evals/postings/example.md
uv run job-apply --list
uv run job-apply --resume <run-id>

# The same, over HTTP (docs at http://127.0.0.1:8000/docs)
uv run job-web

# Score runs against the answer key
uv run job-score example --runs
uv run job-score example --since 2026-10-16T16:00   # only runs since a change

# See which CV entries each run cited
uv run python scripts/show_cited.py example --last 2 --text
```

To use the server from Claude Desktop, add it to `claude_desktop_config.json`:

```json
{"mcpServers": {
  "cv-server":      {"command": "uv", "args": ["--directory", "C:\\path\\to\\agentic_job_assistant", "run", "cv-server"]},
  "tracker-server": {"command": "uv", "args": ["--directory", "C:\\path\\to\\agentic_job_assistant", "run", "tracker-server"]}
}}
```

If Claude Desktop was installed from the Microsoft Store, the config is at `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\` rather than `%APPDATA%\Claude\`.

## Tests and CI

```bash
uv run pytest
uv run ruff check .
```

- **Tests** run against fixture data in `tests/fixtures/`. `CV_SERVER_DATA_DIR` points the server at the fixtures, so they never touch personal data. They cover:
  - the search rules, for both scoring and ranking;
  - the tools and the MCP protocol, using an in-process client;
  - the tracker's rules against a temporary SQLite database (`TRACKER_DB`);
  - both agents (hand-written and LangGraph, with both graphs), using scripted fake models;
  - the eval scoring.
- **GitHub Actions** runs pytest and ruff on every push and pull request.
- **Secret and personal-data checks** ([.pre-commit-config.yaml](../.pre-commit-config.yaml)) run on every commit once installed with `uv run pre-commit install`. In CI, gitleaks also scans the whole git history. The checks block:
  - API keys and tokens (gitleaks);
  - private keys;
  - large files;
  - anything under `data/` or `.env`;
  - local paths, private IP addresses and Anthropic keys.
- **Dependabot** checks the Actions and uv dependencies weekly. Updates are reviewed and merged by hand, never automatically.
