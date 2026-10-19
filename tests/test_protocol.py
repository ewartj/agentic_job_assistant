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

    assert resources == ["cv://full", "postings://index"]
    assert [e["id"] for e in json.loads(content.contents[0].text)] == [
        "cv-001", "cv-002", "cv-003", "cv-004", "cv-005", "cv-006", "cv-007", "cv-008",
    ]


async def test_cv_entry_resource_template():
    async with Client(mcp) as client:
        templates = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
        content = await client.read_resource("cv://entry/cv-003")

    assert templates == ["cv://entry/{entry_id}", "postings://posting/{name}"]
    assert json.loads(content.contents[0].text)["id"] == "cv-003"


# --- postings:// resources ---------------------------------------------------

async def test_postings_index_lists_saved_postings_with_headers():
    async with Client(mcp) as client:
        content = await client.read_resource("postings://index")

    postings = {p["name"]: p for p in json.loads(content.contents[0].text)}
    assert set(postings) == {"acme_ds", "plain"}
    assert postings["acme_ds"]["company"] == "Acme Health"
    assert postings["acme_ds"]["saved"] == "2026-10-19"
    assert postings["plain"]["company"] is None


async def test_posting_resource_returns_text_without_header():
    async with Client(mcp) as client:
        content = await client.read_resource("postings://posting/acme_ds")

    text = content.contents[0].text
    assert text.startswith("# Senior Data Scientist")
    assert "company:" not in text


@pytest.mark.parametrize("name", ["missing", "..%2F..%2F.env"])
async def test_unknown_or_unsafe_posting_is_an_error(name):
    async with Client(mcp) as client:
        with pytest.raises(Exception):  # noqa: B017 - the SDK wraps the server's error
            await client.read_resource(f"postings://posting/{name}")
