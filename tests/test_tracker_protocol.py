"""tracker-server over MCP: tool registration, annotations, schemas, errors."""
import pytest
from mcp import Client

from tracker_server.server import mcp

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def tracker_db(tmp_path, monkeypatch):
    # The server reads TRACKER_DB on every call, so each test gets a fresh database.
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "tracker.db"))


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content
    return result.structured_content


async def test_write_and_read_tools_are_annotated():
    async with Client(mcp) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}

    reads = {"list_applications", "get_application", "get_draft"}
    writes = {"log_application", "update_status", "save_draft"}
    assert set(tools) == reads | writes
    assert all(tools[n].read_only_hint for n in reads)
    assert not any(tools[n].read_only_hint for n in writes)
    assert not any(tools[n].destructive_hint for n in writes)  # nothing deletes or overwrites
    assert tools["log_application"].idempotent_hint
    assert not tools["save_draft"].idempotent_hint  # each call adds a version


async def test_statuses_are_an_enum_in_the_schema():
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["update_status"].input_schema["properties"]["status"]["enum"][:2] == ["interested", "applied"]


async def test_log_update_and_list_round_trip():
    async with Client(mcp) as client:
        app = await call(client, "log_application", {"company": "Acme", "role": "DS"})
        await call(client, "update_status", {"application_id": app["id"], "status": "applied"})
        again = await call(client, "log_application", {"company": "acme", "role": "ds"})
        listed = await call(client, "list_applications", {"status": "applied"})

    assert app["created"] and not again["created"]
    assert [a["id"] for a in listed["result"]] == [app["id"]]


async def test_drafts_round_trip():
    async with Client(mcp) as client:
        app = await call(client, "log_application", {"company": "Acme", "role": "DS"})
        await call(client, "save_draft", {"application_id": app["id"], "kind": "cover_letter", "text": "Dear Acme"})
        draft = await call(client, "get_draft", {"application_id": app["id"], "kind": "cover_letter"})

    assert (draft["version"], draft["text"]) == (1, "Dear Acme")


@pytest.mark.parametrize(("name", "args"), [
    ("update_status", {"application_id": 1, "status": "hired"}),  # not in the enum
    ("update_status", {"application_id": 99, "status": "applied"}),  # no such application
    ("log_application", {"company": " ", "role": "DS"}),  # blank company
])
async def test_bad_calls_come_back_as_tool_errors(name, args):
    async with Client(mcp) as client:
        result = await client.call_tool(name, args)
    assert result.is_error  # the model sees the message and can correct itself


async def test_unknown_id_error_tells_the_model_what_to_do():
    async with Client(mcp) as client:
        result = await client.call_tool("get_application", {"application_id": 99})
    assert "call list_applications" in result.content[0].text
