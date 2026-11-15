"""The MCP server over its real transport: spawn `python -m ticket_mcp_server` and talk MCP to it."""

import json
import os
from contextlib import asynccontextmanager
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ticket_mcp_server.server import READ_TOOLS, WRITE_TOOLS

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def open_session(db):
    """A context manager, not a fixture: anyio task groups must be entered and exited in the same task,
    which pytest-asyncio's separate fixture setup/teardown tasks do not guarantee."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ticket_mcp_server"],
        env={**os.environ, "BUGASSIST_DB": str(db), "PYTHONPATH": str(ROOT)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session, name, **args) -> dict:
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text)


async def test_server_lists_exactly_the_documented_tools(sandbox):
    async with open_session(sandbox) as mcp_session:
        tools = {t.name: t for t in (await mcp_session.list_tools()).tools}
        assert set(tools) == set(READ_TOOLS) | set(WRITE_TOOLS)
        for name in READ_TOOLS:
            assert tools[name].annotations.read_only_hint is True, name
        for name in WRITE_TOOLS:
            assert tools[name].annotations.read_only_hint is False, name
        assert "ticket_id" in tools["get_ticket"].input_schema["properties"]
        assert "BEFORE create_ticket" in tools["find_duplicates"].description


async def test_read_tools_return_seeded_data(sandbox):
    async with open_session(sandbox) as mcp_session:
        found = await call(mcp_session, "search_tickets", query="safari login redirect")
        assert found["status"] == "ok" and found["tickets"][0]["component"] == "auth"
        ticket = await call(mcp_session, "get_ticket", ticket_id=found["tickets"][0]["id"])
        assert ticket["ticket"]["comments"][0]["author"] == "qa"
        dups = await call(mcp_session, "find_duplicates", title="Heap grows until pod is OOMKilled in export worker")
        assert dups["duplicates"][0]["component"] == "workers"


async def test_write_tools_persist_across_calls(sandbox):
    async with open_session(sandbox) as mcp_session:
        created = await call(mcp_session, "create_ticket", title="Avatar upload returns 413",
                             description="Uploads over 1 MB fail behind the proxy.", priority="medium", component="profile")
        tid = created["ticket"]["id"]
        assert created["status"] == "ok" and created["ticket"]["reporter"] == "bug-assistant"
        await call(mcp_session, "add_comment", ticket_id=tid, body="nginx client_max_body_size is 1m.")
        await call(mcp_session, "update_ticket", ticket_id=tid, status="in_progress")
        got = (await call(mcp_session, "get_ticket", ticket_id=tid))["ticket"]
        assert got["status"] == "in_progress" and len(got["comments"]) == 1
        assert (await call(mcp_session, "find_duplicates", title="Avatar upload 413 error"))["duplicates"][0]["id"] == tid


async def test_validation_errors_are_results_not_crashes(sandbox):
    async with open_session(sandbox) as mcp_session:
        assert (await call(mcp_session, "get_ticket", ticket_id=424242))["status"] == "error"
        bad = await call(mcp_session, "create_ticket", title="x", description="y", priority="urgent")
        assert bad["status"] == "error" and "priority" in bad["message"]
        # the session is still usable afterwards
        assert (await call(mcp_session, "search_tickets", query="pool"))["status"] == "ok"
