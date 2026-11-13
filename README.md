# agentic_job_assistant

[![tests](https://github.com/ewartj/agentic_job_assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/ewartj/agentic_job_assistant/actions/workflows/tests.yml)

A learning project in building MCP servers and AI agents, applied to job applications. Given a job posting, it:

- **assesses your fit,** requirement by requirement, citing the CV evidence behind every verdict;
- **plans a cover letter:** paragraph by paragraph, each point citing its evidence;
- **tailors your CV** in your own Word template, choosing and rewording bullets for the role;
- **checks every claim** against the CV entries it cites, and fixes what isn't supported;
- **waits for your approval** before saving anything to your application tracker.

It runs on Claude, or on a local model through Ollama, and every step is measured against hand-written answer keys.

## How it works

```
posting ─► extract requirements ─► match each one in parallel ─► letter plan ∥ tailored CV ─► check each point
                                                                                                    │
            tracker ◄── save (only after you approve) ◄── review: approve / revise / reject ◄───────┘
```

- **Two MCP servers:** one reads the CV, past letters and saved postings; the other records applications and drafts.
- **LangGraph runs the steps.** It matches in parallel, loops between checking and fixing, pauses for your review, and saves every step, so a paused run can be picked up later.
- **Use it from the terminal (`job-apply`) or over HTTP (`job-web`).** A React front end is next.

[Architecture](docs/architecture.md) has the full picture and the layers.

## Results so far

| | Claude Haiku | qwen3.5:9b (local) |
|---|---|---|
| Must-cite evidence cited, one agent doing everything | 85% | 62% |
| The same, with the work split into steps | 86–92% | 74–85% |
| Checker: unsupported claims caught | 14–15 of 15 | 15 of 15 |
| Checker: supported claims wrongly flagged | 0 of 17 | 3 of 17 |

Splitting the work helped the small model most: it only has to judge, not plan. Claude checks every claim, whichever model drafts. The details and their limits are in [evals](docs/evals.md), [the pipeline](docs/pipeline.md) and [letter plans and CVs](docs/letters-and-cvs.md).

## Quick start

```bash
uv sync                                                    # install
uv run job-agent --clipboard acme_ds --company "Acme" --role "Data Scientist"   # save a copied job advert
uv run job-apply data/evals/postings/acme_ds.md            # assess, draft, then review and approve
uv run job-web                                             # or the same over HTTP: http://127.0.0.1:8000/docs
```

It needs your own CV data in `data/`, which is gitignored. [Setup](docs/setup.md) covers that, along with every command and the tests.

## Documentation

| Page | Covers |
|---|---|
| [Architecture](docs/architecture.md) | How the pieces fit, the layers, how the project grew, the file layout |
| [MCP servers](docs/mcp-servers.md) | `cv-server` (tools, resources, search) and `tracker-server` (writes, idempotency) |
| [Agents](docs/agents.md) | The hand-written loop, then LangGraph prebuilt and built by hand, compared on the same evals |
| [Pipeline](docs/pipeline.md) | Extract → parallel match → report, and what it did for Claude and for qwen |
| [Letter plans and CVs](docs/letters-and-cvs.md) | Drafting from evidence, the checker and its eval, patch-based fixes, the CV builder |
| [Workflow and web](docs/workflow-and-web.md) | Pausing for approval (`interrupt()`, checkpointer) and the web API |
| [Evals](docs/evals.md) | Answer keys, metrics, and what the scores showed |
| [Setup](docs/setup.md) | Installing, your data, usage, tests and CI |

## Next steps

Done:
- [x] Measure the prompt change (impact/outcome searches, second-person writing)
- [x] More postings and answer keys, to check the findings generalise (three so far)
- [x] Rebuild the agent in LangGraph and compare it on the same evals, first with `create_agent`, then as a hand-built `StateGraph`
- [x] A multi-step graph: extract requirements and match each in parallel (`job-pipeline`)
- [x] Cover letter plans with every point verified against its cited evidence, and a labelled eval for the checker (`job-letter`, `job-verify-eval`)
- [x] A CV tailored to each posting, written into a Word template (`job-cv`)
- [x] Human approval before anything is saved: `interrupt()` and a SQLite checkpointer (`job-apply`)
- [x] A web API over the workflow: background runs, live progress, review decisions (`job-web`)

Web app:
- [ ] Structured progress events (in place of text lines) and a single settings object, before the frontend
- [ ] React review screen: paste a job, follow progress, review the assessment, letter plan and CV, then decide
- [ ] Postings and tracker pages
- [ ] Tidy-ups: the draft-and-verify engine as its own module, an idempotent tracker save, and typed graph state

Then:
- [ ] Embedding search alongside keywords, to catch evidence phrased differently from the query
- [ ] Deploy to AWS: a job queue and workers, a hosted checkpointer, S3 for files, MCP servers over HTTP, authentication, and tracing
- [ ] Evals at scale: 30–50 postings rated by hand, with the hallucination rate per claim and cost per run
