"""The web API over the apply workflow (stage 2 of the GUI).

    app.py       the FastAPI app: start-up, shutdown, error codes
    routes.py    the endpoints (thin: validate, call, return)
    runs.py      runs in the background, one per posting at a time
    progress.py  live progress per run, for server-sent events
    schemas.py   request and response bodies beyond the workflow's own models
    cli.py       job-web
"""
