"""FastAPI REST routes for the MindContinuum dashboard."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from .core import (
    MemoryStore, VALID_IMPORTANCE, VALID_NAMESPACE, VALID_PRIORITY,
    VALID_STATUS, VALID_TYPES, USER_LINK_TYPES,
)
from .mcp_tools import personal_allowed


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
    namespace: str = "work"


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


class ProposeStableBody(BaseModel):
    reason: str | None = None


class DecisionCreate(BaseModel):
    project: str
    decision: str
    rationale: str | None = None
    tradeoffs: str | None = None
    namespace: str = "work"


class TaskCreate(BaseModel):
    project: str
    title: str
    next_action: str | None = None
    priority: str = "medium"
    namespace: str = "work"


class LinkBody(BaseModel):
    to_id: int
    link_type: str = "related"
    note: str | None = None


class ContradictionBody(BaseModel):
    existing_id: int
    note: str | None = None


def _store(request: Request) -> MemoryStore:
    store: MemoryStore | None = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="memory store not initialized")
    return store


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    # ---- system ----

    @r.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @r.get("/status")
    def status(request: Request) -> dict[str, Any]:
        settings = request.app.state.settings
        host_header = request.headers.get("host") or f"{settings.host}:{settings.port}"
        scheme = request.url.scheme
        emb_status = _store(request).embeddings_status()
        return {
            "ok": True,
            "version": "0.2.0",
            "server_name": settings.server_name,
            "host": settings.host,
            "port": settings.port,
            "data_dir": str(settings.data_dir),
            "db_path": str(settings.db_path),
            "mcp_url_local": f"http://{settings.host}:{settings.port}{settings.mcp_mount}",
            "mcp_url_origin": f"{scheme}://{host_header}{settings.mcp_mount}",
            "personal_mcp_enabled": personal_allowed(),
            "embeddings": emb_status,
            "enums": {
                "type": sorted(VALID_TYPES),
                "status": sorted(VALID_STATUS),
                "importance": sorted(VALID_IMPORTANCE),
                "priority": sorted(VALID_PRIORITY),
                "namespace": sorted(VALID_NAMESPACE),
                "link_type": sorted(USER_LINK_TYPES),
            },
        }

    # ---- memory items ----

    @r.get("/memory")
    def list_memory(
        request: Request,
        limit: int = 50,
        status: str | None = None,
        namespace: str = "work",
    ) -> dict[str, Any]:
        items = _store(request).list_recent(limit=limit, status=status, namespace=namespace)
        return {"count": len(items), "items": items}

    @r.post("/memory")
    def create_memory(request: Request, body: MemoryCreate) -> dict[str, Any]:
        try:
            return _store(request).save_memory(
                title=body.title, body=body.body, summary=body.summary,
                type=body.type, project=body.project, tags=body.tags,
                importance=body.importance, source=body.source, status=body.status,
                namespace=body.namespace, actor="ui",
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
        except (ValueError, PermissionError) as e:
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

    # ---- stable promotion ----

    @r.post("/memory/{memory_id}/propose_stable")
    def propose_stable(request: Request, memory_id: int, body: ProposeStableBody | None = None) -> dict[str, Any]:
        try:
            return _store(request).propose_stable(
                memory_id, reason=body.reason if body else None, actor="ui",
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/memory/{memory_id}/promote_stable")
    def promote_stable(request: Request, memory_id: int) -> dict[str, Any]:
        try:
            return _store(request).promote_to_stable(memory_id, actor="ui")
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    @r.post("/memory/{memory_id}/reject_proposal")
    def reject_proposal(request: Request, memory_id: int) -> dict[str, Any]:
        try:
            return _store(request).reject_proposal(memory_id, actor="ui")
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    # ---- links, contradictions, dedup ----

    @r.post("/memory/{memory_id}/links")
    def add_link(request: Request, memory_id: int, body: LinkBody) -> dict[str, Any]:
        try:
            return _store(request).link_memories(
                memory_id, body.to_id, body.link_type, note=body.note, actor="ui",
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/memory/{memory_id}/contradicts")
    def add_contradiction(request: Request, memory_id: int, body: ContradictionBody) -> dict[str, Any]:
        try:
            return _store(request).mark_contradiction(
                memory_id, body.existing_id, note=body.note, actor="ui",
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/memory/{memory_id}/duplicates")
    def list_duplicates(request: Request, memory_id: int, limit: int = 5) -> dict[str, Any]:
        try:
            candidates = _store(request).suggest_duplicates(memory_id, limit=limit)
            return {"id": memory_id, "count": len(candidates), "candidates": candidates}
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/memory/{memory_id}/merge_into/{target_id}")
    def merge_into(request: Request, memory_id: int, target_id: int) -> dict[str, Any]:
        try:
            return _store(request).merge_into(memory_id, target_id, actor="ui")
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/memory/{memory_id}/supersede_by/{new_id}")
    def supersede(request: Request, memory_id: int, new_id: int) -> dict[str, Any]:
        try:
            return _store(request).supersede(memory_id, new_id, actor="ui")
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ---- search ----

    @r.get("/search")
    def search(
        request: Request,
        q: str,
        project: str | None = None,
        type: str | None = None,
        limit: int = 30,
        mode: str = "auto",
        alpha: float = 0.5,
        namespace: str = "work",
    ) -> dict[str, Any]:
        try:
            items = _store(request).search_memory(
                q, project=project, type=type, limit=limit,
                mode=mode, alpha=alpha, namespace=namespace,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"query": q, "mode": mode, "count": len(items), "results": items}

    # ---- projects / decisions / tasks ----

    @r.get("/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        return {"projects": _store(request).list_projects()}

    @r.get("/projects/{slug}/pack")
    def project_pack(
        request: Request, slug: str, namespace: str = "work",
    ) -> dict[str, Any]:
        try:
            return _store(request).get_project_context_pack(slug, namespace=namespace)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.get("/projects/{slug}/pack.md", response_class=PlainTextResponse)
    def project_pack_md(
        request: Request, slug: str, namespace: str = "work",
    ) -> Response:
        store = _store(request)
        try:
            pack = store.get_project_context_pack(slug, namespace=namespace)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        md = store.render_project_pack_markdown(pack)
        return Response(
            content=md, media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={slug}-pack.md"},
        )

    @r.get("/decisions")
    def list_decisions(
        request: Request, project: str | None = None, namespace: str = "work",
    ) -> dict[str, Any]:
        return {"decisions": _store(request).list_decisions(project=project, namespace=namespace)}

    @r.post("/decisions")
    def create_decision(request: Request, body: DecisionCreate) -> dict[str, Any]:
        try:
            return _store(request).record_decision(
                project=body.project, decision=body.decision,
                rationale=body.rationale, tradeoffs=body.tradeoffs,
                namespace=body.namespace, actor="ui",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/tasks")
    def list_tasks(
        request: Request, project: str | None = None, status: str | None = None,
        namespace: str = "work",
    ) -> dict[str, Any]:
        return {"tasks": _store(request).list_tasks(project=project, status=status, namespace=namespace)}

    @r.post("/tasks")
    def create_task(request: Request, body: TaskCreate) -> dict[str, Any]:
        try:
            return _store(request).record_task(
                project=body.project, title=body.title,
                next_action=body.next_action, priority=body.priority,
                namespace=body.namespace, actor="ui",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ---- events ----

    @r.get("/events")
    def events(
        request: Request, limit: int = 50, namespace: str | None = None,
    ) -> dict[str, Any]:
        return {"events": _store(request).list_events(limit=limit, namespace=namespace)}

    # ---- export ----

    @r.get("/export/json")
    def export_json(request: Request, namespace: str | None = None) -> Response:
        payload = _store(request).export_all(namespace=namespace)
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=mindcontinuum-export.json"},
        )

    @r.get("/export/markdown", response_class=PlainTextResponse)
    def export_md(
        request: Request, project: str | None = None, namespace: str = "work",
    ) -> Response:
        text = _store(request).export_markdown(project=project, namespace=namespace)
        return Response(
            content=text,
            media_type="text/markdown",
            headers={"Content-Disposition": "attachment; filename=mindcontinuum-export.md"},
        )

    # ---- import ----

    @r.post("/import/json")
    async def import_json_route(
        request: Request,
        file: UploadFile | None = File(None),
    ) -> dict[str, Any]:
        store = _store(request)
        if file is not None:
            raw = await file.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e
        else:
            try:
                payload = await request.json()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e
        try:
            return store.import_json(payload, actor="ui")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/import/markdown")
    async def import_markdown_route(
        request: Request,
        file: UploadFile | None = File(None),
    ) -> dict[str, Any]:
        store = _store(request)
        if file is not None:
            text = (await file.read()).decode("utf-8")
        else:
            text = (await request.body()).decode("utf-8")
        return store.import_markdown(text, actor="ui")

    # ---- embeddings ----

    @r.get("/embeddings/status")
    def embeddings_status(request: Request) -> dict[str, Any]:
        return _store(request).embeddings_status()

    @r.post("/embeddings/reindex")
    def embeddings_reindex(request: Request) -> dict[str, Any]:
        return _store(request).reindex_embeddings()

    return r
