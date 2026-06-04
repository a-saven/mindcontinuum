"""Regression tests for the v0.2.1 code-review fixes.

Each test name maps to one of the issues called out in the review:
  * test_migration_preserves_existing_namespace
  * test_link_memories_rejects_cross_namespace
  * test_mark_contradiction_rejects_cross_namespace
  * test_supersede_rejects_cross_namespace
  * test_merge_into_rejects_cross_namespace
  * test_links_for_filters_cross_namespace
  * test_embeddings_env_disable_flag
  * test_status_reports_disabled_by_env
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mindcontinuum import embeddings as emb
from mindcontinuum.core import MemoryStore
from mindcontinuum.db import init_db


# ---------------------------------------------------------------------------
# Migration: rebuild preserves namespace
# ---------------------------------------------------------------------------


def _make_legacy_db(path: Path) -> None:
    """Hand-write a v0.1-shaped memory_items table (with status CHECK, no namespace)."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE memory_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            type        TEXT NOT NULL DEFAULT 'note',
            summary     TEXT,
            body        TEXT NOT NULL DEFAULT '',
            project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            source      TEXT,
            tags_json   TEXT NOT NULL DEFAULT '[]',
            importance  TEXT NOT NULL DEFAULT 'medium',
            status      TEXT NOT NULL DEFAULT 'inbox'
                        CHECK (status IN ('inbox','processed','stable','pinned',
                                          'archived','stale','rejected')),
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE events_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'unknown',
            target_kind TEXT,
            target_id INTEGER,
            payload TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            to_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            link_type TEXT NOT NULL DEFAULT 'related',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            UNIQUE (from_id, to_id, link_type)
        );
        INSERT INTO memory_items (title, status) VALUES
            ('legacy-inbox-item', 'inbox'),
            ('legacy-stable-item', 'stable');
    """)
    conn.commit()
    conn.close()


def test_migration_preserves_existing_namespace(tmp_path: Path):
    """After init_db runs on a v0.1 DB, both rows survive and the rebuild
    leaves the (newly added) namespace column at its default rather than
    losing it."""
    db = tmp_path / "legacy.sqlite"
    _make_legacy_db(db)
    init_db(db)  # runs schema.sql + _migrate()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = {r["title"]: dict(r) for r in conn.execute(
        "SELECT title, status, namespace FROM memory_items"
    ).fetchall()}
    conn.close()
    assert rows["legacy-inbox-item"]["namespace"] == "work"
    assert rows["legacy-stable-item"]["namespace"] == "work"
    assert rows["legacy-stable-item"]["status"] == "stable"


def test_migration_preserves_personal_namespace_after_rebuild(tmp_path: Path):
    """If a legacy DB already had a row with namespace='personal' (e.g.
    the migration ran once with the old buggy rebuild), the next init_db
    must not flatten it back to 'work'."""
    db = tmp_path / "legacy2.sqlite"
    _make_legacy_db(db)
    # Hand-add the namespace column AND a personal row, but keep the old
    # status CHECK constraint so the rebuild will fire.
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE memory_items ADD COLUMN namespace TEXT NOT NULL DEFAULT 'work'")
    conn.execute(
        "INSERT INTO memory_items (title, status, namespace) VALUES (?,?,?)",
        ("legacy-personal-item", "inbox", "personal"),
    )
    conn.commit()
    conn.close()
    init_db(db)  # triggers the rebuild
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = {r["title"]: dict(r) for r in conn.execute(
        "SELECT title, namespace FROM memory_items"
    ).fetchall()}
    conn.close()
    assert rows["legacy-personal-item"]["namespace"] == "personal"
    assert rows["legacy-inbox-item"]["namespace"] == "work"


# ---------------------------------------------------------------------------
# Cross-namespace link enforcement
# ---------------------------------------------------------------------------


def test_link_memories_rejects_cross_namespace(store: MemoryStore):
    a = store.save_memory(title="work item")
    b = store.save_memory(title="personal item", namespace="personal", actor="ui")
    with pytest.raises(ValueError, match="across namespaces"):
        store.link_memories(a["id"], b["id"], "related", actor="ui")


def test_mark_contradiction_rejects_cross_namespace(store: MemoryStore):
    a = store.save_memory(title="work claim")
    b = store.save_memory(title="personal claim", namespace="personal", actor="ui")
    with pytest.raises(ValueError, match="across namespaces"):
        store.mark_contradiction(b["id"], a["id"], actor="ui")


def test_supersede_rejects_cross_namespace(store: MemoryStore):
    old = store.save_memory(title="work old", status="stable", actor="ui")
    new = store.save_memory(title="personal new", namespace="personal", actor="ui")
    with pytest.raises(ValueError, match="across namespaces"):
        store.supersede(old["id"], new["id"], actor="ui")


def test_merge_into_rejects_cross_namespace(store: MemoryStore):
    a = store.save_memory(title="work dup")
    b = store.save_memory(title="personal canonical", namespace="personal", actor="ui")
    with pytest.raises(ValueError, match="across namespaces"):
        store.merge_into(a["id"], b["id"], actor="ui")


def test_links_for_filters_cross_namespace_defensively(store: MemoryStore):
    """Even if cross-namespace link rows exist (e.g. from a legacy DB or
    hand-edit), get_memory must not surface them."""
    a = store.save_memory(title="work A")
    b = store.save_memory(title="personal B", namespace="personal", actor="ui")
    # Bypass the public API to inject a forbidden link.
    store._conn.execute(
        "INSERT INTO memory_links (from_id, to_id, link_type) VALUES (?,?, 'related')",
        (a["id"], b["id"]),
    )
    enriched = store.get_memory(a["id"])
    assert all(link["other_namespace"] == "work" for link in enriched["links"]), (
        "cross-namespace links must not be returned from get_memory"
    )


# ---------------------------------------------------------------------------
# Embeddings opt-out via env flag
# ---------------------------------------------------------------------------


def test_embeddings_env_disable_flag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(emb.DISABLE_ENV_VAR, "1")
    # Force re-evaluation: env check is not cached.
    assert emb.embeddings_available() is False
    monkeypatch.delenv(emb.DISABLE_ENV_VAR)
    # The import-cache may have a stale True from previous tests; if we just
    # asserted False above, we know the env path works. The opposite-direction
    # check uses a fresh module reset to avoid the cache.
    emb.reset_state_for_tests()
    # Don't assert True here — fastembed may not be installed in some envs;
    # just confirm the call path doesn't crash.
    emb.embeddings_available()


def test_status_reports_disabled_by_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(emb.DISABLE_ENV_VAR, "yes")
    store = MemoryStore(db_path=tmp_path / "env_off.sqlite", enable_embeddings=False)
    try:
        s = store.embeddings_status()
        assert s["available"] is False
        assert s["disabled_by_env"] is True
    finally:
        store.close()


def test_index_memory_noop_when_env_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(emb.DISABLE_ENV_VAR, "1")
    store = MemoryStore(db_path=tmp_path / "env_off2.sqlite", enable_embeddings=True)
    try:
        store.save_memory(title="should not crash")
        s = store.embeddings_status()
        assert s["count_indexed"] == 0
    finally:
        store.close()
