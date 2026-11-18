# Architecture

[← Back to the README](../README.md)

How the pieces fit together now, how the project grew to this, and where each piece lives.

## The whole system

```
  browser (React app) ───HTTP──► job-web (FastAPI)            terminal ──► job-apply
                                        │                                       │
                                        └───────────────► ApplyService ◄────────┘
                                                               │
                         LangGraph apply graph, saved step by step in data/runs.db
           extract ─► match ×N ─► assess ─► letter plan ∥ CV ─► review (pause) ─► save
                                                               │
                                                  MCP over stdio│
                              ┌────────────────────────────────┴───────────────┐
                          cv-server                                      tracker-server
              CV entries, past letters, postings                 applications, status, drafts
                              │
                LLM: Claude API, or a local model via Ollama
```

## Layers

There are four packages under `src/`. Each one imports only its own modules and the ones below it, so each can be tested and replaced on its own:

| Package | What it is | May import |
|---|---|---|
| `cv_server` | MCP server: the CV, past letters and postings (read only) | `job_postings` |
| `tracker_server` | MCP server: applications and drafts (the only writes), and `tracker-import` | nothing of ours |
| `job_postings` | The posting file format: header, safe names, saving | nothing of ours |
| `job_assistant` | The application: talks to the servers over MCP only | `job_postings` |

Inside `job_assistant`, each layer may import the layers below it, never above:

| Layer | Code | Knows about |
|---|---|---|
| Commands | `cli/` (`job-apply`, `job-web`, `job-letter`, ...) | Arguments and printing. Nothing else in the package does either |
| Web API | `web/` (FastAPI) | HTTP; the service and its Pydantic models |
| Service and evals | `workflow/` (the apply graph, `ApplyService`), `evals/` | Starting, deciding, retrying and listing runs. Never HTTP, never HTML |
| Drafting | `drafting/` (the engine, the checker, the letter and CV configurations, the Word writer) | The draft → check → fix loop, for any document |
| Assessment | `assessment/` (extract → parallel match → report) | Which requirements a posting has, and the evidence for each |
| Core | `llm`, `mcp`, `events`, `traces`, `console`, then `config` | Models, MCP calls, progress events, run records, the terminal, settings |

`experiments/` holds the learning steps (the hand-written agent loop and the LangGraph agents). They use only the core, and nothing imports them.

These rules are contracts in `pyproject.toml` (`[tool.importlinter]`). `uv run lint-imports` checks them, and so does CI: an import that breaks a rule fails the build.

- **A run's state lives in one place:** the checkpointer (`data/runs.db`). The terminal and the web share runs, and a restart loses nothing.
- **The model can read, but only you can write.** The model's tools are read-only. The one write (saving to the tracker) happens after you approve, which limits what a prompt-injected posting could do.

## How it grew

Each command is one step of the project, and each later one builds on the earlier ones:

| Command | Step | Doc |
|---|---|---|
| `cv-server`, `tracker-server` | MCP servers: tools, resources, and a server that writes | [MCP servers](mcp-servers.md) |
| `job-agent` | A hand-written agent loop, deliberately without a framework | [Agents](agents.md) |
| `job-score` | Scoring runs against hand-written answer keys | [Evals](evals.md) |
| `job-graph` | The same agent in LangGraph, prebuilt and then built by hand | [Agents](agents.md) |
| `job-pipeline` | The work split into extract → parallel match → report | [Pipeline](pipeline.md) |
| `job-letter`, `job-cv`, `job-verify-eval` | Drafting with every point checked against its evidence | [Letter plans and CVs](letters-and-cvs.md) |
| `job-apply`, `job-web` | One run that pauses for your approval, and an API over it | [Workflow and web](workflow-and-web.md) |

The first agent loop, for comparison. It's still there, and still scored the same way:

```
job posting ──► job-agent ──(MCP over stdio)──► cv-server ──► data/cv.json, data/letters.json
                   │  ▲
                   ▼  │ tool calls / results
                 LLM (Claude API or a local model via Ollama)
                   │
                   ▼
   answer + trace ──► data/evals/runs/<posting>.jsonl ──► job-score (vs. data/evals/<posting>.yaml)
```

## Layout

```
src/cv_server/                  MCP server: tools, resources, search
src/tracker_server/             MCP server for applications and drafts: store.py (SQLite rules), server.py (tools),
                                importer.py (tracker-import)
src/job_postings/               the posting file format, shared by cv_server and job_assistant
src/job_assistant/
  cli/                          every command: apply, web, postings, pipeline, letter, cv, score, checker_eval,
                                openapi
  web/                          the web API: routes, background runs, live progress
  workflow/                     the apply workflow: graph, service, models, runtime
  evals/                        scoring runs against answer keys, and the checker against labelled claims
  drafting/                     engine.py (draft <-> check <-> fix), checker.py, letter.py, cv.py, docx.py
  assessment/                   pipeline.py: extract -> parallel match -> report
  llm.py, mcp.py, events.py, traces.py, console.py, config.py    the core
  experiments/                  agent.py (job-agent), graph_agent.py and state_graph.py (job-graph)
scripts/                        build_letters.py, show_cited.py, try_client.py, review.py (review agents)
.claude/agents/                 the review agents' roles: security, backend, frontend, tests & docs
tests/                          one folder per package, plus the shared fixtures
docs/                           these pages
```
