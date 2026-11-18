"""job-web's log file: everything goes in it, and an API key never does."""
import logging
import logging.config

import pytest

from job_assistant.cli.web import log_config
from job_assistant.logs import file_handler, redact

PREFIX = "sk-" + "ant-"  # split, so the no-personal-paths hook doesn't take these fakes for real keys
KEY = PREFIX + "api03-not-a-real-key-0123456789"
TOKEN = "a-token-from-the-environment"


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", TOKEN)


def test_keys_and_tokens_are_hidden_wherever_they_appear():
    text = redact(f"x-api-key: {KEY}, token {TOKEN}, another {PREFIX}api03-zzz_9")
    assert KEY not in text and TOKEN not in text and PREFIX not in text
    assert text.count("[redacted]") == 3


def test_a_placeholder_token_is_left_alone(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")  # what local mode sets: not a secret
    assert redact("model from ollama") == "model from ollama"


def test_the_file_keeps_tracebacks_but_not_the_key(tmp_path):
    path = tmp_path / "logs" / "job-web.log"  # the folder is created
    logger = logging.getLogger("test_logs.file")
    logger.addHandler(handler := file_handler(path))
    try:
        logger.error("calling with %s", KEY)
        try:
            raise RuntimeError(f"Error code: 401 - invalid x-api-key {KEY}")
        except RuntimeError:
            logger.exception("run r1 failed")
    finally:
        logger.removeHandler(handler)
        handler.close()
    text = path.read_text(encoding="utf-8")
    assert "run r1 failed" in text and "Traceback (most recent call last)" in text and "Error code: 401" in text
    assert KEY not in text and TOKEN not in text


@pytest.fixture
def restore_logging():
    """dictConfig changes logging for the whole process: put it back afterwards."""
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access"]
    saved = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).level,
                    logging.getLogger(name).propagate) for name in names}
    yield
    for name, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers[:], logger.level, logger.propagate = handlers, level, propagate


def test_job_web_sends_the_apps_logs_and_uvicorns_errors_to_the_file(tmp_path, restore_logging):
    path = tmp_path / "job-web.log"
    logging.config.dictConfig(log_config(path))
    logging.getLogger("job_assistant.web.runs").info("run r1 started on acme_eng")
    logging.getLogger("uvicorn.error").error("Exception in ASGI application")
    logging.getLogger("uvicorn.access").info("GET /api/health 200")  # requests stay in the terminal
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "run r1 started on acme_eng" in text and "Exception in ASGI application" in text
    assert "/api/health" not in text
