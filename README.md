# agentic_job_assistant

[![tests](https://github.com/ewartj/agentic_job_assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/ewartj/agentic_job_assistant/actions/workflows/tests.yml)

A learning project for building MCP servers and AI agents. It has three parts:

- **An MCP server** (`cv-server`) that exposes my CV and past job-application letters as tools and resources.
- **A hand-written agent loop** (`job-agent`) that reads a job posting, searches the CV through the MCP server, and writes a fit assessment that cites the evidence behind every claim.
- **An eval harness** (`job-score`) that scores the agent's runs against an answer key I wrote by hand.

The agent loop is deliberately written without a framework, so that every step is visible. The next phase rebuilds it in LangGraph and compares the two using the same evals.

## How it fits together

```
job posting ──► job-agent ──(MCP over stdio)──► cv-server ──► data/cv.json, data/letters.json
                   │  ▲
                   ▼  │ tool calls / results
                 LLM (Claude API or a local model via Ollama)
                   │
                   ▼
   answer + trace ──► data/evals/runs/<posting>.jsonl ──► job-score (vs. data/evals/<posting>.yaml)
```

## The MCP server: `src/cv_server/`

It's built with the `mcp` 2.x Python SDK. In 2.x, `FastMCP` was renamed `MCPServer`, which most tutorials don't mention yet.

**Tools** (the model decides when to call them):

| Tool | What it does |
|---|---|
| `get_cv_evidence(query, limit=10)` | Searches the CV and returns matching entries, each with an `id` the model must cite |
| `get_past_letters(company)` | Returns cover letters previously written to a company (a case-insensitive substring match) |
| `list_past_applications()` | Lists every application on file, without the letter text |

**Resources** (the client application decides what to read into context): `cv://full` returns the whole CV, and `cv://entry/{entry_id}` returns one entry.

**Search** is keyword-based. It weights each match by how rare the term is across the CV (IDF), so "parquet" counts for more than "code". On top of that:
- Tag matches score double compared with matches in the text.
- Adjacent query words found together as a phrase, such as "code review", score a bonus.
- Short terms like "R" or "CI" must match a whole word; longer terms match the start of a word, so "test" finds "testing".
- Tags help the search but are never sent to the model.

## The agent: `src/job_agent/agent.py`

The loop is about 20 lines long:
1. The model receives the posting and the tool definitions.
2. It asks for tool calls, the agent runs them over MCP, and the results go back to the model.
3. This repeats until the model answers without asking for a tool, or the turn limit (8) is reached.

Every run is saved as a JSON trace: the turns, the tool calls, the ids retrieved and cited, token counts and timings.

- **Providers:** Claude via the Anthropic API (`claude-haiku-5-5` by default), or any local model via Ollama (`qwen3.5:9b` by default). Both sit behind a small `Provider` protocol, so the loop doesn't depend on either.
- **Modes:**
  - `tools`: the model has to search for evidence.
  - `full-context`: the whole CV is placed in the prompt and no tools are given. This is the baseline that shows how much the search loses.
- **Ollama gotcha:** Ollama's default context window is small, and it silently drops the oldest messages when the conversation is too long. That included the job posting itself. The agent sets `num_ctx` to 32k tokens and warns if a prompt fills the window.

## Evals: `src/job_agent/scoring.py`

For each posting, I write an answer key (`data/evals/<posting>.yaml`). For each requirement it lists the CV entries that **must** be cited (`expected`) and those that are **fine** to cite (`acceptable`):

```yaml
requirements:
  - requirement: AWS (SageMaker, S3, ...)
    expected: [cv-018, cv-035]
    acceptable: [cv-031]
  - requirement: Kubernetes
    expected: []          # a genuine gap: nothing should be cited
```

`job-score` reports these metrics:

| Metric | Meaning |
|---|---|
| retrieved | Share of must-cite entries the agent's searches returned. This measures the **search**. |
| cited | Share of must-cite entries cited in the final answer. This measures the **search and the model's judgement**. |
| precision | Share of cited entries that are in `expected` or `acceptable` |
| covered | Share of requirements with at least one valid citation |
| made-up | Ids cited but never retrieved, which means the model invented them |

The answer key is checked when it loads: unknown ids, or an id listed as both `expected` and `acceptable`, are errors rather than silent misses. That check has already caught typos like `cv-0035`.

### Findings so far

These come from one real senior ML-engineering posting, with 2–3 runs per setup, so treat them as indicative. All rows are scored against the same answer key.

**Current baseline** (current search and prompt):

| Setup | retrieved | cited | precision | made-up |
|---|---|---|---|---|
| Claude Haiku, tools | 96% | 88% | 92% | 0 |
| Claude Haiku, full context (no search) | – | 88% | 91% | 0 |
| qwen3.5:9b (local, via Ollama), tools | 87% | 65% | 90% | 0 |

**How the Claude tools setup got there:**

| Change | retrieved | cited |
|---|---|---|
| Original keyword search, `limit=5` | 79% | 76% |
| IDF weighting + phrase bonus, `limit=10` | 84% | 80% |
| Prompt also asks for impact/outcome evidence | 96% | 88% |

What these show:

- **The search no longer loses anything.** With tools, Claude now cites as much must-cite evidence as when it's given the whole CV (88% both ways). It started 20 points behind.
- **Ties hide evidence.** Nine CV entries share the tag "code review", so with `limit=5` the later ones were never returned, however good the ranking. Raising the limit fixed that; improving the ranking alone couldn't.
- **Some evidence has to be asked for.** Entries written as stories (impact, process, leadership) rarely match skill-style queries. Asking the model to also search for impact and outcomes was the single biggest gain.
- **The small local model's weakness is judgement, not search.** The search fixes helped qwen too: it went from 52% to 65% cited. It still cites much less than Claude from similar retrieved evidence, and it sometimes attaches a real id to the wrong claim. A recall/precision score can't catch that, because the id is valid for the requirement.
- **Answer keys need judgement too.** A few entries were missed even with the full CV. I moved them from "must cite" to "acceptable" rather than tuning the agent to match my opinion of them.
- **The agent found real problems in the CV,** such as the same skill described as "in progress" in one place and "used in production" in another.

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
- **`data/letters.json`**: built from your `.docx` and `.odt` letters by `scripts/build_letters.py`, using a manifest. To set it up, copy [letters.example.yaml](letters.example.yaml) to `data/letters.yaml` and run:
  ```bash
  uv run python scripts/build_letters.py
  ```

To see the expected formats, look at [tests/fixtures/](tests/fixtures/).

## Usage

```bash
# Run the MCP server on its own, e.g. for Claude Desktop or the MCP Inspector
uv run cv-server
npx @modelcontextprotocol/inspector uv run cv-server

# Run the agent on a posting (the trace is appended to data/evals/runs/<posting>.jsonl)
uv run job-agent data/evals/postings/example.md
uv run job-agent data/evals/postings/example.md --provider ollama --model qwen3.5:9b
uv run job-agent data/evals/postings/example.md --mode full-context

# Score runs against the answer key
uv run job-score example --runs
uv run job-score example --since 2026-10-16T16:00   # only runs since a change

# See which CV entries each run cited
uv run python scripts/show_cited.py example --last 2 --text
```

To use the server from Claude Desktop, add it to `claude_desktop_config.json`:

```json
{"mcpServers": {"cv-server": {"command": "uv", "args": ["--directory", "C:\\path\\to\\agentic_job_assistant", "run", "cv-server"]}}}
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
  - the agent loop, using a scripted fake model;
  - the eval scoring.
- **GitHub Actions** runs pytest and ruff on every push and pull request.
- **Secret and personal-data checks** ([.pre-commit-config.yaml](.pre-commit-config.yaml)) run on every commit once installed with `uv run pre-commit install`. In CI, gitleaks also scans the whole git history. The checks block:
  - API keys and tokens (gitleaks);
  - private keys;
  - large files;
  - anything under `data/` or `.env`;
  - local paths, private IP addresses and Anthropic keys.
- **Dependabot** checks the Actions and uv dependencies weekly. Updates are reviewed and merged by hand, never automatically.

## Layout

```
src/cv_server/server.py    MCP server: tools, resources, search
src/job_agent/agent.py     agent loop, providers, CLI
src/job_agent/scoring.py   eval scoring (job-score)
scripts/                   build_letters.py, show_cited.py, try_client.py (stdio smoke test)
tests/                     pytest suite and fixtures
```

## Next steps

- [x] Measure the prompt change (impact/outcome searches, second-person writing)
- [ ] Embedding search alongside keywords, to catch evidence phrased differently from the query
- [ ] A second posting and answer key, to check the findings generalise
- [ ] Phase 3: rebuild the agent in LangGraph and compare it on the same evals
