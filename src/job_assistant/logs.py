"""A log file: what each run did, and every error with its traceback.

job-web writes data/logs/job-web.log, so a run that failed in the browser can be looked
into afterwards. The log holds posting and CV text, so it lives under data/ (gitignored).
It never holds an API key: every line, tracebacks included, passes through Redacting.
"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 2_000_000  # per file; then it rolls over to .1, .2, ...
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")  # an Anthropic key, wherever it came from
HIDDEN = "[redacted]"


class RedactingMixin:
    """Put before any formatter class: formats a record as that class does, then hides API
    keys anywhere in it - the message, the arguments, an exception's text or its traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))  # type: ignore[misc]


class Redacting(RedactingMixin, logging.Formatter):
    pass


def redact(text: str) -> str:
    # Read when each line is written, not at import, so a key loaded from .env later is still hidden.
    for name in SECRET_ENV:
        secret = os.environ.get(name, "")
        if len(secret) >= 8:  # not "" or a placeholder like "ollama"
            text = text.replace(secret, HIDDEN)
    return KEY_PATTERN.sub(HIDDEN, text)


def file_handler(path: str | Path) -> logging.Handler:
    """A size-limited UTF-8 log file at path, its folder created if need be."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    handler.setFormatter(Redacting(FORMAT))
    return handler
