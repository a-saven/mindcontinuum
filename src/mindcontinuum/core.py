"""Safe memory operations.

This module is the ONLY surface the MCP and HTTP layers should use to read and
write the database. It enforces:

  * input validation (allowed enum values)
  * structured event logging on every write
  * NO destructive deletes
  * NO raw SQL execution surface
  * Source-of-truth guard: stable memory body/title cannot be modified by
    agents — only appended to or promoted/superseded explicitly.

Anything that would expose raw SQL or destructive operations belongs OUTSIDE
this module and is intentionally absent from MVP.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import embeddings as _emb
from .db import connect, init_db, transaction

VALID_TYPES = {
    "note", "idea", "research", "project_context", "client_context",
    "prompt", "decision", "task", "risk",
}
VALID_STATUS = {
    "inbox", "processed", "proposed_stable", "stable", "pinned",
    "archived", "stale", "rejected",
}
VALID_IMPORTANCE = {"low", "medium", "high"}
VALID_PRIORITY = {"low", "medium", "high", "urgent"}
VALID_DECISION_STATUS = {"proposed", "accepted", "superseded", "rejected"}
VALID_TASK_STATUS = {"open", "in_progress", "blocked", "done", "cancelled"}
VALID_LINK_TYPES = {"related", "contradicts", "supersedes", "derived_from", "duplicate_of"}
USER_LINK_TYPES = {"related", "supersedes", "derived_from"}  # safe for free link_memories
VALID_NAMESPACE = {"work", "personal"}

MAX_TITLE = 500
MAX_SUMMARY = 4_000
MAX_BODY = 200_000
MAX_TAG = 80
MAX_TAGS = 32

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-") or "project"


def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
    if not tags:
        return []
    seen: list[str] = []
    for raw in tags:
        if not isinstance(raw, str):
            continue
        t = raw.strip().lower()
        if not t or len(t) > MAX_TAG:
            continue
        if t not in seen:
            seen.append(t)
        if len(seen) >= MAX_TAGS:
            break
    return seen


def _require(name: str, value: str | None, allowed: set[str]) -> str:
    v = (value or "").strip()
    if v not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return v


def _validate_title(value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ValueError("title is required")
    if len(v) > MAX_TITLE:
        raise ValueError(f"title exceeds {MAX_TITLE} chars")
    return v


def _validate_body(value: str | None) -> str:
    v = value or ""
    if len(v) > MAX_BODY:
        raise ValueError(f"body exceeds {MAX_BODY} chars")
    return v


def _validate_summary(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if len(v) > MAX_SUMMARY:
        raise ValueError(f"summary exceeds {MAX_SUMMARY} chars")
    return v or None


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if "tags_json" in d and isinstance(d["tags_json"], str):
        try:
            d["tags"] = json.loads(d["tags_json"])
        except Exception:
            d["tags"] = []
    return d


def _log_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    actor: str,
    target_kind: str | None = None,
    target_id: int | None = None,
    payload: dict[str, Any] | None = None,
    namespace: str = "work",
) -> None:
    conn.execute(
        "INSERT INTO events_log (action, actor, target_kind, target_id, payload, namespace) "
        "VALUES (?,?,?,?,?,?)",
        (action, actor, target_kind, target_id,
         json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
         namespace),
    )


def _is_agent(actor: str | None) -> bool:
    a = (actor or "").lower()
    return a == "mcp" or a.startswith("mcp:") or a == "import"


# FTS5 reserves a handful of characters; escape user queries safely.
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|\".*?\"")


def _fts_escape(q: str) -> str:
    raw_tokens = _FTS_TOKEN_RE.findall(q)
    if not raw_tokens:
        return '""'
    quoted: list[str] = []
    for tok in raw_tokens:
        t = tok.strip('"').replace('"', '""').strip()
        if t:
            quoted.append(f'"{t}"')
    return " ".join(quoted) if quoted else '""'


SEMANTIC_FLOOR = 0.55  # minimum cosine for bge-small to count as "relevant".


def _minmax_norm(d: dict[int, float]) -> dict[int, float]:
    if not d:
        return {}
    vals = list(d.values())
    lo, hi = min(vals), max(vals)
    rng = hi - lo or 1.0
    return {k: (v - lo) / rng for k, v in d.items()}


@dataclass
class MemoryStore:
    """Connected, schema-initialized memory store."""

    db_path: Path
    actor_default: str = "system"
    enable_embeddings: bool = True
    _conn: sqlite3.Connection = field(init=False)

    def __post_init__(self) -> None:
        init_db(self.db_path)
        self._conn = connect(self.db_path)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ------- projects -------

    def get_or_create_project(self, name_or_slug: str | None, *, actor: str | None = None) -> int | None:
        if not name_or_slug:
            return None
        slug = _slugify(name_or_slug)
        cur = self._conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
        with transaction(self._conn) as conn:
            cur = conn.execute(
                "INSERT INTO projects (slug, name) VALUES (?, ?)",
                (slug, name_or_slug.strip()),
            )
            project_id = int(cur.lastrowid)
            _log_event(
                conn, action="project.create", actor=actor or self.actor_default,
                target_kind="project", target_id=project_id,
                payload={"slug": slug, "name": name_or_slug.strip()},
            )
        return project_id

    def list_projects(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT id, slug, name, description, created_at FROM projects ORDER BY name"
        )
        return [dict(r) for r in cur.fetchall()]

    def get_project(self, slug: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT id, slug, name, description, created_at FROM projects WHERE slug = ?",
            (_slugify(slug),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    # ------- memory items -------

    def save_memory(
        self,
        *,
        title: str,
        body: str = "",
        type: str = "note",
        summary: str | None = None,
        project: str | None = None,
        tags: Iterable[str] | None = None,
        importance: str = "medium",
        source: str | None = None,
        status: str = "inbox",
        namespace: str = "work",
        actor: str | None = None,
    ) -> dict[str, Any]:
        t = _validate_title(title)
        b = _validate_body(body)
        s = _validate_summary(summary)
        typ = _require("type", type, VALID_TYPES)
        imp = _require("importance", importance, VALID_IMPORTANCE)
        st = _require("status", status, VALID_STATUS)
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        if st == "stable" and _is_agent(actor):
            raise PermissionError(
                "agents may not create items directly as stable; use status='inbox' "
                "and propose_stable instead"
            )
        tags_list = _normalize_tags(tags)
        project_id = self.get_or_create_project(project, actor=actor) if project else None

        with transaction(self._conn) as conn:
            cur = conn.execute(
                """INSERT INTO memory_items
                   (title, type, summary, body, project_id, source, tags_json, importance,
                    status, namespace)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (t, typ, s, b, project_id, source, json.dumps(tags_list), imp, st, ns),
            )
            new_id = int(cur.lastrowid)
            _log_event(
                conn, action="memory.create", actor=actor or self.actor_default,
                target_kind="memory_item", target_id=new_id, namespace=ns,
                payload={"title": t, "type": typ, "project": project, "tags": tags_list, "status": st},
            )
        if self.enable_embeddings:
            _emb.index_memory(self._conn, new_id, self._embed_text(t, s, b))
        return self.get_memory(new_id, namespace=ns)  # type: ignore[return-value]

    @staticmethod
    def _embed_text(title: str, summary: str | None, body: str) -> str:
        parts = [title.strip()]
        if summary:
            parts.append(summary.strip())
        if body:
            parts.append(body.strip())
        return "\n\n".join(parts)[:8000]

    def get_memory(
        self, memory_id: int, *, namespace: str | None = None, enrich: bool = True
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
            "WHERE m.id = ?"
        )
        params: list[Any] = [int(memory_id)]
        if namespace is not None:
            ns = _require("namespace", namespace, VALID_NAMESPACE)
            sql += " AND m.namespace = ?"
            params.append(ns)
        row = self._conn.execute(sql, params).fetchone()
        item = _row_to_dict(row)
        if item is None:
            return None
        if enrich:
            item["links"] = self._links_for(int(memory_id), parent_namespace=item["namespace"])
            item["contradictions"] = [
                lk for lk in item["links"] if lk["link_type"] == "contradicts"
            ]
        return item

    def _links_for(
        self, memory_id: int, *, parent_namespace: str | None = None
    ) -> list[dict[str, Any]]:
        """Return links touching ``memory_id``.

        When ``parent_namespace`` is given, the result is filtered so that
        only links to items in the same namespace are returned. This is a
        defensive measure on top of the same-namespace check at creation —
        if a legacy DB ever contained cross-namespace links (e.g. from an
        older release or a hand-edited DB), they will not leak through here.
        """
        sql = (
            "SELECT l.id AS link_id, l.from_id, l.to_id, l.link_type, l.note, l.created_at, "
            "       m.id AS other_id, m.title AS other_title, m.status AS other_status, "
            "       m.namespace AS other_namespace, "
            "       CASE WHEN l.from_id = ? THEN 'out' ELSE 'in' END AS direction "
            "FROM memory_links l "
            "JOIN memory_items m "
            "  ON m.id = CASE WHEN l.from_id = ? THEN l.to_id ELSE l.from_id END "
            "WHERE (l.from_id = ? OR l.to_id = ?) "
        )
        params: list[Any] = [memory_id, memory_id, memory_id, memory_id]
        if parent_namespace is not None:
            sql += "AND m.namespace = ? "
            params.append(parent_namespace)
        sql += "ORDER BY l.created_at DESC"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _require_same_namespace(a: dict[str, Any], b: dict[str, Any], op: str) -> None:
        if a["namespace"] != b["namespace"]:
            raise ValueError(
                f"{op} across namespaces is not allowed "
                f"(got {a['namespace']!r} and {b['namespace']!r})"
            )

    def list_recent(
        self,
        limit: int = 25,
        *,
        status: str | None = None,
        namespace: str = "work",
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 25), 200))
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
            "WHERE m.namespace = ? "
        )
        params: list[Any] = [ns]
        if status:
            _require("status", status, VALID_STATUS)
            sql += "AND m.status = ? "
            params.append(status)
        sql += "ORDER BY m.created_at DESC, m.id DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [_row_to_dict(r) for r in cur.fetchall()]  # type: ignore[list-item]

    def search_memory(
        self,
        query: str,
        *,
        project: str | None = None,
        type: str | None = None,
        limit: int = 20,
        mode: str = "auto",
        alpha: float = 0.5,
        namespace: str = "work",
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        limit = max(1, min(int(limit or 20), 100))
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        if mode == "auto":
            mode = "hybrid" if _emb.embeddings_available() else "keyword"
        if mode not in {"keyword", "semantic", "hybrid"}:
            raise ValueError("mode must be 'keyword', 'semantic', 'hybrid', or 'auto'")

        bm25_scores: dict[int, float] = {}
        sem_scores: dict[int, float] = {}

        if mode in ("keyword", "hybrid"):
            bm25_scores = self._fts_scores(q, ns, project=project, type=type, limit=limit * 3)
        if mode in ("semantic", "hybrid"):
            sem_pairs = _emb.semantic_search(self._conn, q, top_k=limit * 3, namespace=ns)
            sem_scores = {mid: score for mid, score in sem_pairs}

        if mode == "keyword":
            ranked = sorted(bm25_scores.items(), key=lambda kv: kv[1])  # bm25 ascending
            ids = [mid for mid, _ in ranked[:limit]]
        elif mode == "semantic":
            # Require a minimum cosine to avoid surfacing irrelevant matches.
            ranked = [(mid, sc) for mid, sc in sem_scores.items() if sc >= SEMANTIC_FLOOR]
            ranked.sort(key=lambda kv: -kv[1])
            ids = [mid for mid, _ in ranked[:limit]]
            if not ids:
                return self._fallback_like(q, ns, limit, project=project, type=type)
        else:  # hybrid
            # Hybrid candidate set = all FTS hits ∪ semantic hits above SEMANTIC_FLOOR.
            # This prevents low-similarity items from leaking in just because
            # min-max gives them a non-zero score in a sparse candidate pool.
            sem_above = {mid: sc for mid, sc in sem_scores.items() if sc >= SEMANTIC_FLOOR}
            bm25_pos = {mid: -score for mid, score in bm25_scores.items()}
            n_bm = _minmax_norm(bm25_pos)
            n_se = _minmax_norm(sem_above)
            keys = set(n_bm) | set(n_se)
            merged = {k: alpha * n_se.get(k, 0.0) + (1 - alpha) * n_bm.get(k, 0.0) for k in keys}
            ranked = sorted(merged.items(), key=lambda kv: -kv[1])
            ids = [mid for mid, _ in ranked[:limit]]

        if not ids and mode in ("keyword", "hybrid"):
            return self._fallback_like(q, ns, limit, project=project, type=type)

        return self._hydrate(ids, project=project, type=type, namespace=ns)

    def _fts_scores(
        self,
        query: str,
        namespace: str,
        *,
        project: str | None,
        type: str | None,
        limit: int,
    ) -> dict[int, float]:
        match = _fts_escape(query)
        sql = (
            "SELECT m.id AS id, bm25(memory_items_fts) AS rank "
            "FROM memory_items_fts f "
            "JOIN memory_items m ON m.id = f.rowid "
            "LEFT JOIN projects p ON p.id = m.project_id "
            "WHERE memory_items_fts MATCH ? AND m.namespace = ? "
        )
        params: list[Any] = [match, namespace]
        if project:
            sql += "AND p.slug = ? "
            params.append(_slugify(project))
        if type:
            _require("type", type, VALID_TYPES)
            sql += "AND m.type = ? "
            params.append(type)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {int(r["id"]): float(r["rank"]) for r in rows}

    def _fallback_like(
        self,
        q: str,
        namespace: str,
        limit: int,
        *,
        project: str | None = None,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        like = f"%{q}%"
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
            "WHERE m.namespace = ? AND (m.title LIKE ? OR m.body LIKE ? OR m.summary LIKE ?) "
        )
        params: list[Any] = [namespace, like, like, like]
        if project:
            sql += "AND p.slug = ? "
            params.append(_slugify(project))
        if type:
            _require("type", type, VALID_TYPES)
            sql += "AND m.type = ? "
            params.append(type)
        sql += "ORDER BY m.created_at DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [_row_to_dict(r) for r in cur.fetchall()]  # type: ignore[list-item]

    def _hydrate(
        self,
        ids: list[int],
        *,
        project: str | None = None,
        type: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        qmarks = ",".join(["?"] * len(ids))
        sql = (
            f"SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            f"FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
            f"WHERE m.id IN ({qmarks}) "
        )
        params: list[Any] = list(ids)
        if namespace is not None:
            sql += "AND m.namespace = ? "
            params.append(namespace)
        if project:
            sql += "AND p.slug = ? "
            params.append(_slugify(project))
        if type:
            sql += "AND m.type = ? "
            params.append(type)
        rows = self._conn.execute(sql, params).fetchall()
        index = {int(r["id"]): _row_to_dict(r) for r in rows}
        return [index[i] for i in ids if i in index]

    def append_memory(
        self, memory_id: int, text: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        if not (text or "").strip():
            raise ValueError("append text is required")
        existing = self.get_memory(memory_id, enrich=False)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")
        ns = existing["namespace"]
        new_body = (existing["body"] or "")
        if new_body and not new_body.endswith("\n"):
            new_body += "\n"
        new_body += f"\n--- append @ {_utcnow()} ---\n{text}".rstrip()
        new_body = _validate_body(new_body)
        with transaction(self._conn) as conn:
            conn.execute(
                "UPDATE memory_items SET body = ?, updated_at = ? WHERE id = ?",
                (new_body, _utcnow(), int(memory_id)),
            )
            _log_event(
                conn, action="memory.append", actor=actor or self.actor_default,
                target_kind="memory_item", target_id=int(memory_id), namespace=ns,
                payload={"appended_chars": len(text)},
            )
        if self.enable_embeddings:
            _emb.index_memory(
                self._conn, int(memory_id),
                self._embed_text(existing["title"], existing.get("summary"), new_body),
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def update_memory(
        self,
        memory_id: int,
        *,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        tags: Iterable[str] | None = None,
        type: str | None = None,
        importance: str | None = None,
        status: str | None = None,
        project: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_memory(memory_id, enrich=False)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")

        # Source-of-truth guard: agents cannot modify stable items' body/title.
        if existing["status"] == "stable" and _is_agent(actor):
            for k, v in (("title", title), ("summary", summary), ("body", body),
                         ("type", type), ("status", status)):
                if v is not None:
                    raise PermissionError(
                        "stable memory is immutable from MCP; use append_memory or UI"
                    )

        fields: list[str] = []
        params: list[Any] = []

        if title is not None:
            fields.append("title = ?"); params.append(_validate_title(title))
        if summary is not None:
            fields.append("summary = ?"); params.append(_validate_summary(summary))
        if body is not None:
            fields.append("body = ?"); params.append(_validate_body(body))
        if tags is not None:
            fields.append("tags_json = ?"); params.append(json.dumps(_normalize_tags(tags)))
        if type is not None:
            fields.append("type = ?"); params.append(_require("type", type, VALID_TYPES))
        if importance is not None:
            fields.append("importance = ?"); params.append(_require("importance", importance, VALID_IMPORTANCE))
        if status is not None:
            _require("status", status, VALID_STATUS)
            if status == "stable" and _is_agent(actor):
                raise PermissionError("only the UI may promote a memory to stable")
            fields.append("status = ?"); params.append(status)
        if project is not None:
            project_id = self.get_or_create_project(project, actor=actor) if project else None
            fields.append("project_id = ?"); params.append(project_id)

        if not fields:
            return existing

        fields.append("updated_at = ?")
        params.append(_utcnow())
        params.append(int(memory_id))

        with transaction(self._conn) as conn:
            conn.execute(f"UPDATE memory_items SET {', '.join(fields)} WHERE id = ?", params)
            _log_event(
                conn, action="memory.update", actor=actor or self.actor_default,
                target_kind="memory_item", target_id=int(memory_id),
                namespace=existing["namespace"],
                payload={"changed": [f.split(" = ")[0] for f in fields if f != "updated_at = ?"]},
            )
        text_changed = (title is not None) or (summary is not None) or (body is not None)
        if self.enable_embeddings and text_changed:
            fresh = self.get_memory(memory_id, enrich=False) or {}
            _emb.index_memory(
                self._conn, int(memory_id),
                self._embed_text(fresh["title"], fresh.get("summary"), fresh.get("body") or ""),
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def archive_memory(self, memory_id: int, *, actor: str | None = None) -> dict[str, Any]:
        existing = self.get_memory(memory_id, enrich=False)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")
        with transaction(self._conn) as conn:
            conn.execute(
                "UPDATE memory_items SET status='archived', updated_at=? WHERE id=?",
                (_utcnow(), int(memory_id)),
            )
            _log_event(
                conn, action="memory.archive", actor=actor or self.actor_default,
                target_kind="memory_item", target_id=int(memory_id),
                namespace=existing["namespace"],
                payload={"prev_status": existing["status"]},
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    # ------- stable / contradiction / link workflow -------

    def propose_stable(
        self, memory_id: int, *, reason: str | None = None, actor: str | None = None
    ) -> dict[str, Any]:
        existing = self.get_memory(memory_id, enrich=False)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")
        if existing["status"] in {"stable", "archived", "rejected"}:
            raise ValueError(
                f"cannot propose stable from status='{existing['status']}'"
            )
        with transaction(self._conn) as conn:
            conn.execute(
                "UPDATE memory_items SET status='proposed_stable', updated_at=? WHERE id=?",
                (_utcnow(), int(memory_id)),
            )
            _log_event(
                conn, action="memory.propose_stable", actor=actor or self.actor_default,
                target_kind="memory_item", target_id=int(memory_id),
                namespace=existing["namespace"],
                payload={"prev_status": existing["status"], "reason": reason},
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def promote_to_stable(self, memory_id: int, *, actor: str = "ui") -> dict[str, Any]:
        if _is_agent(actor):
            raise PermissionError("only the UI/user may promote a memory to stable")
        existing = self.get_memory(memory_id, enrich=False)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")
        with transaction(self._conn) as conn:
            conn.execute(
                "UPDATE memory_items SET status='stable', updated_at=? WHERE id=?",
                (_utcnow(), int(memory_id)),
            )
            _log_event(
                conn, action="memory.promote_stable", actor=actor,
                target_kind="memory_item", target_id=int(memory_id),
                namespace=existing["namespace"],
                payload={"prev_status": existing["status"]},
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def reject_proposal(self, memory_id: int, *, actor: str = "ui") -> dict[str, Any]:
        if _is_agent(actor):
            raise PermissionError("only the UI/user may reject a stable proposal")
        existing = self.get_memory(memory_id, enrich=False)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")
        with transaction(self._conn) as conn:
            conn.execute(
                "UPDATE memory_items SET status='processed', updated_at=? WHERE id=?",
                (_utcnow(), int(memory_id)),
            )
            _log_event(
                conn, action="memory.reject_proposal", actor=actor,
                target_kind="memory_item", target_id=int(memory_id),
                namespace=existing["namespace"],
                payload={"prev_status": existing["status"]},
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def mark_contradiction(
        self,
        new_memory_id: int,
        existing_memory_id: int,
        *,
        note: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if new_memory_id == existing_memory_id:
            raise ValueError("a memory cannot contradict itself")
        new_item = self.get_memory(new_memory_id, enrich=False)
        existing_item = self.get_memory(existing_memory_id, enrich=False)
        if not new_item or not existing_item:
            raise LookupError("both memories must exist")
        self._require_same_namespace(new_item, existing_item, "mark_contradiction")
        with transaction(self._conn) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memory_links (from_id, to_id, link_type, note) "
                "VALUES (?,?, 'contradicts', ?)",
                (int(new_memory_id), int(existing_memory_id), note),
            )
            link_id = int(cur.lastrowid or 0)
            _log_event(
                conn, action="memory.mark_contradiction",
                actor=actor or self.actor_default,
                target_kind="memory_link", target_id=link_id or None,
                namespace=new_item["namespace"],
                payload={"from": int(new_memory_id), "to": int(existing_memory_id), "note": note},
            )
        return {"link_id": link_id, "from_id": new_memory_id, "to_id": existing_memory_id, "link_type": "contradicts"}

    def link_memories(
        self,
        from_id: int,
        to_id: int,
        link_type: str = "related",
        *,
        note: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if from_id == to_id:
            raise ValueError("from_id and to_id must differ")
        lt = _require("link_type", link_type, USER_LINK_TYPES)
        a = self.get_memory(from_id, enrich=False)
        b = self.get_memory(to_id, enrich=False)
        if not a or not b:
            raise LookupError("both memories must exist")
        self._require_same_namespace(a, b, "link_memories")
        with transaction(self._conn) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memory_links (from_id, to_id, link_type, note) "
                "VALUES (?,?,?,?)",
                (int(from_id), int(to_id), lt, note),
            )
            link_id = int(cur.lastrowid or 0)
            _log_event(
                conn, action="memory.link", actor=actor or self.actor_default,
                target_kind="memory_link", target_id=link_id or None,
                namespace=a["namespace"],
                payload={"from": int(from_id), "to": int(to_id), "type": lt, "note": note},
            )
        return {"link_id": link_id, "from_id": from_id, "to_id": to_id, "link_type": lt}

    def supersede(
        self, old_id: int, new_id: int, *, actor: str = "ui"
    ) -> dict[str, Any]:
        if _is_agent(actor):
            raise PermissionError("only the UI/user may supersede an existing memory")
        if old_id == new_id:
            raise ValueError("old_id and new_id must differ")
        old = self.get_memory(old_id, enrich=False)
        new = self.get_memory(new_id, enrich=False)
        if not old or not new:
            raise LookupError("both memories must exist")
        self._require_same_namespace(old, new, "supersede")
        with transaction(self._conn) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_links (from_id, to_id, link_type) "
                "VALUES (?,?, 'supersedes')",
                (int(new_id), int(old_id)),
            )
            conn.execute(
                "UPDATE memory_items SET status='stale', updated_at=? WHERE id=?",
                (_utcnow(), int(old_id)),
            )
            _log_event(
                conn, action="memory.supersede", actor=actor,
                target_kind="memory_item", target_id=int(old_id),
                namespace=old["namespace"],
                payload={"new_id": int(new_id), "old_id": int(old_id)},
            )
        return {"old_id": old_id, "new_id": new_id, "old_status": "stale"}

    def suggest_duplicates(self, memory_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
        item = self.get_memory(memory_id, enrich=False)
        if not item:
            raise LookupError(f"memory {memory_id} not found")
        ns = item["namespace"]
        title_tokens = {w for w in re.findall(r"[a-z0-9]+", (item["title"] or "").lower()) if len(w) > 2}
        item_tags = set(item.get("tags") or [])
        project_id = item.get("project_id")

        # Pull candidate ids from FTS by title tokens (cheap pre-filter).
        # Sort tokens for deterministic MATCH expressions across runs.
        candidates: list[dict[str, Any]] = []
        if title_tokens:
            match = " OR ".join(f'"{t}"' for t in sorted(title_tokens)[:10])
            try:
                rows = self._conn.execute(
                    "SELECT m.id, m.title, m.tags_json, m.project_id, m.status, m.namespace "
                    "FROM memory_items_fts f JOIN memory_items m ON m.id = f.rowid "
                    "WHERE memory_items_fts MATCH ? AND m.id != ? AND m.namespace = ? "
                    "LIMIT 200",
                    (match, int(memory_id), ns),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            candidates = [dict(r) for r in rows]
        if not candidates:
            rows = self._conn.execute(
                "SELECT id, title, tags_json, project_id, status, namespace "
                "FROM memory_items WHERE namespace = ? AND id != ? "
                "ORDER BY created_at DESC LIMIT 200",
                (ns, int(memory_id)),
            ).fetchall()
            candidates = [dict(r) for r in rows]

        scored: list[dict[str, Any]] = []
        for c in candidates:
            c_tokens = {w for w in re.findall(r"[a-z0-9]+", (c["title"] or "").lower()) if len(w) > 2}
            if title_tokens and c_tokens:
                jacc = len(title_tokens & c_tokens) / max(1, len(title_tokens | c_tokens))
            else:
                jacc = 0.0
            try:
                c_tags = set(json.loads(c["tags_json"] or "[]"))
            except Exception:
                c_tags = set()
            if item_tags and c_tags:
                tag_overlap = len(item_tags & c_tags) / max(1, len(item_tags | c_tags))
            else:
                tag_overlap = 0.0
            same_proj = 1.0 if (project_id and c["project_id"] == project_id) else 0.0
            score = 0.5 * jacc + 0.3 * tag_overlap + 0.2 * same_proj
            reasons: list[str] = []
            if jacc > 0.3:
                reasons.append("title-overlap")
            if tag_overlap > 0.3:
                reasons.append("tag-overlap")
            if same_proj:
                reasons.append("same-project")
            if score >= 0.45:
                scored.append({
                    "id": int(c["id"]),
                    "title": c["title"],
                    "status": c["status"],
                    "score": round(score, 4),
                    "reasons": reasons,
                })
        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]

    def merge_into(
        self, source_id: int, target_id: int, *, actor: str = "ui"
    ) -> dict[str, Any]:
        if _is_agent(actor):
            raise PermissionError("only the UI/user may merge duplicates")
        if source_id == target_id:
            raise ValueError("source and target must differ")
        src = self.get_memory(source_id, enrich=False)
        tgt = self.get_memory(target_id, enrich=False)
        if not src or not tgt:
            raise LookupError("both memories must exist")
        self._require_same_namespace(src, tgt, "merge_into")
        with transaction(self._conn) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_links (from_id, to_id, link_type) "
                "VALUES (?,?, 'duplicate_of')",
                (int(source_id), int(target_id)),
            )
            conn.execute(
                "UPDATE memory_items SET status='archived', updated_at=? WHERE id=?",
                (_utcnow(), int(source_id)),
            )
            _log_event(
                conn, action="memory.merge_into", actor=actor,
                target_kind="memory_item", target_id=int(source_id),
                namespace=src["namespace"],
                payload={"target_id": int(target_id)},
            )
        return {"source_id": source_id, "target_id": target_id, "source_status": "archived"}

    # ------- decisions -------

    def record_decision(
        self,
        *,
        project: str,
        decision: str,
        rationale: str | None = None,
        tradeoffs: str | None = None,
        status: str = "proposed",
        namespace: str = "work",
        actor: str | None = None,
    ) -> dict[str, Any]:
        if not (decision or "").strip():
            raise ValueError("decision is required")
        st = _require("status", status, VALID_DECISION_STATUS)
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        if st == "accepted" and _is_agent(actor):
            raise PermissionError("agents may not accept decisions directly")
        project_id = self.get_or_create_project(project, actor=actor)

        title = decision.strip().splitlines()[0][:MAX_TITLE]
        body_parts = [decision.strip()]
        if rationale:
            body_parts.append(f"\n### Rationale\n{rationale.strip()}")
        if tradeoffs:
            body_parts.append(f"\n### Tradeoffs\n{tradeoffs.strip()}")
        body = "\n".join(body_parts)
        body = _validate_body(body)

        with transaction(self._conn) as conn:
            cur = conn.execute(
                """INSERT INTO memory_items
                   (title, type, summary, body, project_id, status, tags_json, namespace)
                   VALUES (?, 'decision', NULL, ?, ?, 'inbox', '["decision"]', ?)""",
                (title, body, project_id, ns),
            )
            mem_id = int(cur.lastrowid)
            cur = conn.execute(
                """INSERT INTO decisions (project_id, memory_id, decision, rationale, tradeoffs, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, mem_id, decision.strip(), rationale, tradeoffs, st),
            )
            decision_id = int(cur.lastrowid)
            _log_event(
                conn, action="decision.record", actor=actor or self.actor_default,
                target_kind="decision", target_id=decision_id, namespace=ns,
                payload={"project": project, "status": st, "memory_id": mem_id},
            )
        if self.enable_embeddings:
            _emb.index_memory(self._conn, mem_id, self._embed_text(title, None, body))
        return {
            "decision_id": decision_id,
            "memory_id": mem_id,
            "project_id": project_id,
            "status": st,
            "decision": decision.strip(),
            "rationale": rationale,
            "tradeoffs": tradeoffs,
        }

    def list_decisions(
        self, *, project: str | None = None, namespace: str = "work"
    ) -> list[dict[str, Any]]:
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        sql = (
            "SELECT d.*, p.slug AS project_slug, p.name AS project_name "
            "FROM decisions d "
            "LEFT JOIN projects p ON p.id = d.project_id "
            "LEFT JOIN memory_items m ON m.id = d.memory_id "
            "WHERE (m.namespace = ? OR m.namespace IS NULL) "
        )
        params: list[Any] = [ns]
        if project:
            sql += "AND p.slug = ? "
            params.append(_slugify(project))
        sql += "ORDER BY d.created_at DESC LIMIT 200"
        cur = self._conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # ------- tasks -------

    def record_task(
        self,
        *,
        project: str,
        title: str,
        next_action: str | None = None,
        priority: str = "medium",
        status: str = "open",
        namespace: str = "work",
        actor: str | None = None,
    ) -> dict[str, Any]:
        t = _validate_title(title)
        pri = _require("priority", priority, VALID_PRIORITY)
        st = _require("status", status, VALID_TASK_STATUS)
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        project_id = self.get_or_create_project(project, actor=actor)

        body_parts = [t]
        if next_action:
            body_parts.append(f"\n### Next action\n{next_action.strip()}")
        body = _validate_body("\n".join(body_parts))

        with transaction(self._conn) as conn:
            cur = conn.execute(
                """INSERT INTO memory_items
                   (title, type, summary, body, project_id, status, tags_json, importance, namespace)
                   VALUES (?, 'task', NULL, ?, ?, 'inbox', '["task"]', ?, ?)""",
                (t, body, project_id, "high" if pri in ("high", "urgent") else "medium", ns),
            )
            mem_id = int(cur.lastrowid)
            cur = conn.execute(
                """INSERT INTO tasks (project_id, memory_id, title, next_action, priority, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, mem_id, t, next_action, pri, st),
            )
            task_id = int(cur.lastrowid)
            _log_event(
                conn, action="task.record", actor=actor or self.actor_default,
                target_kind="task", target_id=task_id, namespace=ns,
                payload={"project": project, "priority": pri, "status": st, "memory_id": mem_id},
            )
        if self.enable_embeddings:
            _emb.index_memory(self._conn, mem_id, self._embed_text(t, None, body))
        return {
            "task_id": task_id,
            "memory_id": mem_id,
            "project_id": project_id,
            "title": t,
            "next_action": next_action,
            "priority": pri,
            "status": st,
        }

    def list_tasks(
        self, *, project: str | None = None, status: str | None = None,
        namespace: str = "work",
    ) -> list[dict[str, Any]]:
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        sql = (
            "SELECT t.*, p.slug AS project_slug, p.name AS project_name "
            "FROM tasks t "
            "LEFT JOIN projects p ON p.id = t.project_id "
            "LEFT JOIN memory_items m ON m.id = t.memory_id "
            "WHERE (m.namespace = ? OR m.namespace IS NULL) "
        )
        params: list[Any] = [ns]
        clauses = []
        if project:
            clauses.append("p.slug = ?"); params.append(_slugify(project))
        if status:
            _require("status", status, VALID_TASK_STATUS)
            clauses.append("t.status = ?"); params.append(status)
        if clauses:
            sql += "AND " + " AND ".join(clauses) + " "
        sql += "ORDER BY t.created_at DESC LIMIT 200"
        cur = self._conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # ------- project context pack -------

    def get_project_context_pack(
        self,
        project: str,
        *,
        max_decisions: int = 20,
        max_tasks: int = 50,
        max_stable: int = 30,
        max_risks: int = 25,
        namespace: str = "work",
    ) -> dict[str, Any]:
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        slug = _slugify(project)
        proj = self._conn.execute(
            "SELECT id, slug, name, description, created_at FROM projects WHERE slug=?",
            (slug,),
        ).fetchone()
        if not proj:
            raise LookupError(f"project {slug!r} not found")
        decisions = [dict(r) for r in self._conn.execute(
            "SELECT d.decision, d.rationale, d.tradeoffs, d.status, d.created_at "
            "FROM decisions d JOIN projects p ON p.id=d.project_id "
            "LEFT JOIN memory_items m ON m.id=d.memory_id "
            "WHERE p.slug=? AND d.status IN ('accepted','proposed') "
            "AND (m.namespace=? OR m.namespace IS NULL) "
            "ORDER BY d.created_at DESC LIMIT ?",
            (slug, ns, max_decisions),
        ).fetchall()]
        decisions = list(reversed(decisions))
        tasks = [dict(r) for r in self._conn.execute(
            "SELECT t.title, t.next_action, t.priority, t.status "
            "FROM tasks t JOIN projects p ON p.id=t.project_id "
            "LEFT JOIN memory_items m ON m.id=t.memory_id "
            "WHERE p.slug=? AND t.status IN ('open','in_progress') "
            "AND (m.namespace=? OR m.namespace IS NULL) "
            "ORDER BY CASE t.priority "
            "  WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 ELSE 3 END, t.created_at ASC LIMIT ?",
            (slug, ns, max_tasks),
        ).fetchall()]
        risks = [dict(r) for r in self._conn.execute(
            "SELECT m.title, m.summary, m.body, m.importance, m.status "
            "FROM memory_items m JOIN projects p ON p.id=m.project_id "
            "WHERE p.slug=? AND m.type='risk' "
            "AND m.status NOT IN ('archived','rejected','stale') AND m.namespace=? "
            "ORDER BY CASE m.importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "         m.created_at DESC LIMIT ?",
            (slug, ns, max_risks),
        ).fetchall()]
        stable_items = [dict(r) for r in self._conn.execute(
            "SELECT m.id, m.title, m.summary, m.status, m.updated_at, m.tags_json "
            "FROM memory_items m JOIN projects p ON p.id=m.project_id "
            "WHERE p.slug=? AND m.status IN ('stable','pinned') AND m.namespace=? "
            "ORDER BY (m.status='pinned') DESC, m.updated_at DESC LIMIT ?",
            (slug, ns, max_stable),
        ).fetchall()]
        for s in stable_items:
            try:
                s["tags"] = json.loads(s.pop("tags_json") or "[]")
            except Exception:
                s["tags"] = []
        return {
            "project": dict(proj),
            "decisions": decisions,
            "tasks": tasks,
            "risks": risks,
            "stable_items": stable_items,
            "generated_at": _utcnow(),
        }

    def render_project_pack_markdown(self, pack: dict[str, Any]) -> str:
        p = pack["project"]
        lines: list[str] = [f"# Project: {p['name']}", ""]
        if p.get("description"):
            lines.append(p["description"]); lines.append("")
        lines.append("## Accepted Decisions"); lines.append("")
        if not pack["decisions"]:
            lines.append("_None recorded._")
        for d in pack["decisions"]:
            lines.append(f"- **{d['decision']}**")
            if d.get("rationale"):
                lines.append(f"  - Why: {d['rationale']}")
            if d.get("tradeoffs"):
                lines.append(f"  - Tradeoffs: {d['tradeoffs']}")
        lines.append("")
        lines.append("## Open Tasks"); lines.append("")
        if not pack["tasks"]:
            lines.append("_None._")
        for t in pack["tasks"]:
            line = f"- **[{t['priority']}]** {t['title']}"
            if t.get("next_action"):
                line += f" — next: {t['next_action']}"
            lines.append(line)
        lines.append("")
        lines.append("## Active Risks"); lines.append("")
        if not pack["risks"]:
            lines.append("_None active._")
        for r in pack["risks"]:
            lines.append(f"- **{r['title']}** ({r['importance']})")
            if r.get("summary"):
                lines.append(f"  - {r['summary']}")
        lines.append("")
        lines.append("## Stable Context"); lines.append("")
        if not pack["stable_items"]:
            lines.append("_No stable items yet._")
        for s in pack["stable_items"]:
            lines.append(f"- {s['title']}")
            if s.get("summary"):
                lines.append(f"  - {s['summary']}")
        lines.append("")
        lines.append(f"_Generated {pack['generated_at']}_")
        return "\n".join(lines).rstrip() + "\n"

    # ------- import -------

    def import_json(self, payload: dict[str, Any], *, actor: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("payload must be a v1 MindContinuum export")
        imported = skipped = errors = 0
        err_log: list[str] = []
        for p in payload.get("projects", []) or []:
            try:
                self.get_or_create_project(p.get("name") or p.get("slug"), actor=actor)
            except Exception as e:
                errors += 1; err_log.append(f"project: {e}")
        for it in payload.get("memory_items", []) or []:
            try:
                if not it.get("title"):
                    errors += 1; err_log.append("item: missing title"); continue
                cur = self._conn.execute(
                    "SELECT id FROM memory_items WHERE title=? AND created_at=?",
                    (it.get("title"), it.get("created_at")),
                )
                if cur.fetchone():
                    skipped += 1; continue
                tags = it.get("tags")
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except Exception:
                        tags = []
                self.save_memory(
                    title=it["title"],
                    body=it.get("body", ""),
                    summary=it.get("summary"),
                    type=it.get("type", "note"),
                    project=it.get("project_slug") or it.get("project_name"),
                    tags=tags or [],
                    importance=it.get("importance", "medium"),
                    source=it.get("source"),
                    status=it.get("status", "inbox"),
                    namespace=it.get("namespace", "work"),
                    actor=actor or "import",
                )
                imported += 1
            except Exception as e:
                errors += 1; err_log.append(f"item: {e}")
        with transaction(self._conn) as conn:
            _log_event(
                conn, action="import.json", actor=actor or self.actor_default,
                payload={"imported": imported, "skipped": skipped, "errors": errors},
            )
        return {"imported": imported, "skipped": skipped, "errors": errors,
                "error_messages": err_log[:20]}

    _MD_FM_RE = re.compile(
        r"^>\s*(Type|Status|Project|Tags|Importance|Source|Namespace)\s*:\s*(.+)$",
        re.IGNORECASE,
    )

    def import_markdown(self, text: str, *, actor: str | None = None) -> dict[str, Any]:
        if not text or not text.strip():
            return {"imported": 0, "skipped": 0, "errors": 0, "error_messages": []}
        blocks = re.split(r"(?m)^##\s+", text)
        if len(blocks) > 1:
            # Drop any pre-H2 preamble (often a single H1 title) — we only
            # want the H2 sections as individual items.
            blocks = blocks[1:]
        else:
            blocks = [re.sub(r"(?m)^#\s+", "", blocks[0], count=1)]
        imported = skipped = errors = 0
        err_log: list[str] = []
        for blk in blocks:
            blk = blk.strip()
            if not blk:
                continue
            lines = blk.splitlines()
            title = lines[0].lstrip("# ").strip()[:MAX_TITLE]
            fm: dict[str, str] = {}
            summary: str | None = None
            body_lines: list[str] = []
            for ln in lines[1:]:
                m = self._MD_FM_RE.match(ln)
                if m:
                    fm[m.group(1).lower()] = m.group(2).strip(); continue
                if ln.startswith("> ") and summary is None and not body_lines:
                    summary = ln[2:].strip(); continue
                body_lines.append(ln)
            try:
                exists = self._conn.execute(
                    "SELECT id FROM memory_items WHERE title=? "
                    "AND created_at > datetime('now','-1 day')",
                    (title,),
                ).fetchone()
                if exists:
                    skipped += 1; continue
                tags = [t.strip() for t in (fm.get("tags") or "").split(",") if t.strip()]
                self.save_memory(
                    title=title,
                    body="\n".join(body_lines).strip(),
                    summary=summary,
                    type=fm.get("type", "note"),
                    project=fm.get("project"),
                    tags=tags,
                    importance=fm.get("importance", "medium"),
                    source=fm.get("source"),
                    status=fm.get("status", "inbox"),
                    namespace=fm.get("namespace", "work"),
                    actor=actor or "import",
                )
                imported += 1
            except Exception as e:
                errors += 1; err_log.append(f"item: {e}")
        with transaction(self._conn) as conn:
            _log_event(
                conn, action="import.markdown", actor=actor or self.actor_default,
                payload={"imported": imported, "skipped": skipped, "errors": errors},
            )
        return {"imported": imported, "skipped": skipped, "errors": errors,
                "error_messages": err_log[:20]}

    # ------- export -------

    def export_all(self, *, namespace: str | None = None) -> dict[str, Any]:
        ns_clause = ""
        params: list[Any] = []
        if namespace is not None:
            _require("namespace", namespace, VALID_NAMESPACE)
            ns_clause = " WHERE namespace = ?"
            params = [namespace]
        projects = [dict(r) for r in self._conn.execute(
            "SELECT * FROM projects ORDER BY id"
        ).fetchall()]
        items = [_row_to_dict(r) for r in self._conn.execute(
            f"SELECT * FROM memory_items{ns_clause} ORDER BY id", params
        ).fetchall()]
        decisions = [dict(r) for r in self._conn.execute(
            "SELECT * FROM decisions ORDER BY id"
        ).fetchall()]
        tasks = [dict(r) for r in self._conn.execute(
            "SELECT * FROM tasks ORDER BY id"
        ).fetchall()]
        links = [dict(r) for r in self._conn.execute(
            "SELECT * FROM memory_links ORDER BY id"
        ).fetchall()]
        return {
            "version": 1,
            "exported_at": _utcnow(),
            "projects": projects,
            "memory_items": items,
            "decisions": decisions,
            "tasks": tasks,
            "memory_links": links,
        }

    def export_markdown(self, *, project: str | None = None, namespace: str = "work") -> str:
        ns = _require("namespace", namespace, VALID_NAMESPACE)
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
            "WHERE m.namespace = ? "
        )
        params: list[Any] = [ns]
        if project:
            sql += "AND p.slug = ? "
            params.append(_slugify(project))
        sql += "ORDER BY m.created_at DESC"
        cur = self._conn.execute(sql, params)
        lines: list[str] = ["# MindContinuum export", ""]
        if project:
            lines.append(f"_Project filter: `{_slugify(project)}`_"); lines.append("")
        for row in cur.fetchall():
            item = _row_to_dict(row)
            assert item is not None
            tags = ", ".join(item.get("tags") or [])
            lines.append(f"## {item['title']}"); lines.append("")
            lines.extend([
                f"- **Type**: {item['type']}",
                f"- **Status**: {item['status']}",
                f"- **Importance**: {item['importance']}",
                f"- **Project**: {item.get('project_name') or '—'}",
                f"- **Tags**: {tags or '—'}",
                f"- **Created**: {item['created_at']}",
            ])
            if item.get("summary"):
                lines.append("")
                lines.append(f"> {item['summary']}")
            lines.append("")
            lines.append(item.get("body") or "")
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # ------- events -------

    def list_events(
        self, limit: int = 50, *, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 500))
        sql = (
            "SELECT id, action, actor, target_kind, target_id, payload, namespace, created_at "
            "FROM events_log "
        )
        params: list[Any] = []
        if namespace is not None:
            _require("namespace", namespace, VALID_NAMESPACE)
            sql += "WHERE namespace = ? "
            params.append(namespace)
        sql += "ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        out = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
            out.append(d)
        return out

    # ------- embeddings -------

    def reindex_embeddings(self, *, batch_size: int = 64) -> dict[str, Any]:
        if not _emb.embeddings_available():
            return {"available": False, "indexed": 0}
        rows = self._conn.execute(
            "SELECT id, title, summary, body FROM memory_items"
        ).fetchall()
        indexed = 0
        for row in rows:
            ok = _emb.index_memory(
                self._conn, int(row["id"]),
                self._embed_text(row["title"], row["summary"], row["body"] or ""),
            )
            if ok:
                indexed += 1
        with transaction(self._conn) as conn:
            _log_event(
                conn, action="embeddings.reindex", actor="ui",
                payload={"indexed": indexed, "total": len(rows)},
            )
        return {"available": True, "indexed": indexed, "total": len(rows)}

    def embeddings_status(self) -> dict[str, Any]:
        return _emb.status(self._conn)
