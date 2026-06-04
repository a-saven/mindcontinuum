# Changelog

## v0.2.2 — Thread safety + input hardening

Second-pass review. The big one is real: the previous version had a
threading bug that wasn't tripping in tests because pytest is
single-threaded. 122 cases passing (was 108).

### Thread safety (real bug)

- **`MemoryStore` now uses one SQLite connection per thread** via
  `threading.local`. FastAPI dispatches sync routes to a worker pool
  and MCP tools jump into `asyncio.to_thread`, so a single shared
  connection was being used concurrently by multiple threads.
  Symptoms: interleaved `BEGIN`/`COMMIT` calls, sporadic `SQLITE_BUSY`
  errors, and (in the worst case) FTS index corruption. Now each
  thread gets its own connection; `close()` tears them all down.
- **`busy_timeout = 5000` ms** set on every new connection so writers
  serialise cleanly instead of failing fast.
- **`transaction()` uses `BEGIN IMMEDIATE`** instead of the default
  deferred BEGIN. Under WAL, two deferred transactions racing to
  upgrade to writer can both lose to `SQLITE_BUSY` with no retry; an
  immediate BEGIN takes the reserved lock up front and queues
  contenders through the busy timeout. New tests exercise 60 saves
  across 8 threads + interleaved readers; both pass cleanly.

### Input hardening

- **Pydantic `max_length`** on every string field in `MemoryCreate`,
  `MemoryUpdate`, `AppendBody`, `DecisionCreate`, `TaskCreate`, etc.
  Matches the `MAX_TITLE` / `MAX_BODY` / `MAX_SUMMARY` constants in
  core. Oversize inputs now fail with a 422 at validation time rather
  than streaming megabytes through to core only to bounce them.
- **Import endpoint size cap** of 50 MB enforced via the
  `Content-Length` header AND a post-read check. Anything over the
  cap → 413 with a descriptive message. Removes the OOM-via-bulk-
  import vector flagged in the v0.2.1 changelog.

### API ergonomics

- **`/api/search` returns `resolved_mode`** alongside `mode`. When you
  ask for `mode=auto`, the response now also tells you whether it
  resolved to `keyword` or `hybrid` based on embeddings availability.
  Same field exposed through the MCP `search_memory` tool.
- **`/api/search?mode=bogus` reliably returns 400** with a descriptive
  message. The old test that accepted 400/422/500 is now tightened
  to demand 400.

### Markdown import polish

- **Preamble before the first H2 is kept as an item iff it has body
  content beyond the H1 line.** A bare `# Title` then `## Section`
  still drops the title-only preamble (a useless empty memory). A
  `# Title\n\nbody\n\n## Section` keeps the preamble as a separate
  memory now — previously it was dropped.

## v0.2.1 — Code-review fixes

Bug fixes and hardening surfaced by an audit pass over v0.2.0. 108
pytest cases passing (was 98).

### Bug fixes

- **Migration**: `_rebuild_memory_items_drop_status_check` was losing
  `namespace` data on rebuild (used a `LIMIT 0` subquery that always
  returned NULL, so COALESCE fell through to literal `'work'`). Rebuild
  now copies the column directly. New tests exercise the rebuild on a
  hand-crafted legacy DB and confirm personal items survive.
- **Migration ordering**: `schema.sql` was being executed before the
  pre-existing DB had its `namespace` column added, so `CREATE INDEX
  idx_memory_items_namespace` failed on legacy DBs. Split migrations
  into `_pre_schema_migrate` (adds missing columns) and
  `_post_schema_migrate` (CHECK-constraint rebuild) around the schema
  script.
- **Cross-namespace links** (privacy leak): `link_memories`,
  `mark_contradiction`, `supersede`, and `merge_into` now reject any
  operation across `work`/`personal` boundaries. `_links_for` also
  filters by parent namespace as a defence in depth — even if a legacy
  DB or hand-edit injected a cross-namespace link, `get_memory` will
  no longer surface it.
- **UI namespace toggle**: was only reloading Inbox + Proposed. Now
  reloads whatever tab is currently active (Decisions, Tasks, Projects,
  Events).

