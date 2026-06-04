"""Direct tests for the MCP tool layer (no transport, function-level)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mindcontinuum.core import MemoryStore
from mindcontinuum.mcp_tools import build_server


@pytest.fixture
def mcp_pair(tmp_path: Path):
    store = MemoryStore(db_path=tmp_path / "mcp.sqlite")
    mcp = build_server(store)
    try:
        yield mcp, store
    finally:
        store.close()


async def _call(mcp, name, **kwargs):
    """Invoke a registered MCP tool by name and return the parsed payload.

    FastMCP normalises to ``(content, structured)``. Structured outputs that
    cannot be represented as a top-level JSON object (e.g. Optional[dict]) get
    wrapped in ``{"result": ...}`` — unwrap that here so callers see the same
    shape they got from a plain dict tool.
    """
    result = await mcp.call_tool(name, kwargs)
    if isinstance(result, tuple):
        content, structured = result
    else:
        content, structured = result, None
    if structured is not None:
        if isinstance(structured, dict) and list(structured.keys()) == ["result"]:
            return structured["result"]
        return structured
    for chunk in content:
        if getattr(chunk, "type", None) == "text":
            try:
                return json.loads(chunk.text)
            except Exception:
                return chunk.text
    return None


def test_ping_tool(mcp_pair):
    mcp, _ = mcp_pair
    out = asyncio.run(_call(mcp, "ping"))
    assert out["status"] == "pong"
    assert out["server"] == "MindContinuum"


def test_save_then_search_then_get(mcp_pair):
    mcp, store = mcp_pair
    saved = asyncio.run(_call(mcp, "save_memory",
        title="MindContinuum tunnel test works",
        body="Saved through MCP.",
        type="research",
        project="Bridge Spike",
        tags=["spike", "tunnel"],
    ))
    assert saved["title"].startswith("MindContinuum")
    memory_id = saved["id"]

    searched = asyncio.run(_call(mcp, "search_memory", query="tunnel test"))
    assert searched["count"] >= 1
    assert any(r["id"] == memory_id for r in searched["results"])

    fetched = asyncio.run(_call(mcp, "get_memory", id=memory_id))
    assert fetched["id"] == memory_id
    assert "Saved through MCP" in fetched["body"]


def test_record_decision_tool(mcp_pair):
    mcp, _ = mcp_pair
    out = asyncio.run(_call(mcp, "record_decision",
        project="Bridge Spike",
        decision="Use Cloudflare tunnel for the spike",
        rationale="Simplest HTTPS path; works on Windows.",
    ))
    assert out["decision_id"] >= 1
    assert out["memory_id"] >= 1


def test_record_task_tool(mcp_pair):
    mcp, _ = mcp_pair
    out = asyncio.run(_call(mcp, "record_task",
        project="Bridge Spike",
        title="Document Windows firewall step",
        next_action="Add to docs/WINDOWS_SETUP.md",
        priority="high",
    ))
    assert out["task_id"] >= 1


def test_list_recent_tool(mcp_pair):
    mcp, _ = mcp_pair
    asyncio.run(_call(mcp, "save_memory", title="a"))
    asyncio.run(_call(mcp, "save_memory", title="b"))
    out = asyncio.run(_call(mcp, "list_recent", limit=10))
    titles = [r["title"] for r in out["results"]]
    assert "a" in titles and "b" in titles


def test_append_memory_tool(mcp_pair):
    mcp, _ = mcp_pair
    saved = asyncio.run(_call(mcp, "save_memory", title="t", body="first line"))
    appended = asyncio.run(_call(mcp, "append_memory", id=saved["id"], text="second line"))
    assert "first line" in appended["body"]
    assert "second line" in appended["body"]


def test_no_destructive_tools_exposed(mcp_pair):
    """The MVP must not expose delete/drop/sql tools.

    This guards against regressions where someone adds a destructive tool.
    """
    mcp, _ = mcp_pair

    async def _names():
        tools = await mcp.list_tools()
        return [t.name for t in tools]

    names = asyncio.run(_names())
    forbidden = {"delete_memory", "drop", "execute_sql", "raw_sql", "purge", "wipe", "exec"}
    leaked = forbidden.intersection(names)
    assert not leaked, f"unsafe tools exposed: {leaked}"
    # Required tools present
    for required in ("ping", "save_memory", "search_memory", "get_memory",
                     "list_recent", "record_decision", "record_task", "append_memory"):
        assert required in names, f"missing required MCP tool: {required}"
