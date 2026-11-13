# Architecture

[← Back to the README](../README.md)

How the pieces fit together now, how the project grew to this, and where each piece lives.

## The whole system

```
  browser (React, next) ──HTTP──► job-web (FastAPI)            terminal ──► job-apply
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

Each layer only talks to the one below it, so each can be tested and replaced on its own.

| Layer | Code | Knows about |
|---|---|---|
| Interfaces | `web/` (FastAPI), `workflow/cli.py` (`job-apply`) | HTTP or the terminal; the service and its Pydantic models |
| Service | `workflow/service.py` | Starting, deciding, retrying and listing runs. Never HTTP, never HTML |
| Graphs | `workflow/graph.py`, `letter.py`, `cv.py`, `pipeline.py` | LangGraph: nodes, state, `Send`, `interrupt()` |
| Tools and data | `cv_server/`, `tracker_server/` (MCP servers) | The CV and letters (read only), and the tracker (the only writes) |
| Models | LangChain `ChatAnthropic` / `ChatOllama` | One interface for both providers |

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
src/cv_server/server.py       MCP server: tools, resources, search
src/cv_server/postings.py     saved job postings: header parsing, safe names, saving
src/tracker_server/           MCP server for applications and drafts: store.py (SQLite rules), server.py (tools)
src/job_agent/agent.py        hand-written agent loop, providers, CLI (job-agent)
src/job_agent/graph_agent.py  the same agent on LangChain / LangGraph (job-graph)
src/job_agent/state_graph.py  create_agent's graph built by hand (job-graph --graph explicit)
src/job_agent/pipeline.py     extract -> parallel match -> report (job-pipeline)
src/job_agent/letter.py       draft <-> check loop; cover letter plans (job-letter)
src/job_agent/cv.py           the CV tailored to a posting, written into your Word template (job-cv)
src/job_agent/verify_eval.py  scores the checker on labelled claims (job-verify-eval)
src/job_agent/workflow/       the apply workflow: graph, service, models, runtime, CLI (job-apply)
src/job_agent/web/            the web API: routes, background runs, live progress (job-web)
src/job_agent/scoring.py      eval scoring (job-score)
scripts/                      build_letters.py, import_applications.py, show_cited.py, try_client.py
tests/                        pytest suite and fixtures
docs/                         these pages
```
