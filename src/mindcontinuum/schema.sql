-- MindContinuum SQLite schema v1
-- Conservative + safe-by-default. No destructive AI-facing tooling.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS memory_items (
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
    status      TEXT NOT NULL DEFAULT 'inbox'
                CHECK (status IN ('inbox','processed','stable','pinned','archived','stale','rejected')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_items_status   ON memory_items(status);
CREATE INDEX IF NOT EXISTS idx_memory_items_project  ON memory_items(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_type     ON memory_items(type);
CREATE INDEX IF NOT EXISTS idx_memory_items_created  ON memory_items(created_at DESC);

-- FTS5 virtual table mirrors title/summary/body/tags.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
    title, summary, body, tags,
    content='memory_items',
    content_rowid='id',
    tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS memory_items_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_items_fts(rowid, title, summary, body, tags)
    VALUES (new.id, new.title, IFNULL(new.summary,''), new.body, IFNULL(new.tags_json,''));
END;

CREATE TRIGGER IF NOT EXISTS memory_items_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, summary, body, tags)
    VALUES('delete', old.id, old.title, IFNULL(old.summary,''), old.body, IFNULL(old.tags_json,''));
END;

CREATE TRIGGER IF NOT EXISTS memory_items_au AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, summary, body, tags)
    VALUES('delete', old.id, old.title, IFNULL(old.summary,''), old.body, IFNULL(old.tags_json,''));
    INSERT INTO memory_items_fts(rowid, title, summary, body, tags)
    VALUES (new.id, new.title, IFNULL(new.summary,''), new.body, IFNULL(new.tags_json,''));
END;

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    memory_id   INTEGER REFERENCES memory_items(id) ON DELETE CASCADE,
    decision    TEXT NOT NULL,
    rationale   TEXT,
    tradeoffs   TEXT,
    status      TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','accepted','superseded','rejected')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    memory_id   INTEGER REFERENCES memory_items(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    next_action TEXT,
    priority    TEXT NOT NULL DEFAULT 'medium'
                CHECK (priority IN ('low','medium','high','urgent')),
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','in_progress','blocked','done','cancelled')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    uri         TEXT,
    label       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS memory_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id     INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    to_id       INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    link_type   TEXT NOT NULL DEFAULT 'related'
                CHECK (link_type IN ('related','contradicts','supersedes','derived_from','duplicate_of')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (from_id, to_id, link_type)
);

CREATE TABLE IF NOT EXISTS events_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'unknown',
    target_kind TEXT,
    target_id   INTEGER,
    payload     TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events_log(created_at DESC);
