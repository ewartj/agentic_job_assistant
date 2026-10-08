"""Exercise cv-server over stdio with a real MCP client - no Inspector/Node needed.

    uv run python scripts/try_client.py                 # default checks
    uv run python scripts/try_client.py "Terraform"     # search CV evidence

This is also a preview of Phase 2 step 4: the agent loop uses this same
ClientSession to list tools and call them on the model's behalf.
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(command="uv", args=["run", "cv-server"])


def _result(call_result):
    """Tools returning lists come back as structured content under "result"."""
    if call_result.is_error:
        return f"ERROR: {call_result.content[0].text}"
    return call_result.structured_content["result"]


async def main(queries: list[str]) -> None:
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        print("tools:    ", [t.name for t in (await session.list_tools()).tools])
        print("resources:", [str(r.uri) for r in (await session.list_resources()).resources])
        print("templates:", [t.uri_template for t in (await session.list_resource_templates()).resource_templates])

        print("\nget_cv_evidence")
        for query in queries:
            hits = _result(await session.call_tool("get_cv_evidence", {"query": query, "limit": 3}))
            print(f"  {query!r}:")
            for hit in hits if isinstance(hits, list) else [hits]:
                print(f"    {hit['id']}  {hit['text'][:90]}..." if isinstance(hit, dict) else f"    {hit}")

        print("\nlist_past_applications")
        for app in _result(await session.call_tool("list_past_applications", {})):
            print(f"  {app['id']}  {app['company']} - {app['role']}")

        print("\nget_past_letters('bbc')")
        for letter in _result(await session.call_tool("get_past_letters", {"company": "bbc"})):
            print(f"  {letter['id']}  {letter['category']}  {len(letter['text'])} chars")

        print("\nresources/read")
        full = await session.read_resource("cv://full")
        print("  cv://full          ->", len(json.loads(full.contents[0].text)), "entries")
        one = await session.read_resource("cv://entry/cv-005")
        print("  cv://entry/cv-005  ->", json.loads(one.contents[0].text)["text"][:70], "...")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["Terraform", "mentoring", "LLM judge", "kubernetes"]))
