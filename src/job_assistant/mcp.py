"""Talking to the MCP servers: starting them, and calling their tools.

The application reaches cv-server and tracker-server only through here and the MCP
protocol - never by importing their code - so either could run elsewhere (over HTTP,
on AWS) without the rest changing.
"""
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters

from job_assistant.config import ROOT, get_settings


@dataclass
class ToolRequest:
    """A tool call the model asked for."""
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    """What we send back to the model for one ToolRequest."""
    call_id: str
    name: str
    content: str  # text the model reads
    is_error: bool = False
    cv_ids: list[str] = field(default_factory=list)  # evidence ids returned (for evals)


async def call_mcp_tool(session: ClientSession, request: ToolRequest) -> ToolResult:
    """Run one tool call against the MCP server and package the result for the model."""
    result = await session.call_tool(request.name, request.args)
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None)) or "(no content)"
    items = (result.structured_content or {}).get("result", [])
    cv_ids = [i["id"] for i in items if isinstance(i, dict) and str(i.get("id", "")).startswith("cv-")]
    return ToolResult(request.call_id, request.name, text, is_error=bool(result.is_error), cv_ids=cv_ids)


def server(name: str) -> StdioServerParameters:
    # The server is its own process and doesn't inherit our environment, so it's told
    # where the data is - otherwise a JOB_DATA_DIR setting would silently not reach it.
    return StdioServerParameters(command="uv", args=["run", name], cwd=str(ROOT), env=get_settings().server_env())
