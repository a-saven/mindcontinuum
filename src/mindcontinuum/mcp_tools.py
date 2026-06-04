"""MCP tool surface.

Wraps :class:`MemoryStore` in a small, safe MCP tool set. The tools are the
ONLY way AI clients can write or read MindContinuum memory.

Explicitly excluded from the MVP MCP surface:
    * raw SQL execution
    * destructive delete (only ``archive`` exists, and only via UI/REST)
    * schema mutation
    * bulk overwrite
    * arbitrary filesystem access
    * promote-to-stable (UI-only)
    * merge / supersede (UI-only)

Personal namespace gating: by default, AI clients can only read/write the
``work`` namespace. Setting the env var ``MINDCONTINUUM_ALLOW_PERSONAL_MCP=1``
(or ``yes``/``true``) opens personal access for the running server.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core import MemoryStore, VALID_NAMESPACE, USER_LINK_TYPES

_ALLOWED_FLAGS = {"1", "true", "yes", "on"}
_PERSONAL_REJECT_MSG = (
    "Personal namespace writes are disabled for MCP. Use the MindContinuum "
    "UI to add personal items, or set MINDCONTINUUM_ALLOW_PERSONAL_MCP=1 on "
    "the MCP server."
)
_PERSONAL_WARN_MSG = (
    "personal namespace hidden; set MINDCONTINUUM_ALLOW_PERSONAL_MCP=1 on the "
    "MCP server to expose"
)


def personal_allowed() -> bool:
    return os.environ.get("MINDCONTINUUM_ALLOW_PERSONAL_MCP", "").strip().lower() in _ALLOWED_FLAGS


def _resolve_ns(requested: str | None) -> tuple[str, str | None]:
    """Return (effective_namespace, warning_or_None)."""
    if not requested:
        return "work", None
    if requested not in VALID_NAMESPACE:
        raise ValueError(f"namespace must be one of {sorted(VALID_NAMESPACE)}")
    if requested == "personal" and not personal_allowed():
        return "work", _PERSONAL_WARN_MSG
    return requested, None


def build_server(store: MemoryStore, *, name: str = "MindContinuum") -> FastMCP:
    """Build a FastMCP server bound to a given memory store."""
    mcp = FastMCP(
        name=name,
        instructions=(
            "MindContinuum is a local long-term memory store. Use save_memory "
            "for new captures, record_decision for decisions, and record_task "
            "for tasks. All new items default to status='inbox' and require "
            "human approval before being promoted to stable. Use search_memory "
            "before saving to avoid duplicates. Use suggest_duplicates to "
            "surface similar items. Use mark_contradiction when new info "
            "conflicts with existing — NEVER overwrite stable memory. Use "
            "propose_stable to recommend promotion; only the user can actually "
            "promote. Personal namespace is read-only unless explicitly "
            "enabled by the server operator."
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
        return {
            "status": "pong",
            "server": name,
            "time": datetime.now(timezone.utc).isoformat(),
            "personal_writes_enabled": personal_allowed(),
        }

    @mcp.tool(description=(
        "Save a structured memory item. Defaults: type='note', importance='medium', "
        "status='inbox' (human approval required before promotion to stable). "
        "Recommended types: note, idea, research, project_context, client_context, "
        "prompt, decision, task, risk. Namespace defaults to 'work'; 'personal' "
        "writes are blocked unless the server operator enables them."
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
        namespace: str = "work",
    ) -> dict[str, Any]:
        if namespace == "personal" and not personal_allowed():
            raise PermissionError(_PERSONAL_REJECT_MSG)
        return await _run(
            store.save_memory,
            title=title, body=body, type=type, project=project,
            tags=tags, importance=importance, summary=summary, source=source,
            namespace=namespace, actor="mcp",
        )

    @mcp.tool(description=(
        "Search saved memory. mode='auto' picks hybrid if embeddings are "
        "available, else keyword. Other modes: 'keyword' (FTS5 only), "
        "'semantic' (cosine only), 'hybrid' (weighted blend). Filter by "
        "project slug or memory type. Personal namespace is hidden unless "
        "the server operator enables MCP personal access."
    ))
    async def search_memory(
        query: str,
        project: str | None = None,
        type: str | None = None,
        limit: int = 20,
        mode: str = "auto",
        alpha: float = 0.5,
        namespace: str = "work",
    ) -> dict[str, Any]:
        ns, warn = _resolve_ns(namespace)
        results = await _run(
            store.search_memory, query, project=project, type=type,
            limit=limit, mode=mode, alpha=alpha, namespace=ns,
        )
        out: dict[str, Any] = {"query": query, "mode": mode, "count": len(results), "results": results}
        if warn:
            out["_warning"] = warn
        return out

    @mcp.tool(description="Fetch a single memory item by id (with links and contradictions).")
    async def get_memory(id: int, namespace: str = "work") -> dict[str, Any] | None:
        ns, _ = _resolve_ns(namespace)
        return await _run(store.get_memory, int(id), namespace=ns)

    @mcp.tool(description="List the most recently created memory items.")
    async def list_recent(
        limit: int = 25, status: str | None = None, namespace: str = "work"
    ) -> dict[str, Any]:
        ns, warn = _resolve_ns(namespace)
        results = await _run(store.list_recent, limit, status=status, namespace=ns)
        out: dict[str, Any] = {"count": len(results), "results": results}
        if warn:
            out["_warning"] = warn
        return out

    @mcp.tool(description=(
        "Record a decision with optional rationale and tradeoffs. Creates a "
        "linked memory item of type='decision'. Default decision status is "
        "'proposed' and requires user approval to be accepted."
    ))
    async def record_decision(
        project: str,
        decision: str,
        rationale: str | None = None,
        tradeoffs: str | None = None,
        namespace: str = "work",
    ) -> dict[str, Any]:
        if namespace == "personal" and not personal_allowed():
            raise PermissionError(_PERSONAL_REJECT_MSG)
        return await _run(
            store.record_decision,
            project=project, decision=decision, rationale=rationale, tradeoffs=tradeoffs,
            namespace=namespace, actor="mcp",
        )

    @mcp.tool(description=(
        "Record an open task with optional next action and priority. Creates "
        "a linked memory item of type='task'."
    ))
    async def record_task(
        project: str,
        title: str,
        next_action: str | None = None,
        priority: str = "medium",
        namespace: str = "work",
    ) -> dict[str, Any]:
        if namespace == "personal" and not personal_allowed():
            raise PermissionError(_PERSONAL_REJECT_MSG)
        return await _run(
            store.record_task,
            project=project, title=title, next_action=next_action, priority=priority,
            namespace=namespace, actor="mcp",
        )

    @mcp.tool(description=(
        "Append text to an existing memory item's body. Never overwrites; "
        "always appends with a timestamp marker. Allowed on stable items."
    ))
    async def append_memory(id: int, text: str) -> dict[str, Any]:
        return await _run(store.append_memory, int(id), text, actor="mcp")

    @mcp.tool(description=(
        "Propose that a memory item be promoted to stable. Sets status to "
        "'proposed_stable' and writes an event. Only the user can finally "
        "promote to 'stable' via the UI."
    ))
    async def propose_stable(id: int, reason: str | None = None) -> dict[str, Any]:
        return await _run(store.propose_stable, int(id), reason=reason, actor="mcp")

    @mcp.tool(description=(
        "Mark a contradiction between a new memory and an existing one. "
        "Creates a 'contradicts' link but does NOT change either item's "
        "status. The user resolves contradictions via the UI."
    ))
    async def mark_contradiction(
        new_id: int, existing_id: int, note: str | None = None
    ) -> dict[str, Any]:
        return await _run(
            store.mark_contradiction, int(new_id), int(existing_id), note=note, actor="mcp",
        )

    @mcp.tool(description=(
        "Suggest possible duplicates of an existing memory item. Uses simple "
        "heuristics (title token overlap, tag overlap, shared project). "
        "Does NOT merge anything; merging is UI-only."
    ))
    async def suggest_duplicates(id: int, limit: int = 5) -> dict[str, Any]:
        results = await _run(store.suggest_duplicates, int(id), limit=limit)
        return {"id": int(id), "count": len(results), "candidates": results}

    @mcp.tool(description=(
        "Link two memory items. Allowed link types: 'related', 'supersedes', "
        "'derived_from'. Use mark_contradiction for 'contradicts'; merging "
        "duplicates is UI-only."
    ))
    async def link_memories(
        from_id: int, to_id: int, link_type: str = "related", note: str | None = None,
    ) -> dict[str, Any]:
        return await _run(
            store.link_memories, int(from_id), int(to_id), link_type, note=note, actor="mcp",
        )

    @mcp.tool(description=(
        "Get a compact, paste-friendly context pack for a project: "
        "metadata, accepted decisions, open tasks, active risks, and stable "
        "context. Returns both structured data and a Markdown rendering."
    ))
    async def get_project_context_pack(
        project: str,
        max_items: int | None = None,
        namespace: str = "work",
    ) -> dict[str, Any]:
        ns, warn = _resolve_ns(namespace)
        kwargs: dict[str, Any] = {"namespace": ns}
        if max_items:
            kwargs.update(
                max_decisions=int(max_items),
                max_tasks=int(max_items),
                max_stable=int(max_items),
                max_risks=int(max_items),
            )
        pack = await _run(store.get_project_context_pack, project, **kwargs)
        md = await _run(store.render_project_pack_markdown, pack)
        out = {"pack": pack, "markdown": md}
        if warn:
            out["_warning"] = warn
        return out

    return mcp


__all__ = ["build_server", "personal_allowed"]
