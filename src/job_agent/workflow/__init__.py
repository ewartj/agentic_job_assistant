"""The apply workflow: assess a posting, plan the letter and the CV, pause for review, save.

    graph.py    the LangGraph graph (interrupt, checkpointer)
    service.py  the one entry point for the CLI and the web API
    models.py   what they exchange (Pydantic, no LangGraph or web types)
    cli.py      job-apply
"""
