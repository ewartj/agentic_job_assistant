# Setup, usage and tests

[← Back to the README](../README.md)

Installing, adding your own data, the commands, and how the project is tested.

## Setup

You'll need [uv](https://docs.astral.sh/uv/) and Python 3.13 or later, and [Node.js](https://nodejs.org/) 24 or later for the review screen.

```bash
uv sync
cp .env.example .env    # add ANTHROPIC_API_KEY, and OLLAMA_HOST if Ollama runs on another machine
```

### Settings

Every path and default lives in one place, `src/job_assistant/config.py`. Each can be overridden with a `JOB_*` environment variable or a line in `.env`. Command-line flags override both.

| Setting | Default | What it sets |
|---|---|---|
| `JOB_DATA_DIR` | `data/` | Where all personal data lives. Every other path (CV, template, postings, drafts, traces, the runs and tracker databases) is under it |
| `JOB_PROVIDER`, `JOB_MODEL` | `claude`, the provider's default | The drafting model |
| `JOB_CHECKER`, `JOB_CHECKER_MODEL` | the same as the provider | The checking model, e.g. `JOB_PROVIDER=ollama JOB_CHECKER=claude` |
| `JOB_NUM_CTX` | `32768` | Ollama's context window |
| `JOB_PORT` | `8000` | `job-web`'s port. It always listens on `127.0.0.1` |

The MCP servers run as separate processes and don't inherit the environment, so the agent passes them the data paths explicitly. Before that, setting a data folder silently didn't reach them: they read the default `data/` regardless.

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

- **`data/evals/postings/<name>.md`**: job postings, pasted in by hand (or saved from the clipboard with `job-posting`). An optional header records where each one came from. The agent sends only the text below the header to the model.
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

# Copy a job posting from the browser, then save it (assess it with job-apply or the review screen)
uv run job-posting acme_ds --company "Acme" --role "Data Scientist" --url "https://..."
uv run job-posting acme_ds --replace   # saved the wrong text? copy the advert again and replace it

# After changing what the API takes or returns: refresh the frontend's schema and types
uv run job-openapi
cd frontend && npm run gen:api

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
uv run lint-imports
cd frontend && npm run check && npm test
```

- **Tests** run against fixture data in `tests/fixtures/`, in one folder per package. `CV_SERVER_DATA_DIR` points the server at the fixtures, so they never touch personal data. They cover:
  - the search rules, for both scoring and ranking;
  - the tools and the MCP protocol, using an in-process client;
  - the tracker's rules against a temporary SQLite database (`TRACKER_DB`);
  - both agents (hand-written and LangGraph, with both graphs), using scripted fake models;
  - the eval scoring;
  - the web API end to end, and that `frontend/openapi.json` matches it;
  - the review screen (Vitest): progress, errors, routes and both forms.
- **GitHub Actions** runs on every push and pull request: pytest; ruff and the import contracts; and for the frontend, its generated types, type check, tests and build.
- **Secret and personal-data checks** ([.pre-commit-config.yaml](../.pre-commit-config.yaml)) run on every commit once installed with `uv run pre-commit install`. In CI, gitleaks also scans the whole git history. The checks block:
  - API keys and tokens (gitleaks);
  - private keys;
  - large files;
  - anything under `data/` or `.env`;
  - local paths, private IP addresses and Anthropic keys.
- **Dependabot** checks the Actions, uv and npm dependencies weekly. Updates are reviewed and merged by hand, never automatically.

## Review agents

Four Claude Code agents review a branch's changes before each push, each with one role. The roles are in [`.claude/agents/`](../.claude/agents/):

| Reviewer | Runs when the branch changes | Looks for |
|---|---|---|
| `security` | anything | secrets, personal data in committed files, prompt injection, unsafe paths, the API's exposure, risky dependencies |
| `backend` | `src/`, `tests/`, `scripts/`, `pyproject.toml`, `uv.lock` | bugs, async and LangGraph mistakes, the layering rules, checkpoint compatibility, the API's contract with the frontend |
| `frontend` | `frontend/` | API shape mismatches, state and race bugs, error and loading states, accessibility |
| `tests-docs` | anything | untested changes, and docs that no longer match the code |

```bash
uv run pre-commit install --hook-type pre-push     # once: review before every push
uv run python scripts/review.py                     # or by hand, on your Claude plan
uv run python scripts/review.py --local qwen3.5:9b  # or on a model served by Ollama
SKIP=review-agents git push                         # skip it for one push
```

- **Advisory.** The reviews are printed, and kept in `.git/review/`, but a push is never blocked. Agents can be wrong, and they vary from run to run, so treat them as a second pair of eyes.
- **Your Claude plan, or a local model.**
  - By default the agents run on the Claude plan you're signed in to Claude Code with. The script removes any `ANTHROPIC_API_KEY` first, so a review never bills an API key by accident.
  - With `--local MODEL` (or `REVIEW_LOCAL_MODEL`), Claude Code talks to Ollama instead (`OLLAMA_HOST`, by default `http://localhost:11434`). Give the model a context window of 64k tokens or more, for example `OLLAMA_CONTEXT_LENGTH=65536` where Ollama runs.
- **Read-only.**
  - Each reviewer gets a diff per changed file, and can only read, search and list files in the repository: no commands, no edits, no web.
  - What it reads is treated as data, never instructions.
  - Lock files and generated files are listed, but their diffs are left out.
- **Claude Code** must be installed, or the VS Code extension's copy is used. Set `CLAUDE_BIN` if it's somewhere else.
- **Several in parallel.** On Claude, the reviewers run side by side, and a large branch takes about two minutes. On one local GPU, they run one after another.
