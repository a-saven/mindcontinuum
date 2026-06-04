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
    """Bootstrap the schema and run any pending migrations.

    Order matters:

    1. ``_pre_schema_migrate`` adds columns that older DBs are missing
       (``namespace``, ``memory_links.note``). This has to run BEFORE the
       canonical schema script because the script creates indices that
       reference those columns.
    2. The schema script runs idempotently — ``CREATE TABLE IF NOT EXISTS``
       is a no-op when the table already exists, and FTS triggers /
       indices are re-attached.
    3. ``_post_schema_migrate`` runs the heavier rebuild (drops the legacy
       ``CHECK(status IN …)`` constraint) so newer statuses like
       ``proposed_stable`` can be inserted.
    """
    conn = connect(db_path)
    try:
        _pre_schema_migrate(conn)
        conn.executescript(_load_schema())
        _post_schema_migrate(conn)
    finally:
        conn.close()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _pre_schema_migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns on legacy DBs so the schema script can run."""
    for table in ("memory_items", "events_log"):
        if not _has_table(conn, table):
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "namespace" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN namespace TEXT NOT NULL DEFAULT 'work'"
            )
    if _has_table(conn, "memory_links"):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(memory_links)").fetchall()}
        if "note" not in cols:
            conn.execute("ALTER TABLE memory_links ADD COLUMN note TEXT")


def _post_schema_migrate(conn: sqlite3.Connection) -> None:
    """Drop the legacy status CHECK constraint by rebuilding memory_items."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_items'"
    ).fetchone()
    if row and "CHECK (status IN" in (row["sql"] or ""):
        _rebuild_memory_items_drop_status_check(conn)


def _rebuild_memory_items_drop_status_check(conn: sqlite3.Connection) -> None:
    """Drop the legacy CHECK(status IN ...) constraint by table-rebuild.

    Preserves all rows including ``namespace`` (which was already added by the
    prior ALTER step in :func:`_migrate`, so the old table is guaranteed to
    have that column at the point this runs). After the rebuild the schema
    script's CREATE-IF-NOT-EXISTS pass re-attaches the FTS triggers and
    indices to the new table.
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
               importance,status,namespace,created_at,updated_at
        FROM _memory_items_old;
        DROP TABLE _memory_items_old;
        COMMIT;
        PRAGMA foreign_keys=ON;
    """)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in an explicit write transaction.

    Uses ``BEGIN IMMEDIATE`` rather than the default ``BEGIN DEFERRED``.
    Under WAL, a deferred BEGIN doesn't acquire any lock until the first
    write; two concurrent deferred transactions can race and one will get
    ``SQLITE_BUSY`` with no retry, even when ``busy_timeout`` is set.
    Immediate BEGIN grabs the reserved-writer lock up front, so all
    contention happens at the BEGIN call site and is correctly handled by
    the busy timeout.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
