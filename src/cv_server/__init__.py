def main() -> None:
    # Imported here, not at module level: cv_server.server loads the CV data on import,
    # and job_agent imports cv_server.postings without needing any of that.
    from cv_server.server import main as run

    run()


__all__ = ["main"]
