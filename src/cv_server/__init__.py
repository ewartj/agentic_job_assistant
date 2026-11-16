def main() -> None:
    # Imported here, not at module level: cv_server.server loads the CV data on import,
    # which only the running server needs.
    from cv_server.server import main as run

    run()


__all__ = ["main"]
