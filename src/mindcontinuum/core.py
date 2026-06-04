"""Safe memory operations.

This module is the ONLY surface the MCP and HTTP layers should use to read and
write the database. It enforces:

  * input validation (allowed enum values)
  * structured event logging on every write
  * NO destructive deletes
  * NO raw SQL execution surface

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

from .db import connect, init_db, transaction

VALID_TYPES = {
    "note", "idea", "research", "project_context", "client_context",
    "prompt", "decision", "task", "risk",
}
VALID_STATUS = {"inbox", "processed", "stable", "pinned", "archived", "stale", "rejected"}
VALID_IMPORTANCE = {"low", "medium", "high"}
VALID_PRIORITY = {"low", "medium", "high", "urgent"}
VALID_DECISION_STATUS = {"proposed", "accepted", "superseded", "rejected"}
VALID_TASK_STATUS = {"open", "in_progress", "blocked", "done", "cancelled"}
VALID_LINK_TYPES = {"related", "contradicts", "supersedes", "derived_from", "duplicate_of"}

STATUS_EDITABLE = VALID_STATUS  # AI may set status only via dedicated tools; UI may set any.

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
) -> None:
    conn.execute(
        "INSERT INTO events_log (action, actor, target_kind, target_id, payload) VALUES (?,?,?,?,?)",
        (action, actor, target_kind, target_id,
         json.dumps(payload, ensure_ascii=False, default=str) if payload else None),
    )


@dataclass
class MemoryStore:
    """Connected, schema-initialized memory store.

    Use as a short-lived dependency. Thread-safe at the SQLite level because each
    method opens its own short transaction; the underlying connection is shared
    but the GIL plus SQLite serialization is sufficient for the MVP load.
    """

    db_path: Path
    actor_default: str = "system"
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
        cur = self._conn.execute("SELECT id, slug, name, description, created_at FROM projects ORDER BY name")
        return [dict(r) for r in cur.fetchall()]

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
        actor: str | None = None,
    ) -> dict[str, Any]:
        t = _validate_title(title)
        b = _validate_body(body)
        s = _validate_summary(summary)
        typ = _require("type", type, VALID_TYPES)
        imp = _require("importance", importance, VALID_IMPORTANCE)
        st = _require("status", status, VALID_STATUS)
        tags_list = _normalize_tags(tags)
        project_id = self.get_or_create_project(project, actor=actor) if project else None

        with transaction(self._conn) as conn:
            cur = conn.execute(
                """INSERT INTO memory_items
                   (title, type, summary, body, project_id, source, tags_json, importance, status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (t, typ, s, b, project_id, source, json.dumps(tags_list), imp, st),
            )
            new_id = int(cur.lastrowid)
            _log_event(
                conn, action="memory.create", actor=actor or self.actor_default,
                target_kind="memory_item", target_id=new_id,
                payload={"title": t, "type": typ, "project": project, "tags": tags_list, "status": st},
            )
        return self.get_memory(new_id)  # type: ignore[return-value]

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        cur = self._conn.execute(
            """SELECT m.*, p.slug AS project_slug, p.name AS project_name
               FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id
               WHERE m.id = ?""",
            (int(memory_id),),
        )
        return _row_to_dict(cur.fetchone())

    def list_recent(self, limit: int = 25, *, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 25), 200))
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
        )
        params: list[Any] = []
        if status:
            _require("status", status, VALID_STATUS)
            sql += "WHERE m.status = ? "
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
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        limit = max(1, min(int(limit or 20), 100))
        match = _fts_escape(q)
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name, "
            "       bm25(memory_items_fts) AS rank "
            "FROM memory_items_fts f "
            "JOIN memory_items m ON m.id = f.rowid "
            "LEFT JOIN projects p ON p.id = m.project_id "
            "WHERE memory_items_fts MATCH ? "
        )
        params: list[Any] = [match]
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
            cur = self._conn.execute(sql, params)
            return [_row_to_dict(r) for r in cur.fetchall()]  # type: ignore[list-item]
        except sqlite3.OperationalError:
            # Bad MATCH expression — fall back to LIKE so the user still gets results.
            like = f"%{q}%"
            cur = self._conn.execute(
                "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
                "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
                "WHERE m.title LIKE ? OR m.body LIKE ? OR m.summary LIKE ? "
                "ORDER BY m.created_at DESC LIMIT ?",
                (like, like, like, limit),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]  # type: ignore[list-item]

    def append_memory(self, memory_id: int, text: str, *, actor: str | None = None) -> dict[str, Any]:
        if not (text or "").strip():
            raise ValueError("append text is required")
        existing = self.get_memory(memory_id)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")
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
                target_kind="memory_item", target_id=int(memory_id),
                payload={"appended_chars": len(text)},
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
        existing = self.get_memory(memory_id)
        if not existing:
            raise LookupError(f"memory {memory_id} not found")

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
            fields.append("status = ?"); params.append(_require("status", status, VALID_STATUS))
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
                payload={"changed": [f.split(" = ")[0] for f in fields if f != "updated_at = ?"]},
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def archive_memory(self, memory_id: int, *, actor: str | None = None) -> dict[str, Any]:
        return self.update_memory(memory_id, status="archived", actor=actor)

    # ------- decisions -------

    def record_decision(
        self,
        *,
        project: str,
        decision: str,
        rationale: str | None = None,
        tradeoffs: str | None = None,
        status: str = "proposed",
        actor: str | None = None,
    ) -> dict[str, Any]:
        if not (decision or "").strip():
            raise ValueError("decision is required")
        st = _require("status", status, VALID_DECISION_STATUS)
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
                """INSERT INTO memory_items (title, type, summary, body, project_id, status, tags_json)
                   VALUES (?, 'decision', NULL, ?, ?, 'inbox', '["decision"]')""",
                (title, body, project_id),
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
                target_kind="decision", target_id=decision_id,
                payload={"project": project, "status": st, "memory_id": mem_id},
            )
        return {
            "decision_id": decision_id,
            "memory_id": mem_id,
            "project_id": project_id,
            "status": st,
            "decision": decision.strip(),
            "rationale": rationale,
            "tradeoffs": tradeoffs,
        }

    def list_decisions(self, *, project: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT d.*, p.slug AS project_slug, p.name AS project_name "
            "FROM decisions d LEFT JOIN projects p ON p.id = d.project_id "
        )
        params: list[Any] = []
        if project:
            sql += "WHERE p.slug = ? "
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
        actor: str | None = None,
    ) -> dict[str, Any]:
        t = _validate_title(title)
        pri = _require("priority", priority, VALID_PRIORITY)
        st = _require("status", status, VALID_TASK_STATUS)
        project_id = self.get_or_create_project(project, actor=actor)

        body_parts = [t]
        if next_action:
            body_parts.append(f"\n### Next action\n{next_action.strip()}")
        body = _validate_body("\n".join(body_parts))

        with transaction(self._conn) as conn:
            cur = conn.execute(
                """INSERT INTO memory_items (title, type, summary, body, project_id, status, tags_json, importance)
                   VALUES (?, 'task', NULL, ?, ?, 'inbox', '["task"]', ?)""",
                (t, body, project_id, "high" if pri in ("high", "urgent") else "medium"),
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
                target_kind="task", target_id=task_id,
                payload={"project": project, "priority": pri, "status": st, "memory_id": mem_id},
            )
        return {
            "task_id": task_id,
            "memory_id": mem_id,
            "project_id": project_id,
            "title": t,
            "next_action": next_action,
            "priority": pri,
            "status": st,
        }

    def list_tasks(self, *, project: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT t.*, p.slug AS project_slug, p.name AS project_name "
            "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
        )
        params: list[Any] = []
        clauses = []
        if project:
            clauses.append("p.slug = ?"); params.append(_slugify(project))
        if status:
            _require("status", status, VALID_TASK_STATUS)
            clauses.append("t.status = ?"); params.append(status)
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + " "
        sql += "ORDER BY t.created_at DESC LIMIT 200"
        cur = self._conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # ------- export -------

    def export_all(self) -> dict[str, Any]:
        cur = self._conn.execute("SELECT * FROM projects ORDER BY id")
        projects = [dict(r) for r in cur.fetchall()]
        cur = self._conn.execute("SELECT * FROM memory_items ORDER BY id")
        items = [_row_to_dict(r) for r in cur.fetchall()]
        cur = self._conn.execute("SELECT * FROM decisions ORDER BY id")
        decisions = [dict(r) for r in cur.fetchall()]
        cur = self._conn.execute("SELECT * FROM tasks ORDER BY id")
        tasks = [dict(r) for r in cur.fetchall()]
        cur = self._conn.execute("SELECT * FROM memory_links ORDER BY id")
        links = [dict(r) for r in cur.fetchall()]
        return {
            "version": 1,
            "exported_at": _utcnow(),
            "projects": projects,
            "memory_items": items,
            "decisions": decisions,
            "tasks": tasks,
            "memory_links": links,
        }

    def export_markdown(self, *, project: str | None = None) -> str:
        sql = (
            "SELECT m.*, p.slug AS project_slug, p.name AS project_name "
            "FROM memory_items m LEFT JOIN projects p ON p.id = m.project_id "
        )
        params: list[Any] = []
        if project:
            sql += "WHERE p.slug = ? "
            params.append(_slugify(project))
        sql += "ORDER BY m.created_at DESC"
        cur = self._conn.execute(sql, params)
        lines: list[str] = ["# MindContinuum export", ""]
        if project:
            lines.append(f"_Project filter: `{_slugify(project)}`_")
            lines.append("")
        for row in cur.fetchall():
            item = _row_to_dict(row)
            assert item is not None
            tags = ", ".join(item.get("tags") or [])
            lines.append(f"## {item['title']}")
            lines.append("")
            meta = [
                f"- **Type**: {item['type']}",
                f"- **Status**: {item['status']}",
                f"- **Importance**: {item['importance']}",
                f"- **Project**: {item.get('project_name') or '—'}",
                f"- **Tags**: {tags or '—'}",
                f"- **Created**: {item['created_at']}",
            ]
            lines.extend(meta)
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

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 500))
        cur = self._conn.execute(
            "SELECT id, action, actor, target_kind, target_id, payload, created_at "
            "FROM events_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
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


# FTS5 reserves a handful of characters; escape user queries safely.
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|\".*?\"")


def _fts_escape(q: str) -> str:
    """Turn a free-text user query into a safe FTS5 MATCH expression.

    Strategy: extract word-ish tokens, double-quote each (so punctuation is
    treated literally), AND them together. Short tokens and stop-noise are
    preserved as-is. Anything we cannot parse falls back to an empty match
    which the caller treats as "no FTS match", then falls back to LIKE.
    """
    raw_tokens = _FTS_TOKEN_RE.findall(q)
    if not raw_tokens:
        return '""'
    quoted: list[str] = []
    for tok in raw_tokens:
        t = tok.strip('"').replace('"', '""').strip()
        if t:
            quoted.append(f'"{t}"')
    return " ".join(quoted) if quoted else '""'
