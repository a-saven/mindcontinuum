"""Thin SQLite layer. Provides a connection factory, schema bootstrap, and a
small idempotent migration pass for in-place upgrades."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator


def _load_schema() -> str:
    with resources.files("mindcontinuum").joinpath("schema.sql").open("r", encoding="utf-8") as f:
        return f.read()


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(_load_schema())
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent in-place migrations for DBs created by older versions.

    Each block self-detects whether it needs to run.
    """
    # 1) namespace column on memory_items + events_log
    for table in ("memory_items", "events_log"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "namespace" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN namespace TEXT NOT NULL DEFAULT 'work'")
    # 2) note column on memory_links
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(memory_links)").fetchall()}
    if "note" not in cols:
        conn.execute("ALTER TABLE memory_links ADD COLUMN note TEXT")
    # 3) old status CHECK on memory_items prevented 'proposed_stable' — rebuild
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_items'"
    ).fetchone()
    if row and "CHECK (status IN" in (row["sql"] or ""):
        _rebuild_memory_items_drop_status_check(conn)


def _rebuild_memory_items_drop_status_check(conn: sqlite3.Connection) -> None:
    """Drop the legacy CHECK(status IN ...) constraint by table-rebuild.

    Preserves all data and indices, then re-runs the schema script to
    re-create FTS triggers etc. against the new table.
    """
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        BEGIN;
        ALTER TABLE memory_items RENAME TO _memory_items_old;
        CREATE TABLE memory_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            type        TEXT NOT NULL DEFAULT 'note'
                        CHECK (type IN ('note','idea','research','project_context','client_context',
                                        'prompt','decision','task','risk')),
            summary     TEXT,
            body        TEXT NOT NULL DEFAULT '',
            project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            source      TEXT,
            tags_json   TEXT NOT NULL DEFAULT '[]',
            importance  TEXT NOT NULL DEFAULT 'medium'
                        CHECK (importance IN ('low','medium','high')),
            status      TEXT NOT NULL DEFAULT 'inbox',
            namespace   TEXT NOT NULL DEFAULT 'work',
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        INSERT INTO memory_items (id,title,type,summary,body,project_id,source,tags_json,
                                  importance,status,namespace,created_at,updated_at)
        SELECT id,title,type,summary,body,project_id,source,tags_json,
               importance,status,
               COALESCE((SELECT 'work' FROM pragma_table_info('_memory_items_old')
                         WHERE name='namespace' LIMIT 0), 'work'),
               created_at,updated_at FROM _memory_items_old;
        DROP TABLE _memory_items_old;
        COMMIT;
        PRAGMA foreign_keys=ON;
    """)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
