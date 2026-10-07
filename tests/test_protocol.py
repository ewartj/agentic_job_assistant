"""End-to-end over MCP: an in-process client talking to the real server object.

Catches what the plain function tests can't - tool registration, generated
input schemas, structured output and resource URIs.
"""
import json

import pytest
from mcp import Client

from cv_server.server import mcp

pytestmark = pytest.mark.anyio


async def test_lists_tools_with_expected_parameters():
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert set(tools) == {"list_past_applications", "get_cv_evidence", "get_past_letters"}
    schema = tools["get_cv_evidence"].input_schema
    assert set(schema["properties"]) == {"query", "limit"}
    assert schema["required"] == ["query"]


async def test_call_tool_returns_structured_results():
    async with Client(mcp) as client:
        result = await client.call_tool("get_cv_evidence", {"query": "terraform"})

    assert not result.is_error
    assert [r["id"] for r in result.structured_content["result"]] == ["cv-001", "cv-002"]


async def test_missing_required_argument_is_an_error():
    async with Client(mcp) as client:
        result = await client.call_tool("get_cv_evidence", {})

    assert result.is_error


async def test_full_cv_resource():
    async with Client(mcp) as client:
        resources = [str(r.uri) for r in (await client.list_resources()).resources]
        content = await client.read_resource("cv://full")

    assert resources == ["cv://full"]
    assert [e["id"] for e in json.loads(content.contents[0].text)] == [
        "cv-001", "cv-002", "cv-003", "cv-004", "cv-005",
    ]


async def test_cv_entry_resource_template():
    async with Client(mcp) as client:
        templates = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
        content = await client.read_resource("cv://entry/cv-003")

    assert templates == ["cv://entry/{entry_id}"]
    assert json.loads(content.contents[0].text)["id"] == "cv-003"
