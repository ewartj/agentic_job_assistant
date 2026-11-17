# Approval workflow and web API

[← Back to the README](../README.md)

One run per posting that pauses for your decision before anything is saved, and the HTTP API over it.

## Applying, with your approval: `src/job_assistant/workflow/`

`job-apply` runs everything for one posting, then **stops and waits for you** before writing anything to the tracker.

```mermaid
graph TD;
  __start__ --> extract -.-> match --> assess;
  assess --> letter --> review;
  assess --> cv --> review;
  review -.->|approve| save --> __end__;
  review -.->|revise, with notes| letter;
  review -.->|revise, with notes| cv;
  review -.->|reject| __end__;
```

- **Matched once, drafted in parallel.** The letter plan and the CV share one assessment. The letter graph skips extract and match when it's given one, and the two run side by side.
- **`interrupt()` pauses the run at `review`.** It shows the verdict counts, and for each document whether it verified, how many drafts it took and where the file is. You can approve, revise with notes (for the letter, the CV or both; only those are redrafted, checked and shown again), or reject.
- **A SQLite checkpointer (`data/runs.db`) saves every step.** A paused run can be left: `job-apply --resume <run>` picks it up from the review in a new terminal or the next day, without drafting anything again. A run that fails part-way (a dropped model connection, say) resumes from its last completed step. The checkpointer only loads the project's own types (`Requirement`, `Turn`) besides LangGraph's, so a tampered database can't make it build arbitrary objects.
- **The only writes come after approval.** `save` logs the application (status `interested`, linked to the posting) and saves the assessment, the letter plan and the CV plan as drafts, with the `.docx` path noted. Rejecting writes nothing.
- **Layers:** `graph.py` (LangGraph), `service.py` (`start`, `decide`, `retry`, `view`, `runs`: the only entry point), `models.py` (Pydantic `Review`, `Decision`, `RunView`, with no LangGraph types), `cli.py`. The web interface will use the same service.

## The web API: `src/job_assistant/web/`

`uv run job-web` serves the apply workflow over HTTP on `http://127.0.0.1:8000`, with interactive docs at `/docs`. It's the backend for the React app (below).

| Endpoint | What it does |
|---|---|
| `GET/POST /api/postings`, `GET /api/postings/{name}` | List, save (pasted text with company, role and URL) and read postings, using the same rules as `job-posting` |
| `POST /api/runs` | Start a run. **Returns at once (202)**; the run continues in the background |
| `GET /api/runs`, `GET /api/runs/{id}` | Runs and their status, with the review when one is paused |
| `GET /api/runs/{id}/events` | **Live progress** as server-sent events, then the final status |
| `POST /api/runs/{id}/decision` | Approve, revise (with notes) or reject; also runs in the background |
| `POST /api/runs/{id}/retry` | Carry on a run that stopped part-way, from its last saved step |
| `GET /api/runs/{id}/files/{kind}` | Download the letter plan, the CV plan or the `.docx` |

- **The routes hold no workflow logic.** They validate, call the run manager (which calls `ApplyService`), and return the workflow's own Pydantic models. The OpenAPI schema therefore describes exactly what the frontend gets, and the frontend's TypeScript types are generated from it.
- **Runs never block a request.** Each run is a background task. Its state is always read from the checkpointer, so runs started from `job-apply` show up too, and a restart loses nothing. A run that fails part-way is reported as `stopped` with the reason, and `retry` carries on from its last completed step.
- **Progress is typed events, not text** (`src/job_assistant/events.py`): `started`, `extracted`, `matched` (each requirement and its verdict), `drafted` and `checked` (each point, passed or not, and why), each labelled with its document. The terminal turns them into the familiar lines. The web sends them as `StreamMessage`s (`progress`, `status`, `end`), which are in the API schema, so the frontend gets types for them like everything else.
- **Progress goes to the right run.** Each run's graph is tagged with its id (a context variable), so an event is delivered only to pages following that run, with the backlog first for a page opened part-way through.
- **Clear errors:** 404 for an unknown run or posting; 409 for a decision on a run that isn't paused, or a second run of a posting already in progress; 422 for an invalid name or decision.
- **Local and locked down.** It listens on `127.0.0.1` only, and there's deliberately no option to change that, since there's no login yet. CORS allows only the React dev server. Downloads are chosen by kind and built from the run's own posting name, never from a path in the request, and nothing in `data/` is served directly.

## The review screen: `frontend/`

A React app (Vite, TypeScript) over the web API. Run both, then open http://127.0.0.1:5173:

```bash
uv run job-web                      # the API, on 127.0.0.1:8000
cd frontend && npm install          # once
npm run dev                         # the page, on 127.0.0.1:5173
```

- **Paste a job:** company, role, link and the advert. It's saved as a posting (the same file `job-posting` writes), and a run starts on it. Saved jobs can be run again from the list.
- **Follow the run:** the shared assessment fills in requirement by requirement, then the letter plan and the CV side by side, each with its draft number, how many points passed their check, and every unsupported point with the reason.
- **Review:** the verdict counts, then tabs for the assessment, the letter plan and the CV. Each plan says whether every point is supported, and has the Markdown and `.docx` downloads.
- **Decide:** approve (saved to the tracker), revise with notes (the letter, the CV or both are redrafted, checked and shown again), or reject (it asks first). A run that stopped part-way has a Retry button.

How it's built:

- **Types come from the API itself.** `uv run job-openapi` writes the API's schema to `frontend/openapi.json`, and `npm run gen:api` turns it into `src/api/schema.d.ts`. A route or model that changes on the server then fails the type check, not the page. CI checks both steps are current, so they can't drift.
- **The API stays the source of truth.** The page keeps no copy of a run's state: it follows the events while a run works, and fetches the run again whenever a stretch of work ends.
- **One origin.** In development Vite passes `/api` on to `job-web`, so the browser talks to one origin and server-sent events need no cross-origin set-up.
- **Safe rendering.** Plans and assessments are Markdown, rendered without raw HTML, so text from a job advert can't inject anything into the page.
- **Small, plain parts:** `src/api/` (the typed client and the event stream), `src/run/` (progress built up from events, as pure functions, and the `useRun` hook), and `src/components/`. Pages live in the URL hash (`#/runs/<id>`), so a reload keeps your place without a router library.
- **Tested** with Vitest and Testing Library: the progress logic, error messages, posting names and routes, the decision form (revise needs notes, reject asks first) and the paste form.