### New features

- **`MINDCONTINUUM_DISABLE_EMBEDDINGS=1`** env var and matching
  `--no-embeddings` CLI flag to skip the bge-small download and avoid
  the 5–30 s first-call latency on fresh installs. Re-read on every
  `embeddings_available()` call so operators can toggle without
  restart. `/api/embeddings/status` now reports `disabled_by_env`.

### Polish

- `update_memory` embedding-reindex condition simplified to one
  `text_changed = (title is not None) or …` boolean.
- `suggest_duplicates` sorts FTS title tokens before building the MATCH
  expression so behaviour is deterministic across runs.

## v0.2.0 — Full spec build (Gate 3 waived)

Adds every remaining feature from the original product spec on top of the
Gate-1 spike. 98 pytest cases passing.

### Schema

- `memory_items.namespace` and `events_log.namespace` (`work` / `personal`).
- `memory_links.note` column.
- `memory_embeddings(memory_id, model, dim, vector, indexed_at)`.
- `status` CHECK constraint dropped on `memory_items` (Python enforces) so
  the lifecycle can grow without ALTER-rebuild dances.
- `db.py` includes an idempotent migration pass for older DBs (adds the
  namespace + note columns, rebuilds `memory_items` if the legacy CHECK is
  detected).

### Workflow

- **Stable promotion**: `propose_stable` (agents) → `promote_stable` /
  `reject_proposal` (UI/REST only). Agents cannot create items directly
  at `status=stable`, nor edit a stable item's title/body via MCP.
- **Contradictions**: `mark_contradiction` creates a `contradicts` link
  but never changes status. Surfaced on both ends in `get_memory`.
- **Supersede**: REST-only. Marks the old item `stale` and links
  `new --supersedes--> old`.
- **Duplicate suggestions**: agent-callable scoring by title token Jaccard,
  tag overlap, and shared project.
- **Merge**: REST-only. Archives source, links `source --duplicate_of--> target`.
- **Free links**: `link_memories(related | supersedes | derived_from)`.

### Imports + packs

- `POST /api/import/json` (idempotent on `(title, created_at)`).
- `POST /api/import/markdown` (splits on H2, parses `> Type:`/`> Status:`/
  `> Project:`/`> Tags:` frontmatter, treats first non-frontmatter `>` line
  as summary).
- `get_project_context_pack` MCP tool. Same content available as REST
  `/api/projects/{slug}/pack` (JSON) and `/pack.md`.

### Namespace

- Read tools default to `work`. Personal items are NEVER returned to MCP
  callers without `MINDCONTINUUM_ALLOW_PERSONAL_MCP=1`; the response
  carries a `_warning` field explaining the gate. Personal-namespace writes
  from MCP are rejected with an exact reject message regardless.

### Embeddings + hybrid search

- `fastembed` (ONNX) with BAAI/bge-small-en-v1.5 (384 dim, ~133 MB, MIT).
- Model cache pinned to `<data_dir>/models`.
- Lazy load — first save / search loads the model. If `fastembed` is
  missing, writes still succeed and search degrades to keyword.
- `search_memory(mode=...)` accepts `auto` / `keyword` / `semantic` /
  `hybrid`. Hybrid normalises bm25 + cosine and only includes semantic
  candidates above a relevance floor.
- `POST /api/embeddings/reindex` + `GET /api/embeddings/status`.

### UI

- Namespace toggle (persisted in `localStorage`); Personal mode flips the
  accent colour and shows a `PERSONAL` chip.
- New tabs: Proposed (one-click promote/reject), Projects (Copy-pack button
  + download `pack.md`).
- Detail panel: Propose / Promote / Reject buttons, Related links panel,
  duplicate finder with one-click merge.
- Settings: embeddings status + reindex, JSON/Markdown import buttons.

## v0.1.0 — Gate 1 spike

- SQLite + FTS5 schema, safe core ops, FastMCP Streamable HTTP server with
  8 tools, dashboard, JSON + Markdown export, run scripts, pytest harness.
