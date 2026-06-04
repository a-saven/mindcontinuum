"""MCP tool surface.

Wraps :class:`MemoryStore` in a small, safe MCP tool set. The tools are the
ONLY way ChatGPT/Claude can write or read MindContinuum memory.

Explicitly excluded from the MVP MCP surface:
    * raw SQL execution
    * destructive delete
    * schema mutation
    * bulk overwrite
    * arbitrary filesystem access
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from mcp.server.fastmcp import FastMCP

from .core import MemoryStore


def build_server(store: MemoryStore, *, name: str = "MindContinuum") -> FastMCP:
    """Build a FastMCP server bound to a given memory store.

    ``streamable_http_path`` is set to ``"/"`` so that when the Starlette app is
    mounted under ``/mcp`` in FastAPI the final URL is ``/mcp`` (not ``/mcp/mcp``).
    """
    mcp = FastMCP(
        name=name,
        instructions=(
            "MindContinuum is a local long-term memory store. Use save_memory to "
            "capture notes, ideas, research, project context, prompts, or risks. "
            "Use record_decision for decisions with rationale and record_task for "
            "open work. New items default to status='inbox' and require human "
            "approval before being promoted to stable memory. Use search_memory "
            "before saving to avoid duplicates."
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        warn_on_duplicate_tools=False,
    )

    async def _run(fn, /, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    @mcp.tool(description="Sanity check. Returns 'pong' plus a server timestamp.")
    async def ping() -> dict[str, Any]:
        from datetime import datetime, timezone
        return {"status": "pong", "server": name, "time": datetime.now(timezone.utc).isoformat()}

    @mcp.tool(description=(
        "Save a structured memory item. Defaults: type='note', importance='medium', "
        "status='inbox' (human approval required before promotion to stable). "
        "Recommended types: note, idea, research, project_context, client_context, "
        "prompt, decision, task, risk."
    ))
    async def save_memory(
        title: str,
        body: str = "",
        type: str = "note",
        project: str | None = None,
        tags: list[str] | None = None,
        importance: str = "medium",
        summary: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        return await _run(
            store.save_memory,
            title=title, body=body, type=type, project=project,
            tags=tags, importance=importance, summary=summary, source=source,
            actor="mcp",
        )

    @mcp.tool(description=(
        "Full-text search across saved memory items using SQLite FTS5. "
        "Optionally filter by project slug or memory type."
    ))
    async def search_memory(
        query: str,
        project: str | None = None,
        type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        results = await _run(store.search_memory, query, project=project, type=type, limit=limit)
        return {"query": query, "count": len(results), "results": results}

    @mcp.tool(description="Fetch a single memory item by id.")
    async def get_memory(id: int) -> dict[str, Any] | None:
        return await _run(store.get_memory, int(id))

    @mcp.tool(description="List the most recently created memory items.")
    async def list_recent(limit: int = 25, status: str | None = None) -> dict[str, Any]:
        results = await _run(store.list_recent, limit, status=status)
        return {"count": len(results), "results": results}

    @mcp.tool(description=(
        "Record a decision with optional rationale and tradeoffs. Creates a "
        "linked memory item of type='decision'. Default decision status is 'proposed' "
        "and requires owner approval to become accepted/stable."
    ))
    async def record_decision(
        project: str,
        decision: str,
        rationale: str | None = None,
        tradeoffs: str | None = None,
    ) -> dict[str, Any]:
        return await _run(
            store.record_decision,
            project=project, decision=decision, rationale=rationale, tradeoffs=tradeoffs,
            actor="mcp",
        )

    @mcp.tool(description=(
        "Record an open task with optional next action and priority. "
        "Creates a linked memory item of type='task'."
    ))
    async def record_task(
        project: str,
        title: str,
        next_action: str | None = None,
        priority: str = "medium",
    ) -> dict[str, Any]:
        return await _run(
            store.record_task,
            project=project, title=title, next_action=next_action, priority=priority,
            actor="mcp",
        )

    @mcp.tool(description=(
        "Append text to an existing memory item's body. Never overwrites; "
        "always appends with a timestamp marker."
    ))
    async def append_memory(id: int, text: str) -> dict[str, Any]:
        return await _run(store.append_memory, int(id), text, actor="mcp")

    return mcp


__all__ = ["build_server"]
