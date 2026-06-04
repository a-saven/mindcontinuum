"""FastAPI REST routes for the MindContinuum dashboard."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from .core import (
    MemoryStore, VALID_IMPORTANCE, VALID_PRIORITY, VALID_STATUS, VALID_TYPES,
)


class MemoryCreate(BaseModel):
    title: str = Field(..., min_length=1)
    body: str = ""
    summary: str | None = None
    type: str = "note"
    project: str | None = None
    tags: list[str] | None = None
    importance: str = "medium"
    source: str | None = None
    status: str = "inbox"


class MemoryUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    summary: str | None = None
    type: str | None = None
    project: str | None = None
    tags: list[str] | None = None
    importance: str | None = None
    status: str | None = None


class AppendBody(BaseModel):
    text: str = Field(..., min_length=1)


class DecisionCreate(BaseModel):
    project: str
    decision: str
    rationale: str | None = None
    tradeoffs: str | None = None


class TaskCreate(BaseModel):
    project: str
    title: str
    next_action: str | None = None
    priority: str = "medium"


def _store(request: Request) -> MemoryStore:
    store: MemoryStore | None = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="memory store not initialized")
    return store


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @r.get("/status")
    def status(request: Request) -> dict[str, Any]:
        settings = request.app.state.settings
        host_header = request.headers.get("host") or f"{settings.host}:{settings.port}"
        scheme = request.url.scheme
        return {
            "ok": True,
            "version": "0.1.0",
            "server_name": settings.server_name,
            "host": settings.host,
            "port": settings.port,
            "data_dir": str(settings.data_dir),
            "db_path": str(settings.db_path),
            "mcp_url_local": f"http://{settings.host}:{settings.port}{settings.mcp_mount}",
            "mcp_url_origin": f"{scheme}://{host_header}{settings.mcp_mount}",
            "enums": {
                "type": sorted(VALID_TYPES),
                "status": sorted(VALID_STATUS),
                "importance": sorted(VALID_IMPORTANCE),
                "priority": sorted(VALID_PRIORITY),
            },
        }

    @r.get("/memory")
    def list_memory(
        request: Request,
        limit: int = 50,
        status: str | None = None,
    ) -> dict[str, Any]:
        items = _store(request).list_recent(limit=limit, status=status)
        return {"count": len(items), "items": items}

    @r.post("/memory")
    def create_memory(request: Request, body: MemoryCreate) -> dict[str, Any]:
        try:
            return _store(request).save_memory(
                title=body.title, body=body.body, summary=body.summary,
                type=body.type, project=body.project, tags=body.tags,
                importance=body.importance, source=body.source, status=body.status,
                actor="ui",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/memory/{memory_id}")
    def get_one(request: Request, memory_id: int) -> dict[str, Any]:
        item = _store(request).get_memory(memory_id)
        if not item:
            raise HTTPException(status_code=404, detail="not found")
        return item

    @r.patch("/memory/{memory_id}")
    def update_one(request: Request, memory_id: int, body: MemoryUpdate) -> dict[str, Any]:
        try:
            return _store(request).update_memory(
                memory_id,
                title=body.title, summary=body.summary, body=body.body,
                tags=body.tags, type=body.type, importance=body.importance,
                status=body.status, project=body.project, actor="ui",
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/memory/{memory_id}/archive")
    def archive(request: Request, memory_id: int) -> dict[str, Any]:
        try:
            return _store(request).archive_memory(memory_id, actor="ui")
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/memory/{memory_id}/append")
    def append(request: Request, memory_id: int, body: AppendBody) -> dict[str, Any]:
        try:
            return _store(request).append_memory(memory_id, body.text, actor="ui")
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/search")
    def search(
        request: Request,
        q: str,
        project: str | None = None,
        type: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        items = _store(request).search_memory(q, project=project, type=type, limit=limit)
        return {"query": q, "count": len(items), "results": items}

    @r.get("/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        return {"projects": _store(request).list_projects()}

    @r.get("/decisions")
    def list_decisions(request: Request, project: str | None = None) -> dict[str, Any]:
        return {"decisions": _store(request).list_decisions(project=project)}

    @r.post("/decisions")
    def create_decision(request: Request, body: DecisionCreate) -> dict[str, Any]:
        try:
            return _store(request).record_decision(
                project=body.project, decision=body.decision,
                rationale=body.rationale, tradeoffs=body.tradeoffs, actor="ui",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/tasks")
    def list_tasks(request: Request, project: str | None = None, status: str | None = None) -> dict[str, Any]:
        return {"tasks": _store(request).list_tasks(project=project, status=status)}

    @r.post("/tasks")
    def create_task(request: Request, body: TaskCreate) -> dict[str, Any]:
        try:
            return _store(request).record_task(
                project=body.project, title=body.title,
                next_action=body.next_action, priority=body.priority, actor="ui",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/events")
    def events(request: Request, limit: int = 50) -> dict[str, Any]:
        return {"events": _store(request).list_events(limit=limit)}

    @r.get("/export/json")
    def export_json(request: Request) -> Response:
        payload = _store(request).export_all()
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=mindcontinuum-export.json"},
        )

    @r.get("/export/markdown", response_class=PlainTextResponse)
    def export_md(request: Request, project: str | None = None) -> Response:
        text = _store(request).export_markdown(project=project)
        return Response(
            content=text,
            media_type="text/markdown",
            headers={"Content-Disposition": "attachment; filename=mindcontinuum-export.md"},
        )

    return r
