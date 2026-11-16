"""The models: one place that makes them, for every agent and graph.

Claude through the API, or a local model through Ollama, behind LangChain's one
interface. A Bedrock model (step 8) would be one more branch in make_model.
"""
import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from job_assistant.config import DEFAULT_NUM_CTX


def make_model(provider: str, model: str | None, num_ctx: int = DEFAULT_NUM_CTX,
               think: bool | None = None) -> BaseChatModel:
    """The same settings as agent.py's providers, so only the framework differs.

    think=False turns off a thinking model's reasoning (Ollama only); None leaves
    the model's default, which for qwen3.5 is to think.
    """
    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model or "claude-haiku-5-5",
            max_tokens=16000,
            output_config={"effort": "medium"},  # same effort as agent.py's ClaudeProvider
        )
    from langchain_ollama import ChatOllama

    # num_ctx matters as much here as in agent.py: Ollama silently drops the oldest
    # messages (the system prompt and posting) when the context overflows.
    return ChatOllama(model=model or "qwen3.5:9b", base_url=os.environ.get("OLLAMA_HOST"), num_ctx=num_ctx,
                      reasoning=think)


def model_name(model: BaseChatModel) -> str:
    return getattr(model, "model", None) or getattr(model, "model_name", "unknown")


def usage(raw: Any) -> tuple[int, int]:
    meta = getattr(raw, "usage_metadata", None) or {}
    return meta.get("input_tokens", 0), meta.get("output_tokens", 0)
