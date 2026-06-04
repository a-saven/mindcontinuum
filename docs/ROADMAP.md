# Roadmap

Anything not in v1 lives here. Order matters — the project spec is explicit:
**do not start anything below until the bridge loop (Critical Spike 001) is
proven on Windows.**

## v1 (this repo)

- Safe core operations behind narrow MCP tools.
- SQLite + FTS5.
- Dashboard (Inbox / Search / Decisions / Tasks / Log / Settings).
- JSON + Markdown export.
- Windows + macOS/Linux launchers.
- `pytest` harness (40 tests).
- Owner-facing Gate 2 / Gate 3 playbooks.

## v1.1 — small wins after Gate 3

- Markdown import (`/api/import/markdown`) — useful for seeding from
  existing notes.
- "Copy as project pack" button — concatenates a project's stable items
  into a single Markdown blob for paste-into-new-chat.
- Stable / contradiction workflow in the UI: when a saved item conflicts
  with an existing stable item (by simple title-overlap heuristic for
  now), surface a contradiction marker; require explicit user click to
  promote either side to stable.
- Configurable actor token — name the calling client (e.g. `chatgpt-web`,
  `claude-desktop`) so the Log tab is filterable.

## v1.5 — local embeddings + hybrid search

- Add a local embedding model (e.g. `sentence-transformers` MiniLM, ~22 MB).
- Background job that embeds each new memory item; store vectors in
  `memory_embeddings(memory_id, vector_blob)`.
- Hybrid ranking: weighted blend of FTS5 `bm25` score + cosine similarity.
- `search_memory(..., mode="hybrid"|"keyword"|"semantic")` MCP option.
- Embedding model and dimension are pinned in config; reembed-on-upgrade
  is an explicit user action, not silent.

## v1.6 — dedup + consolidation

- Near-duplicate detection across `memory_items` using vector cosine +
  title Jaccard. Surface in the dashboard as a "Possible duplicates"
  list; merge requires user confirmation.
- Consolidation job: groups inbox items into a project summary; the
  summary is itself a memory item with `type=project_context`.
- `get_project_context_pack(project)` MCP tool — returns the compact
  context pack for a project (decisions, open tasks, risks, key facts).

## v2 — separate personal namespace

- Add a `namespace` column to `memory_items`, with a separate `personal`
  namespace gated behind a passphrase prompt in the UI.
- The MCP layer never sees personal items unless the client passes an
  explicit `namespace=personal` flag — and even then only for
  read tools, not write. Writing to personal is UI-only.

## Possibly later

- Optional Postgres backend (drop-in via DB URL). Keep SQLite as the
  default; document the migration path.
- Packaged desktop launcher (Tauri or Electron-Forge). Today the
  `run_windows.bat` + browser flow is enough.
- Backup scheduler — cron-style daily JSON export to a folder of choice.
- Cloud sync (opt-in only). Likely a separate dedicated server that pulls
  exports; the local MCP store is not directly cloud-backed.
