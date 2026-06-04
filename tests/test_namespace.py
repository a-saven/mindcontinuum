"""Tests for the work/personal namespace boundary."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from mindcontinuum.config import Settings
from mindcontinuum.core import MemoryStore
from mindcontinuum.mcp_tools import build_server, personal_allowed
from mindcontinuum.server import create_app


def test_namespace_default_is_work(store: MemoryStore):
    item = store.save_memory(title="x")
    assert item["namespace"] == "work"


def test_work_does_not_see_personal(store: MemoryStore):
    store.save_memory(title="work-item")
    store.save_memory(title="personal-item", namespace="personal", actor="ui")
    work_items = store.list_recent()
    assert all(i["namespace"] == "work" for i in work_items)
    titles = [i["title"] for i in work_items]
    assert "work-item" in titles
    assert "personal-item" not in titles
    personal_items = store.list_recent(namespace="personal")
    assert any(i["title"] == "personal-item" for i in personal_items)


def test_search_respects_namespace(store: MemoryStore):
    store.save_memory(title="alpha work")
    store.save_memory(title="alpha personal", namespace="personal", actor="ui")
    results = store.search_memory("alpha", mode="keyword")
    assert any(r["title"] == "alpha work" for r in results)
    assert not any(r["title"] == "alpha personal" for r in results)
    results = store.search_memory("alpha", mode="keyword", namespace="personal")
    assert any(r["title"] == "alpha personal" for r in results)


def test_events_log_records_namespace(store: MemoryStore):
    store.save_memory(title="p1", namespace="personal", actor="ui")
    events = store.list_events(namespace="personal")
    assert any(e["action"] == "memory.create" and e["namespace"] == "personal" for e in events)


def test_mcp_personal_writes_blocked_without_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("MINDCONTINUUM_ALLOW_PERSONAL_MCP", raising=False)
    assert personal_allowed() is False
    store = MemoryStore(db_path=tmp_path / "ns.sqlite", enable_embeddings=False)
    try:
        mcp = build_server(store)
        import asyncio
        with pytest.raises(Exception) as ei:
            asyncio.run(mcp.call_tool("save_memory", {
                "title": "private", "namespace": "personal",
            }))
        # FastMCP wraps the PermissionError but message text leaks through
        assert "Personal namespace writes are disabled" in str(ei.value)
    finally:
        store.close()


def test_mcp_personal_reads_filtered_with_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("MINDCONTINUUM_ALLOW_PERSONAL_MCP", raising=False)
    store = MemoryStore(db_path=tmp_path / "ns2.sqlite", enable_embeddings=False)
    store.save_memory(title="personal-item", namespace="personal", actor="ui")
    try:
        mcp = build_server(store)
        import asyncio, json
        async def call():
            result = await mcp.call_tool("list_recent", {"namespace": "personal"})
            content, structured = result if isinstance(result, tuple) else (result, None)
            return structured
        out = asyncio.run(call())
        assert "_warning" in out
        for r in out["results"]:
            assert r["namespace"] == "work"
    finally:
        store.close()


def test_mcp_personal_writes_allowed_with_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDCONTINUUM_ALLOW_PERSONAL_MCP", "1")
    assert personal_allowed() is True
    store = MemoryStore(db_path=tmp_path / "ns3.sqlite", enable_embeddings=False)
    try:
        mcp = build_server(store)
        import asyncio
        async def call():
            result = await mcp.call_tool("save_memory", {
                "title": "private allowed", "namespace": "personal",
            })
            content, structured = result if isinstance(result, tuple) else (result, None)
            return structured
        out = asyncio.run(call())
        assert out["namespace"] == "personal"
    finally:
        store.close()


def test_rest_namespace_query_param(tmp_path):
    settings = Settings(
        data_dir=tmp_path, db_path=tmp_path / "rest_ns.sqlite",
        host="127.0.0.1", port=3791, mcp_mount="/mcp",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        c.post("/api/memory", json={"title": "work-x"})
        c.post("/api/memory", json={"title": "personal-x", "namespace": "personal"})
        work = c.get("/api/memory").json()
        personal = c.get("/api/memory?namespace=personal").json()
        assert all(i["namespace"] == "work" for i in work["items"])
        assert any(i["title"] == "personal-x" for i in personal["items"])
